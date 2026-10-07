"""Finite exchange sessions and explicit one-minute bar boundaries.

The calendar never derives trading days from weekdays. Bar timestamps mark the
end of a completed minute; replay ticks also include each session's opening.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .common import LiveError, aware, digest

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _day(value: object) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value)
    raise ValueError("calendar dates require YYYY-MM-DD")


class TradingCalendar(BaseModel):
    """Versioned explicit sessions with a separate actual visibility clock."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    schema_version: Literal["LIVE_CALENDAR_V1"]
    sessions: tuple[date, ...] = Field(min_length=1)
    coverage_start: date
    coverage_end: date
    exchanges: tuple[Literal["SH", "SZ", "BJ"], ...] = Field(min_length=1)
    source: str = Field(min_length=1)
    available_at: datetime
    pit_status: Literal["TEST_FIXTURE", "VERIFIED_PIT", "RESEARCH_ONLY"]

    @field_validator("sessions", mode="before")
    @classmethod
    def session_dates(cls, value):
        if not isinstance(value, (list, tuple)):
            raise ValueError("sessions must be an explicit sequence")
        return tuple(_day(day) for day in value)

    @field_validator("coverage_start", "coverage_end", mode="before")
    @classmethod
    def coverage_dates(cls, value):
        return _day(value)

    @field_validator("available_at")
    @classmethod
    def visibility_clock(cls, value):
        return aware(value)

    @field_validator("source")
    @classmethod
    def source_nonempty(cls, value):
        if not value.strip():
            raise ValueError("calendar source is required")
        return value

    @model_validator(mode="after")
    def explicit_scope(self):
        if self.coverage_start > self.coverage_end:
            raise ValueError("inverted calendar coverage")
        if len(set(self.sessions)) != len(self.sessions):
            raise ValueError("duplicate calendar session")
        if any(not self.coverage_start <= day <= self.coverage_end for day in self.sessions):
            raise ValueError("session outside calendar coverage")
        if len(set(self.exchanges)) != len(self.exchanges):
            raise ValueError("duplicate exchange scope")
        # Equivalent explicit session/scope lists have the same version even
        # when the caller supplied them in a different order.
        object.__setattr__(self, "sessions", tuple(sorted(self.sessions)))
        object.__setattr__(self, "exchanges", tuple(sorted(self.exchanges)))
        return self

    @classmethod
    def from_file(cls, path: str | Path) -> "TradingCalendar":
        try:
            value = json.loads(Path(path).read_text(encoding="utf-8"))
            return cls.model_validate(value)
        except (OSError, ValueError, TypeError, LiveError) as exc:
            raise LiveError("CALENDAR_INVALID") from exc

    @property
    def version(self) -> str:
        return digest(self.model_dump(mode="json"))

    def _require_day(self, day: date) -> date:
        try:
            day = _day(day)
        except (ValueError, TypeError) as exc:
            raise LiveError("CALENDAR_INVALID_DATE") from exc
        if not self.coverage_start <= day <= self.coverage_end:
            raise LiveError("CALENDAR_OUT_OF_COVERAGE")
        if day not in self.sessions:
            raise LiveError("NON_TRADING_DAY")
        return day

    def require_session(self, day: date, known_at: datetime, exchanges=()) -> None:
        """Fail closed on unknown dates, late calendar capture or scope mismatch."""
        self._require_day(day)
        if self.available_at > aware(known_at):
            raise LiveError("CALENDAR_NOT_YET_AVAILABLE")
        if any(exchange not in self.exchanges for exchange in exchanges):
            raise LiveError("CALENDAR_EXCHANGE_UNSUPPORTED")

    def minutes(self, day: date) -> tuple[datetime, ...]:
        """Return 240 completed-bar end times; no lunch or auction bars."""
        day = self._require_day(day)
        return tuple(
            datetime.combine(day, start, SHANGHAI) + timedelta(minutes=offset)
            for start in (time(9, 30), time(13))
            for offset in range(1, 121)
        )

    def ticks(self, day: date) -> tuple[datetime, ...]:
        """Return 242 replay clock ticks, including 09:30 and 13:00."""
        day = self._require_day(day)
        return tuple(
            datetime.combine(day, start, SHANGHAI) + timedelta(minutes=offset)
            for start in (time(9, 30), time(13))
            for offset in range(121)
        )

    def validate_bar_time(self, timestamp: datetime) -> datetime:
        """Validate only completed-bar time; caller checks date and exchange."""
        local = aware(timestamp)
        if local.second or local.microsecond:
            raise LiveError("INVALID_BAR_TIME")
        clock = local.time()
        if not (time(9, 31) <= clock <= time(11, 30) or time(13, 1) <= clock <= time(15)):
            raise LiveError("INVALID_BAR_TIME")
        return local
