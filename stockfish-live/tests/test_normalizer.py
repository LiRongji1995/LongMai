"""TEST_FIXTURE normalization, arrival order and declared-scope quality tests."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from stockfish_live.calendar import TradingCalendar
from stockfish_live.common import LiveError, digest
from stockfish_live.contracts.market import MinuteBar, SourceReceipt
from stockfish_live.ingest.normalizer import QualityMonitor, normalize_minute

SH = ZoneInfo("Asia/Shanghai")
DAY = date(2026, 9, 30)
IID = "000001.SH"
OTHER = "000001.SZ"


def at(hour=9, minute=31, day=30):
    return datetime(2026, 9, day, hour, minute, tzinfo=SH)


def calendar(**changes):
    value = {
        "schema_version": "LIVE_CALENDAR_V1", "sessions": [DAY],
        "coverage_start": DAY, "coverage_end": date(2026, 10, 1),
        "exchanges": ["SH", "SZ"], "source": "TEST_FIXTURE explicit calendar",
        "available_at": at(9, 29), "pit_status": "TEST_FIXTURE",
    }
    value.update(changes)
    return TradingCalendar.model_validate(value)


def payload(timestamp=None, **changes):
    timestamp = timestamp or at()
    value = {
        "instrument_id": IID, "symbol": "000001", "exchange": "SH", "asset_type": "INDEX",
        "timestamp": timestamp, "open": 10.0, "high": 10.3, "low": 9.9, "close": 10.2,
        "volume": 120.0, "turnover": 2.0, "provider": "TEST_FIXTURE",
        "source_timestamp": timestamp, "source_timestamp_basis": "PUBLISHED",
        "available_at": timestamp, "source_id": digest(["TEST_FIXTURE", timestamp.isoformat(), changes.get("instrument_id", IID)]),
        "quality": "VALID", "pit_status": "TEST_FIXTURE",
    }
    value.update(changes)
    return value


def bar(timestamp=None, **changes):
    return MinuteBar.model_validate(payload(timestamp, **changes))


def receipt(value, **changes):
    fields = {
        "source_id": value.source_id, "provider": value.provider,
        "source_timestamp": value.source_timestamp,
        "source_timestamp_basis": value.source_timestamp_basis,
        "received_at": value.available_at, "available_at": value.available_at,
        "pit_status": value.pit_status,
    }
    fields.update(changes)
    return SourceReceipt.model_validate(fields)


def accept(monitor, rows, now=None):
    rows = tuple(rows)
    monitor.accept(rows, tuple(receipt(value) for value in rows), now or max(value.available_at for value in rows))


def test_explicit_units_and_missing_values():
    converted = normalize_minute(volume_unit="LOT", lot_size=100, turnover_unit="CNY_1K", timestamp_label="END", **payload())
    assert converted.volume == 12000.0 and converted.turnover == 2000.0
    missing = normalize_minute(volume_unit="SHARE", turnover_unit="CNY", timestamp_label="END",
        **payload(volume=None, turnover=None, close=None, quality="PARTIAL"))
    assert missing.volume is None and missing.turnover is None and missing.close is None
    assert missing.quality == "PARTIAL"
    assert missing.price_adjustment == "RAW"


@pytest.mark.parametrize("unit,scale", [("CNY", 1), ("CNY_1K", 1000), ("CNY_10K", 10000), ("CNY_100M", 100000000)])
def test_turnover_scales(unit, scale):
    value = normalize_minute(volume_unit="SHARE", turnover_unit=unit, timestamp_label="END", **payload())
    assert value.turnover == 2.0 * scale


@pytest.mark.parametrize("options,changes,code", [
    ({"volume_unit": "UNKNOWN"}, {}, "VOLUME_UNIT_INVALID"),
    ({"turnover_unit": "UNKNOWN"}, {}, "TURNOVER_UNIT_INVALID"),
    ({"volume_unit": "LOT"}, {}, "LOT_SIZE_REQUIRED"),
    ({"volume_unit": "LOT", "lot_size": 0}, {}, "LOT_SIZE_REQUIRED"),
    ({"volume_unit": "LOT", "lot_size": True}, {}, "LOT_SIZE_REQUIRED"),
    ({"timestamp_label": "UNKNOWN"}, {}, "TIMESTAMP_LABEL_INVALID"),
    ({}, {"volume": -1.0}, "MINUTE_NORMALIZATION_INVALID"),
    ({}, {"turnover": float("inf")}, "MINUTE_NORMALIZATION_INVALID"),
    ({}, {"volume": True}, "MINUTE_NORMALIZATION_INVALID"),
    ({}, {"high": 9.0}, "MINUTE_NORMALIZATION_INVALID"),
    ({}, {"timestamp": at().replace(tzinfo=None)}, "MINUTE_NORMALIZATION_INVALID"),
])
def test_unsafe_normalization_rejected(options, changes, code):
    kwargs = {"volume_unit": "SHARE", "turnover_unit": "CNY", "timestamp_label": "END"}
    kwargs.update(options)
    with pytest.raises(LiveError, match=code):
        normalize_minute(**kwargs, **payload(**changes))


def test_start_label_requires_complete_interval_and_preserves_real_receipt():
    value = normalize_minute(volume_unit="SHARE", turnover_unit="CNY", timestamp_label="START",
        **payload(at(9, 30), source_timestamp=at(9, 31), available_at=at(9, 31)))
    assert value.timestamp == at(9, 31)
    assert value.available_at == at(9, 31)
    with pytest.raises(LiveError, match="MINUTE_NORMALIZATION_INVALID"):
        normalize_minute(volume_unit="SHARE", turnover_unit="CNY", timestamp_label="START", **payload(at(9, 30)))


def test_adjusted_contract_is_preserved_but_not_accepted():
    adjusted = normalize_minute(volume_unit="SHARE", turnover_unit="CNY", timestamp_label="END", **payload(price_adjustment="QFQ"))
    assert adjusted.price_adjustment == "QFQ"
    monitor = QualityMonitor(calendar(), (IID,))
    with pytest.raises(LiveError, match="RAW_MINUTE_REQUIRED"):
        accept(monitor, [adjusted])
    assert monitor.state(at())['bar_count'] == 0


def test_empty_open_and_complete_declared_pool():
    monitor = QualityMonitor(calendar(), (IID,))
    monitor.accept([], [], at(9, 30))
    empty = monitor.state(at(9, 30))
    assert empty["quality"] == "PARTIAL" and empty["gap_count"] == 0
    accept(monitor, [bar()])
    full = monitor.state(at())
    assert full["quality"] == "VALID" and full["warnings"] == ()
    assert full["bar_count"] == 1 and full["last_event_time"] == at().isoformat()


def test_gap_remains_visible_without_synthesis():
    monitor = QualityMonitor(calendar(), (IID,))
    rows = [bar(at(9, minute)) for minute in range(31, 46) if minute != 38]
    accept(monitor, rows, at(9, 45))
    status = monitor.state(at(9, 45))
    assert status["quality"] == "GAPPED" and status["gap_count"] == 1
    assert status["warnings"] == ("DATA_GAP",)
    assert status["missing_examples"] == [{"instrument_id": IID, "timestamp": at(9, 38).isoformat()}]
    assert status["bar_count"] == 14
    with pytest.raises(LiveError, match="OUT_OF_ORDER"):
        accept(monitor, [bar(at(9, 38), available_at=at(9, 46))], at(9, 46))
    assert monitor.state(at(9, 45)) == status


def test_partial_rows_and_any_expected_instrument_stale():
    monitor = QualityMonitor(calendar(), (IID, OTHER))
    other = bar(instrument_id=OTHER, exchange="SZ", asset_type="STOCK")
    accept(monitor, [bar(close=None, quality="PARTIAL"), other])
    assert monitor.state(at())["quality"] == "PARTIAL"
    accept(monitor, [bar(at(9, 32)), bar(at(9, 33))], at(9, 33))
    status = monitor.state(at(9, 33))
    assert status["quality"] == "GAPPED"
    assert "DATA_STALE" in status["warnings"]
    assert "DATA_PARTIAL" in status["warnings"]
    assert status["gap_count"] == 2


def test_lunch_does_not_create_stale_minutes_or_gaps():
    monitor = QualityMonitor(calendar(), (IID,))
    morning = [bar(timestamp) for timestamp in calendar().minutes(DAY)[:120]]
    accept(monitor, morning, at(11, 30))
    status = monitor.state(at(13, 0))
    assert status["quality"] == "VALID" and status["gap_count"] == 0
    assert "DATA_STALE" not in status["warnings"]
    assert status["last_event_time"] == at(11, 30).isoformat()
    late = monitor.state(at(13, 2))
    assert late["gap_count"] == 2 and "DATA_STALE" in late["warnings"]


def test_invalid_batch_is_atomic_and_arrival_order_is_not_sorted():
    monitor = QualityMonitor(calendar(), (IID,))
    accept(monitor, [bar()])
    before = monitor.state(at())
    with pytest.raises(LiveError, match="DUPLICATE_TIMESTAMP"):
        accept(monitor, [bar(at(9, 32)), bar(at(9, 32))], at(9, 32))
    assert monitor.state(at()) == before
    with pytest.raises(LiveError, match="OUT_OF_ORDER"):
        accept(monitor, [bar(at(9, 33)), bar(at(9, 32))], at(9, 33))
    assert monitor.state(at()) == before


@pytest.mark.parametrize("changes,code", [
    ({"instrument_id": "600000.SH", "symbol": "600000"}, "INSTRUMENT_OUTSIDE_DECLARED_SCOPE"),
    ({"quality": "INVALID"}, "INVALID_MINUTE_BAR"),
    ({"timestamp": at(9, 30), "source_timestamp": at(9, 30), "available_at": at(9, 30)}, "INVALID_BAR_TIME"),
])
def test_bad_arrivals_rejected_without_state_change(changes, code):
    monitor = QualityMonitor(calendar(), (IID,))
    value = bar(**changes)
    with pytest.raises(LiveError, match=code):
        accept(monitor, [value], at())
    assert monitor.state(at())["bar_count"] == 0


def test_parent_receipt_is_required_visible_consistent_and_immutable():
    monitor = QualityMonitor(calendar(), (IID,))
    value = bar()
    with pytest.raises(LiveError, match="SOURCE_RECEIPT_REQUIRED"):
        monitor.accept([value], [], at())
    with pytest.raises(LiveError, match="FUTURE_ACCESS_DENIED"):
        monitor.accept([value], [receipt(value, available_at=at(9, 32))], at())
    with pytest.raises(LiveError, match="SOURCE_RECEIPT_MISMATCH"):
        monitor.accept([value], [receipt(value, provider="FOREIGN")], at())
    accept(monitor, [value])
    with pytest.raises(LiveError, match="SOURCE_RECEIPT_CONFLICT"):
        monitor.accept([], [receipt(value, available_at=at(9, 32))], at(9, 32))
    assert monitor.state(at())["bar_count"] == 1


def test_cannot_read_future_or_rewind_accepted_clock():
    monitor = QualityMonitor(calendar(), (IID,))
    with pytest.raises(LiveError, match="FUTURE_ACCESS_DENIED"):
        accept(monitor, [bar(at(9, 46))], at(9, 45))
    assert monitor.state(at(9, 45))["bar_count"] == 0
    accept(monitor, [bar(at(9, 45))], at(9, 45))
    with pytest.raises(LiveError, match="CLOCK_REWIND"):
        monitor.state(at(9, 44))


def test_cross_day_reset_is_explicit_and_missing_examples_are_bounded():
    monitor = QualityMonitor(calendar(), (IID, OTHER))
    monitor.accept([], [], at(9, 30))
    with pytest.raises(LiveError, match="UNEXPECTED_RESET"):
        monitor.accept([], [], at().replace(day=1, month=10))
    many = monitor.state(at(10, 0))
    assert many["gap_count"] == 60 and len(many["missing_examples"]) == 20


def test_fixture_and_real_provenance_cannot_mix():
    monitor = QualityMonitor(calendar(), (IID,))
    real = bar(pit_status="VERIFIED_PIT")
    with pytest.raises(LiveError, match="DATA_PROVENANCE_MIXED"):
        accept(monitor, [real])
    assert monitor.state(at())["bar_count"] == 0
    research_calendar = calendar(pit_status="RESEARCH_ONLY")
    research_monitor = QualityMonitor(research_calendar, (IID,))
    accept(research_monitor, [bar(pit_status="RESEARCH_ONLY")])
    assert research_monitor.state(at())["quality"] == "VALID"


def test_calendar_actual_visibility_cannot_be_bypassed():
    monitor = QualityMonitor(calendar(available_at=at(10, 0)), (IID,))
    with pytest.raises(LiveError, match="CALENDAR_NOT_YET_AVAILABLE"):
        accept(monitor, [bar()])


@pytest.mark.parametrize("scope,threshold,code", [
    ((), 2, "EXPECTED_SCOPE_INVALID"),
    ((IID, IID), 2, "EXPECTED_SCOPE_INVALID"),
    (("000001",), 2, "EXPECTED_SCOPE_INVALID"),
    ((IID,), 0, "STALE_THRESHOLD_INVALID"),
    ((IID,), True, "STALE_THRESHOLD_INVALID"),
])
def test_monitor_configuration_rejected(scope, threshold, code):
    with pytest.raises(LiveError, match=code):
        QualityMonitor(calendar(), scope, stale_minutes=threshold)
