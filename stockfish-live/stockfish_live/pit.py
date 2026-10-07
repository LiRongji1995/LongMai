"""Fail-closed time access; historical download clocks are never rebased."""
from datetime import datetime
from typing import Mapping

from .common import LiveError, aware
from .contracts.market import MinuteBar, SourceReceipt, validate_receipt


class ReplayClock:
    def __init__(self, initial: datetime):
        self._now = aware(initial)

    @property
    def now(self) -> datetime:
        return self._now

    def advance(self, value: datetime) -> None:
        value = aware(value)
        if value < self._now:
            raise LiveError("CLOCK_REVERSAL")
        self._now = value


class PITGuard:
    def __init__(self, clock):
        self.clock = clock

    def cutoff(self, value: datetime) -> datetime:
        value = aware(value)
        if value > self.clock.now:
            raise LiveError("FUTURE_ACCESS_DENIED")
        return value

    def check(self, bar: MinuteBar, receipts: Mapping[str, SourceReceipt], as_of: datetime) -> None:
        cutoff = self.cutoff(as_of)
        receipt = receipts.get(bar.source_id)
        if receipt is None:
            raise LiveError("SOURCE_RECEIPT_MISSING")
        validate_receipt(bar, receipt, cutoff)
