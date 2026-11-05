"""V2 label isolation and cross-plan local trial registration.

V1 formulas, pairs and block estimator remain unchanged. Purging is registered
before outcomes, every excluded signal stays in the report, and neither local
registration nor archived JSON grants PIT or independent-market authority.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import codex_research_protocol_v1 as old
from scripts import research_trial_ledger_v1 as ledger

VERSION = "CODEX_RESEARCH_PROTOCOL_V2"
need, digest = old.need, old.digest


def implementation_identity():
    previous, references = old.implementation_identity()
    hashes = dict(previous["file_hashes"])
    for name in ("scripts/codex_research_protocol_v2.py", "scripts/research_trial_ledger_v1.py"):
        _, ref = old.io.files._read_file(ROOT / name, maximum=1024**2, retain=False)
        references.append(ref)
        hashes[name] = ref["sha256"]
    return dict(version=VERSION, file_hashes=hashes, source_hash=digest(hashes)), references


def trial_configuration_hash(draft, policy):
    """Bind every declared design choice, excluding ledger/history identifiers."""
    return digest(dict(design={k:v for k,v in draft.items() if k not in {"plan_id", "attempts"}}, label_policy=policy))


def label_support(draft, policy):
    need(type(policy) is dict and set(policy) == {"label", "purge", "embargo_sessions", "discovery_labels", "stopping_policy"}, "PROTOCOL_V2_LABEL_POLICY_FIELDS")
    need(policy["label"] == "NEXT_SESSION_THROUGH_HORIZON_CLOSE_REFERENCE"
         and policy["purge"] == "EXCLUDE_LABELS_CROSSING_PHASE_BOUNDARY", "PROTOCOL_V2_UNSUPPORTED_LABEL_POLICY")
    need(policy["stopping_policy"] == "ONE_EVALUATION_PER_PHASE_NO_RETRY", "PROTOCOL_V2_STOPPING_POLICY_REQUIRED")
    embargo = policy["embargo_sessions"]
    need(type(embargo) is int and 0 <= embargo <= 100, "PROTOCOL_V2_EMBARGO_BOUND")
    schedule, evidence, reference = old._registered_axis()
    sessions = [str(day) for day in schedule.sessions]
    indexes = {day: i for i, day in enumerate(sessions)}
    windows = draft["windows"]
    need(all(schedule.coverage_start <= date.fromisoformat(w["start"]) <= date.fromisoformat(w["end"]) <= schedule.coverage_end
             for w in windows.values()), "PROTOCOL_V2_FINITE_CALENDAR_REQUIRED")
    expected = [day for day in sessions if any(windows[name]["start"] <= day <= windows[name]["end"] for name in ("validation", "holdout"))]
    need(expected == draft["expected_signal_sessions"], "PROTOCOL_V2_COMPLETE_PHASE_SIGNAL_CALENDAR_REQUIRED")
    phase_days = {name: [d for d in sessions if windows[name]["start"] <= d <= windows[name]["end"]]
                  for name in ("validation", "holdout")}
    need(all(phase_days.values()), "PROTOCOL_V2_EMPTY_PHASE")
    first_validation = indexes[phase_days["validation"][0]]
    # Historical consumed labels cannot be inferred from a new plan's horizons.
    # These are explicit declarations bound to original evidence hashes, not
    # proof that all offline exploration has been disclosed.
    spans = policy["discovery_labels"]
    need(type(spans) is list and len(spans) == len(draft["discovery_evidence"])
         and all(type(s) is dict and set(s) == {"id", "evidence_content_hash", "label_start", "label_end", "scope"} for s in spans),
         "PROTOCOL_V2_DISCOVERY_LABEL_SPANS_REQUIRED")
    need(len({s["id"] for s in spans}) == len(spans) and {s["id"] for s in spans} == {e["id"] for e in draft["discovery_evidence"]},
         "PROTOCOL_V2_DISCOVERY_LABEL_SPANS_REQUIRED")
    discovery = []
    for item in draft["discovery_evidence"]:
        day = item["observation_date"]
        need(day in indexes, "PROTOCOL_V2_DISCOVERY_SESSION_UNKNOWN")
        span = next(s for s in spans if s["id"] == item["id"])
        need(span["scope"] == "DECLARED_ALL_CONSUMED_LABELS" and span["evidence_content_hash"] == item["content_hash"]
             and span["label_start"] in indexes and span["label_end"] in indexes
             and day < span["label_start"] <= span["label_end"], "PROTOCOL_V2_DISCOVERY_LABEL_SPAN_UNKNOWN")
        end = indexes[span["label_end"]]
        discovery.append(dict(id=item["id"], signal_session=day,
            label_start=span["label_start"], label_end=span["label_end"], evidence_content_hash=span["evidence_content_hash"],
            span_authority="EXPLICIT_LOCAL_DECLARATION_NOT_INDEPENDENTLY_VERIFIED",
            isolated=end < first_validation - embargo))
    need(all(row["isolated"] for row in discovery), "PROTOCOL_V2_DISCOVERY_LABELS_REACH_VALIDATION")
    rows = []
    for phase in ("validation", "holdout"):
        boundary = (indexes[phase_days["holdout"][0]] - embargo if phase == "validation"
                    else indexes[phase_days["holdout"][-1]] + 1)
        for day in phase_days[phase]:
            index = indexes[day]
            for horizon in draft["horizons"]:
                end = index + horizon
                known = end < len(sessions)
                reasons = []
                if not known:
                    reasons.append("LABEL_OUTSIDE_FINITE_CALENDAR")
                if end >= boundary:
                    reasons.append("LABEL_REACHES_NEXT_PHASE_OR_EMBARGO" if phase == "validation" else "LABEL_EXCEEDS_FROZEN_HOLDOUT_END")
                rows.append(dict(phase=phase, signal_session=day, horizon=horizon,
                    signal_session_index=index,
                    label_start=sessions[index + 1] if index + 1 < len(sessions) else None,
                    label_end=sessions[end] if known else None,
                    eligible_for_phase_estimation=known and not reasons,
                    status="ELIGIBLE" if known and not reasons else "PURGED" if known else "UNKNOWN",
                    reason_codes=reasons))
    old.io.verify_snapshots([reference])
    return dict(calendar=evidence, policy=deepcopy(policy), discovery_support=discovery, labels=rows,
        all_declared_signals_retained=True, exclusions_selected_without_outcomes=True,
        execution_exit_delay_scope="NOT_TRADING_LABELS_DELAYED_EXITS_REQUIRE_SEPARATE_SUPPORT"), reference


def freeze_plan(draft, policy, *, ledger_root, expected_head):
    implementation, implementation_refs = implementation_identity()
    draft = deepcopy(old._draft(draft))
    registry = ledger.inspect(ledger_root)
    need(registry["head"] == expected_head, "LEDGER_STALE_HEAD_NO_AUTOMATIC_RETRY")
    need(draft["attempts"] == registry["attempts"] and draft["attempts"], "LEDGER_ALL_SCOPE_ATTEMPTS_REQUIRED")
    need(draft["plan_id"] not in registry["plans"], "LEDGER_PLAN_ID_ALREADY_FROZEN")
    design_hash = trial_configuration_hash(draft, policy)
    active = [a for a in registry["attempts"] if a["status"] == "PLANNED"
              and a["configuration_hash"] == design_hash and a["attempt_id"] not in registry["assigned_attempt_ids"]]
    need(len(active) == 1, "PROTOCOL_V2_ONE_FRESH_DESIGN_BOUND_ATTEMPT_REQUIRED")
    base = old.freeze_plan(draft)
    support, reference = label_support(draft, policy)
    frozen = old.seal(dict(schema_version=VERSION + "_FROZEN", base_plan=base,
        label_support=support, scope_hash=registry["scope"]["content_hash"],
        ledger_predecessor=registry["head"], attempts_hash=digest(draft["attempts"]),
        implementation=implementation,
        active_attempt_id=active[0]["attempt_id"], trial_configuration_hash=design_hash,
        local_trial_count=len(draft["attempts"]), offline_or_other_scope_attempts="UNKNOWN",
        independent_holdout_authority=False, **old.SAFE))
    old.io.verify_snapshots([reference, *registry["references"], *implementation_refs])
    registration = ledger.register_plan(ledger_root, draft["plan_id"], frozen["content_hash"],
        draft["attempts"], expected_head=expected_head, active_attempt_id=active[0]["attempt_id"], frozen_plan=frozen)
    result = old.seal(dict(schema_version=VERSION + "_PLAN", frozen=frozen, registration=registration))
    validate_plan(result, ledger_root=ledger_root)
    return result


def validate_plan(plan, *, ledger_root):
    old.check_seal(plan, VERSION + "_PLAN")
    frozen, registration = plan["frozen"], plan["registration"]
    old.check_seal(frozen, VERSION + "_FROZEN")
    base = old.validate_plan(frozen["base_plan"])
    implementation, implementation_refs = implementation_identity()
    need(frozen.get("implementation") == implementation, "PROTOCOL_V2_FROZEN_IMPLEMENTATION_CHANGED")
    need(all(frozen.get(k) == v for k, v in old.SAFE.items())
         and frozen.get("independent_holdout_authority") is False
         and frozen.get("offline_or_other_scope_attempts") == "UNKNOWN", "PROTOCOL_V2_AUTHORITY_FORBIDDEN")
    support, reference = label_support(base["draft"], frozen["label_support"]["policy"])
    need(support == frozen["label_support"], "PROTOCOL_V2_LABEL_SUPPORT_CHANGED")
    registry = ledger.inspect(ledger_root)
    event = registry["plans"].get(base["draft"]["plan_id"])
    need(event == registration and event is not None and event["payload"]["plan_hash"] == frozen["content_hash"]
         and event["previous_hash"] == frozen["ledger_predecessor"]
         and event["scope_hash"] == frozen["scope_hash"] == registry["scope"]["content_hash"]
         and event["payload"]["frozen_plan"] == frozen
         and event["payload"]["active_attempt_id"] == frozen["active_attempt_id"]
         and frozen["trial_configuration_hash"] == trial_configuration_hash(base["draft"], frozen["label_support"]["policy"])
         and event["payload"]["attempts_hash"] == frozen["attempts_hash"] == digest(base["draft"]["attempts"])
         and frozen["local_trial_count"] == len(base["draft"]["attempts"]), "PROTOCOL_V2_LEDGER_BINDING_MISMATCH")
    need(any(a["attempt_id"] == frozen["active_attempt_id"] and a["configuration_hash"] == frozen["trial_configuration_hash"]
             and a["status"] == "PLANNED" for a in base["draft"]["attempts"]), "PROTOCOL_V2_ACTIVE_ATTEMPT_DESIGN_MISMATCH")
    # Loading the entire chain validates each FREEZE against every earlier
    # attempt. Later attempts are retained and expand the current multiplicity.
    old.io.verify_snapshots([reference, *registry["references"], *implementation_refs])
    return base, registry


def recover_plan(ledger_root, plan_id):
    """Read a committed freeze after report publication failed; never re-freeze."""
    registry = ledger.inspect(ledger_root)
    event = registry["plans"].get(plan_id)
    need(event is not None and event["payload"]["frozen_plan"] is not None, "PROTOCOL_V2_COMMITTED_PLAN_NOT_FOUND")
    plan = old.seal(dict(schema_version=VERSION + "_PLAN", frozen=deepcopy(event["payload"]["frozen_plan"]), registration=event))
    validate_plan(plan, ledger_root=ledger_root)
    return plan


def _completed_study(event):
    report = deepcopy(event["payload"]["report"])
    report["ledger_result_head"] = event["content_hash"]
    return old.seal(report)


def recover_study(ledger_root, plan_id, phase):
    recover_plan(ledger_root, plan_id)  # Recheck frozen implementation and labels.
    registry = ledger.inspect(ledger_root)
    record = registry["studies"].get(plan_id + ":" + phase)
    need(record is not None and record["result"] is not None, "PROTOCOL_V2_STUDY_RESULT_UNKNOWN_NO_RETRY")
    return _completed_study(record["result"])


def evaluate_study(plan, pairs, *, ledger_root, phase="validation", expected_head=None, _pair_loader=None):
    base, registry = validate_plan(plan, ledger_root=ledger_root)
    if expected_head is not None:
        need(expected_head == registry["head"], "LEDGER_STALE_HEAD_NO_AUTOMATIC_RETRY")
    input_hash = digest(pairs)
    declared_inputs = pairs
    claim = ledger.claim_study(ledger_root, base["draft"]["plan_id"], plan["frozen"]["content_hash"],
        phase, input_hash, expected_head=registry["head"])
    registry = ledger.inspect(ledger_root)
    need(registry["head"] == claim["content_hash"], "PROTOCOL_V2_LEDGER_CHANGED_DURING_STUDY")
    _, implementation_refs = implementation_identity()
    if _pair_loader is not None:
        pairs = _pair_loader(pairs)
    original = old.evaluate_study(base, pairs, phase=phase)
    need(digest(declared_inputs) == input_hash, "PROTOCOL_V2_STUDY_INPUT_CHANGED")
    draft = base["draft"]
    support = {(r["signal_session"], r["horizon"]): r for r in plan["frozen"]["label_support"]["labels"] if r["phase"] == phase}
    all_series, estimable = {}, {}
    for key, rows in original["series"].items():
        horizon = int(key.rsplit(":H", 1)[1])
        annotated = []
        for row in rows:
            label = support[(row["date"], horizon)]
            annotated.append(dict(row, label_support=deepcopy(label),
                inference_eligible=row["inference_eligible"] and label["eligible_for_phase_estimation"]))
        all_series[key] = annotated
        estimable[key] = [r for r in annotated if r["label_support"]["eligible_for_phase_estimation"]]
    from stockfish_core.yaogu.research_r1_io import paired_block_comparisons
    sensitivity = {str(block): paired_block_comparisons(estimable, seed=draft["seed"], block_length=block,
        repetitions=draft["bootstrap_repetitions"]) for block in draft["block_lengths"]}
    # One attempt already counts its first look; another phase is another look.
    counts = {}
    for study in registry["studies"].values():
        pid = study["claim"]["payload"]["plan_id"]
        counts[pid] = counts.get(pid, 0) + 1
    multiplicity = len(registry["attempts"]) + sum(max(0, n - 1) for n in counts.values())
    for arm in sensitivity.values():
        for result in arm.values():
            if result["status"] == "ESTIMATED":
                result["selected_scope_trial_bonferroni_p"] = min(1.0, result["holm_adjusted_p"] * multiplicity)
    primary = sensitivity[str(draft["primary_block_length"])][original["primary_key"]]
    result = old.seal(dict(schema_version=VERSION + "_STUDY", plan_hash=plan["content_hash"],
        frozen_plan_hash=plan["frozen"]["content_hash"], phase=phase,
        status="CONDITIONAL_ISOLATED_ARCHIVE_ESTIMATE" if primary["status"] == "ESTIMATED" else "BLOCKED_DATA",
        original_unpurged_estimate_role="DIAGNOSTIC_ONLY_NOT_PHASE_ESTIMATE",
        all_signal_series=all_series, estimation_series=estimable, calendar=original["calendar"],
        missing_dates=original["missing_dates"], block_length_sensitivity=sensitivity,
        primary_key=original["primary_key"], primary_result=primary,
        local_trial_count_at_freeze=plan["frozen"]["local_trial_count"],
        local_trial_count_now=multiplicity, ledger_head=registry["head"],
        study_claim_hash=claim["content_hash"], study_input_hash=input_hash,
        consumed_phase_count=len(registry["studies"]), stopping_policy="ONE_EVALUATION_PER_PHASE_NO_RETRY",
        trial_policy="HOLM_CURRENT_FAMILY_TIMES_REGISTERED_ATTEMPTS_PLUS_EXTRA_PHASE_LOOKS_INCLUDING_UNKNOWN",
        offline_or_other_scope_attempts="UNKNOWN", sample_is_independent_holdout_verified=False,
        label_isolation_is_not_source_or_sample_independence=True, **old.SAFE))
    # An append during computation changes the declared family; refuse stale output.
    need(ledger.inspect(ledger_root)["head"] == registry["head"], "PROTOCOL_V2_LEDGER_CHANGED_DURING_STUDY")
    old.io.verify_snapshots(implementation_refs)
    terminal = ledger.finish_study(ledger_root, draft["plan_id"], phase, claim["content_hash"], result, expected_head=registry["head"])
    return _completed_study(terminal)


def main(argv=None):
    parser = argparse.ArgumentParser(description="标签隔离、跨计划试验账本和条件研究；不采集", allow_abbrev=False)
    parser.add_argument("command", choices=("freeze", "study", "recover", "recover-study", "design-hash"))
    parser.add_argument("--ledger-root")
    parser.add_argument("--expected-head")
    parser.add_argument("--input")
    parser.add_argument("--input-sha256")
    parser.add_argument("--plan-id")
    parser.add_argument("--plan")
    parser.add_argument("--plan-sha256")
    parser.add_argument("--phase", choices=("validation", "holdout"), default="validation")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    refs = []
    try:
        need(not Path(args.output).exists(), "PROTOCOL_V2_NEW_OUTPUT_REQUIRED")
        need(args.command == "design-hash" or args.ledger_root, "PROTOCOL_V2_SELECTED_LEDGER_REQUIRED")
        if args.command == "recover":
            result = recover_plan(args.ledger_root, args.plan_id)
        elif args.command == "recover-study":
            result = recover_study(args.ledger_root, args.plan_id, args.phase)
        else:
            value, ref = old.io.read_snapshot(args.input, args.input_sha256)
            refs.append(ref)
        if args.command == "freeze":
            need(set(value) == {"draft", "label_policy"}, "PROTOCOL_V2_DRAFT_ENVELOPE")
            old.io.verify_snapshots(refs)
            result = freeze_plan(value["draft"], value["label_policy"], ledger_root=args.ledger_root, expected_head=args.expected_head)
        elif args.command == "design-hash":
            need(set(value) == {"draft", "label_policy"}, "PROTOCOL_V2_DRAFT_ENVELOPE")
            support, ref = label_support(value["draft"], value["label_policy"])
            refs.append(ref)
            result = dict(status="DESIGN_HASH_ONLY_NOT_REGISTERED", configuration_hash=trial_configuration_hash(value["draft"], value["label_policy"]),
                          label_support=support, **old.SAFE)
        elif args.command == "study":
            need(args.expected_head is not None, "PROTOCOL_V2_STUDY_EXPECTED_HEAD_REQUIRED")
            plan, ref = old.io.read_snapshot(args.plan, args.plan_sha256)
            refs.append(ref)
            if plan.get("entry_version") == VERSION:
                plan = plan["result"]
            base, _ = validate_plan(plan, ledger_root=args.ledger_root)
            need(set(value) == {"plan_hash", "pairs"} and value["plan_hash"] == plan["content_hash"]
                 and type(value["pairs"]) is list and len(value["pairs"]) <= old.MAX_DAYS, "PROTOCOL_V2_BATCH_BINDING")
            budget = [old.MAX_BATCH_READ_BYTES, old.MAX_BATCH_READ_BYTES]
            def load_pairs(records):
                return [old._read_pair(record, refs, budget, base["draft"]["horizons"], base["draft"]["risk_matching"]) for record in records]
            result = evaluate_study(plan, value["pairs"], ledger_root=args.ledger_root, phase=args.phase,
                                    expected_head=args.expected_head, _pair_loader=load_pairs)
        old.io.verify_snapshots(refs)
        def terminal():
            old.io.verify_snapshots(refs)
            if args.command in {"freeze", "recover"}:
                validate_plan(result, ledger_root=args.ledger_root)
            elif args.command == "recover-study":
                need(recover_study(args.ledger_root, args.plan_id, args.phase) == result,
                     "PROTOCOL_V2_COMMITTED_STUDY_CHANGED_DURING_RECOVERY")
            elif args.command == "study":
                validate_plan(plan, ledger_root=args.ledger_root)
                need(ledger.inspect(args.ledger_root)["head"] == result["ledger_result_head"],
                     "PROTOCOL_V2_LEDGER_CHANGED_DURING_PUBLICATION")
        old.io.publish_new(args.output, dict(entry_version=VERSION, result=result, input_carriers=refs, **old.SAFE),
                           validate=terminal)
        print(json.dumps(dict(status=result.get("status", "FROZEN_AND_REGISTERED"), output=args.output,
                              market_edge="NOT_ESTABLISHED")))
        return 4 if result.get("status") == "BLOCKED_DATA" else 0
    except Exception as error:
        reason = str(error)
        if re.fullmatch(r"[A-Z][A-Z0-9_]{2,160}", reason) is None:
            reason = "PROTOCOL_V2_REJECTED_OR_UNAVAILABLE"
        print(json.dumps(dict(status="REJECTED", reason=reason,
                              mutation_may_have_committed="INSPECT_LEDGER_DO_NOT_AUTOMATICALLY_RETRY" if args.command in {"freeze", "study"} else "NONE")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
