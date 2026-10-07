"""Explicit minute units and atomic, single-process data quality state."""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Iterable, Literal, Mapping

from pydantic import ValidationError

from ..calendar import TradingCalendar
from ..common import LiveError, aware, canonical_instrument
from ..contracts.market import MinuteBar, SourceReceipt, validate_receipt


def normalize_minute(
    *,
    volume_unit: Literal["SHARE", "LOT"],
    turnover_unit: Literal["CNY", "CNY_1K", "CNY_10K", "CNY_100M"],
    timestamp_label: Literal["END", "START"],
    lot_size: int | None = None,
    **payload,
) -> MinuteBar:
    """Normalize only declared units; preserve missing fields and real clocks.

    START labels shift to the completed interval END. Source/availability clocks
    are never shifted to manufacture visibility for an incomplete minute.
    """
    if volume_unit not in ("SHARE", "LOT"):
        raise LiveError("VOLUME_UNIT_INVALID")
    scales = {"CNY": 1, "CNY_1K": 1000, "CNY_10K": 10000, "CNY_100M": 100000000}
    if turnover_unit not in scales:
        raise LiveError("TURNOVER_UNIT_INVALID")
    if volume_unit == "LOT" and (
        isinstance(lot_size, bool) or not isinstance(lot_size, int) or lot_size <= 0
    ):
        raise LiveError("LOT_SIZE_REQUIRED")
    if timestamp_label not in ("END", "START"):
        raise LiveError("TIMESTAMP_LABEL_INVALID")
    try:
        timestamp = payload["timestamp"]
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if not isinstance(timestamp, datetime):
            raise ValueError("minute timestamp required")
        timestamp = aware(timestamp)
        if timestamp_label == "START":
            timestamp += timedelta(minutes=1)
        payload["timestamp"] = timestamp
        for field, scale in (
            ("volume", lot_size if volume_unit == "LOT" else 1),
            ("turnover", scales[turnover_unit]),
        ):
            value = payload.get(field)
            if value is not None:
                if isinstance(value, bool):
                    raise ValueError("boolean market value")
                value = float(value) * scale
                if not math.isfinite(value):
                    raise ValueError("nonfinite market value")
                payload[field] = value
        return MinuteBar.model_validate(payload)
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise LiveError("MINUTE_NORMALIZATION_INVALID") from exc


class QualityMonitor:
    """Append accepted arrivals atomically for one explicit trading day.

    A new instance is required for each trading day. Rejected batches do not
    change accepted state; the collector records their failures separately.
    Scope is only ``expected_instruments``, never a claim of whole-market data.
    """

    def __init__(self, calendar: TradingCalendar, expected_instruments: tuple[str, ...], stale_minutes: int = 2):
        if not expected_instruments or len(set(expected_instruments)) != len(expected_instruments):
            raise LiveError("EXPECTED_SCOPE_INVALID")
        try:
            if any(canonical_instrument(value) != value for value in expected_instruments):
                raise ValueError("canonical expected identities required")
        except (ValueError, TypeError, AttributeError) as exc:
            raise LiveError("EXPECTED_SCOPE_INVALID") from exc
        if isinstance(stale_minutes, bool) or not isinstance(stale_minutes, int) or stale_minutes < 1:
            raise LiveError("STALE_THRESHOLD_INVALID")
        self.calendar = calendar
        self.expected_instruments = tuple(expected_instruments)
        self.stale_minutes = stale_minutes
        self._scope = frozenset(expected_instruments)
        self._exchanges = tuple(sorted({value[-2:] for value in expected_instruments}))
        self._bars: dict[tuple[str, datetime], MinuteBar] = {}
        self._last: dict[str, datetime] = {}
        self._receipts: dict[str, SourceReceipt] = {}
        self._session_date: date | None = None
        self._last_now: datetime | None = None

    def _clock(self, now: datetime) -> datetime:
        try:
            now = aware(now)
        except (ValueError, TypeError, AttributeError) as exc:
            raise LiveError("INVALID_OBSERVATION_TIME") from exc
        if self._session_date is not None and now.date() != self._session_date:
            raise LiveError("UNEXPECTED_RESET")
        if self._last_now is not None and now < self._last_now:
            raise LiveError("CLOCK_REWIND")
        self.calendar.require_session(now.date(), now, exchanges=self._exchanges)
        return now

    def accept(
        self,
        bars: Iterable[MinuteBar],
        receipts: Iterable[SourceReceipt] | Mapping[str, SourceReceipt],
        now: datetime,
    ) -> None:
        now = self._clock(now)
        staged_receipts = dict(self._receipts)
        staged_bars = dict(self._bars)
        staged_last = dict(self._last)
        supplied = receipts.values() if isinstance(receipts, Mapping) else receipts
        try:
            for raw in supplied:
                receipt = SourceReceipt.model_validate(raw.model_dump() if isinstance(raw, SourceReceipt) else raw)
                previous = staged_receipts.get(receipt.source_id)
                if previous is not None and receipt != previous:
                    raise LiveError("SOURCE_RECEIPT_CONFLICT")
                if any(value is not None and value > now for value in (
                    receipt.source_timestamp, receipt.received_at, receipt.available_at
                )):
                    raise LiveError("FUTURE_ACCESS_DENIED")
                staged_receipts[receipt.source_id] = receipt
            for raw in bars:
                bar = MinuteBar.model_validate(raw.model_dump() if isinstance(raw, MinuteBar) else raw)
                if bar.instrument_id not in self._scope:
                    raise LiveError("INSTRUMENT_OUTSIDE_DECLARED_SCOPE")
                if bar.timestamp.date() != now.date():
                    raise LiveError("UNEXPECTED_RESET")
                self.calendar.require_session(bar.timestamp.date(), now, exchanges=(bar.exchange,))
                self.calendar.validate_bar_time(bar.timestamp)
                if bar.price_adjustment != "RAW":
                    raise LiveError("RAW_MINUTE_REQUIRED")
                if bar.quality == "INVALID":
                    raise LiveError("INVALID_MINUTE_BAR")
                receipt = staged_receipts.get(bar.source_id)
                if receipt is None:
                    raise LiveError("SOURCE_RECEIPT_REQUIRED")
                validate_receipt(bar, receipt, cutoff=now)
                previous = staged_last.get(bar.instrument_id)
                if previous is not None and bar.timestamp == previous:
                    raise LiveError("DUPLICATE_TIMESTAMP")
                if previous is not None and bar.timestamp < previous:
                    raise LiveError("OUT_OF_ORDER")
                key = (bar.instrument_id, bar.timestamp)
                if key in staged_bars:
                    raise LiveError("DUPLICATE_TIMESTAMP")
                staged_bars[key] = bar
                staged_last[bar.instrument_id] = bar.timestamp
            statuses = {self.calendar.pit_status}
            statuses.update(value.pit_status for value in staged_receipts.values())
            statuses.update(value.pit_status for value in staged_bars.values())
            if "TEST_FIXTURE" in statuses and len(statuses) > 1:
                raise LiveError("DATA_PROVENANCE_MIXED")
        except (TypeError, ValueError, ValidationError) as exc:
            raise LiveError("MINUTE_BATCH_INVALID") from exc
        self._receipts = staged_receipts
        self._bars = staged_bars
        self._last = staged_last
        self._session_date = now.date()
        self._last_now = now

    def state(self, now: datetime) -> dict:
        now = self._clock(now)
        completed = tuple(value for value in self.calendar.minutes(now.date()) if value <= now)
        positions = {value: index for index, value in enumerate(completed)}
        gaps = 0
        examples = []
        stale = False
        for instrument in self.expected_instruments:
            for timestamp in completed:
                if (instrument, timestamp) not in self._bars:
                    gaps += 1
                    if len(examples) < 20:
                        examples.append({"instrument_id": instrument, "timestamp": timestamp.isoformat()})
            latest = self._last.get(instrument)
            # Count only completed exchange minutes. At 13:00 the reference
            # remains 11:30, so lunch never adds artificial age or gaps.
            lag = len(completed) - (positions[latest] + 1 if latest in positions else 0)
            stale |= lag >= self.stale_minutes
        row_qualities = {bar.quality for bar in self._bars.values()}
        gapped = gaps > 0 or "GAPPED" in row_qualities
        stale |= "STALE" in row_qualities
        partial = not self._bars or "PARTIAL" in row_qualities
        warnings = []
        if gapped:
            warnings.append("DATA_GAP")
        if stale:
            warnings.append("DATA_STALE")
        if partial:
            warnings.append("DATA_PARTIAL")
        quality = "GAPPED" if gapped else "STALE" if stale else "PARTIAL" if partial else "VALID"
        latest = max(self._last.values(), default=None)
        return {
            "quality": quality,
            "warnings": tuple(warnings),
            "gap_count": gaps,
            "missing_examples": examples,
            "last_event_time": latest.isoformat() if latest else None,
            "bar_count": len(self._bars),
        }
