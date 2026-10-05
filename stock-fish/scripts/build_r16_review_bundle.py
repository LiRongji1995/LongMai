"""Assemble the R16 Go migration phase-0 review bundle.

The bundle is a reproducible evidence package, not an acceptance verdict.  It
copies committed source snapshots, git metadata, post-commit command logs, and
machine-readable benchmark/audit results into one zip with SHA-256 sums.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import textwrap
import zipfile


REPO_ROOT = Path(__file__).resolve().parents[2]
STOCK_FISH_ROOT = REPO_ROOT / "stock-fish"
DEFAULT_DB = STOCK_FISH_ROOT / "data" / "stockfish.sqlite3"

SOURCE_SNAPSHOT_FILES = (
    ".gitignore",
    "DEPLOYMENT.md",
    "ENGINEERING_BASELINE.md",
    "USER_GUIDE.md",
    "CURRENT_STATUS.md",
    "CURRENT_STRATEGY.md",
    "stock-fish/docs/PROJECT_OVERVIEW.md",
    "stock-fish/docs/architecture/ADR_GO_CORE_BOUNDARY.md",
    "stock-fish/docs/architecture/FEATURE_EVIDENCE_CONTRACT_V1.md",
    "stock-fish/schemas/feature_evidence_contract_v1.schema.json",
    "stock-fish/scripts/benchmark_daily_v2.py",
    "stock-fish/scripts/build_r16_review_bundle.py",
    "stock-fish/scripts/r16_compare_contract_snapshots.py",
    "stock-fish/scripts/r16_contract_export.py",
    "stock-fish/scripts/r16_go_shadow_equivalence.py",
    "stock-fish/scripts/r16_history_stability_audit.py",
    "stock-fish/scripts/r16_service_path_trace.py",
    "stock-fish/scripts/r16_validate_contract_schema.py",
    "stock-fish/stockfish_core/yaogu/candidate_service.py",
    "stock-fish/stockfish_core/yaogu/data_pipeline.py",
    "stock-fish/stockfish_core/yaogu/derived_feature_store.py",
    "stock-fish/stockfish_core/yaogu/e2e_tracing.py",
    "stock-fish/stockfish_core/yaogu/feature_evidence_contract.py",
    "stock-fish/stockfish_core/yaogu/provider_normalization.py",
    "stock-fish/stockfish_core/yaogu/source_resolution.py",
    "stock-fish/stockfish_core/yaogu/stage_aware_momentum.py",
    "stock-fish/stockfish_core/yaogu/strategy_experiments.py",
    "stock-fish/tests/r05_product/test_r16_go_migration_phase0.py",
    "stock-fish/tests/fixtures/r16_equivalence/golden_manifest.json",
    "go-core/README.md",
    "go-core/go.mod",
    "go-core/cmd/stockfish-go-core/main.go",
    "go-core/internal/contract/contract.go",
    "go-core/internal/contract/contract_test.go",
    "go-core/internal/shadow/shadow.go",
    "go-core/internal/shadow/shadow_test.go",
)

EVIDENCE_SUFFIXES = {".cprofile", ".csv", ".json", ".log", ".md", ".txt"}
SKIP_EVIDENCE_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".zip"}


def _run_git(args: list[str], *, check: bool = False) -> dict:
    completed = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if check and completed.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {completed.stderr}")
    return {
        "command": "git " + " ".join(args),
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def _load_json(path: Path) -> object | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"parse_error": str(exc), "path": str(path)}


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _changed_files(base_ref: str) -> list[str]:
    result = _run_git(["diff", "--name-only", f"{base_ref}..HEAD"])
    if result["returncode"] != 0:
        return []
    return [line.strip().replace("\\", "/") for line in result["stdout"].splitlines() if line.strip()]


def _copy_file(rel: str, destination_root: Path, copied: list[str], skipped: list[str]) -> None:
    source = REPO_ROOT / rel
    if not source.exists() or not source.is_file():
        skipped.append(rel)
        return
    target = destination_root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    copied.append(rel)


def _copy_source_snapshot(bundle_root: Path, base_ref: str) -> dict:
    destination = bundle_root / "final_source"
    copied: list[str] = []
    skipped: list[str] = []
    wanted = sorted(set(SOURCE_SNAPSHOT_FILES) | set(_changed_files(base_ref)))
    for rel in wanted:
        if rel.startswith("review/") or rel.endswith(".zip"):
            continue
        _copy_file(rel, destination, copied, skipped)
    return {"copied": copied, "skipped": skipped}


def _copy_evidence(evidence_dir: Path, bundle_root: Path) -> dict:
    destination = bundle_root / "execution_evidence"
    copied: list[str] = []
    skipped: list[dict] = []
    evidence_dir = evidence_dir.resolve()
    if not evidence_dir.exists():
        return {"copied": copied, "skipped": [{"path": str(evidence_dir), "reason": "missing"}]}
    for path in sorted(evidence_dir.rglob("*")):
        if not path.is_file():
            continue
        if "bundle_contents" in path.parts:
            continue
        if path.suffix.lower() in SKIP_EVIDENCE_SUFFIXES:
            skipped.append({"path": str(path), "reason": "runtime_db_or_zip_excluded"})
            continue
        if path.suffix.lower() not in EVIDENCE_SUFFIXES:
            skipped.append({"path": str(path), "reason": "suffix_not_included"})
            continue
        rel = path.relative_to(evidence_dir)
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied.append(str(rel).replace("\\", "/"))
    return {"copied": copied, "skipped": skipped}


def _collect_git_metadata(bundle_root: Path, base_ref: str) -> dict:
    commands = {
        "head": ["rev-parse", "HEAD"],
        "head_tree": ["rev-parse", "HEAD^{tree}"],
        "base": ["rev-parse", base_ref],
        "origin_main": ["rev-parse", "origin/main"],
        "branch": ["branch", "--show-current"],
        "remote_origin": ["remote", "get-url", "origin"],
        "status_short": ["status", "--short", "--branch"],
        "log": ["log", "-n", "12", "--oneline", "--decorate"],
        "show_stat": ["show", "--stat", "--oneline", "--decorate", "--no-renames", "HEAD"],
        "diff_name_status": ["diff", "--name-status", f"{base_ref}..HEAD"],
        "diff_full": ["diff", "--no-ext-diff", f"{base_ref}..HEAD", "--", "."],
        "ls_files": ["ls-files"],
    }
    results = {name: _run_git(args) for name, args in commands.items()}
    git_dir = bundle_root / "git"
    for name, result in results.items():
        _write_text(
            git_dir / f"{name}.txt",
            f"COMMAND: {result['command']}\nEXIT_CODE: {result['returncode']}\n\nSTDOUT\n{result['stdout']}\nSTDERR\n{result['stderr']}\n",
        )
    return results


def _find_first(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def _performance_path(evidence_dir: Path) -> Path | None:
    return _find_first([
        evidence_dir / "performance" / "r16_performance_result.json",
        evidence_dir / "performance" / "R16_PERFORMANCE_RESULT.json",
        evidence_dir / "r16_performance_result.json",
    ])


def _profile_path(evidence_dir: Path) -> Path | None:
    return _find_first([
        evidence_dir / "profiles" / "r16_profile_result.json",
        evidence_dir / "performance" / "r16_profile_result.json",
        evidence_dir / "r16_profile_result.json",
    ])


def _history_path(evidence_dir: Path) -> Path | None:
    return _find_first([
        evidence_dir / "history" / "r16_history_stability_audit.json",
        evidence_dir / "history" / "R16_HISTORY_STABILITY_AUDIT.json",
        evidence_dir / "r16_history_stability_audit.json",
    ])


def _service_trace_path(evidence_dir: Path) -> Path | None:
    return _find_first([
        evidence_dir / "e2e" / "r16_service_path_trace.json",
        evidence_dir / "r16_service_path_trace.json",
    ])


def _equivalence_path(evidence_dir: Path) -> Path | None:
    return _find_first([
        evidence_dir / "equivalence" / "python_go_shadow_comparison.json",
        evidence_dir / "python_go_shadow_comparison.json",
    ])


def _iter_runs(perf: object | None) -> list[tuple[str, dict]]:
    if not isinstance(perf, dict):
        return []
    runs = perf.get("runs")
    if isinstance(runs, dict):
        return [(str(name), run) for name, run in runs.items() if isinstance(run, dict)]
    if isinstance(runs, list):
        return [(str(run.get("profile") or f"run_{index}"), run) for index, run in enumerate(runs) if isinstance(run, dict)]
    return []


def _cache_metrics(bundle_root: Path, perf: object | None) -> dict:
    rows: list[dict] = []
    for name, run in _iter_runs(perf):
        stats = run.get("derived_feature_store") or {}
        lifecycle = stats.get("row_lifecycle") or {}
        family = stats.get("family_cache") or {}
        rows.append({
            "run_name": name,
            "status": run.get("status"),
            "observation_date": run.get("observation_date"),
            "elapsed_seconds": run.get("elapsed_seconds"),
            "rss_peak_bytes": run.get("rss_peak_bytes"),
            "requested_feature_rows": stats.get("requested_feature_rows"),
            "full_row_reused": lifecycle.get("full_row_reused", stats.get("full_row_reused")),
            "partial_family_reused": lifecycle.get("partial_family_reused", stats.get("partial_family_reused_rows")),
            "new_rows": lifecycle.get("new_rows", stats.get("new_rows")),
            "invalidated_rows": lifecycle.get("invalidated_rows", stats.get("invalidated_rows")),
            "missing_cache_rows": lifecycle.get("missing_cache_rows", stats.get("missing_cache_rows")),
            "corrupt_or_conflict_rows": lifecycle.get("corrupt_or_conflict_rows"),
            "cache_hit_rate": stats.get("cache_hit_rate"),
            "reuse_ratio": stats.get("reuse_ratio"),
            "family_requested": family.get("requested"),
            "family_hits": family.get("hits"),
            "family_misses": family.get("misses"),
            "family_inserted": family.get("inserted"),
            "family_invalidated": family.get("invalidated"),
            "family_lifecycle": family.get("lifecycle"),
        })
    result = {
        "status": "COLLECTED" if rows else "NOT_COLLECTED",
        "rows": rows,
        "policy": "R16_CACHE_ROW_FAMILY_LIFECYCLE_V1",
        "note": "Rows are sourced from post-commit benchmark JSON when present.",
    }
    _write_json(bundle_root / "reports" / "r16_cache_metrics.json", result)
    csv_path = bundle_root / "reports" / "r16_cache_metrics.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    if rows:
        with csv_path.open("w", encoding="utf-8", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            for row in rows:
                writable = dict(row)
                if isinstance(writable.get("family_lifecycle"), dict):
                    writable["family_lifecycle"] = json.dumps(writable["family_lifecycle"], ensure_ascii=False, sort_keys=True)
                writer.writerow(writable)
    else:
        _write_text(csv_path, "status,note\nNOT_COLLECTED,benchmark JSON not found\n")
    return result


def _extract_e2e_trace(bundle_root: Path, perf: object | None, service_trace: object | None) -> dict:
    traces: list[dict] = []
    for name, run in _iter_runs(perf):
        trace = run.get("e2e_trace")
        if isinstance(trace, dict):
            trace = dict(trace)
            trace["evidence_run_name"] = name
            traces.append(trace)
    if isinstance(service_trace, dict):
        trace = service_trace.get("e2e_trace")
        if isinstance(trace, dict):
            trace = dict(trace)
            trace["evidence_run_name"] = "CANDIDATE_SERVICE_DAILY_PRIMARY"
            traces.append(trace)
    required = [
        "USER_REQUEST_COMMAND_START",
        "CALENDAR_TRADING_SESSION_RESOLUTION",
        "DATA_FRESHNESS_STATUS_CHECK",
        "PROVIDER_REFRESH_INCREMENTAL_INGESTION",
        "HISTORY_ARTIFACT_UPDATE",
        "MARKET_INPUT_CREATION_OR_LOAD",
        "SOURCE_RESOLUTION",
        "DEPENDENCY_PLANNING",
        "CACHE_LOOKUP",
        "FEATURE_COMPUTATION",
        "STRATEGY_COMPUTE",
        "PERSISTENCE",
        "CANDIDATE_EXPORT",
        "REPORT_RENDER_API_RESPONSE",
        "RESULT_READY",
    ]
    coverage = []
    for trace in traces:
        seen = {stage.get("name") for stage in trace.get("stages") or []}
        coverage.append({
            "run_name": trace.get("evidence_run_name"),
            "status": trace.get("status"),
            "elapsed_ms": trace.get("elapsed_ms"),
            "missing_stages": [stage for stage in required if stage not in seen],
        })
    result = {
        "status": "COLLECTED" if traces else "NOT_COLLECTED",
        "required_stages": required,
        "coverage": coverage,
        "traces": traces,
    }
    _write_json(bundle_root / "reports" / "r16_e2e_trace.json", result)
    return result


def _storage_report(bundle_root: Path, perf: object | None) -> dict:
    storage = perf.get("storage") if isinstance(perf, dict) else None
    result: dict = {"status": "NOT_COLLECTED"}
    if isinstance(storage, dict):
        after_t = storage.get("after_day_t") or {}
        after_next = storage.get("after_day_t_plus_1") or {}
        payload_t = ((after_t.get("v2") or {}).get("payload_bytes") or 0)
        payload_next = ((after_next.get("v2") or {}).get("payload_bytes") or 0)
        db_growth = storage.get("incremental_db_growth_bytes")
        payload_growth = payload_next - payload_t if payload_next or payload_t else None
        result = {
            "status": "COLLECTED",
            "day_t_store_size_bytes": storage.get("day_t_store_size_bytes"),
            "day_t_plus_1_store_size_bytes": storage.get("day_t_plus_1_store_size_bytes"),
            "incremental_db_growth_bytes": db_growth,
            "day_t_payload_bytes": payload_t,
            "day_t_plus_1_payload_bytes": payload_next,
            "incremental_payload_growth_bytes": payload_growth,
            "sqlite_overhead_growth_bytes": db_growth - payload_growth if db_growth is not None and payload_growth is not None else None,
            "go_store_design_implications": [
                "Do not key derived facts by whole input/history artifact when row-level source tokens are available.",
                "Avoid duplicating large JSON lineage blobs per family row; keep provenance as compact hashes plus optional side tables.",
                "Measure index bloat separately from payload growth before choosing SQLite, Parquet, or a Go-native KV layout.",
            ],
        }
    lines = [
        "# R16 Derived Store Growth Analysis",
        "",
        f"- status: `{result['status']}`",
    ]
    for key in (
        "day_t_store_size_bytes",
        "day_t_plus_1_store_size_bytes",
        "incremental_db_growth_bytes",
        "day_t_payload_bytes",
        "day_t_plus_1_payload_bytes",
        "incremental_payload_growth_bytes",
        "sqlite_overhead_growth_bytes",
    ):
        if key in result:
            lines.append(f"- {key}: `{result.get(key)}`")
    lines.extend([
        "",
        "## Go Store Notes",
        "",
    ])
    for item in result.get("go_store_design_implications", []):
        lines.append(f"- {item}")
    _write_json(bundle_root / "reports" / "r16_store_growth_analysis.json", result)
    _write_text(bundle_root / "reports" / "R16_STORE_GROWTH_ANALYSIS.md", "\n".join(lines))
    return result


def _data_readiness_report(bundle_root: Path, perf: object | None, history: object | None) -> dict:
    run_rows = []
    for name, run in _iter_runs(perf):
        run_rows.append({
            "run_name": name,
            "input_id": run.get("input_id"),
            "observation_date": run.get("observation_date"),
            "coverage_type": run.get("coverage_type"),
            "stage_aware_counts": run.get("stage_aware_counts"),
            "data_readiness": run.get("data_readiness"),
            "data_coverage_diagnostics": run.get("data_coverage_diagnostics"),
        })
    result = {
        "status": "COLLECTED" if run_rows or isinstance(history, dict) else "NOT_COLLECTED",
        "runs": run_rows,
        "history_append_only_status": (history or {}).get("append_only_invariant") if isinstance(history, dict) else None,
        "history_root_cause": (history or {}).get("root_cause") if isinstance(history, dict) else None,
        "market_edge": "NOT_ESTABLISHED",
    }
    lines = [
        "# R16 Data Readiness",
        "",
        f"- status: `{result['status']}`",
        f"- history_root_cause: `{result.get('history_root_cause')}`",
        f"- market_edge: `{result['market_edge']}`",
        "",
        "## Runs",
        "",
    ]
    for row in run_rows:
        lines.extend([
            f"### {row['run_name']}",
            "",
            f"- input_id: `{row.get('input_id')}`",
            f"- observation_date: `{row.get('observation_date')}`",
            f"- coverage_type: `{row.get('coverage_type')}`",
            f"- stage_aware_counts: `{json.dumps(row.get('stage_aware_counts'), ensure_ascii=False, sort_keys=True, default=str)}`",
            "",
        ])
    _write_json(bundle_root / "reports" / "R16_DATA_READINESS.json", result)
    _write_text(bundle_root / "reports" / "R16_DATA_READINESS.md", "\n".join(lines))
    return result


def _prospective_audit(db_path: Path) -> dict:
    result = {
        "status": "NOT_VERIFIED",
        "db_path": str(db_path),
        "generated_at": datetime.now().astimezone().isoformat(),
        "checks": [],
        "market_edge": "NOT_ESTABLISHED",
    }
    if not db_path.exists():
        result["checks"].append({"name": "db_exists", "status": "FAIL", "detail": "database file missing"})
        return result
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        namespaces = [dict(row) for row in con.execute(
            "SELECT namespace, count(*) AS rows FROM documents GROUP BY namespace ORDER BY namespace"
        )]
        ns_count = {row["namespace"]: row["rows"] for row in namespaces}
        result["namespaces"] = namespaces
        result["checks"].append({"name": "strategy_experiment_runs_rows", "status": "OBSERVED", "rows": ns_count.get("strategy_experiment_runs", 0)})
        result["checks"].append({"name": "strategy_trading_session_observations_rows", "status": "OBSERVED", "rows": ns_count.get("strategy_trading_session_observations", 0)})
        candidates = []
        for row in con.execute(
            "SELECT document_id, created_at, payload FROM documents "
            "WHERE namespace='strategy_experiment_runs' ORDER BY created_at DESC LIMIT 200"
        ):
            try:
                payload = json.loads(row["payload"])
            except (TypeError, json.JSONDecodeError):
                continue
            pv = payload.get("prospective_validation") or {}
            if pv.get("validation_role") or pv.get("primary_signal_cutoff") or pv.get("protocol_id"):
                candidates.append({
                    "document_id": row["document_id"],
                    "created_at": row["created_at"],
                    "run_id": payload.get("run_id"),
                    "observation_date": payload.get("observation_date"),
                    "validation_role": pv.get("validation_role"),
                    "official_primary_capture": pv.get("official_primary_capture"),
                    "official_primary_signal_countable": pv.get("official_primary_signal_countable"),
                    "primary_cutoff_policy_id": pv.get("primary_cutoff_policy_id"),
                    "actual_decision_cutoff": pv.get("actual_decision_cutoff"),
                    "stage_aware_counts": pv.get("stage_aware_counts"),
                })
        result["latest_prospective_candidates"] = candidates[:10]
        if candidates:
            result["status"] = "OBSERVED_NOT_INDEPENDENTLY_VALIDATED"
            result["checks"].append({"name": "latest_prospective_metadata", "status": "OBSERVED", "count": len(candidates)})
        else:
            result["checks"].append({"name": "latest_prospective_metadata", "status": "NOT_FOUND_IN_RECENT_RUNS"})
    finally:
        con.close()
    return result


def _write_prospective_report(bundle_root: Path, result: dict) -> None:
    lines = [
        "# R16 Prospective Audit",
        "",
        f"- status: `{result.get('status')}`",
        f"- db_path: `{result.get('db_path')}`",
        f"- market_edge: `{result.get('market_edge')}`",
        "",
        "This audit is a read-only metadata snapshot. It does not certify investment edge or count a reconstructed run as prospective.",
        "",
        "## Checks",
        "",
    ]
    for check in result.get("checks") or []:
        lines.append(f"- {check.get('name')}: `{check.get('status')}`")
    lines.extend(["", "## Latest Candidate Rows", ""])
    for row in result.get("latest_prospective_candidates") or []:
        lines.append(
            f"- `{row.get('document_id')}` `{row.get('validation_role')}` countable=`{row.get('official_primary_signal_countable')}` cutoff=`{row.get('actual_decision_cutoff')}`"
        )
    _write_json(bundle_root / "reports" / "R16_PROSPECTIVE_AUDIT.json", result)
    _write_text(bundle_root / "reports" / "R16_PROSPECTIVE_AUDIT.md", "\n".join(lines))


def _implementation_report(bundle_root: Path, git_meta: dict, perf: object | None, history: object | None, equivalence: object | None) -> None:
    head = (git_meta.get("head") or {}).get("stdout", "").strip()
    tree = (git_meta.get("head_tree") or {}).get("stdout", "").strip()
    origin_main = (git_meta.get("origin_main") or {}).get("stdout", "").strip()
    changed = (git_meta.get("diff_name_status") or {}).get("stdout", "").strip()
    perf_version = perf.get("benchmark_version") if isinstance(perf, dict) else None
    history_status = ((history or {}).get("append_only_invariant") or {}).get("status") if isinstance(history, dict) else None
    equivalence_status = equivalence.get("matched") if isinstance(equivalence, dict) else None
    text = f"""# R16 Implementation Report

Generated: `{datetime.now().astimezone().isoformat()}`

This package is evidence for independent review. It is not a final PASS
declaration.

## Git Identity

- HEAD: `{head}`
- tree: `{tree}`
- origin/main: `{origin_main}`

## Scope

- Contract V1 for feature/evidence rows and manifest validation.
- Canonical resolved price source view shared by dependency fingerprinting and feature materialization.
- R16 cache lifecycle taxonomy separating new, missing, source revision invalidation, dependency/spec/policy invalidation, and corrupt/conflict cases.
- Daily Primary E2E tracing across command, freshness, source resolution, dependency planning, cache lookup, feature build, strategy, persistence, export, response, and result ready.
- Go Phase 0 skeleton and Python-vs-Go shadow fact equivalence fixture.
- Future artifact-build repair: normalization can carry the prior history artifact as `base_ref`.

## Evidence Status

- performance_benchmark_version: `{perf_version}`
- history_append_only_status: `{history_status}`
- go_shadow_equivalence_matched: `{equivalence_status}`
- market_edge: `NOT_ESTABLISHED`

## Changed Files

```text
{changed}
```
"""
    _write_text(bundle_root / "reports" / "R16_IMPLEMENTATION_REPORT.md", textwrap.dedent(text).strip() + "\n")


def _cache_identity_report(bundle_root: Path, history: object | None, cache_metrics: dict) -> None:
    counts = (history or {}).get("classification_counts") if isinstance(history, dict) else None
    lines = [
        "# R16 Cache Identity Audit",
        "",
        "- cache_identity: `FEATURE_SPEC_VERSION + strategy_dependency_version + canonical source token + family/session/instrument range`",
        "- resolver_version: `CANONICAL_RESOLVED_SOURCE_VIEW_V1`",
        "- lifecycle_policy: `R16_CACHE_ROW_FAMILY_LIFECYCLE_V1`",
        "- dependency_completeness_invariant: `SOURCE_RESOLUTION_AND_FEATURE_BUILD_SHARE_IDENTICAL_CANONICAL_VIEW`",
        "",
        "## Cache Metrics",
        "",
        f"- status: `{cache_metrics.get('status')}`",
        "",
        "## Historical Source Stability",
        "",
    ]
    if counts:
        for key, value in sorted(counts.items()):
            lines.append(f"- {key}: `{value}`")
    else:
        lines.append("- `NOT_COLLECTED`")
    _write_text(bundle_root / "reports" / "R16_CACHE_IDENTITY_AUDIT.md", "\n".join(lines) + "\n")


def _reproduce(bundle_root: Path) -> None:
    text = """# Reproduce R16 Evidence

Run from the repository root after the R16 commit:

```powershell
git diff --check
& stock-fish\\.venv\\Scripts\\python.exe -X utf8 -m pytest stock-fish\\tests\\r05_product\\test_r16_go_migration_phase0.py -q
& stock-fish\\.venv\\Scripts\\python.exe -X utf8 stock-fish\\scripts\\run_core_tests.py tests -q
Push-Location go-core; go test ./...; go vet ./...; Pop-Location
& stock-fish\\.venv\\Scripts\\python.exe -X utf8 stock-fish\\scripts\\r16_history_stability_audit.py --output-json review\\<evidence>\\history\\r16_history_stability_audit.json --output-md review\\<evidence>\\history\\R16_HISTORY_STABILITY_AUDIT.md
& stock-fish\\.venv\\Scripts\\python.exe -X utf8 stock-fish\\scripts\\benchmark_daily_v2.py --db stock-fish\\data\\stockfish.sqlite3 --r15-true-incremental --input-id market_63f83798f5098898debce30e0810a0c54f6a0daffdc75ac6404d47d5f8488a23 --next-input-id market_04d95df6f6f699c7f88353d181308159526865877a666a7c3efb84be01b9c08d --derived-cache-db review\\<evidence>\\performance\\r16_derived_cache.sqlite3 --output-json review\\<evidence>\\performance\\r16_performance_result.json --output-md review\\<evidence>\\performance\\R16_E2E_PERFORMANCE_REPORT.md --timeout-seconds 900
& stock-fish\\.venv\\Scripts\\python.exe -X utf8 stock-fish\\scripts\\r16_go_shadow_equivalence.py --output-dir review\\<evidence>\\equivalence
& stock-fish\\.venv\\Scripts\\python.exe -X utf8 stock-fish\\scripts\\build_r16_review_bundle.py --evidence-dir review\\<evidence>
```
"""
    _write_text(bundle_root / "REPRODUCE.md", text)


def _write_metadata(bundle_root: Path, evidence_dir: Path, git_meta: dict, copy_sources: dict, copy_evidence: dict) -> dict:
    metadata = {
        "bundle_version": "R16_GO_MIGRATION_PHASE0_REVIEW_BUNDLE_V1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "repo_root": str(REPO_ROOT),
        "evidence_dir": str(evidence_dir.resolve()),
        "head": (git_meta.get("head") or {}).get("stdout", "").strip(),
        "tree": (git_meta.get("head_tree") or {}).get("stdout", "").strip(),
        "origin_main": (git_meta.get("origin_main") or {}).get("stdout", "").strip(),
        "source_snapshot": copy_sources,
        "execution_evidence": copy_evidence,
        "self_assessment": "EVIDENCE_PACKAGE_READY_FOR_INDEPENDENT_REVIEW_NOT_FINAL_PASS",
        "market_edge": "NOT_ESTABLISHED",
    }
    _write_json(bundle_root / "REVIEW_METADATA.json", metadata)
    _write_text(
        bundle_root / "REVIEW_METADATA.md",
        "\n".join([
            "# R16 Review Metadata",
            "",
            f"- bundle_version: `{metadata['bundle_version']}`",
            f"- generated_at: `{metadata['generated_at']}`",
            f"- head: `{metadata['head']}`",
            f"- tree: `{metadata['tree']}`",
            f"- origin_main: `{metadata['origin_main']}`",
            f"- self_assessment: `{metadata['self_assessment']}`",
            f"- market_edge: `{metadata['market_edge']}`",
        ]) + "\n",
    )
    return metadata


def _write_sha256s(bundle_root: Path) -> list[dict]:
    rows = []
    for path in sorted(bundle_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(bundle_root).as_posix()
        rows.append({"sha256": _sha256_file(path), "path": rel})
    _write_text(bundle_root / "SHA256SUMS", "".join(f"{row['sha256']}  {row['path']}\n" for row in rows))
    return rows


def _zip_bundle(bundle_root: Path, output_zip: Path) -> None:
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    if output_zip.exists():
        output_zip.unlink()
    with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(bundle_root.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(bundle_root).as_posix())


def build(evidence_dir: Path, output_zip: Path | None, base_ref: str, db_path: Path) -> dict:
    evidence_dir = evidence_dir.resolve()
    bundle_root = evidence_dir / "bundle_contents"
    if bundle_root.exists():
        shutil.rmtree(bundle_root)
    bundle_root.mkdir(parents=True, exist_ok=True)

    git_meta = _collect_git_metadata(bundle_root, base_ref)
    copied_sources = _copy_source_snapshot(bundle_root, base_ref)
    copied_evidence = _copy_evidence(evidence_dir, bundle_root)

    perf = _load_json(_performance_path(evidence_dir)) if _performance_path(evidence_dir) else None
    profile = _load_json(_profile_path(evidence_dir)) if _profile_path(evidence_dir) else None
    history = _load_json(_history_path(evidence_dir)) if _history_path(evidence_dir) else None
    service_trace = _load_json(_service_trace_path(evidence_dir)) if _service_trace_path(evidence_dir) else None
    equivalence = _load_json(_equivalence_path(evidence_dir)) if _equivalence_path(evidence_dir) else None

    cache_metrics = _cache_metrics(bundle_root, perf)
    e2e = _extract_e2e_trace(bundle_root, perf, service_trace)
    storage = _storage_report(bundle_root, perf)
    data_readiness = _data_readiness_report(bundle_root, perf, history)
    prospective = _prospective_audit(db_path)
    _write_prospective_report(bundle_root, prospective)
    _implementation_report(bundle_root, git_meta, perf, history, equivalence)
    _cache_identity_report(bundle_root, history, cache_metrics)
    _reproduce(bundle_root)

    summary = {
        "performance_loaded": isinstance(perf, dict),
        "profile_loaded": isinstance(profile, dict),
        "history_loaded": isinstance(history, dict),
        "service_trace_loaded": isinstance(service_trace, dict),
        "equivalence_loaded": isinstance(equivalence, dict),
        "cache_metrics_status": cache_metrics.get("status"),
        "e2e_status": e2e.get("status"),
        "storage_status": storage.get("status"),
        "data_readiness_status": data_readiness.get("status"),
        "prospective_status": prospective.get("status"),
    }
    _write_json(bundle_root / "reports" / "R16_EVIDENCE_SUMMARY.json", summary)
    _write_metadata(bundle_root, evidence_dir, git_meta, copied_sources, copied_evidence)
    _write_sha256s(bundle_root)

    if output_zip is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_zip = REPO_ROOT / f"StockRecommendation_R16_GoMigrationPhase0_ReviewBundle_{stamp}.zip"
    else:
        output_zip = output_zip.resolve()
    _zip_bundle(bundle_root, output_zip)
    result = {
        "bundle_path": str(output_zip),
        "bundle_sha256": _sha256_file(output_zip),
        "bundle_bytes": output_zip.stat().st_size,
        "bundle_root": str(bundle_root),
        "summary": summary,
        "market_edge": "NOT_ESTABLISHED",
    }
    _write_json(evidence_dir / "r16_review_bundle_result.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--output-zip", default=None)
    parser.add_argument("--base-ref", default="HEAD^")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    args = parser.parse_args(argv)
    build(
        Path(args.evidence_dir),
        Path(args.output_zip) if args.output_zip else None,
        args.base_ref,
        Path(args.db).resolve(),
    )


if __name__ == "__main__":
    main()
