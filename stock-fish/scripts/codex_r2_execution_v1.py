"""Explicit R2 account research using the unchanged R1 execution engine.

JSON can request a readiness diagnosis, never restore a source capability. A
real signal needs a live native core/archive capability. The execution adapter
checks local archive consistency, not provider authenticity or historical PIT;
its output only permits a conditional hypothetical simulation. No selector, DB,
broker or network operation is performed. No result admits market validation.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import date
import hashlib
import os
from pathlib import Path
import re
import stat

from stockfish_core.yaogu.contracts import digest
from stockfish_core.yaogu.history_artifacts import digest_json_stream

VERSION = "SHORT_HORIZON_R2_EXECUTION_AUDIT_V1"
STRATEGY = "SHORT_HORIZON_STRENGTH_R2"
MAX_FILE_BYTES = 8 * 1024**2
MAX_READ_BYTES = 128 * 1024**2
MAX_FILES = 4096
FP = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns", "st_flags")


def _need(condition, reason):
    if not condition:
        raise ValueError(reason)


def _validate_run(run, expected_content_hash):
    _need(type(run) is dict and isinstance(expected_content_hash, str)
          and re.fullmatch(r"[0-9a-f]{64}", expected_content_hash), "R2_FULL_RUN_HASH_REQUIRED")
    _need(run.get("content_hash") == expected_content_hash == digest_json_stream(
        {key: value for key, value in run.items() if key != "content_hash"}), "R2_FULL_RUN_HASH_MISMATCH")
    _need(run.get("strategy_id") == STRATEGY and set(run.get("results", {})) == {STRATEGY},
          "R2_SINGLE_FROZEN_STRATEGY_REQUIRED")
    _need(run.get("coverage_type") in {"TEST_FIXTURE", "RECONSTRUCTED_HISTORICAL", "PROSPECTIVE_CAPTURED"},
          "R2_SIGNAL_COVERAGE_TYPE_REQUIRED")
    _need(all(isinstance(run.get(key), str) and run[key] for key in
              ("run_id", "input_id", "observation_date", "target_session", "decision_cutoff")),
          "R2_FROZEN_RUN_BINDING_REQUIRED")
    _need(date.fromisoformat(run["target_session"]) > date.fromisoformat(run["observation_date"]),
          "R2_TARGET_SESSION_INVALID")
    arm = run["results"][STRATEGY]
    rows, eligible = arm.get("items"), arm.get("eligible_pool_ids")
    _need(type(rows) is list and type(eligible) is list, "R2_FULL_POOL_REQUIRED")
    ids = [row.get("instrument_id") for row in rows]
    _need(all(type(iid) is str and iid for iid in ids)
          and len(ids) == len(set(ids)) == len(eligible) == len(set(eligible))
          and set(ids) == set(eligible) and arm.get("full_eligible_pool_retained") is True,
          "R2_FULL_POOL_MISMATCH")
    qualification = run.get("qualification")
    _need(type(qualification) is dict and qualification == arm.get("qualification")
          and qualification.get("eligible_ids") == eligible
          and type(qualification.get("eligible")) is int and qualification["eligible"] == len(eligible)
          and run.get("universe_hash") == digest(qualification),
          "R2_QUALIFICATION_POOL_BINDING_MISMATCH")
    _need(arm.get("strategy_id") == STRATEGY
          and arm.get("observation_date") == run["observation_date"]
          and arm.get("decision_cutoff") == run["decision_cutoff"], "R2_ARM_PARENT_MISMATCH")
    _need(all(type(row.get("research_selected")) is bool and row.get("formal_focus") is False
              and row.get("core_pass") is False and row.get("decision_status") in
              {"WATCH", "REJECTED", "UNKNOWN"} for row in rows), "R2_RESEARCH_SAFETY_MISMATCH")
    _need(all(row["research_selected"] == (row["decision_status"] == "WATCH") for row in rows),
          "R2_SELECTION_DECISION_MISMATCH")
    selected = [row["instrument_id"] for row in rows if row["research_selected"]]
    counts = arm.get("counts", {})
    _need(selected == arm.get("research_selected_ids") and not arm.get("top_ids")
          and not arm.get("qualified_ids") and counts.get("eligible") == len(ids)
          and counts.get("evaluated") == len(ids) and counts.get("focus") == 0
          and counts.get("research_observation") == len(selected),
          "R2_FROZEN_SELECTION_MISMATCH")
    policy = STRATEGY + "_OBSERVATION"
    views = run.get("selection_results", {})
    _need(set(views) == {policy} and views[policy].get("strategy_id") == STRATEGY
          and views[policy].get("selected_ids") == selected and views[policy].get("count") == len(selected)
          and [row.get("instrument_id") for row in views[policy].get("items", [])] == selected,
          "R2_OFFICIAL_SELECTION_VIEW_MISMATCH")
    return rows, selected


def readiness(run, *, expected_content_hash):
    """Integrity-only diagnosis; it does not authenticate source or publication."""
    rows, selected = _validate_run(run, expected_content_hash)
    return {"schema_version": VERSION, "status": "BLOCKED_DATA", "run_id": run["run_id"],
            "input_id": run["input_id"], "signal_content_hash": expected_content_hash,
            "strategy_id": STRATEGY, "eligible_count": len(rows), "selected_count": len(selected),
            "selected_ids": selected, "formal_focus_count": 0,
            "required": ["GENUINE_NATIVE_CORE_OR_FRESH_ARCHIVE_CAPABILITY",
                         "FRESH_DATED_EXECUTION_ARCHIVE", "FROZEN_GENERATION_CLOCK",
                         "DATED_CALENDAR_RULE_COST_ORDER_AND_CORPORATE_ACTION_EVIDENCE"],
            "source_authenticated": False, "signal_policy_mutated": False,
            "account_return": None, "market_edge": "NOT_ESTABLISHED",
            "capacity_status": "NOT_VALIDATED", "execution_requested": False,
            "execution_evidence_role": "LOCAL_ARCHIVE_CONSISTENCY_ONLY",
            "source_reauthenticated_now": False,
            "source_reauthentication_scope": "EXECUTION_EVIDENCE",
            "evaluation_admissible_for_market_validation": False}


def _owner_terminal(owner, run):
    from stockfish_core.yaogu.premarket_native_core_v1 import NativeCore
    from stockfish_core.yaogu import short_horizon_archive_bridge_v1 as bridge
    if type(owner) is NativeCore:
        owner.validate()
        _need(digest_json_stream(owner.run) == digest_json_stream(run), "R2_NATIVE_OWNER_RUN_MISMATCH")
        return "GENUINE_NATIVE_CORE"
    if type(owner) is bridge.ArchiveBridgeCapability:
        state = bridge._state(owner)
        _need(bridge._bridge_identity(state["session"]) == state["identity"],
              "R2_ARCHIVE_VERIFIER_CHANGED")
        _need(digest_json_stream(bridge._verified_run(state)) == digest_json_stream(run),
              "R2_ARCHIVE_OWNER_RUN_MISMATCH")
        state["session"].terminal()
        return "FRESH_ARCHIVE_AUTHENTICATION"
    raise ValueError("R2_GENUINE_SOURCE_OWNER_REQUIRED")


def _read_reference(path, *, remaining):
    from scripts import codex_workspace as workspace
    path = Path(path).absolute()
    _need(path.resolve(strict=True) == path, "R2_EXECUTION_SOURCE_ALIAS")
    with workspace._directory_fd(path.parent) as parent:
        before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        _need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
              and not getattr(before, "st_flags", 0) & 0x40000000, "R2_EXECUTION_SOURCE_NOT_LOCAL_REGULAR")
        _need(before.st_size <= min(MAX_FILE_BYTES, remaining), "R2_EXECUTION_READ_BUDGET")
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            opened = os.fstat(fd)
            with os.fdopen(fd, "rb", closefd=False) as handle:
                raw = handle.read(min(MAX_FILE_BYTES, remaining) + 1)
            after = os.fstat(fd)
            current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            fingerprint = lambda info: tuple(getattr(info, key, 0) for key in FP)
            _need(path.resolve(strict=True) == path
                  and fingerprint(before) == fingerprint(opened) == fingerprint(after)
                  == fingerprint(current) == fingerprint(path.lstat())
                  and len(raw) == before.st_size, "R2_EXECUTION_SOURCE_CHANGED")
            return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
                    "size": len(raw), "fingerprint": list(fingerprint(before))}
        finally:
            os.close(fd)


def _archive_references(archive, base, max_read_bytes):
    root = Path(base).absolute()
    _need(root.resolve(strict=True) == root and root.is_dir(), "R2_EXECUTION_ARCHIVE_ROOT_INVALID")
    artifacts = archive.get("artifacts")
    _need(type(artifacts) is list and 0 < len(artifacts) <= MAX_FILES, "R2_EXECUTION_ARCHIVE_FILES_REQUIRED")
    refs, consumed, seen = [], 0, set()
    for artifact in artifacts:
        relative = Path(artifact["path"])
        _need(not relative.is_absolute() and ".." not in relative.parts, "R2_EXECUTION_ARCHIVE_PATH_ESCAPE")
        path = root / relative
        _need(path not in seen, "R2_DUPLICATE_EXECUTION_ARTIFACT")
        seen.add(path)
        # Reserve the unchanged adapter's read and our complete terminal reread.
        ref = _read_reference(path, remaining=(max_read_bytes - consumed) // 3)
        _need(ref["sha256"] == artifact["receipt"]["content_hash"], "R2_EXECUTION_RECEIPT_HASH_MISMATCH")
        refs.append(ref)
        consumed += 3 * ref["size"]
    return refs, consumed


def evaluate_r2(run, *, expected_content_hash, execution_archive=None, archive_base=None,
                source_owner=None, allow_test_fixture=False, execution_spec=None,
                slippage_bps=None, max_read_bytes=MAX_READ_BYTES):
    """Evaluate only the frozen selection; missing live evidence fails closed.

The original generated_at is an explicit hypothetical publication proxy, not
proof of user receipt. Historical late generation stays late. Caller-supplied
tapes and assumed historical publication timestamps are deliberately unsupported.
"""
    from stockfish_core.yaogu import execution_research_r1 as engine
    from stockfish_core.yaogu.research_r1 import config
    from stockfish_core.yaogu.research_r1_contracts import effective_spec
    from stockfish_core.yaogu.research_r1_adapter import adapt_execution, ResolvedTape

    _need(type(allow_test_fixture) is bool, "R2_FIXTURE_OPT_IN_BOOL_REQUIRED")
    _need(type(max_read_bytes) is int and 0 < max_read_bytes <= MAX_READ_BYTES,
          "R2_EXECUTION_READ_BUDGET_INVALID")
    diagnostic = readiness(run, expected_content_hash=expected_content_hash)
    rows, selected = _validate_run(run, expected_content_hash)
    original_hash = digest_json_stream(run)
    unknown_decisions = sum(row["decision_status"] == "UNKNOWN" for row in rows)
    if not selected and unknown_decisions:
        return {**diagnostic, "execution_requested": True,
                "reason": "R2_EMPTY_SELECTION_NOT_PROVEN_DECIDABLE"}
    fixture = run.get("coverage_type") == "TEST_FIXTURE"
    if fixture:
        _need(allow_test_fixture, "R2_TEST_FIXTURE_OPT_IN_REQUIRED")
        owner_kind = "EXPLICIT_TEST_FIXTURE_ONLY"
    elif source_owner is None:
        return {**diagnostic, "execution_requested": True, "reason": "R2_GENUINE_SOURCE_OWNER_REQUIRED"}
    else:
        owner_kind = _owner_terminal(source_owner, run)
    if execution_archive is None or archive_base is None:
        return {**diagnostic, "execution_requested": True, "reason": "R2_EXECUTION_ARCHIVE_REQUIRED"}
    _need(type(execution_archive) is dict, "R2_ARCHIVE_DECLARATION_REQUIRED_NOT_TAPE")
    archive = deepcopy(execution_archive)
    archive_hash = digest(archive)
    _need((archive.get("coverage_type") == "TEST_FIXTURE") == fixture,
          "R2_SIGNAL_EXECUTION_COVERAGE_TYPE_MISMATCH")
    if not fixture:
        _need(archive.get("coverage_type") == run.get("coverage_type"),
              "R2_SIGNAL_EXECUTION_COVERAGE_TYPE_MISMATCH")
    try:
        published = engine.stamp(run["generated_at"])
        cutoff = engine.stamp(run["decision_cutoff"])
        _need(published >= cutoff, "R2_GENERATION_PRECEDES_DECISION_CUTOFF")
    except (KeyError, TypeError, ValueError) as error:
        return {**diagnostic, "execution_requested": True,
                "reason": "R2_FROZEN_GENERATION_CLOCK_REQUIRED", "detail": type(error).__name__}
    source_root = Path(__file__).resolve().parents[1]
    code_paths = [Path(__file__).resolve(), source_root / "config/stage_aware_research_r1.json",
                  source_root / "scripts/codex_workspace.py"]
    code_paths.extend(source_root / "stockfish_core/yaogu" / name for name in
                      ("execution_research_r1.py", "research_r1.py", "research_r1_adapter.py",
                       "research_r1_contracts.py", "contracts.py", "visibility.py"))
    code_refs, code_budget = [], 0
    for path in code_paths:
        ref = _read_reference(path, remaining=(max_read_bytes - code_budget) // 2)
        code_refs.append(ref)
        code_budget += 2 * ref["size"]
    # The unchanged config() reads the JSON once more after our initial pin.
    default_config_read_bytes = code_refs[1]["size"] if execution_spec is None else 0
    code_budget += default_config_read_bytes
    _need(code_budget <= max_read_bytes, "R2_EXECUTION_READ_BUDGET")
    references, budgeted = _archive_references(archive, archive_base, max_read_bytes - code_budget)
    tape, adaptation = adapt_execution(archive, base=archive_base)
    if tape is None:
        return {**diagnostic, "execution_requested": True, "reason": "R2_EXECUTION_ADAPTER_BLOCKED",
                "adapter_report": adaptation}
    _need(type(tape) is ResolvedTape and tape.intact(), "R2_FRESH_ADAPTER_TAPE_REQUIRED")
    _need(engine.stamp(tape["as_of"]) >= published, "R2_SIGNAL_NOT_YET_VISIBLE_AT_EVALUATION")
    sessions = tape["sessions"]
    day = run["observation_date"]
    _need(day in sessions and sessions.index(day) + 1 < len(sessions)
          and sessions[sessions.index(day) + 1] == run["target_session"], "R2_TARGET_CALENDAR_MISMATCH")
    spec = effective_spec(config() if execution_spec is None else execution_spec, tape["cost_scenario"])
    _need(spec["entry_offset"] == 1, "R2_NEXT_SESSION_ENTRY_REQUIRED")
    slippage = spec["primary_slippage_bps"] if slippage_bps is None else slippage_bps
    # Current R2 does not certify an exhaustive theme map. Unknown themes must
    # stay unknown; no display attachment or name is used to invent exposures.
    signals = [{"instrument_id": iid, "signal_session": day, "themes": None,
                "published_at": published.isoformat(), "evidence_available_at": cutoff.isoformat(),
                "signal_run_id": run["run_id"], "signal_content_hash": expected_content_hash,
                "publication_policy": "FROZEN_GENERATION_PROXY_NOT_USER_RECEIPT"} for iid in selected]
    engine_result = engine.simulate(signals, tape, spec=spec, slippage_bps=slippage)
    _need(tape.intact() and digest_json_stream(run) == original_hash
          and digest(execution_archive) == archive_hash, "R2_BORROWED_INPUT_CHANGED")
    for ref in references:
        current = _read_reference(ref["path"], remaining=ref["size"])
        _need(current == ref, "R2_EXECUTION_SOURCE_CHANGED_AT_TERMINAL")
    for ref in code_refs:
        _need(_read_reference(ref["path"], remaining=ref["size"]) == ref,
              "R2_EXECUTION_IMPLEMENTATION_CHANGED")
    if not fixture:
        _need(_owner_terminal(source_owner, run) == owner_kind, "R2_SOURCE_OWNER_CHANGED")
    _need(digest_json_stream(run) == original_hash and digest(execution_archive) == archive_hash
          and tape.intact(), "R2_BORROWED_INPUT_CHANGED_AT_TERMINAL")
    records = engine_result["records"]
    _need([record["instrument_id"] for record in records] == selected,
          "R2_EXECUTION_SELECTION_CHANGED")
    counts = Counter(record["status"] for record in records)
    result = {"schema_version": VERSION, "status": "SIMULATED_" + engine_result["status"],
              "strategy_id": STRATEGY, "run_id": run["run_id"], "input_id": run["input_id"],
              "signal_content_hash": expected_content_hash, "selected_ids": selected,
              "eligible_count": len(rows), "selected_count": len(selected), "formal_focus_count": 0,
              "signal_policy_mutated": False, "source_owner_kind": owner_kind,
              "execution_archive_hash": archive_hash, "execution_source_references": references,
              "verified_read_bytes": budgeted + code_budget, "adapter_report": adaptation,
              "default_execution_config_read_bytes": default_config_read_bytes,
              "read_budget_scope": "EXECUTION_ARCHIVE_AND_IMPLEMENTATION_OWNER_USES_ITS_ORIGINAL_BUDGET",
              "implementation_references": code_refs,
              "execution_spec_hash": digest(spec), "execution_spec": spec,
              "publication_policy": "FROZEN_GENERATION_PROXY_NOT_USER_RECEIPT",
              "funnel": {"signals": len(selected), "orders_sized": sum("quantity" in r for r in records),
                         "assumed_entries": sum("entry_price" in r for r in records),
                         "closed": counts["CLOSED"], "status_counts": dict(counts)},
              "account_simulation": engine_result, "market_edge": "NOT_ESTABLISHED",
              "strategy_inference_status": "NOT_PERFORMED_SINGLE_FROZEN_SELECTION",
              "strategy_account_return": None,
              "account_result_scope": "CONDITIONAL_FROZEN_SELECTED_ACCOUNT_ONLY",
              "unknown_decision_count": unknown_decisions,
              "execution_evidence_role": "LOCAL_ARCHIVE_CONSISTENCY_ONLY",
              "source_reauthenticated_now": False,
              "source_reauthentication_scope": "EXECUTION_EVIDENCE",
              "evaluation_admissible_for_market_validation": False,
              "capacity_status": "NOT_VALIDATED", "theme_risk_status": "UNKNOWN_NO_AUTHENTICATED_THEME_MAP",
              "history_pit_restored": False, "user_delivery_verified": False,
              "production_promotion": "DISABLED", "source_or_business_database_written": False}
    result["content_hash"] = digest_json_stream(result)
    return result
