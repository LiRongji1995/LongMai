"""TEST_FIXTURE: independent replay clock, provenance and failure boundaries.

All prices and sources below are synthetic engineering inputs held in memory.
No historical price, calendar coverage or predictive validity is established.
"""
from __future__ import annotations

import ast
import asyncio
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from stockfish_live.calendar import TradingCalendar
from stockfish_live.common import LiveError
from stockfish_live.contracts.market import DatasetScope, Instrument, MarketSnapshot, MinuteBar, SourceReceipt
from stockfish_live.ingest.collector import Collector
from stockfish_live.pit import PITGuard, ReplayClock
from stockfish_live.providers.replay import ReplayProvider
from stockfish_live.replay.engine import ReplayEngine

SH = ZoneInfo("Asia/Shanghai")
DAY = date(2026, 10, 9)
IDENTITY = "600000.SH"


def at(hour=9, minute=45, *, day=DAY):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=SH)


def environment():
    calendar = TradingCalendar(schema_version="LIVE_CALENDAR_V1", sessions=(DAY,),
        coverage_start=DAY, coverage_end=DAY, exchanges=("SH",),
        source="TEST_FIXTURE_EXPLICIT_SESSION", available_at=at(9, 0), pit_status="TEST_FIXTURE")
    scope = DatasetScope(calendar_version=calendar.version,
        instruments=(Instrument(instrument_id=IDENTITY, asset_type="STOCK"),),
        available_at=at(9, 0), pit_status="TEST_FIXTURE")
    return calendar, scope


def observation(event, *, available=None, provider="TEST_FIXTURE_REPLAY", pit_status="TEST_FIXTURE",
                source_time_basis="PUBLISHED"):
    available = available or event
    # Hash belongs to explicitly labelled synthetic raw bytes, not real data.
    raw = f"TEST_FIXTURE:{provider}:{event.isoformat()}:{available.isoformat()}".encode()
    source_id = hashlib.sha256(raw).hexdigest()
    receipt = SourceReceipt(source_id=source_id, provider=provider,
        source_timestamp=available, source_timestamp_basis=source_time_basis,
        received_at=available, available_at=available, pit_status=pit_status)
    bar = MinuteBar(instrument_id=IDENTITY, exchange="SH", symbol="600000", asset_type="STOCK",
        timestamp=event, open=10., high=10.2, low=9.8, close=10.1,
        volume=100., turnover=1010., provider=provider, source_timestamp=available,
        source_timestamp_basis=source_time_basis, available_at=available,
        source_id=source_id, quality="VALID", pit_status=pit_status)
    return bar, receipt


def memory_day(calendar, *, exclude=()):
    observations = [observation(event) for event in calendar.minutes(DAY) if event not in exclude]
    return tuple(bar for bar, _ in observations), {receipt.source_id: receipt for _, receipt in observations}


def test_0945_cannot_request_0946_by_any_provider_endpoint():
    bar, receipt = observation(at(minute=46))
    clock = ReplayClock(at())
    provider = ReplayProvider((bar,), {receipt.source_id: receipt}, clock)
    async def verify():
        for request in (
            provider.get_market_snapshot(at(minute=46)),
            provider.get_minute_bars(at(9, 31), at(minute=46), as_of=at()),
            provider.get_index_bars(at(9, 31), at(minute=46), as_of=at()),
            provider.get_minute_bars(at(9, 31), at(), as_of=at(minute=46)),
        ):
            with pytest.raises(LiveError, match="FUTURE_ACCESS_DENIED"):
                await request
        assert (await provider.get_market_snapshot(at())).bars == ()
    asyncio.run(verify())


@pytest.mark.parametrize("owner,field", [
    ("bar", "timestamp"), ("bar", "source_timestamp"), ("bar", "available_at"),
    ("receipt", "source_timestamp"), ("receipt", "received_at"), ("receipt", "available_at"),
])
def test_each_future_clock_is_hidden_and_guard_fails_closed(owner, field):
    bar, receipt = observation(at(minute=44))
    # Deliberately bypass validation to test each independent clock gate, even
    # when the adversarial change also makes the clock relationships invalid.
    if owner == "bar":
        bar = bar.model_copy(update={field: at(minute=46)})
    else:
        receipt = receipt.model_copy(update={field: at(minute=46)})
    receipts = {receipt.source_id: receipt}
    clock = ReplayClock(at())
    with pytest.raises(LiveError):
        PITGuard(clock).check(bar, receipts, at())
    snapshot = asyncio.run(ReplayProvider((bar,), receipts, clock).get_market_snapshot(at()))
    assert snapshot.bars == ()
    assert snapshot.receipts == ()
    assert snapshot.data_cutoff is None


def test_post_hoc_download_cannot_move_receipt_back_to_the_historical_event():
    event = at(day=date(2025, 6, 18))
    downloaded = at(day=date(2026, 10, 9))
    bar, receipt = observation(event, available=downloaded,
        provider="TEST_FIXTURE_RECONSTRUCTED_DOWNLOAD", pit_status="RECONSTRUCTED", source_time_basis="RECEIVED")
    clock = ReplayClock(event)
    provider = ReplayProvider((bar,), {receipt.source_id: receipt}, clock)
    assert asyncio.run(provider.get_market_snapshot(event)).bars == ()
    with pytest.raises(LiveError, match="FUTURE_ACCESS_DENIED"):
        PITGuard(clock).check(bar, {receipt.source_id: receipt}, event)
    clock.advance(downloaded)
    saved, = asyncio.run(provider.get_market_snapshot(downloaded)).bars
    assert saved.timestamp == event
    assert saved.available_at == downloaded
    assert saved.source_timestamp == downloaded
    assert saved.pit_status == "RECONSTRUCTED"
    assert receipt.received_at == downloaded


def test_timeout_does_not_advance_cursor_and_next_minute_can_recover():
    calendar, scope = environment()
    pairs = [observation(at(9, minute)) for minute in (31, 32)]
    bars = tuple(bar for bar, _ in pairs)
    receipts = {receipt.source_id: receipt for _, receipt in pairs}
    clock = ReplayClock(at(9, 31))
    delegate = ReplayProvider(bars, receipts, clock)
    class TimeoutOnce:
        def __init__(self):
            self.calls = []
        async def get_market_snapshot(self, as_of, *, since=None):
            self.calls.append((as_of, since))
            if len(self.calls) == 1:
                await asyncio.sleep(1.)
            return await delegate.get_market_snapshot(as_of, since=since)
    provider = TimeoutOnce()
    collector = Collector(provider, calendar, scope, timeout=.01)
    async def verify():
        failed, _ = await collector.step(at(9, 31))
        assert "PROVIDER_TIMEOUT" in failed["state"]["warnings"]
        assert failed["received_bars"] == []
        assert failed["state"]["bar_count"] == 0
        clock.advance(at(9, 32))
        recovered, _ = await collector.step(at(9, 32))
        assert provider.calls[1][1] is None
        assert len(recovered["received_bars"]) == 2
        assert recovered["state"]["bar_count"] == 2
        assert recovered["state"]["gap_count"] == 0
        assert recovered["state"]["quality"] == "VALID"
        assert "PROVIDER_TIMEOUT" not in recovered["state"]["warnings"]
    asyncio.run(verify())


def test_complete_one_day_replay_is_deterministic_with_240_bars_and_242_ticks(tmp_path):
    calendar, scope = environment()
    bars, receipts = memory_day(calendar)
    assert len(bars) == 240
    summaries = []
    for name in ("first", "second"):
        clock = ReplayClock(at(9, 30))
        engine = ReplayEngine(ReplayProvider(bars, receipts, clock), clock, calendar, scope, tmp_path / name)
        summaries.append(asyncio.run(engine.run(DAY, speed="max")))
    assert summaries[0]["snapshot_sequence_hash"] == summaries[1]["snapshot_sequence_hash"]
    for summary in summaries:
        assert summary["ticks"] == 242
        assert summary["completed_minutes"] == 240
        assert summary["final_state"]["bar_count"] == 240
        assert summary["final_state"]["gap_count"] == 0
        assert summary["final_state"]["quality"] == "VALID"
        assert summary["pit_status"] == "TEST_FIXTURE"
        assert summary["forecast_status"] == "NOT_IMPLEMENTED_PHASE1"
        assert summary["market_edge"] == "NOT_ESTABLISHED"
    first = sorted((tmp_path / "first" / "snapshots").glob("*.json"))
    second = sorted((tmp_path / "second" / "snapshots").glob("*.json"))
    assert len(first) == len(second) == 242
    assert [path.read_bytes() for path in first] == [path.read_bytes() for path in second]
    received = [row for path in first for row in json.loads(path.read_text())["received_bars"]]
    assert len(received) == 240
    assert len({row["timestamp"] for row in received}) == 240
    lunch = json.loads((tmp_path / "first" / "snapshots" / "1300.json").read_text())
    assert lunch["received_bars"] == []
    assert lunch["state"]["bar_count"] == 120
    assert lunch["state"]["gap_count"] == 0


def test_missing_0938_is_a_gap_and_is_never_fabricated(tmp_path):
    calendar, scope = environment()
    bars, receipts = memory_day(calendar, exclude=(at(9, 38),))
    clock = ReplayClock(at(9, 30))
    output = tmp_path / "gapped"
    engine = ReplayEngine(ReplayProvider(bars, receipts, clock), clock, calendar, scope, output)
    summary = asyncio.run(engine.run(DAY))
    assert summary["final_state"]["bar_count"] == 239
    assert summary["final_state"]["gap_count"] == 1
    assert "DATA_GAP" in summary["final_state"]["warnings"]
    assert summary["final_state"]["missing_examples"] == [{"instrument_id": IDENTITY,
                                                            "timestamp": at(9, 38).isoformat()}]
    missing = json.loads((output / "snapshots" / "0938.json").read_text())
    assert missing["received_bars"] == []
    assert "DATA_GAP" in missing["state"]["warnings"]
    actual = [row for path in (output / "snapshots").glob("*.json")
              for row in json.loads(path.read_text())["received_bars"]]
    assert at(9, 38).isoformat() not in {row["timestamp"] for row in actual}


@pytest.mark.parametrize("existing_kind", ["directory", "file"])
def test_existing_output_is_refused_and_preserved(tmp_path, existing_kind):
    calendar, scope = environment()
    output = tmp_path / "existing"
    if existing_kind == "directory":
        output.mkdir()
        marker = output / "user-file.txt"
    else:
        marker = output
    marker.write_bytes(b"TEST_FIXTURE_USER_CONTENT_DO_NOT_OVERWRITE")
    clock = ReplayClock(at(9, 30))
    engine = ReplayEngine(ReplayProvider((), {}, clock), clock, calendar, scope, output)
    with pytest.raises(LiveError, match="REPLAY_OUTPUT_EXISTS"):
        asyncio.run(engine.run(DAY))
    assert marker.read_bytes() == b"TEST_FIXTURE_USER_CONTENT_DO_NOT_OVERWRITE"
    if output.is_dir():
        assert tuple(output.iterdir()) == (marker,)


@pytest.mark.parametrize("contamination", ["future_bar", "future_parent", "unknown_parent"])
def test_collector_revalidates_adversarial_provider_model_copy(contamination):
    calendar, scope = environment()
    bar, receipt = observation(at())
    snapshot = MarketSnapshot(as_of=at(), data_cutoff=at(), provider="TEST_FIXTURE_UNTRUSTED",
                              bars=(bar,), receipts=(receipt,))
    if contamination == "future_bar":
        future, future_receipt = observation(at(minute=46))
        snapshot = snapshot.model_copy(update={"bars": (future,), "receipts": (future_receipt,)})
    elif contamination == "future_parent":
        snapshot = snapshot.model_copy(update={"receipts": (receipt.model_copy(update={"available_at": at(minute=46)}),)})
    else:
        snapshot = snapshot.model_copy(update={"receipts": ()})
    class UntrustedProvider:
        async def get_market_snapshot(self, *_args, **_kwargs):
            return snapshot
    record, _ = asyncio.run(Collector(UntrustedProvider(), calendar, scope).step(at()))
    assert record["state"]["quality"] == "INVALID"
    assert record["state"]["bar_count"] == 0
    assert record["received_bars"] == []
    assert set(record["state"]["warnings"]) & {"INVALID_PROVIDER_DATA", "FUTURE_ACCESS_DENIED", "SOURCE_RECEIPT_REQUIRED"}


def test_live_package_does_not_import_research_runtime():
    package = Path(__file__).resolve().parents[1] / "stockfish_live"
    forbidden = {"stockfish_core", "market_data", "flask"}
    violations = []
    for path in package.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            else:
                continue
            if any(name.split(".")[0] in forbidden for name in names):
                violations.append(f"{path.name}:{node.lineno}")
    assert violations == [], f"Research runtime dependency violates process isolation: {violations}"


@pytest.mark.parametrize("calendar_pit,bar_pit", [
    ("RESEARCH_ONLY", "VERIFIED_PIT"),
    ("VERIFIED_PIT", "RESEARCH_ONLY"),
    ("VERIFIED_PIT", "RECONSTRUCTED"),
])
def test_verified_scope_cannot_upgrade_weaker_calendar_or_observation(calendar_pit, bar_pit, tmp_path):
    fixture_calendar, fixture_scope = environment()
    calendar = fixture_calendar.model_copy(update={"pit_status": calendar_pit,
        "source": "TEST_FIXTURE_SEMANTIC_PROVENANCE_REGRESSION"})
    scope = fixture_scope.model_copy(update={"pit_status": "VERIFIED_PIT", "calendar_version": calendar.version})
    bar, receipt = observation(at(9, 31), provider="TEST_FIXTURE_NON_FIXTURE_STATUS_REGRESSION", pit_status=bar_pit)
    clock = ReplayClock(at(9, 31))
    record, _ = asyncio.run(Collector(ReplayProvider((bar,), {receipt.source_id: receipt}, clock),
                                    calendar, scope).step(at(9, 31)))
    expected = "RESEARCH_ONLY" if "RESEARCH_ONLY" in (calendar_pit, bar_pit) else "RECONSTRUCTED"
    assert record["declared_scope_pit_status"] == "VERIFIED_PIT"
    assert record["pit_status"] == expected
    assert record["received_bars"][0]["pit_status"] == bar_pit
    # Summary must derive its provenance from the accepted run, not scope alone.
    clock = ReplayClock(at(9, 30))
    summary = asyncio.run(ReplayEngine(ReplayProvider((bar,), {receipt.source_id: receipt}, clock),
        clock, calendar, scope, tmp_path / "weak-provenance").run(DAY))
    assert summary["pit_status"] == expected


@pytest.mark.parametrize("failure,expected_code", [
    ("sdk_exception", "PROVIDER_FAILURE"), ("none", "INVALID_PROVIDER_DATA"),
    ("object", "INVALID_PROVIDER_DATA"),
])
def test_sdk_exception_and_malformed_provider_results_recover_next_minute(failure, expected_code):
    calendar, scope = environment()
    pairs = [observation(at(9, minute)) for minute in (31, 32)]
    bars = tuple(bar for bar, _ in pairs)
    receipts = {receipt.source_id: receipt for _, receipt in pairs}
    clock = ReplayClock(at(9, 31))
    delegate = ReplayProvider(bars, receipts, clock)
    class FailOnce:
        def __init__(self):
            self.calls = []
        async def get_market_snapshot(self, as_of, *, since=None):
            self.calls.append((as_of, since))
            if len(self.calls) == 1:
                if failure == "sdk_exception":
                    raise RuntimeError("TEST_FIXTURE_SECRET_IN_SDK_ERROR_MUST_NOT_ESCAPE")
                return None if failure == "none" else object()
            return await delegate.get_market_snapshot(as_of, since=since)
    provider = FailOnce()
    collector = Collector(provider, calendar, scope)
    async def verify():
        failed, _ = await collector.step(at(9, 31))
        assert failed["cycle_error"] == expected_code
        assert expected_code in failed["state"]["warnings"]
        assert failed["state"]["bar_count"] == 0
        assert failed["received_bars"] == []
        assert "TEST_FIXTURE_SECRET_IN_SDK_ERROR" not in json.dumps(failed)
        clock.advance(at(9, 32))
        recovered, _ = await collector.step(at(9, 32))
        assert provider.calls[1][1] is None
        assert recovered["cycle_error"] is None
        assert len(recovered["received_bars"]) == 2
        assert recovered["state"]["bar_count"] == 2
        assert recovered["state"]["gap_count"] == 0
        assert recovered["state"]["quality"] == "VALID"
        assert expected_code not in recovered["state"]["warnings"]
    asyncio.run(verify())


def test_invalid_provider_snapshot_is_rejected_and_its_diagnostics_are_retained():
    calendar, scope = environment()
    bar, receipt = observation(at(9, 31))
    class InvalidSnapshotProvider:
        async def get_market_snapshot(self, *_args, **_kwargs):
            return MarketSnapshot(as_of=at(9, 31), data_cutoff=at(9, 31), provider="TEST_FIXTURE_INVALID",
                bars=(bar,), receipts=(receipt,), quality="INVALID", warnings=("TEST_FIXTURE_VENDOR_INVALID",))
    record, _ = asyncio.run(Collector(InvalidSnapshotProvider(), calendar, scope).step(at(9, 31)))
    assert record["state"]["quality"] == "INVALID"
    assert record["cycle_error"] == "INVALID_PROVIDER_DATA"
    assert record["state"]["bar_count"] == 0
    assert record["received_bars"] == []
    assert "TEST_FIXTURE_VENDOR_INVALID" in record["state"]["warnings"]


@pytest.mark.parametrize("quality,warning", [("GAPPED", "DATA_GAP"), ("STALE", "DATA_STALE")])
def test_empty_provider_snapshot_quality_and_warnings_are_propagated(quality, warning):
    calendar, scope = environment()
    morning = [observation(event) for event in calendar.minutes(DAY)[:120]]
    class QualityProvider:
        async def get_market_snapshot(self, as_of, *, since=None):
            if since is None:
                return MarketSnapshot(as_of=as_of, data_cutoff=at(11, 30), provider="TEST_FIXTURE_QUALITY",
                    bars=tuple(bar for bar, _ in morning), receipts=tuple(receipt for _, receipt in morning),
                    quality="VALID")
            return MarketSnapshot(as_of=as_of, data_cutoff=None, provider="TEST_FIXTURE_QUALITY",
                quality=quality, warnings=("TEST_FIXTURE_VENDOR_DIAGNOSTIC",))
    collector = Collector(QualityProvider(), calendar, scope)
    async def verify():
        accepted, _ = await collector.step(at(11, 30))
        assert accepted["state"]["quality"] == "VALID"
        record, _ = await collector.step(at(13, 0))
        assert record["state"]["bar_count"] == 120
        assert record["state"]["gap_count"] == 0
        assert record["state"]["quality"] == quality
        assert warning in record["state"]["warnings"]
        assert "TEST_FIXTURE_VENDOR_DIAGNOSTIC" in record["state"]["warnings"]
        assert record["received_bars"] == []
    asyncio.run(verify())


def test_gapped_snapshot_quality_survives_even_with_complete_valid_arrivals():
    calendar, scope = environment()
    bar, receipt = observation(at(9, 31))
    class GappedProvider:
        async def get_market_snapshot(self, as_of, *, since=None):
            return MarketSnapshot(as_of=as_of, data_cutoff=as_of, provider="TEST_FIXTURE_GAPPED",
                bars=(bar,), receipts=(receipt,), quality="GAPPED", warnings=("TEST_FIXTURE_VENDOR_GAP",))
    record, _ = asyncio.run(Collector(GappedProvider(), calendar, scope).step(at(9, 31)))
    assert record["state"]["bar_count"] == 1
    assert record["state"]["gap_count"] == 0
    assert record["state"]["quality"] == "GAPPED"
    assert "DATA_GAP" in record["state"]["warnings"]
    assert "TEST_FIXTURE_VENDOR_GAP" in record["state"]["warnings"]
