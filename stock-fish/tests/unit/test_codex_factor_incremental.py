"""Isolated TEST_FIXTURE factor originals; never uses an SDK/network/real cache."""
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(os.environ.get("STOCKFISH_TEST_SOURCE_ROOT", str(ROOT))).resolve()
sys.path.insert(0, str(SOURCE))
spec = importlib.util.spec_from_file_location("factor_incremental_under_test", ROOT / "scripts/codex_factor_incremental.py")
factors = importlib.util.module_from_spec(spec)
spec.loader.exec_module(factors)
from stockfish_core.yaogu import provider_requests
from stockfish_core.yaogu.premarket_capture_cache_v1 import CaptureCache, RequestSpec, PRODUCERS
from stockfish_core.yaogu.premarket_runtime_v1 import Budget, BudgetExceeded
from stockfish_core.yaogu.providers import archive_receipt
from stockfish_core.storage import StorageError

CODES = ["sh.600000", "sz.000001"]
BASE = "2026-10-21"
TARGET = "2026-10-23"


class FixtureExecutor:
    def __init__(self, scenario="normal"):
        self.scenario, self.calls = scenario, []

    def __call__(self, endpoint, params, output, **kwargs):
        self.calls.append((endpoint, params))
        if self.scenario == "crash":
            raise RuntimeError("TEST_FIXTURE_CRASH")
        now = datetime.now(timezone.utc)
        if self.scenario == "future":
            now += timedelta(days=1)
        start, seen = now.isoformat(), now.isoformat()
        status, complete, coverage = "COMPLETE", True, {}
        if endpoint == "calendar":
            a, z = (date.fromisoformat(params[key]) for key in ("start_date", "end_date"))
            rows = [dict(calendar_date=str(a+timedelta(days=n)),
                         is_trading_day="1" if (a+timedelta(days=n)).weekday() < 5 else "0")
                    for n in range((z-a).days+1)]
            if self.scenario == "calendar_gap":
                rows.pop(4)
            if self.scenario == "calendar_unknown":
                rows[0]["is_trading_day"] = "?"
        else:
            assert endpoint == "adjustment_factors"
            rows = [dict(code=code, dividOperateDate=day, backAdjustFactor=value,
                         foreAdjustFactor=value, adjustFactor=value)
                    for code in params["codes"]
                    for day, value in [("2000-01-01", "1"), ("2026-10-20", "2"), ("2026-10-22", "3")]
                    if params["start_date"] <= day <= params["end_date"]]
            if self.scenario == "empty":
                rows = []
            elif self.scenario == "revision":
                for row in rows:
                    if row["dividOperateDate"] == "2026-10-20":
                        row["backAdjustFactor"] = "2.5"
            elif self.scenario == "removed":
                rows = [r for r in rows if r["dividOperateDate"] != "2026-10-20"]
            elif self.scenario == "historical_added":
                rows.append(dict(code=params["codes"][0], dividOperateDate="2026-10-19", backAdjustFactor="1.5"))
            elif self.scenario == "invalid":
                rows[0]["backAdjustFactor"] = "NaN"
            elif self.scenario == "out_of_scope":
                rows[0]["dividOperateDate"] = "1989-01-01"
            elif self.scenario == "duplicate":
                rows.append(dict(rows[0]))
            elif self.scenario == "partial":
                status, complete = "PARTIAL", False
                rows = [r for r in rows if r["code"] == params["codes"][0]]
            elif self.scenario == "failed":
                status, complete, rows = "FETCH_FAILED", False, []
            statuses = [dict(code=code, status="FETCH_FAILED" if self.scenario == "failed" or
                             self.scenario == "partial" and n else "COMPLETE",
                             row_count=sum(r["code"] == code for r in rows),
                             cursor_exhausted=not (self.scenario == "failed" or self.scenario == "partial" and n))
                        for n, code in enumerate(params["codes"])]
            coverage = dict(requested_codes=params["codes"], security_statuses=statuses,
                            cursor_exhausted=complete, returned_rows=len(rows))
        payload = dict(status=status, pagination_complete=complete, rows=rows,
                       coverage=coverage, gaps=[], field_version="TEST_FIXTURE_FACTOR_V1", first_seen_at=seen)
        receipt = archive_receipt(Path(output)/"raw", "TEST_FIXTURE", endpoint, params, payload,
            status=status, pagination_complete=complete, field_version="TEST_FIXTURE_FACTOR_V1", first_seen_at=seen)
        result = dict(endpoint=endpoint, provider="TEST_FIXTURE", parameters=params, rows=rows,
            status=status, pagination_complete=complete, coverage=coverage, gaps=[], field_version="TEST_FIXTURE_FACTOR_V1",
            request_receipt_id=receipt["receipt_id"], receipts=[receipt], started_at=start,
            completed_at=max(now, datetime.now(timezone.utc)).isoformat(),
            implementation_source_hashes={name: hashlib.sha256(Path(provider_requests.__file__).with_name(name).read_bytes()).hexdigest()
                for name in PRODUCERS}, diagnostics=dict(fixture="TEST_FIXTURE"))
        Path(output, "request-TEST_FIXTURE.json").write_text(json.dumps(result), encoding="utf-8")
        return result


def _hold_cache_writer(path, ready, release):
    cache = CaptureCache(path, Budget(factors.CAPS, cache_dir=path), config=dict(allow_test_fixture=True))
    with cache._writer():
        ready.set()
        release.wait(10)


class FactorIncrementalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="TEST_FIXTURE_factor_increment_")
        self.root = Path(self.temp.name).resolve()
        self.cache_root = self.root / "cache"
        self.executor = FixtureExecutor()
        self.cache = CaptureCache(self.cache_root, Budget(factors.CAPS, cache_dir=self.cache_root),
            config=dict(allow_test_fixture=True), executor=self.executor)
        self.calendar = self.cache.capture_one(RequestSpec("calendar",
            dict(start_date="2026-10-01", end_date="2026-11-25"), "2026-10-23", role="TEST_FIXTURE")).metadata
        self.baseline = self.seed()
        self.executor.calls.clear()

    def tearDown(self):
        self.temp.cleanup()

    def seed(self, *, end=BASE, start="1990-01-01", scenario="normal", codes=None, business=None):
        cache = CaptureCache(self.cache_root, Budget(factors.CAPS, cache_dir=self.cache_root),
            config=dict(allow_test_fixture=True), executor=FixtureExecutor(scenario))
        return cache.capture_one(RequestSpec("adjustment_factors",
            dict(codes=codes or CODES, start_date=start, end_date=end), business or end,
            role="TEST_FIXTURE_FULL_OR_TAIL")).metadata

    def call(self, operation="sync", **kwargs):
        options = dict(source_root=SOURCE, cache_root=self.cache_root, baseline_record=self.baseline,
            target_session=TARGET, calendar_record=self.calendar, executor=self.executor,
            allow_test_fixture=True, offline=False, purpose="DELTA_FACTS_ONLY",
            admit=lambda _: dict(allowed=True, fixture="TEST_FIXTURE_ADMISSION"))
        options.update(kwargs)
        return getattr(factors, operation)(**options)

    def test_same_session_reuses_exact_full_original_without_write(self):
        before = {p: p.stat() for p in self.cache_root.rglob("*")}
        value = self.call(target_session=BASE)
        self.assertEqual(value["status"], "ORIGINAL_FULL_WINDOW_AVAILABLE")
        self.assertEqual(value["original_full_record"], self.baseline)
        self.assertEqual(value["new_requests"], 0)
        self.assertEqual(value["added_cache_logical_bytes"], 0)
        self.assertEqual(before, {p: p.stat() for p in self.cache_root.rglob("*")})
        self.assertTrue(value["native_factor_window_available"])
        self.assertFalse(value["strategy_input_promoted"])
        self.assertFalse(value["full_premarket_ready"])

    def test_cross_day_only_tail_then_reuse_same_physical_receipt(self):
        first = self.call()
        again = self.call("reuse")
        self.assertEqual(first["status"], "DELTA_FACTS_OBSERVED_REVALIDATION_REQUIRED")
        self.assertEqual(first["new_requests"], 1)
        self.assertEqual(again["new_requests"], 0)
        self.assertEqual(again["added_cache_logical_bytes"], 0)
        self.assertEqual(first["delta"], again["delta"])
        self.assertEqual(first["baseline"], self.baseline)
        self.assertEqual(first["new_factor_row_count"], 2)
        self.assertEqual(first["overlap_row_count"], 2)
        self.assertEqual(self.executor.calls[0][1]["start_date"], "2026-10-15")
        self.assertFalse(first["native_factor_window_available"])
        self.assertTrue(first["full_window_revalidation_required"])
        self.assertFalse(first["synthetic_full_result_created"])
        self.assertEqual(first["historical_revision_outside_overlap"], "UNKNOWN_NO_UPSTREAM_REVISION_PROOF")
        self.assertFalse((self.cache_root / "index" / first["required_full_window_request_key"]).exists())
        self.assertFalse(list(self.root.rglob("*.sqlite*")))

    def test_next_day_reuses_unchanged_full_baseline_and_queries_bounded_tail(self):
        first = self.call(target_session="2026-10-22")
        later = self.call()
        self.assertEqual(first["baseline"], later["baseline"])
        self.assertEqual([params["start_date"] for _, params in self.executor.calls], ["2026-10-15"]*2)
        self.assertNotEqual(first["delta_request_key"], later["delta_request_key"])
        self.assertEqual(later["new_requests"], 1)

    def test_plan_is_authenticated_and_never_downloads(self):
        value = self.call("plan")
        self.assertEqual(value["status"], "PLANNED_DELTA_FACTS_ONLY")
        self.assertEqual(value["worst_case_sdk_reservations"], 4)
        self.assertEqual(value["new_requests"], 0)
        self.assertEqual(value["added_cache_logical_bytes"], 0)
        self.assertEqual(self.executor.calls, [])

    def test_reuse_and_offline_missing_do_not_download(self):
        for kwargs in (dict(operation="reuse"), dict(operation="sync", offline=True)):
            value = self.call(**kwargs)
            self.assertEqual(value["status"], "MISSING_DELTA_NO_NETWORK")
            self.assertEqual(value["new_requests"], 0)
        self.assertFalse(self.executor.calls)

    def test_full_current_original_takes_precedence_without_tail_capture(self):
        full = self.seed(end=TARGET)
        value = self.call()
        self.assertEqual(value["original_full_record"], full)
        self.assertTrue(value["native_factor_window_available"])
        self.assertFalse(value["full_window_revalidation_required"])
        self.assertIsNone(value["delta"])
        self.assertEqual(value["new_requests"], 0)

    def test_historical_revision_and_removed_or_added_rows_are_detected(self):
        for scenario, kind in [("revision", "CHANGED_HISTORICAL_ROW"),
                               ("removed", "REMOVED_HISTORICAL_ROW"),
                               ("historical_added", "ADDED_HISTORICAL_ROW")]:
            with self.subTest(scenario=scenario):
                # Distinct explicit overlap scopes prevent test-fixture reuse.
                self.executor.scenario = scenario
                value = self.call(overlap_days={"revision": 7, "removed": 6, "historical_added": 5}[scenario])
                self.assertEqual(value["status"], "OVERLAP_REVISION_DETECTED_REVALIDATION_REQUIRED")
                self.assertTrue(any(change["change"] == kind for change in value["overlap_changes"]))
                self.assertFalse(value["native_factor_window_available"])
                self.assertEqual(value["baseline"], self.baseline)

    def test_empty_delta_is_explicit_no_events_not_factor_one(self):
        self.executor.scenario = "empty"
        value = self.call(overlap_days=1)
        self.assertEqual(value["status"], "DELTA_FACTS_OBSERVED_REVALIDATION_REQUIRED")
        self.assertEqual(value["new_factor_row_count"], 0)
        self.assertEqual(value["empty_delta_codes"], CODES)
        self.assertFalse(value["native_factor_window_available"])
        self.assertIn("NOT_FACTOR_ONE", value["empty_delta_meaning"])

    def test_partial_and_failed_sources_are_never_upgraded_or_retried(self):
        for scenario, overlap in [("partial", 7), ("failed", 6)]:
            self.executor.scenario = scenario
            value = self.call(overlap_days=overlap)
            again = self.call(overlap_days=overlap)
            self.assertEqual(value["status"], "DELTA_PARENT_INCOMPLETE_NO_RETRY")
            self.assertEqual(again["new_requests"], 0)
            self.assertEqual(value["delta"], again["delta"])
            self.assertNotIn("overlap_changes", value)
            self.assertFalse(value["native_factor_window_available"])

    def test_unfinished_delta_retained_and_blocks_retry(self):
        self.executor.scenario = "crash"
        with self.assertRaisesRegex(RuntimeError, "TEST_FIXTURE_CRASH"):
            self.call()
        self.executor.scenario = "normal"
        value = self.call()
        self.assertEqual(value["status"], "BLOCKED_UNFINISHED_DELTA_NO_RETRY")
        self.assertEqual(len(self.executor.calls), 1)
        self.assertEqual(value["new_requests"], 0)
        self.assertTrue(list(self.cache_root.rglob("attempt-failed.json")))

    def test_unfinished_full_does_not_trigger_smaller_request_as_retry(self):
        cache = CaptureCache(self.cache_root, Budget(factors.CAPS, cache_dir=self.cache_root),
            config=dict(allow_test_fixture=True), executor=FixtureExecutor("crash"))
        with self.assertRaises(RuntimeError):
            cache.capture_one(RequestSpec("adjustment_factors", dict(codes=CODES, start_date="1990-01-01", end_date=TARGET), TARGET))
        value = self.call()
        self.assertEqual(value["status"], "BLOCKED_UNFINISHED_FULL_WINDOW_NO_RETRY")
        self.assertEqual(value["new_requests"], 0)
        self.assertFalse(self.executor.calls)

    def test_failed_full_does_not_trigger_tail_as_implicit_retry(self):
        self.seed(end=TARGET, scenario="failed")
        value = self.call()
        self.assertEqual(value["status"], "FULL_WINDOW_PARENT_INCOMPLETE_NO_RETRY")
        self.assertEqual(value["new_requests"], 0)

    def test_rows_invalid_duplicate_and_outside_request_rejected(self):
        for scenario, overlap, message in [("invalid", 7, "INVALID_ORIGINAL_FACTOR"),
                                         ("duplicate", 6, "DUPLICATE_FACTOR"),
                                         ("out_of_scope", 5, "FACTOR_ROW_DATE_OUTSIDE")]:
            self.executor.scenario = scenario
            with self.subTest(scenario=scenario), self.assertRaisesRegex(ValueError, message):
                self.call(overlap_days=overlap)

    def test_only_original_full_anchor_and_exact_business_session(self):
        wrong = self.seed(start="2000-01-01")
        with self.assertRaisesRegex(ValueError, "FULL_1990_ANCHORED"):
            self.call(baseline_record=wrong)
        wrong = self.seed(business="2026-10-20")
        with self.assertRaisesRegex(ValueError, "FULL_1990_ANCHORED"):
            self.call(baseline_record=wrong)
        self.assertFalse(self.executor.calls)

    def test_unregistered_or_changed_metadata_is_rejected(self):
        changed = dict(self.baseline, receipt_id="f"*64)
        with self.assertRaisesRegex(ValueError, "EXACT_LATEST_FACTOR_PARENT"):
            self.call(baseline_record=changed)
        changed = dict(self.baseline, endpoint="history")
        with self.assertRaisesRegex(ValueError, "ORIGINAL_ADJUSTMENT_FACTORS"):
            self.call(baseline_record=changed)

    def test_tampered_source_is_error_not_cache_miss(self):
        path = Path(self.baseline["raw"]["path"])
        path.write_bytes(path.read_bytes()+b" ")
        with self.assertRaisesRegex(BudgetExceeded, "FILE_SHA256_CHANGED"):
            self.call("plan")
        self.assertFalse(self.executor.calls)

    def test_future_source_clock_is_rejected(self):
        # Original archive_receipt uses its actual ingest clock, patch that clock
        # too so this is internally consistent future evidence, not bad order.
        real = datetime
        class FutureClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return real.now(tz)+timedelta(days=1)
        with patch(__name__+".datetime", FutureClock), patch("stockfish_core.yaogu.providers.datetime", FutureClock):
            future = self.seed(end="2026-10-22")
        with self.assertRaisesRegex(ValueError, "FACTOR_SOURCE_FROM_FUTURE"):
            self.call(baseline_record=future)

    def test_future_target_and_noncanonical_dates_fail(self):
        for day in ("2999-01-01", "20261023", "2026-10-2", "2026-10-23T00:00:00"):
            with self.subTest(day=day), self.assertRaises(ValueError):
                self.call(target_session=day)
        self.assertFalse(self.executor.calls)

    def test_nontrading_or_prior_target_rejected(self):
        with self.assertRaisesRegex(ValueError, "BOUNDARY_NOT_TRADING"):
            self.call(target_session="2026-10-24")
        with self.assertRaisesRegex(ValueError, "TARGET_WITHIN_31_DAYS"):
            self.call(target_session="2026-10-20")

    def test_missing_calendar_day_rejected_before_network(self):
        executor = FixtureExecutor("calendar_gap")
        cache = CaptureCache(self.cache_root, Budget(factors.CAPS), config=dict(allow_test_fixture=True), executor=executor)
        record = cache.capture_one(RequestSpec("calendar", dict(start_date="2026-10-02", end_date="2026-11-25"), TARGET)).metadata
        with self.assertRaisesRegex(ValueError, "CALENDAR_NATURAL_DAY_GAP"):
            self.call(calendar_record=record)
        self.assertFalse(self.executor.calls)

    def test_budget_and_capacity_fail_before_network(self):
        with self.assertRaisesRegex(ValueError, "COMPLETE_FACTOR_DELTA_BUDGET"):
            self.call(limits=dict(max_sdk_calls=3))
        with self.assertRaisesRegex(ValueError, "GROWTH_RESERVATION"):
            self.call(limits=dict(cache_bytes=2*factors.MIB))
        with self.assertRaisesRegex(ValueError, "LIMIT_MUST_NOT_EXCEED"):
            self.call(limits=dict(max_requests=2))
        self.assertFalse(self.executor.calls)

    def test_admission_called_only_for_new_capture(self):
        values = []
        def admit(value):
            values.append(value)
            return dict(allowed=True, fixture="TEST_FIXTURE_ADMISSION")
        self.call(admit=admit)
        self.call(admit=admit)
        self.assertEqual(values, [16*factors.MIB]*2)

    def test_network_requires_real_admission_result(self):
        for admit, reason in [(None, "REGISTERED_ADMISSION"),
                              (lambda _: None, "CAPACITY_ADMISSION_DENIED"),
                              (lambda _: dict(allowed=False), "CAPACITY_ADMISSION_DENIED")]:
            with self.assertRaisesRegex(ValueError, reason):
                self.call(admit=admit)
        self.assertFalse(self.executor.calls)

    def test_admission_is_rechecked_under_original_writer_lock(self):
        calls = []
        def admit(_):
            calls.append(len(calls))
            return dict(allowed=len(calls) == 1)
        with self.assertRaisesRegex(ValueError, "CAPACITY_ADMISSION_DENIED"):
            self.call(admit=admit)
        self.assertEqual(len(calls), 2)
        self.assertFalse(self.executor.calls)
        self.assertTrue(list(self.cache_root.rglob("attempt-failed.json")))
        self.assertEqual(self.call()["status"], "BLOCKED_UNFINISHED_DELTA_NO_RETRY")

    def test_other_process_writer_blocks_new_network_without_duplicate(self):
        context = multiprocessing.get_context("fork")
        ready, release = context.Event(), context.Event()
        process = context.Process(target=_hold_cache_writer, args=(self.cache_root, ready, release))
        process.start()
        try:
            self.assertTrue(ready.wait(5))
            with self.assertRaises(StorageError):
                self.call()
            self.assertFalse(self.executor.calls)
        finally:
            release.set()
            process.join(5)
            if process.is_alive():
                process.terminate()
                process.join()
        self.assertEqual(process.exitcode, 0)
        self.assertEqual(self.call()["new_requests"], 1)

    def test_sync_requires_explicit_diagnostic_purpose(self):
        with self.assertRaisesRegex(ValueError, "EXPLICIT_DELTA_FACTS_ONLY_PURPOSE"):
            self.call(purpose=None)
        self.assertFalse(self.executor.calls)

    def test_fixture_executor_requires_explicit_gate(self):
        with self.assertRaisesRegex(ValueError, "FACTOR_EXECUTOR_REQUIRES_TEST_FIXTURE"):
            self.call(allow_test_fixture=False)

    def test_foreign_loaded_core_binding_rejected(self):
        foreign = types.ModuleType("stockfish_core.TEST_FIXTURE_foreign")
        foreign.__file__ = __file__
        with patch.dict(sys.modules, {foreign.__name__: foreign}):
            with self.assertRaisesRegex(ValueError, "ALREADY_LOADED_FOREIGN_CORE"):
                self.call()
        self.assertFalse(self.executor.calls)

    def test_cache_parent_symlink_rejected(self):
        alias = self.root / "alias"
        alias.symlink_to(self.cache_root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "CANONICAL_DIRECTORY"):
            self.call(cache_root=alias)

    def test_partial_baseline_blocks_tail_without_promoting_independent_fragments(self):
        partial = self.seed(end="2026-10-22", scenario="partial")
        value = self.call(baseline_record=partial)
        self.assertEqual(value["status"], "BASELINE_PARENT_INCOMPLETE_NO_RETRY")
        self.assertEqual(value["new_requests"], 0)
        self.assertFalse(self.executor.calls)

    def test_51_security_scope_rejected_instead_of_silently_truncated(self):
        codes = ["sh.600%03d" % value for value in range(51)]
        policy = dict(factors.CAPS, max_sdk_calls=53)
        cache = CaptureCache(self.cache_root, Budget(policy, cache_dir=self.cache_root),
            config=dict(allow_test_fixture=True), executor=FixtureExecutor())
        record = cache.capture_one(RequestSpec("adjustment_factors",
            dict(codes=codes, start_date="1990-01-01", end_date=BASE), BASE)).metadata
        with self.assertRaisesRegex(ValueError, "ONE_TO_50_ORIGINAL_MAINBOARD"):
            self.call(baseline_record=record)
        self.assertFalse(self.executor.calls)

    def test_old_baseline_over_31_days_requires_full_recheck(self):
        baseline = self.seed(end="2026-09-11")
        with self.assertRaisesRegex(ValueError, "TARGET_WITHIN_31_DAYS"):
            self.call(baseline_record=baseline)
        self.assertFalse(self.executor.calls)

    def test_admission_denial_does_not_launch_capture(self):
        def deny(_):
            raise RuntimeError("TEST_FIXTURE_CAPACITY_DENIED")
        with self.assertRaisesRegex(RuntimeError, "CAPACITY_DENIED"):
            self.call(admit=deny)
        self.assertFalse(self.executor.calls)

    def test_cli_cannot_enable_test_fixture_or_leak_native_exception(self):
        baseline, calendar = self.root / "baseline.json", self.root / "calendar.json"
        baseline.write_text(json.dumps(self.baseline))
        calendar.write_text(json.dumps(self.calendar))
        argv = ["plan", "--source-root", str(SOURCE), "--cache-root", str(self.cache_root),
                "--baseline-record", str(baseline), "--calendar-record", str(calendar),
                "--target-session", TARGET]
        with redirect_stdout(io.StringIO()) as output:
            code = factors.main(argv)
        value = json.loads(output.getvalue())
        self.assertEqual(code, 3)
        self.assertEqual(value["status"], "FAILED")
        self.assertIn("EXPLICIT_SUPPORTED_PROVIDER_OR_TEST_FIXTURE", value["reason"])
        self.assertFalse(self.executor.calls)
        with redirect_stdout(io.StringIO()) as output:
            code = factors.main(argv+["--execute-network"])
        self.assertEqual(code, 3)
        self.assertIn("REGISTERED_CHAT_COORDINATOR", json.loads(output.getvalue())["reason"])
        with patch.object(factors, "_operate", side_effect=BudgetExceeded("READ_LIMIT_EXCEEDED:capture")):
            with redirect_stdout(io.StringIO()) as output:
                code = factors.main(argv)
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(output.getvalue())["reason"], "READ_LIMIT_EXCEEDED:capture")

    def test_replay_json_is_not_full_native_source(self):
        report = self.call()
        with self.assertRaisesRegex(ValueError, "ORIGINAL_ADJUSTMENT_FACTORS_METADATA"):
            self.call(baseline_record=report)
        self.assertEqual(len(self.executor.calls), 1)

    def test_control_first_read_atime_change_preserves_seven_field_identity(self):
        path = self.root / "TEST_FIXTURE_first_read.json"
        path.write_text('{"source":"TEST_FIXTURE"}')
        original_lstat, original_fstat = Path.lstat, os.fstat
        counts = dict(lstat=0, fstat=0)
        def altered(info, advance):
            value = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
            if advance:
                value["st_atime"] += 1
                value["st_atime_ns"] += 1000000000
            return types.SimpleNamespace(**value)
        def lstat(current):
            info = original_lstat(current)
            if current == path:
                counts["lstat"] += 1
                return altered(info, counts["lstat"] > 1)
            return info
        def fstat(descriptor):
            counts["fstat"] += 1
            return altered(original_fstat(descriptor), counts["fstat"] > 1)
        with patch.object(Path, "lstat", autospec=True, side_effect=lstat), patch.object(os, "fstat", side_effect=fstat):
            self.assertEqual(factors._read_control(path), dict(source="TEST_FIXTURE"))
        self.assertEqual(counts, dict(lstat=2, fstat=2))

    def test_control_opened_descriptor_requires_all_seven_fields(self):
        path = self.root / "TEST_FIXTURE_open_race.json"
        path.write_text('{"source":"TEST_FIXTURE"}')
        original = os.fstat
        for field in factors.FP_KEYS:
            def changed(descriptor):
                info = original(descriptor)
                value = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
                value[field] = value.get(field, 0) + 1
                return types.SimpleNamespace(**value)
            with self.subTest(field=field), patch.object(os, "fstat", side_effect=changed):
                with self.assertRaisesRegex(ValueError, "FACTOR_CONTROL_OPENED_IDENTITY_CHANGED"):
                    factors._read_control(path)

    def test_control_post_read_descriptor_requires_all_seven_fields(self):
        path = self.root / "TEST_FIXTURE_read_race.json"
        path.write_text('{"source":"TEST_FIXTURE"}')
        original = os.fstat
        for field in factors.FP_KEYS:
            count = []
            def changed(descriptor):
                info = original(descriptor)
                count.append(1)
                value = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
                if len(count) == 2:
                    value[field] = value.get(field, 0) + 1
                return types.SimpleNamespace(**value)
            with self.subTest(field=field), patch.object(os, "fstat", side_effect=changed):
                with self.assertRaisesRegex(ValueError, "FACTOR_CONTROL_CHANGED_DURING_READ"):
                    factors._read_control(path)

    def test_control_same_bytes_path_replacement_after_open_is_rejected(self):
        path = self.root / "TEST_FIXTURE_replace.json"
        content = b'{"source":"TEST_FIXTURE"}'
        path.write_bytes(content)
        original = os.fstat
        count = []
        def replace_after_read(descriptor):
            info = original(descriptor)
            count.append(1)
            if len(count) == 2:
                other = path.with_suffix(".replacement")
                other.write_bytes(content)
                other.replace(path)
            return info
        with patch.object(os, "fstat", side_effect=replace_after_read):
            with self.assertRaisesRegex(ValueError, "FACTOR_CONTROL_CHANGED_DURING_READ"):
                factors._read_control(path)

    def test_control_symlink_and_oversize_are_rejected(self):
        original = self.root / "TEST_FIXTURE_original.json"
        original.write_text('{}')
        link = self.root / "TEST_FIXTURE_alias.json"
        link.symlink_to(original)
        with self.assertRaisesRegex(ValueError, "CANONICAL_FACTOR_CONTROL_REQUIRED"):
            factors._read_control(link)
        original.write_bytes(b' '*(2*factors.MIB+1))
        with self.assertRaisesRegex(ValueError, "FACTOR_CONTROL_SIZE_LIMIT"):
            factors._read_control(original)

    def test_inventory_checkpoint_enforces_deadline_during_walk(self):
        calls = []
        def deadline():
            calls.append(1)
            if len(calls) == 4:
                raise factors.FactorIncrementalError("TEST_FIXTURE_DEADLINE")
        with self.assertRaisesRegex(ValueError, "TEST_FIXTURE_DEADLINE"):
            factors._inventory(self.cache_root, deadline)
        self.assertEqual(len(calls), 4)
        self.assertFalse(self.executor.calls)

    def test_overlap_bounds_reject_without_new_requests(self):
        for value in (0, 32, True, 1.0):
            with self.assertRaisesRegex(ValueError, "OVERLAP_ONE_TO_31"):
                self.call(overlap_days=value)
        self.assertFalse(self.executor.calls)


if __name__ == "__main__":
    unittest.main()
