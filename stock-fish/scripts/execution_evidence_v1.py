"""Bound execution dossiers and an independent cash/inventory reconciliation.

This consumes observed fill records; it does not generate orders or assume fills.
Current local archives cannot authenticate execution-provider facts. Consequently
the formal account curve/return is always blocked, including when conditional
arithmetic reconciles. No JSON flag or caller object can upgrade that authority.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import codex_p0 as io
from scripts import codex_research_protocol_v1 as protocol
from scripts import codex_r2_execution_v1 as r2
from stockfish_core.yaogu import execution_research_r1 as account

VERSION = "EXECUTION_EVIDENCE_V1"
KINDS = {"CALENDAR", "RULE", "COST", "SECURITY", "ACTION_COVERAGE", "ORDER", "FILL", "ORDER_STATE", "MARK"}
FIELDS = {
    "CALENDAR": {"sessions", "known_at"},
    "RULE": {"instrument_id", "session", "known_at", "tick_size", "lot_size", "settlement", "limit_up", "limit_down"},
    "SECURITY": {"instrument_id", "session", "known_at", "exchange", "board", "security_type", "suspended"},
    "COST": {"known_at", "effective_from", "effective_to", "commission_rate", "minimum_commission", "transfer_rate", "sell_tax_rate", "commission_includes_transfer_or_tax", "commission_assessment"},
    "ACTION_COVERAGE": {"instrument_id", "session", "known_at", "from_session", "through_session", "complete", "events"},
    "ORDER": {"order_id", "instrument_id", "side", "session", "submitted_at", "known_at", "quantity", "limit_price", "planned_exit_session"},
    "FILL": {"fill_id", "order_id", "session", "executed_at", "known_at", "quantity", "price", "paid_fees_cny"},
    "ORDER_STATE": {"order_id", "session", "known_at", "status", "cumulative_filled_quantity"},
    "MARK": {"instrument_id", "session", "known_at", "close", "basis"},
}
MAX_FILES, MAX_ROWS, MAX_READ = 256, 200000, 128 * 1024**2
need, digest = protocol.need, protocol.digest


def number(value, *, positive=False):
    need(not isinstance(value, bool) and isinstance(value, (str, int, Decimal)) and len(str(value)) <= 128,
         "EXECUTION_EXACT_DECIMAL_REQUIRED")
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError("EXECUTION_EXACT_DECIMAL_REQUIRED") from error
    need(result.is_finite() and abs(result.as_tuple().exponent) <= 32
         and (result > 0 if positive else result >= 0), "EXECUTION_NONNEGATIVE_FINITE_VALUE_REQUIRED")
    return result


def _integer(value, *, positive=False):
    need(type(value) is int and (value > 0 if positive else value >= 0), "EXECUTION_INTEGER_QUANTITY_REQUIRED")
    return value


def _day(value):
    return protocol.iso_day(value).isoformat()


def reconcile(records, *, sessions, initial_cash_cny, selected_ids, generated_at, target_session, evaluated_at):
    """Pure local-evidence arithmetic, never provider or investment certification.

The registered fee convention is cumulative per order/session, so split fills
do not repeatedly charge minimum commission. Unsupported corporate actions and
incomplete order states leave NAV unknown; no synthetic fills or zeros are used.
"""
    need(type(records) is list and len(records) <= MAX_ROWS, "EXECUTION_ROW_BUDGET")
    need(type(sessions) is list and sessions == sorted(set(sessions)) and 0 < len(sessions) <= 366,
         "EXECUTION_ORDERED_SESSIONS_REQUIRED")
    for day in sessions:
        _day(day)
    need(target_session in sessions and len(selected_ids) == len(set(selected_ids)), "EXECUTION_TARGET_OR_SELECTION_INVALID")
    asof, published = account.stamp(evaluated_at), account.stamp(generated_at)
    need(published <= asof, "EXECUTION_EVALUATION_PRECEDES_SIGNAL")
    rows = defaultdict(list)
    for record in records:
        need(type(record) is dict and set(record) == {"kind", "value", "evidence_ref"}
             and record["kind"] in KINDS, "EXECUTION_RECORD_ENVELOPE")
        value = deepcopy(record["value"])
        need(type(value) is dict and record["evidence_ref"], "EXECUTION_RECORD_SOURCE_REQUIRED")
        need(set(value) == FIELDS[record["kind"]], "EXECUTION_TYPED_RECORD_FIELDS")
        if "session" in value:
            need(_day(value["session"]) in sessions, "EXECUTION_RECORD_SESSION_OUTSIDE_SCOPE")
        if record["kind"] == "COST":
            need(_day(value["effective_from"]) < _day(value["effective_to"]), "EXECUTION_COST_INTERVAL")
            for field in ("commission_rate", "minimum_commission", "transfer_rate", "sell_tax_rate"):
                number(value[field])
        # No late document is silently treated as evidence already observed.
        known = account.stamp(value["known_at"])
        need(known <= asof, "EXECUTION_EVIDENCE_AFTER_EVALUATION")
        value["evidence_ref"] = record["evidence_ref"]
        rows[record["kind"]].append(value)
    need(len(rows["CALENDAR"]) == 1 and rows["CALENDAR"][0]["sessions"] == sessions,
         "EXECUTION_CALENDAR_EVIDENCE_REQUIRED")
    finite, _, ref = protocol._registered_axis()
    need(all(date.fromisoformat(day) in finite.sessions for day in sessions), "EXECUTION_FINITE_CALENDAR_REQUIRED")
    complete = [str(day) for day in finite.sessions if sessions[0] <= str(day) <= sessions[-1]]
    need(sessions == complete, "EXECUTION_MISSING_EXCHANGE_SESSION")
    io.verify_snapshots([ref])
    need(account.stamp(rows["CALENDAR"][0]["known_at"]) <= account.deadline(target_session),
         "EXECUTION_CALENDAR_NOT_KNOWN_AT_ENTRY")
    initial = cash = number(initial_cash_cny, positive=True)
    selected = set(selected_ids)
    orders, fills, order_totals, inventory = {}, {}, defaultdict(lambda: [0, Decimal(0), Decimal(0)]), defaultdict(list)
    for order in rows["ORDER"]:
        oid, iid = order["order_id"], order["instrument_id"]
        need(type(oid) is str and oid and oid not in orders and iid in selected, "EXECUTION_DUPLICATE_ORDER_OR_SELECTION_CHANGED")
        need(order["side"] in {"BUY", "SELL"} and order["session"] in sessions, "EXECUTION_ORDER_SIDE_SESSION")
        submitted = account.stamp(order["submitted_at"])
        need(published <= submitted <= account.stamp(order["known_at"])
             and submitted.astimezone(account.SH).date().isoformat() == order["session"], "EXECUTION_ORDER_CLOCK_INVALID")
        _integer(order["quantity"], positive=True)
        number(order["limit_price"], positive=True)
        if order["side"] == "BUY":
            need(order["session"] == target_session and order["planned_exit_session"] in sessions
                 and order["planned_exit_session"] > target_session, "EXECUTION_FROZEN_NEXT_SESSION_AND_T1_EXIT_REQUIRED")
        orders[oid] = order
    buy_ids = [order["instrument_id"] for order in orders.values() if order["side"] == "BUY"]
    need(len(buy_ids) == len(set(buy_ids)) and set(buy_ids) == selected,
         "EXECUTION_FULL_FROZEN_SELECTION_ORDER_ACCOUNTING_REQUIRED")
    for fill in rows["FILL"]:
        fid, oid = fill["fill_id"], fill["order_id"]
        need(type(fid) is str and fid and fid not in fills and oid in orders, "EXECUTION_DUPLICATE_FILL_OR_UNKNOWN_ORDER")
        need(fill["session"] in sessions and fill["session"] >= orders[oid]["session"], "EXECUTION_FILL_SESSION_INVALID")
        need(orders[oid]["side"] != "BUY" or fill["session"] == target_session,
             "EXECUTION_BUY_FILL_OUTSIDE_FROZEN_TARGET_SESSION")
        executed = account.stamp(fill["executed_at"])
        need(account.stamp(orders[oid]["submitted_at"]) <= executed <= account.stamp(fill["known_at"])
             and executed.astimezone(account.SH).date().isoformat() == fill["session"], "EXECUTION_FILL_CLOCK_INVALID")
        _integer(fill["quantity"], positive=True)
        number(fill["price"], positive=True)
        number(fill["paid_fees_cny"])
        fills[fid] = fill
    states = {}
    for state in rows["ORDER_STATE"]:
        key = (state["order_id"], state["session"])
        need(key not in states and key[0] in orders and key[1] in sessions, "EXECUTION_ORDER_STATE_CONFLICT")
        need(state["status"] in {"OPEN", "PARTIAL", "FILLED", "CANCELLED", "REJECTED", "UNKNOWN"}, "EXECUTION_ORDER_STATE_INVALID")
        _integer(state["cumulative_filled_quantity"])
        need(account.stamp(state["known_at"]) >= account.deadline(key[1]), "EXECUTION_ORDER_STATE_PRECEDES_SESSION")
        states[key] = state

    def unique(kind, iid, day, *, at=None):
        candidates = [r for r in rows[kind] if (iid is None or r.get("instrument_id") == iid)
            and (r.get("session") is None or r["session"] == day)
            and r.get("effective_from", day) <= day < r.get("effective_to", "9999-12-31")
            and (at is None or account.stamp(r["known_at"]) <= at)]
        need(len(candidates) <= 1, "EXECUTION_CONFLICTING_DATED_EVIDENCE")
        return candidates[0] if candidates else None

    curve, transactions, gaps, closed_orders, tainted = [], [], [], set(), False
    last_states = {}
    for day in sessions:
        day_gaps, point_refs = [], {rows["CALENDAR"][0]["evidence_ref"]}
        if account.stamp(day + "T15:00:00+08:00") > asof:
            day_gaps.append("SESSION_NOT_CLOSED")
        day_fills = sorted((f for f in fills.values() if f["session"] == day), key=lambda f: (account.stamp(f["executed_at"]), f["fill_id"]))
        # Submission commitments precede fills; same-session sale proceeds
        # cannot retrospectively fund already submitted entry orders.
        commitment = Decimal(0)
        for order in orders.values():
            if order["session"] == day and order["side"] == "BUY":
                cost = unique("COST", None, day, at=account.stamp(order["submitted_at"]))
                if cost is None:
                    day_gaps.append("ORDER_COMMITMENT_COST_UNKNOWN")
                    tainted = True
                else:
                    notional = number(order["limit_price"], positive=True) * order["quantity"]
                    commitment += notional + account.fees(notional, "BUY", cost)
        need(commitment <= cash, "EXECUTION_PRETRADE_CASH_COMMITMENT_EXCEEDED")
        for fill in day_fills:
            order = orders[fill["order_id"]]
            oid, iid, side = order["order_id"], order["instrument_id"], order["side"]
            need(oid not in closed_orders, "EXECUTION_FILL_AFTER_TERMINAL_ORDER")
            rule = unique("RULE", iid, day, at=account.stamp(order["submitted_at"]))
            cost = unique("COST", None, day, at=account.stamp(order["submitted_at"]))
            security = unique("SECURITY", iid, day, at=account.stamp(order["submitted_at"]))
            action = unique("ACTION_COVERAGE", iid, day)
            action_ok = (action is not None and action.get("complete") is True and action.get("events") == []
                         and action.get("from_session", "9999") <= target_session
                         and action.get("through_session", "0000") >= day)
            if rule is None or cost is None or security is None or not action_ok or tainted:
                day_gaps.append("DATED_RULE_COST_SECURITY_ACTION_OR_PRIOR_EXECUTION_UNKNOWN")
                tainted = True
                continue
            need(rule["settlement"] == "T_PLUS_1" and security["exchange"] in {"SSE", "SZSE"}
                 and security["board"] == "MAIN" and security["security_type"] == "A_COMMON"
                 and security["suspended"] is False, "EXECUTION_SECURITY_OR_SETTLEMENT_UNSUPPORTED")
            need(cost["commission_assessment"] == "PER_ORDER_SESSION_CUMULATIVE", "EXECUTION_FEE_ASSESSMENT_UNSUPPORTED")
            qty, price = fill["quantity"], number(fill["price"], positive=True)
            tick, lot = number(rule["tick_size"], positive=True), _integer(rule["lot_size"], positive=True)
            need(price % tick == 0 and number(order["limit_price"], positive=True) % tick == 0,
                 "EXECUTION_PRICE_OFF_TICK")
            need(number(rule["limit_down"], positive=True) <= price <= number(rule["limit_up"], positive=True),
                 "EXECUTION_FILL_OUTSIDE_DATED_LIMIT")
            need((side == "BUY" and price <= number(order["limit_price"], positive=True))
                 or (side == "SELL" and price >= number(order["limit_price"], positive=True)), "EXECUTION_FILL_OUTSIDE_ORDER")
            need(side != "BUY" or order["quantity"] % lot == 0, "EXECUTION_BUY_ORDER_NOT_WHOLE_LOT")
            previous = order_totals[(oid, day)]
            notional = price * qty
            fee = account.fees(previous[1] + notional, side, cost) - (account.fees(previous[1], side, cost) if previous[0] else Decimal(0))
            need(number(fill["paid_fees_cny"]) == fee, "EXECUTION_REPORTED_FEE_MISMATCH")
            total_qty = sum(t[0] for (key, _), t in order_totals.items() if key == oid)
            need(total_qty + qty <= order["quantity"], "EXECUTION_OVERFILLED_ORDER")
            delay = None
            if side == "BUY":
                need(cash >= notional + fee, "EXECUTION_NEGATIVE_CASH")
                cash -= notional + fee
                inventory[iid].append(dict(session=day, quantity=qty, planned_exit_session=order["planned_exit_session"], basis="RAW_CNY"))
            else:
                available = sum(l["quantity"] for l in inventory[iid] if l["session"] < day)
                need(available >= qty, "EXECUTION_T1_OR_INVENTORY_VIOLATION")
                remaining, delays = qty, []
                for position in inventory[iid]:
                    if position["session"] >= day or not remaining:
                        continue
                    taken = min(position["quantity"], remaining)
                    if taken:
                        need(day >= position["planned_exit_session"], "EXECUTION_EXIT_BEFORE_FROZEN_PLAN")
                        delays.append(sessions.index(day) - sessions.index(position["planned_exit_session"]))
                    position["quantity"] -= taken
                    remaining -= taken
                delay = max(delays, default=0)
                cash += notional - fee
            previous[0] += qty
            previous[1] += notional
            previous[2] += fee
            references = sorted({fill["evidence_ref"], order["evidence_ref"], rule["evidence_ref"], cost["evidence_ref"], security["evidence_ref"], action["evidence_ref"]})
            point_refs.update(references)
            transactions.append(dict(fill_id=fill["fill_id"], order_id=oid, instrument_id=iid, session=day,
                side=side, quantity=qty, price=str(price), notional_cny=str(notional), fees_cny=str(fee),
                cash_after_cny=str(cash), exit_delay_sessions=delay, evidence_refs=references))
        for oid, order in orders.items():
            if order["session"] > day or oid in closed_orders:
                continue
            state = states.get((oid, day))
            total_qty = sum(t[0] for (key, _), t in order_totals.items() if key == oid)
            if state is None or state["status"] == "UNKNOWN":
                day_gaps.append("ORDER_STATE_UNKNOWN")
                tainted = True
                continue
            if tainted:
                day_gaps.append("ORDER_QUANTITY_NOT_RECONCILABLE_AFTER_UNKNOWN")
                continue
            need(state["cumulative_filled_quantity"] == total_qty, "EXECUTION_ORDER_STATE_FILL_MISMATCH")
            need(state["status"] != "FILLED" or total_qty == order["quantity"], "EXECUTION_FALSE_FILLED_STATE")
            need(state["status"] not in {"OPEN", "REJECTED"} or total_qty == 0, "EXECUTION_FALSE_UNFILLED_STATE")
            need(state["status"] != "PARTIAL" or 0 < total_qty < order["quantity"], "EXECUTION_FALSE_PARTIAL_STATE")
            latest_fill = max((account.stamp(f["executed_at"]) for f in day_fills if f["order_id"] == oid), default=None)
            need(latest_fill is None or account.stamp(state["known_at"]) >= latest_fill, "EXECUTION_STATE_PRECEDES_FILL")
            point_refs.add(state["evidence_ref"])
            last_states[oid] = state["status"]
            if state["status"] in {"FILLED", "CANCELLED", "REJECTED"}:
                closed_orders.add(oid)
        positions, market_value = [], Decimal(0)
        for iid, lots in sorted(inventory.items()):
            quantity = sum(l["quantity"] for l in lots)
            if not quantity:
                continue
            mark = unique("MARK", iid, day)
            action = unique("ACTION_COVERAGE", iid, day)
            action_ok = (action is not None and action.get("complete") is True and action.get("events") == []
                         and action.get("from_session", "9999") <= min(l["session"] for l in lots if l["quantity"])
                         and action.get("through_session", "0000") >= day)
            if not action_ok or mark is None or mark.get("basis") != "RAW_CNY" or account.stamp(mark["known_at"]) < account.stamp(day + "T15:00:00+08:00"):
                day_gaps.append("MARK_OR_CORPORATE_ACTION_COVERAGE_UNKNOWN")
                positions.append(dict(instrument_id=iid, quantity=quantity, value_cny=None))
                continue
            price = number(mark["close"], positive=True)
            value = price * quantity
            market_value += value
            point_refs.update((mark["evidence_ref"], action["evidence_ref"]))
            positions.append(dict(instrument_id=iid, quantity=quantity, mark_cny=str(price), value_cny=str(value),
                                  evidence_refs=[mark["evidence_ref"], action["evidence_ref"]]))
        if tainted:
            day_gaps.append("PRIOR_UNRESOLVED_EXECUTION_PRESERVED")
        gaps.extend(dict(session=day, reason=reason) for reason in sorted(set(day_gaps)))
        curve.append(dict(session=day, conditional_cash_cny=None if tainted else str(cash), positions=positions,
            conditional_nav_cny=None if day_gaps else str(cash + market_value),
            evidence_refs=sorted(point_refs), unknown_reasons=sorted(set(day_gaps))))
    open_inventory = sum(l["quantity"] for lots in inventory.values() for l in lots)
    reconciled = not gaps and len(closed_orders) == len(orders) and not open_inventory
    return dict(schema_version=VERSION + "_RECONCILIATION", status="CONDITIONAL_RECONCILED" if reconciled else "BLOCKED_DATA",
        conditional_curve=curve, transactions=transactions, gaps=gaps, order_states=last_states,
        original_order_count=len(orders), original_fill_count=len(fills), open_inventory_quantity=open_inventory,
        unclosed_order_ids=sorted(set(orders) - closed_orders), initial_cash_cny=str(initial),
        conditional_account_return=str(cash / initial - 1) if reconciled else None,
        capital_evidence="UNVERIFIED_CALLER_INITIAL_CASH_SCENARIO",
        buy_validity_policy="FROZEN_TARGET_SESSION_ONLY_NO_CARRY_FORWARD",
        fee_policy="PER_ORDER_SESSION_CUMULATIVE_USING_UNCHANGED_FEE_FUNCTION",
        account_curve=None, strategy_account_return=None, source_reauthenticated_now=False,
        evaluation_admissible_for_market_validation=False, market_edge="NOT_ESTABLISHED")


def audit_dossier(run, dossier, *, base, source_owner=None, allow_test_fixture=False):
    implementation, implementation_refs = protocol.implementation_identity()
    hashes = dict(implementation["file_hashes"])
    for name in ("scripts/execution_evidence_v1.py", "stockfish_core/yaogu/research_r1_adapter.py"):
        _, reference = io.files._read_file(ROOT / name, maximum=1024**2, retain=False)
        implementation_refs.append(reference)
        hashes[name] = reference["sha256"]
    implementation = dict(version=VERSION, file_hashes=hashes, source_hash=digest(hashes))
    diagnostic = r2.readiness(run, expected_content_hash=run["content_hash"])
    source_rows, selected_ids = r2._validate_run(run, run["content_hash"])
    need(selected_ids or not any(r["decision_status"] == "UNKNOWN" for r in source_rows),
         "EXECUTION_EMPTY_UNKNOWN_SELECTION_CANNOT_BECOME_CASH_RETURN")
    need(type(allow_test_fixture) is bool and type(dossier) is dict and dossier.get("schema_version") == VERSION + "_DOSSIER",
         "EXECUTION_DOSSIER_REQUIRED")
    need(set(dossier) == {"schema_version", "coverage_type", "run_id", "signal_content_hash", "sessions", "initial_cash_cny", "evaluated_at", "files"},
         "EXECUTION_DOSSIER_FIELDS")
    need(dossier["run_id"] == run["run_id"] and dossier["signal_content_hash"] == run["content_hash"], "EXECUTION_SIGNAL_BINDING_MISMATCH")
    fixture = dossier["coverage_type"] == "TEST_FIXTURE"
    need(dossier["coverage_type"] == run["coverage_type"] and (not fixture or allow_test_fixture), "EXECUTION_FIXTURE_OPT_IN_OR_COVERAGE_MISMATCH")
    owner_role = "EXPLICIT_TEST_FIXTURE" if fixture else "SAVED_SIGNAL_ONLY"
    if source_owner is not None:
        owner_role = r2._owner_terminal(source_owner, run)
    original = digest([run, dossier])
    need(type(dossier["files"]) is list and 0 < len(dossier["files"]) <= MAX_FILES, "EXECUTION_DOSSIER_FILE_BUDGET")
    root = Path(base).absolute()
    need(root.resolve(strict=True) == root and root.is_dir(), "EXECUTION_CANONICAL_BASE_REQUIRED")
    implementation_bytes = 2 * sum(ref["fp"]["st_size"] for ref in implementation_refs)
    need(implementation_bytes < MAX_READ, "EXECUTION_IMPLEMENTATION_READ_BUDGET")
    refs, records, seen, consumed = [], [], set(), implementation_bytes
    for carrier in dossier["files"]:
        need(type(carrier) is dict and set(carrier) == {"path", "sha256"}, "EXECUTION_CARRIER_FIELDS")
        relative = Path(carrier["path"])
        need(not relative.is_absolute() and ".." not in relative.parts and str(relative) not in seen, "EXECUTION_PATH_ESCAPE_OR_DUPLICATE")
        seen.add(str(relative))
        value, ref = io.read_snapshot(root / relative, carrier["sha256"], max_stored=min(8 * 1024**2, (MAX_READ - consumed) // 2), max_json=8 * 1024**2)
        refs.append(ref)
        consumed += 2 * ref["fp"]["st_size"]
        need(consumed <= MAX_READ, "EXECUTION_READ_BUDGET")
        need(set(value) == {"schema_version", "records"} and value["schema_version"] == VERSION + "_RECORDS"
             and type(value["records"]) is list, "EXECUTION_RECORD_FILE_SCHEMA")
        for row in value["records"]:
            need(type(row) is dict and set(row) == {"kind", "value"}, "EXECUTION_RECORD_FIELDS")
            records.append(dict(row, evidence_ref=ref["sha256"]))
        need(len(records) <= MAX_ROWS, "EXECUTION_ROW_BUDGET")
    result = reconcile(records, sessions=dossier["sessions"], initial_cash_cny=dossier["initial_cash_cny"],
        selected_ids=diagnostic["selected_ids"], generated_at=run["generated_at"], target_session=run["target_session"], evaluated_at=dossier["evaluated_at"])
    io.verify_snapshots([*refs, *implementation_refs])
    need(digest([run, dossier]) == original, "EXECUTION_BORROWED_INPUT_CHANGED")
    if source_owner is not None:
        need(r2._owner_terminal(source_owner, run) == owner_role, "EXECUTION_SIGNAL_OWNER_CHANGED")
    return protocol.seal(dict(schema_version=VERSION, status="BLOCKED_PROVIDER_AUTHORITY", run_id=run["run_id"],
        signal_content_hash=run["content_hash"], signal_owner_role=owner_role, input_carriers=refs,
        local_reconciliation=result, verified_read_bytes=consumed,
        implementation_read_bytes=implementation_bytes,
        read_accounting_scope="AUDIT_INITIAL_AND_TERMINAL_READS_CLI_PUBLICATION_READS_SEPARATE",
        implementation=implementation, implementation_carriers=implementation_refs,
        required_native_producer_roles=["DATED_CALENDAR", "SECURITY_AND_LIMIT_RULE", "FEE_SCHEDULE",
            "CORPORATE_ACTION_COVERAGE", "ORDER_SUBMISSION", "FILL_OR_NONFILL_COMPLETE_FEED", "CLOSING_MARK"],
        execution_provider_authentication="NATIVE_EXECUTION_PRODUCERS_NOT_REGISTERED",
        source_binding_role="EXACT_LOCAL_BYTES_ONLY_NOT_PROVIDER_OR_PIT_AUTHORITY",
        account_curve=None, strategy_account_return=None, source_reauthenticated_now=False,
        evaluation_admissible_for_market_validation=False, market_edge="NOT_ESTABLISHED"))


def main(argv=None):
    parser = argparse.ArgumentParser(description="执行证据与部分成交现金/NAV对账；缺原生来源则正式曲线阻塞", allow_abbrev=False)
    for name in ("run", "dossier"):
        parser.add_argument("--" + name, required=True)
        parser.add_argument("--" + name + "-sha256", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--test-fixture", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        run, run_ref = io.read_snapshot(args.run, args.run_sha256)
        dossier, dossier_ref = io.read_snapshot(args.dossier, args.dossier_sha256)
        result = audit_dossier(run, dossier, base=args.base, allow_test_fixture=args.test_fixture)
        refs = [run_ref, dossier_ref, *result["input_carriers"], *result["implementation_carriers"]]
        publication_bytes = 2 * sum(ref["fp"]["st_size"] for ref in refs)
        need(publication_bytes <= MAX_READ, "EXECUTION_PUBLICATION_READ_BUDGET")
        envelope = dict(entry_version=VERSION, result=result, top_level_carriers=[run_ref, dossier_ref],
                        publication_input_read_bytes=publication_bytes,
                        total_input_read_bytes=result["verified_read_bytes"] + run_ref["fp"]["st_size"] + dossier_ref["fp"]["st_size"] + publication_bytes)
        io.publish_new(args.output, envelope, validate=lambda: io.verify_snapshots(refs))
        print(json.dumps(dict(status=result["status"], local_status=result["local_reconciliation"]["status"], output=args.output)))
        return 4
    except Exception as error:
        reason = str(error)
        if re.fullmatch(r"[A-Z][A-Z0-9_]{2,160}", reason) is None:
            reason = "EXECUTION_EVIDENCE_REJECTED_OR_UNAVAILABLE"
        print(json.dumps(dict(status="REJECTED", reason=reason)))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
