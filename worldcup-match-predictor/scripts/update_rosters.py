#!/usr/bin/env python3
"""Build World Cup roster CSVs used by the prediction workflow.

The script creates a dated roster folder:

  data/rosters/YYYY-MM-DD/
    all_rosters.csv
    teams/<TEAM>.csv

Base squads are parsed from ESPN's published 48-team squad list, which states
that final 26-player squads were submitted to FIFA and announced June 2. Daily
availability is then overlaid from a local CSV so late injuries, replacements,
and suspensions can be corrected before generating match cards.
"""

import argparse
import csv
import re
import sys
import urllib.request
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path


ESPN_SQUADS_URL = (
    "https://www.espn.com/soccer/story/_/id/48757621/"
    "2026-world-cup-squad-lists-players-announced-all-48-teams"
)

TEAM_NAMES = [
    "Mexico",
    "South Africa",
    "South Korea",
    "Czechia",
    "Bosnia-Herzegovina",
    "Canada",
    "Qatar",
    "Switzerland",
    "Brazil",
    "Morocco",
    "Haiti",
    "Scotland",
    "United States",
    "Australia",
    "Paraguay",
    "Türkiye",
    "Germany",
    "Curacao",
    "Ivory Coast",
    "Ecuador",
    "Japan",
    "Netherlands",
    "Sweden",
    "Tunisia",
    "Belgium",
    "Egypt",
    "Iran",
    "New Zealand",
    "Spain",
    "Cape Verde",
    "Saudi Arabia",
    "Uruguay",
    "France",
    "Senegal",
    "Iraq",
    "Norway",
    "Argentina",
    "Algeria",
    "Austria",
    "Jordan",
    "Portugal",
    "Congo DR",
    "Uzbekistan",
    "Colombia",
    "England",
    "Croatia",
    "Ghana",
    "Panama",
]

TEAM_CODES = {
    "Algeria": "ALG",
    "Argentina": "ARG",
    "Australia": "AUS",
    "Austria": "AUT",
    "Belgium": "BEL",
    "Bosnia-Herzegovina": "BIH",
    "Brazil": "BRA",
    "Canada": "CAN",
    "Cape Verde": "CPV",
    "Colombia": "COL",
    "Congo DR": "COD",
    "Croatia": "CRO",
    "Czechia": "CZE",
    "Curacao": "CUW",
    "Ecuador": "ECU",
    "Egypt": "EGY",
    "England": "ENG",
    "France": "FRA",
    "Germany": "GER",
    "Ghana": "GHA",
    "Haiti": "HAI",
    "Iran": "IRN",
    "Iraq": "IRQ",
    "Ivory Coast": "CIV",
    "Japan": "JPN",
    "Jordan": "JOR",
    "Mexico": "MEX",
    "Morocco": "MAR",
    "Netherlands": "NED",
    "New Zealand": "NZL",
    "Norway": "NOR",
    "Panama": "PAN",
    "Paraguay": "PAR",
    "Portugal": "POR",
    "Qatar": "QAT",
    "Saudi Arabia": "KSA",
    "Scotland": "SCO",
    "Senegal": "SEN",
    "South Africa": "RSA",
    "South Korea": "KOR",
    "Spain": "ESP",
    "Sweden": "SWE",
    "Switzerland": "SUI",
    "Tunisia": "TUN",
    "Türkiye": "TUR",
    "United States": "USA",
    "Uruguay": "URU",
    "Uzbekistan": "UZB",
}

ROSTER_FIELDS = [
    "snapshot_date",
    "team",
    "team_code",
    "player",
    "position_group",
    "club",
    "roster_status",
    "availability_status",
    "absence_note",
    "expected_absence_matches",
    "suspension_status",
    "yellow_card_risk",
    "starting_role",
    "notes",
    "base_roster_source",
    "availability_source",
    "source_url",
    "last_checked_bj",
]


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "svg"}:
            self.skip_depth += 1
        if not self.skip_depth and tag in {"h1", "h2", "h3", "p", "li", "br"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "svg"} and self.skip_depth:
            self.skip_depth -= 1
        if not self.skip_depth and tag in {"h1", "h2", "h3", "p", "li"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.skip_depth:
            return
        text = " ".join(data.split())
        if text:
            self.parts.append(text + " ")

    def text(self):
        return "".join(self.parts)


def fetch_text(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            )
        },
    )
    html = urllib.request.urlopen(request, timeout=30).read().decode("utf-8", "ignore")
    parser = TextExtractor()
    parser.feed(html)
    return parser.text()


def team_slug(team):
    return TEAM_CODES.get(team, re.sub(r"\W+", "_", team).strip("_").upper())


def find_team_ranges(text):
    ranges = {}
    starts = []
    for team in TEAM_NAMES:
        marker = f"\n{team} \n"
        start = text.find(marker)
        if start != -1:
            starts.append((start, team))
    starts.sort()
    for index, (start, team) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(text)
        ranges[team] = text[start:end]
    return ranges


def normalize_block(block):
    replacements = {
        "Tyler Adams ( AFC Bournemouth , Sebastian Berhalter": (
            "Tyler Adams ( AFC Bournemouth ), Sebastian Berhalter"
        ),
        "Paris Saint-Germian": "Paris Saint-Germain",
    }
    for old, new in replacements.items():
        block = block.replace(old, new)
    return block


def role_block(section, role, following_roles):
    boundaries = [rf"\n{next_role}\s*:?" for next_role in following_roles]
    boundaries.extend([r"\nManager:", r"\Z"])
    pattern = rf"{role}\s*:?\s*(.*?)(?=" + "|".join(boundaries) + r")"
    match = re.search(pattern, section, flags=re.S | re.I)
    if not match:
        return ""
    return normalize_block(match.group(1)).strip()


def parse_players(block):
    players = []
    for name, club in re.findall(r"([^,\n]+?)\s*\(([^()]+)\)", block):
        name = " ".join(name.split()).strip(" ,")
        club = " ".join(club.split()).strip(" ,")
        if not name or ":" in name:
            continue
        players.append((name, club))
    return players


def parse_rosters(text, snapshot_date, last_checked):
    ranges = find_team_ranges(text)
    rosters = []
    for team in TEAM_NAMES:
        section = ranges.get(team)
        if not section:
            continue
        role_order = ["Goalkeepers", "Defenders", "Midfielders", "Forwards"]
        for i, role in enumerate(role_order):
            block = role_block(section, role, role_order[i + 1 :])
            for player, club in parse_players(block):
                rosters.append(
                    {
                        "snapshot_date": snapshot_date,
                        "team": team,
                        "team_code": TEAM_CODES.get(team, ""),
                        "player": player,
                        "position_group": role,
                        "club": club,
                        "roster_status": "final_squad",
                        "availability_status": "base_roster_not_daily_verified",
                        "absence_note": "",
                        "expected_absence_matches": "",
                        "suspension_status": "",
                        "yellow_card_risk": "",
                        "starting_role": "",
                        "notes": "",
                        "base_roster_source": "ESPN final 26-player squad list / FIFA submitted roster",
                        "availability_source": "",
                        "source_url": ESPN_SQUADS_URL,
                        "last_checked_bj": last_checked,
                    }
                )
        manager = re.search(r"\nManager:\s*([^\n]+)", section)
        if manager:
            rosters.append(
                {
                    "snapshot_date": snapshot_date,
                    "team": team,
                    "team_code": TEAM_CODES.get(team, ""),
                    "player": " ".join(manager.group(1).split()),
                    "position_group": "Manager",
                    "club": "",
                    "roster_status": "manager",
                    "availability_status": "active",
                    "absence_note": "",
                    "expected_absence_matches": "",
                    "suspension_status": "",
                    "yellow_card_risk": "",
                    "starting_role": "manager",
                    "notes": "",
                    "base_roster_source": "ESPN final 26-player squad list / FIFA submitted roster",
                    "availability_source": "",
                    "source_url": ESPN_SQUADS_URL,
                    "last_checked_bj": last_checked,
                }
            )
    return rosters


def load_overrides(path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def apply_overrides(rosters, overrides, snapshot_date, last_checked):
    index = {(row["team"], row["player"]): row for row in rosters}
    for override in overrides:
        team = override.get("team", "").strip()
        player = override.get("player", "").strip()
        if not team or not player:
            continue
        key = (team, player)
        row = index.get(key)
        if not row:
            row = {
                field: ""
                for field in ROSTER_FIELDS
            }
            row.update(
                {
                    "snapshot_date": snapshot_date,
                    "team": team,
                    "team_code": TEAM_CODES.get(team, override.get("team_code", "")),
                    "player": player,
                    "position_group": override.get("position_group", ""),
                    "club": override.get("club", ""),
                    "roster_status": override.get("roster_status", "not_in_base_roster"),
                    "base_roster_source": "",
                    "last_checked_bj": last_checked,
                }
            )
            rosters.append(row)
            index[key] = row
        for field in ROSTER_FIELDS:
            value = override.get(field, "")
            if value:
                row[field] = value
        row["snapshot_date"] = snapshot_date
        row["last_checked_bj"] = override.get("last_checked_bj") or last_checked
    return rosters


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ROSTER_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in ROSTER_FIELDS})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="Beijing snapshot date, YYYY-MM-DD")
    parser.add_argument("--output-root", default="data/rosters")
    parser.add_argument("--source-html", help="Optional cached ESPN squads HTML")
    parser.add_argument("--source-text", help="Optional cached ESPN squads text")
    args = parser.parse_args()

    last_checked = datetime.now().strftime("%Y-%m-%d %H:%M")
    output_dir = Path(args.output_root) / args.date
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.source_text:
        text = Path(args.source_text).read_text(encoding="utf-8")
    elif args.source_html:
        parser_obj = TextExtractor()
        parser_obj.feed(Path(args.source_html).read_text(encoding="utf-8", errors="ignore"))
        text = parser_obj.text()
    else:
        text = fetch_text(ESPN_SQUADS_URL)

    (output_dir / "source_espn_squads.txt").write_text(text, encoding="utf-8")
    rosters = parse_rosters(text, args.date, last_checked)
    overrides = load_overrides(output_dir / "availability_overrides.csv")
    rosters = apply_overrides(rosters, overrides, args.date, last_checked)
    rosters.sort(key=lambda row: (row["team"], row["position_group"], row["player"]))

    write_csv(output_dir / "all_rosters.csv", rosters)
    teams_dir = output_dir / "teams"
    for team in sorted({row["team"] for row in rosters}):
        write_csv(teams_dir / f"{team_slug(team)}.csv", [row for row in rosters if row["team"] == team])

    print(f"Wrote {len(rosters)} roster rows to {output_dir}")
    print(f"Wrote {len({row['team'] for row in rosters})} team CSV files to {teams_dir}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"update_rosters.py failed: {exc}", file=sys.stderr)
        sys.exit(1)
