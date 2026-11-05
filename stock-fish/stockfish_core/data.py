"""Finite exchange calendar shared by screening and outcome maturity checks."""
from __future__ import annotations

from datetime import date, datetime, time
from typing import Iterable
from zoneinfo import ZoneInfo
from .common import UnavailableError, digest as content_digest

SHANGHAI = ZoneInfo('Asia/Shanghai')


def aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    return value


class TradingCalendar:
    """An explicit, finite exchange calendar; no weekday/holiday guessing."""
    def __init__(self, sessions: Iterable[date], coverage_start: date | None = None, coverage_end: date | None = None):
        values = tuple(sessions)
        if not values or any(isinstance(d, datetime) or not isinstance(d, date) for d in values) or len(set(values)) != len(values):
            raise ValueError("calendar needs unique explicit dates")
        self.sessions = tuple(sorted(values))
        self.coverage_start = coverage_start or self.sessions[0]
        self.coverage_end = coverage_end or self.sessions[-1]
        if self.coverage_start > self.sessions[0] or self.coverage_end < self.sessions[-1]:
            raise ValueError("session outside coverage")
        self.version = content_digest({"sessions": [d.isoformat() for d in self.sessions], "start": self.coverage_start.isoformat(), "end": self.coverage_end.isoformat()})

    def is_session(self, day: date) -> bool:
        if not self.coverage_start <= day <= self.coverage_end:
            raise UnavailableError("CALENDAR_OUT_OF_COVERAGE", "data")
        return day in self.sessions

    is_trading_day = is_session

    def next_session(self, day: date, inclusive: bool = False) -> date:
        self.is_session(day)  # ensures covered even when day is a holiday
        for candidate in self.sessions:
            if candidate > day or (inclusive and candidate == day):
                return candidate
        raise UnavailableError("NEXT_SESSION_UNAVAILABLE", "data")

    def earliest_trade_at(self, decision_at: datetime) -> datetime:
        local = aware(decision_at).astimezone(SHANGHAI)
        day = local.date()
        same_open = datetime.combine(day, time(9, 30), SHANGHAI)
        if self.is_session(day) and local < same_open:
            return same_open
        return datetime.combine(self.next_session(day), time(9, 30), SHANGHAI)

    def count_sessions(self, start: date, end: date) -> int:
        self.is_session(start)
        self.is_session(end)
        if end < start:
            raise ValueError("inverted session range")
        return sum(start <= d <= end for d in self.sessions)
