import importlib.util
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
