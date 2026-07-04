import importlib.util
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "worldcup-match-predictor" / "scripts" / "api_football_odds.py"


def bookmaker(name, bets):
    return {
        "id": 1,
        "name": name,
        "bets": [
            {"id": index, "name": market, "values": values}
            for index, (market, values) in enumerate(bets.items(), start=1)
        ],
    }


class ApiFootballOddsTests(unittest.TestCase):
    def load_module(self):
        spec = importlib.util.spec_from_file_location("api_football_odds", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def sample_payload(self):
        return {
            "errors": [],
            "results": 1,
            "paging": {"current": 1, "total": 1},
            "response": [
                {
                    "league": {"id": 1, "name": "World Cup", "season": 2026},
                    "fixture": {"id": 1567824, "date": "2026-07-04T17:00:00+00:00"},
                    "update": "2026-07-04T04:02:31+00:00",
                    "bookmakers": [
                        bookmaker(
                            "Bet365",
                            {
                                "Match Winner": [
                                    {"value": "Home", "odd": "4.75"},
                                    {"value": "Draw", "odd": "3.40"},
                                    {"value": "Away", "odd": "1.85"},
                                ],
                                "Handicap Result": [
                                    {"value": "Home +1", "odd": "1.91"},
                                    {"value": "Draw +1", "odd": "3.60"},
                                    {"value": "Away +1", "odd": "3.50"},
                                ],
                                "Goals Over/Under": [
                                    {"value": "Over 2.5", "odd": "2.20"},
                                    {"value": "Under 2.5", "odd": "1.67"},
                                ],
                                "Exact Score": [
                                    {"value": "0:1", "odd": "6.50"},
                                    {"value": "1:1", "odd": "6.50"},
                                    {"value": "0:0", "odd": "8.00"},
                                ],
                            },
                        ),
                        bookmaker(
                            "Pinnacle",
                            {
                                "Match Winner": [
                                    {"value": "Home", "odd": "4.64"},
                                    {"value": "Draw", "odd": "3.43"},
                                    {"value": "Away", "odd": "1.89"},
                                ],
                                "Goals Over/Under": [
                                    {"value": "Over 2.5", "odd": "2.21"},
                                    {"value": "Under 2.5", "odd": "1.71"},
                                ],
                                "Exact Score": [
                                    {"value": "0:1", "odd": "5.75"},
                                    {"value": "1:1", "odd": "6.60"},
                                ],
                            },
                        ),
                    ],
                }
            ],
        }

    def test_normalizes_bet365_main_path_and_pinnacle_calibration(self):
        module = self.load_module()

        normalized = module.normalize_odds_payload(self.sample_payload())

        self.assertEqual(normalized["primary"]["bookmaker"], "Bet365")
        self.assertTrue(normalized["primary"]["coverage"]["complete"])
        self.assertEqual(normalized["primary"]["markets"]["match_winner"]["values"][0]["value"], "Home")
        self.assertEqual(normalized["primary"]["markets"]["exact_score"]["top_values"][0]["value"], "0:1")
        self.assertEqual(normalized["calibration"]["bookmaker"], "Pinnacle")
        self.assertFalse(normalized["calibration"]["coverage"]["complete"])
        self.assertIn("handicap_result", normalized["calibration"]["coverage"]["missing"])

    def test_calibration_deltas_only_compare_shared_values(self):
        module = self.load_module()

        normalized = module.normalize_odds_payload(self.sample_payload())
        match_winner = normalized["calibration_deltas"]["match_winner"]

        self.assertEqual(match_winner[0]["value"], "Home")
        self.assertEqual(match_winner[0]["primary_odd"], "4.75")
        self.assertEqual(match_winner[0]["calibration_odd"], "4.64")
        self.assertIn("odd_delta", match_winner[0])
        self.assertNotIn("handicap_result", normalized["calibration_deltas"])

    def test_primary_markets_include_selected_main_lines_and_value_signals(self):
        module = self.load_module()

        normalized = module.normalize_odds_payload(
            self.sample_payload(),
            model_probabilities={"home": 0.25, "draw": 0.25, "away": 0.50},
        )

        self.assertEqual(normalized["primary"]["main_lines"]["match_winner"]["best_value"], "Home")
        self.assertEqual(normalized["primary"]["main_lines"]["goals_over_under"]["line"], "2.5")
        self.assertEqual(normalized["primary"]["main_lines"]["handicap_result"]["line"], "+1")
        self.assertGreater(normalized["value_signals"]["match_winner"][0]["value_ratio"], 1)

    def test_primary_missing_required_market_is_incomplete(self):
        module = self.load_module()
        payload = self.sample_payload()
        payload["response"][0]["bookmakers"][0]["bets"] = [
            bet for bet in payload["response"][0]["bookmakers"][0]["bets"] if bet["name"] != "Exact Score"
        ]

        normalized = module.normalize_odds_payload(payload)

        self.assertFalse(normalized["primary"]["coverage"]["complete"])
        self.assertEqual(normalized["primary"]["coverage"]["missing"], ["exact_score"])
        self.assertIn("Primary bookmaker Bet365 missing required markets: exact_score", normalized["warnings"])

    def test_resolve_fixture_filters_league_and_season_locally(self):
        module = self.load_module()
        calls = []

        def fake_get_json(path, params=None, api_key=None, timeout=30):
            calls.append(params)
            if params["date"] != "2026-07-04":
                return {"response": []}
            return {
                "response": [
                    {
                        "fixture": {"id": 1567824, "date": "2026-07-04T17:00:00+00:00"},
                        "league": {"id": 1, "name": "World Cup", "season": 2026},
                        "teams": {"home": {"name": "Canada"}, "away": {"name": "Morocco"}},
                    },
                    {
                        "fixture": {"id": 999},
                        "league": {"id": 2, "name": "Friendly", "season": 2026},
                        "teams": {"home": {"name": "Canada"}, "away": {"name": "Morocco"}},
                    },
                ]
            }

        old = module.http_get_json
        module.http_get_json = fake_get_json
        try:
            fixture = module.resolve_fixture(
                api_key="key",
                date="2026-07-05",
                home="Canada",
                away="Morocco",
                league=1,
                season=2026,
            )
        finally:
            module.http_get_json = old

        self.assertEqual(fixture["fixture"]["id"], 1567824)
        self.assertTrue(all("league" not in call and "season" not in call for call in calls))


if __name__ == "__main__":
    unittest.main()
