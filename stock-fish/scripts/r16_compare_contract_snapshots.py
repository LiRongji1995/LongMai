"""Compare two Feature/Evidence Contract V1 snapshots."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stockfish_core.yaogu.feature_evidence_contract import (  # noqa: E402
    compare_facts,
    _strict_json,
    compare_manifests,
    read_facts_jsonl,
    validate_facts,
    validate_manifest,
    validate_snapshot_binding,
)


def _load_snapshot(path):
    path = Path(path)
    manifest_path = path / "feature_evidence_manifest_v1.json"
    facts_path = path / "feature_evidence_facts_v1.jsonl"
    try:
        manifest = _strict_json(manifest_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"{manifest_path}: {exc}") from exc
    facts = read_facts_jsonl(facts_path)
    return manifest, facts


def compare(left, right):
    left_manifest, left_facts = _load_snapshot(left)
    right_manifest, right_facts = _load_snapshot(right)
    result = {
        "comparison_version": "R16_FEATURE_EVIDENCE_CONTRACT_SNAPSHOT_COMPARISON_V1",
        "left_manifest": validate_manifest(left_manifest),
        "right_manifest": validate_manifest(right_manifest),
        "left_facts": validate_facts(left_facts),
        "right_facts": validate_facts(right_facts),
        "left_binding": validate_snapshot_binding(left_manifest, left_facts),
        "right_binding": validate_snapshot_binding(right_manifest, right_facts),
        "manifest_comparison": compare_manifests(left_manifest, right_manifest),
        "comparison": compare_facts(left_facts, right_facts, numeric_tolerances={}, compare_provenance=True),
        "business_result_equivalence": {
            "status": "NOT_EVALUATED_BY_THIS_FILE_COMPARATOR",
            "policy": "Use Python Strategy result hash comparison for FOCUS/WATCH/REJECTED/UNKNOWN, setup, ranking and reasons.",
        },
        "market_edge": "NOT_ESTABLISHED",
    }
    result["matched"] = bool(all(result[name]["valid"] for name in ("left_manifest", "right_manifest", "left_facts", "right_facts", "left_binding", "right_binding"))
                             and result["manifest_comparison"]["matched"] and result["comparison"]["matched"])
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--left", required=True)
    parser.add_argument("--right", required=True)
    parser.add_argument("--output-json", required=True)
    args = parser.parse_args(argv)
    try:
        result = compare(args.left, args.right)
    except (ValueError, OSError) as exc:
        result = {"matched": False, "status": "INVALID_INPUT", "error_type": type(exc).__name__, "error": str(exc)}
    Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output_json).write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({
        "matched": result["matched"],
        "mismatch_count": result.get("comparison", {}).get("mismatch_count"),
        "left_hash": result.get("comparison", {}).get("left_hash"),
        "right_hash": result.get("comparison", {}).get("right_hash"),
        "error": result.get("error"),
    }, ensure_ascii=False, indent=2))
    if not result["matched"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
