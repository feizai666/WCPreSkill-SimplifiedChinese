#!/usr/bin/env python3
"""Prepare automated World Cup prediction inputs without manual source triage."""

import argparse
import csv
import datetime as dt
import html
import importlib.util
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo


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
        f"site:fifa.com/en/match-centre/match {home} {away} {competition} referee",
        f"{home} v {away} referee {competition}",
        f"{home} {away} referee appointment {competition}",
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


def parse_fixture_datetime(value):
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def fixture_local_date(fixture, timezone_name):
    fixture_date = (fixture.get("fixture") or {}).get("date")
    parsed = parse_fixture_datetime(fixture_date)
    if not parsed:
        return None
    return parsed.astimezone(ZoneInfo(timezone_name)).date().isoformat()


def filter_fixtures_by_local_date(fixtures, target_date, timezone_name="Asia/Shanghai"):
    return [
        fixture
        for fixture in fixtures
        if fixture_local_date(fixture, timezone_name) == target_date
    ]


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
                if (row.get("roster_status") or "").strip().lower() == "manager":
                    continue
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
                        "fixture_id": record.get("fixture_id", ""),
                    }
                    for player in (players or [""])
                ],
            )
        )
    return targets


def record_matches_fixture(record, home="", away=""):
    teams = [team for team in (home, away) if team]
    if not teams:
        return True
    haystack = network.ascii_fold(
        " ".join(
            [
                record.get("url", ""),
                record.get("title", ""),
                record.get("content", ""),
            ]
        )
    )
    return any(network.contains_alias(haystack, network.ascii_fold(team)) for team in teams)


def discover_and_extract_sources(
    queries,
    out_dir,
    api_key,
    timeout=30,
    max_results=8,
    min_quality_score=40,
    players=None,
    match_label="",
    fixture_id=None,
    home="",
    away="",
):
    records = []
    usage = []
    for query in queries:
        referee_query = "referee" in network.ascii_fold(query)
        payload = network.tavily_search(
            query,
            api_key=api_key,
            timeout=timeout,
            max_results=max_results,
            search_depth="advanced" if referee_query else "basic",
            topic="general" if referee_query else "news",
        )
        for record in network.search_candidate_records(query, payload):
            if not record_matches_fixture(record, home=home, away=away):
                continue
            record.update(
                {
                    "fixture_id": fixture_id,
                    "home": home,
                    "away": away,
                    "match_label": match_label,
                }
            )
            records.append(record)
        usage.append({"query": query, "usage": payload.get("usage")})
    ranked = network.rank_candidate_records(records, min_quality_score=min_quality_score)
    referee_records = [
        record
        for record in records
        if "referee" in network.ascii_fold(" ".join([record.get("title", ""), record.get("content", ""), record.get("query", "")]))
    ]
    relaxed_referee_ranked = network.rank_candidate_records(referee_records, min_quality_score=min(30, min_quality_score))
    seen_urls = {record.get("url") for record in ranked}
    for record in relaxed_referee_ranked:
        if record.get("url") not in seen_urls:
            ranked.append(record)
            seen_urls.add(record.get("url"))
    ranked = sorted(ranked, key=lambda item: (-item["quality_score"], item.get("score") or 0, item.get("url", "")))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "discovered_sources.json").write_text(
        json.dumps({"queries": queries, "usage": usage, "results": ranked}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    targets = records_to_targets(ranked, match_label=match_label, players=players)
    events, extra = network.audit_tavily(
        targets,
        out_dir,
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


def fetch_json_url(url, timeout=30):
    request = urllib.request.Request(url, headers={"User-Agent": "WCPreSkill/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_text_url(url, timeout=30):
    request = urllib.request.Request(url, headers={"User-Agent": "WCPreSkill/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def nearest_hour_index(times, target_time):
    if not times:
        return None
    best_index = None
    best_delta = None
    for index, value in enumerate(times):
        parsed = dt.datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=target_time.tzinfo)
        delta = abs((parsed - target_time).total_seconds())
        if best_delta is None or delta < best_delta:
            best_index = index
            best_delta = delta
    return best_index


def fetch_weather_context(venue, kickoff_utc, timezone_name="UTC", timeout=30, fetch_json=fetch_json_url):
    city = (venue or {}).get("city") or (venue or {}).get("name") or ""
    if not city or not kickoff_utc:
        return {"status": "未核验", "reason": "missing venue city or kickoff time"}
    try:
        query = urllib.parse.urlencode({"name": city, "count": 1, "language": "en", "format": "json"})
        geo = fetch_json(f"https://geocoding-api.open-meteo.com/v1/search?{query}", timeout=timeout)
        result = (geo.get("results") or [None])[0]
        if not result:
            return {"status": "未核验", "city": city, "reason": "geocoding failed"}
        forecast_timezone = result.get("timezone") or timezone_name
        kickoff = parse_fixture_datetime(kickoff_utc).astimezone(ZoneInfo(forecast_timezone))
        date_value = kickoff.date().isoformat()
        params = urllib.parse.urlencode(
            {
                "latitude": result["latitude"],
                "longitude": result["longitude"],
                "hourly": "temperature_2m,relative_humidity_2m,precipitation_probability,wind_speed_10m,weather_code",
                "timezone": forecast_timezone,
                "start_date": date_value,
                "end_date": date_value,
            }
        )
        forecast = fetch_json(f"https://api.open-meteo.com/v1/forecast?{params}", timeout=timeout)
        hourly = forecast.get("hourly") or {}
        index = nearest_hour_index(hourly.get("time") or [], kickoff.replace(minute=0, second=0, microsecond=0))
        if index is None:
            return {"status": "未核验", "city": result.get("name", city), "reason": "hourly forecast missing"}
        return {
            "status": "已获取",
            "source": "Open-Meteo",
            "city": result.get("name", city),
            "country": result.get("country", ""),
            "timezone": forecast_timezone,
            "kickoff_local": kickoff.isoformat(),
            "forecast_time": hourly.get("time", [])[index],
            "temperature_c": hourly.get("temperature_2m", [None])[index],
            "humidity_percent": hourly.get("relative_humidity_2m", [None])[index],
            "precipitation_probability_percent": hourly.get("precipitation_probability", [None])[index],
            "wind_kmh": hourly.get("wind_speed_10m", [None])[index],
            "weather_code": hourly.get("weather_code", [None])[index],
        }
    except Exception as exc:
        return {"status": "未核验", "city": city, "error": f"{type(exc).__name__}: {exc}"}


COUNTRY_TO_CODE = {
    "argentina": "ARG",
    "australia": "AUS",
    "brazil": "BRA",
    "canada": "CAN",
    "china": "CHN",
    "england": "ENG",
    "france": "FRA",
    "germany": "GER",
    "italy": "ITA",
    "mexico": "MEX",
    "netherlands": "NED",
    "portugal": "POR",
    "spain": "ESP",
    "united states": "USA",
    "usa": "USA",
    "uruguay": "URU",
}
NATIONALITY_TO_CODE = {
    "argentine": "ARG",
    "australian": "AUS",
    "brazilian": "BRA",
    "canadian": "CAN",
    "chinese": "CHN",
    "dutch": "NED",
    "english": "ENG",
    "french": "FRA",
    "german": "GER",
    "italian": "ITA",
    "mexican": "MEX",
    "portuguese": "POR",
    "spanish": "ESP",
    "uruguayan": "URU",
}
SOCIAL_REFEREE_DOMAINS = {
    "facebook.com",
    "instagram.com",
    "x.com",
    "twitter.com",
}


def normalize_referee_name(value):
    value = html.unescape(network.normalize_text(value or ""))
    value = re.sub(r"\s*\([^)]*\)\s*$", "", value).strip()
    if "," in value:
        value = value.split(",", 1)[0].strip()
    return value.rstrip(" .;:，。；：")


def is_plausible_referee_name(value):
    value = normalize_referee_name(value)
    if not value or value.upper() in {"TBC", "TBD", "VAR", "FIFA"}:
        return False
    words = value.split()
    return 2 <= len(words) <= 4 and all(re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ]", word) for word in words)


def referee_country_code(value):
    value = html.unescape(network.normalize_text(value or ""))
    paren = re.search(r"\(([A-Z]{3})\)\s*$", value)
    if paren:
        return paren.group(1)
    if "," in value:
        country = value.split(",", 1)[1].strip().lower()
        return COUNTRY_TO_CODE.get(country, country[:3].upper() if country else "")
    return ""


def referee_country_code_from_text(value):
    folded = network.ascii_fold(value or "")
    for nationality, code in NATIONALITY_TO_CODE.items():
        if re.search(rf"\b{re.escape(nationality)}\s+referee\b", folded):
            return code
    return ""


def referee_base_context(status, name="", source="API-Football fixture", **extra):
    context = {
        "status": status,
        "name": normalize_referee_name(name),
        "country": referee_country_code(name),
        "source": source,
        "confidence": "high" if status == "已获取" else "none",
        "fallback_used": False,
    }
    context.update(extra)
    return context


def fifa_match_centre_search_url(home="", away="", target_date=""):
    query = urllib.parse.urlencode(
        {
            "q": f"site:fifa.com/en/match-centre/match {home} {away} {target_date} referee",
        }
    )
    return f"https://www.fifa.com/en/match-centre/match?{query}"


def strip_html_tags(value):
    value = re.sub(r"<script\b[^>]*>.*?</script>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<style\b[^>]*>.*?</style>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    return network.normalize_text(html.unescape(value))


def extract_referee_from_html(value):
    if not value:
        return ""
    patterns = [
        r">\s*Referee\s*</[^>]+>\s*<[^>]+>\s*([^<]{3,80})\s*<",
        r'"referee"\s*:\s*"([^"]{3,80})"',
        r'"name"\s*:\s*"([^"]{3,80})"\s*,\s*"role"\s*:\s*"Referee"',
        r"([A-Z][A-Za-zÀ-ÖØ-öø-ÿ' .-]{2,80})\s+to\s+Referee\b",
        r"appointed\s+(?:English|French|Spanish|Italian|Portuguese|Argentine|Brazilian|German|Dutch|Uruguayan|Canadian|American)?\s*referee\s+([A-Z][A-Za-zÀ-ÖØ-öø-ÿ' .-]{2,80})\s+to\s+officiate",
    ]
    for pattern in patterns:
        match = re.search(pattern, value, flags=re.I | re.S)
        if match and is_plausible_referee_name(match.group(1)):
            return normalize_referee_name(match.group(1))
    text = strip_html_tags(value)
    match = re.search(r"\bReferee\b[.:]?\s+([A-Z][A-Za-zÀ-ÖØ-öø-ÿ' .-]{2,80})", text)
    if match:
        name = re.split(r"\b(?:Assistant|VAR|Fourth|Competition|Kick|Location|City)\b", match.group(1))[0]
        if is_plausible_referee_name(name):
            return normalize_referee_name(name)
    return ""


def extract_referee_from_records(records):
    media_candidate = None
    for record in records or []:
        url = record.get("url", "")
        quality = network.source_quality(record)
        record_text = " ".join([record.get("title", ""), record.get("content", "")])
        name = extract_referee_from_html(record_text)
        if not name:
            continue
        domain = quality.get("domain", "")
        country = referee_country_code_from_text(record_text)
        if quality.get("tier") == "official" or domain.endswith("fifa.com"):
            return name, url, quality, "FIFA Match Centre", country
        if domain in SOCIAL_REFEREE_DOMAINS:
            continue
        folded = network.ascii_fold(record_text)
        if "referee" in folded and ("fifa has appointed" in folded or "appointed" in folded):
            media_candidate = (name, url, quality, "Media referee report", country)
    if media_candidate:
        return media_candidate
    return "", "", {}, "", ""


def referee_context(
    fixture,
    home="",
    away="",
    target_date="",
    source_records=None,
    fetch_html=fetch_text_url,
    timeout=30,
):
    name = ((fixture.get("fixture") or {}).get("referee") or "").strip()
    if name:
        return referee_base_context(
            "已获取",
            name,
            "API-Football fixture",
            api_football_referee=name,
            fallback_used=False,
        )

    record_name, record_url, quality, record_source, record_country = extract_referee_from_records(source_records)
    if record_name:
        return referee_base_context(
            "已获取",
            record_name,
            record_source,
            country=record_country or referee_country_code(record_name),
            source_url=record_url,
            confidence="high" if record_source == "FIFA Match Centre" else "medium",
            api_football_referee="",
            fallback_used=True,
        )

    source_url = fifa_match_centre_search_url(home, away, target_date)
    if fetch_html is None:
        return referee_base_context(
            "未公布",
            "",
            "API-Football fixture",
            source_url=source_url,
            confidence="none",
            api_football_referee="",
            fallback_used=True,
        )
    try:
        fifa_html = fetch_html(source_url, timeout=timeout)
    except Exception as exc:
        return referee_base_context(
            "未公布",
            "",
            "API-Football fixture",
            source_url=source_url,
            confidence="none",
            api_football_referee="",
            fallback_used=True,
            error=f"{type(exc).__name__}: {exc}",
        )
    html_name = extract_referee_from_html(fifa_html)
    if html_name:
        return referee_base_context(
            "已获取",
            html_name,
            "FIFA Match Centre",
            source_url=source_url,
            confidence="high",
            api_football_referee="",
            fallback_used=True,
        )
    return referee_base_context(
        "未公布",
        "",
        "API-Football fixture",
        source_url=source_url,
        confidence="none",
        api_football_referee="",
        fallback_used=True,
    )


def merge_source_results(out_dir, source_by_fixture):
    events = []
    records = []
    search_usage = []
    summaries = []
    for result in source_by_fixture.values():
        events.extend(result.get("events", []))
        records.extend(result.get("records", []))
        summary = result.get("summary") or {}
        summaries.append(summary)
        search_usage.extend(summary.get("search_usage") or [])
    summary = network.summarize(
        events,
        {
            "usage": network.merge_usage([summary.get("usage") for summary in summaries]),
            "response_time": max([summary.get("response_time") or 0 for summary in summaries] or [0]),
        },
    )
    summary["discovered_source_count"] = len(records)
    summary["fixture_count"] = len(source_by_fixture)
    summary["search_usage"] = search_usage
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "discovered_sources.json").write_text(
        json.dumps({"results": records, "usage": search_usage}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    network.write_outputs(out_dir, events, summary)
    return {
        "bundle": json.loads((out_dir / "prediction_source_bundle.json").read_text(encoding="utf-8")),
        "availability_candidates": network.availability_candidates(events),
        "summary": summary,
    }


def build_prediction_input(
    target_date,
    fixtures,
    odds_by_fixture,
    source_bundle=None,
    availability_candidates=None,
    source_by_fixture=None,
    context_by_fixture=None,
):
    source_bundle = source_bundle or {"sources": []}
    availability_candidates = availability_candidates or []
    source_by_fixture = source_by_fixture or {}
    context_by_fixture = context_by_fixture or {}
    matches = []
    for fixture in fixtures:
        fixture_info = fixture.get("fixture") or {}
        league = fixture.get("league") or {}
        venue = fixture.get("fixture", {}).get("venue") or fixture.get("venue") or {}
        home, away = fixture_match_name(fixture)
        odds = odds_by_fixture.get(fixture_info.get("id"), {})
        fixture_source = source_by_fixture.get(fixture_info.get("id")) or {}
        match_bundle = fixture_source.get("bundle") or source_bundle
        match_candidates = fixture_source.get("availability_candidates", availability_candidates)
        context = context_by_fixture.get(fixture_info.get("id")) or {}
        match = {
            "fixture_id": fixture_info.get("id"),
            "home": home,
            "away": away,
            "competition": league.get("name", "World Cup"),
            "kickoff_utc": fixture_info.get("date"),
            "venue": venue,
            "odds": odds,
            "source_refs": [source.get("url") for source in match_bundle.get("sources", []) if source.get("ok")],
            "availability_candidates": match_candidates,
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
        if context.get("referee"):
            match["referee"] = context["referee"]
        if context.get("weather"):
            match["weather"] = context["weather"]
        matches.append(
            match
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
    parser.add_argument("--timezone", default="Asia/Shanghai")
    parser.add_argument("--skip-network", action="store_true")
    parser.add_argument("--skip-weather", action="store_true")
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

    api_window_fixtures = fetch_worldcup_fixtures(
        api_key=api_key,
        target_date=args.date,
        league=args.league,
        season=args.season,
        timeout=args.timeout,
    )
    fixtures = filter_fixtures_by_local_date(api_window_fixtures, args.date, args.timezone)
    odds_by_fixture = {}
    source_by_fixture = {}
    context_by_fixture = {}
    tavily_key = os.environ.get(args.tavily_api_key_env)
    for fixture in fixtures:
        fixture_id = (fixture.get("fixture") or {}).get("id")
        home, away = fixture_match_name(fixture)
        competition = (fixture.get("league") or {}).get("name", "World Cup")
        match_label = f"{home};{away}"
        roster_players = load_roster_players(args.roster_root, args.date, [home, away])
        context_by_fixture[fixture_id] = {
            "referee": referee_context(
                fixture,
                home=home,
                away=away,
                target_date=args.date,
                fetch_html=None,
                timeout=args.timeout,
            )
        }
        if not args.skip_weather:
            context_by_fixture[fixture_id]["weather"] = fetch_weather_context(
                (fixture.get("fixture") or {}).get("venue") or {},
                (fixture.get("fixture") or {}).get("date"),
                "UTC",
                timeout=args.timeout,
            )
        payload = api_odds.http_get_json("/odds", params={"fixture": fixture_id}, api_key=api_key, timeout=args.timeout)
        try:
            odds_by_fixture[fixture_id] = api_odds.normalize_odds_payload(payload)
        except ValueError as exc:
            odds_by_fixture[fixture_id] = {"error": str(exc), "warnings": [str(exc)]}
        if not args.skip_network and tavily_key:
            source_result = discover_and_extract_sources(
                match_search_queries(home, away, competition, args.date),
                out_dir / "network" / str(fixture_id),
                api_key=tavily_key,
                timeout=args.timeout,
                max_results=args.discover_max_results,
                min_quality_score=args.discover_min_quality_score,
                players=roster_players,
                match_label=match_label,
                fixture_id=fixture_id,
                home=home,
                away=away,
            )
            source_by_fixture[fixture_id] = source_result
            if context_by_fixture[fixture_id]["referee"].get("status") != "已获取":
                context_by_fixture[fixture_id]["referee"] = referee_context(
                    fixture,
                    home=home,
                    away=away,
                    target_date=args.date,
                    source_records=source_result.get("records"),
                    fetch_html=None,
                    timeout=args.timeout,
                )

    source_bundle = {"summary": {"skipped": args.skip_network}, "sources": []}
    availability_candidates = []
    if source_by_fixture:
        aggregate = merge_source_results(out_dir / "network", source_by_fixture)
        source_bundle = aggregate["bundle"]
        availability_candidates = aggregate["availability_candidates"]
    elif not args.skip_network and not tavily_key:
        source_bundle = {"summary": {"error": f"Missing {args.tavily_api_key_env}"}, "sources": []}

    prediction_input = build_prediction_input(
        args.date,
        fixtures,
        odds_by_fixture,
        source_bundle,
        availability_candidates,
        source_by_fixture=source_by_fixture,
        context_by_fixture=context_by_fixture,
    )
    prediction_input["automation"]["roster_updated"] = roster_updated
    (out_dir / "fixtures_api_window.json").write_text(
        json.dumps(api_window_fixtures, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
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
