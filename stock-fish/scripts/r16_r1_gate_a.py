"""Bounded, real-import Gate A diagnostics; all output is external to source."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()

    binding = {"commit": git("rev-parse", "HEAD"), "tree": git("rev-parse", "HEAD^{tree}"),
               "dirty": bool(git("status", "--porcelain", "--untracked-files=no")),
               "python": sys.version, "go": subprocess.check_output(["go", "version"], text=True).strip()}
    commands = [
        ("python_regressions", [sys.executable, "-m", "pytest", "tests/r05_product/test_r16_r1_contract_regressions.py", "tests/r05_product/test_r16_go_migration_phase0.py", "-q"], ROOT / "stock-fish", 0),
        ("go_test", ["go", "test", "./..."], ROOT / "go-core", 0),
        ("go_vet", ["go", "vet", "./..."], ROOT / "go-core", 0),
    ]
    inputs = {}
    for name in ("missing_fields", "null_fields"):
        source = ROOT / "stock-fish/tests/fixtures/r16_r1_contract" / (name + "_input.json")
        inputs[str(source.relative_to(ROOT))] = hashlib.sha256(source.read_bytes()).hexdigest()
        commands.append(("go_" + name, ["go", "run", "./cmd/stockfish-go-core", "--input", str(source), "--output", str(output / (name + ".jsonl"))], ROOT / "go-core", 0))
    duplicate = ROOT / "stock-fish/tests/fixtures/r16_r1_contract/duplicate_members_input.json"
    inputs[str(duplicate.relative_to(ROOT))] = hashlib.sha256(duplicate.read_bytes()).hexdigest()
    commands.append(("go_duplicate_members", ["go", "run", "./cmd/stockfish-go-core", "--input", str(duplicate),
                                              "--output", str(output / "duplicate_members.jsonl")], ROOT / "go-core", 1))
    results = []
    for name, command, cwd, expected in commands:
        start = datetime.now(timezone.utc).isoformat()
        completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=180)
        (output / (name + ".stdout.log")).write_text(completed.stdout, encoding="utf-8")
        (output / (name + ".stderr.log")).write_text(completed.stderr, encoding="utf-8")
        result = {**binding, "command": command, "cwd": str(cwd), "start": start,
                  "end": datetime.now(timezone.utc).isoformat(), "exit_code": completed.returncode,
                  "expected_exit_code": expected, "input_hashes": inputs}
        results.append(result)
        print(name, completed.returncode, flush=True)
        if name == "go_duplicate_members" and "duplicate JSON object member" not in completed.stderr:
            raise AssertionError("DUPLICATE_MEMBER_NOT_REJECTED_FOR_EXPECTED_REASON")
    # A successful parser may retain legal missing inputs; their facts must be null,
    # UNKNOWN, never zero+KNOWN. Invalid numerics are covered by real Go tests.
    for name in ("missing_fields", "null_fields"):
        rows = [json.loads(line) for line in (output / (name + ".jsonl")).read_text().splitlines()]
        for row in rows:
            if row["field"] in {"open", "high", "low", "close", "prev_close", "comparable_price"}:
                assert row["value"] is None and row["status"] == "UNKNOWN", row
    (output / "commands.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    if any(row["exit_code"] != row["expected_exit_code"] for row in results):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
