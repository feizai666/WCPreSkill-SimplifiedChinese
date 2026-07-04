# Tavily A/B Network Eval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compare the current non-Tavily network acquisition path against a Tavily Extract path for the same 2026-07-04 World Cup prediction workflow.

**Architecture:** Keep prediction logic unchanged. Add a small audit script that reads the same availability source ledger, fetches article content through Tavily Extract by default, can still run local HTTP for A/B baselines, records structured metrics, and writes logs under `experiments/tavily_ab/`.

**Tech Stack:** Python standard library, existing CSV ledgers, existing report JSON/PNG generator, existing Playwright Sporttery script.

## Global Constraints

- Tavily Extract is now the default path for known ordinary news URLs after the A/B comparison passed.
- Do not store Tavily API keys in files, command logs, or committed output.
- Keep old-scheme and Tavily-scheme outputs in separate experiment directories.
- Compare the same match date, same three matches, same source URL set, and same metric fields.

---

### Task 1: Network Fetch Audit Tool

**Files:**
- Create: `worldcup-match-predictor/scripts/network_fetch_audit.py`
- Test: `tests/test_network_fetch_audit.py`

**Interfaces:**
- Consumes: `availability_overrides.csv` with `source_url`, `player`, `team`, and source fields.
- Produces: `network_events.jsonl`, `network_summary.json`, and extracted content files for each URL.

- [x] **Step 1: Write focused tests**

Tests cover source URL grouping, player/term hit metrics, and Tavily request header construction without real network calls.

- [x] **Step 2: Implement minimal audit script**

The script defaults to `--scheme tavily`, still supports `--scheme local-http` for baselines, and reads Tavily API keys only from `.env` / environment variables.

- [x] **Step 3: Run tests**

Run: `python3 tests/test_network_fetch_audit.py`

- [x] **Step 4: Run old-scheme audit**

Run local HTTP extraction against `data/rosters/2026-07-04/availability_overrides.csv`.

- [x] **Step 5: Run Tavily audit after key is provided**

Run Tavily extraction against the same CSV and compare outputs.
