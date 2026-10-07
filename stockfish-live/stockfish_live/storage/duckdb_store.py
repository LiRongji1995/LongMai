"""Fixed PIT queries over integrity-checked Parquet, using an in-memory DuckDB.

Phase 1 deliberately validates complete day partitions before Arrow registration.
This adds read/hash overhead; benchmark before adopting larger live workloads.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import duckdb
import pyarrow as pa

from ..common import LiveError, aware
from ..contracts.market import MinuteBar, validate_receipt
from .parquet_store import ParquetStore, SHANGHAI, table_for_bars


class DuckDBStore:
    def __init__(self, root: str | Path):
        self.parquet = ParquetStore(root)
        self.connection = duckdb.connect(":memory:")
        self._closed = False

    def close(self) -> None:
        if not self._closed:
            self.connection.close()
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def visible(self, day: date | str, *, cutoff: datetime, now: datetime,
                instrument_ids=None) -> tuple[MinuteBar, ...]:
        aware(cutoff)
        aware(now)
        if cutoff > now:
            raise LiveError("FUTURE_ACCESS_DENIED")
        if self._closed:
            raise LiveError("STORAGE_CLOSED")
        bars = self.parquet.read_day(day)
        if not bars:
            return ()
        receipts = {source: self.parquet._source(source) for source in {bar.source_id for bar in bars}}
        table = table_for_bars(bars)
        for column, field in (("_parent_available_at", "available_at"),
                              ("_parent_received_at", "received_at"),
                              ("_parent_source_timestamp", "source_timestamp")):
            values = [getattr(receipts[bar.source_id], field) for bar in bars]
            table = table.append_column(column, pa.array(values, type=pa.timestamp("us", tz="Asia/Shanghai")))
        self.connection.register("visible_minute_input", table)
        cutoff = cutoff.astimezone(SHANGHAI)
        query = """SELECT * EXCLUDE (_parent_available_at, _parent_received_at, _parent_source_timestamp)
                   FROM visible_minute_input
                   WHERE timestamp <= ? AND available_at <= ?
                     AND (source_timestamp IS NULL OR source_timestamp <= ?)
                     AND _parent_available_at <= ? AND _parent_received_at <= ?
                     AND (_parent_source_timestamp IS NULL OR _parent_source_timestamp <= ?)"""
        parameters = [cutoff] * 6
        if instrument_ids is not None:
            instruments = tuple(instrument_ids)
            if not instruments:
                self.connection.unregister("visible_minute_input")
                return ()
            if any(not isinstance(value, str) for value in instruments):
                self.connection.unregister("visible_minute_input")
                raise LiveError("INVALID_INSTRUMENT_FILTER")
            query += " AND instrument_id IN (" + ",".join("?" for _ in instruments) + ")"
            parameters.extend(instruments)
        query += " ORDER BY timestamp, instrument_id"
        try:
            cursor = self.connection.execute(query, parameters)
            arrow = cursor.to_arrow_table() if hasattr(cursor, "to_arrow_table") else cursor.fetch_arrow_table()
            rows = arrow.to_pylist()
        finally:
            self.connection.unregister("visible_minute_input")
        result = tuple(MinuteBar.model_validate(row) for row in rows)
        for bar in result:
            validate_receipt(bar, receipts[bar.source_id], cutoff=cutoff)
        return result
