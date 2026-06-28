# WCPreSkill-SimplifiedChinese

世界杯赛事比分预测 Skill（简体中文）——用于生成世界杯预测 PNG 卡片的 Codex skill / 本地报告工作流。

## 简介

`worldcup-match-predictor` 是一个面向世界杯比赛的中文预测 skill。给定日期（默认北京时间次日）后，它会先复盘前一比赛日，再生成当天每场比赛的 PNG 预测卡片和结构化 JSON 数据。

主要能力：

1. **赛后复盘校准** —— 对历史预测和真实赛果做误差归因，生成 `00_赛后复盘_今日校准.png`。
2. **赛程与赛事情境分析** —— 按北京时间确定目标日期，检查小组积分、淘汰赛路径、轮换动机、出线/晋级压力。
3. **多维信息收集** —— 结合球队状态、历史交锋、打法风格、球员伤停、黄牌停赛、天气场地、主裁判执法尺度、主教练临场调整和替补后手。
4. **比分与晋级建模** —— 小组赛输出 90 分钟预测比分；淘汰赛会拆分常规时间比分、加时/点球概率和最终晋级判断。
5. **竞彩赔率对比** —— 只使用中国体育彩票竞彩官方赔率，抓取胜平负、让球胜平负、总进球数和比分/波胆，做投注参考。
6. **PNG 报告输出** —— 为每场比赛生成高清 PNG 卡片，中文宋体、英文和数字 Times New Roman，并写入 300 DPI 元数据。

报告里提到球员、主教练、裁判等人名时，会在名字后标注三字母国家/队伍简称，例如 `Davies（CAN）`、`Hugo Broos（RSA）`、`João Pinheiro（POR）`，避免读者混淆归属。

## 目录结构

```
worldcup-match-predictor/
├── SKILL.md                          # 技能主指令
├── references/
│   └── prediction-framework.md       # 预测方法论与权重框架
└── scripts/
    ├── generate_report.py            # PNG 报告卡片生成脚本
    └── sample_data.json              # 示例数据
```

## 使用方式

可以将 `worldcup-match-predictor/` 作为 Codex skill 使用，也可以直接在本仓库中按 `SKILL.md` 的流程准备 JSON 数据并渲染 PNG。

常见触发语义：

- `预测明天的世界杯比赛`
- `生成 2026-06-29 的报告`
- `今天赛果出来了，继续生成明天的卡片`

渲染脚本用法：

```bash
python worldcup-match-predictor/scripts/generate_report.py <data.json> reports/YYYY-MM-DD/
```

输出规则：

- 若 JSON 包含 `reflection`，会先生成 `00_赛后复盘_今日校准.png`。
- 每场比赛生成一张 PNG，命名为 `01_主队_vs_客队.png`、`02_主队_vs_客队.png` 等。
- 如果当天有 6 场比赛，通常会输出 7 张 PNG：1 张复盘卡 + 6 张比赛卡。
- 结构化 JSON 保存在 `reports/worldcup_YYYY-MM-DD_predictions.json`。

示例：

```bash
python worldcup-match-predictor/scripts/generate_report.py \
  reports/worldcup_2026-06-29_predictions.json \
  reports/2026-06-29/
```

## 数据与报告口径

- 日期口径统一使用北京时间。
- 赔率只使用中国体育彩票竞彩官方数据，不再抓取北单。
- 淘汰赛卡片中，`预测比分`默认指常规时间比分；最终晋级判断单独写在淘汰赛预测区块。
- 如果主裁判尚未公布，报告必须明确标注不确定，不得编造裁判姓名。
- 预测报告是分析与娱乐参考，不应被理解为确定赛果。

## ⚠️ 免责声明

本技能的所有预测与投注建议均基于公开信息与启发式分析，**仅供参考，存在不确定性，不构成任何盈利保证**。投注有风险，请理性娱乐。

## License

MIT
