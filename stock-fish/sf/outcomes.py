"""Outcome labels for a pre-market list, measured the way the list is actually traded.

Entry is the TARGET session open (the list is published before that open). Labels:
- gap            open_T / close_O - 1                      (O = observation session)
- unbuyable      T opened at the limit-up price (likely no fill at the open)
- r_oc           close_T / open_T - 1                      (same-day, not realizable under T+1)
- r_n1o          open_{T+1} / open_T - 1                   (buy T open, sell next open)  <- primary
- r_n1c          close_{T+1} / open_T - 1
- r_n2c          close_{T+2} / open_T - 1
- mfe2 / mae2    max high / min low over T..T+1 vs open_T
- T flags        limit_up_T, broken_T (touched, closed below), one_price_T, limit_down_T
Multi-day returns use the preclose-chained adjusted series, so ex-rights events don't distort them.
Missing future sessions -> NaN (pending), never zero.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_COST = 0.0015  # round trip: commission both sides + 0.05% stamp duty on sell + slippage
PRIMARY = "r_n1o"


def label(w: dict, target: str, codes) -> pd.DataFrame:
    index = list(w["close"].index)
    codes = pd.Index(codes).intersection(w["close"].columns)
    if target not in index:
        return pd.DataFrame(index=codes)
    t = index.index(target)

    def row(field, k):
        return w[field].iloc[t + k].reindex(codes) if t + k < len(index) else pd.Series(np.nan, index=codes)

    open_t, close_t = row("open", 0), row("close", 0)
    adj_t = row("adj", 0)
    adj_open_t = open_t * adj_t / close_t
    traded_t = row("traded", 0).fillna(False).astype(bool)

    def adj_price(field, k):
        """Price on session t+k expressed in the adjusted basis of session t."""
        if t + k >= len(index):
            return pd.Series(np.nan, index=codes)
        return row(field, k) * row("adj", k) / row("close", k)

    out = pd.DataFrame(index=codes)
    out["gap"] = open_t / row("close", -1) - 1 if t > 0 else np.nan
    out["unbuyable"] = row("open_at_limit", 0).fillna(False).astype(bool) & traded_t
    out["suspended_T"] = ~traded_t
    out["r_oc"] = close_t / open_t - 1
    out["r_n1o"] = adj_price("open", 1) / adj_open_t - 1
    out["r_n1c"] = adj_price("close", 1) / adj_open_t - 1
    out["r_n2c"] = adj_price("close", 2) / adj_open_t - 1
    highs = pd.concat([adj_price("high", 0), adj_price("high", 1)], axis=1)
    lows = pd.concat([adj_price("low", 0), adj_price("low", 1)], axis=1)
    out["mfe2"] = highs.max(axis=1, skipna=False) / adj_open_t - 1
    out["mae2"] = lows.min(axis=1, skipna=False) / adj_open_t - 1
    for name, field in (("limit_up_T", "limit_up"), ("one_price_T", "one_price_up"), ("limit_down_T", "limit_down")):
        out[name] = row(field, 0).fillna(False).astype(bool) & traded_t
    out["broken_T"] = row("touched_up", 0).fillna(False).astype(bool) & ~out["limit_up_T"] & traded_t
    # Suspended at T: no entry possible.
    for col in ("r_oc", "r_n1o", "r_n1c", "r_n2c", "mfe2", "mae2"):
        out.loc[out["suspended_T"], col] = np.nan
    out["mature"] = out["r_n2c"].notna() | out["suspended_T"]
    return out


def stats(frame: pd.DataFrame, ret: str = PRIMARY, cost: float = DEFAULT_COST) -> dict:
    """Trading statistics on buyable, mature rows."""
    usable = frame[~frame["unbuyable"] & frame[ret].notna()]
    net = usable[ret] - cost
    wins, losses = net[net > 0], net[net <= 0]
    payoff = wins.mean() / -losses.mean() if len(wins) and len(losses) and losses.mean() < 0 else np.nan
    return dict(
        n=int(len(frame)), buyable=int(len(usable)),
        unbuyable_rate=round(float(frame["unbuyable"].mean()), 3) if len(frame) else np.nan,
        hit_rate=round(float((net > 0).mean()), 3) if len(net) else np.nan,
        avg=round(float(net.mean()), 4) if len(net) else np.nan,
        median=round(float(net.median()), 4) if len(net) else np.nan,
        payoff=round(float(payoff), 2) if pd.notna(payoff) else np.nan,
        limit_up_T=round(float(usable["limit_up_T"].mean()), 3) if len(usable) else np.nan,
        broken_T=round(float(usable["broken_T"].mean()), 3) if len(usable) else np.nan,
        mae2=round(float(usable["mae2"].mean()), 4) if len(usable) else np.nan,
    )


def grouped(frame: pd.DataFrame, by: str, ret: str = PRIMARY, cost: float = DEFAULT_COST) -> pd.DataFrame:
    rows = []
    for key, part in frame.groupby(by, dropna=False, observed=True):
        rows.append({by: key, **stats(part, ret, cost)})
    return pd.DataFrame(rows).set_index(by) if rows else pd.DataFrame()


def equity(frame: pd.DataFrame, ret: str = PRIMARY, cost: float = DEFAULT_COST, date_col: str = "target") -> pd.DataFrame:
    """Equal-weight daily basket of buyable picks; days with no picks are flat (cash)."""
    usable = frame[~frame["unbuyable"] & frame[ret].notna()]
    daily = (usable[ret] - cost).groupby(usable[date_col]).mean()
    daily = daily.reindex(sorted(frame[date_col].unique())).fillna(0.0)
    curve = (1 + daily).cumprod()
    drawdown = curve / curve.cummax() - 1
    return pd.DataFrame({"daily": daily, "equity": curve, "drawdown": drawdown})


def summary_line(curve: pd.DataFrame) -> dict:
    if curve.empty:
        return {}
    d = curve["daily"]
    losing = (d < 0).astype(int)
    streak = int(losing.groupby((losing != losing.shift()).cumsum()).cumsum().max())
    return dict(days=int(len(d)), total=round(float(curve["equity"].iloc[-1] - 1), 4),
                max_drawdown=round(float(curve["drawdown"].min()), 4),
                win_days=round(float((d > 0).mean()), 3), longest_losing_streak=streak)
