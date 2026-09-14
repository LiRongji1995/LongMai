"""R16-R1 test-only production oracle and exact fact injection. No production switch."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))
# This order respects the existing modules' initialization dependency.
from stockfish_core.yaogu import strategy_experiments as se
from stockfish_core.yaogu import stage_aware_momentum as sam
from stockfish_core.yaogu import theme_capital_trend as tct
from stockfish_core.yaogu.contracts import MarketInput, digest
from stockfish_core.yaogu.feature_evidence_contract import (
    CONTRACT_VERSION, SCHEMA_REVISION, compare_facts, exact_fraction,
    read_facts_jsonl, snapshot_hash, validate_facts, _verified_provenance,
    manifest_from_run, validate_manifest, validate_snapshot_binding, compare_manifests,
)
from stockfish_core.yaogu.history_artifacts import daily_bar_payload, history_receipts
from stockfish_core.yaogu.source_resolution import canonical_price_source_view

COVERAGE_FIELDS = ("open", "high", "low", "close", "prev_close", "comparable_price",
                   "price_basis", "ret1_pct", "MA20", "MA60")
RAW_FIELDS = {"open": "open", "high": "high", "low": "low", "close": "close",
              "prev_close": "reference_prev_close", "comparable_price": "comparable_close",
              "price_basis": "comparable_basis"}
BASE_COMMIT = "e2855d695f8818bb13103997e2ba1f1552538d99"
DEFAULT_FIXTURES = ROOT / "tests/fixtures/r16_r1_equivalence"


def json_text(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str, allow_nan=False)


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str, allow_nan=False), encoding="utf-8")


def write_jsonl(path, rows):
    with Path(path).open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows: stream.write(json_text(row) + "\n")


def provenance_identity():
    from r16_r1_supervise import source_metadata
    identity = source_metadata(REPO)
    paths = [Path(se.__file__), Path(tct.__file__), Path(sam.__file__)]
    return {"commit": identity['commit'], "tree": identity['tree'],
            "dirty": identity['tracked_dirty'], "git_root_matches": identity['git_root_matches'],
            "production_oracle_baseline_commit": BASE_COMMIT,
            "production_files": {str(p.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
            "functions": ["strategy_experiments._prepare_context", "strategy_experiments._bar_decimal",
                          "theme_capital_trend._window_return_uncached", "theme_capital_trend._ma_value_uncached",
                          "stage_aware_momentum._pct", "strategy_experiments._number"],
            "python": sys.version, "command": sys.argv}


def _clock(value):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def freeze_source(bundle, cutoff):
    """Resolve once, retaining full tokens and parent receipts without computation."""
    ctx = se._prepare_context(bundle, cutoff)
    view = canonical_price_source_view(ctx, ctx.sessions, ctx.eligible_ids)
    tokens = [view["tokens"][key] for key in sorted(view["tokens"])]
    receipts = [row.model_dump(mode="json") for row in history_receipts(bundle)]
    manifest = {"source_mode": "CANONICAL_RESOLVED_PRICE_SOURCE_VIEW", "resolver_version": view["resolver_version"],
                "tokens": tokens, "receipts": receipts, "calendar_sessions": ctx.sessions,
                "calendar_revision": digest({"MarketInput.sessions": ctx.sessions}),
                "calendar_authority": "ACTUAL_MARKET_INPUT_SESSION_CONTENT", "decision_cutoff": ctx.cutoff.isoformat(),
                "coverage_type": bundle.coverage_type, "rejected_evidence": ctx.rejected_evidence,
                "feature_spec": {"ret1_pct": "production fraction * 100; HALF_EVEN 4 places", "PRICE": "production HALF_EVEN 8 places",
                                 "MA20_MA60": "production exchange-session windows; HALF_EVEN 6 places",
                                 "exact_value": "reduced rational in declared contract unit"}}
    manifest_hash = digest(manifest)
    receipt_map = {row["receipt_id"]: row for row in receipts}
    dependency_metadata = {}
    for key, token in view["tokens"].items():
        if token["status"] != "SELECTED": continue
        refs = token["provenance"]["source_refs"]
        clocks = list(token["source_revision"].values())
        for ref in refs:
            clocks.extend(receipt_map[ref][name] for name in ("source_published_at", "first_seen_at", "ingested_at"))
        dependency_metadata[key] = (refs, max(map(_clock, clocks)))
    source_rows, selected_bars = [], {}
    for (iid, session), selected in sorted(view["selected"].items()):
        token = view["tokens"][(iid, session)]
        full = daily_bar_payload({**token["values"], "instrument_id": iid, "session": session})
        selected_bars.setdefault(session, {})[iid] = full
        index = ctx.sessions.index(session)
        field_provenance = {}
        for field in COVERAGE_FIELDS:
            lookback = 1 if field == "ret1_pct" else int(field[2:]) - 1 if field.startswith("MA") else 0
            sessions = ctx.sessions[max(0, index - lookback):index + 1]
            dependencies = [{"session": day, "token_hash": digest(view["tokens"][(iid, day)]),
                             "status": view["tokens"][(iid, day)]["status"]} for day in sessions]
            parents = [dependency_metadata[(iid, day)] for day in sessions if (iid, day) in dependency_metadata]
            refs = sorted({ref for parent_refs, _ in parents for ref in parent_refs})
            visible = max(clock for _, clock in parents)
            field_provenance[field] = {"status": "KNOWN", "pit_status": "VISIBLE", "source_refs": refs,
                "source_revision_id": token["row_hash"], "source_manifest_hash": manifest_hash,
                "visible_at": visible.isoformat(), "decision_cutoff": ctx.cutoff.isoformat(),
                "calendar_revision": manifest["calendar_revision"], "window_sessions": sessions, "dependencies": dependencies,
                "dependency_completeness": "COMPLETE" if len(parents) == len(sessions) else "GAPS"}
        source_rows.append({"instrument": iid, "exchange": ctx.securities[iid].exchange, "session": session,
            "open": full.get("open"), "high": full.get("high"), "low": full.get("low"), "close": full.get("close"),
            "prev_close": full.get("reference_prev_close"), "comparable_close": full.get("comparable_close"),
            "comparable_basis": full.get("comparable_basis") or "", "source_revision_id": token["row_hash"],
            "calendar_sessions": ctx.sessions, "field_provenance": field_provenance})
    return replace(ctx, bars_by_session=selected_bars, scalar_cache={}), source_rows, manifest


def _fact(row, field, value, reason=None):
    group = "RETURN" if field == "ret1_pct" else "ROLLING" if field.startswith("MA") else "PRICE"
    exact = None if value is None or isinstance(value, str) else Fraction(value)
    displayed = value if exact is None else se._number(exact, places=4 if group == "RETURN" else 6 if group == "ROLLING" else 8)
    provenance = row["field_provenance"][field]
    status = "UNKNOWN" if value is None else "KNOWN" if _verified_provenance(provenance) else "UNVERIFIED"
    return {"contract_version": CONTRACT_VERSION, "schema_revision": SCHEMA_REVISION, "instrument": row["instrument"],
        "exchange": row["exchange"], "session": row["session"], "eligibility": "ELIGIBLE", "eligibility_reason": None,
        "field_group": group, "field": field, "value": displayed, "value_json": json_text(displayed),
        "exact_value": {"numerator": str(exact.numerator), "denominator": str(exact.denominator)} if exact is not None else None,
        "status": status, "reason": None if status == "KNOWN" else reason or "SOURCE_PROVENANCE_NOT_VERIFIED",
        "unit": None if field == "price_basis" else "percent" if group == "RETURN" else "CNY",
        "provenance": provenance, "source_label": "ORIGINAL_PRODUCTION_PYTHON_ORACLE"}


def production_facts(ctx, source_rows):
    """Call original production algorithms on the same frozen source consumed by Go."""
    facts = []
    for row in source_rows:
        iid, session = row["instrument"], row["session"]
        index, bar = ctx.sessions.index(session), se._bar(ctx, session, iid)
        for field, original in RAW_FIELDS.items():
            value = bar.get(original) if field == "price_basis" else se._bar_decimal(bar, original)
            facts.append(_fact(row, field, value, "SOURCE_VALUE_MISSING"))
        ret = tct._window_return_uncached(ctx, iid, index, 1)
        previous = se._bar(ctx, ctx.sessions[index - 1], iid) if index else None
        reason = "RETURN_WINDOW_UNAVAILABLE" if index == 0 else "MISSING_COMPARABLE_PRICE"
        if previous and bar.get("comparable_close") is not None and previous.get("comparable_close") is not None:
            reason = "COMPARABLE_BASIS_MISMATCH"
        facts.append(_fact(row, "ret1_pct", ret * 100 if ret is not None else None, reason))
        for window in (20, 60):
            value, reason = tct._ma_value_uncached(ctx, iid, index, window)
            facts.append(_fact(row, f"MA{window}", value, reason))
    return facts


class RecordingDict(dict):
    def __init__(self):
        super().__init__()
        self.reads, self.keys_read = Counter(), set()

    def __getitem__(self, key):
        if isinstance(key, tuple) and key[0] in {"BAR", "WINDOW_RETURN", "MA_VALUE"}:
            self.reads[key[0]] += 1
            self.keys_read.add(key)
        return super().__getitem__(key)


def validate_injection(facts, expected_keys):
    validation = validate_facts(facts)
    if not validation["valid"]: raise ValueError("INVALID_INJECTED_FACTS: " + json_text(validation))
    if {(r["instrument"], r["session"], r["field"]) for r in facts} != expected_keys:
        raise ValueError("INJECTION_COVERAGE_MISMATCH")
    grouped = {}
    for row in facts:
        grouped.setdefault((row["instrument"], row["session"]), {})[row["field"]] = row
        if row["status"] == "UNVERIFIED" or (row["value"] is not None and not _verified_provenance(row["provenance"])):
            raise ValueError("UNVERIFIED_INJECTION_PROVENANCE")
        if row["value"] is not None and row["field"] != "price_basis": exact_fraction(row)
    for values in grouped.values():
        if values["comparable_price"]["value"] is not None and not values["price_basis"]["value"]:
            raise ValueError("OUTSIDE_VERIFIED_GO_SLICE_COMPARABLE_BASIS_UNSPECIFIED")
    return grouped


def strategy_with_facts(bundle, cutoff, facts, *, expected_keys):
    """TEST ONLY: replace current slice; all remaining baseline facts stay intact."""
    grouped = validate_injection(facts, expected_keys)
    original = se._prepare_context(bundle, cutoff)
    bars = {session: {iid: deepcopy(se._bar(original, session, iid)) for iid in original.eligible_ids
                      if se._bar(original, session, iid) is not None} for session in original.sessions}
    cache = RecordingDict()
    ctx = replace(original, bars_by_session=bars, scalar_cache=cache)
    for (iid, session), values in grouped.items():
        current = bars.get(session, {}).get(iid)
        if current is None: raise ValueError("INJECTED_SOURCE_ROW_NOT_IN_PRODUCTION_CONTEXT")
        for field, original_field in RAW_FIELDS.items():
            fact = values[field]
            current[original_field] = fact["value"] if field == "price_basis" else (
                se._as_decimal(exact_fraction(fact)) if fact["value"] is not None else None)
        index, ret = ctx.sessions.index(session), values["ret1_pct"]
        cache[("WINDOW_RETURN", iid, index, 1)] = exact_fraction(ret) / 100 if ret["value"] is not None else None
        for window in (20, 60):
            fact = values[f"MA{window}"]
            cache[("MA_VALUE", iid, index, window)] = (exact_fraction(fact) if fact["value"] is not None else None, fact["reason"])
    result = sam.run_stage_aware_momentum(bundle, cutoff=cutoff, _context=ctx)
    return result, {"counts": dict(cache.reads), "keys": [list(k) for k in sorted(cache.keys_read, key=str)]}


def business_projection(result):
    fields = ("instrument_id", "decision_status", "primary_stage", "stage_candidates", "rank", "setup_rank", "display_order",
              "setup_rank_features", "reason_codes", "watch_reasons", "ret1_pct", "daily_path", "trend_strength")
    return {"counts": result["counts"]["decision_counts"], "top_ids": result["top_ids"],
            "items": [{key: row.get(key) for key in fields} for row in result["items"]]}


def assert_business_slice(bundle, cutoff, baseline, candidate):
    comparison = compare_facts(baseline, candidate)
    if not comparison["matched"]:
        raise ValueError("BUSINESS_INJECTION_COMPARISON_FAILED: " + json_text(comparison))
    keys = {(r["instrument"], r["session"], r["field"]) for r in baseline}
    native = sam.run_stage_aware_momentum(bundle, cutoff=cutoff)
    left, left_reads = strategy_with_facts(bundle, cutoff, baseline, expected_keys=keys)
    right, right_reads = strategy_with_facts(bundle, cutoff, candidate, expected_keys=keys)
    hashes = {"native": digest(native), "python_injected": digest(left), "go_injected": digest(right)}
    if len(set(hashes.values())) != 1: raise AssertionError("NATIVE_PYTHON_GO_BUSINESS_MISMATCH " + json_text(hashes))
    if not left["items"] or left["counts"]["unknown"] == left["counts"]["eligible"]:
        raise AssertionError("BUSINESS_FIXTURE_EMPTY_OR_ALL_UNKNOWN")
    if any(left_reads["counts"].get(key, 0) == 0 for key in ("BAR", "WINDOW_RETURN", "MA_VALUE")):
        raise AssertionError("INJECTED_FACTS_NOT_CONSUMED")
    changed = deepcopy(candidate)
    target = next(r for r in changed if r["instrument"] == "TEST000" and r["session"] == str(bundle.observation_date) and r["field"] == "MA60")
    target.update(value=1.0, value_json="1.0", exact_value={"numerator": "1", "denominator": "1"})
    counterfactual, reads = strategy_with_facts(bundle, cutoff, changed, expected_keys=keys)
    before = next(r for r in left["items"] if r["instrument_id"] == "TEST000")
    after = next(r for r in counterfactual["items"] if r["instrument_id"] == "TEST000")
    if before["decision_status"] == after["decision_status"] or after["trend_strength"]["ma_values"]["60"] != 1.0:
        raise AssertionError("VALID_COUNTERFACTUAL_NOT_CONSUMED")
    # A second controlled test intervention demonstrates consumption of OHLC,
    # return and MA20 individually in concrete output fields, beyond read counts.
    price_changed = deepcopy(candidate)
    interventions = {"open": Fraction(25), "high": Fraction(30), "low": Fraction(20), "close": Fraction(27),
                     "prev_close": Fraction(26), "comparable_price": Fraction(27),
                     "ret1_pct": Fraction(-20), "MA20": Fraction(1)}
    for fact in price_changed:
        if fact["instrument"] != "TEST000" or fact["session"] != str(bundle.observation_date): continue
        if fact["field"] == "price_basis":
            fact.update(value="TEST_FIXTURE_COUNTERFACTUAL_BASIS", value_json=json_text("TEST_FIXTURE_COUNTERFACTUAL_BASIS"))
        if fact["field"] in interventions:
            number = interventions[fact["field"]]
            fact.update(value=float(number), value_json=json_text(float(number)),
                        exact_value={"numerator": str(number.numerator), "denominator": str(number.denominator)})
    sensitivity, sensitivity_reads = strategy_with_facts(bundle, cutoff, price_changed, expected_keys=keys)
    sensitivity_row = next(r for r in sensitivity["items"] if r["instrument_id"] == "TEST000")
    observed = {field: sensitivity_row["daily_path"].get(field) for field in ("open", "high", "low", "close", "prev_close")}
    observed.update(ret1_pct=sensitivity_row["ret1_pct"], MA20=sensitivity_row["trend_strength"]["ma_values"]["20"],
                    comparable_price=sensitivity_row["comparable_close"], price_basis=sensitivity_row["comparable_basis"])
    if any(observed[field] != float(value) for field, value in interventions.items()):
        raise AssertionError("PRICE_RETURN_MA20_INTERVENTION_NOT_CONSUMED")
    if observed["price_basis"] != "TEST_FIXTURE_COUNTERFACTUAL_BASIS": raise AssertionError("BASIS_NOT_CONSUMED")
    negatives = []
    for name in ("unit", "value", "provenance"):
        bad = deepcopy(candidate)
        item = next(r for r in bad if r["field"] == "ret1_pct" and r["status"] == "KNOWN")
        if name == "unit": item["unit"] = "fraction"
        elif name == "value": item["value"] = 99.0; item["value_json"] = "99.0"
        else: item["provenance"]["pit_status"] = "NOT_VERIFIED"
        try: strategy_with_facts(bundle, cutoff, bad, expected_keys=keys)
        except ValueError as exc: negatives.append({"mutation": name, "status": "REJECTED_BEFORE_STRATEGY", "error": str(exc)})
        else: raise AssertionError("INVALID_INJECTION_ACCEPTED " + name)
    return {"status": "PASS_CURRENT_SLICE_ONLY", "coverage_fields": list(COVERAGE_FIELDS), "hashes": hashes,
            "native": business_projection(native), "python_injected": business_projection(left), "go_injected": business_projection(right),
            "python_consumption": left_reads, "go_consumption": right_reads,
            "counterfactual": {"field": "MA60", "value": 1, "before": business_projection(left), "after": business_projection(counterfactual), "reads": reads},
            "field_consumption_intervention": {"data_class": "CONTROLLED_TEST_COUNTERFACTUAL_NOT_SOURCE_ASSERTION",
                "injected": {**{k: float(v) for k, v in interventions.items()}, "price_basis": "TEST_FIXTURE_COUNTERFACTUAL_BASIS"},
                "observed": observed, "reads": sensitivity_reads},
            "invalid_injections": negatives}


def load_case(path, *, state_dir):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    bundle = MarketInput.model_validate(payload["market_input"])
    if payload.get("materialize_history"):
        from stockfish_core.storage import SQLiteStore
        from stockfish_core.yaogu.history_artifacts import compact_input
        from stockfish_core.yaogu.contracts import DailyBar
        bundle = compact_input(bundle, SQLiteStore(Path(state_dir) / "fixture-history.sqlite3"), recent_sessions=1)
        additions = tuple(DailyBar.model_validate(row) for row in payload.get("inline_rows", []))
        bundle = bundle.model_copy(update={"bars": bundle.bars + additions})
    return payload, bundle, _clock(payload["cutoff"])


def golden_summary(facts):
    latest = max(row["session"] for row in facts)
    fields = ("instrument", "session", "field", "value", "exact_value", "status", "reason", "unit")
    return {"row_count": len(facts), "facts_hash": snapshot_hash(facts), "latest_session": latest,
            "latest_facts": [{key: row.get(key) for key in fields} for row in facts if row["session"] == latest]}


def fact_manifest(ctx, source_manifest, facts, *, builder):
    """Map actual frozen-input identities into the shared manifest schema."""
    source_hash = digest(source_manifest)
    metadata = {"observation_date": ctx.observation_date, "decision_cutoff": ctx.cutoff.isoformat(),
        "universe_hash": digest(ctx.bundle.universe), "qualification": ctx.qualification,
        "calendar_revision": source_manifest["calendar_revision"],
        "source_policy_revision": source_manifest["resolver_version"],
        "feature_spec_revision": "R16_R1_PRODUCTION_EXACT_PRICE_RET1_MA20_MA60",
        "input_hash": digest(ctx.bundle.model_dump(mode="json")), "source_hash": source_hash,
        "source_refs": sorted({ref for row in facts for ref in row["provenance"]["source_refs"]})}
    result = manifest_from_run(metadata, facts, snapshot_id="resolved_" + source_hash,
                               artifact_names={"source_manifest": "source_manifest.json", "facts_jsonl": builder + "_facts.jsonl"})
    result.update(source_manifest_hash=source_hash, builder_version=builder, coverage_fields=list(COVERAGE_FIELDS))
    result["manifest_hash"] = digest({k: v for k, v in result.items() if k != "manifest_hash"})
    return result


def run_case(input_path, output, *, go_command="go", expected_path=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    started = datetime.now().astimezone().isoformat()
    payload, bundle, cutoff = load_case(input_path, state_dir=output)
    ctx, sources, manifest = freeze_source(bundle, cutoff)
    baseline = production_facts(ctx, sources)
    if not validate_facts(baseline)["valid"]: raise AssertionError(json_text(validate_facts(baseline)))
    write_json(output / "source_manifest.json", manifest)
    write_json(output / "resolved_source.json", sources)
    write_jsonl(output / "python_production_facts.jsonl", baseline)
    command = [go_command, "run", "./cmd/stockfish-go-core", "--input", str((output / "resolved_source.json").resolve()),
               "--output", str((output / "go_facts.jsonl").resolve())]
    go = subprocess.run(command, cwd=REPO / "go-core", text=True, encoding="utf-8", capture_output=True, timeout=120)
    toolchain = subprocess.run([go_command, "version"], cwd=REPO / "go-core", text=True, capture_output=True, timeout=30)
    write_json(output / "go_command.json", {"command": command, "exit_code": go.returncode, "stdout": go.stdout,
        "stderr": go.stderr, "toolchain": toolchain.stdout.strip(), "start": started,
        "end": datetime.now().astimezone().isoformat(), "provenance": provenance_identity()})
    if go.returncode: raise RuntimeError("GO_FAILED: " + go.stderr)
    candidate = read_facts_jsonl(output / "go_facts.jsonl")
    manifests = {"python": fact_manifest(ctx, manifest, baseline, builder="python_production"),
                 "go": fact_manifest(ctx, manifest, candidate, builder="go")}
    validations = {name: {"schema": validate_manifest(value),
                          "binding": validate_snapshot_binding(value, baseline if name == "python" else candidate)}
                   for name, value in manifests.items()}
    validations["manifest_comparison"] = compare_manifests(manifests["python"], manifests["go"])
    for name, value in manifests.items(): write_json(output / (name + "_manifest.json"), value)
    write_json(output / "manifest_validation.json", validations)
    if any(not check["valid"] for name in ("python", "go") for check in validations[name].values()):
        raise AssertionError("INVALID_FACT_MANIFEST_OR_BINDING")
    if not validations["manifest_comparison"]["matched"]: raise AssertionError("FACT_MANIFEST_MISMATCH")
    comparison = compare_facts(baseline, candidate)
    write_json(output / "comparison.json", comparison)
    if payload.get("expected_unsupported_domain"):
        if comparison["matched"]: raise AssertionError("UNSPECIFIED_BASIS_NEGATIVE_DID_NOT_MISMATCH")
        try: validate_injection(baseline, {(r["instrument"], r["session"], r["field"]) for r in baseline})
        except ValueError as exc:
            if "COMPARABLE_BASIS_UNSPECIFIED" not in str(exc): raise
        else: raise AssertionError("UNSUPPORTED_DOMAIN_ACCEPTED")
        result = {"status": "EXPECTED_MISMATCH_UNSUPPORTED_DOMAIN", "production_business_boundary": "NOT_CLOSED_NO_SEMANTICS_CHANGE"}
    else:
        if not comparison["matched"]: raise AssertionError("UNEXPECTED_FACT_MISMATCH see " + str(output / "comparison.json"))
        result = {"status": "PASS"}
    summary = golden_summary(baseline)
    if expected_path:
        expected = json.loads(Path(expected_path).read_text(encoding="utf-8"))
        if summary != expected["facts"]:
            write_json(output / "golden_actual.json", summary)
            raise AssertionError("FROZEN_GOLDEN_MISMATCH")
    if payload.get("business_slice"):
        result["business"] = assert_business_slice(bundle, cutoff, baseline, candidate)
        write_json(output / "business_slice.json", result["business"])
        if expected_path and result["business"]["native"] != expected["business"]: raise AssertionError("FROZEN_BUSINESS_GOLDEN_MISMATCH")
    result.update({"case_id": payload["case_id"], "facts": summary, "provenance": provenance_identity(),
        "input_file_sha256": hashlib.sha256(Path(input_path).read_bytes()).hexdigest(), "source_manifest_hash": digest(manifest),
        "start": started, "end": datetime.now().astimezone().isoformat(), "exit_code": 0,
        "toolchain": toolchain.stdout.strip(), "strategy_config_hash": digest(sam.CONFIG), "market_edge": "NOT_ESTABLISHED"})
    write_json(output / "result.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--fixtures-dir", default=str(DEFAULT_FIXTURES))
    parser.add_argument("--case", action="append")
    parser.add_argument("--input", help="Separate real source extract; no synthetic label")
    parser.add_argument("--go-binary-command", default="go")
    args = parser.parse_args(argv)
    if args.input:
        run_case(args.input, args.output_dir, go_command=args.go_binary_command)
        return 0
    folder = Path(args.fixtures_dir)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    results = []
    for case in manifest["cases"]:
        if args.case and case["case_id"] not in args.case: continue
        print(json_text({"case": case["case_id"], "status": "STARTED"}), flush=True)
        result = run_case(folder / case["input"], Path(args.output_dir) / case["case_id"], expected_path=folder / case["expected"], go_command=args.go_binary_command)
        results.append({"case_id": case["case_id"], "status": result["status"], "facts": result["facts"]["row_count"]})
        print(json_text(results[-1]), flush=True)
    if not results: raise ValueError("NO_CASES_SELECTED")
    write_json(Path(args.output_dir) / "summary.json", {"cases": results, "provenance": provenance_identity()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
