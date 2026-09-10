"""Full-market data coverage diagnostics shared by API, reports and scripts."""
from __future__ import annotations

from collections import Counter, defaultdict, OrderedDict
from datetime import date
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import hashlib
import csv
import json
import re
import sqlite3
from pathlib import Path

from .contracts import digest


DIAGNOSTIC_VERSION = "FULL_MARKET_COVERAGE_DIAGNOSTIC_V1"
SCOPE_VERSION = "FULL_MARKET_COVERAGE_SCOPE_V1"
MISSING_REASON_LEDGER_VERSION = "FULL_MARKET_MISSING_REASON_LEDGER_V1"
SESSION_GAP_VERSION = "HISTORY_SESSION_GAP_MATRIX_V1"
FACTOR_QUEUE_AUDIT_VERSION = "ADJUSTMENT_FACTOR_QUEUE_COVERAGE_AUDIT_V1"
DEFAULT_FULL_MARKET_GATE = Fraction(98, 100)
EXCHANGES = ("ALL", "SSE", "SZSE")
MA_WINDOWS = (5, 10, 20, 60, 120, 250)
LONG_SLOPE_WINDOWS = (60, 120, 250)
FACTOR_BASIS_PREFIXES = ("BAOSTOCK_HFQ_RAW_X_BACK_FACTOR_", "TUSHARE_RAW_X_ADJ_FACTOR_")


def exchange_of(instrument_id, security=None):
    if security is not None and getattr(security, "exchange", None) in {"SSE", "SZSE"}:
        return security.exchange
    text = str(instrument_id)
    if text.endswith(".SH"):
        return "SSE"
    if text.endswith(".SZ"):
        return "SZSE"
    return "UNKNOWN"


def _decimal(value):
    try:
        if value is None:
            return None
        result = value if isinstance(value, Decimal) else Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _ratio(numerator, denominator):
    return float(numerator / denominator) if denominator else None


def _pct(numerator, denominator):
    return float((Fraction(numerator, denominator) * 100)) if denominator else None


def _scope(stats, *, gate=DEFAULT_FULL_MARKET_GATE):
    all_row = stats["ALL"]
    if all_row["denominator"] <= 0 or all_row["numerator"] <= 0:
        return "UNAVAILABLE"
    for exchange in EXCHANGES:
        row = stats[exchange]
        if row["denominator"] and Fraction(row["numerator"], row["denominator"]) < gate:
            return "LIMITED_SCOPE"
    return "FULL_MARKET"


def _exchange_imbalance(stats, *, max_delta=Fraction(20, 100)):
    sse = stats["SSE"]
    szse = stats["SZSE"]
    if not sse["denominator"] or not szse["denominator"]:
        return False
    left = Fraction(sse["numerator"], sse["denominator"])
    right = Fraction(szse["numerator"], szse["denominator"])
    return abs(left - right) > max_delta or (left == 0) != (right == 0)


def _stats(flagged_ids, eligible_ids, exchange_by):
    flagged = set(flagged_ids)
    eligible = set(eligible_ids)
    rows = OrderedDict()
    for exchange in EXCHANGES:
        denominator = sum(1 for iid in eligible if exchange == "ALL" or exchange_by.get(iid) == exchange)
        numerator = sum(1 for iid in eligible if iid in flagged and (exchange == "ALL" or exchange_by.get(iid) == exchange))
        rows[exchange] = {
            "numerator": numerator,
            "denominator": denominator,
            "coverage": _ratio(numerator, denominator),
            "coverage_pct": _pct(numerator, denominator),
        }
    return rows


def _raw_ok(bar):
    if not bar or bar.get("quality") == "INVALID":
        return False
    values = [_decimal(bar.get(key)) for key in ("open", "high", "low", "close")]
    if any(value is None or value <= 0 for value in values):
        return False
    open_, high, low, close = values
    return low <= min(open_, close) <= max(open_, close) <= high


def _amount_ok(bar):
    value = _decimal((bar or {}).get("amount_cny"))
    return value is not None and value >= 0


def _turnover_ok(bar):
    try:
        value = (bar or {}).get("turnover_fraction")
        return value is not None and 0 <= float(value) <= 10
    except (TypeError, ValueError):
        return False


def _comparable_ok(bar):
    return bool((bar or {}).get("comparable_basis")) and (_decimal((bar or {}).get("comparable_close")) or 0) > 0


def _factor_backed_basis(bar):
    basis = str((bar or {}).get("comparable_basis") or "")
    return basis.startswith(FACTOR_BASIS_PREFIXES)


def _same_comparable_window(bars):
    if not bars:
        return False
    basis = bars[0].get("comparable_basis")
    return bool(basis) and all(row and row.get("comparable_basis") == basis and _comparable_ok(row) for row in bars)


def _stage_rows(stage_aware_result):
    return {row["instrument_id"]: row for row in (stage_aware_result or {}).get("items", ()) if row.get("instrument_id")}


def _ma_flags_from_stage(row):
    values = ((row or {}).get("trend_strength") or {}).get("ma_values") or {}
    return {window: values.get(str(window)) is not None for window in MA_WINDOWS}


def _long_slope_from_stage(row):
    slopes = ((row or {}).get("trend_strength") or {}).get("ma_slopes") or {}
    return all(slopes.get(str(window)) is not None for window in LONG_SLOPE_WINDOWS)


def _trend_computable_from_stage(row):
    status = ((row or {}).get("condition_statuses") or {}).get("trend_state")
    return status in {"PASS", "FAIL"}


def _context_available_from_stage(row, key):
    status = ((row or {}).get("condition_statuses") or {}).get(key)
    return status in {"PASS", "FAIL"}


def _factor_reason(window_bars):
    bases = [row.get("comparable_basis") for row in window_bars if row]
    if not bases or not any(bases):
        return "MISSING_ADJUSTMENT_FACTOR"
    if len(set(bases)) > 1:
        return "COMPARABLE_BASIS_MISMATCH"
    if not all(_comparable_ok(row) for row in window_bars if row):
        return "MISSING_COMPARABLE_CLOSE"
    return "UNKNOWN"


def _selected_bar_map(ctx, sessions, eligible_ids):
    wanted = {str(session) for session in sessions}
    eligible = set(eligible_ids)
    if not wanted or not eligible:
        return {}
    selected = {}
    if hasattr(ctx.bars_by_session, "bundle"):
        groups = defaultdict(list)
        bundle = ctx.bars_by_session.bundle
        owner = getattr(bundle, "_history_store", None)
        if owner is None:
            return selected
        manifest = owner.manifest(bundle.history_artifact_ref, bundle.history_artifact_hash)
        partition_hashes = [
            part["partition_hash"]
            for part in manifest["partitions"]
            if part["session"] in wanted
        ]
        for offset in range(0, len(partition_hashes), 20):
            batch = partition_hashes[offset : offset + 20]
            if not batch:
                continue
            placeholders = ",".join("?" for _ in batch)
            sql = (
                "SELECT m.row_hash,m.instrument_id,m.session,r.payload "
                "FROM history_partition_members m "
                "JOIN history_evidence_rows r ON r.row_hash=m.row_hash "
                f"WHERE m.partition_hash IN ({placeholders}) AND m.kind='bar'"
            )
            with owner.store.transaction(write=False) as con:
                for row in con.execute(sql, batch):
                    payload = row["payload"]
                    if hashlib.sha256(payload.encode()).hexdigest() != row["row_hash"]:
                        continue
                    value = json.loads(payload)
                    session = str(value.get("session"))
                    iid = value.get("instrument_id")
                    if iid != row["instrument_id"] or session != row["session"] or session not in wanted or iid not in eligible:
                        continue
                    if value.get("quality") == "INVALID":
                        continue
                    groups[(session, iid)].append(value)

        def revision_key(row):
            return (row.get("source_published_at"), row.get("ingested_at"), row.get("first_seen_at"))

        for key, rows in groups.items():
            latest = max(revision_key(row) for row in rows)
            chosen = [row for row in rows if revision_key(row) == latest]
            if len({digest(row) for row in chosen}) == 1:
                selected[key] = chosen[0]
        return selected
    for session in wanted:
        for iid, bar in ctx.bars_by_session.get(session, {}).items():
            if iid in eligible:
                selected[(session, iid)] = bar
    return selected


def build_coverage_diagnostics(ctx, *, pv_result=None, stage_aware_result=None, gate=DEFAULT_FULL_MARKET_GATE):
    """Build a full eligible-universe funnel without changing strategy output.

    The caller supplies the already prepared R05 context. This function reuses
    existing bar selection semantics through strategy_experiments._bar.
    """
    from . import strategy_experiments as se
    from . import stage_aware_momentum as sam

    eligible = list(ctx.eligible_ids)
    exchange_by = {iid: exchange_of(iid, ctx.securities.get(iid)) for iid in eligible}
    stage_by_id = _stage_rows(stage_aware_result)
    focus_ids = set((pv_result or {}).get("focus_ids") or [])
    pv_pass_ids = {row["instrument_id"] for row in (pv_result or {}).get("items", ()) if row.get("instrument_id")}
    pv_complete_ids = set((pv_result or {}).get("complete_feature_ids") or [])
    if not pv_complete_ids and pv_result is not None:
        pv_complete_ids = set(pv_result.get("qualified_ids") or ())
    window = list(ctx.sessions[-6:]) if len(ctx.sessions) >= 6 else []
    bar_map = _selected_bar_map(ctx, sorted(set(window + [ctx.observation_date])), eligible)
    pv_missing_reasons = {
        row.get("instrument_id"): row.get("reason")
        for row in list((pv_result or {}).get("missing", ())) + list((pv_result or {}).get("incomplete", ()))
        if row.get("instrument_id")
    }
    end_index = ctx.sessions.index(ctx.observation_date) if ctx.observation_date in ctx.sessions else None
    flags = defaultdict(set)
    ledger = []
    for iid in eligible:
        current = bar_map.get((str(ctx.observation_date), iid))
        window_bars = [bar_map.get((str(session), iid)) for session in window]
        if _raw_ok(current):
            flags["has_current_raw_bar"].add(iid)
        if len(window) == 6 and all(_raw_ok(row) for row in window_bars):
            flags["has_6_session_raw"].add(iid)
        if len(window) == 6 and all(_amount_ok(row) for row in window_bars):
            flags["has_required_amount_history"].add(iid)
        if _turnover_ok(current):
            flags["has_turnover"].add(iid)
        if _factor_backed_basis(current):
            flags["has_adjustment_factor"].add(iid)
        if _comparable_ok(current):
            flags["has_valid_comparable_basis"].add(iid)
        if len(window) == 6 and _same_comparable_window(window_bars):
            flags["has_6_session_comparable_close"].add(iid)
        row = None
        pv_reason = None
        if iid in pv_complete_ids:
            flags["pv_feature_computable"].add(iid)
        elif pv_result is not None:
            pv_reason = pv_missing_reasons.get(iid)
        else:
            row, pv_reason = se._complete_pv_row(ctx, iid, window)
            if row is not None:
                flags["pv_feature_computable"].add(iid)
        if iid in pv_pass_ids:
            flags["pv_base_pass"].add(iid)
        if iid in focus_ids:
            flags["pv_focus_pass"].add(iid)
        stage_row = stage_by_id.get(iid)
        ma_flags = _ma_flags_from_stage(stage_row) if stage_row else {}
        for ma_window in MA_WINDOWS:
            ok = ma_flags.get(ma_window)
            if ok is None:
                value, reason = sam._ma_value(ctx, iid, end_index, ma_window)
                ok = reason is None and value is not None
            if ok:
                flags[f"has_ma{ma_window}"].add(iid)
        if stage_row:
            if _long_slope_from_stage(stage_row):
                flags["has_long_ma_slope"].add(iid)
            if _trend_computable_from_stage(stage_row):
                flags["trend_computable"].add(iid)
            if _context_available_from_stage(stage_row, "sector_context"):
                flags["sector_available"].add(iid)
            if _context_available_from_stage(stage_row, "capital_linkage"):
                flags["capital_available"].add(iid)
        else:
            long_ok = True
            for ma_window in LONG_SLOPE_WINDOWS:
                current_ma, current_reason = sam._ma_value(ctx, iid, end_index, ma_window)
                past_ma, past_reason = sam._ma_value(ctx, iid, end_index - 5 if end_index is not None else None, ma_window)
                long_ok = long_ok and current_reason is None and past_reason is None and current_ma is not None and past_ma not in (None, 0)
            if long_ok:
                flags["has_long_ma_slope"].add(iid)
            if all(iid in flags[f"has_ma{window}"] for window in LONG_SLOPE_WINDOWS) and long_ok:
                flags["trend_computable"].add(iid)
        reasons = []
        if iid not in flags["has_current_raw_bar"]:
            reasons.append("MISSING_CURRENT_RAW")
        if iid not in flags["has_6_session_raw"]:
            reasons.append("MISSING_HISTORY_SESSION")
        if iid not in flags["has_required_amount_history"]:
            reasons.append("MISSING_AMOUNT")
        if iid not in flags["has_turnover"]:
            reasons.append("MISSING_TURNOVER")
        if iid not in flags["has_adjustment_factor"]:
            reasons.append("MISSING_ADJUSTMENT_FACTOR")
        if iid not in flags["has_valid_comparable_basis"]:
            reasons.append("MISSING_COMPARABLE_CLOSE")
        if iid not in flags["has_6_session_comparable_close"]:
            reason = _factor_reason(window_bars)
            if reason not in reasons:
                reasons.append(reason)
        if iid not in flags["has_ma20"] or iid not in flags["has_ma60"] or iid not in flags["has_ma120"] or iid not in flags["has_ma250"]:
            reasons.append("INSUFFICIENT_MA_HISTORY")
        if pv_reason and row is None:
            reasons.append(pv_reason)
        reasons = sorted(dict.fromkeys(reason for reason in reasons if reason != "UNKNOWN")) or ["NONE"]
        ledger.append({
            "instrument_id": iid,
            "exchange": exchange_by.get(iid, "UNKNOWN"),
            "pv_complete": iid in flags["pv_feature_computable"],
            "pv_base_pass": iid in flags["pv_base_pass"],
            "pv_focus_pass": iid in flags["pv_focus_pass"],
            "trend_computable": iid in flags["trend_computable"],
            "missing_reasons": reasons,
            "primary_reason": next((reason for reason in reasons if reason != "NONE"), "NONE"),
        })
    ordered_layers = [
        "eligible",
        "has_current_raw_bar",
        "has_6_session_raw",
        "has_required_amount_history",
        "has_turnover",
        "has_adjustment_factor",
        "has_valid_comparable_basis",
        "has_6_session_comparable_close",
        "pv_feature_computable",
        "pv_base_pass",
        "pv_focus_pass",
        "has_ma5",
        "has_ma10",
        "has_ma20",
        "has_ma60",
        "has_ma120",
        "has_ma250",
        "has_long_ma_slope",
        "trend_computable",
        "sector_available",
        "capital_available",
    ]
    flags["eligible"] = set(eligible)
    funnel = OrderedDict((layer, _stats(flags[layer], eligible, exchange_by)) for layer in ordered_layers)
    scope = {
        "scope_version": SCOPE_VERSION,
        "gate_fraction": float(gate),
        "gate_policy": "FULL_MARKET_REQUIRES_ALL_AND_EACH_EXCHANGE_COVERAGE",
        "PV_READINESS": {
            "status": _scope(funnel["pv_feature_computable"], gate=gate),
            "coverage": funnel["pv_feature_computable"],
            "exchange_coverage_imbalance": _exchange_imbalance(funnel["pv_feature_computable"]),
        },
        "TREND_READINESS": {
            "status": _scope(funnel["trend_computable"], gate=gate),
            "coverage": funnel["trend_computable"],
            "ma_coverage": {f"MA{window}": funnel[f"has_ma{window}"] for window in MA_WINDOWS},
            "long_slope": funnel["has_long_ma_slope"],
            "exchange_coverage_imbalance": _exchange_imbalance(funnel["trend_computable"]),
        },
        "CONTEXT_READINESS": {
            "status": "FULL_MARKET" if _scope(funnel["sector_available"], gate=gate) == "FULL_MARKET" and _scope(funnel["capital_available"], gate=gate) == "FULL_MARKET" else "LIMITED_SCOPE" if funnel["sector_available"]["ALL"]["numerator"] or funnel["capital_available"]["ALL"]["numerator"] else "UNAVAILABLE",
            "sector": funnel["sector_available"],
            "capital": funnel["capital_available"],
        },
    }
    return {
        "diagnostic_version": DIAGNOSTIC_VERSION,
        "observation_date": ctx.observation_date,
        "eligible_count": len(eligible),
        "exchange_counts": {exchange: funnel["eligible"][exchange]["denominator"] for exchange in EXCHANGES},
        "coverage_funnel": funnel,
        "coverage_scope": scope,
        "missing_reason_ledger": ledger,
        "reason_aggregates": reason_aggregates(ledger),
    }


def reason_aggregates(ledger):
    counts = defaultdict(Counter)
    for row in ledger:
        exchange = row.get("exchange") or "UNKNOWN"
        for reason in row.get("missing_reasons") or ["UNKNOWN"]:
            counts[reason]["ALL"] += 1
            counts[reason][exchange] += 1
    return {
        reason: {exchange: counts[reason].get(exchange, 0) for exchange in EXCHANGES}
        for reason in sorted(counts)
    }


def history_session_gap_matrix(ctx, *, windows=MA_WINDOWS, slope_lookback=5):
    eligible = list(ctx.eligible_ids)
    exchange_by = {iid: exchange_of(iid, ctx.securities.get(iid)) for iid in eligible}
    denominator = {exchange: sum(1 for iid in eligible if exchange == "ALL" or exchange_by.get(iid) == exchange) for exchange in EXCHANGES}
    sessions = list(ctx.sessions)
    end_index = sessions.index(ctx.observation_date) if ctx.observation_date in sessions else None

    def required(count):
        if end_index is None:
            return []
        left = max(0, end_index - count + 1)
        return sessions[left : end_index + 1]

    requirements = {f"MA{window}": required(window) for window in windows}
    long_required = []
    if end_index is not None:
        for window in LONG_SLOPE_WINDOWS:
            long_required.extend(required(window))
            past_index = end_index - slope_lookback
            if past_index >= 0:
                left = max(0, past_index - window + 1)
                long_required.extend(sessions[left : past_index + 1])
    requirements["LONG_MA_SLOPE"] = sorted(set(long_required))
    wanted = sorted(set(session for values in requirements.values() for session in values))
    observed_raw = {str(session): Counter() for session in wanted}
    observed_comparable = {str(session): Counter() for session in wanted}
    for (session, iid), bar in _selected_bar_map(ctx, wanted, eligible).items():
        exchange = exchange_by.get(iid)
        if _raw_ok(bar):
            observed_raw[session]["ALL"] += 1
            observed_raw[session][exchange] += 1
        if _comparable_ok(bar):
            observed_comparable[session]["ALL"] += 1
            observed_comparable[session][exchange] += 1
    rows = {}
    for name, needed in requirements.items():
        session_rows = []
        for session in needed:
            key = str(session)
            raw_counts = {exchange: observed_raw[key].get(exchange, 0) for exchange in EXCHANGES}
            comparable_counts = {exchange: observed_comparable[key].get(exchange, 0) for exchange in EXCHANGES}
            session_rows.append({
                "session": key,
                "observed": raw_counts,
                "observed_raw": raw_counts,
                "observed_comparable": comparable_counts,
                "denominator": denominator,
                "missing_all": raw_counts["ALL"] == 0,
                "partial_all": 0 < raw_counts["ALL"] < denominator["ALL"],
                "missing_comparable_all": comparable_counts["ALL"] == 0,
                "partial_comparable_all": 0 < comparable_counts["ALL"] < denominator["ALL"],
            })
        rows[name] = {
            "required_sessions": [str(session) for session in needed],
            "available_sessions": [row["session"] for row in session_rows if row["observed"]["ALL"] > 0],
            "missing_sessions": [row["session"] for row in session_rows if row["missing_all"]],
            "partial_sessions": [row["session"] for row in session_rows if row["partial_all"]],
            "available_comparable_sessions": [row["session"] for row in session_rows if row["observed_comparable"]["ALL"] > 0],
            "missing_comparable_sessions": [row["session"] for row in session_rows if row["missing_comparable_all"]],
            "partial_comparable_sessions": [row["session"] for row in session_rows if row["partial_comparable_all"]],
            "session_rows": session_rows,
        }
    return {
        "matrix_version": SESSION_GAP_VERSION,
        "observation_date": ctx.observation_date,
        "eligible_count": len(eligible),
        "exchange_counts": denominator,
        "requirements": rows,
    }


def comparable_chain_sample(ctx, *, sample_size=100, seed=20260909):
    import random

    rng = random.Random(seed)
    eligible = list(ctx.eligible_ids)
    chosen = sorted(rng.sample(eligible, min(sample_size, len(eligible)))) if eligible else []
    sessions = list(ctx.sessions[-6:])
    bar_map = _selected_bar_map(ctx, sessions, chosen)
    rows = []
    for iid in chosen:
        for session in sessions:
            bar = bar_map.get((str(session), iid))
            raw_close = _decimal((bar or {}).get("close"))
            comparable = _decimal((bar or {}).get("comparable_close"))
            factor = comparable / raw_close if raw_close and comparable else None
            rows.append({
                "instrument": iid,
                "session": str(session),
                "raw_close": str(raw_close) if raw_close is not None else "",
                "factor": str(factor) if factor is not None else "",
                "comparable_close": str(comparable) if comparable is not None else "",
                "basis_id": (bar or {}).get("comparable_basis") or "",
                "source_ref": "|".join((bar or {}).get("source_refs") or ()),
            })
    return rows


def _normalize_code(code):
    text = str(code)
    match = re.fullmatch(r"(?i)(sh|sz|bj)\.(\d{6})", text)
    if match:
        return f"{match.group(2)}.{match.group(1).upper()}"
    match = re.fullmatch(r"(?i)(\d{6})\.(SH|SZ|BJ)", text)
    if match:
        return f"{match.group(1)}.{match.group(2).upper()}"
    return text


def audit_factor_queue(queue_root, *, expected_ids=()):
    root = Path(queue_root)
    db = root / "request-queue.sqlite3"
    requested = set()
    observed = set()
    failed = []
    batches = []
    if not db.exists():
        return {
            "audit_version": FACTOR_QUEUE_AUDIT_VERSION,
            "queue_root": str(root),
            "status": "UNAVAILABLE",
            "reason": "REQUEST_QUEUE_DB_MISSING",
        }
    with sqlite3.connect(str(db)) as con:
        con.row_factory = sqlite3.Row
        for row in con.execute("SELECT request_id,payload,state,attempts,result_path,failure_category,updated_at FROM requests"):
            payload = json.loads(row["payload"])
            if payload.get("endpoint") != "adjustment_factors":
                continue
            codes = [_normalize_code(code) for code in payload.get("parameters", {}).get("codes") or ()]
            requested.update(codes)
            result_path = row["result_path"]
            complete_codes = set()
            row_counts = Counter()
            if result_path:
                value = json.loads((root / result_path).read_text(encoding="utf-8"))
                for result_row in value.get("rows", ()):
                    row_counts[_normalize_code(result_row.get("code"))] += 1
                for status in value.get("coverage", {}).get("security_statuses", ()):
                    iid = _normalize_code(status.get("code"))
                    if status.get("status") == "COMPLETE" and status.get("cursor_exhausted") is True and status.get("row_count") == row_counts.get(iid, 0):
                        complete_codes.add(iid)
                    else:
                        failed.append({
                            "instrument_id": iid,
                            "request_id": row["request_id"],
                            "status": status.get("status"),
                            "failure_category": status.get("failure_category"),
                            "error_code": status.get("error_code"),
                        })
            observed.update(complete_codes)
            batches.append({
                "request_id": row["request_id"],
                "state": row["state"],
                "attempts": row["attempts"],
                "failure_category": row["failure_category"],
                "code_count": len(codes),
                "first_code": codes[0] if codes else None,
                "last_code": codes[-1] if codes else None,
                "sse": sum(code.endswith(".SH") for code in codes),
                "szse": sum(code.endswith(".SZ") for code in codes),
                "observed_complete": len(complete_codes),
            })
    expected = set(expected_ids) or requested

    def split(values):
        return {
            "ALL": len(values),
            "SSE": sum(code.endswith(".SH") for code in values),
            "SZSE": sum(code.endswith(".SZ") for code in values),
        }

    expected_stats = _stats(observed, expected, {iid: exchange_of(iid) for iid in expected})
    demand_stats = _stats(requested & expected, expected, {iid: exchange_of(iid) for iid in expected})
    root_cause = "FACTOR_EXECUTION_INCOMPLETE"
    if expected - requested:
        root_cause = "FACTOR_DEMAND_DOES_NOT_COVER_EXPECTED_UNIVERSE"
    elif expected_stats["SZSE"]["denominator"] and expected_stats["SZSE"]["numerator"] == 0 and expected_stats["SSE"]["numerator"]:
        root_cause = "FACTOR_EXECUTION_PRIORITY_BUDGET_LEFT_SZSE_UNATTEMPTED_OR_PENDING"
    elif _scope(expected_stats) == "FULL_MARKET":
        root_cause = "FACTOR_COVERAGE_FULL_MARKET"
    return {
        "audit_version": FACTOR_QUEUE_AUDIT_VERSION,
        "queue_root": str(root),
        "status": "COMPLETE",
        "expected": split(expected),
        "demanded": split(requested & expected),
        "observed": split(observed & expected),
        "demand_coverage": demand_stats,
        "observed_coverage": expected_stats,
        "batch_count": len(batches),
        "batch_state_counts": dict(Counter(batch["state"] for batch in batches)),
        "batch_code_count_max": max((batch["code_count"] for batch in batches), default=0),
        "missing_from_demand": sorted(expected - requested),
        "missing_observed": sorted(expected - observed),
        "failed_security_statuses": failed,
        "batches": sorted(batches, key=lambda row: (row["first_code"] or "", row["request_id"])),
        "ROOT_CAUSE_ADJUSTMENT_FACTOR_COVERAGE": root_cause,
        "source_locations": [
            "stock-fish/stockfish_core/yaogu/data_pipeline.py: adjustment_factors demand generation",
            "stock-fish/stockfish_core/yaogu/request_queue.py: RequestQueue.rows priority/session/request_id ordering",
            "stock-fish/stockfish_core/yaogu/provider_requests_worker.py: per-security adjustment factor SDK execution",
            "stock-fish/stockfish_core/yaogu/provider_normalization.py: partial factor fragment authorization",
        ],
    }


def write_ledger_csv(path, ledger):
    fields = ["instrument_id", "exchange", "pv_complete", "pv_base_pass", "pv_focus_pass", "trend_computable", "primary_reason", "missing_reasons"]
    with Path(path).open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in ledger:
            writer.writerow({**row, "missing_reasons": "|".join(row.get("missing_reasons") or ())})


def write_comparable_chain_csv(path, rows):
    fields = ["instrument", "session", "raw_close", "factor", "comparable_close", "basis_id", "source_ref"]
    with Path(path).open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def funnel_markdown(diagnostics):
    headers = ["Layer", "ALL", "SSE", "SZSE"]
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for layer, stats in diagnostics["coverage_funnel"].items():
        cells = [layer]
        for exchange in EXCHANGES:
            row = stats[exchange]
            pct = "n/a" if row["coverage_pct"] is None else f"{row['coverage_pct']:.2f}%"
            cells.append(f"{row['numerator']}/{row['denominator']} ({pct})")
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def reason_aggregates_markdown(aggregates):
    lines = ["| Reason | ALL | SSE | SZSE |", "|---|---:|---:|---:|"]
    for reason, counts in aggregates.items():
        lines.append(f"| {reason} | {counts.get('ALL', 0)} | {counts.get('SSE', 0)} | {counts.get('SZSE', 0)} |")
    return "\n".join(lines) + "\n"


def json_safe(value):
    return json.loads(json.dumps(value, ensure_ascii=False, default=str, allow_nan=False))
