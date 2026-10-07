"""Explicit TEST_FIXTURE sessions; no real historical calendar claims."""
import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from stockfish_live.calendar import TradingCalendar
from stockfish_live.common import LiveError

SHANGHAI = ZoneInfo("Asia/Shanghai")
DAY = date(2026, 9, 30)
KNOWN_AT = datetime(2026, 9, 29, 12, tzinfo=SHANGHAI)


def calendar_payload(**changes):
    value = {
        "schema_version": "LIVE_CALENDAR_V1",
        "sessions": [DAY.isoformat()],
        "coverage_start": "2026-09-28",
        "coverage_end": "2026-10-02",
        "exchanges": ["SH", "SZ"],
        "source": "TEST_FIXTURE explicit session inventory",
        "available_at": KNOWN_AT.isoformat(),
        "pit_status": "TEST_FIXTURE",
    }
    value.update(changes)
    return value


@pytest.fixture
def calendar():
    return TradingCalendar.model_validate(calendar_payload())


def test_explicit_sessions_never_infer_weekdays(calendar):
    calendar.require_session(DAY, KNOWN_AT, exchanges=("SH", "SZ"))
    # This covered weekday is absent from the explicit session inventory.
    with pytest.raises(LiveError, match="NON_TRADING_DAY"):
        calendar.require_session(date(2026, 9, 29), KNOWN_AT)
    with pytest.raises(LiveError, match="CALENDAR_OUT_OF_COVERAGE"):
        calendar.require_session(date(2026, 10, 3), KNOWN_AT)


def test_visibility_and_exchange_scope(calendar):
    with pytest.raises(LiveError, match="CALENDAR_NOT_YET_AVAILABLE"):
        calendar.require_session(DAY, KNOWN_AT - timedelta(microseconds=1))
    with pytest.raises(LiveError, match="CALENDAR_EXCHANGE_UNSUPPORTED"):
        calendar.require_session(DAY, KNOWN_AT, exchanges=("BJ",))
    with pytest.raises((ValueError, LiveError)):
        calendar.require_session(DAY, KNOWN_AT.replace(tzinfo=None))


@pytest.mark.parametrize("change", [
    {"sessions": []},
    {"sessions": [DAY.isoformat(), DAY.isoformat()]},
    {"sessions": ["2026-10-03"]},
    {"coverage_start": "2026-10-03"},
    {"coverage_end": "2026-09-27"},
    {"sessions": ["20260930"]},
    {"sessions": [datetime(2026, 9, 30, tzinfo=SHANGHAI)]},
    {"available_at": "2026-09-29T12:00:00"},
    {"exchanges": []},
    {"exchanges": ["SH", "SH"]},
    {"exchanges": ["UNKNOWN"]},
    {"source": " "},
    {"pit_status": "UNDECLARED"},
    {"schema_version": "LIVE_CALENDAR_V2"},
])
def test_invalid_calendar_rejected(change):
    with pytest.raises((ValidationError, ValueError, LiveError)):
        TradingCalendar.model_validate(calendar_payload(**change))


def test_schema_identity_is_required_and_unknown_fields_rejected():
    missing = calendar_payload()
    del missing["schema_version"]
    with pytest.raises(ValidationError):
        TradingCalendar.model_validate(missing)
    with pytest.raises(ValidationError):
        TradingCalendar.model_validate(calendar_payload(weekday_rule="not authorized"))


def test_file_loading_version_and_frozen_contract(tmp_path, calendar):
    path = tmp_path / "TEST_FIXTURE_calendar.json"
    path.write_text(json.dumps(calendar_payload()), encoding="utf-8")
    loaded = TradingCalendar.from_file(path)
    assert loaded == calendar
    assert len(loaded.version) == 64
    assert loaded.version == TradingCalendar.model_validate(calendar_payload(exchanges=["SZ", "SH"])).version
    assert loaded.version != TradingCalendar.model_validate(calendar_payload(source="TEST_FIXTURE revision")).version
    with pytest.raises(ValidationError):
        loaded.source = "overwritten"
    with pytest.raises(LiveError, match="CALENDAR_INVALID"):
        TradingCalendar.from_file(tmp_path / "TEST_FIXTURE_missing.json")
    path.write_text("not JSON", encoding="utf-8")
    with pytest.raises(LiveError, match="CALENDAR_INVALID"):
        TradingCalendar.from_file(path)


def test_completed_minutes_and_replay_ticks(calendar):
    bars = calendar.minutes(DAY)
    ticks = calendar.ticks(DAY)
    assert len(bars) == len(set(bars)) == 240
    assert len(ticks) == len(set(ticks)) == 242
    assert bars[0] == datetime(2026, 9, 30, 9, 31, tzinfo=SHANGHAI)
    assert bars[119] == datetime(2026, 9, 30, 11, 30, tzinfo=SHANGHAI)
    assert bars[120] == datetime(2026, 9, 30, 13, 1, tzinfo=SHANGHAI)
    assert bars[-1] == datetime(2026, 9, 30, 15, tzinfo=SHANGHAI)
    assert ticks[0] == datetime(2026, 9, 30, 9, 30, tzinfo=SHANGHAI)
    assert ticks[121] == datetime(2026, 9, 30, 13, tzinfo=SHANGHAI)
    assert set(ticks) - set(bars) == {ticks[0], ticks[121]}
    assert all(calendar.validate_bar_time(value) == value for value in bars)
    with pytest.raises(LiveError, match="NON_TRADING_DAY"):
        calendar.minutes(date(2026, 9, 29))
    with pytest.raises(LiveError, match="CALENDAR_OUT_OF_COVERAGE"):
        calendar.ticks(date(2026, 10, 3))


@pytest.mark.parametrize("hour,minute,second,microsecond", [
    (9, 30, 0, 0), (11, 31, 0, 0), (12, 0, 0, 0),
    (13, 0, 0, 0), (15, 1, 0, 0), (9, 31, 1, 0), (9, 31, 0, 1),
])
def test_invalid_completed_bar_time(calendar, hour, minute, second, microsecond):
    value = datetime(2026, 9, 30, hour, minute, second, microsecond, tzinfo=SHANGHAI)
    with pytest.raises(LiveError, match="INVALID_BAR_TIME"):
        calendar.validate_bar_time(value)


def test_bar_time_timezone_and_separate_date_checks(calendar):
    utc_time = datetime(2026, 9, 30, 1, 31, tzinfo=timezone.utc)
    assert calendar.validate_bar_time(utc_time) == datetime(2026, 9, 30, 9, 31, tzinfo=SHANGHAI)
    with pytest.raises((ValueError, LiveError)):
        calendar.validate_bar_time(datetime(2026, 9, 30, 9, 31))
    # Time validation alone does not authorize an absent business session.
    absent = datetime(2026, 9, 29, 9, 31, tzinfo=SHANGHAI)
    calendar.validate_bar_time(absent)
    with pytest.raises(LiveError, match="NON_TRADING_DAY"):
        calendar.require_session(absent.date(), KNOWN_AT)
