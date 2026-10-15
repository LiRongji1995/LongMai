"""Independent replay, research, local dashboard and explicitly configured feed."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import re
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
from .runtime import forecast_config, load_pipeline
from .storage.research_store import ResearchStore
from .storage.context_store import ContextStore

RESEARCH_DECISION_TIMES = ("09:45", "10:00", "10:30", "13:30")


def _decision_times(value):
    """Accept exact scheduled HH:MM values, including explicit early research."""
    if not isinstance(value, str) or not value:
        raise LiveError("INVALID_EVALUATION_DECISION_TIMES")
    values = tuple(value.split(","))
    if len(set(values)) != len(values):
        raise LiveError("INVALID_EVALUATION_DECISION_TIMES")
    for item in values:
        if not re.fullmatch(r"[0-2][0-9]:[0-5][0-9]", item):
            raise LiveError("INVALID_EVALUATION_DECISION_TIMES")
        hour, minute = map(int, item.split(":"))
        if hour > 23 or minute % 5 or not (
            (9, 35) <= (hour, minute) <= (11, 30)
            or (13, 0) <= (hour, minute) < (15, 0)
        ):
            raise LiveError("INVALID_EVALUATION_DECISION_TIMES")
    return values


def _research_readiness(root, calendar, scope):
    """One archive proof, then linear coverage accounting; never fit a model."""
    from .features.market import INDEX_IDS, MarketStateEngine
    from .features.streaming import StreamingMarketStateEngine
    from .forecast.engine import horizon_targets
    from .contracts.quality import effective_pit_status
    from .storage.lineage import validate_history_provenance

    day = calendar.sessions[-1]
    cutoff = datetime.combine(day, time(9, 30), SHANGHAI)
    if scope.pit_status != "TEST_FIXTURE":
        cutoff = min(cutoff, datetime.now(SHANGHAI))
    config = forecast_config(root)
    if config.feature_version not in ("MARKET_FEATURES_V1", "MARKET_FEATURES_V2"):
        raise LiveError("FEATURE_VERSION_MISMATCH")
    engine = (StreamingMarketStateEngine if config.feature_version == "MARKET_FEATURES_V2"
              else MarketStateEngine)(calendar, scope)
    feature_hash = engine.config_hash if config.feature_version == "MARKET_FEATURES_V2" else engine.config.config_hash
    research = ResearchStore(root)
    history = tuple(item for item in research.visible("features", cutoff=cutoff, now=cutoff)
                    if item.decision_at.date() < day)
    state_ids = {item.state_id for item in history}
    labels, label_statuses = {}, set()
    def label_rows():
        for item in research.iter_visible("outcomes", cutoff=cutoff, now=cutoff):
            if item.state_id in state_ids:
                labels[(item.state_id, item.horizon)] = item.pit_status
                label_statuses.add(item.pit_status)
                yield item
    proof = validate_history_provenance(root, history, label_rows(), calendar, scope, as_of=cutoff)
    context = ContextStore(root).load()
    effective_pit_status([scope.pit_status, calendar.pit_status,
        *(item.pit_status for item in history), *label_statuses,
        *(item.pit_status for item in context.records())])

    buckets, excluded, compatible, decisions = Counter(), Counter(), defaultdict(list), {}
    for state in history:
        key = (state.feature_version, canonical_json(state.context.get("configuration_hash")))
        buckets[key] += 1
        logical = (state.instrument_id, state.decision_at, *key)
        if logical in decisions and decisions[logical] != state.state_id:
            raise LiveError("STATE_DECISION_CONFLICT")
        decisions[logical] = state.state_id
        if state.feature_version != config.feature_version:
            excluded["FEATURE_VERSION_MISMATCH"] += 1
        elif state.context.get("configuration_hash") != feature_hash:
            excluded["FEATURE_CONFIGURATION_MISMATCH"] += 1
        elif state.available_at >= state.decision_at + timedelta(minutes=1):
            excluded["STALE_DECISION_CAPTURE"] += 1
        else:
            compatible[(state.instrument_id, state.decision_at.strftime("%H:%M"))].append(state)

    coverage = []
    all_ready = fixture_ready = verified_ready = unverified_ready = True
    for identity in INDEX_IDS:
        for clock in RESEARCH_DECISION_TIMES:
            states = compatible.get((identity, clock), ())
            features = config.feature_names or tuple(sorted({name for state in states
                for name, value in state.features.items() if value is not None}))
            hour, minute = map(int, clock.split(":"))
            targets = horizon_targets(calendar, datetime.combine(day, time(hour, minute), SHANGHAI))
            horizons = {}
            for horizon in targets:
                sessions = {"VERIFIED_PIT": set(), "TEST_FIXTURE": set(), "PIT_UNVERIFIED": set()}
                for state in states:
                    label_status = labels.get((state.state_id, horizon))
                    if label_status is None or not features or any(state.features.get(name) is None for name in features):
                        continue
                    status = effective_pit_status([scope.pit_status, calendar.pit_status, state.pit_status, label_status])
                    bucket = status if status in sessions else "PIT_UNVERIFIED"
                    sessions[bucket].add(state.decision_at.date())
                counts = {name: len(values) for name, values in sessions.items()}
                count = len(set().union(*sessions.values()))
                ready = count >= config.min_samples
                all_ready &= ready
                fixture_ready &= counts["TEST_FIXTURE"] >= config.min_samples
                verified_ready &= counts["VERIFIED_PIT"] >= config.min_samples
                unverified_ready &= counts["PIT_UNVERIFIED"] >= config.min_samples
                horizons[horizon] = {"eligible_prior_sessions": count, "ready": ready,
                    "verified_pit_sessions": counts["VERIFIED_PIT"], "test_fixture_sessions": counts["TEST_FIXTURE"],
                    "pit_unverified_sessions": counts["PIT_UNVERIFIED"]}
            coverage.append({"instrument_id": identity, "decision_time": clock,
                "compatible_prior_sessions": len({state.decision_at.date() for state in states}),
                "required_feature_names": features, "horizons": horizons,
                "ready": all(item["ready"] for item in horizons.values())})
    status = ("TEST_FIXTURE_HISTORY_READY" if fixture_ready else "VERIFIED_HISTORY_READY" if verified_ready
              else "PIT_UNVERIFIED_HISTORY_READY" if unverified_ready else "INSUFFICIENT_HISTORY")
    return {"schema_version": "LIVE_RESEARCH_READINESS_V1", "as_of": cutoff.isoformat(),
        "assessment_session": day.isoformat(), "feature_states": len(history), "mature_outcomes": len(labels),
        "context_records": len(context.records()), "configuration_hash": config.configuration_hash,
        "feature_version": config.feature_version, "feature_configuration_hash": feature_hash,
        "min_samples": config.min_samples, "decision_times": RESEARCH_DECISION_TIMES,
        "label_proof": proof, "excluded_states": dict(excluded),
        "feature_buckets": [{"feature_version": version_name,
            "feature_configuration_hash": json.loads(hash_text), "feature_states": count,
            "matches_active_configuration": version_name == config.feature_version and hash_text == canonical_json(feature_hash)}
            for (version_name, hash_text), count in sorted(buckets.items())],
        "coverage": coverage, "history_ready": all_ready, "test_fixture_ready": fixture_ready,
        "verified_history_ready": verified_ready, "pit_unverified_history_ready": unverified_ready,
        "status": status, "investment_evaluation": "NOT_EVALUATED", "incremental_edge": "NO_EVIDENCE_OF_INCREMENTAL_EDGE",
        "limitations": ["HISTORY_COVERAGE_IS_NOT_MODEL_EFFECTIVENESS", "QUERY_FEATURES_AND_CURRENT_DATA_STILL_REQUIRE_VALIDATION",
            "PARAMETERS_UNVALIDATED", "NO_REAL_PROVIDER_AUTHENTICATION"]}


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
        "model": "SIMILARITY_MODEL_V1", "market_edge": MARKET_EDGE,
        "live_trading": LIVE_TRADING, "broker_connection": BROKER_CONNECTION,
        "real_minute_validation": "NOT_PERFORMED", "research_ready": False,
        "test_fixture_research_ready": False}
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
            parents = store.receipts()
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
                state = _validate_scope_rows(rows, parents, calendar, scope, cutoff)
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
    if calendar and scope:
        try:
            inputs = _research_readiness(root, calendar, scope)
            report["research_inputs"] = inputs
            report["research_ready"] = not errors and inputs["verified_history_ready"]
            report["test_fixture_research_ready"] = not errors and inputs["test_fixture_ready"]
            report["live_provider"] = "CONFIGURED_REQUIRES_CAPTURE_VALIDATION" if (root / "provider.json").is_file() else "LIVE_PROVIDER_UNAVAILABLE"
        except (LiveError, OSError, ValueError) as exc:
            errors.append(exc.code if isinstance(exc, LiveError) else "RESEARCH_INPUTS_INVALID")
            report["replay_ready"] = False
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
            model_start = perf_counter()
            pipeline = None if getattr(args, "foundation_only", False) else load_pipeline(root, calendar, scope, day,
                early_forecasts=getattr(args, "early_forecasts", False))
            model_ms = (perf_counter() - model_start) * 1000
            engine = ReplayEngine(provider, clock, calendar, scope,
                                  output / day.isoformat() if len(days) > 1 else output,
                                  timeout=args.timeout, processor=pipeline)
            summary = await engine.run(day, speed=args.speed)
            results.append({**summary, "archive_load_ms": load_ms, "model_prepare_ms": model_ms})
    except BaseException:
        if len(days) > 1:
            try:
                with (output / "FAILED").open("x", encoding="utf-8") as handle:
                    handle.write("REPLAY_RANGE_INCOMPLETE\n")
            except OSError:
                pass
        raise
    return {"output": str(output.resolve()), "results": results}, 2 if any(r["failed_cycles"] for r in results) else 0


def evaluate(args, root):
    from .evaluation.walk_forward import expanding_walk_forward
    from .forecast.engine import ForecastEngine, horizon_targets, mature_outcomes
    from .common import aware, digest
    if args.as_of is None:
        raise LiveError("EVALUATION_CUTOFF_REQUIRED")
    selected_times = _decision_times(args.decision_times)
    as_of = aware(args.as_of)
    calendar = TradingCalendar.from_file(root / "calendar.json")
    scope = _scope(root)
    if scope.pit_status != "TEST_FIXTURE" and as_of > datetime.now(SHANGHAI):
        raise LiveError("FUTURE_ACCESS_DENIED")
    research = ResearchStore(root)
    states = list(research.iter_visible("features", cutoff=as_of, now=as_of))
    forecasts = list(research.iter_visible("forecasts", cutoff=as_of, now=as_of))
    if args.run_dir:
        extra = ResearchStore(args.run_dir)
        states.extend(extra.iter_visible("features", cutoff=as_of, now=as_of))
        forecasts.extend(extra.iter_visible("forecasts", cutoff=as_of, now=as_of))
    state_map = {}
    for item in states:
        if item.state_id in state_map and state_map[item.state_id] != item:
            raise LiveError("STATE_ID_CONFLICT")
        state_map[item.state_id] = item
    saved_state_ids = {item.state_id for item in forecasts}
    states = sorted((item for item in state_map.values() if args.all_decision_times
        or item.decision_at.strftime("%H:%M") in selected_times or item.state_id in saved_state_ids),
        key=lambda item: (item.decision_at, item.instrument_id, item.state_id))
    required_ids = {item.state_id for item in states}
    outcome_hashes = {}
    store = ParquetStore(root)
    def outcome_rows():
        # Each archive part is fully validated even when none of its labels is
        # needed. Retain only compressed required labels after the source proof.
        for item in research.iter_visible("outcomes", cutoff=as_of, now=as_of):
            if item.state_id in required_ids:
                key = (item.state_id, item.horizon)
                fingerprint = digest(item)
                if key in outcome_hashes and outcome_hashes[key] != fingerprint:
                    raise LiveError("OUTCOME_ID_CONFLICT")
                outcome_hashes[key] = fingerprint
                yield item
        by_day = defaultdict(list)
        for item in states:
            targets = horizon_targets(calendar, item.decision_at)
            if any(target <= as_of and (item.state_id, horizon) not in outcome_hashes
                   for horizon, target in targets.items()):
                by_day[item.decision_at.date()].append(item)
        # Existing complete labels already passed raw/path/anchor/regime proof.
        # Only missing labels need maturation; no second full-history rebuild.
        parents = store.receipts() if by_day else {}
        for day, missing_states in sorted(by_day.items()):
            labels = mature_outcomes(missing_states, store.read_day(day), parents.values(), calendar, as_of=as_of)
            for item in labels:
                key = (item.state_id, item.horizon)
                fingerprint = digest(item)
                if key in outcome_hashes:
                    if outcome_hashes[key] != fingerprint:
                        raise LiveError("OUTCOME_ID_CONFLICT")
                    continue
                outcome_hashes[key] = fingerprint
                yield item
    from .storage.lineage import validate_history_provenance
    checked = validate_history_provenance(root, states, outcome_rows(), calendar, scope, as_of=as_of, yield_outcomes=True)
    proof = {}
    def checked_rows():
        while True:
            try:
                item = next(checked)
            except StopIteration as completed:
                proof.update(completed.value)
                return
            yield item
    config = forecast_config(root)
    prepared = ForecastEngine(calendar, config).prepare_history(states, checked_rows())
    report = expanding_walk_forward(states, prepared, calendar, config, as_of=as_of,
        decision_times=None if args.all_decision_times else selected_times,
        min_train_sessions=args.min_train_sessions, tune_k=not args.no_tune)
    report["label_proof"] = proof
    from .evaluation.saved import evaluate_saved_forecasts
    report["saved_forecast_posterior"] = evaluate_saved_forecasts(forecasts, states, prepared, as_of=as_of)
    report["k_selection_enabled"] = not args.no_tune
    report["decision_times"] = "ALL_RECORDED_SCHEDULED" if args.all_decision_times else list(selected_times)
    report["input_scope"] = {"method": "SAME_DECISION_TIME_HISTORY_AND_SAVED_FORECAST_STATES",
        "all_decision_times": args.all_decision_times, "decision_times": list(selected_times),
        "archive_feature_states": len(state_map), "included_feature_states": len(states),
        "saved_forecast_state_count": len(saved_state_ids), "included_mature_outcomes": len(outcome_hashes),
        "unselected_archive_parts_integrity_checked": True, "history_storage": "LOSSLESS_COMPRESSED_PREPARED_HISTORY"}
    report["input_hash"] = digest({"features": [(item.state_id, digest(item)) for item in states],
        "outcomes": [(key, value) for key, value in sorted(outcome_hashes.items())], "scope": report["input_scope"]})
    report["evaluation_id"] = digest({key: value for key, value in report.items() if key != "evaluation_id"})
    target = research.save_evaluation(report)
    return {"output": str(target.resolve()), **{key: report[key] for key in ("evaluation_id", "method",
        "investment_evaluation", "incremental_edge", "real_oos_sample_count", "fixture_sample_count",
        "pit_unverified_sample_count", "market_edge")}}, 0


def parser() -> argparse.ArgumentParser:
    root_default = Path(os.environ.get("STOCKFISH_LIVE_DATA_ROOT", Path.home() / ".stockfish-live"))
    result = argparse.ArgumentParser(prog="stockfish-live", description="Independent PIT-first intraday market research")
    result.add_argument("--version", action="version", version=__version__)
    sub = result.add_subparsers(dest="command", required=True)
    for name in ("doctor", "fixture", "demo", "replay", "live", "serve", "evaluate"):
        command = sub.add_parser(name)
        command.add_argument("--data-root", type=Path, default=root_default)
        if name in ("fixture", "demo", "replay"):
            command.add_argument("--date", type=date.fromisoformat, required=name in ("fixture", "demo"))
        if name == "demo":
            command.add_argument("--history-sessions", type=int, default=60)
        if name == "replay":
            command.add_argument("--start", type=date.fromisoformat)
            command.add_argument("--end", type=date.fromisoformat)
            command.add_argument("--speed", default="max", help="max or positive multiple of wall-clock minute speed")
            command.add_argument("--output", type=Path, help="new output directory; existing directories refused")
            command.add_argument("--timeout", type=float, default=10.)
            command.add_argument("--foundation-only", action="store_true", help="explicit frozen Phase1 replay without research")
            command.add_argument("--early-forecasts", action="store_true", help="experimental 09:35/09:40 low confidence")
        if name == "serve":
            command.add_argument("--run-dir", type=Path,
                help="saved single run or range root containing YYYY-MM-DD runs; read-only latest-year dashboard")
            command.add_argument("--host", default="127.0.0.1")
            command.add_argument("--port", type=int, default=8765)
        if name == "evaluate":
            command.add_argument("--as-of", type=datetime.fromisoformat)
            command.add_argument("--run-dir", type=Path)
            command.add_argument("--decision-times", default="09:45,10:00,10:30,13:30")
            command.add_argument("--all-decision-times", action="store_true")
            command.add_argument("--min-train-sessions", type=int)
            command.add_argument("--no-tune", action="store_true", help="fixed initial K, clearly recorded in report")
        if name == "live":
            command.add_argument("--output", type=Path)
            command.add_argument("--max-ticks", type=int, default=240)
            command.add_argument("--timeout", type=float, default=10.)
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    root = args.data_root.expanduser().resolve()
    try:
        if args.command == "fixture":
            result, code = create_fixture(root, args.date), 0
        elif args.command == "demo":
            from .providers.demo import create_demo
            result, code = create_demo(root, args.date, history_sessions=args.history_sessions), 0
        elif args.command == "doctor":
            result, code = doctor(root)
        elif args.command == "replay":
            if args.date and (args.start or args.end):
                raise LiveError("REPLAY_DATE_ARGUMENT_CONFLICT")
            result, code = asyncio.run(replay(args, root))
        elif args.command == "live":
            from .ingest.live import run_live
            result, code = asyncio.run(run_live(root, output=args.output, max_ticks=args.max_ticks, timeout=args.timeout))
        elif args.command == "evaluate":
            result, code = evaluate(args, root)
        elif args.command == "serve":
            from .api.local_access import is_loopback_host
            if not is_loopback_host(args.host):
                raise LiveError("REMOTE_LISTEN_REQUIRES_AUTHENTICATION")
            # Do not let host-file/DNS resolution turn localhost into a remote
            # listener. Literal loopback IPv4/IPv6 addresses need no lookup.
            listen_host = "127.0.0.1" if args.host.lower() == "localhost" else args.host
            if not args.run_dir:
                raise LiveError("RUN_DIRECTORY_REQUIRED")
            if not 1 <= args.port <= 65535:
                raise LiveError("INVALID_SERVER_PORT")
            from .api.app import create_app
            import uvicorn
            app = create_app(args.run_dir)
            app.state.run_catalog.validate_startup()
            uvicorn.run(app, host=listen_host, port=args.port, proxy_headers=False)
            result, code = {"server": "STOPPED"}, 0
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
