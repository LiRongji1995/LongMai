from __future__ import annotations

from datetime import date, datetime

from ..common import LiveError, aware
from ..contracts.market import MarketSnapshot, MinuteBar, SourceReceipt
from ..pit import PITGuard


class ReplayProvider:
    """Trusted archive loader. Consumers see only the clock-gated protocol.

    Full archive loading happens here, outside feature/inference state. Access
    to the backing filesystem/Python internals is not a process sandbox.
    """
    def __init__(self, bars: tuple[MinuteBar, ...], receipts: dict[str, SourceReceipt], clock):
        self._bars = tuple(sorted(bars, key=lambda b: (b.available_at, b.timestamp, b.instrument_id)))
        self._receipts = receipts.copy()
        self._guard = PITGuard(clock)

    @classmethod
    def from_store(cls, store, day: date, clock):
        return cls(store.read_day(day), store.receipts(), clock)

    def _visible(self, as_of: datetime) -> tuple[MinuteBar, ...]:
        cutoff = self._guard.cutoff(as_of)
        result = []
        for bar in self._bars:
            receipt = self._receipts.get(bar.source_id)
            if receipt is None:
                raise LiveError("SOURCE_RECEIPT_MISSING")
            clocks = (bar.timestamp, bar.source_timestamp, bar.available_at,
                      receipt.source_timestamp, receipt.received_at, receipt.available_at)
            if any(value is not None and value > cutoff for value in clocks):
                continue
            self._guard.check(bar, self._receipts, cutoff)
            result.append(bar)
        return tuple(result)

    async def get_market_snapshot(self, as_of: datetime, *, since: datetime | None = None) -> MarketSnapshot:
        cutoff = self._guard.cutoff(as_of)
        if since is not None:
            since = self._guard.cutoff(since)
            if since > cutoff:
                raise LiveError("INVALID_TIME_RANGE")
        bars = tuple(b for b in self._visible(cutoff) if since is None or b.available_at > since)
        refs = sorted({b.source_id for b in bars})
        return MarketSnapshot(as_of=cutoff, data_cutoff=max((b.available_at for b in bars), default=None),
                              provider="replay", bars=bars, receipts=tuple(self._receipts[r] for r in refs),
                              quality="VALID" if bars and all(b.quality == "VALID" for b in bars) else "PARTIAL")

    async def get_minute_bars(self, start: datetime, end: datetime, *, as_of: datetime) -> tuple[MinuteBar, ...]:
        start, end = aware(start), self._guard.cutoff(end)
        cutoff = self._guard.cutoff(as_of)
        if start > end or end > cutoff:
            raise LiveError("FUTURE_ACCESS_DENIED" if end > cutoff else "INVALID_TIME_RANGE")
        return tuple(b for b in self._visible(cutoff) if start <= b.timestamp <= end)

    async def get_index_bars(self, start: datetime, end: datetime, *, as_of: datetime) -> tuple[MinuteBar, ...]:
        return tuple(b for b in await self.get_minute_bars(start, end, as_of=as_of) if b.asset_type == "INDEX")
