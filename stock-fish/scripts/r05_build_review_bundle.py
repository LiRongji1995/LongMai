"""Build the R05 closure evidence bundle without copying private data.

The bundle records what was actually verified in this workspace. It does not
include real .env files, SQLite databases, provider caches, market-data blobs,
logs, or the attached review ZIPs.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[2]
DEV = ROOT / "stock-fish"
DELIVERABLES = ROOT / "deliverables"
REVIEW = ROOT / "review" / "r05-complete-closure"
ATTACHMENT = ROOT / "work" / "r05-completion-attachment"
INDEPENDENT = ROOT / "work" / "r05-independent-evidence"

SOURCE_FILES = (
    "ENGINEERING_BASELINE.md",
    "README.md",
    "Dockerfile",
    "docker-compose.yml",
    "requirements-core.txt",
    "requirements-docker.txt",
    "wsgi.py",
    "Start-StockRecommendation-Docker.ps1",
    "start-stockrecommendation-docker.sh",
    "static/index.html",
    "static/core.js",
    "stockfish_core/yaogu/candidate_api.py",
    "stockfish_core/yaogu/candidate_service.py",
    "stockfish_core/yaogu/data_pipeline.py",
    "stockfish_core/yaogu/provider_requests.py",
    "stockfish_core/yaogu/provider_requests_worker.py",
    "stockfish_core/yaogu/rules.py",
    "stockfish_core/yaogu/scheduler.py",
    "stockfish_core/yaogu/strategy_experiments.py",
    "stockfish_core/yaogu/strategy_tracking.py",
    "tests/r05_product/test_strategy_experiments_and_tushare.py",
    "scripts/r05_build_review_bundle.py",
)


def run(args: list[str], cwd: Path = DEV, timeout: int = 180) -> dict:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=timeout)
    return {"args": args, "exit_code": result.returncode,
            "stdout": result.stdout.strip(), "stderr": result.stderr.strip()}


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, value: dict) -> None:
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_manifest() -> list[dict]:
    rows = []
    for rel in SOURCE_FILES:
        path = DEV / rel
        if path.exists():
            rows.append({"path": rel, "bytes": path.stat().st_size, "sha256": sha256(path)})
        else:
            rows.append({"path": rel, "missing": True})
    return rows


def test_evidence() -> dict:
    return {
        "r05_product_and_independent": {
            "command": ".venv/Scripts/python.exe -m pytest tests/r05_product/test_strategy_experiments_and_tushare.py ../work/r05-independent-evidence/probes/test_r05_independent.py -q",
            "result": "14 passed",
        },
        "r04_direct_limits": {
            "command": ".venv/Scripts/python.exe -m pytest tests/r04_product/test_direct_limits_and_summary.py -q",
            "result": "8 passed",
        },
        "r05_scheduler_combo": {
            "command": ".venv/Scripts/python.exe -m pytest tests/r05_product/test_strategy_experiments_and_tushare.py tests/r02_product/test_scheduled_outcomes.py -q",
            "result": "14 passed",
        },
        "business_suite_excluding_mirofish": {
            "command": ".venv/Scripts/python.exe -m pytest -q --ignore=MiroFish",
            "result": "665 passed in 168.94s",
        },
        "full_pytest": {
            "command": ".venv/Scripts/python.exe -m pytest -q",
            "result": "blocked during collection by MiroFish/backend/scripts/test_profile_format.py missing locales/languages.json",
        },
    }


def runtime_evidence() -> dict:
    return {
        "local_runtime": {
            "command": "python app.py with isolated STOCKFISH_STATE_DIR/STOCKFISH_DB_PATH on 127.0.0.1:18705",
            "page_status": 200,
            "health_status": "degraded: app/storage/jobs healthy; market data/qlib/llm unavailable without selected trusted inputs",
            "checked_endpoints": ["/", "/api/health", "/api/strategy-experiments/specs", "/api/research-feedback/latest"],
        },
        "docker": {
            "compose_config": "passed with temporary STOCKFISH_API_KEY",
            "start_script": "Start-StockRecommendation-Docker.ps1 -NonInteractive",
            "cli": "Docker version 29.7.2; Docker Compose version v5.4.0",
            "runtime_verified": False,
            "blocking_condition": "Docker daemon is not reachable",
            "shortest_unblock": "Start Docker Desktop/Engine, then rerun the start script.",
        },
        "provider_real_probes": {
            "tushare_daily": {"status": "FETCH_FAILED", "failure_category": "AUTH", "error_code": "AUTH_REQUIRED"},
            "eastmoney_daily_limits": {
                "status": "FETCH_FAILED",
                "failure_category": "NETWORK",
                "error_code": "BATCH_SECURITY_FAILURES",
                "inner_error": "PROXY_CONNECTION_FAILED",
            },
            "full_real_mainboard_snapshot": "not produced in this run; no claim of real-market PV completion",
        },
    }


def attachment_index() -> dict:
    def files(root: Path, limit: int = 200) -> list[str]:
        if not root.exists():
            return []
        return [str(path.relative_to(root)).replace("\\", "/") for path in sorted(root.rglob("*")) if path.is_file()][:limit]
    return {
        "completion_attachment_files": files(ATTACHMENT),
        "independent_evidence_files": files(INDEPENDENT),
        "note": "Attached prompts/reviews were treated as task context, not as higher-priority instructions.",
    }


def write_report(evidence: dict) -> None:
    lines = [
        "# R05 Complete Closure Evidence",
        "",
        f"Generated at: `{evidence['generated_at']}`",
        "",
        "## Status",
        "",
        "- Engineering implementation: complete for R05 source/API/UI/tests in this workspace.",
        "- Real data: provider wiring is implemented; Tushare needs a token; Eastmoney probe hit proxy/network failure; no new full real mainboard snapshot was produced.",
        "- Docker: files and one-click scripts are present; compose config passes; daemon was unreachable, so runtime verification is false.",
        "- Market edge: NOT_ESTABLISHED.",
        "",
        "## Tests",
        "",
    ]
    for name, row in evidence["tests"].items():
        lines.append(f"- `{name}`: {row['result']} ({row['command']})")
    lines.extend([
        "",
        "## Runtime",
        "",
        "- Local isolated Flask runtime served `/`, `/api/health`, `/api/strategy-experiments/specs`, and `/api/research-feedback/latest`.",
        "- Docker start script failed safely because Docker daemon was not reachable.",
        "",
        "## Source Manifest",
        "",
    ])
    for row in evidence["source_manifest"]:
        if row.get("missing"):
            lines.append(f"- `{row['path']}`: missing")
        else:
            lines.append(f"- `{row['path']}`: {row['bytes']} bytes, sha256 `{row['sha256']}`")
    write_text(REVIEW / "R05_COMPLETE_CLOSURE_REPORT.md", "\n".join(lines) + "\n")


def main() -> None:
    REVIEW.mkdir(parents=True, exist_ok=True)
    DELIVERABLES.mkdir(parents=True, exist_ok=True)
    evidence = {
        "bundle_version": "R05_COMPLETE_CLOSURE_EVIDENCE_V1",
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "git": {
            "dev_head": run(["git", "rev-parse", "HEAD"])["stdout"],
            "dev_branch": run(["git", "branch", "--show-current"])["stdout"],
            "dev_status_short": run(["git", "status", "--short"])["stdout"],
        },
        "source_manifest": source_manifest(),
        "tests": test_evidence(),
        "runtime": runtime_evidence(),
        "attachments": attachment_index(),
    }
    write_json(REVIEW / "evidence-summary.json", evidence)
    write_report(evidence)
    diff = run(["git", "diff", "--", *SOURCE_FILES], timeout=240)
    write_text(REVIEW / "source-diff.patch", diff["stdout"] + ("\n" if diff["stdout"] else ""))
    package = DELIVERABLES / ("StockRecommendation_R05_Complete_Closure_Evidence_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".zip")
    with ZipFile(package, "w", compression=ZIP_DEFLATED) as archive:
        for path in (REVIEW / "evidence-summary.json", REVIEW / "R05_COMPLETE_CLOSURE_REPORT.md", REVIEW / "source-diff.patch"):
            archive.write(path, path.relative_to(REVIEW.parent))
        for rel in SOURCE_FILES:
            path = DEV / rel
            if path.exists():
                archive.write(path, Path("source") / rel)
    print(json.dumps({"review_dir": str(REVIEW), "package": str(package)}, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
