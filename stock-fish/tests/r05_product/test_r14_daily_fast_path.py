"""R14 daily fast path engineering tests; TEST_FIXTURE only."""
from __future__ import annotations

from pathlib import Path
import time

import pytest

from tests.yaogu.fixtures import make_bundle
from stockfish_core.service import ApplicationService
from stockfish_core.storage import SQLiteStore
from stockfish_core.api import create_app
from stockfish_core.yaogu.candidate_service import CandidateRequest
from stockfish_core.yaogu.contracts import digest
from stockfish_core.yaogu.derived_feature_store import (
    DerivedFeatureStore,
    dependency_plan_for_context,
)
from stockfish_core.yaogu.request_queue import RequestQueue
from stockfish_core.yaogu.strategy_experiments import (
    _prepare_context,
    run_active_daily_strategy,
    run_strategy_experiments,
)


def _store(tmp_path):
    return SQLiteStore(tmp_path / "r14.sqlite3")


def _stage_slice(run):
    stage = run["results"]["STAGE_AWARE_MOMENTUM_V2"]
    return {
        "counts": stage["counts"],
        "top_ids": stage["top_ids"],
        "items": stage["items"],
    }


def _wait_job(core, job_id, *, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = core.jobs.get(job_id)
        if state["state"] in {"SUCCEEDED", "FAILED"}:
            return state
        time.sleep(0.05)
    raise AssertionError(f"job did not finish: {job_id}")


def test_daily_fast_path_does_not_execute_research_arms(tmp_path, monkeypatch):
    import stockfish_core.yaogu.strategy_experiments as module
    import stockfish_core.yaogu.theme_capital_trend as theme_capital_module

    def forbidden(*_args, **_kwargs):
        raise AssertionError("research arm entered the daily primary path")

    for name in (
        "run_pv_observation",
        "run_pv_focus_preview",
        "run_dna_rerank",
        "run_theme_context",
    ):
        monkeypatch.setattr(module, name, forbidden)
    for name in (
        "run_theme_capital_trend",
        "run_theme_capital_trend_technical_only",
        "run_simple_trend_template",
    ):
        monkeypatch.setattr(theme_capital_module, name, forbidden)

    bundle, cutoff = make_bundle(7)
    result = run_active_daily_strategy(
        bundle,
        cutoff=cutoff,
        input_id="TEST_INPUT",
        derived_store=DerivedFeatureStore(_store(tmp_path)),
    )

    assert result["execution_profile"] == "DAILY_PRIMARY_FAST"
    assert set(result["results"]) == {"STAGE_AWARE_MOMENTUM_V2"}
    assert result["daily_fast_path"]["research_arms_executed"] == []
    assert result["daily_fast_path"]["score_in_critical_path"] is False


def test_daily_fast_path_preserves_stage_aware_business_result(tmp_path):
    bundle, cutoff = make_bundle(7)
    research = run_strategy_experiments(bundle, cutoff=cutoff)
    fast = run_active_daily_strategy(
        bundle,
        cutoff=cutoff,
        input_id="TEST_INPUT",
        derived_store=DerivedFeatureStore(_store(tmp_path)),
    )

    assert digest(_stage_slice(fast)) == digest(_stage_slice(research))
    assert fast["selection_results"]["STAGE_AWARE_MOMENTUM_V2_FOCUS"]["selected_ids"] == [
        row["instrument_id"]
        for row in fast["results"]["STAGE_AWARE_MOMENTUM_V2"]["items"]
        if row["decision_status"] == "FOCUS"
    ]


def test_derived_feature_cache_hits_and_invalidation_identity(tmp_path):
    bundle, cutoff = make_bundle(7)
    store = DerivedFeatureStore(_store(tmp_path))
    first = run_active_daily_strategy(bundle, cutoff=cutoff, input_id="TEST_INPUT", derived_store=store)
    second = run_active_daily_strategy(bundle, cutoff=cutoff, input_id="TEST_INPUT", derived_store=store)

    assert first["derived_feature_store"]["cache_misses"] > 0
    assert second["derived_feature_store"]["cache_misses"] == 0
    assert second["derived_feature_store"]["cache_hits"] == first["derived_feature_store"]["requested_feature_rows"]
    assert digest(_stage_slice(first)) == digest(_stage_slice(second))

    ctx = _prepare_context(bundle, cutoff)
    identity = first["derived_feature_store"]["identity"]
    sessions = first["derived_feature_store"]["target_sessions"]
    plan = dependency_plan_for_context(ctx, sessions, list(ctx.eligible_ids))
    cached = store.get_many(identity, list(ctx.eligible_ids), sessions, dependency_plan=plan)
    invalid_plan = {
        key: {
            family: {**dep, "dependency_fingerprint": "TEST_OTHER_SOURCE_DEPENDENCY"}
            for family, dep in value.items()
        }
        for key, value in plan.items()
    }
    invalid = store.get_many(identity, list(ctx.eligible_ids), sessions, dependency_plan=invalid_plan)

    assert len(cached) == first["derived_feature_store"]["requested_feature_rows"]
    assert invalid == {}


def test_cold_recompute_after_cache_clear_is_business_equivalent(tmp_path):
    bundle, cutoff = make_bundle(7)
    store = DerivedFeatureStore(_store(tmp_path))
    first = run_active_daily_strategy(bundle, cutoff=cutoff, input_id="TEST_INPUT", derived_store=store)
    store.clear_identity(first["derived_feature_store"]["identity"])
    rebuilt = run_active_daily_strategy(bundle, cutoff=cutoff, input_id="TEST_INPUT", derived_store=store)

    assert rebuilt["derived_feature_store"]["cache_misses"] == first["derived_feature_store"]["cache_misses"]
    assert digest(_stage_slice(first)) == digest(_stage_slice(rebuilt))


def _service(tmp_path, monkeypatch):
    import stockfish_core.yaogu.candidate_service as module

    bundle, cutoff = make_bundle(7)
    monkeypatch.setattr(module, "utc_now", lambda: cutoff)
    core = ApplicationService(
        root=tmp_path / "state",
        store=SQLiteStore(tmp_path / "service.sqlite3"),
        allow_test_fixture=True,
        data_dir=tmp_path / "no_model",
    )
    input_id = core.candidates.register_input(bundle, activate=False)
    return core, input_id, bundle, cutoff


def test_daily_primary_persists_compact_summary_and_expands_detail(tmp_path, monkeypatch):
    core, input_id, bundle, cutoff = _service(tmp_path, monkeypatch)
    try:
        result = core.candidates._build_daily_primary_strategy_result(
            input_id,
            bundle,
            cutoff,
            core.candidates.current_config(),
        )
        with core.store.transaction() as connection:
            core.candidates._write_strategy_result(connection, result, created_at=cutoff.isoformat())

        saved = core.store.get_document("strategy_experiment_runs", result["run_id"])
        rows = saved["results"]["STAGE_AWARE_MOMENTUM_V2"]["items"]
        assert rows and "detail_ref" in rows[0] and "conditions" not in rows[0]
        assert saved["daily_primary_persistence"]["detail_document_count"] == len(rows)

        original_get_document = core.store.get_document
        seen_namespaces = []

        def spy_get_document(namespace, document_id):
            seen_namespaces.append(namespace)
            if namespace == "strategy_experiment_runs":
                raise AssertionError("summary pagination must not read the full run document")
            return original_get_document(namespace, document_id)

        monkeypatch.setattr(core.store, "get_document", spy_get_document)
        summary = core.candidates.strategy_experiment_summary(
            result["run_id"],
            strategy_id="STAGE_AWARE_MOMENTUM_V2",
            page_size=3,
        )
        monkeypatch.setattr(core.store, "get_document", original_get_document)
        detail = core.candidates.strategy_experiment_item_detail(result["run_id"], rows[0]["instrument_id"])

        assert len(summary["items"]) == 3
        assert "strategy_experiment_runs" not in seen_namespaces
        assert "conditions" in detail["strategy_results"]["STAGE_AWARE_MOMENTUM_V2"][0]
    finally:
        core.close()


def test_daily_primary_api_runs_fast_path_and_research_api_keeps_full_suite(tmp_path, monkeypatch):
    core, input_id, _bundle, _cutoff = _service(tmp_path, monkeypatch)
    try:
        client = create_app(core).test_client()

        daily_accepted = client.post("/api/daily-primary/run", json={"input_id": input_id})
        assert daily_accepted.status_code == 202
        daily_job = _wait_job(core, daily_accepted.json["job_id"])
        assert daily_job["state"] == "SUCCEEDED", daily_job
        daily_run = core.store.get_document("strategy_experiment_runs", daily_job["result"]["run_id"])
        assert daily_run["execution_profile"] == "DAILY_PRIMARY_FAST"
        assert set(daily_run["results"]) == {"STAGE_AWARE_MOMENTUM_V2"}

        research_accepted = client.post("/api/strategy-experiments/run", json={"input_id": input_id})
        assert research_accepted.status_code == 202
        research_job = _wait_job(core, research_accepted.json["job_id"])
        assert research_job["state"] == "SUCCEEDED", research_job
        research_run = core.store.get_document("strategy_experiment_runs", research_job["result"]["run_id"])
        assert research_run["execution_profile"] == "RESEARCH_FULL"
        assert {"PV_OBSERVATION_V1", "DNA_RERANK_V1", "THEME_CONTEXT_V1", "STAGE_AWARE_MOMENTUM_V2"} <= set(
            research_run["results"]
        )
    finally:
        core.close()


def test_evening_candidate_job_uses_daily_primary_builder(tmp_path, monkeypatch):
    core, input_id, _bundle, cutoff = _service(tmp_path, monkeypatch)
    try:
        def forbidden(*_args, **_kwargs):
            raise AssertionError("full research suite entered evening candidate job")

        monkeypatch.setattr(core.candidates, "_build_strategy_experiment_result", forbidden)
        job = core.candidates.submit(
            CandidateRequest(input_id=input_id),
            market_session="EVENING",
            cutoff=cutoff,
            job_id="TEST_R14_EVENING",
        )

        state = _wait_job(core, job["job_id"])

        assert state["state"] == "SUCCEEDED", state.get("error")
        run_id = state["result"]["stage_aware_primary_run_id"]
        saved = core.store.get_document("strategy_experiment_runs", run_id)
        assert saved["execution_profile"] == "DAILY_PRIMARY_FAST"
        assert "PV_OBSERVATION_V1" not in saved["results"]
    finally:
        core.close()


def test_request_queue_retry_preserves_original_failure_provenance(tmp_path):
    with RequestQueue(tmp_path) as queue:
        rid = queue.enqueue(
            "fixture.history",
            {"codes": ["TEST000"]},
            stage="TEST_FIXTURE",
            definition_version="TEST_FIXTURE_V1",
        )
        queue.connection.execute(
            "UPDATE requests SET state='BLOCKED',failure_category='AUTH' WHERE request_id=?",
            (rid,),
        )
        queue.connection.commit()

        assert queue.retry_blocked([rid], reason="R14_OPERATOR_RETRY", actor="TEST_OPERATOR") == 1
        row = queue.rows([rid])[0]
        audit = queue.connection.execute(
            "SELECT * FROM request_retries WHERE request_id=?",
            (rid,),
        ).fetchone()

        assert row["state"] == "PENDING"
        assert row["failure_category"] == "R14_OPERATOR_RETRY"
        assert audit["original_failure_category"] == "AUTH"
        assert audit["retry_reason"] == "R14_OPERATOR_RETRY"
        assert audit["retry_actor"] == "TEST_OPERATOR"
