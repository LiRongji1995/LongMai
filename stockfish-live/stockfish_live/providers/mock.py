"""Explicit synthetic engineering dataset; never selected as fallback."""
from __future__ import annotations

import hashlib
from datetime import date, datetime, time
from pathlib import Path

from ..calendar import TradingCalendar
from ..common import LiveError, SHANGHAI, canonical_json
from ..contracts.market import DatasetScope, Instrument, MinuteBar, SourceReceipt
from ..storage.parquet_store import ParquetStore
from .replay import ReplayProvider

INDICES = ("000001.SH", "000300.SH", "000905.SH", "000852.SH", "399006.SZ")
STOCKS = ("600000.SH", "000001.SZ", "300001.SZ")


class MockProvider(ReplayProvider):
    def __init__(self, bars, receipts, clock):
        if any(b.pit_status != "TEST_FIXTURE" or b.provider != "mock" for b in bars):
            raise LiveError("MOCK_REQUIRES_TEST_FIXTURE")
        super().__init__(bars, receipts, clock)


def create_fixture(root: Path, day: date) -> dict:
    """Create a new, labelled full-session fixture, refusing existing roots."""
    root = Path(root)
    try:
        root.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise LiveError("FIXTURE_DESTINATION_EXISTS") from exc
    before_open = datetime.combine(day, time(9), SHANGHAI)
    calendar = TradingCalendar(schema_version="LIVE_CALENDAR_V1", sessions=(day,),
        coverage_start=day, coverage_end=day, exchanges=("SH", "SZ"),
        source="TEST_FIXTURE_EXPLICIT_SESSION_NOT_OFFICIAL_CALENDAR",
        available_at=before_open, pit_status="TEST_FIXTURE")
    scope = DatasetScope(calendar_version=calendar.version, available_at=before_open,
        pit_status="TEST_FIXTURE", instruments=tuple(Instrument(instrument_id=i,
        asset_type="INDEX" if i in INDICES else "STOCK") for i in INDICES + STOCKS))
    (root / "calendar.json").write_text(canonical_json(calendar), encoding="utf-8")
    (root / "dataset.json").write_text(canonical_json(scope), encoding="utf-8")
    store = ParquetStore(root)
    count = 0
    for minute_no, end in enumerate(calendar.minutes(day), 1):
        rows = []
        for offset, item in enumerate(scope.instruments):
            base = 3000. if item.asset_type == "INDEX" else 10.
            close = base + offset + minute_no * base / 100000
            rows.append(dict(instrument_id=item.instrument_id, exchange=item.instrument_id[-2:],
                symbol=item.instrument_id[:6], asset_type=item.asset_type, timestamp=end.isoformat(),
                open=close, high=close + base / 10000, low=close - base / 10000, close=close,
                volume=1000., turnover=1000. * close, provider="mock", quality="VALID",
                source_timestamp=end.isoformat(), source_timestamp_basis="PUBLISHED",
                available_at=end.isoformat(), pit_status="TEST_FIXTURE"))
        payload = canonical_json({"label": "TEST_FIXTURE", "rows": rows}).encode()
        source_id = hashlib.sha256(payload).hexdigest()
        bars = tuple(MinuteBar(**row, source_id=source_id) for row in rows)
        receipt = SourceReceipt(source_id=source_id, provider="mock", source_timestamp=end,
            source_timestamp_basis="PUBLISHED", received_at=end, available_at=end, pit_status="TEST_FIXTURE")
        store.append(bars, receipt, payload)
        count += len(bars)
    return {"label": "TEST_FIXTURE", "date": day.isoformat(), "rows": count,
            "instruments": len(scope.instruments), "completed_minutes": 240, "data_root": str(root.resolve())}
