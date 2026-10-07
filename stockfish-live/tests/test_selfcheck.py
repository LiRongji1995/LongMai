"""Independent TEST_FIXTURE regressions found during the Phase 1 self-audit.

These tests use a synthetic explicit session and isolated output paths. They do
not establish real-minute coverage or investment validity.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from stockfish_live.calendar import TradingCalendar
from stockfish_live.contracts.market import DatasetScope, Instrument, MarketSnapshot
from stockfish_live.pit import ReplayClock
from stockfish_live.providers.replay import ReplayProvider
from stockfish_live.replay.engine import ReplayEngine


SH = ZoneInfo("Asia/Shanghai")
DAY = date(2026, 10, 9)


def _fixture_environment():
    before_open = datetime(2026, 10, 9, 9, 0, tzinfo=SH)
    calendar = TradingCalendar(schema_version="LIVE_CALENDAR_V1", sessions=(DAY,),
        coverage_start=DAY, coverage_end=DAY, exchanges=("SH",),
        source="TEST_FIXTURE_SELFCHECK_EXPLICIT_SESSION", available_at=before_open,
        pit_status="TEST_FIXTURE")
    scope = DatasetScope(calendar_version=calendar.version,
        instruments=(Instrument(instrument_id="600000.SH", asset_type="STOCK"),),
        available_at=before_open, pit_status="TEST_FIXTURE")
    clock = ReplayClock(datetime(2026, 10, 9, 9, 30, tzinfo=SH))
    return calendar, scope, clock


def test_cancelled_replay_retains_saved_snapshot_and_marks_incomplete(tmp_path):
    calendar, scope, clock = _fixture_environment()
    output = tmp_path / "TEST_FIXTURE_cancelled_replay"

    async def verify():
        blocked = asyncio.Event()
        release = asyncio.Event()

        class BlockingProvider:
            async def get_market_snapshot(self, as_of, *, since=None):
                if since is not None:
                    blocked.set()
                    await release.wait()
                return MarketSnapshot(as_of=as_of, data_cutoff=None,
                    provider="TEST_FIXTURE_CANCELLED_PROVIDER")

        engine = ReplayEngine(BlockingProvider(), clock, calendar, scope, output)
        run = asyncio.create_task(engine.run(DAY))
        await asyncio.wait_for(blocked.wait(), timeout=5.)
        saved = (output / "snapshots" / "0930.json").read_bytes()
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run
        assert (output / "snapshots" / "0930.json").read_bytes() == saved
        assert not (output / "summary.json").exists()
        assert (output / "FAILED").read_text() == "REPLAY_INCOMPLETE\n"

    asyncio.run(verify())


def test_summary_persistence_failure_marks_replay_incomplete(tmp_path, monkeypatch):
    calendar, scope, clock = _fixture_environment()
    output = tmp_path / "TEST_FIXTURE_summary_write_failure"
    provider = ReplayProvider((), {}, clock)
    original_open = Path.open

    def fail_summary(path, *args, **kwargs):
        if path == output / "summary.json":
            raise OSError("TEST_FIXTURE_SUMMARY_PERSISTENCE_FAILURE")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_summary)
    engine = ReplayEngine(provider, clock, calendar, scope, output)
    with pytest.raises(OSError, match="TEST_FIXTURE_SUMMARY_PERSISTENCE_FAILURE"):
        asyncio.run(engine.run(DAY))
    assert len(tuple((output / "snapshots").glob("*.json"))) == 242
    assert not (output / "summary.json").exists()
    assert (output / "FAILED").read_text() == "REPLAY_INCOMPLETE\n"
