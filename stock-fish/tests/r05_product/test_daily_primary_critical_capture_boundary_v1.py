from __future__ import annotations

from copy import deepcopy
from datetime import date
import json
import time
import pytest
from pathlib import Path

from tests.support.local_service import ResearchService
from stockfish_core.storage import SQLiteStore
from stockfish_core.yaogu.contracts import MarketInput, digest
from stockfish_core.yaogu.data_pipeline import (
    CAPTURE_PURPOSE_BACKGROUND_RESEARCH,
    CAPTURE_PURPOSE_DAILY_PRIMARY_CRITICAL,
    MainboardPipeline,
)
from stockfish_core.yaogu.daily_primary_fast_decision import evaluate_fast_decision_sufficiency
from stockfish_core.yaogu.request_queue import RequestBudget, RequestQueue, logical_identity
from tests.yaogu.fixtures import make_bundle


def _wait_job(core, job_id, *, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = core.jobs.get(job_id)
        if job["state"] in {"SUCCEEDED", "FAILED", "CANCELLED", "INTERRUPTED"}:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job did not finish: {job_id}")


def _run_daily_primary(core, input_id):
    accepted = core.candidates.submit_daily_primary_strategy(input_id=input_id)
    job = _wait_job(core, accepted["job_id"])
    attempt = core.store.get_document(
        "daily_primary_attempts",
        "daily_primary_attempt_" + digest({"job_id": job["job_id"]}),
    )
    return job, attempt


def _service(tmp_path, monkeypatch, *, now):
    import stockfish_core.yaogu.candidate_service as module

    monkeypatch.setattr(module, "utc_now", lambda: now)
    return ResearchService(
        root=tmp_path / "state",
        store=SQLiteStore(tmp_path / "service.sqlite3"),
        allow_test_fixture=True,
        data_dir=tmp_path / "no_model",
    )


def _market_fixture(count=12, *, session_count=260):
    bundle, cutoff = make_bundle(count, session_count=session_count, start_date=date(2025, 9, 22))
    mapping = {security.instrument_id: f"{600000 + i:06d}.SH"
               for i, security in enumerate(bundle.universe.securities)}

    def convert(value):
        if isinstance(value, dict):
            return {key: convert(item) for key, item in value.items()}
        if isinstance(value, list):
            return [convert(item) for item in value]
        if isinstance(value, str):
            return mapping.get(value, value)
        return value

    return MarketInput.model_validate(convert(bundle.model_dump(mode="json"))), cutoff


def _provider_source(bundle, calls):
    def executor(endpoint, params, root, timeout):
        calls.append((endpoint, deepcopy(params)))
        rows = []
        if endpoint == "mainboard_sse":
            rows = [
                {"证券代码": security.instrument_id[:6], "证券简称": security.name, "上市日期": str(security.listed_on)}
                for security in bundle.universe.securities
                if security.exchange == "SSE"
            ]
        elif endpoint == "master_szse":
            rows = [{"A股代码": "300001", "板块": "创业板", "A股上市日期": "2010-01-01"}]
        elif endpoint == "calendar":
            rows = [{"calendar_date": str(session), "is_trading_day": "1"} for session in bundle.sessions]
        elif endpoint == "market_daily":
            rows = [
                bar.model_dump(mode="json")
                for bar in bundle.bars
                if str(bar.session) == params["date"]
            ]
        elif endpoint == "history":
            wanted = set(params['codes'])
            rows = [bar.model_dump(mode='json') for bar in bundle.bars
                    if bar.instrument_id in wanted
                    and params['start_date'] <= str(bar.session) <= params['end_date']]
        elif endpoint == "master_all":
            rows = [
                {"code": security.instrument_id, "code_name": security.name, "tradeStatus": "1"}
                for security in bundle.universe.securities
            ]
        elif endpoint == "stock_basic_all":
            rows = [
                {"code": security.instrument_id, "ipoDate": str(security.listed_on), "outDate": "",
                 "type": "1", "status": "1", "code_name": security.name}
                for security in bundle.universe.securities
            ]
        elif endpoint == "stock_basic":
            wanted = set(params.get("codes") or [])
            rows = [
                {"code": security.instrument_id, "ipoDate": str(security.listed_on), "outDate": "",
                 "type": "1", "status": "1", "code_name": security.name}
                for security in bundle.universe.securities
                if security.instrument_id in wanted
            ]
        receipt = endpoint + digest(params)[:16]
        return {
            "status": "COMPLETE",
            "endpoint": endpoint,
            "parameters": params,
            "rows": rows,
            "request_receipt_id": receipt,
            "pagination_complete": True,
            "receipts": [{"receipt_id": receipt, "status": "COMPLETE", "pagination_complete": True}],
        }

    return executor


def _normalizer(base):
    def normalize(results, **_kwargs):
        rows = {}
        for result in results:
            if result.get("endpoint") in {"market_daily", "history"}:
                for row in result["rows"]:
                    rows[(row["instrument_id"], row["session"])] = row
        value = base.model_dump(mode="json")
        value["bars"] = list(rows.values())
        return MarketInput.model_validate(value), []

    return normalize


def _seed_published_outcome_backlog(queue_root: Path, count=10000):
    with RequestQueue(queue_root, budget=RequestBudget(max_requests=0)) as queue:
        rows = []
        aliases = []
        for index in range(count):
            parameters = {
                "codes": [f"BG{index:06d}.SH"],
                "start_date": "2026-01-01",
                "end_date": "2026-01-02",
            }
            identity = logical_identity(
                "history",
                parameters,
                "TEST_FIXTURE_V1",
                provider="TEST_FIXTURE",
                session="2026-01-02",
            )
            payload = {
                **identity,
                "stage": "PublishedCandidateOutcomeCapture",
                "stages": ["PublishedCandidateOutcomeCapture"],
                "purpose": CAPTURE_PURPOSE_BACKGROUND_RESEARCH,
                "purposes": [CAPTURE_PURPOSE_BACKGROUND_RESEARCH],
                "priority": 2,
                "dependencies": [],
            }
            request_id = f"background_outcome_{index:06d}"
            rows.append((request_id, json.dumps(payload, sort_keys=True), "PENDING", queue.timestamp()))
            aliases.append(("request_" + digest(identity), request_id))
        queue.connection.executemany(
            "INSERT INTO requests(request_id,payload,state,updated_at) VALUES(?,?,?,?)",
            rows,
        )
        queue.connection.executemany(
            "INSERT INTO logical_requests(logical_id,request_id) VALUES(?,?)",
            aliases,
        )
        queue.connection.commit()


def test_critical_capture_complete_background_outcome_backlog_does_not_block_ready(tmp_path, monkeypatch):
    bundle, cutoff = _market_fixture(12, session_count=260)
    calls = []
    cache_root = tmp_path / "provider_cache"
    _seed_published_outcome_backlog(cache_root, 10000)
    pipeline = MainboardPipeline(
        cache_root,
        executor=_provider_source(bundle, calls),
        normalizer=_normalizer(bundle),
        budget=RequestBudget(max_requests=1000, max_seconds=60, minimum_interval_seconds=0),
    )

    captured = pipeline.capture(
        tmp_path / "critical_capture",
        cutoff,
        observation_date=bundle.observation_date,
        purpose=CAPTURE_PURPOSE_DAILY_PRIMARY_CRITICAL,
    )

    forbidden_endpoints = {
        "limit_up_pool",
        "daily_limits_eastmoney",
        "concept_list",
        "concept_list_sina",
        "concept_members",
        "concept_members_sina",
        "lhb_summary",
        "money_flow",
    }
    endpoints = [endpoint for endpoint, _params in calls]
    assert not any(endpoint in forbidden_endpoints or endpoint.startswith("tushare_") for endpoint in endpoints)
    assert pipeline.last_report["critical_path_trace"]["critical_completed"] is True
    assert pipeline.last_report["critical_path_trace"]["background_pending"] >= 10000
    assert pipeline.last_report["daily_primary_readiness"]["global_required_ready"] is True

    core = _service(tmp_path / "app", monkeypatch, now=cutoff)
    try:
        input_id = core.candidates.register_input(captured, activate=False)
        job, attempt = _run_daily_primary(core, input_id)

        assert job["state"] == "SUCCEEDED", job
        assert attempt["final_status"] == "READY"
        assert attempt["strategy_started"] is True
        assert attempt["recommendation_published"] is True
    finally:
        core.close()


def test_critical_eligibility_unknown_remains_not_ready(tmp_path, monkeypatch):
    bundle, cutoff = make_bundle(7, session_count=260)
    securities = list(bundle.universe.securities)
    securities[0] = securities[0].model_copy(update={"st": None})
    bad = bundle.model_copy(update={
        "universe": bundle.universe.model_copy(update={"securities": tuple(securities)})
    })
    core = _service(tmp_path, monkeypatch, now=cutoff)
    try:
        input_id = core.candidates.register_input(bad, activate=False)
        job, attempt = _run_daily_primary(core, input_id)

        assert job["state"] == "SUCCEEDED", job
        assert attempt["final_status"] == "NOT_READY"
        assert attempt["strategy_started"] is False
        assert attempt["recommendation_published"] is False
        assert not core.store.list_documents("strategy_experiment_runs")
        assert "DATED_UNIVERSE_MASTER_USABLE" in [
            row["code"] for row in attempt["blocking_requirements"]
        ]
        assert attempt["readiness"]["qualification"]["eligibility_unknown_records"][0]["reason"] == "UNKNOWN_HISTORICAL_STATUS"
    finally:
        core.close()


def _missing_current_price(bundle):
    return bundle.model_copy(update={
        "bars": tuple(bar for bar in bundle.bars if bar.session != bundle.observation_date)
    })


def _missing_comparable_basis(bundle):
    return bundle.model_copy(update={
        "bars": tuple(bar.model_copy(update={"comparable_close": None, "comparable_basis": None})
                      for bar in bundle.bars)
    })


def _missing_history_window(bundle):
    retained = set(bundle.sessions[-10:])
    return bundle.model_copy(update={
        "bars": tuple(bar for bar in bundle.bars if bar.session in retained)
    })


def _low_current_comparable(bundle, pass_count):
    current = bundle.observation_date
    ordered = sorted({bar.instrument_id for bar in bundle.bars if bar.session == current})
    allowed = set(ordered[:pass_count])
    return bundle.model_copy(update={
        "bars": tuple(
            bar if bar.session != current or bar.instrument_id in allowed
            else bar.model_copy(update={"comparable_close": None, "comparable_basis": None})
            for bar in bundle.bars
        )
    })


def test_fast_decision_waits_for_structure_history_before_comparable_not_ready(tmp_path, monkeypatch):
    bundle, cutoff = _market_fixture(12, session_count=260)
    low_comparable = _low_current_comparable(bundle, pass_count=4)
    calls = []
    core = _service(tmp_path / "app", monkeypatch, now=cutoff)
    try:
        pipeline = MainboardPipeline(
            tmp_path / "provider_cache",
            service=core.candidates,
            config=core.candidates.current_config(),
            executor=_provider_source(low_comparable, calls),
            normalizer=_normalizer(low_comparable),
            budget=RequestBudget(max_requests=1000, max_seconds=60, minimum_interval_seconds=0),
        )

        captured = pipeline.capture(
            tmp_path / "critical_fast_decision",
            cutoff,
            observation_date=low_comparable.observation_date,
            purpose=CAPTURE_PURPOSE_DAILY_PRIMARY_CRITICAL,
            # This boundary fixture must not inherit an operator-disabled mode.
            fast_decision=True,
        )
        report = pipeline.last_report
        decision = report["daily_primary_fast_decision"]

        assert decision["decision"] == "NOT_READY"
        assert decision["reason"] == "GLOBAL_REQUIRED_BLOCKER_CONCLUSIVE"
        assert decision["selected_blocker"]["code"] == "CURRENT_COMPARABLE_PRICE_COVERAGE"
        assert decision["queue"]["pending_structure_history"] is False
        assert report["critical_path_trace"]["critical_completed"] is False
        assert report["critical_path_trace"]["strategy_started"] is False
        assert not any("StructureHistoryCapture" in row["payload"] for row in report["remaining_requests"])
        assert sum(1 for endpoint, _params in calls if endpoint == "market_daily") >= 260

        input_id = core.candidates.register_input(captured, activate=False)
        job, attempt = _run_daily_primary(core, input_id)

        assert job["state"] == "SUCCEEDED", job
        assert attempt["final_status"] == "NOT_READY"
        assert attempt["strategy_started"] is False
        assert attempt["recommendation_published"] is False
        assert not core.store.list_documents("strategy_experiment_runs")
    finally:
        core.close()


def test_fast_decision_does_not_stop_on_comparable_while_structure_history_pending():
    readiness = {
        "global_required_ready": False,
        "gate_fraction": 0.98,
        "blocking_requirements": [{
            "code": "CURRENT_COMPARABLE_PRICE_COVERAGE",
            "scope": "GLOBAL",
            "requiredness": "REQUIRED",
            "status": "FAIL",
            "details": {
                "coverage": {
                    "ALL": {"numerator": 4, "denominator": 12},
                    "SSE": {"numerator": 4, "denominator": 12},
                    "SZSE": {"numerator": 0, "denominator": 0},
                }
            },
        }],
    }
    payload = {
        "endpoint": "market_daily",
        "parameters": {"date": "2026-09-01"},
        "stage": "StructureHistoryCapture",
        "stages": ["StructureHistoryCapture"],
        "purpose": CAPTURE_PURPOSE_DAILY_PRIMARY_CRITICAL,
        "purposes": [CAPTURE_PURPOSE_DAILY_PRIMARY_CRITICAL],
        "priority": 2,
    }

    decision = evaluate_fast_decision_sufficiency(
        readiness,
        [{"payload": json.dumps(payload), "state": "PENDING"}],
        checkpoint="TEST_PENDING_STRUCTURE",
    )

    assert decision["decision"] == "CONTINUE_REFRESH"
    assert decision["sufficient"] is False
    assert decision["reason"] == "MISSING_EVIDENCE_CAN_STILL_CHANGE_REQUIRED_GATE"


def test_fast_decision_continues_when_pending_factor_batch_can_still_reach_gate():
    readiness = {
        "global_required_ready": False,
        "gate_fraction": 0.98,
        "blocking_requirements": [{
            "code": "CURRENT_COMPARABLE_PRICE_COVERAGE",
            "scope": "GLOBAL",
            "requiredness": "REQUIRED",
            "status": "FAIL",
            "details": {
                "coverage": {
                    "ALL": {"numerator": 4, "denominator": 12},
                    "SSE": {"numerator": 4, "denominator": 12},
                    "SZSE": {"numerator": 0, "denominator": 0},
                }
            },
        }],
    }
    payload = {
        "endpoint": "adjustment_factors",
        "parameters": {"codes": [f"{600000 + index:06d}.SH" for index in range(12)]},
        "stage": "StructureHistoryCapture",
        "stages": ["StructureHistoryCapture"],
        "purpose": CAPTURE_PURPOSE_DAILY_PRIMARY_CRITICAL,
        "purposes": [CAPTURE_PURPOSE_DAILY_PRIMARY_CRITICAL],
        "priority": 1,
    }

    decision = evaluate_fast_decision_sufficiency(
        readiness,
        [{"payload": json.dumps(payload), "state": "PENDING"}],
        checkpoint="TEST_PENDING_FACTOR",
    )

    assert decision["decision"] == "CONTINUE_REFRESH"
    assert decision["sufficient"] is False
    assert decision["reason"] == "MISSING_EVIDENCE_CAN_STILL_CHANGE_REQUIRED_GATE"


def test_real_critical_dependency_missing_is_not_ready_with_typed_reason(tmp_path, monkeypatch):
    cases = [
        (_missing_current_price, "CURRENT_RAW_BAR_COVERAGE", "MISSING_BAR"),
        (_missing_comparable_basis, "CURRENT_COMPARABLE_PRICE_COVERAGE", "MISSING_COMPARABLE_CLOSE"),
        (_missing_history_window, "CORE_TREND_INPUT_COVERAGE", "INCOMPLETE_WINDOW"),
    ]
    for index, (mutate, blocker, typed_reason) in enumerate(cases):
        bundle, cutoff = make_bundle(7, session_count=260)
        core = _service(tmp_path / str(index), monkeypatch, now=cutoff)
        try:
            input_id = core.candidates.register_input(mutate(bundle), activate=False)
            job, attempt = _run_daily_primary(core, input_id)

            assert job["state"] == "SUCCEEDED", job
            assert attempt["final_status"] == "NOT_READY"
            assert attempt["strategy_started"] is False
            assert attempt["recommendation_published"] is False
            assert blocker in [row["code"] for row in attempt["blocking_requirements"]]
            typed = {
                reason
                for row in attempt["readiness"]["instrument_required_incomplete_sample"]
                for reason in row["typed_reason_codes"]
            }
            assert typed_reason in typed
        finally:
            core.close()


def test_per_security_bootstrap_preserves_full_pool_and_window(tmp_path):
    bundle, cutoff = _market_fixture(12, session_count=260)
    captured = []
    for mode in ('ALL_MARKET_DAILY', 'PER_SECURITY_RAW'):
        calls = []
        pipeline = MainboardPipeline(tmp_path / mode / 'cache', history_mode=mode,
            executor=_provider_source(bundle, calls), normalizer=_normalizer(bundle),
            budget=RequestBudget(max_requests=1000, max_seconds=60, minimum_interval_seconds=0))
        value = pipeline.capture(tmp_path / mode / 'capture', cutoff,
            observation_date=bundle.observation_date, purpose=CAPTURE_PURPOSE_DAILY_PRIMARY_CRITICAL)
        captured.append(value)
        if mode == 'PER_SECURITY_RAW':
            histories = [params for ep, params in calls if ep == 'history']
            wanted = {s.instrument_id for s in bundle.universe.securities if s.exchange == 'SSE'}
            assert {code for params in histories for code in params['codes']} == wanted
            assert all(len(params['codes']) <= 10 for params in histories)
            assert all(params['start_date'] == str(bundle.sessions[0]) for params in histories)
            assert all(params['end_date'] == str(bundle.observation_date) for params in histories)
            assert not any(ep == 'stock_basic_all' for ep, _ in calls)
            assert all(params['date'] == str(bundle.observation_date)
                       for ep, params in calls if ep == 'market_daily')
            assert pipeline.last_report['daily_primary_readiness']['global_required_ready']
    def bars(value):
        return {(b.instrument_id, b.session): b.model_dump(mode='json') for b in value.bars}
    assert bars(captured[0]) == bars(captured[1])


@pytest.mark.parametrize("ready", [True, False])
def test_disabled_early_decision_preserves_final_readiness(tmp_path, monkeypatch, ready):
    bundle, cutoff = _market_fixture(12, session_count=260)
    if not ready:
        bundle = _low_current_comparable(bundle, pass_count=4)
    monkeypatch.setenv("STOCKFISH_CAPTURE_FAST_DECISION", "false")
    early_calls = []
    def unexpected_early_evaluation(*args, **kwargs):
        early_calls.append(True)
        raise AssertionError("early decision must not run")
    monkeypatch.setattr("stockfish_core.yaogu.data_pipeline.evaluate_fast_decision_sufficiency", unexpected_early_evaluation)
    core = _service(tmp_path / "app", monkeypatch, now=cutoff)
    try:
        pipeline = MainboardPipeline(tmp_path / "cache", service=core.candidates,
            executor=_provider_source(bundle, []), normalizer=_normalizer(bundle),
            budget=RequestBudget(max_requests=1000, max_seconds=60, minimum_interval_seconds=0))
        captured = pipeline.capture(tmp_path / "output", cutoff,
            observation_date=bundle.observation_date, purpose=CAPTURE_PURPOSE_DAILY_PRIMARY_CRITICAL)
        report = pipeline.last_report
        assert not early_calls
        assert report["fast_decision_enabled"] is False
        assert report["daily_primary_fast_decision"]["reason"] == "FINAL_CRITICAL_CAPTURE_EVALUATION"
        assert report["daily_primary_readiness"]["global_required_ready"] is ready
        input_id = core.candidates.register_input(captured, activate=False)
        job, attempt = _run_daily_primary(core, input_id)
        assert job["state"] == "SUCCEEDED"
        assert attempt["final_status"] == ("READY" if ready else "NOT_READY")
        assert attempt["recommendation_published"] is ready
    finally:
        core.close()


@pytest.mark.parametrize("value", ["0", "invalid", ""])
def test_early_decision_invalid_configuration_rejected(tmp_path, monkeypatch, value):
    from datetime import datetime, timezone
    monkeypatch.setenv("STOCKFISH_CAPTURE_FAST_DECISION", value)
    pipeline = MainboardPipeline(tmp_path / "cache")
    with pytest.raises(ValueError, match="INVALID_CAPTURE_FAST_DECISION"):
        pipeline.capture(tmp_path / "output", datetime.now(timezone.utc))
    assert not (tmp_path / "output").exists()
