"""Isolated TEST_FIXTURE checks for a restriction-only premarket deadline."""
from contextlib import nullcontext
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
import socket
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from scripts import codex_premarket_shared_v1 as shared
from stockfish_core.yaogu.premarket_runtime_v1 import BudgetExceeded


EXPECTED = ("2026-11-06", "2026-11-09", "2026-11-09")
DEADLINE = "2026-11-09T09:30:00+08:00"
BEFORE = datetime(2026, 11, 9, 1, 29, 59, tzinfo=timezone.utc)
AT_OPEN = datetime(2026, 11, 9, 1, 30, tzinfo=timezone.utc)


def clock(state):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            value = state[0]
            return cls.fromtimestamp(value.timestamp(), tz=tz)
    return Clock


def test_opening_boundary_and_original_session_gate_are_distinct():
    assert shared._premarket_gate(EXPECTED, BEFORE) == DEADLINE
    with pytest.raises(BudgetExceeded, match="OPENING_DEADLINE_EXCEEDED"):
        shared._premarket_gate(EXPECTED, AT_OPEN)
    assert shared._expected_gate(EXPECTED, AT_OPEN) == AT_OPEN
    with pytest.raises(ValueError, match="REQUIRES_EXPECTED_SESSIONS"):
        shared._premarket_gate(None)
    with pytest.raises(ValueError, match="AWARE_CURRENT_CLOCK_REQUIRED"):
        shared._premarket_gate(EXPECTED, BEFORE.replace(tzinfo=None))


def test_supervisor_deadline_is_sticky_even_if_wall_clock_moves_back():
    state = [BEFORE]
    with patch.object(shared, "datetime", clock(state)):
        guard = shared._SupervisorGuard({"max_seconds": 30}, expected_sessions=EXPECTED,
                                        require_premarket=True)
        guard.check()
        state[0] = AT_OPEN
        with pytest.raises(BudgetExceeded, match="OPENING_DEADLINE_EXCEEDED"):
            guard.check()
        state[0] = BEFORE
        with pytest.raises(BudgetExceeded, match="SUPERVISOR_BUDGET_EXHAUSTED"):
            guard.check()


def test_ordinary_supervisor_unchanged_and_monotonic_limit_still_applies():
    with patch.object(shared, "datetime", clock([AT_OPEN])):
        guard = shared._SupervisorGuard({"max_seconds": 30}, expected_sessions=EXPECTED)
        guard.check()
        assert guard.premarket_deadline is None
        guard.started -= 31
        with pytest.raises(BudgetExceeded, match="TOTAL_TIME_LIMIT_EXCEEDED"):
            guard.check()


@pytest.mark.parametrize("flag", [1, "true", None])
def test_deadline_flag_has_strict_boolean_type(flag):
    with pytest.raises(ValueError, match="DEADLINE_FLAG_REQUIRED"):
        shared._SupervisorGuard({"max_seconds": 30}, require_premarket=flag)
    with pytest.raises(ValueError, match="DEADLINE_FLAG_REQUIRED"):
        shared.execute_workflow_shared({}, Path("TEST_FIXTURE_unused"), require_premarket=flag)


def test_production_cannot_supply_fixture_clock():
    with pytest.raises(ValueError, match="PRODUCTION_CLOCK_OVERRIDE_FORBIDDEN"):
        shared.execute_workflow_shared({}, Path("TEST_FIXTURE_unused"), now=BEFORE,
            expected_sessions=EXPECTED, require_premarket=True)


def test_new_source_crossing_opening_keeps_commit_and_prevents_more_requests():
    state, committed = [BEFORE], []
    class Original:
        def _capture_new(self, spec, **kwargs):
            committed.append(spec)
            state[0] = AT_OPEN
            return SimpleNamespace(status="COMPLETE", metadata={"pagination_complete": True})
    cache = shared._premarket_cache(Original, lambda: shared._premarket_gate(EXPECTED, state[0]))()
    with pytest.raises(BudgetExceeded, match="OPENING_DEADLINE_EXCEEDED"):
        cache._capture_new("TEST_FIXTURE_first")
    assert committed == ["TEST_FIXTURE_first"]
    state[0] = BEFORE
    with pytest.raises(ValueError, match="FAILED_NO_MORE_REQUESTS"):
        cache._capture_new("TEST_FIXTURE_second")
    assert committed == ["TEST_FIXTURE_first"]


@pytest.mark.parametrize("status,pagination", [("FETCH_FAILED", True), ("PARTIAL", True),
                                               ("EMPTY", True), ("COMPLETE", False)])
def test_known_new_failure_keeps_original_collection_semantics(status, pagination):
    committed = []
    class Original:
        def _capture_new(self, spec, **kwargs):
            record = SimpleNamespace(status=status, metadata={"pagination_complete": pagination})
            committed.append(record)
            return record
    cache = shared._premarket_cache(Original, lambda: DEADLINE)()
    record = cache._capture_new("TEST_FIXTURE_first")
    assert len(committed) == 1 and committed[0] is record and record.status == status
    assert cache._premarket_stopped is False


def test_deadline_before_source_does_not_call_original():
    class Original:
        def _capture_new(self, *args, **kwargs):
            raise AssertionError("TEST_FIXTURE_PROVIDER_FORBIDDEN")
    cache = shared._premarket_cache(Original, lambda: shared._premarket_gate(EXPECTED, AT_OPEN))()
    with pytest.raises(BudgetExceeded, match="OPENING_DEADLINE_EXCEEDED"):
        cache._capture_new("TEST_FIXTURE_missing")


def test_owned_worker_flag_and_final_deadline_binding(tmp_path):
    state = [BEFORE]
    with patch.object(shared, "datetime", clock(state)):
        guard = shared._SupervisorGuard({"max_seconds": 30, "output_bytes": 4096},
            expected_sessions=EXPECTED, require_premarket=True)
        worker = shared._WorkerSubprocess(tmp_path / "TEST_FIXTURE_absent", guard)
        command = [shared.sys.executable, "-B", "-m", shared.legacy.__spec__.name,
                   "_worker", "--job", str(tmp_path)]
        with patch.object(shared.subprocess, "Popen") as popen:
            worker.Popen(command, env={})
        assert popen.call_args.args[0][-1] == "--require-premarket"
        parsed = shared.parser().parse_args(["_worker", "--job", str(tmp_path), "--require-premarket"])
        assert parsed.require_premarket is True
        value = {"status": "COMPLETE"}
        with patch.object(shared.legacy, "_seal"):
            guard.seal(tmp_path / "final-control.json", value)
        assert value["premarket_deadline"] == DEADLINE


def test_completion_binds_deadline_and_checks_again_after_source_validation():
    gates = []
    budget = SimpleNamespace(premarket_deadline=DEADLINE, premarket_gate=lambda: gates.append(True),
        phase=lambda _: nullcontext(), shared_source_refs=[], execution_engine="original",
        phase_timings=[], expected_sessions=EXPECTED, charge_read=lambda _: None)
    def completion(job, **fields):
        return fields
    with patch.object(shared.legacy, "_completion", completion):
        result = shared._shared_completion(Path("TEST_FIXTURE_job"), status="COMPLETE", phases={},
            budget=budget, validation_references=[])
    assert result["premarket_deadline"] == DEADLINE
    assert len(gates) == 2


def test_full_native_workflow_deadline_preserves_candidates_order_and_artifacts():
    from tests.yaogu import test_premarket_shared_workflow_v1 as differential
    from tests.yaogu.test_premarket_workflow_v1 import WorkflowFixtureTests
    fixture = WorkflowFixtureTests(methodName="runTest")
    fixture.setUp()
    fixture.config["limits"]["max_seconds"] = 120
    try:
        adapters, now = fixture._adapters()
        collection = adapters["collect"]()
        expected = (collection.observation_date, collection.target_session,
                    str(now.astimezone(shared.legacy.SH).date()))
        ordinary = partial(shared.execute_workflow_shared, expected_sessions=expected)
        restricted = partial(shared.execute_workflow_shared, expected_sessions=expected, require_premarket=True)
        with patch.object(socket.socket, "connect", side_effect=AssertionError("TEST_FIXTURE_NETWORK")), \
             patch.object(sqlite3, "connect", side_effect=AssertionError("TEST_FIXTURE_DB")):
            before, _ = differential._execute(fixture, ordinary, "TEST_FIXTURE_before", adapters, now)
            after, _ = differential._execute(fixture, restricted, "TEST_FIXTURE_premarket", adapters, now)
        assert before["status"] == after["status"] == "COMPLETED_WITH_UNKNOWNS"
        assert before["phases"] == after["phases"]
        assert before["run_id"] == after["run_id"]
        assert before["display"]["counts"] == after["display"]["counts"]
        assert before["display"]["original_selected_items_hash"] == after["display"]["original_selected_items_hash"]
        assert "premarket_deadline" not in before
        assert after["premarket_deadline"] == shared._premarket_gate(expected, now)
        assert after["core_verified"] is True
        assert after["metrics"]["calls"]["requests"] == before["metrics"]["calls"]["requests"] == 0
        for name in ("all.csv", "observation.csv", "focus.csv", "unselected.csv", "observation.json"):
            assert Path(before["core_artifacts"][name]["path"]).read_bytes() == \
                Path(after["core_artifacts"][name]["path"]).read_bytes()
        first = differential._read(before["theme_artifacts"]["native-theme-parent.json.gz"]["path"])
        second = differential._read(after["theme_artifacts"]["native-theme-parent.json.gz"]["path"])
        for name in ("items", "effective_input_hash", "source_refs", "computed_exchange_sessions",
                     "source_input_hash", "membership_mode", "status", "market_edge"):
            assert first[name] == second[name]
    finally:
        fixture.doCleanups()
