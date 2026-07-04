import importlib.util
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "worldcup-match-predictor" / "scripts" / "generate_report.py"


class GenerateReportOddsDisplayTests(unittest.TestCase):
    def load_module(self):
        spec = importlib.util.spec_from_file_location("generate_report", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_api_football_odds_display_prefers_bet365_primary(self):
        module = self.load_module()
        odds = {
            "primary": {
                "bookmaker": "Bet365",
                "main_lines": {
                    "handicap_result": {"line": "+1", "home": "1.91", "draw": "3.60", "away": "3.50"},
                    "goals_over_under": {"line": "2.5", "over": "2.20", "under": "1.67"},
                },
                "markets": {
                    "match_winner": {
                        "values": [
                            {"value": "Home", "odd": "4.75"},
                            {"value": "Draw", "odd": "3.40"},
                            {"value": "Away", "odd": "1.85"},
                        ]
                    },
                    "handicap_result": {
                        "values": [
                            {"value": "Home -1", "odd": "12.00"},
                            {"value": "Draw -1", "odd": "6.00"},
                            {"value": "Away -1", "odd": "1.18"},
                            {"value": "Home +1", "odd": "1.91"},
                            {"value": "Draw +1", "odd": "3.60"},
                            {"value": "Away +1", "odd": "3.50"},
                        ]
                    },
                    "goals_over_under": {
                        "values": [
                            {"value": "Over 2.5", "odd": "2.20"},
                            {"value": "Under 2.5", "odd": "1.67"},
                        ]
                    },
                    "exact_score": {
                        "top_values": [
                            {"value": "0:1", "odd": "6.50"},
                            {"value": "1:1", "odd": "6.50"},
                            {"value": "0:0", "odd": "8.00"},
                        ]
                    },
                },
            },
            "calibration": {"bookmaker": "Pinnacle", "coverage": {"missing": ["handicap_result"]}},
        }

        rows = module.odds_table_rows(odds)

        self.assertEqual(rows[0], ("独赢", "4.75", "3.40", "1.85"))
        self.assertEqual(rows[1], ("让球(+1)", "1.91", "3.60", "3.50"))
        self.assertEqual(module.odds_goals_text(odds), "2.5球：大 2.20 / 小 1.67")
        self.assertEqual(module.odds_score_text(odds), "0:1(6.50)、1:1(6.50)、0:0(8.00)")

    def test_legacy_sporttery_odds_display_still_works(self):
        module = self.load_module()
        odds = {
            "spf": {"win": "1.45", "draw": "4.20", "lose": "6.80"},
            "rspf": {"handicap": "-1", "win": "2.30", "draw": "3.40", "lose": "2.55"},
            "goals": "2球(3.40)最低",
            "score": "2:0(5.5)",
        }

        self.assertEqual(module.odds_table_rows(odds)[0], ("独赢", "1.45", "4.20", "6.80"))
        self.assertEqual(module.odds_goals_text(odds), "2球(3.40)最低")
        self.assertEqual(module.odds_score_text(odds), "2:0(5.5)")


if __name__ == "__main__":
    unittest.main()
