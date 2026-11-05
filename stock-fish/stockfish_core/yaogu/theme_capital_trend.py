"""Unchanged price, liquidity and fund primitives used by active momentum V2."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path
import yaml

from .strategy_experiments import _as_decimal, _bar, _complete_pv_row, _rank_pv
from .visibility import clock_visible

# Original configuration bytes retain the unchanged moving-average defaults.
ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / 'config/theme_capital_trend_v1.yaml'
CONFIG = yaml.safe_load(CONFIG_PATH.read_text(encoding='utf-8'))
MA_WINDOWS = tuple(CONFIG['research_seed_parameters']['moving_averages']['windows'])


def _frac(value):
    value = _as_decimal(value)
    return Fraction(value) if value is not None else None


def _money(value):
    value = _as_decimal(value)
    if value is None:
        return None
    return float(value.quantize(Decimal("0.01")))


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
    from .derived_feature_store import MISSING, ma_value_from_context

    derived = ma_value_from_context(ctx, iid, end_index, window)
    if derived is not MISSING:
        return derived
    cache=getattr(ctx,'scalar_cache',None)
    key=('MA_VALUE',iid,end_index,window)
    if cache is not None and key in cache:return cache[key]
    value=_ma_value_uncached(ctx,iid,end_index,window)
    if cache is not None:cache[key]=value
    return value


def _ma_value_uncached(ctx, iid, end_index, window):
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


def _window_return(ctx, iid, end_index, lookback):
    from .derived_feature_store import MISSING, window_return_from_context

    derived = window_return_from_context(ctx, iid, end_index, lookback)
    if derived is not MISSING:
        return derived
    cache=getattr(ctx,'scalar_cache',None)
    key=('WINDOW_RETURN',iid,end_index,lookback)
    if cache is not None and key in cache:return cache[key]
    value=_window_return_uncached(ctx,iid,end_index,lookback)
    if cache is not None:cache[key]=value
    return value


def _window_return_uncached(ctx, iid, end_index, lookback):
    if end_index is None or end_index - lookback < 0:
        return None
    current = _bar(ctx, ctx.sessions[end_index], iid)
    previous = _bar(ctx, ctx.sessions[end_index - lookback], iid)
    if not current or not previous or current.get("comparable_basis") != previous.get("comparable_basis"):
        return None
    c0, c1 = _close_fraction(previous), _close_fraction(current)
    return c1 / c0 - 1 if c0 and c1 and c0 > 0 else None


def _amount_change(ctx, iid, end_index, lookback):
    from .derived_feature_store import MISSING, amount_change_from_context

    derived = amount_change_from_context(ctx, iid, end_index, lookback)
    if derived is not MISSING:
        return derived
    cache=getattr(ctx,'scalar_cache',None)
    key=('AMOUNT_CHANGE',iid,end_index,lookback)
    if cache is not None and key in cache:return cache[key]
    value=_amount_change_uncached(ctx,iid,end_index,lookback)
    if cache is not None:cache[key]=value
    return value


def _amount_change_uncached(ctx, iid, end_index, lookback):
    if end_index is None or end_index - lookback < 0:
        return None
    current = _amount_fraction(_bar(ctx, ctx.sessions[end_index], iid))
    previous = _amount_fraction(_bar(ctx, ctx.sessions[end_index - lookback], iid))
    return current / previous - 1 if current is not None and previous and previous > 0 else None


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
