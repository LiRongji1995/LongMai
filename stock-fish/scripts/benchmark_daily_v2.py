"""Benchmark R14 daily primary fast path against the research comparison path.

This harness reads immutable MarketInput documents from the configured SQLite
store. It never backfills provider data, never changes strategy semantics, and
uses a hard child-process timeout for the optional full research comparison.
"""
from __future__ import annotations

import argparse
import cProfile
from datetime import datetime
import io
import json
import multiprocessing as mp
import os
from pathlib import Path
import pstats
import sqlite3
import sys
import threading
import time
import traceback
import zlib
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[2]
STOCK_FISH_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STOCK_FISH_ROOT))

from stockfish_core.storage import SQLiteStore, checked_payload  # noqa: E402
from stockfish_core.yaogu.config import StrategyConfig  # noqa: E402
from stockfish_core.yaogu.contracts import MarketInput  # noqa: E402
from stockfish_core.yaogu.derived_feature_store import DerivedFeatureStore  # noqa: E402
from stockfish_core.yaogu.history_artifacts import bind_history, digest_json_stream, digest_market_input  # noqa: E402
from stockfish_core.yaogu.strategy_experiments import (  # noqa: E402
    run_active_daily_strategy,
    run_strategy_experiments,
)


def _canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False).encode("utf-8")


def _json_size_bytes(value):
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False)
    total = 0
    for chunk in encoder.iterencode(value):
        total += len(chunk.encode("utf-8"))
    return total


def _db_path(value):
    if value:
        return Path(value).resolve()
    return (STOCK_FISH_ROOT / "data" / "stockfish.sqlite3").resolve()


def _latest_input_id(store, observation_date=None):
    candidates = []
    offset = 0
    wanted = str(observation_date) if observation_date else None
    while True:
        rows = store.list_document_metadata("candidate_inputs", limit=1000, offset=offset, descending=True)
        for row in rows:
            index = store.get_document("candidate_input_indexes", row["document_id"])
            if index is None:
                continue
            if wanted is None or index.get("observation_date") == wanted:
                candidates.append((index.get("observation_date") or "", row["created_at"], row["document_id"]))
        if len(rows) < 1000:
            break
        offset += len(rows)
    if not candidates:
        raise RuntimeError("candidate input not found")
    return max(candidates)[2]


def _current_config(store):
    configs = [row["payload"] for row in store.list_documents("strategy_configs", limit=1000)]
    activations = [row["payload"] for row in store.list_documents("strategy_config_activations", limit=1000)]
    if not configs:
        raise RuntimeError("strategy config not registered")
    wanted = activations[-1]["config_hash"] if activations else configs[-1]["config_hash"]
    for row in configs:
        if row["config_hash"] == wanted:
            return StrategyConfig.model_validate(row["config"])
    raise RuntimeError("active strategy config missing")


def _load_bundle(store, input_id):
    raw = store.get_document_bytes("candidate_inputs", input_id, expected_hash=input_id[7:])
    if raw is None:
        raise RuntimeError("candidate input missing or corrupt")
    return bind_history(MarketInput.model_validate_json(raw), store)


def _stage_slice(run):
    stage = (run.get("results") or {}).get("STAGE_AWARE_MOMENTUM_V2") or {}
    return {"counts": stage.get("counts"), "top_ids": stage.get("top_ids"), "items": stage.get("items")}


def _compact_daily_primary_size(run):
    from stockfish_core.yaogu.candidate_service import CandidateService

    fields = CandidateService._strategy_scalar_fields()
    stage = (run.get("results") or {}).get("STAGE_AWARE_MOMENTUM_V2") or {}
    summaries = []
    raw_bytes = compressed_bytes = 0
    for row in stage.get("items") or ():
        raw = _canonical_bytes(row)
        compressed = zlib.compress(raw, level=6)
        raw_bytes += len(raw)
        compressed_bytes += len(compressed)
        summaries.append({key: value for key, value in row.items() if key in fields})
    compact_stage = dict(stage)
    compact_stage["items"] = summaries
    compact_stage["diagnostics"] = []
    compact = {key: value for key, value in run.items() if key != "results"}
    compact["results"] = {"STAGE_AWARE_MOMENTUM_V2": compact_stage}
    return {
        "run_header_and_summary_bytes_estimate": _json_size_bytes(compact),
        "instrument_summary_items_bytes": _json_size_bytes(summaries),
        "detail_raw_bytes": raw_bytes,
        "detail_compressed_bytes": compressed_bytes,
        "detail_document_count": len(summaries),
    }


def _measure(profile, db_path, input_id, cutoff_iso, derived_cache_db_path=None):
    import psutil

    process = psutil.Process(os.getpid())
    rss_start = process.memory_info().rss
    peak = {"rss": rss_start}
    stop = threading.Event()

    def sample():
        while not stop.is_set():
            try:
                peak["rss"] = max(peak["rss"], process.memory_info().rss)
            except psutil.Error:
                return
            time.sleep(0.05)

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    started = time.perf_counter()
    store = SQLiteStore(db_path)
    after_store = time.perf_counter()
    bundle = _load_bundle(store, input_id)
    after_bundle = time.perf_counter()
    config = _current_config(store)
    after_config = time.perf_counter()
    cutoff = datetime.fromisoformat(cutoff_iso)
    if profile == "fast":
        cache_store = SQLiteStore(derived_cache_db_path or db_path)
        after_cache_store = time.perf_counter()
        result = run_active_daily_strategy(
            bundle,
            cutoff=cutoff,
            config=config,
            input_id=input_id,
            derived_store=DerivedFeatureStore(cache_store),
            execution_profile="DAILY_PRIMARY_FAST",
        )
    elif profile == "full":
        after_cache_store = after_config
        result = run_strategy_experiments(bundle, cutoff=cutoff, config=config)
        result["execution_profile"] = "RESEARCH_FULL"
    else:
        raise ValueError(profile)
    after_strategy = time.perf_counter()
    payload_bytes = _json_size_bytes(result)
    after_payload_size = time.perf_counter()
    compact_size = _compact_daily_primary_size(result) if profile == "fast" else None
    after_compact_size = time.perf_counter()
    elapsed = after_compact_size - started
    stop.set()
    sampler.join(timeout=1)
    stage = (result.get("results") or {}).get("STAGE_AWARE_MOMENTUM_V2") or {}
    return {
        "profile": profile,
        "status": "SUCCEEDED",
        "elapsed_seconds": elapsed,
        "rss_start_bytes": rss_start,
        "rss_peak_bytes": peak["rss"],
        "rss_delta_peak_bytes": peak["rss"] - rss_start,
        "payload_bytes": payload_bytes,
        "phase_timings": {
            "sqlite_store_init": after_store - started,
            "market_input_load_and_bind": after_bundle - after_store,
            "config_load": after_config - after_bundle,
            "derived_cache_store_init": after_cache_store - after_config,
            "strategy_compute": after_strategy - after_cache_store,
            "output_size_stream": after_payload_size - after_strategy,
            "compact_size_estimate": after_compact_size - after_payload_size,
            "total": after_compact_size - started,
        },
        "input_id": input_id,
        "observation_date": str(bundle.observation_date),
        "input_hash": digest_market_input(bundle),
        "history_artifact_hash": bundle.history_artifact_hash,
        "derived_cache_db_path": str(derived_cache_db_path or db_path) if profile == "fast" else None,
        "coverage_type": bundle.coverage_type,
        "strategy_id": "STAGE_AWARE_MOMENTUM_V2",
        "stage_aware_counts": stage.get("counts"),
        "stage_aware_business_hash": digest_json_stream(_stage_slice(result)),
        "derived_feature_store": result.get("derived_feature_store"),
        "daily_primary_compact_estimate": compact_size,
        "market_edge": "NOT_ESTABLISHED",
    }


def _profiled_measure(profile, db_path, input_id, cutoff_iso, derived_cache_db_path, cprofile_output):
    if not cprofile_output:
        return _measure(profile, db_path, input_id, cutoff_iso, derived_cache_db_path)
    profiler = cProfile.Profile()
    result = profiler.runcall(_measure, profile, db_path, input_id, cutoff_iso, derived_cache_db_path)
    profiler.dump_stats(cprofile_output)
    stream = io.StringIO()
    pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats("cumtime").print_stats(40)
    result["cprofile_output"] = cprofile_output
    result["cprofile_top_cumulative"] = stream.getvalue()
    return result


def _worker(queue, profile, db_path, input_id, cutoff_iso, derived_cache_db_path, cprofile_output):
    try:
        queue.put({"ok": True, "result": _profiled_measure(
            profile, db_path, input_id, cutoff_iso, derived_cache_db_path, cprofile_output)})
    except BaseException as exc:
        queue.put({
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(limit=20),
        })


def run_with_timeout(profile, db_path, input_id, cutoff_iso, timeout_seconds, derived_cache_db_path=None,
                     cprofile_output=None):
    queue = mp.Queue()
    process = mp.Process(target=_worker, args=(queue, profile, str(db_path), input_id, cutoff_iso,
                                               str(derived_cache_db_path) if derived_cache_db_path else None,
                                               str(cprofile_output) if cprofile_output else None), daemon=False)
    process.start()
    process.join(timeout_seconds)
    if process.is_alive():
        process.terminate()
        process.join(10)
        return {
            "profile": profile,
            "status": "TIMEOUT",
            "timeout_seconds": timeout_seconds,
            "input_id": input_id,
            "market_edge": "NOT_ESTABLISHED",
        }
    if queue.empty():
        return {
            "profile": profile,
            "status": "FAILED_NO_RESULT",
            "exitcode": process.exitcode,
            "input_id": input_id,
            "market_edge": "NOT_ESTABLISHED",
        }
    payload = queue.get()
    if payload["ok"]:
        return payload["result"]
    return {
        "profile": profile,
        "status": "FAILED",
        "exitcode": process.exitcode,
        **{k: payload[k] for k in ("error_type", "error", "traceback")},
        "input_id": input_id,
        "market_edge": "NOT_ESTABLISHED",
    }


def sqlite_size_audit(db_path):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    audit = {
        "db_path": str(db_path),
        "db_file_bytes": db_path.stat().st_size if db_path.exists() else None,
        "wal_file_bytes": db_path.with_suffix(db_path.suffix + "-wal").stat().st_size if db_path.with_suffix(db_path.suffix + "-wal").exists() else 0,
        "shm_file_bytes": db_path.with_suffix(db_path.suffix + "-shm").stat().st_size if db_path.with_suffix(db_path.suffix + "-shm").exists() else 0,
        "namespace_counts": [],
        "largest_documents": [],
        "derived_feature_store": None,
        "dbstat": [],
    }
    audit["namespace_counts"] = [dict(row) for row in con.execute(
        "SELECT namespace,count(*) AS rows,sum(length(CAST(payload AS BLOB))) AS payload_bytes "
        "FROM documents GROUP BY namespace ORDER BY payload_bytes DESC"
    )]
    audit["largest_documents"] = [dict(row) for row in con.execute(
        "SELECT namespace,document_id,length(CAST(payload AS BLOB)) AS payload_bytes,created_at "
        "FROM documents ORDER BY payload_bytes DESC LIMIT 30"
    )]
    try:
        table = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='yaogu_derived_features_v1'"
        ).fetchone()
        if table:
            audit["derived_feature_store"] = dict(con.execute(
                "SELECT count(*) AS rows,sum(length(CAST(payload AS BLOB))) AS payload_bytes,"
                "max(length(CAST(payload AS BLOB))) AS largest_payload_bytes "
                "FROM yaogu_derived_features_v1"
            ).fetchone())
    except sqlite3.DatabaseError as exc:
        audit["derived_feature_store_error"] = str(exc)
    try:
        audit["dbstat"] = [dict(row) for row in con.execute(
            "SELECT name,sum(pgsize) AS bytes,count(*) AS pages FROM dbstat GROUP BY name ORDER BY bytes DESC"
        )]
    except sqlite3.DatabaseError as exc:
        audit["dbstat_error"] = str(exc)
    con.close()
    audit["market_edge"] = "NOT_ESTABLISHED"
    return audit


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def write_markdown(path, result):
    lines = [
        "# R14 Daily Fast Path Performance Result",
        "",
        f"- generated_at: {datetime.now().astimezone().isoformat()}",
        f"- db_path: `{result['db_path']}`",
        f"- input_id: `{result['input_id']}`",
        f"- cutoff: `{result['cutoff']}`",
        f"- market_edge: `{result['market_edge']}`",
        "",
        "## Runs",
        "",
    ]
    for run in result["runs"]:
        lines.extend([
            f"### {run['profile']}",
            "",
            f"- status: {run['status']}",
            f"- elapsed_seconds: {run.get('elapsed_seconds')}",
            f"- rss_peak_bytes: {run.get('rss_peak_bytes')}",
            f"- payload_bytes: {run.get('payload_bytes')}",
            f"- stage_aware_business_hash: `{run.get('stage_aware_business_hash')}`",
            "",
        ])
        if run.get("derived_feature_store"):
            stats = run["derived_feature_store"]
            lines.extend([
                f"- derived_cache_hits: {stats.get('cache_hits')}",
                f"- derived_cache_misses: {stats.get('cache_misses')}",
                f"- requested_feature_rows: {stats.get('requested_feature_rows')}",
                "",
            ])
    if result.get("sqlite_size_audit"):
        audit = result["sqlite_size_audit"]
        lines.extend([
            "## SQLite Size Audit",
            "",
            f"- db_file_bytes: {audit.get('db_file_bytes')}",
            f"- wal_file_bytes: {audit.get('wal_file_bytes')}",
            f"- namespace_count: {len(audit.get('namespace_counts') or [])}",
            "",
        ])
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=None)
    parser.add_argument("--input-id", default=None)
    parser.add_argument("--observation-date", default=None)
    parser.add_argument("--cutoff", default=None)
    parser.add_argument("--profile", choices=("fast", "full", "both"), default="fast")
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--derived-cache-db", default=None)
    parser.add_argument("--cprofile-output", default=None)
    parser.add_argument("--output-json", default=None)
    parser.add_argument("--output-md", default=None)
    parser.add_argument("--include-sqlite-audit", action="store_true")
    args = parser.parse_args(argv)

    db_path = _db_path(args.db)
    store = SQLiteStore(db_path)
    input_id = args.input_id or _latest_input_id(store, args.observation_date)
    bundle = _load_bundle(store, input_id)
    cutoff = args.cutoff or datetime.combine(
        bundle.observation_date,
        datetime.min.time().replace(hour=20, minute=30),
        ZoneInfo("Asia/Shanghai"),
    ).isoformat()
    profiles = ("fast", "full") if args.profile == "both" else (args.profile,)
    derived_cache_db = Path(args.derived_cache_db).resolve() if args.derived_cache_db else None
    runs = []
    for profile in profiles:
        cprofile_output = None
        if args.cprofile_output:
            path = Path(args.cprofile_output)
            cprofile_output = path if len(profiles) == 1 else path.with_name(path.stem + "_" + profile + path.suffix)
        runs.append(run_with_timeout(profile, db_path, input_id, cutoff, args.timeout_seconds,
                                     derived_cache_db, cprofile_output))
    result = {
        "benchmark_version": "R14_DAILY_FAST_PATH_BENCHMARK_V1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "db_path": str(db_path),
        "input_id": input_id,
        "observation_date": str(bundle.observation_date),
        "cutoff": cutoff,
        "runs": runs,
        "sqlite_size_audit": sqlite_size_audit(db_path) if args.include_sqlite_audit else None,
        "market_edge": "NOT_ESTABLISHED",
    }
    if args.output_json:
        write_json(Path(args.output_json), result)
    if args.output_md:
        write_markdown(Path(args.output_md), result)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    mp.freeze_support()
    main()
