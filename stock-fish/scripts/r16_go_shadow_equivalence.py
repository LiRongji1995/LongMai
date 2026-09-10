"""Run the R16 Python-vs-Go shadow contract equivalence fixture.

The fixture starts from canonical-resolved source rows, not provider files or
SQLite internals.  It proves the Contract V1 toolchain can compare a future Go
fact core against a Python baseline with field-level mismatch detail.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import subprocess
import sys

STOCK_FISH_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = STOCK_FISH_ROOT.parent
sys.path.insert(0, str(STOCK_FISH_ROOT))

from stockfish_core.yaogu.feature_evidence_contract import (  # noqa: E402
    CONTRACT_VERSION,
    compare_facts,
    read_facts_jsonl,
    validate_facts,
)


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str, allow_nan=False)


def _source_rows():
    rows = []
    start = date(2026, 1, 2)
    previous_close = 10.0
    for index in range(60):
        session = (start + timedelta(days=index)).isoformat()
        close = round(10.0 + index * 0.2, 6)
        rows.append({
            "instrument": "R16FIX.SZ",
            "exchange": "SZSE",
            "session": session,
            "open": round(close - 0.05, 6),
            "high": round(close + 0.15, 6),
            "low": round(close - 0.20, 6),
            "close": close,
            "prev_close": previous_close,
            "comparable_close": close,
            "comparable_basis": "HFQ_FIXTURE_V1",
            "source_revision_id": f"fixture-rev-{session}",
        })
        previous_close = close
    return rows


def _fact(row, group, field, value, status):
    return {
        "contract_version": CONTRACT_VERSION,
        "instrument": row["instrument"],
        "exchange": row["exchange"],
        "session": row["session"],
        "eligibility": "ELIGIBLE",
        "eligibility_reason": None,
        "field_group": group,
        "field": field,
        "value": value,
        "value_json": _json(value),
        "status": status,
        "unit": None,
        "provenance": {
            "source": "GO_SHADOW_SLICE_R16",
            "source_revision_id": row["source_revision_id"],
            "comparable_basis": row["comparable_basis"],
        },
        "source_label": "PYTHON_SHADOW_BASELINE",
    }


def _python_shadow_facts(rows):
    rows = sorted(rows, key=lambda row: (row["instrument"], row["session"]))
    by_instrument = {}
    for row in rows:
        by_instrument.setdefault(row["instrument"], []).append(row)
    facts = []
    for row in rows:
        facts.extend([
            _fact(row, "PRICE", "open", row["open"], "KNOWN"),
            _fact(row, "PRICE", "high", row["high"], "KNOWN"),
            _fact(row, "PRICE", "low", row["low"], "KNOWN"),
            _fact(row, "PRICE", "close", row["close"], "KNOWN"),
            _fact(row, "PRICE", "prev_close", row["prev_close"], "KNOWN"),
            _fact(row, "PRICE", "comparable_price", row["comparable_close"], "KNOWN"),
            _fact(row, "PRICE", "price_basis", row["comparable_basis"], "KNOWN"),
        ])
        instrument_rows = by_instrument[row["instrument"]]
        position = next(index for index, candidate in enumerate(instrument_rows) if candidate["session"] == row["session"])
        if position > 0 and instrument_rows[position - 1]["comparable_close"] > 0 and row["comparable_basis"] == instrument_rows[position - 1]["comparable_basis"]:
            facts.append(_fact(row, "RETURN", "ret1_pct", row["comparable_close"] / instrument_rows[position - 1]["comparable_close"] - 1, "KNOWN"))
        else:
            facts.append(_fact(row, "RETURN", "ret1_pct", None, "UNKNOWN"))
        for window in (20, 60):
            field = f"MA{window}"
            if position - window + 1 < 0:
                facts.append(_fact(row, "ROLLING", field, None, "UNKNOWN"))
                continue
            window_rows = instrument_rows[position - window + 1 : position + 1]
            if all(candidate["comparable_basis"] == row["comparable_basis"] and candidate["comparable_close"] > 0 for candidate in window_rows):
                facts.append(_fact(row, "ROLLING", field, sum(candidate["comparable_close"] for candidate in window_rows) / window, "KNOWN"))
            else:
                facts.append(_fact(row, "ROLLING", field, None, "UNKNOWN"))
    return facts


def _write_jsonl(path, rows):
    with Path(path).open("w", encoding="utf-8", newline="\n") as fp:
        for row in rows:
            fp.write(_json(row) + "\n")


def write_markdown(path, comparison, command):
    lines = [
        "# R16 Python / Go Shadow Equivalence",
        "",
        f"- generated_at: `{datetime.now().astimezone().isoformat()}`",
        f"- command: `{command}`",
        f"- comparison_version: `{comparison.get('comparison_version')}`",
        f"- matched: `{comparison.get('matched')}`",
        f"- mismatch_count: `{comparison.get('mismatch_count')}`",
        f"- left_hash: `{comparison.get('left_hash')}`",
        f"- right_hash: `{comparison.get('right_hash')}`",
        "",
        "This fixture compares only the Phase 0 Go shadow slice: OHLC pass-through, ret1_pct, MA20 and MA60.",
    ]
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--go-binary-command", default="go")
    args = parser.parse_args(argv)

    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    source_rows = _source_rows()
    source_path = output / "resolved_source_rows_fixture.json"
    source_path.write_text(json.dumps(source_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    python_facts = _python_shadow_facts(source_rows)
    validation = validate_facts(python_facts)
    if not validation["valid"]:
        raise RuntimeError("python shadow facts failed validation: " + json.dumps(validation, ensure_ascii=False))
    python_path = output / "python_shadow_facts_v1.jsonl"
    go_path = output / "go_shadow_facts_v1.jsonl"
    _write_jsonl(python_path, python_facts)

    cmd = [
        args.go_binary_command,
        "run",
        "./cmd/stockfish-go-core",
        "--input",
        str(source_path),
        "--output",
        str(go_path),
    ]
    completed = subprocess.run(cmd, cwd=REPO_ROOT / "go-core", text=True, capture_output=True, check=False)
    (output / "go_shadow_command.log").write_text(
        "COMMAND " + " ".join(cmd) + "\n"
        + f"EXIT_CODE {completed.returncode}\n"
        + "STDOUT\n" + completed.stdout + "\nSTDERR\n" + completed.stderr,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise RuntimeError("go shadow command failed")
    go_facts = read_facts_jsonl(go_path)
    tolerances = {
        ("PRICE", "open"): 1e-12,
        ("PRICE", "high"): 1e-12,
        ("PRICE", "low"): 1e-12,
        ("PRICE", "close"): 1e-12,
        ("PRICE", "prev_close"): 1e-12,
        ("PRICE", "comparable_price"): 1e-12,
        ("RETURN", "ret1_pct"): 1e-12,
        ("ROLLING", "MA20"): 1e-12,
        ("ROLLING", "MA60"): 1e-12,
    }
    comparison = compare_facts(
        python_facts,
        go_facts,
        numeric_tolerances=tolerances,
        compare_provenance=True,
    )
    comparison["business_result_equivalence"] = {
        "status": "NOT_APPLICABLE_PHASE0_FACT_SLICE_ONLY",
        "reason": "Production Python strategy is not switched to Go; R16 validates fact-contract equivalence only.",
    }
    comparison_path = output / "python_go_shadow_comparison.json"
    comparison_path.write_text(json.dumps(comparison, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    write_markdown(output / "R16_PYTHON_GO_SHADOW_EQUIVALENCE.md", comparison, " ".join(cmd))
    print(json.dumps(comparison, ensure_ascii=False, indent=2, default=str))
    if not comparison["matched"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
