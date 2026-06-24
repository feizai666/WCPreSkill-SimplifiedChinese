#!/usr/bin/env python3
"""生成世界杯比分预测 PNG 报告卡片。

用法：
  python generate_report.py <data.json> <output_dir>

data.json 结构示例见同目录 sample_data.json。
脚本仅做渲染，不含预测逻辑——预测数据由 skill 分析后写入 JSON。

输出规则：
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


WIDTH = 1600
MARGIN = 56
GAP = 36
CARD_RADIUS = 30

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


def find_font():
    candidates = [
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    return None


FONT_PATH = find_font()


def font(size):
    if not FONT_PATH:
        return ImageFont.load_default()
    return ImageFont.truetype(FONT_PATH, size)


FONTS = {
    "title": font(46),
    "date": font(28),
    "teams": font(58),
    "score": font(92),
    "section": font(34),
    "body": font(30),
    "small": font(25),
    "tiny": font(21),
}


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


def text_size(draw, text, text_font):
    box = draw.textbbox((0, 0), str(text), font=text_font)
    return box[2] - box[0], box[3] - box[1]


def line_height(draw, text_font, gap=9):
    return text_size(draw, "国", text_font)[1] + gap


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
        draw.text((x, y), line, font=text_font, fill=fill)
        y += step
    return y


def draw_badge(draw, text, x, y, fill, fg="#ffffff", pad_x=18, pad_y=8, text_font=None):
    text_font = text_font or FONTS["small"]
    w, h = text_size(draw, text, text_font)
    rect = (x, y, x + w + pad_x * 2, y + h + pad_y * 2)
    draw.rounded_rectangle(rect, radius=18, fill=fill)
    draw.text((x + pad_x, y + pad_y - 1), text, font=text_font, fill=fg)
    return rect[2]


def draw_section(draw, title, x, y, width):
    draw.text((x, y), title, font=FONTS["section"], fill=COLORS["blue"])
    y += 42
    draw.line((x, y, x + width, y), fill=COLORS["line"], width=2)
    return y + 18


def draw_odds_table(draw, odds, x, y, width):
    spf = odds.get("spf") or {}
    rspf = odds.get("rspf") or {}
    handicap = rspf.get("handicap", "")
    rows = [
        ("胜平负", spf.get("win", "-"), spf.get("draw", "-"), spf.get("lose", "-")),
        (f"让球({handicap})" if handicap else "让球", rspf.get("win", "-"), rspf.get("draw", "-"), rspf.get("lose", "-")),
    ]

    row_h = 52
    headers = ["玩法", "胜", "平", "负"]
    col_w = [width * 0.42, width * 0.19, width * 0.19, width * 0.20]

    draw.rounded_rectangle((x, y, x + width, y + row_h * 3), radius=16, fill="#f8fbff", outline=COLORS["line"], width=2)
    cur_x = x
    for i, header in enumerate(headers):
        draw.text((cur_x + 16, y + 14), header, font=FONTS["small"], fill=COLORS["muted"])
        cur_x += col_w[i]
        if i < 3:
            draw.line((cur_x, y, cur_x, y + row_h * 3), fill=COLORS["line"], width=1)
    draw.line((x, y + row_h, x + width, y + row_h), fill=COLORS["line"], width=1)
    draw.line((x, y + row_h * 2, x + width, y + row_h * 2), fill=COLORS["line"], width=1)

    for r, row in enumerate(rows, start=1):
        cur_x = x
        for i, value in enumerate(row):
            draw.text((cur_x + 16, y + row_h * r + 14), str(value), font=FONTS["small"], fill=COLORS["ink"])
            cur_x += col_w[i]
    return y + row_h * 3


def safe_filename(text):
    text = re.sub(r"[\\/:*?\"<>|]+", "_", text)
    text = re.sub(r"\s+", "_", text)
    return text.strip("_")


def layout_metrics(draw, match):
    card_w = WIDTH - MARGIN * 2
    inner_w = card_w - 76
    left_w = int(inner_w * 0.58)
    right_w = inner_w - left_w - GAP

    y = 0
    y += 78
    time_h, _ = measure_wrapped(
        draw,
        f"北京 {match.get('time_bj', '-')} | 当地 {match.get('time_local', '-')} | {match.get('venue', '')}",
        inner_w,
        FONTS["small"],
        8,
    )
    y += time_h + 24
    y += 164
    prob = match.get("probability", {})
    prob_text = f"胜平负概率：主胜 {prob.get('win', '-')} | 平 {prob.get('draw', '-')} | 客胜 {prob.get('lose', '-')}"
    prob_h, _ = measure_wrapped(draw, prob_text, inner_w, FONTS["body"], 9)
    goals_h, _ = measure_wrapped(draw, "总进球预测：" + match.get("total_goals", "-"), inner_w, FONTS["body"], 9)
    y += prob_h + goals_h + 28

    left_y = 60
    for factor in match.get("key_factors", []):
        h, _ = measure_wrapped(draw, "• " + factor, left_w, FONTS["body"], 8)
        left_y += h + 10

    right_y = 60 + 156 + 22
    odds = match.get("odds", {})
    h, _ = measure_wrapped(draw, "总进球数：" + (odds.get("goals") or "-"), right_w, FONTS["small"], 8)
    right_y += h + 10
    h, _ = measure_wrapped(draw, "比分/波胆：" + (odds.get("score") or "-"), right_w, FONTS["small"], 8)
    right_y += h + 28
    h, _ = measure_wrapped(draw, match.get("bet_advice", "-"), right_w - 34, FONTS["body"], 9)
    right_y += 60 + h + 42

    content_h = y + max(left_y, right_y) + 72
    return {
        "card_h": content_h,
        "inner_w": inner_w,
        "left_w": left_w,
        "right_w": right_w,
        "top_after_intro": y,
    }


def render_match_png(data, match, index, output_dir):
    probe = Image.new("RGB", (WIDTH, 100), COLORS["bg"])
    probe_draw = ImageDraw.Draw(probe)
    metrics = layout_metrics(probe_draw, match)

    header_h = 122
    card_w = WIDTH - MARGIN * 2
    card_y = MARGIN + header_h + 28
    card_h = int(metrics["card_h"])
    height = card_y + card_h + MARGIN

    img = Image.new("RGB", (WIDTH, height), COLORS["bg"])
    draw = ImageDraw.Draw(img)
    x = MARGIN
    y = MARGIN

    draw.rounded_rectangle((x, y, x + card_w, y + header_h), radius=CARD_RADIUS, fill=COLORS["blue"])
    draw.text((x + 34, y + 24), "世界杯比分预测与投注参考", font=FONTS["title"], fill="#ffffff")
    draw.text((x + 36, y + 78), data.get("date", ""), font=FONTS["date"], fill="#dbeafe")
    draw.text((x + card_w - 240, y + 78), f"第 {index:02d} 场", font=FONTS["date"], fill="#dbeafe")

    draw.rounded_rectangle((x, card_y, x + card_w, card_y + card_h), radius=CARD_RADIUS, fill=COLORS["panel"])
    inner_x = x + 38
    inner_y = card_y + 34
    inner_w = metrics["inner_w"]
    left_w = metrics["left_w"]
    right_w = metrics["right_w"]

    title = f"{match.get('home', '?')} vs {match.get('away', '?')}"
    draw.text((inner_x, inner_y), title, font=FONTS["teams"], fill=COLORS["ink"])
    group = match.get("group", "")
    if group:
        badge_w = text_size(draw, group, FONTS["small"])[0] + 42
        draw_badge(draw, group, x + card_w - badge_w - 38, inner_y + 10, COLORS["purple_soft"], fg=COLORS["purple"])
    inner_y += 78

    inner_y = draw_wrapped(
        draw,
        f"北京 {match.get('time_bj', '-')} | 当地 {match.get('time_local', '-')} | {match.get('venue', '')}",
        inner_x,
        inner_y,
        inner_w,
        FONTS["small"],
        fill=COLORS["muted"],
        gap=8,
    ) + 24

    strip_h = 150
    draw.rounded_rectangle((inner_x, inner_y, inner_x + inner_w, inner_y + strip_h), radius=22, fill=COLORS["blue_soft"], outline="#bdd0f5", width=2)
    draw.text((inner_x + 28, inner_y + 32), "预测比分", font=FONTS["section"], fill=COLORS["blue_dark"])
    draw.text((inner_x + 205, inner_y + 16), match.get("predicted_score", "-"), font=FONTS["score"], fill=COLORS["red"])
    if match.get("alt_score"):
        draw.text((inner_x + 470, inner_y + 50), f"次选 {match.get('alt_score')}", font=FONTS["body"], fill=COLORS["muted"])
    risk = match.get("risk", "观望")
    conf = match.get("confidence", "-")
    draw_badge(draw, risk, inner_x + inner_w - 210, inner_y + 28, risk_color(risk), text_font=FONTS["small"])
    draw.text((inner_x + inner_w - 210, inner_y + 88), f"置信度 {conf}", font=FONTS["small"], fill=conf_color(conf))
    inner_y += strip_h + 24

    prob = match.get("probability", {})
    prob_text = f"胜平负概率：主胜 {prob.get('win', '-')} | 平 {prob.get('draw', '-')} | 客胜 {prob.get('lose', '-')}"
    inner_y = draw_wrapped(draw, prob_text, inner_x, inner_y, inner_w, FONTS["body"], fill=COLORS["ink"]) + 6
    inner_y = draw_wrapped(draw, "总进球预测：" + match.get("total_goals", "-"), inner_x, inner_y, inner_w, FONTS["body"], fill=COLORS["blue"]) + 24

    left_x = inner_x
    right_x = inner_x + left_w + GAP
    columns_y = inner_y

    left_y = draw_section(draw, "关键分析", left_x, columns_y, left_w)
    for factor in match.get("key_factors", []):
        left_y = draw_wrapped(draw, "• " + factor, left_x, left_y, left_w, FONTS["body"], fill=COLORS["ink"], gap=8) + 10

    right_y = draw_section(draw, "竞彩赔率 / 建议", right_x, columns_y, right_w)
    odds = match.get("odds", {})
    right_y = draw_odds_table(draw, odds, right_x, right_y, right_w) + 22
    right_y = draw_wrapped(draw, "总进球数：" + (odds.get("goals") or "-"), right_x, right_y, right_w, FONTS["small"], fill=COLORS["muted"], gap=8) + 10
    right_y = draw_wrapped(draw, "比分/波胆：" + (odds.get("score") or "-"), right_x, right_y, right_w, FONTS["small"], fill=COLORS["muted"], gap=8) + 24

    advice_top = right_y
    draw.text((right_x, right_y), "投注建议", font=FONTS["section"], fill=COLORS["green"])
    right_y += 46
    advice_y = draw_wrapped(draw, match.get("bet_advice", "-"), right_x + 18, right_y + 16, right_w - 36, FONTS["body"], fill=COLORS["ink"], gap=9)
    right_y = advice_y + 20
    draw.rounded_rectangle((right_x, advice_top + 40, right_x + right_w, right_y), radius=18, outline="#95e7b2", width=2)

    footer = f"worldcup-match-predictor | {datetime.now().strftime('%Y-%m-%d %H:%M')} | 投注有风险，仅供参考"
    draw.text((inner_x, card_y + card_h - 42), footer, font=FONTS["tiny"], fill=COLORS["muted"])

    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{index:02d}_{safe_filename(match.get('home', 'home'))}_vs_{safe_filename(match.get('away', 'away'))}.png"
    out_path = output_dir / filename
    img.save(out_path)
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
    for idx, match in enumerate(matches, start=1):
        generated.append(render_match_png(data, match, idx, output_dir))

    print(f"已生成 {len(generated)} 张 PNG 到: {output_dir}")
    for path in generated:
        print(path)


if __name__ == "__main__":
    main()
