"""Real TestClient checks over isolated, explicitly synthetic saved runs."""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
import pytest

from stockfish_live.api.app import create_app
from stockfish_live.api.view import RunView
from stockfish_live.common import LiveError, canonical_json, digest
from stockfish_live.contracts.forecast import ForecastResult, HorizonForecast, MatureOutcome, REGIMES, SimilarNeighbor
from stockfish_live.contracts.market import MinuteBar


SH = ZoneInfo("Asia/Shanghai")
ENDPOINTS = ("health", "status", "market", "indices", "breadth", "sentiment", "sectors",
             "forecast", "forecast/history", "similar-states", "warnings")


def at(minute=45):
    return datetime(2026, 10, 9, 9, minute, tzinfo=SH)


def synthetic_forecast():
    historical = at() - timedelta(days=1)
    outcome = MatureOutcome(state_id="TEST_FIXTURE_PAST_STATE", instrument_id="000001.SH",
        decision_at=historical, horizon="30m", target_at=historical + timedelta(minutes=30),
        available_at=historical + timedelta(minutes=30), anchor_price=100., target_price=101.,
        regime="TREND_UP", pit_status="TEST_FIXTURE")
    neighbor = SimilarNeighbor(state_id=outcome.state_id, decision_at=historical, distance=.1,
        context={"regime": "TEST_FIXTURE_REGIME", "sentiment": "EXPANSION"}, outcome=outcome)
    quantiles = dict(zip(("q10", "q25", "q50", "q75", "q90"), (-.02, -.01, .01, .02, .03)))
    horizon = HorizonForecast(horizon="30m", target_at=at()+timedelta(minutes=30), sample_size=1,
        available_sample_size=1, status="AVAILABLE", confidence="LOW", quantiles=quantiles,
        price_quantiles={key: 100*(1+value) for key, value in quantiles.items()}, up_probability=1.,
        regime_probabilities={regime: float(regime == "TREND_UP") for regime in REGIMES},
        regime_sample_size=1, neighbors=(neighbor,), model_hash=digest("TEST_FIXTURE_MODEL"))
    return ForecastResult(forecast_id=digest("TEST_FIXTURE_FORECAST"), instrument_id="000001.SH",
        decision_at=at(), as_of=at(), current_price=100., state_id="TEST_FIXTURE_CURRENT_STATE",
        configuration_hash=digest("TEST_FIXTURE_CONFIG"), horizons=(horizon,), confidence="LOW",
        status="AVAILABLE", warnings=("MODEL_LOW_CONFIDENCE",), pit_status="TEST_FIXTURE").model_dump(mode="json")


def record(minute=45, *, model=True):
    now = at(minute)
    price = 100. if minute == 45 else 1234.  # Future sentinel must never reach 09:45 responses.
    raw = f"TEST_FIXTURE_API_MINUTE_{minute}".encode()
    bar = MinuteBar(instrument_id="000001.SH", exchange="SH", symbol="000001", asset_type="INDEX",
        timestamp=now, open=price, high=price, low=price, close=price, volume=100., turnover=price*100,
        provider="TEST_FIXTURE_API", source_timestamp=now, source_timestamp_basis="PUBLISHED",
        available_at=now, source_id=hashlib.sha256(raw).hexdigest(), quality="VALID", pit_status="TEST_FIXTURE")
    result = {"schema_version": "LIVE_REPLAY_STATE_V1", "as_of": now.isoformat(),
        "data_cutoff": now.isoformat(), "input_hash": digest(raw.hex()), "config_hash": digest("TEST_FIXTURE_CONFIG"),
        "scope": "DECLARED_SCOPE_ONLY", "pit_status": "TEST_FIXTURE", "cycle_error": None,
        "state": {"quality": "GAPPED", "bar_count": minute-44, "warnings": ["DATA_GAP"]},
        "received_bars": [bar.model_dump(mode="json")], "market_edge": "NOT_ESTABLISHED"}
    if model:
        result.update(market_state={"as_of": now.isoformat(),
            "indices": {"000001.SH": {"current_price": price, "features": {"return_pct": .1}}},
            "breadth": {"status": "UNAVAILABLE", "full_market_status": "UNAVAILABLE", "advance_count": None},
            "sentiment": {"state": "UNKNOWN", "previous_state": "UNKNOWN", "next_state_probabilities": None},
            "sectors": {"status": "UNAVAILABLE", "sectors": []}},
            forecasts={"000001.SH": synthetic_forecast()},
            forecast_revisions={"000001.SH": {"status": "INSUFFICIENT", "comparisons": []}})
    result["snapshot_id"] = digest(result)
    return result


def write_record(root, value):
    path = root / "snapshots" / f"{datetime.fromisoformat(value['as_of']):%H%M}.json"
    path.write_text(canonical_json(value)+"\n", encoding="utf-8")


def write_summary(root, records):
    summary = {"date": at().date().isoformat(), "ticks": len(records),
        "snapshot_sequence_hash": digest([r["snapshot_id"] for r in records]),
        "final_state": records[-1]["state"], "pit_status": "TEST_FIXTURE", "scope": "DECLARED_SCOPE_ONLY",
        "failed_cycles": 0}
    (root / "summary.json").write_text(canonical_json(summary)+"\n", encoding="utf-8")


def saved_run(tmp_path, *, model=True, complete=True):
    root = tmp_path / "TEST_FIXTURE_API_RUN"
    (root / "snapshots").mkdir(parents=True)
    records = [record(minute, model=model) for minute in (45, 46)]
    for value in records:
        write_record(root, value)
    if complete:
        write_summary(root, records)
    return root, records


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_all_eleven_get_endpoints_are_live_read_only_and_explicit_fixture(tmp_path, endpoint):
    root, _ = saved_run(tmp_path)
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    with TestClient(create_app(root)) as client:
        response = client.get("/api/live/"+endpoint)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["market_edge"] == "NOT_ESTABLISHED"
    assert value["live_trading"] == "DISABLED"
    assert value["broker_connection"] == "NONE"
    assert value["fixture"] is True
    assert value["pit_status"] == "TEST_FIXTURE"
    assert value["integrity_status"] == "VERIFIED"
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_historical_as_of_cannot_return_future_actual_or_final_summary(tmp_path, endpoint):
    root, _ = saved_run(tmp_path)
    with TestClient(create_app(root)) as client:
        response = client.get("/api/live/"+endpoint, params={"as_of": at().isoformat()})
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["as_of"] == at().isoformat()
    assert "1234" not in response.text
    assert at(46).isoformat() not in response.text
    if endpoint == "market":
        assert len(value["actual_paths"]["000001.SH"]) == 1
        assert value["indices"]["000001.SH"]["current_price"] == 100.
    if endpoint == "status":
        assert value["timeline"] == [at().isoformat()]
        assert value["summary"] is None


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_every_endpoint_rejects_a_future_as_of(tmp_path, endpoint):
    root, _ = saved_run(tmp_path)
    with TestClient(create_app(root)) as client:
        response = client.get("/api/live/"+endpoint, params={"as_of": at(47).isoformat()})
    assert response.status_code == 422
    assert response.json()["error"] == "FUTURE_ACCESS_DENIED"


@pytest.mark.parametrize("as_of", ["2026-10-09T09:45:00", "not-a-date", "2026-99-09T09:45:00+08:00"])
def test_naive_or_invalid_as_of_is_rejected(tmp_path, as_of):
    root, _ = saved_run(tmp_path)
    with TestClient(create_app(root)) as client:
        response = client.get("/api/live/market", params={"as_of": as_of})
    assert response.status_code == 422 and response.json()["error"] == "INVALID_AS_OF"


def test_forecast_history_deduplicates_immutable_prediction_and_exposes_mature_neighbors(tmp_path):
    root, _ = saved_run(tmp_path)
    with TestClient(create_app(root)) as client:
        history = client.get("/api/live/forecast/history").json()
        similar = client.get("/api/live/similar-states").json()
        assert client.get("/api/live/similar-states?limit=11").status_code == 422
        assert client.get("/api/live/forecast?index=../secret").status_code == 422
    assert len(history["history"]) == 1
    assert len(history["revisions"]) == 1
    assert len(similar["similar_states"]) == 1
    assert similar["similar_states"][0]["future_results"]["30m"] == pytest.approx(.01)


def test_phase1_run_remains_usable_but_never_fabricates_model_data(tmp_path):
    root, _ = saved_run(tmp_path, model=False)
    with TestClient(create_app(root)) as client:
        market = client.get("/api/live/market").json()
        forecast = client.get("/api/live/forecast").json()
        status = client.get("/api/live/status").json()
    assert market["market_state"] == {"status": "UNAVAILABLE", "reason": "NO_SAVED_MARKET_STATE"}
    assert market["forecasts"] == {}
    assert forecast["forecast"] == {"status": "UNAVAILABLE", "reason": "NO_SAVED_FORECAST"}
    assert status["model_status"] == "UNAVAILABLE"
    assert market["actual_paths"]["000001.SH"][-1]["close"] == 1234.


@pytest.mark.parametrize("kind", ["snapshot", "sequence", "summary_state", "future_actual", "extra_file"])
def test_archive_tampering_fails_closed(tmp_path, kind):
    root, records = saved_run(tmp_path)
    if kind in ("sequence", "summary_state"):
        path = root / "summary.json"
        value = json.loads(path.read_text())
        if kind == "sequence":
            value["snapshot_sequence_hash"] = "0"*64
        else:
            value["final_state"]["bar_count"] = 999999
        path.write_text(canonical_json(value))
    elif kind == "extra_file":
        (root / "snapshots" / "secret.json").write_text("{}")
    else:
        value = records[0]
        value["received_bars"][0]["available_at"] = at(46).isoformat()
        if kind == "future_actual":
            value["snapshot_id"] = digest({key:item for key,item in value.items() if key!="snapshot_id"})
            write_summary(root, records)
        write_record(root, value)
    with TestClient(create_app(root)) as client:
        response = client.get("/api/live/market")
    assert response.status_code == 503
    assert response.json()["error"] == "RUN_INTEGRITY_ERROR"


def test_no_query_or_asset_route_can_read_an_arbitrary_file(tmp_path):
    root, _ = saved_run(tmp_path)
    private = tmp_path / "private-secret.txt"
    private.write_text("TEST_FIXTURE_PRIVATE_DATA_MUST_NOT_LEAK")
    with TestClient(create_app(root)) as client:
        ordinary = client.get("/api/live/market", params={"path":str(private), "run_dir":str(tmp_path)})
        assert ordinary.status_code == 200
        assert private.read_text() not in ordinary.text
        for path in ("/assets/private-secret.txt", "/assets/../private-secret.txt", "/api/live/file"):
            response = client.get(path)
            assert response.status_code == 404
            assert private.read_text() not in response.text
    snapshot = root / "snapshots" / "0945.json"
    snapshot.unlink()
    snapshot.symlink_to(private)
    with pytest.raises(LiveError, match="RUN_INTEGRITY_ERROR"):
        RunView(root).market()


def test_in_progress_and_failed_runs_are_distinct_and_read_only(tmp_path):
    root, _ = saved_run(tmp_path, complete=False)
    assert RunView(root).status()["run_status"] == "IN_PROGRESS"
    with pytest.raises(LiveError, match="RUN_INCOMPLETE"):
        RunView(root, allow_in_progress=False).status()
    (root / "FAILED").write_text("REPLAY_INCOMPLETE\n")
    assert RunView(root).market()["run_status"] == "FAILED"


def test_websocket_history_is_time_bounded_and_future_request_is_denied(tmp_path):
    root, _ = saved_run(tmp_path)
    with TestClient(create_app(root, poll_interval=.05)) as client:
        with client.websocket_connect("/ws/live?"+"as_of="+at().isoformat().replace("+", "%2B")) as socket:
            value = socket.receive_json()
            assert value["type"] == "snapshot"
            assert value["as_of"] == at().isoformat()
            assert len(value["actual_paths"]["000001.SH"]) == 1
            assert "1234" not in canonical_json(value)
        with client.websocket_connect("/ws/live?"+"as_of="+at(47).isoformat().replace("+", "%2B")) as socket:
            assert socket.receive_json()["error"] == "FUTURE_ACCESS_DENIED"


def test_websocket_pushes_newly_saved_snapshot_without_restarting_service(tmp_path):
    root, _ = saved_run(tmp_path, complete=False)
    with TestClient(create_app(root, poll_interval=.05)) as client:
        with client.websocket_connect("/ws/live") as socket:
            assert socket.receive_json()["as_of"] == at(46).isoformat()
            write_record(root, record(47))
            socket.send_text("ping")
            update = socket.receive_json()
            assert update["as_of"] == at(47).isoformat()
            assert len(update["actual_paths"]["000001.SH"]) == 3


def test_dashboard_and_assets_are_local_and_display_research_boundaries(tmp_path):
    root, _ = saved_run(tmp_path)
    with TestClient(create_app(root)) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "TEST_FIXTURE" in response.text
        assert "MARKET_EDGE=NOT_ESTABLISHED" in response.text
        assert "LIVE_TRADING=DISABLED" in response.text
        assert "Similar" not in response.text or "SIMILAR" in response.text
        for filename in ("dashboard.js", "dashboard.css"):
            asset = client.get("/assets/"+filename)
            assert asset.status_code == 200
            assert "cdn." not in asset.text
        assert 'aria-label="历史观察时间"' in response.text


def test_empty_missing_run_does_not_create_files(tmp_path):
    root = tmp_path / "TEST_FIXTURE_NOT_CREATED"
    with TestClient(create_app(root)) as client:
        response = client.get("/api/live/status")
    assert response.status_code == 503 and response.json()["error"] == "RUN_UNAVAILABLE"
    assert not root.exists()


def test_stat_cache_reuses_validated_bytes_but_catches_same_size_restored_mtime_tamper(tmp_path, monkeypatch):
    import stockfish_live.api.view as module
    root, _ = saved_run(tmp_path)
    reads = []
    original = module._read_json
    def record_read(path):
        reads.append(path)
        return original(path)
    monkeypatch.setattr(module,"_read_json",record_read)
    view = RunView(root)
    view.market()
    initial = list(reads)
    view.status(at())
    view.forecast()
    view.similar_states()
    assert reads == initial
    snapshot = root/"snapshots"/"0945.json"
    before = snapshot.stat()
    snapshot.write_text(snapshot.read_text().replace('"close":100.0','"close":101.0'))
    assert snapshot.stat().st_size == before.st_size
    os.utime(snapshot,ns=(before.st_atime_ns,before.st_mtime_ns))
    with pytest.raises(LiveError,match="RUN_INTEGRITY_ERROR"):
        view.market()
    assert len(reads) == len(initial)+1


def test_cached_public_responses_are_detached_and_failed_marker_is_rechecked(tmp_path):
    root, _ = saved_run(tmp_path)
    view = RunView(root)
    result = view.market()
    result["indices"]["000001.SH"]["current_price"] = 999999
    result["forecasts"]["000001.SH"]["current_price"] = 999999
    result["market_state"]["sentiment"]["state"] = "FALSE_STRONG"
    expected = view.market()
    assert expected["indices"]["000001.SH"]["current_price"] == 1234.
    assert expected["forecasts"]["000001.SH"]["current_price"] == 100.
    assert expected["market_state"]["sentiment"]["state"] == "UNKNOWN"
    (root/"FAILED").write_text("TEST_FIXTURE_INCOMPLETE\n")
    assert view.status()["run_status"] == "FAILED"


def test_model_implementation_distribution_and_data_quality_are_separate(tmp_path):
    root, records = saved_run(tmp_path)
    for value in records:
        value["schema_version"] = "LIVE_REPLAY_STATE_V2"
        forecast = value["forecasts"]["000001.SH"]
        forecast.update(status="INSUFFICIENT",confidence="INSUFFICIENT",horizons=[],
                        forecast_id=digest("TEST_FIXTURE_INSUFFICIENT_FORECAST"))
        value["forecast_status"] = "DATA_QUALITY_LIMITED"
        value["snapshot_id"] = digest({key:item for key,item in value.items() if key!="snapshot_id"})
        write_record(root,value)
    write_summary(root,records)
    view = RunView(root)
    status = view.status()
    assert status["model_status"] == "INSUFFICIENT"
    assert status["data_quality"] == "GAPPED"
    assert status["forecast_status"] == "DATA_QUALITY_LIMITED"
    for value in records:
        value["forecasts"] = {}
        value["snapshot_id"] = digest({key:item for key,item in value.items() if key!="snapshot_id"})
        write_record(root,value)
    write_summary(root,records)
    assert view.status()["model_status"] == "IMPLEMENTED"


def test_api_compacts_transport_only_without_modifying_saved_forecast_evidence(tmp_path):
    root, records = saved_run(tmp_path)
    before = {path:path.read_bytes() for path in root.rglob("*") if path.is_file()}
    view = RunView(root)
    forecast = view.forecast()["forecast"]
    assert "DASHBOARD_SUMMARY" in forecast["response_detail"]
    neighbor = forecast["horizons"][0]["neighbors"][0]
    assert "future_path" not in neighbor["outcome"]
    assert "path_point_count" in neighbor["outcome"]
    assert "neighbors" not in view.history()["history"][0]["horizons"][0]
    assert {path:path.read_bytes() for path in before} == before


def test_v2_preopen_index_metadata_without_bars_can_receive_first_actual_minute(tmp_path):
    root, records = saved_run(tmp_path)
    opening = record(44)
    opening.update(schema_version="LIVE_REPLAY_STATE_V2",received_bars=[],forecasts={},data_cutoff=None)
    opening["state"]["bar_count"] = 0
    opening["market_state"]["indices"]["000001.SH"]["current_price"] = None
    opening["snapshot_id"] = digest({key:item for key,item in opening.items() if key!="snapshot_id"})
    write_record(root,opening)
    write_summary(root,[opening]+records)
    with TestClient(create_app(root)) as client:
        before = client.get("/api/live/market",params={"as_of":at(44).isoformat()})
        after = client.get("/api/live/market")
    assert before.status_code == after.status_code == 200
    assert before.json()["indices"]["000001.SH"]["current_price"] is None
    assert after.json()["indices"]["000001.SH"]["current_price"] == 1234.
