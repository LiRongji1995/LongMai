"""Contract regressions use only explicit synthetic TEST_FIXTURE records."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from stockfish_live.common import LiveError, canonical_instrument, digest
from stockfish_live.contracts.market import MinuteBar, SourceReceipt, validate_receipt
from stockfish_live.contracts.quality import effective_pit_status

NOW = datetime(2025, 6, 18, 9, 45, tzinfo=ZoneInfo("Asia/Shanghai"))


def bar(**changes):
    values = dict(instrument_id="000001.SH", exchange="SH", symbol="000001", asset_type="INDEX",
        timestamp=NOW, open=10., high=11., low=9., close=10., volume=100., turnover=1000.,
        provider="TEST_FIXTURE", source_timestamp=NOW, source_timestamp_basis="PUBLISHED",
        available_at=NOW, source_id=digest("TEST_FIXTURE"), quality="VALID", pit_status="TEST_FIXTURE")
    values.update(changes)
    return MinuteBar(**values)


@pytest.mark.parametrize("value,exchange,expected", [
    ("SH000001", None, "000001.SH"), ("sz.000001", None, "000001.SZ"),
    ("920001", "BJ", "920001.BJ"), ("600000.SH", "SH", "600000.SH"),
])
def test_explicit_identity_semantics(value, exchange, expected):
    assert canonical_instrument(value, exchange) == expected


@pytest.mark.parametrize("value,exchange", [("000001", None), ("000001.SH", "SZ"), ("000001.US", None)])
def test_no_exchange_guessing(value, exchange):
    with pytest.raises(ValueError):
        canonical_instrument(value, exchange)


@pytest.mark.parametrize("changes", [
    {"symbol": "000002"}, {"exchange": "SZ"}, {"timestamp": NOW.replace(tzinfo=None)},
    {"volume": -1}, {"close": float("nan")}, {"open": None},
    {"source_timestamp": NOW + timedelta(minutes=1)}, {"available_at": NOW - timedelta(seconds=1)},
    {"high": 9., "low": 11., "open": None, "close": None, "quality": "PARTIAL"},
    {"high": 9., "close": None, "quality": "PARTIAL"},
    *({field: True} for field in ("open", "high", "low", "close", "volume", "turnover")),
])
def test_invalid_and_partial_impossible_values_rejected(changes):
    with pytest.raises(ValidationError):
        bar(**changes)


def test_frozen_and_utc_roundtrip():
    value = bar(timestamp=NOW.astimezone(ZoneInfo("UTC")))
    assert value.timestamp.tzinfo.key == "Asia/Shanghai"
    with pytest.raises(ValidationError):
        value.close = 999.


def test_receipt_cannot_predate_complete_event():
    value = bar(quality="PARTIAL", source_timestamp=None, source_timestamp_basis="UNKNOWN")
    receipt = SourceReceipt(source_id=value.source_id, provider=value.provider, pit_status="TEST_FIXTURE",
                            received_at=NOW - timedelta(minutes=1), available_at=NOW)
    with pytest.raises(LiveError, match="PARENT_RECEIPT_BEFORE_EVENT"):
        validate_receipt(value, receipt)


def test_no_implicit_verified_pit_or_fixture_upgrade():
    assert bar(pit_status="RESEARCH_ONLY").pit_status == "RESEARCH_ONLY"
    with pytest.raises(ValidationError):
        bar(provider="mock", pit_status="VERIFIED_PIT")
    with pytest.raises(ValidationError):
        bar(pit_status="VERIFIED_PIT", source_timestamp_basis="RECEIVED")
    assert effective_pit_status(["VERIFIED_PIT", "RESEARCH_ONLY"]) == "RESEARCH_ONLY"
    assert effective_pit_status(["VERIFIED_PIT", "RECONSTRUCTED"]) == "RECONSTRUCTED"
    with pytest.raises(LiveError, match="DATA_PROVENANCE_MIXED"):
        effective_pit_status(["VERIFIED_PIT", "TEST_FIXTURE"])
