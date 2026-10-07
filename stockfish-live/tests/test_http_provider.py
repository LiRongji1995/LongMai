"""Injected transport only. No test opens an exchange or external HTTP URL."""
import asyncio
import hashlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from stockfish_live.common import LiveError, canonical_json, digest
from stockfish_live.contracts.market import DatasetScope, Instrument
from stockfish_live.providers.http_feed import HttpFeedProvider, HttpResponse

TZ = ZoneInfo("Asia/Shanghai")


def at(hour, minute, second=0):
    return datetime(2026, 9, 30, hour, minute, second, tzinfo=TZ)


class Clock:
    now = at(9, 31)


class Transport:
    def __init__(self, clock, *responses):
        self.clock, self.responses, self.calls = clock, list(responses), []

    async def __call__(self, url, timeout):
        self.calls.append((url, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        self.clock.now += timedelta(milliseconds=200)
        return response


def scope(*, fixture=False):
    return DatasetScope(calendar_version=digest("TEST_FIXTURE_SCOPE"), available_at=at(9, 0),
        pit_status="TEST_FIXTURE" if fixture else "RESEARCH_ONLY",
        instruments=(Instrument(instrument_id="000001.SH", asset_type="INDEX"),
                     Instrument(instrument_id="600001.SH", asset_type="STOCK")))


def wire_bar(**changes):
    row = {"instrument_id": "000001.SH", "exchange": "SH", "symbol": "000001", "asset_type": "INDEX",
        "timestamp": at(9, 31).isoformat(), "open": 3000, "high": 3002, "low": 2999, "close": 3001,
        "volume": 100, "turnover": 300100, "quality": "VALID"}
    row.update(changes)
    return row


def envelope(*bars, **changes):
    payload = {"schema_version": "LIVE_HTTP_FEED_V1", "provider": "http_feed",
        "timestamp_label": "END", "volume_unit": "SHARE", "turnover_unit": "CNY", "price_adjustment": "RAW",
        "state": "AVAILABLE", "published_at": at(9, 31).isoformat(), "bars": list(bars or (wire_bar(),))}
    payload.update(changes)
    return payload


def response(payload):
    return HttpResponse(body=canonical_json(payload).encode())


def provider(*responses, fixture=False, trusted=False, clock=None, **changes):
    clock = clock or Clock()
    transport = Transport(clock, *responses)
    result = HttpFeedProvider("https://feed.example.test/minute", scope(fixture=fixture),
        transport=transport, clock=clock, fixture_mode=fixture,
        trusted_published_timestamps=trusted, **changes)
    return result, clock, transport


def fetch(instance, cutoff, since=None):
    return asyncio.run(instance.poll(cutoff, since=since))


def test_actual_receive_is_buffered_until_next_cutoff_and_hashed_from_response_bytes():
    raw = response(envelope())
    feed, clock, transport = provider(raw, raw)
    first = fetch(feed, at(9, 31))
    assert first.bars == first.receipts == ()
    assert first.quality == "PARTIAL"
    assert first.warnings == ("FEED_CAPTURE_PENDING_NEXT_CUTOFF",)
    [(stored_raw, receipt)] = feed.source_records()
    assert stored_raw == raw.body
    assert receipt.source_id == hashlib.sha256(raw.body).hexdigest()
    assert receipt.received_at == at(9, 31) + timedelta(milliseconds=200)
    assert receipt.available_at == receipt.received_at
    assert receipt.source_timestamp_basis == "RECEIVED"
    assert receipt.pit_status == "RESEARCH_ONLY"
    clock.now = at(9, 32)
    second = fetch(feed, at(9, 32), since=at(9, 31))
    assert second.as_of == at(9, 32)
    assert len(second.bars) == len(second.receipts) == 1
    assert second.bars[0].timestamp == at(9, 31)
    assert second.bars[0].available_at == receipt.available_at
    assert second.bars[0].source_id == receipt.source_id
    assert len(transport.calls) == 2
    assert len(feed.source_records()) == 1  # identical response never rebases first receipt


def test_default_market_snapshot_is_cache_only_and_capture_then_actual_cutoff_is_immediate():
    feed, clock, transport = provider(response(envelope()))
    initial = asyncio.run(feed.get_market_snapshot(at(9, 31)))
    assert initial.bars == ()
    assert initial.warnings == ("FEED_NOT_CAPTURED",)
    assert transport.calls == []
    receipt = asyncio.run(feed.capture())
    snapshot = asyncio.run(feed.get_market_snapshot(clock.now))
    assert len(snapshot.bars) == 1
    assert snapshot.as_of == receipt.available_at
    assert snapshot.bars[0].timestamp == at(9, 31)
    assert len(transport.calls) == 1


def test_capture_error_is_visible_to_collector_cache_reads_until_successful_capture():
    raw = response(envelope())
    feed, clock, _ = provider(raw, TimeoutError("TEST_FIXTURE timeout"), raw)
    asyncio.run(feed.capture())
    before = feed.source_records()
    clock.now = at(9, 32)
    with pytest.raises(LiveError, match="PROVIDER_TIMEOUT"):
        asyncio.run(feed.capture())
    for _ in range(2):
        with pytest.raises(LiveError, match="PROVIDER_TIMEOUT"):
            asyncio.run(feed.get_market_snapshot(clock.now))
    assert feed.source_records() == before
    asyncio.run(feed.capture())
    assert len(asyncio.run(feed.get_market_snapshot(clock.now)).bars) == 1


def test_capture_if_empty_is_explicit_and_only_fetches_before_initial_capture():
    feed, clock, transport = provider(response(envelope()), capture_if_empty=True)
    initial = asyncio.run(feed.get_market_snapshot(at(9, 31)))
    assert initial.bars == ()
    assert len(transport.calls) == 1
    clock.now = at(9, 32)
    assert len(asyncio.run(feed.get_market_snapshot(at(9, 32))).bars) == 1
    assert len(transport.calls) == 1


def test_actual_processing_availability_is_recorded_separately_from_received_time():
    times = iter((at(9, 31), at(9, 31), at(9, 31, 1), at(9, 31, 2), at(9, 31, 2)))

    async def transport(url, timeout):
        return response(envelope())

    feed = HttpFeedProvider("https://feed.example.test/minute", scope(), transport=transport, now_fn=lambda: next(times))
    fetch(feed, at(9, 31))
    receipt = feed.source_records()[0][1]
    assert receipt.received_at == at(9, 31, 1)
    assert receipt.available_at == at(9, 31, 2)


def test_trusted_publication_keeps_actual_local_availability():
    raw = response(envelope())
    feed, clock, _ = provider(raw, raw, trusted=True)
    fetch(feed, at(9, 31))
    receipt = feed.source_records()[0][1]
    assert receipt.source_timestamp == at(9, 31)
    assert receipt.source_timestamp_basis == "PUBLISHED"
    assert receipt.pit_status == "VERIFIED_PIT"
    assert receipt.available_at > at(9, 31)
    clock.now = at(9, 32)
    second = fetch(feed, at(9, 32), since=at(9, 31))
    assert second.bars[0].pit_status == "VERIFIED_PIT"


def test_fixture_transport_never_becomes_verified_real_data():
    feed, _, _ = provider(response(envelope()), fixture=True, trusted=True)
    fetch(feed, at(9, 31))
    assert feed.source_records()[0][1].pit_status == "TEST_FIXTURE"


def test_failure_keeps_pending_buffer_and_does_not_advance_any_archive_state():
    raw = response(envelope())
    feed, clock, _ = provider(raw, TimeoutError("TEST_FIXTURE timeout"), raw)
    fetch(feed, at(9, 31))
    before = feed.source_records()
    clock.now = at(9, 32)
    with pytest.raises(LiveError, match="PROVIDER_TIMEOUT"):
        fetch(feed, at(9, 32), since=at(9, 31))
    assert feed.source_records() == before
    clock.now = at(9, 33)
    result = fetch(feed, at(9, 33), since=at(9, 31))
    assert len(result.bars) == 1
    assert result.bars[0].available_at == before[0][1].available_at


def test_malformed_later_response_keeps_pending_buffer():
    raw = response(envelope())
    feed, clock, _ = provider(raw, HttpResponse(b"not json"), raw)
    fetch(feed, at(9, 31))
    before = feed.source_records()
    clock.now = at(9, 32)
    with pytest.raises(LiveError, match="FEED_SCHEMA_INVALID"):
        fetch(feed, at(9, 32), since=at(9, 31))
    assert feed.source_records() == before
    clock.now = at(9, 33)
    assert len(fetch(feed, at(9, 33), since=at(9, 31)).bars) == 1


@pytest.mark.parametrize("field,value", [
    ("timestamp_label", "START"), ("volume_unit", "LOT"), ("turnover_unit", "CNY_10K"),
    ("price_adjustment", "QFQ"), ("state", "UNKNOWN"), ("available_at", at(9, 30).isoformat()),
])
def test_ambiguous_units_unknown_state_or_server_local_clock_are_rejected(field, value):
    feed, _, _ = provider(response(envelope(**{field: value})))
    with pytest.raises(LiveError, match="FEED_SCHEMA_INVALID"):
        fetch(feed, at(9, 31))
    assert feed.source_records() == ()


@pytest.mark.parametrize("field", ["volume_unit", "turnover_unit", "timestamp_label", "published_at", "state"])
def test_required_feed_declaration_cannot_be_omitted(field):
    payload = envelope()
    payload.pop(field)
    feed, _, _ = provider(response(payload))
    with pytest.raises(LiveError, match="FEED_SCHEMA_INVALID"):
        fetch(feed, at(9, 31))


@pytest.mark.parametrize("row", [
    wire_bar(instrument_id="600099.SH", symbol="600099", asset_type="STOCK"),
    wire_bar(asset_type="STOCK"), wire_bar(instrument_id="sh000001"),
])
def test_identity_or_type_outside_scope_is_rejected(row):
    feed, _, _ = provider(response(envelope(row)))
    with pytest.raises(LiveError, match="FEED_SCOPE_MISMATCH"):
        fetch(feed, at(9, 31))


def test_mismatched_exchange_symbol_fails_minute_contract():
    feed, _, _ = provider(response(envelope(wire_bar(exchange="SZ"))))
    with pytest.raises(LiveError, match="FEED_MINUTE_CONTRACT_INVALID"):
        fetch(feed, at(9, 31))
    assert feed.source_records() == ()


def test_source_provider_mismatch_rejected():
    feed, _, _ = provider(response(envelope(provider="another_feed")))
    with pytest.raises(LiveError, match="FEED_PROVIDER_MISMATCH"):
        fetch(feed, at(9, 31))


def test_future_event_and_publication_are_rejected():
    feed, _, _ = provider(response(envelope(wire_bar(timestamp=at(9, 32).isoformat()), published_at=None)))
    with pytest.raises(LiveError, match="FEED_FUTURE_BAR"):
        fetch(feed, at(9, 31))
    feed, _, _ = provider(response(envelope(published_at=at(9, 32).isoformat())))
    with pytest.raises(LiveError, match="FEED_FUTURE_PUBLICATION"):
        fetch(feed, at(9, 31))


def test_declared_publication_before_completed_event_is_rejected_even_if_untrusted():
    feed, _, _ = provider(response(envelope(published_at=at(9, 30).isoformat())))
    with pytest.raises(LiveError, match="FEED_PUBLICATION_BEFORE_EVENT"):
        fetch(feed, at(9, 31))


def test_trusted_timestamp_requires_explicit_publication():
    feed, _, _ = provider(response(envelope(published_at=None)), trusted=True)
    with pytest.raises(LiveError, match="FEED_PUBLICATION_REQUIRED"):
        fetch(feed, at(9, 31))


def test_modified_retransmission_is_rejected_and_original_is_preserved():
    raw = response(envelope())
    modified = response(envelope(wire_bar(close=3002)))
    feed, clock, _ = provider(raw, modified)
    fetch(feed, at(9, 31))
    original = feed.source_records()
    clock.now = at(9, 32)
    with pytest.raises(LiveError, match="FEED_BAR_REVISION_DENIED"):
        fetch(feed, at(9, 32), since=at(9, 31))
    assert feed.source_records() == original


def test_repeated_bar_in_new_response_dedupes_and_only_new_bar_is_staged():
    first = response(envelope())
    second = response(envelope(wire_bar(), wire_bar(timestamp=at(9, 32).isoformat()), published_at=at(9, 32).isoformat()))
    feed, clock, _ = provider(first, second, second)
    fetch(feed, at(9, 31))
    clock.now = at(9, 32)
    result = fetch(feed, at(9, 32), since=at(9, 31))
    assert [bar.timestamp for bar in result.bars] == [at(9, 31)]
    clock.now = at(9, 33)
    result = fetch(feed, at(9, 33), since=at(9, 32))
    assert [bar.timestamp for bar in result.bars] == [at(9, 32)]
    assert len(feed.source_records()) == 2


def test_late_missing_bar_cannot_be_inserted_after_newer_bar():
    clock = Clock()
    clock.now = at(9, 33)
    feed, clock, _ = provider(response(envelope(wire_bar(timestamp=at(9, 33).isoformat()), published_at=at(9, 33).isoformat())),
        response(envelope(wire_bar(timestamp=at(9, 32).isoformat()), published_at=at(9, 34).isoformat())), clock=clock)
    fetch(feed, at(9, 33))
    clock.now = at(9, 34)
    with pytest.raises(LiveError, match="FEED_LATE_BAR_DENIED"):
        fetch(feed, at(9, 34), since=at(9, 33))


def test_duplicate_in_one_response_is_invalid_and_atomic():
    feed, _, _ = provider(response(envelope(wire_bar(), wire_bar())))
    with pytest.raises(LiveError, match="FEED_DUPLICATE_BAR"):
        fetch(feed, at(9, 31))
    assert feed.source_records() == ()


@pytest.mark.parametrize("minute", [at(9, 30), at(12, 1), at(13, 0)])
def test_invalid_minute_boundaries_are_rejected(minute):
    clock = Clock()
    clock.now = at(14, 0)
    feed, _, _ = provider(response(envelope(wire_bar(timestamp=minute.isoformat()), published_at=at(14, 0).isoformat())), clock=clock)
    with pytest.raises(LiveError, match="INVALID_BAR_TIME"):
        fetch(feed, at(14, 0))


@pytest.mark.parametrize("wire", [
    b'{"schema_version":"LIVE_HTTP_FEED_V1","schema_version":"LIVE_HTTP_FEED_V1"}',
    b'{"close":NaN}', b"\xff", b"[]",
])
def test_duplicate_json_keys_nonfinite_and_non_utf8_are_rejected(wire):
    feed, _, _ = provider(HttpResponse(body=wire))
    with pytest.raises(LiveError, match="FEED_SCHEMA_INVALID"):
        fetch(feed, at(9, 31))


def test_http_failure_and_non_json_content_are_rejected():
    feed, _, _ = provider(HttpResponse(body=b"", status=503))
    with pytest.raises(LiveError, match="FEED_HTTP_STATUS"):
        fetch(feed, at(9, 31))
    feed, _, _ = provider(HttpResponse(body=b"<html>", headers={"Content-Type": "text/html"}))
    with pytest.raises(LiveError, match="FEED_CONTENT_TYPE_INVALID"):
        fetch(feed, at(9, 31))


def test_explicit_empty_is_partial_and_missing_bars_is_not_implicitly_empty():
    feed, actual_clock, _ = provider(response(envelope(state="EMPTY", bars=[])))
    result = fetch(feed, at(9, 31))
    assert result.bars == ()
    assert result.quality == "PARTIAL"
    assert "FEED_DECLARED_EMPTY" not in result.warnings
    visible = asyncio.run(feed.get_market_snapshot(actual_clock.now))
    assert "FEED_DECLARED_EMPTY" in visible.warnings
    assert len(visible.receipts) == 1
    feed, _, _ = provider(response(envelope(state="AVAILABLE", bars=[])))
    with pytest.raises(LiveError, match="FEED_SCHEMA_INVALID"):
        fetch(feed, at(9, 31))


def test_empty_capture_keeps_visible_parent_evidence_and_hides_future_parent():
    feed, clock, _ = provider(response(envelope(state="EMPTY", bars=[])))
    asyncio.run(feed.capture())
    hidden = asyncio.run(feed.get_market_snapshot(at(9, 31)))
    assert hidden.receipts == ()
    assert "FEED_DECLARED_EMPTY" not in hidden.warnings
    visible = asyncio.run(feed.get_market_snapshot(clock.now))
    assert visible.bars == ()
    assert len(visible.receipts) == 1
    assert visible.data_cutoff == visible.receipts[0].available_at
    assert "FEED_DECLARED_EMPTY" in visible.warnings


def test_future_available_capture_does_not_hide_previously_visible_empty_state():
    empty = response(envelope(state="EMPTY", bars=[]))
    available = response(envelope(wire_bar(timestamp=at(9, 32).isoformat()), published_at=at(9, 32).isoformat()))
    feed, clock, _ = provider(empty, available)
    asyncio.run(feed.capture())
    empty_receipt = feed.source_records()[0][1]
    clock.now = at(9, 32)
    asyncio.run(feed.capture())
    previous = asyncio.run(feed.get_market_snapshot(at(9, 32)))
    assert previous.bars == ()
    assert "FEED_DECLARED_EMPTY" in previous.warnings
    assert [parent.source_id for parent in previous.receipts] == [empty_receipt.source_id]
    current = asyncio.run(feed.get_market_snapshot(clock.now))
    assert len(current.bars) == 1
    assert "FEED_DECLARED_EMPTY" not in current.warnings


def test_new_payload_retransmission_without_new_bars_has_its_own_visible_parent():
    first = response(envelope())
    new_bytes = response(envelope(published_at=at(9, 32).isoformat()))
    feed, clock, _ = provider(first, new_bytes)
    asyncio.run(feed.capture())
    clock.now = at(9, 32)
    asyncio.run(feed.capture())
    visible = asyncio.run(feed.get_market_snapshot(clock.now, since=at(9, 32)))
    assert visible.bars == ()
    assert len(visible.receipts) == 1
    assert visible.receipts[0].source_id == hashlib.sha256(new_bytes.body).hexdigest()


def test_cached_queries_never_redownload_or_rebase_and_index_filter_is_explicit():
    stock = wire_bar(instrument_id="600001.SH", symbol="600001", asset_type="STOCK")
    feed, clock, transport = provider(response(envelope(wire_bar(), stock)))
    fetch(feed, at(9, 31))
    clock.now = at(9, 32)
    rows = asyncio.run(feed.get_minute_bars(at(9, 31), at(9, 32), as_of=at(9, 32)))
    indices = asyncio.run(feed.get_index_bars(at(9, 31), at(9, 32), as_of=at(9, 32)))
    assert len(rows) == 2 and len(indices) == 1
    assert len(transport.calls) == 1
    assert asyncio.run(feed.get_minute_bars(at(9, 31), at(9, 31), as_of=at(9, 31))) == ()


def test_future_cutoff_is_denied_without_network_request():
    feed, _, transport = provider(response(envelope()))
    with pytest.raises(LiveError, match="FUTURE_ACCESS_DENIED"):
        fetch(feed, at(9, 32))
    assert transport.calls == []


def test_provenance_and_clock_constructor_guards():
    with pytest.raises(LiveError, match="DATA_PROVENANCE_MIXED"):
        HttpFeedProvider("https://feed.example.test/minute", scope(fixture=True))
    with pytest.raises(ValueError, match="provide clock or now_fn"):
        HttpFeedProvider("https://feed.example.test/minute", scope(), clock=Clock(), now_fn=lambda: at(9, 31))
    with pytest.raises(ValueError, match="explicit HTTP"):
        HttpFeedProvider("file:///tmp/feed.json", scope())


def test_actual_clock_reversal_is_denied_without_commit():
    times = iter((at(9, 31, 2), at(9, 31, 2), at(9, 31, 1)))

    async def transport(url, timeout):
        return response(envelope())

    feed = HttpFeedProvider("https://feed.example.test/minute", scope(), transport=transport, now_fn=lambda: next(times))
    with pytest.raises(LiveError, match="ACTUAL_CLOCK_REVERSED"):
        fetch(feed, at(9, 31))
    assert feed.source_records() == ()
