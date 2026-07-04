import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "worldcup-match-predictor" / "scripts" / "prepare_prediction_inputs.py"


class PreparePredictionInputsTests(unittest.TestCase):
    def load_module(self):
        spec = importlib.util.spec_from_file_location("prepare_prediction_inputs", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_build_prediction_input_does_not_require_manual_actions(self):
        module = self.load_module()
        fixtures = [
            {
                "fixture": {"id": 1567824, "date": "2026-07-04T17:00:00+00:00"},
                "league": {"name": "World Cup", "season": 2026},
                "teams": {"home": {"name": "Canada"}, "away": {"name": "Morocco"}},
                "venue": {"name": "BC Place", "city": "Vancouver"},
            }
        ]
        odds = {
            1567824: {
                "primary": {"bookmaker": "Bet365", "coverage": {"complete": True, "missing": []}},
                "warnings": [],
            }
        }
        source_bundle = {
            "sources": [
                {
                    "url": "https://www.espn.com/soccer/story/team-news",
                    "ok": True,
                    "evidence": [{"players": ["Alphonso Davies"], "terms": ["fit"], "text": "Davies is fit."}],
                }
            ]
        }
        availability = [{"player": "Alphonso Davies", "availability_status": "available"}]

        output = module.build_prediction_input(
            target_date="2026-07-05",
            fixtures=fixtures,
            odds_by_fixture=odds,
            source_bundle=source_bundle,
            availability_candidates=availability,
        )

        self.assertFalse(output["manual_action_required"])
        self.assertEqual(output["matches"][0]["home"], "Canada")
        self.assertEqual(output["matches"][0]["odds"]["primary"]["bookmaker"], "Bet365")
        self.assertEqual(output["automation"]["source_bundle_count"], 1)

    def test_search_queries_cover_team_news_and_referee_without_user_input(self):
        module = self.load_module()

        queries = module.match_search_queries("Canada", "Morocco", "World Cup", "2026-07-05")

        self.assertTrue(any("team news" in query for query in queries))
        self.assertTrue(any("referee" in query for query in queries))
        self.assertTrue(any("weather" in query for query in queries))

    def test_filter_fixtures_keeps_only_target_beijing_date(self):
        module = self.load_module()
        fixtures = [
            {
                "fixture": {"id": 1, "date": "2026-07-04T01:30:00+00:00"},
                "teams": {"home": {"name": "Colombia"}, "away": {"name": "Ghana"}},
            },
            {
                "fixture": {"id": 2, "date": "2026-07-04T17:00:00+00:00"},
                "teams": {"home": {"name": "Canada"}, "away": {"name": "Morocco"}},
            },
            {
                "fixture": {"id": 3, "date": "2026-07-04T21:00:00+00:00"},
                "teams": {"home": {"name": "Paraguay"}, "away": {"name": "France"}},
            },
            {
                "fixture": {"id": 4, "date": "2026-07-05T20:00:00+00:00"},
                "teams": {"home": {"name": "Brazil"}, "away": {"name": "Norway"}},
            },
        ]

        filtered = module.filter_fixtures_by_local_date(fixtures, "2026-07-05", "Asia/Shanghai")

        self.assertEqual([(row["fixture"]["id"]) for row in filtered], [2, 3])

    def test_roster_players_are_attached_to_discovered_targets(self):
        module = self.load_module()
        with tempfile.TemporaryDirectory() as tmp:
            roster = Path(tmp) / "2026-07-05" / "all_rosters.csv"
            roster.parent.mkdir(parents=True)
            roster.write_text(
                "team,team_code,player\n"
                "Canada,CAN,Alphonso Davies\n"
                "Morocco,MAR,Achraf Hakimi\n"
                "Brazil,BRA,Vinicius Junior\n",
                encoding="utf-8",
            )

            players = module.load_roster_players(Path(tmp), "2026-07-05", ["Canada", "Morocco"])
            targets = module.records_to_targets(
                [{"url": "https://www.espn.com/soccer/story/team-news", "quality_tier": "mainstream"}],
                match_label="Canada;Morocco",
                players=players,
            )

        self.assertEqual(players, ["Achraf Hakimi", "Alphonso Davies"])
        self.assertEqual(targets[0].players, ["Achraf Hakimi", "Alphonso Davies"])

    def test_load_roster_players_ignores_manager_rows(self):
        module = self.load_module()
        with tempfile.TemporaryDirectory() as tmp:
            roster = Path(tmp) / "2026-07-05" / "all_rosters.csv"
            roster.parent.mkdir(parents=True)
            roster.write_text(
                "team,team_code,player,roster_status\n"
                "Canada,CAN,Alphonso Davies,final_squad\n"
                "Canada,CAN,Jesse Marsch,manager\n",
                encoding="utf-8",
            )

            players = module.load_roster_players(Path(tmp), "2026-07-05", ["Canada"])

        self.assertEqual(players, ["Alphonso Davies"])

    def test_build_prediction_input_scopes_sources_and_candidates_per_fixture(self):
        module = self.load_module()
        fixtures = [
            {
                "fixture": {"id": 1, "date": "2026-07-04T17:00:00+00:00"},
                "league": {"name": "World Cup"},
                "teams": {"home": {"name": "Canada"}, "away": {"name": "Morocco"}},
            },
            {
                "fixture": {"id": 2, "date": "2026-07-04T21:00:00+00:00"},
                "league": {"name": "World Cup"},
                "teams": {"home": {"name": "Paraguay"}, "away": {"name": "France"}},
            },
        ]
        source_by_fixture = {
            1: {
                "bundle": {"sources": [{"url": "https://example.test/canada", "ok": True}]},
                "availability_candidates": [{"player": "Alphonso Davies"}],
            },
            2: {
                "bundle": {"sources": [{"url": "https://example.test/france", "ok": True}]},
                "availability_candidates": [{"player": "Aurelien Tchouameni"}],
            },
        }

        output = module.build_prediction_input(
            "2026-07-05",
            fixtures,
            odds_by_fixture={},
            source_by_fixture=source_by_fixture,
        )

        self.assertEqual(output["matches"][0]["source_refs"], ["https://example.test/canada"])
        self.assertEqual(output["matches"][0]["availability_candidates"], [{"player": "Alphonso Davies"}])
        self.assertEqual(output["matches"][1]["source_refs"], ["https://example.test/france"])
        self.assertEqual(output["matches"][1]["availability_candidates"], [{"player": "Aurelien Tchouameni"}])

    def test_discover_and_extract_sources_writes_discovered_source_audit(self):
        module = self.load_module()
        old_search = module.network.tavily_search
        old_audit = module.network.audit_tavily
        try:
            module.network.tavily_search = lambda *args, **kwargs: {
                "results": [
                    {
                        "title": "Canada Morocco team news",
                        "url": "https://www.espn.com/soccer/story/team-news",
                        "content": "injury update",
                        "score": 0.9,
                    }
                ],
                "usage": {"credits": 1},
            }

            def fake_audit(targets, out_dir, **kwargs):
                return (
                    [
                        {
                            "scheme": "tavily",
                            "url": targets[0].url,
                            "teams": targets[0].teams,
                            "source_labels": targets[0].source_labels,
                            "source_quality": {"score": 60, "tier": "mainstream"},
                            "ok": True,
                            "status": 200,
                            "content_type": "text/markdown",
                            "raw_chars": 25,
                            "error": "",
                            "tavily_error": "",
                            "elapsed_ms": 1,
                            "content_chars": 25,
                            "player_hits": [],
                            "player_misses": [],
                            "player_hit_count": 0,
                            "player_total": 0,
                            "term_hits": ["injury"],
                            "term_hit_count": 1,
                            "evidence_snippet_count": 0,
                            "evidence": [],
                            "evidence_chars": 0,
                        }
                    ],
                    {"usage": {"credits": 1}, "response_time": 0.1},
                )

            module.network.audit_tavily = fake_audit
            with tempfile.TemporaryDirectory() as tmp:
                result = module.discover_and_extract_sources(
                    ["Canada Morocco World Cup team news"],
                    Path(tmp),
                    api_key="tvly-test",
                    players=["Alphonso Davies"],
                    match_label="Canada;Morocco",
                    fixture_id=1567824,
                    home="Canada",
                    away="Morocco",
                )
                discovered = json.loads((Path(tmp) / "discovered_sources.json").read_text(encoding="utf-8"))

        finally:
            module.network.tavily_search = old_search
            module.network.audit_tavily = old_audit

        self.assertEqual(discovered["results"][0]["fixture_id"], 1567824)
        self.assertEqual(result["records"][0]["home"], "Canada")

    def test_discover_and_extract_sources_filters_records_unrelated_to_fixture(self):
        module = self.load_module()
        old_search = module.network.tavily_search
        old_audit = module.network.audit_tavily
        try:
            module.network.tavily_search = lambda *args, **kwargs: {
                "results": [
                    {
                        "title": "Ecuador defender suspended after red card",
                        "url": "https://apnews.com/article/ecuador-red-card",
                        "content": "Ecuador red card and suspension news.",
                        "score": 0.9,
                    },
                    {
                        "title": "France team news before Paraguay clash",
                        "url": "https://www.reuters.com/sports/soccer/france-paraguay-team-news",
                        "content": "France and Paraguay injury update.",
                        "score": 0.8,
                    },
                ],
                "usage": {"credits": 1},
            }
            module.network.audit_tavily = lambda targets, out_dir, **kwargs: ([], {"usage": {"credits": 1}, "response_time": 0.1})
            with tempfile.TemporaryDirectory() as tmp:
                result = module.discover_and_extract_sources(
                    ["Paraguay France World Cup team news"],
                    Path(tmp),
                    api_key="tvly-test",
                    match_label="Paraguay;France",
                    fixture_id=1569870,
                    home="Paraguay",
                    away="France",
                )

        finally:
            module.network.tavily_search = old_search
            module.network.audit_tavily = old_audit

        self.assertEqual([record["url"] for record in result["records"]], ["https://www.reuters.com/sports/soccer/france-paraguay-team-news"])

    def test_weather_context_uses_structured_forecast(self):
        module = self.load_module()

        def fake_fetch(url, timeout=30):
            if "geocoding-api" in url:
                return {"results": [{"latitude": 29.7604, "longitude": -95.3698, "name": "Houston", "country": "United States"}]}
            return {
                "hourly": {
                    "time": ["2026-07-04T12:00", "2026-07-04T13:00"],
                    "temperature_2m": [36.0, 37.0],
                    "relative_humidity_2m": [55, 50],
                    "precipitation_probability": [20, 30],
                    "wind_speed_10m": [12.5, 15.0],
                    "weather_code": [3, 95],
                }
            }

        context = module.fetch_weather_context(
            {"name": "NRG Stadium", "city": "Houston"},
            "2026-07-04T17:00:00+00:00",
            "America/Chicago",
            fetch_json=fake_fetch,
        )

        self.assertEqual(context["status"], "已获取")
        self.assertEqual(context["city"], "Houston")
        self.assertEqual(context["temperature_c"], 36.0)

    def test_ensure_rosters_skips_existing_snapshot(self):
        module = self.load_module()
        with tempfile.TemporaryDirectory() as tmp:
            existing = Path(tmp) / "2026-07-05" / "all_rosters.csv"
            existing.parent.mkdir(parents=True)
            existing.write_text("team,player\nCanada,Alphonso Davies\n", encoding="utf-8")

            ran = module.ensure_rosters("2026-07-05", Path(tmp), runner=lambda *args, **kwargs: None)

        self.assertFalse(ran)


if __name__ == "__main__":
    unittest.main()
