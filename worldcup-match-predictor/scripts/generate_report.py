#!/usr/bin/env python3
"""生成世界杯比分预测 PNG 报告卡片。

用法：
  python generate_report.py <data.json> <output_dir>

data.json 结构示例见同目录 sample_data.json。
脚本仅做渲染，不含预测逻辑——预测数据由 skill 分析后写入 JSON。

输出规则：
  - 如果 data.json 包含 reflection，则先生成 00 号赛后复盘卡。
  - 每场比赛生成 1 张 PNG。
  - 当天 6 场比赛就生成 6 张 PNG。
  - PNG 文件保存到调用方传入的日期目录，例如 reports/2026-06-25/。
"""
import html
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


RENDER_SCALE = 2
OUTPUT_DPI = 300


def px(value):
    return int(round(value * RENDER_SCALE))


WIDTH = px(1600)
MARGIN = px(56)
GAP = px(36)
CARD_RADIUS = px(30)

COLORS = {
    "bg": "#edf2f8",
    "panel": "#ffffff",
    "ink": "#101827",
    "muted": "#5e6d83",
    "line": "#d6e1ee",
    "blue": "#1f4ed8",
    "blue_dark": "#153c9f",
    "blue_soft": "#e8f0ff",
    "green": "#159447",
    "green_soft": "#e7f8ee",
    "amber": "#cf7600",
    "amber_soft": "#fff4d8",
    "red": "#d7222a",
    "red_soft": "#ffe6e8",
    "purple": "#6d28d9",
    "purple_soft": "#eee7ff",
}


def find_font(candidates):
    for path in candidates:
        if Path(path).exists():
            return path
    return None


SONGTI_PATH = find_font(
    [
        "/System/Library/Fonts/Supplemental/Songti.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
)
TIMES_PATH = find_font(
    [
        "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
        "/Library/Fonts/Times New Roman.ttf",
        "/System/Library/Fonts/Times.ttc",
    ]
)
TIMES_BOLD_PATH = find_font(
    [
        "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf",
        "/Library/Fonts/Times New Roman Bold.ttf",
        "/System/Library/Fonts/Times.ttc",
    ]
)


def load_font(path, size, index=0):
    if not path:
        return ImageFont.load_default()
    try:
        return ImageFont.truetype(path, px(size), index=index)
    except (OSError, IndexError):
        return ImageFont.truetype(path, px(size))


def font_pair(size, zh_index=6, en_bold=False):
    return {
        "zh": load_font(SONGTI_PATH, size, index=zh_index),
        "en": load_font(TIMES_BOLD_PATH if en_bold else TIMES_PATH, size),
    }


FONTS = {
    "title": font_pair(46, zh_index=1, en_bold=True),
    "date": font_pair(28),
    "teams": font_pair(58, zh_index=1, en_bold=True),
    "score": font_pair(92, zh_index=1, en_bold=True),
    "section": font_pair(34, zh_index=1, en_bold=True),
    "body": font_pair(30),
    "small": font_pair(25),
    "tiny": font_pair(21),
}


def latin_font_char(ch):
    return ch.isascii() or ord(ch) < 256


def text_runs(text, text_font):
    if not isinstance(text_font, dict):
        return [("single", str(text))]
    runs = []
    current_key = None
    current = []
    for ch in str(text):
        key = "en" if latin_font_char(ch) else "zh"
        if key != current_key and current:
            runs.append((current_key, "".join(current)))
            current = []
        current_key = key
        current.append(ch)
    if current:
        runs.append((current_key, "".join(current)))
    return runs


def run_font(text_font, key):
    if key == "single":
        return text_font
    return text_font[key]


def baseline_textbbox(draw, text, font_obj):
    try:
        return draw.textbbox((0, 0), str(text), font=font_obj, anchor="ls")
    except TypeError:
        return draw.textbbox((0, 0), str(text), font=font_obj)


def text_width(draw, text, font_obj):
    try:
        return int(round(draw.textlength(str(text), font=font_obj)))
    except AttributeError:
        box = baseline_textbbox(draw, text, font_obj)
        return box[2] - box[0]


def draw_text(draw, xy, text, text_font, fill):
    x, y = xy
    runs = []
    top = 0
    for key, run in text_runs(text, text_font):
        font_obj = run_font(text_font, key)
        box = baseline_textbbox(draw, run, font_obj)
        top = min(top, box[1])
        runs.append((run, font_obj))

    # Different fonts expose different ascender/descender boxes. Drawing each
    # run on a shared baseline keeps Latin digits and Chinese text on one line.
    baseline_y = y - top
    for run, font_obj in runs:
        try:
            draw.text((x, baseline_y), run, font=font_obj, fill=fill, anchor="ls")
        except TypeError:
            draw.text((x, y), run, font=font_obj, fill=fill)
        x += text_width(draw, run, font_obj)


def text_size(draw, text, text_font):
    width = 0
    top = 0
    bottom = 0
    for key, run in text_runs(text, text_font):
        font_obj = run_font(text_font, key)
        box = baseline_textbbox(draw, run, font_obj)
        width += text_width(draw, run, font_obj)
        top = min(top, box[1])
        bottom = max(bottom, box[3])
    return width, bottom - top


def risk_color(level):
    return {
        "稳胆": COLORS["green"],
        "博胆": COLORS["amber"],
        "高风险": COLORS["red"],
        "观望": COLORS["muted"],
    }.get(level, COLORS["muted"])


def conf_color(level):
    return {"高": COLORS["green"], "中": COLORS["amber"], "低": COLORS["red"]}.get(level, COLORS["muted"])


def strip_markup(text):
    text = text or ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</?(b|strong|i|em)>", "", text, flags=re.I)
    text = re.sub(r"<a\s+[^>]*>(.*?)</a>", r"\1", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def line_height(draw, text_font, gap=9):
    return text_size(draw, "国Ag09", text_font)[1] + px(gap)


def wrap_line(draw, line, text_font, max_width):
    if not line:
        return [""]
    wrapped = []
    current = ""
    for ch in line:
        candidate = current + ch
        width, _ = text_size(draw, candidate, text_font)
        if width <= max_width or not current:
            current = candidate
        else:
            wrapped.append(current)
            current = ch
    if current:
        wrapped.append(current)
    return wrapped


def wrap_text(draw, text, text_font, max_width):
    lines = []
    for line in str(text).splitlines():
        lines.extend(wrap_line(draw, line.strip(), text_font, max_width))
    return lines


def measure_wrapped(draw, text, max_width, text_font, gap=9):
    lines = wrap_text(draw, text, text_font, max_width)
    return len(lines) * line_height(draw, text_font, gap), lines


def draw_wrapped(draw, text, x, y, max_width, text_font, fill=COLORS["ink"], gap=9):
    _, lines = measure_wrapped(draw, text, max_width, text_font, gap)
    step = line_height(draw, text_font, gap)
    for line in lines:
        draw_text(draw, (x, y), line, text_font, fill)
        y += step
    return y


def draw_badge(draw, text, x, y, fill, fg="#ffffff", pad_x=18, pad_y=8, text_font=None):
    text_font = text_font or FONTS["small"]
    pad_x = px(pad_x)
    pad_y = px(pad_y)
    w, h = text_size(draw, text, text_font)
    rect = (x, y, x + w + pad_x * 2, y + h + pad_y * 2)
    draw.rounded_rectangle(rect, radius=px(18), fill=fill)
    draw_text(draw, (x + pad_x, y + pad_y - px(1)), text, text_font, fg)
    return rect[2]


def draw_section(draw, title, x, y, width):
    draw_text(draw, (x, y), title, FONTS["section"], COLORS["blue"])
    y += px(42)
    draw.line((x, y, x + width, y), fill=COLORS["line"], width=px(2))
    return y + px(18)


def draw_odds_table(draw, odds, x, y, width):
    spf = odds.get("spf") or {}
    rspf = odds.get("rspf") or {}
    handicap = rspf.get("handicap", "")
    rows = [
        ("胜平负", spf.get("win", "-"), spf.get("draw", "-"), spf.get("lose", "-")),
        (f"让球({handicap})" if handicap else "让球", rspf.get("win", "-"), rspf.get("draw", "-"), rspf.get("lose", "-")),
    ]

    row_h = px(52)
    headers = ["玩法", "胜", "平", "负"]
    col_w = [width * 0.42, width * 0.19, width * 0.19, width * 0.20]

    draw.rounded_rectangle((x, y, x + width, y + row_h * 3), radius=px(16), fill="#f8fbff", outline=COLORS["line"], width=px(2))
    cur_x = x
    for i, header in enumerate(headers):
        draw_text(draw, (cur_x + px(16), y + px(14)), header, FONTS["small"], COLORS["muted"])
        cur_x += col_w[i]
        if i < 3:
            draw.line((cur_x, y, cur_x, y + row_h * 3), fill=COLORS["line"], width=px(1))
    draw.line((x, y + row_h, x + width, y + row_h), fill=COLORS["line"], width=px(1))
    draw.line((x, y + row_h * 2, x + width, y + row_h * 2), fill=COLORS["line"], width=px(1))

    for r, row in enumerate(rows, start=1):
        cur_x = x
        for i, value in enumerate(row):
            draw_text(draw, (cur_x + px(16), y + row_h * r + px(14)), str(value), FONTS["small"], COLORS["ink"])
            cur_x += col_w[i]
    return y + row_h * 3


def knockout_summary(match):
    knockout = match.get("knockout") or {}
    if not knockout:
        return ""
    parts = []
    if knockout.get("regular_time"):
        parts.append(f"常规时间 {knockout['regular_time']}")
    if knockout.get("extra_time"):
        parts.append(f"加时 {knockout['extra_time']}")
    if knockout.get("penalties"):
        parts.append(f"点球 {knockout['penalties']}")
    if knockout.get("advancer"):
        parts.append(f"晋级 {knockout['advancer']}")
    if knockout.get("advancement_prob"):
        parts.append(f"晋级概率 {knockout['advancement_prob']}")
    return "；".join(parts)


def match_extra_sections(match):
    sections = []
    if match.get("weather"):
        sections.append(("天气/场地", [match["weather"]]))

    referee = match.get("referee")
    card_prediction = match.get("card_prediction")
    referee_items = []
    if isinstance(referee, dict):
        if referee.get("name"):
            referee_items.append(f"主裁判：{referee['name']}")
        if referee.get("style"):
            referee_items.append(f"执法尺度：{referee['style']}")
        if referee.get("history"):
            referee_items.append(f"历史倾向：{referee['history']}")
    elif referee:
        referee_items.append(str(referee))
    if card_prediction:
        referee_items.append(f"出牌推测：{card_prediction}")
    if referee_items:
        sections.append(("裁判/出牌", referee_items))

    coach_items = []
    if match.get("coach_tactics"):
        coach_items.append(match["coach_tactics"])
    if match.get("bench_depth"):
        coach_items.append(f"替补后手：{match['bench_depth']}")
    if coach_items:
        sections.append(("教练/后手", coach_items))

    knockout = knockout_summary(match)
    if knockout:
        sections.append(("淘汰赛预测", [knockout]))
    return sections


def safe_filename(text):
    text = re.sub(r"[\\/:*?\"<>|]+", "_", text)
    text = re.sub(r"\s+", "_", text)
    return text.strip("_")


def layout_metrics(draw, match):
    card_w = WIDTH - MARGIN * 2
    inner_w = card_w - px(76)
    left_w = int(inner_w * 0.58)
    right_w = inner_w - left_w - GAP

    y = 0
    y += px(78)
    time_h, _ = measure_wrapped(
        draw,
        f"北京 {match.get('time_bj', '-')} | 当地 {match.get('time_local', '-')} | {match.get('venue', '')}",
        inner_w,
        FONTS["small"],
        8,
    )
    y += time_h + px(24)
    y += px(164)
    prob = match.get("probability", {})
    prob_text = f"胜平负概率：主胜 {prob.get('win', '-')} | 平 {prob.get('draw', '-')} | 客胜 {prob.get('lose', '-')}"
    prob_h, _ = measure_wrapped(draw, prob_text, inner_w, FONTS["body"], 9)
    goals_h, _ = measure_wrapped(draw, "总进球预测：" + match.get("total_goals", "-"), inner_w, FONTS["body"], 9)
    knockout = knockout_summary(match)
    knockout_h = 0
    if knockout:
        knockout_h, _ = measure_wrapped(draw, "淘汰赛预测：" + knockout, inner_w, FONTS["body"], 9)
    y += prob_h + goals_h + knockout_h + px(28)

    left_y = px(60)
    for factor in match.get("key_factors", []):
        h, _ = measure_wrapped(draw, "• " + factor, left_w, FONTS["body"], 8)
        left_y += h + px(10)
    for title, items in match_extra_sections(match):
        left_y += px(54)
        for item in items:
            h, _ = measure_wrapped(draw, "• " + item, left_w, FONTS["small"], 8)
            left_y += h + px(8)

    right_y = px(60) + px(156) + px(22)
    odds = match.get("odds", {})
    h, _ = measure_wrapped(draw, "总进球数：" + (odds.get("goals") or "-"), right_w, FONTS["small"], 8)
    right_y += h + px(10)
    h, _ = measure_wrapped(draw, "比分/波胆：" + (odds.get("score") or "-"), right_w, FONTS["small"], 8)
    right_y += h + px(28)
    h, _ = measure_wrapped(draw, match.get("bet_advice", "-"), right_w - px(34), FONTS["body"], 9)
    right_y += px(60) + h + px(42)

    bottom_pad = px(160) if match_extra_sections(match) else px(72)
    content_h = y + max(left_y, right_y) + bottom_pad
    return {
        "card_h": content_h,
        "inner_w": inner_w,
        "left_w": left_w,
        "right_w": right_w,
        "top_after_intro": y,
    }


def reflection_layout_metrics(draw, reflection):
    card_w = WIDTH - MARGIN * 2
    inner_w = card_w - px(76)
    y = px(90)
    subtitle = reflection.get("subtitle", "")
    if subtitle:
        h, _ = measure_wrapped(draw, subtitle, inner_w, FONTS["small"], 8)
        y += h + px(28)

    for section in reflection.get("sections", []):
        y += px(60)
        for item in section.get("items", []):
            h, _ = measure_wrapped(draw, "• " + item, inner_w, FONTS["body"], 8)
            y += h + px(12)
        y += px(10)

    return {"card_h": y + px(70), "inner_w": inner_w}


def render_reflection_png(data, reflection, output_dir):
    probe = Image.new("RGB", (WIDTH, px(100)), COLORS["bg"])
    probe_draw = ImageDraw.Draw(probe)
    metrics = reflection_layout_metrics(probe_draw, reflection)

    header_h = px(122)
    card_w = WIDTH - MARGIN * 2
    card_y = MARGIN + header_h + px(28)
    card_h = int(metrics["card_h"])
    height = card_y + card_h + MARGIN

    img = Image.new("RGB", (WIDTH, height), COLORS["bg"])
    draw = ImageDraw.Draw(img)
    x = MARGIN
    y = MARGIN

    draw.rounded_rectangle((x, y, x + card_w, y + header_h), radius=CARD_RADIUS, fill=COLORS["blue"])
    draw_text(draw, (x + px(34), y + px(24)), "世界杯预测复盘与今日校准", FONTS["title"], "#ffffff")
    draw_text(draw, (x + px(36), y + px(78)), data.get("date", ""), FONTS["date"], "#dbeafe")
    draw_text(draw, (x + card_w - px(240), y + px(78)), "第 00 场", FONTS["date"], "#dbeafe")

    draw.rounded_rectangle((x, card_y, x + card_w, card_y + card_h), radius=CARD_RADIUS, fill=COLORS["panel"])
    inner_x = x + px(38)
    inner_y = card_y + px(34)
    inner_w = metrics["inner_w"]

    draw_text(draw, (inner_x, inner_y), reflection.get("title", "赛后复盘 / 今日校准"), FONTS["teams"], COLORS["ink"])
    inner_y += px(78)
    subtitle = reflection.get("subtitle", "")
    if subtitle:
        inner_y = draw_wrapped(draw, subtitle, inner_x, inner_y, inner_w, FONTS["small"], fill=COLORS["muted"], gap=8) + px(28)

    for section in reflection.get("sections", []):
        inner_y = draw_section(draw, section.get("title", ""), inner_x, inner_y, inner_w)
        for item in section.get("items", []):
            inner_y = draw_wrapped(draw, "• " + item, inner_x, inner_y, inner_w, FONTS["body"], fill=COLORS["ink"], gap=8) + px(12)
        inner_y += px(10)

    footer = f"worldcup-match-predictor | {datetime.now().strftime('%Y-%m-%d %H:%M')} | 复盘用于校准，不等于赛果保证"
    draw_text(draw, (inner_x, card_y + card_h - px(42)), footer, FONTS["tiny"], COLORS["muted"])

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "00_赛后复盘_今日校准.png"
    img.save(out_path, dpi=(OUTPUT_DPI, OUTPUT_DPI))
    return out_path


def render_match_png(data, match, index, output_dir):
    probe = Image.new("RGB", (WIDTH, px(100)), COLORS["bg"])
    probe_draw = ImageDraw.Draw(probe)
    metrics = layout_metrics(probe_draw, match)

    header_h = px(122)
    card_w = WIDTH - MARGIN * 2
    card_y = MARGIN + header_h + px(28)
    card_h = int(metrics["card_h"])
    height = card_y + card_h + MARGIN

    img = Image.new("RGB", (WIDTH, height), COLORS["bg"])
    draw = ImageDraw.Draw(img)
    x = MARGIN
    y = MARGIN

    draw.rounded_rectangle((x, y, x + card_w, y + header_h), radius=CARD_RADIUS, fill=COLORS["blue"])
    draw_text(draw, (x + px(34), y + px(24)), "世界杯比分预测与投注参考", FONTS["title"], "#ffffff")
    draw_text(draw, (x + px(36), y + px(78)), data.get("date", ""), FONTS["date"], "#dbeafe")
    draw_text(draw, (x + card_w - px(240), y + px(78)), f"第 {index:02d} 场", FONTS["date"], "#dbeafe")

    draw.rounded_rectangle((x, card_y, x + card_w, card_y + card_h), radius=CARD_RADIUS, fill=COLORS["panel"])
    inner_x = x + px(38)
    inner_y = card_y + px(34)
    inner_w = metrics["inner_w"]
    left_w = metrics["left_w"]
    right_w = metrics["right_w"]

    title = f"{match.get('home', '?')} vs {match.get('away', '?')}"
    draw_text(draw, (inner_x, inner_y), title, FONTS["teams"], COLORS["ink"])
    group = match.get("group", "")
    if group:
        badge_w = text_size(draw, group, FONTS["small"])[0] + px(42)
        draw_badge(draw, group, x + card_w - badge_w - px(38), inner_y + px(10), COLORS["purple_soft"], fg=COLORS["purple"])
    inner_y += px(78)

    inner_y = draw_wrapped(
        draw,
        f"北京 {match.get('time_bj', '-')} | 当地 {match.get('time_local', '-')} | {match.get('venue', '')}",
        inner_x,
        inner_y,
        inner_w,
        FONTS["small"],
        fill=COLORS["muted"],
        gap=8,
    ) + px(24)

    strip_h = px(150)
    draw.rounded_rectangle((inner_x, inner_y, inner_x + inner_w, inner_y + strip_h), radius=px(22), fill=COLORS["blue_soft"], outline="#bdd0f5", width=px(2))
    draw_text(draw, (inner_x + px(28), inner_y + px(32)), "预测比分", FONTS["section"], COLORS["blue_dark"])
    draw_text(draw, (inner_x + px(205), inner_y + px(16)), match.get("predicted_score", "-"), FONTS["score"], COLORS["red"])
    if match.get("alt_score"):
        draw_text(draw, (inner_x + px(470), inner_y + px(50)), f"次选 {match.get('alt_score')}", FONTS["body"], COLORS["muted"])
    risk = match.get("risk", "观望")
    conf = match.get("confidence", "-")
    draw_badge(draw, risk, inner_x + inner_w - px(210), inner_y + px(28), risk_color(risk), text_font=FONTS["small"])
    draw_text(draw, (inner_x + inner_w - px(210), inner_y + px(88)), f"置信度 {conf}", FONTS["small"], conf_color(conf))
    inner_y += strip_h + px(24)

    prob = match.get("probability", {})
    prob_text = f"胜平负概率：主胜 {prob.get('win', '-')} | 平 {prob.get('draw', '-')} | 客胜 {prob.get('lose', '-')}"
    inner_y = draw_wrapped(draw, prob_text, inner_x, inner_y, inner_w, FONTS["body"], fill=COLORS["ink"]) + px(6)
    inner_y = draw_wrapped(draw, "总进球预测：" + match.get("total_goals", "-"), inner_x, inner_y, inner_w, FONTS["body"], fill=COLORS["blue"]) + px(24)
    knockout = knockout_summary(match)
    if knockout:
        inner_y = draw_wrapped(draw, "淘汰赛预测：" + knockout, inner_x, inner_y - px(18), inner_w, FONTS["body"], fill=COLORS["green"]) + px(24)

    left_x = inner_x
    right_x = inner_x + left_w + GAP
    columns_y = inner_y

    left_y = draw_section(draw, "关键分析", left_x, columns_y, left_w)
    for factor in match.get("key_factors", []):
        left_y = draw_wrapped(draw, "• " + factor, left_x, left_y, left_w, FONTS["body"], fill=COLORS["ink"], gap=8) + px(10)
    for title, items in match_extra_sections(match):
        left_y += px(14)
        draw_text(draw, (left_x, left_y), title, FONTS["small"], COLORS["blue"])
        left_y += px(36)
        draw.line((left_x, left_y, left_x + left_w, left_y), fill=COLORS["line"], width=px(1))
        left_y += px(14)
        for item in items:
            left_y = draw_wrapped(draw, "• " + item, left_x, left_y, left_w, FONTS["small"], fill=COLORS["ink"], gap=8) + px(8)

    right_y = draw_section(draw, "竞彩赔率 / 建议", right_x, columns_y, right_w)
    odds = match.get("odds", {})
    right_y = draw_odds_table(draw, odds, right_x, right_y, right_w) + px(22)
    right_y = draw_wrapped(draw, "总进球数：" + (odds.get("goals") or "-"), right_x, right_y, right_w, FONTS["small"], fill=COLORS["muted"], gap=8) + px(10)
    right_y = draw_wrapped(draw, "比分/波胆：" + (odds.get("score") or "-"), right_x, right_y, right_w, FONTS["small"], fill=COLORS["muted"], gap=8) + px(24)

    advice_top = right_y
    draw_text(draw, (right_x, right_y), "投注建议", FONTS["section"], COLORS["green"])
    right_y += px(46)
    advice_y = draw_wrapped(draw, match.get("bet_advice", "-"), right_x + px(18), right_y + px(16), right_w - px(36), FONTS["body"], fill=COLORS["ink"], gap=9)
    right_y = advice_y + px(20)
    draw.rounded_rectangle((right_x, advice_top + px(40), right_x + right_w, right_y), radius=px(18), outline="#95e7b2", width=px(2))

    footer = f"worldcup-match-predictor | {datetime.now().strftime('%Y-%m-%d %H:%M')} | 投注有风险，仅供参考"
    draw_text(draw, (inner_x, card_y + card_h - px(42)), footer, FONTS["tiny"], COLORS["muted"])

    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{index:02d}_{safe_filename(match.get('home', 'home'))}_vs_{safe_filename(match.get('away', 'away'))}.png"
    out_path = output_dir / filename
    img.save(out_path, dpi=(OUTPUT_DPI, OUTPUT_DPI))
    return out_path


def main():
    if len(sys.argv) < 3:
        print("用法: python generate_report.py <data.json> <output_dir>")
        sys.exit(1)

    with open(sys.argv[1], encoding="utf-8") as f:
        data = json.load(f)

    output_dir = Path(sys.argv[2])
    matches = data.get("matches", [])
    if not matches:
        print("未发现 matches，未生成 PNG。")
        return

    generated = []
    reflection = data.get("reflection")
    if reflection:
        generated.append(render_reflection_png(data, reflection, output_dir))
    for idx, match in enumerate(matches, start=1):
        generated.append(render_match_png(data, match, idx, output_dir))

    print(f"已生成 {len(generated)} 张 PNG 到: {output_dir}")
    for path in generated:
        print(path)


if __name__ == "__main__":
    main()
