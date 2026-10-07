from __future__ import annotations

from bisect import bisect_right
from datetime import date, datetime

from ..common import LiveError, aware
from ..contracts.market import MarketSnapshot, MinuteBar, SourceReceipt, validate_receipt
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
        self._available = tuple(bar.available_at for bar in self._bars)
        # The fast path requires a complete, immutable, valid parent archive.
        # A malformed archive keeps the original full scan and its exact error
        # timing, including missing parents on bars beyond the requested cutoff.
        self._availability_index_safe = True
        for bar in self._bars:
            try:
                receipt = self._receipts.get(bar.source_id)
                if receipt is None:
                    raise LiveError("SOURCE_RECEIPT_MISSING")
                validate_receipt(bar, receipt)
            except Exception:
                self._availability_index_safe = False
                break

    @classmethod
    def from_store(cls, store, day: date, clock):
        bars = store.read_day(day)
        return cls(bars, {source: store._source(source) for source in sorted({bar.source_id for bar in bars})}, clock)

    def _visible(self, as_of: datetime, *, since: datetime | None = None) -> tuple[MinuteBar, ...]:
        cutoff = self._guard.cutoff(as_of)
        result = []
        if self._availability_index_safe:
            start = bisect_right(self._available, since) if since is not None else 0
            stop = bisect_right(self._available, cutoff)
            candidates = self._bars[start:stop]
        else:
            candidates = self._bars
        for bar in candidates:
            receipt = self._receipts.get(bar.source_id)
            if receipt is None:
                raise LiveError("SOURCE_RECEIPT_MISSING")
            clocks = (bar.timestamp, bar.source_timestamp, bar.available_at,
                      receipt.source_timestamp, receipt.received_at, receipt.available_at)
            if any(value is not None and value > cutoff for value in clocks):
                continue
            self._guard.check(bar, self._receipts, cutoff)
            result.append(bar)
        return tuple(bar for bar in result if since is None or bar.available_at > since)

    async def get_market_snapshot(self, as_of: datetime, *, since: datetime | None = None) -> MarketSnapshot:
        cutoff = self._guard.cutoff(as_of)
        if since is not None:
            since = self._guard.cutoff(since)
            if since > cutoff:
                raise LiveError("INVALID_TIME_RANGE")
        bars = self._visible(cutoff, since=since)
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
