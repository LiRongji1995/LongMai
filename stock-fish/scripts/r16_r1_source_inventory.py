"""Read-only R16-R1 Gate C source inventory; does not build any market input."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

T = "market_63f83798f5098898debce30e0810a0c54f6a0daffdc75ac6404d47d5f8488a23"
TP1 = "market_04d95df6f6f699c7f88353d181308159526865877a666a7c3efb84be01b9c08d"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--budget-seconds", type=float, default=120)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    output = Path(args.output).resolve()
    if output.exists():
        raise ValueError("INVENTORY_OUTPUT_MUST_NOT_EXIST")
    output.mkdir(parents=True)
    sys.path.insert(0, str(root / "stock-fish"))
    from stockfish_core.yaogu.contracts import digest

    start = time.monotonic()
    started = datetime.now(timezone.utc).isoformat()
    last_emit = start
    checkpoint = output / "checkpoint.json"

    def progress(stage, **extra):
        value = {"stage": stage, "elapsed_seconds": time.monotonic() - start, **extra}
        print(json.dumps(value, ensure_ascii=False), flush=True)
        checkpoint.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    db = root / "stock-fish/data/stockfish.sqlite3"
    source = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    source.execute("PRAGMA query_only=ON")
    inputs = {}
    for input_id in (T, TP1):
        row = source.execute("SELECT * FROM documents WHERE namespace='candidate_inputs' AND document_id=?", (input_id,)).fetchone()
        raw = row["payload"].encode("utf-8")
        checksum = hashlib.sha256(raw).hexdigest()
        if checksum != row["content_hash"] or input_id != "market_" + checksum:
            raise ValueError("INPUT_CONTENT_ADDRESS_MISMATCH")
        value = json.loads(raw)
        inputs[input_id] = value
        (output / (input_id + ".source-metadata.json")).write_text(json.dumps({
            "input_id": input_id, "source_db": str(db), "content_hash": checksum,
            "payload_bytes": len(raw), "created_at": row["created_at"],
            "observation_date": value["observation_date"], "coverage_type": value["coverage_type"],
            "history_artifact_ref": value.get("history_artifact_ref"), "history_artifact_hash": value.get("history_artifact_hash"),
            "receipt_count": len(value["receipts"]),
            "actual_first_capture": min(x["ingested_at"] for x in value["receipts"]),
            "actual_last_capture": max(x["ingested_at"] for x in value["receipts"]),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    source.close()
    old_receipts = {r["receipt_id"]: r for r in inputs[T]["receipts"]}
    new_receipts = {r["receipt_id"]: r for r in inputs[TP1]["receipts"]}
    wanted = {**old_receipts, **new_receipts}
    if any(old_receipts[rid] != new_receipts[rid] for rid in old_receipts.keys() & new_receipts.keys()):
        raise ValueError("REUSED_RECEIPT_METADATA_CONFLICT")
    candidates = {}
    queues = [root / "stock-fish/data/core/yaogu/provider_cache", root / "stock-fish/data/codex-pv-cache"]
    for queue_root in queues:
        queue_db = queue_root / "request-queue.sqlite3"
        if not queue_db.exists():
            continue
        con = sqlite3.connect(queue_db.as_uri() + "?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        for table in ("requests", "attempts"):
            for row in con.execute(f"SELECT request_id,result_path,result_hash FROM {table} WHERE result_path IS NOT NULL"):
                path = (queue_root / row["result_path"]).resolve()
                if not path.is_relative_to(queue_root.resolve()):
                    raise ValueError("CACHE_PATH_ESCAPES_QUEUE_ROOT")
                entry = candidates.setdefault(path, {"expected_result_hashes": set(), "request_ids": set(), "kind": "REGISTERED_QUEUE_RESULT"})
                entry["expected_result_hashes"].add(row["result_hash"])
                entry["request_ids"].add(row["request_id"])
        con.close()
    # Explicitly known original premarket repair sources were saved outside the queue.
    for folder in ("core-refresh", "core-refresh-2026-09-04"):
        for endpoint in ("market_daily", "master_all"):
            path = root / "reports/codex/2026-09-09/premarket" / folder / (endpoint + "-1.json")
            if path.exists():
                candidates[path] = {"expected_result_hashes": set(), "request_ids": set(), "kind": "ORIGINAL_CAPTURE_RESULT_FILE"}
    found = {}
    errors = []
    scanned_bytes = 0
    progress("SCAN_START", candidate_files=len(candidates), wanted_receipts=len(wanted))
    for index, (path, provenance) in enumerate(sorted(candidates.items()), 1):
        if time.monotonic() - start > args.budget_seconds:
            progress("TIMED_OUT", processed=index - 1, total=len(candidates), matched_receipts=len(found))
            raise TimeoutError("BOUNDED_ASSET_INVENTORY_BUDGET_EXCEEDED")
        try:
            raw = path.read_bytes()
            scanned_bytes += len(raw)
            value = json.loads(raw)
            rid = value.get("request_receipt_id")
            if rid in wanted:
                expected = wanted[rid]
                result_hash = digest(value)
                receipts = [r for r in value.get("receipts", ()) if r.get("receipt_id") == rid]
                if len(receipts) != 1:
                    raise ValueError("UNIQUE_AUTHORITATIVE_SOURCE_RECEIPT_REQUIRED")
                receipt = receipts[0]
                if receipt.get("content_hash") != expected["content_hash"]:
                    raise ValueError("SOURCE_CONTENT_HASH_MISMATCH")
                if value.get("endpoint") != expected["dataset"] or value.get("provider") != expected["provider"]:
                    raise ValueError("SOURCE_PROVIDER_OR_DATASET_MISMATCH")
                if provenance["expected_result_hashes"] and result_hash not in provenance["expected_result_hashes"]:
                    raise ValueError("REGISTERED_RESULT_HASH_MISMATCH")
                entry = {
                    "path": str(path), "relative_path": str(path.relative_to(root)),
                    "file_sha256": hashlib.sha256(raw).hexdigest(), "file_bytes": len(raw),
                    "result_canonical_sha256": result_hash, "request_ids": sorted(provenance["request_ids"]),
                    "source_kind": provenance["kind"], "receipt_id": rid,
                    "source_content_hash": receipt["content_hash"], "provider": value["provider"],
                    "endpoint": value["endpoint"], "parameters": value.get("parameters", {}),
                    "field_version": value.get("field_version"), "status": value.get("status"),
                    "pagination_complete": value.get("pagination_complete"),
                    "actual_ingested_at": receipt.get("ingested_at"), "row_count": len(value.get("rows", ())),
                    "in_frozen_t": rid in old_receipts, "in_frozen_t_plus_1": rid in new_receipts,
                    "clock_match": datetime.fromisoformat(receipt["ingested_at"]) == datetime.fromisoformat(expected["ingested_at"]),
                }
                if not entry["clock_match"]:
                    raise ValueError("SOURCE_CAPTURE_CLOCK_MISMATCH")
                found.setdefault(rid, []).append(entry)
        except (OSError, ValueError, KeyError) as exc:
            errors.append({"path": str(path), "error": type(exc).__name__ + ": " + str(exc)})
        if time.monotonic() - last_emit > 10:
            progress("SCAN_PROGRESS", processed=index, total=len(candidates), matched_receipts=len(found), scanned_bytes=scanned_bytes)
            last_emit = time.monotonic()

    delta = []
    old_raw_sessions = {r.get("parameters", {}).get("date"): r for r in old_receipts.values() if r["dataset"] == "market_daily"}
    for rid, receipt in new_receipts.items():
        if receipt["dataset"] != "market_daily" or rid in old_receipts:
            continue
        session = receipt.get("parameters", {}).get("date")
        previous = old_raw_sessions.get(session)
        classification = ("FAILED_CAPTURE_NO_OBSERVATION_NOT_EMPTY_NOT_RETRACTION" if receipt['status'] not in {'COMPLETE','PARTIAL'} else
                          "APPENDED_NEW_SESSION" if session > inputs[T]["observation_date"] else "EXISTING_SESSION_NEW_CAPTURE_REQUIRES_VALUE_AUDIT" if previous else "HISTORICAL_SOURCE_ADDITION_REQUIRES_PIT_AUDIT")
        delta.append({"session": session, "classification": classification, "receipt_id": rid,
                      "previous_receipt_id": previous["receipt_id"] if previous else None,
                      "same_source_content_hash": previous["content_hash"] == receipt["content_hash"] if previous else None,
                      "source_receipt": receipt, "matched_assets": found.get(rid, [])})
    delta.sort(key=lambda item: item["session"])
    output_value = {
        "inventory_version": "R16_R1_READ_ONLY_RAW_ASSET_INVENTORY_V1",
        "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": time.monotonic() - start, "budget_seconds": args.budget_seconds,
        "input_t": T, "input_t_plus_1": TP1, "source_db_access": "SQLITE_URI_MODE_RO_QUERY_ONLY",
        "git_commit_at_inventory": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "git_dirty_at_inventory": bool(subprocess.check_output(["git", "status", "--short"], cwd=root, text=True).strip()),
        "scope": "READ_ONLY_SOURCE_BINDING_ONLY_NO_HISTORY_AUDIT_OR_BUILD",
        "candidate_file_count": len(candidates), "scanned_bytes": scanned_bytes,
        "wanted_receipt_count": len(wanted), "matched_receipt_count": len(found),
        "missing_receipts": [{"receipt_id": rid, "endpoint": r["dataset"], "parameters": r.get("parameters")} for rid, r in wanted.items() if rid not in found],
        "read_errors": errors, "asset_bindings": found,
        "shared_market_daily_receipt_count": sum(rid in old_receipts and r["dataset"] == "market_daily" for rid, r in new_receipts.items()),
        "delta_market_daily_count": len(delta), "delta_classification_counts": dict(Counter(item["classification"] for item in delta)),
        "factor_receipt_ids_from_t": [rid for rid, r in old_receipts.items() if r["dataset"] == "adjustment_factors"],
        "factor_receipt_ids_from_t_plus_1": [rid for rid, r in new_receipts.items() if r["dataset"] == "adjustment_factors"],
        "market_edge": "NOT_ESTABLISHED",
    }
    (output / "raw_asset_inventory.json").write_text(json.dumps(output_value, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "delta_33_manifest.json").write_text(json.dumps(delta, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# R16-R1 Gate C raw asset inventory", "", "Read-only preparation; source revisions and PIT are not yet accepted as valid.", "",
             "| Session | Classification | Original receipt | Matched files |", "|---|---|---|---:|"]
    lines.extend(f"| {d['session']} | {d['classification']} | {d['receipt_id']} | {len(d['matched_assets'])} |" for d in delta)
    (output / "DELTA_33_MANIFEST.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    progress("DONE", processed=len(candidates), total=len(candidates), matched_receipts=len(found), missing_receipts=len(output_value["missing_receipts"]), delta_count=len(delta), read_error_count=len(errors))
    if any(not item["matched_assets"] for item in delta):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
