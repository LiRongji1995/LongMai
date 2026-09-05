"""Local administrative artifact ingestion. No HTTP endpoint accepts trust assertions."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import settings  # Load the preserved deployment environment.
from stockfish_core.common import digest
from stockfish_core.model_registry import ModelRegistry, file_hash
from stockfish_core.observed_data import MarketObservation, exchange_calendar_2026
from stockfish_core.service import ApplicationService, InputSnapshot, RecommendationRequest
from stockfish_core.storage import SQLiteStore
from stockfish_core.tracker import RecommendationTracker


def state_root():
    return Path(os.environ.get("STOCKFISH_STATE_DIR") or ROOT / "data" / "core").resolve()


def activate(snapshot_id: str, store: SQLiteStore):
    value = store.get_document("data_snapshots", snapshot_id)
    if value is None or snapshot_id != "data_" + digest(value):
        raise ValueError("unknown or corrupt snapshot")
    InputSnapshot.model_validate(value)
    root = state_root()
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / (".active-" + uuid.uuid4().hex)
    with temporary.open("x", encoding="utf-8") as stream:
        stream.write(snapshot_id)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, root / "active_snapshot.txt")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    observed = sub.add_parser("import-observation")
    observed.add_argument("path", type=Path)
    observed.add_argument("--activate", action="store_true")
    snapshot = sub.add_parser("import-snapshot")
    snapshot.add_argument("path", type=Path)
    snapshot.add_argument("--activate", action="store_true")
    selected = sub.add_parser("activate-snapshot")
    selected.add_argument("snapshot_id")
    model = sub.add_parser("import-reviewed-model")
    model.add_argument("path", type=Path)
    model.add_argument("--expected-sha256", required=True)
    run = sub.add_parser("recommend")
    run.add_argument("request", type=Path)
    run.add_argument("--output", type=Path)
    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("recommendation_id")
    evaluate.add_argument("input", type=Path, help="raw bars, benchmark_bars, sessions and cost assumptions; no supplied returns")
    evaluate.add_argument("--horizon", type=int, default=7)
    args = parser.parse_args()
    store = SQLiteStore()
    if args.command in ("import-observation", "import-snapshot"):
        raw = json.loads(args.path.read_text(encoding="utf-8"))
        if args.command == "import-observation":
            observation = MarketObservation.model_validate(raw)
            calendar = exchange_calendar_2026()
            payload = InputSnapshot(as_of=max(b.timestamp for b in observation.bars), available_at=observation.observed_at,
                source="baostock+eastmoney_observed_public_data", kind="OBSERVED", bars=observation.bars,
                securities=observation.security_states, sessions=calendar.sessions,
                calendar_start=calendar.coverage_start, calendar_end=calendar.coverage_end,
                data_license="REVIEW_REQUIRED", evidence={"observation": raw, "input_file_sha256": file_hash(args.path),
                    "original_availability": "UNKNOWN", "execution_recheck_required": True})
        else:
            payload = InputSnapshot.model_validate(raw)
        value = payload.model_dump(mode="json")
        snapshot_id = "data_" + digest(value)
        store.put_document("data_snapshots", snapshot_id, value)
        if args.activate:
            activate(snapshot_id, store)
        print(json.dumps({"snapshot_id": snapshot_id, "kind": payload.kind, "source": payload.source,
                          "active": args.activate, "data_license": payload.data_license}, ensure_ascii=False))
    elif args.command == "activate-snapshot":
        activate(args.snapshot_id, store)
        print(json.dumps({"active_snapshot": args.snapshot_id}))
    elif args.command == "import-reviewed-model":
        result = ModelRegistry(state_root() / "model_registry").import_legacy(args.path, expected_checkpoint_hash=args.expected_sha256)
        print(result.model_dump_json(indent=2))
    elif args.command == "recommend":
        core = ApplicationService(store=store)
        try:
            result = core.recommend(RecommendationRequest.model_validate_json(args.request.read_text(encoding="utf-8")))
            text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
            if args.output:
                with args.output.open("x", encoding="utf-8") as stream:
                    stream.write(text)
            print(text)
        finally:
            core.close()
    elif args.command == "evaluate":
        from datetime import date
        inputs = json.loads(args.input.read_text(encoding="utf-8"))
        result = RecommendationTracker(store).evaluate(args.recommendation_id, inputs["bars"], inputs["benchmark_bars"],
            horizon=args.horizon, costs=inputs.get("costs"), trading_sessions=[date.fromisoformat(d) for d in inputs["sessions"]])
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
