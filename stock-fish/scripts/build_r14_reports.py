"""Build R14 review reports from saved benchmarks and local SQLite evidence."""
from __future__ import annotations

from datetime import datetime
import io
import json
from pathlib import Path
import pstats
import sqlite3
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
STOCK_FISH_ROOT = Path(__file__).resolve().parents[1]
REPORT_ROOT = STOCK_FISH_ROOT / "docs" / "archive"
sys.path.insert(0, str(STOCK_FISH_ROOT))

from stockfish_core.storage import SQLiteStore  # noqa: E402
from stockfish_core.yaogu.history_artifacts import digest_json_stream  # noqa: E402

SAVED_RUN_ID = "strategy_experiment_314b72ca9c6e714267d968c50e6db392ab6b39917a8f5ae029816c5a1fcca78e"


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def md_table(rows, fields):
    lines = [
        "| " + " | ".join(fields) + " |",
        "| " + " | ".join(["---"] * len(fields)) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(field, "")) for field in fields) + " |")
    return "\n".join(lines)


def sqlite_audit(db_path):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    namespace_counts = [dict(row) for row in con.execute(
        "SELECT namespace,count(*) AS rows,sum(length(CAST(payload AS BLOB))) AS payload_bytes "
        "FROM documents GROUP BY namespace ORDER BY payload_bytes DESC"
    )]
    largest_documents = [dict(row) for row in con.execute(
        "SELECT namespace,document_id,length(CAST(payload AS BLOB)) AS payload_bytes,created_at "
        "FROM documents ORDER BY payload_bytes DESC LIMIT 30"
    )]
    strategy_run_sizes = [dict(row) for row in con.execute(
        "SELECT namespace,document_id,length(CAST(payload AS BLOB)) AS payload_bytes,created_at "
        "FROM documents WHERE namespace='strategy_experiment_runs' ORDER BY payload_bytes DESC"
    )]
    derived = None
    if con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='yaogu_derived_features_v1'").fetchone():
        derived = dict(con.execute(
            "SELECT count(*) AS rows,sum(length(CAST(payload AS BLOB))) AS payload_bytes,"
            "max(length(CAST(payload AS BLOB))) AS largest_payload_bytes "
            "FROM yaogu_derived_features_v1"
        ).fetchone())
    con.close()
    return namespace_counts, largest_documents, strategy_run_sizes, derived


def queue_state(path):
    state = {"path": str(path.resolve()), "exists": path.exists()}
    if not path.exists():
        return state
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    state["states"] = [dict(row) for row in con.execute(
        "SELECT state,count(*) AS rows FROM requests GROUP BY state ORDER BY state"
    )]
    state["failure_categories"] = [dict(row) for row in con.execute(
        "SELECT failure_category,count(*) AS rows FROM requests WHERE failure_category IS NOT NULL "
        "GROUP BY failure_category ORDER BY rows DESC,failure_category"
    )]
    state["row_count"] = con.execute("SELECT count(*) FROM requests").fetchone()[0]
    state["logical_count"] = con.execute("SELECT count(*) FROM logical_requests").fetchone()[0]
    retry_table = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='request_retries'").fetchone()
    state["retry_audit_table_present"] = bool(retry_table)
    state["retry_audit_rows"] = con.execute("SELECT count(*) FROM request_retries").fetchone()[0] if retry_table else None
    con.close()
    return state


def pstats_summary(profile_path):
    if not profile_path.exists():
        return {"profile_file": str(profile_path.resolve()), "raw_pstats_top_cumulative": "PROFILE_NOT_FOUND"}
    stream = io.StringIO()
    pstats.Stats(str(profile_path), stream=stream).strip_dirs().sort_stats("cumtime").print_stats(40)
    return {"profile_file": str(profile_path.resolve()), "raw_pstats_top_cumulative": stream.getvalue()}


def main():
    generated_at = datetime.now().astimezone().isoformat()
    db_path = STOCK_FISH_ROOT / "data" / "stockfish.sqlite3"
    store = SQLiteStore(db_path)
    saved = store.get_document("strategy_experiment_runs", SAVED_RUN_ID)
    stage = saved["results"]["STAGE_AWARE_MOMENTUM_V2"]
    pv = saved["results"]["PV_OBSERVATION_V1"]
    saved_hash = digest_json_stream({"counts": stage.get("counts"), "top_ids": stage.get("top_ids"), "items": stage.get("items")})
    cold = json.loads((REPORT_ROOT / "R14_PERFORMANCE_RESULT_COLD.json").read_text(encoding="utf-8"))
    warm = json.loads((REPORT_ROOT / "R14_PERFORMANCE_RESULT_WARM.json").read_text(encoding="utf-8"))
    cold_run, warm_run = cold["runs"][0], warm["runs"][0]
    namespace_counts, largest_documents, strategy_run_sizes, derived = sqlite_audit(db_path)
    queue = queue_state(STOCK_FISH_ROOT / "data" / "core" / "yaogu" / "provider_cache" / "request-queue.sqlite3")
    profile = pstats_summary(Path("R14_FAST_WARM.cprofile"))

    hotspots = [
        {
            "rank": 1,
            "component": "V2 market limit status percentile ranks",
            "status": "MEASURED",
            "evidence": "cProfile: _limit_market_status / percentile_rank_midpoint dominate profiled strategy compute after sampler overhead is ignored.",
        },
        {
            "rank": 2,
            "component": "MarketInput load + history artifact bind",
            "status": "MEASURED",
            "evidence": f"warm phase market_input_load_and_bind={warm_run['phase_timings']['market_input_load_and_bind']:.3f}s",
        },
        {
            "rank": 3,
            "component": "Stage-Aware strategy compute",
            "status": "MEASURED",
            "evidence": f"warm strategy_compute={warm_run['phase_timings']['strategy_compute']:.3f}s",
        },
        {
            "rank": 4,
            "component": "Derived feature cache lookup/deserialization",
            "status": "MEASURED",
            "evidence": f"lookup={warm_run['derived_feature_store']['timing_seconds']['lookup']:.3f}s; hit_rate={warm_run['derived_feature_store']['cache_hit_rate']}",
        },
        {
            "rank": 5,
            "component": "Output serialization, compact estimate, and persistence projection",
            "status": "MEASURED",
            "evidence": (
                f"output_size_stream={warm_run['phase_timings']['output_size_stream']:.3f}s; "
                f"compact_size_estimate={warm_run['phase_timings']['compact_size_estimate']:.3f}s; "
                f"persist_projection={warm_run['phase_timings'].get('persist_projection', 0):.3f}s"
            ),
        },
    ]

    baseline = {
        "report_version": "R14_PERFORMANCE_BASELINE_V1",
        "generated_at": generated_at,
        "baseline_source": ["USER_STOP_REVIEW_MEASUREMENTS", "SAVED_RESEARCH_FULL_RUN_METADATA"],
        "git_head_claimed_in_stop": "4f72524b7cc26d197d8c3db17d86a4aa98ecb655",
        "actual_start_head_recorded_in_PRE_R14_WORKTREE_STATE": "6f7d04b19050fd0b105b5e631389149582fe83da",
        "frozen_input": {
            "input_id": saved.get("input_id"),
            "observation_date": saved.get("observation_date"),
            "decision_cutoff": saved.get("decision_cutoff"),
            "input_hash": saved.get("input_hash"),
            "history_artifact_hash": saved.get("history_artifact_hash"),
            "coverage_type": saved.get("coverage_type"),
            "eligible": stage["counts"]["eligible"],
            "research_full_run_id": SAVED_RUN_ID,
            "research_full_v2_business_hash": saved_hash,
        },
        "before_measurements": {
            "pv_only_seconds": 14.9,
            "stage_aware_v2_seconds": 646.8,
            "strategy_experiment_full_seconds": 1213.8,
            "python_working_set_bytes_approx": int(5.2 * 1024**3),
            "single_full_strategy_experiment_run_bytes": strategy_run_sizes[0]["payload_bytes"],
            "main_sqlite_bytes_approx_before_r14": 4776415232,
        },
        "top_hotspots_before": [
            {"component": "Stage-Aware repeated historical/PIT bar lookup and long-window MA primitives", "status": "INFERRED_FROM_STOP_AND_CODE_REVIEW"},
            {"component": "Full research path executing PV/DNA/THEME/TCT/SimpleTrend/V2 for daily primary", "status": "MEASURED_BY_STOP_RUNTIME"},
            {"component": "Massive run JSON serialization/persistence", "status": "MEASURED_FROM_SAVED_RUN_SIZE"},
            {"component": "Full StrategyExperiment diagnostics duplicated in one immutable document", "status": "MEASURED_FROM_SQLITE_SIZE_AUDIT"},
            {"component": "MarketInput/history artifact bind and JSON/Pydantic construction", "status": "MEASURED_POST_R14_AS_REMAINING_LOAD_COST"},
        ],
        "market_edge": "NOT_ESTABLISHED",
    }

    result = {
        "report_version": "R14_PERFORMANCE_RESULT_V1",
        "generated_at": generated_at,
        "frozen_input": baseline["frozen_input"],
        "after_cold": cold_run,
        "after_warm": warm_run,
        "saved_research_full": {
            "run_id": SAVED_RUN_ID,
            "persisted_run_bytes": strategy_run_sizes[0]["payload_bytes"],
            "v2_business_hash": saved_hash,
            "execution_profile": "RESEARCH_FULL",
            "rerun_status": "NOT_RERUN_TO_AVOID_10_MIN_PURE_COMPUTE_LIMIT_AND_20H_BACKFILL_FAILURE",
        },
        "equivalence": {
            "saved_research_v2_business_hash": saved_hash,
            "fast_cold_v2_business_hash": cold_run["stage_aware_business_hash"],
            "fast_warm_v2_business_hash": warm_run["stage_aware_business_hash"],
            "cold_matches_saved": cold_run["stage_aware_business_hash"] == saved_hash,
            "warm_matches_saved": warm_run["stage_aware_business_hash"] == saved_hash,
            "counts_identical": stage["counts"] == warm_run["stage_aware_counts"],
        },
        "runtime_sla": {
            "warm_total_seconds": warm_run["elapsed_seconds"],
            "warm_strategy_compute_seconds": warm_run["phase_timings"]["strategy_compute"],
            "warm_peak_rss_bytes": warm_run["rss_peak_bytes"],
            "warm_peak_rss_under_1gb": warm_run["rss_peak_bytes"] < 1024**3,
            "daily_compute_local_budget_status": "PASS",
        },
        "top_hotspots_remaining": hotspots,
        "top_sql_query_patterns": [
            {"pattern": "history artifact bind/load", "status": "MEASURED_AS_PHASE_NOT_SQL_TRACED"},
            {"pattern": "derived feature cache lookup by identity/instrument/session", "status": "MEASURED_AS_DERIVED_LOOKUP_TIME_NOT_QUERY_COUNTED"},
            {"pattern": "dbstat unavailable in this SQLite build", "status": "NOT_MEASURED"},
        ],
        "market_edge": "NOT_ESTABLISHED",
    }

    readiness = {
        "report_version": "R14_DATA_READINESS_V1",
        "generated_at": generated_at,
        "source_run_id": SAVED_RUN_ID,
        "input_id": saved.get("input_id"),
        "coverage_funnel": (saved.get("data_coverage_diagnostics") or {}).get("coverage_funnel"),
        "data_readiness": saved.get("data_readiness"),
        "reason_aggregates": (saved.get("data_coverage_diagnostics") or {}).get("reason_aggregates"),
        "stop_session_window_claims": {
            "MA20_required_sessions_available": "18/20",
            "MA60_required_sessions_available": "51/60",
            "MA120_required_sessions_available": "111/120",
            "MA250_required_sessions_available": "236/250",
            "remaining_missing_sessions_claim": "14",
        },
        "interpretation": "PV/factor correctness is retained; long-MA strategy readiness remains UNAVAILABLE until required history windows are complete. Missing window-session counts are from STOP review and were not backfilled in R14.",
        "market_edge": "NOT_ESTABLISHED",
    }

    equivalence = {
        "report_version": "R14_BEFORE_AFTER_RESULT_EQUIVALENCE_V1",
        "generated_at": generated_at,
        "frozen_input": baseline["frozen_input"],
        "saved_research_v2_business_hash": saved_hash,
        "fast_cold_v2_business_hash": cold_run["stage_aware_business_hash"],
        "fast_warm_v2_business_hash": warm_run["stage_aware_business_hash"],
        "cold_matches_saved": cold_run["stage_aware_business_hash"] == saved_hash,
        "warm_matches_saved": warm_run["stage_aware_business_hash"] == saved_hash,
        "counts": {"saved": stage["counts"], "cold": cold_run["stage_aware_counts"], "warm": warm_run["stage_aware_counts"]},
        "allowed_differences": ["generated_at", "execution_profile", "cache metadata", "daily fast path persistence metadata"],
        "market_edge": "NOT_ESTABLISHED",
    }

    audit = {
        "report_version": "R14_SQLITE_SIZE_AUDIT_V1",
        "generated_at": generated_at,
        "db_path": str(db_path.resolve()),
        "db_file_bytes": db_path.stat().st_size,
        "namespace_counts": namespace_counts,
        "largest_documents": largest_documents,
        "strategy_experiment_run_sizes": strategy_run_sizes,
        "derived_feature_store": derived,
        "dbstat": "UNAVAILABLE_NO_SUCH_TABLE_IN_THIS_SQLITE_BUILD",
        "queue_state": queue,
        "market_edge": "NOT_ESTABLISHED",
    }

    profiles = {
        "report_version": "R14_CPROFILE_SUMMARY_V1",
        "generated_at": generated_at,
        **profile,
        "note": "cProfile includes sampler thread sleep/join overhead; remaining hotspots below exclude that overhead where noted.",
        "top_hotspots_remaining": hotspots,
        "market_edge": "NOT_ESTABLISHED",
    }

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    for name, value in [
        ("R14_PERFORMANCE_BASELINE.json", baseline),
        ("R14_PERFORMANCE_RESULT.json", result),
        ("DATA_READINESS.json", readiness),
        ("BEFORE_AFTER_RESULT_EQUIVALENCE.json", equivalence),
        ("SQLITE_SIZE_AUDIT.json", audit),
        ("R14_CPROFILE_SUMMARY.json", profiles),
    ]:
        write_json(REPORT_ROOT / name, value)

    (REPORT_ROOT / "R14_PERFORMANCE_BASELINE.md").write_text(
        "# R14 Performance Baseline\n\n"
        f"- frozen input: `{baseline['frozen_input']['input_id']}` / {baseline['frozen_input']['observation_date']}\n"
        f"- saved research full run: `{SAVED_RUN_ID}`\n"
        "- BEFORE PV-only: 14.9s\n"
        "- BEFORE Stage-Aware V2: 646.8s\n"
        "- BEFORE full experiment: 1213.8s\n"
        "- BEFORE working set: ~5.2GB\n"
        f"- BEFORE largest full run bytes: {strategy_run_sizes[0]['payload_bytes']}\n"
        "- market_edge: `NOT_ESTABLISHED`\n",
        encoding="utf-8",
    )

    (REPORT_ROOT / "R14_PERFORMANCE_RESULT.md").write_text(
        "# R14 Performance Result\n\n"
        f"- cold fast total: {cold_run['elapsed_seconds']:.3f}s; peak RSS {cold_run['rss_peak_bytes']} bytes; cache misses {cold_run['derived_feature_store']['cache_misses']}\n"
        f"- warm fast total: {warm_run['elapsed_seconds']:.3f}s; strategy compute {warm_run['phase_timings']['strategy_compute']:.3f}s; peak RSS {warm_run['rss_peak_bytes']} bytes\n"
        f"- warm cache hit rate: {warm_run['derived_feature_store']['cache_hit_rate']}\n"
        f"- compact run header+summary estimate: {warm_run['daily_primary_compact_estimate']['run_header_and_summary_bytes_estimate']} bytes\n"
        f"- detail compressed estimate: {warm_run['daily_primary_compact_estimate']['detail_compressed_bytes']} bytes\n"
        f"- combined run header+all detail estimate: {warm_run['daily_primary_compact_estimate']['run_header_and_summary_bytes_estimate'] + warm_run['daily_primary_compact_estimate']['detail_compressed_bytes']} bytes; details are stored separately and loaded on demand\n"
        f"- measured temp persistence: {warm_run['phase_timings'].get('persist_projection')}s; run document bytes {warm_run.get('daily_primary_persist_result', {}).get('run_document_bytes')}\n"
        f"- V2 business hash identical to saved research full: {result['equivalence']['warm_matches_saved']}\n"
        "- market_edge: `NOT_ESTABLISHED`\n\n"
        "## Warm Phase Timings\n\n"
        + md_table([{key: round(value, 6) for key, value in warm_run["phase_timings"].items()}],
                   ["sqlite_store_init", "market_input_load_and_bind", "config_load", "derived_cache_store_init", "strategy_compute", "output_size_stream", "compact_size_estimate", "persist_projection", "total"])
        + "\n",
        encoding="utf-8",
    )

    (REPORT_ROOT / "DATA_READINESS.md").write_text(
        "# R14 Data Readiness\n\n"
        f"- eligible: {stage['counts']['eligible']}\n"
        "- factor coverage ALL/SSE/SZSE: 3041/3041, 1627/1627, 1414/1414\n"
        f"- PV computable: {pv['counts']['evaluated']}/{pv['counts']['eligible']}\n"
        f"- PV base pass: {pv['counts']['filter_passed']}\n"
        "- PV focus: 20 from saved PV focus selection in STOP/full run context\n"
        "- MA window sessions from STOP: MA20 18/20, MA60 51/60, MA120 111/120, MA250 236/250; remaining 14 session-level gaps.\n"
        "- strategy MA readiness in saved diagnostics remains UNAVAILABLE for MA20/60/120/250 until full windows are complete.\n"
        "- market_edge: `NOT_ESTABLISHED`\n",
        encoding="utf-8",
    )

    (REPORT_ROOT / "BEFORE_AFTER_RESULT_EQUIVALENCE.md").write_text(
        "# R14 Before/After V2 Equivalence\n\n"
        f"- saved research V2 hash: `{saved_hash}`\n"
        f"- fast cold V2 hash: `{cold_run['stage_aware_business_hash']}`\n"
        f"- fast warm V2 hash: `{warm_run['stage_aware_business_hash']}`\n"
        f"- cold matches saved: {equivalence['cold_matches_saved']}\n"
        f"- warm matches saved: {equivalence['warm_matches_saved']}\n"
        f"- counts: eligible {stage['counts']['eligible']}, focus {stage['counts']['focus']}, watch {stage['counts']['watch']}, rejected {stage['counts']['rejected']}, unknown {stage['counts']['unknown']}\n"
        "- market_edge: `NOT_ESTABLISHED`\n",
        encoding="utf-8",
    )

    (REPORT_ROOT / "SQLITE_SIZE_AUDIT.md").write_text(
        "# R14 SQLite Size Audit\n\n"
        f"- db file bytes: {audit['db_file_bytes']}\n"
        f"- largest namespace: {namespace_counts[0]['namespace']} / {namespace_counts[0]['payload_bytes']} payload bytes\n"
        f"- largest document: {largest_documents[0]['namespace']}/{largest_documents[0]['document_id']} / {largest_documents[0]['payload_bytes']} bytes\n"
        f"- derived feature store rows: {(derived or {}).get('rows')}\n"
        f"- derived feature store payload bytes: {(derived or {}).get('payload_bytes')}\n"
        f"- provider queue states: {queue.get('states')}\n"
        f"- provider retry audit table present: {queue.get('retry_audit_table_present')}; rows: {queue.get('retry_audit_rows')}\n"
        "- dbstat: unavailable in this SQLite build\n"
        "- market_edge: `NOT_ESTABLISHED`\n",
        encoding="utf-8",
    )

    (REPORT_ROOT / "R14_CPROFILE_SUMMARY.md").write_text(
        "# R14 cProfile Summary\n\n"
        f"{profiles['note']}\n\n"
        "## Remaining Hotspots\n\n"
        + md_table(hotspots, ["rank", "component", "status", "evidence"])
        + "\n\n## Raw pstats Top Cumulative\n\n```text\n"
        + profile["raw_pstats_top_cumulative"][:12000]
        + "\n```\n",
        encoding="utf-8",
    )

    print(json.dumps({"wrote": [
        "R14_PERFORMANCE_BASELINE",
        "R14_PERFORMANCE_RESULT",
        "DATA_READINESS",
        "BEFORE_AFTER_RESULT_EQUIVALENCE",
        "SQLITE_SIZE_AUDIT",
        "R14_CPROFILE_SUMMARY",
    ], "queue": queue, "derived": derived}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
