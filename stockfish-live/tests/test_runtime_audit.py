"""Independent runtime audit regressions with isolated TEST_FIXTURE storage."""
from argparse import Namespace
import asyncio
from datetime import date, datetime, timedelta
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread
from time import monotonic_ns
from zoneinfo import ZoneInfo

import pytest

from stockfish_live.calendar import TradingCalendar
from stockfish_live.cli import evaluate
from stockfish_live.common import LiveError, canonical_json
from stockfish_live.contracts.forecast import HistoricalState, MatureOutcome
from stockfish_live.contracts.context import ContextBundle
from stockfish_live.contracts.market import DatasetScope, Instrument, SourceReceipt
from stockfish_live.runtime import load_pipeline
from stockfish_live.ingest.collector import Collector
from stockfish_live.providers.http_feed import HttpFeedProvider, HttpResponse
from stockfish_live.storage.research_store import ResearchStore
from stockfish_live.storage.context_store import ContextStore

TZ = ZoneInfo("Asia/Shanghai")
DAY = date(2026, 9, 30)


def at(day, hour=9, minute=45):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


def inventory(root):
    calendar = TradingCalendar(schema_version="LIVE_CALENDAR_V1", sessions=(DAY - timedelta(days=1), DAY),
        coverage_start=DAY - timedelta(days=1), coverage_end=DAY, exchanges=("SH",),
        source="TEST_FIXTURE_RUNTIME_AUDIT_NOT_OFFICIAL", available_at=at(DAY - timedelta(days=2), 9),
        pit_status="TEST_FIXTURE")
    scope = DatasetScope(calendar_version=calendar.version, available_at=calendar.available_at,
        instruments=(Instrument(instrument_id="000001.SH", asset_type="INDEX"),), pit_status="TEST_FIXTURE")
    root.mkdir(parents=True)
    (root / "calendar.json").write_text(canonical_json(calendar))
    (root / "dataset.json").write_text(canonical_json(scope))
    return calendar, scope


def state(*, day=DAY, price=100., identity="TEST_FIXTURE_STATE_AUDIT"):
    return HistoricalState(state_id=identity, instrument_id="000001.SH", decision_at=at(day),
        available_at=at(day), current_price=price, features={"return_pct": 1.},
        pit_status="TEST_FIXTURE", context={"fixture": True})


def test_evaluate_rejects_conflicting_state_identity_across_archive_and_run(tmp_path):
    root, run = tmp_path / "TEST_FIXTURE_ARCHIVE", tmp_path / "TEST_FIXTURE_RUN"
    inventory(root)
    ResearchStore(root).append("features", [state(price=100.)])
    ResearchStore(run).append("features", [state(price=101.)])
    args = Namespace(as_of=at(DAY, 16), run_dir=run, all_decision_times=False,
        decision_times="09:45", min_train_sessions=1, no_tune=True)
    # Both stores independently pass integrity. Their conflicting logical ID
    # must fail at the union rather than letting the run silently replace it.
    with pytest.raises(LiveError, match="STATE_ID_CONFLICT"):
        evaluate(args, root)


def test_runtime_rejects_outcome_whose_declared_parent_raw_is_missing(tmp_path):
    root = tmp_path / "TEST_FIXTURE_MISSING_LABEL_PARENT"
    calendar, scope = inventory(root)
    historical = state(day=DAY - timedelta(days=1))
    label = MatureOutcome(state_id=historical.state_id, instrument_id=historical.instrument_id,
        decision_at=historical.decision_at, horizon="15:00", target_at=at(historical.decision_at.date(), 15, 0),
        available_at=at(historical.decision_at.date(), 15, 0), anchor_price=100., target_price=101.,
        pit_status="TEST_FIXTURE", source_ids=("f" * 64,))
    store = ResearchStore(root)
    store.append("features", [historical])
    store.append("outcomes", [label])
    with pytest.raises(LiveError, match="STORAGE_INTEGRITY_ERROR|SOURCE_RECEIPT_MISSING"):
        load_pipeline(root, calendar, scope, DAY)


def test_runtime_rejects_label_availability_before_its_actual_parent_receipt(tmp_path):
    root = tmp_path / "TEST_FIXTURE_LATE_LABEL_PARENT"
    calendar, scope = inventory(root)
    historical = state(day=DAY - timedelta(days=1))
    raw = b'TEST_FIXTURE actual late source capture for previous session outcome'
    source_id = hashlib.sha256(raw).hexdigest()
    parent = SourceReceipt(source_id=source_id, provider="mock", source_timestamp=at(historical.decision_at.date(), 15, 0),
        source_timestamp_basis="PUBLISHED", received_at=at(DAY, 9, 0), available_at=at(DAY, 9, 0),
        pit_status="TEST_FIXTURE")
    ContextStore(root).save(ContextBundle(receipts=(parent,)), {source_id: raw})
    label = MatureOutcome(state_id=historical.state_id, instrument_id=historical.instrument_id,
        decision_at=historical.decision_at, horizon="15:00", target_at=at(historical.decision_at.date(), 15, 0),
        available_at=at(historical.decision_at.date(), 15, 0), anchor_price=100., target_price=101.,
        pit_status="TEST_FIXTURE", source_ids=(source_id,))
    store = ResearchStore(root)
    store.append("features", [historical])
    store.append("outcomes", [label])
    with pytest.raises(LiveError, match="OUTCOME_PARENT|SOURCE|STORAGE_INTEGRITY_ERROR"):
        load_pipeline(root, calendar, scope, DAY)


def test_research_writer_cache_rechecks_modified_prior_partition(tmp_path):
    store = ResearchStore(tmp_path / "TEST_FIXTURE_TAMPERED_RESEARCH")
    path = store.append("features", [state(identity="TEST_FIXTURE_PRIOR")])
    raw = bytearray(path.read_bytes())
    raw[len(raw) // 2] ^= 1
    path.write_bytes(raw)
    with pytest.raises(LiveError, match="STORAGE_INTEGRITY_ERROR"):
        store.append("features", [state(identity="TEST_FIXTURE_NEW")])


def test_research_visibility_does_not_leak_unmatured_actual(tmp_path):
    historical = state(day=DAY - timedelta(days=1))
    label = MatureOutcome(state_id=historical.state_id, instrument_id=historical.instrument_id,
        decision_at=historical.decision_at, horizon="15:00", target_at=at(historical.decision_at.date(), 15, 0),
        available_at=at(DAY, 11, 0), anchor_price=100., target_price=1000., pit_status="TEST_FIXTURE")
    store = ResearchStore(tmp_path / "TEST_FIXTURE_FUTURE_ACTUAL")
    store.append("outcomes", [label])
    assert store.visible("outcomes", cutoff=at(DAY, 10, 0), now=at(DAY, 10, 0)) == ()
    assert store.visible("outcomes", cutoff=at(DAY, 11, 0), now=at(DAY, 11, 0)) == (label,)


def test_empty_feed_state_is_not_visible_before_its_actual_parent_availability(tmp_path):
    calendar, scope = inventory(tmp_path / "TEST_FIXTURE_EMPTY_CLOCK")
    nominal = at(DAY, 9, 31)
    class Clock:
        now = nominal
    clock = Clock()
    raw = canonical_json({"schema_version": "LIVE_HTTP_FEED_V1", "provider": "http_feed",
        "timestamp_label": "END", "volume_unit": "SHARE", "turnover_unit": "CNY", "price_adjustment": "RAW",
        "state": "EMPTY", "published_at": nominal, "bars": []}).encode()
    async def transport(_url, _timeout):
        clock.now = nominal + timedelta(milliseconds=200)
        return HttpResponse(body=raw)
    feed = HttpFeedProvider("http://127.0.0.1/TEST_FIXTURE", scope, calendar=calendar, fixture_mode=True,
        clock=clock, transport=transport)
    async def scenario():
        receipt = await feed.capture()
        before = await feed.get_market_snapshot(nominal)
        assert "FEED_DECLARED_EMPTY" not in before.warnings
        after = await feed.get_market_snapshot(receipt.available_at)
        assert "FEED_DECLARED_EMPTY" in after.warnings
        assert receipt in after.receipts  # empty-response fact retains its parent lineage
    asyncio.run(scenario())


def test_future_available_response_does_not_hide_earlier_visible_empty_fact(tmp_path):
    calendar, scope = inventory(tmp_path / "TEST_FIXTURE_EMPTY_HISTORY")
    nominal = at(DAY, 9, 31)
    class Clock:
        now = nominal
    clock = Clock()
    empty = {"schema_version": "LIVE_HTTP_FEED_V1", "provider": "http_feed", "timestamp_label": "END",
        "volume_unit": "SHARE", "turnover_unit": "CNY", "price_adjustment": "RAW",
        "state": "EMPTY", "published_at": nominal, "bars": []}
    later = empty | {"state": "AVAILABLE", "published_at": nominal + timedelta(minutes=1),
        "bars": [{"instrument_id": "000001.SH", "exchange": "SH", "symbol": "000001", "asset_type": "INDEX",
            "timestamp": nominal + timedelta(minutes=1), "open": 3000., "high": 3001., "low": 2999.,
            "close": 3000., "volume": 1., "turnover": 3000., "quality": "VALID"}]}
    calls = 0
    async def transport(_url, _timeout):
        nonlocal calls
        value = empty if calls == 0 else later
        clock.now = nominal + timedelta(minutes=calls, milliseconds=200)
        calls += 1
        return HttpResponse(body=canonical_json(value).encode())
    feed = HttpFeedProvider("http://127.0.0.1/TEST_FIXTURE", scope, calendar=calendar, fixture_mode=True,
        clock=clock, transport=transport)
    async def scenario():
        original = await feed.capture()
        assert "FEED_DECLARED_EMPTY" in (await feed.get_market_snapshot(original.available_at)).warnings
        await feed.capture()
        past = await feed.get_market_snapshot(original.available_at)
        assert "FEED_DECLARED_EMPTY" in past.warnings
        assert original in past.receipts
        assert past.bars == ()
    asyncio.run(scenario())


def test_default_http_transport_real_localhost_socket_and_collector_actual_cutoff(tmp_path):
    """Real HTTP socket, fixture business clock plus actual elapsed wall time.

    This is intentionally TEST_FIXTURE: the simulated 09:31 session anchor is
    needed for an offline test at arbitrary actual time, including midnight.
    No custom transport replaces urllib and no external host is contacted.
    """
    today = datetime.now(ZoneInfo("Europe/London")).date()
    anchor = at(today, 9, 31)
    started = monotonic_ns()
    def now():
        return anchor + timedelta(microseconds=(monotonic_ns() - started) // 1000)
    payload = canonical_json({"schema_version": "LIVE_HTTP_FEED_V1", "provider": "http_feed",
        "timestamp_label": "END", "volume_unit": "SHARE", "turnover_unit": "CNY", "price_adjustment": "RAW",
        "state": "AVAILABLE", "published_at": anchor, "bars": [{"instrument_id": "000001.SH",
            "exchange": "SH", "symbol": "000001", "asset_type": "INDEX", "timestamp": anchor,
            "open": 3000., "high": 3002., "low": 2999., "close": 3001., "volume": 100.,
            "turnover": 300100., "quality": "VALID"}]}).encode()
    class Handler(BaseHTTPRequestHandler):
        mode = "AVAILABLE"
        requests = 0
        def do_GET(self):
            type(self).requests += 1
            if self.path != "/TEST_FIXTURE/minute":
                self.send_error(404)
                return
            if type(self).mode == "FAIL":
                self.send_error(503, "TEST_FIXTURE_UNAVAILABLE")
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        def log_message(self, _format, *_args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    calendar = TradingCalendar(schema_version="LIVE_CALENDAR_V1", sessions=(today,), coverage_start=today,
        coverage_end=today, exchanges=("SH",), source="TEST_FIXTURE_LOCALHOST_NOT_OFFICIAL_CALENDAR",
        available_at=at(today, 9, 0), pit_status="TEST_FIXTURE")
    scope = DatasetScope(calendar_version=calendar.version, available_at=at(today, 9, 0), pit_status="TEST_FIXTURE",
        instruments=(Instrument(instrument_id="000001.SH", asset_type="INDEX"),))
    url = f"http://127.0.0.1:{server.server_port}/TEST_FIXTURE/minute"
    provider = HttpFeedProvider(url, scope, calendar=calendar, fixture_mode=True, now_fn=now, timeout=2.)
    collector = Collector(provider, calendar, scope, timeout=2.)
    async def scenario():
        initial = await provider.get_market_snapshot(now())
        assert initial.bars == () and Handler.requests == 0
        receipt = await provider.capture()
        after_capture = now()
        snapshot = await provider.get_market_snapshot(after_capture)
        assert Handler.requests == 1  # cache read causes no second socket request
        assert snapshot.as_of == after_capture
        assert len(snapshot.bars) == 1
        assert anchor < receipt.received_at <= receipt.available_at <= after_capture
        assert receipt.pit_status == "TEST_FIXTURE"
        assert receipt.source_timestamp_basis == "RECEIVED"
        assert receipt.source_id == hashlib.sha256(payload).hexdigest()
        assert provider.source_records() == ((payload, receipt),)
        accepted, _ = await collector.step(now())
        assert accepted["cycle_error"] is None
        assert accepted["state"]["quality"] == "VALID"
        assert accepted["state"]["bar_count"] == 1
        assert datetime.fromisoformat(accepted["as_of"]) >= receipt.available_at
        Handler.mode = "FAIL"
        with pytest.raises(LiveError, match="FEED_HTTP_STATUS"):
            await provider.capture()
        failed, _ = await collector.step(now())
        assert failed["cycle_error"] == "FEED_HTTP_STATUS"
        assert failed["state"]["quality"] == "PARTIAL"
        assert failed["state"]["bar_count"] == 1
        assert failed["received_bars"] == []
        assert failed["input_hash"] == accepted["input_hash"]
        assert Handler.requests == 2
        assert provider.source_records() == ((payload, receipt),)
        return {"label": "TEST_FIXTURE", "transport": "DEFAULT_URLLIB_REAL_LOCALHOST_HTTP_SOCKET",
            "clock_basis": "CONTROLLED_SESSION_ANCHOR_PLUS_REAL_MONOTONIC_ELAPSED",
            "session_date": today.isoformat(), "actual_utc_at_test": datetime.now(ZoneInfo("UTC")).isoformat(),
            "received_at": receipt.received_at.isoformat(), "available_at": receipt.available_at.isoformat(),
            "post_capture_as_of": accepted["as_of"], "raw_response_sha256": receipt.source_id,
            "http_requests": Handler.requests, "cache_read_additional_requests": 0,
            "accepted_bars": 1, "initial_quality": accepted["state"]["quality"],
            "after_http_503_quality": failed["state"]["quality"], "cycle_error": failed["cycle_error"],
            "buffer_retained_after_failure": True, "external_network_used": False,
            "real_market_validation": "NOT_PERFORMED", "market_edge": "NOT_ESTABLISHED"}
    try:
        summary = asyncio.run(scenario())
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2.)
    path = tmp_path / "TEST_FIXTURE-localhost-http-summary.json"
    path.write_text(canonical_json(summary) + "\n")
    print("LOCALHOST_HTTP_ACCEPTANCE " + canonical_json({"summary_path": str(path), **summary}))
