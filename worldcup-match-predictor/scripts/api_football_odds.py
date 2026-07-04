#!/usr/bin/env python3
"""Fetch and normalize API-Football odds for World Cup predictions."""

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


API_HOST = "https://v3.football.api-sports.io"
DEFAULT_API_KEY_ENVS = ("API_FOOTBALL_KEY", "APISPORTS_KEY", "API_SPORTS_KEY", "FOOTBALL_API_KEY")
PRIMARY_BOOKMAKER = "Bet365"
CALIBRATION_BOOKMAKER = "Pinnacle"

REQUIRED_MARKETS = {
    "match_winner": "Match Winner",
    "handicap_result": "Handicap Result",
    "goals_over_under": "Goals Over/Under",
    "exact_score": "Exact Score",
}

OPTIONAL_MARKETS = {
    "asian_handicap": "Asian Handicap",
}


def repo_root():
    return Path(__file__).resolve().parents[2]


def load_dotenv(env_path=None):
    path = Path(env_path) if env_path else repo_root() / ".env"
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def require_api_key(env_names=None):
    for env_name in env_names or DEFAULT_API_KEY_ENVS:
        value = os.environ.get(env_name)
        if value:
            return value
    names = ", ".join(env_names or DEFAULT_API_KEY_ENVS)
    raise SystemExit(f"Missing API-Football key. Set one of: {names}")


def http_get_json(path, params=None, api_key=None, timeout=30, host=API_HOST):
    query = urllib.parse.urlencode(params or {})
    url = f"{host}{path}"
    if query:
        url = f"{url}?{query}"
    request = urllib.request.Request(
        url,
        headers={
            "x-apisports-key": api_key or require_api_key(),
            "Accept": "application/json",
        },
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"API-Football HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"API-Football request failed: {exc.reason}") from exc
    payload = json.loads(body)
    payload["_elapsed_ms"] = int((time.monotonic() - started) * 1000)
    return payload


def normalize_name(value):
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def team_matches(actual, expected):
    actual_norm = normalize_name(actual)
    expected_norm = normalize_name(expected)
    return actual_norm == expected_norm or expected_norm in actual_norm or actual_norm in expected_norm


def date_window(date_text):
    target = dt.date.fromisoformat(date_text)
    return [(target + dt.timedelta(days=offset)).isoformat() for offset in (-1, 0, 1)]


def resolve_fixture(api_key, date, home, away, league=1, season=None, timeout=30):
    candidates = []
    for api_date in date_window(date):
        payload = http_get_json("/fixtures", params={"date": api_date}, api_key=api_key, timeout=timeout)
        for row in payload.get("response") or []:
            row_league = row.get("league") or {}
            if league and row_league.get("id") != league:
                continue
            if season and row_league.get("season") != season:
                continue
            teams = row.get("teams") or {}
            home_team = (teams.get("home") or {}).get("name", "")
            away_team = (teams.get("away") or {}).get("name", "")
            direct = team_matches(home_team, home) and team_matches(away_team, away)
            reverse = team_matches(home_team, away) and team_matches(away_team, home)
            if direct or reverse:
                candidates.append(
                    {
                        "fixture": row.get("fixture") or {},
                        "league": row.get("league") or {},
                        "teams": teams,
                        "api_date": api_date,
                        "reversed": reverse,
                    }
                )
    if not candidates:
        raise SystemExit(f"No fixture found for {home} vs {away} near {date}.")
    if len(candidates) > 1:
        ids = ", ".join(str((item.get("fixture") or {}).get("id")) for item in candidates)
        raise SystemExit(f"Multiple matching fixtures found: {ids}. Use --fixture-id.")
    return candidates[0]


def bookmaker_by_name(bookmakers, name):
    target = normalize_name(name)
    for bookmaker in bookmakers or []:
        if normalize_name(bookmaker.get("name")) == target:
            return bookmaker
    return None


def clean_values(values):
    cleaned = []
    for value in values or []:
        if "value" not in value or "odd" not in value:
            continue
        cleaned.append({"value": str(value["value"]), "odd": str(value["odd"])})
    return cleaned


def odd_as_float(value):
    try:
        return float(value.get("odd"))
    except (TypeError, ValueError):
        return float("inf")


def canonical_markets(bookmaker, top_n=8):
    by_name = {bet.get("name"): bet for bet in bookmaker.get("bets") or []}
    markets = {}
    for key, market_name in {**REQUIRED_MARKETS, **OPTIONAL_MARKETS}.items():
        bet = by_name.get(market_name)
        if not bet:
            continue
        values = clean_values(bet.get("values"))
        markets[key] = {
            "api_name": market_name,
            "values": values,
            "top_values": sorted(values, key=odd_as_float)[:top_n],
            "value_count": len(values),
        }
    return markets


def coverage(markets, required=None):
    required = required or REQUIRED_MARKETS
    missing = [key for key in required if key not in markets or not markets[key].get("values")]
    return {"complete": not missing, "missing": missing}


def compare_market(primary_market, calibration_market):
    calibration_by_value = {item["value"]: item for item in calibration_market.get("values") or []}
    deltas = []
    for item in primary_market.get("values") or []:
        peer = calibration_by_value.get(item["value"])
        if not peer:
            continue
        try:
            primary_odd = float(item["odd"])
            calibration_odd = float(peer["odd"])
        except ValueError:
            continue
        deltas.append(
            {
                "value": item["value"],
                "primary_odd": item["odd"],
                "calibration_odd": peer["odd"],
                "odd_delta": round(primary_odd - calibration_odd, 4),
            }
        )
    return deltas


def parse_line_value(value, prefix):
    match = re.match(rf"^{re.escape(prefix)}\s+([+-]?\d+(?:\.\d+)?)$", value or "")
    return match.group(1) if match else None


def grouped_two_way_line(values, over_prefix="Over", under_prefix="Under"):
    grouped = {}
    for item in values or []:
        line = parse_line_value(item.get("value"), over_prefix) or parse_line_value(item.get("value"), under_prefix)
        if not line:
            continue
        side = over_prefix if item["value"].startswith(over_prefix) else under_prefix
        grouped.setdefault(line, {})[side] = item.get("odd")
    return grouped


def choose_goals_main_line(market):
    values = market.get("values") or []
    grouped = grouped_two_way_line(values)
    if "2.5" in grouped and {"Over", "Under"}.issubset(grouped["2.5"]):
        return {"line": "2.5", "over": grouped["2.5"]["Over"], "under": grouped["2.5"]["Under"], "selection_rule": "prefer_2.5"}
    complete = []
    for line, odds in grouped.items():
        if {"Over", "Under"}.issubset(odds):
            try:
                spread = abs(float(odds["Over"]) - float(odds["Under"]))
            except (TypeError, ValueError):
                spread = 99.0
            complete.append((spread, line, odds))
    if not complete:
        return {}
    _, line, odds = sorted(complete)[0]
    return {"line": line, "over": odds["Over"], "under": odds["Under"], "selection_rule": "balanced_pair"}


def parse_handicap_value(value):
    match = re.match(r"^(Home|Draw|Away)\s+([+-]?\d+(?:\.\d+)?)$", value or "")
    if not match:
        return None
    return match.group(1), match.group(2)


def choose_handicap_result_main_line(market):
    grouped = {}
    for item in market.get("values") or []:
        parsed = parse_handicap_value(item.get("value"))
        if not parsed:
            continue
        side, line = parsed
        grouped.setdefault(line, {})[side] = item.get("odd")
    complete = []
    for line, odds in grouped.items():
        if {"Home", "Draw", "Away"}.issubset(odds):
            try:
                spread = max(float(value) for value in odds.values()) - min(float(value) for value in odds.values())
            except (TypeError, ValueError):
                spread = 99.0
            complete.append((spread, line, odds))
    if not complete:
        return {}
    _, line, odds = sorted(complete)[0]
    return {"line": line, "home": odds["Home"], "draw": odds["Draw"], "away": odds["Away"], "selection_rule": "balanced_three_way"}


def match_winner_value_signals(market, model_probabilities=None):
    probabilities = model_probabilities or {}
    mapping = {"Home": "home", "Draw": "draw", "Away": "away"}
    signals = []
    for item in market.get("values") or []:
        value = item.get("value")
        probability = probabilities.get(mapping.get(value, ""))
        if probability is None or probability <= 0:
            continue
        try:
            market_odd = float(item.get("odd"))
        except (TypeError, ValueError):
            continue
        fair_odd = 1 / probability
        signals.append(
            {
                "value": value,
                "model_probability": probability,
                "fair_odd": round(fair_odd, 4),
                "market_odd": item.get("odd"),
                "value_ratio": round(market_odd / fair_odd, 4),
                "has_value": market_odd > fair_odd,
            }
        )
    return sorted(signals, key=lambda item: item["value_ratio"], reverse=True)


def main_lines(markets, model_probabilities=None):
    lines = {}
    if "match_winner" in markets:
        signals = match_winner_value_signals(markets["match_winner"], model_probabilities)
        lines["match_winner"] = {
            "values": markets["match_winner"].get("values", []),
            "best_value": signals[0]["value"] if signals else None,
        }
    if "handicap_result" in markets:
        lines["handicap_result"] = choose_handicap_result_main_line(markets["handicap_result"])
    if "goals_over_under" in markets:
        lines["goals_over_under"] = choose_goals_main_line(markets["goals_over_under"])
    if "exact_score" in markets:
        lines["exact_score"] = {"top_values": markets["exact_score"].get("top_values", [])[:5]}
    return lines


def normalize_odds_payload(
    payload,
    primary_bookmaker=PRIMARY_BOOKMAKER,
    calibration_bookmaker=CALIBRATION_BOOKMAKER,
    model_probabilities=None,
):
    responses = payload.get("response") or []
    if not responses:
        raise ValueError("API-Football odds payload has no response rows.")
    row = responses[0]
    bookmakers = row.get("bookmakers") or []
    primary = bookmaker_by_name(bookmakers, primary_bookmaker)
    calibration = bookmaker_by_name(bookmakers, calibration_bookmaker)
    if not primary:
        raise ValueError(f"Primary bookmaker {primary_bookmaker} not found.")
    if not calibration:
        raise ValueError(f"Calibration bookmaker {calibration_bookmaker} not found.")

    primary_markets = canonical_markets(primary)
    calibration_markets = canonical_markets(calibration)
    primary_coverage = coverage(primary_markets)
    calibration_coverage = coverage(calibration_markets)

    warnings = []
    if not primary_coverage["complete"]:
        warnings.append(
            f"Primary bookmaker {primary_bookmaker} missing required markets: "
            + ", ".join(primary_coverage["missing"])
        )
    if not calibration_coverage["complete"]:
        warnings.append(
            f"Calibration bookmaker {calibration_bookmaker} missing markets: "
            + ", ".join(calibration_coverage["missing"])
        )

    calibration_deltas = {}
    for key in REQUIRED_MARKETS:
        if key in primary_markets and key in calibration_markets:
            deltas = compare_market(primary_markets[key], calibration_markets[key])
            if deltas:
                calibration_deltas[key] = deltas

    return {
        "source": "api-football",
        "api_host": API_HOST,
        "fixture": row.get("fixture") or {},
        "league": row.get("league") or {},
        "odds_update": row.get("update"),
        "bookmaker_count": len(bookmakers),
        "market_count": sum(len(bookmaker.get("bets") or []) for bookmaker in bookmakers),
        "strategy": {
            "primary_bookmaker": primary_bookmaker,
            "calibration_bookmaker": calibration_bookmaker,
            "required_markets": REQUIRED_MARKETS,
        },
        "primary": {
            "bookmaker": primary.get("name"),
            "coverage": primary_coverage,
            "markets": primary_markets,
            "main_lines": main_lines(primary_markets, model_probabilities=model_probabilities),
        },
        "calibration": {
            "bookmaker": calibration.get("name"),
            "coverage": calibration_coverage,
            "markets": calibration_markets,
        },
        "calibration_deltas": calibration_deltas,
        "value_signals": {
            "match_winner": match_winner_value_signals(
                primary_markets.get("match_winner", {}),
                model_probabilities=model_probabilities,
            )
        },
        "warnings": warnings,
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-id", type=int)
    parser.add_argument("--date", help="Target local date. Resolver checks date-1, date, date+1 against API fixtures.")
    parser.add_argument("--home")
    parser.add_argument("--away")
    parser.add_argument("--league", type=int, default=1)
    parser.add_argument("--season", type=int)
    parser.add_argument("--out")
    parser.add_argument("--env-file", default=str(repo_root() / ".env"))
    parser.add_argument("--api-key-env", action="append")
    parser.add_argument("--primary-bookmaker", default=PRIMARY_BOOKMAKER)
    parser.add_argument("--calibration-bookmaker", default=CALIBRATION_BOOKMAKER)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--include-raw", action="store_true")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    load_dotenv(args.env_file)
    api_key = require_api_key(args.api_key_env)

    fixture_context = None
    fixture_id = args.fixture_id
    if not fixture_id:
        if not args.date or not args.home or not args.away:
            raise SystemExit("Use --fixture-id or provide --date, --home, and --away.")
        fixture_context = resolve_fixture(
            api_key=api_key,
            date=args.date,
            home=args.home,
            away=args.away,
            league=args.league,
            season=args.season,
            timeout=args.timeout,
        )
        fixture_id = fixture_context["fixture"]["id"]

    payload = http_get_json("/odds", params={"fixture": fixture_id}, api_key=api_key, timeout=args.timeout)
    normalized = normalize_odds_payload(
        payload,
        primary_bookmaker=args.primary_bookmaker,
        calibration_bookmaker=args.calibration_bookmaker,
    )
    normalized["fetched_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    if fixture_context:
        normalized["fixture_context"] = fixture_context
    if args.include_raw:
        normalized["raw"] = payload

    if not args.allow_partial and not normalized["primary"]["coverage"]["complete"]:
        raise SystemExit(normalized["warnings"][0])

    output = json.dumps(normalized, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(f"{output}\n", encoding="utf-8")
    else:
        print(output)


if __name__ == "__main__":
    main()
