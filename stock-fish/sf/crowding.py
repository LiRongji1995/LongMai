"""Crowding (拥挤度) indicators: how much short-term money is in play and how concentrated it is.

Used by the list and `sf crowding` (the study code that computed the rest is at tag `legacy-r2`).
Market level (per session, all SSE/SZSE A shares):
  spec_share        amount of stocks with a limit-up in the last 5 sessions / whole-market amount
  hot_turn_share    amount of stocks with turnover rate >= 15% / whole-market amount
  spec_share_chg5   spec_share minus its value 5 sessions earlier (still growing or saturating)
  hot_turn_chg5     same for hot_turn_share
  amount_ratio20    whole-market amount / its previous 20-session mean (new money)
  top3_share        amount of the union of the top-3 mainline boards' members / whole-market amount
Theme level (per session x board):
  share, share_pct60 (percentile within the board's last 60 sessions), share_chg5
Stock level:
  leader_theme_share  the leader's amount / its board's amount
All values use data up to and including the session (known after its close).

Study result (docs/CROWDING_STUDY.md): no market-level indicator separates good from bad relay or
leader days consistently. One theme-level fact holds in train and both tests, controlled for the
day's limit-up count: when a mainline's turnover share is at its 60-session high (percentile >=
HOT_PCT), the chance that its limit-ups collapse within 2 sessions rises (train 33% vs 19%, test1
23% vs 16%, test2 34% vs 21%). Leader returns did not differ consistently, so the list shows it as a
warning, not a rule.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import mainline

HOT_PCT = 0.983  # train top-quintile edge of share_pct60


def spec_share(aw: dict) -> pd.Series:
    amount = aw["amount"]
    lu5 = aw["limit_up"].fillna(False).astype(bool).rolling(5, min_periods=1).max().astype(bool)
    return amount.where(lu5).sum(axis=1) / amount.sum(axis=1)


def theme_heat(ts: dict, t: int, board: str, window: int = 60) -> tuple[float, float]:
    """(share, percentile of today's share within the board's last `window` sessions)."""
    hist = ts["share"][board].iloc[max(0, t - window + 1):t + 1].dropna()
    if len(hist) < 20:
        return float(ts["share"][board].iloc[t]), float("nan")
    return float(hist.iloc[-1]), float(hist.rank(pct=True).iloc[-1])


def environment(aw: dict, ts: dict, days: int = 20, p: dict | None = None) -> pd.DataFrame:
    """Short-term money temperature for the last `days` sessions, for review and context only
    (the study found no trading threshold). One row per session."""
    if p is None:
        from .longmai import params
        p = params()
    spec = spec_share(aw)
    spec_pct = spec.rolling(250, min_periods=60).rank(pct=True)
    amount = aw["amount"]
    total = amount.sum(axis=1)
    hot = amount.where(aw["turn"] >= 15).sum(axis=1) / total
    ratio20 = total / total.rolling(20, min_periods=10).mean().shift(1)
    rows = []
    n = len(amount.index)
    for t in range(max(0, n - days), n):
        lines = mainline.mainlines(ts, t, p)
        themes = []
        for b in lines:
            share, pct = theme_heat(ts, t, b)
            flag = "!" if pd.notna(pct) and pct >= HOT_PCT else ""
            themes.append(f"{b} {share:.1%}{flag}")
        rows.append(dict(date=amount.index[t], market_amount=total.iloc[t], amount_ratio20=ratio20.iloc[t],
                         spec_share=spec.iloc[t], spec_pct250=spec_pct.iloc[t], hot_turn_share=hot.iloc[t],
                         mainlines="；".join(themes)))
    return pd.DataFrame(rows).set_index("date")
