"""Trusted data contracts. No network, provider SDK, Flask or Qlib imports.

Prices are never inferred across adjustment bases. ``VERIFIED`` describes an
explicit caller-verified data contract, not commercial permission. See
``provider_classification.json`` for the separate production entitlement gate.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import struct
from datetime import date, datetime, time, timezone, timedelta
from pathlib import Path
from typing import Iterable, Literal
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator

from .common import CoreModel, UnavailableError, digest as content_digest, utc_now

SHANGHAI = ZoneInfo("Asia/Shanghai")
PriceAdjustment = Literal["RAW", "QFQ", "HFQ", "QLIB_NORMALIZED"]
DataQuality = Literal["VERIFIED", "PARTIAL", "RESEARCH_ONLY", "UNAVAILABLE", "MOCK"]
Decision = Literal["ACCEPT", "REJECT", "UNAVAILABLE"]


def aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include timezone")
    return value


def canonical_instrument(value: str, exchange: str | None = None) -> str:
    """Canonicalize explicit SH/SZ/BJ forms; never guess a bare numeric symbol."""
    code = value.strip().upper()
    if re.fullmatch(r"(?:SH|SZ|BJ)[.]?\d{6}", code):
        code = f"{code[-6:]}.{code[:2]}"
    if re.fullmatch(r"\d{6}", code) and exchange:
        code = f"{code}.{exchange.upper()}"
    if not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", code):
        raise ValueError("instrument requires an explicit exchange, e.g. 000001.SZ")
    if exchange and code[-2:] != exchange.upper():
        raise ValueError("instrument/exchange mismatch")
    return code


class CanonicalMarketBar(CoreModel):
    instrument_id: str
    exchange: Literal["SH", "SZ", "BJ"]
    symbol: str
    timestamp: datetime
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None  # shares, never lots
    turnover: float | None = None  # currency units, never thousands/millions
    currency: Literal["CNY"] = "CNY"
    price_adjustment: PriceAdjustment
    adjustment_factor: float | None = None
    provider: str = Field(min_length=1)
    source_timestamp: datetime | None = None
    available_at: datetime
    quality: DataQuality = "UNAVAILABLE"
    missing_fields: tuple[str, ...] = ()
    quality_notes: tuple[str, ...] = ()
    snapshot_id: str = Field(min_length=1)

    @field_validator("timestamp", "available_at", "source_timestamp")
    @classmethod
    def timestamps(cls, value):
        return aware(value) if value is not None else value

    @model_validator(mode="after")
    def contract(self):
        canonical = canonical_instrument(self.instrument_id, self.exchange)
        if self.instrument_id != canonical or self.symbol != canonical[:6]:
            raise ValueError("noncanonical identity")
        missing = tuple(n for n in ("open", "high", "low", "close", "volume", "turnover", "source_timestamp") if getattr(self, n) is None)
        object.__setattr__(self, "missing_fields", missing)
        if self.timestamp > self.available_at or (self.source_timestamp and self.source_timestamp > self.available_at):
            raise ValueError("market/source timestamp cannot follow availability")
        for name in ("open", "high", "low", "close", "volume", "turnover", "adjustment_factor"):
            value = getattr(self, name)
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError(f"invalid {name}")
        for name in ("open", "high", "low", "close", "adjustment_factor"):
            if getattr(self, name) == 0:
                raise ValueError(f"{name} must be positive")
        if all(getattr(self, n) is not None for n in ("open", "high", "low", "close")):
            if self.low > min(self.open, self.close) or self.high < max(self.open, self.close) or self.low > self.high:
                raise ValueError("invalid OHLC envelope")
        if self.price_adjustment == "RAW" and self.adjustment_factor not in (None, 1.0):
            raise ValueError("RAW bar cannot carry a non-unit adjustment factor")
        if self.quality == "VERIFIED" and missing:
            raise ValueError("VERIFIED bar requires all market fields and source time")
        if self.quality == "MOCK" and self.provider != "mock":
            raise ValueError("mock bars require provider=mock")
        if self.provider.lower() == "mock" and self.quality != "MOCK":
            raise ValueError("mock provider must remain explicitly MOCK")
        return self

    @property
    def raw_price_available(self) -> bool:
        return self.price_adjustment == "RAW" and self.quality == "VERIFIED" and self.close is not None and self.provider.lower() not in ("mock", "unknown")

    def require_raw_price(self) -> float:
        if not self.raw_price_available:
            raise UnavailableError("RAW_PRICE_UNAVAILABLE", "data")
        return self.close

    def assert_visible(self, as_of: datetime) -> None:
        if self.available_at > aware(as_of):
            raise UnavailableError("DATA_NOT_YET_AVAILABLE", "data")


def normalize_market_bar(*, volume_unit: Literal["SHARE", "LOT"],
                         turnover_unit: Literal["CNY", "CNY_1K", "CNY_10K", "CNY_100M"],
                         lot_size: int | None = None, **payload) -> CanonicalMarketBar:
    """Explicit unit conversion; no unit guessing or missing-value synthesis."""
    if volume_unit not in ("SHARE", "LOT"):
        raise ValueError("unsupported volume unit")
    scales = {"CNY": 1, "CNY_1K": 1000, "CNY_10K": 10000, "CNY_100M": 100000000}
    if turnover_unit not in scales:
        raise ValueError("unsupported turnover unit")
    if volume_unit == "LOT" and (lot_size is None or lot_size <= 0):
        raise ValueError("lot size required for lots")
    if payload.get("volume") is not None:
        payload["volume"] = float(payload["volume"]) * (lot_size if volume_unit == "LOT" else 1)
    if payload.get("turnover") is not None:
        payload["turnover"] = float(payload["turnover"]) * scales[turnover_unit]
    return CanonicalMarketBar(**payload)


class SecurityState(CoreModel):
    instrument_id: str
    effective_from: date
    effective_to: date | None = None  # exclusive
    available_at: datetime
    provider: str = Field(min_length=1)
    verified: bool = False
    market: Literal["CN"] = "CN"
    listing_date: date | None = None
    delisting_date: date | None = None  # first nontradable day
    board: str | None = None
    is_st: bool | None = None
    suspended: bool | None = None
    lot_size: int | None = Field(default=None, gt=0)
    min_buy_shares: int | None = Field(default=None, gt=0)
    industry: str | None = None
    limits_date: date | None = None
    limit_up: float | None = Field(default=None, gt=0)
    limit_down: float | None = Field(default=None, gt=0)
    no_price_limit: bool | None = None
    limit_provider: str | None = None
    limit_source_timestamp: datetime | None = None
    limit_available_at: datetime | None = None
    limit_evidence_id: str | None = None

    @field_validator("instrument_id")
    @classmethod
    def identity(cls, value):
        if value != canonical_instrument(value):
            raise ValueError("SecurityState instrument must be canonical")
        return value

    @field_validator("available_at")
    @classmethod
    def timestamp_valid(cls, value):
        return aware(value)

    @model_validator(mode="after")
    def dates_valid(self):
        if self.effective_to and self.effective_to <= self.effective_from:
            raise ValueError("empty security effective interval")
        if self.listing_date and self.delisting_date and self.delisting_date <= self.listing_date:
            raise ValueError("invalid listing interval")
        if self.limit_up is not None and self.limit_down is not None and self.limit_down > self.limit_up:
            raise ValueError("invalid price limits")
        if self.no_price_limit and (self.limit_up is not None or self.limit_down is not None):
            raise ValueError("unlimited state cannot have price limits")
        for when in (self.limit_source_timestamp, self.limit_available_at):
            if when is not None and aware(when) > self.available_at:
                raise ValueError("limit evidence cannot follow state availability")
        return self


class SecurityMaster:
    def __init__(self, states: Iterable[SecurityState] = ()):
        self.states = tuple(states)
        self.version = content_digest([s.model_dump(mode="json") for s in sorted(self.states, key=lambda s: (s.instrument_id, s.effective_from, s.available_at))])

    def resolve(self, instrument_id: str, session_date: date, known_at: datetime) -> SecurityState:
        instrument_id = canonical_instrument(instrument_id)
        eligible = [s for s in self.states if s.instrument_id == instrument_id and s.available_at <= aware(known_at)
                    and s.effective_from <= session_date and (s.effective_to is None or session_date < s.effective_to)]
        if not eligible:
            raise UnavailableError("SECURITY_STATE_UNAVAILABLE", "data")
        eligible.sort(key=lambda s: s.available_at, reverse=True)
        if len(eligible) > 1 and eligible[0].available_at == eligible[1].available_at:
            raise UnavailableError("AMBIGUOUS_SECURITY_STATE", "data")
        if not eligible[0].verified:
            raise UnavailableError("SECURITY_STATE_UNVERIFIED", "data")
        return eligible[0]


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


class TradabilityResult(CoreModel):
    decision: Decision
    reason_codes: tuple[str, ...]
    instrument_id: str
    trade_date: date
    lot_size: int | None = None
    min_buy_shares: int | None = None
    industry: str | None = None
    security_version: str = ""
    calendar_version: str = ""


class TradabilityService:
    def __init__(self, calendar: TradingCalendar, security_master: SecurityMaster, *, min_listing_sessions: int = 1, allow_st: bool = False):
        if min_listing_sessions < 1:
            raise ValueError("min listing sessions must be positive")
        self.calendar, self.security_master = calendar, security_master
        self.min_listing_sessions, self.allow_st = min_listing_sessions, allow_st

    def evaluate(self, instrument_id: str, decision_at: datetime, trade_date: date,
                 side: Literal["BUY", "SELL"], bar: CanonicalMarketBar) -> TradabilityResult:
        try:
            if bar.instrument_id != canonical_instrument(instrument_id):
                raise ValueError("bar/security mismatch")
            bar.assert_visible(decision_at)
            price = bar.require_raw_price()
        except UnavailableError as exc:
            return self._result(instrument_id, trade_date, "UNAVAILABLE", [exc.reason])
        return self._evaluate(instrument_id, decision_at, trade_date, side, price)

    def evaluate_execution(self, instrument_id: str, execution_at: datetime,
                           side: Literal["BUY", "SELL"], raw_execution_price: float) -> TradabilityResult:
        """Check a separately evidenced RAW execution price; never use future OHLC.

        The caller must verify the raw price's source. State is resolved only from
        records already available at execution_at; daily end-of-day status cannot
        be backdated to the opening auction.
        """
        local = aware(execution_at).astimezone(SHANGHAI)
        if not (time(9, 30) <= local.time() <= time(11, 30) or time(13) <= local.time() <= time(15)):
            return self._result(instrument_id, local.date(), "REJECT", ["OUTSIDE_EXECUTION_SESSION"])
        return self._evaluate(instrument_id, execution_at, local.date(), side, raw_execution_price)

    def _result(self, instrument_id, trade_date, decision, reasons, state=None):
        return TradabilityResult(decision=decision, reason_codes=tuple(reasons or ["TRADABLE"]),
                                 instrument_id=canonical_instrument(instrument_id), trade_date=trade_date,
                                 lot_size=state.lot_size if state else None, industry=state.industry if state else None,
                                 min_buy_shares=(state.min_buy_shares if state.min_buy_shares is not None else
                                     100 if state.board == "MAIN" and state.lot_size == 100 else None) if state else None,
                                 security_version=self.security_master.version, calendar_version=self.calendar.version)

    def _evaluate(self, instrument_id, decision_at, trade_date, side, price):
        instrument_id = canonical_instrument(instrument_id)
        state = None
        decision, reasons = "ACCEPT", []
        try:
            aware(decision_at)
            if side not in ("BUY", "SELL"):
                raise ValueError("invalid side")
            if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
                raise ValueError("invalid raw execution price")
            if not self.calendar.is_session(trade_date):
                decision, reasons = "REJECT", ["NON_TRADING_DAY"]
            else:
                state = self.security_master.resolve(instrument_id, trade_date, decision_at)
                required = (state.listing_date, state.board, state.is_st, state.suspended, state.lot_size, state.industry)
                if any(v is None or v == "" for v in required):
                    raise UnavailableError("SECURITY_FACTS_MISSING", "data")
                if side == "BUY" and state.min_buy_shares is None and not (state.board == "MAIN" and state.lot_size == 100):
                    raise UnavailableError("MIN_BUY_SHARES_UNAVAILABLE", "data")
                if trade_date < state.listing_date or (state.delisting_date and trade_date >= state.delisting_date):
                    decision, reasons = "REJECT", ["NOT_LISTED"]
                elif state.suspended:
                    decision, reasons = "REJECT", ["SUSPENDED"]
                elif side == "BUY" and state.is_st and not self.allow_st:
                    decision, reasons = "REJECT", ["ST_EXCLUDED"]
                elif state.limits_date != trade_date or state.no_price_limit is None:
                    raise UnavailableError("TRADE_DATE_LIMITS_UNAVAILABLE", "data")
                elif not state.no_price_limit and (state.limit_up is None or state.limit_down is None):
                    raise UnavailableError("PRICE_LIMITS_MISSING", "data")
                elif side == "BUY" and not state.no_price_limit and price >= state.limit_up:
                    decision, reasons = "REJECT", ["LIMIT_UP"]
                elif side == "SELL" and not state.no_price_limit and price <= state.limit_down:
                    decision, reasons = "REJECT", ["LIMIT_DOWN"]
                elif side == "BUY" and self.min_listing_sessions > 1 and self.calendar.count_sessions(state.listing_date, trade_date) < self.min_listing_sessions:
                    decision, reasons = "REJECT", ["INSUFFICIENT_LISTING_SESSIONS"]
        except UnavailableError as exc:
            decision, reasons = "UNAVAILABLE", [exc.reason]
        return self._result(instrument_id, trade_date, decision, reasons, state)


class FundamentalValue(CoreModel):
    name: str = Field(min_length=1)
    value: float
    unit: str = Field(min_length=1)


class FundamentalRecord(CoreModel):
    instrument_id: str
    period_end: date
    published_at: datetime
    available_at: datetime
    revision: int = Field(ge=0)
    provider: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    pit_verified: bool = False
    values: tuple[FundamentalValue, ...]

    @model_validator(mode="after")
    def validate_record(self):
        if canonical_instrument(self.instrument_id) != self.instrument_id:
            raise ValueError("noncanonical fundamental instrument")
        aware(self.published_at)
        aware(self.available_at)
        if self.published_at > self.available_at or self.period_end > self.published_at.astimezone(SHANGHAI).date():
            raise ValueError("invalid fundamental visibility dates")
        if len({v.name for v in self.values}) != len(self.values):
            raise ValueError("duplicate fundamental values")
        return self


class PITFundamentals:
    def __init__(self, records: Iterable[FundamentalRecord] = ()):
        self.records = tuple(records)

    def visible(self, instrument_id: str, as_of: datetime) -> tuple[FundamentalRecord, ...]:
        instrument_id = canonical_instrument(instrument_id)
        eligible = [r for r in self.records if r.instrument_id == instrument_id and r.published_at <= aware(as_of)
                    and r.available_at <= as_of and r.pit_verified]
        if not eligible:
            raise UnavailableError("PIT_UNAVAILABLE", "data")
        latest, revisions = {}, {}
        for record in sorted(eligible, key=lambda r: (r.available_at, r.revision)):
            key = record.period_end
            revision_key = (key, record.revision)
            previous = revisions.get(revision_key)
            if previous is not None and record != previous:
                raise UnavailableError("AMBIGUOUS_FUNDAMENTAL_REVISION", "data")
            revisions[revision_key] = record
            old = latest.get(key)
            if old and record.revision == old.revision and record != old:
                raise UnavailableError("AMBIGUOUS_FUNDAMENTAL_REVISION", "data")
            if old is None or record.revision > old.revision:
                latest[key] = record
        return tuple(latest[key] for key in sorted(latest))


class DataFile(CoreModel):
    path: str
    size: int
    sha256: str


class DatasetManifest(CoreModel):
    snapshot_id: str
    digest: str
    provider: str
    quality: DataQuality
    files: tuple[DataFile, ...]
    observed_at: datetime
    price_adjustment: PriceAdjustment = "QLIB_NORMALIZED"
    raw_price_available: bool = False
    production_classification: Literal["REVIEW_REQUIRED"] = "REVIEW_REQUIRED"


class LocalQlibDataAdapter:
    """Read local binaries as adjusted research data, not executable raw prices.

    Manifest digest = SHA256(canonical JSON list of {path,size,sha256}, sorted
    by POSIX relative path). Quant must copy/pin files and compare manifests
    before/after inference. Files outside calendars/instruments/features ignored.
    Original vendor publication times/security facts cannot be recovered here.
    """
    def __init__(self, data_dir: str | Path, provider: str = "chenditc/investment_data"):
        self.data_dir = Path(data_dir).resolve()
        self.provider = provider

    def manifest(self) -> DatasetManifest:
        files = []
        for folder in ("calendars", "instruments", "features"):
            path = self.data_dir / folder
            if not path.is_dir() or path.is_symlink():
                raise UnavailableError("QLIB_DATA_INCOMPLETE", "data")
            for source in sorted(path.rglob("*")):
                if source.is_symlink() or not source.resolve().is_relative_to(self.data_dir):
                    raise UnavailableError("QLIB_DATA_SYMLINK_REJECTED", "data")
                if source.is_file():
                    before = source.stat()
                    h = hashlib.sha256()
                    with source.open("rb") as handle:
                        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                            h.update(chunk)
                    after = source.stat()
                    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        raise UnavailableError("DATA_CHANGED_DURING_HASH", "data")
                    files.append(DataFile(path=source.relative_to(self.data_dir).as_posix(), size=after.st_size, sha256=h.hexdigest()))
        files.sort(key=lambda f: f.path)
        if not any(f.path.endswith("close.day.bin") for f in files):
            raise UnavailableError("QLIB_FEATURES_MISSING", "data")
        value = content_digest([f.model_dump(mode="json") for f in files])
        return DatasetManifest(snapshot_id=value, digest=value, provider=self.provider, quality="RESEARCH_ONLY", files=tuple(files), observed_at=utc_now())

    def calendar(self) -> TradingCalendar:
        try:
            sessions = [date.fromisoformat(line.strip()) for line in (self.data_dir / "calendars/day.txt").read_text(encoding="utf-8-sig").splitlines() if line.strip()]
            return TradingCalendar(sessions)
        except (OSError, ValueError) as exc:
            raise UnavailableError("QLIB_CALENDAR_INVALID", "data") from exc

    def _field(self, instrument: str, name: str, index: int) -> float | None:
        symbol, exchange = instrument.split(".")
        path = self.data_dir / "features" / f"{exchange}{symbol}".lower() / f"{name}.day.bin"
        if not path.is_file():
            return None
        with path.open("rb") as handle:
            header = handle.read(4)
            if len(header) != 4:
                raise UnavailableError("QLIB_BINARY_INVALID", "data")
            start = struct.unpack("<f", header)[0]
            if not math.isfinite(start) or start < 0 or start != int(start):
                raise UnavailableError("QLIB_BINARY_INDEX_INVALID", "data")
            offset = index - int(start)
            if offset < 0:
                return None
            handle.seek((offset + 1) * 4)
            raw = handle.read(4)
            if len(raw) != 4:
                return None
            value = struct.unpack("<f", raw)[0]
            return value if math.isfinite(value) else None

    def snapshot(self, as_of: datetime | date, instruments: Iterable[str]) -> tuple[CanonicalMarketBar, ...]:
        manifest = self.manifest()
        calendar = self.calendar()
        day = aware(as_of).astimezone(SHANGHAI).date() if isinstance(as_of, datetime) else as_of
        if not calendar.is_session(day):
            raise UnavailableError("QLIB_NON_SESSION_REQUEST", "data")
        timestamp = datetime.combine(day, time(15), SHANGHAI)
        if timestamp > manifest.observed_at or (isinstance(as_of, datetime) and timestamp > as_of):
            raise UnavailableError("QLIB_FUTURE_BAR", "data")
        index = calendar.sessions.index(day)
        bars = []
        for identity in instruments:
            identity = canonical_instrument(identity)
            values = {name: self._field(identity, name, index) for name in ("open", "high", "low", "close")}
            bars.append(CanonicalMarketBar(instrument_id=identity, exchange=identity[-2:], symbol=identity[:6], timestamp=timestamp,
                        **values, volume=None, turnover=None, price_adjustment="QLIB_NORMALIZED", adjustment_factor=self._field(identity, "factor", index),
                        provider=self.provider, source_timestamp=None, available_at=manifest.observed_at, quality="RESEARCH_ONLY",
                        quality_notes=("RAW_PRICE_UNAVAILABLE", "RAW_VOLUME_UNAVAILABLE", "ORIGINAL_AVAILABILITY_UNKNOWN", "SECURITY_STATE_UNAVAILABLE"), snapshot_id=manifest.snapshot_id))
        if self.manifest().digest != manifest.digest:
            raise UnavailableError("DATA_CHANGED_DURING_READ", "data")
        return tuple(bars)


def provider_classification(provider: str) -> dict:
    """Engineering entitlement inventory; unknown sources always require review."""
    registry = json.loads(Path(__file__).with_name("provider_classification.json").read_text(encoding="utf-8"))
    return registry["providers"].get(provider, {"classification": "REVIEW_REQUIRED", "reason": "UNREGISTERED_PROVIDER"}).copy()
