"""TEST_FIXTURE integration at market/forecast/collector/live boundaries."""
import asyncio
from datetime import timedelta
import json

import pytest

from stockfish_live.common import LiveError, canonical_json
from stockfish_live.forecast.pipeline import MarketResearchPipeline
from stockfish_live.ingest.live import run_live
from stockfish_live.providers.http_feed import HttpFeedProvider, HttpResponse
from stockfish_live.storage.parquet_store import ParquetStore
from stockfish_live.storage.research_store import ResearchStore
from test_market_state import market_inputs, context_bundle, clock


def test_pipeline_schedule_unknown_and_append_only_artifacts(tmp_path, market_inputs):
    calendar, scope, bars, receipts = market_inputs
    pipeline = MarketResearchPipeline(calendar, scope, context=context_bundle())
    quality = {"quality": "VALID", "warnings": [], "gap_count": 0}
    first = pipeline.process(clock(9, 40), [b for b in bars if b.timestamp <= clock(9, 40)], receipts, quality)
    assert first["forecast_status"] == "WAITING_FOR_DECISION"
    assert first["forecasts"] == {}
    decision = pipeline.process(clock(9, 45), [b for b in bars if b.timestamp <= clock(9, 45)], receipts, quality)
    assert len(decision["forecasts"]) == 5
    assert decision["forecast_status"] == "INSUFFICIENT_HISTORY"
    assert all(f["status"] == "INSUFFICIENT" for f in decision["forecasts"].values())
    assert decision["market_state"]["sentiment"]["next_state_probabilities"] is None
    prior = canonical_json(decision)
    later = pipeline.process(clock(9, 50), [b for b in bars if b.timestamp <= clock(9, 50)], receipts, quality)
    assert canonical_json(decision) == prior  # later revisions never rewrite an earlier snapshot
    assert later["forecasts"]["000001.SH"]["forecast_id"] != decision["forecasts"]["000001.SH"]["forecast_id"]
    pipeline.persist(ResearchStore(tmp_path))
    assert len(ResearchStore(tmp_path).visible("forecasts", cutoff=clock(9, 50), now=clock(9, 50))) == 10


def test_actual_capture_seconds_are_preserved(market_inputs):
    calendar, scope, bars, receipts = market_inputs
    now = clock(9, 45) + timedelta(seconds=3)
    pipeline = MarketResearchPipeline(calendar, scope)
    result = pipeline.process(now, [b for b in bars if b.timestamp <= now], receipts, {"quality": "VALID", "warnings": []})
    forecast = result["forecasts"]["000001.SH"]
    assert forecast["decision_at"] == clock(9, 45).isoformat()
    assert forecast["as_of"] == now.isoformat()
    assert pipeline.feature_states[0].available_at == now


def test_gap_and_invalid_cycle_cannot_appear_healthy(market_inputs):
    calendar, scope, bars, receipts = market_inputs
    pipeline = MarketResearchPipeline(calendar, scope)
    result = pipeline.process(clock(9, 45), [b for b in bars if b.timestamp <= clock(9, 45)], receipts,
        {"quality": "INVALID", "warnings": ["DATA_GAP"], "gap_count": 1})
    assert result["forecasts"] == {}
    assert result["forecast_status"] == "DATA_QUALITY_LIMITED"
    assert any(w["code"] == "DATA_GAP" for w in result["warnings"])


def test_live_shared_collector_capture_and_saved_actual_clock(tmp_path, market_inputs):
    calendar, scope, bars, _ = market_inputs
    root, output = tmp_path / "TEST_FIXTURE_DATA", tmp_path / "TEST_FIXTURE_RUN"
    root.mkdir()
    (root / "calendar.json").write_text(canonical_json(calendar))
    (root / "dataset.json").write_text(canonical_json(scope))
    class Wall:
        now = clock(9, 45) + timedelta(milliseconds=100)
        async def sleep(self, seconds):
            self.now += timedelta(seconds=seconds)
    wall = Wall()
    async def transport(_url, _timeout):
        event = wall.now.replace(second=0, microsecond=0)
        rows = [{key: value for key, value in b.model_dump(mode="json").items() if key in (
            "instrument_id", "exchange", "symbol", "asset_type", "timestamp", "open", "high", "low", "close", "volume", "turnover", "quality")}
            for b in bars if b.timestamp == event]
        raw = canonical_json({"schema_version": "LIVE_HTTP_FEED_V1", "provider": "TEST_FIXTURE_FEED",
            "timestamp_label": "END", "volume_unit": "SHARE", "turnover_unit": "CNY", "price_adjustment": "RAW",
            "state": "AVAILABLE", "published_at": event.isoformat(), "bars": rows}).encode()
        wall.now += timedelta(seconds=2)
        return HttpResponse(body=raw)
    feed = HttpFeedProvider("https://feed.example.test", scope, provider_name="TEST_FIXTURE_FEED", fixture_mode=True,
        now_fn=lambda: wall.now, calendar=calendar, transport=transport)
    result, code = asyncio.run(run_live(root, output=output, max_ticks=2, provider=feed,
        now_fn=lambda: wall.now, sleeper=wall.sleep, _test_fixture=True))
    assert code == 0
    assert result["results"][0]["ticks"] == 2
    assert result["results"][0]["pit_status"] == "TEST_FIXTURE"
    snapshot = json.loads((output / "snapshots" / "0945.json").read_text())
    assert snapshot["schema_version"] == "LIVE_REPLAY_STATE_V2"
    assert snapshot["as_of"] == (clock(9, 45) + timedelta(seconds=2, milliseconds=100)).isoformat()
    saved = ParquetStore(root).read_day(calendar.sessions[0])
    assert len(saved) == 2 * len(scope.instruments)
    assert saved[0].available_at > saved[0].timestamp
    assert not (output / "FAILED").exists()


def test_real_live_command_cannot_select_fixture_fallback(tmp_path, market_inputs):
    calendar, scope, _, _ = market_inputs
    (tmp_path / "calendar.json").write_text(canonical_json(calendar))
    (tmp_path / "dataset.json").write_text(canonical_json(scope))
    (tmp_path / "provider.json").write_text("{}")
    with pytest.raises(LiveError, match="TEST_FIXTURE_LIVE_DENIED"):
        asyncio.run(run_live(tmp_path, now_fn=lambda: clock(9, 45)))
    assert not (tmp_path / "live-runs").exists()
