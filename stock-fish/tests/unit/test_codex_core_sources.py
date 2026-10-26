"""Explicit TEST_FIXTURE planning, identity unknowns, and offline source closure."""
from datetime import datetime, time as wall_time, timedelta, timezone
import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.unit.test_codex_core import Base
from tests.yaogu.test_premarket_capture_cache_v1 import FixtureBudget, FixtureExecutor
from scripts import codex_core_sources as sources
from scripts import codex_core as original
from stockfish_core.yaogu import premarket_capture_cache_v1 as cache_module


class SourcesTests(Base):
    def setUp(self):
        super().setUp()
        self.executor = FixtureExecutor()
        self.cache = cache_module.CaptureCache(self.state / "cache", FixtureBudget(),
            config=dict(allow_test_fixture=True), executor=self.executor)
        self.today = datetime.now(sources.SH).date()
        start, end = self.today-timedelta(days=180), self.today+timedelta(days=10)
        self.calendar = self.cache.capture_one(cache_module.RequestSpec("calendar",
            dict(start_date=str(start), end_date=str(end)), str(self.today), role="CALENDAR"))
        now = datetime.now(timezone.utc)
        calendar_result = self.cache.verify(self.calendar)
        self.observed, self.target, self.price, self.theme, _ = sources._calendar(calendar_result, now)
        self.records = []
        for endpoint in ("mainboard_sse", "master_szse", "delisting_sse"):
            self.records.append(self.cache.capture_one(cache_module.RequestSpec(endpoint, {}, str(self.today),
                role="CURRENT_OFFICIAL", current=True)))
        for day in self.price:
            for endpoint in ("market_daily", "master_all"):
                self.records.append(self.cache.capture_one(cache_module.RequestSpec(endpoint, dict(date=day), day,
                    role="FULL_MARKET_CONTEXT")))
        self.basic = self.cache.capture_one(cache_module.RequestSpec("stock_basic_all", {}, str(self.today),
            role="CURRENT_BASIC_ATTEMPT", current=True))
        self.factor = self.cache.capture_one(cache_module.RequestSpec("adjustment_factors",
            dict(codes=["sh.600001"], start_date="1990-01-01", end_date=self.observed), self.observed,
            role="FULL_ANCHORED_FACTOR_WINDOW"))
        self.executor.calls.clear()
        self.assertNotIn("provider", self.calendar.metadata)

    def execute_sources(self, operation="plan", **kwargs):
        return sources.execute(self.runtime, operation=operation, profile=sources.PROFILE,
            identity_policy=kwargs.pop("identity_policy", sources.NATIVE_IDENTITY),
            allow_test_fixture=True, **kwargs)

    def files(self):
        return {str(path): original._reference(path) for path in self.state.rglob("*") if path.is_file()}

    def remove_index(self, record):
        for path in (self.cache.root / "index" / record.metadata["request_key"]).glob("*.json"):
            path.unlink()

    def test_25_day_plan_no_42_day_download_or_change(self):
        before = self.files()
        value = self.execute_sources()
        self.assertTrue(value["core_ready_to_run"])
        self.assertEqual(len(value["core_sessions"]), 25)
        self.assertEqual(value["original_capture_count"], 56)
        self.assertEqual(value["missing_request_count"], 0)
        self.assertEqual(value["theme_context"]["additional_market_sessions_deferred"], self.theme[:-25])
        self.assertEqual(value["theme_context"]["additional_market_requests_not_issued"], 84)
        self.assertFalse(value["full_premarket_ready"])
        self.assertEqual(value["network_calls"], 0)
        self.assertEqual(value["strategy_calls"], 0)
        self.assertEqual(before, self.files())
        self.assertFalse(self.executor.calls)
        self.assertFalse(list(self.report.iterdir()))

    def test_build_legal_frozen_request_preserves_all_source_rows(self):
        before = self.files()
        value = self.execute_sources("build")
        request, _ = original._request(Path(value["manifest"]["path"]), True)
        self.assertEqual(len(request["captures"]), 56)
        self.assertEqual(request["observation_date"], self.observed)
        self.assertEqual(request["target_session"], self.target)
        self.assertEqual(request["coverage_type"], "TEST_FIXTURE")
        self.assertEqual({item["receipt_id"] for item in request["captures"]},
                         {self.calendar.receipt_id, self.basic.receipt_id, self.factor.receipt_id,
                          *(record.receipt_id for record in self.records)})
        self.assertEqual(value["status"], "BUILT_ORIGINAL_CORE_REQUEST_NOT_EXECUTED")
        self.assertEqual(before, self.files())
        self.assertEqual(len(list(self.report.rglob("*.json"))), 2)
        self.assertFalse(value["full_premarket_ready"])

    def test_unknown_listing_is_preserved_only_in_explicit_native_policy(self):
        # A current positive-basic source genuinely contains only mainboard codes.
        # Seed a different native scope rather than alter authenticated fixture bytes.
        self.remove_index(self.basic)
        alternate = self.cache.capture_one(cache_module.RequestSpec("stock_basic", dict(codes=["sh.600001"]),
            str(self.today), role="REGISTERED_BASIC_FALLBACK", current=True))
        native = self.execute_sources()
        self.assertTrue(native["core_ready_to_run"])
        self.assertFalse(native["legacy_collector_ready"])
        self.assertEqual(native["qualification_diagnostics"]["missing_positive_listing_codes"], ["sh.688001"])
        self.assertEqual(native["qualification_diagnostics"]["RAW_union_count"], 2)
        self.assertFalse(any(row["request_spec"]["endpoint"] == "stock_basic" for row in native["requests"]))
        built = self.execute_sources("build")
        request = json.loads(Path(built["manifest"]["path"]).read_bytes())
        self.assertIn(alternate.receipt_id, {row["receipt_id"] for row in request["captures"]})
        strict = self.execute_sources(identity_policy=sources.LEGACY_IDENTITY)
        self.assertFalse(strict["core_ready_to_run"])
        self.assertTrue(any(row["status"] == "UNFINISHED_NO_IMPLICIT_RETRY" for row in strict["requests"]))

    def test_missing_market_has_precise_gap_no_fabricated_factor_or_listing_scope(self):
        record = next(record for record in self.records if record.endpoint == "market_daily")
        self.remove_index(record)
        value = self.execute_sources()
        self.assertFalse(value["core_ready_to_run"])
        self.assertFalse(value["qualification_diagnostics"]["exact_missing_scope"])
        self.assertEqual(value["unfinished_request_count"], 1)
        with self.assertRaisesRegex(sources.SourcesError, "BUILD_BLOCKED"):
            self.execute_sources("build")
        self.assertFalse(list(self.report.iterdir()))

    def test_exact_new_factor_required_no_tail_or_previous_day_substitution(self):
        self.remove_index(self.factor)
        value = self.execute_sources()
        self.assertFalse(value["core_ready_to_run"])
        factor = [row for row in value["requests"] if row["request_spec"]["endpoint"] == "adjustment_factors"]
        self.assertEqual(len(factor), 1)
        self.assertEqual(factor[0]["request_spec"]["parameters"]["start_date"], "1990-01-01")
        self.assertEqual(factor[0]["request_spec"]["parameters"]["end_date"], self.observed)
        self.assertEqual(factor[0]["status"], "UNFINISHED_NO_IMPLICIT_RETRY")

    def test_current_day_official_missing_does_not_relabel_old_source(self):
        with patch.object(sources, "_collect", wraps=sources._collect):
            tomorrow = datetime.now(timezone.utc)+timedelta(days=1)
            value = self.execute_sources(_now=tomorrow)
        self.assertFalse(value["core_ready_to_run"])
        official = [row for row in value["requests"] if row["request_spec"]["current"]]
        self.assertEqual(official, [])
        self.assertEqual(value["status"], "BLOCKED_CURRENT_SESSION_GAPS")
        self.assertEqual(value["current_official_status"], "NOT_EVALUATED")
        self.assertEqual(value["historical_core_market_status"], "NOT_EVALUATED")

    def test_latest_missing_stops_before_official_old_raw_and_factor_reads(self):
        from stockfish_core.yaogu.premarket_runtime_v1 import Budget
        latest = next(record for record in self.records if record.endpoint == "market_daily"
                      and record.parameters["date"] == self.observed)
        self.remove_index(latest)
        forbidden = {ref["path"] for record in [*self.records, self.basic, self.factor]
                     if record.endpoint not in ("market_daily", "master_all")
                     or record.parameters.get("date") != self.observed
                     for ref in (record.metadata[key] for key in ("raw", "result", "receipt"))}
        before, seen = self.files(), []
        read = Budget.read
        verify = cache_module.CaptureCache.verify
        def guarded(budget, path, *args, **kwargs):
            self.assertNotIn(str(Path(path).resolve()), forbidden, "EARLY_GATE_READ_OLD_OR_DEFERRED_ORIGINAL")
            return read(budget, path, *args, **kwargs)
        def counted(cache, record):
            seen.append(cache.metadata(record)["endpoint"])
            return verify(cache, record)
        with patch.object(Budget, "read", guarded), patch.object(cache_module.CaptureCache, "verify", counted):
            result = self.execute_sources()
            self.assertEqual(result["status"], "BLOCKED_CURRENT_SESSION_GAPS")
            self.assertEqual(result["gap_scope"], "CURRENT_SESSION_PREREQUISITES_ONLY")
            self.assertEqual(result["current_official_status"], "NOT_EVALUATED")
            self.assertEqual(result["remaining_core_dependencies"], "NOT_EVALUATED")
            self.assertIsNone(result["qualification_diagnostics"]["RAW_union_count"])
            self.assertIsNone(result["qualification_diagnostics"]["factor_query_codes"])
            self.assertIsNone(result["native_request_constraints"])
            self.assertEqual(seen, ["calendar", "master_all"])
            self.assertEqual(result["unfinished_request_count"], 1)
            self.assertEqual(result["distinct_request_lookups"], 2)
            self.assertFalse(result["core_ready_to_run"])
            with self.assertRaisesRegex(sources.SourcesError, "BUILD_BLOCKED"):
                self.execute_sources("build")
        self.assertEqual(before, self.files())
        self.assertFalse(list(self.report.iterdir()))

    def test_official_missing_after_current_market_stops_before_older_rows(self):
        from stockfish_core.yaogu.premarket_runtime_v1 import Budget
        missing = next(record for record in self.records if record.endpoint == "mainboard_sse")
        self.remove_index(missing)
        forbidden = {ref["path"] for record in [*self.records, self.basic, self.factor]
                     if record.endpoint in ("market_daily", "master_all") and record.parameters.get("date") != self.observed
                     or record.endpoint in ("stock_basic_all", "adjustment_factors")
                     for ref in (record.metadata[key] for key in ("raw", "result", "receipt"))}
        read, seen = Budget.read, []
        verify = cache_module.CaptureCache.verify
        def guarded(budget, path, *args, **kwargs):
            self.assertNotIn(str(Path(path).resolve()), forbidden)
            return read(budget, path, *args, **kwargs)
        def counted(cache, record):
            seen.append(cache.metadata(record)["endpoint"])
            return verify(cache, record)
        with patch.object(Budget, "read", guarded), patch.object(cache_module.CaptureCache, "verify", counted):
            result = self.execute_sources()
        self.assertEqual(result["blocked_prerequisite_stage"], "CURRENT_OFFICIAL")
        self.assertEqual(result["current_official_status"], "EVALUATED_WITH_GAPS")
        self.assertEqual(len(seen), 5)
        self.assertNotIn("stock_basic_all", seen)
        self.assertNotIn("adjustment_factors", seen)
        self.assertFalse(result["core_ready_to_run"])

    def test_early_failure_still_terminal_checks_consumed_calendar(self):
        latest = next(record for record in self.records if record.endpoint == "market_daily"
                      and record.parameters["date"] == self.observed)
        self.remove_index(latest)
        terminal = cache_module.CaptureCache.terminal
        def changed(cache):
            path = Path(self.calendar.metadata["raw"]["path"])
            raw = path.read_bytes()
            path.write_bytes(b"["+raw[1:])  # Same length: exercise SHA, not the size ceiling.
            return terminal(cache)
        with patch.object(cache_module.CaptureCache, "terminal", changed):
            with self.assertRaisesRegex(RuntimeError, "FILE_SHA256_CHANGED"):
                self.execute_sources()
        self.assertFalse(list(self.report.iterdir()))

    def test_historical_as_of_explicit_and_future_rejected(self):
        cutoff = datetime.now(timezone.utc).isoformat()
        value = self.execute_sources(as_of=cutoff)
        self.assertEqual(value["clock_mode"], "EXPLICIT_HISTORICAL_REPLAY")
        self.assertEqual(value["planned_at"], cutoff)
        with self.assertRaisesRegex(sources.SourcesError, "FUTURE_AS_OF"):
            self.execute_sources(as_of=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat())
        with self.assertRaisesRegex(sources.SourcesError, "AWARE_CLOCK"):
            self.execute_sources(as_of="2026-10-25T15:00:00")

    def test_unknown_identity_profile_and_limits_rejected(self):
        with self.assertRaisesRegex(sources.SourcesError, "EXPLICIT_IDENTITY_POLICY"):
            self.execute_sources(identity_policy="guess")
        with self.assertRaisesRegex(sources.SourcesError, "LIMIT_EXCEEDS_CAP"):
            self.execute_sources(limits={"cache_bytes": sources.LIMITS["cache_bytes"]+1})
        with self.assertRaisesRegex(sources.SourcesError, "READONLY_CACHE_CAP"):
            self.execute_sources(limits={"cache_bytes": 1})

    def test_build_capacity_rejected_without_output(self):
        with patch.object(self.runtime, "preflight", return_value={"allowed": False}):
            with self.assertRaisesRegex(sources.SourcesError, "CAPACITY_BLOCKED"):
                self.execute_sources("build")
        self.assertFalse(list(self.report.iterdir()))

    def test_changed_original_rejected_no_repair(self):
        path = Path(self.factor.metadata["raw"]["path"])
        path.write_bytes(path.read_bytes()+b" ")
        with self.assertRaisesRegex(RuntimeError, "FILE_SHA256_CHANGED"):
            self.execute_sources()
        self.assertFalse(self.executor.calls)

    def test_one_semantic_authentication_per_selected_original(self):
        count = []
        verify = cache_module.CaptureCache.verify
        def counted(cache, record):
            value = cache.metadata(record)
            count.append(value["receipt_id"])
            return verify(cache, record)
        with patch.object(cache_module.CaptureCache, "verify", counted):
            value = self.execute_sources()
        self.assertEqual(len(count), len(set(count)))
        self.assertEqual(len(count), value["original_capture_count"])

    def test_fixture_sources_cannot_enter_real_mode(self):
        with self.assertRaisesRegex(sources.SourcesError, "REAL_AND_FIXTURE"):
            sources.plan(self.runtime, profile=sources.PROFILE, identity_policy=sources.NATIVE_IDENTITY)

    def test_read_bound_is_sticky_before_build(self):
        with self.assertRaisesRegex(RuntimeError, "READ_LIMIT_EXCEEDED"):
            self.execute_sources("build", limits={"capture_read_bytes": 1})
        self.assertFalse(list(self.report.iterdir()))


class SourceRequestConstraintsTests(unittest.TestCase):
    def request(self, count=1, size=1):
        references = []
        for n in range(count):
            references.append({role: dict(path=f"/TEST_FIXTURE/{n}/{role}", bytes=size)
                               for role in ("result", "raw", "receipt")})
        return dict(schema_version=original.INPUT_VERSION, captures=references,
            observation_date="2026-10-23", target_session="2026-10-26",
            cutoff="2026-10-25T19:48:10+00:00", coverage_type="TEST_FIXTURE")

    def test_consumer_capture_and_individual_and_unique_bounds(self):
        self.assertTrue(sources._request_constraints(self.request())["compatible"])
        too_many = sources._request_constraints(self.request(count=257))
        self.assertIn("CAPTURE_COUNT_MUST_BE_1_TO_256", too_many["violations"])
        big = sources._request_constraints(self.request(size=17*sources.MIB))
        self.assertIn("INDIVIDUAL_ORIGINAL_EXCEEDS_16_MIB", big["violations"])
        total = sources._request_constraints(self.request(count=6, size=16*sources.MIB))
        self.assertIn("UNIQUE_ORIGINALS_EXCEED_256_MIB", total["violations"])

    def test_manifest_bound_and_conflicting_physical_refs(self):
        request = self.request(count=2)
        request["captures"][0]["fixture_padding"] = "X" * (2*sources.MIB)
        self.assertIn("MANIFEST_EXCEEDS_2_MIB", sources._request_constraints(request)["violations"])
        request = self.request(count=2)
        request["captures"][1]["raw"] = dict(request["captures"][0]["raw"], bytes=2)
        self.assertIn("CONFLICTING_PHYSICAL_SOURCE_REFERENCES", sources._request_constraints(request)["violations"])
