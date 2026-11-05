"""Daily Primary reliability v1 acceptance scenarios; TEST_FIXTURE only."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from stockfish_core.api import create_app
from stockfish_core.service import ApplicationService
from stockfish_core.storage import SQLiteStore
from stockfish_core.yaogu.contracts import digest
from stockfish_core.yaogu.scheduler import CandidateScheduler
from tests.yaogu.fixtures import make_bundle

TZ = ZoneInfo("Asia/Shanghai")


def _wait_job(core, job_id, *, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = core.jobs.get(job_id)
        if job["state"] in {"SUCCEEDED", "FAILED", "CANCELLED", "INTERRUPTED"}:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job did not finish: {job_id}")


class _MutableClock:
    def __init__(self, current):
        self.current = current

    def __call__(self):
        return self.current


def _core(tmp_path, monkeypatch, bundle, *, now=None):
    import stockfish_core.yaogu.candidate_service as module

    cutoff = make_bundle(1, session_count=260)[1].replace(
        year=bundle.observation_date.year,
        month=bundle.observation_date.month,
        day=bundle.observation_date.day,
    )
    clock = now or cutoff
    monkeypatch.setattr(module, "utc_now", clock if callable(clock) else lambda: clock)
    core = ApplicationService(
        root=tmp_path / "state",
        store=SQLiteStore(tmp_path / "service.sqlite3"),
        allow_test_fixture=True,
        data_dir=tmp_path / "no_model",
    )
    input_id = core.candidates.register_input(bundle, activate=False)
    return core, input_id, cutoff


def _empty_core(tmp_path, monkeypatch, *, now):
    import stockfish_core.yaogu.candidate_service as module

    monkeypatch.setattr(module, "utc_now", now if callable(now) else lambda: now)
    return ApplicationService(
        root=tmp_path / "state",
        store=SQLiteStore(tmp_path / "service.sqlite3"),
        allow_test_fixture=True,
        data_dir=tmp_path / "no_model",
    )


def _attempt(core, job_id):
    return core.store.get_document("daily_primary_attempts", "daily_primary_attempt_" + digest({"job_id": job_id}))


def _without_bars(bundle, predicate):
    return bundle.model_copy(update={"bars": tuple(bar for bar in bundle.bars if not predicate(bar))})


def _with_liquid_amount(bundle, instrument_ids):
    wanted = set(instrument_ids)
    return bundle.model_copy(update={
        "bars": tuple(
            bar.model_copy(update={"amount_cny": Decimal("600000000")})
            if bar.instrument_id in wanted else bar
            for bar in bundle.bars
        )
    })


def _shift_clocked(obj, delta, extra=None):
    updates = {name: getattr(obj, name) + delta
               for name in ("event_time", "source_published_at", "first_seen_at", "ingested_at")}
    if extra:
        updates.update(extra)
    return obj.model_copy(update=updates)


def _shift_optional_day(value, delta):
    return value + delta if value is not None else None


def _bundle_ending_on(bundle, observation_date):
    delta = observation_date - bundle.observation_date
    securities = tuple(_shift_clocked(sec, delta, {
        "listed_on": sec.listed_on + delta,
        "effective_from": sec.effective_from + delta,
        "effective_to": _shift_optional_day(sec.effective_to, delta),
        "delisted_on": _shift_optional_day(sec.delisted_on, delta),
    }) for sec in bundle.universe.securities)
    bars = tuple(_shift_clocked(bar, delta, {"session": bar.session + delta}) for bar in bundle.bars)
    themes = tuple(_shift_clocked(theme, delta, {
        "effective_from": theme.effective_from + delta,
        "effective_to": _shift_optional_day(theme.effective_to, delta),
    }) for theme in bundle.themes)
    receipts = tuple(_shift_clocked(receipt, delta, {
        "business_sessions": tuple(day + delta for day in receipt.business_sessions),
    }) for receipt in bundle.receipts)
    universe = _shift_clocked(bundle.universe, delta, {"securities": securities})
    return bundle.model_copy(update={
        "observation_date": observation_date,
        "sessions": tuple(day + delta for day in bundle.sessions),
        "universe": universe,
        "bars": bars,
        "themes": themes,
        "receipts": receipts,
    })


def _capture_visible_at(bundle, visible_at):
    def visible(obj):
        return obj.model_copy(update={"first_seen_at": visible_at, "ingested_at": visible_at})

    return bundle.model_copy(update={
        "universe": visible(bundle.universe).model_copy(update={
            "securities": tuple(visible(sec) for sec in bundle.universe.securities),
        }),
        "bars": tuple(visible(bar) for bar in bundle.bars),
        "themes": tuple(visible(theme) for theme in bundle.themes),
        "receipts": tuple(visible(receipt) for receipt in bundle.receipts),
    })


def _scenario_evidence(name, *, core, accepted=None, job=None, attempt=None, trace=None,
                       export_response=None, extra=None):
    evidence = {
        "scenario": name,
        "evidence_label": "SYNTHETIC TEST",
        "source_identity": (trace.snapshot().get("source_identity") if trace is not None else None),
        "job": {
            "job_id": (job or {}).get("job_id"),
            "state": (job or {}).get("state"),
            "result": (job or {}).get("result"),
            "error": (job or {}).get("error"),
        },
        "http": {
            "accepted_status_code": getattr(accepted, "status_code", None),
            "export_status_code": getattr(export_response, "status_code", None),
            "export_bytes": len(export_response.data) if export_response is not None else None,
        },
        "attempt": attempt,
        "trace": trace.snapshot() if trace is not None else None,
        "strategy_run_count": len(core.store.list_documents("strategy_experiment_runs", limit=1000)),
        "extra": extra or {},
    }
    root = os.environ.get("STOCKFISH_DAILY_PRIMARY_RELIABILITY_EVIDENCE_DIR")
    if root:
        path = Path(root)
        path.mkdir(parents=True, exist_ok=True)
        (path / f"{name}.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return evidence


def _run_daily_primary(core, input_id=None, body=None, *, job_wait_timeout=20):
    client = create_app(core).test_client()
    payload = {"input_id": input_id} if body is None and input_id is not None else (body or {})
    accepted = client.post("/api/daily-primary/run", json=payload)
    assert accepted.status_code == 202, accepted.text
    trace = core.candidates.daily_traces.get(accepted.headers["X-Daily-Trace-Id"])
    job = _wait_job(core, accepted.json["job_id"], timeout=job_wait_timeout)
    attempt = _attempt(core, job["job_id"])
    export_response = None
    if job["state"] == "SUCCEEDED" and (job.get("result") or {}).get("run_id"):
        export_response = client.get("/api/strategy-experiments/" + job["result"]["run_id"] + "/export")
    return accepted, job, attempt, trace, export_response


def test_scenario_a_complete_required_data_becomes_ready(tmp_path, monkeypatch):
    bundle, _ = make_bundle(7, session_count=260)
    core, input_id, _ = _core(tmp_path, monkeypatch, bundle)
    try:
        accepted, job, attempt, trace, export_response = _run_daily_primary(core, input_id=input_id)
        assert job["state"] == "SUCCEEDED", job
        assert attempt["final_status"] == "READY"
        assert attempt["strategy_started"] is True
        assert attempt["strategy_completed"] is True
        assert attempt["persistence_completed"] is True
        assert attempt["export_completed"] is True
        assert attempt["recommendation_published"] is True
        assert export_response.status_code == 200
        _scenario_evidence("scenario_a_complete_required_data", core=core, accepted=accepted,
                           job=job, attempt=attempt, trace=trace, export_response=export_response)
    finally:
        core.close()


def test_scenario_b_initially_missing_refresh_succeeds(tmp_path, monkeypatch):
    ready, _ = make_bundle(7, session_count=260, start_date=date(2026, 1, 1))
    bad = _without_bars(ready, lambda bar: bar.session == ready.observation_date)
    core, _bad_input_id, _ = _core(tmp_path, monkeypatch, bad)
    try:
        def refresh(**_kwargs):
            input_id = core.candidates.register_input(ready, activate=False)
            return {"status": "COMPLETE", "input_id": input_id,
                    "observation_date": str(ready.observation_date), "coverage_type": ready.coverage_type}

        monkeypatch.setattr(core.candidates, "_daily_primary_incremental_refresh", refresh)
        accepted, job, attempt, trace, export_response = _run_daily_primary(core, body={})
        evaluations = attempt["readiness_evaluations"]
        assert job["state"] == "SUCCEEDED", job
        assert attempt["final_status"] == "READY"
        assert evaluations[0]["readiness"]["global_required_ready"] is False
        assert evaluations[-1]["readiness"]["global_required_ready"] is True
        assert attempt["refresh_actions"][0]["status"] == "COMPLETE"
        assert attempt["strategy_started"] is True
        assert export_response.status_code == 200
        _scenario_evidence("scenario_b_refresh_succeeds", core=core, accepted=accepted,
                           job=job, attempt=attempt, trace=trace, export_response=export_response)
    finally:
        core.close()


def test_scenario_b2_real_refresh_implementation_registers_expected_session(tmp_path, monkeypatch):
    request_started = datetime(2026, 9, 21, 10, 0, tzinfo=TZ)
    expected = date(2026, 9, 18)
    ready, _ = make_bundle(7, session_count=260)
    ready = _bundle_ending_on(ready, expected)
    clock = _MutableClock(request_started)
    core = _empty_core(tmp_path, monkeypatch, now=clock)
    try:
        scheduler = CandidateScheduler(core.candidates, capture=lambda *_args: ready, clock=clock)
        accepted, job, attempt, trace, export_response = _run_daily_primary(core, body={})
        action = attempt["refresh_actions"][0]
        evaluations = attempt["readiness_evaluations"]

        assert scheduler.custom_capture is True
        assert job["state"] == "SUCCEEDED", job
        assert attempt["final_status"] == "READY"
        assert evaluations[0]["readiness"]["global_required_ready"] is False
        assert evaluations[-1]["freshness"]["freshness_status"] == "FRESH"
        assert action["status"] == "COMPLETE"
        assert action["target_observation_date"] == str(expected)
        assert action["observation_date"] == str(expected)
        assert action["refresh_implementation_path"].startswith("CandidateService._daily_primary_incremental_refresh")
        assert action["scheduler_capture_invoked"] is True
        assert attempt["input_id"] == action["input_id"]
        assert attempt["expected_observation_session"] == str(expected)
        assert attempt["strategy_started"] is True
        assert export_response.status_code == 200
        _scenario_evidence("scenario_b2_real_refresh_implementation", core=core, accepted=accepted,
                           job=job, attempt=attempt, trace=trace, export_response=export_response,
                           extra={"refresh_action": action})
    finally:
        core.close()


def test_scenario_c_required_data_never_ready_does_not_start_strategy(tmp_path, monkeypatch):
    ready, _ = make_bundle(7, session_count=260, start_date=date(2026, 1, 1))
    bad = _without_bars(ready, lambda bar: bar.session == ready.observation_date)
    core, _bad_input_id, _ = _core(tmp_path, monkeypatch, bad)
    calls = {"strategy": 0}
    try:
        monkeypatch.setattr(core.candidates, "_daily_primary_incremental_refresh",
                            lambda **_kwargs: {"status": "SKIPPED", "reason": "TEST_FIXTURE_STILL_MISSING"})

        def forbidden(*_args, **_kwargs):
            calls["strategy"] += 1
            raise AssertionError("strategy must not run when global readiness fails")

        monkeypatch.setattr(core.candidates, "_build_daily_primary_strategy_result", forbidden)
        accepted, job, attempt, trace, export_response = _run_daily_primary(core, body={})
        assert job["state"] == "SUCCEEDED", job
        assert attempt["final_status"] == "NOT_READY"
        assert attempt["strategy_started"] is False
        assert attempt["strategy_invocation_count"] == 0
        assert attempt["recommendation_published"] is False
        assert calls["strategy"] == 0
        assert not core.store.list_documents("strategy_experiment_runs")
        _scenario_evidence("scenario_c_never_ready", core=core, accepted=accepted,
                           job=job, attempt=attempt, trace=trace, export_response=export_response,
                           extra={"strategy_invocation_count_observed": calls["strategy"]})
    finally:
        core.close()


def test_scenario_g_stale_but_complete_input_refreshes_expected_session(tmp_path, monkeypatch):
    request_started = datetime(2026, 9, 21, 10, 0, tzinfo=TZ)
    stale_day = date(2026, 9, 17)
    expected = date(2026, 9, 18)
    ready, _ = make_bundle(7, session_count=260)
    stale = _bundle_ending_on(ready, stale_day)
    fresh = _bundle_ending_on(ready, expected)
    clock = _MutableClock(request_started)
    core, stale_input_id, _ = _core(tmp_path, monkeypatch, stale, now=clock)
    try:
        CandidateScheduler(core.candidates, capture=lambda *_args: fresh, clock=clock)
        accepted, job, attempt, trace, export_response = _run_daily_primary(core, body={})
        initial = attempt["readiness_evaluations"][0]
        action = attempt["refresh_actions"][0]

        assert job["state"] == "SUCCEEDED", job
        assert initial["readiness"]["freshness"]["freshness_status"] == "STALE_INPUT"
        assert "OBSERVATION_SESSION_STALE" in [
            row["code"] for row in initial["readiness"]["blocking_requirements"]
        ]
        assert initial["readiness"]["global_required_ready"] is False
        assert action["target_observation_date"] == str(expected)
        assert action["target_observation_date"] != str(stale_day)
        assert attempt["input_id"] != stale_input_id
        assert attempt["observation_date"] == str(expected)
        assert attempt["freshness"]["freshness_status"] == "FRESH"
        assert attempt["final_status"] == "READY"
        assert attempt["strategy_started"] is True
        assert export_response.status_code == 200
        _scenario_evidence("scenario_g_stale_but_complete", core=core, accepted=accepted,
                           job=job, attempt=attempt, trace=trace, export_response=export_response,
                           extra={"initial_input_id": stale_input_id, "refresh_action": action})
    finally:
        core.close()


def test_scheduled_evening_rejects_stale_complete_input(tmp_path, monkeypatch):
    stale_day = date(2026, 9, 17)
    expected = date(2026, 9, 18)
    request_started = datetime(2026, 9, 21, 20, 30, tzinfo=TZ)
    ready, _ = make_bundle(7, session_count=260)
    stale = _bundle_ending_on(ready, stale_day)
    core, input_id, _ = _core(tmp_path, monkeypatch, stale, now=request_started)
    try:
        config = core.candidates.current_config()
        job = core.jobs.submit("candidate_run", {
            "input_id": input_id,
            "config_hash": config.config_hash,
            "profile": "DAILY_V1",
            "decision_cutoff": request_started.isoformat(),
            "market_session": "EVENING",
            "parent_run_id": None,
            "revision": 1,
            "expected_observation_session": str(expected),
        }, job_id="schedule_stale_evening_fixture")
        done = _wait_job(core, job["job_id"])
        attempt = _attempt(core, job["job_id"])

        assert done["state"] == "SUCCEEDED", done
        assert done["result"]["daily_primary_final_status"] == "NOT_READY"
        assert "stage_aware_primary_run_id" not in done["result"]
        assert attempt["final_status"] == "NOT_READY"
        assert attempt["daily_primary_mode"] == "SCHEDULED_EVENING"
        assert attempt["strategy_started"] is False
        assert attempt["recommendation_published"] is False
        assert "OBSERVATION_SESSION_STALE" in [
            row["code"] for row in attempt["blocking_requirements"]
        ]
        _scenario_evidence("scheduled_evening_stale_complete_barrier", core=core,
                           job=done, attempt=attempt,
                           extra={"input_id": input_id, "expected_observation_session": str(expected)})
    finally:
        core.close()


def test_scenario_h_refresh_visibility_sets_actual_decision_cutoff(tmp_path, monkeypatch):
    request_started = datetime(2026, 9, 21, 10, 0, tzinfo=TZ)
    visible_at = datetime(2026, 9, 21, 10, 7, tzinfo=TZ)
    expected = date(2026, 9, 18)
    ready, _ = make_bundle(7, session_count=260)
    ready = _capture_visible_at(_bundle_ending_on(ready, expected), visible_at)
    clock = _MutableClock(request_started)
    core = _empty_core(tmp_path, monkeypatch, now=clock)

    def delayed_capture(*_args):
        clock.current = visible_at
        return ready

    try:
        CandidateScheduler(core.candidates, capture=delayed_capture, clock=clock)
        accepted, job, attempt, trace, export_response = _run_daily_primary(core, body={})
        action = attempt["refresh_actions"][0]
        actual = datetime.fromisoformat(attempt["actual_decision_cutoff"])
        visible = datetime.fromisoformat(action["refreshed_data_visible_at"])

        assert job["state"] == "SUCCEEDED", job
        assert attempt["request_started_at"] == request_started.isoformat()
        assert attempt["actual_decision_cutoff"] != attempt["request_started_at"]
        assert action["capture_started_at"] == request_started.isoformat()
        assert action["capture_completed_at"] == visible_at.isoformat()
        assert actual >= visible
        assert actual == visible_at
        run = core.candidates.strategy_experiment(job["result"]["run_id"])
        assert run["decision_cutoff"] == visible_at.isoformat()
        assert run["prospective_validation"]["actual_decision_cutoff"] == visible_at.isoformat()
        assert export_response.status_code == 200
        _scenario_evidence("scenario_h_refresh_cutoff_visibility", core=core, accepted=accepted,
                           job=job, attempt=attempt, trace=trace, export_response=export_response,
                           extra={"refresh_action": action, "strategy_decision_cutoff": run["decision_cutoff"]})
    finally:
        core.close()


def test_scenario_d_subset_incomplete_becomes_unknown_not_rejected(tmp_path, monkeypatch):
    bundle, _ = make_bundle(60, session_count=260)
    missing_id = "TEST005"
    bundle = _with_liquid_amount(bundle, {missing_id})
    cutoff_start = bundle.sessions[-250]
    changed = _without_bars(bundle, lambda bar: bar.instrument_id == missing_id and bar.session <= cutoff_start)
    core, input_id, _ = _core(tmp_path, monkeypatch, changed)
    try:
        # This 60/100-security fixture checks typed correctness, not a 20s SLO.
        # Independent complete-job measurements were 39.18s / 69.71s; the
        # original 20s failures are retained in the delivery evidence.
        accepted, job, attempt, trace, export_response = _run_daily_primary(
            core, input_id=input_id, job_wait_timeout=180)
        assert job["state"] == "SUCCEEDED", job
        assert attempt["final_status"] == "READY"
        run = core.candidates.strategy_experiment(job["result"]["run_id"])
        row = next(item for item in run["results"]["STAGE_AWARE_MOMENTUM_V2"]["items"] if item["instrument_id"] == missing_id)
        row = core.candidates._expand_strategy_row_detail(row)
        assert row["decision_status"] == "UNKNOWN"
        assert row["decision_status"] != "REJECTED"
        assert any("TREND" in code or "MA" in code for code in row["reason_codes"])
        _scenario_evidence("scenario_d_subset_incomplete_unknown", core=core, accepted=accepted,
                           job=job, attempt=attempt, trace=trace, export_response=export_response,
                           extra={"instrument_id": missing_id, "decision_status": row["decision_status"],
                                  "reason_codes": row["reason_codes"]})
    finally:
        core.close()


def test_scenario_e_none_bar_and_incomplete_window_are_typed_not_crashes(tmp_path, monkeypatch):
    bundle, _ = make_bundle(100, session_count=260)
    missing_current = "TEST000"
    missing_window = "TEST005"
    bundle = _with_liquid_amount(bundle, {missing_current, missing_window})
    cutoff_start = bundle.sessions[-250]
    changed = _without_bars(bundle, lambda bar: (
        (bar.instrument_id == missing_current and bar.session == bundle.observation_date)
        or (bar.instrument_id == missing_window and bar.session <= cutoff_start)
    ))
    core, input_id, _ = _core(tmp_path, monkeypatch, changed)
    try:
        # This 60/100-security fixture checks typed correctness, not a 20s SLO.
        # Independent complete-job measurements were 39.18s / 69.71s; the
        # original 20s failures are retained in the delivery evidence.
        accepted, job, attempt, trace, export_response = _run_daily_primary(
            core, input_id=input_id, job_wait_timeout=180)
        assert job["state"] == "SUCCEEDED", job
        assert attempt["final_status"] == "READY"
        sample = {
            row["instrument_id"]: row
            for row in attempt["readiness"]["instrument_required_incomplete_sample"]
            if row["instrument_id"] in {missing_current, missing_window}
        }
        assert "MISSING_BAR" in sample[missing_current]["typed_reason_codes"]
        assert "INCOMPLETE_WINDOW" in sample[missing_window]["typed_reason_codes"]
        run = core.candidates.strategy_experiment(job["result"]["run_id"])
        rows = {item["instrument_id"]: core.candidates._expand_strategy_row_detail(item)
                for item in run["results"]["STAGE_AWARE_MOMENTUM_V2"]["items"]
                if item["instrument_id"] in {missing_current, missing_window}}
        assert rows[missing_current]["decision_status"] == "UNKNOWN"
        assert rows[missing_window]["decision_status"] == "UNKNOWN"
        assert all(row["decision_status"] != "REJECTED" for row in rows.values())
        _scenario_evidence("scenario_e_none_and_window_typed", core=core, accepted=accepted,
                           job=job, attempt=attempt, trace=trace, export_response=export_response,
                           extra={"diagnostics": sample})
    finally:
        core.close()


def test_typed_diagnostics_preserve_amount_turnover_and_adjustment_names(tmp_path, monkeypatch):
    bundle, _ = make_bundle(12, session_count=260)
    target = "TEST006"
    current = bundle.observation_date
    window = set(bundle.sessions[-6:])
    changed = bundle.model_copy(update={
        "bars": tuple(
            bar.model_copy(update={
                "amount_cny": None if bar.instrument_id == target and bar.session in window else bar.amount_cny,
                "turnover_fraction": None if bar.instrument_id == target and bar.session == current else bar.turnover_fraction,
            })
            for bar in bundle.bars
        )
    })
    core, input_id, _ = _core(tmp_path, monkeypatch, changed)
    try:
        accepted, job, attempt, trace, export_response = _run_daily_primary(core, input_id=input_id)
        assert job["state"] == "SUCCEEDED", job
        optional = {
            row["instrument_id"]: row
            for row in attempt["readiness"]["instrument_optional_evidence_gap_sample"]
        }
        typed = set(optional[target]["typed_reason_codes"])
        assert "MISSING_AMOUNT" in typed
        assert "MISSING_TURNOVER" in typed
        assert "MISSING_ADJUSTMENT_FACTOR" in typed
        assert "MISSING_FACTOR" not in typed
        assert optional[target]["requiredness"] == "OPTIONAL"
        _scenario_evidence("typed_diagnostics_preserve_specific_reasons", core=core, accepted=accepted,
                           job=job, attempt=attempt, trace=trace, export_response=export_response,
                           extra={"instrument_id": target, "typed_reason_codes": sorted(typed)})
    finally:
        core.close()


def test_scenario_f_internal_failure_after_readiness_is_failed_not_not_ready(tmp_path, monkeypatch):
    bundle, _ = make_bundle(7, session_count=260)
    core, input_id, _ = _core(tmp_path, monkeypatch, bundle)
    try:
        def fail_export(_result):
            raise RuntimeError("TEST_FIXTURE_EXPORT_FAILURE")

        monkeypatch.setattr(core.candidates, "_daily_primary_export_validation", fail_export)
        accepted, job, attempt, trace, export_response = _run_daily_primary(core, input_id=input_id)
        assert job["state"] == "FAILED"
        assert attempt["final_status"] == "FAILED"
        assert attempt["readiness_passed"] is True
        assert attempt["strategy_started"] is True
        assert attempt["recommendation_published"] is False
        assert not core.store.list_documents("strategy_experiment_runs")
        assert job["error"]["code"] == "DAILY_PRIMARY_INTERNAL_FAILURE"
        _scenario_evidence("scenario_f_internal_failure", core=core, accepted=accepted,
                           job=job, attempt=attempt, trace=trace, export_response=export_response)
    finally:
        core.close()
