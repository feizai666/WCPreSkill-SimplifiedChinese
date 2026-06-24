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


WIDTH = 1240
MARGIN = 54
CARD_RADIUS = 24
LINE_GAP = 9


COLORS = {
    "bg": "#eef2f7",
    "panel": "#ffffff",
    "ink": "#172033",
    "muted": "#64748b",
    "line": "#dbe4ef",
    "blue": "#1d4ed8",
    "blue_soft": "#dbeafe",
    "green": "#16a34a",
    "green_soft": "#dcfce7",
    "amber": "#d97706",
    "amber_soft": "#fef3c7",
    "red": "#dc2626",
    "red_soft": "#fee2e2",
    "purple": "#6d28d9",
    "purple_soft": "#ede9fe",
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


def font(size, bold=False):
    path = FONT_PATH
    if not path:
        return ImageFont.load_default()
    return ImageFont.truetype(path, size)


FONTS = {
    "title": font(38, True),
    "teams": font(40, True),
    "score": font(58, True),
    "section": font(25, True),
    "body": font(23),
    "small": font(20),
    "tiny": font(18),
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
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def summary_brief(summary):
    text = strip_markup(summary)
    if "信息源" in text:
        text = text.split("信息源", 1)[0].strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[:4]


def text_size(draw, text, text_font):
    box = draw.textbbox((0, 0), text, font=text_font)
    return box[2] - box[0], box[3] - box[1]


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


def draw_wrapped(draw, text, x, y, max_width, text_font, fill=COLORS["ink"], line_gap=LINE_GAP):
    lines = wrap_text(draw, text, text_font, max_width)
    line_h = text_size(draw, "国", text_font)[1] + line_gap
    for line in lines:
        draw.text((x, y), line, font=text_font, fill=fill)
        y += line_h
    return y


def draw_badge(draw, text, x, y, fill, fg="#ffffff", pad_x=16, pad_y=7, text_font=None):
    text_font = text_font or FONTS["small"]
    w, h = text_size(draw, text, text_font)
    rect = (x, y, x + w + pad_x * 2, y + h + pad_y * 2)
    draw.rounded_rectangle(rect, radius=16, fill=fill)
    draw.text((x + pad_x, y + pad_y - 1), text, font=text_font, fill=fg)
    return rect[2]


def draw_section(draw, title, x, y, width):
    draw.text((x, y), title, font=FONTS["section"], fill=COLORS["blue"])
    y += 34
    draw.line((x, y, x + width, y), fill=COLORS["line"], width=2)
    return y + 14


def draw_odds_table(draw, odds, x, y, width):
    spf = odds.get("spf") or {}
    rspf = odds.get("rspf") or {}
    rows = []
    rows.append(("胜平负", spf.get("win", "-"), spf.get("draw", "-"), spf.get("lose", "-")))
    handicap = rspf.get("handicap", "")
    label = f"让球胜平负({handicap})" if handicap else "让球胜平负"
    rows.append((label, rspf.get("win", "-"), rspf.get("draw", "-"), rspf.get("lose", "-")))

    col_w = [width * 0.34, width * 0.22, width * 0.22, width * 0.22]
    headers = ["玩法", "胜", "平", "负"]
    row_h = 42
    draw.rounded_rectangle((x, y, x + width, y + row_h * 3), radius=12, fill="#f8fafc", outline=COLORS["line"], width=2)
    cur_x = x
    for i, header in enumerate(headers):
        draw.text((cur_x + 14, y + 10), header, font=FONTS["small"], fill=COLORS["muted"])
        cur_x += col_w[i]
        if i < len(headers) - 1:
            draw.line((cur_x, y, cur_x, y + row_h * 3), fill=COLORS["line"], width=1)
    draw.line((x, y + row_h, x + width, y + row_h), fill=COLORS["line"], width=1)
    draw.line((x, y + row_h * 2, x + width, y + row_h * 2), fill=COLORS["line"], width=1)

    for r, row in enumerate(rows, start=1):
        cur_x = x
        for i, value in enumerate(row):
            draw.text((cur_x + 14, y + row_h * r + 10), str(value), font=FONTS["small"], fill=COLORS["ink"])
            cur_x += col_w[i]
    return y + row_h * 3


def safe_filename(text):
    text = re.sub(r"[\\/:*?\"<>|]+", "_", text)
    text = re.sub(r"\s+", "_", text)
    return text.strip("_")


def calc_height(data, match):
    probe = Image.new("RGB", (WIDTH, 100), COLORS["bg"])
    draw = ImageDraw.Draw(probe)
    width = WIDTH - MARGIN * 2
    y = 0
    y += 165
    for line in summary_brief(data.get("summary", "")):
        y += len(wrap_text(draw, line, FONTS["small"], width - 36)) * 30 + 6
    if data.get("summary"):
        y += 28
    y += 185
    y += len(wrap_text(draw, match.get("total_goals", ""), FONTS["body"], width - 40)) * 34
    y += 70
    for factor in match.get("key_factors", []):
        y += len(wrap_text(draw, "• " + factor, FONTS["body"], width - 38)) * 32 + 8
    y += 70
    y += 150
    y += len(wrap_text(draw, match.get("odds", {}).get("goals", ""), FONTS["small"], width - 40)) * 30
    y += len(wrap_text(draw, match.get("odds", {}).get("score", ""), FONTS["small"], width - 40)) * 30 + 40
    y += len(wrap_text(draw, match.get("bet_advice", ""), FONTS["body"], width - 44)) * 34 + 90
    # Keep generous slack for CJK wrapping differences between measurement and final draw.
    return max(1850, y + MARGIN + 320)


def render_match_png(data, match, index, output_dir):
    height = calc_height(data, match)
    img = Image.new("RGB", (WIDTH, height), COLORS["bg"])
    draw = ImageDraw.Draw(img)
    width = WIDTH - MARGIN * 2
    x = MARGIN
    y = MARGIN

    # Header
    draw.rounded_rectangle((x, y, x + width, y + 112), radius=CARD_RADIUS, fill=COLORS["blue"])
    draw.text((x + 28, y + 24), "世界杯比分预测与投注参考", font=FONTS["title"], fill="#ffffff")
    draw.text((x + 30, y + 72), data.get("date", ""), font=FONTS["small"], fill="#dbeafe")
    draw.text((x + width - 250, y + 72), f"第 {index:02d} 场", font=FONTS["small"], fill="#dbeafe")
    y += 136

    # Summary calibration block
    brief = summary_brief(data.get("summary", ""))
    if brief:
        block_start = y
        inner_x = x + 22
        y += 18
        draw.text((inner_x, y), "赛后复盘 / 今日校准", font=FONTS["section"], fill=COLORS["amber"])
        y += 38
        for line in brief:
            y = draw_wrapped(draw, line, inner_x, y, width - 44, FONTS["small"], fill=COLORS["ink"], line_gap=8) + 4
        draw.rounded_rectangle((x, block_start, x + width, y + 10), radius=18, outline="#f59e0b", width=2, fill=None)
        y += 32

    # Match panel
    panel_top = y
    draw.rounded_rectangle((x, panel_top, x + width, height - MARGIN), radius=CARD_RADIUS, fill=COLORS["panel"])
    y += 28
    inner_x = x + 30
    inner_w = width - 60

    title = f"{match.get('home', '?')} vs {match.get('away', '?')}"
    draw.text((inner_x, y), title, font=FONTS["teams"], fill=COLORS["ink"])
    group = match.get("group", "")
    if group:
        badge_w = text_size(draw, group, FONTS["small"])[0] + 32
        draw_badge(draw, group, x + width - badge_w - 28, y + 8, COLORS["purple_soft"], fg=COLORS["purple"], text_font=FONTS["small"])
    y += 58
    y = draw_wrapped(draw, f"北京 {match.get('time_bj', '-')} | 当地 {match.get('time_local', '-')} | {match.get('venue', '')}",
                     inner_x, y, inner_w, FONTS["small"], fill=COLORS["muted"], line_gap=7) + 16

    # Score strip
    strip_top = y
    draw.rounded_rectangle((inner_x, strip_top, inner_x + inner_w, strip_top + 126), radius=18, fill="#f8fafc", outline=COLORS["line"], width=2)
    draw.text((inner_x + 22, strip_top + 22), "预测比分", font=FONTS["section"], fill=COLORS["muted"])
    draw.text((inner_x + 150, strip_top + 10), match.get("predicted_score", "-"), font=FONTS["score"], fill=COLORS["red"])
    alt = match.get("alt_score")
    if alt:
        draw.text((inner_x + 360, strip_top + 34), f"次选 {alt}", font=FONTS["body"], fill=COLORS["muted"])
    risk = match.get("risk", "观望")
    conf = match.get("confidence", "-")
    draw_badge(draw, risk, inner_x + inner_w - 170, strip_top + 20, risk_color(risk), text_font=FONTS["small"])
    draw.text((inner_x + inner_w - 170, strip_top + 74), f"置信度 {conf}", font=FONTS["small"], fill=conf_color(conf))
    y = strip_top + 150

    prob = match.get("probability", {})
    prob_text = f"胜平负概率：主胜 {prob.get('win', '-')} | 平 {prob.get('draw', '-')} | 客胜 {prob.get('lose', '-')}"
    y = draw_wrapped(draw, prob_text, inner_x, y, inner_w, FONTS["body"], fill=COLORS["ink"]) + 6
    y = draw_wrapped(draw, "总进球预测：" + match.get("total_goals", "-"), inner_x, y, inner_w, FONTS["body"], fill=COLORS["blue"]) + 20

    y = draw_section(draw, "关键分析", inner_x, y, inner_w)
    for factor in match.get("key_factors", []):
        y = draw_wrapped(draw, "• " + factor, inner_x, y, inner_w, FONTS["body"], fill=COLORS["ink"], line_gap=8) + 8
    y += 10

    y = draw_section(draw, "竞彩赔率", inner_x, y, inner_w)
    odds = match.get("odds", {})
    y = draw_odds_table(draw, odds, inner_x, y, inner_w) + 18
    y = draw_wrapped(draw, "总进球数：" + (odds.get("goals") or "-"), inner_x, y, inner_w, FONTS["small"], fill=COLORS["muted"]) + 8
    y = draw_wrapped(draw, "比分/波胆：" + (odds.get("score") or "-"), inner_x, y, inner_w, FONTS["small"], fill=COLORS["muted"]) + 22

    y = draw_section(draw, "投注建议", inner_x, y, inner_w)
    advice_top = y
    y = draw_wrapped(draw, match.get("bet_advice", "-"), inner_x + 18, y + 16, inner_w - 36, FONTS["body"], fill=COLORS["ink"]) + 18
    draw.rounded_rectangle((inner_x, advice_top, inner_x + inner_w, y), radius=16, outline="#bbf7d0", width=2, fill=None)
    y += 26

    footer = f"由 worldcup-match-predictor 生成 | {datetime.now().strftime('%Y-%m-%d %H:%M')} | 投注有风险，仅供参考"
    draw.text((inner_x, height - MARGIN - 34), footer, font=FONTS["tiny"], fill=COLORS["muted"])

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
