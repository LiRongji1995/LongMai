"""Export actual STAGE_AWARE_MOMENTUM_V2 prospective scheduler state.

This script does not accept a free-form readiness string. It either reads the
running StockFish API or instantiates the real CandidateScheduler against the
configured SQLite state and writes the structured scheduler snapshot.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from urllib.request import urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SH = ZoneInfo("Asia/Shanghai")


def dump_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str, allow_nan=False), encoding="utf-8")


def read_http(base_url):
    url = base_url.rstrip("/") + "/api/candidate-schedule"
    with urlopen(url, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    state = payload.get("prospective_validation") or {}
    state["service_probe"] = {"mode": "HTTP_API", "url": url, "status_code": response.status}
    return state


def read_local(now):
    from stockfish_core.service import ApplicationService
    from stockfish_core.yaogu.scheduler import CandidateScheduler

    service = ApplicationService()
    try:
        scheduler = CandidateScheduler(service.candidates, clock=lambda: now)
        return scheduler.describe_state(now)
    finally:
        service.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--now", default=None)
    args = parser.parse_args()

    now = datetime.fromisoformat(args.now) if args.now else datetime.now(SH)
    if now.tzinfo is None:
        now = now.replace(tzinfo=SH)
    state = read_http(args.base_url) if args.base_url else read_local(now)
    if state.get("source") != "ACTUAL_CANDIDATE_SCHEDULER_STATE_R10":
        raise SystemExit("UNEXPECTED_SCHEDULER_STATE_SOURCE")
    dump_json(args.output, state)
    print(json.dumps({
        "output": str(args.output.resolve()),
        "status": state.get("status"),
        "next_real_primary_capture": state.get("next_real_primary_capture"),
        "scheduler_job_id": state.get("scheduler_job_id"),
        "expected_target_session": state.get("expected_target_session"),
    }, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
