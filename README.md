# WCPreSkill-SimplifiedChinese

世界杯赛事比分预测 Skill（简体中文）——用于生成世界杯预测 PNG 卡片的 Codex skill / 本地报告工作流。

## 简介

`worldcup-match-predictor` 是一个面向世界杯比赛的中文预测 skill。给定日期（默认北京时间次日）后，它会先复盘前一比赛日，再生成当天每场比赛的 PNG 预测卡片和结构化 JSON 数据。

主要能力：

1. **赛后复盘校准** —— 对历史预测和真实赛果做误差归因，生成 `00_赛后复盘_今日校准.png`。
2. **阵容台账更新** —— 先生成 `data/rosters/YYYY-MM-DD/` CSV 台账，记录最终名单、补招替换、伤停、预计缺席、停赛和最近可用性。
3. **赛程与赛事情境分析** —— 按北京时间确定目标日期，检查小组积分、淘汰赛路径、轮换动机、出线/晋级压力。
4. **多维信息收集** —— 结合球队状态、历史交锋、打法风格、球员伤停、黄牌停赛、天气场地、主裁判执法尺度、主教练临场调整和替补后手。
5. **比分与晋级建模** —— 小组赛输出 90 分钟预测比分；淘汰赛会拆分常规时间比分、加时/点球概率和最终晋级判断。
6. **欧洲盘口赔率对比** —— 使用 API-Football 结构化赔率，Bet365 做完整主盘口，Pinnacle 做 sharp line 校准，覆盖全场独赢胜平负、让球胜平负、全场大小和波胆。
7. **PNG 报告输出** —— 为每场比赛生成高清 PNG 卡片，中文宋体、英文和数字 Times New Roman，并写入 300 DPI 元数据。

报告里提到球员、主教练、裁判等人名时，会在名字后标注三字母国家/队伍简称，例如 `Davies（CAN）`、`Hugo Broos（RSA）`、`João Pinheiro（POR）`，避免读者混淆归属。

## 目录结构

```
worldcup-match-predictor/
├── SKILL.md                          # 技能主指令
├── references/
│   ├── capability-boundaries.md      # 脚本/LLM/人工/API 能力边界
│   └── prediction-framework.md       # 预测方法论与权重框架
└── scripts/
    ├── generate_report.py            # PNG 报告卡片生成脚本
    ├── update_rosters.py             # 阵容台账 CSV 生成脚本
    ├── network_fetch_audit.py        # 赛前新闻 URL 正文抽取与日志审计脚本
    ├── api_football_odds.py          # API-Football 欧洲盘口抓取与标准化脚本
    ├── prepare_prediction_inputs.py  # 全链路自动准备赛程/新闻/赔率输入
    ├── sporttery_odds.js             # 竞彩官方动态页抓取与解析脚本（旧工具）
    └── sample_data.json              # 示例数据
```

## 使用方式

可以将 `worldcup-match-predictor/` 作为 Codex skill 使用，也可以直接在本仓库中按 `SKILL.md` 的流程准备 JSON 数据并渲染 PNG。

能力边界：

- 脚本只负责抓取、过滤、校验、标准化、日志和渲染。
- LLM 负责证据权衡、冲突处理、比分、概率、风险和投注建议。
- `prediction_input.json` 是输入包，不是最终预测。
- `availability_candidates.json/csv` 是候选线索，不是确认伤停。
- `main_lines`、`calibration_deltas`、`value_signals` 是市场信号，不是投注结论。

详细协议见 `worldcup-match-predictor/references/capability-boundaries.md`。

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

阵容台账更新：

```bash
python worldcup-match-predictor/scripts/update_rosters.py \
  --date 2026-07-02
```

脚本优先使用 ESPN content API 读取最终名单正文；如果 API 与 HTML 页面都不可用，会回退到仓库中最近一个可解析的 `source_espn_squads.txt` 缓存，并在写入前校验球队数和行数，避免空抓取覆盖已有台账。

输出到 `data/rosters/YYYY-MM-DD/`：

- `all_rosters.csv`：全量球队名单台账。
- `teams/<TEAM_CODE>.csv`：单队名单台账，例如 `teams/ENG.csv`。
- `availability_overrides.csv`：当天基于官方赛前稿、新闻和发布会维护的伤停、停赛、补招和名单冲突覆盖表。

赛前新闻正文抓取（默认使用 Tavily Extract）：

```bash
python worldcup-match-predictor/scripts/network_fetch_audit.py \
  --availability-csv data/rosters/2026-07-04/availability_overrides.csv \
  --out-dir /tmp/worldcup_network_sources
```

脚本会读取项目根目录 `.env` 中的 `TAVILY_API_KEY`，对 `availability_overrides.csv` 里的 `source_url` 做 Tavily Extract，输出：

- `network_events.jsonl`：逐 URL 的抓取方式、成功状态、正文长度、球员命中、伤停关键词命中和错误信息。
- `network_summary.json`：本轮抓取成功率、正文总量、证据片段总量、球员命中率、Tavily credits 等摘要。
- `prediction_source_bundle.json`：供后续预测 JSON 编写使用的紧凑证据包，只保留 URL、命中球员、关键词和相关原文片段。
- `contents/*.md`：每个 URL 抽取后的 Markdown 正文。
- `snippets/*.md`：每个 URL 裁剪后的相关证据片段，避免把整篇文章直接喂给模型。

默认会分批调用 Tavily 并重试失败批次；若单个 URL 仍失败，会用 `local-http` 做诊断兜底，并把 Tavily 错误写入日志。如需复现旧方案或做 A/B 对照，可显式传入 `--scheme local-http`。

可用 Tavily Search 发现候选新闻 URL：

```bash
python worldcup-match-predictor/scripts/network_fetch_audit.py \
  --availability-csv data/rosters/2026-07-04/availability_overrides.csv \
  --out-dir /tmp/worldcup_network_sources \
  --discover-query 'Argentina Cape Verde World Cup team news injuries suspensions predicted lineups' \
  --extract-discovered
```

候选 URL 会按 source quality 自动过滤和排序，写入 `discovered_sources.json`；加 `--extract-discovered` 后会直接抽取合格 URL，不需要人工先筛进 `availability_overrides.csv`。脚本还会输出 `availability_candidates.json/csv`，把证据片段转成机器可读的候选伤停/停赛状态。Tavily 只作为赛前新闻/媒体文章正文抽取主路径；基础名单仍优先使用 ESPN content API，赔率使用 API-Football 结构化接口。

全链路自动准备输入：

```bash
python worldcup-match-predictor/scripts/prepare_prediction_inputs.py \
  --date 2026-07-05 \
  --season 2026 \
  --out-dir logs/pipeline/2026-07-05
```

该脚本会自动完成赛程发现、Tavily 新闻发现与抽取、候选可用性生成、API-Football 欧赔抓取，并输出：

- `fixtures_api_window.json`：API-Football 前后日期窗口原始赛程，用于审计跨 UTC/北京时间边界。
- `fixtures.json`
- `odds_by_fixture.json`
- `network/discovered_sources.json`
- `network/prediction_source_bundle.json`
- `network/availability_candidates.json`
- `network/<fixture_id>/discovered_sources.json`
- `network/<fixture_id>/prediction_source_bundle.json`
- `network/<fixture_id>/availability_candidates.json`
- `prediction_input.json`

`prediction_input.json` 是后续写预测 JSON 和渲染 PNG 的统一输入；其中 `manual_action_required` 固定为 `false`，中间步骤不再要求人工筛 URL 或拼赔率。脚本会先用 API-Football 的 UTC 日期窗口补齐凌晨比赛，再按目标北京时间过滤 `fixtures.json`；新闻、候选伤停和 source refs 按 `fixture_id` 单独生成，避免把不同比赛的球员与新闻混在一起。

默认情况下，若 `data/rosters/YYYY-MM-DD/all_rosters.csv` 不存在，脚本会先自动运行 `update_rosters.py` 创建 roster snapshot；调试时可传 `--no-update-rosters` 跳过。

欧洲盘口赔率抓取：

```bash
python worldcup-match-predictor/scripts/api_football_odds.py \
  --date 2026-07-05 \
  --home Canada \
  --away Morocco \
  --season 2026 \
  --out /tmp/api_football_odds.json
```

脚本会读取项目根目录 `.env` 中的 `API_FOOTBALL_KEY`（也兼容 `APISPORTS_KEY`、`API_SPORTS_KEY`、`FOOTBALL_API_KEY`），调用 API-Football 的 fixtures 与 odds 接口。日期解析会检查目标日期前后一天，避免北京时间凌晨比赛落在 UTC 前一天；全链路准备脚本会在拿到窗口结果后再按北京时间过滤。

输出标准化 JSON：

- `primary`：Bet365 主盘口，必须覆盖 `match_winner`、`handicap_result`、`goals_over_under`、`exact_score`。
- `calibration`：Pinnacle 校准盘口；若缺少某些市场，只写入 warning，不影响 Bet365 主路径。
- `calibration_deltas`：Bet365 与 Pinnacle 共有市场的赔率差。
- `warnings`：缺失盘口、校准缺口等问题。

如已知 fixture ID，可直接指定：

```bash
python worldcup-match-predictor/scripts/api_football_odds.py \
  --fixture-id 1567824 \
  --out /tmp/api_football_odds.json
```

旧竞彩赔率抓取（legacy 诊断工具，非主路径）：

```bash
node worldcup-match-predictor/scripts/sporttery_odds.js \
  --matches '澳大利亚:埃及,阿根廷:佛得角,哥伦比亚:加纳' \
  --out /tmp/sporttery_odds.json
```

该脚本会用 Playwright 抓取中国体育彩票竞彩官方动态页面，并解析胜平负、让球胜平负、总进球数和比分赔率。如果 Playwright 自带浏览器未安装，脚本会尝试使用本机已安装的 Chrome/Chromium/Edge。可通过 `PLAYWRIGHT_CHROME_EXECUTABLE` 指定浏览器路径。正常预测管线不依赖该脚本；赔率主路径只使用 API-Football 的 Bet365 主盘口与 Pinnacle 校准。

## 数据与报告口径

- 日期口径统一使用北京时间。
- 生成比赛卡前必须先更新阵容台账；未入选、已被替换、伤缺、停赛或来源冲突未确认的球员不得写成首发、替补后手或点球手。
- 自动新闻证据按比赛隔离；`availability_candidates` 是候选线索，不是确认伤停，不能跨比赛复用。
- 天气和裁判以结构化字段给出获取状态；`未核验` 或 `未公布` 时必须在报告里保留不确定性。
- 已知赛前新闻 URL 的正文抽取默认使用 Tavily Extract；普通搜索仍用于发现 URL，动态结构化页面不走 Tavily。
- 赔率主路径使用 API-Football 欧洲盘口：Bet365 完整主盘口，Pinnacle 校准；体彩竞彩仅作为旧工具或人工对照。
- 淘汰赛卡片中，`预测比分`默认指常规时间比分；最终晋级判断单独写在淘汰赛预测区块。
- 如果主裁判尚未公布，报告必须明确标注不确定，不得编造裁判姓名。
- 预测报告是分析与娱乐参考，不应被理解为确定赛果。

## ⚠️ 免责声明

本技能的所有预测与投注建议均基于公开信息与启发式分析，**仅供参考，存在不确定性，不构成任何盈利保证**。投注有风险，请理性娱乐。

## License

MIT
