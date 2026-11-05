"""TEST_FIXTURE: maturity is descriptive until original outcome authority binds it."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import socket

import pytest

from scripts import daily_delivery_contract_v1 as contract
from scripts import business_authority_v1 as business_authority
from scripts import durable_stage_metrics_v1 as metrics
from tests.unit.test_business_authority_v1 import authority
from tests.unit.test_daily_delivery_contract_v1 import protocol, calendar_rows


def _day(plan, pair, *, mature="MATURE", coverage="PROSPECTIVE_CAPTURED", source=False):
    row = {"business": {**pair, "strategy_id": plan["strategy_id"]}, "status": "COMPLETE",
        "test_fixture": False, "population": {"signal_coverage_type": coverage,
            "qualification": {"saved_qualification_has_no_reported_gaps": True}},
        "horizon_cells": {"full_cartesian_product_provided": True, "expected": 15,
            "by_horizon": [{"horizon": h, "states": {s: 3 if s == mature else 0 for s in
                ("MATURE", "PENDING", "UNKNOWN", "NOT_PROVIDED")}} for h in (1, 2, 3, 5, 10)]},
        "workflow": {"timing": {"final_sealed_at": pair["deadline"]}},
        "resources": {"budget_known": True}, "stage_metrics": {"status": "COMPLETE"},
        "native_process_cleanup": {"verified": True}, "identity": {"TEST_FIXTURE": True},
        "content_hash": "TEST_FIXTURE", "manual_interventions": None,
        "historical_feature_pit_established": False}
    if source:
        row.update(native_source_evidence={"status": "VERIFIED"},
            original_day_execution={"status": "VERIFIED", "authority_completed_at": pair["deadline"]},
            outcome_authority={"status": "VERIFIED", "source_reauthenticated_now": True,
                "native_evaluator_recomputed": True, "test_fixture": False})
    return row


def _sequence(monkeypatch, plan, **kwargs):
    def one(authority, *, pair, _budget):
        return _day(plan, pair, **kwargs)
    monkeypatch.setattr(contract, "audit_delivery", one)
    return contract.audit_sequence(None, plan, [{"pair": p} for p in plan["expected_sessions"]])


def test_saved_mature_counts_without_source_authority_do_not_enter_acceptance(monkeypatch):
    # Isolated acceptance predicate, not fabricated native days. Missing global
    # calendar/freeze still prevents total establishment even in positive cases.
    report = _sequence(monkeypatch, protocol())
    assert report["label_maturity"]["complete_sessions"] == 20
    assert report["operational_accepted_sessions"] == 20
    assert report["accepted_sessions"] == 0
    assert all(day["acceptance_gaps"] == ["OUTCOME_NATIVE_AUTHORITY_NOT_PROVEN"] for day in report["days"])
    assert report["status"] == "NOT_ESTABLISHED"


def _v2(scope):
    old = protocol()
    return contract.plan_protocol(calendar_rows(), first_target_session=old["expected_sessions"][0]["target_session"],
        strategy_id=old["strategy_id"], calendar_reference=old["calendar_reference"], acceptance_scope=scope)


def test_v1_remains_strict_and_new_scope_is_explicit_frozen_design(monkeypatch):
    old = protocol()
    assert old["schema_version"] == contract.PROTOCOL and "acceptance_scope" not in old
    report = _sequence(monkeypatch, old, coverage="RECONSTRUCTED_HISTORICAL", source=True)
    assert report["accepted_sessions"] == 0
    assert all("REAL_PROSPECTIVE_SIGNAL_NOT_PROVEN" in day["operational_gaps"] for day in report["days"])
    new = _v2(contract.OPERATIONAL_SCOPE)
    report = _sequence(monkeypatch, new, coverage="RECONSTRUCTED_HISTORICAL", source=True)
    assert report["accepted_sessions"] == report["operational_accepted_sessions"] == 20
    assert report["status"] == "NOT_ESTABLISHED"  # this predicate fixture has no calendar/freeze
    assert report["historical_feature_pit_established"] is False
    assert report["market_edge"] == "NOT_ESTABLISHED" and report["investment_validation"] == "NOT_CLAIMED"
    assert all(day["original_signal_coverage_type"] == "RECONSTRUCTED_HISTORICAL" for day in report["days"])
    changed = deepcopy(new); changed["acceptance_scope"] = contract.STRICT_PIT
    with pytest.raises(ValueError, match="PROTOCOL_CHANGED"):
        contract._protocol(changed)
    changed["protocol_hash"] = metrics.digest({k: v for k, v in changed.items() if k != "protocol_hash"})
    with pytest.raises(ValueError, match="V2_SCOPE_WEAKENED"):
        contract._protocol(changed)


@pytest.mark.parametrize("state", ["UNKNOWN", "PENDING", "NOT_PROVIDED"])
def test_new_operational_scope_cannot_waive_natural_label_maturity(monkeypatch, state):
    report = _sequence(monkeypatch, _v2(contract.OPERATIONAL_SCOPE), mature=state,
                       coverage="RECONSTRUCTED_HISTORICAL", source=True)
    assert report["operational_accepted_sessions"] == 20
    assert report["accepted_sessions"] == 0 and report["label_maturity"]["complete_sessions"] == 0


def test_strict_v2_does_not_claim_historical_pit_from_a_coverage_string(monkeypatch):
    report = _sequence(monkeypatch, _v2(contract.STRICT_PIT), source=True)
    assert report["accepted_sessions"] == 0
    assert all("HISTORICAL_FEATURE_PIT_NOT_ESTABLISHED" in day["operational_gaps"] for day in report["days"])


@pytest.mark.parametrize("missing", ["source", "execution", "late", "qualification", "cleanup", "budget", "cartesian"])
def test_operational_scope_retains_each_real_delivery_gate(monkeypatch, missing):
    plan = _v2(contract.OPERATIONAL_SCOPE)
    def one(authority, *, pair, _budget):
        row = _day(plan, pair, source=True, coverage="RECONSTRUCTED_HISTORICAL")
        if missing == "source": row["native_source_evidence"]["status"] = "NOT_PROVIDED"
        elif missing == "execution": row["original_day_execution"]["status"] = "NOT_ESTABLISHED"
        elif missing == "late": row["original_day_execution"]["authority_completed_at"] = pair["deadline"].replace("09:00", "09:01")
        elif missing == "qualification": row["population"]["qualification"]["saved_qualification_has_no_reported_gaps"] = False
        elif missing == "cleanup": row["native_process_cleanup"]["verified"] = False
        elif missing == "budget": row["resources"]["budget_known"] = False
        else: row["horizon_cells"]["full_cartesian_product_provided"] = False
        return row
    monkeypatch.setattr(contract, "audit_delivery", one)
    result = contract.audit_sequence(None, plan, [{"pair": p} for p in plan["expected_sessions"]])
    assert result["accepted_sessions"] == result["operational_accepted_sessions"] == 0


def test_original_attempt_time_is_read_from_authority_not_report(authority, monkeypatch):
    clock = ["2026-11-04T18:00:00+08:00"]
    monkeypatch.setattr(business_authority, "stamp", lambda: clock[0])
    claim = authority.claim({"operation": "PREMARKET", "observation_date": "2026-11-04",
        "target_session": "2026-11-05", "strategy_id": "SHORT_HORIZON_STRENGTH_R2"}, epoch_id="TEST_FIXTURE_epoch")
    clock[0] = "2026-11-04T18:04:00+08:00"
    ended = authority.finish(claim, success=True, receipt={"TEST_FIXTURE": True})
    source = {"status": "VERIFIED", "signal_archive": {"coverage_type": "RECONSTRUCTED_HISTORICAL",
        "original_decision_cutoff": "2026-11-04T18:01:00+08:00",
        "original_generated_at": "2026-11-04T18:02:00+08:00", "original_final_sealed_at": "2026-11-04T18:03:00+08:00"}}
    result = contract._original_day_execution(authority, ended, source, contract._AuditBudget())
    assert result["status"] == "VERIFIED" and result["claim_started_at"] == "2026-11-04T18:00:00+08:00"
    assert result["historical_feature_pit_established"] is False
    source["signal_archive"]["original_decision_cutoff"] = "2026-11-04T17:59:59+08:00"
    assert contract._original_day_execution(authority, ended, source, contract._AuditBudget())["status"] == "NOT_ESTABLISHED"


def test_native_original_archives_and_evaluator_are_required(tmp_path, monkeypatch):
    from stockfish_core.yaogu import short_horizon_archive_bridge_v1 as bridge
    from stockfish_core.yaogu import short_horizon_outcomes_v1 as outcomes
    from tests.support.short_horizon_archive_fixture import fixture_archive
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **kw: pytest.fail("TEST_FIXTURE_NO_NETWORK"))
    signal = fixture_archive(tmp_path/"TEST_FIXTURE_signal", observation_date="2026-10-15")
    future = fixture_archive(tmp_path/"TEST_FIXTURE_future", observation_date="2026-10-15",
        seen_at="2026-10-15T09:02:00+00:00")
    session = bridge.ArchiveVerificationSession()
    cap = bridge.authenticate_archive(future["job"], expected_final_sha256=future["final_sha256"],
        session=session, allow_test_fixture=True, retain_input=True)
    product = outcomes.evaluate(signal["run"], bridge._state(cap)["retained_input"],
        datetime.now(timezone.utc), horizons=list(contract.coverage.HORIZONS))["outcomes"]
    kwargs = dict(job=signal["job"], final_reference={"sha256": signal["final_sha256"]},
        run_reference=signal["artifacts"]["canonical-run.json.gz"],
        outcome_archive={"job": str(future["job"]), "final_sha256": future["final_sha256"]},
        product=product, allow_test_fixture=True)
    result, native, charged = contract._native_sources(**kwargs, budget=contract._AuditBudget())
    native.terminal()
    assert result["status"] == "VERIFIED" and result["outcome_recomputed"] is True
    assert result["test_fixture"] is True and result["historical_feature_pit_established"] is False
    assert result["vendor_authentication_established"] is False and charged > 0
    fake_source = {"receipt": {"job_final": {"path": str(future["job"]/"final-control.json"),
        "sha256": future["final_sha256"]}, "canonical_run": future["artifacts"]["canonical-run.json.gz"]},
        "business": {"observation_date": future["run"]["observation_date"],
                     "target_session": future["run"]["target_session"], "strategy_id": future["run"]["strategy_id"]}}
    # A matching SHA without the original physical reference cannot substitute
    # for the registered parent terminal. This dictionary grants no authority.
    with pytest.raises(ValueError, match="SOURCE_ORIGINAL_REFERENCE_MISMATCH"):
        contract._native_sources(**kwargs, outcome_source=fake_source, budget=contract._AuditBudget())
    changed = deepcopy(product); changed["source_authority"] = True
    with pytest.raises(ValueError, match="NATIVE_OUTCOME_RECOMPUTATION_MISMATCH"):
        contract._native_sources(**{**kwargs, "product": changed}, budget=contract._AuditBudget())
    shortened = fixture_archive(tmp_path/"TEST_FIXTURE_rolling_truncated_calendar", observation_date="2026-10-30")
    shifted = bridge.authenticate_archive(shortened["job"], expected_final_sha256=shortened["final_sha256"],
        session=session, allow_test_fixture=True, retain_input=True)
    shifted_product = outcomes.evaluate(signal["run"], bridge._state(shifted)["retained_input"],
        datetime.now(timezone.utc), horizons=list(contract.coverage.HORIZONS))["outcomes"]
    with pytest.raises(ValueError, match="SIGNAL_CALENDAR_PREFIX_CONFLICT"):
        contract._native_sources(**{**kwargs, "product": shifted_product,
            "outcome_archive": {"job": str(shortened["job"]), "final_sha256": shortened["final_sha256"]}},
            budget=contract._AuditBudget())
    with pytest.raises(ValueError, match="PROSPECTIVE_CAPTURED_OR_EXPLICIT_FIXTURE_REQUIRED"):
        contract._native_sources(**{**kwargs, "allow_test_fixture": False}, budget=contract._AuditBudget())


@pytest.mark.parametrize("mutation", [None, "wrong_run", "wrong_product", "failed", "unknown_budget",
    "missing_source_business", "unknown_source_business", "wrong_source_reference",
    "composite", "composite_ids_changed", "composite_no_manifest", "composite_unknown_day",
    "composite_wrong_day_ref", "composite_duplicate_day", "composite_includes_signal"])
def test_native_terminal_receipt_is_bound_but_does_not_replace_source_reverification(authority, tmp_path, mutation):
    product_hash = "a"*64
    population = {"run_id": "TEST_FIXTURE_run", "outcome_revision_id": "TEST_FIXTURE_revision",
                  "outcome_product_hash": product_hash}
    claim = authority.claim({"operation": "OUTCOME", "run_ids": [population["run_id"]],
        "input_ids": ["TEST_FIXTURE_outcome_input"]}, epoch_id="TEST_FIXTURE_epoch")
    journal_path = tmp_path/"TEST_FIXTURE_outcome_journal"
    journal = metrics.StageJournal(journal_path, claim, test_fixture=True)
    with journal.stage("original_evaluation"):
        pass
    writer = journal.finish("COMPLETE")
    writer_path = tmp_path/"TEST_FIXTURE_writer.json"
    writer_path.write_bytes(metrics.encoded(writer))
    reader = contract.delivery._Reader(4*1024**2, 10)
    _, writer_ref = reader.read(writer_path, control=True)
    audit = metrics.audit_journal(journal_path, expected_identity=claim)
    signal = tmp_path/"TEST_FIXTURE_signal_job"; signal.mkdir()
    final_ref = {"sha256": "b"*64}
    product = {"path": str(tmp_path/"TEST_FIXTURE_product.json"), "sha256": "c"*64}
    source_claim = authority.claim({"operation": "PREMARKET", "strategy_id": "SHORT_HORIZON_STRENGTH_R2",
        "observation_date": "2026-10-15", "target_session": "2026-10-16"}, epoch_id="TEST_FIXTURE_epoch")
    source_final = {"path": str(signal/"final-control.json"), **final_ref}
    if mutation == "wrong_source_reference": source_final["sha256"] = "f"*64
    # This minimal terminal is only an oracle for binding validation. It cannot
    # pass _native_sources, which still requires original physical archives.
    authority.finish(source_claim, success=True, receipt={"schema_version": contract.VERSION+"_BINDING",
        "identity": metrics.identity(source_claim), "job_final": source_final,
        "canonical_run": {"path": str(signal/"canonical-run.json.gz"), "sha256": "e"*64}})
    binding = {"schema_version": contract.OUTCOME_BINDING, "identity": metrics.identity(claim),
        "execution_id": claim["attempt_id"], "native_execution_completed": True,
        "parent_business_id": "TEST_FIXTURE_parent", "source_authority_restored": False,
        "historical_pit_verified": False, "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "signal_archive": {"job": str(signal), "final_sha256": final_ref["sha256"]},
        "outcome_source_business_id": source_claim["business_id"],
        "outcome_archive": {"job": str(signal), "final_sha256": final_ref["sha256"]},
        "product": product, "run_id": population["run_id"], "revision_id": population["outcome_revision_id"],
        "input_id": "TEST_FIXTURE_outcome_input", "outcome_product_hash": product_hash,
        "journal_directory": str(journal_path), "journal_terminal_event_hash": audit["terminal_event_hash"],
        "journal_evidence_hash": metrics.digest(audit["evidence"]), "writer_receipt": writer,
        "writer_receipt_reference": writer_ref}
    if mutation == "wrong_run": binding["run_id"] = "TEST_FIXTURE_other"
    if mutation == "wrong_product": binding["product"] = {**product, "sha256": "d"*64}
    if mutation == "missing_source_business": binding.pop("outcome_source_business_id")
    if mutation == "unknown_source_business": binding["outcome_source_business_id"] = "business_"+"f"*64
    if mutation and mutation.startswith("composite"):
        # A typed minimal native terminal can bind claimed input identities,
        # but cannot replace live archive capability/compose re-verification.
        binding.update(input_id="market_"+"d"*64, source_input_ids=["TEST_FIXTURE_outcome_input"],
            composite_input_manifest={"TEST_FIXTURE": "ONLY_BINDING_NOT_SOURCE_AUTHORITY"},
            outcome_daily_archives=[{"business_id":source_claim["business_id"], "job":str(signal),
                "final_sha256":final_ref["sha256"], "input_id":"TEST_FIXTURE_outcome_input"}])
        if mutation == "composite_ids_changed": binding["source_input_ids"] = ["TEST_FIXTURE_other"]
        if mutation == "composite_no_manifest": binding.pop("composite_input_manifest")
        if mutation == "composite_unknown_day": binding["outcome_daily_archives"][0]["business_id"] = "business_"+"e"*64
        if mutation == "composite_wrong_day_ref": binding["outcome_daily_archives"][0]["final_sha256"] = "e"*64
        if mutation == "composite_duplicate_day": binding["outcome_daily_archives"] *= 2
        if mutation == "composite_includes_signal": binding["outcome_daily_archives"][0]["business_id"] = "TEST_FIXTURE_parent"
    successful = mutation not in {"failed", "unknown_budget"}
    authority.finish(claim, success=successful, receipt=binding, budget_known=mutation != "unknown_budget")
    kwargs = dict(population=population, product_reference=product, signal_job=signal,
                  signal_final=final_ref, parent_business_id="TEST_FIXTURE_parent", budget=contract._AuditBudget())
    if mutation and mutation != "composite":
        expected = ("BUSINESS_NOT_REGISTERED" if mutation in {"unknown_source_business", "composite_unknown_day"}
                    else "OUTCOME|COMPOSITE|DAILY_SOURCE|DAILY_ARCHIVE" if mutation.startswith("composite") else "OUTCOME_")
        with pytest.raises(ValueError, match=expected):
            contract._outcome_authority(authority, claim["business_id"], **kwargs)
    else:
        result, guard = contract._outcome_authority(authority, claim["business_id"], **kwargs)
        assert result["status"] == "NATIVE_TERMINAL_BOUND_REQUIRES_SOURCE_REVERIFICATION"
        assert result["source_reauthenticated_now"] is False and result["test_fixture"] is True
        assert guard[0]["business_id"] == claim["business_id"]
        if mutation == "composite":
            assert [day["business_id"] for day in guard[4]] == [source_claim["business_id"]]


def test_composite_audit_reopens_native_sources_and_rejects_manifest_or_input_self_claims(tmp_path, monkeypatch):
    """Real native originals; authority-binding predicates are tested separately."""
    from copy import deepcopy
    from scripts import active_outcome_input_v1 as composition
    from stockfish_core.yaogu import short_horizon_archive_bridge_v1 as bridge
    from stockfish_core.yaogu import short_horizon_outcomes_v1 as outcomes
    from stockfish_core.yaogu import premarket_workflow_v1 as workflow
    from tests.support.short_horizon_archive_fixture import fixture_archive
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **kw: pytest.fail("TEST_FIXTURE_NO_NETWORK"))
    signal = fixture_archive(tmp_path/"TEST_FIXTURE_composite_signal", observation_date="2026-10-15")
    daily = fixture_archive(tmp_path/"TEST_FIXTURE_composite_daily", observation_date="2026-10-16")
    session = bridge.ArchiveVerificationSession()
    signal_cap = bridge.authenticate_archive(signal["job"], expected_final_sha256=signal["final_sha256"],
        session=session, allow_test_fixture=True, retain_input=True)
    daily_cap = bridge.authenticate_archive(daily["job"], expected_final_sha256=daily["final_sha256"],
        session=session, allow_test_fixture=True, retain_input=True)
    bundle, manifest = composition.compose(signal["run"], signal_cap, [daily_cap], session)
    product = outcomes.evaluate(signal["run"], bundle, datetime.now(timezone.utc),
        horizons=list(contract.coverage.HORIZONS))["outcomes"]
    session.terminal()
    daily_id = daily_cap.audit()["input_id"]
    daily_source = {"business_id":"TEST_FIXTURE_daily", "business":{
        "observation_date":daily["run"]["observation_date"], "target_session":daily["run"]["target_session"],
        "strategy_id":daily["run"]["strategy_id"]}, "receipt":{
        "job_final":workflow._ref(daily["job"]/"final-control.json"),
        "canonical_run":daily["artifacts"]["canonical-run.json.gz"]}}
    kwargs = dict(job=signal["job"], final_reference={"sha256":signal["final_sha256"]},
        run_reference=signal["artifacts"]["canonical-run.json.gz"],
        outcome_archive={"job":str(daily["job"]), "final_sha256":daily["final_sha256"]},
        outcome_source=daily_source, product=product, allow_test_fixture=True,
        outcome_daily_archives=[{"business_id":"TEST_FIXTURE_daily", "job":str(daily["job"]),
            "final_sha256":daily["final_sha256"], "input_id":daily_id}],
        outcome_daily_sources=[daily_source], composite_input_manifest=manifest, source_input_ids=[daily_id])
    result, guard, charged = contract._native_sources(**kwargs, budget=contract._AuditBudget())
    assert result["outcome_recomputed"] is True and result["composite_input_manifest"] == manifest
    assert result["source_input_ids"] == [daily_id] and result["test_fixture"] is True
    assert product["counts"] == {"MATURE":2,"UNKNOWN":8}
    assert charged > 0 and result["historical_feature_pit_established"] is False
    guard.terminal()
    changed = deepcopy(manifest); changed["source_authority_restored"] = True
    with pytest.raises(ValueError, match="COMPOSITE_MANIFEST_RECOMPUTATION_MISMATCH"):
        contract._native_sources(**{**kwargs,"composite_input_manifest":changed}, budget=contract._AuditBudget())
    with pytest.raises(ValueError, match="COMPOSITE_SOURCE_INPUT_IDS_MISMATCH"):
        contract._native_sources(**{**kwargs,"source_input_ids":sorted([daily_id,signal_cap.audit()["input_id"]])},
            budget=contract._AuditBudget())
    changed_daily = deepcopy(kwargs["outcome_daily_archives"]); changed_daily[0]["input_id"] = "market_"+"f"*64
    with pytest.raises(ValueError, match="DAILY_SOURCE_INPUT_ID_MISMATCH"):
        contract._native_sources(**{**kwargs,"outcome_daily_archives":changed_daily}, budget=contract._AuditBudget())
