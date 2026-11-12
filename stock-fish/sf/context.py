"""Descriptive facts added to the pre-market list after every group and order is fixed (never a selection input):

  position(w, t)          adjusted close vs the 250-session moving average (年线) and the 250-session high
  amount_share(aw, t)     a stock's amount as a share of the whole SSE/SZSE A-share amount that day (万分之)
  high_fate(aw, t)        yesterday's tallest boards (>= 4, else the tallest level) and what happened to them on t
All inputs are bars up to t. Fewer than 250 stored sessions -> position unknown ("?"), never zero.
"""
from __future__ import annotations

import pandas as pd

from . import radar

VERSION = "SF_CONTEXT_V1"


def position(w: dict, t: int) -> tuple[pd.Series, pd.Series]:
    adj = w["adj"].iloc[: t + 1]
    last = adj.iloc[-1]
    if len(adj) < 250:
        nan = pd.Series(float("nan"), index=last.index)
        return nan, nan
    tail = adj.iloc[-250:]
    ma, hi = tail.mean(skipna=False), tail.max(skipna=False)
    return last / ma - 1, last / hi - 1


def position_text(vs_ma, vs_hi) -> str:
    if pd.isna(vs_ma) or pd.isna(vs_hi):
        return "年线未知（不足250日）"
    side = "年线上" if vs_ma >= 0 else "年线下"
    hi = "一年新高" if vs_hi > -0.005 else f"距一年高{vs_hi * 100:.0f}%"
    return f"{side}{vs_ma * 100:+.0f}%·{hi}"


def amount_share(aw: dict, t: int) -> pd.Series:
    amt = aw["amount"].iloc[t]
    return amt / amt.sum(skipna=True)


def share_text(x) -> str:
    return "" if pd.isna(x) else f"·全市场万分之{x * 1e4:.1f}"


def high_fate(aw: dict, t: int, names: pd.Series) -> list[dict]:
    """The high-flyers of t-1 and their session t: next board / broke the board / big loss."""
    if t < 1:
        return []
    h = radar.heights_all(aw)
    prev = _tall(h.iloc[t - 1])  # same selection as radar.high_flyers, on t-1
    out = []
    for code in prev:
        c, pc = aw["close"].iloc[t].get(code), aw["preclose"].iloc[t].get(code)
        ret = c / pc - 1 if pd.notna(c) and pd.notna(pc) and pc > 0 else float("nan")
        sealed = bool(aw["limit_up"].iloc[t].get(code, False))
        height = int(h.iloc[t - 1][code])
        if pd.isna(ret):
            fate = "停牌或无数据"
        elif sealed:
            fate = f"晋级{height + 1}板"
        else:
            fate = f"断板，收{ret * 100:+.1f}%" + ("（大面）" if ret <= -0.05 else "")
        out.append(dict(code=code, name=names.get(code, code), height=height, fate=fate,
                        ret=None if pd.isna(ret) else round(float(ret), 4), sealed=sealed))
    return out


def _tall(h: pd.Series, min_height: int = 4, top: int = 3) -> list[str]:
    h = h.fillna(0)
    h = h[h > 0].sort_values(ascending=False)
    if h.empty:
        return []
    cut = min_height if int(h.iloc[0]) >= min_height else int(h.iloc[0])
    return h[h >= cut].index.tolist()[:top]
