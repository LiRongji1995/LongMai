"""Validate a FEATURE_EVIDENCE_CONTRACT_V1 snapshot against the JSON schema."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))

from stockfish_core.yaogu.feature_evidence_contract import read_facts_jsonl, validate_facts, validate_manifest  # noqa: E402


def validate(snapshot_dir, schema_path=None):
    snapshot_dir = Path(snapshot_dir)
    manifest = json.loads((snapshot_dir / "feature_evidence_manifest_v1.json").read_text(encoding="utf-8"))
    facts = read_facts_jsonl(snapshot_dir / "feature_evidence_facts_v1.jsonl")
    result = {
        "validation_version": "R16_CONTRACT_SCHEMA_VALIDATION_V1",
        "snapshot_dir": str(snapshot_dir.resolve()),
        "helper_manifest": validate_manifest(manifest),
        "helper_facts": validate_facts(facts),
        "json_schema": {"valid": None, "status": "NOT_RUN"},
    }
    schema_path = Path(schema_path or REPO_ROOT / "stock-fish" / "schemas" / "feature_evidence_contract_v1.schema.json")
    envelope = {"manifest": manifest, "facts": facts}
    try:
        import jsonschema

        jsonschema.Draft202012Validator.check_schema(json.loads(schema_path.read_text(encoding="utf-8")))
        jsonschema.validate(envelope, json.loads(schema_path.read_text(encoding="utf-8")))
        result["json_schema"] = {"valid": True, "schema_path": str(schema_path.resolve())}
    except Exception as exc:
        result["json_schema"] = {"valid": False, "schema_path": str(schema_path.resolve()), "error": type(exc).__name__, "message": str(exc)}
    result["valid"] = bool(result["helper_manifest"]["valid"] and result["helper_facts"]["valid"] and result["json_schema"]["valid"])
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-dir", required=True)
    parser.add_argument("--schema", default=None)
    parser.add_argument("--output-json", required=True)
    args = parser.parse_args(argv)
    result = validate(args.snapshot_dir, args.schema)
    Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output_json).write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if not result["valid"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
