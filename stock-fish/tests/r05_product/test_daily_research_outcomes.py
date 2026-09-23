"""Isolated TEST_FIXTURE proofs for Phase 1; no market or investment claims."""
from copy import deepcopy
from datetime import datetime, time, timedelta
from decimal import Decimal

import pytest

from stockfish_core.storage import ImmutableConflict, SQLiteStore
from stockfish_core.yaogu.contracts import digest
from stockfish_core.yaogu.daily_research_outcomes import (
    HORIZONS, POLICY_ID, evaluate_daily_research_outcomes,
    select_daily_outcome_revisions, snapshot_available_as_of, summarize_daily_research_outcomes,
)
from stockfish_core.yaogu.history_artifacts import compact_input
from tests.yaogu.fixtures import TZ, make_bundle


def case(count=2):
    bundle, cutoff = make_bundle(count, session_count=32)
    day = bundle.sessions[5]
    items = []
    for index in range(count):
        iid = f"TEST{index:03d}"
        bar = next(bar for bar in bundle.bars if bar.instrument_id == iid and bar.session == day)
        items.append({"run_id": "TEST_FIXTURE_RUN", "instrument_id": iid,
                      "decision_status": "FOCUS" if index == 0 else "WATCH", "primary_setup": "START",
                      "comparable_close": str(bar.comparable_close), "comparable_basis": bar.comparable_basis})
    snapshot = {"snapshot_version": "DAILY_RECOMMENDATION_SNAPSHOT_V1", "source_run_hash": "TEST_FIXTURE_SOURCE",
                "identity": {"run_id": "TEST_FIXTURE_RUN", "observation_date": str(day),
                             "target_session": str(bundle.sessions[6]), "strategy_version": "TEST_FIXTURE_STRATEGY_V1",
                             "config_version": "TEST_FIXTURE_CONFIG_V1", "data_cutoff": datetime.combine(day, time(20), TZ).isoformat(),
                             "generated_at": datetime.combine(day, time(20, 30), TZ).isoformat()},
                "validation_epoch_id": "TEST_FIXTURE_EPOCH", "coverage_type": "TEST_FIXTURE", "items": items}
    return snapshot, bundle, cutoff


def edit_bar(bundle, iid, session, **updates):
    return bundle.model_copy(update={"bars": tuple(bar.model_copy(update=updates) if bar.instrument_id == iid and bar.session == session else bar for bar in bundle.bars)})


def security_interval(bundle, iid, session, **updates):
    original = next(row for row in bundle.universe.securities if row.instrument_id == iid)
    end = bundle.sessions[bundle.sessions.index(session) + 1]
    rows = tuple(row for row in bundle.universe.securities if row.instrument_id != iid) + (
        original.model_copy(update={"effective_to": session}),
        original.model_copy(update={"effective_from": session, "effective_to": end, **updates}),
        original.model_copy(update={"effective_from": end}),
    )
    return bundle.model_copy(update={"universe": bundle.universe.model_copy(update={"securities": rows})})


def test_all_twenty_sessions_freeze_reference_and_explain_prices_without_mutation():
    snapshot, bundle, cutoff = case()
    before = digest(snapshot), digest(bundle)
    result = evaluate_daily_research_outcomes(snapshot, bundle, cutoff)
    assert len(result) == 40
    assert {row["offset"] for row in result} == set(range(1, 21))
    assert {row["status"] for row in result} == {"NORMAL"}
    assert {row["expectation_status"] for row in result} == {"NOT_DEFINED"}
    reference = Decimal(snapshot["items"][0]["comparable_close"])
    by_session = {bar.session: bar for bar in bundle.bars if bar.instrument_id == "TEST000"}
    for offset in (1, 5, 20):
        row = next(row for row in result if row["instrument_id"] == "TEST000" and row["offset"] == offset)
        window = [by_session[session] for session in bundle.sessions[6:6 + offset]]
        assert row["close_price"] == str(window[-1].close)
        assert row["close_reference_change"] == pytest.approx(float(window[-1].comparable_close / reference - 1))
        assert Decimal(row["cumulative_high"]) == max(bar.comparable_high for bar in window)
        assert Decimal(row["cumulative_low"]) == min(bar.comparable_low for bar in window)
        assert row["mfe_bound"] == pytest.approx(float(max(bar.comparable_high for bar in window) / reference - 1))
        assert row["mae_bound"] == pytest.approx(float(min(bar.comparable_low for bar in window) / reference - 1))
        assert row["source_refs"] == ["fixture-bars", "fixture-master"]
    assert (digest(snapshot), digest(bundle)) == before


def test_missing_middle_does_not_erase_valid_endpoint_or_invent_complete_path():
    snapshot, bundle, cutoff = case(1)
    missing = bundle.sessions[8]  # T+3
    bundle = bundle.model_copy(update={"bars": tuple(bar for bar in bundle.bars if bar.session != missing)})
    result = evaluate_daily_research_outcomes(snapshot, bundle, cutoff)
    assert result[2]["status"] == "MISSING"
    assert result[2]["close_reference_change"] is None
    assert result[4]["status"] == "NORMAL"
    assert result[4]["close_reference_change"] is not None
    assert result[4]["path_status"] == "INCOMPLETE"
    assert result[4]["cumulative_high"] is result[4]["mfe_bound"] is result[4]["mae_bound"] is None
    assert result[4]["path_observed_sessions"] == 4


def test_suspension_keeps_original_signal_and_does_not_forward_fill_price():
    snapshot, bundle, cutoff = case(1)
    bundle = security_interval(bundle, "TEST000", bundle.sessions[7], suspended=True)
    result = evaluate_daily_research_outcomes(snapshot, bundle, cutoff)
    assert len(result) == 20
    assert result[1]["status"] == "SUSPENDED"
    assert result[1]["close_price"] is result[1]["close_reference_change"] is None
    assert result[2]["status"] == "NORMAL"
    assert result[2]["path_status"] == "INCOMPLETE"
    assert "VERIFIED_SUSPENSION" in result[2]["path_reason_codes"]


@pytest.mark.parametrize("change,reason", [
    ({"suspended": None}, "SUSPENSION_STATE_UNKNOWN"),
    ({"delisted_on": "SESSION"}, "DELISTED"),
])
def test_unknown_or_delisted_security_does_not_disappear(change, reason):
    snapshot, bundle, cutoff = case(1)
    session = bundle.sessions[6]
    change = {key: session if value == "SESSION" else value for key, value in change.items()}
    bundle = security_interval(bundle, "TEST000", session, **change)
    row = evaluate_daily_research_outcomes(snapshot, bundle, cutoff)[0]
    assert row["status"] == "UNAVAILABLE"
    assert row["close_reference_change"] is None
    assert reason in row["reason_codes"]


def test_removed_from_current_eligibility_is_not_a_tracking_filter():
    snapshot, bundle, cutoff = case(1)
    bundle = security_interval(bundle, "TEST000", bundle.sessions[6], st=True, board="UNKNOWN", security_type="UNKNOWN")
    row = evaluate_daily_research_outcomes(snapshot, bundle, cutoff)[0]
    assert row["status"] == "NORMAL"
    assert row["decision_status"] == "FOCUS"


def test_comparability_failure_preserves_visible_raw_close_but_not_return():
    snapshot, bundle, cutoff = case(1)
    bundle = edit_bar(bundle, "TEST000", bundle.sessions[6], comparable_basis="TEST_FIXTURE_NEW_BASIS")
    row = evaluate_daily_research_outcomes(snapshot, bundle, cutoff)[0]
    assert row["status"] == "UNAVAILABLE"
    assert row["close_price"] is not None
    assert row["comparable_close"] is row["close_reference_change"] is None
    assert row["reason_codes"] == ["COMPARABLE_PRICE_BASIS_UNAVAILABLE"]


@pytest.mark.parametrize("missing", ["comparable_close", "comparable_basis"])
def test_missing_frozen_anchor_is_never_filled_from_later_input(missing):
    snapshot, bundle, cutoff = case(1)
    snapshot["items"][0][missing] = None
    result = evaluate_daily_research_outcomes(snapshot, bundle, cutoff)
    assert all(row["close_reference_change"] is None for row in result)
    assert result[0]["reason_codes"] == ["FROZEN_REFERENCE_UNAVAILABLE", "REFERENCE_PRICE_REVISION_MISMATCH"]


def test_later_revision_cannot_restate_original_anchor_even_with_same_basis():
    snapshot, bundle, cutoff = case(1)
    bundle = edit_bar(bundle, "TEST000", bundle.sessions[5], comparable_close=Decimal("100"), comparable_high=Decimal("100"))
    row = evaluate_daily_research_outcomes(snapshot, bundle, cutoff)[0]
    assert row["status"] == "UNAVAILABLE"
    assert row["reference_close"] == snapshot["items"][0]["comparable_close"]
    assert row["close_reference_change"] is None
    assert "REFERENCE_PRICE_REVISION_MISMATCH" in row["reason_codes"]


def test_parent_actual_visibility_gates_price_use():
    snapshot, bundle, cutoff = case(1)
    future = cutoff + timedelta(seconds=1)
    receipts = tuple(row.model_copy(update={"first_seen_at": future, "ingested_at": future}) if row.receipt_id == "fixture-bars" else row for row in bundle.receipts)
    delayed = bundle.model_copy(update={"receipts": receipts})
    row = evaluate_daily_research_outcomes(snapshot, delayed, cutoff)[0]
    assert row["status"] == "UNAVAILABLE"
    assert row["close_price"] is row["close_reference_change"] is None
    assert "CAPTURE_AFTER_CUTOFF" in row["reason_codes"]


def test_known_future_is_not_matured_and_missing_calendar_is_unavailable():
    snapshot, bundle, _ = case(1)
    cutoff = datetime.combine(bundle.sessions[7], time(14, 59), TZ)
    result = evaluate_daily_research_outcomes(snapshot, bundle, cutoff)
    assert result[0]["status"] == "NORMAL"
    assert result[1]["status"] == "NOT_MATURED"
    assert result[-1]["status"] == "NOT_MATURED"
    limited = evaluate_daily_research_outcomes(snapshot, bundle, cutoff, sessions=bundle.sessions[:8])
    assert limited[2]["status"] == "UNAVAILABLE"
    assert limited[2]["session"] is None
    assert limited[2]["reason_codes"] == ["EXCHANGE_CALENDAR_HORIZON_UNAVAILABLE"]


def test_latest_revision_invalidation_asof_and_immutable_idempotence(tmp_path):
    snapshot, bundle, cutoff = case(1)
    early = evaluate_daily_research_outcomes(snapshot, bundle, cutoff)
    late_bundle = edit_bar(bundle, "TEST000", bundle.sessions[6], comparable_basis="TEST_FIXTURE_INVALIDATED")
    late = evaluate_daily_research_outcomes(snapshot, late_bundle, cutoff + timedelta(seconds=1))
    assert evaluate_daily_research_outcomes(snapshot, bundle, cutoff) == early
    assert early[0]["logical_observation_id"] == late[0]["logical_observation_id"]
    assert early[0]["observation_id"] != late[0]["observation_id"]
    assert select_daily_outcome_revisions(early + late, cutoff) == early
    assert select_daily_outcome_revisions(early + late, cutoff + timedelta(seconds=1)) == late
    assert late[0]["close_reference_change"] is None
    store = SQLiteStore(tmp_path / "TEST_FIXTURE.sqlite3")
    for row in early + early + late:
        store.put_document("daily_research_outcomes", row["observation_id"], row)
    assert len(store.list_documents("daily_research_outcomes", limit=100)) == 40
    with pytest.raises(ImmutableConflict):
        store.put_document("daily_research_outcomes", early[0]["observation_id"], late[0])


def test_summary_keeps_full_denominator_when_labels_are_missing_and_separates_versions():
    snapshot, bundle, cutoff = case(2)
    second = deepcopy(snapshot)
    second["identity"]["config_version"] = "TEST_FIXTURE_CONFIG_V2"
    result = summarize_daily_research_outcomes([snapshot, snapshot, second], [], cutoff, sessions=bundle.sessions)
    assert len(result["groups"]) == 80
    assert result["ranked"] is False and result["validation_status"] == "NOT_VERIFIED"
    assert result["horizons"] == list(HORIZONS)
    for group in result["groups"]:
        assert group["counts"]["signals"] == group["counts"]["missing_labels"] == group["counts"]["matured"] == 1
        assert group["counts"]["valid_return"] == 0
        assert group["up_rate"] is None
        assert group["return_distribution"]["mean"] is None
        assert group["valid_outcome_rate"] == 0
    unknown = summarize_daily_research_outcomes([snapshot], [], cutoff)
    assert all(group["counts"]["calendar_unavailable"] == 1 for group in unknown["groups"])
    assert all(group["maturity_rate"] is None for group in unknown["groups"])


def test_summary_uses_only_its_exact_frozen_identity_and_latest_revision():
    snapshot, bundle, cutoff = case(1)
    rows = evaluate_daily_research_outcomes(snapshot, bundle, cutoff)
    poisoned = {**rows[0], "config_version": "TEST_OTHER_CONFIG", "evaluated_at": (cutoff + timedelta(seconds=1)).isoformat()}
    summary = summarize_daily_research_outcomes([snapshot], rows + [poisoned], cutoff + timedelta(seconds=1), sessions=bundle.sessions)
    first = next(group for group in summary["groups"] if group["offset"] == 1)
    assert first["counts"]["valid_return"] == 0
    assert first["counts"]["missing_labels"] == 1
    other = deepcopy(snapshot)
    other["items"][0]["decision_status"] = "WATCH"
    different = summarize_daily_research_outcomes([other], rows, cutoff, sessions=bundle.sessions)
    assert all(group["counts"]["valid_return"] == 0 for group in different["groups"])


def test_compacted_history_produces_same_prices_and_statuses_in_isolated_database(tmp_path):
    snapshot, bundle, cutoff = case(1)
    compact = compact_input(bundle, SQLiteStore(tmp_path / "TEST_FIXTURE_HISTORY.sqlite3"))
    original = evaluate_daily_research_outcomes(snapshot, bundle, cutoff)
    backed = evaluate_daily_research_outcomes(snapshot, compact, cutoff)
    excluded = {"input_hash", "input_id", "observation_id"}
    assert [{key: value for key, value in row.items() if key not in excluded} for row in backed] == [
        {key: value for key, value in row.items() if key not in excluded} for row in original]


def test_hash_clock_and_calendar_contracts_are_enforced():
    snapshot, bundle, cutoff = case(1)
    snapshot["snapshot_hash"] = digest(snapshot)
    assert len(evaluate_daily_research_outcomes(snapshot, bundle, cutoff)) == 20
    with pytest.raises(ValueError, match="hash mismatch"):
        evaluate_daily_research_outcomes({**snapshot, "source_run_hash": "REPLACED"}, bundle, cutoff)
    with pytest.raises(ValueError, match="timezone"):
        evaluate_daily_research_outcomes(snapshot, bundle, cutoff.replace(tzinfo=None))
    with pytest.raises(ValueError, match="precedes"):
        evaluate_daily_research_outcomes(snapshot, bundle, datetime.fromisoformat(snapshot["identity"]["generated_at"]) - timedelta(seconds=1))
    with pytest.raises(ValueError, match="ascending"):
        evaluate_daily_research_outcomes(snapshot, bundle, cutoff, sessions=tuple(reversed(bundle.sessions)))


def test_zero_return_is_an_observation_and_never_an_imputed_missing_value():
    snapshot, bundle, cutoff = case(1)
    reference = Decimal(snapshot["items"][0]["comparable_close"])
    bundle = edit_bar(bundle, "TEST000", bundle.sessions[6], open=reference, high=reference, low=reference, close=reference,
                      comparable_open=reference, comparable_high=reference, comparable_low=reference, comparable_close=reference)
    rows = evaluate_daily_research_outcomes(snapshot, bundle, cutoff)
    summary = summarize_daily_research_outcomes([snapshot], rows, cutoff, sessions=bundle.sessions)
    first = next(group for group in summary["groups"] if group["offset"] == 1)
    assert rows[0]["close_reference_change"] == 0
    assert first["counts"]["zero"] == first["counts"]["valid_return"] == 1
    assert first["up_rate"] == 0
    assert first["return_distribution"]["median"] == 0


def test_invalid_partial_comparable_range_is_never_a_valid_path():
    snapshot, bundle, cutoff = case(1)
    bundle = edit_bar(bundle, "TEST000", bundle.sessions[6], comparable_open=None, comparable_high=Decimal("1"))
    row = evaluate_daily_research_outcomes(snapshot, bundle, cutoff)[0]
    assert row["status"] == "UNAVAILABLE"
    assert row["close_reference_change"] is row["mfe_bound"] is None
    assert row["reason_codes"] == ["INVALID_COMPARABLE_PRICE_RANGE"]


def test_old_pending_labels_show_due_backlog_when_report_clock_advances():
    snapshot, bundle, cutoff = case(1)
    early = datetime.combine(bundle.sessions[6], time(14), TZ)
    rows = evaluate_daily_research_outcomes(snapshot, bundle, early)
    summary = summarize_daily_research_outcomes([snapshot], rows, cutoff, sessions=bundle.sessions)
    assert [group["offset"] for group in summary["groups"]] == list(HORIZONS)
    for group in summary["groups"]:
        assert group["counts"]["matured"] == group["counts"]["stale_not_matured_labels"] == 1
        assert group["counts"]["due_without_valid_return"] == group["counts"]["due_without_complete_path"] == 1


@pytest.mark.parametrize("boundary", ["cutoff_without_generation", "publication", "capture_completion", "no_clocks"])
def test_every_saved_availability_boundary_gates_historical_evaluation(boundary):
    snapshot, bundle, cutoff = case(1)
    if boundary == "cutoff_without_generation":
        snapshot["identity"]["generated_at"] = None
        cutoff = datetime.fromisoformat(snapshot["identity"]["data_cutoff"]) - timedelta(seconds=1)
    elif boundary == "publication":
        snapshot["published_at"] = (cutoff + timedelta(seconds=1)).isoformat()
    elif boundary == "capture_completion":
        snapshot["prospective_validation"] = {"capture_completed_at": (cutoff + timedelta(seconds=1)).isoformat()}
    else:
        snapshot["identity"]["generated_at"] = snapshot["identity"]["data_cutoff"] = None
    assert not snapshot_available_as_of(snapshot, cutoff)
    with pytest.raises(ValueError, match="availability"):
        evaluate_daily_research_outcomes(snapshot, bundle, cutoff)
    summary = summarize_daily_research_outcomes([snapshot], [], cutoff, sessions=bundle.sessions)
    assert summary["groups"] == []
    field = "excluded_availability_unknown" if boundary == "no_clocks" else "excluded_not_yet_available"
    assert summary["snapshot_availability"][field] == 1
