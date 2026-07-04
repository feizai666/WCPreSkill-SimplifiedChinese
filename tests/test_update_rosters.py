import subprocess
import tempfile
import unittest
import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "worldcup-match-predictor" / "scripts" / "update_rosters.py"


class UpdateRostersTests(unittest.TestCase):
    def load_module(self):
        spec = importlib.util.spec_from_file_location("update_rosters", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_empty_source_text_fails_before_overwriting_existing_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "empty_source.txt"
            source.write_text("", encoding="utf-8")

            output_dir = root / "rosters" / "2026-07-04"
            output_dir.mkdir(parents=True)
            source_snapshot = output_dir / "source_espn_squads.txt"
            roster_snapshot = output_dir / "all_rosters.csv"
            source_snapshot.write_text("existing source", encoding="utf-8")
            roster_snapshot.write_text("existing roster", encoding="utf-8")

            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "--date",
                    "2026-07-04",
                    "--output-root",
                    str(root / "rosters"),
                    "--source-text",
                    str(source),
                ],
                cwd=REPO_ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("source is empty", result.stderr)
            self.assertEqual(source_snapshot.read_text(encoding="utf-8"), "existing source")
            self.assertEqual(roster_snapshot.read_text(encoding="utf-8"), "existing roster")

    def test_latest_valid_cached_source_can_replace_blocked_live_fetch(self):
        module = self.load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "rosters"
            cache_dir = root / "2026-07-03"
            cache_dir.mkdir(parents=True)
            cached_source = REPO_ROOT / "data/rosters/2026-07-03/source_espn_squads.txt"
            (cache_dir / "source_espn_squads.txt").write_text(
                cached_source.read_text(encoding="utf-8"),
                encoding="utf-8",
            )

            text, rosters, source_path = module.load_latest_valid_cached_source(
                root,
                "2026-07-04",
                "2026-07-03 22:00",
            )

            self.assertEqual(source_path, cache_dir / "source_espn_squads.txt")
            self.assertGreater(len(text), 1000)
            self.assertGreater(len(rosters), 1000)
            self.assertEqual(len({row["team"] for row in rosters}), 48)

    def test_extracts_roster_text_from_espn_content_api_payload(self):
        module = self.load_module()
        payload = {
            "headlines": [
                {
                    "story": """
                        <p>Intro</p>
                        <h2>Australia</h2>
                        <p>Goalkeepers: Joe Gauci (Aston Villa)</p>
                        <p>Defenders: Harry Souttar (Sheffield United)</p>
                        <p>Midfielders: Jackson Irvine (St. Pauli)</p>
                        <p>Forwards: Nestory Irankunda (Bayern Munich)</p>
                        <p>Manager: Tony Popovic</p>
                        <h2>Egypt</h2>
                        <p>Goalkeepers: Mohamed El Shenawy (Al Ahly)</p>
                        <p>Defenders: Mohamed Hany (Al Ahly)</p>
                        <p>Midfielders: Hamdy Fathy (Al Wakrah)</p>
                        <p>Forwards: Mohamed Salah (Liverpool)</p>
                        <p>Manager: Hossam Hassan</p>
                    """
                }
            ]
        }

        text = module.extract_text_from_espn_api_payload(payload)

        self.assertIn("\nAustralia \n", text)
        self.assertIn("Mohamed Salah (Liverpool)", text)
        self.assertIn("Manager: Hossam Hassan", text)


if __name__ == "__main__":
    unittest.main()
