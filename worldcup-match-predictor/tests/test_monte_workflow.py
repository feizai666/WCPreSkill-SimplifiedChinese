import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


consensus = load_module("monte_consensus", SCRIPTS / "monte_consensus.py")
renderer = load_module("generate_report", SCRIPTS / "generate_report.py")


class MonteConsensusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sample = json.loads((SCRIPTS / "sample_data.json").read_text(encoding="utf-8"))

    def test_sample_aggregates_with_expected_consensus(self):
        data = copy.deepcopy(self.sample)
        self.assertEqual(consensus.aggregate_report(data), 1)

        analysis = data["matches"][0]["multi_agent_analysis"]
        self.assertAlmostEqual(analysis["consensus"]["p90"]["home"], 0.5625)
        self.assertEqual(analysis["consensus"]["top_scores"][0]["score"], "1-0")
        self.assertEqual(analysis["agreement"]["level"], "低分歧")
        self.assertEqual(set(analysis["effective_weights"].values()), {0.25})

    def test_probability_mismatch_is_rejected(self):
        data = copy.deepcopy(self.sample)
        prediction = data["matches"][0]["multi_agent_analysis"]["agents"][0]["final"]
        prediction["p90"] = {"home": 0.60, "draw": 0.25, "away": 0.15}

        with self.assertRaises(consensus.ConsensusError):
            consensus.aggregate_report(data)

    def test_unknown_agent_reference_is_rejected(self):
        data = copy.deepcopy(self.sample)
        data["matches"][0]["multi_agent_analysis"]["challenges"][0]["to"] = "missing"

        with self.assertRaises(consensus.ConsensusError):
            consensus.aggregate_report(data)

    def test_missing_final_revision_is_rejected(self):
        data = copy.deepcopy(self.sample)
        del data["matches"][0]["multi_agent_analysis"]["agents"][1]["final"]

        with self.assertRaises(consensus.ConsensusError):
            consensus.aggregate_report(data)

    def test_score_spellings_are_canonicalized_and_zero_keys_ignored(self):
        data = copy.deepcopy(self.sample)
        analysis = data["matches"][0]["multi_agent_analysis"]
        spellings = ["1-0", "1:0", " 1 - 0 ", "01-00"]
        for agent, spelling in zip(analysis["agents"], spellings):
            prediction = {
                "lean": "主胜 1-0",
                "p90": {"home": 1.0, "draw": 0.0, "away": 0.0},
                "score_grid": {spelling: 1.0, "9-9": 0.0},
            }
            agent["initial"] = copy.deepcopy(prediction)
            agent["final"] = {
                **prediction,
                "revision_reason": "保持原判断",
            }

        consensus.aggregate_report(data)
        result = analysis["consensus"]
        self.assertEqual(result["score_grid"], {"1-0": 1.0})
        self.assertEqual(analysis["agreement"]["jsd_score"], 0.0)

    def test_tolerance_inputs_are_normalized_before_statistics(self):
        data = copy.deepcopy(self.sample)
        analysis = data["matches"][0]["multi_agent_analysis"]
        for agent in analysis["agents"]:
            prediction = {
                "lean": "主胜",
                "p90": {"home": 0.604, "draw": 0.25, "away": 0.15},
                "score_grid": {"1-0": 0.604, "0-0": 0.25, "0-1": 0.15},
            }
            agent["initial"] = copy.deepcopy(prediction)
            agent["final"] = {
                **prediction,
                "revision_reason": "保持原判断",
            }

        consensus.aggregate_report(data)
        result = analysis["consensus"]
        self.assertAlmostEqual(sum(result["p90"].values()), 1.0, places=5)
        self.assertAlmostEqual(sum(result["score_grid"].values()), 1.0, places=5)
        self.assertLessEqual(result["predictive_entropy"], 1.0)

    def test_high_disagreement_downgrades_steady_and_high_confidence(self):
        data = copy.deepcopy(self.sample)
        match = data["matches"][0]
        match["risk"] = "稳胆"
        match["confidence"] = "高"
        analysis = match["multi_agent_analysis"]
        for index, agent in enumerate(analysis["agents"]):
            if index < 2:
                p90 = {"home": 1.0, "draw": 0.0, "away": 0.0}
                grid = {"1-0": 1.0}
            else:
                p90 = {"home": 0.0, "draw": 0.0, "away": 1.0}
                grid = {"0-1": 1.0}
            agent["initial"] = {"lean": "极端测试", "p90": p90, "score_grid": grid}
            agent["final"] = {
                "lean": "极端测试",
                "p90": p90,
                "score_grid": grid,
                "revision_reason": "保持原判断",
            }

        consensus.aggregate_report(data)
        self.assertEqual(analysis["agreement"]["level"], "高分歧")
        self.assertEqual(match["risk"], "高风险")
        self.assertEqual(match["confidence"], "低")
        self.assertEqual(analysis["guardrails"][0]["rule"], "high_disagreement_no_steady")

    def test_strict_mode_rejects_missing_panel_but_legacy_mode_skips_it(self):
        data = copy.deepcopy(self.sample)
        del data["matches"][0]["multi_agent_analysis"]
        self.assertEqual(consensus.aggregate_report(data), 0)
        with self.assertRaises(consensus.ConsensusError):
            consensus.aggregate_report(data, require_panel=True)

    def test_strict_mode_requires_visible_audit_process(self):
        data = copy.deepcopy(self.sample)
        analysis = data["matches"][0]["multi_agent_analysis"]
        del analysis["audit_display"]

        self.assertEqual(consensus.aggregate_report(data), 1)
        with self.assertRaises(consensus.ConsensusError):
            consensus.aggregate_report(data, require_panel=True)

    def test_strict_audit_must_cover_every_voting_agent(self):
        data = copy.deepcopy(self.sample)
        audit = data["matches"][0]["multi_agent_analysis"]["audit_display"]
        audit["agents"].pop()

        with self.assertRaises(consensus.ConsensusError):
            consensus.aggregate_report(data, require_panel=True)

    def test_historical_weights_allow_bounded_non_equal_pool(self):
        data = copy.deepcopy(self.sample)
        analysis = data["matches"][0]["multi_agent_analysis"]
        analysis["weighting_mode"] = "historical"
        analysis["history_sample_size"] = 30
        for agent, weight in zip(analysis["agents"], [0.35, 0.25, 0.20, 0.20]):
            agent["weight"] = weight

        consensus.aggregate_report(data)
        self.assertEqual(
            analysis["effective_weights"],
            {"qbase": 0.35, "monte": 0.25, "tact": 0.20, "squad": 0.20},
        )


class MonteRendererTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sample = json.loads((SCRIPTS / "sample_data.json").read_text(encoding="utf-8"))

    def test_display_items_are_opt_in_sanitized_and_capped(self):
        match = {
            "multi_agent_analysis": {
                "display": {
                    "enabled": True,
                    "items": [" A ", "B", 3, "", "C", "D", "E"],
                }
            }
        }
        self.assertEqual(renderer.multi_agent_display_items(match), ["A", "B", "C", "D"])
        match["multi_agent_analysis"]["display"]["enabled"] = False
        self.assertEqual(renderer.multi_agent_display_items(match), [])

    def test_audit_display_is_opt_in_and_sanitized(self):
        match = copy.deepcopy(self.sample["matches"][0])
        audit = renderer.multi_agent_audit_display(match)
        self.assertIsNotNone(audit)
        self.assertEqual(len(audit["agents"]), 4)
        self.assertIn("qbase", {agent["id"] for agent in audit["agents"]})

        match["multi_agent_analysis"]["audit_display"]["enabled"] = False
        self.assertIsNone(renderer.multi_agent_audit_display(match))

    def test_sample_renders_three_300_dpi_cards(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir)
            reflection_path = renderer.render_reflection_png(
                self.sample,
                self.sample["reflection"],
                output_dir,
            )
            match_path = renderer.render_match_png(
                self.sample,
                self.sample["matches"][0],
                1,
                output_dir,
            )
            audit_path = renderer.render_audit_png(
                self.sample,
                self.sample["matches"][0],
                1,
                output_dir,
            )

            self.assertTrue(reflection_path.exists())
            self.assertTrue(match_path.exists())
            self.assertIsNotNone(audit_path)
            self.assertTrue(audit_path.exists())
            self.assertEqual(len(list(output_dir.glob("*.png"))), 3)
            for path in (match_path, audit_path):
                with Image.open(path) as image:
                    dpi = image.info.get("dpi")
                    self.assertIsNotNone(dpi)
                    self.assertAlmostEqual(dpi[0], 300, delta=1)
                    self.assertGreater(image.height, 2500)


if __name__ == "__main__":
    unittest.main()
