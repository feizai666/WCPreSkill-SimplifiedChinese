#!/usr/bin/env python3
"""生成世界杯比分预测 HTML 报告。

用法：
  python generate_report.py <data.json> <output.html>

data.json 结构示例见同目录 sample_data.json。
脚本仅做渲染，不含预测逻辑——预测数据由 skill 分析后写入 JSON。
"""
import json
import sys
from datetime import datetime


def conf_color(level):
    return {"高": "#16a34a", "中": "#d97706", "低": "#dc2626"}.get(level, "#6b7280")


def risk_badge(level):
    color = {"稳胆": "#16a34a", "博胆": "#d97706", "高风险": "#dc2626",
             "观望": "#6b7280"}.get(level, "#6b7280")
    return f'<span class="badge" style="background:{color}">{level}</span>'


def render_match(m):
    odds = m.get("odds", {})
    spf = odds.get("spf", {})        # 不让球胜平负 {win,draw,lose}
    rspf = odds.get("rspf", {})      # 让球胜平负 {handicap,win,draw,lose}
    goals = odds.get("goals", "")    # 总进球数赔率描述
    score_odds = odds.get("score", "")  # 比分(波胆)最看好档描述

    def spf_row(name, o, extra=""):
        if not o:
            return f'<tr><td>{name}</td><td colspan="3" class="muted">暂无</td></tr>'
        return (f'<tr><td>{name}{extra}</td><td>{o.get("win","-")}</td>'
                f'<td>{o.get("draw","-")}</td><td>{o.get("lose","-")}</td></tr>')

    factors = "".join(f"<li>{f}</li>" for f in m.get("key_factors", []))
    prob = m.get("probability", {})
    handicap = rspf.get("handicap", "")
    rspf_label = f"（让{handicap}）" if handicap else ""
    return f"""
    <div class="match">
      <div class="match-head">
        <div class="teams">{m.get('home','?')} <span class="vs">vs</span> {m.get('away','?')}</div>
        <div class="group">{m.get('group','')}</div>
      </div>
      <div class="time">🕐 北京 {m.get('time_bj','-')} ｜ 当地 {m.get('time_local','-')} ｜ {m.get('venue','')}</div>
      <div class="score">预测比分：<b>{m.get('predicted_score','-')}</b>
        <span class="alt">{('次选 ' + m['alt_score']) if m.get('alt_score') else ''}</span></div>
      <div class="goals">⚽ 总进球预测：<b>{m.get('total_goals','-')}</b></div>
      <div class="prob">主胜 {prob.get('win','-')} ｜ 平 {prob.get('draw','-')} ｜ 客胜 {prob.get('lose','-')}
        ｜ 置信度 <b style="color:{conf_color(m.get('confidence',''))}">{m.get('confidence','-')}</b></div>
      <div class="section-title">关键分析</div>
      <ul class="factors">{factors}</ul>
      <div class="section-title">竞彩赔率（胜/平/负）</div>
      <table class="odds">
        <tr><th>玩法</th><th>胜</th><th>平</th><th>负</th></tr>
        {spf_row("胜平负", spf)}
        {spf_row("让球胜平负", rspf, extra=rspf_label)}
      </table>
      <div class="extra-odds">
        <div>🥅 总进球数（竞彩）：{goals or '—'}</div>
        <div>🎯 比分/波胆（市场最看好）：{score_odds or '—'}</div>
      </div>
      <div class="bet">💡 投注建议：{m.get('bet_advice','-')} {risk_badge(m.get('risk','观望'))}</div>
    </div>"""


def main():
    if len(sys.argv) < 3:
        print("用法: python generate_report.py <data.json> <output.html>")
        sys.exit(1)
    with open(sys.argv[1], encoding="utf-8") as f:
        data = json.load(f)

    matches = "".join(render_match(m) for m in data.get("matches", []))
    date = data.get("date", datetime.now().strftime("%Y-%m-%d"))
    summary = data.get("summary", "")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>世界杯比分预测 · {date}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,'PingFang SC',sans-serif;background:#f1f5f9;color:#1e293b;line-height:1.6;padding:24px}}
.wrap{{max-width:860px;margin:0 auto}}
header{{background:linear-gradient(135deg,#7c3aed,#2563eb);color:#fff;padding:28px;border-radius:16px;margin-bottom:20px}}
header h1{{font-size:24px}} header .date{{opacity:.9;margin-top:6px}}
.disclaimer{{background:#fef3c7;border:1px solid #fcd34d;color:#92400e;padding:12px 16px;border-radius:10px;font-size:13px;margin-bottom:20px}}
.summary{{background:#fff;border-radius:14px;padding:18px 20px;margin-bottom:20px;border-left:4px solid #2563eb}}
.match{{background:#fff;border-radius:14px;padding:20px;margin-bottom:18px;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
.match-head{{display:flex;justify-content:space-between;align-items:center}}
.teams{{font-size:19px;font-weight:700}} .vs{{color:#94a3b8;font-size:14px;margin:0 6px}}
.group{{background:#ede9fe;color:#6d28d9;padding:3px 10px;border-radius:20px;font-size:12px}}
.time{{color:#64748b;font-size:13px;margin:8px 0}}
.score{{font-size:16px;margin:6px 0}} .score b{{color:#dc2626;font-size:20px}} .alt{{color:#94a3b8;font-size:13px;margin-left:8px}}
.goals{{font-size:14px;margin:4px 0;color:#0369a1}} .goals b{{color:#0369a1}}
.prob{{font-size:13px;color:#475569;margin-bottom:10px}}
.section-title{{font-size:13px;font-weight:700;color:#334155;margin:12px 0 6px;border-bottom:1px solid #e2e8f0;padding-bottom:4px}}
.factors{{padding-left:20px;font-size:13px;color:#475569}} .factors li{{margin:3px 0}}
table.odds{{width:100%;border-collapse:collapse;font-size:13px;margin-top:4px}}
table.odds th,table.odds td{{border:1px solid #e2e8f0;padding:6px 10px;text-align:center}}
table.odds th{{background:#f8fafc}} .muted{{color:#94a3b8}}
.extra-odds{{margin-top:8px;font-size:13px;color:#475569;background:#f8fafc;border-radius:8px;padding:8px 12px}} .extra-odds div{{margin:3px 0}}
.bet{{margin-top:12px;background:#f0fdf4;border:1px solid #bbf7d0;padding:10px 14px;border-radius:10px;font-size:14px}}
.badge{{color:#fff;padding:2px 10px;border-radius:20px;font-size:12px;margin-left:6px}}
footer{{text-align:center;color:#94a3b8;font-size:12px;margin-top:24px}}
</style></head>
<body><div class="wrap">
<header><h1>⚽ 世界杯比分预测与投注参考</h1><div class="date">{date}</div></header>
<div class="disclaimer">⚠️ 本报告基于公开信息与启发式分析，所有预测与投注建议仅供参考，存在不确定性，不构成盈利保证。投注有风险，请理性娱乐。</div>
{f'<div class="summary"><b>今日综述</b><br>{summary}</div>' if summary else ''}
{matches}
<footer>由 worldcup-match-predictor 生成 · {datetime.now().strftime('%Y-%m-%d %H:%M')}</footer>
</div></body></html>"""

    with open(sys.argv[2], "w", encoding="utf-8") as f:
        f.write(html)
    print(f"报告已生成: {sys.argv[2]}")


if __name__ == "__main__":
    main()
