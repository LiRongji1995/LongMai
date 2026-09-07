"""Read-only, one-day actual-input review; no threshold edits or data acquisition.

Run from repository root with its existing Python environment. All outputs stay
beside this script. No production database/business storage class is imported.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "stock-fish"
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(APP))
from stockfish_core.yaogu.contracts import MarketInput, digest
from stockfish_core.yaogu.strategy_experiments import run_strategy_experiments
from stockfish_core.yaogu.strategy_tracking import authoritative_strategy_items, evaluate_strategy_experiment_outcomes
from stockfish_core.yaogu.replay_statistics import price_statistics
from stockfish_core.yaogu.paper_execution import assumptions, evaluate_paper_execution, summarize_paper

PV = "PV_OBSERVATION_V1"
FOCUS = "PV_FOCUS_PREVIEW_V0.1"
STAGE = "STAGE_AWARE_MOMENTUM_V1"
HORIZONS = (1, 2, 3, 5, 10)
REPORTS = APP / "reports/codex"
INPUT = REPORTS / "pv_observation_20260904_rebuilt_20260907T091708/market-input.json"
OLD = INPUT.with_name("strategy-experiments.json")
SAVED = REPORTS / "stage_aware_momentum_v1_initial_audit_20260907T111840/stage-aware-strategy-experiments.json"


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def counts(rows, key):
    return dict(sorted(Counter(r.get(key, "MISSING") for r in rows).items()))


def reasons(rows):
    # Multiple independent reasons overlap; do not sum this as distinct people.
    return dict(Counter(code for r in rows for code in r.get("reason_codes", [])).most_common())


def tri(statuses):
    return "FAIL" if "FAIL" in statuses else "UNKNOWN" if "UNKNOWN" in statuses else "PASS"


def main():
    old, saved = read(OLD), read(SAVED)
    raw = read(INPUT)
    bundle = MarketInput.model_validate(raw)
    cutoff = datetime.fromisoformat(saved["decision_cutoff"])
    print("Validated actual saved input; rerunning current production entry with original cutoff.", flush=True)
    run = run_strategy_experiments(bundle, cutoff=cutoff)
    print("Current production strategy entry completed.", flush=True)
    pv, focus, stage = run["results"][PV], run["selection_results"][FOCUS], run["results"][STAGE]
    pool = set(pv["qualified_ids"])
    complete = set(pv["complete_feature_ids"])
    selected = {sid: {r["instrument_id"] for r in items} for sid, items in authoritative_strategy_items(run)}
    selected[FOCUS] = set(focus["selected_ids"])
    old_consistency = {}
    for sid in (PV, "DNA_RERANK_V1", "THEME_CONTEXT_V1"):
        fields = ("items", "top_ids", "counts", "qualified_ids", "complete_feature_ids", "eligible_feature_items", "rejected", "missing", "status")
        checks = {k: run["results"][sid].get(k) == old["results"][sid].get(k) for k in fields}
        old_consistency[sid] = checks
    old_consistency[FOCUS] = {k: focus.get(k) == old["selection_results"][FOCUS].get(k) for k in ("items", "selected_ids", "count", "selection_policy_hash")}
    new_consistency = {k: stage.get(k) == saved["results"][STAGE].get(k) for k in ("items", "counts", "qualified_ids", "top_ids", "status")}
    by_iid = {r["instrument_id"]: r for r in stage["items"]}
    known_all = {iid for iid,r in by_iid.items() if all(s != "UNKNOWN" for s in r["condition_statuses"].values())}
    strategy_coverage = {}
    for sid, result in run["results"].items():
        if result.get("compatibility_alias"):
            continue
        strategy_coverage[sid] = {"status": result["status"], "actual_selected": len(selected[sid]),
            "qualified_ids_count": len(result.get("qualified_ids", [])), "counts": result.get("counts"),
            "note": "qualified_ids is not a uniform data-coverage field across strategies"}
    strategy_coverage[FOCUS] = {"status": focus["status"], "actual_selected": len(selected[FOCUS]), "parent_pv_count": len(pv["items"])}
    # Count reasons and single-condition removals. These are diagnostics only;
    # UNKNOWN remains UNKNOWN and no modified run is written as a strategy run.
    pv_reasons = {r["instrument_id"]: r["reason_codes"] for r in pv["rejected"]}
    focus_reasons = {r["instrument_id"]: r["reason_codes"] for r in focus["items"] if not r["selected"]}
    ablations = []
    for rule in ("PV_AMOUNT_RATIO_BELOW_1_2", "PV_RET5_NOT_POSITIVE"):
        added = [iid for iid, codes in pv_reasons.items() if set(codes) == {rule}]
        ablations.append({"strategy": PV, "removed_condition": rule, "base_selected":len(selected[PV]), "additional_pass":len(added), "new_total":len(selected[PV])+len(added), "outcome_evaluable":0, "outcomes":"NOT_VERIFIED_NO_FUTURE_PRICES"})
    for rule in ("A_BELOW_2_0", "R_BELOW_0_10", "RAW_CLOSE_BELOW_RAW_OPEN", "CLOSE_LOCATION_BELOW_0_70"):
        added = [iid for iid,codes in focus_reasons.items() if set(codes) == {rule}]
        ablations.append({"strategy":FOCUS, "removed_condition":rule, "parent_scope":"FROZEN_PV_SELECTED_PARENT_UNCHANGED", "base_selected":len(selected[FOCUS]), "additional_pass":len(added), "new_total":len(selected[FOCUS])+len(added), "outcome_evaluable":0, "outcomes":"NOT_VERIFIED_NO_FUTURE_PRICES"})
    for rule in ("liquidity_bottom", "stage_identification", "sector_linkage", "funds_linkage"):
        status = Counter(tri([s for k,s in r["condition_statuses"].items() if k != rule]) for r in stage["items"])
        ablations.append({"strategy":STAGE, "removed_condition":rule, "base_selected":len(selected[STAGE]), "additional_pass":status["PASS"]-len(selected[STAGE]), "new_status_counts":dict(status), "outcome_evaluable":0, "outcomes":"NOT_VERIFIED_NO_FUTURE_PRICES"})
    stage_populations = {}
    for name in ("START", "ACCELERATION", "HIGH_LEVEL_DIVERGENCE", "TREND_PULLBACK"):
        ids = {iid for iid,r in by_iid.items() if r["stage_candidates"][name]["status"] == "PASS"}
        liq = {iid for iid in ids if by_iid[iid]["liquidity_status"] == "PASS"}
        stage_populations[name] = {"stage_pass":len(ids), "stage_and_liquidity_pass":len(liq), "stage_pass_outside_pv":len(ids-selected[PV]), "stage_pass_outside_focus":len(ids-selected[FOCUS]), "stage_liquidity_outside_pv":len(liq-selected[PV]), "stage_liquidity_outside_focus":len(liq-selected[FOCUS]), "formal_selected":len(ids&selected[STAGE]), "outcome_evaluable":0, "outcomes":"NOT_VERIFIED_NO_FUTURE_PRICES"}
    # Direct strategy function output has no baseline; CandidateService normally
    # adds one with a signal-close cohort. Here explicitly use the narrower
    # six-day PV-complete diagnostic, as in the weekly replay, without storage.
    diagnostic = dict(run)
    diagnostic["evaluation_baselines"] = {"ELIGIBLE_UNIVERSE_EQUAL_WEIGHT_PRICE_DIAGNOSTIC":{
        "strategy_id":"ELIGIBLE_UNIVERSE_EQUAL_WEIGHT_PRICE_DIAGNOSTIC", "items":[dict(r,rank=i) for i,r in enumerate(pv["eligible_feature_items"],1)]}}
    labels = evaluate_strategy_experiment_outcomes(diagnostic, bundle, cutoff, horizons=HORIZONS)
    summaries = []
    for sid in list(selected)+["ELIGIBLE_UNIVERSE_EQUAL_WEIGHT_PRICE_DIAGNOSTIC"]:
        for h in HORIZONS:
            rows = [r for r in labels if r["horizon"]==h and (r["strategy_id"]==sid if sid!=FOCUS else r["strategy_id"]==PV and r["selected_focus"])]
            summaries.append({"strategy_id":sid,"horizon":h,**price_statistics(rows,expected_dates=[run["observation_date"]])})
    quadrants = []
    for sid, ids in selected.items():
        excluded = pool-ids
        rr = [r for r in stage["items"] if r["instrument_id"] in excluded] if sid==STAGE else pv["rejected"] if sid==PV else [r for r in focus["items"] if not r["selected"]] if sid==FOCUS else []
        if sid in (PV, FOCUS):
            excluded_rows = list(pv["rejected"]) + [dict(r,reason_codes=[r["reason"]]) for r in pv["missing"]]
            if sid == FOCUS:
                excluded_rows += [r for r in focus["items"] if not r["selected"]]
        else:
            source = run["results"].get(sid, {})
            present = {r["instrument_id"]:r for r in source.get("items", [])}
            excluded_rows = [present.get(iid,{"instrument_id":iid,"reason_codes":["NO_STRATEGY_EVALUATED_ROW"]}) for iid in sorted(excluded)]
        signatures = Counter(tuple(sorted(r.get("reason_codes") or ["UNSPECIFIED_EXCLUSION"])) for r in excluded_rows)
        exclusion_groups = [{"reason_codes":list(codes),"count":n,"up":None,"down":None,"flat":None,"mature_outcome_denominator":0} for codes,n in sorted(signatures.items(), key=lambda pair: (-pair[1], pair[0]))]
        quadrants.append({"strategy_id":sid, "eligible_pool":len(pool),"selected":len(ids),"not_selected":len(excluded),
            "selected_up":None,"selected_down":None,"not_selected_up":None,"not_selected_down":None,"flat":None,"mature_outcome_denominator":0,
            "missing_reference_in_eligible_pool":len(pool-complete),"status":"NOT_VERIFIED_NO_FUTURE_PRICES",
            "exclusion_reason_counts_overlapping":reasons(rr), "exclusive_full_pool_exclusion_groups":exclusion_groups,
            "exclusive_group_total":sum(g["count"] for g in exclusion_groups),"reason_scope":"FULL_QUALIFIED_POOL; MISSING_ROWS_RETAINED_EXPLICITLY"})
    print("Outcome denominators and single-condition diagnostics computed.", flush=True)
    paper = evaluate_paper_execution(run,bundle,cutoff,horizons=HORIZONS)
    inventories = []
    for path in sorted(REPORTS.rglob("*strategy-experiments.json")):
        r=read(path);inventories.append({"path":str(path.relative_to(ROOT)),"observation_date":r.get("observation_date"),"cutoff":r.get("decision_cutoff"),"sha256":sha(path)})
    sqlite_tables = []
    for path in sorted((APP / "data/codex-pv-cache").rglob("*.sqlite3")):
        con=sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True)
        tables=[r[0] for r in con.execute("select name from sqlite_master where type='table' order by name")]
        sqlite_tables.append({"path":str(path.relative_to(ROOT)),"size_bytes":path.stat().st_size,"tables":tables,"access":"SQLITE_URI_MODE_RO"})
        con.close()
    output = {
        "evidence":"ACTUAL_EXECUTION_ON_REAL_SAVED_INPUT_NOT_ARCHIVED_HISTORICAL_SIGNAL", "source_input":str(INPUT.relative_to(ROOT)), "input_sha256":sha(INPUT),
        "saved_old_sha256":sha(OLD),"saved_stage_sha256":sha(SAVED),"current_entry": "run_strategy_experiments", "cutoff":cutoff.isoformat(),
        "observation_date":run["observation_date"], "saved_coverage_type":run["coverage_type"], "saved_audit_mode":run["audit_mode"], "bar_sessions":sorted({r["session"] for r in raw["bars"]}),
        "bar_count":len(raw["bars"]),"calendar_last":raw["sessions"][-1],"themes":len(raw["themes"]),"funds":len(raw["funds"]),
        "available_run_inventory":inventories,"cache_sqlite_inventory":sqlite_tables,
        "five_signal_days":{"requested":5,"actually_available_dates":sorted({r['observation_date'] for r in inventories}),"status":"NOT_VERIFIED_ONLY_ONE_SAVED_SIGNAL_DAY", "reason":"Earlier price sessions are not earlier frozen qualification snapshots or six-day-complete signal inputs; no backdating or synthetic market history."},
        "old_frozen_comparison":old_consistency,"saved_stage_comparison":new_consistency,
        "pool":{"date_qualified":len(pool),"pv_features_complete":len(complete),"missing_pv_features":len(pool-complete), "all_stage_conditions_known":len(known_all),"pv_features_and_all_stage_conditions_known":len(known_all&complete),"not_common_selected_intersection":True,"common_pool_definition":"STRICT_DIAGNOSTIC_ALL_FOUR_STAGE_CONDITIONS_KNOWN_INCLUDING_FAIL; NOT_AN_EXISTING_CANONICAL_COMMON_DATA_COHORT"},
        "strategy_coverage":strategy_coverage,"stage_status":counts(stage["items"],"status"),"stage_condition_counts":stage["counts"]["condition_counts"],
        "stage_overlap":{"number_of_pass_stages":dict(Counter(sum(c['status']=='PASS' for c in r['stage_candidates'].values()) for r in stage['items']))},
        "stage_populations":stage_populations,"single_condition_ablations":ablations,"whole_pool_quadrants":quadrants,
        "price_statistics":summaries,"paper_assumptions":assumptions(),"paper_results":summarize_paper(paper),
        "case_rescue_scope":"Stage/liq eligibility is a diagnostic and cannot be substituted for sector/funds-complete formal selection. No future-outcome good/bad effect is estimable.",
        "limitations":["One signal date; pooled and date-equal remain unestimable with zero mature labels.","No repeat-security time-series independence assessment from one signal date.","Missing later calendar and future prices are not zero returns.","Main MA/sector/funds deletion cannot estimate condition contribution when source data are absent."]}
    target=OUT / "evaluation_actual_summary.json"
    target.write_text(json.dumps(output,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"output":str(target),"old_checks_all_pass":all(all(v.values()) for v in old_consistency.values()),"saved_stage_all_pass":all(new_consistency.values()),"pool":output["pool"],"selected":{k:len(v) for k,v in selected.items()},"label_count":len(labels),"paper":output["paper_results"]},ensure_ascii=False),flush=True)


if __name__ == "__main__":
    main()
