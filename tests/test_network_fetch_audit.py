import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "worldcup-match-predictor" / "scripts" / "network_fetch_audit.py"


class NetworkFetchAuditTests(unittest.TestCase):
    def load_module(self):
        spec = importlib.util.spec_from_file_location("network_fetch_audit", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_load_source_targets_groups_rows_by_url(self):
        module = self.load_module()
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "availability.csv"
            csv_path.write_text(
                "team,player,availability_source,source_url\n"
                "Egypt,Mohamed Salah,Guardian,https://example.test/a\n"
                "Australia,Mathew Leckie,Guardian,https://example.test/a\n"
                "Ghana,Antoine Semenyo,RotoWire,https://example.test/b\n",
                encoding="utf-8",
            )

            targets = module.load_source_targets(csv_path)

            self.assertEqual([target.url for target in targets], ["https://example.test/a", "https://example.test/b"])
            self.assertEqual(targets[0].players, ["Mathew Leckie", "Mohamed Salah"])
            self.assertEqual(targets[0].teams, ["Australia", "Egypt"])

    def test_content_metrics_counts_player_and_availability_terms(self):
        module = self.load_module()
        target = module.SourceTarget(
            url="https://example.test/a",
            rows=[
                {"player": "Mohamed Salah", "team": "Egypt"},
                {"player": "Mathew Leckie", "team": "Australia"},
            ],
        )
        text = "Salah is fit after a hamstring injury. Leckie is ruled out."

        metrics = module.content_metrics(text, target)

        self.assertEqual(metrics["player_hit_count"], 2)
        self.assertEqual(metrics["player_total"], 2)
        self.assertIn("hamstring", metrics["term_hits"])
        self.assertIn("ruled out", metrics["term_hits"])

    def test_player_matching_does_not_match_common_first_name_only(self):
        module = self.load_module()

        hits, misses = module.player_matches(
            "TOP STORIES: LeBron hits free agency. James will not return to the Lakers.",
            ["James Rodriguez"],
        )

        self.assertEqual(hits, [])
        self.assertEqual(misses, ["James Rodriguez"])

    def test_player_matching_accepts_last_name_and_ascii_alias(self):
        module = self.load_module()

        hits, _ = module.player_matches(
            "Rodriguez trained with Colombia after a knock.",
            ["James Rodríguez"],
        )

        self.assertEqual(hits, ["James Rodríguez"])

    def test_availability_terms_do_not_match_inside_unrelated_words(self):
        module = self.load_module()

        self.assertNotIn("knock", module.term_hits("Paraguay won a knockout match on penalties."))
        self.assertIn("knock", module.term_hits("The forward took a knock in training."))

    def test_evidence_snippets_skip_navigation_boilerplate(self):
        module = self.load_module()
        target = module.SourceTarget(
            url="https://example.test/a",
            rows=[{"player": "James Rodriguez", "team": "Colombia"}],
        )
        text = (
            "TOP STORIES NFL NHL Tennis Golf Soccer LeBron James free agency knock "
            "unrelated navigation block. Colombia confirmed James Rodriguez trained after a knock."
        )

        snippets = module.evidence_snippets(text, target, context_chars=20)

        self.assertEqual(len(snippets), 1)
        self.assertIn("Colombia confirmed", snippets[0]["text"])

    def test_evidence_snippets_keep_relevant_context_only(self):
        module = self.load_module()
        target = module.SourceTarget(
            url="https://example.test/a",
            rows=[
                {"player": "Mohamed Salah", "availability_status": "available_fitness_managed"},
                {"player": "Mohanad Lasheen", "availability_status": "suspended"},
            ],
        )
        text = (
            "Long unrelated intro. " * 50
            + "Mohamed Salah trained after his hamstring injury and is fit to play. "
            + "Mohanad Lasheen is suspended for the knockout match. "
            + "Long unrelated outro. " * 50
        )

        snippets = module.evidence_snippets(text, target, context_chars=30)

        joined = "\n".join(snippet["text"] for snippet in snippets)
        self.assertIn("Salah", joined)
        self.assertIn("Lasheen", joined)
        self.assertLess(len(joined), len(text))
        self.assertEqual(snippets[0]["matched_players"], ["Mohamed Salah"])

    def test_content_metrics_include_structured_evidence(self):
        module = self.load_module()
        target = module.SourceTarget(
            url="https://example.test/a",
            rows=[{"player": "Mathew Leckie", "availability_status": "ruled_out"}],
        )
        text = "Mathew Leckie is ruled out with injury."

        metrics = module.content_metrics(text, target)

        self.assertEqual(metrics["evidence_snippet_count"], 1)
        self.assertEqual(metrics["evidence"][0]["players"], ["Mathew Leckie"])
        self.assertIn("ruled out", metrics["evidence"][0]["terms"])

    def test_tavily_headers_use_key_without_logging_secret(self):
        module = self.load_module()

        headers = module.tavily_headers(api_key="tvly-secret", keyless=True)

        self.assertEqual(headers["Authorization"], "Bearer tvly-secret")
        self.assertNotIn("X-Tavily-Access-Mode", headers)

    def test_load_dotenv_sets_missing_environment_values(self):
        module = self.load_module()
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("TAVILY_TEST_KEY='abc123'\n", encoding="utf-8")

            old_value = module.os.environ.pop("TAVILY_TEST_KEY", None)
            try:
                module.load_dotenv(env_path)
                self.assertEqual(module.os.environ["TAVILY_TEST_KEY"], "abc123")
            finally:
                module.os.environ.pop("TAVILY_TEST_KEY", None)
                if old_value is not None:
                    module.os.environ["TAVILY_TEST_KEY"] = old_value

    def test_parse_args_defaults_to_tavily_scheme(self):
        module = self.load_module()

        args = module.parse_args(["--availability-csv", "availability.csv", "--out-dir", "out"])

        self.assertEqual(args.scheme, "tavily")

    def test_parse_args_can_still_select_local_http_for_baseline(self):
        module = self.load_module()

        args = module.parse_args(
            ["--scheme", "local-http", "--availability-csv", "availability.csv", "--out-dir", "out"]
        )

        self.assertEqual(args.scheme, "local-http")

    def test_require_tavily_auth_gives_actionable_message(self):
        module = self.load_module()
        old = module.os.environ.pop("TAVILY_TEST_MISSING", None)
        try:
            with self.assertRaisesRegex(SystemExit, "Missing TAVILY_TEST_MISSING"):
                module.require_tavily_auth("TAVILY_TEST_MISSING", keyless=False)
        finally:
            if old is not None:
                module.os.environ["TAVILY_TEST_MISSING"] = old

    def test_chunked_batches_urls(self):
        module = self.load_module()

        chunks = list(module.chunked(["a", "b", "c", "d", "e"], 2))

        self.assertEqual(chunks, [["a", "b"], ["c", "d"], ["e"]])

    def test_audit_tavily_falls_back_to_local_http_for_failed_url(self):
        module = self.load_module()
        target = module.SourceTarget(
            url="https://example.test/a",
            rows=[{"player": "Mohamed Salah", "team": "Egypt"}],
        )

        def fake_extract(urls, **kwargs):
            return {
                "results": [],
                "failed_results": [{"url": "https://example.test/a", "error": "blocked"}],
                "usage": {"credits": 1},
                "response_time": 0.1,
                "_elapsed_ms": 5,
            }

        def fake_local(url, timeout=30):
            return {
                "ok": True,
                "status": 200,
                "content_type": "text/html",
                "raw_chars": 50,
                "text": "Mohamed Salah is fit.",
                "error": "",
                "elapsed_ms": 2,
            }

        with tempfile.TemporaryDirectory() as tmp:
            events, extra = module.audit_tavily(
                [target],
                Path(tmp),
                timeout=30,
                api_key="tvly-test",
                keyless=False,
                extract_depth="basic",
                batch_size=5,
                retries=0,
                retry_sleep_ms=0,
                fallback_local_http=True,
                tavily_extract_func=fake_extract,
                local_http_func=fake_local,
            )

        self.assertTrue(events[0]["ok"])
        self.assertEqual(events[0]["scheme"], "tavily+local-http")
        self.assertEqual(events[0]["tavily_error"], '{"url": "https://example.test/a", "error": "blocked"}')
        self.assertEqual(extra["usage"], {"credits": 1})

    def test_tavily_search_builds_candidate_records(self):
        module = self.load_module()

        payload = {
            "results": [
                {"title": "Team news", "url": "https://example.test/a", "content": "injury update", "score": 0.9},
                {"title": "Duplicate", "url": "https://example.test/a", "content": "same", "score": 0.8},
            ],
            "usage": {"credits": 1},
            "response_time": "0.2",
        }

        records = module.search_candidate_records("Argentina Cape Verde team news", payload)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["url"], "https://example.test/a")
        self.assertEqual(records[0]["query"], "Argentina Cape Verde team news")

    def test_source_quality_prefers_official_and_penalizes_low_quality_predictions(self):
        module = self.load_module()

        official = module.source_quality(
            {
                "url": "https://www.fifa.com/en/tournaments/mens/worldcup/canada-morocco",
                "title": "Canada v Morocco team news",
                "content": "official match centre lineups injury suspension",
            }
        )
        seo = module.source_quality(
            {
                "url": "https://random-bets.example/prediction/canada-vs-morocco-free-tips",
                "title": "Canada vs Morocco prediction betting tips odds",
                "content": "free betting prediction accumulator tips",
            }
        )

        self.assertGreater(official["score"], seo["score"])
        self.assertEqual(official["tier"], "official")
        self.assertEqual(seo["tier"], "low")

    def test_discovered_records_are_ranked_and_filtered_without_manual_step(self):
        module = self.load_module()
        records = [
            {"url": "https://random-bets.example/tips", "title": "Betting tips", "content": "free picks"},
            {"url": "https://www.espn.com/soccer/story/team-news", "title": "Canada team news", "content": "injury update"},
        ]

        ranked = module.rank_candidate_records(records, min_quality_score=0)
        filtered = module.rank_candidate_records(records, min_quality_score=40)

        self.assertEqual(ranked[0]["url"], "https://www.espn.com/soccer/story/team-news")
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["quality_tier"], "mainstream")

    def test_discovered_records_can_become_extract_targets(self):
        module = self.load_module()
        records = [
            {"url": "https://www.espn.com/soccer/story/team-news", "quality_tier": "mainstream"},
        ]

        targets = module.source_targets_from_records(records, team_label="Canada;Morocco")

        self.assertEqual(targets[0].url, "https://www.espn.com/soccer/story/team-news")
        self.assertEqual(targets[0].teams, ["Canada;Morocco"])

    def test_parse_args_can_extract_discovered_sources(self):
        module = self.load_module()

        args = module.parse_args(
            [
                "--availability-csv",
                "availability.csv",
                "--out-dir",
                "out",
                "--discover-query",
                "Canada Morocco team news",
                "--extract-discovered",
            ]
        )

        self.assertTrue(args.extract_discovered)

    def test_availability_candidates_are_generated_from_evidence(self):
        module = self.load_module()
        events = [
            {
                "url": "https://www.espn.com/soccer/story/team-news",
                "ok": True,
                "teams": ["Canada"],
                "evidence": [
                    {
                        "players": ["Alphonso Davies"],
                        "terms": ["ruled out", "injury"],
                        "text": "Alphonso Davies is ruled out with injury.",
                    }
                ],
                "source_quality": {"score": 65, "tier": "mainstream"},
            }
        ]

        candidates = module.availability_candidates(events)

        self.assertEqual(candidates[0]["player"], "Alphonso Davies")
        self.assertEqual(candidates[0]["availability_status"], "ruled_out")
        self.assertEqual(candidates[0]["confidence"], "medium")

    def test_availability_candidates_skip_match_event_false_positives(self):
        module = self.load_module()
        events = [
            {
                "url": "https://www.skysports.com/football/match-report",
                "ok": True,
                "teams": ["Paraguay;France"],
                "evidence": [
                    {
                        "players": ["Julio Enciso"],
                        "terms": ["ruled out", "knock"],
                        "text": "Julio Enciso scored before Jonathan Tah had a goal ruled out by VAR in a knockout tie.",
                    }
                ],
                "source_quality": {"score": 60, "tier": "mainstream"},
            }
        ]

        candidates = module.availability_candidates(events)

        self.assertEqual(candidates, [])

    def test_availability_candidates_do_not_apply_status_to_replacement_player(self):
        module = self.load_module()
        events = [
            {
                "url": "https://www.reuters.com/sports/soccer/france-team-news",
                "ok": True,
                "teams": ["Paraguay;France"],
                "evidence": [
                    {
                        "players": ["Aurélien Tchouaméni", "Manu Koné"],
                        "terms": ["ruled out", "injury", "replacement"],
                        "text": (
                            "Aurelien Tchouameni has been ruled out of the World Cup clash against "
                            "Paraguay with a thigh injury and is expected to be replaced by Manu Kone."
                        ),
                    }
                ],
                "source_quality": {"score": 75, "tier": "mainstream"},
            }
        ]

        candidates = module.availability_candidates(events)

        self.assertEqual([candidate["player"] for candidate in candidates], ["Aurélien Tchouaméni"])
        self.assertEqual(candidates[0]["availability_status"], "ruled_out")

    def test_write_outputs_creates_jsonl_and_summary(self):
        module = self.load_module()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            events = [
                {
                    "scheme": "local-http",
                    "url": "https://example.test/a",
                    "ok": True,
                    "teams": ["Egypt"],
                    "source_labels": ["Guardian"],
                    "evidence": [{"players": ["Mohamed Salah"], "terms": ["fit"], "text": "Salah is fit."}],
                    "snippet_file": str(out / "snippets/a.md"),
                    "content_file": str(out / "contents/a.md"),
                }
            ]
            summary = {"scheme": "local-http", "ok_count": 1}

            module.write_outputs(out, events, summary)

            self.assertEqual(
                json.loads((out / "network_events.jsonl").read_text(encoding="utf-8")),
                events[0],
            )
            self.assertEqual(
                json.loads((out / "network_summary.json").read_text(encoding="utf-8")),
                summary,
            )
            bundle = json.loads((out / "prediction_source_bundle.json").read_text(encoding="utf-8"))
            self.assertEqual(bundle["summary"], summary)
            self.assertEqual(bundle["sources"][0]["evidence"][0]["text"], "Salah is fit.")


if __name__ == "__main__":
    unittest.main()
