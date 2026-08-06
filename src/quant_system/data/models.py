"""Governed, vendor-neutral daily Bar contract models.

This module is deliberately limited to the Bar contract used by the synthetic
Phase 2 kernel.  It does not contain a provider adapter, calendar service, or
instrument registry.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum, StrEnum
from pathlib import Path
from typing import Any, ClassVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

BAR_SCHEMA_NAME = "Bar"
BAR_SCHEMA_VERSION = "1.0.0"
DAILY_RESOLUTION = "1d"
BAR_FIELDS = (
    "schema_name",
    "schema_version",
    "record_id",
    "instrument_id",
    "symbol",
    "bar_start_time",
    "bar_end_time",
    "event_time",
    "availability_time",
    "ingestion_time",
    "as_of_time",
    "exchange_timezone",
    "session",
    "resolution",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "price_basis",
    "adjustment_method_version",
    "source",
    "data_version",
    "data_manifest_sha256",
    "quality_status",
    "quality_issues",
)
DERIVED_BAR_FIELDS = frozenset({"data_version", "data_manifest_sha256"})
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
UTC_TIMESTAMP_PATTERN = re.compile(r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z")


class ContractError(ValueError):
    """Base error for contract parsing and validation failures."""


class ValidationError(ContractError):
    """Raised when a Bar violates a fail-closed invariant."""


class Session(StrEnum):
    REGULAR = "REGULAR"


class Resolution(StrEnum):
    DAILY = DAILY_RESOLUTION


class PriceBasis(StrEnum):
    RAW = "RAW"


class QualityStatus(StrEnum):
    VALID = "VALID"
    SUSPECT = "SUSPECT"
    QUARANTINED = "QUARANTINED"
    REJECTED = "REJECTED"


class QualityIssue(StrEnum):
    MISSING = "MISSING"
    DUPLICATE = "DUPLICATE"
    LATE = "LATE"
    STALE = "STALE"
    INVALID = "INVALID"


def _require_non_empty_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValidationError(f"{field} must be a non-empty string")
    return value


def _parse_utc_timestamp(value: object, field: str) -> datetime:
    text = _require_non_empty_string(value, field)
    if not UTC_TIMESTAMP_PATTERN.fullmatch(text):
        raise ValidationError(f"{field} must be an ISO-8601 UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as error:
        raise ValidationError(f"{field} is not a valid timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValidationError(f"{field} must carry UTC timezone information")
    return parsed


def _validate_datetime(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValidationError(f"{field} must be timezone-aware")
    if value.utcoffset() != timedelta(0):
        raise ValidationError(f"{field} must be UTC")
    return value.astimezone(timezone.utc)  # noqa: UP017


def _timestamp_to_json(value: datetime) -> str:
    normalized = _validate_datetime(value, "timestamp")
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_decimal_string(value: object, field: str) -> Decimal:
    if type(value) is not str or not value or value.strip() != value:
        raise ValidationError(f"{field} must be a decimal string")
    if "e" in value.lower():
        raise ValidationError(f"{field} must not use scientific notation")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise ValidationError(f"{field} is not a valid decimal string") from error
    if not parsed.is_finite():
        raise ValidationError(f"{field} must be finite")
    if parsed.is_signed() or parsed < 0:
        raise ValidationError(f"{field} must not be negative")
    return parsed


def _validate_decimal(value: Decimal, field: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise ValidationError(f"{field} must be Decimal internally")
    if not value.is_finite() or value.is_signed() or value < 0:
        raise ValidationError(f"{field} must be a finite non-negative Decimal")
    return value


def _parse_volume(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValidationError("volume must be a non-negative integer")
    return value


def _validate_instrument_id(value: str, symbol: str) -> str:
    if value.upper().startswith("US-EQUITY:"):
        raise ValidationError("instrument_id must not be generated from a symbol")
    if value.casefold() == symbol.casefold():
        raise ValidationError("instrument_id must be opaque and distinct from symbol")
    return value


def _validate_timezone(value: str) -> str:
    try:
        zone = ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValidationError(f"exchange_timezone is not a valid IANA timezone: {value}") from error
    if not zone.key:
        raise ValidationError("exchange_timezone must name an IANA timezone")
    return value


def _parse_enum(enum_type: type[Enum], value: object, field: str) -> Any:
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be an enum string")
    try:
        return enum_type(value)
    except ValueError as error:
        allowed = ", ".join(item.value for item in enum_type)
        raise ValidationError(f"{field} must be one of: {allowed}") from error


def _validate_sha256(value: object, field: str) -> str:
    text = _require_non_empty_string(value, field)
    if not SHA256_PATTERN.fullmatch(text):
        raise ValidationError(f"{field} must be a lowercase SHA-256")
    return text


def _parse_quality_issues(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValidationError("quality_issues must be an array")
    issues: list[str] = []
    allowed = {item.value for item in QualityIssue}
    for item in value:
        if not isinstance(item, str) or item not in allowed:
            raise ValidationError("quality_issues contains an unsupported reason code")
        if item not in issues:
            issues.append(item)
    return tuple(issues)


@dataclass(frozen=True, slots=True)
class Bar:
    """Validated daily raw Bar with explicit point-in-time timestamps."""

    schema_name: str
    schema_version: str
    record_id: str
    instrument_id: str
    symbol: str
    bar_start_time: datetime
    bar_end_time: datetime
    event_time: datetime
    availability_time: datetime
    ingestion_time: datetime
    as_of_time: datetime
    exchange_timezone: str
    session: Session
    resolution: Resolution
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    price_basis: PriceBasis
    adjustment_method_version: str | None
    source: str
    data_version: str
    data_manifest_sha256: str
    quality_status: QualityStatus
    quality_issues: tuple[str, ...]

    REQUIRED_FIELDS: ClassVar[tuple[str, ...]] = BAR_FIELDS

    def __post_init__(self) -> None:
        if self.schema_name != BAR_SCHEMA_NAME:
            raise ValidationError(f"schema_name must be {BAR_SCHEMA_NAME}")
        if self.schema_version != BAR_SCHEMA_VERSION:
            raise ValidationError(f"unsupported Bar schema_version: {self.schema_version}")
        for field in ("record_id", "symbol", "source"):
            _require_non_empty_string(getattr(self, field), field)
        _require_non_empty_string(self.instrument_id, "instrument_id")
        _validate_instrument_id(self.instrument_id, self.symbol)
        _validate_timezone(self.exchange_timezone)

        timestamps = {
            field: _validate_datetime(getattr(self, field), field)
            for field in (
                "bar_start_time",
                "bar_end_time",
                "event_time",
                "availability_time",
                "ingestion_time",
                "as_of_time",
            )
        }
        for field, value in timestamps.items():
            object.__setattr__(self, field, value)
        if self.bar_start_time >= self.bar_end_time:
            raise ValidationError("bar_start_time must be before bar_end_time")
        if self.event_time != self.bar_end_time:
            raise ValidationError("event_time must equal bar_end_time")
        if self.ingestion_time < self.availability_time:
            raise ValidationError("ingestion_time must not precede availability_time")
        if self.as_of_time < self.ingestion_time:
            raise ValidationError("as_of_time must not precede ingestion_time")
        if self.availability_time > self.as_of_time:
            raise ValidationError("availability_time must not follow as_of_time")

        session = (
            self.session
            if isinstance(self.session, Session)
            else _parse_enum(Session, self.session, "session")
        )
        resolution = (
            self.resolution
            if isinstance(self.resolution, Resolution)
            else _parse_enum(Resolution, self.resolution, "resolution")
        )
        price_basis = (
            self.price_basis
            if isinstance(self.price_basis, PriceBasis)
            else _parse_enum(PriceBasis, self.price_basis, "price_basis")
        )
        quality_status = (
            self.quality_status
            if isinstance(self.quality_status, QualityStatus)
            else _parse_enum(QualityStatus, self.quality_status, "quality_status")
        )
        object.__setattr__(self, "session", session)
        object.__setattr__(self, "resolution", resolution)
        object.__setattr__(self, "price_basis", price_basis)
        object.__setattr__(self, "quality_status", quality_status)
        if session is not Session.REGULAR:
            raise ValidationError("this kernel only accepts REGULAR synthetic sessions")
        if resolution is not Resolution.DAILY:
            raise ValidationError("this kernel only accepts 1d Bars")
        if price_basis is not PriceBasis.RAW:
            raise ValidationError("this kernel only accepts RAW Bars")
        if self.adjustment_method_version is not None:
            raise ValidationError("RAW Bars must have adjustment_method_version=null")

        for field in ("open", "high", "low", "close"):
            _validate_decimal(getattr(self, field), field)
        if self.high < max(self.open, self.low, self.close):
            raise ValidationError("high must be at least open, low, and close")
        if self.low > min(self.open, self.high, self.close):
            raise ValidationError("low must be at most open, high, and close")
        _parse_volume(self.volume)
        issues = tuple(self.quality_issues)
        allowed = {item.value for item in QualityIssue}
        if any(issue not in allowed for issue in issues):
            raise ValidationError("quality_issues contains an unsupported reason code")
        object.__setattr__(self, "quality_issues", issues)
        if quality_status is QualityStatus.VALID and issues:
            raise ValidationError("VALID Bars must not contain quality issues")
        if quality_status is not QualityStatus.VALID and not issues:
            raise ValidationError("non-VALID Bars must contain quality issues")
        if QualityIssue.INVALID.value in issues and quality_status not in {
            QualityStatus.QUARANTINED,
            QualityStatus.REJECTED,
        }:
            raise ValidationError("INVALID Bars must be quarantined or rejected")
        _validate_sha256(self.data_version, "data_version")
        _validate_sha256(self.data_manifest_sha256, "data_manifest_sha256")

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object], *, reject_unknown: bool = True) -> Bar:
        """Parse the strict JSON-boundary representation of a Bar."""

        missing = [field for field in BAR_FIELDS if field not in payload]
        if missing:
            raise ValidationError(f"Bar is missing required fields: {', '.join(missing)}")
        if reject_unknown:
            unknown = sorted(set(payload) - set(BAR_FIELDS))
            if unknown:
                raise ValidationError(f"Bar contains unknown fields: {', '.join(unknown)}")
        return cls(
            schema_name=_require_non_empty_string(payload["schema_name"], "schema_name"),
            schema_version=_require_non_empty_string(payload["schema_version"], "schema_version"),
            record_id=_require_non_empty_string(payload["record_id"], "record_id"),
            instrument_id=_require_non_empty_string(payload["instrument_id"], "instrument_id"),
            symbol=_require_non_empty_string(payload["symbol"], "symbol"),
            bar_start_time=_parse_utc_timestamp(payload["bar_start_time"], "bar_start_time"),
            bar_end_time=_parse_utc_timestamp(payload["bar_end_time"], "bar_end_time"),
            event_time=_parse_utc_timestamp(payload["event_time"], "event_time"),
            availability_time=_parse_utc_timestamp(
                payload["availability_time"], "availability_time"
            ),
            ingestion_time=_parse_utc_timestamp(payload["ingestion_time"], "ingestion_time"),
            as_of_time=_parse_utc_timestamp(payload["as_of_time"], "as_of_time"),
            exchange_timezone=_validate_timezone(
                _require_non_empty_string(payload["exchange_timezone"], "exchange_timezone")
            ),
            session=_parse_enum(Session, payload["session"], "session"),
            resolution=_parse_enum(Resolution, payload["resolution"], "resolution"),
            open=_parse_decimal_string(payload["open"], "open"),
            high=_parse_decimal_string(payload["high"], "high"),
            low=_parse_decimal_string(payload["low"], "low"),
            close=_parse_decimal_string(payload["close"], "close"),
            volume=_parse_volume(payload["volume"]),
            price_basis=_parse_enum(PriceBasis, payload["price_basis"], "price_basis"),
            adjustment_method_version=(
                None
                if payload["adjustment_method_version"] is None
                else _require_non_empty_string(
                    payload["adjustment_method_version"], "adjustment_method_version"
                )
            ),
            source=_require_non_empty_string(payload["source"], "source"),
            data_version=_validate_sha256(payload["data_version"], "data_version"),
            data_manifest_sha256=_validate_sha256(
                payload["data_manifest_sha256"], "data_manifest_sha256"
            ),
            quality_status=_parse_enum(QualityStatus, payload["quality_status"], "quality_status"),
            quality_issues=_parse_quality_issues(payload["quality_issues"]),
        )

    @classmethod
    def from_json(cls, text: str) -> Bar:
        """Parse one Bar while rejecting JSON NaN and Infinity constants."""

        def reject_constant(value: str) -> None:
            raise ValidationError(f"non-finite JSON constant is not allowed: {value}")

        payload = json.loads(text, parse_constant=reject_constant)
        if not isinstance(payload, dict):
            raise ValidationError("Bar JSON root must be an object")
        return cls.from_mapping(payload)

    @classmethod
    def from_json_file(cls, path: Path) -> Bar:
        return cls.from_json(path.read_text(encoding="utf-8"))

    def to_mapping(self, *, include_derived: bool = True) -> dict[str, object]:
        """Return the stable JSON-boundary representation."""

        payload: dict[str, object] = {
            "schema_name": self.schema_name,
            "schema_version": self.schema_version,
            "record_id": self.record_id,
            "instrument_id": self.instrument_id,
            "symbol": self.symbol,
            "bar_start_time": _timestamp_to_json(self.bar_start_time),
            "bar_end_time": _timestamp_to_json(self.bar_end_time),
            "event_time": _timestamp_to_json(self.event_time),
            "availability_time": _timestamp_to_json(self.availability_time),
            "ingestion_time": _timestamp_to_json(self.ingestion_time),
            "as_of_time": _timestamp_to_json(self.as_of_time),
            "exchange_timezone": self.exchange_timezone,
            "session": self.session.value,
            "resolution": self.resolution.value,
            "open": str(self.open),
            "high": str(self.high),
            "low": str(self.low),
            "close": str(self.close),
            "volume": self.volume,
            "price_basis": self.price_basis.value,
            "adjustment_method_version": self.adjustment_method_version,
            "source": self.source,
            "data_version": self.data_version,
            "data_manifest_sha256": self.data_manifest_sha256,
            "quality_status": self.quality_status.value,
            "quality_issues": list(self.quality_issues),
        }
        if not include_derived:
            for field in DERIVED_BAR_FIELDS:
                payload.pop(field)
        return payload

    def canonical_payload(self) -> dict[str, object]:
        """Return the logical payload used by the non-circular identity."""

        return self.to_mapping(include_derived=False)

    def identity_key(self) -> tuple[str, str, str, str, str]:
        return (
            self.instrument_id,
            _timestamp_to_json(self.bar_start_time),
            _timestamp_to_json(self.bar_end_time),
            self.resolution.value,
            self.session.value,
        )

    def with_lineage(self, *, data_version: str, data_manifest_sha256: str) -> Bar:
        return replace(
            self,
            data_version=_validate_sha256(data_version, "data_version"),
            data_manifest_sha256=_validate_sha256(data_manifest_sha256, "data_manifest_sha256"),
        )


def parse_bar(payload: Mapping[str, object]) -> Bar:
    """Convenience alias for strict mapping parsing."""

    return Bar.from_mapping(payload)


def load_bar_json(path: Path) -> Bar:
    return Bar.from_json_file(path)
