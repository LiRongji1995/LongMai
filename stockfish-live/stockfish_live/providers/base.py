from datetime import datetime
from typing import Protocol

from ..contracts.market import MarketSnapshot, MinuteBar


class LiveMarketProvider(Protocol):
    async def get_market_snapshot(self, as_of: datetime, *, since: datetime | None = None) -> MarketSnapshot:
        """Return newly available records in (since, as_of], with parent receipts."""
        ...

    async def get_minute_bars(self, start: datetime, end: datetime, *, as_of: datetime) -> tuple[MinuteBar, ...]:
        ...

    async def get_index_bars(self, start: datetime, end: datetime, *, as_of: datetime) -> tuple[MinuteBar, ...]:
        ...
