import time

import pytest

from stockfish_core.api import create_app
from stockfish_core.yaogu.data_pipeline import PIPELINE_VERSION
from tests.integration.core_fixture import fixture_service, DECISION, IIDS


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.delenv("STOCKFISH_API_KEY", raising=False)
    core, snapshot_id, model = fixture_service(tmp_path)
    app = create_app(core)
    yield app.test_client(), core, snapshot_id, model
    core.close()


@pytest.mark.parametrize("path,payload", [
    ("/api/analyze", {"symbol": []}), ("/api/batch/analyze", {"symbols": []}),
    ("/api/qlib/infer", {"model": 123}), ("/api/analyze", []),
    ("/api/analyze", {"instruments": ["600000.SH"], "cash": -1}),
    ("/api/analyze", {"symbol": "600000.SH", "available_cash": True}),
    ("/api/analyze", {"instruments": ["600000.SH"], "cash": True}),
    ("/api/analyze", {"instruments": ["600000.SH"], "cash": "100000"}),
    ("/api/qlib/train", {"target": "legacy"}),
    ("/api/report/download", {"symbol": '<img src=x onerror=alert(1)>'}),
])
def test_invalid_input_is_4xx_json(api, path, payload):
    client, _, _, _ = api
    response = client.post(path, json=payload)
    assert response.status_code == 400
    assert response.is_json
    assert "Traceback" not in response.text


def test_same_canonical_result_and_durable_job_api(api):
    client, core, sid, model = api
    request = dict(snapshot_id=sid, model_id=model.model_id, instruments=list(IIDS), mode="REPLAY",
                   decision_time=DECISION.isoformat(), cash=100000)
    response = client.post("/api/recommendations", json=request)
    assert response.status_code == 202
    job_id = response.json["job_id"]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = client.get("/api/jobs/" + job_id).json
        if result["state"] in ("SUCCEEDED", "FAILED"):
            break
        time.sleep(.02)
    assert result["state"] == "SUCCEEDED", result
    assert result["best_stock"] == result["top5"][0] == result["top20"][0]
    history = client.get("/api/recommendations/history").json
    assert len(history) == 3
    assert client.get("/api/recommendations/" + history[0]["recommendation_id"]).status_code == 200
    report = client.post("/api/report/download", json={"recommendation_run_id": result["recommendation_run_id"]})
    assert report.status_code == 200 and "统一推荐" in report.text
    stream = client.get("/api/jobs/" + job_id + "/stream")
    assert stream.status_code == 200 and '"state": "SUCCEEDED"' in stream.text


def test_health_is_component_specific_and_disk_persistence_visible(api):
    client, _, _, _ = api
    health = client.get("/api/health")
    assert health.status_code == 200
    assert set(health.json["components"]) == {"application", "market_data", "quant", "model_registry", "research", "llm", "tracker", "jobs"}
    assert health.json["components"]["llm"]["status"] == "unavailable"
    assert health.json["components"]["market_data"]["reason"] == "STALE_SNAPSHOT"
    assert health.json["production_ready"] is False
    assert client.get("/api/memory/stats").status_code == 200


def test_cross_origin_remote_and_dns_rebinding_rejected(api):
    client, _, _, _ = api
    assert client.post("/api/recommendations", json={}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 403
    assert client.get("/api/health", environ_base={"REMOTE_ADDR": "192.0.2.1"}).status_code == 403
    assert "Access-Control-Allow-Origin" not in client.get("/api/health").headers


def test_missing_job_and_model_never_fake_success(api):
    client, _, _, _ = api
    assert client.get("/api/jobs/unknown").status_code == 404
    response = client.post("/api/analyze", json={"instruments": ["600000.SH"], "model_id": "missing"})
    assert response.status_code == 503
    assert response.json["quality"] == "UNAVAILABLE"


def test_candidate_data_pipeline_status_exposes_registered_pipeline_version(api):
    client, _, _, _ = api
    response = client.get("/api/candidate-data-pipeline")
    assert response.status_code == 200
    assert response.json["pipeline_version"] == PIPELINE_VERSION
    assert response.json["resume_policy"] == "PERSISTENT_REQUEST_QUEUE_FINITE_BUDGET_NO_FALSE_COMPLETION"


def test_quant_only_missing_features_is_failed_with_saved_diagnostics(api):
    client, core, sid, model = api
    original = core.snapshot(sid)
    partial_id = core.register_snapshot(original.model_copy(update={"features": original.features[:1]}))
    response = client.post("/api/quant/runs", json=dict(snapshot_id=partial_id, model_id=model.model_id,
        instruments=list(IIDS[1:]), mode="REPLAY", decision_time=DECISION.isoformat()))
    assert response.status_code == 202
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = client.get("/api/jobs/" + response.json["job_id"]).json
        if job["state"] in ("SUCCEEDED", "FAILED"):
            break
        time.sleep(.02)
    assert job["state"] == "FAILED" and job["quality"] == "UNAVAILABLE"
    assert job["quant"]["scores"] == [] and len(job["quant"]["rejected"]) == 2
    assert client.get("/api/quant/runs/" + job["run_id"]).json == job["quant"]
