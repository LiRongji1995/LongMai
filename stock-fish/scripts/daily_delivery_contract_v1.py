"""Bind daily delivery, full-pool coverage and durable metrics to one business.

Saved JSON cannot issue source authority. Production acceptance reopens the
transactional authority, original job/carriers and journals on every call; a
twenty-day declaration is only a protocol until actual independent days exist.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, time as wall_time, timedelta, timezone
import json
import math
from pathlib import Path
import statistics
import sys
import time
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import codex_operational_report_v1 as operational
from scripts import codex_delivery_audit_v1 as delivery
from scripts import codex_research_coverage_v1 as coverage
from scripts import durable_stage_metrics_v1 as metrics

VERSION = "DAILY_DELIVERY_CONTRACT_V1"
PROTOCOL = "DAILY_DELIVERY_20_SESSION_PROTOCOL_V1"
PROTOCOL_V2 = "DAILY_DELIVERY_20_SESSION_PROTOCOL_V2"
STRICT_PIT = "STRICT_PROSPECTIVE_PIT"
OPERATIONAL_SCOPE = "PROSPECTIVE_OPERATIONAL_DELIVERY"
OUTCOME_BINDING = "ACTIVE_OUTCOME_PIPELINE_V1_BINDING"
SH = ZoneInfo("Asia/Shanghai")


class _AuditBudget:
    def __init__(self, byte_limit=512*1024**2, seconds=180):
        need(type(byte_limit) is int and 0 < byte_limit <= 2*1024**3 and
             metrics._number(seconds) and 0 < seconds <= 600, "DELIVERY_AUDIT_LIMITS_INVALID")
        self.limit, self.seconds, self.used, self.started = byte_limit, seconds, 0, time.monotonic()

    def remaining(self, cap):
        need(self.used < self.limit, "DELIVERY_CUMULATIVE_READ_LIMIT")
        return min(cap, self.limit-self.used)

    def remaining_seconds(self, cap):
        left = self.seconds - (time.monotonic()-self.started)
        need(left > 0, "DELIVERY_CUMULATIVE_TIME_LIMIT")
        return min(cap, left)

    def charge(self, amount):
        need(type(amount) is int and amount >= 0, "DELIVERY_READ_COUNT_INVALID")
        self.used += amount
        need(self.used <= self.limit, "DELIVERY_CUMULATIVE_READ_LIMIT")
        self.remaining_seconds(600)


def _journal(budget, directory, expected):
    result = metrics.audit_journal(directory, expected_identity=expected,
        max_bytes=budget.remaining(metrics.MAX_BYTES), max_seconds=budget.remaining_seconds(180))
    budget.charge(result["read_bytes"])
    return result


def need(ok, reason):
    if not ok:
        raise ValueError(reason)


def _sessions(rows):
    need(type(rows) is list and rows, "DELIVERY_COMPLETE_CALENDAR_ROWS_REQUIRED")
    days = {}
    for row in rows:
        need(type(row) is dict and set(row) == {"calendar_date", "is_trading_day"},
             "DELIVERY_CALENDAR_FIELDS")
        day = date.fromisoformat(row["calendar_date"])
        need(str(day) == row["calendar_date"] and day not in days and row["is_trading_day"] in ("0", "1"),
             "DELIVERY_CALENDAR_DUPLICATE_OR_FLAG")
        days[day] = row["is_trading_day"]
    need((max(days)-min(days)).days <= 1866 and len(days) == (max(days)-min(days)).days+1,
         "DELIVERY_CALENDAR_NATURAL_DAY_GAP")
    return sorted(str(day) for day, flag in days.items() if flag == "1")


def plan_protocol(calendar_rows, *, first_target_session, strategy_id,
                  calendar_reference, session_count=20, deadline_local="09:00", acceptance_scope=None):
    """Prospective bounded protocol, not evidence of completed market work."""
    need(type(session_count) is int and 20 <= session_count <= 60,
         "DELIVERY_AT_LEAST_20_REAL_SESSIONS_REQUIRED")
    need(type(strategy_id) is str and strategy_id in coverage.STRATEGIES,
         "DELIVERY_STRATEGY_REQUIRED")
    sessions = _sessions(calendar_rows)
    need(first_target_session in sessions, "DELIVERY_FIRST_TARGET_NOT_IN_CALENDAR")
    start = sessions.index(first_target_session)
    targets = sessions[start:start+session_count]
    need(start > 0 and len(targets) == session_count, "DELIVERY_CALENDAR_WINDOW_TOO_SHORT")
    parsed = wall_time.fromisoformat(deadline_local)
    need(parsed.tzinfo is None and parsed.second == 0 and parsed.microsecond == 0,
         "DELIVERY_LOCAL_DEADLINE_MINUTE_REQUIRED")
    need(type(calendar_reference) is dict and type(calendar_reference.get("sha256")) is str,
         "DELIVERY_CALENDAR_REFERENCE_REQUIRED")
    now = datetime.now(timezone.utc)
    pairs = [{"observation_date": sessions[start+i-1], "target_session": target,
              "deadline": datetime.combine(date.fromisoformat(target), parsed, SH).isoformat()}
             for i, target in enumerate(targets)]
    # Freezing after the first observation's close cannot manufacture a
    # prospective twenty-day test from old completed outputs.
    first_observation_close = datetime.combine(date.fromisoformat(pairs[0]["observation_date"]), wall_time(15), SH)
    result = {"schema_version": PROTOCOL, "frozen_at": now.isoformat(),
        "design_role": "PROSPECTIVE" if now < first_observation_close else "RETROSPECTIVE",
        "strategy_id": strategy_id, "expected_sessions": pairs,
        "calendar_rows_hash": metrics.digest(calendar_rows),
        "calendar_reference": deepcopy(calendar_reference),
        "calendar_authentication": "BOUND_DECLARATION_REQUIRES_NATIVE_REVERIFICATION",
        "required_horizons": list(coverage.HORIZONS), "minimum_sessions": 20,
        "acceptance": {"complete_workflow_every_session": True, "on_time_every_session": True,
            "full_pool_partition_every_session": True, "all_five_horizon_cells_accounted": True,
            "all_five_horizon_labels_mature_before_final_acceptance": True,
            "real_prospective_inputs_only": True, "native_cleanup_every_session": True},
        "unknown_policy": "PENDING_AND_UNKNOWN_RETAINED; NO_ZERO_RETURN_IMPUTATION",
        "user_delivery_is_separate": True, "market_edge": "NOT_ESTABLISHED",
        "real_acceptance": "NOT_RUN", "collection_authorized": False}
    if acceptance_scope is not None:
        need(acceptance_scope in {STRICT_PIT, OPERATIONAL_SCOPE}, "DELIVERY_ACCEPTANCE_SCOPE_INVALID")
        result.update(schema_version=PROTOCOL_V2, acceptance_scope=acceptance_scope,
            historical_feature_pit_required=acceptance_scope == STRICT_PIT,
            historical_feature_pit_established=False,
            investment_validation="PROHIBITED_BY_OPERATIONAL_ACCEPTANCE")
        result["acceptance"].update(
            real_prospective_inputs_only=acceptance_scope == STRICT_PIT,
            native_outcome_sources_and_evaluator_reverified=True,
            native_original_day_execution_required=True,
            preserve_original_signal_coverage_type=True)
    result["protocol_hash"] = metrics.digest(result)
    return result


def _protocol(value):
    need(type(value) is dict and value.get("schema_version") in {PROTOCOL, PROTOCOL_V2} and
         value.get("protocol_hash") == metrics.digest({k: v for k, v in value.items() if k != "protocol_hash"}),
         "DELIVERY_PROTOCOL_CHANGED")
    need(value.get("required_horizons") == list(coverage.HORIZONS) and value.get("minimum_sessions") == 20,
         "DELIVERY_PROTOCOL_SCOPE_WEAKENED")
    if value["schema_version"] == PROTOCOL_V2:
        scope = value.get("acceptance_scope")
        need(scope in {STRICT_PIT, OPERATIONAL_SCOPE} and
             value.get("historical_feature_pit_required") is (scope == STRICT_PIT) and
             value.get("historical_feature_pit_established") is False and
             value.get("investment_validation") == "PROHIBITED_BY_OPERATIONAL_ACCEPTANCE" and
             value.get("acceptance", {}).get("real_prospective_inputs_only") is (scope == STRICT_PIT) and
             all(value.get("acceptance", {}).get(key) is True for key in (
                 "native_outcome_sources_and_evaluator_reverified", "native_original_day_execution_required",
                 "preserve_original_signal_coverage_type")), "DELIVERY_V2_SCOPE_WEAKENED")
    pairs = value.get("expected_sessions")
    need(type(pairs) is list and 20 <= len(pairs) <= 60 and
         len({r["target_session"] for r in pairs}) == len(pairs), "DELIVERY_PROTOCOL_SESSIONS")
    for index, row in enumerate(pairs):
        need(date.fromisoformat(row["observation_date"]) < date.fromisoformat(row["target_session"]),
             "DELIVERY_PROTOCOL_DATE_ORDER")
        delivery._clock(row["deadline"])
        if index:
            need(row["observation_date"] == pairs[index-1]["target_session"],
                 "DELIVERY_PROTOCOL_SKIPPED_SESSION")
    return pairs


def freeze_protocol(authority, protocol, *, epoch_id):
    """Explicit authorized pre-registration, never called by an audit or plan."""
    from scripts.business_authority_v1 import Authority
    need(type(authority) is Authority, "DELIVERY_NATIVE_BUSINESS_AUTHORITY_REQUIRED")
    pairs = _protocol(protocol)
    first_close = datetime.combine(date.fromisoformat(pairs[0]["observation_date"]), wall_time(15), SH)
    need(datetime.now(timezone.utc) < first_close and protocol["design_role"] == "PROSPECTIVE",
         "DELIVERY_PROSPECTIVE_FREEZE_TOO_LATE")
    claim = authority.claim({"operation": "MAINTENANCE", "subject": "DAILY_DELIVERY_PROTOCOL",
        "parameters": {"protocol_hash": protocol["protocol_hash"]}}, epoch_id=epoch_id)
    if claim["state"] == "RECORDED":
        return claim
    return authority.finish(claim, success=True, receipt={"schema_version": protocol["schema_version"] + "_FREEZE",
        "protocol_hash": protocol["protocol_hash"], "first_observation_close": first_close.isoformat()})


def _authority(authority, business_id):
    from scripts.business_authority_v1 import Authority
    need(type(authority) is Authority, "DELIVERY_NATIVE_BUSINESS_AUTHORITY_REQUIRED")
    snapshot = authority.inspect(business_id)
    need(type(snapshot) is dict and snapshot.get("business_id") == business_id,
         "DELIVERY_BUSINESS_NOT_REGISTERED")
    metrics.identity(snapshot)
    need(snapshot["business"]["operation"] == "PREMARKET", "DELIVERY_PREMARKET_BUSINESS_REQUIRED")
    return snapshot


def bind_terminal_evidence(*, job, journal_directory, canonical_run_path=None,
                           supervisor_report_path=None, writer_receipt=None, _budget=None):
    """Reference payload for Authority.finish, called at the native final boundary.

    This only captures exact references. The native caller must already have
    validated worker identity/exit and original workflow; this helper cannot
    authorize success. The subsequent auditor repeats native saved-job checks.
    """
    budget = _budget or _AuditBudget()
    initial, started = budget.used, time.monotonic()
    reader = delivery._Reader(budget.remaining(64*1024**2), budget.remaining_seconds(60))
    final = reader.read(Path(job).absolute()/"final-control.json")
    budget.charge(reader.returned_bytes)
    charged = reader.returned_bytes
    journal = _journal(budget, journal_directory, None)
    reader.max_bytes = reader.returned_bytes+budget.remaining(64*1024**2)
    value = {"schema_version": VERSION + "_BINDING", "job_final": final,
        "journal_directory": str(Path(journal_directory).absolute()),
        "journal_terminal_event_hash": journal["terminal_event_hash"],
        "journal_evidence_hash": metrics.digest(journal["evidence"]),
        "journal_status": journal["status"], "identity": journal["identity"]}
    if canonical_run_path is not None:
        value["canonical_run"] = reader.read(Path(canonical_run_path).absolute())
    if supervisor_report_path is not None:
        value["supervisor_report"] = reader.read(Path(supervisor_report_path).absolute())
    if writer_receipt is not None:
        _writer_receipt(writer_receipt, journal)
        value["writer_receipt"] = deepcopy(writer_receipt)
    budget.charge(reader.returned_bytes-charged)
    charged = reader.returned_bytes
    reader.max_bytes = reader.returned_bytes+budget.remaining(64*1024**2)
    reader.max_seconds = time.monotonic()-reader.started+budget.remaining_seconds(60)
    reader.terminal()
    budget.charge(reader.returned_bytes-charged)
    value["binding_audit_resources"] = {"returned_read_bytes": budget.used-initial,
        "wall_seconds": time.monotonic()-started,
        "scope": "ACTUAL_INITIAL_AND_TERMINAL_CONTROL_JOURNAL_READ_RETURNS"}
    return value


def _writer_receipt(receipt, journal):
    need(type(receipt) is dict and receipt.get("schema_version") == metrics.VERSION + "_WRITER_RECEIPT" and
         receipt.get("identity") == journal["identity"] and
         receipt.get("terminal_event_hash") == journal["terminal_event_hash"],
         "DELIVERY_WRITER_RECEIPT_CONFLICT")
    cost = receipt.get("publication_cost")
    lower = journal.get("publication_cost_lower_bound")
    need(type(cost) is dict and type(lower) is dict and set(cost) == set(lower) and
         all(metrics._number(v) and v >= lower[k] for k, v in cost.items()) and
         cost["written_bytes"] == sum(r["bytes"] for r in journal["evidence"]) and
         cost["fsync_calls"] == 2*journal["event_count"], "DELIVERY_WRITER_COST_UNDERSTATED")


def _supervisor_audit(reader, reference, journal):
    from scripts import codex_sdk_tree_supervisor_v3 as native
    value, _ = reader.read(reference["path"], expected=reference, control=True)
    directory = Path(reference["path"]).parent
    need(Path(reference["path"]).name == "completion.json" and
         value.get("schema_version") == native.VERSION and value.get("identity_basis") == native.IDENTITY_BASIS,
         "DELIVERY_NATIVE_SUPERVISOR_REQUIRED")
    need(not reader.exists(directory/"failure.json"), "DELIVERY_SUPERVISOR_TERMINAL_FAILURE")
    claim_ref = value.get("claim")
    need(type(claim_ref) is dict and Path(claim_ref["path"]) == directory/"claim.json",
         "DELIVERY_SUPERVISOR_CLAIM_BINDING")
    claim, _ = reader.read(claim_ref["path"], expected=claim_ref, control=True)
    need(claim.get("schema_version") == native.VERSION and
         claim.get("identity_basis") == native.IDENTITY_BASIS, "DELIVERY_SUPERVISOR_CLAIM_IDENTITY")
    for reference in claim["sources"]:
        reader.read(reference["path"], expected=reference)
    verified = (value.get("status") == "COMPLETE" and value.get("root_reaped") is True
        and value.get("all_observed_descendants_gone") is True
        and value.get("root_identity") == journal["writer_identity"]
        and value.get("cleanup", {}).get("errors") == []
        and value.get("cleanup", {}).get("remaining") == [])
    return {"verified": verified, "root_identity": value.get("root_identity"),
        "child_exit_code": value.get("child_exit_code"), "status": value.get("status"),
        "scope": "PINNED_ROOT_AND_ACTUALLY_OBSERVED_DESCENDANTS_NOT_OS_HARD_QUOTA",
        "utc_elapsed_seconds": value.get("actual_utc_elapsed_seconds"),
        "monotonic_elapsed_seconds": value.get("monotonic_elapsed_seconds")}


def _claim_clock(authority, snapshot, budget):
    """Read the original attempt event; a terminal update is not its start."""
    with authority._connect(readonly=True) as connection:
        rows = connection.execute("SELECT event,at FROM events WHERE business_id=? AND attempt_id=? "
            "AND event IN ('CLAIMED','RESUMED') ORDER BY sequence LIMIT 2",
            (snapshot["business_id"], snapshot["attempt_id"])).fetchall()
    # Two bounded rows, not a fabricated measurement of SQLite/kernel bytes.
    budget.charge(0)
    return rows[0]["at"] if len(rows) == 1 and rows[0]["event"] == "CLAIMED" else None


def _native_sources(*, job, final_reference, run_reference, budget,
                    outcome_archive=None, outcome_source=None, product=None, allow_test_fixture=False,
                    outcome_daily_archives=None, outcome_daily_sources=None,
                    composite_input_manifest=None, source_input_ids=None):
    """Reopen original physical archives and run the unchanged outcome evaluator.

    This is local source integrity, not vendor authentication or historical PIT.
    Only the native verifier can mint the live capability used below. A caller
    dictionary, even with matching hashes, never supplies that capability.
    """
    from stockfish_core.yaogu import short_horizon_archive_bridge_v1 as bridge
    from stockfish_core.yaogu import short_horizon_outcomes_v1 as outcomes
    need(type(allow_test_fixture) is bool, "DELIVERY_FIXTURE_FLAG_INVALID")
    if allow_test_fixture:
        need(any(part.startswith("TEST_FIXTURE") for part in Path(job).absolute().parts),
             "DELIVERY_FIXTURE_REQUIRES_ISOLATED_PATH")
    session = bridge.ArchiveVerificationSession(
        checkpoint=lambda: budget.charge(0), max_total_read_bytes=budget.remaining(2*1024**3),
        max_unique_bytes=budget.remaining(2*1024**3), max_seconds=budget.remaining_seconds(600))
    charged = 0
    try:
        capability = bridge.authenticate_archive(job, expected_final_sha256=final_reference["sha256"],
            session=session, allow_test_fixture=allow_test_fixture, retain_input=outcome_daily_archives is not None)
        state = bridge._state(capability)
        need(state["run_reference"] == run_reference, "DELIVERY_AUTHENTICATED_SIGNAL_CARRIER_MISMATCH")
        run = bridge._verified_run(state)
        signal = capability.audit()
        proof = {"status": "VERIFIED", "signal_archive": signal,
            "historical_feature_pit_established": False,
            "vendor_authentication_established": False,
            "test_fixture": allow_test_fixture or signal["coverage_type"] == "TEST_FIXTURE"
                or any(part.startswith("TEST_FIXTURE") for part in Path(job).absolute().parts),
            "outcome_recomputed": False}
        if product is not None:
            need(type(outcome_archive) is dict and set(outcome_archive) == {"job", "final_sha256"},
                 "DELIVERY_ORIGINAL_OUTCOME_ARCHIVE_REQUIRED")
            later = bridge.authenticate_archive(outcome_archive["job"],
                expected_final_sha256=outcome_archive["final_sha256"], session=session,
                allow_test_fixture=allow_test_fixture, retain_input=True)
            later_state = bridge._state(later)
            if outcome_source is not None:
                binding = outcome_source["receipt"]
                final_path = str(Path(outcome_archive["job"]).resolve()/"final-control.json")
                need(session.files.get(final_path) == binding["job_final"] and
                     later_state["run_reference"] == binding["canonical_run"],
                     "DELIVERY_OUTCOME_SOURCE_ORIGINAL_REFERENCE_MISMATCH")
                need(all(later_state["audit"][key] == outcome_source["business"][key]
                     for key in ("observation_date", "target_session")) and
                     outcome_source["business"]["strategy_id"] == run["strategy_id"],
                     "DELIVERY_OUTCOME_SOURCE_BUSINESS_IDENTITY_MISMATCH")
            bundle = later_state["retained_input"]
            if outcome_daily_archives is not None:
                from scripts import active_outcome_input_v1 as composition
                need(type(outcome_daily_archives) is list and bool(outcome_daily_archives) and
                     type(outcome_daily_sources) is list and
                     len(outcome_daily_sources) == len(outcome_daily_archives),
                     "DELIVERY_COMPOSITE_NATIVE_SOURCE_BINDINGS_REQUIRED")
                # The immutable run already binds its signal input. Adding that
                # redundant ID would change an old one-input failure identity.
                daily_caps, verified_ids, daily_audits = [], set(), []
                for locator, daily_source in zip(outcome_daily_archives, outcome_daily_sources):
                    budget.charge(0)
                    daily_cap = bridge.authenticate_archive(locator["job"],
                        expected_final_sha256=locator["final_sha256"], session=session,
                        allow_test_fixture=allow_test_fixture, retain_input=True)
                    daily_state = bridge._state(daily_cap)
                    binding = daily_source["receipt"]
                    final_path = str(Path(locator["job"]).resolve()/"final-control.json")
                    need(session.files.get(final_path) == binding["job_final"] and
                         daily_state["run_reference"] == binding["canonical_run"],
                         "DELIVERY_DAILY_SOURCE_ORIGINAL_REFERENCE_MISMATCH")
                    need(all(daily_state["audit"][key] == daily_source["business"][key]
                         for key in ("observation_date", "target_session")) and
                         daily_source["business"]["strategy_id"] == run["strategy_id"],
                         "DELIVERY_DAILY_SOURCE_BUSINESS_IDENTITY_MISMATCH")
                    need(locator["input_id"] == daily_state["audit"]["input_id"],
                         "DELIVERY_DAILY_SOURCE_INPUT_ID_MISMATCH")
                    verified_ids.add(locator["input_id"])
                    daily_caps.append(daily_cap)
                    daily_audits.append(daily_cap.audit())
                need(source_input_ids == sorted(verified_ids), "DELIVERY_COMPOSITE_SOURCE_INPUT_IDS_MISMATCH")
                need(daily_audits[-1]["archive_job"] == outcome_archive["job"] and
                     daily_audits[-1]["archive_final_sha256"] == outcome_archive["final_sha256"] and
                     daily_audits[-1]["observation_date"] == max(item["observation_date"] for item in daily_audits),
                     "DELIVERY_COMPOSITE_LAST_SOURCE_MISMATCH")
                bundle, actual_manifest = composition.compose(run, capability, daily_caps, session)
                need(actual_manifest == composite_input_manifest,
                     "DELIVERY_COMPOSITE_MANIFEST_RECOMPUTATION_MISMATCH")
                proof.update(composite_input_manifest=actual_manifest,
                    outcome_daily_archives=daily_audits, source_input_ids=sorted(verified_ids),
                    test_fixture=proof["test_fixture"] or any(item["coverage_type"] == "TEST_FIXTURE" or
                        any(part.startswith("TEST_FIXTURE") for part in Path(item["archive_job"]).parts)
                        for item in daily_audits))
            else:
                need(bundle is not None and product["outcome_input_id"] == later_state["audit"]["input_id"],
                     "DELIVERY_NATIVE_OUTCOME_INPUT_MISMATCH")
            signal_day = run["observation_date"]
            prefix = tuple(day for day in state["input_sessions"] if day <= signal_day)
            need(not prefix or tuple(str(day) for day in bundle.sessions
                 if prefix[0] <= str(day) <= signal_day) == prefix,
                 "DELIVERY_NATIVE_OUTCOME_SIGNAL_CALENDAR_PREFIX_CONFLICT")
            evaluated = delivery._clock(product["evaluated_at"])
            need(evaluated <= datetime.now(timezone.utc), "DELIVERY_OUTCOME_EVALUATION_IN_FUTURE")
            actual = outcomes.evaluate(run, bundle, evaluated, horizons=list(coverage.HORIZONS),
                checkpoint=session.check)["outcomes"]
            # Display wrappers are not accepted here. The exact immutable
            # producer product is pinned by the native outcome terminal.
            need(actual == product, "DELIVERY_NATIVE_OUTCOME_RECOMPUTATION_MISMATCH")
            proof.update(outcome_archive=later.audit(), outcome_recomputed=True,
                outcome_product_hash=coverage._digest(actual), revision_id=actual["revision_id"],
                test_fixture=proof["test_fixture"] or actual["outcome_coverage_type"] == "TEST_FIXTURE"
                    or any(part.startswith("TEST_FIXTURE") for part in Path(outcome_archive["job"]).absolute().parts))
        session.terminal()
        budget.charge(session.read_bytes)
        charged = session.read_bytes
        proof["source_read_bytes"] = charged
        return proof, session, charged
    except BaseException:
        budget.charge(session.read_bytes-charged)
        raise


def _original_day_execution(authority, before, source, budget):
    """Contemporaneous operational clocks; never promotes reconstructed history."""
    if source.get("status") != "VERIFIED":
        return {"status": "NOT_ESTABLISHED", "gaps": ["NATIVE_SIGNAL_SOURCE_NOT_REVERIFIED"],
                "historical_feature_pit_established": False}
    audit = source["signal_archive"]
    business = before["business"]
    start = _claim_clock(authority, before, budget)
    observed_close = datetime.combine(date.fromisoformat(business["observation_date"]), wall_time(18), SH)
    clocks = [start, audit["original_decision_cutoff"], audit["original_generated_at"],
              audit["original_final_sealed_at"], before.get("authority_updated_at")]
    gaps = []
    if any(clock is None for clock in clocks):
        gaps.append("ORIGINAL_ATTEMPT_CLOCK_NOT_PROVEN")
    else:
        instants = [delivery._clock(clock) for clock in clocks]
        if instants[0] < observed_close or instants != sorted(instants):
            gaps.append("ORIGINAL_DAY_EXECUTION_CLOCK_ORDER_INVALID")
        if any(instant.astimezone(SH).date() > date.fromisoformat(business["target_session"])
               for instant in instants):
            gaps.append("ORIGINAL_DAY_EXECUTION_AFTER_TARGET")
    return {"status": "VERIFIED" if not gaps else "NOT_ESTABLISHED", "gaps": gaps,
        "claim_started_at": start, "decision_cutoff": clocks[1], "generated_at": clocks[2],
        "final_sealed_at": clocks[3], "authority_completed_at": clocks[4],
        "signal_coverage_type": audit["coverage_type"], "historical_feature_pit_established": False,
        "scope": "ORIGINAL_DAY_NATIVE_EXECUTION_AND_LOCAL_SOURCE_INTEGRITY_ONLY"}


def _outcome_authority(authority, business_id, *, population, product_reference,
                       signal_job, signal_final, parent_business_id, budget):
    """Native terminal + original journal binding, never a supplied report flag."""
    if business_id is None:
        return {"status": "NOT_ESTABLISHED", "gaps": ["OUTCOME_NATIVE_AUTHORITY_NOT_PROVIDED"]}, None
    snapshot = authority.inspect(business_id)
    need(type(snapshot) is dict and snapshot["state"] == "RECORDED" and snapshot["budget_known"] is True,
         "DELIVERY_OUTCOME_NATIVE_SUCCESS_REQUIRED")
    business, binding = snapshot["business"], snapshot.get("receipt") or {}
    need(business["operation"] == "OUTCOME" and business["run_ids"] == [population["run_id"]],
         "DELIVERY_OUTCOME_BUSINESS_RUN_MISMATCH")
    need(binding.get("schema_version") == OUTCOME_BINDING and
         binding.get("identity") == metrics.identity(snapshot) and
         binding.get("execution_id") == snapshot["attempt_id"] and
         binding.get("parent_business_id") == parent_business_id and
         binding.get("native_execution_completed") is True and
         binding.get("source_authority_restored") is False and
         binding.get("historical_pit_verified") is False, "DELIVERY_OUTCOME_NATIVE_BINDING_REQUIRED")
    need(binding.get("signal_archive") == {"job": str(Path(signal_job).absolute()),
         "final_sha256": signal_final["sha256"]}, "DELIVERY_OUTCOME_SIGNAL_ARCHIVE_MISMATCH")
    daily_archives = binding.get("outcome_daily_archives")
    source_input_ids = binding.get("source_input_ids")
    need(binding.get("product") == product_reference and
         binding.get("run_id") == population["run_id"] and
         binding.get("revision_id") == population["outcome_revision_id"] and
         binding.get("outcome_product_hash") == population["outcome_product_hash"] and
         business["input_ids"] == (source_input_ids if daily_archives is not None else [binding.get("input_id")]),
         "DELIVERY_OUTCOME_PRODUCT_BINDING_MISMATCH")
    source_id = binding.get("outcome_source_business_id")
    need(type(source_id) is str and bool(source_id), "DELIVERY_OUTCOME_SOURCE_BUSINESS_REQUIRED")
    source = _authority(authority, source_id)
    source_binding = source.get("receipt") or {}
    locator = binding.get("outcome_archive")
    need(type(locator) is dict and set(locator) == {"job", "final_sha256"},
         "DELIVERY_ORIGINAL_OUTCOME_ARCHIVE_REQUIRED")
    need(source["state"] == "RECORDED" and source["budget_known"] is True and
         source_binding.get("schema_version") == VERSION + "_BINDING" and
         source_binding.get("identity") == metrics.identity(source),
         "DELIVERY_OUTCOME_SOURCE_NATIVE_SUCCESS_REQUIRED")
    source_final, source_run = source_binding.get("job_final"), source_binding.get("canonical_run")
    need(type(source_final) is dict and type(source_run) is dict and
         source_final.get("path") == str(Path(locator["job"]).absolute()/"final-control.json") and
         source_final.get("sha256") == locator["final_sha256"],
         "DELIVERY_OUTCOME_SOURCE_ARCHIVE_BINDING_MISMATCH")
    daily_sources = []
    if daily_archives is not None:
        need(type(daily_archives) is list and bool(daily_archives) and
             type(binding.get("composite_input_manifest")) is dict and
             type(source_input_ids) is list and bool(source_input_ids) and
             all(type(value) is str for value in source_input_ids) and
             source_input_ids == sorted(set(source_input_ids)),
             "DELIVERY_COMPOSITE_NATIVE_MANIFEST_REQUIRED")
        seen_businesses, seen_jobs = set(), set()
        for daily in daily_archives:
            budget.charge(0)
            need(type(daily) is dict and set(daily) == {"business_id", "job", "final_sha256", "input_id"}
                 and all(type(value) is str and bool(value) for value in daily.values()),
                 "DELIVERY_DAILY_ARCHIVE_DESCRIPTOR_REQUIRED")
            need(daily["business_id"] not in seen_businesses and daily["job"] not in seen_jobs and
                 daily["business_id"] != parent_business_id, "DELIVERY_DAILY_ARCHIVE_DUPLICATE_OR_SIGNAL")
            seen_businesses.add(daily["business_id"]); seen_jobs.add(daily["job"])
            daily_source = _authority(authority, daily["business_id"])
            daily_binding = daily_source.get("receipt") or {}
            need(daily_source["state"] == "RECORDED" and daily_source["budget_known"] is True and
                 daily_binding.get("schema_version") == VERSION+"_BINDING" and
                 daily_binding.get("identity") == metrics.identity(daily_source),
                 "DELIVERY_DAILY_SOURCE_NATIVE_SUCCESS_REQUIRED")
            daily_final, daily_run = daily_binding.get("job_final"), daily_binding.get("canonical_run")
            need(type(daily_final) is dict and type(daily_run) is dict and
                 daily_final.get("path") == str(Path(daily["job"]).absolute()/"final-control.json") and
                 daily_final.get("sha256") == daily["final_sha256"],
                 "DELIVERY_DAILY_SOURCE_ARCHIVE_BINDING_MISMATCH")
            daily_sources.append(daily_source)
        need(daily_archives[-1]["business_id"] == source_id and
             {key:daily_archives[-1][key] for key in ("job", "final_sha256")} == locator,
             "DELIVERY_COMPOSITE_LAST_SOURCE_MISMATCH")
    else:
        need(binding.get("composite_input_manifest") is None and source_input_ids is None,
             "DELIVERY_COMPOSITE_FIELDS_WITHOUT_DAILY_SOURCES")
    claimed = _claim_clock(authority, snapshot, budget)
    need(claimed is not None and delivery._clock(claimed) <= delivery._clock(binding["evaluated_at"])
         <= delivery._clock(snapshot["authority_updated_at"]), "DELIVERY_OUTCOME_ORIGINAL_CLOCK_ORDER")
    journal = _journal(budget, binding["journal_directory"], snapshot)
    need(journal["status"] == "COMPLETE" and
         binding.get("journal_terminal_event_hash") == journal["terminal_event_hash"] and
         binding.get("journal_evidence_hash") == metrics.digest(journal["evidence"]),
         "DELIVERY_OUTCOME_JOURNAL_BINDING_MISMATCH")
    _writer_receipt(binding.get("writer_receipt"), journal)
    reader = delivery._Reader(budget.remaining(4*1024**2), budget.remaining_seconds(60))
    writer_ref = binding.get("writer_receipt_reference")
    need(type(writer_ref) is dict, "DELIVERY_OUTCOME_WRITER_REFERENCE_REQUIRED")
    writer, _ = reader.read(writer_ref["path"], expected=writer_ref, control=True)
    need(writer == binding["writer_receipt"], "DELIVERY_OUTCOME_WRITER_CHANGED")
    reader.terminal()
    budget.charge(reader.returned_bytes)
    return {"status": "NATIVE_TERMINAL_BOUND_REQUIRES_SOURCE_REVERIFICATION", "gaps": [],
        "identity": metrics.identity(snapshot), "test_fixture": journal["test_fixture"],
        "outcome_source_business_id": source_id,
        "revision_id": binding["revision_id"], "outcome_product_hash": binding["outcome_product_hash"],
        "source_reauthenticated_now": False}, (snapshot, journal, reader, source, daily_sources)


def audit_delivery(authority, business_id, *, job, journal_directory, run_path=None,
                   run_sha256=None, outcomes_path=None, outcomes_sha256=None,
                   outcome_business_id=None, reauthenticate_signal=False, allow_test_fixture=False,
                   deadline=None, manual_interventions=None, _budget=None):
    """Native read-only composition; no claim, retry, source fetch or DB init."""
    budget = _budget or _AuditBudget()
    read_start, time_start = budget.used, time.monotonic()
    before = _authority(authority, business_id)
    business = before["business"]
    need(type(reauthenticate_signal) is bool and type(allow_test_fixture) is bool,
         "DELIVERY_REAUTHENTICATION_FLAGS_INVALID")
    need(outcome_business_id is None or outcomes_path is not None,
         "DELIVERY_OUTCOME_AUTHORITY_WITHOUT_PRODUCT")
    need((run_path is None) == (run_sha256 is None), "DELIVERY_RUN_REFERENCE_PAIR_REQUIRED")
    need(manual_interventions is None or type(manual_interventions) is int and manual_interventions >= 0,
         "DELIVERY_MANUAL_INTERVENTION_COUNT")
    journal = _journal(budget, journal_directory, before)
    if run_path is not None:
        inspection = operational.Inspection(max_bytes=budget.remaining(operational.MAX_BYTES),
                                            max_seconds=budget.remaining_seconds(operational.MAX_SECONDS))
        combined, inspection = operational.combined_delivery(job, run_path, run_sha256,
            outcomes_path=outcomes_path, outcomes_sha=outcomes_sha256, deadline=deadline, inspection=inspection)
        budget.charge(inspection.used)
        workflow, population = combined["workflow"], combined["coverage"]
        for key in ("observation_date", "target_session", "strategy_id"):
            need(workflow[key] == business[key], "DELIVERY_BUSINESS_JOB_IDENTITY_CONFLICT")
    else:
        need(outcomes_path is None and outcomes_sha256 is None, "DELIVERY_OUTCOME_WITHOUT_SIGNAL")
        combined, inspection, population = None, None, None
        workflow = delivery.audit_job(job, deadline=deadline, max_bytes=budget.remaining(256*1024**2),
                                      max_seconds=budget.remaining_seconds(120))
        budget.charge(workflow["audit_returned_read_bytes"])
        for key in ("observation_date", "target_session", "strategy_id"):
            need(workflow.get(key) is None or workflow[key] == business[key],
                 "DELIVERY_BUSINESS_FAILED_JOB_IDENTITY_CONFLICT")
    final_path = str(Path(job).absolute()/"final-control.json")
    final_refs = [r for r in workflow["evidence"] if r["path"] == final_path]
    binding = before.get("receipt") or {}
    bound = (binding.get("schema_version") == VERSION + "_BINDING" and len(final_refs) == 1
        and binding.get("job_final") == final_refs[0]
        and binding.get("identity") == metrics.identity(before)
        and binding.get("journal_directory") == str(Path(journal_directory).absolute())
        and binding.get("journal_terminal_event_hash") == journal["terminal_event_hash"]
        and binding.get("journal_evidence_hash") == metrics.digest(journal["evidence"]))
    if combined is not None:
        bound = bound and binding.get("canonical_run") == combined["canonical_run_reference"]
    supervisor_reader = delivery._Reader(budget.remaining(64*1024**2), budget.remaining_seconds(60))
    supervisor = (_supervisor_audit(supervisor_reader, binding["supervisor_report"], journal)
                  if bound and binding.get("supervisor_report") else
                  {"verified": False, "status": "NOT_PROVIDED"})
    if bound and binding.get("bridge_version") == "ACTIVE_DELIVERY_BRIDGE_V1":
        terminal_ref = binding.get("native_supervisor_terminal_receipt")
        need(type(terminal_ref) is dict, "DELIVERY_BRIDGE_TERMINAL_COST_REFERENCE_REQUIRED")
        terminal, _ = supervisor_reader.read(terminal_ref["path"], expected=terminal_ref, control=True)
        need(terminal == binding.get("bridge_supervision_cost"), "DELIVERY_BRIDGE_TERMINAL_COST_CHANGED")
    supervisor_reader.terminal()
    budget.charge(supervisor_reader.returned_bytes)
    if bound and binding.get("writer_receipt"):
        _writer_receipt(binding["writer_receipt"], journal)
    # Native authority terminal and original workflow terminal are independent.
    # A successful core cannot replace an unbound/failed overall business effect.
    status = workflow["workflow_status"]
    if before["state"] != "RECORDED":
        status = "FAILED_OR_UNKNOWN" if before["state"] == "FAILED_OR_UNKNOWN" else "INCOMPLETE"
    elif not bound or journal["status"] != "COMPLETE" or not supervisor["verified"]:
        status = "UNKNOWN"
    horizon_cells = None
    if population is not None:
        all_rows = population["cohorts"]["ALL_ELIGIBLE"]
        horizon_cells = {"expected": all_rows["population"]*len(coverage.HORIZONS),
            "by_horizon": deepcopy(all_rows["horizons"]),
            "full_cartesian_product_provided": population["outcome_status"] == "AVAILABLE",
            "missing_imputation": "NONE"}
    source = {"status": "NOT_PROVIDED", "historical_feature_pit_established": False}
    outcome = {"status": "NOT_ESTABLISHED", "gaps": ["OUTCOME_NATIVE_AUTHORITY_NOT_PROVIDED"]}
    outcome_guard, source_session, source_charged = None, None, 0
    if outcome_business_id is not None:
        prior = inspection.used
        product, product_reference = inspection.snapshot(outcomes_path, outcomes_sha256)
        budget.charge(inspection.used-prior)
        outcome, outcome_guard = _outcome_authority(authority, outcome_business_id,
            population=population, product_reference=product_reference,
            signal_job=job, signal_final=final_refs[0], parent_business_id=business_id, budget=budget)
        outcome_binding = outcome_guard[0]["receipt"]
        need(product["outcome_input_id"] == outcome_binding["input_id"] and
             product["evaluated_at"] == outcome_binding["evaluated_at"],
             "DELIVERY_OUTCOME_INPUT_OR_EVALUATION_MISMATCH")
        source, source_session, source_charged = _native_sources(job=job,
            final_reference=final_refs[0], run_reference=combined["canonical_run_reference"], budget=budget,
            outcome_archive=outcome_binding["outcome_archive"], outcome_source=outcome_guard[3], product=product,
            allow_test_fixture=allow_test_fixture,
            outcome_daily_archives=outcome_binding.get("outcome_daily_archives"),
            outcome_daily_sources=outcome_guard[4],
            composite_input_manifest=outcome_binding.get("composite_input_manifest"),
            source_input_ids=outcome_binding.get("source_input_ids"))
        outcome.update(status="VERIFIED", source_reauthenticated_now=True,
            native_evaluator_recomputed=True, test_fixture=outcome["test_fixture"] or source["test_fixture"])
    elif reauthenticate_signal:
        need(combined is not None and len(final_refs) == 1, "DELIVERY_SIGNAL_REVERIFICATION_REQUIRES_ORIGINAL_RUN")
        source, source_session, source_charged = _native_sources(job=job,
            final_reference=final_refs[0], run_reference=combined["canonical_run_reference"], budget=budget,
            allow_test_fixture=allow_test_fixture)
    execution = _original_day_execution(authority, before, source, budget)
    result = {"schema_version": VERSION, "identity": metrics.identity(before),
        "authority_state": before["state"], "authority_sequence": before["sequence"],
        "authority_terminal_bound": bool(bound), "business": deepcopy(business),
        "status": status, "workflow": workflow, "population": population,
        "horizon_cells": horizon_cells, "stage_metrics": journal,
        "outcome_authority": outcome, "native_source_evidence": source,
        "original_day_execution": execution, "historical_feature_pit_established": False,
        "native_process_cleanup": supervisor,
        "writer_publication_cost_receipt": deepcopy(binding.get("writer_receipt")) if bound else None,
        "bridge_supervision_cost": deepcopy(binding.get("bridge_supervision_cost")) if bound else None,
        "authority_receipt_validation_resources": deepcopy(binding.get("authority_receipt_validation_resources")) if bound else None,
        "manual_interventions": manual_interventions,
        "manual_interventions_source": "EXPLICIT_CALLER_COUNT_NOT_OBSERVED" if manual_interventions is not None else "UNKNOWN",
        "resources": {"cumulative_spent": before["cumulative_spent"],
            "remaining_limits": before["remaining_limits"], "budget_known": before["budget_known"]},
        "source_reauthenticated_now": source["status"] == "VERIFIED", "user_delivery_status": "UNKNOWN_NOT_OBSERVED",
        "daily_v1_readiness": "UNKNOWN_NOT_EVALUATED", "market_edge": "NOT_ESTABLISHED",
        "test_fixture": allow_test_fixture or journal["test_fixture"] or source.get("test_fixture", False)
            or outcome.get("test_fixture", False) or population is not None and population["signal_coverage_type"] == "TEST_FIXTURE"}
    if inspection is not None:
        inspected_before = inspection.used
        inspection.limit = min(inspection.limit, inspection.used+budget.remaining(operational.MAX_BYTES))
        inspection.seconds = min(inspection.seconds,
            time.monotonic()-inspection.started+budget.remaining_seconds(operational.MAX_SECONDS))
        inspection.terminal()
        budget.charge(inspection.used-inspected_before)
    supervisor_before = supervisor_reader.returned_bytes
    supervisor_reader.max_bytes = min(supervisor_reader.max_bytes,
        supervisor_reader.returned_bytes+budget.remaining(64*1024**2))
    supervisor_reader.max_seconds = min(supervisor_reader.max_seconds,
        time.monotonic()-supervisor_reader.started+budget.remaining_seconds(60))
    supervisor_reader.terminal()
    budget.charge(supervisor_reader.returned_bytes-supervisor_before)
    need(_authority(authority, business_id) == before, "DELIVERY_AUTHORITY_CHANGED_DURING_AUDIT")
    # Journal evidence must remain unchanged through the whole composition.
    need(_journal(budget, journal_directory, before) == journal,
         "DELIVERY_JOURNAL_CHANGED_DURING_AUDIT")
    if outcome_guard is not None:
        snapshot, outcome_journal, outcome_reader, outcome_source, daily_sources = outcome_guard
        need(authority.inspect(outcome_business_id) == snapshot, "DELIVERY_OUTCOME_AUTHORITY_CHANGED_DURING_AUDIT")
        need(_journal(budget, snapshot["receipt"]["journal_directory"], snapshot) == outcome_journal,
             "DELIVERY_OUTCOME_JOURNAL_CHANGED_DURING_AUDIT")
        prior = outcome_reader.returned_bytes
        outcome_reader.max_bytes = prior+budget.remaining(4*1024**2)
        outcome_reader.max_seconds = time.monotonic()-outcome_reader.started+budget.remaining_seconds(60)
        outcome_reader.terminal()
        budget.charge(outcome_reader.returned_bytes-prior)
    if source_session is not None:
        source_session.max_total_read_bytes = min(source_session.max_total_read_bytes,
            source_session.read_bytes+budget.remaining(2*1024**3))
        source_session.max_seconds = min(source_session.max_seconds,
            time.monotonic()-source_session.started+budget.remaining_seconds(600))
        source_session.terminal()
        budget.charge(source_session.read_bytes-source_charged)
        source["source_read_bytes"] = source_session.read_bytes
    if outcome_guard is not None:
        for daily_source in daily_sources:
            budget.charge(0)
            need(_authority(authority, daily_source["business_id"]) == daily_source,
                 "DELIVERY_DAILY_SOURCE_AUTHORITY_CHANGED_DURING_AUDIT")
        need(_authority(authority, outcome_source["business_id"]) == outcome_source,
             "DELIVERY_OUTCOME_SOURCE_AUTHORITY_CHANGED_DURING_AUDIT")
        need(authority.inspect(outcome_business_id) == snapshot,
             "DELIVERY_OUTCOME_AUTHORITY_CHANGED_DURING_AUDIT")
    need(_authority(authority, business_id) == before, "DELIVERY_AUTHORITY_CHANGED_DURING_AUDIT")
    result["audit_resources"] = {"returned_read_bytes": budget.used-read_start,
        "wall_seconds": time.monotonic()-time_start,
        "scope": "ACTUAL_CONTROL_CARRIER_JOURNAL_AND_NATIVE_ARCHIVE_READ_RETURNS; SQL_ENGINE_IO_NOT_MEASURED"}
    result["content_hash"] = metrics.digest(result)
    return result


def _verify_calendar_window(actual, pairs):
    need(pairs[0]["target_session"] in actual, "DELIVERY_FIRST_TARGET_NOT_IN_NATIVE_CALENDAR")
    start = actual.index(pairs[0]["target_session"])
    need(start > 0 and pairs[0]["observation_date"] == actual[start-1],
         "DELIVERY_FIRST_OBSERVATION_NOT_PREVIOUS_SESSION")
    need(actual[start:start+len(pairs)] == [p["target_session"] for p in pairs],
         "DELIVERY_NONCONSECUTIVE_TRADING_DAYS")


def _label_maturity(cells):
    """Complete accounting and mature observations are different assertions."""
    gaps, totals = [], {state: 0 for state in ("MATURE", "PENDING", "UNKNOWN", "NOT_PROVIDED")}
    if not cells or cells.get("full_cartesian_product_provided") is not True:
        return {"status": "NOT_ESTABLISHED", "gaps": ["LABEL_CARTESIAN_PRODUCT_NOT_PROVIDED"],
                "counts": None, "expected": cells.get("expected") if cells else None}
    rows, expected = cells.get("by_horizon"), cells.get("expected")
    valid = (type(expected) is int and expected >= 0 and expected % len(coverage.HORIZONS) == 0
             and type(rows) is list and all(type(r) is dict for r in rows)
             and [r.get("horizon") for r in rows] == list(coverage.HORIZONS))
    if valid:
        for row in rows:
            states = row.get("states")
            if (type(states) is not dict or set(states) != set(totals)
                or any(type(v) is not int or v < 0 for v in states.values())
                or sum(states.values()) != expected // len(coverage.HORIZONS)):
                valid = False
                break
            for state, count in states.items(): totals[state] += count
    if not valid:
        gaps.append("LABEL_STATE_COUNTS_INCOMPLETE")
    else:
        if totals["PENDING"]: gaps.append("LABELS_PENDING_NATURAL_MATURITY")
        if totals["UNKNOWN"]: gaps.append("LABELS_UNKNOWN")
        if totals["NOT_PROVIDED"]: gaps.append("LABELS_NOT_PROVIDED")
        if totals["MATURE"] != expected: gaps.append("ALL_LABELS_MATURE_NOT_PROVEN")
    return {"status": "COMPLETE" if not gaps else "NOT_ESTABLISHED", "gaps": gaps,
            "counts": totals if valid else None, "expected": expected}


def audit_sequence(authority, protocol, requests, *, protocol_business_id=None,
                   calendar_cache=None, calendar_record=None,
                   max_read_bytes=2*1024**3, max_seconds=600):
    """Reaudit each requested native day; missing dates stay explicit NOT_RUN."""
    pairs = _protocol(protocol)
    scope = protocol.get("acceptance_scope", "LEGACY_STRICT_PROSPECTIVE_CAPTURED")
    budget = _AuditBudget(max_read_bytes, max_seconds)
    need(type(requests) is list and len(requests) <= len(pairs), "DELIVERY_REQUEST_SET_INVALID")
    rows = {}
    for request in requests:
        need(type(request) is dict, "DELIVERY_NATIVE_REQUEST_DESCRIPTOR_REQUIRED")
        need("_budget" not in request, "DELIVERY_REQUEST_CANNOT_REPLACE_BUDGET")
        row = audit_delivery(authority, **request, _budget=budget)
        day = row["business"]["target_session"]
        need(day not in rows, "DELIVERY_DUPLICATE_DAY")
        rows[day] = row
    expected = {p["target_session"]: p for p in pairs}
    need(set(rows) <= set(expected), "DELIVERY_OUTSIDE_FROZEN_WINDOW")
    calendar_verified = False
    if calendar_cache is not None or calendar_record is not None:
        from stockfish_core.yaogu.premarket_capture_cache_v1 import CaptureCache
        from scripts import codex_current_calendar_v2 as calendar
        need(type(calendar_cache) is CaptureCache and calendar_record is not None,
             "DELIVERY_NATIVE_CALENDAR_CACHE_REQUIRED")
        calendar.validate_calendar(calendar_cache, calendar_record, now=datetime.now(timezone.utc))
        result = calendar_cache.verify(calendar_record)
        need(metrics.digest(result["rows"]) == protocol["calendar_rows_hash"],
             "DELIVERY_CALENDAR_CHANGED")
        actual = _sessions(result["rows"])
        _verify_calendar_window(actual, pairs)
        calendar_verified = True
        budget.charge(0)
    observations, durations = [], []
    for pair in pairs:
        day = pair["target_session"]
        row = rows.get(day)
        if row is None:
            observations.append({**pair, "status": "NOT_RUN", "acceptance_gaps": ["NO_NATIVE_DAY_EVIDENCE"],
                "operational_gaps": ["NO_NATIVE_DAY_EVIDENCE"], "label_maturity": _label_maturity(None)})
            continue
        need(row["business"]["observation_date"] == pair["observation_date"] and
             row["business"]["strategy_id"] == protocol["strategy_id"], "DELIVERY_PROTOCOL_BUSINESS_MISMATCH")
        gaps = []
        if row["status"] != "COMPLETE": gaps.append("WORKFLOW_NOT_COMPLETE")
        if row["test_fixture"]: gaps.append("TEST_FIXTURE_NOT_REAL_DAY")
        pop = row["population"]
        if scope != OPERATIONAL_SCOPE and (pop is None or pop["signal_coverage_type"] != "PROSPECTIVE_CAPTURED"):
            gaps.append("REAL_PROSPECTIVE_SIGNAL_NOT_PROVEN")
        if protocol["schema_version"] == PROTOCOL_V2:
            executed = row.get("original_day_execution", {})
            source = row.get("native_source_evidence", {})
            if source.get("status") != "VERIFIED":
                gaps.append("NATIVE_SIGNAL_SOURCE_NOT_REVERIFIED")
            if executed.get("status") != "VERIFIED":
                gaps.append("ORIGINAL_DAY_NATIVE_EXECUTION_NOT_PROVEN")
            completed = executed.get("authority_completed_at")
            if completed is None or delivery._clock(completed) > delivery._clock(pair["deadline"]):
                gaps.append("TIMELY_NATIVE_BUSINESS_COMPLETION_NOT_PROVEN")
            if scope == STRICT_PIT and row.get("historical_feature_pit_established") is not True:
                gaps.append("HISTORICAL_FEATURE_PIT_NOT_ESTABLISHED")
            if pop is None or pop["signal_coverage_type"] not in {"PROSPECTIVE_CAPTURED", "RECONSTRUCTED_HISTORICAL"}:
                gaps.append("REAL_SIGNAL_COVERAGE_NOT_ESTABLISHED")
        if pop is None or not pop["qualification"]["saved_qualification_has_no_reported_gaps"]:
            gaps.append("QUALIFICATION_GAPS")
        if not row["horizon_cells"] or not row["horizon_cells"]["full_cartesian_product_provided"]:
            gaps.append("FIVE_HORIZON_CARTESIAN_PRODUCT_NOT_PROVIDED")
        sealed = row["workflow"].get("timing", {}).get("final_sealed_at")
        if sealed is None or delivery._clock(sealed) > delivery._clock(pair["deadline"]):
            gaps.append("TIMELY_FINAL_SEAL_NOT_PROVEN")
        if not row["resources"]["budget_known"]: gaps.append("RESOURCE_COST_UNKNOWN")
        if row["stage_metrics"]["status"] != "COMPLETE": gaps.append("DURABLE_METRICS_INCOMPLETE")
        # The native workflow validator owns process cleanup assertions. A
        # process-local journal alone never proves descendants were reaped.
        if row["native_process_cleanup"]["verified"] is not True:
            gaps.append("NATIVE_TREE_CLEANUP_NOT_ATTESTED")
        duration = row["native_process_cleanup"].get("utc_elapsed_seconds")
        if duration is not None: durations.append(duration)
        maturity = _label_maturity(row["horizon_cells"])
        outcome = row.get("outcome_authority", {})
        outcome_gaps = ([] if outcome.get("status") == "VERIFIED" and
            outcome.get("source_reauthenticated_now") is True and
            outcome.get("native_evaluator_recomputed") is True and
            outcome.get("test_fixture") is False else ["OUTCOME_NATIVE_AUTHORITY_NOT_PROVEN"])
        observations.append({**pair, "status": row["status"], "identity": row["identity"],
            "content_hash": row["content_hash"], "operational_gaps": gaps,
            "label_maturity": maturity, "outcome_authority": deepcopy(outcome),
            "original_signal_coverage_type": pop["signal_coverage_type"] if pop else None,
            "historical_feature_pit_established": False,
            "acceptance_gaps": gaps + maturity["gaps"] + outcome_gaps,
            "manual_interventions": row["manual_interventions"], "resources": row["resources"]})
    global_gaps = []
    if protocol["design_role"] != "PROSPECTIVE": global_gaps.append("RETROSPECTIVE_PROTOCOL")
    if not calendar_verified: global_gaps.append("NATIVE_CALENDAR_NOT_REVERIFIED")
    if protocol_business_id is None:
        global_gaps.append("NATIVE_PROSPECTIVE_FREEZE_NOT_PROVIDED")
    else:
        from scripts.business_authority_v1 import Authority
        need(type(authority) is Authority, "DELIVERY_NATIVE_BUSINESS_AUTHORITY_REQUIRED")
        frozen = authority.inspect(protocol_business_id)
        need(frozen is not None and frozen["state"] == "RECORDED" and
             frozen["business"]["operation"] == "MAINTENANCE" and
             frozen["business"]["subject"] == "DAILY_DELIVERY_PROTOCOL" and
             frozen["business"]["parameters"] == {"protocol_hash": protocol["protocol_hash"]} and
             (frozen.get("receipt") or {}).get("protocol_hash") == protocol["protocol_hash"],
             "DELIVERY_NATIVE_FREEZE_BINDING_INVALID")
        frozen_clock = frozen.get("authority_updated_at")
        first_close = datetime.combine(date.fromisoformat(pairs[0]["observation_date"]), wall_time(15), SH)
        if frozen_clock is None or delivery._clock(frozen_clock) >= first_close:
            global_gaps.append("NATIVE_PROSPECTIVE_FREEZE_TIME_NOT_PROVEN")
    accepted = sum(not row["acceptance_gaps"] for row in observations)
    operational_accepted = sum(not row["operational_gaps"] for row in observations)
    mature = sum(row["label_maturity"]["status"] == "COMPLETE" for row in observations)
    budget.charge(0)
    return {"schema_version": protocol["schema_version"] + "_AUDIT", "protocol_hash": protocol["protocol_hash"],
        "acceptance_scope": scope, "historical_feature_pit_established": False,
        "status": "REAL_DAILY_ACCEPTANCE_ESTABLISHED" if not global_gaps and accepted == len(pairs) else "NOT_ESTABLISHED",
        "expected_sessions": len(pairs), "audited_sessions": len(rows), "accepted_sessions": accepted,
        "completion_rate": {"numerator": accepted, "denominator": len(pairs)},
        "operational_accepted_sessions": operational_accepted,
        "operational_completion_rate": {"numerator": operational_accepted, "denominator": len(pairs)},
        "operational_acceptance_status": "ESTABLISHED" if not global_gaps and operational_accepted == len(pairs) else "NOT_ESTABLISHED",
        "label_maturity": {"complete_sessions": mature, "expected_sessions": len(pairs),
            "status": "COMPLETE" if mature == len(pairs) else "NOT_ESTABLISHED",
            "policy": "ALL_SECURITY_BY_HORIZON_LABELS_MATURE; FINAL_H10_REQUIRES_ADDITIONAL_REAL_SESSIONS"},
        "workflow_status_counts": dict(Counter(r["status"] for r in observations)),
        "latency": {"known_count": len(durations), "missing_count": len(pairs)-len(durations),
            "median_seconds": statistics.median(durations) if durations else None,
            "p95_nearest_rank_seconds": sorted(durations)[math.ceil(.95*len(durations))-1] if durations else None,
            "sample_max_seconds": max(durations) if durations else None},
        "audit_resources": {"returned_read_bytes": budget.used,
            "wall_seconds": time.monotonic()-budget.started, "limits": {"bytes": budget.limit, "seconds": budget.seconds},
            "native_calendar_cost_scope": "ORIGINAL_CAPTURE_CACHE_BUDGET; NOT_COUNTED_AS_CONTROL_READ_BYTES"},
        "global_gaps": global_gaps, "days": observations,
        "market_edge": "NOT_ESTABLISHED", "investment_validation": "NOT_CLAIMED",
        "collection_started": False, "retry_authorized": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--authority-db", type=Path, required=True)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--business-id", required=True)
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--run", type=Path)
    parser.add_argument("--run-sha256")
    parser.add_argument("--outcomes", type=Path)
    parser.add_argument("--outcomes-sha256")
    parser.add_argument("--outcome-business-id")
    parser.add_argument("--reauthenticate-signal", action="store_true")
    parser.add_argument("--allow-test-fixture", action="store_true")
    parser.add_argument("--deadline")
    args = parser.parse_args(argv)
    try:
        from scripts.business_authority_v1 import Authority
        result = audit_delivery(Authority(args.authority_db, args.workspace_id), args.business_id,
            job=args.job, journal_directory=args.journal, run_path=args.run, run_sha256=args.run_sha256,
            outcomes_path=args.outcomes, outcomes_sha256=args.outcomes_sha256,
            outcome_business_id=args.outcome_business_id, reauthenticate_signal=args.reauthenticate_signal,
            allow_test_fixture=args.allow_test_fixture, deadline=args.deadline)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["status"] == "COMPLETE" else 2
    except (ValueError, OSError) as error:
        print(json.dumps({"status": "REJECTED", "error_type": type(error).__name__}))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
