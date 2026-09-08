"""Generate Codex-facing Recommendation Score artifacts from saved score revisions."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stockfish_core.storage import SQLiteStore
from stockfish_core.yaogu.recommendation_score_artifacts import write_score_artifacts
from stockfish_core.yaogu.recommendation_scoring import (
    OFFICIAL_PROSPECTIVE,
    RECONSTRUCTED_HISTORICAL,
    summarize_score_history,
)
from stockfish_core.yaogu.validation_identity import current_validation_identity


def _all(store, namespace):
    rows, offset = [], 0
    while True:
        page = store.list_documents(namespace, limit=1000, offset=offset)
        rows.extend(row["payload"] for row in page)
        if len(page) < 1000:
            return rows
        offset += len(page)


def _latest(documents, *, run_id=None, evidence_type=None, validation_epoch_id=None):
    rows = [
        row for row in documents
        if (run_id is None or row.get("signal_run_id") == run_id)
        and (evidence_type is None or row.get("evidence_type") == evidence_type)
        and row.get("validation_epoch_id") == validation_epoch_id
    ]
    return max(rows, key=lambda row: (
        row.get("signal_observation_date") or "",
        row.get("revision") or 0,
        row.get("generated_at") or "",
        row.get("score_run_id") or "",
    ), default=None)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--run-id")
    parser.add_argument("--evidence-type")
    parser.add_argument("--validation-epoch-id", help="Read-only historical epoch; defaults to the installed active epoch.")
    args = parser.parse_args(argv)

    store = SQLiteStore(args.db) if args.db else SQLiteStore()
    documents = _all(store, "recommendation_score_revisions")
    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = args.output or ROOT / "reports" / "codex" / datetime.now().strftime("%Y-%m-%d") / "recommendation-score"
    epoch_id = args.validation_epoch_id or current_validation_identity()["validation_epoch_id"]
    latest = _latest(documents, run_id=args.run_id, evidence_type=args.evidence_type, validation_epoch_id=epoch_id)
    files = write_score_artifacts(
        output,
        latest_document=latest,
        official_history=summarize_score_history(documents, evidence_type=OFFICIAL_PROSPECTIVE, validation_epoch_id=epoch_id),
        reconstructed_history=summarize_score_history(documents, evidence_type=RECONSTRUCTED_HISTORICAL, validation_epoch_id=epoch_id),
    )
    result = {
        "generated_at": now,
        "output": str(output.resolve()),
        "latest_score_run_id": latest.get("score_run_id") if latest else None,
        "validation_epoch_id": epoch_id,
        "files": files,
        "market_edge": "NOT_ESTABLISHED",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
