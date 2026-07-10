#!/usr/bin/env python3
"""Validate and aggregate Monte multi-agent football forecasts.

The script reads a report JSON, finds ``multi_agent_analysis`` on each match,
and writes deterministic consensus probabilities and disagreement metrics.
It never invents narrative conclusions; those remain the adjudicator's job.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path


OUTCOMES = ("home", "draw", "away")
OTHER_SCORE_KEYS = {
    "home_other": "home",
    "draw_other": "draw",
    "away_other": "away",
}
SCORE_RE = re.compile(r"^(\d+)\s*[-:]\s*(\d+)$")
PROBABILITY_TOLERANCE = 0.005


class ConsensusError(ValueError):
    """Raised when a panel submission violates the aggregation contract."""


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConsensusError(f"{label} 必须是数字")
    value = float(value)
    if not math.isfinite(value):
        raise ConsensusError(f"{label} 必须是有限数字")
    return value


def _probability(value, label):
    value = _number(value, label)
    if not 0.0 <= value <= 1.0:
        raise ConsensusError(f"{label} 必须在 0 到 1 之间")
    return value


def _validate_distribution(raw, keys, label):
    if not isinstance(raw, dict):
        raise ConsensusError(f"{label} 必须是对象")
    distribution = {
        key: _probability(raw.get(key), f"{label}.{key}") for key in keys
    }
    total = sum(distribution.values())
    if abs(total - 1.0) > PROBABILITY_TOLERANCE:
        raise ConsensusError(f"{label} 合计为 {total:.4f}，必须接近 1")
    if total <= 0:
        raise ConsensusError(f"{label} 合计必须大于 0")
    return {key: value / total for key, value in distribution.items()}


def _canonical_score_key(score_key):
    if score_key in OTHER_SCORE_KEYS:
        return score_key
    match = SCORE_RE.match(score_key)
    if not match:
        raise ConsensusError(f"无法识别比分键: {score_key}")
    home_goals, away_goals = (int(value) for value in match.groups())
    return f"{home_goals}-{away_goals}"


def _score_outcome(score_key):
    if score_key in OTHER_SCORE_KEYS:
        return OTHER_SCORE_KEYS[score_key]
    match = SCORE_RE.match(score_key)
    if not match:
        raise ConsensusError(f"无法识别比分键: {score_key}")
    home_goals, away_goals = (int(value) for value in match.groups())
    if home_goals > away_goals:
        return "home"
    if home_goals < away_goals:
        return "away"
    return "draw"


def _validate_score_grid(raw, p90, label):
    if not isinstance(raw, dict) or not raw:
        raise ConsensusError(f"{label} 必须是非空对象")

    grid = {}
    for raw_key, raw_value in raw.items():
        raw_key = str(raw_key).strip()
        key = _canonical_score_key(raw_key)
        value = _probability(raw_value, f"{label}.{raw_key}")
        grid[key] = grid.get(key, 0.0) + value

    total = sum(grid.values())
    if abs(total - 1.0) > PROBABILITY_TOLERANCE:
        raise ConsensusError(f"{label} 合计为 {total:.4f}，必须接近 1")
    if total <= 0:
        raise ConsensusError(f"{label} 合计必须大于 0")
    grid = {key: value / total for key, value in grid.items() if value > 0}

    derived = {outcome: 0.0 for outcome in OUTCOMES}
    for key, value in grid.items():
        derived[_score_outcome(key)] += value
    for outcome in OUTCOMES:
        gap = abs(derived[outcome] - p90[outcome])
        if gap > PROBABILITY_TOLERANCE:
            raise ConsensusError(
                f"{label} 反推 {outcome}={derived[outcome]:.4f}，"
                f"与 p90={p90[outcome]:.4f} 相差超过 0.5 个百分点"
            )
    return grid


def _final_prediction(agent, label):
    initial = agent.get("initial")
    if not isinstance(initial, dict):
        raise ConsensusError(f"{label} 缺少 initial 盲审预测")
    initial_p90 = _validate_distribution(
        initial.get("p90"), OUTCOMES, f"{label}.initial.p90"
    )
    initial_grid = _validate_score_grid(
        initial.get("score_grid"), initial_p90, f"{label}.initial.score_grid"
    )

    final = agent.get("final")
    if not isinstance(final, dict):
        raise ConsensusError(f"{label} 缺少 final 修订结果")
    revision_reason = str(final.get("revision_reason", "")).strip()
    if not revision_reason:
        raise ConsensusError(f"{label}.final 缺少 revision_reason")
    if final.get("unchanged") is True:
        prediction = dict(initial)
        prediction["revision_reason"] = revision_reason
        return prediction, initial_p90, initial_grid

    prediction = final
    p90 = _validate_distribution(prediction.get("p90"), OUTCOMES, f"{label}.p90")
    score_grid = _validate_score_grid(
        prediction.get("score_grid"), p90, f"{label}.score_grid"
    )
    return prediction, p90, score_grid


def _validate_references(analysis, agent_ids):
    for index, challenge in enumerate(analysis.get("challenges", [])):
        if not isinstance(challenge, dict):
            raise ConsensusError(f"challenges[{index}] 必须是对象")
        for key in ("from", "to"):
            if challenge.get(key) not in agent_ids:
                raise ConsensusError(
                    f"challenges[{index}].{key} 引用了未知 agent: {challenge.get(key)}"
                )

    for index, revision in enumerate(analysis.get("revisions", [])):
        if not isinstance(revision, dict):
            raise ConsensusError(f"revisions[{index}] 必须是对象")
        if revision.get("agent") not in agent_ids:
            raise ConsensusError(
                f"revisions[{index}].agent 引用了未知 agent: {revision.get('agent')}"
            )

    for index, disagreement in enumerate(analysis.get("disagreements", [])):
        if not isinstance(disagreement, dict):
            raise ConsensusError(f"disagreements[{index}] 必须是对象")
        for position in disagreement.get("positions", []):
            if not isinstance(position, dict) or position.get("agent") not in agent_ids:
                raise ConsensusError(
                    f"disagreements[{index}] 包含未知 agent: {position}"
                )


def _weights(analysis, voters):
    mode = analysis.get("weighting_mode", "equal")
    if mode == "equal":
        return [1.0 / len(voters)] * len(voters)
    if mode != "historical":
        raise ConsensusError(f"未知 weighting_mode: {mode}")

    sample_size = analysis.get("history_sample_size", 0)
    if not isinstance(sample_size, int) or sample_size < 30:
        raise ConsensusError("historical 加权至少需要 30 场可结算样本")
    raw_weights = [_number(agent.get("weight", 0), f"{agent['id']}.weight") for agent in voters]
    if any(weight <= 0 for weight in raw_weights):
        raise ConsensusError("historical 权重必须大于 0")
    total = sum(raw_weights)
    weights = [weight / total for weight in raw_weights]
    if max(weights) > 0.350001:
        raise ConsensusError("单个 agent 的 historical 权重不得超过 35%")
    return weights


def _pool(distributions, weights):
    keys = sorted({key for distribution in distributions for key in distribution})
    return {
        key: sum(weight * distribution.get(key, 0.0) for distribution, weight in zip(distributions, weights))
        for key in keys
    }


def _kl_divergence(distribution, center):
    value = 0.0
    for key, probability in distribution.items():
        if probability <= 0:
            continue
        midpoint = center.get(key, 0.0)
        if midpoint <= 0:
            raise ConsensusError(f"JSD 中心分布缺少正概率键: {key}")
        value += probability * math.log(probability / midpoint)
    return value


def _weighted_jsd(distributions, weights, center, category_count):
    raw = sum(
        weight * _kl_divergence(distribution, center)
        for distribution, weight in zip(distributions, weights)
    )
    denominator = math.log(max(2, category_count))
    return raw / denominator if denominator else 0.0


def _entropy(distribution, category_count):
    raw = -sum(value * math.log(value) for value in distribution.values() if value > 0)
    denominator = math.log(max(2, category_count))
    return raw / denominator if denominator else 0.0


def _round_distribution(distribution):
    return {key: round(value, 6) for key, value in distribution.items()}


def _auto_display(analysis, voter_records, consensus, agreement):
    display = analysis.get("display")
    if not isinstance(display, dict) or not display.get("auto"):
        return

    view_parts = []
    for agent, prediction, _, _ in voter_records:
        lean = str(prediction.get("lean", "")).strip()
        if lean:
            view_parts.append(f"{agent.get('role', agent['id'])}：{lean}")

    p90 = consensus["p90"]
    top_scores = consensus["top_scores"][:5]
    items = []
    if view_parts:
        items.append("独立观点：" + "；".join(view_parts))
    items.append(
        "概率合议：主胜 {:.1%}，平 {:.1%}，客胜 {:.1%}；{}。".format(
            p90["home"], p90["draw"], p90["away"], agreement["level"]
        )
    )
    if top_scores:
        score_text = "、".join(
            f"{item['score']} {item['probability']:.1%}" for item in top_scores
        )
        items.append("波胆合议：" + score_text)

    adjudication = analysis.get("adjudication") or {}
    verdict = str(adjudication.get("verdict", "")).strip()
    minority = str(adjudication.get("minority_report", "")).strip()
    if verdict:
        items.append("裁决：" + verdict)
    if minority and len(items) < 4:
        items.append("少数意见：" + minority)

    display["enabled"] = True
    display["items"] = items[:4]


def aggregate_analysis(analysis):
    if not isinstance(analysis, dict):
        raise ConsensusError("multi_agent_analysis 必须是对象")
    agents = analysis.get("agents")
    if not isinstance(agents, list) or not agents:
        raise ConsensusError("multi_agent_analysis.agents 必须是非空数组")

    agent_ids = []
    for index, agent in enumerate(agents):
        if not isinstance(agent, dict):
            raise ConsensusError(f"agents[{index}] 必须是对象")
        agent_id = str(agent.get("id", "")).strip()
        if not agent_id:
            raise ConsensusError(f"agents[{index}] 缺少 id")
        if agent_id in agent_ids:
            raise ConsensusError(f"agent id 重复: {agent_id}")
        agent_ids.append(agent_id)

    _validate_references(analysis, set(agent_ids))
    voters = [agent for agent in agents if agent.get("voting") is True]
    if len(voters) < 2:
        raise ConsensusError("至少需要两个 voting=true 的 agent")
    weights = _weights(analysis, voters)

    voter_records = []
    for agent in voters:
        prediction, p90, score_grid = _final_prediction(agent, agent["id"])
        voter_records.append((agent, prediction, p90, score_grid))

    p90_distributions = [record[2] for record in voter_records]
    score_distributions = [record[3] for record in voter_records]
    p90_pool = {
        outcome: sum(
            weight * distribution[outcome]
            for distribution, weight in zip(p90_distributions, weights)
        )
        for outcome in OUTCOMES
    }
    score_pool = _pool(score_distributions, weights)

    consensus_outcome = max(OUTCOMES, key=p90_pool.get)
    modal_dissent = sum(
        weight
        for (_, _, p90, _), weight in zip(voter_records, weights)
        if max(OUTCOMES, key=p90.get) != consensus_outcome
    )
    max_spread = max(
        max(distribution[outcome] for distribution in p90_distributions)
        - min(distribution[outcome] for distribution in p90_distributions)
        for outcome in OUTCOMES
    )
    jsd_wdl = _weighted_jsd(p90_distributions, weights, p90_pool, len(OUTCOMES))

    exact_scores = {
        key: value for key, value in score_pool.items() if SCORE_RE.match(key)
    }
    top_scores = [
        {"score": score, "probability": round(probability, 6)}
        for score, probability in sorted(
            exact_scores.items(), key=lambda item: (-item[1], item[0])
        )[:10]
    ]
    top_score = top_scores[0]["score"] if top_scores else None
    score_mode_share = 0.0
    if top_score:
        for (_, _, _, grid), weight in zip(voter_records, weights):
            agent_exact = {key: value for key, value in grid.items() if SCORE_RE.match(key)}
            if agent_exact and max(agent_exact, key=agent_exact.get) == top_score:
                score_mode_share += weight

    score_keys = sorted(score_pool)
    score_jsd = _weighted_jsd(
        score_distributions, weights, score_pool, len(score_keys)
    )
    if jsd_wdl < 0.02 and max_spread < 0.10 and modal_dissent < 0.25:
        level = "低分歧"
    elif jsd_wdl > 0.06 or max_spread > 0.20 or modal_dissent >= 0.40:
        level = "高分歧"
    else:
        level = "中分歧"

    consensus = {
        "p90": _round_distribution(p90_pool),
        "score_grid": _round_distribution(score_pool),
        "top_scores": top_scores,
        "top_scores_mass": round(sum(item["probability"] for item in top_scores), 6),
        "predictive_entropy": round(_entropy(p90_pool, len(OUTCOMES)), 6),
    }
    agreement = {
        "level": level,
        "jsd_wdl": round(jsd_wdl, 6),
        "jsd_score": round(score_jsd, 6),
        "max_spread_pp": round(max_spread * 100, 2),
        "modal_dissent_weight": round(modal_dissent, 6),
        "score_mode_share": round(score_mode_share, 6),
    }
    analysis["effective_weights"] = {
        agent["id"]: round(weight, 6) for agent, weight in zip(voters, weights)
    }
    analysis["consensus"] = consensus
    analysis["agreement"] = agreement
    _auto_display(analysis, voter_records, consensus, agreement)
    return analysis


def _enforce_disagreement_guardrail(match, analysis):
    if analysis.get("agreement", {}).get("level") != "高分歧":
        return

    changes = {}
    if "稳" in str(match.get("risk", "")):
        changes["risk"] = {"before": match.get("risk"), "after": "高风险"}
        match["risk"] = "高风险"
    if match.get("confidence") == "高":
        changes["confidence"] = {"before": "高", "after": "低"}
        match["confidence"] = "低"

    adjudication = analysis.get("adjudication")
    if isinstance(adjudication, dict) and adjudication.get("confidence") == "高":
        changes["adjudication.confidence"] = {"before": "高", "after": "低"}
        adjudication["confidence"] = "低"
    if changes:
        analysis.setdefault("guardrails", []).append(
            {
                "rule": "high_disagreement_no_steady",
                "changes": changes,
            }
        )


def aggregate_report(data, require_panel=False):
    if not isinstance(data, dict):
        raise ConsensusError("报告根节点必须是对象")
    matches = data.get("matches", [])
    if not isinstance(matches, list):
        raise ConsensusError("matches 必须是数组")
    if require_panel and matches:
        schema_version = data.get("schema_version")
        if not isinstance(schema_version, int) or schema_version < 2:
            raise ConsensusError("严格 Monte 模式要求 schema_version >= 2")

    processed = 0
    for index, match in enumerate(matches):
        if not isinstance(match, dict):
            raise ConsensusError(f"matches[{index}] 必须是对象")
        analysis = match.get("multi_agent_analysis")
        if analysis is None:
            if require_panel:
                fixture = f"{match.get('home', '?')} vs {match.get('away', '?')}"
                raise ConsensusError(f"{fixture}: 缺少 multi_agent_analysis")
            continue
        try:
            aggregate_analysis(analysis)
        except ConsensusError as exc:
            fixture = f"{match.get('home', '?')} vs {match.get('away', '?')}"
            raise ConsensusError(f"{fixture}: {exc}") from exc
        _enforce_disagreement_guardrail(match, analysis)
        processed += 1
    return processed


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="包含 multi_agent_analysis 的报告 JSON")
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument("-o", "--output", type=Path, help="输出 JSON 路径")
    output_group.add_argument("--in-place", action="store_true", help="原子覆盖输入文件")
    parser.add_argument(
        "--require-panel",
        action="store_true",
        help="要求 schema_version >= 2 且每场比赛都有多智能体面板",
    )
    args = parser.parse_args(argv)

    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
        processed = aggregate_report(data, require_panel=args.require_panel)
    except (OSError, json.JSONDecodeError, ConsensusError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1

    if args.in_place:
        _write_json(args.input, data)
    elif args.output:
        _write_json(args.output, data)
    else:
        json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    print(f"已聚合 {processed} 场多智能体预测", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
