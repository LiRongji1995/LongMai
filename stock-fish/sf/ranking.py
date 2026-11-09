"""Tiering and scoring of R2 matches (new heuristic layer, unvalidated).

Every component is stored on the row so `sf review` / `sf backtest` can test each one.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

RANKING_VERSION = "SF_TIER_SCORE_V1"
TIERS = {"A": "连板/龙头", "B": "首板强启动", "C": "趋势延续", "D": "首次回调"}
WEIGHTS = dict(rs1=0.20, A=0.10, clv=0.10, heat=0.20, seal=0.20, cap=0.10, lhb=0.10)


def tier(f: pd.DataFrame) -> pd.Series:
    return pd.Series(np.select(
        [f["board_height"] >= 2, f["STRONG_START"], f["STRONG_CONTINUATION"]], ["A", "B", "C"], default="D"),
        index=f.index)


def _pct_rank(series: pd.Series, ascending=True) -> pd.Series:
    return series.rank(pct=True, ascending=ascending).fillna(0.5)


def seal_quality(f: pd.DataFrame) -> pd.Series:
    """Seal quality for limit-up closes from pool facts; 0.5 when pool data is unknown."""
    if "first_seal" not in f:
        return pd.Series(0.5, index=f.index)
    first = f["first_seal"]
    early = first.map(lambda t: 0.5 if not isinstance(t, str) else 1.0 if t <= "09:35" else 0.7 if t <= "10:30" else 0.4)
    clean = (1 - f["breaks"].clip(0, 3) / 3).fillna(0.5)
    strength = _pct_rank(f["seal_amount"] / f["float_cap"])
    quality = 0.4 * early + 0.3 * clean + 0.3 * strength
    return quality.where(f["limit_up"].fillna(False).astype(bool), 0.3)


def components(f: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "c_rs1": _pct_rank(f["rs1"]),
        "c_A": _pct_rank(f["A"]),
        "c_clv": f["clv"].fillna(1.0).clip(0, 1),
        "c_heat": _pct_rank(f["theme_heat"]),
        "c_seal": seal_quality(f),
        "c_cap": _pct_rank(f["float_cap"], ascending=False),
        "c_lhb": (f["lhb_times"].fillna(0) > 0).astype(float),
    }, index=f.index)


def score(f: pd.DataFrame, weights: dict | None = None) -> tuple[pd.Series, pd.DataFrame]:
    weights = weights or WEIGHTS
    parts = components(f)
    total = sum(parts[f"c_{k}"] * w for k, w in weights.items()) / sum(weights.values())
    return (total * 100).round(1), parts


def plan(r) -> str:
    stop = round(r["close"] * 0.95, 2)
    if r["tier"] == "A":
        return f"竞价高开2%-7%且竞价额≥昨日成交额5%才接力；低开/平开放弃；失守{stop}止损"
    if r["tier"] == "B":
        if bool(r.get("limit_up")):
            return f"首板次日：高开≥2%且板块有跟风再考虑；低开放弃；失守{stop}止损"
        return f"强启动未封板：回踩不破昨收{r['close']}再看；跌破{stop}放弃"
    if r["tier"] == "C":
        return f"趋势票：开盘不追高>5%；回踩均价线企稳可低吸；跌破{stop}止损"
    return f"回调票：等企稳翻红再介入；跌破{stop}放弃"
