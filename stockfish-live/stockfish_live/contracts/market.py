from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from ..common import FrozenModel, LiveError, aware, canonical_instrument
from .quality import DataQuality, PITStatus, SourceTimeBasis


class SourceReceipt(FrozenModel):
    source_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider: str = Field(min_length=1)
    source_timestamp: datetime | None = None
    source_timestamp_basis: SourceTimeBasis = "UNKNOWN"
    received_at: datetime
    available_at: datetime
    pit_status: PITStatus = "RESEARCH_ONLY"

    @field_validator("source_timestamp", "received_at", "available_at")
    @classmethod
    def times(cls, value):
        return aware(value) if value is not None else value

    @model_validator(mode="after")
    def clocks(self):
        if self.received_at > self.available_at:
            raise ValueError("receipt availability precedes actual receipt")
        if self.source_timestamp and self.source_timestamp > self.received_at:
            raise ValueError("source publication follows actual receipt")
        if self.pit_status == "VERIFIED_PIT" and (
            self.source_timestamp is None or self.source_timestamp_basis != "PUBLISHED"
        ):
            raise ValueError("VERIFIED_PIT requires evidenced publication time")
        if self.provider == "mock" and self.pit_status != "TEST_FIXTURE":
            raise ValueError("mock receipt must remain TEST_FIXTURE")
        return self


class MinuteBar(FrozenModel):
    schema_version: Literal["LIVE_MINUTE_V1"] = "LIVE_MINUTE_V1"
    instrument_id: str
    exchange: Literal["SH", "SZ", "BJ"]
    symbol: str
    asset_type: Literal["STOCK", "INDEX"]
    timestamp: datetime  # completed interval END; event_time is identical
    open: float | None = Field(default=None, gt=0)
    high: float | None = Field(default=None, gt=0)
    low: float | None = Field(default=None, gt=0)
    close: float | None = Field(default=None, gt=0)
    volume: float | None = Field(default=None, ge=0)  # shares; per minute
    turnover: float | None = Field(default=None, ge=0)  # CNY; per minute
    price_adjustment: Literal["RAW", "QFQ", "HFQ"] = "RAW"
    provider: str = Field(min_length=1)
    source_timestamp: datetime | None = None
    source_timestamp_basis: SourceTimeBasis = "UNKNOWN"
    available_at: datetime
    source_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    quality: DataQuality = "PARTIAL"
    pit_status: PITStatus = "RESEARCH_ONLY"

    @field_validator("open", "high", "low", "close", "volume", "turnover", mode="before")
    @classmethod
    def numeric(cls, value):
        if isinstance(value, bool):
            raise ValueError("boolean is not a market value")
        return value

    @field_validator("timestamp", "source_timestamp", "available_at")
    @classmethod
    def times(cls, value):
        return aware(value) if value is not None else value

    @model_validator(mode="after")
    def contract(self):
        if self.instrument_id != canonical_instrument(self.instrument_id, self.exchange):
            raise ValueError("noncanonical instrument")
        if self.symbol != self.instrument_id[:6]:
            raise ValueError("symbol mismatch")
        if self.timestamp.second or self.timestamp.microsecond:
            raise ValueError("minute END must be an exact minute")
        if self.timestamp > self.available_at:
            raise ValueError("incomplete future bar")
        if self.source_timestamp and not self.timestamp <= self.source_timestamp <= self.available_at:
            raise ValueError("invalid event/source/availability order")
        if self.low is not None and self.high is not None and self.low > self.high:
            raise ValueError("impossible OHLC")
        for value in (self.open, self.close):
            if value is not None and ((self.low is not None and value < self.low)
                                     or (self.high is not None and value > self.high)):
                raise ValueError("impossible OHLC")
        if self.quality == "VALID" and any(getattr(self, field) is None for field in (
            "open", "high", "low", "close", "volume", "turnover", "source_timestamp"
        )):
            raise ValueError("VALID cannot have missing fields")
        if self.pit_status == "VERIFIED_PIT" and (
            self.source_timestamp is None or self.source_timestamp_basis != "PUBLISHED"
        ):
            raise ValueError("VERIFIED_PIT requires publication evidence")
        if self.provider == "mock" and self.pit_status != "TEST_FIXTURE":
            raise ValueError("mock must remain TEST_FIXTURE")
        return self

    @property
    def event_time(self) -> datetime:
        return self.timestamp


def validate_receipt(bar: MinuteBar, receipt: SourceReceipt, cutoff: datetime | None = None) -> None:
    if (bar.source_id != receipt.source_id or bar.provider != receipt.provider
        or bar.pit_status != receipt.pit_status
        or bar.source_timestamp_basis != receipt.source_timestamp_basis):
        raise LiveError("SOURCE_RECEIPT_MISMATCH")
    if receipt.available_at > bar.available_at or receipt.received_at > bar.available_at:
        raise LiveError("PARENT_RECEIPT_AFTER_BAR")
    if bar.timestamp > receipt.received_at:
        raise LiveError("PARENT_RECEIPT_BEFORE_EVENT")
    if receipt.source_timestamp and (bar.source_timestamp is None or receipt.source_timestamp > bar.source_timestamp):
        raise LiveError("PARENT_SOURCE_AFTER_BAR")
    if cutoff is not None:
        cutoff = aware(cutoff)
        clocks = (bar.timestamp, bar.available_at, receipt.received_at, receipt.available_at,
                  bar.source_timestamp, receipt.source_timestamp)
        if any(value is not None and value > cutoff for value in clocks):
            raise LiveError("FUTURE_ACCESS_DENIED")


class MarketSnapshot(FrozenModel):
    schema_version: Literal["LIVE_SNAPSHOT_V1"] = "LIVE_SNAPSHOT_V1"
    as_of: datetime
    data_cutoff: datetime | None
    provider: str
    bars: tuple[MinuteBar, ...] = ()
    receipts: tuple[SourceReceipt, ...] = ()
    quality: DataQuality = "PARTIAL"
    warnings: tuple[str, ...] = ()
    scope: Literal["DECLARED_SCOPE_ONLY"] = "DECLARED_SCOPE_ONLY"

    @field_validator("as_of", "data_cutoff")
    @classmethod
    def times(cls, value):
        return aware(value) if value is not None else value

    @model_validator(mode="after")
    def visible(self):
        if self.data_cutoff and self.data_cutoff > self.as_of:
            raise ValueError("snapshot cutoff follows as_of")
        for bar in self.bars:
            if bar.timestamp > self.as_of or bar.available_at > self.as_of:
                raise ValueError("snapshot contains future data")
        return self


class Instrument(FrozenModel):
    instrument_id: str
    asset_type: Literal["STOCK", "INDEX"]

    @field_validator("instrument_id")
    @classmethod
    def identity(cls, value):
        if value != canonical_instrument(value):
            raise ValueError("scope identity must be canonical")
        return value


class DatasetScope(FrozenModel):
    """Expected stream scope, never a claim about historical trading eligibility."""
    schema_version: Literal["LIVE_DATASET_V1"] = "LIVE_DATASET_V1"
    scope: Literal["DECLARED_SCOPE_ONLY"] = "DECLARED_SCOPE_ONLY"
    calendar_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    instruments: tuple[Instrument, ...] = Field(min_length=1)
    available_at: datetime
    pit_status: PITStatus = "RESEARCH_ONLY"

    @field_validator("available_at")
    @classmethod
    def time(cls, value):
        return aware(value)

    @model_validator(mode="after")
    def unique(self):
        if len({item.instrument_id for item in self.instruments}) != len(self.instruments):
            raise ValueError("duplicate scope instrument")
        return self
