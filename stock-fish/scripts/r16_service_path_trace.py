"""Generate an R16 E2E trace from the CandidateService Daily Primary builder.

The source MarketInput and history artifact are read from the configured
production store, but service/cache writes are routed to an isolated temporary
SQLite store.  This keeps the evidence close to the actual service code path
without mutating production data.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stockfish_core.service import ApplicationService  # noqa: E402
from stockfish_core.storage import SQLiteStore  # noqa: E402
from stockfish_core.yaogu.config import StrategyConfig  # noqa: E402
from stockfish_core.yaogu.contracts import MarketInput  # noqa: E402
from stockfish_core.yaogu.history_artifacts import bind_history  # noqa: E402


def _latest_input_id(store):
    candidates = []
    offset = 0
    while True:
        rows = store.list_document_metadata("candidate_inputs", limit=1000, offset=offset, descending=True)
        for row in rows:
            index = store.get_document("candidate_input_indexes", row["document_id"])
            if index is not None:
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
        return max(receipt_times).isoformat()
    from datetime import time
    from zoneinfo import ZoneInfo

    return datetime.combine(bundle.observation_date, time(20, 30), ZoneInfo("Asia/Shanghai")).isoformat()


def write_markdown(path, result):
    trace = result.get("e2e_trace") or {}
    lines = [
        "# R16 Actual Daily Primary Service Trace",
        "",
        f"- generated_at: `{datetime.now().astimezone().isoformat()}`",
        f"- input_id: `{result.get('input_id')}`",
        f"- run_id: `{result.get('run_id')}`",
        f"- observation_date: `{result.get('observation_date')}`",
        f"- decision_cutoff: `{result.get('decision_cutoff')}`",
        f"- trace_status: `{trace.get('status')}`",
        f"- elapsed_ms: `{trace.get('elapsed_ms')}`",
        "",
        "## Stages",
        "",
        "| Stage | elapsed_ms | success | item_count |",
        "|---|---:|---|---:|",
    ]
    for stage in trace.get("stages") or []:
        lines.append(
            f"| {stage.get('name')} | {stage.get('elapsed_ms')} | {stage.get('success')} | {stage.get('item_count')} |"
        )
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def run(db_path, output_json, output_md, *, input_id=None, cutoff=None):
    source_store = SQLiteStore(db_path)
    resolved_input_id = input_id
    if resolved_input_id is None:
        resolved_input_id = _latest_input_id(source_store)
    bundle = _load_bundle(source_store, resolved_input_id)
    config = _current_config(source_store)
    decision_cutoff = datetime.fromisoformat(cutoff or _default_cutoff(bundle))
    with tempfile.TemporaryDirectory(prefix="r16_service_trace_") as tmp:
        service = ApplicationService(
            root=Path(tmp) / "state",
            store=SQLiteStore(Path(tmp) / "service.sqlite3"),
            allow_test_fixture=True,
            data_dir=Path(tmp) / "no_model",
        )
        try:
            result = service.candidates._build_daily_primary_strategy_result(
                resolved_input_id,
                bundle,
                decision_cutoff,
                config,
                market_session="R16_SERVICE_TRACE",
                validation_role="POST_COMMIT_EVIDENCE",
                primary_signal_cutoff="INPUT_RECEIPT_WATERMARK_OR_EXPLICIT_CUTOFF",
            )
        finally:
            service.close()
    output_json = Path(output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    if output_md:
        output_md = Path(output_md)
        output_md.parent.mkdir(parents=True, exist_ok=True)
        write_markdown(output_md, result)
    return {
        "input_id": resolved_input_id,
        "run_id": result.get("run_id"),
        "observation_date": result.get("observation_date"),
        "decision_cutoff": result.get("decision_cutoff"),
        "trace_elapsed_ms": (result.get("e2e_trace") or {}).get("elapsed_ms"),
        "trace_stage_count": len((result.get("e2e_trace") or {}).get("stages") or []),
        "status": "SUCCEEDED",
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "data" / "stockfish.sqlite3"))
    parser.add_argument("--input-id", default=None)
    parser.add_argument("--cutoff", default=None)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", default=None)
    args = parser.parse_args(argv)
    print(json.dumps(run(args.db, args.output_json, args.output_md, input_id=args.input_id, cutoff=args.cutoff),
                     ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
