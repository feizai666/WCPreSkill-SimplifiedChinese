# WCPreSkill-SimplifiedChinese

世界杯赛事比分预测 Skill（简体中文）—— A World Cup match score prediction skill for Box AI.

## 简介

`worldcup-match-predictor` 是一个用于预测世界杯比赛比分并给出投注参考的 Box AI 技能。给定日期（默认北京时间次日）后，它会：

1. **确定赛程** —— 搜索并交叉印证当天的世界杯比赛。
2. **多维信息收集** —— 球队近半年国际赛事成绩、历史交锋、打法风格、球员状态、停赛/累计黄牌风险、小组出线形势、当地开球时间与气候等。
3. **综合分析建模** —— 估算双方综合状态分，给出预测比分、胜平负概率与置信度。
4. **赔率抓取与投注建议** —— 用 Playwright 抓取北京单场（北单）与竞彩的实时胜平负赔率，做价值对比，输出仅针对世界杯比赛的下注参考。
5. **输出报告** —— 多场比赛时生成自包含 HTML 报告。

## 目录结构

```
worldcup-match-predictor/
├── SKILL.md                          # 技能主指令
├── references/
│   └── prediction-framework.md       # 预测方法论与权重框架
└── scripts/
    ├── generate_report.py            # HTML 报告生成脚本
    └── sample_data.json              # 示例数据
```

## 使用方式

将本技能安装到 Box AI，然后说"预测明天的世界杯比赛"即可触发；也可由定时任务每天自动调用。

## ⚠️ 免责声明

本技能的所有预测与投注建议均基于公开信息与启发式分析，**仅供参考，存在不确定性，不构成任何盈利保证**。投注有风险，请理性娱乐。

## License

MIT
