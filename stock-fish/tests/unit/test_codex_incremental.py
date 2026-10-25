"""TEST_FIXTURE original-cache integration; no SDK, network or business DB."""
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import codex_incremental as incremental
from stockfish_core.yaogu import provider_requests
from stockfish_core.yaogu.premarket_capture_cache_v1 import CaptureCache, RequestSpec, PRODUCERS
from stockfish_core.yaogu.premarket_runtime_v1 import Budget
from stockfish_core.yaogu.providers import archive_receipt

CODES = ["sh.600000", "sz.000001"]
SESSIONS = ["2026-10-19", "2026-10-20", "2026-10-21"]


class FixtureExecutor:
    def __init__(self, scenario="normal"):
        self.calls = []
        self.scenario = scenario

    def __call__(self, endpoint, params, output, **kwargs):
        self.calls.append((endpoint, params))
        if endpoint == "history" and self.scenario == "crash":
            raise RuntimeError("TEST_FIXTURE_CRASH")
        start = datetime.now(timezone.utc).isoformat()
        seen = datetime.now(timezone.utc).isoformat()
        status, complete = "COMPLETE", True
        if endpoint == "calendar":
            a, z = (date.fromisoformat(params[key]) for key in ("start_date", "end_date"))
            rows = [dict(calendar_date=str(a+timedelta(days=n)), is_trading_day="1")
                    for n in range((z-a).days+1)]
        elif endpoint == "history":
            a, z = (date.fromisoformat(params[key]) for key in ("start_date", "end_date"))
            rows = [dict(code=code, date=str(a+timedelta(days=n)), adjustflag="3",
                         open="10.00", high="11.00", low="9.00", close="10.50", preclose="10.10",
                         amount="500000000", volume="50000000", turn="10", isST="0", tradestatus="1")
                    for n in range((z-a).days+1) for code in params["codes"]]
            if self.scenario == "empty":
                rows = []
            elif self.scenario == "partial":
                status, complete = "PARTIAL", False
            elif self.scenario == "failed":
                status, complete, rows = "FETCH_FAILED", False, []
            elif self.scenario == "duplicate":
                rows = [rows[0], dict(rows[0])]
            elif self.scenario == "wrong_code":
                rows[0]["code"] = "sh.600001"
            elif self.scenario == "wrong_date":
                rows[0]["date"] = "2026-10-16"
            elif self.scenario == "adjusted":
                rows[0]["adjustflag"] = "1"
            elif self.scenario == "invalid_price":
                rows[0]["close"] = "NaN"
            elif self.scenario == "suspended":
                rows[0]["tradestatus"] = "0"
            elif self.scenario == "missing_first":
                rows = rows[1:]
            elif self.scenario == "changed_price":
                for row in rows:
                    row["close"] = "10.60"
        else:
            raise AssertionError("UNEXPECTED_TEST_FIXTURE_ENDPOINT")
        payload = dict(status=status, pagination_complete=complete, rows=rows,
                       coverage={}, gaps=[], field_version="TEST_FIXTURE_PROVIDER_ROWS_V1", first_seen_at=seen)
        receipt = archive_receipt(Path(output)/"raw", "TEST_FIXTURE", endpoint, params, payload,
                                  status=status, pagination_complete=complete,
                                  field_version="TEST_FIXTURE_PROVIDER_ROWS_V1", first_seen_at=seen)
        result = dict(endpoint=endpoint, provider="TEST_FIXTURE", parameters=params, rows=rows,
                      status=status, pagination_complete=complete, coverage=dict(sdk_operations=3, http_attempts=0),
                      gaps=[], field_version="TEST_FIXTURE_PROVIDER_ROWS_V1", request_receipt_id=receipt["receipt_id"],
                      receipts=[receipt], started_at=start, completed_at=datetime.now(timezone.utc).isoformat(),
                      implementation_source_hashes={name: hashlib.sha256(Path(provider_requests.__file__).with_name(name).read_bytes()).hexdigest()
                                                    for name in PRODUCERS}, diagnostics=dict(fixture="TEST_FIXTURE"))
        # RAW and RESULT coverage must be identical for the mature verifier.
        # Transport counts are intentionally absent here: that remains UNKNOWN.
        result["coverage"] = {}
        Path(output, "request-TEST_FIXTURE.json").write_text(json.dumps(result), encoding="utf-8")
        return result


class IncrementalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="TEST_FIXTURE_increment_")
        self.root = Path(self.temp.name).resolve()
        self.cache_root = self.root / "cache"
        self.cache_root.mkdir()
        self.executor = FixtureExecutor()
        self.cache = CaptureCache(self.cache_root, Budget(incremental.CAPS, cache_dir=self.cache_root),
                                  config=dict(allow_test_fixture=True), executor=self.executor)
        self.calendar = self.cache.capture_one(RequestSpec("calendar", dict(start_date=SESSIONS[0], end_date=SESSIONS[-1]),
                                                         SESSIONS[-1], role="TEST_FIXTURE_CALENDAR")).metadata
        self.executor.calls.clear()

    def tearDown(self):
        self.temp.cleanup()

    def sync(self, **kwargs):
        options = dict(cache_root=self.cache_root, codes=CODES, sessions=SESSIONS,
                       calendar_record=self.calendar, executor=self.executor, allow_test_fixture=True)
        options.update(kwargs)
        if Path(options["cache_root"]).resolve() != self.cache_root and "calendar_record" not in kwargs:
            executor = FixtureExecutor()
            cache = CaptureCache(options["cache_root"], Budget(incremental.CAPS),
                                 config=dict(allow_test_fixture=True), executor=executor)
            options["calendar_record"] = cache.capture_one(RequestSpec("calendar",
                dict(start_date=SESSIONS[0], end_date=SESSIONS[-1]), SESSIONS[-1], role="TEST_FIXTURE_CALENDAR")).metadata
        return incremental.sync(**options)

    def test_extended_window_fetches_only_missing_shards_then_zero(self):
        first = self.sync(sessions=SESSIONS[:2])
        later = self.sync()
        repeated = self.sync()
        self.assertEqual((first["new_requests"], later["new_requests"], repeated["new_requests"]), (2, 1, 0))
        self.assertEqual((first["cache_hits"], later["cache_hits"], repeated["cache_hits"]), (0, 4, 6))
        self.assertEqual(len(self.executor.calls), 3)
        self.assertEqual(repeated["actual_raw_rows"], 6)
        self.assertEqual(repeated["added_cache_logical_bytes"], 0)
        self.assertEqual(first["shards"][0]["receipt"], later["shards"][0]["receipt"])
        self.assertFalse(repeated["database_created"])
        self.assertFalse(repeated["strategy_executed"])
        self.assertFalse(repeated["full_premarket_ready"])
        self.assertIsNone(repeated["network_transfer_bytes"])
        self.assertIsNone(first["metrics"]["calls"]["sdk_actual"])
        self.assertEqual(first["metrics"]["calls"]["sdk_reserved"], 8)
        self.assertEqual(later["metrics"]["calls"]["sdk_reserved"], 4)
        self.assertEqual(first["new_batch_row_views"], 4)
        self.assertTrue(all(len(params["codes"]) == 2 and params["start_date"] == params["end_date"]
                            for _, params in self.executor.calls))
        self.assertTrue(all(not (self.cache_root / "index" / row["request_key"]).exists()
                            for row in repeated["shards"]))
        self.assertFalse(list(self.root.rglob("*.sqlite*")))

    def test_security_order_and_set_extension_keep_original_keys(self):
        first = self.sync(codes=CODES[:1], sessions=SESSIONS[:1])
        second = self.sync(codes=list(reversed(CODES)), sessions=SESSIONS[:1])
        self.assertEqual(second["new_requests"], 1)
        self.assertEqual(first["shards"][0]["request_key"], second["shards"][0]["request_key"])
        self.assertEqual(self.executor.calls[-1][1]["codes"], CODES[1:])
        self.assertEqual(second["metrics"]["calls"]["sdk_reserved"], 3)

    def test_same_session_batch_budget_is_four_sdk_and_one_request(self):
        with self.assertRaisesRegex(incremental.IncrementalError, "COMPLETE_INCREMENT_REQUEST_BUDGET"):
            self.sync(sessions=SESSIONS[:1], limits=dict(max_requests=1, max_sdk_calls=3))
        self.assertFalse(self.executor.calls)
        value = self.sync(sessions=SESSIONS[:1], limits=dict(max_requests=1, max_sdk_calls=4))
        self.assertEqual(value["new_requests"], 1)
        self.assertEqual(value["metrics"]["calls"]["sdk_reserved"], 4)
        self.assertEqual(len(value["new_batch_receipts"]), 1)
        self.assertEqual(value["shards"][0]["receipt"], value["shards"][1]["receipt"])

    def test_failed_existing_security_does_not_enter_new_batch(self):
        self.executor.scenario = "failed"
        first = self.sync(codes=CODES[:1], sessions=SESSIONS[:1])
        self.executor.scenario = "normal"
        later = self.sync(sessions=SESSIONS[:1])
        self.assertEqual(later["new_requests"], 1)
        self.assertEqual(later["shards"][0]["receipt"], first["shards"][0]["receipt"])
        self.assertEqual(later["shards"][0]["status"], "UNKNOWN_PARENT_INCOMPLETE")
        self.assertEqual(self.executor.calls[-1][1]["codes"], CODES[1:])

    def test_new_partial_batch_remains_unknown_without_later_narrow_retry(self):
        self.executor.scenario = "partial"
        first = self.sync(sessions=SESSIONS[:1])
        later = self.sync(codes=CODES[:1], sessions=SESSIONS[:1])
        self.assertEqual((first["new_requests"], later["new_requests"]), (1, 0))
        self.assertEqual(later["shards"][0]["status"], "UNKNOWN_PARENT_INCOMPLETE")
        self.assertEqual(later["shards"][0]["receipt"], first["new_batch_receipts"][0])
        self.assertEqual(len(self.executor.calls), 1)

    def seed_batch(self, *, scenario="normal", start=None, end=None, codes=None, business_session=None):
        cache = CaptureCache(self.cache_root, Budget(incremental.CAPS),
                             config=dict(allow_test_fixture=True), executor=FixtureExecutor(scenario))
        spec = RequestSpec("history", dict(codes=codes or CODES,
            start_date=start or SESSIONS[0], end_date=end or SESSIONS[-1]), business_session,
            role="TEST_FIXTURE_ORIGINAL_WIDE_REQUEST")
        return cache.capture_one(spec).metadata

    def test_original_wide_request_supplies_six_views_without_new_files(self):
        original = self.seed_batch()
        before = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in self.cache_root.rglob("*")}
        value = self.sync()
        after = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in self.cache_root.rglob("*")}
        self.assertEqual(value["new_requests"], 0)
        self.assertEqual(value["range_reuse_views"], 6)
        self.assertEqual(value["actual_raw_rows"], 6)
        self.assertEqual(value["cache_hits"], 6)
        self.assertEqual(value["added_cache_logical_bytes"], 0)
        self.assertEqual(before, after)
        self.assertEqual(value["range_originals"], [original])
        self.assertTrue(all(row["receipt"] == original for row in value["shards"]))
        self.assertTrue(all(row["coverage_from_original_request"]["exact_shard_index_created"] is False
                            for row in value["shards"]))
        self.assertEqual(self.sync(offline=True)["new_requests"], 0)
        self.assertFalse(self.executor.calls)

    def test_wide_range_missing_actual_row_is_unknown_not_a_retry(self):
        original = self.seed_batch(scenario="missing_first")
        value = self.sync()
        self.assertEqual(value["new_requests"], 0)
        self.assertEqual(value["actual_raw_rows"], 5)
        self.assertEqual(value["status"], "PARTIAL_EXPLICIT_RAW_SCOPE")
        self.assertEqual(value["unknowns"][0]["status"], "UNKNOWN_SOURCE_RANGE_MISSING_ACTUAL_ROW")
        self.assertEqual(value["shards"][0]["receipt"], original)
        self.assertEqual(self.sync()["new_requests"], 0)
        self.assertFalse(self.executor.calls)

    def assert_wide_incomplete_is_retained(self, scenario, with_complete=False):
        original = self.seed_batch(scenario=scenario)
        if with_complete:
            self.seed_batch(start="2026-10-16")
        value = self.sync(codes=CODES[:1], sessions=SESSIONS[:1])
        self.assertEqual(value["new_requests"], 0)
        self.assertEqual(value["status"], "PARTIAL_EXPLICIT_RAW_SCOPE")
        self.assertEqual(value["shards"][0]["status"], "UNKNOWN_PARENT_INCOMPLETE")
        self.assertEqual(value["shards"][0]["receipt"], original)
        self.assertEqual(value["shards"][0]["coverage_from_original_request"]["incomplete_original_receipt_ids"],
                         [original["receipt_id"]])
        self.assertEqual(len(value["range_originals"]), 2 if with_complete else 1)
        self.assertFalse(self.executor.calls)

    def test_wide_partial_is_not_retried_as_smaller_scope(self):
        self.assert_wide_incomplete_is_retained("partial")

    def test_wide_failure_is_not_retried_as_smaller_scope(self):
        self.assert_wide_incomplete_is_retained("failed")

    def test_wide_partial_is_not_masked_by_complete_parent(self):
        self.assert_wide_incomplete_is_retained("partial", with_complete=True)

    def test_wide_failure_is_not_masked_by_complete_parent(self):
        self.assert_wide_incomplete_is_retained("failed", with_complete=True)

    def test_wide_unfinished_claim_blocks_smaller_scope_without_index(self):
        with self.assertRaisesRegex(RuntimeError, "TEST_FIXTURE_CRASH"):
            self.seed_batch(scenario="crash")
        value = self.sync(codes=CODES[:1], sessions=SESSIONS[:1])
        self.assertEqual(value["status"], "BLOCKED_UNFINISHED_CAPTURE_NO_RETRY")
        self.assertEqual(value["new_requests"], 0)
        self.assertEqual(len(value["blocking_original_attempts"]), 1)
        self.assertEqual(value["blocking_original_attempts"][0]["original_request_spec"]["parameters"]["codes"], CODES)
        self.assertFalse(self.executor.calls)

    def test_wide_unfinished_claim_is_not_masked_by_complete_parent(self):
        self.seed_batch(start="2026-10-16")
        with self.assertRaisesRegex(RuntimeError, "TEST_FIXTURE_CRASH"):
            self.seed_batch(scenario="crash")
        value = self.sync(codes=CODES[:1], sessions=SESSIONS[:1])
        self.assertEqual(value["status"], "BLOCKED_UNFINISHED_CAPTURE_NO_RETRY")
        self.assertEqual(value["new_requests"], 0)
        self.assertFalse(self.executor.calls)

    def test_uncommitted_wide_index_does_not_hide_unfinished_attempt(self):
        original = self.seed_batch()
        Path(original["capture_attempt_dir"], "committed.json").unlink()
        value = self.sync(codes=CODES[:1], sessions=SESSIONS[:1])
        self.assertEqual(value["status"], "BLOCKED_UNFINISHED_CAPTURE_NO_RETRY")
        self.assertEqual(value["new_requests"], 0)
        self.assertFalse(self.executor.calls)

    def test_changed_wide_claim_request_key_rejects_before_provider(self):
        with self.assertRaisesRegex(RuntimeError, "TEST_FIXTURE_CRASH"):
            self.seed_batch(scenario="crash")
        # Select the history claim rather than the normal calendar claim.
        path = next(p for p in self.cache_root.glob("raw/*/raw/*/request-claim.json")
                    if json.loads(p.read_text())["request_spec"]["endpoint"] == "history")
        value = json.loads(path.read_text())
        value["request_spec"]["parameters"]["codes"] = CODES[:1]
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(incremental.IncrementalError, "ATTEMPT_REQUEST_KEY_CHANGED"):
            self.sync(codes=CODES[:1], sessions=SESSIONS[:1])
        self.assertFalse(self.executor.calls)

    def test_symlinked_wide_claim_parent_rejects_before_provider(self):
        with self.assertRaisesRegex(RuntimeError, "TEST_FIXTURE_CRASH"):
            self.seed_batch(scenario="crash")
        claim = next(p for p in self.cache_root.glob("raw/*/raw/*/request-claim.json")
                     if json.loads(p.read_text())["request_spec"]["endpoint"] == "history")
        actual = self.root / "TEST_FIXTURE_moved_attempt"
        claim.parent.rename(actual)
        claim.parent.symlink_to(actual, target_is_directory=True)
        with self.assertRaisesRegex(incremental.IncrementalError, "ATTEMPT_SYMLINK_FORBIDDEN"):
            self.sync(codes=CODES[:1], sessions=SESSIONS[:1])
        self.assertFalse(self.executor.calls)

    def test_attempt_scan_is_bounded_before_provider(self):
        folder = self.cache_root / "raw" / ("0" * 64) / "raw"
        folder.mkdir(parents=True)
        for index in range(256):
            (folder / str(index)).mkdir()
        with self.assertRaisesRegex(incremental.IncrementalError, "ATTEMPT_SCAN_LIMIT"):
            self.sync(codes=CODES[:1], sessions=SESSIONS[:1])
        self.assertFalse(self.executor.calls)

    def test_future_wide_source_clocks_reject_without_narrow_download(self):
        class FutureClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return super().now(tz) + timedelta(days=1)
        with patch(__name__ + ".datetime", FutureClock), patch("stockfish_core.yaogu.providers.datetime", FutureClock):
            self.seed_batch()
        with self.assertRaisesRegex(incremental.IncrementalError, "ORIGINAL_HISTORY_FROM_FUTURE"):
            self.sync(codes=CODES[:1], sessions=SESSIONS[:1])
        self.assertFalse(self.executor.calls)

    def test_future_exact_source_clocks_reject_before_any_new_request(self):
        class FutureClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return super().now(tz) + timedelta(days=1)
        with patch(__name__ + ".datetime", FutureClock), patch("stockfish_core.yaogu.providers.datetime", FutureClock):
            self.seed_batch(codes=CODES[:1], start=SESSIONS[0], end=SESSIONS[0], business_session=SESSIONS[0])
        with self.assertRaisesRegex(incremental.IncrementalError, "ORIGINAL_HISTORY_FROM_FUTURE"):
            self.sync()
        self.assertFalse(self.executor.calls)

    def test_new_future_capture_is_retained_and_rejected_without_next_request(self):
        class FutureClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return super().now(tz) + timedelta(days=1)
        def future_executor(*args, **kwargs):
            with patch(__name__ + ".datetime", FutureClock), patch("stockfish_core.yaogu.providers.datetime", FutureClock):
                return self.executor(*args, **kwargs)
        with self.assertRaisesRegex(incremental.IncrementalError, "ORIGINAL_HISTORY_FROM_FUTURE"):
            self.sync(executor=future_executor)
        self.assertEqual(len(self.executor.calls), 1)
        with self.assertRaisesRegex(incremental.IncrementalError, "ORIGINAL_HISTORY_FROM_FUTURE"):
            self.sync()
        self.assertEqual(len(self.executor.calls), 1)

    def test_corrupt_wide_parent_is_rejected_without_smaller_redownload(self):
        original = self.seed_batch()
        Path(original["raw"]["path"]).write_text("TEST_FIXTURE_CORRUPT_WIDE_SOURCE")
        with self.assertRaises((ValueError, RuntimeError)):
            self.sync()
        self.assertFalse(self.executor.calls)

    def test_exact_failure_is_not_hidden_by_wider_complete_parent(self):
        self.executor.scenario = "failed"
        failed = self.sync(codes=CODES[:1], sessions=SESSIONS[:1])
        self.seed_batch()
        value = self.sync(codes=CODES[:1], sessions=SESSIONS[:1])
        self.assertEqual(value["new_requests"], 0)
        self.assertEqual(value["range_reuse_views"], 0)
        self.assertEqual(value["shards"][0]["status"], "UNKNOWN_PARENT_INCOMPLETE")
        self.assertEqual(value["shards"][0]["receipt"], failed["shards"][0]["receipt"])
        self.assertEqual(len(self.executor.calls), 1)

    def test_conflicting_wide_parents_do_not_choose_convenient_source(self):
        self.seed_batch()
        self.seed_batch(scenario="changed_price", start="2026-10-16")
        with self.assertRaisesRegex(incremental.IncrementalError, "CONFLICTING_ORIGINAL_RANGE_ROWS"):
            self.sync()
        self.assertFalse(self.executor.calls)

    def test_equal_wide_parents_retain_both_complete_original_references(self):
        one = self.seed_batch()
        two = self.seed_batch(start="2026-10-16")
        value = self.sync()
        self.assertEqual(value["new_requests"], 0)
        self.assertEqual({r["receipt_id"] for r in value["range_originals"]}, {one["receipt_id"], two["receipt_id"]})
        self.assertTrue(all(len(row["coverage_from_original_request"]["original_receipt_ids"]) == 2
                            for row in value["shards"]))

    def test_wide_row_outside_original_request_is_rejected(self):
        self.seed_batch(scenario="wrong_code")
        with self.assertRaisesRegex(incremental.IncrementalError, "OUTSIDE_REQUESTED_SECURITY"):
            self.sync()
        self.assertFalse(self.executor.calls)

    def test_range_index_scan_is_bounded_before_any_provider_call(self):
        extra = self.cache_root / "index" / ("0"*64)
        extra.mkdir()
        for n in range(256):
            (extra / (str(n)+".json")).write_text("TEST_FIXTURE_NOT_READ")
        with self.assertRaisesRegex(incremental.IncrementalError, "RANGE_INDEX_LIMIT"):
            self.sync()
        self.assertFalse(self.executor.calls)

    def test_plan_makes_no_writes_and_claims_no_verified_coverage(self):
        before = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in self.root.rglob("*")}
        plan = incremental.plan(cache_root=self.cache_root, codes=CODES, sessions=SESSIONS)
        after = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in self.root.rglob("*")}
        self.assertEqual(before, after)
        self.assertEqual(plan["network_calls"], 0)
        self.assertEqual(plan["coverage"], "NOT_CHECKED")
        self.assertTrue(all(row["actual_row_coverage"] == "NOT_CHECKED" for row in plan["shards"]))
        self.assertFalse(self.executor.calls)

    def test_scope_resource_limits_reject_before_provider(self):
        cases = [dict(codes=CODES+["sh.600001"]), dict(sessions=SESSIONS+["2026-10-22"]),
                 dict(codes=["sh.６０００００"]), dict(codes=[CODES[0], CODES[0]]),
                 dict(sessions=["2026-10-01"]), dict(sessions=["2027-01-04"]),
                 dict(limits=dict(max_requests=7)), dict(limits=dict(cache_bytes=64*incremental.MIB)),
                 dict(limits=dict(max_seconds=None)), dict(limits=dict(max_sdk_calls=True)),
                 dict(limits=dict(unknown=1)), dict(limits=dict(max_requests=1))]
        for values in cases:
            with self.subTest(values=values), self.assertRaises((incremental.IncrementalError, ValueError)):
                self.sync(**values)
        self.assertFalse(self.executor.calls)

    def test_calendar_is_verified_before_any_network(self):
        absent = dict(self.calendar, endpoint="history")
        with self.assertRaisesRegex(incremental.IncrementalError, "ORIGINAL_CALENDAR"):
            self.sync(calendar_record=absent)
        with self.assertRaisesRegex(incremental.IncrementalError, "REQUESTED_SESSION"):
            self.sync(sessions=["2026-10-22"])
        Path(self.calendar["raw"]["path"]).write_text("TEST_FIXTURE_CORRUPT")
        with self.assertRaises((ValueError, RuntimeError)):
            self.sync()
        self.assertFalse(self.executor.calls)

    def test_calendar_from_another_cache_is_not_implicitly_imported(self):
        other = self.root / "TEST_FIXTURE_other_cache"
        other.mkdir()
        with self.assertRaisesRegex(incremental.IncrementalError, "SAME_SHARED_CACHE"):
            self.sync(cache_root=other, calendar_record=self.calendar)
        self.assertFalse(self.executor.calls)

    def test_unknown_rows_and_failed_parents_never_trigger_implicit_retry(self):
        scenarios = ["empty", "partial", "failed", "duplicate", "invalid_price"]
        for index, scenario in enumerate(scenarios):
            with self.subTest(scenario=scenario):
                # Each fixture scenario owns a different tiny shared cache.
                cache_root = self.root / ("TEST_FIXTURE_"+scenario)
                cache_root.mkdir()
                executor = FixtureExecutor(scenario)
                first = self.sync(cache_root=cache_root, executor=executor, codes=CODES[:1], sessions=SESSIONS[:1])
                again = self.sync(cache_root=cache_root, executor=executor, codes=CODES[:1], sessions=SESSIONS[:1])
                self.assertEqual(first["status"], "PARTIAL_EXPLICIT_RAW_SCOPE")
                self.assertEqual(again["new_requests"], 0)
                self.assertEqual(len(executor.calls), 1)
                self.assertTrue(again["unknowns"])

    def test_suspended_actual_row_is_preserved_without_computing_strategy(self):
        self.executor.scenario = "suspended"
        value = self.sync(codes=CODES[:1], sessions=SESSIONS[:1])
        self.assertEqual(value["shards"][0]["status"], "SUSPENDED_SOURCE_ROW")
        self.assertFalse(value["full_premarket_ready"])

    def test_wrong_source_scope_and_adjusted_rows_are_rejected(self):
        for scenario in ("wrong_code", "wrong_date", "adjusted"):
            with self.subTest(scenario=scenario):
                cache_root = self.root / ("TEST_FIXTURE_"+scenario)
                cache_root.mkdir()
                with self.assertRaises(ValueError):
                    self.sync(cache_root=cache_root, executor=FixtureExecutor(scenario), codes=CODES[:1], sessions=SESSIONS[:1])

    def test_crash_retains_attempt_and_blocks_every_new_request(self):
        self.executor.scenario = "crash"
        with self.assertRaisesRegex(RuntimeError, "TEST_FIXTURE_CRASH"):
            self.sync()
        self.executor.scenario = "normal"
        again = self.sync()
        self.assertEqual(again["status"], "BLOCKED_UNFINISHED_CAPTURE_NO_RETRY")
        self.assertEqual(again["new_requests"], 0)
        self.assertEqual(len(self.executor.calls), 1)

    def test_offline_missing_rejected_and_verified_hits_allowed(self):
        with self.assertRaisesRegex(incremental.IncrementalError, "OFFLINE_INCREMENT"):
            self.sync(offline=True)
        self.assertFalse(self.executor.calls)
        self.sync()
        self.assertEqual(self.sync(offline=True)["new_requests"], 0)

    def test_corrupt_prior_raw_is_not_redownloaded(self):
        first = self.sync()
        path = Path(first["shards"][0]["receipt"]["raw"]["path"])
        path.write_text("TEST_FIXTURE_CORRUPT")
        with self.assertRaises((ValueError, RuntimeError)):
            self.sync()
        self.assertEqual(len(self.executor.calls), 3)

    def test_capacity_and_symlink_fail_before_provider(self):
        with self.assertRaisesRegex(incremental.IncrementalError, "CAPACITY"):
            self.sync(limits=dict(cache_bytes=1))
        link = self.root / "TEST_FIXTURE_alias"
        link.symlink_to(self.cache_root, target_is_directory=True)
        with self.assertRaisesRegex(incremental.IncrementalError, "CANONICAL"):
            self.sync(cache_root=link)
        self.assertFalse(self.executor.calls)

    def test_injected_executor_requires_explicit_test_fixture(self):
        with self.assertRaisesRegex(incremental.IncrementalError, "TEST_FIXTURE"):
            self.sync(allow_test_fixture=False)

    def test_plan_cli_is_readonly_and_reports_resource_scope(self):
        args = [sys.executable, "-I", "-B", str(ROOT/"scripts/codex_incremental.py"), "plan",
                "--cache-root", str(self.cache_root), "--codes", *CODES, "--sessions", *SESSIONS]
        completed = subprocess.run(args, capture_output=True, text=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        value = json.loads(completed.stdout)
        self.assertEqual(value["operation"], "PLAN_METADATA_ONLY")
        self.assertEqual(value["worst_case_new_requests"], 3)
        self.assertEqual(value["worst_case_sdk_reservations"], 12)

    def register(self, *, python=None, source=None, limits=None, offline=False, maximum=32*incremental.MIB):
        from scripts.codex_workspace import register_profile
        report = self.root / "reports"
        report.mkdir(exist_ok=True)
        config = self.root / "TEST_FIXTURE_config.json"
        config.write_text(json.dumps(dict(schema_version="STANDALONE_PREMARKET_WORKFLOW_V1",
            state_root=str(self.root), limits=limits or {}, capture=dict(offline=offline))))
        profile = dict(schema_version="CODEX_CHAT_RUNTIME_V1", name="TEST_FIXTURE", backend="premarket",
            source_root=str(source or ROOT), python=str(python or sys.executable), config=str(config),
            state_root=str(self.root), report_root=str(report), storage=dict(
                roots=[dict(name="state", path=str(self.root), role="runtime")], target_root="state",
                expected_growth_bytes=1024, min_free_bytes=0, max_managed_bytes=maximum))
        path = self.root / "TEST_FIXTURE_profile.json"
        path.write_text(json.dumps(profile))
        registry = self.root / "registry"
        register_profile(path, registry_root=registry)
        return registry

    def test_registered_cli_plan_uses_original_shared_cache(self):
        registry = self.register()
        args = [sys.executable, "-I", "-B", str(ROOT/"scripts/codex_incremental.py"), "plan",
                "--runtime", "TEST_FIXTURE", "--registry-root", str(registry),
                "--codes", *CODES, "--sessions", *SESSIONS]
        completed = subprocess.run(args, capture_output=True, text=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stdout+completed.stderr)
        value = json.loads(completed.stdout)
        self.assertEqual(value["cache_root"], str(self.cache_root))
        self.assertFalse(list(self.root.glob("capture-cache")))

    def test_registered_cli_inherits_smaller_budget_and_offline(self):
        registry = self.register(limits=dict(max_requests=2, max_sdk_calls=6, max_seconds=50), offline=True)
        calendar = self.root / "TEST_FIXTURE_calendar.json"
        calendar.write_text(json.dumps(self.calendar))
        args = ["sync", "--runtime", "TEST_FIXTURE", "--registry-root", str(registry),
                "--codes", *CODES, "--sessions", *SESSIONS[:1], "--calendar-record", str(calendar)]
        with patch.object(incremental, "sync", return_value=dict(status="COMPLETE_EXPLICIT_RAW_SCOPE")) as mocked, redirect_stdout(io.StringIO()):
            self.assertEqual(incremental.main(args), 0)
        self.assertEqual(mocked.call_args.kwargs["limits"]["max_requests"], 2)
        self.assertEqual(mocked.call_args.kwargs["limits"]["max_seconds"], 50)
        self.assertTrue(mocked.call_args.kwargs["offline"])
        self.assertEqual(mocked.call_args.kwargs["cache_root"], self.cache_root)

    def test_registered_source_and_python_must_match_current_process(self):
        fake = self.root / "TEST_FIXTURE_source"
        fake.mkdir()
        (fake / "stockfish_core").mkdir()
        registry = self.register(source=fake)
        with self.assertRaisesRegex(incremental.IncrementalError, "SOURCE_ROOT"):
            incremental._registered_runtime("TEST_FIXTURE", registry, {}, False)
        # A separate registry can pin an alias to the same executable; that
        # must still not be confused with this process's selected environment.
        alias = self.root / "TEST_FIXTURE_python"
        alias.symlink_to(sys.executable)
        registry2 = self.root / "registry2"
        from scripts.codex_workspace import register_profile
        path = self.root / "TEST_FIXTURE_profile.json"
        profile = json.loads(path.read_text())
        profile.update(source_root=str(ROOT), python=str(alias))
        path.write_text(json.dumps(profile))
        register_profile(path, registry_root=registry2)
        with self.assertRaisesRegex(incremental.IncrementalError, "PYTHON_MISMATCH"):
            incremental._registered_runtime("TEST_FIXTURE", registry2, {}, False)

    def test_registered_capacity_blocks_before_increment_action(self):
        registry = self.register(maximum=1)
        calendar = self.root / "TEST_FIXTURE_calendar.json"
        calendar.write_text(json.dumps(self.calendar))
        args = ["sync", "--runtime", "TEST_FIXTURE", "--registry-root", str(registry),
                "--codes", *CODES, "--sessions", *SESSIONS[:1], "--calendar-record", str(calendar)]
        with patch.object(incremental, "sync") as mocked, redirect_stdout(io.StringIO()) as output:
            self.assertEqual(incremental.main(args), 3)
        self.assertIn("CAPACITY_PREFLIGHT_BLOCKED", output.getvalue())
        mocked.assert_not_called()


if __name__ == "__main__":
    unittest.main()
