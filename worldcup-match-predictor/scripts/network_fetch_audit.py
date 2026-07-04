#!/usr/bin/env python3
"""Audit network content extraction for World Cup prediction sources."""

import argparse
import csv
import hashlib
import json
import os
import re
import ssl
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path


DEFAULT_TERMS = [
    "injury",
    "injured",
    "hamstring",
    "suspended",
    "suspension",
    "fit",
    "fitness",
    "doubt",
    "ruled out",
    "unavailable",
    "knock",
    "trained",
    "replacement",
    "lineup",
    "line-ups",
    "team news",
]

MAX_SNIPPETS_PER_URL = 8
DEFAULT_SNIPPET_CONTEXT_CHARS = 260

COMMON_SINGLE_TOKEN_NAMES = {
    "aaron",
    "alex",
    "andre",
    "andrew",
    "anthony",
    "ben",
    "bruno",
    "carlos",
    "chris",
    "christian",
    "daniel",
    "david",
    "diego",
    "eric",
    "frank",
    "gabriel",
    "harry",
    "james",
    "john",
    "jose",
    "juan",
    "junior",
    "kevin",
    "leon",
    "luis",
    "marco",
    "marcus",
    "mario",
    "martin",
    "michael",
    "mohamed",
    "mohammed",
    "nicolas",
    "oscar",
    "paul",
    "peter",
    "robert",
    "samuel",
    "thomas",
    "victor",
}

OFFICIAL_DOMAINS = (
    "fifa.com",
    "concacaf.com",
    "cafonline.com",
    "uefa.com",
    "the-afc.com",
    "conmebol.com",
)

MAINSTREAM_DOMAINS = (
    "espn.com",
    "bbc.com",
    "theguardian.com",
    "reuters.com",
    "apnews.com",
    "skysports.com",
    "cbssports.com",
    "sports.yahoo.com",
    "nytimes.com",
    "theathletic.com",
)

LOW_QUALITY_HINTS = (
    "free tips",
    "betting tips",
    "prediction",
    "picks",
    "accumulator",
    "best bets",
    "casino",
)


class ArticleTextExtractor(HTMLParser):
    skip_tags = {"script", "style", "noscript", "svg"}
    block_tags = {"article", "section", "div", "p", "li", "h1", "h2", "h3", "h4", "br"}

    def __init__(self):
        super().__init__()
        self.skip_depth = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in self.skip_tags:
            self.skip_depth += 1
        if tag in self.block_tags:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in self.skip_tags and self.skip_depth:
            self.skip_depth -= 1
        if tag in self.block_tags:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.skip_depth:
            return
        text = data.strip()
        if text:
            self.parts.append(text)

    def text(self):
        text = " ".join(self.parts)
        text = re.sub(r"\s+", " ", text)
        return text.strip()


@dataclass
class SourceTarget:
    url: str
    rows: list[dict] = field(default_factory=list)

    @property
    def players(self):
        return sorted({row.get("player", "") for row in self.rows if row.get("player")})

    @property
    def teams(self):
        return sorted({row.get("team", "") for row in self.rows if row.get("team")})

    @property
    def source_labels(self):
        return sorted({row.get("availability_source", "") for row in self.rows if row.get("availability_source")})


def normalize_text(value):
    return re.sub(r"\s+", " ", value or "").strip()


def ascii_fold(value):
    return (
        unicodedata.normalize("NFKD", value or "")
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )


def source_quality(record):
    url = normalize_text(record.get("url"))
    title = normalize_text(record.get("title"))
    content = normalize_text(record.get("content"))
    haystack = f"{url} {title} {content}".lower()
    domain = urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")
    score = 20
    tier = "other"

    if any(domain.endswith(item) for item in OFFICIAL_DOMAINS):
        score += 60
        tier = "official"
    elif any(domain.endswith(item) for item in MAINSTREAM_DOMAINS):
        score += 40
        tier = "mainstream"

    if any(term in haystack for term in ("team news", "injury", "suspension", "lineup", "squad", "referee")):
        score += 15
    if any(term in haystack for term in ("official", "match centre", "press conference")):
        score += 10
    if any(term in haystack for term in LOW_QUALITY_HINTS):
        score -= 30
    if re.search(r"/(?:odds|betting|tips|prediction|predictions)(?:/|$|-)", url.lower()):
        score -= 25

    score = max(0, min(100, score))
    if score < 30:
        tier = "low"
    elif tier == "other" and score >= 45:
        tier = "usable"
    return {"score": score, "tier": tier, "domain": domain}


def rank_candidate_records(records, min_quality_score=30):
    ranked = []
    for record in records:
        quality = source_quality(record)
        if quality["score"] < min_quality_score:
            continue
        enriched = {**record, "quality_score": quality["score"], "quality_tier": quality["tier"], "domain": quality["domain"]}
        ranked.append(enriched)
    return sorted(ranked, key=lambda item: (-item["quality_score"], item.get("score") or 0, item.get("url", "")))


def source_targets_from_records(records, team_label=""):
    targets = []
    for record in records:
        url = normalize_text(record.get("url"))
        if not url:
            continue
        targets.append(
            SourceTarget(
                url=url,
                rows=[
                    {
                        "team": team_label,
                        "player": "",
                        "availability_source": record.get("quality_tier", ""),
                        "source_url": url,
                    }
                ],
            )
        )
    return targets


def infer_availability_status(terms, text):
    haystack = f"{' '.join(terms or [])} {text or ''}".lower()
    if any(term in haystack for term in ("suspended", "suspension")):
        return "suspended"
    if any(term in haystack for term in ("ruled out", "unavailable", "out injured")):
        return "ruled_out"
    if any(term in haystack for term in ("doubt", "fitness", "knock")):
        return "fitness_doubt"
    if any(term in haystack for term in ("fit", "trained", "available")):
        return "available"
    return "needs_review"


def confidence_for_candidate(event, evidence):
    quality = event.get("source_quality") or {}
    score = quality.get("score", 0)
    terms = evidence.get("terms") or []
    if score >= 70 and terms:
        return "high"
    if score >= 40 and terms:
        return "medium"
    return "low"


def has_availability_signal(evidence):
    terms = set(evidence.get("terms") or [])
    text = ascii_fold(evidence.get("text") or "")
    strong_terms = {
        "injury",
        "injured",
        "hamstring",
        "suspended",
        "suspension",
        "fitness",
        "doubt",
        "unavailable",
        "out injured",
        "trained",
        "available",
    }
    if terms & strong_terms:
        return True
    if "fit" in terms and re.search(r"\bfit\b", text):
        return True
    if "ruled out" in terms:
        return any(
            phrase in text
            for phrase in (
                "injury",
                "injured",
                "thigh",
                "hamstring",
                "unavailable",
                "suspended",
                "suspension",
                "ruled out of",
                "ruled out for",
            )
        )
    if "knock" in terms:
        return re.search(r"\b(took|picked up|suffered|carrying|with|after)\s+(a\s+)?knock\b", text) is not None
    return False


def player_evidence_context(player, text, before_chars=20, after_chars=160):
    folded_text = ascii_fold(text)
    for alias in player_aliases(player):
        match = re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", folded_text)
        if match:
            start = max(0, match.start() - before_chars)
            end = min(len(text), match.end() + after_chars)
            return text[start:end]
    return ""


def availability_candidates(events):
    candidates = []
    seen = set()
    for event in events:
        if not event.get("ok"):
            continue
        for evidence in event.get("evidence", []):
            players = evidence.get("players") or []
            if not players:
                continue
            for player in players:
                context = player_evidence_context(player, evidence.get("text") or "")
                scoped_evidence = {"terms": term_hits(context), "text": context}
                if not context or not has_availability_signal(scoped_evidence):
                    continue
                status = infer_availability_status(scoped_evidence.get("terms") or [], context)
                key = (
                    ";".join(event.get("teams") or []),
                    player,
                    event.get("url"),
                    status,
                )
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(
                    {
                        "team": ";".join(event.get("teams") or []),
                        "player": player,
                        "availability_status": status,
                        "confidence": confidence_for_candidate(event, evidence),
                        "source_url": event.get("url"),
                        "source_quality_score": (event.get("source_quality") or {}).get("score"),
                        "source_quality_tier": (event.get("source_quality") or {}).get("tier"),
                        "evidence": context,
                    }
                )
    return candidates


def load_source_targets(csv_path):
    targets = {}
    with Path(csv_path).open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            url = normalize_text(row.get("source_url"))
            if not url:
                continue
            targets.setdefault(url, SourceTarget(url=url)).rows.append(row)
    return list(targets.values())


def player_aliases(player):
    folded = ascii_fold(player)
    tokens = [token for token in re.split(r"\s+", folded) if len(token) >= 4]
    aliases = {folded}
    if len(tokens) >= 2:
        last = tokens[-1]
        if len(last) >= 5 and last not in COMMON_SINGLE_TOKEN_NAMES:
            aliases.add(last)
    return sorted(alias for alias in aliases if alias)


def contains_alias(haystack, alias):
    return re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", haystack) is not None


def player_matches(text, players):
    haystack = ascii_fold(text)
    hits = []
    misses = []
    for player in players:
        if any(contains_alias(haystack, alias) for alias in player_aliases(player)):
            hits.append(player)
        else:
            misses.append(player)
    return hits, misses


def term_hits(text, terms=DEFAULT_TERMS):
    haystack = ascii_fold(text)
    hits = []
    for term in terms:
        folded = ascii_fold(term)
        if not folded:
            continue
        if contains_alias(haystack, folded):
            hits.append(term)
    return hits


def player_tokens(player):
    return player_aliases(player)


def is_boilerplate_snippet(text):
    folded = ascii_fold(text)
    hard_boilerplate = (
        "redirecturl",
        "#main-content",
        "skip to main content",
        "exclusive news data",
        "my news",
        "subscribe -",
        "press releases",
        "location=article-paragraph",
        "%2f",
        "ago soccer[",
    )
    if any(term in folded for term in hard_boilerplate):
        return True
    nav_terms = (
        "top stories",
        "nfl",
        "nhl",
        "tennis",
        "golf",
        "free agency",
        "navigation",
        "skip to main content",
        "sign in",
        "subscribe",
        "press releases",
        "exclusive news data",
        "my news",
    )
    return sum(1 for term in nav_terms if term in folded) >= 3


def evidence_snippets(text, target, context_chars=DEFAULT_SNIPPET_CONTEXT_CHARS, max_snippets=MAX_SNIPPETS_PER_URL):
    snippets = []
    seen_ranges = []
    haystack = ascii_fold(text)
    needles = []
    for player in target.players:
        for token in player_tokens(player):
            if token:
                needles.append(("player", player, token))
    for term in DEFAULT_TERMS:
        needles.append(("term", term, term))

    for kind, label, needle in needles:
        pattern = rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])"
        for match in re.finditer(pattern, haystack):
            start = max(0, match.start() - context_chars)
            end = min(len(text), match.end() + context_chars)
            if any(start <= old_end and end >= old_start for old_start, old_end in seen_ranges):
                continue
            snippet_text = text[start:end].strip()
            if is_boilerplate_snippet(snippet_text):
                continue
            snippet_lower = ascii_fold(snippet_text)
            matched_players = [
                player for player in target.players if any(token in snippet_lower for token in player_tokens(player))
            ]
            matched_terms = term_hits(snippet_text)
            if kind == "player" and not matched_terms:
                continue
            if kind == "term" and not matched_players:
                continue
            if matched_players and matched_terms and not has_availability_signal(
                {"terms": matched_terms, "text": snippet_text}
            ):
                continue
            snippets.append(
                {
                    "text": snippet_text,
                    "matched_players": matched_players,
                    "matched_terms": matched_terms,
                    "start": start,
                    "end": end,
                }
            )
            seen_ranges.append((start, end))
            if len(snippets) >= max_snippets:
                return snippets
    return snippets


def structured_evidence(snippets):
    return [
        {
            "players": snippet["matched_players"],
            "terms": snippet["matched_terms"],
            "text": snippet["text"],
        }
        for snippet in snippets
    ]


def content_metrics(text, target):
    player_hits, player_misses = player_matches(text, target.players)
    terms = term_hits(text)
    snippets = evidence_snippets(text, target)
    return {
        "content_chars": len(text),
        "player_hits": player_hits,
        "player_misses": player_misses,
        "player_hit_count": len(player_hits),
        "player_total": len(target.players),
        "term_hits": terms,
        "term_hit_count": len(terms),
        "evidence_snippet_count": len(snippets),
        "evidence": structured_evidence(snippets),
        "evidence_chars": sum(len(snippet["text"]) for snippet in snippets),
    }


def content_path_for(out_dir, url, suffix=".md"):
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return Path(out_dir) / "contents" / f"{digest}{suffix}"


def snippet_path_for(out_dir, url, suffix=".md"):
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return Path(out_dir) / "snippets" / f"{digest}{suffix}"


def local_http_extract(url, timeout=30):
    started = time.monotonic()
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
            raw = response.read(2_500_000)
            status = getattr(response, "status", None)
            content_type = response.headers.get("content-type", "")
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "status": exc.code,
            "content_type": exc.headers.get("content-type", ""),
            "raw_chars": 0,
            "text": "",
            "error": f"HTTPError: {exc.code} {exc.reason}",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": None,
            "content_type": "",
            "raw_chars": 0,
            "text": "",
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }

    charset = "utf-8"
    match = re.search(r"charset=([^;]+)", content_type, re.I)
    if match:
        charset = match.group(1).strip()
    html = raw.decode(charset, errors="replace")
    parser = ArticleTextExtractor()
    parser.feed(html)
    text = parser.text()
    ok = status == 200 and bool(text)
    return {
        "ok": ok,
        "status": status,
        "content_type": content_type,
        "raw_chars": len(html),
        "text": text,
        "error": "" if ok else "empty extracted text" if not text else f"non-200 status: {status}",
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


def tavily_headers(api_key=None, keyless=False):
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    elif keyless:
        headers["X-Tavily-Access-Mode"] = "keyless"
    else:
        raise ValueError("Tavily requires an API key or --tavily-keyless.")
    return headers


def require_tavily_auth(env_name="TAVILY_API_KEY", keyless=False):
    api_key = os.environ.get(env_name)
    if api_key or keyless:
        return api_key
    raise SystemExit(
        f"Missing {env_name}. Add {env_name}=tvly-... to .env, "
        "or pass --tavily-keyless for trial mode, or pass --scheme local-http for diagnostics."
    )


def chunked(items, size):
    if size < 1:
        raise ValueError("batch size must be >= 1")
    for index in range(0, len(items), size):
        yield items[index : index + size]


def tavily_extract_batch(urls, api_key=None, keyless=False, timeout=60, extract_depth="basic"):
    started = time.monotonic()
    body = json.dumps(
        {
            "urls": urls,
            "extract_depth": extract_depth,
            "format": "markdown",
            "timeout": min(max(timeout, 1), 60),
            "include_usage": True,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://api.tavily.com/extract",
        data=body,
        headers=tavily_headers(api_key=api_key, keyless=keyless),
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout + 15, context=ssl.create_default_context()) as response:
        payload = json.loads(response.read().decode("utf-8"))
    payload["_elapsed_ms"] = int((time.monotonic() - started) * 1000)
    return payload


def tavily_search(query, api_key=None, keyless=False, timeout=30, max_results=8, search_depth="basic", topic="news"):
    started = time.monotonic()
    body = json.dumps(
        {
            "query": query,
            "search_depth": search_depth,
            "topic": topic,
            "max_results": max_results,
            "include_answer": False,
            "include_raw_content": False,
            "include_usage": True,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://api.tavily.com/search",
        data=body,
        headers=tavily_headers(api_key=api_key, keyless=keyless),
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout + 15, context=ssl.create_default_context()) as response:
        payload = json.loads(response.read().decode("utf-8"))
    payload["_elapsed_ms"] = int((time.monotonic() - started) * 1000)
    return payload


def search_candidate_records(query, payload):
    records = []
    seen = set()
    for result in payload.get("results", []):
        url = normalize_text(result.get("url"))
        if not url or url in seen:
            continue
        seen.add(url)
        records.append(
            {
                "query": query,
                "title": result.get("title", ""),
                "url": url,
                "content": result.get("content", ""),
                "score": result.get("score"),
            }
        )
    return records


def write_discovered_sources(out_dir, queries, api_key, keyless, timeout, max_results, search_depth, min_quality_score=30):
    all_records = []
    usage = []
    out_dir = Path(out_dir)
    for query in queries:
        payload = tavily_search(
            query,
            api_key=api_key,
            keyless=keyless,
            timeout=timeout,
            max_results=max_results,
            search_depth=search_depth,
        )
        all_records.extend(search_candidate_records(query, payload))
        usage.append({"query": query, "usage": payload.get("usage"), "response_time": payload.get("response_time")})
    all_records = rank_candidate_records(all_records, min_quality_score=min_quality_score)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "discovered_sources.json").write_text(
        json.dumps({"queries": queries, "usage": usage, "results": all_records}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return all_records


def load_dotenv(path=".env"):
    env_path = Path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def audit_local_http(targets, out_dir, timeout):
    events = []
    for target in targets:
        result = local_http_extract(target.url, timeout=timeout)
        text = result.pop("text")
        event = {
            "scheme": "local-http",
            "url": target.url,
            "teams": target.teams,
            "source_labels": target.source_labels,
            "source_quality": source_quality({"url": target.url, "title": " ".join(target.source_labels), "content": ""}),
            **result,
            **content_metrics(text, target),
        }
        write_content_file(out_dir, target.url, text, event)
        events.append(event)
    return events, {"usage": None, "response_time": None}


def extract_with_retries(urls, api_key, keyless, timeout, extract_depth, retries, retry_sleep_ms, tavily_extract_func):
    last_error = None
    for attempt in range(retries + 1):
        try:
            return tavily_extract_func(
                urls,
                api_key=api_key,
                keyless=keyless,
                timeout=timeout,
                extract_depth=extract_depth,
            )
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(retry_sleep_ms / 1000)
    return {
        "results": [],
        "failed_results": [{"url": url, "error": f"{type(last_error).__name__}: {last_error}"} for url in urls],
        "usage": None,
        "response_time": None,
        "_elapsed_ms": None,
    }


def merge_usage(usages):
    credits = 0
    has_usage = False
    for usage in usages:
        if isinstance(usage, dict) and "credits" in usage:
            credits += usage.get("credits") or 0
            has_usage = True
    return {"credits": credits} if has_usage else None


def audit_tavily(
    targets,
    out_dir,
    timeout,
    api_key,
    keyless,
    extract_depth,
    batch_size=5,
    retries=2,
    retry_sleep_ms=500,
    fallback_local_http=True,
    tavily_extract_func=tavily_extract_batch,
    local_http_func=local_http_extract,
):
    payloads = []
    for target_batch in chunked(targets, batch_size):
        urls = [target.url for target in target_batch]
        payloads.append(
            extract_with_retries(
                urls,
                api_key,
                keyless,
                timeout,
                extract_depth,
                retries,
                retry_sleep_ms,
                tavily_extract_func,
            )
        )

    by_url = {}
    failures = {}
    elapsed_by_url = {}
    for payload in payloads:
        by_url.update({item.get("url"): item for item in payload.get("results", [])})
        failures.update({item.get("url"): item for item in payload.get("failed_results", [])})
        for item in payload.get("results", []):
            elapsed_by_url[item.get("url")] = payload.get("_elapsed_ms")
        for item in payload.get("failed_results", []):
            elapsed_by_url[item.get("url")] = payload.get("_elapsed_ms")

    events = []
    for target in targets:
        item = by_url.get(target.url)
        failed = failures.get(target.url)
        text = (item or {}).get("raw_content") or ""
        ok = bool(item) and bool(text)
        scheme = "tavily"
        tavily_error = "" if ok else json.dumps(failed or {"error": "missing result"}, ensure_ascii=False)
        local_result = None
        if not ok and fallback_local_http:
            local_result = local_http_func(target.url, timeout=timeout)
            if local_result.get("ok"):
                text = local_result.pop("text")
                ok = True
                scheme = "tavily+local-http"

        event = {
            "scheme": scheme,
            "url": target.url,
            "teams": target.teams,
            "source_labels": target.source_labels,
            "source_quality": source_quality({"url": target.url, "title": " ".join(target.source_labels), "content": ""}),
            "ok": ok,
            "status": local_result.get("status") if local_result else 200 if item else None,
            "content_type": local_result.get("content_type") if local_result else "text/markdown",
            "raw_chars": local_result.get("raw_chars") if local_result else len(text),
            "error": "" if ok else tavily_error,
            "tavily_error": tavily_error,
            "elapsed_ms": local_result.get("elapsed_ms") if local_result else elapsed_by_url.get(target.url),
            **content_metrics(text, target),
        }
        write_content_file(out_dir, target.url, text, event)
        events.append(event)
    return events, {
        "usage": merge_usage([payload.get("usage") for payload in payloads]),
        "response_time": max([payload.get("response_time") or 0 for payload in payloads] or [0]),
    }


def write_content_file(out_dir, url, text, event):
    path = content_path_for(out_dir, url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    event["content_file"] = str(path)
    snippet_file = snippet_path_for(out_dir, url)
    snippet_file.parent.mkdir(parents=True, exist_ok=True)
    snippets = event.get("evidence", [])
    snippet_file.write_text(
        "\n\n---\n\n".join(snippet.get("text", "") for snippet in snippets),
        encoding="utf-8",
    )
    event["snippet_file"] = str(snippet_file)


def summarize(events, extra):
    return {
        "url_count": len(events),
        "ok_count": sum(1 for event in events if event["ok"]),
        "failed_count": sum(1 for event in events if not event["ok"]),
        "player_hit_count": sum(event["player_hit_count"] for event in events),
        "player_total": sum(event["player_total"] for event in events),
        "content_chars": sum(event["content_chars"] for event in events),
        "evidence_chars": sum(event.get("evidence_chars", 0) for event in events),
        "evidence_snippet_count": sum(event.get("evidence_snippet_count", 0) for event in events),
        "term_hit_count": sum(event["term_hit_count"] for event in events),
        **extra,
    }


def write_outputs(out_dir, events, summary):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "network_events.jsonl").open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    (out_dir / "network_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    bundle_sources = []
    for event in events:
        bundle_sources.append(
            {
                "url": event.get("url"),
                "ok": event.get("ok"),
                "scheme": event.get("scheme"),
                "teams": event.get("teams", []),
                "source_labels": event.get("source_labels", []),
                "source_quality": event.get("source_quality", {}),
                "player_hits": event.get("player_hits", []),
                "player_misses": event.get("player_misses", []),
                "term_hits": event.get("term_hits", []),
                "evidence": event.get("evidence", []),
                "snippet_file": event.get("snippet_file"),
                "content_file": event.get("content_file"),
                "error": event.get("error", ""),
            }
        )
    (out_dir / "prediction_source_bundle.json").write_text(
        json.dumps({"summary": summary, "sources": bundle_sources}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    candidates = availability_candidates(events)
    (out_dir / "availability_candidates.json").write_text(
        json.dumps({"candidates": candidates}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with (out_dir / "availability_candidates.csv").open("w", encoding="utf-8", newline="") as handle:
        fieldnames = [
            "team",
            "player",
            "availability_status",
            "confidence",
            "source_url",
            "source_quality_score",
            "source_quality_tier",
            "evidence",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(candidates)


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--scheme", choices=["local-http", "tavily"], default="tavily")
    parser.add_argument("--availability-csv", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--tavily-api-key-env", default="TAVILY_API_KEY")
    parser.add_argument("--tavily-keyless", action="store_true")
    parser.add_argument("--extract-depth", choices=["basic", "advanced"], default="basic")
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--retry-sleep-ms", type=int, default=500)
    parser.add_argument("--no-fallback-local-http", action="store_true")
    parser.add_argument("--discover-query", action="append", default=[])
    parser.add_argument("--discover-max-results", type=int, default=8)
    parser.add_argument("--discover-search-depth", choices=["basic", "advanced", "fast", "ultra-fast"], default="basic")
    parser.add_argument("--discover-min-quality-score", type=int, default=30)
    parser.add_argument("--extract-discovered", action="store_true")
    parser.add_argument("--discover-team-label", default="")
    return parser.parse_args(argv)


def main():
    args = parse_args()
    load_dotenv()
    api_key = None
    if args.scheme == "tavily" or args.discover_query:
        api_key = require_tavily_auth(args.tavily_api_key_env, args.tavily_keyless)

    availability_path = Path(args.availability_csv)
    if availability_path.exists():
        targets = load_source_targets(availability_path)
    elif args.extract_discovered:
        targets = []
    else:
        targets = load_source_targets(availability_path)
    discovered = []
    if args.discover_query:
        discovered = write_discovered_sources(
            args.out_dir,
            args.discover_query,
            api_key=api_key,
            keyless=args.tavily_keyless,
            timeout=args.timeout,
            max_results=args.discover_max_results,
            search_depth=args.discover_search_depth,
            min_quality_score=args.discover_min_quality_score,
        )
        if args.extract_discovered:
            existing_urls = {target.url for target in targets}
            for target in source_targets_from_records(discovered, team_label=args.discover_team_label):
                if target.url not in existing_urls:
                    targets.append(target)
                    existing_urls.add(target.url)
    if args.scheme == "local-http":
        events, extra = audit_local_http(targets, Path(args.out_dir), args.timeout)
    else:
        events, extra = audit_tavily(
            targets,
            Path(args.out_dir),
            args.timeout,
            api_key=api_key,
            keyless=args.tavily_keyless,
            extract_depth=args.extract_depth,
            batch_size=args.batch_size,
            retries=args.retries,
            retry_sleep_ms=args.retry_sleep_ms,
            fallback_local_http=not args.no_fallback_local_http,
        )
    summary = summarize(events, extra)
    summary.update(
        {
            "scheme": args.scheme,
            "availability_csv": args.availability_csv,
            "discovered_source_count": len(discovered),
        }
    )
    write_outputs(args.out_dir, events, summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
