"""Market sentiment (情绪周期) switch: indicators, rolling percentiles, cycle phase.

All indicators come from daily bars (main board, non-ST), so they exist for the whole history and can
be validated out of sample. Each indicator is turned into its percentile within the trailing
`window` sessions (only past and current data), so thresholds adapt to changing market size.

Composite level = mean of the oriented percentiles of the indicators in `SIGNS` (+1: higher is a
better mood, -1: higher is worse). Direction = level minus its mean over the previous `trend` sessions.

Phase (level x direction):
  REPAIR   level < 0.5, rising    修复
  ADVANCE  level >= 0.5, rising   主升
  DIVERGE  level >= 0.5, falling  高潮/分歧
  ICE      level < 0.5, falling   冰点/退潮
"""
from __future__ import annotations

import numpy as np
import pandas as pd

VERSION = "SF_MOOD_V1"
# Every indicator with its intuitive orientation (kept for display and future research).
ALL_SIGNS = {
    "lu_count": +1, "ld_count": -1, "broken_rate": -1, "max_height": +1,
    "prem_lu": +1, "prem_multi": +1, "promo_rate": +1, "big_loss": -1, "broken_prem": +1,
    "up_ratio": +1, "amount_ratio": +1, "top_prem": +1,
}
# Composite used by the switch: indicators selected and oriented on the TRAIN segment only
# (2022-06 -> 2025-02, |rank corr| >= 0.05 with the next-day relay basket). Note promo_rate is
# negative: a high promotion rate today predicts a worse relay tomorrow (crowding).
# Out of sample (2025-02 -> 2026-11) the top tercile beat the bottom tercile in both test segments
# (relay basket -0.22%/-0.16% vs -0.85%/-1.01% per day), but stayed negative: a risk filter, not alpha.
SIGNS = {"ld_count": -1, "promo_rate": -1, "big_loss": -1, "broken_prem": +1, "up_ratio": +1}
TIERS = ((1 / 3, "低"), (2 / 3, "中"), (1.01, "高"))
ADVICE = {"低": "空仓观望", "中": "轻仓，只做最强", "高": "正常参与"}
PHASES = ("REPAIR", "ADVANCE", "DIVERGE", "ICE")


def indicators(w: dict) -> pd.DataFrame:
    """Day x indicator frame from a strategy.panel() dict."""
    tr = w["traded"].fillna(False).astype(bool) & ~w["st"].fillna(False).astype(bool)
    lu = w["limit_up"].fillna(False).astype(bool) & tr
    touched = w["touched_up"].fillna(False).astype(bool) & tr
    broken = touched & ~lu
    ld = w["limit_down"].fillna(False).astype(bool) & tr
    pct = (w["close"] / w["preclose"] - 1).where(tr)
    height = w["height"].where(tr, 0)
    lu_y, broken_y, height_y = lu.shift(1, fill_value=False), broken.shift(1, fill_value=False), height.shift(1)
    multi_y = lu_y & (height_y >= 2)
    top_y = height_y.eq(height_y.max(axis=1), axis=0) & (height_y >= 2)

    def mean_where(mask):
        return pct.where(mask).mean(axis=1)

    out = pd.DataFrame(index=w["close"].index)
    out["lu_count"] = lu.sum(axis=1)
    out["ld_count"] = ld.sum(axis=1)
    out["broken_rate"] = broken.sum(axis=1) / (lu.sum(axis=1) + broken.sum(axis=1)).replace(0, np.nan)
    out["max_height"] = height.max(axis=1)
    out["prem_lu"] = mean_where(lu_y)
    out["prem_multi"] = mean_where(multi_y)
    out["promo_rate"] = (lu_y & lu).sum(axis=1) / lu_y.sum(axis=1).replace(0, np.nan)
    out["big_loss"] = (lu_y & (pct <= -0.05)).sum(axis=1)
    out["broken_prem"] = mean_where(broken_y)
    out["up_ratio"] = (pct > 0).sum(axis=1) / tr.sum(axis=1).replace(0, np.nan)
    amount = w["amount"].where(tr).sum(axis=1)
    out["amount_ratio"] = amount / amount.rolling(20, min_periods=10).mean().shift(1)
    out["top_prem"] = mean_where(top_y)
    return out


def percentiles(ind: pd.DataFrame, window: int = 250, min_periods: int = 60) -> pd.DataFrame:
    return ind.rolling(window, min_periods=min_periods).rank(pct=True)


def composite(pct: pd.DataFrame, signs: dict | None = None, trend: int = 5) -> pd.DataFrame:
    signs = signs or SIGNS
    oriented = pd.DataFrame({k: pct[k] if s > 0 else 1 - pct[k] for k, s in signs.items() if k in pct})  # noqa: E501
    level = oriented.mean(axis=1, skipna=True).where(oriented.notna().sum(axis=1) >= max(1, len(oriented.columns) // 2))
    direction = level - level.shift(1).rolling(trend, min_periods=trend).mean()
    phase = pd.Series(np.select(
        [(level < 0.5) & (direction > 0), (level >= 0.5) & (direction > 0),
         (level >= 0.5) & (direction <= 0), (level < 0.5) & (direction <= 0)], PHASES, ""), index=level.index)
    phase = phase.where(level.notna() & direction.notna(), "UNKNOWN")
    tier = pd.Series(np.select([level < TIERS[0][0], level < TIERS[1][0]], ["低", "中"], "高"), index=level.index)
    tier = tier.where(level.notna(), "UNKNOWN")
    return pd.DataFrame({"level": level, "direction": direction, "phase": phase, "tier": tier,
                         "advice": tier.map(ADVICE).fillna("数据不足")})


def table(w: dict, signs: dict | None = None, window: int = 250, trend: int = 5) -> pd.DataFrame:
    ind = indicators(w)
    pct = percentiles(ind, window)
    return ind.join(pct.add_suffix("_pct")).join(composite(pct, signs, trend))


def relay_basket(w: dict) -> pd.Series:
    """Equal-weight return of buying today's (main board, non-ST) limit-ups at the next open and
    selling at the open after that; the day's value is indexed by the observation session."""
    tr = w["traded"].fillna(False).astype(bool) & ~w["st"].fillna(False).astype(bool)
    lu = w["limit_up"].fillna(False).astype(bool) & tr
    adj_open = w["open"] * w["adj"] / w["close"]
    buyable = (w["traded"].fillna(False).astype(bool) & ~w["open_at_limit"].fillna(False).astype(bool)).shift(-1)
    entry = adj_open.shift(-1).where(buyable.fillna(False).astype(bool))
    r = adj_open.shift(-2) / entry - 1
    return r.where(lu).mean(axis=1)
