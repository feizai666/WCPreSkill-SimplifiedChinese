# Capability Boundaries

This skill is an LLM-led football analysis workflow backed by deterministic data-preparation scripts. Scripts prepare, normalize, validate, and render data. The LLM performs the football judgment.

## Core Contract

1. Scripts provide evidence, structured inputs, market data, diagnostics, and rendered artifacts.
2. The LLM interprets evidence, resolves conflicts, estimates probabilities, writes predictions, and explains uncertainty.
3. Human intervention is not part of the normal data path. If an external service fails, the LLM reports the failure and uses the best available structured evidence.
4. No component may silently promote a weak data object into a confirmed fact.

## Data Status

| Data object | Produced by | Status | How the LLM may use it |
| --- | --- | --- | --- |
| `all_rosters.csv` | `update_rosters.py` | Roster snapshot after source and size validation | Treat as squad baseline unless newer evidence contradicts it. |
| `teams/<TEAM_CODE>.csv` | `update_rosters.py` | Team-specific roster snapshot | Use to block not-in-squad, replaced, injured, or suspended players from lineups and key roles. |
| `availability_candidates.json/csv` | `network_fetch_audit.py` or `prepare_prediction_inputs.py` | Candidate status extracted from snippets | Use as leads only. Confirm with evidence snippets before writing a player as out, suspended, fit, or doubtful. |
| `prediction_source_bundle.json` | `network_fetch_audit.py` | Evidence package with snippets and source quality | Use snippets as source-grounded evidence. Do not paste full articles into reasoning unless needed. |
| `discovered_sources.json` | `network_fetch_audit.py` | Ranked URL candidates | Use as source discovery output, not as evidence until extracted. |
| `network/<fixture_id>/...` | `prepare_prediction_inputs.py` | Per-fixture discovered sources, extracted snippets, and candidate availability | Prefer this match-scoped evidence over aggregate files. Do not mix candidates across fixtures. |
| `odds_by_fixture.json` | `prepare_prediction_inputs.py` | API-Football odds normalized by fixture | Use as market data. It is not a prediction by itself. |
| `primary.main_lines` | `api_football_odds.py` | Selected Bet365 display lines | Use as the main market view; do not treat it as a recommended bet. |
| `value_signals` | `api_football_odds.py` | Mechanical comparison of market odds against model probabilities when provided | Use as a diagnostic signal only. The LLM must decide final betting advice. |
| `prediction_input.json` | `prepare_prediction_inputs.py` | Unified input package | Primary handoff from scripts to LLM. It is not the final prediction report. |
| `weather` / `referee` fields | `prepare_prediction_inputs.py` | Structured fetch status and available data | Use when status is available; if status is `未核验` or `未公布`, keep uncertainty visible. |
| `reports/worldcup_*.json` | LLM-authored workflow | Final structured prediction source | Use for rendering and future calibration. |
| PNG report cards | `generate_report.py` | Rendered artifact | Presentation only. Do not infer new analysis from the image. |

## Script Responsibilities

### `update_rosters.py`

Provides roster snapshots.

Inputs:
- Target date.
- ESPN content API or valid cached source text.
- Optional availability overrides.

Outputs:
- `all_rosters.csv`
- `teams/<TEAM_CODE>.csv`
- `availability_overrides.csv`
- `source_espn_squads.txt`

Must:
- Reject empty or obviously incomplete source text.
- Avoid overwriting valid roster files with failed fetches.
- Mark baseline availability conservatively when daily news is not verified.

Must not:
- Predict lineups.
- Decide tactical importance.
- Convert uncertain news into confirmed absence.

### `network_fetch_audit.py`

Provides source discovery, extraction, evidence snippets, source quality, and candidate availability records.

Inputs:
- Known `availability_overrides.csv` URLs, Tavily discover queries, or both.
- Optional `--extract-discovered` for no-human source discovery and extraction.

Outputs:
- `discovered_sources.json`
- `network_events.jsonl`
- `network_summary.json`
- `prediction_source_bundle.json`
- `availability_candidates.json`
- `availability_candidates.csv`

Must:
- Prefer official and mainstream sources.
- Penalize low-quality betting SEO and prediction pages.
- Keep snippets small and relevant.
- Match player names conservatively, avoiding common first-name-only false positives and navigation boilerplate.
- Log failures and fallback paths.

Must not:
- Treat search snippets as verified facts.
- Treat `availability_candidates` as final player availability.
- Decide starting lineups, betting advice, scorelines, or probabilities.

### `api_football_odds.py`

Provides structured European market data.

Inputs:
- Fixture ID or date plus teams.
- API-Football key.

Outputs:
- Normalized odds JSON with Bet365 primary market, Pinnacle calibration, `main_lines`, `calibration_deltas`, and optional `value_signals`.

Must:
- Require Bet365 coverage for `Match Winner`, `Handicap Result`, `Goals Over/Under`, and `Exact Score`.
- Treat Pinnacle as calibration only; missing `Handicap Result` is a warning, not a primary failure.
- Keep `Handicap Result` separate from `Asian Handicap`.

Must not:
- Recommend bets.
- Infer line movement from one snapshot.
- Turn market odds into match probabilities without an explicit model assumption.

### `prepare_prediction_inputs.py`

Provides the automated script-to-LLM handoff.

Inputs:
- Target date.
- API-Football key.
- Tavily key unless `--skip-network` is used.
- Roster root.

Outputs:
- `fixtures_api_window.json`
- `fixtures.json`
- `odds_by_fixture.json`
- `network/discovered_sources.json`
- `network/prediction_source_bundle.json`
- `network/availability_candidates.json`
- `network/<fixture_id>/discovered_sources.json`
- `network/<fixture_id>/prediction_source_bundle.json`
- `network/<fixture_id>/availability_candidates.json`
- `prediction_input.json`

Must:
- Auto-create roster snapshot if missing.
- Query the API-Football UTC date window, then filter final fixtures back to the target Beijing date.
- Auto-discover and extract news sources when network mode is enabled.
- Keep source discovery, extracted snippets, and availability candidates scoped to each fixture.
- Attach only the two fixture teams' roster players to that fixture's source targets.
- Exclude manager rows from player matching.
- Add structured referee status from API-Football and structured weather status from Open-Meteo when available.
- Set `manual_action_required` to `false` for normal successful preparation.

Must not:
- Fill final prediction fields such as score, probability, key factors, or betting advice.
- Hide missing API keys or upstream failures.

### `generate_report.py`

Provides rendering only.

Inputs:
- Final LLM-authored prediction JSON.

Outputs:
- PNG report cards.

Must:
- Render the supplied structured prediction faithfully.
- Support current API-Football odds format and legacy odds fields.

Must not:
- Add new analysis.
- Change predictions, risks, odds, or player status.

### `sporttery_odds.js`

Legacy diagnostic tool only.

Must:
- Stay outside the primary odds path.
- Be treated as an optional manual comparison tool if explicitly invoked.

Must not:
- Replace API-Football Bet365/Pinnacle odds in normal prediction input preparation.
- Be required for automated pipeline success.

## LLM Responsibilities

The LLM must:

1. Run or consume `prepare_prediction_inputs.py` before analysis when possible.
2. Read `prediction_input.json`, `prediction_source_bundle.json`, roster CSVs, and odds JSON as inputs.
3. Treat `availability_candidates` as leads. Confirm status from evidence snippets and roster rows before asserting an absence or availability.
4. Treat `main_lines`, `calibration_deltas`, and `value_signals` as market signals, not betting advice.
5. Resolve conflicts explicitly. If evidence conflicts, write the uncertainty instead of choosing a side silently.
6. Generate the final prediction fields: predicted score, alternate score, probability, total goals, key factors, confidence, card risk, coach/bench analysis, and betting advice.
7. Mark unverified or unpublished referee, weather, lineup, or injury information as uncertain.
8. Ensure final JSON can be rendered by `generate_report.py`.

The LLM must not:

1. Invent raw data that scripts failed to fetch.
2. Promote one low-quality source into a confirmed fact.
3. Treat odds as deterministic truth.
4. Claim line movement from a single odds snapshot.
5. Put players in lineups or tactical roles when roster or evidence marks them out, suspended, replaced, or source-conflicted.
6. Ask the user to manually screen URLs, manually enter odds, or manually assemble intermediate JSON during normal operation.

## Human Role

The user may request overrides, review outputs, or provide private information. Normal operation must not require the user to:

- Find source URLs.
- Copy article text.
- Fill odds.
- Build `availability_overrides.csv`.
- Assemble `prediction_input.json`.

If the user supplies overrides, the LLM must keep them visible as user-provided inputs, not silently merge them into verified public evidence.

## Failure Handling

When a script fails:

1. Preserve the failed command, error summary, and missing data category.
2. Use cached validated data only if the script marks it valid.
3. Continue analysis only when the missing data does not make the prediction misleading.
4. State uncertainty in the final report.

When API-Football odds are missing:

1. Do not fabricate odds.
2. Analyze football fundamentals without betting-value claims, or mark betting advice unavailable.

When Tavily or source extraction fails:

1. Use already extracted snippets and roster data.
2. Do not present unextracted search results as confirmed evidence.

## Normal Flow

1. Confirm target Beijing date.
2. Run `prepare_prediction_inputs.py`.
3. Inspect `prediction_input.json`.
4. Use match-scoped evidence snippets, roster rows, weather/referee status, and odds JSON to generate final prediction JSON.
5. Run `generate_report.py`.
6. Report files, key uncertainties, and failed data sources.
