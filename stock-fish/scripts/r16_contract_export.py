"""Export a Python baseline Feature/Evidence Contract V1 snapshot."""
from __future__ import annotations

import argparse
from datetime import datetime, time
import json
from pathlib import Path
import sys
import tempfile
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stockfish_core.storage import SQLiteStore  # noqa: E402
from stockfish_core.yaogu.config import StrategyConfig  # noqa: E402
from stockfish_core.yaogu.contracts import MarketInput  # noqa: E402
from stockfish_core.yaogu.derived_feature_store import DerivedFeatureStore  # noqa: E402
from stockfish_core.yaogu.feature_evidence_contract import write_contract_snapshot  # noqa: E402
from stockfish_core.yaogu.history_artifacts import bind_history  # noqa: E402
from stockfish_core.yaogu.strategy_experiments import run_active_daily_strategy  # noqa: E402


SH = ZoneInfo("Asia/Shanghai")


def _latest_input_id(store, observation_date=None):
    wanted = str(observation_date) if observation_date else None
    candidates = []
    offset = 0
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


def _default_cutoff(bundle):
    receipt_times = [receipt.ingested_at for receipt in bundle.receipts if receipt.ingested_at is not None]
    if receipt_times:
        return max(receipt_times)
    return datetime.combine(bundle.observation_date, time(20, 30), SH)


def export(db_path, output_dir, *, input_id=None, observation_date=None, cutoff=None, derived_cache_db=None):
    store = SQLiteStore(db_path)
    resolved_input_id = input_id or _latest_input_id(store, observation_date)
    bundle = _load_bundle(store, resolved_input_id)
    config = _current_config(store)
    decision_cutoff = datetime.fromisoformat(cutoff) if cutoff else _default_cutoff(bundle)
    if derived_cache_db:
        cache_db = Path(derived_cache_db)
    else:
        cache_db = Path(tempfile.mkdtemp(prefix="r16_contract_cache_")) / "derived-cache.sqlite3"
    run = run_active_daily_strategy(
        bundle,
        cutoff=decision_cutoff,
        config=config,
        input_id=resolved_input_id,
        derived_store=DerivedFeatureStore(SQLiteStore(cache_db)),
        execution_profile="DAILY_PRIMARY_FAST_CONTRACT_EXPORT",
    )
    files = write_contract_snapshot(run, output_dir, snapshot_id=resolved_input_id, source_label="PYTHON_BASELINE")
    metadata = {
        "export_version": "R16_PYTHON_BASELINE_CONTRACT_EXPORT_V1",
        "input_id": resolved_input_id,
        "observation_date": str(bundle.observation_date),
        "decision_cutoff": decision_cutoff.isoformat(),
        "manifest_hash": files["manifest_hash"],
        "files": {key: str(value) for key, value in files.items()},
        "market_edge": "NOT_ESTABLISHED",
    }
    Path(output_dir, "python_baseline_export_metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return metadata


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "data" / "stockfish.sqlite3"))
    parser.add_argument("--input-id", default=None)
    parser.add_argument("--observation-date", default=None)
    parser.add_argument("--cutoff", default=None)
    parser.add_argument("--derived-cache-db", default=None)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    print(json.dumps(export(args.db, args.output, input_id=args.input_id, observation_date=args.observation_date,
                            cutoff=args.cutoff, derived_cache_db=args.derived_cache_db),
                     ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
