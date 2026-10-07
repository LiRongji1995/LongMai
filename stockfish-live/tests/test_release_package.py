"""TEST_FIXTURE release and cross-version integration boundaries."""
from datetime import timedelta
from importlib.resources import files
from pathlib import Path
import json
import re
import socket
import subprocess
import sys
from time import monotonic, sleep
import tomllib
from urllib.error import URLError
from urllib.request import urlopen

import pytest

from stockfish_live import __version__
from stockfish_live.api.app import create_app
from stockfish_live.common import LiveError, canonical_json, digest
from stockfish_live.contracts.forecast import ForecastConfig, HistoricalState, MatureOutcome
from stockfish_live.features.market import MarketStateEngine
from stockfish_live.features.streaming import StreamingMarketStateEngine
from stockfish_live.forecast.engine import horizon_targets, state_from_market_state
from stockfish_live.forecast.pipeline import MarketResearchPipeline
from test_market_state import market_inputs, context_bundle, clock


def test_public_release_version_entrypoint_and_bundled_assets(tmp_path):
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    assert project["project"]["version"] == __version__
    assert project["project"]["scripts"] == {"stockfish-live": "stockfish_live.cli:main"}
    assert "wsproto>=1.2,<2" in project["project"]["dependencies"]
    assert create_app(tmp_path / "TEST_FIXTURE_MISSING_RUN").version == __version__
    frontend = files("stockfish_live").joinpath("frontend")
    for name in ("index.html", "dashboard.css", "dashboard.js"):
        assert frontend.joinpath(name).is_file()


def _process_prefix(pipeline, now, bars, receipts):
    quality = {"quality": "VALID", "warnings": []}
    if pipeline.consumes_deltas:
        for tick in pipeline.market.calendar.ticks(now.date()):
            if tick > now:
                break
            delta = [bar for bar in bars if bar.timestamp == tick]
            result = pipeline.process(tick, delta, {bar.source_id: receipts[bar.source_id] for bar in delta}, quality)
        return result
    return pipeline.process(now, [bar for bar in bars if bar.timestamp <= now], receipts, quality)


@pytest.mark.parametrize("feature_version", ["MARKET_FEATURES_V1", "MARKET_FEATURES_V2"])
def test_pipeline_version_selects_matching_computation_and_never_trains_other_config(market_inputs, feature_version):
    calendar, scope, bars, receipts = market_inputs
    now = clock(9, 45)
    days = tuple(now.date()-timedelta(days=n) for n in (4, 3, 2, 1, 0))
    calendar = calendar.model_copy(update={"sessions": days, "coverage_start": days[0]})
    scope = scope.model_copy(update={"calendar_version": calendar.version})
    config = ForecastConfig(feature_version=feature_version, k=2, min_samples=2,
        feature_names=("return_from_open_pct",))
    context = context_bundle()
    probe = MarketResearchPipeline(calendar, scope, context=context, config=config)
    current = _process_prefix(probe, now, bars, receipts)["market_state"]
    template = state_from_market_state(current, "000001.SH", now)
    assert type(probe.market) is (StreamingMarketStateEngine if feature_version.endswith("V2") else MarketStateEngine)
    assert template.feature_version == feature_version
    assert template.context["configuration_hash"] == probe.market_config_hash
    history, outcomes, matching = [], [], []
    for n, day in enumerate(days[:-1]):
        payload = template.model_dump(exclude={"state_id"})
        payload.update(decision_at=now.replace(year=day.year, month=day.month, day=day.day),
            available_at=now.replace(year=day.year, month=day.month, day=day.day),
            features={"return_from_open_pct": float(n+1) if n < 2 else 999999.})
        if n == 2:
            payload["feature_version"] = "MARKET_FEATURES_V1" if feature_version.endswith("V2") else "MARKET_FEATURES_V2"
        if n == 3:
            payload["context"]["configuration_hash"] = digest("TEST_FIXTURE_OTHER_FEATURE_CONFIGURATION")
        item = HistoricalState(state_id=digest(payload), **payload)
        history.append(item)
        if n < 2:
            matching.append(item.state_id)
        for horizon, target in horizon_targets(calendar, item.decision_at).items():
            outcomes.append(MatureOutcome(state_id=item.state_id, instrument_id=item.instrument_id,
                decision_at=item.decision_at, horizon=horizon, target_at=target, available_at=target,
                anchor_price=item.current_price, target_price=item.current_price*1.01,
                regime="TREND_UP", pit_status="TEST_FIXTURE"))
    pipeline = MarketResearchPipeline(calendar, scope, context=context, config=config,
        history=history, outcomes=outcomes)
    result = _process_prefix(pipeline, now, bars, receipts)
    forecast = result["forecasts"]["000001.SH"]
    assert forecast["status"] == "AVAILABLE"
    assert forecast["feature_version"] == feature_version
    for horizon in forecast["horizons"]:
        assert set(horizon["training_state_ids"]) == set(matching)
        assert horizon["normalization_mean"]["return_from_open_pct"] == 1.5
        assert horizon["available_sample_size"] == 2
    earlier = digest(result)
    following = now+timedelta(minutes=1)
    next_bars = [bar for bar in bars if (bar.timestamp == following if pipeline.consumes_deltas else bar.timestamp <= following)]
    next_receipts = {bar.source_id: receipts[bar.source_id] for bar in next_bars}
    next_result = pipeline.process(following, next_bars, next_receipts, {"quality": "VALID", "warnings": []})
    assert next_result["forecasts"] == {}
    assert next_result["latest_forecast_ids"]["000001.SH"] == forecast["forecast_id"]
    assert digest(result) == earlier


def test_pipeline_rejects_unknown_feature_version_before_first_capture(market_inputs):
    calendar, scope, _, _ = market_inputs
    with pytest.raises(LiveError):
        MarketResearchPipeline(calendar, scope, config=ForecastConfig(feature_version="TEST_FIXTURE_UNKNOWN_VERSION"))


def test_actual_uvicorn_default_runtime_serves_http_and_websocket_snapshot(tmp_path):
    """Real localhost TCP and HTTP upgrade; no TestClient substitutes here."""
    from wsproto import ConnectionType, WSConnection
    from wsproto.events import AcceptConnection, CloseConnection, Request, TextMessage
    root = tmp_path / "TEST_FIXTURE_REAL_UVICORN_RUN"
    (root / "snapshots").mkdir(parents=True)
    record = {"schema_version":"LIVE_REPLAY_STATE_V2","as_of":clock(9,45).isoformat(),
        "pit_status":"TEST_FIXTURE","scope":"DECLARED_SCOPE_ONLY","received_bars":[],
        "state":{"quality":"INVALID","warnings":["TEST_FIXTURE_NO_DATA"]},
        "forecasts":{},"forecast_status":"DATA_QUALITY_LIMITED"}
    record["snapshot_id"] = digest(record)
    (root / "snapshots" / "0945.json").write_text(canonical_json(record))
    before = {p:p.read_bytes() for p in root.rglob("*") if p.is_file()}
    module = tmp_path / "TEST_FIXTURE_socket_app.py"
    module.write_text("from stockfish_live.api.app import create_app\napp = create_app("+repr(str(root))+")\n")
    logs = tmp_path / "TEST_FIXTURE_UVICORN.log"
    process = None
    try:
        with logs.open("w") as output:
            process = subprocess.Popen([sys.executable,"-m","uvicorn","TEST_FIXTURE_socket_app:app",
                "--app-dir",str(tmp_path),"--host","127.0.0.1","--port","0","--log-level","info","--no-access-log"],
                stdout=output,stderr=subprocess.STDOUT,cwd=tmp_path)
        deadline = monotonic()+15
        while True:
            assert process.poll() is None,logs.read_text()
            address = re.search(r"Uvicorn running on http://127\.0\.0\.1:(\d+)",logs.read_text())
            if address is not None:
                port = int(address.group(1))
                break
            assert monotonic()<deadline,logs.read_text()
            sleep(.05)
        while True:
            assert process.poll() is None,logs.read_text()
            try:
                with urlopen(f"http://127.0.0.1:{port}/",timeout=.5) as response:
                    assert response.status == 200 and b"TEST_FIXTURE" in response.read()
                break
            except (URLError,TimeoutError):
                assert monotonic()<deadline,logs.read_text()
                sleep(.05)
        protocol = WSConnection(ConnectionType.CLIENT)
        with socket.create_connection(("127.0.0.1",port),timeout=5) as network:
            network.sendall(protocol.send(Request(host=f"127.0.0.1:{port}",target="/ws/live")))
            accepted, message, result = False,"",None
            while result is None:
                data = network.recv(65536)
                assert data,logs.read_text()
                protocol.receive_data(data)
                for event in protocol.events():
                    if isinstance(event,AcceptConnection):
                        accepted = True
                    if isinstance(event,TextMessage):
                        assert accepted,"snapshot arrived before WebSocket HTTP 101 upgrade"
                        message += event.data
                        if event.message_finished:
                            result = json.loads(message)
            assert accepted and result["type"] == "snapshot"
            assert result["pit_status"] == "TEST_FIXTURE"
            assert result["snapshot_id"] == record["snapshot_id"]
            assert result["data_quality"]["quality"] == "INVALID"
            network.sendall(protocol.send(CloseConnection(code=1000,reason="TEST_FIXTURE_COMPLETE")))
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    assert "No supported WebSocket library detected" not in logs.read_text()
    assert {p:p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
