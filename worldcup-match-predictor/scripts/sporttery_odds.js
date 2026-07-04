#!/usr/bin/env node
"use strict";

const fs = require("fs");
const Module = require("module");
const os = require("os");
const path = require("path");

const URLS = {
  spf: "https://www.sporttery.cn/jc/jsq/zqspf/",
  bf: "https://www.sporttery.cn/jc/jsq/zqbf/",
  zjq: "https://www.sporttery.cn/jc/jsq/zqzjq/",
  bqc: "https://www.sporttery.cn/jc/jsq/zqbqc/",
};

const DEFAULT_CHROME_CANDIDATES = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
  "/usr/bin/google-chrome",
  "/usr/bin/google-chrome-stable",
  "/usr/bin/chromium",
  "/usr/bin/chromium-browser",
];

function resolveChromeExecutable(existsSync = fs.existsSync, env = process.env, candidates = DEFAULT_CHROME_CANDIDATES) {
  const envPath = env.PLAYWRIGHT_CHROME_EXECUTABLE || env.CHROME_EXECUTABLE;
  if (envPath && existsSync(envPath)) {
    return envPath;
  }
  return candidates.find((candidate) => existsSync(candidate)) || null;
}

function playwrightPackageCandidates(homeDir = os.homedir(), cwd = process.cwd()) {
  const candidates = [
    path.join(cwd, "node_modules", "playwright", "package.json"),
    path.join(__dirname, "..", "..", "node_modules", "playwright", "package.json"),
  ];
  const npxRoot = path.join(homeDir, ".npm", "_npx");
  try {
    for (const entry of fs.readdirSync(npxRoot)) {
      candidates.push(path.join(npxRoot, entry, "node_modules", "playwright", "package.json"));
    }
  } catch (error) {
    if (error.code !== "ENOENT") {
      throw error;
    }
  }
  return candidates;
}

function resolvePlaywrightPackageDir(existsSync = fs.existsSync, candidates = playwrightPackageCandidates()) {
  const packageJson = candidates.find((candidate) => existsSync(candidate));
  if (!packageJson) {
    return null;
  }
  return path.dirname(path.dirname(packageJson));
}

function requirePlaywright() {
  try {
    return require("playwright");
  } catch (error) {
    if (error.code !== "MODULE_NOT_FOUND") {
      throw error;
    }
  }

  const nodeModulesDir = resolvePlaywrightPackageDir();
  if (!nodeModulesDir) {
    throw new Error(
      "Cannot find module 'playwright'. Install it locally with `npm install playwright` "
      + "or run through an environment that provides Playwright."
    );
  }
  const localRequire = Module.createRequire(path.join(nodeModulesDir, ".codex-require.js"));
  return localRequire("playwright");
}

async function launchChromiumWithFallback(chromium, options = {}) {
  try {
    return await chromium.launch(options);
  } catch (error) {
    if (!/Executable doesn't exist|browser.*not found|Host system is missing dependencies/i.test(error.message)) {
      throw error;
    }
    const executablePath = resolveChromeExecutable();
    if (!executablePath) {
      throw error;
    }
    return chromium.launch({ ...options, executablePath });
  }
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function matchPattern(home, away) {
  return new RegExp(`${escapeRegExp(home)}\\s*VS\\s*${escapeRegExp(away)}`);
}

function extractMatchBlock(text, home, away) {
  const pattern = matchPattern(home, away);
  const match = pattern.exec(text);
  if (!match) {
    return "";
  }
  const startMarkers = [...text.slice(0, match.index).matchAll(/周[一二三四五六日]\s*\n?\d{3}/g)];
  const start = startMarkers.length ? startMarkers[startMarkers.length - 1].index : match.index;
  const after = text.slice(match.index + match[0].length);
  const next = /周[一二三四五六日]\s*\n?\d{3}/.exec(after);
  const end = next ? match.index + match[0].length + next.index : text.length;
  return text.slice(start, end);
}

function splitOddsBlob(blob) {
  return (blob.match(/\d+\.\d{2}/g) || []).map((odd) => Number(odd).toFixed(2));
}

function isOddsBlob(line) {
  return splitOddsBlob(line).length >= 3;
}

function parseSpfBlock(block) {
  if (!block) {
    return { spf: null, rspf: null };
  }
  const lines = block.split(/\n+/).map((line) => line.trim()).filter(Boolean);
  const handicaps = lines.filter((line) => /^(?:未开售|[+-]?\d+)$/.test(line));
  const oddsBlobs = lines.filter(isOddsBlob).map(splitOddsBlob);
  let oddsIndex = 0;
  const markets = [];

  for (const handicap of handicaps.slice(0, 2)) {
    if (handicap === "未开售") {
      markets.push({ handicap, odds: null });
      continue;
    }
    const odds = oddsBlobs[oddsIndex++] || null;
    markets.push({ handicap, odds });
  }

  let spf = null;
  let rspf = null;
  for (const market of markets) {
    if (!market.odds || market.odds.length < 3) {
      continue;
    }
    const payload = { win: market.odds[0], draw: market.odds[1], lose: market.odds[2] };
    if (market.handicap === "0" && !spf) {
      spf = payload;
    } else if (!rspf) {
      rspf = { handicap: market.handicap, ...payload };
    }
  }

  return { spf, rspf };
}

function parseGoalsBlock(block) {
  const labels = ["0球", "1球", "2球", "3球", "4球", "5球", "6球", "7+球"];
  const odds = splitOddsBlob(block).slice(0, labels.length);
  return odds
    .map((odd, index) => ({ goals: labels[index], odds: odd }))
    .sort((a, b) => Number(a.odds) - Number(b.odds));
}

function parseScoreBlock(block) {
  const scores = [];
  const regex = /((?:\d+:\d+)|胜其它|平其它|负其它)\s*\n\s*(\d+(?:\.\d+)?)/g;
  for (const match of block.matchAll(regex)) {
    const odd = Number(match[2]).toFixed(2);
    scores.push({ score: match[1], odds: odd });
  }
  return scores.sort((a, b) => Number(a.odds) - Number(b.odds));
}

function parseSportteryTexts(texts, matches) {
  return matches.map(({ home, away }) => {
    const spfBlock = extractMatchBlock(texts.spf || "", home, away);
    const zjqBlock = extractMatchBlock(texts.zjq || "", home, away);
    const bfBlock = extractMatchBlock(texts.bf || "", home, away);
    const { spf, rspf } = parseSpfBlock(spfBlock);
    return {
      home,
      away,
      spf,
      rspf,
      goals: parseGoalsBlock(zjqBlock),
      scores: parseScoreBlock(bfBlock),
    };
  });
}

async function fetchSportteryTexts(options = {}) {
  const { chromium } = requirePlaywright();
  const browser = await launchChromiumWithFallback(chromium, { headless: options.headless !== false });
  const texts = {};

  for (const [key, url] of Object.entries(URLS)) {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1200 } });
    try {
      await page.goto(url, { waitUntil: "domcontentloaded", timeout: options.timeout || 45000 });
      await page.waitForTimeout(options.waitMs || 8000);
      if (key === "bf") {
        await page.locator("span.folderTd").evaluateAll((els) => {
          for (const el of els.slice(0, 80)) {
            if ((el.textContent || "").trim() === "+") {
              el.click();
            }
          }
        }).catch(() => null);
        await page.waitForTimeout(2000);
      }
      texts[key] = await page.evaluate(() => document.body.innerText);
    } finally {
      await page.close().catch(() => null);
    }
  }

  await browser.close();
  return texts;
}

function parseArgs(argv) {
  const args = {};
  for (let i = 2; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--out") {
      args.out = argv[++i];
    } else if (arg === "--matches") {
      args.matches = argv[++i];
    }
  }
  return args;
}

function parseMatchArgs(value) {
  if (!value) {
    return [];
  }
  return value.split(",").map((item) => {
    const [home, away] = item.split(":");
    if (!home || !away) {
      throw new Error(`Invalid match spec: ${item}`);
    }
    return { home, away };
  });
}

async function main() {
  const args = parseArgs(process.argv);
  const matches = parseMatchArgs(args.matches);
  const texts = await fetchSportteryTexts();
  const output = {
    fetchedAt: new Date().toISOString(),
    texts,
    parsed: matches.length ? parseSportteryTexts(texts, matches) : [],
  };
  const json = JSON.stringify(output, null, 2);
  if (args.out) {
    fs.writeFileSync(args.out, `${json}\n`, "utf8");
  } else {
    process.stdout.write(`${json}\n`);
  }
}

module.exports = {
  extractMatchBlock,
  fetchSportteryTexts,
  launchChromiumWithFallback,
  parseSportteryTexts,
  resolveChromeExecutable,
  resolvePlaywrightPackageDir,
  splitOddsBlob,
};

if (require.main === module) {
  main().catch((error) => {
    console.error(`sporttery_odds.js failed: ${error.message}`);
    process.exit(1);
  });
}
