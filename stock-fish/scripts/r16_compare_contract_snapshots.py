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
    read_facts_jsonl,
    validate_facts,
    validate_manifest,
)


def _load_snapshot(path):
    path = Path(path)
    manifest_path = path / "feature_evidence_manifest_v1.json"
    facts_path = path / "feature_evidence_facts_v1.jsonl"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
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
        "comparison": compare_facts(left_facts, right_facts, numeric_tolerances={}, compare_provenance=True),
        "business_result_equivalence": {
            "status": "NOT_EVALUATED_BY_THIS_FILE_COMPARATOR",
            "policy": "Use Python Strategy result hash comparison for FOCUS/WATCH/REJECTED/UNKNOWN, setup, ranking and reasons.",
        },
        "market_edge": "NOT_ESTABLISHED",
    }
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--left", required=True)
    parser.add_argument("--right", required=True)
    parser.add_argument("--output-json", required=True)
    args = parser.parse_args(argv)
    result = compare(args.left, args.right)
    Path(args.output_json).write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({
        "matched": result["comparison"]["matched"],
        "mismatch_count": result["comparison"]["mismatch_count"],
        "left_hash": result["comparison"]["left_hash"],
        "right_hash": result["comparison"]["right_hash"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
