"""Explicit, sourced context. Business validity never substitutes for visibility.

These records are supplied by the caller; Live does not infer previous close,
limits, historical eligibility or industry membership from minute prices.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import Field, StrictBool, field_validator, model_validator

from ..common import FrozenModel, LiveError, aware, canonical_instrument
from .market import SourceReceipt
from .quality import PITStatus, SourceTimeBasis


class ContextRecord(FrozenModel):
    source_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider: str = Field(min_length=1)
    source_timestamp: datetime | None = None
    source_timestamp_basis: SourceTimeBasis = "UNKNOWN"
    available_at: datetime
    pit_status: PITStatus = "RESEARCH_ONLY"
    effective_from: datetime
    effective_to: datetime | None = None  # half-open interval

    @field_validator("source_timestamp", "available_at", "effective_from", "effective_to")
    @classmethod
    def clocks(cls, value):
        return aware(value) if value is not None else value

    @model_validator(mode="after")
    def chronology(self):
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("context effective interval must be nonempty")
        if self.source_timestamp is not None and self.source_timestamp > self.available_at:
            raise ValueError("context source follows availability")
        if self.pit_status == "VERIFIED_PIT" and (
            self.source_timestamp is None or self.source_timestamp_basis != "PUBLISHED"
        ):
            raise ValueError("VERIFIED_PIT requires publication evidence")
        if self.provider == "mock" and self.pit_status != "TEST_FIXTURE":
            raise ValueError("mock context must remain TEST_FIXTURE")
        return self

    def effective(self, as_of: datetime) -> bool:
        return self.effective_from <= as_of and (self.effective_to is None or as_of < self.effective_to)


class InstrumentContext(ContextRecord):
    instrument_id: str

    @field_validator("instrument_id")
    @classmethod
    def identity(cls, value):
        if value != canonical_instrument(value):
            raise ValueError("context identity must be canonical")
        return value


class ReferencePrice(InstrumentContext):
    trading_date: date
    previous_close: float | None = Field(default=None, gt=0)
    limit_up: float | None = Field(default=None, gt=0)
    limit_down: float | None = Field(default=None, gt=0)
    price_adjustment: Literal["RAW"] = "RAW"

    @field_validator("previous_close", "limit_up", "limit_down", mode="before")
    @classmethod
    def numeric(cls, value):
        if isinstance(value, bool):
            raise ValueError("boolean is not a reference price")
        return value

    @model_validator(mode="after")
    def limits(self):
        if self.limit_down is not None and self.limit_up is not None and self.limit_down > self.limit_up:
            raise ValueError("inverted price limits")
        return self


class SecurityStatus(InstrumentContext):
    trading_eligible: StrictBool | None = None
    is_st: StrictBool | None = None
    suspended: StrictBool | None = None
    size_bucket: Literal["LARGE", "SMALL", "UNKNOWN"] = "UNKNOWN"
    previous_limit_up: StrictBool | None = None
    previous_board_height: int | None = Field(default=None, ge=0)

    @field_validator("previous_board_height", mode="before")
    @classmethod
    def board_numeric(cls, value):
        if isinstance(value, bool):
            raise ValueError("boolean is not board height")
        return value

    @model_validator(mode="after")
    def board_facts(self):
        if self.previous_board_height is not None and self.previous_limit_up is not None:
            if (self.previous_board_height > 0) != self.previous_limit_up:
                raise ValueError("previous limit-up and board-height facts conflict")
        return self


class SectorMembership(InstrumentContext):
    classification: Literal["SW_LEVEL1"] = "SW_LEVEL1"
    classification_version: str = Field(min_length=1)
    sector_id: str = Field(min_length=1)
    sector_name: str = Field(min_length=1)


class TurnoverHistory(InstrumentContext):
    observation_date: date
    minute_index: int = Field(ge=1, le=240)
    cumulative_turnover: float = Field(ge=0)

    @field_validator("cumulative_turnover", mode="before")
    @classmethod
    def numeric(cls, value):
        if isinstance(value, bool):
            raise ValueError("boolean is not turnover")
        return value


class DailyContext(InstrumentContext):
    based_on_date: date
    long_return_20d_pct: float | None = None
    medium_return_5d_pct: float | None = None
    yesterday_sentiment: Literal["WEAK", "REPAIR", "EXPANSION", "STRONG", "DIVERGENCE", "RETREAT", "UNKNOWN"] = "UNKNOWN"

    @field_validator("long_return_20d_pct", "medium_return_5d_pct", mode="before")
    @classmethod
    def numeric(cls, value):
        if isinstance(value, bool):
            raise ValueError("boolean is not a return")
        return value


class UniverseCoverage(ContextRecord):
    """A sourced complete market inventory, separate from DatasetScope."""
    trading_date: date
    coverage: Literal["COMPLETE_MARKET", "DECLARED_SCOPE_ONLY"] = "DECLARED_SCOPE_ONLY"
    instrument_ids: tuple[str, ...] = Field(min_length=1)

    @field_validator("instrument_ids")
    @classmethod
    def identities(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("duplicate universe identity")
        if any(value != canonical_instrument(value) for value in values):
            raise ValueError("universe identities must be canonical")
        return tuple(sorted(values))


def validate_context_receipt(record: ContextRecord, receipt: SourceReceipt, cutoff: datetime | None = None) -> None:
    if (record.source_id != receipt.source_id or record.provider != receipt.provider
        or record.pit_status != receipt.pit_status
        or record.source_timestamp_basis != receipt.source_timestamp_basis):
        raise LiveError("CONTEXT_RECEIPT_MISMATCH")
    if receipt.available_at > record.available_at or receipt.received_at > record.available_at:
        raise LiveError("CONTEXT_PARENT_AFTER_RECORD")
    if receipt.source_timestamp is not None and (
        record.source_timestamp is None or receipt.source_timestamp > record.source_timestamp
    ):
        raise LiveError("CONTEXT_PARENT_SOURCE_AFTER_RECORD")
    if cutoff is not None:
        cutoff = aware(cutoff)
        if any(value is not None and value > cutoff for value in (
            record.available_at, record.source_timestamp, receipt.available_at, receipt.received_at, receipt.source_timestamp
        )):
            raise LiveError("FUTURE_CONTEXT_ACCESS_DENIED")


class ContextBundle(FrozenModel):
    schema_version: Literal["LIVE_CONTEXT_V1"] = "LIVE_CONTEXT_V1"
    receipts: tuple[SourceReceipt, ...] = ()
    reference_prices: tuple[ReferencePrice, ...] = ()
    security_statuses: tuple[SecurityStatus, ...] = ()
    sector_memberships: tuple[SectorMembership, ...] = ()
    turnover_history: tuple[TurnoverHistory, ...] = ()
    daily_context: tuple[DailyContext, ...] = ()
    universe_coverage: tuple[UniverseCoverage, ...] = ()

    @model_validator(mode="after")
    def provenance(self):
        parents = {receipt.source_id: receipt for receipt in self.receipts}
        if len(parents) != len(self.receipts):
            raise ValueError("duplicate context receipt")
        for record in self.records():
            parent = parents.get(record.source_id)
            if parent is None:
                raise LiveError("CONTEXT_RECEIPT_MISSING")
            validate_context_receipt(record, parent)
        return self

    def records(self):
        return (*self.reference_prices, *self.security_statuses, *self.sector_memberships,
                *self.turnover_history, *self.daily_context, *self.universe_coverage)

    def visible(self, records, as_of: datetime):
        """Do not use future downloads, even for a historically effective row."""
        now = aware(as_of)
        parents = {receipt.source_id: receipt for receipt in self.receipts}
        result = []
        for record in records:
            if record.available_at > now or not record.effective(now):
                continue
            parent = parents.get(record.source_id)
            if parent is None:
                raise LiveError("CONTEXT_RECEIPT_MISSING")
            validate_context_receipt(record, parent, now)
            result.append(record)
        return tuple(result)


def latest_records(records, *, key=lambda item: item.instrument_id):
    """Visible revisions supersede earlier captures; tied conflicts fail closed."""
    selected = {}
    for record in records:
        identity = key(record)
        previous = selected.get(identity)
        if previous is not None and previous.available_at == record.available_at and previous != record:
            raise LiveError("CONTEXT_AMBIGUOUS_REVISION")
        if previous is None or record.available_at > previous.available_at:
            selected[identity] = record
    return selected
