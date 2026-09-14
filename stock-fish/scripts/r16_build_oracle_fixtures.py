"""Explicit one-time fixture creation. Verification never rewrites golden files."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import argparse
import tempfile

from r16_production_oracle import (
    DEFAULT_FIXTURES, _clock, business_projection, freeze_source, golden_summary,
    load_case, production_facts, sam, write_json,
)
from tests.r05_product.test_stage_aware_momentum_v1 import _with_high_level_repair_target
from tests.r05_product.test_theme_capital_trend_v1 import trend_bundle


def generate(folder):
    folder = Path(folder)
    if (folder / "manifest.json").exists():
        raise FileExistsError("Frozen goldens exist; choose a new explicit output directory")
    bundle, cutoff = trend_bundle(sessions=65)
    base = bundle.model_dump(mode="json")
    wanted = {"TEST000", "TEST001"}
    base["bars"] = [row for row in base["bars"] if row["instrument_id"] in wanted]
    base["universe"]["securities"] = [row for row in base["universe"]["securities"] if row["instrument_id"] in wanted]
    base["universe"]["complete"] = False
    cases = []
    def add(case_id, market=None, **extra):
        case = {"case_id": case_id, "data_class": "SYNTHETIC_TEST_FIXTURE",
                "source_scope": "explicit subset of existing TEST_FIXTURE parent; original receipt metadata retained",
                "cutoff": cutoff.isoformat(), "market_input": deepcopy(market or base), **extra}
        cases.append(case)
        return case
    add("NORMAL")
    gap = add("TRADING_SESSION_GAP")
    gap["market_input"]["bars"] = [r for r in gap["market_input"]["bars"] if not
                                      (r["instrument_id"] == "TEST000" and r["session"] == base["sessions"][-2])]
    missing = add("MISSING_UNKNOWN")
    for row in missing["market_input"]["bars"]:
        if row["instrument_id"] == "TEST000" and row["session"] == base["sessions"][-1]:
            row.update(open=None, comparable_close=None)
    for name, future, materialized in (("SOURCE_REVISION", False, False), ("HISTORICAL_PIT_FUTURE_SOURCE", True, False),
                                       ("INLINE_HIGHER_REVISION", False, True)):
        case = add(name)
        original = next(r for r in case["market_input"]["bars"] if r["instrument_id"] == "TEST000" and r["session"] == base["sessions"][-2])
        revised = deepcopy(original)
        clock = cutoff + timedelta(hours=1) if future else _clock(original["ingested_at"]) + timedelta(hours=1)
        revised.update(close="30", high="31", comparable_close="30", comparable_high="31")
        for field in ("source_published_at", "first_seen_at", "ingested_at"): revised[field] = clock.isoformat()
        if materialized: case.update(materialize_history=True, inline_rows=[revised])
        else: case["market_input"]["bars"].append(revised)
    units = add("UNIT_AND_ROUNDING")
    targets = {base["sessions"][-3]: "10", base["sessions"][-2]: "10.2", base["sessions"][-1]: "10.3259139"}
    for row in units["market_input"]["bars"]:
        if row["instrument_id"] == "TEST000" and row["session"] in targets:
            row.update(comparable_close=targets[row["session"]])
    unsupported = add("BASIS_UNSPECIFIED_NEGATIVE", expected_unsupported_domain="NUMERIC_COMPARABLE_WITHOUT_BASIS")
    for row in unsupported["market_input"]["bars"]:
        if row["instrument_id"] == "TEST000" and row["session"] in base["sessions"][-2:]: row["comparable_basis"] = None
    offsets = add("CROSS_OFFSET_REVISION")
    original = next(r for r in offsets["market_input"]["bars"] if r["instrument_id"] == "TEST000" and r["session"] == base["sessions"][-1])
    revised = deepcopy(original)
    day = original["session"]
    revised.update(close="30", high="31", comparable_close="30", comparable_high="31")
    for field in ("source_published_at", "first_seen_at", "ingested_at"): revised[field] = day + "T08:00:00+00:00"
    offsets["market_input"]["bars"].append(revised)
    full, full_cutoff = _with_high_level_repair_target()
    business = full.model_dump(mode="json")
    # An actual numeric tie, consumed by the same setup rank implementation.
    reference = {r["session"]: r for r in business["bars"] if r["instrument_id"] == "TEST001"}
    for index, row in enumerate(business["bars"]):
        if row["instrument_id"] == "TEST005":
            business["bars"][index] = {**deepcopy(reference[row["session"]]), "instrument_id": "TEST005"}
    add("BUSINESS_SLICE_RANK_TIE", business, business_slice=True, cutoff=full_cutoff.isoformat())
    manifest = {"version": "R16_R1_REAL_INPUT_PRODUCTION_ORACLE_GOLDENS", "cases": []}
    for case in cases:
        case_id = case["case_id"]
        input_name, expected_name = case_id + ".input.json", case_id + ".expected.json"
        write_json(folder / input_name, case)
        with tempfile.TemporaryDirectory(prefix="r16_r1_golden_") as state:
            _, parsed, limit = load_case(folder / input_name, state_dir=state)
            context, sources, _ = freeze_source(parsed, limit)
            facts = production_facts(context, sources)
            expected = {"facts": golden_summary(facts)}
            if case.get("business_slice"):
                expected["business"] = business_projection(sam.run_stage_aware_momentum(parsed, cutoff=limit))
        write_json(folder / expected_name, expected)
        manifest["cases"].append({"case_id": case_id, "input": input_name, "expected": expected_name,
                                  "expected_exit_code": 0, "expected_status": "EXPECTED_MISMATCH_UNSUPPORTED_DOMAIN" if case.get("expected_unsupported_domain") else "PASS"})
        print(case_id, len(facts), flush=True)
    write_json(folder / "manifest.json", manifest)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    generate(parser.parse_args().output)
