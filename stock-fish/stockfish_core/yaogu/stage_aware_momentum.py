"""Stage-aware momentum research arm.

This is an additive research view.  It keeps the frozen PV/focus/DNA/theme
rules intact and records stage diagnostics before any formal inclusion check.
Seed thresholds in the config are explicitly unvalidated research parameters.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from fractions import Fraction
import hashlib
from pathlib import Path
from statistics import median

import yaml

from .contracts import MarketInput, digest
from .events import TRUE, UNKNOWN, annotate_streaks


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config/stage_aware_momentum_v1.yaml"
CONFIG = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))

STAGE_AWARE_ID = CONFIG["strategy_id"]
IMPLEMENTATION_VERSION = "R07_STAGE_AWARE_MOMENTUM_V1"
PARAMETER_STATUS = CONFIG["parameter_status"]
PARAMETER_STATUS_LABEL_ZH = CONFIG["parameter_status_label_zh"]
STAGE_IDS = tuple(CONFIG["research_seed_parameters"]["stages"]["ids"])
PRIMARY_STAGE_PRECEDENCE = tuple(CONFIG["research_seed_parameters"]["stages"]["primary_stage_precedence"])
PRICE_WINDOWS = tuple(CONFIG["research_seed_parameters"]["price_strength_windows"])
RS_WINDOWS = tuple(CONFIG["research_seed_parameters"]["relative_strength"]["windows"])
SECTOR_WINDOWS = tuple(CONFIG["research_seed_parameters"]["sector_evidence"]["windows"])
MA_WINDOWS = tuple(CONFIG["research_seed_parameters"]["stages"]["trend_pullback"]["ma_windows"])
LONG_CLOSE_WINDOWS = tuple(CONFIG["research_seed_parameters"]["stages"]["trend_pullback"]["close_minimums"])


def _se():
    from . import strategy_experiments

    return strategy_experiments


def _tct():
    from . import theme_capital_trend

    return theme_capital_trend


def _as_decimal(value):
    return _se()._as_decimal(value)


def _bar(ctx, session, iid):
    return _se()._bar(ctx, session, iid)


def _bar_decimal(bar, field):
    return _se()._bar_decimal(bar, field)


def _bar_limit_event(bar, iid, session):
    return _se()._bar_limit_event(bar, iid, session)


def _number(value, *, places=6):
    return _se()._number(value, places=places)


def _prepare_context(bundle, cutoff):
    return _se()._prepare_context(bundle, cutoff)


def _public(row):
    return _se()._public(row)


def _select_revision(rows):
    return _se()._select_revision(rows)


def percentile_rank_midpoint(value, values):
    return _se().percentile_rank_midpoint(value, values)


def _amount_change(ctx, iid, end_index, lookback):
    return _tct()._amount_change(ctx, iid, end_index, lookback)


def _amount_fraction(bar):
    return _tct()._amount_fraction(bar)


def _close_fraction(bar):
    return _tct()._close_fraction(bar)


def _fund_amounts(row):
    return _tct()._fund_amounts(row)


def _fund_date(value):
    return _tct()._fund_date(value)


def _fund_window(row):
    return _tct()._fund_window(row)


def _ma_snapshot(ctx, iid, end_index, windows):
    return _tct()._ma_snapshot(ctx, iid, end_index, windows)


def _ma_value(ctx, iid, end_index, window):
    return _tct()._ma_value(ctx, iid, end_index, window)


def _money(value):
    return _tct()._money(value)


def _reference_pv_scores(ctx):
    return _tct()._reference_pv_scores(ctx)


def _turnover_fraction(bar):
    return _tct()._turnover_fraction(bar)


def _visible_fund(row, cutoff, coverage_type):
    return _tct()._visible_fund(row, cutoff, coverage_type)


def _window_return(ctx, iid, end_index, lookback):
    return _tct()._window_return(ctx, iid, end_index, lookback)


def stage_aware_source_identity():
    paths = [Path(__file__), CONFIG_PATH]
    hashes = {
        str(path.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }
    return {
        "stage_aware_implementation_version": IMPLEMENTATION_VERSION,
        "stage_aware_source_hash": digest(hashes),
        "stage_aware_source_file_hashes": hashes,
        "stage_aware_config_hash": digest(CONFIG),
        "stage_aware_config_path": str(CONFIG_PATH.relative_to(ROOT)).replace("\\", "/"),
    }


def _frac(value):
    value = _as_decimal(value)
    return Fraction(value) if value is not None else None


def _pct(value, *, places=4):
    return _number(value * 100, places=places) if value is not None else None


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


def _visible_stage_themes(ctx):
    allowed = set(CONFIG["research_seed_parameters"]["sector_evidence"]["allowed_theme_types"])
    day = date.fromisoformat(ctx.observation_date)
    groups, rejected = defaultdict(list), []
    for theme in ctx.bundle.themes:
        if theme.theme_type not in allowed:
            rejected.append({"kind": "THEME", "theme_id": theme.theme_id, "reason_codes": ["SAM_THEME_TYPE_NOT_ALLOWED"]})
            continue
        if theme.effective_from > day or (theme.effective_to is not None and day >= theme.effective_to):
            rejected.append({"kind": "THEME", "theme_id": theme.theme_id, "reason_codes": ["SAM_THEME_NOT_EFFECTIVE_ON_OBSERVATION_DATE"]})
            continue
        proof = ctx.resolver.resolve(theme, session=day, require_complete=True)
        if proof.allowed:
            groups[theme.theme_id].append(theme)
        else:
            rejected.append({"kind": "THEME", "theme_id": theme.theme_id, "reason_codes": list(proof.reason_codes)})
    visible = []
    for theme_id, rows in groups.items():
        chosen = _select_revision(rows)
        if chosen is None:
            rejected.append({"kind": "THEME", "theme_id": theme_id, "reason_codes": ["SAM_CONFLICTING_THEME_REVISION"]})
        else:
            visible.append(chosen.model_dump(mode="json"))
    visible.sort(key=lambda row: row["theme_id"])
    return visible, rejected


def _price_return(ctx, iid, end_index, lookback):
    return _window_return(ctx, iid, end_index, lookback)


def _return_map(ctx, iid, end_index, windows=PRICE_WINDOWS):
    return {str(window): _price_return(ctx, iid, end_index, window) for window in windows}


def _market_benchmarks(ctx, end_index):
    primary = CONFIG["research_seed_parameters"]["relative_strength"]["primary_market_benchmark"]
    values = {}
    for window in set(PRICE_WINDOWS) | set(RS_WINDOWS):
        returns = [value for iid in ctx.eligible_ids if (value := _price_return(ctx, iid, end_index, window)) is not None]
        values[str(window)] = {
            "status": "COMPLETE" if returns else "UNKNOWN",
            "benchmark_return": sum(returns, Fraction(0)) / len(returns) if returns else None,
            "observed_count": len(returns),
            "eligible_count": len(ctx.eligible_ids),
            "coverage": Fraction(len(returns), len(ctx.eligible_ids)) if ctx.eligible_ids else None,
        }
    optional = {
        key: {"status": "UNKNOWN", "reason_codes": ["SAM_INDEX_BARS_NOT_IN_MARKET_INPUT"]}
        for key in CONFIG["research_seed_parameters"]["relative_strength"]["optional_market_benchmarks"]
    }
    return {
        "primary": primary,
        "benchmarks": {
            primary: {
                str(window): {
                    "status": row["status"],
                    "return_pct": _pct(row["benchmark_return"]),
                    "observed_count": row["observed_count"],
                    "eligible_count": row["eligible_count"],
                    "coverage": _number(row["coverage"]),
                }
                for window, row in values.items()
            },
            **optional,
        },
        "_primary_values": values,
    }


def _relative_strength(ctx, iid, end_index, return_values, market):
    windows = {}
    for window in RS_WINDOWS:
        stock_ret = return_values.get(str(window))
        benchmark = market["_primary_values"].get(str(window), {}).get("benchmark_return")
        rs = stock_ret - benchmark if stock_ret is not None and benchmark is not None else None
        windows[str(window)] = {
            "stock_return_pct": _pct(stock_ret),
            "benchmark_return_pct": _pct(benchmark),
            "rs_market_pct": _pct(rs),
            "status": "PASS" if rs is not None and rs > 0 else "FAIL" if rs is not None else "UNKNOWN",
        }
    statuses = [row["status"] for row in windows.values()]
    status = "PASS" if any(status == "PASS" for status in statuses) else "UNKNOWN" if any(status == "UNKNOWN" for status in statuses) else "FAIL"
    reasons = [] if status == "PASS" else ["SAM_RS_MARKET_UNAVAILABLE" if status == "UNKNOWN" else "SAM_RS_MARKET_NOT_POSITIVE"]
    return _condition(status, reasons, details={"primary_benchmark": market["primary"], "windows": windows})


def _daily_path(ctx, iid):
    current = _bar(ctx, ctx.observation_date, iid)
    if not current:
        return _condition("UNKNOWN", ["SAM_DAILY_BAR_UNAVAILABLE"], details={"daily_path_status": "UNKNOWN"})
    open_, high, low, close, prev = (_frac(current.get(key)) for key in ("open", "high", "low", "close", "reference_prev_close"))
    if any(value is None or value <= 0 for value in (open_, high, low, close, prev)) or not low <= min(open_, close) <= max(open_, close) <= high:
        return _condition("UNKNOWN", ["SAM_RAW_DAILY_PATH_UNAVAILABLE_OR_INVALID"], details={"daily_path_status": "UNKNOWN"})
    open_ret = open_ / prev - 1
    high_ret = high / prev - 1
    low_ret = low / prev - 1
    close_ret = close / prev - 1
    amplitude = (high - low) / prev
    clv = (close - low) / (high - low) if high > low else None
    recovery = close_ret - low_ret
    low_to_close = close / low - 1
    event = _bar_limit_event(current, iid, ctx.observation_date)
    limit_down = _frac(current.get("limit_down"))
    touched_limit_down = UNKNOWN
    if current.get("limit_applicable") is False:
        touched_limit_down = "NOT_APPLICABLE"
    elif current.get("limit_verified") and limit_down is not None:
        touched_limit_down = TRUE if low == limit_down else "FALSE"
    near_floor_to_ceiling = (
        "TOUCHED_BOTH_LIMITS_CHECK_LIMIT_RULE"
        if touched_limit_down == TRUE and event.touched_limit_up == TRUE
        else "NOT_FORMAL_FLOOR_TO_CEILING"
    )
    return _condition(
        "PASS",
        details={
            "daily_path_status": "AVAILABLE",
            "prev_close": _number(prev, places=8),
            "open": _number(open_, places=8),
            "high": _number(high, places=8),
            "low": _number(low, places=8),
            "close": _number(close, places=8),
            "open_return_pct": _pct(open_ret),
            "high_return_pct": _pct(high_ret),
            "low_return_pct": _pct(low_ret),
            "close_return_pct": _pct(close_ret),
            "amplitude_pct": _pct(amplitude),
            "CLV": _number(clv),
            "recovery_points_pct": _pct(recovery),
            "low_to_close_rebound_pct": _pct(low_to_close),
            "close_limit_up": event.close_limit_up,
            "touched_limit_up": event.touched_limit_up,
            "close_limit_down": event.close_limit_down,
            "touched_limit_down": touched_limit_down,
            "limit_event_quality": event.quality,
            "floor_to_ceiling_label": near_floor_to_ceiling,
            "_close_ret": close_ret,
            "_low_ret": low_ret,
            "_amplitude": amplitude,
            "_recovery": recovery,
        },
    )


def _prior_values(ctx, iid, end_index, count, getter):
    if end_index is None or end_index - count < 0:
        return None
    values = [getter(_bar(ctx, ctx.sessions[index], iid)) for index in range(end_index - count, end_index)]
    return values if len(values) == count and all(value is not None for value in values) else None


def _liquidity(ctx, iid, end_index):
    current = _bar(ctx, ctx.observation_date, iid)
    amount = _amount_fraction(current)
    turnover = _turnover_fraction(current)
    hard = CONFIG["hard_requirements"]["liquidity_bottom"]
    amount_threshold = Fraction(hard["amount_cny_min_inclusive"])
    turnover_threshold = Fraction(str(hard["turnover_fraction_min_inclusive"]))
    reasons = []
    if amount is None:
        reasons.append("SAM_AMOUNT_UNAVAILABLE")
    elif amount < amount_threshold:
        reasons.append("SAM_AMOUNT_BELOW_500M")
    if turnover is None:
        reasons.append("SAM_TURNOVER_UNAVAILABLE")
    elif turnover < turnover_threshold:
        reasons.append("SAM_TURNOVER_BELOW_7PCT")
    status = "UNKNOWN" if any(reason.endswith("UNAVAILABLE") for reason in reasons) else "FAIL" if reasons else "PASS"

    prior5_amount = _prior_values(ctx, iid, end_index, 5, _amount_fraction)
    prior10_amount = _prior_values(ctx, iid, end_index, 10, _amount_fraction)
    prior5_turnover = _prior_values(ctx, iid, end_index, 5, _turnover_fraction)
    a = amount * len(prior5_amount) / sum(prior5_amount, Fraction(0)) if amount is not None and prior5_amount and sum(prior5_amount, Fraction(0)) > 0 else None
    high_turnover_threshold = Fraction(str(CONFIG["research_seed_parameters"]["liquidity"]["high_turnover_fraction_inclusive"]))
    high_turnover_days_5 = sum(value >= high_turnover_threshold for value in prior5_turnover or [])
    if turnover is not None and turnover >= high_turnover_threshold:
        high_turnover_days_5 += 1
    return _condition(
        status,
        reasons,
        details={
            "amount_cny": _money(amount),
            "turnover_fraction": _number(turnover),
            "amount_ratio_prior5": _number(a),
            "prior5_amount_avg_cny": _money(sum(prior5_amount, Fraction(0)) / 5) if prior5_amount else None,
            "prior10_amount_avg_cny": _money(sum(prior10_amount, Fraction(0)) / 10) if prior10_amount else None,
            "prior5_amount_min_cny": _money(min(prior5_amount)) if prior5_amount else None,
            "prior5_amount_max_cny": _money(max(prior5_amount)) if prior5_amount else None,
            "prior5_turnover_min": _number(min(prior5_turnover)) if prior5_turnover else None,
            "prior5_turnover_max": _number(max(prior5_turnover)) if prior5_turnover else None,
            "high_turnover_days_5_including_today": high_turnover_days_5 if prior5_turnover else None,
        },
    )


def _ma_reason(reason):
    return reason.replace("TCT_", "SAM_") if reason else reason


def _trend(ctx, iid, end_index):
    values, reasons = _ma_snapshot(ctx, iid, end_index, MA_WINDOWS)
    reasons = [_ma_reason(reason) for reason in reasons]
    current = _bar(ctx, ctx.observation_date, iid)
    close = _close_fraction(current)
    if close is None:
        reasons.append("SAM_COMPARABLE_CLOSE_UNAVAILABLE")
    ma_values = {str(window): _number(values.get(window)) for window in MA_WINDOWS}
    distances = {
        str(window): _number((close - value) / value) if close is not None and value is not None and value > 0 else None
        for window, value in values.items()
    }
    if reasons:
        return _condition("UNKNOWN", sorted(set(reasons)), details={
            "ma_values": ma_values,
            "dist_ma": distances,
            "ma250_policy": CONFIG["research_seed_parameters"]["stages"]["trend_pullback"]["insufficient_ma250_policy"],
            "pullback_days_below_ma5": None,
        })
    ordered = [values[window] for window in MA_WINDOWS]
    fail = []
    bullish = all(left > right for left, right in zip(ordered, ordered[1:]))
    if not bullish:
        fail.append("SAM_MA_BULLISH_ARRANGEMENT_FAIL")
    for window in LONG_CLOSE_WINDOWS:
        if close < values[window]:
            fail.append(f"SAM_CLOSE_BELOW_MA{window}")

    pullback_days, pullback_unknown = 0, []
    for index in range(end_index, -1, -1):
        row_close = _close_fraction(_bar(ctx, ctx.sessions[index], iid))
        ma5, reason = _ma_value(ctx, iid, index, 5)
        if reason or row_close is None or ma5 is None:
            pullback_unknown.append("SAM_PULLBACK_SEGMENT_PRICE_OR_MA5_UNAVAILABLE")
            break
        if row_close < ma5:
            pullback_days += 1
            continue
        break
    near_values = {
        window: abs(close - values[window]) / values[window]
        for window in (5, 10, 20)
        if values[window] is not None and values[window] > 0
    }
    matched_ma, matched_distance = min(near_values.items(), key=lambda item: (item[1], item[0]))
    status = "FAIL" if fail else "UNKNOWN" if pullback_unknown else "PASS"
    return _condition(status, fail or pullback_unknown, details={
        "ma_values": ma_values,
        "dist_ma": distances,
        "bullish_arrangement": bullish,
        "pullback_days_below_ma5": pullback_days,
        "matched_ma": matched_ma,
        "matched_ma_distance": _number(matched_distance),
    })


def _market_position_context(ctx, end_index):
    instrument_limits = {}
    market_heights = []
    close_limit_count = 0
    touched_not_closed_count = 0
    if end_index is not None:
        for candidate in ctx.eligible_ids:
            cevents = [
                _bar_limit_event(_bar(ctx, session, candidate), candidate, session)
                for session in ctx.sessions[max(0, end_index - 19) : end_index + 1]
            ]
            cann = annotate_streaks(cevents) if cevents else []
            if not cann:
                continue
            event = cann[-1]
            instrument_limits[candidate] = {
                "close_limit_up": event.close_limit_up,
                "touched_limit_up": event.touched_limit_up,
                "touched_not_closed": event.touched_not_closed,
                "streak_exchange": event.streak_exchange,
                "streak_lower_bound": event.streak_lower_bound,
                "streak_quality": event.streak_quality,
            }
            close_limit_count += event.close_limit_up == TRUE
            touched_not_closed_count += event.touched_not_closed == TRUE
            if event.close_limit_up == TRUE and event.streak_exchange is not None:
                market_heights.append(event.streak_exchange)
    return {
        "instrument_limits": instrument_limits,
        "return_populations": {
            str(window): [ret for candidate in ctx.eligible_ids if (ret := _price_return(ctx, candidate, end_index, window)) is not None]
            for window in (10, 20)
        },
        "market_width": {
            "close_limit_count": close_limit_count,
            "touched_not_closed_count": touched_not_closed_count,
            "verified_max_height": max(market_heights) if market_heights else None,
        },
        "status_label": (
            "HIGH" if close_limit_count >= 50 or (market_heights and max(market_heights) >= 5)
            else "MEDIUM" if close_limit_count >= 15 or (market_heights and max(market_heights) >= 3)
            else "LOW"
        ),
    }


def _limit_market_status(ctx, iid, end_index, return_values, market_position):
    current = market_position["instrument_limits"].get(iid)
    percentiles = {}
    for window in (10, 20):
        value = return_values.get(str(window))
        population = market_position["return_populations"].get(str(window), [])
        percentiles[str(window)] = percentile_rank_midpoint(value, population) if value is not None and population else None
    return {
        "instrument_limit": {
            "close_limit_up": current.get("close_limit_up") if current else UNKNOWN,
            "touched_limit_up": current.get("touched_limit_up") if current else UNKNOWN,
            "touched_not_closed": current.get("touched_not_closed") if current else UNKNOWN,
            "streak_exchange": current.get("streak_exchange") if current else None,
            "streak_lower_bound": current.get("streak_lower_bound") if current else None,
            "streak_quality": current.get("streak_quality") if current else "UNKNOWN",
        },
        "market_width": market_position["market_width"],
        "return_percentiles": percentiles,
        "status_label": market_position["status_label"],
        "status_label_policy": "DESCRIPTIVE_ONLY_WITH_VISIBLE_LIMIT_EVIDENCE",
    }


def _sector_stats_for_theme(ctx, iid, theme, end_index, return_values):
    members = sorted(set(theme.get("member_ids") or []) & set(ctx.eligible_ids))
    if iid not in members:
        return None
    peers = [member for member in members if member != iid]
    expected = len(peers)
    seed = CONFIG["research_seed_parameters"]["sector_evidence"]
    windows = {}
    best_coverage = Fraction(0)
    has_return = False
    for window in SECTOR_WINDOWS:
        returns = [(peer, _price_return(ctx, peer, end_index, window)) for peer in peers]
        valid = [value for _, value in returns if value is not None]
        coverage = Fraction(len(valid), expected) if expected else Fraction(0)
        best_coverage = max(best_coverage, coverage)
        has_return |= bool(valid)
        stock_ret = return_values.get(str(window))
        peer_mean = sum(valid, Fraction(0)) / len(valid) if valid else None
        rs = stock_ret - peer_mean if stock_ret is not None and peer_mean is not None else None
        amount_changes = [value for peer in peers if (value := _amount_change(ctx, peer, end_index, window)) is not None]
        windows[str(window)] = {
            "peer_expected": expected,
            "peer_return_valid_count": len(valid),
            "peer_return_coverage": _number(coverage),
            "peer_equal_weight_return_pct": _pct(peer_mean),
            "peer_median_return_pct": _pct(median(valid)) if valid else None,
            "peer_up_fraction": _number(Fraction(sum(value > 0 for value in valid), len(valid))) if valid else None,
            "peer_amount_change_median_pct": _pct(median(amount_changes)) if amount_changes else None,
            "stock_return_pct": _pct(stock_ret),
            "rs_sector_pct": _pct(rs),
        }
    current_peer_returns = [value for peer in peers if (value := _price_return(ctx, peer, end_index, 1)) is not None]
    strong_gain = Fraction(str(seed["strong_gain_return_1d_min_inclusive"]))
    close_limit_count = 0
    if end_index is not None:
        close_limit_count = sum(_bar_limit_event(_bar(ctx, ctx.observation_date, peer), peer, ctx.observation_date).close_limit_up == TRUE for peer in peers)
    state = "UNKNOWN"
    if current_peer_returns:
        peer_mean_1 = sum(current_peer_returns, Fraction(0)) / len(current_peer_returns)
        stock_ret_1 = return_values.get("1")
        up_fraction = Fraction(sum(value > 0 for value in current_peer_returns), len(current_peer_returns))
        if peer_mean_1 > 0 and up_fraction >= Fraction(3, 5):
            state = "BROAD_STRENGTH"
        elif peer_mean_1 <= 0 and stock_ret_1 is not None and stock_ret_1 > peer_mean_1:
            state = "DIVERGENCE"
        elif peer_mean_1 < 0:
            state = "FADE"
        elif stock_ret_1 is not None and stock_ret_1 > peer_mean_1:
            state = "REPAIR"
    status = "PASS"
    reasons = []
    if expected < seed["min_other_valid_members_inclusive"]:
        status = "FAIL"
        reasons.append("SAM_SECTOR_PEER_COUNT_BELOW_MIN")
    elif not has_return:
        status = "UNKNOWN"
        reasons.append("SAM_SECTOR_PEER_RETURNS_UNAVAILABLE")
    elif best_coverage < Fraction(str(seed["min_peer_return_coverage_fraction_inclusive"])):
        status = "UNKNOWN"
        reasons.append("SAM_SECTOR_PEER_RETURN_COVERAGE_INSUFFICIENT")
    return {
        "theme_id": theme.get("theme_id"),
        "theme_name": theme.get("name"),
        "theme_type": theme.get("theme_type"),
        "status": status,
        "reason_codes": reasons,
        "member_count": len(members),
        "peer_expected": expected,
        "day_state": state,
        "strong_gain_count_1d": sum(value >= strong_gain for value in current_peer_returns),
        "close_limit_count_1d": close_limit_count,
        "windows": windows,
        "source_refs": list(theme.get("source_refs") or []),
    }


def _sector(ctx, iid, visible_themes, end_index, return_values):
    candidates = [
        row for theme in visible_themes
        if (row := _sector_stats_for_theme(ctx, iid, theme, end_index, return_values)) is not None
    ]
    candidates.sort(key=lambda row: row["theme_id"])
    if not candidates:
        if ctx.bundle.themes:
            return _condition("FAIL", ["SAM_NO_AUTHORIZED_SECTOR_LINKAGE"], details={"primary_theme_id": None}), []
        return _condition("UNKNOWN", ["SAM_SECTOR_DATA_UNAVAILABLE"], details={"primary_theme_id": None}), []
    if any(row["status"] == "PASS" for row in candidates):
        return _condition("PASS", details={"primary_theme_id": candidates[0]["theme_id"], "primary_theme_order": "theme_id_asc"}), candidates
    status = "UNKNOWN" if any(row["status"] == "UNKNOWN" for row in candidates) else "FAIL"
    reasons = sorted({reason for row in candidates for reason in row.get("reason_codes", [])})
    return _condition(status, reasons, details={"primary_theme_id": candidates[0]["theme_id"], "primary_theme_order": "theme_id_asc"}), candidates


def _classified_capital(row):
    seed = CONFIG["research_seed_parameters"]["funds"]
    layer = str(row.get("source_layer") or row.get("evidence_layer") or "").upper()
    classification = str(row.get("classification") or "").upper()
    return (
        layer in set(seed["classified_source_layers"])
        or row.get("classified_active") is True
        or ("ACTIVE" in classification and ("SEAT" in classification or "HOT_MONEY" in classification))
    )


def _proxy_capital(row):
    layer = str(row.get("source_layer") or row.get("evidence_layer") or "").upper()
    return layer in set(CONFIG["research_seed_parameters"]["funds"]["proxy_source_layers"])


def _visible_theme_funds(ctx, iid, theme, end_index):
    seed = CONFIG["research_seed_parameters"]["funds"]
    sessions_5 = [
        date.fromisoformat(day)
        for day in ctx.sessions[max(0, end_index - seed["lookback_sessions"] + 1) : end_index + 1]
    ] if end_index is not None else []
    session_set = {str(day) for day in sessions_5}
    peers = set(theme.get("member_ids") or []) & set(ctx.eligible_ids)
    if seed.get("peers_exclude_self", True):
        peers.discard(iid)
    visible = [
        dict(row)
        for row in ctx.bundle.funds
        if _visible_fund(row, ctx.cutoff, ctx.bundle.coverage_type)
        and str(row.get("theme_id")) == str(theme.get("theme_id"))
        and str(row.get("instrument_id")) in peers
        and str(_fund_date(row.get("session") or row.get("trade_date") or row.get("event_session"))) in session_set
    ]
    return visible, sessions_5


def _dedupe_funds(rows):
    deduped, seen = [], set()
    for row in rows:
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
    return deduped


def _overlap_risk(rows):
    ranges = defaultdict(list)
    for row in rows:
        window = _fund_window(row)
        if window is None or window[0] is None or window[1] is None:
            return True, "SAM_FUNDS_WINDOW_UNAVAILABLE_OR_INVALID"
        holder = (str(row.get("instrument_id")), str(row.get("seat_id") or row.get("seat_name") or row.get("source_id") or ""))
        ranges[holder].append((window[0], window[1], row["_dedupe_key"]))
    for values in ranges.values():
        values.sort()
        for left, right in zip(values, values[1:]):
            if left[1] >= right[0] and left[2] != right[2]:
                return True, "SAM_FUNDS_OVERLAPPING_CLASSIFIED_WINDOWS"
    return False, None


def _grade_funds(metrics, sessions):
    mapping = CONFIG["research_seed_parameters"]["funds"]["grade_mapping"]
    last_session = metrics.get("_last_session")
    days_since = sessions.index(sessions[-1]) - sessions.index(last_session) if last_session in sessions else None
    metrics["days_since_last_activity"] = days_since
    for grade in ("STRONG", "MODERATE"):
        rule = mapping[grade]
        if metrics["active_instrument_count"] < rule["min_active_instruments_inclusive"]:
            continue
        if metrics["active_seat_count"] < rule["min_active_seats_inclusive"]:
            continue
        if metrics["_dedup_buy"] < Decimal(str(rule["min_dedup_buy_amount_cny_inclusive"])):
            continue
        if rule.get("require_dedup_net_amount_positive") and metrics["_dedup_net"] <= 0:
            continue
        if days_since is None or days_since > rule["max_days_since_last_activity_inclusive"]:
            continue
        return grade
    return "WEAK"


def _funds_for_theme(ctx, iid, theme, end_index):
    if not ctx.bundle.funds:
        return _condition("UNKNOWN", ["SAM_FUNDS_DATA_UNAVAILABLE"], details={"theme_id": theme.get("theme_id"), "grade": "UNKNOWN"}), {}
    visible, sessions = _visible_theme_funds(ctx, iid, theme, end_index)
    classified = _dedupe_funds([row for row in visible if _classified_capital(row)])
    proxy = [row for row in visible if _proxy_capital(row)]
    if not classified:
        reason = "SAM_FUNDS_PROXY_ONLY_PENDING_VERIFICATION" if proxy else "SAM_NO_CLASSIFIED_SECTOR_FUNDS_IN_WINDOW"
        return _condition("UNKNOWN", [reason], details={"theme_id": theme.get("theme_id"), "grade": "UNKNOWN"}), {
            "theme_id": theme.get("theme_id"),
            "classified_events": 0,
            "proxy_events": len(proxy),
        }
    overlap, overlap_reason = _overlap_risk(classified)
    if overlap:
        return _condition("UNKNOWN", [overlap_reason], details={"theme_id": theme.get("theme_id"), "grade": "UNKNOWN"}), {
            "theme_id": theme.get("theme_id"),
            "classified_events": len(classified),
            "overlapping_window_risk": True,
        }
    total_buy, total_sell, total_net = Decimal(0), Decimal(0), Decimal(0)
    active_instruments, active_seats, evidence, last_session = set(), set(), [], None
    for row in classified:
        buy, net = _fund_amounts(row)
        sell = _as_decimal(row.get("sell_amount_cny"))
        if buy is None or net is None:
            return _condition("UNKNOWN", ["SAM_FUNDS_AMOUNT_UNAVAILABLE"], details={"theme_id": theme.get("theme_id"), "grade": "UNKNOWN"}), {}
        sell = sell or Decimal(0)
        total_buy += buy
        total_sell += sell
        total_net += net
        instrument = str(row.get("instrument_id"))
        seat = str(row.get("seat_id") or row.get("seat_name") or row.get("source_id") or "")
        session = _fund_date(row.get("session") or row.get("trade_date") or row.get("event_session"))
        active_instruments.add(instrument)
        if seat:
            active_seats.add(seat)
        if session and (last_session is None or session > last_session):
            last_session = session
        evidence.append({
            "instrument_id": instrument,
            "session": str(session) if session else None,
            "seat_id": row.get("seat_id"),
            "seat_name": row.get("seat_name"),
            "buy_amount_cny": _money(buy),
            "sell_amount_cny": _money(sell),
            "net_amount_cny": _money(net),
            "source_layer": row.get("source_layer") or row.get("evidence_layer"),
        })
    metrics = {
        "theme_id": theme.get("theme_id"),
        "classified_events": len(classified),
        "proxy_events": len(proxy),
        "active_instrument_count": len(active_instruments),
        "active_seat_count": len(active_seats),
        "dedup_buy_amount_cny": _money(total_buy),
        "dedup_sell_amount_cny": _money(total_sell),
        "dedup_net_amount_cny": _money(total_net),
        "overlapping_window_risk": False,
        "evidence": evidence,
        "_dedup_buy": total_buy,
        "_dedup_net": total_net,
        "_last_session": str(last_session) if last_session else None,
    }
    grade = _grade_funds(metrics, list(map(str, sessions)))
    status = "PASS" if grade in set(CONFIG["hard_requirements"]["funds_linkage"]["required_grade"]) else "FAIL"
    reason = [] if status == "PASS" else ["SAM_FUNDS_GRADE_WEAK"]
    public_metrics = {key: value for key, value in metrics.items() if not key.startswith("_")}
    public_metrics["grade"] = grade
    return _condition(status, reason, details={"theme_id": theme.get("theme_id"), "grade": grade}), public_metrics


def _funds(ctx, iid, visible_themes, sector_candidates, end_index):
    if not sector_candidates:
        return _condition("UNKNOWN", ["SAM_FUNDS_WAITING_FOR_SECTOR_LINKAGE"], details={"grade": "UNKNOWN"}), []
    theme_by_id = {row["theme_id"]: row for row in visible_themes}
    rows = []
    for sector in sector_candidates:
        theme = theme_by_id.get(sector["theme_id"])
        if theme is None:
            continue
        condition, metrics = _funds_for_theme(ctx, iid, theme, end_index)
        rows.append({"theme_id": sector["theme_id"], "funds_condition": condition, "funds_metrics": metrics})
    passing = [row for row in rows if row["funds_condition"]["status"] == "PASS"]
    if passing:
        return _condition("PASS", details={"grade": passing[0]["funds_condition"].get("grade"), "primary_theme_id": passing[0]["theme_id"]}), rows
    if not rows:
        return _condition("UNKNOWN", ["SAM_FUNDS_WAITING_FOR_SECTOR_LINKAGE"], details={"grade": "UNKNOWN"}), rows
    status = "UNKNOWN" if any(row["funds_condition"]["status"] == "UNKNOWN" for row in rows) else "FAIL"
    reasons = sorted({reason for row in rows for reason in row["funds_condition"].get("reason_codes", [])})
    grade = "UNKNOWN" if status == "UNKNOWN" else "WEAK"
    return _condition(status, reasons, details={"grade": grade, "primary_theme_id": rows[0]["theme_id"]}), rows


def _sector_rs_support(sector_candidates):
    for row in sector_candidates:
        for window in row.get("windows", {}).values():
            value = _frac(window.get("rs_sector_pct"))
            if value is not None and value > 0:
                return True
    return False


def _stage_start(return_values, liquidity):
    seed = CONFIG["research_seed_parameters"]["stages"]["start"]
    a = _frac(liquidity.get("amount_ratio_prior5"))
    ret5 = return_values.get("5")
    if a is None or ret5 is None:
        return _condition("UNKNOWN", ["SAM_START_A_OR_RET5_UNAVAILABLE"])
    reasons = []
    if a < Fraction(str(seed["amount_ratio_prior5_min_inclusive"])):
        reasons.append("SAM_START_AMOUNT_RATIO_BELOW_SEED")
    if ret5 <= Fraction(str(seed["ret5_min_exclusive"])):
        reasons.append("SAM_START_RET5_NOT_POSITIVE")
    return _condition("FAIL" if reasons else "PASS", reasons, details={
        "amount_ratio_policy": "APPLIES_ONLY_TO_START_STAGE",
        "A": _number(a),
        "ret5_pct": _pct(ret5),
    })


def _stage_acceleration(return_values, liquidity, market_status, rs_market):
    seed = CONFIG["research_seed_parameters"]["stages"]["acceleration"]
    points, unknown = [], []
    for window, threshold, code in (
        ("3", seed["ret3_min_inclusive"], "SAM_ACCEL_RET3_STRONG"),
        ("5", seed["ret5_min_inclusive"], "SAM_ACCEL_RET5_STRONG"),
        ("10", seed["ret10_min_inclusive"], "SAM_ACCEL_RET10_STRONG"),
    ):
        value = return_values.get(window)
        if value is None:
            unknown.append(code + "_UNAVAILABLE")
        elif value >= Fraction(str(threshold)):
            points.append(code)
    high_turnover_days = liquidity.get("high_turnover_days_5_including_today")
    if high_turnover_days is None:
        unknown.append("SAM_ACCEL_HIGH_TURNOVER_PERSISTENCE_UNAVAILABLE")
    elif high_turnover_days >= seed["high_turnover_days_5_min_inclusive"]:
        points.append("SAM_ACCEL_HIGH_TURNOVER_PERSISTENCE")
    if market_status.get("instrument_limit", {}).get("close_limit_up") == TRUE:
        points.append("SAM_ACCEL_CLOSE_LIMIT_UP")
    if any(row.get("status") == "PASS" for row in rs_market.get("windows", {}).values()):
        points.append("SAM_ACCEL_RS_MARKET_POSITIVE")
    if len(points) >= seed["evidence_points_min_inclusive"]:
        return _condition("PASS", details={"evidence_points": points, "unknown_points": unknown})
    if points or unknown:
        return _condition("UNKNOWN" if unknown else "FAIL", unknown or ["SAM_ACCEL_EVIDENCE_POINTS_BELOW_SEED"], details={"evidence_points": points, "unknown_points": unknown})
    return _condition("FAIL", ["SAM_ACCEL_NO_EVIDENCE"], details={"evidence_points": points})


def _stage_high_level_divergence(return_values, daily_path, market_status, rs_market, sector_candidates):
    seed = CONFIG["research_seed_parameters"]["stages"]["high_level_divergence"]
    if daily_path.get("daily_path_status") != "AVAILABLE":
        return _condition("UNKNOWN", daily_path.get("reason_codes") or ["SAM_HLD_DAILY_PATH_UNAVAILABLE"])
    ret5, ret10 = return_values.get("5"), return_values.get("10")
    percentile10 = market_status.get("return_percentiles", {}).get("10")
    high_level = []
    if ret5 is not None and ret5 >= Fraction(str(seed["ret5_context_min_inclusive"])):
        high_level.append("SAM_HLD_RET5_CONTEXT")
    if ret10 is not None and ret10 >= Fraction(str(seed["ret10_context_min_inclusive"])):
        high_level.append("SAM_HLD_RET10_CONTEXT")
    if percentile10 is not None and percentile10 >= seed["percentile_context_min_inclusive"]:
        high_level.append("SAM_HLD_RETURN_PERCENTILE_CONTEXT")
    if market_status.get("instrument_limit", {}).get("streak_lower_bound"):
        high_level.append("SAM_HLD_LIMIT_STREAK_CONTEXT")
    if not high_level:
        unknown = ret5 is None and ret10 is None and percentile10 is None
        return _condition("UNKNOWN" if unknown else "FAIL", ["SAM_HLD_HIGH_LEVEL_CONTEXT_UNAVAILABLE" if unknown else "SAM_HLD_HIGH_LEVEL_CONTEXT_NOT_MET"])

    amplitude = daily_path.get("_amplitude")
    low_ret = daily_path.get("_low_ret")
    close_ret = daily_path.get("_close_ret")
    recovery = daily_path.get("_recovery")
    divergence = []
    if amplitude is not None and amplitude >= Fraction(str(seed["amplitude_min_inclusive"])):
        divergence.append("SAM_HLD_AMPLITUDE")
    if low_ret is not None and low_ret <= Fraction(str(seed["deep_low_return_max_inclusive"])):
        divergence.append("SAM_HLD_DEEP_LOW")
    if daily_path.get("touched_limit_down") == TRUE:
        divergence.append("SAM_HLD_TOUCHED_LIMIT_DOWN")
    if not divergence:
        return _condition("FAIL", ["SAM_HLD_DIVERGENCE_PATH_NOT_MET"], details={"high_level_context": high_level})

    repair = []
    if recovery is not None and recovery >= Fraction(str(seed["recovery_points_min_inclusive"])):
        repair.append("SAM_HLD_RECOVERY_POINTS")
    if close_ret is not None and low_ret is not None and close_ret > 0 and low_ret < 0:
        repair.append("SAM_HLD_REPAIRED_FROM_UNDERWATER")
    if any(row.get("status") == "PASS" for row in rs_market.get("windows", {}).values()):
        repair.append("SAM_HLD_RS_MARKET_SUPPORT")
    if _sector_rs_support(sector_candidates):
        repair.append("SAM_HLD_RS_SECTOR_SUPPORT")
    if not repair:
        return _condition("FAIL", ["SAM_HLD_REPAIR_EVIDENCE_NOT_MET"], details={"high_level_context": high_level, "divergence_evidence": divergence})
    return _condition("PASS", details={
        "high_level_context": high_level,
        "divergence_evidence": divergence,
        "repair_evidence": repair,
        "amount_ratio_policy": "NOT_USED_FOR_HIGH_LEVEL_DIVERGENCE_GATE",
        "clv_policy": "NO_HARD_CLV_THRESHOLD",
    })


def _stage_trend_pullback(trend):
    seed = CONFIG["research_seed_parameters"]["stages"]["trend_pullback"]
    if trend["status"] == "UNKNOWN":
        return _condition("UNKNOWN", trend.get("reason_codes") or ["SAM_TREND_PULLBACK_MA_UNKNOWN"])
    if trend["status"] == "FAIL":
        return _condition("FAIL", trend.get("reason_codes") or ["SAM_TREND_PULLBACK_TREND_FAIL"])
    days = trend.get("pullback_days_below_ma5")
    distance = _frac(trend.get("matched_ma_distance"))
    min_days, max_days = seed["below_ma5_sessions_inclusive"]
    reasons = []
    if days is None:
        reasons.append("SAM_TREND_PULLBACK_DAYS_UNKNOWN")
    elif not min_days <= days <= max_days:
        reasons.append("SAM_TREND_PULLBACK_DAYS_OUTSIDE_SEED")
    if distance is None:
        reasons.append("SAM_TREND_PULLBACK_DISTANCE_UNKNOWN")
    elif distance > Fraction(str(seed["near_ma_5_10_20_fraction_inclusive"])):
        reasons.append("SAM_TREND_PULLBACK_NOT_NEAR_MA5_10_20")
    return _condition("FAIL" if reasons else "PASS", reasons, details={
        "pullback_days_below_ma5": days,
        "matched_ma": trend.get("matched_ma"),
        "matched_ma_distance": trend.get("matched_ma_distance"),
        "near_ma_threshold": seed["near_ma_5_10_20_fraction_inclusive"],
    })


def _stages(return_values, daily_path, liquidity, trend, market_status, rs_market, sector_candidates):
    candidates = {
        "START": _stage_start(return_values, liquidity),
        "ACCELERATION": _stage_acceleration(return_values, liquidity, market_status, rs_market),
        "HIGH_LEVEL_DIVERGENCE": _stage_high_level_divergence(return_values, daily_path, market_status, rs_market, sector_candidates),
        "TREND_PULLBACK": _stage_trend_pullback(trend),
    }
    passing = [stage for stage in PRIMARY_STAGE_PRECEDENCE if candidates[stage]["status"] == "PASS"]
    unknown = [stage for stage in STAGE_IDS if candidates[stage]["status"] == "UNKNOWN"]
    primary = passing[0] if passing else None
    status = "PASS" if primary else "UNKNOWN" if unknown else "FAIL"
    reasons = [] if primary else ["SAM_STAGE_ALL_CANDIDATES_UNKNOWN" if status == "UNKNOWN" else "SAM_STAGE_NO_PASSING_CANDIDATE"]
    return _condition(status, reasons, details={
        "primary_stage": primary,
        "alternate_stages": [stage for stage in passing if stage != primary],
        "stage_candidates": candidates,
        "stage_label_policy": CONFIG["traceability"]["stage_label_policy"],
    })


def _row_score_key(row):
    precedence = {stage: index for index, stage in enumerate(PRIMARY_STAGE_PRECEDENCE)}
    return (
        row.get("_reference_score_exact") is None,
        -(row.get("_reference_score_exact") or Fraction(0)),
        precedence.get(row.get("primary_stage"), len(precedence)),
        row["instrument_id"],
    )


def _rank_rows(rows):
    rows.sort(key=_row_score_key)
    for rank, row in enumerate(rows, 1):
        row["rank"] = rank
    return rows


def _evaluate_rows(bundle, *, top_n=100, cutoff=None, _context=None):
    ctx = _context or _prepare_context(bundle, cutoff)
    end_index = _session_index(ctx)
    market = _market_benchmarks(ctx, end_index)
    market_position = _market_position_context(ctx, end_index)
    visible_themes, rejected_themes = _visible_stage_themes(ctx)
    reference, reference_counts = _reference_pv_scores(ctx)
    selected, diagnostics = [], []
    condition_counts = defaultdict(lambda: {"PASS": 0, "FAIL": 0, "UNKNOWN": 0})
    stage_counts = {stage: {"PASS": 0, "FAIL": 0, "UNKNOWN": 0} for stage in STAGE_IDS}
    for iid in ctx.eligible_ids:
        security = ctx.securities[iid]
        current = _bar(ctx, ctx.observation_date, iid)
        returns = _return_map(ctx, iid, end_index)
        daily_path = _daily_path(ctx, iid)
        liquidity = _liquidity(ctx, iid, end_index)
        trend = _trend(ctx, iid, end_index)
        market_status = _limit_market_status(ctx, iid, end_index, returns, market_position)
        rs_market = _relative_strength(ctx, iid, end_index, returns, market)
        sector, sector_candidates = _sector(ctx, iid, visible_themes, end_index, returns)
        funds, funds_rows = _funds(ctx, iid, visible_themes, sector_candidates, end_index)
        stages = _stages(returns, daily_path, liquidity, trend, market_status, rs_market, sector_candidates)
        conditions = {
            "liquidity_bottom": liquidity,
            "stage_identification": stages,
            "sector_linkage": sector,
            "funds_linkage": funds,
        }
        for key, condition in conditions.items():
            condition_counts[key][condition["status"]] += 1
        for stage, condition in stages["stage_candidates"].items():
            stage_counts[stage][condition["status"]] += 1
        formal_status = _combine(conditions.values())
        ref = reference.get(iid)
        return_fields = {
            f"ret{window}_pct": _pct(value)
            for window, value in returns.items()
        }
        rs_market_1 = rs_market.get("windows", {}).get("1", {}).get("rs_market_pct")
        primary_sector = sector_candidates[0] if sector_candidates else {}
        rs_sector_1 = (primary_sector.get("windows") or {}).get("1", {}).get("rs_sector_pct")
        row = {
            "strategy_id": STAGE_AWARE_ID,
            "canonical_strategy_id": STAGE_AWARE_ID,
            "instrument_id": iid,
            "name": security.name,
            "observation_date": ctx.observation_date,
            "status": formal_status,
            "formal_status": formal_status,
            "reason_codes": _condition_reasons(conditions.values()) or ["SAM_SELECTED_BY_STAGE_AWARE_FORMAL_REQUIREMENTS"],
            "condition_statuses": {key: condition["status"] for key, condition in conditions.items()},
            "conditions": conditions,
            "parameter_status": PARAMETER_STATUS,
            "parameter_status_label_zh": PARAMETER_STATUS_LABEL_ZH,
            "primary_stage": stages.get("primary_stage"),
            "alternate_stages": stages.get("alternate_stages", []),
            "stage_candidates": stages.get("stage_candidates", {}),
            "stage_label_policy": stages.get("stage_label_policy"),
            "liquidity_status": liquidity["status"],
            "sector_status": sector["status"],
            "funds_status": funds["status"],
            "funds_grade": funds.get("grade"),
            "relative_strength_status": rs_market["status"],
            "close": _number(_bar_decimal(current, "close"), places=8),
            "comparable_close": _number(_bar_decimal(current, "comparable_close"), places=8),
            "comparable_basis": (current or {}).get("comparable_basis"),
            "amount_cny": liquidity.get("amount_cny"),
            "turnover_fraction": liquidity.get("turnover_fraction"),
            "A": liquidity.get("amount_ratio_prior5"),
            **return_fields,
            "CLV": daily_path.get("CLV"),
            "amplitude_pct": daily_path.get("amplitude_pct"),
            "low_return_pct": daily_path.get("low_return_pct"),
            "high_return_pct": daily_path.get("high_return_pct"),
            "close_return_pct": daily_path.get("close_return_pct"),
            "recovery_points_pct": daily_path.get("recovery_points_pct"),
            "low_to_close_rebound_pct": daily_path.get("low_to_close_rebound_pct"),
            "RS_market_1d_pct": rs_market_1,
            "RS_sector_1d_pct": rs_sector_1,
            "shape_type": "TREND_PULLBACK" if stages.get("stage_candidates", {}).get("TREND_PULLBACK", {}).get("status") == "PASS" else None,
            "trend_strength": trend,
            "daily_path": {key: value for key, value in daily_path.items() if not key.startswith("_")},
            "relative_strength": rs_market,
            "sector_candidates": sector_candidates,
            "funds_candidates": funds_rows,
            "market_status": market_status,
            "reference_score_policy": reference_counts["ranked_by"],
            "reference_score_status": "AVAILABLE" if ref is not None else "UNAVAILABLE",
            "S_PV_REFERENCE": ref.get("S_PV") if ref else None,
            "reference_score": ref.get("S_PV") if ref else None,
            "reference_rank": ref.get("rank") if ref else None,
            "_reference_score_exact": ref.get("_S_PV") if ref else None,
        }
        target = selected if formal_status == "PASS" else diagnostics
        target.append(row)
    _rank_rows(selected)
    selected_ids = {row["instrument_id"] for row in selected}
    rows = [_public(row) for row in selected] + [_public(row) for row in diagnostics if row["instrument_id"] not in selected_ids]
    unknown = sum(1 for row in diagnostics if row["status"] == "UNKNOWN")
    failed = sum(1 for row in diagnostics if row["status"] == "FAIL")
    result_status = (
        "COMPLETE" if selected and ctx.universe_complete else
        "LIMITED_SCOPE" if selected else
        "INSUFFICIENT_EVIDENCE" if unknown else
        "EMPTY"
    )
    return ctx, {
        "strategy_id": STAGE_AWARE_ID,
        "canonical_strategy_id": STAGE_AWARE_ID,
        "implementation_version": IMPLEMENTATION_VERSION,
        "parameter_status": PARAMETER_STATUS,
        "parameter_status_label_zh": PARAMETER_STATUS_LABEL_ZH,
        "spec_conformance": "SPEC_CONFORMANT",
        "status": result_status,
        "scope_label": "FULL_SCOPE" if ctx.universe_complete else "LIMITED_SCOPE",
        "formal_list_policy": "STAGE_PASS_PLUS_LIQUIDITY_SECTOR_AND_FUNDS_NOT_WEAK_NO_FIXED_COUNT",
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
            "stage_counts": stage_counts,
            "visible_theme_count": len(visible_themes),
            "rejected_theme_evidence_count": len(rejected_themes),
            "reference_score_available": sum(1 for row in selected + diagnostics if row.get("reference_score") is not None),
            "reference_score": reference_counts,
        },
        "market_benchmarks": {key: value for key, value in market["benchmarks"].items()},
        "traceability": {
            "hard_requirements": CONFIG["hard_requirements"],
            "research_seed_parameters": CONFIG["research_seed_parameters"],
            "initial_parameters_are_not_user_rules": True,
            "initial_parameters_status_zh": PARAMETER_STATUS_LABEL_ZH,
            "stage_labels_are_research_classifications": True,
            "old_strategies_preserved": True,
        },
        "rejected_theme_evidence": rejected_themes,
        "tie_break": "reference_score desc, stage_precedence, instrument_id asc",
        "market_edge": "NOT_ESTABLISHED",
        **stage_aware_source_identity(),
    }


def run_stage_aware_momentum(bundle: MarketInput, *, top_n=100, cutoff=None, _context=None):
    _, result = _evaluate_rows(bundle, top_n=top_n, cutoff=cutoff, _context=_context)
    return result


def stage_aware_strategy_specs():
    return [{
        "strategy_id": STAGE_AWARE_ID,
        "parameter_status": PARAMETER_STATUS,
        "parameter_status_label_zh": PARAMETER_STATUS_LABEL_ZH,
        "formal_role": "FORMAL_RESEARCH_LIST_REQUIRES_STAGE_LIQUIDITY_SECTOR_AND_FUNDS",
        "hard_requirements": CONFIG["hard_requirements"],
        "research_seed_parameters": CONFIG["research_seed_parameters"],
        "traceability": CONFIG["traceability"],
        "review_policy": CONFIG["review_policy"],
        "ai_responsibilities": CONFIG["ai_responsibilities"],
        **stage_aware_source_identity(),
    }]


def audit_stage_aware_instrument(bundle: MarketInput, instrument_id: str, *, cutoff=None):
    ctx, result = _evaluate_rows(bundle, cutoff=cutoff)
    row = next((item for item in result.get("items", []) if item.get("instrument_id") == instrument_id), None)
    return {
        "instrument_id": instrument_id,
        "observation_date": ctx.observation_date,
        "strategy_id": STAGE_AWARE_ID,
        "stage_aware": row,
        "audit_policy": "FACTS_RULE_CALCULATION_JUDGMENT_UNKNOWNS_SEPARATED_BY_CONSUMER_REPORT",
        "parameter_status": PARAMETER_STATUS,
        "parameter_status_label_zh": PARAMETER_STATUS_LABEL_ZH,
    }
