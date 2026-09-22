from __future__ import annotations

from copy import deepcopy
from datetime import date
import json
import time
from pathlib import Path

from stockfish_core.api import create_app
from stockfish_core.service import ApplicationService
from stockfish_core.storage import SQLiteStore
from stockfish_core.yaogu.contracts import MarketInput, digest
from stockfish_core.yaogu.data_pipeline import (
    CAPTURE_PURPOSE_BACKGROUND_RESEARCH,
    CAPTURE_PURPOSE_DAILY_PRIMARY_CRITICAL,
    MainboardPipeline,
)
from stockfish_core.yaogu.request_queue import RequestBudget, RequestQueue
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
    client = create_app(core).test_client()
    accepted = client.post("/api/daily-primary/run", json={"input_id": input_id})
    assert accepted.status_code == 202, accepted.text
    job = _wait_job(core, accepted.json["job_id"])
    attempt = core.store.get_document(
        "daily_primary_attempts",
        "daily_primary_attempt_" + digest({"job_id": job["job_id"]}),
    )
    return job, attempt


def _service(tmp_path, monkeypatch, *, now):
    import stockfish_core.yaogu.candidate_service as module

    monkeypatch.setattr(module, "utc_now", lambda: now)
    return ApplicationService(
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
            if result.get("endpoint") == "market_daily":
                for row in result["rows"]:
                    rows[(row["instrument_id"], row["session"])] = row
        value = base.model_dump(mode="json")
        value["bars"] = list(rows.values())
        return MarketInput.model_validate(value), []

    return normalize


def _seed_published_outcome_backlog(queue_root: Path, count=10000):
    with RequestQueue(queue_root, budget=RequestBudget(max_requests=0)) as queue:
        rows = []
        for index in range(count):
            payload = {
                "logical_identity_version": "PROVIDER_ENDPOINT_PARAMETERS_SESSION_V1",
                "endpoint": "history",
                "provider": "TEST_FIXTURE",
                "parameters": {
                    "codes": [f"BG{index:06d}.SH"],
                    "start_date": "2026-01-01",
                    "end_date": "2026-01-02",
                },
                "session": "2026-01-02",
                "definition_version": "TEST_FIXTURE_V1",
                "stage": "PublishedCandidateOutcomeCapture",
                "stages": ["PublishedCandidateOutcomeCapture"],
                "purpose": CAPTURE_PURPOSE_BACKGROUND_RESEARCH,
                "purposes": [CAPTURE_PURPOSE_BACKGROUND_RESEARCH],
                "priority": 2,
                "dependencies": [],
            }
            rows.append((f"background_outcome_{index:06d}", json.dumps(payload, sort_keys=True),
                         "PENDING", queue.timestamp()))
        queue.connection.executemany(
            "INSERT INTO requests(request_id,payload,state,updated_at) VALUES(?,?,?,?)",
            rows,
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
