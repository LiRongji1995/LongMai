"""TEST_FIXTURE independent fill arithmetic and fail-closed source boundaries."""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json

import pytest

from scripts import execution_evidence_v1 as subject
from tests.unit.test_codex_r2_execution_v1 import native_run

DAYS = ["2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05"]
IID = "TEST_FIXTURE_A"


def evidence(ids=(IID,), days=None):
    days = days or DAYS
    rows = []
    def add(kind, value):
        rows.append(dict(kind=kind, value=value, evidence_ref="TEST_FIXTURE_" + str(len(rows))))
    add("CALENDAR", dict(sessions=days, known_at=days[0] + "T08:00:00+08:00"))
    add("COST", dict(effective_from=days[0], effective_to="2027-01-01", known_at=days[0] + "T08:00:00+08:00",
        commission_rate=".0003", minimum_commission="5", transfer_rate=".00001", sell_tax_rate=".0005",
        commission_includes_transfer_or_tax=False, commission_assessment="PER_ORDER_SESSION_CUMULATIVE"))
    for iid in ids:
        for day in days[1:]:
            known = day + "T08:00:00+08:00"
            add("RULE", dict(instrument_id=iid, session=day, known_at=known, tick_size=".01", lot_size=100,
                settlement="T_PLUS_1", limit_up="11", limit_down="9"))
            add("SECURITY", dict(instrument_id=iid, session=day, known_at=known, exchange="SSE", board="MAIN", security_type="A_COMMON", suspended=False))
            add("ACTION_COVERAGE", dict(instrument_id=iid, session=day, known_at=day + "T15:00:00+08:00",
                from_session=days[1], through_session=day, complete=True, events=[]))
            add("MARK", dict(instrument_id=iid, session=day, known_at=day + "T15:00:00+08:00", close="10.10", basis="RAW_CNY"))
        for suffix, side, index, qty, limit in (("BUY", "BUY", 1, 300, "11"), ("SELL1", "SELL", 2, 100, "10"), ("SELL2", "SELL", 3, 200, "10")):
            day = days[index]
            add("ORDER", dict(order_id=iid+suffix, instrument_id=iid, side=side, session=day, submitted_at=day + "T09:10:00+08:00",
                known_at=day + "T09:10:01+08:00", quantity=qty, limit_price=limit, planned_exit_session=days[2]))
            add("ORDER_STATE", dict(order_id=iid+suffix, session=day, known_at=day + "T15:01:00+08:00", status="FILLED", cumulative_filled_quantity=qty))
        for suffix, order, index, qty, price, fee, minute in (
            ("B1", "BUY", 1, 100, "10", "5.01000", "30"),
            ("B2", "BUY", 1, 200, "10", ".02000", "31"),
            ("S1", "SELL1", 2, 100, "10.5", "5.53550", "30"),
            ("S2", "SELL2", 3, 200, "10.5", "6.07100", "30")):
            day = days[index]
            add("FILL", dict(fill_id=iid+suffix, order_id=iid+order, session=day, executed_at=day + "T09:"+minute+":00+08:00",
                known_at=day + "T09:"+minute+":01+08:00", quantity=qty, price=price, paid_fees_cny=fee))
    return rows


def run(rows, **kwargs):
    params = dict(sessions=DAYS, initial_cash_cny="100000", selected_ids=[IID], generated_at=DAYS[0]+"T20:00:00+08:00",
                  target_session=DAYS[1], evaluated_at=DAYS[-1]+"T16:00:00+08:00")
    params.update(kwargs)
    return subject.reconcile(rows, **params)


def test_partial_fills_minimum_fee_once_and_delayed_exit_independent_oracle():
    result = run(evidence())
    assert result["status"] == "CONDITIONAL_RECONCILED"
    # Independent cash arithmetic, not calls back into the implementation.
    final = Decimal("100000") - Decimal("3000") - Decimal("5.03") + Decimal("3150") - Decimal("11.6065")
    assert final == Decimal("100133.3635")
    assert Decimal(result["conditional_curve"][-1]["conditional_nav_cny"]) == final
    assert Decimal(result["conditional_curve"][1]["conditional_nav_cny"]) == Decimal("100024.97")
    assert result["transactions"][-1]["exit_delay_sessions"] == 1
    assert [Decimal(r["fees_cny"]) for r in result["transactions"][:2]] == [Decimal("5.01"), Decimal(".02")]
    assert all(point["evidence_refs"] for point in result["conditional_curve"])
    assert result["account_curve"] is None and result["strategy_account_return"] is None
    assert result["capital_evidence"] == "UNVERIFIED_CALLER_INITIAL_CASH_SCENARIO"
    assert all("observed_cash_cny" not in point for point in result["conditional_curve"])


def test_explicit_unfilled_order_kept_and_never_assumed_filled():
    rows = [r for r in evidence() if r["kind"] != "FILL" and not (r["kind"] in {"ORDER", "ORDER_STATE"} and "SELL" in r["value"]["order_id"])]
    state = next(r["value"] for r in rows if r["kind"] == "ORDER_STATE")
    state.update(status="REJECTED", cumulative_filled_quantity=0)
    result = run(rows)
    assert result["original_order_count"] == 1 and result["original_fill_count"] == 0
    assert result["order_states"][IID+"BUY"] == "REJECTED"
    assert result["transactions"] == [] and result["conditional_account_return"] == "0"
    assert result["strategy_account_return"] is None


def test_partial_cancel_retains_actual_quantity_and_unfilled_remainder():
    rows = [r for r in evidence() if not (r["kind"] == "FILL" and r["value"]["fill_id"] in {IID+"B2", IID+"S2"})
            and not (r["kind"] in {"ORDER", "ORDER_STATE"} and r["value"]["order_id"] == IID+"SELL2")]
    state = next(r["value"] for r in rows if r["kind"] == "ORDER_STATE" and r["value"]["order_id"] == IID+"BUY")
    state.update(status="CANCELLED", cumulative_filled_quantity=100)
    result = run(rows)
    assert result["status"] == "CONDITIONAL_RECONCILED"
    assert sum(t["quantity"] for t in result["transactions"] if t["side"] == "BUY") == 100
    assert result["order_states"][IID+"BUY"] == "CANCELLED"


@pytest.mark.parametrize("fault,reason", [("fees", "FEE_MISMATCH"), ("duplicate", "DUPLICATE_FILL"), ("selection", "FULL_FROZEN_SELECTION"),
    ("overfill", "OVERFILLED"), ("offtick", "OFF_TICK"), ("cost_after_order", None), ("missing_day", "RECORD_SESSION_OUTSIDE_SCOPE"),
    ("cash", "PRETRADE_CASH"), ("false_state", "STATE_FILL_MISMATCH")])
def test_invalid_execution_cannot_produce_curve(fault, reason):
    rows = evidence()
    kwargs = {}
    fill = next(r["value"] for r in rows if r["kind"] == "FILL")
    if fault == "fees": fill["paid_fees_cny"] = "0"
    elif fault == "duplicate": rows.append(deepcopy(next(r for r in rows if r["kind"] == "FILL")))
    elif fault == "selection": kwargs["selected_ids"] = [IID, "TEST_FIXTURE_MISSING"]
    elif fault == "overfill":
        order = next(r["value"] for r in rows if r["kind"] == "ORDER")
        order["quantity"] = 200
    elif fault == "offtick": fill["price"] = "10.001"
    elif fault == "cost_after_order":
        next(r["value"] for r in rows if r["kind"] == "COST")["known_at"] = DAYS[1]+"T10:00:00+08:00"
    elif fault == "missing_day":
        kwargs["sessions"] = [DAYS[0], DAYS[1], DAYS[3]]
        next(r["value"] for r in rows if r["kind"] == "CALENDAR")["sessions"] = kwargs["sessions"]
    elif fault == "cash": kwargs["initial_cash_cny"] = "1000"
    else: next(r["value"] for r in rows if r["kind"] == "ORDER_STATE")["cumulative_filled_quantity"] = 299
    if reason:
        with pytest.raises(ValueError, match=reason): run(rows, **kwargs)
    else:
        result = run(rows, **kwargs)
        assert result["status"] == "BLOCKED_DATA" and result["conditional_account_return"] is None


@pytest.mark.parametrize("fault", ["unknown_state", "missing_mark", "corporate_action_on_exit", "unknown_action"])
def test_unknowns_never_become_zero_nav_or_completed_return(fault):
    rows = evidence()
    if fault == "unknown_state": next(r["value"] for r in rows if r["kind"] == "ORDER_STATE")["status"] = "UNKNOWN"
    elif fault == "missing_mark": rows = [r for r in rows if not (r["kind"] == "MARK" and r["value"]["session"] == DAYS[1])]
    else:
        action = next(r["value"] for r in rows if r["kind"] == "ACTION_COVERAGE" and r["value"]["session"] == DAYS[3])
        if fault == "corporate_action_on_exit": action["events"] = ["TEST_FIXTURE_DIVIDEND"]
        else: action["complete"] = False
    result = run(rows)
    assert result["status"] == "BLOCKED_DATA"
    assert result["conditional_account_return"] is None
    assert any(p["conditional_nav_cny"] is None for p in result["conditional_curve"])
    if fault == "unknown_state":
        assert all(p["conditional_cash_cny"] is None for p in result["conditional_curve"][1:])


def test_cross_day_buy_fill_is_not_a_frozen_next_session_entry():
    rows = evidence()
    fill = next(r["value"] for r in rows if r["kind"] == "FILL" and r["value"]["fill_id"] == IID+"B2")
    fill.update(session=DAYS[2], executed_at=DAYS[2]+"T09:31:00+08:00", known_at=DAYS[2]+"T09:31:01+08:00", paid_fees_cny="5.02")
    with pytest.raises(ValueError, match="BUY_FILL_OUTSIDE_FROZEN_TARGET_SESSION"):
        run(rows)


def dossier(tmp_path, native_run):
    finite, _, _ = subject.protocol._registered_axis()
    start = finite.sessions.index(subject.protocol.iso_day(native_run["target_session"]))
    days = [str(d) for d in finite.sessions[start-1:start+3]]
    ids = native_run["results"][subject.r2.STRATEGY]["research_selected_ids"]
    rows = evidence(ids, days)
    raw = json.dumps(dict(schema_version=subject.VERSION+"_RECORDS", records=[{k:r[k] for k in ("kind", "value")} for r in rows])).encode()
    (tmp_path / "TEST_FIXTURE_records.json").write_bytes(raw)
    value = dict(schema_version=subject.VERSION+"_DOSSIER", coverage_type="TEST_FIXTURE", run_id=native_run["run_id"],
        signal_content_hash=native_run["content_hash"], sessions=days, initial_cash_cny="1000000", evaluated_at=days[-1]+"T16:00:00+08:00",
        files=[dict(path="TEST_FIXTURE_records.json", sha256=hashlib.sha256(raw).hexdigest())])
    return value


def test_dossier_to_curve_keeps_provider_authority_blocked(native_run, tmp_path):
    value = dossier(tmp_path, native_run)
    result = subject.audit_dossier(native_run, value, base=tmp_path.resolve(), allow_test_fixture=True)
    assert result["local_reconciliation"]["status"] == "CONDITIONAL_RECONCILED"
    assert result["status"] == "BLOCKED_PROVIDER_AUTHORITY"
    assert result["account_curve"] is None and result["source_reauthenticated_now"] is False
    assert "FILL_OR_NONFILL_COMPLETE_FEED" in result["required_native_producer_roles"]


def test_fixture_opt_in_fake_owner_and_source_replacement_rejected(native_run, tmp_path):
    value = dossier(tmp_path, native_run)
    with pytest.raises(ValueError, match="FIXTURE_OPT_IN"):
        subject.audit_dossier(native_run, value, base=tmp_path.resolve())
    with pytest.raises(ValueError, match="GENUINE_SOURCE_OWNER"):
        subject.audit_dossier(native_run, value, base=tmp_path.resolve(), source_owner=object(), allow_test_fixture=True)
    (tmp_path / "TEST_FIXTURE_records.json").write_text("{}")
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        subject.audit_dossier(native_run, value, base=tmp_path.resolve(), allow_test_fixture=True)


def test_record_changed_during_reconciliation_is_rejected(native_run, tmp_path, monkeypatch):
    value = dossier(tmp_path, native_run)
    original = subject.reconcile
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        with (tmp_path / "TEST_FIXTURE_records.json").open("ab") as stream: stream.write(b" ")
        return result
    monkeypatch.setattr(subject, "reconcile", changed)
    with pytest.raises(ValueError):
        subject.audit_dossier(native_run, value, base=tmp_path.resolve(), allow_test_fixture=True)


def test_t1_even_with_reported_same_day_sale_is_not_silently_accepted():
    rows = evidence()
    for row in rows:
        value = row["value"]
        if row["kind"] in {"ORDER", "ORDER_STATE", "FILL"} and value.get("order_id") == IID+"SELL1":
            value["session"] = DAYS[1]
            for key in ("submitted_at", "executed_at", "known_at"):
                if key in value: value[key] = value[key].replace(DAYS[2], DAYS[1])
    with pytest.raises(ValueError, match="T1_OR_INVENTORY"):
        run(rows)


def test_missing_mark_session_and_forged_authority_field_are_rejected():
    rows = evidence()
    mark = next(r["value"] for r in rows if r["kind"] == "MARK")
    del mark["session"]
    with pytest.raises(ValueError, match="TYPED_RECORD_FIELDS"):
        run(rows)
    rows = evidence()
    rows[0]["value"]["source_authenticated"] = True
    with pytest.raises(ValueError, match="TYPED_RECORD_FIELDS"):
        run(rows)


def test_actual_cli_publishes_blocked_formal_curve_and_complete_read_accounting(native_run, tmp_path):
    value = dossier(tmp_path, native_run)
    paths = {}
    for name, data in (("run", native_run), ("dossier", value)):
        path = (tmp_path / ("TEST_FIXTURE_" + name + ".json")).resolve()
        raw = json.dumps(data, default=str).encode()
        path.write_bytes(raw)
        paths[name] = (path, hashlib.sha256(raw).hexdigest())
    output = (tmp_path / "TEST_FIXTURE_result.json").resolve()
    argv = ["--base", str(tmp_path.resolve()), "--test-fixture", "--output", str(output)]
    for name, (path, sha) in paths.items(): argv.extend(["--"+name, str(path), "--"+name+"-sha256", sha])
    assert subject.main(argv) == 4
    result = json.loads(output.read_text())
    assert result["result"]["status"] == "BLOCKED_PROVIDER_AUTHORITY"
    assert result["result"]["local_reconciliation"]["status"] == "CONDITIONAL_RECONCILED"
    assert result["total_input_read_bytes"] > result["result"]["verified_read_bytes"]
    assert result["result"]["account_curve"] is None
    assert output.stat().st_mode & 0o777 == 0o600
    assert subject.main(argv) == 2  # No overwrite.
