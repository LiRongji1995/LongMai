"""Active stage-aware strong-stock core strategy.

Version 2 freezes the old additive STAGE_AWARE_MOMENTUM_V1 identity for
historical replay and promotes the stage-aware semantics to the active core
strategy.  The implementation records layered evidence first; FOCUS is only a
final decision label after trend, setup, behavior, context and counter-evidence
have all been evaluated without imputing UNKNOWN evidence.
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
from .events import FALSE, TRUE, UNKNOWN, annotate_streaks


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config/stage_aware_momentum_v2.yaml"
CONFIG = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))

STAGE_AWARE_ID = CONFIG["strategy_id"]
IMPLEMENTATION_VERSION = CONFIG.get("implementation_version", "R10_STAGE_AWARE_MOMENTUM_V2_DATA_REPLAY_PROSPECTIVE_ACTIVATION")
PARAMETER_STATUS = CONFIG["parameter_status"]
PARAMETER_STATUS_LABEL_ZH = CONFIG["parameter_status_label_zh"]
LEGACY_STAGE_AWARE_IDS = tuple(CONFIG.get("legacy_read_aliases", ()))
SPEC_REVISION = CONFIG.get("spec_revision", "R10_DATA_REPLAY_PROSPECTIVE_ACTIVATION")
STAGE_IDS = tuple(CONFIG["research_seed_parameters"]["setups"]["ids"])
PRIMARY_STAGE_PRECEDENCE = tuple(CONFIG["research_seed_parameters"]["setups"]["display_order"])
PRICE_WINDOWS = tuple(CONFIG["research_seed_parameters"]["price_strength_windows"])
RS_WINDOWS = tuple(CONFIG["research_seed_parameters"]["relative_strength"]["windows"])
SECTOR_WINDOWS = tuple(CONFIG["research_seed_parameters"]["sector_evidence"]["windows"])
MA_WINDOWS = tuple(CONFIG["research_seed_parameters"]["trend_state"]["ma_windows"])
LONG_CLOSE_WINDOWS = tuple(CONFIG["research_seed_parameters"]["trend_state"]["close_minimums"])


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
        "stage_aware_spec_revision": SPEC_REVISION,
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
    coverage_min = Fraction(str(CONFIG["research_seed_parameters"]["market_context"]["min_benchmark_coverage_fraction_inclusive"]))
    values = {}
    for window in set(PRICE_WINDOWS) | set(RS_WINDOWS):
        returns = [value for iid in ctx.eligible_ids if (value := _price_return(ctx, iid, end_index, window)) is not None]
        coverage = Fraction(len(returns), len(ctx.eligible_ids)) if ctx.eligible_ids else Fraction(0)
        status = "COMPLETE" if returns and coverage >= coverage_min else "INSUFFICIENT_COVERAGE" if returns else "UNKNOWN"
        values[str(window)] = {
            "status": status,
            "benchmark_return": sum(returns, Fraction(0)) / len(returns) if returns else None,
            "valid_count": len(returns),
            "expected_count": len(ctx.eligible_ids),
            "coverage": coverage,
            "membership_basis": "DATED_ELIGIBLE_MAINBOARD_A_COMMON_AT_OBSERVATION_DATE",
            "reason_codes": [] if status == "COMPLETE" else ["SAM_MARKET_BENCHMARK_COVERAGE_INSUFFICIENT"] if returns else ["SAM_MARKET_BENCHMARK_RETURNS_UNAVAILABLE"],
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
                    "observed_count": row["valid_count"],
                    "valid_count": row["valid_count"],
                    "expected_count": row["expected_count"],
                    "eligible_count": row["expected_count"],
                    "coverage": _number(row["coverage"]),
                    "membership_basis": row["membership_basis"],
                    "observation_date": ctx.observation_date,
                    "decision_cutoff": ctx.cutoff.isoformat(),
                    "reason_codes": row["reason_codes"],
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
        benchmark_row = market["_primary_values"].get(str(window), {})
        benchmark = benchmark_row.get("benchmark_return") if benchmark_row.get("status") == "COMPLETE" else None
        rs = stock_ret - benchmark if stock_ret is not None and benchmark is not None else None
        windows[str(window)] = {
            "stock_return_pct": _pct(stock_ret),
            "benchmark_return_pct": _pct(benchmark_row.get("benchmark_return")),
            "rs_market_pct": _pct(rs),
            "status": "PASS" if rs is not None and rs > 0 else "FAIL" if rs is not None else "UNKNOWN",
            "benchmark_status": benchmark_row.get("status", "UNKNOWN"),
            "benchmark_coverage": _number(benchmark_row.get("coverage")),
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


def _recent_values_including_t(ctx, iid, end_index, count, getter):
    if end_index is None or end_index - count + 1 < 0:
        return None
    values = [getter(_bar(ctx, ctx.sessions[index], iid)) for index in range(end_index - count + 1, end_index + 1)]
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
    hard_fail_reasons = [reason for reason in reasons if not reason.endswith("UNAVAILABLE")]
    unknown_reasons = [reason for reason in reasons if reason.endswith("UNAVAILABLE")]
    status = "FAIL" if hard_fail_reasons else "UNKNOWN" if unknown_reasons else "PASS"

    prior5_amount = _prior_values(ctx, iid, end_index, 5, _amount_fraction)
    prior10_amount = _prior_values(ctx, iid, end_index, 10, _amount_fraction)
    prior5_turnover = _prior_values(ctx, iid, end_index, 5, _turnover_fraction)
    recent5_turnover = _recent_values_including_t(ctx, iid, end_index, 5, _turnover_fraction)
    a = amount * len(prior5_amount) / sum(prior5_amount, Fraction(0)) if amount is not None and prior5_amount and sum(prior5_amount, Fraction(0)) > 0 else None
    high_turnover_threshold = Fraction(str(CONFIG["research_seed_parameters"]["liquidity"]["high_turnover_fraction_inclusive"]))
    high_turnover_days_prior5 = sum(value >= high_turnover_threshold for value in prior5_turnover or []) if prior5_turnover else None
    high_turnover_days_recent5 = sum(value >= high_turnover_threshold for value in recent5_turnover or []) if recent5_turnover else None
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
            "high_turnover_days_5_excluding_T": high_turnover_days_prior5,
            "high_turnover_days_5_including_T": high_turnover_days_recent5,
            "high_turnover_window_policy": {
                "recent_5_including_T": "sessions[end_index-4:end_index+1]",
                "prior_5_excluding_T": "sessions[end_index-5:end_index]",
            },
        },
    )


def _ma_reason(reason):
    return reason.replace("TCT_", "SAM_") if reason else reason


def _known_limit_flag(value):
    return value in (TRUE, FALSE)


def _rs_exact(rs_market, window):
    row = (rs_market.get("windows") or {}).get(str(window), {})
    value = _frac(row.get("rs_market_pct"))
    return value / 100 if value is not None else None


def _sector_rs_exact(sector_candidates, window):
    values = []
    for row in sector_candidates:
        if row.get("status") != "PASS":
            continue
        window_row = ((row.get("windows") or {}).get(str(window)) or {})
        if window_row.get("coverage_status") != "COMPLETE":
            continue
        value = _frac(window_row.get("rs_sector_pct"))
        if value is not None:
            values.append(value / 100)
    return max(values) if values else None


def _close_value(ctx, iid, index):
    if index is None or index < 0 or index >= len(ctx.sessions):
        return None
    return _close_fraction(_bar(ctx, ctx.sessions[index], iid))


def _prior_window_return(ctx, iid, end_index, lookback):
    return _window_return(ctx, iid, end_index - 1, lookback) if end_index is not None else None


def _prior_close_high(ctx, iid, end_index, lookback):
    if end_index is None or end_index - lookback < 0:
        return None
    closes = [_close_value(ctx, iid, index) for index in range(end_index - lookback, end_index)]
    return max(closes) if len(closes) == lookback and all(value is not None for value in closes) else None


def _average_recent(ctx, iid, end_index, count, getter):
    if end_index is None or end_index - count + 1 < 0:
        return None
    values = [getter(_bar(ctx, ctx.sessions[index], iid)) for index in range(end_index - count + 1, end_index + 1)]
    return sum(values, Fraction(0)) / count if len(values) == count and all(value is not None for value in values) else None


def _state_from_delta(value, band, *, rising="RISING", stable="STABLE", falling="FALLING"):
    if value is None:
        return "UNKNOWN"
    if value > band:
        return rising
    if value < -band:
        return falling
    return stable


def _market_return_at(ctx, end_index, lookback):
    if end_index is None:
        return None, {
            "status": "UNKNOWN",
            "reason_codes": ["SAM_MARKET_BENCHMARK_RETURNS_UNAVAILABLE"],
            "valid_count": 0,
            "expected_count": len(ctx.eligible_ids),
            "coverage": Fraction(0),
        }
    coverage_min = Fraction(str(CONFIG["research_seed_parameters"]["market_context"]["min_benchmark_coverage_fraction_inclusive"]))
    returns = [value for candidate in ctx.eligible_ids if (value := _price_return(ctx, candidate, end_index, lookback)) is not None]
    coverage = Fraction(len(returns), len(ctx.eligible_ids)) if ctx.eligible_ids else Fraction(0)
    if returns and coverage >= coverage_min:
        status, reasons = "COMPLETE", []
    elif returns:
        status, reasons = "INSUFFICIENT_COVERAGE", ["SAM_MARKET_BENCHMARK_COVERAGE_INSUFFICIENT"]
    else:
        status, reasons = "UNKNOWN", ["SAM_MARKET_BENCHMARK_RETURNS_UNAVAILABLE"]
    return (sum(returns, Fraction(0)) / len(returns) if status == "COMPLETE" else None), {
        "status": status,
        "reason_codes": reasons,
        "valid_count": len(returns),
        "expected_count": len(ctx.eligible_ids),
        "coverage": coverage,
    }


def _rs_at(ctx, iid, end_index, lookback):
    stock = _price_return(ctx, iid, end_index, lookback)
    market, coverage = _market_return_at(ctx, end_index, lookback)
    return (stock - market if stock is not None and market is not None else None), {
        **coverage,
        "stock_return_pct": _pct(stock),
        "benchmark_return_pct": _pct(market),
    }


def _combined_momentum_state(return_state, rs_state):
    states = [state for state in (return_state, rs_state) if state != "UNKNOWN"]
    if len(states) < 2:
        return "UNKNOWN"
    if "FALLING" in states:
        return "FALLING"
    if "RISING" in states:
        return "RISING"
    return "STABLE"


def _strength_momentum(ctx, iid, end_index, return_values, rs_market, liquidity):
    seed = CONFIG["research_seed_parameters"]["strength_momentum"]
    accel = CONFIG["research_seed_parameters"]["setups"]["acceleration"]
    count = seed["equal_window_sessions"]
    band = Fraction(str(seed["stable_band_fraction_inclusive"]))
    recent_ret = _price_return(ctx, iid, end_index, count)
    previous_ret = _price_return(ctx, iid, end_index - count, count) if end_index is not None and end_index - count >= 0 else None
    return_momentum = recent_ret - previous_ret if recent_ret is not None and previous_ret is not None else None
    recent_rs, recent_rs_coverage = _rs_at(ctx, iid, end_index, count)
    previous_rs, previous_rs_coverage = _rs_at(ctx, iid, end_index - count, count) if end_index is not None and end_index - count >= 0 else (None, {
        "status": "UNKNOWN",
        "reason_codes": ["SAM_RS_PREVIOUS_EQUAL_WINDOW_UNAVAILABLE"],
        "valid_count": 0,
        "expected_count": len(ctx.eligible_ids),
        "coverage": Fraction(0),
    })
    rs_momentum = recent_rs - previous_rs if recent_rs is not None and previous_rs is not None else None
    recent_amount = _average_recent(ctx, iid, end_index, count, _amount_fraction)
    previous_amount = _average_recent(ctx, iid, end_index - count, count, _amount_fraction) if end_index is not None and end_index - count >= 0 else None
    amount_momentum = recent_amount / previous_amount - 1 if recent_amount is not None and previous_amount and previous_amount > 0 else None
    recent_turnover = _average_recent(ctx, iid, end_index, count, _turnover_fraction)
    previous_turnover = _average_recent(ctx, iid, end_index - count, count, _turnover_fraction) if end_index is not None and end_index - count >= 0 else None
    turnover_momentum = recent_turnover - previous_turnover if recent_turnover is not None and previous_turnover is not None else None
    activity_known = [value for value in (amount_momentum, turnover_momentum) if value is not None]
    activity_momentum = sum(activity_known, Fraction(0)) / len(activity_known) if activity_known else None

    ret5, ret10 = return_values.get("5"), return_values.get("10")
    rs5 = _rs_exact(rs_market, 5)
    level_points, level_unknown = [], []
    if ret5 is None:
        level_unknown.append("SAM_STRENGTH_LEVEL_RET5_UNAVAILABLE")
    elif ret5 >= Fraction(str(accel["high_level_ret5_min_inclusive"])):
        level_points.append("SAM_STRENGTH_LEVEL_RET5_HIGH")
    if ret10 is None:
        level_unknown.append("SAM_STRENGTH_LEVEL_RET10_UNAVAILABLE")
    elif ret10 >= Fraction(str(accel["high_level_ret10_min_inclusive"])):
        level_points.append("SAM_STRENGTH_LEVEL_RET10_HIGH")
    if rs5 is None:
        level_unknown.append("SAM_STRENGTH_LEVEL_RS5_UNAVAILABLE")
    elif rs5 > Fraction(str(accel["high_level_rs5_min_exclusive"])):
        level_points.append("SAM_STRENGTH_LEVEL_RS5_HIGH")
    if level_points:
        level_state = "HIGH"
    elif len(level_unknown) == 3:
        level_state = "UNKNOWN"
    elif level_unknown:
        level_state = "UNKNOWN"
    else:
        level_state = "LOW"

    return_state = _state_from_delta(return_momentum, band)
    rs_state = _state_from_delta(rs_momentum, band)
    strength_state = _combined_momentum_state(return_state, rs_state)
    activity_state = _state_from_delta(activity_momentum, band)
    return {
        "calculation_id": seed["calculation_id"],
        "equal_window_sessions": count,
        "stable_band_fraction_inclusive": _number(band),
        "strength_level": level_state,
        "strength_level_raw": {
            "return_5d_pct": _pct(ret5),
            "return_10d_pct": _pct(ret10),
            "rs_5d_pct": _pct(rs5),
        },
        "level_points": level_points,
        "level_unknown_points": sorted(set(level_unknown)),
        "strength_momentum": strength_state,
        "strength_momentum_raw": _pct((return_momentum + rs_momentum) / 2) if return_momentum is not None and rs_momentum is not None else None,
        "return_momentum": return_state,
        "return_momentum_raw": _pct(return_momentum),
        "rs_momentum": rs_state,
        "rs_momentum_raw": _pct(rs_momentum),
        "activity_momentum": activity_state,
        "activity_momentum_raw": _pct(activity_momentum),
        "recent_return_equal_window_pct": _pct(recent_ret),
        "previous_return_equal_window_pct": _pct(previous_ret),
        "recent_rs_equal_window_pct": _pct(recent_rs),
        "previous_rs_equal_window_pct": _pct(previous_rs),
        "recent_rs_coverage": {
            **recent_rs_coverage,
            "coverage": _number(recent_rs_coverage.get("coverage")),
        },
        "previous_rs_coverage": {
            **previous_rs_coverage,
            "coverage": _number(previous_rs_coverage.get("coverage")),
        },
        "recent_amount_avg_cny": _money(recent_amount),
        "previous_amount_avg_cny": _money(previous_amount),
        "amount_momentum_raw": _pct(amount_momentum),
        "recent_turnover_avg": _number(recent_turnover),
        "previous_turnover_avg": _number(previous_turnover),
        "turnover_momentum_raw": _pct(turnover_momentum),
        "parameter_status": PARAMETER_STATUS,
    }


def _trend(ctx, iid, end_index):
    seed = CONFIG["research_seed_parameters"]["trend_state"]
    long_windows = tuple(seed.get("long_term_windows", (60, 120, 250)))
    short_windows = tuple(seed.get("short_term_windows", (5, 10, 20)))
    long_values, long_reasons = _ma_snapshot(ctx, iid, end_index, long_windows)
    short_values, short_reasons = _ma_snapshot(ctx, iid, end_index, short_windows)
    long_reasons = [_ma_reason(reason) for reason in long_reasons]
    short_reasons = [_ma_reason(reason) for reason in short_reasons]
    values = {**short_values, **long_values}
    current = _bar(ctx, ctx.observation_date, iid)
    close = _close_fraction(current)
    close_reason = [] if close is not None else ["SAM_COMPARABLE_CLOSE_UNAVAILABLE"]
    ma_values = {str(window): _number(values.get(window)) for window in MA_WINDOWS}
    distances = {
        str(window): _number((close - value) / value) if close is not None and value is not None and value > 0 else None
        for window, value in values.items()
    }
    slope_lookback = seed["slope_lookback_sessions"]
    slopes, slope_reasons = {}, []
    for window in long_windows:
        current_ma = values.get(window)
        past_ma, reason = _ma_value(ctx, iid, end_index - slope_lookback, window) if end_index is not None else (None, f"TCT_MA{window}_SLOPE_WINDOW_UNAVAILABLE")
        if reason:
            slope_reasons.append(_ma_reason(reason).replace(f"SAM_MA{window}_", f"SAM_MA{window}_SLOPE_"))
        slopes[str(window)] = _number((current_ma - past_ma) / past_ma) if current_ma is not None and past_ma is not None and past_ma > 0 else None
    for window in (20,):
        current_ma = values.get(window)
        past_ma, reason = _ma_value(ctx, iid, end_index - slope_lookback, window) if end_index is not None else (None, f"TCT_MA{window}_SLOPE_WINDOW_UNAVAILABLE")
        slopes[str(window)] = _number((current_ma - past_ma) / past_ma) if not reason and current_ma is not None and past_ma is not None and past_ma > 0 else None

    long_unknown = sorted(set(long_reasons + slope_reasons + close_reason))
    ordered_long = [values.get(window) for window in long_windows]
    long_bullish = len(ordered_long) == len(long_windows) and all(value is not None for value in ordered_long) and all(left > right for left, right in zip(ordered_long, ordered_long[1:]))
    short_ordered = [values.get(window) for window in short_windows]
    short_bullish = len(short_ordered) == len(short_windows) and all(value is not None for value in short_ordered) and all(left > right for left, right in zip(short_ordered, short_ordered[1:]))
    full_ordered = [values.get(window) for window in MA_WINDOWS]
    bullish = len(full_ordered) == len(MA_WINDOWS) and all(value is not None for value in full_ordered) and all(left > right for left, right in zip(full_ordered, full_ordered[1:]))

    pullback_days, pullback_unknown = 0, []
    if end_index is None:
        pullback_unknown.append("SAM_PULLBACK_SEGMENT_PRICE_OR_MA5_UNAVAILABLE")
    else:
        for index in range(end_index, -1, -1):
            row_close = _close_fraction(_bar(ctx, ctx.sessions[index], iid))
            ma5, reason = _ma_value(ctx, iid, index, 5)
            if reason or row_close is None or ma5 is None:
                pullback_unknown.append("SAM_PULLBACK_SEGMENT_PRICE_OR_MA5_UNAVAILABLE")
                pullback_days = None
                break
            if row_close < ma5:
                pullback_days += 1
                continue
            break
    near_values = {
        window: abs(close - values[window]) / values[window]
        for window in short_windows
        if close is not None and values.get(window) is not None and values[window] > 0
    }
    matched_ma, matched_distance = min(near_values.items(), key=lambda item: (item[1], item[0])) if near_values else (None, None)

    short_health_reasons = list(short_reasons)
    if short_reasons:
        short_health = "UNKNOWN"
    elif short_bullish and close is not None and values.get(20) is not None and close >= values[20]:
        short_health = "HEALTHY"
    else:
        short_health = "PRESSURED"
        if not short_bullish:
            short_health_reasons.append("SAM_SHORT_MA_STRUCTURE_PRESSURED")
        if close is not None and values.get(20) is not None and close < values[20]:
            short_health_reasons.append("SAM_CLOSE_BELOW_MA20_SHORT_HEALTH")

    if long_unknown:
        return _condition("UNKNOWN", long_unknown, details={
            "trend_state": "UNKNOWN",
            "long_term_trend_qualified": False,
            "short_term_trend_health": short_health,
            "short_term_reason_codes": sorted(set(short_health_reasons)),
            "ma_values": ma_values,
            "dist_ma": distances,
            "ma_slopes": slopes,
            "ma250_policy": seed["insufficient_long_window_policy"],
            "long_term_bullish_arrangement": long_bullish,
            "short_term_bullish_arrangement": short_bullish,
            "bullish_arrangement": bullish,
            "pullback_segment_status": "UNKNOWN" if pullback_unknown else "AVAILABLE",
            "pullback_days_below_ma5": pullback_days,
            "matched_ma": matched_ma,
            "matched_ma_distance": _number(matched_distance) if matched_distance is not None else None,
        })

    broken, deteriorating = [], []
    for window in LONG_CLOSE_WINDOWS:
        value = values.get(window)
        if close is not None and value is not None and close < value:
            broken.append(f"SAM_CLOSE_BELOW_MA{window}")
    if not long_bullish:
        deteriorating.append("SAM_LONG_MA_BULLISH_ARRANGEMENT_FAIL")
    long_slope_min = Fraction(str(seed["long_slope_min_inclusive"]))
    for window in long_windows:
        slope = slopes.get(str(window))
        if slope is None:
            deteriorating.append(f"SAM_MA{window}_SLOPE_UNAVAILABLE")
            continue
        if Fraction(str(slope)) < long_slope_min:
            deteriorating.append(f"SAM_MA{window}_SLOPE_NEGATIVE")
    trend_state = "TREND_BROKEN" if broken else "TREND_DETERIORATING" if deteriorating else "STRONG_UPTREND"
    status = "PASS" if trend_state == "STRONG_UPTREND" else "FAIL"
    return _condition(status, broken or deteriorating, details={
        "trend_state": trend_state,
        "long_term_trend_qualified": trend_state == "STRONG_UPTREND",
        "short_term_trend_health": short_health,
        "short_term_reason_codes": sorted(set(short_health_reasons)),
        "ma_values": ma_values,
        "dist_ma": distances,
        "ma_slopes": slopes,
        "long_term_bullish_arrangement": long_bullish,
        "short_term_bullish_arrangement": short_bullish,
        "bullish_arrangement": bullish,
        "pullback_segment_status": "UNKNOWN" if pullback_unknown else "AVAILABLE",
        "pullback_segment_reason_codes": sorted(set(pullback_unknown)),
        "pullback_days_below_ma5": pullback_days,
        "matched_ma": matched_ma,
        "matched_ma_distance": _number(matched_distance) if matched_distance is not None else None,
    })


def _market_day_summary(ctx, end_index, *, include_instruments=False):
    instrument_limits = {}
    market_heights = []
    close_limit_count = 0
    touched_not_closed_count = 0
    close_limit_down_count = 0
    known_count = 0
    unknown_count = 0
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
            known_current = (
                _known_limit_flag(event.close_limit_up)
                and _known_limit_flag(event.touched_limit_up)
                and _known_limit_flag(event.touched_not_closed)
                and _known_limit_flag(event.close_limit_down)
            )
            known_count += int(known_current)
            unknown_count += int(not known_current)
            if include_instruments:
                instrument_limits[candidate] = {
                    "close_limit_up": event.close_limit_up,
                    "touched_limit_up": event.touched_limit_up,
                    "touched_not_closed": event.touched_not_closed,
                    "close_limit_down": event.close_limit_down,
                    "streak_exchange": event.streak_exchange,
                    "streak_lower_bound": event.streak_lower_bound,
                    "streak_quality": event.streak_quality,
                    "event_quality": event.quality,
                    "known_current_limit_event": known_current,
                }
            if not known_current:
                continue
            close_limit_count += event.close_limit_up == TRUE
            touched_not_closed_count += event.touched_not_closed == TRUE
            close_limit_down_count += event.close_limit_down == TRUE
            if event.close_limit_up == TRUE and event.streak_exchange is not None:
                market_heights.append(event.streak_exchange)
    expected = len(ctx.eligible_ids)
    coverage = Fraction(known_count, expected) if expected else Fraction(0)
    return {
        "instrument_limits": instrument_limits,
        "close_limit_count": close_limit_count,
        "touched_not_closed_count": touched_not_closed_count,
        "close_limit_down_count": close_limit_down_count,
        "verified_max_height": max(market_heights) if market_heights else None,
        "known_limit_event_count": known_count,
        "unknown_limit_event_count": unknown_count,
        "expected_limit_event_count": expected,
        "limit_event_coverage": coverage,
    }


def _market_raw_state(summary, seed):
    seed = CONFIG["research_seed_parameters"]["market_context"]
    coverage_min = Fraction(str(seed["min_limit_event_coverage_fraction_inclusive"]))
    coverage = summary["limit_event_coverage"]
    expected = summary["expected_limit_event_count"]
    max_height = summary["verified_max_height"]
    if expected and coverage < coverage_min:
        return "UNKNOWN", ["SAM_MARKET_LIMIT_EVENT_COVERAGE_INSUFFICIENT"]
    expansion_strength = (
        summary["close_limit_count"] >= seed["expansion_limit_count_min_inclusive"]
        or (max_height is not None and max_height >= seed["expansion_max_height_min_inclusive"])
    )
    pressure_reasons = []
    if summary["close_limit_down_count"] >= seed.get("divergence_close_limit_down_count_min_inclusive", 1):
        pressure_reasons.append("SAM_MARKET_CLOSE_LIMIT_DOWN_PRESSURE")
    if summary["touched_not_closed_count"] >= seed["divergence_touched_not_closed_count_min_inclusive"]:
        pressure_reasons.append("SAM_MARKET_BROKEN_BOARD_PRESSURE")
    if expansion_strength and pressure_reasons:
        return "DIVERGENCE", ["SAM_MARKET_EXPANSION_WITH_MAJOR_DIVERGENCE_PRESSURE", *pressure_reasons]
    if pressure_reasons:
        return "DIVERGENCE", pressure_reasons
    if expansion_strength:
        return "EXPANSION", []
    return "RETREAT", ["SAM_MARKET_PARTIAL_OR_WEAK_STRENGTH_WITHOUT_EXPANSION"]


def _market_strength_metrics(summary, seed=None):
    seed = seed or CONFIG["research_seed_parameters"]["market_context"]
    known = summary.get("known_limit_event_count", 0) or 0
    height = summary.get("verified_max_height") or 0
    height_unit = max(1, int(seed["expansion_max_height_min_inclusive"]))

    def rate(field):
        return Fraction(summary.get(field, 0), known) if known else None

    close_rate = rate("close_limit_count")
    broken_rate = rate("touched_not_closed_count")
    down_rate = rate("close_limit_down_count")
    height_component = Fraction(min(height, height_unit), height_unit)
    score = None
    if close_rate is not None and broken_rate is not None and down_rate is not None:
        score = close_rate * 2 + height_component - down_rate * 2 - broken_rate
    return {
        "close_limit_rate": close_rate,
        "broken_board_rate": broken_rate,
        "close_limit_down_rate": down_rate,
        "height_component": height_component,
        "normalized_strength_score": score,
    }


def _market_strength_score(summary):
    return _market_strength_metrics(summary)["normalized_strength_score"]


def _market_strong_cohort_feedback(ctx, end_index, current_summary, previous_summary, seed):
    if not previous_summary or not previous_summary.get("instrument_limits"):
        return {"status": "UNKNOWN", "reason_codes": ["SAM_MARKET_PREVIOUS_STRONG_COHORT_UNAVAILABLE"]}
    previous_limits = previous_summary.get("instrument_limits") or {}
    current_limits = current_summary.get("instrument_limits") or {}
    cohort = sorted(iid for iid, row in previous_limits.items() if row.get("close_limit_up") == TRUE)
    if not cohort:
        return {
            "status": "UNKNOWN",
            "previous_close_limit_cohort_count": 0,
            "reason_codes": ["SAM_MARKET_PREVIOUS_CLOSE_LIMIT_COHORT_EMPTY"],
        }
    values = []
    advanced = 0
    current_known = 0
    for iid in cohort:
        ret = _price_return(ctx, iid, end_index, 1)
        if ret is not None:
            values.append(ret)
        current = current_limits.get(iid) or {}
        if _known_limit_flag(current.get("close_limit_up")):
            current_known += 1
            advanced += int(current.get("close_limit_up") == TRUE)
    coverage = Fraction(len(values), len(cohort)) if cohort else Fraction(0)
    coverage_min = Fraction(str(seed.get("strong_cohort_min_matched_coverage_fraction_inclusive", seed["min_limit_event_coverage_fraction_inclusive"])))
    status = "COMPLETE" if values and coverage >= coverage_min else "INSUFFICIENT_COVERAGE" if values else "UNKNOWN"
    return {
        "status": status,
        "previous_close_limit_cohort_count": len(cohort),
        "matched_return_count": len(values),
        "matched_coverage": _number(coverage),
        "median_return_pct": _pct(median(values)) if status == "COMPLETE" else None,
        "advance_close_limit_count": advanced,
        "current_limit_event_known_count": current_known,
        "reason_codes": [] if status == "COMPLETE" else ["SAM_MARKET_STRONG_COHORT_FEEDBACK_COVERAGE_INSUFFICIENT"],
    }


def _market_position_context(ctx, end_index):
    seed = CONFIG["research_seed_parameters"]["market_context"]
    current_summary = _market_day_summary(ctx, end_index, include_instruments=True)
    previous_summary = _market_day_summary(ctx, end_index - 1, include_instruments=True) if end_index is not None and end_index > 0 else None
    current_raw_state, current_reasons = _market_raw_state(current_summary, seed)
    previous_raw_state, previous_reasons = _market_raw_state(previous_summary, seed) if previous_summary else ("UNKNOWN", ["SAM_MARKET_PREVIOUS_SESSION_UNAVAILABLE"])
    current_metrics = _market_strength_metrics(current_summary, seed)
    previous_metrics = _market_strength_metrics(previous_summary, seed) if previous_summary else {}
    current_score = current_metrics.get("normalized_strength_score")
    previous_score = previous_metrics.get("normalized_strength_score")
    current_coverage = current_summary["limit_event_coverage"]
    previous_coverage = previous_summary["limit_event_coverage"] if previous_summary else None
    coverage_delta = abs(current_coverage - previous_coverage) if previous_coverage is not None else None
    coverage_min = Fraction(str(seed["min_limit_event_coverage_fraction_inclusive"]))
    max_coverage_delta = Fraction(str(seed.get("momentum_max_coverage_delta_inclusive", "0.10")))
    momentum_band = Fraction(str(seed.get("momentum_stable_band_fraction_inclusive", "0")))
    momentum_reasons = []
    if previous_score is None or current_score is None:
        market_momentum = "UNKNOWN"
        momentum_reasons.append("SAM_MARKET_NORMALIZED_STRENGTH_UNAVAILABLE")
    elif min(current_coverage, previous_coverage) < coverage_min or coverage_delta > max_coverage_delta:
        market_momentum = "UNKNOWN"
        momentum_reasons.append("SAM_MARKET_MOMENTUM_COVERAGE_NOT_COMPARABLE")
    elif current_score - previous_score > momentum_band:
        market_momentum = "IMPROVING"
    elif previous_score - current_score > momentum_band:
        market_momentum = "WEAKENING"
    else:
        market_momentum = "STABLE"
    strong_cohort = _market_strong_cohort_feedback(ctx, end_index, current_summary, previous_summary, seed)
    if current_raw_state == "EXPANSION" and strong_cohort.get("status") == "COMPLETE":
        cohort_return = _frac(strong_cohort.get("median_return_pct"))
        if cohort_return is not None and cohort_return < 0:
            current_raw_state = "DIVERGENCE"
            current_reasons = [*current_reasons, "SAM_MARKET_STRONG_COHORT_FEEDBACK_NEGATIVE"]

    transition = {
        "previous_state": previous_raw_state,
        "current_raw_state": current_raw_state,
        "previous_score": _number(previous_score) if previous_score is not None else None,
        "current_score": _number(current_score) if current_score is not None else None,
        "previous_normalized_metrics": {key: (_number(value) if isinstance(value, Fraction) else value) for key, value in previous_metrics.items()},
        "current_normalized_metrics": {key: (_number(value) if isinstance(value, Fraction) else value) for key, value in current_metrics.items()},
        "previous_coverage": _number(previous_coverage) if previous_coverage is not None else None,
        "current_coverage": _number(current_coverage),
        "coverage_delta": _number(coverage_delta) if coverage_delta is not None else None,
        "market_momentum": market_momentum,
        "market_momentum_reason_codes": momentum_reasons,
        "transition_evidence": [],
    }
    if previous_summary:
        if market_momentum == "UNKNOWN":
            transition["transition_evidence"].extend(momentum_reasons)
        if market_momentum != "UNKNOWN" and current_metrics.get("close_limit_rate") is not None and previous_metrics.get("close_limit_rate") is not None and current_metrics["close_limit_rate"] > previous_metrics.get("close_limit_rate"):
            transition["transition_evidence"].append("SAM_MARKET_CLOSE_LIMIT_COUNT_IMPROVED")
        if market_momentum != "UNKNOWN" and current_metrics.get("broken_board_rate") is not None and previous_metrics.get("broken_board_rate") is not None and current_metrics["broken_board_rate"] < previous_metrics.get("broken_board_rate"):
            transition["transition_evidence"].append("SAM_MARKET_BROKEN_BOARD_COUNT_IMPROVED")
        if market_momentum != "UNKNOWN" and current_metrics.get("close_limit_down_rate") is not None and previous_metrics.get("close_limit_down_rate") is not None and current_metrics["close_limit_down_rate"] < previous_metrics.get("close_limit_down_rate"):
            transition["transition_evidence"].append("SAM_MARKET_CLOSE_LIMIT_DOWN_COUNT_IMPROVED")

    current_has_strength = bool(current_summary["close_limit_count"] or current_summary["touched_not_closed_count"] or current_summary["verified_max_height"])
    if current_raw_state == "RETREAT" and previous_raw_state in {"RETREAT", "DIVERGENCE"} and market_momentum == "IMPROVING" and current_has_strength and transition["transition_evidence"]:
        ecosystem_state, status, reasons = "REPAIR", "PASS", []
    elif current_raw_state in {"EXPANSION", "DIVERGENCE", "RETREAT"}:
        ecosystem_state, status, reasons = current_raw_state, "PASS", current_reasons
    elif current_raw_state == "UNKNOWN":
        ecosystem_state, status, reasons = "UNKNOWN", "UNKNOWN", current_reasons
    else:
        ecosystem_state = "UNKNOWN"
        status = "UNKNOWN"
        reasons = ["SAM_MARKET_REPAIR_TRANSITION_EVIDENCE_UNAVAILABLE"]

    return {
        "instrument_limits": current_summary["instrument_limits"],
        "return_populations": {
            str(window): [ret for candidate in ctx.eligible_ids if (ret := _price_return(ctx, candidate, end_index, window)) is not None]
            for window in (10, 20)
        },
        "market_width": {
            "close_limit_count": current_summary["close_limit_count"],
            "touched_not_closed_count": current_summary["touched_not_closed_count"],
            "close_limit_down_count": current_summary["close_limit_down_count"],
            "verified_max_height": current_summary["verified_max_height"],
            "known_limit_event_count": current_summary["known_limit_event_count"],
            "unknown_limit_event_count": current_summary["unknown_limit_event_count"],
            "expected_limit_event_count": current_summary["expected_limit_event_count"],
            "limit_event_coverage": _number(current_summary["limit_event_coverage"]),
            "close_limit_rate": _number(current_metrics["close_limit_rate"]) if current_metrics["close_limit_rate"] is not None else None,
            "broken_board_rate": _number(current_metrics["broken_board_rate"]) if current_metrics["broken_board_rate"] is not None else None,
            "close_limit_down_rate": _number(current_metrics["close_limit_down_rate"]) if current_metrics["close_limit_down_rate"] is not None else None,
            "normalized_strength_score": _number(current_score) if current_score is not None else None,
        },
        "market_level": current_raw_state,
        "market_momentum": market_momentum,
        "market_momentum_reason_codes": momentum_reasons,
        "previous_state": previous_raw_state,
        "current_raw_state": current_raw_state,
        "transition": transition,
        "strong_cohort_feedback": strong_cohort,
        "previous_market_width": {
            key: (_number(value) if isinstance(value, Fraction) else value)
            for key, value in (previous_summary or {}).items()
            if key != "instrument_limits"
        },
        "previous_state_reason_codes": previous_reasons,
        "status": status,
        "reason_codes": reasons,
        "ecosystem_state": ecosystem_state,
        "status_label": ecosystem_state,
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
            "close_limit_down": current.get("close_limit_down") if current else UNKNOWN,
            "streak_exchange": current.get("streak_exchange") if current else None,
            "streak_lower_bound": current.get("streak_lower_bound") if current else None,
            "streak_quality": current.get("streak_quality") if current else "UNKNOWN",
            "event_quality": current.get("event_quality") if current else "UNKNOWN",
            "known_current_limit_event": current.get("known_current_limit_event") if current else False,
        },
        "market_width": market_position["market_width"],
        "return_percentiles": percentiles,
        "status_label": market_position["status_label"],
        "status": market_position["status"],
        "ecosystem_state": market_position["ecosystem_state"],
        "reason_codes": market_position["reason_codes"],
        "status_label_policy": "DESCRIPTIVE_ONLY_WITH_VISIBLE_LIMIT_EVIDENCE",
    }


def _sector_window_stats(ctx, iid, peers, end_index, window, return_values, seed):
    expected = len(peers)
    coverage_min = Fraction(str(seed["min_peer_return_coverage_fraction_inclusive"]))
    returns = [(peer, _price_return(ctx, peer, end_index, window)) for peer in peers]
    valid = [value for _, value in returns if value is not None]
    coverage = Fraction(len(valid), expected) if expected else Fraction(0)
    if valid and coverage >= coverage_min:
        coverage_status = "COMPLETE"
    elif valid:
        coverage_status = "INSUFFICIENT_COVERAGE"
    else:
        coverage_status = "UNKNOWN"
    usable = coverage_status == "COMPLETE"
    peer_mean = sum(valid, Fraction(0)) / len(valid) if valid and usable else None
    market_return, market_coverage = _market_return_at(ctx, end_index, window)
    sector_market_rs = peer_mean - market_return if peer_mean is not None and market_return is not None else None
    stock_ret = return_values.get(str(window))
    rs = stock_ret - peer_mean if stock_ret is not None and peer_mean is not None else None
    amount_changes = [value for peer in peers if (value := _amount_change(ctx, peer, end_index, window)) is not None]
    return {
        "expected_peer_count": expected,
        "valid_peer_count": len(valid),
        "coverage_fraction": _number(coverage),
        "coverage_status": coverage_status,
        "used_as_pass_evidence": usable,
        "peer_equal_weight_return_pct": _pct(peer_mean),
        "market_equal_weight_return_pct": _pct(market_return),
        "market_benchmark_coverage": _number(market_coverage.get("coverage")),
        "sector_market_rs_pct": _pct(sector_market_rs),
        "peer_median_return_pct": _pct(median(valid)) if valid and usable else None,
        "peer_up_fraction": _number(Fraction(sum(value > 0 for value in valid), len(valid))) if valid and usable else None,
        "peer_amount_change_median_pct": _pct(median(amount_changes)) if amount_changes and usable else None,
        "stock_return_pct": _pct(stock_ret),
        "rs_sector_pct": _pct(rs),
        "reason_codes": [] if usable else ["SAM_SECTOR_WINDOW_COVERAGE_INSUFFICIENT" if valid else "SAM_SECTOR_WINDOW_RETURNS_UNAVAILABLE"],
    }


def _sector_momentum_stats(ctx, peers, end_index, seed):
    count = seed.get("momentum_equal_window_sessions", 3)
    coverage_min = Fraction(str(seed["min_peer_return_coverage_fraction_inclusive"]))
    band = Fraction(str(seed.get("momentum_stable_band_fraction_inclusive", 0)))
    recent = {peer: _price_return(ctx, peer, end_index, count) for peer in peers}
    previous = {peer: _price_return(ctx, peer, end_index - count, count) for peer in peers} if end_index is not None and end_index - count >= 0 else {}
    recent_valid = [value for value in recent.values() if value is not None]
    previous_valid = [value for value in previous.values() if value is not None]
    matched = [(recent[peer], previous[peer]) for peer in peers if recent.get(peer) is not None and previous.get(peer) is not None]
    expected = len(peers)
    recent_coverage = Fraction(len(recent_valid), expected) if expected else Fraction(0)
    previous_coverage = Fraction(len(previous_valid), expected) if expected else Fraction(0)
    matched_coverage = Fraction(len(matched), expected) if expected else Fraction(0)
    matched_ok = bool(matched) and matched_coverage >= coverage_min
    recent_ok = bool(recent_valid) and recent_coverage >= coverage_min
    previous_ok = bool(previous_valid) and previous_coverage >= coverage_min
    recent_mean = sum((pair[0] for pair in matched), Fraction(0)) / len(matched) if matched_ok else None
    previous_mean = sum((pair[1] for pair in matched), Fraction(0)) / len(matched) if matched_ok else None
    raw = recent_mean - previous_mean if recent_mean is not None and previous_mean is not None else None
    state = _state_from_delta(raw, band, rising="STRENGTHENING", stable="STABLE", falling="WEAKENING")
    if raw is None and not matched_ok:
        state = "UNKNOWN"
    return {
        "calculation_id": "PEER_MATCHED_EQUAL_WINDOW_RECENT_MINUS_PREVIOUS_RETURN_V1",
        "equal_window_sessions": count,
        "sector_momentum_state": state,
        "sector_momentum_raw_pct": _pct(raw),
        "recent_peer_return_pct": _pct(recent_mean),
        "previous_peer_return_pct": _pct(previous_mean),
        "recent_expected": expected,
        "previous_expected": expected,
        "matched_peer_count": len(matched),
        "matched_coverage": _number(matched_coverage),
        "matched_coverage_status": "COMPLETE" if matched_ok else "INSUFFICIENT_COVERAGE" if matched else "UNKNOWN",
        "recent_coverage": {
            "expected_peer_count": expected,
            "valid_peer_count": len(recent_valid),
            "coverage_fraction": _number(recent_coverage),
            "coverage_status": "COMPLETE" if recent_ok else "INSUFFICIENT_COVERAGE" if recent_valid else "UNKNOWN",
        },
        "previous_coverage": {
            "expected_peer_count": expected,
            "valid_peer_count": len(previous_valid),
            "coverage_fraction": _number(previous_coverage),
            "coverage_status": "COMPLETE" if previous_ok else "INSUFFICIENT_COVERAGE" if previous_valid else "UNKNOWN",
        },
        "reason_codes": [] if matched_ok else ["SAM_SECTOR_MOMENTUM_MATCHED_PEER_COVERAGE_INSUFFICIENT"],
    }


def _percentile_or_none(value, values):
    return percentile_rank_midpoint(value, values) if value is not None and values else None


def _sector_setup_compatibility(level_state, momentum_state, breadth_state, stock_role, data_status):
    rules = CONFIG["research_seed_parameters"]["sector_evidence"].get("setup_compatibility", {})
    result = {}
    for setup in STAGE_IDS:
        if data_status != "PASS":
            result[setup] = {"context_status": "UNKNOWN", "reason_codes": ["SAM_SECTOR_CONTEXT_DATA_INCOMPLETE"]}
            continue
        rule = rules.get(setup, {})
        negative_momentum = set(rule.get("negative_momentum", ()))
        supportive_level = set(rule.get("supportive_level", ()))
        supportive_momentum = set(rule.get("supportive_momentum", ()))
        supportive_breadth = set(rule.get("supportive_breadth", ()))
        if level_state == "LOW" or momentum_state in negative_momentum and setup in {"START", "ACCELERATION", "TREND_PULLBACK"}:
            result[setup] = {"context_status": "NEGATIVE", "reason_codes": ["SAM_SECTOR_CONTEXT_NEGATIVE_FOR_SETUP"]}
            continue
        level_ok = level_state in supportive_level
        momentum_ok = momentum_state in supportive_momentum
        breadth_ok = not supportive_breadth or breadth_state in supportive_breadth
        role_ok = stock_role in {"LEADER", "CORE", "FIRST_TIER"}
        if level_ok and momentum_ok and breadth_ok and role_ok:
            result[setup] = {"context_status": "SUPPORTIVE", "reason_codes": []}
        elif level_state == "UNKNOWN" or momentum_state == "UNKNOWN" or breadth_state == "UNKNOWN" or stock_role == "UNKNOWN":
            result[setup] = {"context_status": "UNKNOWN", "reason_codes": ["SAM_SECTOR_CONTEXT_COMPONENT_UNKNOWN"]}
        else:
            result[setup] = {"context_status": "NEUTRAL", "reason_codes": ["SAM_SECTOR_CONTEXT_NEUTRAL_FOR_SETUP"]}
    return result


def _sector_stats_for_theme(ctx, iid, theme, end_index, return_values):
    members = sorted(set(theme.get("member_ids") or []) & set(ctx.eligible_ids))
    if iid not in members:
        return None
    peers = [member for member in members if member != iid]
    expected = len(peers)
    seed = CONFIG["research_seed_parameters"]["sector_evidence"]
    windows = {str(window): _sector_window_stats(ctx, iid, peers, end_index, window, return_values, seed) for window in SECTOR_WINDOWS}
    complete_windows = [row for row in windows.values() if row["coverage_status"] == "COMPLETE"]
    current_peer_returns = [
        value for peer in peers
        if (value := _price_return(ctx, peer, end_index, 1)) is not None
    ] if windows.get("1", {}).get("coverage_status") == "COMPLETE" else []
    strong_gain = Fraction(str(seed["strong_gain_return_1d_min_inclusive"]))
    close_limit_count = 0
    known_limit_count = 0
    if end_index is not None:
        for peer in peers:
            event = _bar_limit_event(_bar(ctx, ctx.observation_date, peer), peer, ctx.observation_date)
            known = _known_limit_flag(event.close_limit_up)
            known_limit_count += int(known)
            close_limit_count += int(event.close_limit_up == TRUE)
    ret5 = return_values.get("5")
    peer_ret5_values = [value for peer in peers if (value := _price_return(ctx, peer, end_index, 5)) is not None]
    peer_ret5_mean = _frac(windows.get("5", {}).get("peer_equal_weight_return_pct"))
    peer_ret5_mean = peer_ret5_mean / 100 if peer_ret5_mean is not None else None
    market_ret5, market_ret5_coverage = _market_return_at(ctx, end_index, 5)
    sector_market_rs = peer_ret5_mean - market_ret5 if peer_ret5_mean is not None and market_ret5 is not None else None
    if peer_ret5_mean is None:
        absolute_level_state = "UNKNOWN"
    elif peer_ret5_mean >= Fraction(str(seed["level_high_peer_ret5_min_inclusive"])):
        absolute_level_state = "HIGH"
    elif peer_ret5_mean <= Fraction(str(seed["level_low_peer_ret5_max_inclusive"])):
        absolute_level_state = "LOW"
    else:
        absolute_level_state = "NORMAL"
    relative_high = Fraction(str(seed.get("level_high_sector_market_rs_min_exclusive", "0")))
    relative_low = Fraction(str(seed.get("level_low_sector_market_rs_max_exclusive", "0")))
    if sector_market_rs is None:
        relative_level_state = "UNKNOWN"
    elif sector_market_rs > relative_high:
        relative_level_state = "OUTPERFORMING"
    elif sector_market_rs < relative_low:
        relative_level_state = "UNDERPERFORMING"
    else:
        relative_level_state = "MARKET_IN_LINE"
    if peer_ret5_mean is None or sector_market_rs is None:
        level_state = "UNKNOWN"
    elif absolute_level_state == "HIGH" and relative_level_state == "OUTPERFORMING":
        level_state = "HIGH"
    elif absolute_level_state == "LOW" or relative_level_state == "UNDERPERFORMING":
        level_state = "LOW"
    else:
        level_state = "NORMAL"

    momentum = _sector_momentum_stats(ctx, peers, end_index, seed)
    sector_momentum_state = momentum["sector_momentum_state"]
    breadth_fraction = Fraction(sum(value > 0 for value in current_peer_returns), len(current_peer_returns)) if current_peer_returns else None
    if breadth_fraction is None:
        breadth_state = "UNKNOWN"
    elif breadth_fraction >= Fraction(str(seed["breadth_broad_up_fraction_min_inclusive"])):
        breadth_state = "BROAD"
    elif breadth_fraction <= Fraction(str(seed["breadth_narrow_up_fraction_max_inclusive"])):
        breadth_state = "NARROW"
    else:
        breadth_state = "NORMAL"

    stock_rs5 = ret5 - peer_ret5_mean if ret5 is not None and peer_ret5_mean is not None else None
    peer_rs5 = [value - peer_ret5_mean for value in peer_ret5_values] if peer_ret5_mean is not None else []
    current_bar = _bar(ctx, ctx.observation_date, iid)
    peer_bars = [_bar(ctx, ctx.observation_date, peer) for peer in peers]
    amount_values = [value for bar in [current_bar, *peer_bars] if (value := _amount_fraction(bar)) is not None]
    turnover_values = [value for bar in [current_bar, *peer_bars] if (value := _turnover_fraction(bar)) is not None]
    ret20_values = [value for member in [iid, *peers] if (value := _price_return(ctx, member, end_index, 20)) is not None]
    role_features = {
        "sector_return_percentile_5d": _percentile_or_none(ret5, peer_ret5_values + ([ret5] if ret5 is not None else [])),
        "sector_rs_percentile_5d": _percentile_or_none(stock_rs5, peer_rs5 + ([stock_rs5] if stock_rs5 is not None else [])),
        "amount_percentile": _percentile_or_none(_amount_fraction(current_bar), amount_values),
        "turnover_percentile": _percentile_or_none(_turnover_fraction(current_bar), turnover_values),
        "persistent_strength_percentile_20d": _percentile_or_none(return_values.get("20"), ret20_values),
    }
    current_event = _bar_limit_event(current_bar, iid, ctx.observation_date)
    limit_scores = []
    target_limit_score = None
    known_limit_members = 0
    for member in [iid, *peers]:
        event = _bar_limit_event(_bar(ctx, ctx.observation_date, member), member, ctx.observation_date)
        if not _known_limit_flag(event.close_limit_up):
            continue
        known_limit_members += 1
        score = event.streak_exchange if event.close_limit_up == TRUE and event.streak_exchange is not None else event.streak_lower_bound if event.close_limit_up == TRUE and event.streak_lower_bound else 1 if event.close_limit_up == TRUE else 0
        limit_scores.append(score)
        if member == iid:
            target_limit_score = score
    limit_evidence_total = len([iid, *peers])
    limit_evidence_coverage = Fraction(known_limit_members, limit_evidence_total) if limit_evidence_total else Fraction(0)
    if target_limit_score is not None and limit_evidence_coverage >= Fraction(str(seed["min_peer_return_coverage_fraction_inclusive"])):
        role_features["limit_streak_percentile"] = _percentile_or_none(target_limit_score, limit_scores)
    else:
        role_features["limit_streak_percentile"] = None
    known_role_features = [Fraction(str(value)) for value in role_features.values() if value is not None]
    role_feature_total = len(role_features)
    role_score = sum(known_role_features, Fraction(0)) / len(known_role_features) if len(known_role_features) >= 3 else None
    if role_score is None:
        stock_role = "UNKNOWN"
    elif role_score >= Fraction(str(seed["leader_rank_percentile_min_inclusive"])) * 100:
        stock_role = "LEADER"
    elif role_score >= Fraction(str(seed["core_rank_percentile_min_inclusive"])) * 100:
        stock_role = "CORE"
    elif role_score >= Fraction(str(seed["first_tier_rank_percentile_min_inclusive"])) * 100:
        stock_role = "FIRST_TIER"
    else:
        stock_role = "FOLLOWER"

    status = "PASS"
    reasons = []
    if expected < seed["min_other_valid_members_inclusive"]:
        status = "FAIL"
        reasons.append("SAM_SECTOR_PEER_COUNT_BELOW_MIN")
    elif not any(row["valid_peer_count"] for row in windows.values()):
        status = "UNKNOWN"
        reasons.append("SAM_SECTOR_PEER_RETURNS_UNAVAILABLE")
    elif not complete_windows:
        status = "UNKNOWN"
        reasons.append("SAM_SECTOR_PEER_RETURN_COVERAGE_INSUFFICIENT")
    compatibility = _sector_setup_compatibility(level_state, sector_momentum_state, breadth_state, stock_role, status)
    return {
        "theme_id": theme.get("theme_id"),
        "theme_name": theme.get("name"),
        "theme_type": theme.get("theme_type"),
        "status": status,
        "reason_codes": reasons,
        "data_status": status,
        "member_count": len(members),
        "peer_expected": expected,
        "sector_level_state": level_state,
        "sector_level_raw_pct": _pct(peer_ret5_mean),
        "sector_absolute_level": absolute_level_state,
        "sector_relative_level": relative_level_state,
        "sector_market_rs_pct": _pct(sector_market_rs),
        "sector_market_benchmark_ret5_pct": _pct(market_ret5),
        "sector_market_benchmark_coverage": _number(market_ret5_coverage.get("coverage")),
        "sector_momentum_state": sector_momentum_state,
        "sector_momentum": momentum,
        "sector_breadth_fraction": _number(breadth_fraction),
        "breadth_state": breadth_state,
        "stock_role": stock_role,
        "stock_role_score": _number(role_score),
        "stock_role_features": role_features,
        "stock_role_known_feature_count": len(known_role_features),
        "stock_role_unknown_feature_count": role_feature_total - len(known_role_features),
        "stock_role_evidence_coverage": _number(Fraction(len(known_role_features), role_feature_total)) if role_feature_total else None,
        "limit_streak_evidence": {
            "same_theme_member_count": limit_evidence_total,
            "known_limit_event_member_count": known_limit_members,
            "coverage_fraction": _number(limit_evidence_coverage),
            "target_limit_streak_score": target_limit_score,
            "status": "COMPLETE" if role_features.get("limit_streak_percentile") is not None else "UNKNOWN",
            "reason_codes": [] if role_features.get("limit_streak_percentile") is not None else ["SAM_ROLE_LIMIT_STREAK_PEER_EVIDENCE_INSUFFICIENT"],
        },
        "temporal_leadership": {"status": "UNKNOWN", "reason_codes": ["SAM_INTRADAY_FIRST_SEAL_TIMESTAMPS_NOT_IN_MARKET_INPUT"]},
        "setup_compatibility": compatibility,
        "context_status": "SUPPORTIVE" if any(row["context_status"] == "SUPPORTIVE" for row in compatibility.values()) else "NEGATIVE" if any(row["context_status"] == "NEGATIVE" for row in compatibility.values()) else "UNKNOWN" if any(row["context_status"] == "UNKNOWN" for row in compatibility.values()) else "NEUTRAL",
        "stock_role_percentile_5d": role_features.get("sector_return_percentile_5d"),
        "strong_gain_count_1d": sum(value >= strong_gain for value in current_peer_returns),
        "close_limit_count_1d": close_limit_count,
        "known_limit_event_peer_count_1d": known_limit_count,
        "breadth_denominator": {
            "eligible_theme_peers": expected,
            "observed_peers": len(current_peer_returns),
            "positive_peers": sum(value > 0 for value in current_peer_returns),
            "strong_peers": sum(value >= strong_gain for value in current_peer_returns),
            "limit_up_peers": close_limit_count,
        },
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
            return _condition("UNKNOWN", ["SAM_NO_VISIBLE_AUTHORIZED_SECTOR_LINKAGE"], details={"primary_theme_id": None}), []
        return _condition("UNKNOWN", ["SAM_SECTOR_DATA_UNAVAILABLE"], details={"primary_theme_id": None}), []
    passing = [row for row in candidates if row["status"] == "PASS"]
    if passing:
        return _condition("PASS", details={"primary_theme_id": passing[0]["theme_id"], "primary_theme_order": "first_passing_theme_id_asc"}), candidates
    status = "UNKNOWN" if any(row["status"] == "UNKNOWN" for row in candidates) else "FAIL"
    reasons = sorted({reason for row in candidates for reason in row.get("reason_codes", [])})
    return _condition(status, reasons, details={"primary_theme_id": candidates[0]["theme_id"], "primary_theme_order": "theme_id_asc"}), candidates


def _classified_capital(row):
    seed = CONFIG["research_seed_parameters"]["funds"]
    layer = str(row.get("source_layer") or row.get("evidence_layer") or "").upper()
    classification = str(row.get("classification") or "").upper()
    if layer in set(seed["proxy_source_layers"]):
        return False
    return (
        layer in set(seed["classified_source_layers"])
        or ("ACTIVE" in classification and ("SEAT" in classification or "HOT_MONEY" in classification))
    )


def _proxy_capital(row):
    layer = str(row.get("source_layer") or row.get("evidence_layer") or "").upper()
    return layer in set(CONFIG["research_seed_parameters"]["funds"]["proxy_source_layers"])


def _visible_theme_funds(ctx, iid, theme, end_index):
    seed = CONFIG["research_seed_parameters"]["funds"]
    sessions_5 = [
        date.fromisoformat(day)
        for day in ctx.sessions[max(0, end_index - seed["lookback_sessions_including_T"] + 1) : end_index + 1]
    ] if end_index is not None else []
    session_set = {str(day) for day in sessions_5}
    all_sessions = {date.fromisoformat(str(day)) for day in ctx.sessions}
    observation = date.fromisoformat(ctx.observation_date)
    peers = set(theme.get("member_ids") or []) & set(ctx.eligible_ids)
    if seed.get("peers_exclude_self", True):
        peers.discard(iid)
    visible, rejected = [], []
    for original in ctx.bundle.funds:
        row = dict(original)
        if str(row.get("theme_id")) != str(theme.get("theme_id")):
            continue
        row_iid = str(row.get("instrument_id"))
        if row_iid not in peers:
            continue
        session = _fund_date(row.get("session") or row.get("trade_date") or row.get("event_session"))
        if session is None or str(session) not in session_set:
            rejected.append({"theme_id": theme.get("theme_id"), "instrument_id": row_iid, "reason_codes": ["SAM_FUNDS_SESSION_OUTSIDE_LOOKBACK_OR_INVALID"]})
            continue
        window = _fund_window(row)
        if window is None or window[0] is None or window[1] is None:
            rejected.append({"theme_id": theme.get("theme_id"), "instrument_id": row_iid, "reason_codes": ["SAM_FUNDS_WINDOW_UNAVAILABLE_OR_INVALID"]})
            continue
        if window[1] > observation:
            rejected.append({"theme_id": theme.get("theme_id"), "instrument_id": row_iid, "reason_codes": ["SAM_FUNDS_FUTURE_WINDOW_END"]})
            continue
        if window[0] not in all_sessions or window[1] not in all_sessions or not window[0] <= session <= window[1]:
            rejected.append({"theme_id": theme.get("theme_id"), "instrument_id": row_iid, "reason_codes": ["SAM_FUNDS_WINDOW_NOT_ALIGNED_TO_EXCHANGE_SESSIONS"]})
            continue
        proof = ctx.resolver.resolve(row, instrument_id=row_iid, session=session, require_session=True)
        if not proof.allowed:
            rejected.append({"theme_id": theme.get("theme_id"), "instrument_id": row_iid, "reason_codes": list(proof.reason_codes)})
            continue
        if not _visible_fund(row, ctx.cutoff, ctx.bundle.coverage_type):
            rejected.append({"theme_id": theme.get("theme_id"), "instrument_id": row_iid, "reason_codes": ["SAM_FUNDS_ROW_CLOCK_NOT_VISIBLE"]})
            continue
        row["_source_resolution"] = {"source_ids": list(proof.source_ids), "reconstruction_used": proof.reconstruction_used}
        visible.append(row)
    return visible, sessions_5, rejected


def _dedupe_funds(rows):
    deduped, by_key = [], defaultdict(list)
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
        row["_dedupe_key"] = key
        by_key[key].append(row)
    for copies in by_key.values():
        fingerprints = {
            digest({
                "instrument_id": str(row.get("instrument_id")),
                "theme_id": str(row.get("theme_id")),
                "session": str(_fund_date(row.get("session") or row.get("trade_date") or row.get("event_session"))),
                "window_start": str(_fund_date(row.get("window_start") or row.get("start_session") or row.get("session"))),
                "window_end": str(_fund_date(row.get("window_end") or row.get("end_session") or row.get("session"))),
                "seat_id": str(row.get("seat_id") or ""),
                "seat_name": str(row.get("seat_name") or ""),
                "source_id": str(row.get("source_id") or ""),
                "buy_amount_cny": str(_as_decimal(row.get("buy_amount_cny"))),
                "sell_amount_cny": str(_as_decimal(row.get("sell_amount_cny"))),
                "net_amount_cny": str(_as_decimal(row.get("net_amount_cny"))),
                "source_layer": str(row.get("source_layer") or row.get("evidence_layer") or ""),
                "classification": str(row.get("classification") or ""),
                "source_refs": sorted(row.get("source_refs") or []),
            })
            for row in copies
        }
        if len(fingerprints) > 1:
            return [], "SAM_FUNDS_DISCLOSURE_KEY_CONFLICT"
        deduped.append(copies[0])
    deduped.sort(key=lambda row: row["_dedupe_key"])
    return deduped, None


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


def _funds_completeness_for_theme(ctx, theme, end_index, sessions):
    theme_id = str(theme.get("theme_id"))
    observation = date.fromisoformat(ctx.observation_date)
    expected_members = set(theme.get("member_ids") or []) & set(ctx.eligible_ids)
    rows, rejected = [], []
    for original in ctx.bundle.funds:
        row = dict(original)
        if str(row.get("theme_id")) != theme_id:
            continue
        markers = {
            str(row.get(key) or "").upper()
            for key in ("record_type", "evidence_type", "completion_status", "provider_status", "query_status", "result_status")
        }
        row_count = row.get("row_count")
        try:
            zero_rows = row_count is not None and int(row_count) == 0
        except (TypeError, ValueError):
            zero_rows = False
        complete_no_record = "COMPLETE_NO_RECORD" in markers or ("COMPLETE" in markers and zero_rows)
        if not complete_no_record:
            continue
        session_value = row.get("session") or row.get("trade_date") or row.get("event_session")
        session = _fund_date(session_value) if session_value is not None else observation
        window = _fund_window(row)
        if window is not None and window[1] is not None and window[1] > observation:
            rejected.append({"theme_id": theme_id, "reason_codes": ["SAM_FUNDS_COMPLETENESS_FUTURE_WINDOW_END"]})
            continue
        if sessions and session not in sessions:
            rejected.append({"theme_id": theme_id, "reason_codes": ["SAM_FUNDS_COMPLETENESS_SESSION_OUTSIDE_LOOKBACK"]})
            continue
        proof = ctx.resolver.resolve(row, session=session, require_complete=True, require_session=bool(session_value))
        if not proof.allowed:
            rejected.append({"theme_id": theme_id, "reason_codes": list(proof.reason_codes)})
            continue
        expected_scope = set(map(str, row.get("expected_scope") or row.get("expected_instruments") or row.get("instrument_ids") or ()))
        actual_scope = set(map(str, row.get("actual_scope") or row.get("actual_instruments") or ()))
        if expected_scope and not expected_members.issubset(expected_scope):
            rejected.append({"theme_id": theme_id, "reason_codes": ["SAM_FUNDS_COMPLETENESS_EXPECTED_SCOPE_MISMATCH"]})
            continue
        rows.append({
            "theme_id": theme_id,
            "session": str(session) if session else None,
            "window_start": str(window[0]) if window and window[0] else None,
            "window_end": str(window[1]) if window and window[1] else None,
            "provider_status": row.get("provider_status") or row.get("query_status") or row.get("completion_status") or "COMPLETE_NO_RECORD",
            "query_scope": row.get("query_scope") or row.get("scope") or "THEME_PEER_LOOKBACK",
            "expected_scope_size": len(expected_scope) if expected_scope else len(expected_members),
            "actual_scope_size": len(actual_scope) if actual_scope else 0,
            "source_refs": sorted(row.get("source_refs") or []),
            "source_resolution": {"source_ids": list(proof.source_ids), "reconstruction_used": proof.reconstruction_used},
        })
    return rows, rejected


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
    visible, sessions, rejected = _visible_theme_funds(ctx, iid, theme, end_index)
    classified, conflict_reason = _dedupe_funds([row for row in visible if _classified_capital(row)])
    proxy = [row for row in visible if _proxy_capital(row)]
    if conflict_reason:
        return _condition("UNKNOWN", [conflict_reason], details={"theme_id": theme.get("theme_id"), "grade": "UNKNOWN", "rejected_funds": rejected}), {
            "theme_id": theme.get("theme_id"),
            "classified_events": 0,
            "proxy_events": len(proxy),
            "rejected_events": len(rejected),
        }
    if not classified:
        completeness, completeness_rejected = _funds_completeness_for_theme(ctx, theme, end_index, sessions)
        if completeness and not proxy and not rejected:
            return _condition("FAIL", ["SAM_FUNDS_COMPLETE_NO_RECORD"], details={
                "theme_id": theme.get("theme_id"),
                "grade": "WEAK",
                "completeness_evidence": completeness,
            }), {
                "theme_id": theme.get("theme_id"),
                "classified_events": 0,
                "proxy_events": 0,
                "rejected_events": len(completeness_rejected),
                "completeness_status": "COMPLETE_NO_RECORD",
                "completeness_evidence": completeness,
                "completeness_rejected": completeness_rejected,
            }
        reason = (
            "SAM_FUNDS_PROXY_ONLY_PENDING_VERIFICATION" if proxy else
            "SAM_FUNDS_EVIDENCE_REJECTED_OR_UNVERIFIED" if rejected or completeness_rejected else
            "SAM_FUNDS_NOT_CAPTURED_OR_COMPLETENESS_UNPROVEN"
        )
        return _condition("UNKNOWN", [reason], details={
            "theme_id": theme.get("theme_id"),
            "grade": "UNKNOWN",
            "rejected_funds": rejected + completeness_rejected,
            "completeness_status": "UNKNOWN",
        }), {
            "theme_id": theme.get("theme_id"),
            "classified_events": 0,
            "proxy_events": len(proxy),
            "rejected_events": len(rejected) + len(completeness_rejected),
            "completeness_status": "UNKNOWN",
        }
    overlap, overlap_reason = _overlap_risk(classified)
    if overlap:
        return _condition("UNKNOWN", [overlap_reason], details={"theme_id": theme.get("theme_id"), "grade": "UNKNOWN"}), {
            "theme_id": theme.get("theme_id"),
            "classified_events": len(classified),
            "rejected_events": len(rejected),
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
            "source_refs": sorted(row.get("source_refs") or []),
            "source_resolution": row.get("_source_resolution"),
        })
    metrics = {
        "theme_id": theme.get("theme_id"),
        "classified_events": len(classified),
        "proxy_events": len(proxy),
        "rejected_events": len(rejected),
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
    passing_sectors = [row for row in sector_candidates if row.get("status") == "PASS"]
    if not passing_sectors:
        return _condition("UNKNOWN", ["SAM_FUNDS_WAITING_FOR_SECTOR_LINKAGE"], details={"grade": "UNKNOWN"}), []
    theme_by_id = {row["theme_id"]: row for row in visible_themes}
    rows = []
    for sector in passing_sectors:
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
        if row.get("status") != "PASS":
            continue
        for window in row.get("windows", {}).values():
            if window.get("coverage_status") != "COMPLETE":
                continue
            value = _frac(window.get("rs_sector_pct"))
            if value is not None and value > 0:
                return True
    return False


def _stage_start(ctx, iid, end_index, return_values, liquidity, rs_market, strength_momentum):
    seed = CONFIG["research_seed_parameters"]["setups"]["start"]
    a = _frac(liquidity.get("amount_ratio_prior5"))
    ret1 = return_values.get("1")
    prior_ret5 = _prior_window_return(ctx, iid, end_index, seed["prior_state_window_sessions_excluding_T"])
    prior_high_turnover = liquidity.get("high_turnover_days_5_excluding_T")
    prior_high = _prior_close_high(ctx, iid, end_index, seed["breakout_lookback_sessions_excluding_T"])
    close = _close_value(ctx, iid, end_index)
    rs1 = _rs_exact(rs_market, 1)
    momentum_state = strength_momentum.get("strength_momentum", "UNKNOWN")
    reasons, unknown = [], []
    if a is None:
        unknown.append("SAM_START_AMOUNT_RATIO_UNAVAILABLE")
    elif a < Fraction(str(seed["amount_ratio_prior5_min_inclusive"])):
        reasons.append("SAM_START_AMOUNT_RATIO_BELOW_SEED")
    if ret1 is None:
        unknown.append("SAM_START_RET1_UNAVAILABLE")
    elif ret1 <= Fraction(str(seed["ret1_min_exclusive"])):
        reasons.append("SAM_START_RET1_NOT_POSITIVE")
    if prior_ret5 is None:
        unknown.append("SAM_START_PRIOR_RET5_UNAVAILABLE")
    elif prior_ret5 > Fraction(str(seed["prior_ret5_max_inclusive"])):
        reasons.append("SAM_START_PRIOR_STATE_ALREADY_EXPANDED")
    if prior_high_turnover is None:
        unknown.append("SAM_START_PRIOR_TURNOVER_UNAVAILABLE")
    elif prior_high_turnover > seed["prior_high_turnover_days_5_max_inclusive"]:
        reasons.append("SAM_START_PRIOR_HIGH_TURNOVER_ALREADY_EXPANDED")
    if prior_high is None or close is None:
        unknown.append("SAM_START_BREAKOUT_REFERENCE_UNAVAILABLE")
    elif close <= prior_high:
        reasons.append("SAM_START_BREAKOUT_NOT_CONFIRMED")
    if rs1 is None:
        unknown.append("SAM_START_RS1_UNAVAILABLE")
    elif rs1 <= Fraction(str(seed["rs_market_1d_min_exclusive"])):
        reasons.append("SAM_START_RS1_NOT_POSITIVE")
    if momentum_state == "UNKNOWN":
        unknown.append("SAM_START_SHARED_MOMENTUM_UNAVAILABLE")
    elif momentum_state != seed.get("required_strength_momentum_state", "RISING"):
        reasons.append("SAM_START_SHARED_MOMENTUM_NOT_RISING")
    status = "FAIL" if reasons else "UNKNOWN" if unknown else "PASS"
    return _condition(status, reasons or unknown, details={
        "setup_id": "START",
        "setup_state": "START_CONFIRMED" if status == "PASS" else "START_PENDING_OR_REJECTED",
        "supporting_evidence": [] if status != "PASS" else ["SAM_START_SHARED_MOMENTUM_RISING", "SAM_START_BREAKOUT_CONFIRMED"],
        "counter_evidence": reasons,
        "critical_counter_evidence": [],
        "amount_ratio_policy": "APPLIES_ONLY_TO_START_STAGE",
        "A": _number(a),
        "ret1_pct": _pct(ret1),
        "prior_ret5_excluding_T_pct": _pct(prior_ret5),
        "prior_high_turnover_days_5_excluding_T": prior_high_turnover,
        "breakout_close": _number(close),
        "breakout_prior_high": _number(prior_high),
        "rs_market_1d_pct": _pct(rs1),
        "shared_momentum_primitive": strength_momentum.get("calculation_id"),
        "strength_momentum": strength_momentum,
    })


def _stage_acceleration(return_values, liquidity, market_status, rs_market, strength_momentum):
    seed = CONFIG["research_seed_parameters"]["setups"]["acceleration"]
    ret1, ret3, ret5, ret10 = (return_values.get(str(window)) for window in (1, 3, 5, 10))
    rs1, rs3, rs5 = (_rs_exact(rs_market, window) for window in (1, 3, 5))
    high_turnover_days = liquidity.get("high_turnover_days_5_including_T")
    level_points = [point.replace("SAM_STRENGTH_LEVEL", "SAM_ACCEL") for point in strength_momentum.get("level_points", [])]
    momentum_points, unknown = [], []
    strength_level = strength_momentum.get("strength_level", "UNKNOWN")
    momentum_state = strength_momentum.get("strength_momentum", "UNKNOWN")
    if strength_level == "UNKNOWN":
        unknown.extend(strength_momentum.get("level_unknown_points") or ["SAM_ACCEL_STRENGTH_LEVEL_UNAVAILABLE"])
    if momentum_state == "UNKNOWN":
        unknown.append("SAM_ACCEL_STRENGTH_MOMENTUM_UNAVAILABLE")
    elif momentum_state == "RISING":
        momentum_points.append("SAM_ACCEL_SHARED_MOMENTUM_RISING")
    elif momentum_state == "FALLING":
        momentum_points.append("SAM_ACCEL_SHARED_MOMENTUM_FALLING")
    turnover_status = "UNKNOWN"
    if high_turnover_days is None:
        unknown.append("SAM_ACCEL_HIGH_TURNOVER_PERSISTENCE_UNAVAILABLE")
    elif high_turnover_days >= seed["high_turnover_sessions_5_including_T_min_inclusive"]:
        turnover_status = "PASS"
    else:
        turnover_status = "FAIL"
    if market_status.get("instrument_limit", {}).get("close_limit_up") == TRUE:
        momentum_points.append("SAM_ACCEL_CLOSE_LIMIT_UP")
    if strength_level == "HIGH" and momentum_state == seed.get("required_strength_momentum_state", "RISING") and turnover_status == "PASS":
        status, reasons, state = "PASS", [], "ACCELERATION_CONFIRMED"
    elif strength_level == "HIGH" and momentum_state == "FALLING" and turnover_status == "PASS":
        status, reasons, state = "FAIL", ["SAM_ACCEL_WEAKENING_HIGH_ACTIVITY"], "WEAKENING_HIGH_ACTIVITY"
    elif unknown and strength_level != "LOW" and turnover_status != "FAIL":
        status, reasons, state = "UNKNOWN", sorted(set(unknown)), "ACCELERATION_PENDING"
    else:
        status, reasons, state = "FAIL", ["SAM_ACCEL_LEVEL_MOMENTUM_OR_TURNOVER_NOT_MET"], "ACCELERATION_NOT_CONFIRMED"
    return _condition(status, reasons, details={
        "setup_id": "ACCELERATION",
        "setup_state": state,
        "strength_level": strength_level,
        "strength_momentum": momentum_state,
        "supporting_evidence": level_points + [point for point in momentum_points if point != "SAM_ACCEL_SHARED_MOMENTUM_FALLING"],
        "counter_evidence": reasons if status == "FAIL" else [],
        "critical_counter_evidence": [],
        "level_points": level_points,
        "momentum_points": momentum_points,
        "unknown_points": sorted(set(unknown)),
        "shared_momentum_primitive": strength_momentum.get("calculation_id"),
        "strength_momentum_details": strength_momentum,
        "high_turnover_days_5_including_T": high_turnover_days,
        "turnover_persistence_status": turnover_status,
        "ret1_pct": _pct(ret1),
        "ret3_pct": _pct(ret3),
        "ret5_pct": _pct(ret5),
        "ret10_pct": _pct(ret10),
        "rs_market_1d_pct": _pct(rs1),
        "rs_market_3d_pct": _pct(rs3),
        "rs_market_5d_pct": _pct(rs5),
    })


def _stage_high_level_repair(ctx, iid, end_index, return_values, daily_path, market_status, rs_market, sector_candidates):
    seed = CONFIG["research_seed_parameters"]["setups"]["high_level_repair"]
    if daily_path.get("daily_path_status") != "AVAILABLE":
        return _condition("UNKNOWN", daily_path.get("reason_codes") or ["SAM_HLR_DAILY_PATH_UNAVAILABLE"], details={"setup_id": "HIGH_LEVEL_REPAIR"})
    if daily_path.get("close_limit_down") == TRUE:
        return _condition("FAIL", ["SAM_HLR_CLOSE_LIMIT_DOWN_VERIFIED"], details={"setup_id": "HIGH_LEVEL_REPAIR", "setup_state": "LATE_CYCLE_WARNING"})
    ret5 = _prior_window_return(ctx, iid, end_index, 5)
    ret10 = _prior_window_return(ctx, iid, end_index, 10)
    population10 = [
        value for candidate in ctx.eligible_ids
        if (value := _price_return(ctx, candidate, end_index - 1, 10)) is not None
    ] if end_index is not None and end_index > 0 else []
    percentile10 = percentile_rank_midpoint(ret10, population10) if ret10 is not None and population10 else None
    prior_event = None
    if end_index is not None and end_index > 0:
        cevents = [
            _bar_limit_event(_bar(ctx, session, iid), iid, session)
            for session in ctx.sessions[max(0, end_index - 20) : end_index]
        ]
        cann = annotate_streaks(cevents) if cevents else []
        prior_event = cann[-1] if cann else None
    high_level = []
    if ret5 is not None and ret5 >= Fraction(str(seed["background_ret5_min_inclusive"])):
        high_level.append("SAM_HLR_RET5_CONTEXT")
    if ret10 is not None and ret10 >= Fraction(str(seed["background_ret10_min_inclusive"])):
        high_level.append("SAM_HLR_RET10_CONTEXT")
    if percentile10 is not None and percentile10 >= seed["background_percentile10_min_inclusive"]:
        high_level.append("SAM_HLR_RETURN_PERCENTILE_CONTEXT")
    if prior_event and prior_event.streak_lower_bound:
        high_level.append("SAM_HLR_LIMIT_STREAK_CONTEXT")
    if not high_level:
        unknown = ret5 is None and ret10 is None and percentile10 is None
        return _condition("UNKNOWN" if unknown else "FAIL", ["SAM_HLR_HIGH_LEVEL_CONTEXT_UNAVAILABLE" if unknown else "SAM_HLR_HIGH_LEVEL_CONTEXT_NOT_MET"], details={
            "setup_id": "HIGH_LEVEL_REPAIR",
            "pre_event_high_level_state": "UNKNOWN" if unknown else "FAIL",
            "pre_event_ret5_excluding_T_pct": _pct(ret5),
            "pre_event_ret10_excluding_T_pct": _pct(ret10),
            "pre_event_percentile10": percentile10,
            "subconditions": {"HIGH_LEVEL_BACKGROUND": "UNKNOWN" if unknown else "FAIL"},
        })

    amplitude = daily_path.get("_amplitude")
    low_ret = daily_path.get("_low_ret")
    close_ret = daily_path.get("_close_ret")
    recovery = daily_path.get("_recovery")
    clv = _frac(daily_path.get("CLV"))
    rs1 = _rs_exact(rs_market, 1)
    sector_rs1 = _sector_rs_exact(sector_candidates, 1)
    divergence = []
    if amplitude is not None and amplitude >= Fraction(str(seed["divergence_amplitude_min_inclusive"])):
        divergence.append("SAM_HLR_AMPLITUDE")
    if low_ret is not None and low_ret <= Fraction(str(seed["divergence_low_return_max_inclusive"])):
        divergence.append("SAM_HLR_DEEP_LOW")
    if daily_path.get("touched_limit_down") == TRUE:
        divergence.append("SAM_HLR_TOUCHED_LIMIT_DOWN")
    if not divergence:
        return _condition("FAIL", ["SAM_HLR_DIVERGENCE_PATH_NOT_MET"], details={
            "setup_id": "HIGH_LEVEL_REPAIR",
            "pre_event_high_level_state": "PASS",
            "pre_event_ret5_excluding_T_pct": _pct(ret5),
            "pre_event_ret10_excluding_T_pct": _pct(ret10),
            "pre_event_percentile10": percentile10,
            "high_level_context": high_level,
            "subconditions": {"HIGH_LEVEL_BACKGROUND": "PASS", "TODAY_DIVERGENCE": "FAIL"},
        })

    repair, repair_fail, repair_unknown = [], [], []
    if recovery is not None and recovery >= Fraction(str(seed["repair_recovery_points_min_inclusive"])):
        repair.append("SAM_HLR_RECOVERY_POINTS")
    else:
        (repair_unknown if recovery is None else repair_fail).append("SAM_HLR_RECOVERY_POINTS_NOT_MET")
    if clv is not None and clv >= Fraction(str(seed["repair_clv_min_inclusive"])):
        repair.append("SAM_HLR_CLV_REPAIR")
    else:
        (repair_unknown if clv is None else repair_fail).append("SAM_HLR_CLV_REPAIR_NOT_MET")
    support = []
    if close_ret is None:
        repair_unknown.append("SAM_HLR_CLOSE_RETURN_UNAVAILABLE")
    elif close_ret > Fraction(str(seed["repair_close_return_min_exclusive"])):
        support.append("SAM_HLR_CLOSE_RETURN_POSITIVE")
    if rs1 is not None and rs1 > Fraction(str(seed["repair_today_rs_market_min_exclusive"])):
        support.append("SAM_HLR_TODAY_RS_MARKET_SUPPORT")
    if sector_rs1 is not None and sector_rs1 > 0:
        support.append("SAM_HLR_TODAY_RS_SECTOR_SUPPORT")
    if support:
        repair.extend(support)
    elif rs1 is None and sector_rs1 is None and close_ret is None:
        repair_unknown.append("SAM_HLR_TODAY_REPAIR_SUPPORT_UNAVAILABLE")
    else:
        repair_fail.append("SAM_HLR_TODAY_REPAIR_SUPPORT_NOT_MET")
    if repair_fail:
        return _condition("FAIL", sorted(set(repair_fail)), details={
            "setup_id": "HIGH_LEVEL_REPAIR",
            "setup_state": "HIGH_LEVEL_DIVERGENCE_WITHOUT_TODAY_REPAIR",
            "pre_event_high_level_state": "PASS",
            "pre_event_ret5_excluding_T_pct": _pct(ret5),
            "pre_event_ret10_excluding_T_pct": _pct(ret10),
            "pre_event_percentile10": percentile10,
            "high_level_context": high_level,
            "divergence_evidence": divergence,
            "repair_evidence": repair,
            "counter_evidence": sorted(set(repair_fail)),
            "critical_counter_evidence": [],
            "legacy_setup_alias": "HIGH_LEVEL_DIVERGENCE",
            "subconditions": {"HIGH_LEVEL_BACKGROUND": "PASS", "TODAY_DIVERGENCE": "PASS", "TODAY_REPAIR": "FAIL"},
        })
    if repair_unknown:
        return _condition("UNKNOWN", sorted(set(repair_unknown)), details={
            "setup_id": "HIGH_LEVEL_REPAIR",
            "setup_state": "HIGH_LEVEL_REPAIR_PENDING",
            "pre_event_high_level_state": "PASS",
            "pre_event_ret5_excluding_T_pct": _pct(ret5),
            "pre_event_ret10_excluding_T_pct": _pct(ret10),
            "pre_event_percentile10": percentile10,
            "high_level_context": high_level,
            "divergence_evidence": divergence,
            "repair_evidence": repair,
            "counter_evidence": [],
            "critical_counter_evidence": [],
            "legacy_setup_alias": "HIGH_LEVEL_DIVERGENCE",
            "subconditions": {"HIGH_LEVEL_BACKGROUND": "PASS", "TODAY_DIVERGENCE": "PASS", "TODAY_REPAIR": "UNKNOWN"},
        })
    return _condition("PASS", details={
        "setup_id": "HIGH_LEVEL_REPAIR",
        "setup_state": "HIGH_LEVEL_REPAIR_CONFIRMED",
        "pre_event_high_level_state": "PASS",
        "pre_event_ret5_excluding_T_pct": _pct(ret5),
        "pre_event_ret10_excluding_T_pct": _pct(ret10),
        "pre_event_percentile10": percentile10,
        "high_level_context": high_level,
        "divergence_evidence": divergence,
        "repair_evidence": repair,
        "supporting_evidence": high_level + divergence + repair,
        "counter_evidence": [],
        "critical_counter_evidence": [],
        "legacy_setup_alias": "HIGH_LEVEL_DIVERGENCE",
        "amount_ratio_policy": "NOT_USED_FOR_HIGH_LEVEL_REPAIR_GATE",
        "clv_policy": "SAME_DAY_REPAIR_GATE_FOR_HIGH_LEVEL_REPAIR_ONLY",
        "subconditions": {"HIGH_LEVEL_BACKGROUND": "PASS", "TODAY_DIVERGENCE": "PASS", "TODAY_REPAIR": "PASS"},
    })


def _stage_trend_pullback(trend, liquidity, rs_market, sector_candidates):
    seed = CONFIG["research_seed_parameters"]["setups"]["trend_pullback"]
    if trend["status"] == "UNKNOWN":
        return _condition("UNKNOWN", trend.get("reason_codes") or ["SAM_TREND_PULLBACK_TREND_UNKNOWN"], details={"setup_id": "TREND_PULLBACK"})
    if trend.get("trend_state") != "STRONG_UPTREND":
        return _condition("FAIL", trend.get("reason_codes") or ["SAM_TREND_PULLBACK_TREND_FAIL"])
    days = trend.get("pullback_days_below_ma5")
    distance = _frac(trend.get("matched_ma_distance"))
    a = _frac(liquidity.get("amount_ratio_prior5"))
    rs1 = _rs_exact(rs_market, 1)
    sector_rs1 = _sector_rs_exact(sector_candidates, 1)
    min_days, max_days = seed["below_ma5_sessions_inclusive"]
    reasons, unknown = [], []
    if days is None:
        unknown.append("SAM_TREND_PULLBACK_DAYS_UNKNOWN")
    elif not min_days <= days <= max_days:
        reasons.append("SAM_TREND_PULLBACK_DAYS_OUTSIDE_SEED")
    if distance is None:
        unknown.append("SAM_TREND_PULLBACK_DISTANCE_UNKNOWN")
    elif distance > Fraction(str(seed["near_ma_5_10_20_fraction_inclusive"])):
        reasons.append("SAM_TREND_PULLBACK_NOT_NEAR_MA5_10_20")
    if a is None:
        unknown.append("SAM_TREND_PULLBACK_AMOUNT_CONTRACTION_UNAVAILABLE")
    elif a > Fraction(str(seed["amount_contraction_ratio_max_inclusive"])):
        reasons.append("SAM_TREND_PULLBACK_AMOUNT_NOT_CONTRACTED")
    known_rs = [value for value in (rs1, sector_rs1) if value is not None]
    if known_rs and all(value < Fraction(str(seed["resilience_rs_market_1d_min_inclusive"])) for value in known_rs):
        reasons.append("SAM_TREND_PULLBACK_RELATIVE_WEAKNESS_COUNTER")
    elif not known_rs:
        unknown.append("SAM_TREND_PULLBACK_RESILIENCE_UNKNOWN")
    status = "FAIL" if reasons else "UNKNOWN" if unknown else "PASS"
    return _condition(status, reasons or unknown, details={
        "setup_id": "TREND_PULLBACK",
        "setup_state": "TREND_PULLBACK_CONFIRMED" if status == "PASS" else "TREND_PULLBACK_PENDING_OR_REJECTED",
        "pullback_days_below_ma5": days,
        "matched_ma": trend.get("matched_ma"),
        "matched_ma_distance": trend.get("matched_ma_distance"),
        "near_ma_threshold": seed["near_ma_5_10_20_fraction_inclusive"],
        "amount_ratio_prior5": _number(a),
        "rs_market_1d_pct": _pct(rs1),
        "rs_sector_1d_pct": _pct(sector_rs1),
    })


def _stages(ctx, iid, end_index, return_values, daily_path, liquidity, trend, market_status, rs_market, sector_candidates):
    strength_momentum = _strength_momentum(ctx, iid, end_index, return_values, rs_market, liquidity)
    candidates = {
        "START": _stage_start(ctx, iid, end_index, return_values, liquidity, rs_market, strength_momentum),
        "ACCELERATION": _stage_acceleration(return_values, liquidity, market_status, rs_market, strength_momentum),
        "HIGH_LEVEL_REPAIR": _stage_high_level_repair(ctx, iid, end_index, return_values, daily_path, market_status, rs_market, sector_candidates),
        "TREND_PULLBACK": _stage_trend_pullback(trend, liquidity, rs_market, sector_candidates),
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
        "shared_strength_momentum": strength_momentum,
        "legacy_stage_aliases": CONFIG["research_seed_parameters"]["setups"].get("legacy_aliases", {}),
        "stage_label_policy": CONFIG["traceability"]["setup_label_policy"],
    })


def _behavioral_confirmation(primary_setup, daily_path, stages):
    path_available = daily_path.get("daily_path_status") == "AVAILABLE"
    low_ret = daily_path.get("_low_ret")
    close_ret = daily_path.get("_close_ret")
    recovery = daily_path.get("_recovery")
    intraday_status = "UNKNOWN"
    intraday_reasons = ["SAM_INTRADAY_PATH_UNAVAILABLE"]
    if path_available:
        intraday_pass = low_ret is not None and close_ret is not None and recovery is not None and low_ret < 0 and close_ret > 0 and recovery > 0
        intraday_status = "PASS" if intraday_pass else "FAIL"
        intraday_reasons = [] if intraday_pass else ["SAM_INTRADAY_WEAK_TO_STRONG_NOT_CONFIRMED_FROM_DAILY_OHLC"]
    events = {
        "AUCTION_WEAK_TO_STRONG": {"status": "UNKNOWN", "reason_codes": ["SAM_AUCTION_PHASES_NOT_IN_MARKET_INPUT"], "signal_policy": "NO_FINAL_AUCTION_SNAPSHOT_PROXY"},
        "INTRADAY_WEAK_TO_STRONG": {"status": intraday_status, "reason_codes": intraday_reasons, "evidence_basis": "DAILY_OHLC_ONLY"},
        "NEXT_SESSION_REVERSAL": {"status": "NOT_EVALUATED", "reason_codes": ["SAM_TRACKING_OUTCOME_ONLY_NOT_SIGNAL_FEATURE"]},
        "REPAIR_LATENCY": {"status": "NOT_EVALUATED", "reason_codes": ["SAM_TRACKING_OUTCOME_ONLY_NOT_SIGNAL_FEATURE"]},
        "TIME_TO_NEW_HIGH": {"status": "NOT_EVALUATED", "reason_codes": ["SAM_TRACKING_OUTCOME_ONLY_NOT_SIGNAL_FEATURE"]},
        "FAILED_LIMIT_UP_REPAIR": {"status": "UNKNOWN", "reason_codes": ["SAM_INTRADAY_LIMIT_UP_REPAIR_SEQUENCE_UNAVAILABLE"]},
        "SUPPLY_ABSORPTION": {"status": "UNKNOWN", "reason_codes": ["SAM_SUPPLY_ABSORPTION_NEEDS_MULTI_SESSION_RECLAIM_TRACKING"]},
        "BREAKOUT_HOLD": {"status": "NOT_EVALUATED", "reason_codes": ["SAM_TRACKING_OUTCOME_ONLY_NOT_SIGNAL_FEATURE"]},
        "BREAKOUT_FAILURE": {"status": "NOT_EVALUATED", "reason_codes": ["SAM_TRACKING_OUTCOME_ONLY_NOT_SIGNAL_FEATURE"]},
        "TEMPORAL_LEADERSHIP": {"status": "UNKNOWN", "reason_codes": ["SAM_FIRST_SEAL_OR_FIRST_MOVE_TIMESTAMPS_NOT_IN_MARKET_INPUT"]},
    }
    setup = (stages.get("stage_candidates") or {}).get(primary_setup or "", {})
    requirements_by_setup = CONFIG["research_seed_parameters"]["behavioral_confirmation"].get("setup_requirements", {})
    required_events = list(requirements_by_setup.get(primary_setup or "", []))
    if not primary_setup:
        status = "UNKNOWN"
        reasons = ["SAM_BEHAVIORAL_WAITING_FOR_SETUP"]
    elif setup.get("status") != "PASS":
        status = setup.get("status", "UNKNOWN")
        reasons = setup.get("reason_codes") or []
    elif not required_events:
        status = "PASS"
        reasons = []
    else:
        required_statuses = [events[event]["status"] for event in required_events if event in events]
        if any(state == "FAIL" for state in required_statuses):
            status = "FAIL"
        elif any(state == "UNKNOWN" for state in required_statuses):
            status = "UNKNOWN"
        else:
            status = "PASS"
        reasons = [] if status == "PASS" else sorted({
            reason
            for event in required_events
            for reason in events.get(event, {}).get("reason_codes", [])
            if events.get(event, {}).get("status") in {"FAIL", "UNKNOWN"}
        })
    return _condition(status, reasons, details={
        "primary_setup": primary_setup,
        "events": events,
        "required_events": required_events,
        "required_events_by_setup": requirements_by_setup,
        "setup_specific_policy": "BEHAVIORAL_FAIL_BLOCKS_ONLY_THE_SETUP_THAT_REQUIRES_IT",
    })


def _supply_pressure(ctx, iid, end_index):
    seed = CONFIG["research_seed_parameters"]["supply_pressure"]
    count = seed["recent_window_sessions_including_T"]
    if end_index is None or end_index - count + 1 < 0:
        return _condition("UNKNOWN", ["SAM_SUPPLY_PRESSURE_WINDOW_UNAVAILABLE"], details={"upper_shadow_rows": []})
    threshold = Fraction(str(seed["long_upper_shadow_to_range_min_inclusive"]))
    current_close = _close_value(ctx, iid, end_index)
    rows, long_count, unresolved, reclaimed_failed = [], 0, 0, 0
    for index in range(end_index - count + 1, end_index + 1):
        bar = _bar(ctx, ctx.sessions[index], iid)
        open_, high, low, close = (_frac((bar or {}).get(key)) for key in ("open", "high", "low", "close"))
        if any(value is None for value in (open_, high, low, close)) or high <= low:
            return _condition("UNKNOWN", ["SAM_SUPPLY_PRESSURE_DAILY_PATH_UNAVAILABLE"], details={"upper_shadow_rows": rows})
        shadow = (high - max(open_, close)) / (high - low)
        is_long = shadow >= threshold
        reclaim_dates = []
        for future_index in range(index + 1, end_index + 1):
            future_close = _close_value(ctx, iid, future_index)
            if future_close is not None and future_close >= high:
                reclaim_dates.append(ctx.sessions[future_index])
        ever_reclaimed = bool(reclaim_dates)
        first_reclaim_date = reclaim_dates[0] if reclaim_dates else None
        current_above = current_close is not None and current_close >= high
        unresolved_supply = is_long and not ever_reclaimed and not current_above
        reclaimed_then_failed = is_long and ever_reclaimed and not current_above
        long_count += int(is_long)
        unresolved += int(unresolved_supply)
        reclaimed_failed += int(reclaimed_then_failed)
        rows.append({
            "session": ctx.sessions[index],
            "shadow_high": _number(high, places=8),
            "upper_shadow_to_range": _number(shadow),
            "long_upper_shadow": is_long,
            "first_reclaim_date": first_reclaim_date,
            "sessions_to_first_reclaim": ctx.sessions.index(first_reclaim_date) - index if first_reclaim_date else None,
            "ever_reclaimed": ever_reclaimed,
            "reclaimed_then_failed": reclaimed_then_failed,
            "current_above_reclaimed_level": current_above,
            "unresolved_supply": unresolved_supply,
            "unreclaimed_by_observation_close": unresolved_supply,
        })
    warning_min = seed["unreclaimed_recent_shadow_count_warning_min_inclusive"]
    status = "UNKNOWN" if unresolved >= warning_min else "PASS"
    reasons = ["SAM_REPEATED_UNRECLAIMED_UPPER_SHADOW_SUPPLY_WARNING"] if status == "UNKNOWN" else []
    return _condition(status, reasons, details={
        "upper_shadow_rows": rows,
        "long_upper_shadow_count": long_count,
        "unreclaimed_recent_shadow_count": unresolved,
        "unresolved_supply_count": unresolved,
        "reclaimed_then_failed_count": reclaimed_failed,
        "single_upper_shadow_policy": "NOT_CRITICAL_REJECTION_WITHOUT_REPEATED_UNRECLAIMED_SUPPLY",
    })


def _market_compatibility(primary_setup, market_status):
    if not primary_setup:
        return _condition("UNKNOWN", ["SAM_MARKET_COMPATIBILITY_WAITING_FOR_SETUP"], details={"ecosystem_state": market_status.get("ecosystem_state")})
    state = market_status.get("ecosystem_state", "UNKNOWN")
    if state == "UNKNOWN":
        return _condition("UNKNOWN", market_status.get("reason_codes") or ["SAM_MARKET_CONTEXT_UNKNOWN"], details={"primary_setup": primary_setup, "ecosystem_state": state})
    compatible = set(CONFIG["research_seed_parameters"]["market_context"]["setup_compatibility"].get(primary_setup, []))
    if state in compatible:
        return _condition("PASS", details={"primary_setup": primary_setup, "ecosystem_state": state, "compatible_states": sorted(compatible)})
    return _condition("FAIL", ["SAM_MARKET_CONTEXT_INCOMPATIBLE_WITH_SETUP"], details={"primary_setup": primary_setup, "ecosystem_state": state, "compatible_states": sorted(compatible)})


def _same_theme_context(primary_setup, sector, funds, sector_candidates, funds_rows):
    if not primary_setup:
        return _condition("UNKNOWN", ["SAM_SAME_THEME_WAITING_FOR_SETUP"], details={
            "sector_status": sector.get("status"),
            "funds_status": funds.get("status"),
            "primary_theme_id": funds.get("primary_theme_id") or sector.get("primary_theme_id"),
            "context_status": "UNKNOWN",
            "same_theme_policy": "SETUP_SPECIFIC_SECTOR_AND_FUNDS_MUST_SHARE_THEME",
        })
    funds_by_theme = {row["theme_id"]: row["funds_condition"] for row in funds_rows}
    supportive_unknown, neutral, negative, hard_fail = [], [], [], []
    for candidate in sector_candidates:
        compatibility = ((candidate.get("setup_compatibility") or {}).get(primary_setup) or {})
        context_status = compatibility.get("context_status", "UNKNOWN")
        theme_id = candidate.get("theme_id")
        if context_status == "SUPPORTIVE":
            fund_condition = funds_by_theme.get(theme_id)
            if fund_condition is None or fund_condition.get("status") == "UNKNOWN":
                supportive_unknown.extend((fund_condition or {}).get("reason_codes") or ["SAM_FUNDS_CONTEXT_FOR_SUPPORTIVE_THEME_UNKNOWN"])
                continue
            if fund_condition.get("status") == "PASS":
                return _condition("PASS", details={
                    "sector_status": sector.get("status"),
                    "funds_status": funds.get("status"),
                    "primary_theme_id": theme_id,
                    "context_status": "SUPPORTIVE",
                    "setup_id": primary_setup,
                    "same_theme_policy": "SETUP_SPECIFIC_SECTOR_AND_FUNDS_MUST_SHARE_THEME",
                })
            hard_fail.extend(fund_condition.get("reason_codes") or ["SAM_FUNDS_CONTEXT_KNOWN_FAIL"])
        elif context_status == "NEGATIVE":
            negative.extend(compatibility.get("reason_codes") or ["SAM_SECTOR_CONTEXT_NEGATIVE_FOR_SETUP"])
        elif context_status == "NEUTRAL":
            neutral.extend(compatibility.get("reason_codes") or ["SAM_SECTOR_CONTEXT_NEUTRAL_FOR_SETUP"])
        else:
            supportive_unknown.extend(compatibility.get("reason_codes") or ["SAM_SECTOR_CONTEXT_UNKNOWN_FOR_SETUP"])
    if hard_fail:
        return _condition("FAIL", sorted(set(hard_fail)), details={
            "sector_status": sector.get("status"),
            "funds_status": funds.get("status"),
            "primary_theme_id": funds.get("primary_theme_id") or sector.get("primary_theme_id"),
            "context_status": "NEGATIVE",
            "setup_id": primary_setup,
            "same_theme_policy": "SETUP_SPECIFIC_SECTOR_AND_FUNDS_MUST_SHARE_THEME",
        })
    if negative and not supportive_unknown and not neutral:
        return _condition("FAIL", sorted(set(negative)), details={
            "sector_status": sector.get("status"),
            "funds_status": funds.get("status"),
            "primary_theme_id": sector.get("primary_theme_id"),
            "context_status": "NEGATIVE",
            "setup_id": primary_setup,
            "same_theme_policy": "SETUP_SPECIFIC_SECTOR_AND_FUNDS_MUST_SHARE_THEME",
        })
    reasons = sorted(set(supportive_unknown + neutral + _condition_reasons((sector, funds)))) or ["SAM_SAME_THEME_CONTEXT_NOT_SUPPORTIVE"]
    return _condition("UNKNOWN", reasons, details={
        "sector_status": sector.get("status"),
        "funds_status": funds.get("status"),
        "primary_theme_id": funds.get("primary_theme_id") or sector.get("primary_theme_id"),
        "context_status": "UNKNOWN" if supportive_unknown else "NEUTRAL",
        "setup_id": primary_setup,
        "same_theme_policy": "SETUP_SPECIFIC_SECTOR_AND_FUNDS_MUST_SHARE_THEME",
    })


def _critical_counterevidence(daily_path, stages, supply, trend):
    reasons = []
    if daily_path.get("close_limit_down") == TRUE:
        reasons.extend(["SAM_CRITICAL_CLOSE_LIMIT_DOWN", "SAM_LATE_CYCLE_WARNING"])
    if trend.get("trend_state") == "TREND_BROKEN":
        reasons.append("SAM_CRITICAL_TREND_BROKEN")
    if reasons:
        return _condition("FAIL", sorted(set(reasons)), details={"critical_policy": "VERIFIED_HARD_COUNTEREVIDENCE_ONLY"})
    if supply.get("status") == "UNKNOWN" and "SAM_REPEATED_UNRECLAIMED_UPPER_SHADOW_SUPPLY_WARNING" in supply.get("reason_codes", []):
        return _condition("UNKNOWN", supply.get("reason_codes"), details={"critical_policy": "SUPPLY_WARNING_NOT_SINGLE_SHADOW_REJECTION"})
    return _condition("PASS", details={"critical_policy": "VERIFIED_HARD_COUNTEREVIDENCE_ONLY"})


def _decision(liquidity, trend, stages, behavioral, market_compatibility, same_theme, critical):
    primary = stages.get("primary_stage")
    accel = (stages.get("stage_candidates") or {}).get("ACCELERATION", {})
    hard_failures = []
    for condition, code in (
        (liquidity, "SAM_DECISION_LIQUIDITY_HARD_FAIL"),
        (trend, "SAM_DECISION_TREND_HARD_FAIL"),
        (critical, "SAM_DECISION_CRITICAL_COUNTEREVIDENCE"),
        (market_compatibility, "SAM_DECISION_MARKET_CONTEXT_INCOMPATIBLE"),
        (same_theme, "SAM_DECISION_SAME_THEME_CONTEXT_KNOWN_FAIL"),
    ):
        if condition.get("status") == "FAIL":
            hard_failures.append(code)
    if stages.get("status") == "FAIL" and accel.get("setup_state") != "WEAKENING_HIGH_ACTIVITY":
        hard_failures.append("SAM_DECISION_NO_PASSING_SETUP")
    if hard_failures:
        return _condition("REJECTED", sorted(set(hard_failures)), details={"decision_status": "REJECTED", "primary_setup": primary, "watch_reasons": []})
    if behavioral.get("status") == "FAIL":
        return _condition("WATCH", ["SAM_WATCH_BEHAVIORAL_CONFIRMATION_FAILED_FOR_SETUP"], details={"decision_status": "WATCH", "primary_setup": primary, "watch_reasons": ["SAM_WATCH_BEHAVIORAL_CONFIRMATION_FAILED_FOR_SETUP"]})
    if not primary and accel.get("setup_state") == "WEAKENING_HIGH_ACTIVITY":
        return _condition("WATCH", ["SAM_WATCH_WEAKENING_HIGH_ACTIVITY"], details={
            "decision_status": "WATCH",
            "primary_setup": primary,
            "observation_bucket": "WEAKENING_HIGH_ACTIVITY_OBSERVATION",
            "watch_reasons": ["SAM_WATCH_WEAKENING_HIGH_ACTIVITY"],
        })
    unknown_core = []
    for condition, code in (
        (liquidity, "SAM_DECISION_LIQUIDITY_UNKNOWN"),
        (trend, "SAM_DECISION_TREND_UNKNOWN"),
        (stages, "SAM_DECISION_SETUP_UNKNOWN"),
    ):
        if condition.get("status") == "UNKNOWN":
            unknown_core.append(code)
    if unknown_core:
        return _condition("UNKNOWN", unknown_core, details={"decision_status": "UNKNOWN", "primary_setup": primary, "watch_reasons": []})
    watch = []
    for condition, code in (
        (behavioral, "SAM_WATCH_BEHAVIORAL_CONFIRMATION_PENDING"),
        (market_compatibility, "SAM_WATCH_MARKET_CONTEXT_INCOMPLETE"),
        (same_theme, "SAM_WATCH_SAME_THEME_CONTEXT_INCOMPLETE"),
        (critical, "SAM_WATCH_COUNTEREVIDENCE_REVIEW_PENDING"),
    ):
        if condition.get("status") == "UNKNOWN":
            watch.append(code)
    if watch:
        return _condition("WATCH", watch, details={"decision_status": "WATCH", "primary_setup": primary, "watch_reasons": watch})
    return _condition("FOCUS", ["SAM_FOCUS_BY_STAGE_AWARE_V2_REQUIREMENTS"], details={"decision_status": "FOCUS", "primary_setup": primary, "watch_reasons": []})


def _setup_rank_features(row):
    setup = row.get("primary_stage")
    if setup == "START":
        return {"ret1_pct": row.get("ret1_pct"), "A": row.get("A"), "RS_market_1d_pct": row.get("RS_market_1d_pct")}
    if setup == "ACCELERATION":
        return {"ret3_pct": row.get("ret3_pct"), "ret5_pct": row.get("ret5_pct"), "high_turnover_days_5_including_T": row.get("high_turnover_days_5_including_T")}
    if setup == "HIGH_LEVEL_REPAIR":
        return {"recovery_points_pct": row.get("recovery_points_pct"), "CLV": row.get("CLV"), "RS_market_1d_pct": row.get("RS_market_1d_pct")}
    if setup == "TREND_PULLBACK":
        return {"matched_ma_distance": (row.get("trend_strength") or {}).get("matched_ma_distance"), "RS_sector_1d_pct": row.get("RS_sector_1d_pct"), "A": row.get("A")}
    return {}


def _rank_key_value(value, *, ascending=False):
    numeric = _frac(value)
    if numeric is None:
        return Fraction(10**9)
    return numeric if ascending else -numeric


def _row_score_key(row):
    setup = row.get("primary_stage")
    features = row.get("setup_rank_features") or {}
    if setup == "TREND_PULLBACK":
        feature_key = (_rank_key_value(features.get("matched_ma_distance"), ascending=True), _rank_key_value(features.get("RS_sector_1d_pct")), _rank_key_value(features.get("A")))
    else:
        feature_key = tuple(_rank_key_value(value) for value in features.values())
    return (feature_key, row["instrument_id"])


def _rank_rows(rows):
    decision_order = {"FOCUS": 0, "WATCH": 1, "UNKNOWN": 2, "REJECTED": 3}
    rows.sort(key=lambda row: (
        decision_order.get(row.get("decision_status") or row.get("status"), 9),
        PRIMARY_STAGE_PRECEDENCE.index(row.get("primary_stage")) if row.get("primary_stage") in PRIMARY_STAGE_PRECEDENCE else len(PRIMARY_STAGE_PRECEDENCE),
        _row_score_key(row),
    ))
    setup_counts = defaultdict(int)
    for display_order, row in enumerate(rows, 1):
        decision_tier = row.get("decision_status") or row.get("status")
        setup_id = row.get("primary_stage") or row.get("observation_bucket") or "NO_PASSING_SETUP"
        rank_key = (decision_tier, setup_id)
        setup_counts[rank_key] += 1
        tier_rank = setup_counts[rank_key]
        row["decision_tier"] = decision_tier
        row["rank_scope"] = f"{decision_tier}:{setup_id}"
        row["setup_id_for_rank"] = setup_id
        row["setup_rank"] = tier_rank
        row["focus_rank"] = tier_rank if decision_tier == "FOCUS" else None
        row["watch_rank"] = tier_rank if decision_tier == "WATCH" else None
        row["diagnostic_rank"] = tier_rank if decision_tier in {"UNKNOWN", "REJECTED"} else None
        row["display_order"] = display_order
        row["rank"] = display_order
        row["rank_semantics"] = "SETUP_AND_DECISION_TIER_RESEARCH_ORDER_NOT_GLOBAL_SCORE"
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
    decision_counts = {state: 0 for state in CONFIG["hard_requirements"]["final_decision_states"]}
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
        stages = _stages(ctx, iid, end_index, returns, daily_path, liquidity, trend, market_status, rs_market, sector_candidates)
        behavioral = _behavioral_confirmation(stages.get("primary_stage"), daily_path, stages)
        supply = _supply_pressure(ctx, iid, end_index)
        market_compatibility = _market_compatibility(stages.get("primary_stage"), market_status)
        same_theme = _same_theme_context(stages.get("primary_stage"), sector, funds, sector_candidates, funds_rows)
        critical = _critical_counterevidence(daily_path, stages, supply, trend)
        decision = _decision(liquidity, trend, stages, behavioral, market_compatibility, same_theme, critical)
        decision_status = decision.get("decision_status", decision["status"])
        decision_counts[decision_status] += 1
        formal_status = "PASS" if decision_status == "FOCUS" else "FAIL" if decision_status == "REJECTED" else "UNKNOWN"
        conditions = {
            "liquidity_bottom": liquidity,
            "trend_state": trend,
            "setup_identification": stages,
            "behavioral_confirmation": behavioral,
            "market_compatibility": market_compatibility,
            "sector_context": sector,
            "capital_linkage": funds,
            "same_theme_context": same_theme,
            "critical_counterevidence": critical,
        }
        for key, condition in conditions.items():
            condition_counts[key][condition["status"]] += 1
        for stage, condition in stages["stage_candidates"].items():
            stage_counts[stage][condition["status"]] += 1
        ref = reference.get(iid)
        return_fields = {
            f"ret{window}_pct": _pct(value)
            for window, value in returns.items()
        }
        rs_market_1 = rs_market.get("windows", {}).get("1", {}).get("rs_market_pct")
        primary_setup = stages.get("primary_stage")
        primary_sector = next((
            row for row in sector_candidates
            if ((row.get("setup_compatibility") or {}).get(primary_setup or "", {}) or {}).get("context_status") == "SUPPORTIVE"
        ), next((row for row in sector_candidates if row.get("status") == "PASS"), sector_candidates[0] if sector_candidates else {}))
        rs_sector_1 = (primary_sector.get("windows") or {}).get("1", {}).get("rs_sector_pct")
        strength = stages.get("shared_strength_momentum") or {}
        row = {
            "strategy_id": STAGE_AWARE_ID,
            "canonical_strategy_id": STAGE_AWARE_ID,
            "legacy_read_aliases": list(LEGACY_STAGE_AWARE_IDS),
            "implementation_version": IMPLEMENTATION_VERSION,
            "spec_revision": SPEC_REVISION,
            "instrument_id": iid,
            "name": security.name,
            "observation_date": ctx.observation_date,
            "status": decision_status,
            "formal_status": formal_status,
            "decision_status": decision_status,
            "watch_reasons": decision.get("watch_reasons", []),
            "observation_bucket": decision.get("observation_bucket"),
            "reason_codes": sorted(set(decision.get("reason_codes", []) + _condition_reasons(conditions.values()))) or ["SAM_FOCUS_BY_STAGE_AWARE_V2_REQUIREMENTS"],
            "condition_statuses": {key: condition["status"] for key, condition in conditions.items()},
            "conditions": conditions,
            "decision_layer": decision,
            "parameter_status": PARAMETER_STATUS,
            "parameter_status_label_zh": PARAMETER_STATUS_LABEL_ZH,
            "primary_stage": stages.get("primary_stage"),
            "alternate_stages": stages.get("alternate_stages", []),
            "stage_candidates": stages.get("stage_candidates", {}),
            "legacy_stage_aliases": stages.get("legacy_stage_aliases", {}),
            "stage_label_policy": stages.get("stage_label_policy"),
            "liquidity_status": liquidity["status"],
            "sector_status": sector["status"],
            "funds_status": funds["status"],
            "funds_grade": funds.get("grade"),
            "capital_evidence_status": funds["status"],
            "market_compatibility_status": market_compatibility["status"],
            "same_theme_context_status": same_theme["status"],
            "behavioral_confirmation_status": behavioral["status"],
            "critical_counterevidence_status": critical["status"],
            "trend_state": trend.get("trend_state"),
            "market_context_state": market_status.get("ecosystem_state"),
            "relative_strength_status": rs_market["status"],
            "strength_level": strength.get("strength_level"),
            "strength_level_raw": strength.get("strength_level_raw"),
            "strength_momentum": strength.get("strength_momentum"),
            "strength_momentum_raw": strength.get("strength_momentum_raw"),
            "return_momentum": strength.get("return_momentum"),
            "return_momentum_raw": strength.get("return_momentum_raw"),
            "rs_level": strength.get("strength_level_raw", {}).get("rs_5d_pct") if strength.get("strength_level_raw") else None,
            "rs_momentum": strength.get("rs_momentum"),
            "rs_momentum_raw": strength.get("rs_momentum_raw"),
            "activity_momentum": strength.get("activity_momentum"),
            "activity_momentum_raw": strength.get("activity_momentum_raw"),
            "shared_strength_momentum": strength,
            "primary_sector_level_state": primary_sector.get("sector_level_state"),
            "primary_sector_momentum_state": primary_sector.get("sector_momentum_state"),
            "primary_sector_breadth_state": primary_sector.get("breadth_state"),
            "primary_sector_stock_role": primary_sector.get("stock_role"),
            "primary_sector_absolute_level": primary_sector.get("sector_absolute_level"),
            "primary_sector_relative_level": primary_sector.get("sector_relative_level"),
            "primary_sector_market_rs_pct": primary_sector.get("sector_market_rs_pct"),
            "primary_sector_stock_role_known_feature_count": primary_sector.get("stock_role_known_feature_count"),
            "primary_sector_stock_role_unknown_feature_count": primary_sector.get("stock_role_unknown_feature_count"),
            "primary_sector_stock_role_evidence_coverage": primary_sector.get("stock_role_evidence_coverage"),
            "close": _number(_bar_decimal(current, "close"), places=8),
            "comparable_close": _number(_bar_decimal(current, "comparable_close"), places=8),
            "comparable_basis": (current or {}).get("comparable_basis"),
            "amount_cny": liquidity.get("amount_cny"),
            "turnover_fraction": liquidity.get("turnover_fraction"),
            "A": liquidity.get("amount_ratio_prior5"),
            "high_turnover_days_5_including_T": liquidity.get("high_turnover_days_5_including_T"),
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
            "behavioral_confirmation": behavioral,
            "supply_pressure": supply,
            "critical_counterevidence": critical,
            "temporal_leadership": behavioral.get("events", {}).get("TEMPORAL_LEADERSHIP"),
            "relative_strength": rs_market,
            "sector_candidates": sector_candidates,
            "funds_candidates": funds_rows,
            "market_status": market_status,
            "market_context": market_status,
            "theme_context_candidates": sector_candidates,
            "reference_score_policy": reference_counts["ranked_by"],
            "reference_score_status": "AVAILABLE" if ref is not None else "UNAVAILABLE",
            "S_PV_REFERENCE": ref.get("S_PV") if ref else None,
            "reference_score": ref.get("S_PV") if ref else None,
            "reference_rank": ref.get("rank") if ref else None,
            "_reference_score_exact": ref.get("_S_PV") if ref else None,
        }
        row["setup_rank_features"] = _setup_rank_features(row)
        target = selected if decision_status == "FOCUS" else diagnostics
        target.append(row)
    all_rows = selected + diagnostics
    _rank_rows(all_rows)
    selected = [row for row in all_rows if row["decision_status"] == "FOCUS"]
    diagnostics = [row for row in all_rows if row["decision_status"] != "FOCUS"]
    selected_ids = {row["instrument_id"] for row in selected}
    rows = [_public(row) for row in selected] + [_public(row) for row in diagnostics if row["instrument_id"] not in selected_ids]
    unknown = decision_counts["UNKNOWN"]
    failed = decision_counts["REJECTED"]
    watch = decision_counts["WATCH"]
    result_status = (
        "COMPLETE" if selected and ctx.universe_complete else
        "LIMITED_SCOPE" if selected else
        "INSUFFICIENT_EVIDENCE" if unknown or watch else
        "EMPTY"
    )
    return ctx, {
        "strategy_id": STAGE_AWARE_ID,
        "canonical_strategy_id": STAGE_AWARE_ID,
        "implementation_version": IMPLEMENTATION_VERSION,
        "spec_revision": SPEC_REVISION,
        "parameter_status": PARAMETER_STATUS,
        "parameter_status_label_zh": PARAMETER_STATUS_LABEL_ZH,
        "spec_conformance": "SPEC_CONFORMANT",
        "status": result_status,
        "scope_label": "FULL_SCOPE" if ctx.universe_complete else "LIMITED_SCOPE",
        "formal_list_policy": "V2_FOCUS_REQUIRES_TREND_SETUP_BEHAVIOR_MARKET_SAME_THEME_FUNDS_AND_NO_CRITICAL_COUNTEREVIDENCE",
        "observation_date": ctx.observation_date,
        "decision_cutoff": ctx.cutoff.isoformat(),
        "top_ids": [row["instrument_id"] for row in selected[:top_n]],
        "qualified_ids": [row["instrument_id"] for row in selected],
        "eligible_pool_ids": list(ctx.eligible_ids),
        "full_eligible_pool_retained": True,
        "items": rows,
        "diagnostics": [_public(row) for row in diagnostics],
        "counts": {
            "eligible": len(ctx.eligible_ids),
            "evaluated": len(ctx.eligible_ids),
            "focus": decision_counts["FOCUS"],
            "watch": watch,
            "rejected": failed,
            "passed": len(selected),
            "failed": failed,
            "unknown": unknown,
            "decision_counts": decision_counts,
            "condition_counts": dict(condition_counts),
            "stage_counts": stage_counts,
            "setup_counts": stage_counts,
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
            "setup_labels_are_research_classifications": True,
            "product_role": CONFIG["traceability"]["product_role"],
            "legacy_read_aliases": list(LEGACY_STAGE_AWARE_IDS),
            "old_strategies_preserved": True,
        },
        "rejected_theme_evidence": rejected_themes,
        "tie_break": "setup_rank_features within decision tier and setup, then instrument_id asc",
        "rank_semantics": "SETUP_AND_DECISION_TIER_RESEARCH_ORDER_NOT_GLOBAL_SCORE",
        "market_edge": CONFIG["market_edge"],
        **stage_aware_source_identity(),
    }


def run_stage_aware_momentum(bundle: MarketInput, *, top_n=100, cutoff=None, _context=None):
    _, result = _evaluate_rows(bundle, top_n=top_n, cutoff=cutoff, _context=_context)
    return result


def stage_aware_strategy_specs():
    return [{
        "strategy_id": STAGE_AWARE_ID,
        "canonical_replaces": CONFIG.get("canonical_replaces"),
        "legacy_read_aliases": list(LEGACY_STAGE_AWARE_IDS),
        "parameter_status": PARAMETER_STATUS,
        "parameter_status_label_zh": PARAMETER_STATUS_LABEL_ZH,
        "spec_revision": SPEC_REVISION,
        "formal_role": CONFIG["traceability"]["product_role"],
        "market_edge": CONFIG["market_edge"],
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
