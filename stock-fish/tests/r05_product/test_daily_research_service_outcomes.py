"""Service/API acceptance with isolated TEST_FIXTURE database and state only."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier, Lock

import pytest

from stockfish_core.api import create_app
from stockfish_core.yaogu.contracts import digest
from stockfish_core.yaogu.daily_research import recommendation_snapshot
from stockfish_core.yaogu.daily_research_outcomes import POLICY_ID
from tests.yaogu.fixtures import make_bundle
from tests.r05_product.test_daily_primary_reliability_v1 import (
    _MutableClock, _core, _run_daily_primary, _wait_job,
)
from tests.r05_product.test_daily_research_outcomes import case, edit_bar


STAGE = "STAGE_AWARE_MOMENTUM_V2"


def fingerprint(core):
    with core.store.transaction(write=False) as connection:
        docs = list(connection.execute("SELECT namespace,document_id,content_hash FROM documents ORDER BY namespace,document_id"))
        jobs = list(connection.execute("SELECT job_id,state,payload FROM jobs ORDER BY job_id"))
        events = list(connection.execute("SELECT job_id,state,recorded_at,detail FROM job_events ORDER BY event_id"))
    return digest([[tuple(row) for row in values] for values in (docs, jobs, events)])


def store_fixture_snapshot(core, snapshot, *, persist=True):
    """A frozen synthetic signal for consumer tests, not a modified strategy result."""
    snapshot = deepcopy(snapshot)
    run_id = snapshot["identity"]["run_id"]
    snapshot["snapshot_hash"] = digest({key: value for key, value in snapshot.items() if key != "snapshot_hash"})
    if persist:
        core.store.put_document("daily_research_snapshots", run_id, snapshot)
    core.store.put_document("strategy_experiment_summaries", run_id, {"run_id": run_id, "results": {STAGE: {}}, "coverage_type": "TEST_FIXTURE"})
    return snapshot


def isolated_consumer(tmp_path, monkeypatch, *, count=2):
    snapshot, bundle, cutoff = case(count)
    clock = _MutableClock(cutoff)
    core, input_id, _ = _core(tmp_path, monkeypatch, bundle, now=clock)
    # This explicit TEST_FIXTURE exchange calendar belongs to these synthetic
    # observations. It is not substituted for any production exchange calendar.
    monkeypatch.setattr(core.candidates, "_daily_research_sessions", lambda: list(bundle.sessions))
    snapshot = store_fixture_snapshot(core, snapshot)
    return core, input_id, snapshot, bundle, clock


def test_primary_transaction_saves_the_same_four_state_snapshot_and_pending(tmp_path, monkeypatch):
    bundle, _ = make_bundle(7, session_count=260)
    core, input_id, _ = _core(tmp_path, monkeypatch, bundle)
    try:
        _, job, attempt, _, response = _run_daily_primary(core, input_id=input_id)
        assert job["state"] == "SUCCEEDED", job
        assert attempt["final_status"] == "READY"
        assert response.status_code == 200
        run_id = job["result"]["run_id"]
        run = core.candidates.strategy_experiment(run_id)
        snapshot = core.store.get_document("daily_research_snapshots", run_id)
        pending = core.store.get_document("daily_research_outcome_pending", run_id)
        assert snapshot is not None and pending is not None
        expanded = [core.candidates._expand_strategy_row_detail(row) for row in run["results"][STAGE]["items"]]
        expected = recommendation_snapshot(run, expanded_items=expanded, attempt={"final_status": "READY"})
        assert snapshot == expected
        assert snapshot["source_run_hash"] == run["content_hash"]
        assert [(row["instrument_id"], row["decision_status"]) for row in snapshot["items"]] == [
            (row["instrument_id"], row["decision_status"]) for row in run["results"][STAGE]["items"]]
        assert pending["horizons"] == list(range(1, 21))
        assert pending["instrument_ids"] == [row["instrument_id"] for row in snapshot["items"] if row["decision_status"] in {"FOCUS", "WATCH"}]
    finally:
        core.close()


def test_snapshot_failure_rolls_back_entire_primary_publication(tmp_path, monkeypatch):
    bundle, _ = make_bundle(7, session_count=260)
    core, input_id, _ = _core(tmp_path, monkeypatch, bundle)
    original = core.candidates._write_daily_research_snapshot
    def fail_after_write(connection, run, expanded_items):
        original(connection, run, expanded_items)
        raise RuntimeError("TEST_FIXTURE_SNAPSHOT_ATOMIC_FAILURE")
    monkeypatch.setattr(core.candidates, "_write_daily_research_snapshot", fail_after_write)
    try:
        _, job, attempt, _, _ = _run_daily_primary(core, input_id=input_id)
        assert job["state"] == "FAILED", job
        assert attempt["recommendation_published"] is False
        for namespace in ("strategy_experiment_runs", "strategy_experiment_summaries", "daily_research_snapshots", "daily_research_outcome_pending"):
            assert core.store.list_documents(namespace) == []
    finally:
        core.close()


def test_worker_persists_twenty_rows_per_focus_watch_and_same_session_is_idempotent(tmp_path, monkeypatch):
    core, input_id, snapshot, _, clock = isolated_consumer(tmp_path, monkeypatch)
    run_id = snapshot["identity"]["run_id"]
    try:
        first = core.candidates.submit_daily_recommendation_outcomes(run_id, input_id)
        done = _wait_job(core, first["job_id"])
        assert done["state"] == "SUCCEEDED", done
        assert done["result"]["row_count"] == 40
        saved = core.store.list_documents("daily_research_outcomes", limit=100)
        assert len(saved) == 40
        assert {row["payload"]["decision_status"] for row in saved} == {"FOCUS", "WATCH"}
        assert {row["payload"]["offset"] for row in saved} == set(range(1, 21))
        assert all(row["payload"]["snapshot_hash"] == snapshot["snapshot_hash"] for row in saved)
        assert len(core.store.list_documents("daily_research_outcome_captures")) == 1
        before = fingerprint(core)
        clock.current += timedelta(minutes=1)
        repeated = core.candidates.submit_daily_recommendation_outcomes(run_id, input_id)
        assert repeated["job_id"] == first["job_id"]
        assert fingerprint(core) == before
        assert core.candidates.pending_daily_recommendation_outcomes() == []
    finally:
        core.close()


def test_api_reads_do_not_write_and_reject_client_computed_or_future_values(tmp_path, monkeypatch):
    core, input_id, snapshot, _, _ = isolated_consumer(tmp_path, monkeypatch)
    run_id = snapshot["identity"]["run_id"]
    client = create_app(core).test_client()
    path = f"/api/daily-research/runs/{run_id}"
    try:
        accepted = client.post(path + "/outcomes", json={"input_id": input_id})
        assert accepted.status_code == 202, accepted.text
        assert _wait_job(core, accepted.json["job_id"])["state"] == "SUCCEEDED"
        before = fingerprint(core)
        assert client.get(path + "/snapshot").json == snapshot
        outcome = client.get(path + "/outcomes")
        assert outcome.status_code == 200 and len(outcome.json["items"]) == 40
        assert client.get(path + "/outcomes?instrument_id=TEST000").status_code == 200
        assert len(client.get(path + "/outcomes?instrument_id=TEST000").json["items"]) == 20
        assert client.get("/api/daily-research/analysis").status_code == 200
        report = client.get("/api/daily-research/report", query_string={"run_id": run_id, "format": "markdown"})
        assert report.status_code == 200
        assert "FOCUS" in report.text and "WATCH" in report.text
        for forbidden in ("close_reference_change", "return", "evaluated_at", "cutoff", "trust", "path"):
            response = client.post(path + "/outcomes", json={"input_id": input_id, forbidden: "TEST_FIXTURE_FORBIDDEN"})
            assert response.status_code == 400, (forbidden, response.text)
        assert client.get(path + "/outcomes?return=100").status_code == 400
        assert client.get("/api/daily-research/analysis?return=100").status_code == 400
        assert fingerprint(core) == before
    finally:
        core.close()


def test_legacy_saved_run_projects_without_new_snapshot_or_current_identity(tmp_path, monkeypatch):
    snapshot, bundle, cutoff = case(1)
    core, _, _ = _core(tmp_path, monkeypatch, bundle, now=cutoff)
    monkeypatch.setattr(core.candidates, "_daily_research_sessions", lambda: list(bundle.sessions))
    run_id = "TEST_FIXTURE_LEGACY_RUN"
    item = snapshot["items"][0]
    run = {"run_id": run_id, "observation_date": snapshot["identity"]["observation_date"],
           "generated_at": snapshot["identity"]["generated_at"], "coverage_type": "TEST_FIXTURE",
           "decision_cutoff": snapshot["identity"]["data_cutoff"],
           "results": {STAGE: {"strategy_id": STAGE, "items": [{"instrument_id": item["instrument_id"],
               "decision_status": "WATCH", "primary_stage": "START", "rank": 1, "reason_codes": ["TEST_FIXTURE_FROZEN_REASON"],
               "comparable_close": item["comparable_close"], "comparable_basis": item["comparable_basis"]}]}}}
    core.store.put_document("strategy_experiment_runs", run_id, run)
    core.store.put_document("strategy_experiment_summaries", run_id, {"run_id": run_id, "results": {STAGE: {}}})
    client = create_app(core).test_client()
    try:
        before = fingerprint(core)
        response = client.get(f"/api/daily-research/runs/{run_id}/snapshot")
        assert response.status_code == 200, response.text
        projected = response.json
        assert projected["identity"]["strategy_version"] is None
        assert projected["identity"]["config_version"] is None
        assert projected["items"][0]["decision_status"] == "WATCH"
        assert projected["items"][0]["reason_codes"] == ["TEST_FIXTURE_FROZEN_REASON"]
        assert core.store.get_document("daily_research_snapshots", run_id) is None
        outcomes = client.get(f"/api/daily-research/runs/{run_id}/outcomes")
        assert outcomes.status_code == 200 and outcomes.json["items"] == []
        assert len(outcomes.json["summary"]["groups"]) == 20
        assert all(row["counts"]["signals"] == row["counts"]["missing_labels"] == 1 for row in outcomes.json["summary"]["groups"])
        assert fingerprint(core) == before
    finally:
        core.close()


def test_new_input_appends_revision_and_asof_retrieves_original_observation(tmp_path, monkeypatch):
    core, input_id, snapshot, bundle, clock = isolated_consumer(tmp_path, monkeypatch, count=1)
    run_id = snapshot["identity"]["run_id"]
    try:
        original_clock = clock.current
        job = core.candidates.submit_daily_recommendation_outcomes(run_id, input_id)
        assert _wait_job(core, job["job_id"])["state"] == "SUCCEEDED"
        original = core.candidates.daily_recommendation_outcomes(run_id, as_of=original_clock)
        assert original["items"][0]["status"] == "NORMAL"
        revised_bundle = edit_bar(bundle, "TEST000", bundle.sessions[6], comparable_basis="TEST_FIXTURE_CHANGED_BASIS")
        changed_input = core.candidates.register_input(revised_bundle, activate=False)
        clock.current += timedelta(seconds=1)
        newer = core.candidates.submit_daily_recommendation_outcomes(run_id, changed_input)
        assert newer["job_id"] != job["job_id"]
        assert _wait_job(core, newer["job_id"])["state"] == "SUCCEEDED"
        now = core.candidates.daily_recommendation_outcomes(run_id)
        assert len(now["items"]) == 20
        assert now["items"][0]["status"] == "UNAVAILABLE"
        assert now["items"][0]["close_reference_change"] is None
        assert core.candidates.daily_recommendation_outcomes(run_id, as_of=original_clock)["items"] == original["items"]
        assert len(core.store.list_documents("daily_research_outcomes", limit=100)) == 40
        assert core.candidates.daily_recommendation_snapshot(run_id) == snapshot
        assert core.candidates.pending_daily_recommendation_outcomes()[0]["run_id"] == run_id
    finally:
        core.close()


def test_analysis_keeps_versions_separate_and_filters_future_generated_snapshot(tmp_path, monkeypatch):
    core, input_id, snapshot, _, clock = isolated_consumer(tmp_path, monkeypatch, count=1)
    try:
        job = core.candidates.submit_daily_recommendation_outcomes(snapshot["identity"]["run_id"], input_id)
        assert _wait_job(core, job["job_id"])["state"] == "SUCCEEDED"
        initial_clock = clock.current
        second = deepcopy(snapshot)
        second.pop("snapshot_hash")
        second["identity"].update(run_id="TEST_FIXTURE_SECOND_RUN", strategy_version="TEST_FIXTURE_STRATEGY_V2",
                                  generated_at=(initial_clock + timedelta(seconds=1)).isoformat())
        second["items"][0]["run_id"] = second["identity"]["run_id"]
        second = store_fixture_snapshot(core, second)
        earlier = core.candidates.daily_recommendation_analysis(as_of=initial_clock)
        assert len(earlier["groups"]) == 20
        assert {row["strategy_version"] for row in earlier["groups"]} == {"TEST_FIXTURE_STRATEGY_V1"}
        assert earlier["snapshot_availability"]["excluded_not_yet_available"] == 1
        clock.current += timedelta(seconds=2)
        analysis = core.candidates.daily_recommendation_analysis()
        assert len(analysis["groups"]) == 40
        second_only = core.candidates.daily_recommendation_analysis(strategy_version="TEST_FIXTURE_STRATEGY_V2")
        assert len(second_only["groups"]) == 20
        assert all(row["counts"]["missing_labels"] == 1 for row in second_only["groups"])
        assert all(row["return_distribution"]["mean"] is None for row in second_only["groups"])
        with pytest.raises(ValueError, match="availability cannot be established"):
            core.candidates.daily_recommendation_outcomes(second["identity"]["run_id"], as_of=initial_clock)
    finally:
        core.close()


@pytest.mark.parametrize("mismatch", ["policy", "snapshot_hash"])
def test_worker_refuses_changed_policy_or_snapshot_and_writes_no_outcomes(tmp_path, monkeypatch, mismatch):
    core, input_id, snapshot, _, clock = isolated_consumer(tmp_path, monkeypatch, count=1)
    payload = {"run_id": snapshot["identity"]["run_id"], "input_id": input_id,
               "evaluated_at": clock.current.isoformat(), "snapshot_hash": snapshot["snapshot_hash"], "policy": POLICY_ID}
    payload[mismatch] = "TEST_FIXTURE_DIFFERENT_" + mismatch.upper()
    try:
        job = core.jobs.submit("daily_research_outcomes", payload)
        done = _wait_job(core, job["job_id"])
        assert done["state"] == "FAILED", done
        assert core.store.list_documents("daily_research_outcomes") == []
        assert core.store.list_documents("daily_research_outcome_captures") == []
    finally:
        core.close()


@pytest.mark.parametrize("mismatch", ["run", "input", "kind"])
def test_explicit_job_id_never_returns_an_unrelated_existing_task(tmp_path, monkeypatch, mismatch):
    core, input_id, snapshot, bundle, _ = isolated_consumer(tmp_path, monkeypatch, count=1)
    run_id = snapshot["identity"]["run_id"]
    key = "TEST_FIXTURE_EXPLICIT_JOB_ID"
    try:
        if mismatch == "kind":
            core.jobs.create("TEST_FIXTURE_OTHER_KIND", {}, job_id=key)
        else:
            first = core.candidates.submit_daily_recommendation_outcomes(run_id, input_id, job_id=key)
            assert _wait_job(core, first["job_id"])["state"] == "SUCCEEDED"
            if mismatch == "run":
                second = deepcopy(snapshot)
                second.pop("snapshot_hash")
                second["identity"]["run_id"] = "TEST_FIXTURE_OTHER_RUN"
                second["items"][0]["run_id"] = second["identity"]["run_id"]
                store_fixture_snapshot(core, second)
                run_id = second["identity"]["run_id"]
            else:
                input_id = core.candidates.register_input(edit_bar(bundle, "TEST000", bundle.sessions[6], comparable_basis="TEST_FIXTURE_OTHER_INPUT"), activate=False)
        before = fingerprint(core)
        with pytest.raises(ValueError):
            core.candidates.submit_daily_recommendation_outcomes(run_id, input_id, job_id=key)
        assert fingerprint(core) == before
    finally:
        core.close()


def test_future_source_receipt_can_save_missingness_but_never_a_price_result(tmp_path, monkeypatch):
    core, _, snapshot, bundle, clock = isolated_consumer(tmp_path, monkeypatch, count=1)
    run_id = snapshot["identity"]["run_id"]
    try:
        future = clock.current + timedelta(seconds=1)
        receipts = tuple(receipt.model_copy(update={"first_seen_at": future, "ingested_at": future})
                         if receipt.receipt_id == "fixture-bars" else receipt for receipt in bundle.receipts)
        input_id = core.candidates.register_input(bundle.model_copy(update={"receipts": receipts}), activate=False)
        job = core.candidates.submit_daily_recommendation_outcomes(run_id, input_id)
        done = _wait_job(core, job["job_id"])
        assert done["state"] == "SUCCEEDED", done
        result = core.candidates.daily_recommendation_outcomes(run_id)
        assert len(result["items"]) == 20
        assert all(row["status"] == "UNAVAILABLE" for row in result["items"])
        assert all(row["close_price"] is row["close_reference_change"] is row["mfe_bound"] is None for row in result["items"])
        assert all(row["counts"]["valid_return"] == 0 and row["return_distribution"]["mean"] is None for row in result["summary"]["groups"])
        assert core.candidates.pending_daily_recommendation_outcomes()[0]["run_id"] == run_id
    finally:
        core.close()


@pytest.mark.parametrize("boundary", ["cutoff_without_generated", "publication_after_generation"])
def test_asof_cannot_expose_a_snapshot_before_its_known_availability_boundaries(tmp_path, monkeypatch, boundary):
    snapshot, bundle, cutoff = case(1)
    if boundary == "cutoff_without_generated":
        snapshot["identity"]["generated_at"] = None
        requested = datetime.fromisoformat(snapshot["identity"]["data_cutoff"]) - timedelta(seconds=1)
    else:
        requested = datetime.fromisoformat(snapshot["identity"]["generated_at"])
        snapshot["published_at"] = (requested + timedelta(seconds=1)).isoformat()
    core, _, _ = _core(tmp_path, monkeypatch, bundle, now=cutoff)
    monkeypatch.setattr(core.candidates, "_daily_research_sessions", lambda: list(bundle.sessions))
    snapshot = store_fixture_snapshot(core, snapshot)
    run_id = snapshot["identity"]["run_id"]
    client = create_app(core).test_client()
    try:
        before = fingerprint(core)
        for path in (f"/api/daily-research/runs/{run_id}/outcomes", "/api/daily-research/report"):
            query = {"as_of": requested.isoformat()}
            if path.endswith("/report"):
                query["run_id"] = run_id
            response = client.get(path, query_string=query)
            assert response.status_code == 400, (path, response.text)
        analysis = client.get("/api/daily-research/analysis", query_string={"as_of": requested.isoformat()})
        assert analysis.status_code == 200 and analysis.json["groups"] == []
        assert fingerprint(core) == before
    finally:
        core.close()


def test_concurrent_same_session_requests_share_one_job_despite_clock_difference(tmp_path, monkeypatch):
    core, input_id, snapshot, _, clock = isolated_consumer(tmp_path, monkeypatch, count=1)
    run_id = snapshot["identity"]["run_id"]
    gate, clock_lock = Barrier(2), Lock()
    state = {"calls": 0}
    original_get = core.jobs.get
    def separated_clock():
        with clock_lock:
            state["calls"] += 1
            return clock.current + timedelta(microseconds=state["calls"])
    def simultaneous_missing_lookup(job_id):
        value = original_get(job_id)
        if value is None and job_id.startswith("daily_research_outcome_"):
            gate.wait(timeout=5)
        return value
    monkeypatch.setattr(core.candidates, "_daily_research_now", separated_clock)
    monkeypatch.setattr(core.jobs, "get", simultaneous_missing_lookup)
    try:
        with ThreadPoolExecutor(max_workers=2) as callers:
            futures = [callers.submit(core.candidates.submit_daily_recommendation_outcomes, run_id, input_id) for _ in range(2)]
            responses = [future.result(timeout=10) for future in futures]
        assert responses[0]["job_id"] == responses[1]["job_id"]
        assert _wait_job(core, responses[0]["job_id"])["state"] == "SUCCEEDED"
        assert len(core.jobs.list(kind="daily_research_outcomes")) == 1
        assert len(core.store.list_documents("daily_research_outcomes", limit=100)) == 20
    finally:
        core.close()


def test_run_without_focus_or_watch_has_no_tracked_recommendations_or_false_pending(tmp_path, monkeypatch):
    snapshot, bundle, cutoff = case(2)
    snapshot["items"][0]["decision_status"] = "UNKNOWN"
    snapshot["items"][1]["decision_status"] = "REJECTED"
    core, _, _ = _core(tmp_path, monkeypatch, bundle, now=cutoff)
    monkeypatch.setattr(core.candidates, "_daily_research_sessions", lambda: list(bundle.sessions))
    snapshot = store_fixture_snapshot(core, snapshot)
    client = create_app(core).test_client()
    try:
        before = fingerprint(core)
        response = client.get(f"/api/daily-research/runs/{snapshot['identity']['run_id']}/outcomes")
        assert response.status_code == 200, response.text
        assert response.json["tracking_status"] == "NO_TRACKED_RECOMMENDATIONS"
        assert response.json["items"] == []
        assert response.json["horizons"] == list(range(1, 21))
        assert response.json["summary_scope"] == "RUN"
        assert response.json["instrument_id"] is None
        assert response.json["summary"]["groups"] == []
        assert core.candidates.pending_daily_recommendation_outcomes() == []
        assert fingerprint(core) == before
    finally:
        core.close()


@pytest.mark.parametrize("instrument_id", ["TEST002", "TEST003"])
def test_untracked_decision_query_retains_run_summary_without_fake_capture_pending(tmp_path, monkeypatch, instrument_id):
    snapshot, bundle, cutoff = case(4)
    snapshot["items"][2]["decision_status"] = "UNKNOWN"
    snapshot["items"][3]["decision_status"] = "REJECTED"
    core, input_id, _ = _core(tmp_path, monkeypatch, bundle, now=cutoff)
    monkeypatch.setattr(core.candidates, "_daily_research_sessions", lambda: list(bundle.sessions))
    snapshot = store_fixture_snapshot(core, snapshot)
    run_id = snapshot["identity"]["run_id"]
    client = create_app(core).test_client()
    try:
        job = core.candidates.submit_daily_recommendation_outcomes(run_id, input_id)
        assert _wait_job(core, job["job_id"])["state"] == "SUCCEEDED"
        before = fingerprint(core)
        full = client.get(f"/api/daily-research/runs/{run_id}/outcomes").json
        assert full["tracking_status"] == "RECORDED" and len(full["items"]) == 40
        response = client.get(f"/api/daily-research/runs/{run_id}/outcomes", query_string={"instrument_id": instrument_id})
        assert response.status_code == 200, response.text
        selected = response.json
        assert selected["tracking_status"] == "NOT_TRACKED_DECISION"
        assert selected["instrument_id"] == instrument_id
        assert selected["summary_scope"] == "RUN"
        assert selected["items"] == []
        assert selected["summary"] == full["summary"]
        assert len(selected["summary"]["groups"]) == 40
        assert all(group["counts"]["signals"] == group["counts"]["valid_return"] == 1 for group in selected["summary"]["groups"])
        assert fingerprint(core) == before
    finally:
        core.close()
