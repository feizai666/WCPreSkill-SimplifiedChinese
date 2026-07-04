import json
import subprocess
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "worldcup-match-predictor" / "scripts" / "sporttery_odds.js"


class SportteryOddsTests(unittest.TestCase):
    def run_node(self, code):
        result = subprocess.run(
            ["node", "-e", code],
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_parse_concatenated_official_odds_text(self):
        self.assertTrue(SCRIPT.exists())
        code = textwrap.dedent(
            r"""
            const { parseSportteryTexts } = require('./worldcup-match-predictor/scripts/sporttery_odds.js');
            const data = parseSportteryTexts({
              spf: `周五\n086\t世界杯\t07-04\n02:00\t[世界杯2]澳大利亚VS埃及[世界杯2]\t\n0\n+1\n\t\n3.202.702.24\n1.483.525.90\n\t同奖\n周五\n087\t世界杯\t07-04\n06:00\t[世界杯1]阿根廷VS佛得角[世界杯2]\t\n未开售\n-2\n\t\n------\n2.033.452.88\n\t同奖\n周五\n088\t世界杯\t07-04\n09:30\t[世界杯1]哥伦比亚VS加纳[世界杯3]\t\n0\n-1\n\t\n1.264.508.80\n2.212.793.14\n\t同奖`,
              zjq: `周五\n086\t世界杯\t07-04\n02:00\t[世界杯2]澳大利亚VS埃及[世界杯2]\t6.503.702.753.908.9019.0035.0060.00\t同奖\n周五\n087\t世界杯\t07-04\n06:00\t[世界杯1]阿根廷VS佛得角[世界杯2]\t18.006.304.003.004.608.0015.0021.00\t同奖\n周五\n088\t世界杯\t07-04\n09:30\t[世界杯1]哥伦比亚VS加纳[世界杯3]\t11.004.802.803.106.9014.0027.0045.00\t同奖`,
              bf: `周五086\t世界杯\t07-04 02:00\t[世界杯2]澳大利亚VS 埃及[世界杯2]\t同奖\n胜\n1:0\n8.50\n平\n0:0\n6.50\n1:1\n4.30\n负\n0:1\n6.25\n1:2\n6.25\n周五087\t世界杯\t07-04 06:00\t[世界杯1]阿根廷VS 佛得角[世界杯2]\t同奖\n胜\n2:0\n4.20\n3:0\n3.85\n平\n0:0\n18.00\n负\n0:1\n36.00\n周五088\t世界杯\t07-04 09:30\t[世界杯1]哥伦比亚VS 加纳[世界杯3]\t同奖\n胜\n2:0\n4.20\n2:1\n4.05\n平\n1:1\n8.00\n负\n0:1\n21.00`
            }, [
              { home: '澳大利亚', away: '埃及' },
              { home: '阿根廷', away: '佛得角' },
              { home: '哥伦比亚', away: '加纳' }
            ]);
            console.log(JSON.stringify(data));
            """
        )

        parsed = json.loads(self.run_node(code))
        self.assertEqual(parsed[0]["spf"], {"win": "3.20", "draw": "2.70", "lose": "2.24"})
        self.assertEqual(parsed[0]["rspf"], {"handicap": "+1", "win": "1.48", "draw": "3.52", "lose": "5.90"})
        self.assertEqual(parsed[0]["goals"][0], {"goals": "2球", "odds": "2.75"})
        self.assertEqual(parsed[0]["scores"][0], {"score": "1:1", "odds": "4.30"})
        self.assertIsNone(parsed[1]["spf"])
        self.assertEqual(parsed[1]["rspf"]["handicap"], "-2")
        self.assertEqual(parsed[1]["scores"][0], {"score": "3:0", "odds": "3.85"})
        self.assertEqual(parsed[2]["scores"][0], {"score": "2:1", "odds": "4.05"})

    def test_resolve_chrome_executable_falls_back_to_installed_browser(self):
        self.assertTrue(SCRIPT.exists())
        code = textwrap.dedent(
            r"""
            const { resolveChromeExecutable } = require('./worldcup-match-predictor/scripts/sporttery_odds.js');
            const resolved = resolveChromeExecutable(
              (candidate) => candidate === '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
              {},
              [
                '/Applications/Chromium.app/Contents/MacOS/Chromium',
                '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
              ]
            );
            console.log(resolved);
            """
        )

        self.assertEqual(
            self.run_node(code).strip(),
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        )

    def test_resolve_playwright_package_dir_uses_fallback_candidates(self):
        self.assertTrue(SCRIPT.exists())
        code = textwrap.dedent(
            r"""
            const { resolvePlaywrightPackageDir } = require('./worldcup-match-predictor/scripts/sporttery_odds.js');
            const resolved = resolvePlaywrightPackageDir(
              (candidate) => candidate === '/home/me/.npm/_npx/run/node_modules/playwright/package.json',
              [
                '/repo/node_modules/playwright/package.json',
                '/home/me/.npm/_npx/run/node_modules/playwright/package.json'
              ]
            );
            console.log(resolved);
            """
        )

        self.assertEqual(
            self.run_node(code).strip(),
            "/home/me/.npm/_npx/run/node_modules",
        )


if __name__ == "__main__":
    unittest.main()
