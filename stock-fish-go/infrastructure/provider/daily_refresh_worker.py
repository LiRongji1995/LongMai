"""Fixed public capture only. Never evaluates a strategy or connects to an LLM."""
import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "python"))


def main():
    request = json.load(sys.stdin)
    if request.get("contract") != "FIXED_MAINBOARD_PUBLIC_V1":
        raise ValueError("UNREGISTERED_PROVIDER_CONTRACT")
    root = Path(request["output"])
    if not root.is_dir() or (root / "capture").exists():
        raise ValueError("NEW_CAPTURE_DIRECTORY_REQUIRED")
    from stockfish_core.yaogu.config import StrategyConfig
    from stockfish_core.yaogu.data_pipeline import MainboardPipeline, CAPTURE_PURPOSE_DAILY_PRIMARY_CRITICAL
    from stockfish_core.yaogu.request_queue import RequestBudget
    wall, mono = datetime.now(timezone.utc), time.monotonic()

    def checkpoint():
        if sum(path.stat().st_size for path in root.rglob("*") if path.is_file()) > (1 << 30):
            raise ValueError("REFRESH_ARTIFACT_LIMIT")
        waited = (datetime.now(timezone.utc) - wall).total_seconds()
        elapsed = time.monotonic() - mono
        if waited < -2 or abs(waited - elapsed) > 2:
            raise ValueError("CLOCK_OR_SUSPEND_ANOMALY")
        if max(waited, elapsed) >= 600:
            raise ValueError("REFRESH_WAIT_BUDGET_EXCEEDED")

    pipeline = MainboardPipeline(root / "queue", config=StrategyConfig(),
        budget=RequestBudget(max_requests=100, max_seconds=600,
                             timeout_seconds=40, max_attempts=2, minimum_interval_seconds=.25))
    # The critical path returns normalized input/readiness, before background
    # strategy preview. Its existing persistent HTTP ledger includes retries.
    report = pipeline.capture(root / "capture", datetime.now(timezone.utc),
        observation_date=date.fromisoformat(request["observation_date"]),
        purpose=CAPTURE_PURPOSE_DAILY_PRIMARY_CRITICAL,
        checkpoint=checkpoint, fast_decision=False)
    checkpoint()
    raw = json.loads((root / "capture" / "market-input.json").read_text())
    cutoff = datetime.now(timezone.utc).isoformat()
    envelope = {"purpose": "PROSPECTIVE_CAPTURED", "input": raw, "cutoff": cutoff}
    frozen = root / "frozen.json"
    with frozen.open("x") as handle:
        json.dump(envelope, handle, ensure_ascii=False, allow_nan=False)
    print(json.dumps({"contract": request["contract"], "status": "COMPLETE" if report.get("actual_daily_ready") else "PARTIAL",
                      "frozen_file": "frozen.json", "started_at": wall.isoformat(), "completed_at": cutoff,
                      "elapsed_seconds": time.monotonic() - mono,
                      "report_file": "capture/capture-report.json", "ai_dependencies": []}))


if __name__ == "__main__":
    if sys.argv[1:] == ["--check"]:
        import importlib.util
        modules = ("akshare", "baostock", "requests", "pydantic", "yaml")
        missing = [name for name in modules if importlib.util.find_spec(name) is None]
        print(json.dumps({"network_probe": False, "missing_dependencies": missing}))
        raise SystemExit(bool(missing))
    try:
        main()
    except Exception:
        # SDK details can contain secrets. Persistent receipts remain private;
        # the parent records a failure attempt, never a fictitious frozen input.
        print(json.dumps({"status": "FAILED", "reason": "FIXED_CAPTURE_FAILED_OR_DEPENDENCY_MISSING"}))
        raise SystemExit(1)
