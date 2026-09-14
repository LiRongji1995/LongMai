"""Read-only, bounded extraction of real source rows for a private oracle fixture."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

from r16_production_oracle import _clock, digest, write_json


def extract(db, input_id, cutoff, output, *, instrument_ids, session_count=60):
    output = Path(output)
    if output.exists(): raise FileExistsError("Real fixture is immutable; choose a new output directory")
    if not 1 <= session_count <= 280 or not 1 <= len(instrument_ids) <= 8:
        raise ValueError("Bounded fixture only: 1..280 sessions, 1..8 instruments")
    _clock(cutoff)
    con = sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        raw, input_hash = con.execute("SELECT payload,content_hash FROM documents WHERE namespace=? AND document_id=?",
                                     ("candidate_inputs", input_id)).fetchone()
        if hashlib.sha256(raw.encode()).hexdigest() != input_hash or input_id != "market_" + input_hash:
            raise ValueError("SOURCE_INPUT_CHECKSUM_MISMATCH")
        payload = json.loads(raw)
        ref, expected = payload.get("history_artifact_ref"), payload.get("history_artifact_hash")
        manifest = json.loads(con.execute("SELECT payload FROM documents WHERE namespace=? AND document_id=?",
                                         ("history_artifact_manifests", ref)).fetchone()[0]) if ref else None
        if manifest and (digest(manifest) != expected or ref != "history_" + expected):
            raise ValueError("SOURCE_HISTORY_MANIFEST_CHECKSUM_MISMATCH")
        days = [day for day in payload["sessions"] if day <= payload["observation_date"]][-session_count:]
        selected = {digest(row): row for row in payload["bars"] if row["instrument_id"] in instrument_ids and row["session"] in days}
        raw_history_rows = []
        if manifest:
            partitions = [part["partition_hash"] for part in manifest["partitions"] if part["session"] in days]
            pp, pi = ",".join("?" for _ in partitions), ",".join("?" for _ in instrument_ids)
            records = con.execute("SELECT DISTINCT r.row_hash,r.payload FROM history_partition_members m "
                "JOIN history_evidence_rows r ON r.row_hash=m.row_hash "
                f"WHERE m.partition_hash IN ({pp}) AND m.kind='bar' AND m.instrument_id IN ({pi})",
                (*partitions, *instrument_ids)).fetchall()
            for row_hash, text in records:
                if hashlib.sha256(text.encode()).hexdigest() != row_hash: raise ValueError("HISTORY_ROW_CHECKSUM_MISMATCH")
                selected[row_hash] = json.loads(text)
                raw_history_rows.append((row_hash, text))
        original_universe = payload["universe"]
        original_expected = original_universe["expected_count"]
        original_universe["securities"] = [row for row in original_universe["securities"] if row["instrument_id"] in instrument_ids]
        receipt_map = {r["receipt_id"]: r for r in (manifest or {}).get("receipts", [])}
        for receipt in payload["receipts"]:
            previous = receipt_map.get(receipt["receipt_id"])
            if previous is not None and previous != receipt: raise ValueError("HISTORY_RECEIPT_IDENTITY_CONFLICT")
            receipt_map[receipt["receipt_id"]] = receipt
        payload.update(bars=list(selected.values()), receipts=list(receipt_map.values()),
                       history_artifact_ref=None, history_artifact_hash=None)
        # Keep original calendar, qualification clocks and denominator. A subset
        # cannot become a full market universe or a new prospective capture.
        fixture = {"case_id": "REAL_SOURCE_EXTRACT", "data_class": "REAL_SOURCE_EXTRACT",
                   "cutoff": cutoff, "market_input": payload,
                   "extraction": {"source_db": str(Path(db).resolve()), "original_input_id": input_id,
                     "original_input_sha256": input_hash, "original_history_ref": ref,
                     "original_history_manifest_sha256": expected, "instrument_ids": instrument_ids,
                     "sessions": days, "original_universe_expected_count": original_expected,
                     "raw_candidate_row_hashes": sorted(selected), "selection": "PIT resolver runs after extraction",
                     "not_official_capture": True, "scope": "bounded private source subset; no historical clock rewriting"}}
        output.mkdir(parents=True)
        write_json(output / "real_source.input.json", fixture)
        write_json(output / "original_history_manifest.json", manifest)
        with (output / "original_history_rows.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
            for _, text in raw_history_rows: stream.write(text + "\n")
        write_json(output / "extraction.json", fixture["extraction"])
        return fixture["extraction"]
    finally:
        con.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--input-id", required=True)
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--instrument", action="append", required=True)
    parser.add_argument("--sessions", type=int, default=60)
    args = parser.parse_args()
    print(json.dumps(extract(args.db, args.input_id, args.cutoff, args.output,
                             instrument_ids=args.instrument, session_count=args.sessions), ensure_ascii=False))
