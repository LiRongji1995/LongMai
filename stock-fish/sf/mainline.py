"""MAINLINE_LEADER: trade leaders of the market's main themes, exit when trading gets crowded.

Definitions (all computed from data visible at the signal-day close; entry at next open):

Mainline themes (day t)
  For every board that already existed on day t (index history start <= t):
    lu      = members closing at limit-up (all A shares, board-specific limit ratio)
    lu3     = lu summed over the last 3 sessions (persistence)
    share   = members' turnover / whole-market turnover
  Mainline = top K boards by (lu3, lu, share) with lu >= min_theme_limit_ups.

Leader (day t), main-board eligible stocks only
  - member of a mainline board
  - relative turnover: market turnover rank <= amount_rank_max (all A shares), i.e. money concentrates here
  - strong today: closed at limit-up, or ret1 >= 5% with CLV >= 0.7
  - within its mainline board, rank <= leader_rank_max by (board height desc, turnover desc)

Entry (target session open, auction-time filter)
  skip if the open is at limit-up (no fill) or the open gap <= -3% (weak auction)

Exit (decided at each close from the entry day on; executed at the next open)
  BROKEN       touched limit-up but closed below (炸板)
  CHURN        turnover >= churn_mult x prior-5-day average and ret1 <= 2% (放量滞涨, crowded)
  TREND        close below MA5
  THEME_FADE   the leader's best mainline board has lu < fade_ratio x its signal-day lu (退潮/分化)
  STOP         close <= entry x (1 - stop_loss)
  TIME         held max_hold sessions
Point-in-time caveat: board membership is today's snapshot (see themes.py).
"""
from __future__ import annotations

from copy import deepcopy

import numpy as np
import pandas as pd

from . import outcomes, store, strategy, themes

VERSION = "SF_MAINLINE_LEADER_V1"
DEFAULTS = dict(top_k=3, min_theme_limit_ups=3, amount_rank_max=100, leader_rank_max=2,
                gap_min=-0.03, churn_mult=2.0, fade_ratio=0.34, stop_loss=0.07, max_hold=10)


def params(overrides=None):
    p = deepcopy(DEFAULTS)
    p.update(overrides or {})
    return p


# ------------------------------------------------------------------ panels

def a_share_panel(bars: pd.DataFrame) -> dict:
    """All SSE/SZSE A shares (incl. ChiNext/STAR) with board-specific limit ratios, for theme stats."""
    code = bars["code"]
    num = code.str[3:]
    keep = (code.str.startswith("sh.") & num.str.startswith(("60", "68"))) | \
           (code.str.startswith("sz.") & num.str.startswith(("00", "30")))
    bars = bars[keep]
    w = {c: bars.pivot(index="date", columns="code", values=c).sort_index()
         for c in ("close", "preclose", "amount", "volume")}
    st = bars.pivot(index="date", columns="code", values="isST").sort_index() == "1"
    cols = w["close"].columns
    wide_board = pd.Series(cols.str[3:].str.startswith(("30", "68")), index=cols)
    ratio = np.where(wide_board.to_numpy()[None, :], 0.20, np.where(st, 0.05, 0.10))
    up = np.floor(w["preclose"] * (1 + ratio) * 100 + 0.5) / 100
    traded = (w["volume"] > 0) & (w["close"] > 0)
    w["limit_up"] = traded & (w["close"] >= up - 1e-9)
    w["amount"] = w["amount"].where(traded)
    return w


def theme_stats(aw: dict, boards: pd.DataFrame, members: pd.DataFrame) -> dict:
    """Day x board frames: lu, lu3, share; boards masked before their first_date."""
    cols = aw["close"].columns
    members = members[members["code"].isin(cols)]
    names = sorted(members["board"].unique())
    bidx = {b: i for i, b in enumerate(names)}
    cidx = {c: i for i, c in enumerate(cols)}
    M = np.zeros((len(names), len(cols)), dtype=np.float32)
    M[[bidx[b] for b in members["board"]], [cidx[c] for c in members["code"]]] = 1.0
    lu_m = aw["limit_up"].fillna(False).to_numpy(dtype=np.float32)
    amt = aw["amount"].fillna(0).to_numpy(dtype=np.float64)
    days = aw["close"].index
    lu = pd.DataFrame(lu_m @ M.T, index=days, columns=names)
    share = pd.DataFrame((amt @ M.T) / amt.sum(axis=1, keepdims=True), index=days, columns=names)
    first = boards.drop_duplicates("board").set_index("board")["first_date"].reindex(names)
    exists = pd.DataFrame(np.asarray(days)[:, None] >= first.fillna("9999-12-31").to_numpy()[None, :],
                          index=days, columns=names)
    lu, share = lu.where(exists), share.where(exists)
    return dict(lu=lu, lu3=lu.rolling(3, min_periods=1).sum(), share=share, M=M, names=names, cols=cols)


def mainlines(ts: dict, t: int, p: dict) -> list[str]:
    day = ts["lu"].index[t]
    frame = pd.DataFrame({"lu": ts["lu"].loc[day], "lu3": ts["lu3"].loc[day], "share": ts["share"].loc[day]}).dropna()
    frame = frame[frame["lu"] >= p["min_theme_limit_ups"]]
    return frame.sort_values(["lu3", "lu", "share"], ascending=False).head(p["top_k"]).index.tolist()


# ------------------------------------------------------------------ signals

def signals(w: dict, aw: dict, ts: dict, pool: pd.Series, t: int, p: dict) -> pd.DataFrame:
    lines = mainlines(ts, t, p)
    if not lines:
        return pd.DataFrame()
    day = w["close"].index[t]
    amount_rank = aw["amount"].loc[day].rank(ascending=False)
    raw = {c: w[c].iloc[t] for c in ("close", "preclose", "high", "low", "amount")}
    ret1 = raw["close"] / raw["preclose"] - 1
    rng = raw["high"] - raw["low"]
    clv = ((raw["close"] - raw["low"]) / rng).where(rng > 0, 1.0)
    limit_up = w["limit_up"].iloc[t].fillna(False).astype(bool)
    strong = limit_up | ((ret1 >= 0.05) & (clv >= 0.7))
    height = w["height"].iloc[t]
    cols = list(ts["cols"])
    rows = []
    for board in lines:
        member_codes = [cols[j] for j in np.flatnonzero(ts["M"][ts["names"].index(board)])]
        cand = pd.DataFrame(index=pd.Index(member_codes).intersection(w["close"].columns))
        if cand.empty:
            continue
        cand["height"] = height.reindex(cand.index)
        cand["amount"] = raw["amount"].reindex(cand.index)
        cand["leader_rank"] = cand.sort_values(["height", "amount"], ascending=False).assign(
            r=range(1, len(cand) + 1))["r"]
        ok = (pool.reindex(cand.index).fillna(False) & strong.reindex(cand.index).fillna(False)
              & (amount_rank.reindex(cand.index) <= p["amount_rank_max"])
              & (cand["leader_rank"] <= p["leader_rank_max"]))
        for code in cand.index[ok]:
            rows.append(dict(code=code, board=board, board_lu=float(ts["lu"].loc[day, board]),
                             leader_rank=int(cand.loc[code, "leader_rank"]), height=int(cand.loc[code, "height"]),
                             amount=float(cand.loc[code, "amount"]), amount_rank=float(amount_rank.get(code, np.nan)),
                             ret1=float(ret1.get(code, np.nan))))
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows).sort_values(["board_lu", "leader_rank"], ascending=[False, True])
    return frame.drop_duplicates("code").set_index("code")


# ------------------------------------------------------------------ trade simulation

def simulate(w: dict, ts: dict, t: int, code: str, board: str, board_lu: float, p: dict,
             cost: float = outcomes.DEFAULT_COST) -> dict | None:
    index = w["close"].index
    e = t + 1  # entry session
    if e >= len(index):
        return None
    o, pc = w["open"].iloc[e].get(code), w["preclose"].iloc[e].get(code)
    if pd.isna(o) or pd.isna(pc) or not bool(w["traded"].iloc[e].get(code, False)):
        return dict(status="NO_ENTRY", reason="SUSPENDED")
    if bool(w["open_at_limit"].iloc[e].get(code, False)):
        return dict(status="NO_ENTRY", reason="OPEN_AT_LIMIT")
    gap = o / pc - 1
    if gap <= p["gap_min"]:
        return dict(status="NO_ENTRY", reason="WEAK_AUCTION", gap=gap)
    adj, close = w["adj"][code], w["close"][code]
    entry_adj = o * adj.iloc[e] / close.iloc[e]
    turn = w["turn"][code]
    for k in range(e, min(e + p["max_hold"], len(index))):
        c_adj = adj.iloc[k]
        reason = None
        if bool(w["touched_up"].iloc[k].get(code, False)) and not bool(w["limit_up"].iloc[k].get(code, False)):
            reason = "BROKEN"
        elif turn.iloc[k] >= p["churn_mult"] * turn.iloc[max(0, k - 5):k].mean() and \
                close.iloc[k] / w["preclose"][code].iloc[k] - 1 <= 0.02:
            reason = "CHURN"
        elif c_adj < adj.iloc[k - 4:k + 1].mean():
            reason = "TREND"
        elif ts["lu"].iloc[k].get(board, 0) < p["fade_ratio"] * board_lu:
            reason = "THEME_FADE"
        elif c_adj <= entry_adj * (1 - p["stop_loss"]):
            reason = "STOP"
        elif k == e + p["max_hold"] - 1:
            reason = "TIME"
        if reason:
            if k + 1 >= len(index):
                return dict(status="OPEN", gap=gap, held=k - e + 1)
            x = w["open"][code].iloc[k + 1]
            exit_adj = x * adj.iloc[k + 1] / close.iloc[k + 1]
            return dict(status="CLOSED", gap=gap, held=k - e + 1, exit_reason=reason,
                        ret=exit_adj / entry_adj - 1 - cost, exit_date=index[k + 1])
    return dict(status="OPEN", gap=gap)


def backtest(p: dict | None = None, start: str | None = None, end: str | None = None, log=print,
             cache: dict | None = None) -> pd.DataFrame:
    """One row per signal (incl. skipped entries) with trade outcome."""
    p = params(p)
    cache = cache if cache is not None else {}
    if "w" not in cache:
        loaded = themes.load()
        if loaded is None:
            raise FileNotFoundError("theme data missing; run `sf themes` first")
        stored = sorted(x.stem for x in (store.home() / "bars").glob("*.parquet"))
        bars = store.load_window(stored)
        cache["w"] = strategy.panel(bars)
        aw = a_share_panel(bars)
        cache["ts"] = theme_stats(aw, *loaded)
        cache["aw"] = aw
        cache["basic"] = store.stock_basic()
        cache["calendar"] = store.calendar()
        cache["pools"] = {}
    w, aw, ts = cache["w"], cache["aw"], cache["ts"]
    rows = []
    sp = strategy.params()
    for t in range(strategy.MIN_HISTORY, len(w["close"].index) - 1):
        day = w["close"].index[t]
        if (start and day < start) or (end and day > end):
            continue
        if t not in cache["pools"]:
            cache["pools"][t] = strategy.eligible(w, cache["basic"], cache["calendar"], t, sp)
        sig = signals(w, aw, ts, cache["pools"][t], t, p)
        for code, s in sig.iterrows():
            trade = simulate(w, ts, t, code, s["board"], s["board_lu"], p) or {}
            rows.append(dict(obs=day, code=code, **s.to_dict(), **trade))
    return pd.DataFrame(rows)


def summarize(trades: pd.DataFrame, split=(0.6, 0.8)) -> dict:
    closed = trades[trades.get("status") == "CLOSED"] if not trades.empty else trades
    if closed.empty:
        return dict(trades=0)
    days = sorted(trades["obs"].unique())
    c1, c2 = days[int(len(days) * split[0])], days[int(len(days) * split[1])]
    seg = np.select([closed["obs"] < c1, closed["obs"] < c2], ["train", "test1"], "test2")
    daily = closed["ret"].groupby([seg, closed["obs"]]).mean()
    out = dict(signals=len(trades), trades=len(closed),
               no_entry=trades["status"].eq("NO_ENTRY").mean().round(3),
               avg=round(closed["ret"].mean(), 4), hit=round((closed["ret"] > 0).mean(), 3),
               payoff=round(closed.loc[closed.ret > 0, "ret"].mean() / -closed.loc[closed.ret <= 0, "ret"].mean(), 2),
               held=round(closed["held"].mean(), 1))
    for name in ("train", "test1", "test2"):
        x = daily[name] if name in daily.index.get_level_values(0) else pd.Series(dtype=float)
        out[f"{name}_days"] = len(x)
        out[f"{name}_avg"] = round(x.mean(), 4) if len(x) else np.nan
        out[f"{name}_t"] = round(x.mean() / x.std(ddof=1) * np.sqrt(len(x)), 2) if len(x) > 2 else np.nan
    out["exit_mix"] = closed["exit_reason"].value_counts(normalize=True).round(2).to_dict()
    return out
