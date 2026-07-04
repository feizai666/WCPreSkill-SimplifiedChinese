#!/usr/bin/env python3
"""Prepare automated World Cup prediction inputs without manual source triage."""

import argparse
import csv
import datetime as dt
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path


def script_dir():
    return Path(__file__).resolve().parent


def repo_root():
    return Path(__file__).resolve().parents[2]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


api_odds = load_module("api_football_odds", script_dir() / "api_football_odds.py")
network = load_module("network_fetch_audit", script_dir() / "network_fetch_audit.py")


def match_search_queries(home, away, competition, target_date):
    base = f"{home} {away} {competition} {target_date}"
    return [
        f"{base} team news injuries suspensions predicted lineup",
        f"{base} press conference squad availability",
        f"{base} referee appointment cards",
        f"{base} weather venue pitch",
    ]


def fetch_worldcup_fixtures(api_key, target_date, league=1, season=None, timeout=30):
    fixtures = []
    for api_date in api_odds.date_window(target_date):
        payload = api_odds.http_get_json("/fixtures", params={"date": api_date}, api_key=api_key, timeout=timeout)
        for row in payload.get("response") or []:
            row_league = row.get("league") or {}
            if league and row_league.get("id") != league:
                continue
            if season and row_league.get("season") != season:
                continue
            row = dict(row)
            row["api_date"] = api_date
            fixtures.append(row)
    return sorted(fixtures, key=lambda item: (item.get("fixture") or {}).get("timestamp") or 0)


def ensure_rosters(target_date, roster_root, runner=subprocess.run):
    roster_root = Path(roster_root)
    output = roster_root / target_date / "all_rosters.csv"
    if output.exists() and output.stat().st_size > 0:
        return False
    command = [
        sys.executable,
        str(script_dir() / "update_rosters.py"),
        "--date",
        target_date,
        "--output-root",
        str(roster_root),
    ]
    runner(command, cwd=repo_root(), check=True)
    return True


def fixture_match_name(fixture):
    teams = fixture.get("teams") or {}
    home = (teams.get("home") or {}).get("name", "")
    away = (teams.get("away") or {}).get("name", "")
    return home, away


def load_roster_players(roster_root, target_date, teams):
    path = Path(roster_root) / target_date / "all_rosters.csv"
    if not path.exists():
        return []
    wanted = {team.lower() for team in teams}
    players = set()
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if (row.get("team") or "").lower() in wanted or (row.get("team_code") or "").lower() in wanted:
                player = (row.get("player") or "").strip()
                if player:
                    players.add(player)
    return sorted(players)


def records_to_targets(records, match_label="", players=None):
    targets = []
    players = players or []
    for record in records:
        targets.append(
            network.SourceTarget(
                url=record["url"],
                rows=[
                    {
                        "team": match_label,
                        "player": player,
                        "availability_source": record.get("quality_tier", ""),
                        "source_url": record["url"],
                    }
                    for player in (players or [""])
                ],
            )
        )
    return targets


def discover_and_extract_sources(
    queries,
    out_dir,
    api_key,
    timeout=30,
    max_results=8,
    min_quality_score=40,
    players=None,
    match_label="",
):
    records = []
    usage = []
    for query in queries:
        payload = network.tavily_search(query, api_key=api_key, timeout=timeout, max_results=max_results)
        records.extend(network.search_candidate_records(query, payload))
        usage.append({"query": query, "usage": payload.get("usage")})
    ranked = network.rank_candidate_records(records, min_quality_score=min_quality_score)
    targets = records_to_targets(ranked, match_label=match_label, players=players)
    events, extra = network.audit_tavily(
        targets,
        Path(out_dir),
        timeout=timeout,
        api_key=api_key,
        keyless=False,
        extract_depth="basic",
        batch_size=5,
        retries=2,
        retry_sleep_ms=500,
        fallback_local_http=True,
    )
    summary = network.summarize(events, extra)
    summary["discovered_source_count"] = len(ranked)
    summary["search_usage"] = usage
    network.write_outputs(out_dir, events, summary)
    return {
        "records": ranked,
        "events": events,
        "summary": summary,
        "bundle": json.loads((Path(out_dir) / "prediction_source_bundle.json").read_text(encoding="utf-8")),
        "availability_candidates": network.availability_candidates(events),
    }


def build_prediction_input(target_date, fixtures, odds_by_fixture, source_bundle, availability_candidates):
    matches = []
    for fixture in fixtures:
        fixture_info = fixture.get("fixture") or {}
        league = fixture.get("league") or {}
        venue = fixture.get("fixture", {}).get("venue") or fixture.get("venue") or {}
        home, away = fixture_match_name(fixture)
        odds = odds_by_fixture.get(fixture_info.get("id"), {})
        matches.append(
            {
                "fixture_id": fixture_info.get("id"),
                "home": home,
                "away": away,
                "competition": league.get("name", "World Cup"),
                "kickoff_utc": fixture_info.get("date"),
                "venue": venue,
                "odds": odds,
                "source_refs": [source.get("url") for source in source_bundle.get("sources", []) if source.get("ok")],
                "availability_candidates": availability_candidates,
                "prediction_fields_pending": [
                    "predicted_score",
                    "alt_score",
                    "total_goals",
                    "probability",
                    "confidence",
                    "key_factors",
                    "bet_advice",
                ],
            }
        )
    return {
        "date": target_date,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "manual_action_required": False,
        "automation": {
            "fixture_count": len(fixtures),
            "source_bundle_count": len(source_bundle.get("sources", [])),
            "availability_candidate_count": len(availability_candidates),
            "odds_count": len([item for item in odds_by_fixture.values() if item]),
        },
        "matches": matches,
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--league", type=int, default=1)
    parser.add_argument("--season", type=int)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--skip-network", action="store_true")
    parser.add_argument("--roster-root", default=str(repo_root() / "data" / "rosters"))
    parser.add_argument("--no-update-rosters", action="store_true")
    parser.add_argument("--tavily-api-key-env", default="TAVILY_API_KEY")
    parser.add_argument("--api-football-key-env", action="append")
    parser.add_argument("--discover-max-results", type=int, default=8)
    parser.add_argument("--discover-min-quality-score", type=int, default=40)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    api_odds.load_dotenv(repo_root() / ".env")
    api_key = api_odds.require_api_key(args.api_football_key_env)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    roster_updated = False
    if not args.no_update_rosters:
        roster_updated = ensure_rosters(args.date, args.roster_root)

    fixtures = fetch_worldcup_fixtures(
        api_key=api_key,
        target_date=args.date,
        league=args.league,
        season=args.season,
        timeout=args.timeout,
    )
    odds_by_fixture = {}
    all_queries = []
    match_labels = []
    roster_players = set()
    for fixture in fixtures:
        fixture_id = (fixture.get("fixture") or {}).get("id")
        home, away = fixture_match_name(fixture)
        competition = (fixture.get("league") or {}).get("name", "World Cup")
        match_labels.append(f"{home};{away}")
        roster_players.update(load_roster_players(args.roster_root, args.date, [home, away]))
        all_queries.extend(match_search_queries(home, away, competition, args.date))
        payload = api_odds.http_get_json("/odds", params={"fixture": fixture_id}, api_key=api_key, timeout=args.timeout)
        try:
            odds_by_fixture[fixture_id] = api_odds.normalize_odds_payload(payload)
        except ValueError as exc:
            odds_by_fixture[fixture_id] = {"error": str(exc), "warnings": [str(exc)]}

    source_bundle = {"summary": {"skipped": args.skip_network}, "sources": []}
    availability_candidates = []
    if all_queries and not args.skip_network:
        tavily_key = os.environ.get(args.tavily_api_key_env)
        if tavily_key:
            source_result = discover_and_extract_sources(
                all_queries,
                out_dir / "network",
                api_key=tavily_key,
                timeout=args.timeout,
                max_results=args.discover_max_results,
                min_quality_score=args.discover_min_quality_score,
                players=sorted(roster_players),
                match_label=";".join(match_labels),
            )
            source_bundle = source_result["bundle"]
            availability_candidates = source_result["availability_candidates"]
        else:
            source_bundle = {"summary": {"error": f"Missing {args.tavily_api_key_env}"}, "sources": []}

    prediction_input = build_prediction_input(args.date, fixtures, odds_by_fixture, source_bundle, availability_candidates)
    prediction_input["automation"]["roster_updated"] = roster_updated
    (out_dir / "fixtures.json").write_text(json.dumps(fixtures, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "odds_by_fixture.json").write_text(
        json.dumps(odds_by_fixture, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "prediction_input.json").write_text(
        json.dumps(prediction_input, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(prediction_input["automation"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
