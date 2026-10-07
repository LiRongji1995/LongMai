"""Bounded offline commands. No live network calls in Phase 1."""
from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime, time
from importlib.metadata import version
import math
import os
from pathlib import Path
import sys
from time import perf_counter
from uuid import uuid4

from pydantic import ValidationError

from . import BROKER_CONNECTION, LIVE_TRADING, MARKET_EDGE, __version__
from .calendar import TradingCalendar
from .common import LiveError, SHANGHAI, canonical_json
from .contracts.market import DatasetScope
from .pit import ReplayClock
from .ingest.normalizer import QualityMonitor
from .providers.mock import create_fixture
from .providers.replay import ReplayProvider
from .replay.engine import ReplayEngine, replay_speed
from .storage.duckdb_store import DuckDBStore
from .storage.parquet_store import ParquetStore


def _validate_scope_rows(rows, receipts, calendar, scope, cutoff):
    types = {i.instrument_id: i.asset_type for i in scope.instruments}
    for bar in rows:
        if types.get(bar.instrument_id) != bar.asset_type:
            raise LiveError("INSTRUMENT_TYPE_MISMATCH")
        if (bar.pit_status == "TEST_FIXTURE") != (scope.pit_status == "TEST_FIXTURE"):
            raise LiveError("DATA_PROVENANCE_MIXED")
    refs = {bar.source_id for bar in rows}
    monitor = QualityMonitor(calendar, tuple(types))
    monitor.accept(sorted(rows, key=lambda b: (b.available_at, b.timestamp, b.instrument_id)),
                   {key: receipts[key] for key in refs}, cutoff)
    return monitor.state(cutoff)


def _scope(root: Path) -> DatasetScope:
    try:
        return DatasetScope.model_validate_json((root / "dataset.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LiveError("DATASET_SCOPE_UNAVAILABLE") from exc


def doctor(root: Path) -> tuple[dict, int]:
    report = {"version": __version__, "environment": {"python": sys.version.split()[0],
        **{name: version(name) for name in ("pydantic", "pyarrow", "duckdb")}},
        "data_root": str(root.resolve()), "live_provider": "LIVE_PROVIDER_UNAVAILABLE",
        "model": "NOT_IMPLEMENTED_PHASE1", "market_edge": MARKET_EDGE,
        "live_trading": LIVE_TRADING, "broker_connection": BROKER_CONNECTION,
        "real_minute_validation": "NOT_PERFORMED", "research_ready": False}
    errors = []
    calendar = None
    scope = None
    try:
        calendar = TradingCalendar.from_file(root / "calendar.json")
        report["calendar"] = {"status": "CONFIGURED", "version": calendar.version,
            "sessions": len(calendar.sessions), "exchanges": calendar.exchanges,
            "coverage_start": calendar.coverage_start.isoformat(), "coverage_end": calendar.coverage_end.isoformat(),
            "pit_status": calendar.pit_status}
        scope = _scope(root)
        if scope.calendar_version != calendar.version:
            raise LiveError("SCOPE_CALENDAR_MISMATCH")
        if (scope.pit_status == "TEST_FIXTURE") != (calendar.pit_status == "TEST_FIXTURE"):
            raise LiveError("DATA_PROVENANCE_MIXED")
        report["scope"] = {"status": "DECLARED_SCOPE_ONLY", "instruments": len(scope.instruments),
                           "pit_status": scope.pit_status}
    except LiveError as exc:
        errors.append(exc.code)
    try:
        store = ParquetStore(root)
        coverage = store.coverage()
        report["historical_data_coverage"] = coverage
        report["storage"] = "AVAILABLE" if coverage.get("row_count", 0) else "UNAVAILABLE"
        if not coverage.get("row_count", 0):
            errors.append("MINUTE_DATA_UNAVAILABLE")
        if calendar and scope:
            strict_rows = 0
            declared_coverage = []
            for day_text in coverage.get("dates", []):
                day = date.fromisoformat(day_text)
                # Exercise actual DuckDB and strict cutoff, rather than only
                # checking that a module imports or a folder exists.
                cutoff = datetime.combine(day, time(15), SHANGHAI)
                with DuckDBStore(root) as query:
                    rows = query.visible(day, cutoff=cutoff, now=cutoff)
                    strict_rows += len(rows)
                before_open = datetime.combine(day, time(9, 30), SHANGHAI)
                calendar.require_session(day, before_open, tuple(i.instrument_id[-2:] for i in scope.instruments))
                if scope.available_at > before_open:
                    raise LiveError("SCOPE_NOT_YET_AVAILABLE")
                state = _validate_scope_rows(rows, store.receipts(), calendar, scope, cutoff)
                declared_coverage.append({"date": day_text, "strict_visible_rows": len(rows),
                    "expected_rows": len(scope.instruments) * 240, "gap_count": state["gap_count"],
                    "quality": state["quality"]})
            report["strict_visible_rows_at_close"] = strict_rows
            report["late_or_unavailable_rows"] = coverage.get("row_count", 0) - strict_rows
            report["declared_scope_coverage"] = declared_coverage
            if not strict_rows:
                errors.append("STRICT_REPLAY_DATA_UNAVAILABLE")
    except (LiveError, OSError, ValueError) as exc:
        errors.append(exc.code if isinstance(exc, LiveError) else "STORAGE_UNAVAILABLE")
    report["errors"] = errors
    report["replay_ready"] = not errors
    return report, 2 if errors else 0


async def replay(args, root: Path) -> tuple[dict, int]:
    calendar = TradingCalendar.from_file(root / "calendar.json")
    scope = _scope(root)
    replay_speed(args.speed)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise LiveError("INVALID_PROVIDER_TIMEOUT")
    if scope.calendar_version != calendar.version:
        raise LiveError("SCOPE_CALENDAR_MISMATCH")
    if (scope.pit_status == "TEST_FIXTURE") != (calendar.pit_status == "TEST_FIXTURE"):
        raise LiveError("DATA_PROVENANCE_MIXED")
    if args.date:
        days = (args.date,)
    else:
        if args.start is None or args.end is None or args.start > args.end:
            raise LiveError("REPLAY_DATE_RANGE_REQUIRED")
        if args.start < calendar.coverage_start or args.end > calendar.coverage_end:
            raise LiveError("CALENDAR_OUT_OF_COVERAGE")
        days = tuple(day for day in calendar.sessions if args.start <= day <= args.end)
        if not days:
            raise LiveError("REPLAY_NO_SESSIONS")
    output = args.output or root / "replay" / uuid4().hex
    for day in days:
        before_open = datetime.combine(day, time(9, 30), SHANGHAI)
        calendar.require_session(day, before_open, tuple(i.instrument_id[-2:] for i in scope.instruments))
        if scope.available_at > before_open:
            raise LiveError("SCOPE_NOT_YET_AVAILABLE")
    if len(days) > 1:
        if output.exists():
            raise LiveError("REPLAY_OUTPUT_EXISTS")
        output.mkdir(parents=True)
    results = []
    store = ParquetStore(root)
    try:
        for day in days:
            before_open = datetime.combine(day, time(9, 30), SHANGHAI)
            calendar.require_session(day, before_open, tuple(i.instrument_id[-2:] for i in scope.instruments))
            load_start = perf_counter()
            clock = ReplayClock(before_open)
            provider = ReplayProvider.from_store(store, day, clock)
            if not store.read_day(day):
                raise LiveError("REPLAY_DATA_UNAVAILABLE")
            cutoff = datetime.combine(day, time(15), SHANGHAI)
            with DuckDBStore(root) as query:
                rows = query.visible(day, cutoff=cutoff, now=cutoff)
                if not rows:
                    raise LiveError("STRICT_REPLAY_DATA_UNAVAILABLE")
            _validate_scope_rows(rows, store.receipts(), calendar, scope, cutoff)
            load_ms = (perf_counter() - load_start) * 1000
            engine = ReplayEngine(provider, clock, calendar, scope,
                                  output / day.isoformat() if len(days) > 1 else output,
                                  timeout=args.timeout)
            summary = await engine.run(day, speed=args.speed)
            results.append({**summary, "archive_load_ms": load_ms})
    except Exception:
        if len(days) > 1:
            with (output / "FAILED").open("x", encoding="utf-8") as handle:
                handle.write("REPLAY_RANGE_INCOMPLETE\n")
        raise
    return {"output": str(output.resolve()), "results": results}, 2 if any(r["failed_cycles"] for r in results) else 0


def parser() -> argparse.ArgumentParser:
    root_default = Path(os.environ.get("STOCKFISH_LIVE_DATA_ROOT", Path.home() / ".stockfish-live"))
    result = argparse.ArgumentParser(prog="stockfish-live", description="Phase 0/1 independent strict replay foundation")
    result.add_argument("--version", action="version", version=__version__)
    sub = result.add_subparsers(dest="command", required=True)
    for name in ("doctor", "fixture", "replay", "live", "serve", "evaluate"):
        command = sub.add_parser(name)
        command.add_argument("--data-root", type=Path, default=root_default)
        if name in ("fixture", "replay"):
            command.add_argument("--date", type=date.fromisoformat, required=name == "fixture")
        if name == "replay":
            command.add_argument("--start", type=date.fromisoformat)
            command.add_argument("--end", type=date.fromisoformat)
            command.add_argument("--speed", default="max", help="max or positive multiple of wall-clock minute speed")
            command.add_argument("--output", type=Path, help="new output directory; existing directories refused")
            command.add_argument("--timeout", type=float, default=10.)
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    root = args.data_root.expanduser().resolve()
    try:
        if args.command == "fixture":
            result, code = create_fixture(root, args.date), 0
        elif args.command == "doctor":
            result, code = doctor(root)
        elif args.command == "replay":
            if args.date and (args.start or args.end):
                raise LiveError("REPLAY_DATE_ARGUMENT_CONFLICT")
            result, code = asyncio.run(replay(args, root))
        elif args.command == "live":
            raise LiveError("LIVE_PROVIDER_UNAVAILABLE")
        else:
            raise LiveError("NOT_IMPLEMENTED_PHASE1")
        print(canonical_json(result))
        return code
    except (LiveError, ValidationError, OSError, ValueError) as exc:
        print(canonical_json({"error": exc.code if isinstance(exc, LiveError) else "INVALID_INPUT_OR_STORAGE",
                              "market_edge": MARKET_EDGE}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
