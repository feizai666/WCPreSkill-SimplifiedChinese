import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BOUNDARY_DOC = REPO_ROOT / "worldcup-match-predictor" / "references" / "capability-boundaries.md"
SKILL_DOC = REPO_ROOT / "worldcup-match-predictor" / "SKILL.md"
README = REPO_ROOT / "README.md"


class CapabilityBoundaryDocTests(unittest.TestCase):
    def test_boundary_doc_defines_script_and_llm_responsibilities(self):
        text = BOUNDARY_DOC.read_text(encoding="utf-8")

        self.assertIn("Scripts provide evidence", text)
        self.assertIn("The LLM interprets evidence", text)
        self.assertIn("availability_candidates.json/csv", text)
        self.assertIn("value_signals", text)
        self.assertIn("Must not", text)

    def test_skill_doc_requires_boundary_protocol(self):
        text = SKILL_DOC.read_text(encoding="utf-8")

        self.assertIn("references/capability-boundaries.md", text)
        self.assertIn("prediction_input.json", text)
        self.assertIn("统一输入包，不是最终预测", text)
        self.assertIn("availability_candidates.json/csv", text)
        self.assertIn("候选伤停/停赛线索，不是确认结论", text)
        self.assertIn("main_lines", text)
        self.assertIn("value_signals", text)

    def test_readme_links_boundary_doc(self):
        text = README.read_text(encoding="utf-8")

        self.assertIn("capability-boundaries.md", text)
        self.assertIn("脚本只负责抓取、过滤、校验、标准化、日志和渲染", text)
        self.assertIn("LLM 负责证据权衡、冲突处理、比分、概率、风险和投注建议", text)


if __name__ == "__main__":
    unittest.main()
