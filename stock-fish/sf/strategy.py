"""Vectorized SHORT_HORIZON_STRENGTH_R1/R2 rules, market sentiment and gate diagnostics.

Design:
- `panel(bars)` builds wide (session x code) frames once for any history length.
- `features(w, t, ...)` computes per-stock facts for session index t using only rows <= t.
- `evaluate(feat, params)` applies stage rules and records every gate result, so outcomes can be
  attributed per gate and near-misses (exactly one failed gate) can be reviewed.

Comparable (adjusted) prices are reconstructed by chaining the exchange `preclose`:
adj_t / adj_{t-1} = close_t / preclose_t, which is exact across ex-rights events without
adjustment-factor downloads. Same-day facts (CLV, limit events) use RAW OHLC and RAW preclose.

Default thresholds are the original R1/R2 hypotheses (unvalidated); the stage tags are descriptive only.
"""
from __future__ import annotations

from copy import deepcopy

import numpy as np
import pandas as pd

STRATEGY_VERSION = "SF_R2_LEAN_V2"  # V2 (龙脉 V9): S2 limit-up start, C1 turnover-surge exclusion
STAGES = ("STRONG_START", "STRONG_CONTINUATION", "FIRST_PULLBACK")

DEFAULT_PARAMS = dict(
    STRONG_START=dict(ret1_min=0.06, rs1_min=0.02, amount_min=1e8, a_min=1.5, clv_min=0.7, prev_ret5_max=0.12,
                      limit_up_start=True),
    STRONG_CONTINUATION=dict(amount_min=5e7, ret5_min=0.15, ret10_min=0.25, ret1_min=0.05, ret3_min=0.0,
                             clv_min=0.7, turn_surge_max=2.0),
    FIRST_PULLBACK=dict(amount_min=2e8, ret1_min=-0.08, ret1_max=0.0, ret5_min=0.20, ret3_min=0.05),
    R2=dict(turnover_min_pct=5.0, turnover_window=5),
    universe=dict(min_listing_sessions=60, benchmark_min_coverage=0.6),
)
# 龙脉 V7 stage rules (SF_R2_LEAN_V1), kept for replays that must reproduce V7
V7_OVERRIDES = {"STRONG_START": {"limit_up_start": False}, "STRONG_CONTINUATION": {"turn_surge_max": None}}
MIN_HISTORY = 25  # sessions needed before t (MA20 five sessions ago)


def params(overrides: dict | None = None) -> dict:
    merged = deepcopy(DEFAULT_PARAMS)
    for section, values in (overrides or {}).items():
        merged.setdefault(section, {}).update(values)
    return merged


# ------------------------------------------------------------------ panel

def is_main_board(code: pd.Series) -> pd.Series:
    num = code.str[3:]
    sh = code.str.startswith("sh.") & num.str.startswith(("600", "601", "603", "605"))
    sz = code.str.startswith("sz.") & num.str.startswith(("000", "001", "002", "003"))
    return sh | sz


def _round_price(value):
    return np.floor(value * 100 + 0.5) / 100


# Price-limit rules. V2: SSE/SZSE main-board risk-warning (ST/*ST) stocks moved from 5% to 10% on
# 2026-07-06; ChiNext/STAR stay at 20% whether ST or not.
LIMITS_VERSION = "SF_LIMITS_V2"
ST_TEN_PCT_FROM = "2026-07-06"


def limit_ratio(index, columns, st) -> np.ndarray:
    """Session x code price-limit ratio for the given (string-dated) index, codes and ST flags."""
    wide_board = np.asarray(pd.Index(columns).str[3:].str.startswith(("30", "68")), dtype=bool)[None, :]
    st_ratio = np.where(np.asarray(pd.Index(index).astype(str)) >= ST_TEN_PCT_FROM, 0.10, 0.05)[:, None]
    return np.where(wide_board, 0.20, np.where(np.asarray(st, dtype=bool), st_ratio, 0.10))


def panel(bars: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Wide (session x code) frames for main-board stocks."""
    from .store import wide as _wide
    bars = bars[is_main_board(bars["code"])]
    wide = _wide(bars, ("open", "high", "low", "close", "preclose", "amount", "turn", "volume"),
                 {"st": "isST", "_status": "tradestatus"})
    present = wide["close"].notna()
    traded = wide.pop("_status") & (wide["volume"] > 0) & (wide["close"] > 0) & (wide["preclose"] > 0)
    # Suspended-but-present rows carry no price change. Absent rows (not listed yet / missing) are
    # masked to NaN so any window touching them is unknown, without poisoning later sessions.
    step = (wide["close"] / wide["preclose"]).where(traded, 1.0)
    wide["adj"] = step.where(present, 1.0).cumprod().where(present)
    wide["traded"] = traded
    ratio = limit_ratio(wide["close"].index, wide["close"].columns, wide["st"])
    wide["up_limit"] = _round_price(wide["preclose"] * (1 + ratio))
    wide["down_limit"] = _round_price(wide["preclose"] * (1 - ratio))
    eps = 1e-9
    wide["limit_up"] = traded & (wide["close"] >= wide["up_limit"] - eps)
    wide["touched_up"] = traded & (wide["high"] >= wide["up_limit"] - eps)
    wide["limit_down"] = traded & (wide["close"] <= wide["down_limit"] + eps)
    wide["one_price_up"] = wide["limit_up"] & (wide["low"] >= wide["up_limit"] - eps)
    wide["open_at_limit"] = traded & (wide["open"] >= wide["up_limit"] - eps)
    wide["height"] = _heights(wide["limit_up"])
    return wide


def _heights(limit_up: pd.DataFrame) -> pd.DataFrame:
    """Consecutive limit-up closes ending at each session (board height)."""
    hit = limit_up.fillna(False).astype(int).to_numpy()
    out = np.zeros_like(hit)
    for i in range(hit.shape[0]):
        out[i] = (out[i - 1] + 1) * hit[i] if i else hit[i]
    return pd.DataFrame(out, index=limit_up.index, columns=limit_up.columns)


# ------------------------------------------------------------------ universe

def eligible(w: dict, basic: pd.DataFrame, calendar: list[str], t: int, p: dict) -> pd.Series:
    day = w["close"].index[t]
    codes = w["close"].columns
    listed = basic.drop_duplicates("code").set_index("code").reindex(codes)
    cal = pd.Index(calendar)
    pos = cal.searchsorted(day, side="right")
    ipo_pos = cal.searchsorted(listed["ipoDate"].fillna("9999-12-31").astype(str).to_numpy(), side="left")
    age = pos - ipo_pos
    old_enough = pd.Series(age >= p["universe"]["min_listing_sessions"], index=codes)
    common = listed["type"].astype(str).eq("1")
    out_date = listed["outDate"].fillna("").astype(str)
    alive = (out_date == "") | (out_date > day)
    st = w["st"].iloc[t].fillna(True).astype(bool)
    trading = w["traded"].iloc[t].fillna(False).astype(bool)
    return (common & alive & old_enough & ~st & trading).fillna(False)


# ------------------------------------------------------------------ features

def features(w: dict, t: int, pool: pd.Series, p: dict | None = None) -> pd.DataFrame:
    p = p or DEFAULT_PARAMS
    if t < MIN_HISTORY:
        raise ValueError(f"need at least {MIN_HISTORY} prior sessions, have {t}")
    adj = w["adj"]
    present = w["close"].notna()

    def ret(window, end=t):
        complete = present.iloc[end - window:end + 1].all()
        return (adj.iloc[end] / adj.iloc[end - window] - 1).where(complete)

    def ma(window, end=t):
        return adj.iloc[end - window + 1:end + 1].mean(skipna=False)

    rets = {k: ret(k) for k in (1, 3, 5, 10, 20)}
    rs = {}
    coverage = {}
    for k in (1, 5):
        valid = rets[k][pool].dropna()
        coverage[k] = len(valid) / max(int(pool.sum()), 1)
        bench = valid.mean() if coverage[k] >= p["universe"]["benchmark_min_coverage"] else np.nan
        rs[k] = rets[k] - bench
    raw = {c: w[c].iloc[t] for c in ("open", "high", "low", "close", "preclose", "amount", "turn")}
    rng = raw["high"] - raw["low"]
    clv = ((raw["close"] - raw["low"]) / rng).where(rng > 0)
    flat_up = (rng == 0) & (raw["close"] > raw["preclose"]) & w["traded"].iloc[t]
    prior_amount = w["amount"].iloc[t - 5:t]
    win = p["R2"]["turnover_window"]
    daily_ret = adj.iloc[t - 4:t + 1].pct_change(fill_method=None)
    feat = pd.DataFrame({
        "eligible": pool,
        "close": raw["close"], "preclose": raw["preclose"], "amount": raw["amount"], "turn": raw["turn"],
        "ret1": rets[1], "ret3": rets[3], "ret5": rets[5], "ret10": rets[10], "ret20": rets[20],
        "rs1": rs[1], "rs5": rs[5],
        "A": raw["amount"] * 5 / prior_amount.sum(skipna=False),
        "clv": clv, "flat_up": flat_up.fillna(False).astype(bool),
        "close_adj": adj.iloc[t], "prior5_high": adj.iloc[t - 5:t].max(skipna=False),
        "prev_ret5": ret(5, t - 1),
        "ma5": ma(5), "ma10": ma(10), "ma20": ma(20), "ma20_prev": ma(20, t - 5),
        "three_up": (daily_ret.iloc[1:4] > 0).all(),
        "max_turn": w["turn"].iloc[t - win + 1:t + 1].max(),
        "turn_prev5": w["turn"].where(w["traded"].fillna(False).astype(bool)).iloc[t - 5:t].mean(skipna=False),
        "limit_up": w["limit_up"].iloc[t], "touched_up": w["touched_up"].iloc[t],
        "one_price": w["one_price_up"].iloc[t], "limit_down": w["limit_down"].iloc[t],
        "board_height": w["height"].iloc[t],
        "float_cap": raw["amount"] / (raw["turn"] / 100),
    })
    feat.attrs.update(date=w["close"].index[t], rs_coverage=coverage)
    return feat


# ------------------------------------------------------------------ rules

def _gates(f: pd.DataFrame, p: dict) -> dict[str, dict[str, pd.Series]]:
    clv_ok = lambda m: (f["clv"] >= m) | f["flat_up"]  # noqa: E731
    s, c, b = p["STRONG_START"], p["STRONG_CONTINUATION"], p["FIRST_PULLBACK"]
    trend20 = (f["close_adj"] > f["ma20"]) & (f["ma20"] >= f["ma20_prev"])
    return {
        "STRONG_START": {
            "ret1": f["ret1"] >= s["ret1_min"], "rs1": f["rs1"] >= s["rs1_min"],
            "amount": f["amount"] >= s["amount_min"], "A": f["A"] >= s["a_min"],
            "breakout": f["close_adj"] > f["prior5_high"],
            "ma5_10": (f["close_adj"] > f["ma5"]) & (f["close_adj"] > f["ma10"]),
            "prev_ret5": f["prev_ret5"] <= s["prev_ret5_max"], "clv": clv_ok(s["clv_min"]),
        } | ({"limit_up_start": f["limit_up"].fillna(False).astype(bool) & ~f["one_price"].fillna(False).astype(bool)}
             if s.get("limit_up_start") else {}),
        "STRONG_CONTINUATION": {
            "amount": f["amount"] >= c["amount_min"],
            "momentum": (f["ret5"] >= c["ret5_min"]) | (f["ret10"] >= c["ret10_min"]),
            "ret1": f["ret1"] >= c["ret1_min"], "ret3": f["ret3"] >= c["ret3_min"], "rs5": f["rs5"] > 0,
            "clv": clv_ok(c["clv_min"]), "trend20": trend20,
        } | ({"no_turn_surge": ~(f["turn"] >= c["turn_surge_max"] * f["turn_prev5"])}  # unknown turnover: kept
             if c.get("turn_surge_max") else {}),
        "FIRST_PULLBACK": {
            "amount": f["amount"] >= b["amount_min"],
            "ret1": (f["ret1"] >= b["ret1_min"]) & (f["ret1"] < b["ret1_max"]),
            "ret5": f["ret5"] >= b["ret5_min"], "ret3": f["ret3"] >= b["ret3_min"],
            "three_up": f["three_up"].astype(bool), "trend20": trend20,
        },
    }


def evaluate(f: pd.DataFrame, p: dict | None = None) -> pd.DataFrame:
    """Add stage matches, R2 selection and gate diagnostics to a feature frame."""
    p = p or DEFAULT_PARAMS
    out = f.copy()
    gates = _gates(f, p)
    near = pd.Series("", index=f.index)
    for stage, checks in gates.items():
        table = pd.DataFrame({k: v.fillna(False).astype(bool) for k, v in checks.items()})
        out[stage] = table.all(axis=1)
        failed = (~table).sum(axis=1)
        out[f"{stage}_failed"] = failed
        missing_gate = (~table).idxmax(axis=1).where(failed == 1, "")
        near = near.where(near != "", (stage + ":" + missing_gate).where(failed == 1, ""))
    out["near_miss"] = near
    out["limit_down"] = out["limit_down"].fillna(False).astype(bool)
    out["r1_match"] = out[list(STAGES)].any(axis=1) & ~out["limit_down"]
    out["participation"] = (f["max_turn"] >= p["R2"]["turnover_min_pct"]).fillna(False)
    out["r2_selected"] = out["r1_match"] & out["participation"] & out["eligible"]
    out["stages"] = out[list(STAGES)].apply(lambda r: "+".join(s for s in STAGES if r[s]), axis=1)
    return out


def compute(bars: pd.DataFrame, basic: pd.DataFrame, day: str, calendar: list[str] | None = None,
            p: dict | None = None) -> tuple[pd.DataFrame, dict]:
    """Convenience: facts + sentiment for one observation day."""
    p = p or DEFAULT_PARAMS
    w = panel(bars)
    t = list(w["close"].index).index(day)
    calendar = calendar or list(w["close"].index)
    pool = eligible(w, basic, calendar, t, p)
    facts = evaluate(features(w, t, pool, p), p)
    return facts, sentiment(w, t, pool)


# ------------------------------------------------------------------ sentiment

def sentiment(w: dict, t: int, pool: pd.Series | None = None) -> dict:
    """Market emotion facts from daily bars (main board, non-ST)."""
    def day_stats(k):
        tr = w["traded"].iloc[k].fillna(False).astype(bool) & ~w["st"].iloc[k].fillna(False).astype(bool)
        lu = w["limit_up"].iloc[k].fillna(False).astype(bool) & tr
        touched = w["touched_up"].iloc[k].fillna(False).astype(bool) & tr
        return tr, lu, touched

    tr, lu, touched = day_stats(t)
    _, lu_prev, _ = day_stats(t - 1)
    up, broken = int(lu.sum()), int((touched & ~lu).sum())
    down = int((w["limit_down"].iloc[t].fillna(False).astype(bool) & tr).sum())
    heights = w["height"].iloc[t].where(tr, 0)
    pct = w["close"].iloc[t] / w["preclose"].iloc[t] - 1
    base = pool if pool is not None else tr
    y_ret = pct[lu_prev & tr]
    promoted = int((lu_prev & lu).sum())
    history = []
    for k in range(max(1, t - 4), t + 1):
        tk, lk, _ = day_stats(k)
        history.append(dict(date=w["close"].index[k], limit_up=int(lk.sum()),
                            limit_down=int((w["limit_down"].iloc[k].fillna(False).astype(bool) & tk).sum()),
                            max_height=int(w["height"].iloc[k].where(tk, 0).max())))
    ladder = heights[heights >= 2].value_counts().sort_index(ascending=False)
    return dict(
        date=w["close"].index[t], limit_up=up, limit_down=down, broken=broken,
        broken_rate=round(broken / (up + broken), 3) if up + broken else None,
        max_height=int(heights.max()), ladder={int(k): int(v) for k, v in ladder.items()},
        yesterday_limit_up=int(lu_prev.sum()), promoted=promoted,
        promotion_rate=round(promoted / int(lu_prev.sum()), 3) if lu_prev.sum() else None,
        yesterday_limit_up_avg_ret=round(float(y_ret.mean()), 4) if len(y_ret) else None,
        up_ratio=round(float((pct[base] > 0).mean()), 3), median_pct=round(float(pct[base].median()), 4),
        history=history,
    )


def regime(s: dict) -> tuple[str, str]:
    """Coarse sentiment state. Heuristic labels; `sf backtest --by regime` tests them."""
    score = 0
    score += 1 if s["limit_up"] >= 50 else -1 if s["limit_up"] < 25 else 0
    br = s["broken_rate"]
    score += 0 if br is None else 1 if br <= 0.25 else -1 if br >= 0.40 else 0
    yr = s["yesterday_limit_up_avg_ret"]
    score += 0 if yr is None else 1 if yr >= 0.02 else -1 if yr < 0 else 0
    score += 1 if s["max_height"] >= 5 else -1 if s["max_height"] <= 2 else 0
    score -= 1 if s["limit_down"] >= 15 else 0
    if score >= 3:
        return "HOT", "进攻：情绪强，可做连板/龙头"
    if score >= 1:
        return "WARM", "偏强：可做首板/低位跟随，控制仓位"
    if score >= -1:
        return "NEUTRAL", "震荡：只做最强辨识度，轻仓"
    return "COLD", "退潮：建议空仓或极轻仓"
