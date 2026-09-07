"""Versioned theme-capital bullish-trend research arm.

The module deliberately keeps THEME_CAPITAL_TREND_V1 separate from the frozen
R05 PV/focus/DNA/theme rules.  Seed parameters are traceable implementation
hypotheses, not verified market edge.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import hashlib
from pathlib import Path
from statistics import median

import yaml

from .contracts import MarketInput, digest
from .strategy_experiments import (
    _as_decimal,
    _bar,
    _bar_decimal,
    _complete_pv_row,
    _number,
    _prepare_context,
    _public,
    _rank_pv,
    _select_revision,
)
from .visibility import clock_visible


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config/theme_capital_trend_v1.yaml"
CONFIG = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))

THEME_CAPITAL_TREND_ID = CONFIG["strategy_id"]
THEME_CAPITAL_TECHNICAL_ID = "THEME_CAPITAL_TREND_TECHNICAL_ONLY_V1"
SIMPLE_TREND_TEMPLATE_ID = "SIMPLE_TREND_TEMPLATE_REFERENCE_V1"
IMPLEMENTATION_VERSION = "R06_THEME_CAPITAL_TREND_V1"
PARAMETER_STATUS = CONFIG["parameter_status"]
PARAMETER_STATUS_LABEL_ZH = CONFIG["parameter_status_label_zh"]

MA_WINDOWS = tuple(CONFIG["research_seed_parameters"]["moving_averages"]["windows"])
LONG_CLOSE_WINDOWS = tuple(CONFIG["research_seed_parameters"]["moving_averages"]["close_minimums"])
TECHNICAL_REQUIRED = ("amount", "turnover", "ma_bullish", "shape")
FULL_REQUIRED = (*TECHNICAL_REQUIRED, "theme_linkage", "capital_linkage")


def theme_capital_source_identity():
    paths = [Path(__file__), CONFIG_PATH]
    hashes = {
        str(path.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }
    return {
        "theme_capital_implementation_version": IMPLEMENTATION_VERSION,
        "theme_capital_source_hash": digest(hashes),
        "theme_capital_source_file_hashes": hashes,
        "theme_capital_config_hash": digest(CONFIG),
        "theme_capital_config_path": str(CONFIG_PATH.relative_to(ROOT)).replace("\\", "/"),
    }


def _frac(value):
    value = _as_decimal(value)
    return Fraction(value) if value is not None else None


def _money(value):
    value = _as_decimal(value)
    if value is None:
        return None
    return float(value.quantize(Decimal("0.01")))


def _condition(status, reason_codes=(), *, value=None, threshold=None, details=None):
    result = {"status": status, "reason_codes": list(reason_codes)}
    if value is not None:
        result["value"] = value
    if threshold is not None:
        result["threshold"] = threshold
    if details:
        result.update(details)
    return result


def _combine(conditions):
    statuses = [row.get("status") for row in conditions if row]
    if any(status == "FAIL" for status in statuses):
        return "FAIL"
    if any(status == "UNKNOWN" for status in statuses):
        return "UNKNOWN"
    return "PASS"


def _condition_reasons(conditions):
    reasons = []
    for row in conditions:
        if row and row.get("status") != "PASS":
            reasons.extend(row.get("reason_codes") or [])
    return sorted(set(reasons))


def _session_index(ctx, session=None):
    wanted = str(session or ctx.observation_date)
    return ctx.sessions.index(wanted) if wanted in ctx.sessions else None


def _close_fraction(bar):
    return _frac((bar or {}).get("comparable_close"))


def _amount_fraction(bar):
    return _frac((bar or {}).get("amount_cny"))


def _turnover_fraction(bar):
    try:
        value = (bar or {}).get("turnover_fraction")
        return Fraction(str(value)) if value is not None else None
    except (InvalidOperation, ValueError, TypeError, ZeroDivisionError):
        return None


def _ma_value(ctx, iid, end_index, window):
    if end_index is None or end_index - window + 1 < 0:
        return None, f"TCT_MA{window}_WINDOW_UNAVAILABLE"
    rows = [_bar(ctx, session, iid) for session in ctx.sessions[end_index - window + 1 : end_index + 1]]
    if any(row is None for row in rows):
        return None, f"TCT_MA{window}_PRICE_WINDOW_INCOMPLETE"
    basis = rows[0].get("comparable_basis")
    closes = [_close_fraction(row) for row in rows]
    if not basis or any(row.get("comparable_basis") != basis for row in rows) or any(close is None or close <= 0 for close in closes):
        return None, f"TCT_MA{window}_COMPARABLE_BASIS_MISMATCH"
    return sum(closes, Fraction(0)) / window, None


def _ma_snapshot(ctx, iid, end_index, windows=MA_WINDOWS):
    values, reasons = {}, []
    for window in windows:
        value, reason = _ma_value(ctx, iid, end_index, window)
        if reason:
            reasons.append(reason)
        values[window] = value
    return values, reasons


def _ma_stack_condition(ctx, iid, end_index):
    values, reasons = _ma_snapshot(ctx, iid, end_index)
    bar = _bar(ctx, ctx.sessions[end_index], iid) if end_index is not None and end_index < len(ctx.sessions) else None
    close = _close_fraction(bar)
    if close is None:
        reasons.append("TCT_COMPARABLE_CLOSE_UNAVAILABLE")
    if reasons:
        return _condition("UNKNOWN", sorted(set(reasons)), details={"ma_values": {str(k): _number(v) for k, v in values.items()}})
    ordered = [values[window] for window in MA_WINDOWS]
    fail = []
    if not all(left > right for left, right in zip(ordered, ordered[1:])):
        fail.append("TCT_MA_BULLISH_ARRANGEMENT_FAIL")
    for window in LONG_CLOSE_WINDOWS:
        if close < values[window]:
            fail.append(f"TCT_CLOSE_BELOW_MA{window}")
    status = "FAIL" if fail else "PASS"
    return _condition(status, fail, details={"ma_values": {str(k): _number(v) for k, v in values.items()}})


def _current_amount_condition(bar):
    amount = _amount_fraction(bar)
    threshold = Fraction(CONFIG["hard_requirements"]["amount"]["observation_day_min_cny_inclusive"])
    if amount is None:
        return _condition("UNKNOWN", ["TCT_AMOUNT_UNAVAILABLE"], threshold=float(threshold))
    if amount < threshold:
        return _condition("FAIL", ["TCT_AMOUNT_BELOW_500M"], value=_money(amount), threshold=float(threshold))
    return _condition("PASS", value=_money(amount), threshold=float(threshold))


def _current_turnover_condition(bar):
    turnover = _turnover_fraction(bar)
    threshold = Fraction(str(CONFIG["hard_requirements"]["turnover"]["observation_day_min_fraction_inclusive"]))
    if turnover is None:
        return _condition("UNKNOWN", ["TCT_TURNOVER_UNAVAILABLE"], threshold=float(threshold))
    if turnover < threshold:
        return _condition("FAIL", ["TCT_TURNOVER_BELOW_7PCT"], value=_number(turnover), threshold=float(threshold))
    return _condition("PASS", value=_number(turnover), threshold=float(threshold))


def _shape_condition(ctx, iid, end_index):
    bar = _bar(ctx, ctx.observation_date, iid)
    close = _close_fraction(bar)
    values, reasons = _ma_snapshot(ctx, iid, end_index)
    if close is None:
        reasons.append("TCT_COMPARABLE_CLOSE_UNAVAILABLE")
    if reasons:
        return _condition("UNKNOWN", sorted(set(reasons)), details={"shape_type": "UNKNOWN"})
    if close >= values[5]:
        return _condition("PASS", details={"shape_type": "TREND_RUN", "pullback_days_below_ma5": 0})

    pullback = CONFIG["research_seed_parameters"]["shapes"]["pullback"]
    min_days, max_days = pullback["below_ma5_sessions_inclusive"]
    segment = []
    previous_above_index = None
    for index in range(end_index, -1, -1):
        row = _bar(ctx, ctx.sessions[index], iid)
        row_close = _close_fraction(row)
        ma5, reason = _ma_value(ctx, iid, index, 5)
        if row is None or row_close is None or reason:
            return _condition("UNKNOWN", ["TCT_PULLBACK_SEGMENT_PRICE_OR_MA5_UNAVAILABLE"], details={"shape_type": "PULLBACK_UNKNOWN"})
        if row_close < ma5:
            segment.append(index)
            if len(segment) > max_days:
                return _condition("FAIL", ["TCT_PULLBACK_BEYOND_3_SESSIONS"], details={"shape_type": "PULLBACK_TOO_LONG", "pullback_days_below_ma5": len(segment)})
        else:
            previous_above_index = index
            break
    days = len(segment)
    if days < min_days or previous_above_index is None:
        return _condition("UNKNOWN", ["TCT_PULLBACK_START_UNCONFIRMED"], details={"shape_type": "PULLBACK_UNKNOWN", "pullback_days_below_ma5": days})

    near_windows = (5, 10, 20)
    distances = {}
    for window in near_windows:
        ma = values[window]
        distances[window] = abs(close - ma) / ma if ma and ma > 0 else None
    known_distances = {k: v for k, v in distances.items() if v is not None}
    if not known_distances:
        return _condition("UNKNOWN", ["TCT_PULLBACK_DISTANCE_TO_MA_UNAVAILABLE"], details={"shape_type": "PULLBACK_UNKNOWN", "pullback_days_below_ma5": days})
    matched_ma, matched_distance = min(known_distances.items(), key=lambda item: (item[1], item[0]))
    max_distance = Fraction(str(pullback["max_distance_to_ma_5_10_20_fraction_inclusive"]))
    if matched_distance > max_distance:
        return _condition(
            "FAIL",
            ["TCT_PULLBACK_NOT_NEAR_MA5_10_20"],
            details={
                "shape_type": "PULLBACK_TOO_FAR",
                "pullback_days_below_ma5": days,
                "matched_ma": matched_ma,
                "matched_ma_distance": _number(matched_distance),
            },
        )

    fail, unknown = [], []
    segment_closes = []
    for index in segment:
        row = _bar(ctx, ctx.sessions[index], iid)
        row_close = _close_fraction(row)
        segment_closes.append(row_close)
        stack = _ma_stack_condition(ctx, iid, index)
        if stack["status"] == "UNKNOWN":
            unknown.extend(stack.get("reason_codes") or [])
        elif stack["status"] == "FAIL":
            fail.extend(stack.get("reason_codes") or [])
        row_values, row_reasons = _ma_snapshot(ctx, iid, index, (20,))
        if row_reasons or row_close is None or row_values[20] is None:
            unknown.append("TCT_PULLBACK_MA20_SEGMENT_UNAVAILABLE")
        elif row_close < row_values[20] * Fraction(str(pullback["min_close_vs_ma20_fraction_inclusive"])):
            fail.append("TCT_PULLBACK_CLOSE_BELOW_MA20_97PCT")
    if unknown:
        return _condition("UNKNOWN", sorted(set(unknown)), details={"shape_type": "PULLBACK_UNKNOWN", "pullback_days_below_ma5": days})
    if fail:
        return _condition("FAIL", sorted(set(fail)), details={"shape_type": "PULLBACK_FAIL", "pullback_days_below_ma5": days})

    prior_bar = _bar(ctx, ctx.sessions[previous_above_index], iid)
    prior_close = _close_fraction(prior_bar)
    drawdown = (min(segment_closes) / prior_close - 1) if prior_close and prior_close > 0 and segment_closes else None
    return _condition(
        "PASS",
        details={
            "shape_type": "PULLBACK_1_TO_3_SESSIONS",
            "pullback_start_session": ctx.sessions[min(segment)],
            "pullback_days_below_ma5": days,
            "matched_ma": matched_ma,
            "matched_ma_distance": _number(matched_distance),
            "max_pullback_drawdown": _number(drawdown),
        },
    )


def _window_return(ctx, iid, end_index, lookback):
    if end_index is None or end_index - lookback < 0:
        return None
    current = _bar(ctx, ctx.sessions[end_index], iid)
    previous = _bar(ctx, ctx.sessions[end_index - lookback], iid)
    if not current or not previous or current.get("comparable_basis") != previous.get("comparable_basis"):
        return None
    c0, c1 = _close_fraction(previous), _close_fraction(current)
    return c1 / c0 - 1 if c0 and c1 and c0 > 0 else None


def _amount_change(ctx, iid, end_index, lookback):
    if end_index is None or end_index - lookback < 0:
        return None
    current = _amount_fraction(_bar(ctx, ctx.sessions[end_index], iid))
    previous = _amount_fraction(_bar(ctx, ctx.sessions[end_index - lookback], iid))
    return current / previous - 1 if current is not None and previous and previous > 0 else None


def _visible_themes(ctx):
    allowed = set(CONFIG["research_seed_parameters"]["theme_linkage"]["allowed_theme_types"])
    groups, rejected = defaultdict(list), []
    for theme in ctx.bundle.themes:
        if theme.theme_type not in allowed:
            rejected.append({"kind": "THEME", "theme_id": theme.theme_id, "reason_codes": ["TCT_THEME_TYPE_NOT_ALLOWED"]})
            continue
        proof = ctx.resolver.resolve(theme, session=ctx.observation_date, require_complete=True)
        if proof.allowed:
            groups[theme.theme_id].append(theme)
        else:
            rejected.append({"kind": "THEME", "theme_id": theme.theme_id, "reason_codes": list(proof.reason_codes)})
    visible = []
    for theme_id, rows in groups.items():
        chosen = _select_revision(rows)
        if chosen is None:
            rejected.append({"kind": "THEME", "theme_id": theme_id, "reason_codes": ["TCT_CONFLICTING_THEME_REVISION"]})
        else:
            visible.append(chosen.model_dump(mode="json"))
    return visible, rejected


def _theme_stats(ctx, iid, theme, mainboard_ret5_median, end_index):
    members = sorted(set(theme.get("member_ids") or []) & set(ctx.eligible_ids))
    if iid not in members:
        return None
    peers = [member for member in members if member != iid]
    expected = len(peers)
    seed = CONFIG["research_seed_parameters"]["theme_linkage"]
    reasons, unknown = [], []
    windows = {}
    for lookback in seed["descriptive_windows"]:
        returns = [(peer, _window_return(ctx, peer, end_index, lookback)) for peer in peers]
        valid = [value for _, value in returns if value is not None]
        amount_changes = [value for peer in peers if (value := _amount_change(ctx, peer, end_index, lookback)) is not None]
        windows[str(lookback)] = {
            "valid_return_count": len(valid),
            "return_coverage": _number(Fraction(len(valid), expected)) if expected else None,
            "median_return": _number(median(valid)) if valid else None,
            "up_fraction": _number(Fraction(sum(value > 0 for value in valid), len(valid))) if valid else None,
            "median_amount_change": _number(median(amount_changes)) if amount_changes else None,
        }
    ret5 = [(peer, _window_return(ctx, peer, end_index, 5)) for peer in peers]
    valid5 = [value for _, value in ret5 if value is not None]
    if expected < seed["min_other_valid_members_inclusive"]:
        reasons.append("TCT_THEME_PEER_COUNT_BELOW_5")
    coverage = Fraction(len(valid5), expected) if expected else Fraction(0)
    if expected >= seed["min_other_valid_members_inclusive"] and coverage < Fraction(str(seed["min_peer_return_coverage_fraction_inclusive"])):
        unknown.append("TCT_THEME_PEER_RET5_COVERAGE_INSUFFICIENT")
    if valid5:
        median_ret5 = median(valid5)
        up_fraction = Fraction(sum(value > 0 for value in valid5), len(valid5))
        ret5_ge_10 = sum(value >= Fraction(1, 10) for value in valid5)
        if median_ret5 <= 0:
            reasons.append("TCT_THEME_MEDIAN_RET5_NOT_POSITIVE")
        if up_fraction < Fraction(str(seed["peer_up_fraction_min_inclusive"])):
            reasons.append("TCT_THEME_UP_FRACTION_BELOW_60PCT")
        if ret5_ge_10 < seed["peer_ret5_ge_10pct_count_min_inclusive"]:
            reasons.append("TCT_THEME_RET5_GE_10PCT_PEERS_BELOW_2")
        if mainboard_ret5_median is None:
            unknown.append("TCT_MAINBOARD_RET5_MEDIAN_UNAVAILABLE")
        elif median_ret5 < mainboard_ret5_median:
            reasons.append("TCT_THEME_MEDIAN_RET5_BELOW_MAINBOARD_MEDIAN")
    else:
        median_ret5, up_fraction, ret5_ge_10 = None, None, 0
        if expected:
            unknown.append("TCT_THEME_RET5_UNAVAILABLE")

    status = "FAIL" if reasons else "UNKNOWN" if unknown else "PASS"
    return {
        "theme_id": theme.get("theme_id"),
        "theme_name": theme.get("name"),
        "theme_type": theme.get("theme_type"),
        "status": status,
        "reason_codes": sorted(set(reasons or unknown)),
        "peer_expected": expected,
        "peer_ret5_coverage": _number(coverage),
        "peer_ret5_median": _number(median_ret5) if median_ret5 is not None else None,
        "peer_up_fraction": _number(up_fraction) if up_fraction is not None else None,
        "peer_ret5_ge_10pct_count": ret5_ge_10,
        "mainboard_ret5_median": _number(mainboard_ret5_median) if mainboard_ret5_median is not None else None,
        "descriptive_windows": windows,
        "source_refs": list(theme.get("source_refs") or []),
    }


def _mainboard_ret5_median(ctx, end_index):
    values = [value for iid in ctx.eligible_ids if (value := _window_return(ctx, iid, end_index, 5)) is not None]
    return median(values) if values else None


def _theme_condition(ctx, iid, themes, mainboard_ret5_median, end_index):
    candidates = [
        stat for theme in themes if (stat := _theme_stats(ctx, iid, theme, mainboard_ret5_median, end_index)) is not None
    ]
    candidates.sort(key=lambda row: (-(row["peer_ret5_median"] if row["peer_ret5_median"] is not None else -999.0), row["theme_id"]))
    if not candidates:
        if ctx.bundle.themes:
            return _condition("FAIL", ["TCT_NO_AUTHORIZED_THEME_LINKAGE"]), []
        return _condition("UNKNOWN", ["TCT_THEME_DATA_UNAVAILABLE"]), []
    if any(row["status"] == "PASS" for row in candidates):
        return _condition("PASS"), candidates
    status = "UNKNOWN" if any(row["status"] == "UNKNOWN" for row in candidates) else "FAIL"
    reasons = sorted({reason for row in candidates for reason in row.get("reason_codes", [])})
    return _condition(status, reasons), candidates


def _fund_date(value):
    if value is None:
        return None
    if isinstance(value, date) and not hasattr(value, "hour"):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _visible_fund(row, cutoff, coverage_type):
    try:
        return clock_visible(row, cutoff, allow_late_capture=coverage_type == "RECONSTRUCTED_HISTORICAL")
    except (TypeError, ValueError, KeyError):
        return False


def _is_classified_capital(row):
    seed = CONFIG["research_seed_parameters"]["capital_linkage"]
    layer = str(row.get("source_layer") or row.get("evidence_layer") or "").upper()
    classification = str(row.get("classification") or "").upper()
    return (
        layer in set(seed["classified_source_layers"])
        or row.get("classified_active") is True
        or ("ACTIVE" in classification and ("SEAT" in classification or "HOT_MONEY" in classification))
    )


def _is_proxy_capital(row):
    layer = str(row.get("source_layer") or row.get("evidence_layer") or "").upper()
    return layer in set(CONFIG["research_seed_parameters"]["capital_linkage"]["proxy_source_layers"])


def _fund_amounts(row):
    buy = _as_decimal(row.get("buy_amount_cny"))
    sell = _as_decimal(row.get("sell_amount_cny"))
    net = _as_decimal(row.get("net_amount_cny"))
    if net is None and buy is not None and sell is not None:
        net = buy - sell
    return buy, net


def _fund_window(row):
    session = _fund_date(row.get("session") or row.get("trade_date") or row.get("event_session"))
    start = _fund_date(row.get("window_start") or row.get("start_session") or session)
    end = _fund_date(row.get("window_end") or row.get("end_session") or session)
    if start is not None and end is not None and start > end:
        return None
    return start, end


def _capital_condition_for_theme(ctx, iid, theme, end_index):
    seed = CONFIG["research_seed_parameters"]["capital_linkage"]
    sessions_5 = {str(day) for day in ctx.sessions[max(0, end_index - seed["lookback_sessions"] + 1) : end_index + 1]}
    sessions_3 = {str(day) for day in ctx.sessions[max(0, end_index - seed["recent_activity_sessions"] + 1) : end_index + 1]}
    peers = set(theme.get("member_ids") or []) & set(ctx.eligible_ids)
    peers.discard(iid)
    visible = [
        dict(row)
        for row in ctx.bundle.funds
        if _visible_fund(row, ctx.cutoff, ctx.bundle.coverage_type)
        and str(row.get("theme_id")) == str(theme.get("theme_id"))
        and str(row.get("instrument_id")) in peers
        and str(_fund_date(row.get("session") or row.get("trade_date") or row.get("event_session"))) in sessions_5
    ]
    classified = [row for row in visible if _is_classified_capital(row)]
    proxy = [row for row in visible if _is_proxy_capital(row)]
    if not ctx.bundle.funds:
        return _condition("UNKNOWN", ["TCT_CAPITAL_DATA_UNAVAILABLE"]), {"theme_id": theme.get("theme_id"), "classified_events": 0}
    if not classified:
        reason = "TCT_CAPITAL_PROXY_ONLY_PENDING_VERIFICATION" if proxy else "TCT_NO_ACTIVE_CLASSIFIED_SECTOR_CAPITAL_EVIDENCE"
        status = "UNKNOWN" if proxy else "FAIL"
        return _condition(status, [reason]), {
            "theme_id": theme.get("theme_id"),
            "classified_events": 0,
            "proxy_events": len(proxy),
            "proxy_only_label": seed["proxy_only_label"] if proxy else None,
        }

    deduped, seen = [], set()
    for row in classified:
        key = str(row.get("dedupe_key") or (
            str(row.get("instrument_id")),
            str(row.get("theme_id")),
            str(row.get("seat_id") or row.get("seat_name") or row.get("source_id") or ""),
            str(row.get("session") or row.get("trade_date") or row.get("event_session")),
            str(row.get("window_start") or ""),
            str(row.get("window_end") or ""),
            str(row.get("source_layer") or ""),
        ))
        if key in seen:
            continue
        seen.add(key)
        row["_dedupe_key"] = key
        deduped.append(row)

    ranges = defaultdict(list)
    for row in deduped:
        window = _fund_window(row)
        if window is None or window[0] is None or window[1] is None:
            return _condition("UNKNOWN", ["TCT_CAPITAL_WINDOW_UNAVAILABLE_OR_INVALID"]), {"theme_id": theme.get("theme_id")}
        holder = (str(row.get("instrument_id")), str(row.get("seat_id") or row.get("seat_name") or row.get("source_id") or ""))
        ranges[holder].append((window[0], window[1], row["_dedupe_key"]))
    for rows in ranges.values():
        rows.sort()
        for left, right in zip(rows, rows[1:]):
            if left[1] >= right[0] and left[2] != right[2]:
                return _condition("UNKNOWN", ["TCT_CAPITAL_OVERLAPPING_CLASSIFIED_WINDOWS"]), {"theme_id": theme.get("theme_id")}

    peer_ids, total_buy, total_net, max_buy, recent = set(), Decimal(0), Decimal(0), Decimal(0), False
    evidence = []
    for row in deduped:
        buy, net = _fund_amounts(row)
        if buy is None or net is None:
            return _condition("UNKNOWN", ["TCT_CAPITAL_AMOUNT_UNAVAILABLE"]), {"theme_id": theme.get("theme_id")}
        peer_ids.add(str(row.get("instrument_id")))
        total_buy += buy
        total_net += net
        max_buy = max(max_buy, buy)
        session_text = str(_fund_date(row.get("session") or row.get("trade_date") or row.get("event_session")))
        recent |= session_text in sessions_3
        evidence.append({
            "instrument_id": str(row.get("instrument_id")),
            "session": session_text,
            "seat_id": row.get("seat_id"),
            "seat_name": row.get("seat_name"),
            "buy_amount_cny": _money(buy),
            "net_amount_cny": _money(net),
            "source_layer": row.get("source_layer") or row.get("evidence_layer"),
        })

    reasons = []
    if len(peer_ids) < seed["min_other_members_with_classified_active_money_events_inclusive"]:
        reasons.append("TCT_CAPITAL_CLASSIFIED_PEER_COUNT_BELOW_2")
    if total_buy < Decimal(str(seed["min_dedup_classified_buy_amount_cny_inclusive"])):
        reasons.append("TCT_CAPITAL_DEDUP_BUY_BELOW_100M")
    if total_net <= 0:
        reasons.append("TCT_CAPITAL_DEDUP_NET_NOT_POSITIVE")
    if max_buy < Decimal(str(seed["min_single_classified_buy_amount_cny_inclusive"])):
        reasons.append("TCT_CAPITAL_SINGLE_BUY_BELOW_20M")
    if not recent:
        reasons.append("TCT_CAPITAL_NO_ACTIVITY_IN_RECENT_3_SESSIONS")
    status = "FAIL" if reasons else "PASS"
    return _condition(status, reasons), {
        "theme_id": theme.get("theme_id"),
        "classified_events": len(deduped),
        "classified_peer_count": len(peer_ids),
        "dedup_buy_amount_cny": _money(total_buy),
        "dedup_net_amount_cny": _money(total_net),
        "max_single_buy_amount_cny": _money(max_buy),
        "has_recent_3_session_activity": recent,
        "evidence": evidence,
    }


def _same_theme_capital_condition(ctx, iid, themes, theme_rows, end_index):
    theme_by_id = {row["theme_id"]: row for row in themes}
    pairs = []
    for row in theme_rows:
        if row["status"] != "PASS":
            continue
        capital_condition, capital = _capital_condition_for_theme(ctx, iid, theme_by_id[row["theme_id"]], end_index)
        pairs.append({"theme": row, "capital_condition": capital_condition, "capital": capital})
    if not pairs:
        return _condition("FAIL", ["TCT_NO_PASSING_THEME_AVAILABLE_FOR_CAPITAL"]), []
    passing = [row for row in pairs if row["capital_condition"]["status"] == "PASS"]
    if passing:
        passing.sort(key=lambda row: (-(row["theme"]["peer_ret5_median"] if row["theme"]["peer_ret5_median"] is not None else -999.0), row["theme"]["theme_id"]))
        return _condition("PASS"), pairs
    status = "UNKNOWN" if any(row["capital_condition"]["status"] == "UNKNOWN" for row in pairs) else "FAIL"
    reasons = sorted({reason for row in pairs for reason in row["capital_condition"].get("reason_codes", [])})
    return _condition(status, reasons), pairs


def _reference_pv_scores(ctx):
    window = ctx.sessions[-6:] if len(ctx.sessions) >= 6 else []
    complete, missing = [], []
    for iid in ctx.eligible_ids:
        row, reason = _complete_pv_row(ctx, iid, window)
        if row is None:
            missing.append({"instrument_id": iid, "reason": reason})
        else:
            complete.append(row)
    ranked = _rank_pv([row.copy() for row in complete]) if complete else []
    return {
        row["instrument_id"]: row for row in ranked
    }, {
        "status": "COMPLETE" if ranked else "UNAVAILABLE",
        "evaluated": len(complete),
        "missing": len(missing),
        "ranked_by": "PV_CONTINUOUS_SCORE_NO_PV_AR_OR_FOCUS_PREFILTER",
    }


def _sort_ranked(rows):
    rows.sort(key=lambda row: (row.get("_reference_score_exact") is None, -(row.get("_reference_score_exact") or Fraction(0)), row["instrument_id"]))
    for rank, row in enumerate(rows, 1):
        row["rank"] = rank
    return rows


def _evaluate_rows(bundle, *, top_n=100, cutoff=None, _context=None, require_theme_capital=True):
    ctx = _context or _prepare_context(bundle, cutoff)
    end_index = _session_index(ctx)
    visible_themes, rejected_themes = _visible_themes(ctx)
    main_ret5 = _mainboard_ret5_median(ctx, end_index)
    reference, reference_counts = _reference_pv_scores(ctx)
    selected, diagnostics = [], []
    condition_counts = defaultdict(lambda: {"PASS": 0, "FAIL": 0, "UNKNOWN": 0})
    for iid in ctx.eligible_ids:
        security = ctx.securities[iid]
        current = _bar(ctx, ctx.observation_date, iid)
        amount = _current_amount_condition(current)
        turnover = _current_turnover_condition(current)
        ma = _ma_stack_condition(ctx, iid, end_index)
        shape = _shape_condition(ctx, iid, end_index)
        theme, theme_rows = _theme_condition(ctx, iid, visible_themes, main_ret5, end_index)
        if theme["status"] == "UNKNOWN":
            capital, pairs = _condition("UNKNOWN", ["TCT_CAPITAL_WAITING_FOR_AUTHORIZED_THEME_LINKAGE"]), []
        else:
            capital, pairs = _same_theme_capital_condition(ctx, iid, visible_themes, theme_rows, end_index)
        conditions = {
            "amount": amount,
            "turnover": turnover,
            "ma_bullish": ma,
            "shape": shape,
            "theme_linkage": theme,
            "capital_linkage": capital,
        }
        for key, condition in conditions.items():
            condition_counts[key][condition["status"]] += 1
        required_keys = FULL_REQUIRED if require_theme_capital else TECHNICAL_REQUIRED
        status = _combine(conditions[key] for key in required_keys)
        ref = reference.get(iid)
        row = {
            "strategy_id": THEME_CAPITAL_TREND_ID if require_theme_capital else THEME_CAPITAL_TECHNICAL_ID,
            "canonical_strategy_id": THEME_CAPITAL_TREND_ID if require_theme_capital else THEME_CAPITAL_TECHNICAL_ID,
            "instrument_id": iid,
            "name": security.name,
            "observation_date": ctx.observation_date,
            "status": status,
            "reason_codes": _condition_reasons(conditions[key] for key in required_keys)
            or [("TCT_SELECTED_BY_ALL_REQUIRED_CONDITIONS" if require_theme_capital else "TCT_SELECTED_BY_TECHNICAL_ONLY_CONDITIONS")],
            "condition_statuses": {key: value["status"] for key, value in conditions.items()},
            "conditions": conditions,
            "technical_status": _combine(conditions[key] for key in TECHNICAL_REQUIRED),
            "hard_requirements_status": status,
            "parameter_status": PARAMETER_STATUS,
            "parameter_status_label_zh": PARAMETER_STATUS_LABEL_ZH,
            "shape_type": shape.get("shape_type"),
            "close": _number(_bar_decimal(current, "close"), places=8),
            "comparable_close": _number(_bar_decimal(current, "comparable_close"), places=8),
            "comparable_basis": (current or {}).get("comparable_basis"),
            "amount_cny": _money((current or {}).get("amount_cny")),
            "turnover_fraction": _number(_turnover_fraction(current)),
            "theme_candidates": theme_rows,
            "theme_status": theme["status"],
            "capital_status": capital["status"],
            "theme_capital_pairs": pairs,
            "reference_score_policy": reference_counts["ranked_by"],
            "reference_score_status": "AVAILABLE" if ref is not None else "UNAVAILABLE",
            "S_PV_REFERENCE": ref.get("S_PV") if ref else None,
            "reference_score": ref.get("S_PV") if ref else None,
            "reference_rank": ref.get("rank") if ref else None,
            "_reference_score_exact": ref.get("_S_PV") if ref else None,
        }
        target = selected if status == "PASS" else diagnostics
        target.append(row)
    _sort_ranked(selected)
    ranked_ids = {row["instrument_id"] for row in selected}
    rows = [_public(row) for row in selected] + [_public(row) for row in diagnostics if row["instrument_id"] not in ranked_ids]
    unknown = sum(1 for row in diagnostics if row["status"] == "UNKNOWN")
    failed = sum(1 for row in diagnostics if row["status"] == "FAIL")
    result_status = (
        "COMPLETE" if selected and ctx.universe_complete else
        "LIMITED_SCOPE" if selected else
        "INSUFFICIENT_EVIDENCE" if unknown else
        "EMPTY"
    )
    return ctx, {
        "strategy_id": THEME_CAPITAL_TREND_ID if require_theme_capital else THEME_CAPITAL_TECHNICAL_ID,
        "canonical_strategy_id": THEME_CAPITAL_TREND_ID if require_theme_capital else THEME_CAPITAL_TECHNICAL_ID,
        "implementation_version": IMPLEMENTATION_VERSION,
        "parameter_status": PARAMETER_STATUS,
        "parameter_status_label_zh": PARAMETER_STATUS_LABEL_ZH,
        "spec_conformance": "SPEC_CONFORMANT",
        "status": result_status,
        "scope_label": "FULL_SCOPE" if ctx.universe_complete else "LIMITED_SCOPE",
        "formal_list_policy": "ALL_REQUIRED_CONDITIONS_PASS_NO_FIXED_COUNT" if require_theme_capital else "TECHNICAL_ONLY_DIAGNOSTIC_NOT_FORMAL_LIST",
        "observation_date": ctx.observation_date,
        "decision_cutoff": ctx.cutoff.isoformat(),
        "top_ids": [row["instrument_id"] for row in selected[:top_n]],
        "qualified_ids": [row["instrument_id"] for row in selected],
        "items": rows,
        "diagnostics": [_public(row) for row in diagnostics],
        "counts": {
            "eligible": len(ctx.eligible_ids),
            "evaluated": len(ctx.eligible_ids),
            "passed": len(selected),
            "failed": failed,
            "unknown": unknown,
            "condition_counts": dict(condition_counts),
            "visible_theme_count": len(visible_themes),
            "rejected_theme_evidence_count": len(rejected_themes),
            "reference_score_available": sum(1 for row in selected + diagnostics if row.get("reference_score") is not None),
            "reference_score": reference_counts,
        },
        "traceability": {
            "hard_requirements": CONFIG["hard_requirements"],
            "research_seed_parameters": CONFIG["research_seed_parameters"],
            "initial_parameters_are_not_user_rules": True,
            "initial_parameters_status_zh": PARAMETER_STATUS_LABEL_ZH,
        },
        "rejected_theme_evidence": rejected_themes,
        "tie_break": "reference_score desc, instrument_id asc",
        "market_edge": "NOT_ESTABLISHED",
        **theme_capital_source_identity(),
    }


def run_theme_capital_trend(bundle: MarketInput, *, top_n=100, cutoff=None, _context=None):
    _, result = _evaluate_rows(bundle, top_n=top_n, cutoff=cutoff, _context=_context, require_theme_capital=True)
    return result


def run_theme_capital_trend_technical_only(bundle: MarketInput, *, top_n=100, cutoff=None, _context=None):
    _, result = _evaluate_rows(bundle, top_n=top_n, cutoff=cutoff, _context=_context, require_theme_capital=False)
    return result


def run_simple_trend_template(bundle: MarketInput, *, top_n=100, cutoff=None, _context=None):
    ctx = _context or _prepare_context(bundle, cutoff)
    end_index = _session_index(ctx)
    reference, reference_counts = _reference_pv_scores(ctx)
    selected, diagnostics = [], []
    for iid in ctx.eligible_ids:
        security = ctx.securities[iid]
        current = _bar(ctx, ctx.observation_date, iid)
        close = _close_fraction(current)
        ma50, r50 = _ma_value(ctx, iid, end_index, 50)
        ma150, r150 = _ma_value(ctx, iid, end_index, 150)
        ma200, r200 = _ma_value(ctx, iid, end_index, 200)
        ma200_prev, r200p = _ma_value(ctx, iid, end_index - 20 if end_index is not None else None, 200)
        reasons = [reason for reason in (r50, r150, r200, r200p) if reason]
        if close is None:
            reasons.append("SIMPLE_TREND_CLOSE_UNAVAILABLE")
        if reasons:
            status = "UNKNOWN"
        else:
            reasons = []
            if not (close > ma50 > ma150 > ma200):
                reasons.append("SIMPLE_TREND_C_GT_MA50_GT_MA150_GT_MA200_FAIL")
            if not (ma200 > ma200_prev):
                reasons.append("SIMPLE_TREND_MA200_NOT_RISING_VS_T_MINUS_20")
            status = "FAIL" if reasons else "PASS"
        ref = reference.get(iid)
        row = {
            "strategy_id": SIMPLE_TREND_TEMPLATE_ID,
            "canonical_strategy_id": SIMPLE_TREND_TEMPLATE_ID,
            "instrument_id": iid,
            "name": security.name,
            "observation_date": ctx.observation_date,
            "status": status,
            "reason_codes": reasons or ["SIMPLE_TREND_TEMPLATE_PASS"],
            "close": _number(_bar_decimal(current, "close"), places=8),
            "comparable_close": _number(_bar_decimal(current, "comparable_close"), places=8),
            "comparable_basis": (current or {}).get("comparable_basis"),
            "ma_values": {"50": _number(ma50), "150": _number(ma150), "200": _number(ma200), "200_t_minus_20": _number(ma200_prev)},
            "reference_score": ref.get("S_PV") if ref else None,
            "reference_rank": ref.get("rank") if ref else None,
            "_reference_score_exact": ref.get("_S_PV") if ref else None,
            "parameter_status": PARAMETER_STATUS,
            "parameter_status_label_zh": PARAMETER_STATUS_LABEL_ZH,
        }
        (selected if status == "PASS" else diagnostics).append(row)
    _sort_ranked(selected)
    unknown = sum(1 for row in diagnostics if row["status"] == "UNKNOWN")
    failed = sum(1 for row in diagnostics if row["status"] == "FAIL")
    return {
        "strategy_id": SIMPLE_TREND_TEMPLATE_ID,
        "canonical_strategy_id": SIMPLE_TREND_TEMPLATE_ID,
        "implementation_version": IMPLEMENTATION_VERSION,
        "parameter_status": PARAMETER_STATUS,
        "parameter_status_label_zh": PARAMETER_STATUS_LABEL_ZH,
        "status": "COMPLETE" if selected and ctx.universe_complete else "LIMITED_SCOPE" if selected else "INSUFFICIENT_EVIDENCE" if unknown else "EMPTY",
        "scope_label": "FULL_SCOPE" if ctx.universe_complete else "LIMITED_SCOPE",
        "formal_list_policy": "EXTERNAL_REFERENCE_ONLY_NOT_FORMAL_THEME_CAPITAL_LIST",
        "observation_date": ctx.observation_date,
        "decision_cutoff": ctx.cutoff.isoformat(),
        "formula": "C > MA50 > MA150 > MA200 and MA200[t] > MA200[t-20]",
        "top_ids": [row["instrument_id"] for row in selected[:top_n]],
        "qualified_ids": [row["instrument_id"] for row in selected],
        "items": [_public(row) for row in selected] + [_public(row) for row in diagnostics],
        "diagnostics": [_public(row) for row in diagnostics],
        "counts": {
            "eligible": len(ctx.eligible_ids),
            "evaluated": len(ctx.eligible_ids),
            "passed": len(selected),
            "failed": failed,
            "unknown": unknown,
            "reference_score": reference_counts,
        },
        "market_edge": "NOT_ESTABLISHED",
        **theme_capital_source_identity(),
    }


def theme_capital_strategy_specs():
    identity = theme_capital_source_identity()
    seed = CONFIG["research_seed_parameters"]
    hard = CONFIG["hard_requirements"]
    return [
        {
            "strategy_id": THEME_CAPITAL_TECHNICAL_ID,
            "parameter_status": PARAMETER_STATUS,
            "parameter_status_label_zh": PARAMETER_STATUS_LABEL_ZH,
            "formal_role": "DIAGNOSTIC_TECHNICAL_ONLY_NOT_FORMAL_LIST",
            "hard_requirements": {key: hard[key] for key in ("qualified_universe", "amount", "turnover", "trend", "pullback", "statuses")},
            "research_seed_parameters": {key: seed[key] for key in ("moving_averages", "shapes", "ranking")},
            **identity,
        },
        {
            "strategy_id": THEME_CAPITAL_TREND_ID,
            "parameter_status": PARAMETER_STATUS,
            "parameter_status_label_zh": PARAMETER_STATUS_LABEL_ZH,
            "formal_role": "FORMAL_LIST_REQUIRES_TECHNICAL_THEME_AND_CAPITAL_PASS",
            "hard_requirements": hard,
            "research_seed_parameters": seed,
            "traceability": CONFIG["traceability"],
            **identity,
        },
        {
            "strategy_id": SIMPLE_TREND_TEMPLATE_ID,
            "parameter_status": PARAMETER_STATUS,
            "parameter_status_label_zh": PARAMETER_STATUS_LABEL_ZH,
            "formal_role": "EXTERNAL_REFERENCE_ONLY",
            "formula": "C > MA50 > MA150 > MA200 and MA200[t] > MA200[t-20]",
            "source": "Minervini-style trend template comparison arm; not adopted as verified rule.",
            **identity,
        },
    ]


def audit_theme_capital_instrument(bundle: MarketInput, instrument_id: str, *, cutoff=None):
    ctx, full = _evaluate_rows(bundle, cutoff=cutoff, require_theme_capital=True)
    _, technical = _evaluate_rows(bundle, cutoff=cutoff, _context=ctx, require_theme_capital=False)
    simple = run_simple_trend_template(bundle, cutoff=cutoff, _context=ctx)

    def find(result):
        return next((row for row in result.get("items", []) if row.get("instrument_id") == instrument_id), None)

    return {
        "instrument_id": instrument_id,
        "observation_date": ctx.observation_date,
        "full_strategy": find(full),
        "technical_only": find(technical),
        "simple_trend_template": find(simple),
        "audit_policy": "PASS_FAIL_UNKNOWN_WITHOUT_IMPUTATION",
        "parameter_status": PARAMETER_STATUS,
        "parameter_status_label_zh": PARAMETER_STATUS_LABEL_ZH,
    }
