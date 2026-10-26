"""TEST_FIXTURE scopes and original capture authority; no real SDK calls."""
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import codex_latest_capture as latest
from scripts import codex_workspace as workspace
from stockfish_core.yaogu import provider_requests
from stockfish_core.yaogu.premarket_capture_cache_v1 import CaptureCache, RequestSpec, PRODUCERS
from stockfish_core.yaogu.premarket_runtime_v1 import Budget, BudgetExceeded
from stockfish_core.yaogu.providers import archive_receipt

CODES = ["sh.600000", "sz.000001"]
SESSIONS = ["2026-10-22", "2026-10-23", "2026-10-26"]
HISTORY = "LATEST_SCOPED_HISTORY_V1"
MARKET = "LATEST_MARKET_SESSION_V1"


class ClosedMonday(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 26, 12, tzinfo=timezone.utc).astimezone(tz) if tz else cls(2026, 10, 26, 12)


class Fixture:
    def __init__(self, scenario="normal"):
        self.scenario, self.calls = scenario, []

    def __call__(self, endpoint, params, output, **kwargs):
        self.calls.append((endpoint, params))
        if endpoint != "calendar" and self.scenario == "crash":
            raise RuntimeError("TEST_FIXTURE_CRASH")
        started = datetime.now(timezone.utc).isoformat()
        first = datetime.now(timezone.utc).isoformat()
        if endpoint == "calendar":
            a, z = (date.fromisoformat(params[k]) for k in ("start_date", "end_date"))
            rows = [dict(calendar_date=str(a+timedelta(days=n)),
                         is_trading_day=str(int((a+timedelta(days=n)).weekday() < 5)))
                    for n in range((z-a).days+1)]
        elif endpoint in ("history", "market_daily"):
            a, z = (date.fromisoformat(params[k]) for k in ("start_date", "end_date")) if endpoint == "history" else (
                date.fromisoformat(params["date"]), date.fromisoformat(params["date"]))
            rows = [dict(code=code, date=str(a+timedelta(days=n)), adjustflag="3", open="10", high="11",
                         low="9", close="10.5", preclose="10.1", volume="100", amount="1000", turn="1",
                         tradestatus="1", isST="0")
                    for n in range((z-a).days+1) for code in params.get("codes", CODES)
                    if (a+timedelta(days=n)).weekday() < 5 or self.scenario == "weekend_row"]
        elif endpoint == "master_all":
            rows = [dict(code=code, code_name="TEST_FIXTURE", tradeStatus="1") for code in CODES]
        else:
            raise AssertionError(endpoint)
        status, pagination = "COMPLETE", True
        if endpoint != "calendar":
            if self.scenario == "partial":
                status, pagination = "PARTIAL", False
            elif self.scenario == "empty":
                rows = []
            elif self.scenario == "missing":
                rows = rows[1:]
            elif self.scenario == "invalid_price":
                rows[0]["close"] = "NaN"
            elif self.scenario == "duplicate":
                rows.append(dict(rows[0]))
        payload = dict(rows=rows, status=status, pagination_complete=pagination, coverage={}, gaps=[],
                       field_version="TEST_FIXTURE_PROVIDER_ROWS_V1", first_seen_at=first)
        receipt = archive_receipt(Path(output)/"raw", "TEST_FIXTURE", endpoint, params, payload,
            status=status, pagination_complete=pagination, field_version="TEST_FIXTURE_PROVIDER_ROWS_V1", first_seen_at=first)
        result = dict(endpoint=endpoint, provider="TEST_FIXTURE", parameters=params, rows=rows,
            status=status, pagination_complete=pagination, coverage={}, gaps=[], field_version="TEST_FIXTURE_PROVIDER_ROWS_V1",
            request_receipt_id=receipt["receipt_id"], receipts=[receipt], started_at=started,
            completed_at=datetime.now(timezone.utc).isoformat(), diagnostics=dict(fixture="TEST_FIXTURE"),
            implementation_source_hashes={name: hashlib.sha256(Path(provider_requests.__file__).with_name(name).read_bytes()).hexdigest()
                                          for name in PRODUCERS})
        Path(output, "request-TEST_FIXTURE.json").write_text(json.dumps(result))
        return result


class LatestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="TEST_FIXTURE_latest_capture_")
        self.root = Path(self.temp.name).resolve()
        self.cache_root = self.root / "cache"
        self.cache_root.mkdir()
        self.fixture = Fixture()
        self.clock = patch.object(latest, "datetime", ClosedMonday)
        self.clock.start()
        policy, _ = latest._policy(HISTORY, "scoped-history")
        self.cache = CaptureCache(self.cache_root, Budget(policy, cache_dir=self.cache_root),
            config=dict(allow_test_fixture=True), executor=self.fixture)
        self.calendar = self.cache.capture_one(RequestSpec("calendar",
            dict(start_date="2026-10-19", end_date="2026-10-30"), "2026-10-26",
            role="TEST_FIXTURE_CALENDAR")).metadata
        self.fixture.calls.clear()

    def tearDown(self):
        self.clock.stop()
        self.temp.cleanup()

    def call(self, **values):
        defaults = dict(cache_root=self.cache_root, mode="scoped-history", profile=HISTORY,
            calendar_record=self.calendar, operation="sync", codes=CODES, sessions=SESSIONS,
            execute_network=True, executor=self.fixture, allow_test_fixture=True,
            admit=lambda _: {"allowed": True})
        defaults.update(values)
        return latest.run_scope(**defaults)

    def seed(self, codes=None, sessions=None, scenario="normal"):
        sessions = sessions or SESSIONS
        return CaptureCache(self.cache_root, self.cache.budget, config=dict(allow_test_fixture=True),
            executor=Fixture(scenario)).capture_one(RequestSpec("history", dict(codes=codes or CODES,
                start_date=min(sessions), end_date=max(sessions)), max(sessions), role="TEST_FIXTURE_OLD_PARENT")).metadata

    def test_first_missing_three_sessions_merge_across_weekend_then_reuse(self):
        first = self.call()
        second = self.call(execute_network=False)
        self.assertEqual((first["old_daily_group_requests"], first["merged_missing_requests"], first["new_requests"]), (3, 1, 1))
        self.assertEqual(first["metrics"]["calls"]["sdk_reserved"], 4)
        self.assertEqual(first["actual_raw_rows"], 6)
        self.assertEqual(self.fixture.calls, [("history", dict(codes=CODES, start_date=SESSIONS[0], end_date=SESSIONS[-1]))])
        self.assertEqual((second["new_requests"], second["added_cache_logical_bytes"], second["cache_hits"]), (0, 0, 6))
        self.assertEqual(first["new_batch_receipts"], second["original_range_receipts"])
        self.assertFalse(first["strategy_executed"])
        self.assertFalse(first["full_premarket_ready"])
        self.assertFalse(first["historical_67_session_backfill"])
        self.assertIsNone(first["network_transfer_bytes"])

    def test_plan_verifies_without_network_or_disk_growth(self):
        before = {p: p.stat() for p in self.cache_root.rglob("*")}
        value = self.call(operation="plan", execute_network=False)
        self.assertEqual(value["status"], "PLAN_VERIFIED_ORIGINAL_SOURCES")
        self.assertEqual(value["planned_sdk_reservations"], 4)
        self.assertEqual(value["new_requests"], 0)
        self.assertEqual(before, {p: p.stat() for p in self.cache_root.rglob("*")})
        self.assertFalse(self.fixture.calls)

    def test_no_bridge_across_unrequested_trading_day(self):
        value = self.call(sessions=[SESSIONS[0], SESSIONS[-1]])
        self.assertEqual(value["new_requests"], 2)
        self.assertTrue(all(params["start_date"] == params["end_date"] for _, params in self.fixture.calls))

    def test_different_missing_code_sets_do_not_redownload_existing_row(self):
        original = self.seed(codes=CODES[:1], sessions=[SESSIONS[1]])
        value = self.call()
        self.assertEqual(value["new_requests"], 3)
        self.assertEqual(self.fixture.calls[1][1]["codes"], CODES[1:])
        self.assertEqual(next(row for row in value["shards"] if row["request_spec"]["business_session"] == SESSIONS[1]
                              and row["request_spec"]["parameters"]["codes"] == CODES[:1])["receipt"], original)

    def test_failed_partial_empty_missing_invalid_rows_remain_unknown_without_retry(self):
        for scenario in ("partial", "empty", "missing", "invalid_price", "duplicate"):
            with self.subTest(scenario=scenario):
                other = self.root / scenario
                other.mkdir()
                policy, _ = latest._policy(HISTORY, "scoped-history")
                fixture = Fixture(scenario)
                cache = CaptureCache(other, Budget(policy), config=dict(allow_test_fixture=True), executor=fixture)
                calendar = cache.capture_one(RequestSpec("calendar", self.calendar["parameters"], "2026-10-26")).metadata
                fixture.calls.clear()
                a = self.call(cache_root=other, calendar_record=calendar, executor=fixture)
                b = self.call(cache_root=other, calendar_record=calendar, executor=fixture, execute_network=False)
                self.assertEqual(a["status"], "PARTIAL_EXPLICIT_SOURCE_SCOPE")
                self.assertEqual(b["new_requests"], 0)
                self.assertEqual(len(fixture.calls), 1)

    def test_failed_unfinished_range_is_not_retried_as_smaller_scopes(self):
        self.fixture.scenario = "crash"
        with self.assertRaisesRegex(RuntimeError, "TEST_FIXTURE_CRASH"):
            self.call()
        self.fixture.scenario = "normal"
        value = self.call(codes=CODES[:1], sessions=SESSIONS[-1:])
        self.assertEqual(value["status"], "BLOCKED_UNFINISHED_CAPTURE_NO_RETRY")
        self.assertEqual(value["new_requests"], 0)
        self.assertEqual(len(self.fixture.calls), 1)

    def test_weekend_returned_row_rejected_in_fresh_scope(self):
        self.fixture.scenario = "weekend_row"
        with self.assertRaisesRegex(latest.LatestCaptureError, "FRESH_ROWS_OUTSIDE_DECLARED"):
            self.call()
        with self.assertRaisesRegex(latest.LatestCaptureError, "ORIGINAL_HISTORY_ROW_ON_NONTRADING_DAY"):
            self.call()

    def test_calendar_closed_day_and_actual_currentness_required(self):
        with self.assertRaisesRegex(latest.LatestCaptureError, "LATEST_COMPLETED_SESSION_MUST_BE_INCLUDED"):
            self.call(sessions=SESSIONS[:2])
        class BeforeClose(ClosedMonday):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 10, 26, 9, tzinfo=timezone.utc).astimezone(tz)
        with patch.object(latest, "datetime", BeforeClose):
            with self.assertRaisesRegex(latest.LatestCaptureError, "REQUESTED_SESSION_NOT_COMPLETED"):
                self.call()
        old = self.cache.capture_one(RequestSpec("calendar",
            dict(start_date="2026-10-19", end_date="2026-10-26"), "2026-10-26")).metadata
        self.fixture.calls.clear()
        with self.assertRaisesRegex(latest.LatestCaptureError, "CALENDAR_MUST_COVER_ACTUAL"):
            self.call(calendar_record=old)
        self.assertFalse(self.fixture.calls)

    def test_crossing_next_close_during_capture_refuses_stale_latest_completion(self):
        class CrossingGate(ClosedMonday):
            calls = 0
            @classmethod
            def now(cls, tz=None):
                cls.calls += 1
                return cls(2026, 10, 26, 9 if cls.calls == 1 else 10, 59 if cls.calls == 1 else 1,
                           tzinfo=timezone.utc).astimezone(tz)
        with patch.object(latest, "datetime", CrossingGate):
            with self.assertRaisesRegex(latest.LatestCaptureError, "LATEST_COMPLETED_SESSION_CHANGED_AT_TERMINAL"):
                self.call(sessions=SESSIONS[:2])
        self.assertEqual(len(self.fixture.calls), 1)

    def test_scope_profiles_budget_and_fixture_are_explicit(self):
        for options, message in [({"profile": MARKET}, "EXPLICIT_MATCHING"),
            ({"allow_test_fixture": False}, "EXECUTOR_REQUIRES"),
            ({"admit": None}, "REGISTERED_NETWORK_ADMISSION"),
            ({"operation": "plan"}, "PLAN_CANNOT_EXECUTE_NETWORK"),
            ({"limits": {"cache_bytes": 257*latest.MIB}}, "MUST_NOT_EXCEED"),
            ({"limits": {"max_sdk_calls": 3}}, "COMPLETE_LATEST_REQUEST_BUDGET")]:
            with self.subTest(options=options):
                with self.assertRaisesRegex(latest.LatestCaptureError, message):
                    self.call(**options)
        self.assertFalse(self.fixture.calls)

    def test_capacity_negative_or_missing_allowed_never_calls_provider(self):
        for verdict in ({}, {"allowed": False}):
            with self.subTest(verdict=verdict):
                # Different original keys keep the failed admission history.
                code = [CODES[len(list((self.cache_root/"raw").iterdir())) % 2]]
                with self.assertRaisesRegex(latest.LatestCaptureError, "LATEST_NETWORK_CAPACITY_BLOCKED"):
                    self.call(codes=code, admit=lambda _, v=verdict: v)
        self.assertFalse(self.fixture.calls)

    def test_growth_limit_rejects_but_retains_attempt(self):
        with self.assertRaisesRegex(latest.LatestCaptureError, "GROWTH_LIMIT"):
            self.call(limits=dict(output_bytes=1024))
        self.assertEqual(len(self.fixture.calls), 1)
        self.assertTrue(list(self.cache_root.glob("raw/*/raw/*/attempt-failed.json")))
        self.assertEqual(self.call()["status"], "BLOCKED_UNFINISHED_CAPTURE_NO_RETRY")

    def test_market_only_latest_two_original_endpoints_then_zero(self):
        value = self.call(mode="market-session", profile=MARKET, codes=None, sessions=None)
        self.assertEqual(value["new_requests"], 2)
        self.assertEqual(value["metrics"]["calls"]["sdk_reserved"], 6)
        self.assertEqual(self.fixture.calls, [("market_daily", {"date": SESSIONS[-1]}), ("master_all", {"date": SESSIONS[-1]})])
        later = self.call(mode="market-session", profile=MARKET, codes=None, sessions=None, execute_network=False)
        self.assertEqual((later["new_requests"], later["added_cache_logical_bytes"]), (0, 0))
        self.assertEqual(later["status"], "COMPLETE_EXPLICIT_SOURCE_SCOPE")

    def test_market_partial_retained_not_refetched(self):
        self.fixture.scenario = "partial"
        first = self.call(mode="market-session", profile=MARKET, codes=None, sessions=None)
        second = self.call(mode="market-session", profile=MARKET, codes=None, sessions=None, execute_network=False)
        self.assertEqual(first["status"], "PARTIAL_EXPLICIT_SOURCE_SCOPE")
        self.assertEqual(second["new_requests"], 0)
        self.assertEqual(len(self.fixture.calls), 2)

    def test_changed_original_calendar_cannot_be_treated_as_missing(self):
        Path(self.calendar["raw"]["path"]).write_text("corrupt TEST_FIXTURE")
        with self.assertRaisesRegex(BudgetExceeded, "FILE_SHA256_CHANGED"):
            self.call()
        self.assertFalse(self.fixture.calls)

    def test_registered_lock_admission_and_configuration_are_preserved(self):
        config = self.root / "config.json"
        config.write_text(json.dumps({"capture": {"offline": False}, "limits": {"cache_bytes": 64*latest.MIB}}))
        active, admissions = [], []
        @contextmanager
        def exclusive():
            active.append(True)
            try:
                yield
            finally:
                active.pop()
        def preflight(**kwargs):
            self.assertTrue(active)
            admissions.append(kwargs)
            return {"allowed": True}
        runtime = SimpleNamespace(profile={"backend": "premarket", "report_root": str(self.root)},
            exclusive=exclusive, preflight=preflight, check_binding=lambda: None)
        prepared = dict(source=ROOT, python=Path(sys.executable).absolute(), state=self.root, config=config)
        original = config.read_bytes(), config.stat()
        with patch("scripts.codex_premarket._prepared", return_value=prepared), patch.object(latest, "run_scope", return_value={"status": "TEST_FIXTURE"}) as scoped:
            value = latest.run_registered(runtime, operation="plan", mode="scoped-history", profile=HISTORY,
                                          calendar_record=self.calendar, codes=CODES, sessions=SESSIONS)
            self.assertEqual(scoped.call_args.kwargs["limits"]["cache_bytes"], 256*latest.MIB)
        self.assertEqual(original, (config.read_bytes(), config.stat()))
        self.assertTrue(value["registered_config_unchanged"])
        self.assertEqual(len(admissions), 2)
        with patch("scripts.codex_premarket._prepared", return_value=prepared):
            with self.assertRaisesRegex(latest.LatestCaptureError, "PROCESS_SUPERVISOR"):
                latest.run_registered(runtime, operation="sync", mode="scoped-history", profile=HISTORY,
                    calendar_record=self.calendar, codes=CODES, sessions=SESSIONS, execute_network=True)


@unittest.skipUnless(os.name == "posix", "Original owned-process supervisor requires POSIX")
class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="TEST_FIXTURE_latest_supervisor_")
        self.root = Path(self.temp.name).resolve()
        self.cache_root = self.root / "state" / "cache"
        self.report = self.root / "reports"
        self.cache_root.mkdir(parents=True)
        self.report.mkdir()
        self.config = self.root / "config.json"
        self.config.write_text('{"TEST_FIXTURE":true}')
        @contextmanager
        def exclusive():
            yield
        self.runtime = SimpleNamespace(profile=dict(backend="premarket", name="TEST_FIXTURE",
            report_root=str(self.report)), registry_root=self.root, exclusive=exclusive,
            preflight=lambda **_: {"allowed": True})
        self.prepared = dict(source=ROOT, python=Path(sys.executable).absolute(),
                             state=self.cache_root.parent, config=self.config)
        self.actual_popen, self.process = subprocess.Popen, None

    def tearDown(self):
        if self.process is not None and self.process.poll() is None:
            self.process.kill()
            self.process.wait()
        self.temp.cleanup()

    def launch(self, statement):
        def popen(command, **kwargs):
            if "--worker-control" not in command:
                return self.actual_popen(command, **kwargs)
            control = Path(command[command.index("--worker-control")+1])
            prelude = "import json, pathlib, time, sys; p=pathlib.Path("+repr(str(control))+"); "
            prelude += "json.dump("+repr(dict(cache_root=str(self.cache_root), bytes=0))+", (p/'locked-cache-baseline.json').open('w')); "
            self.process = self.actual_popen([sys.executable, "-I", "-B", "-c", prelude+statement], **kwargs)
            return self.process
        return popen

    def run_supervisor(self, statement, **patches):
        with patch("scripts.codex_premarket._prepared", return_value=self.prepared), \
             patch.object(latest.subprocess, "Popen", side_effect=self.launch(statement)):
            return latest.supervised_command(self.runtime, operation="sync", mode="scoped-history",
                profile=HISTORY, calendar_record={"TEST_FIXTURE": True}, codes=CODES,
                sessions=SESSIONS, execute_network=True)

    def test_actual_owned_subprocess_success_and_completion(self):
        result = self.run_supervisor("json.dump({'schema_version': 'CODEX_LATEST_CAPTURE_V1', 'status': 'COMPLETE_EXPLICIT_SOURCE_SCOPE'}, (p/'result.json').open('w')); time.sleep(.3)")
        self.assertEqual(self.process.returncode, 0)
        self.assertTrue(result["external_supervision"]["reaped"])
        self.assertGreater(result["external_supervision"]["peak_tree_rss_bytes"], 0)
        self.assertEqual(result["external_supervision"]["peak_added_cache_bytes"], 0)
        self.assertTrue(list(self.report.glob("*/completion.json")))

    def test_worker_failure_is_preserved_and_reported(self):
        with self.assertRaisesRegex(latest.LatestCaptureError, "TEST_FIXTURE_FAILURE"):
            self.run_supervisor("json.dump({'schema_version': 'CODEX_LATEST_CAPTURE_V1', 'status': 'FAILED', 'error': 'TEST_FIXTURE_FAILURE'}, (p/'result.json').open('w')); sys.exit(3)")
        self.assertEqual(self.process.returncode, 3)
        self.assertTrue(list(self.report.glob("*/failure.json")))

    def test_tree_rss_limit_stops_only_owned_group(self):
        with patch("stockfish_core.yaogu.premarket_workflow_v1._tree_sample", return_value=latest.BASE_LIMITS["tree_rss_bytes"]+1):
            with self.assertRaisesRegex(latest.LatestCaptureError, "TREE_RSS_LIMIT"):
                self.run_supervisor("time.sleep(10)")
        self.assertIsNotNone(self.process.returncode)
        self.assertTrue(list(self.report.glob("*/failure.json")))

    def test_growth_limit_stops_worker_and_retains_generated_bytes(self):
        statement = "pathlib.Path("+repr(str(self.cache_root / "TEST_FIXTURE_growth"))+").write_bytes(b'x'*(4*1024*1024+1)); time.sleep(10)"
        with self.assertRaisesRegex(latest.LatestCaptureError, "CACHE_GROWTH_LIMIT"):
            self.run_supervisor(statement)
        self.assertEqual((self.cache_root / "TEST_FIXTURE_growth").stat().st_size, 4*latest.MIB+1)
        self.assertIsNotNone(self.process.returncode)

    def test_changed_request_cannot_be_accepted_by_supervisor(self):
        statement = "json.dump({}, (p/'request.json').open('w')); json.dump({'schema_version': 'CODEX_LATEST_CAPTURE_V1', 'status': 'COMPLETE_EXPLICIT_SOURCE_SCOPE'}, (p/'result.json').open('w')); time.sleep(.3)"
        with self.assertRaisesRegex(latest.LatestCaptureError, "SUPERVISOR_REQUEST_CHANGED"):
            self.run_supervisor(statement)

    def test_worker_ticket_requires_parent_owned_session_and_no_prior_attempt(self):
        control = self.report / "latest-capture-abc"
        control.mkdir()
        (control / "console.txt").write_text("")
        workspace._write(control / "request.json", dict(supervisor=dict(parent={"pid": 42, "start": "TEST_FIXTURE"}, launch_id="abc")))
        with patch.object(latest.os, "getpgrp", return_value=2), patch.object(latest.os, "getpid", return_value=1):
            with self.assertRaisesRegex(latest.LatestCaptureError, "OWNED_PROCESS_SESSION"):
                latest._validate_worker_ticket(self.runtime, control)
        with patch.object(latest.os, "getpgrp", return_value=1), patch.object(latest.os, "getpid", return_value=1), \
             patch.object(latest.os, "getsid", return_value=1), patch.object(latest, "_process_identity", return_value={"pid": 42, "start": "TEST_FIXTURE"}):
            latest._validate_worker_ticket(self.runtime, control)
            (control / "result.json").write_text("{}")
            with self.assertRaisesRegex(latest.LatestCaptureError, "ALREADY_ATTEMPTED"):
                latest._validate_worker_ticket(self.runtime, control)


if __name__ == "__main__":
    unittest.main()
