"""Mainlines (主线) and their leaders for 龙脉 V7 (docs/STRATEGY_LONGMAI_V7.md).

Mainline themes (day t), from all SSE/SZSE A shares with board-specific limit ratios:
  z3 = binomial z of a board's 3-session limit-ups against the market's 3-session limit-up rate;
  eligible boards have >= min_theme_lu3 limit-ups in 3 sessions, >= 1 today and >= 5 traded members;
  boards are taken by z3, skipping one whose 3-session limit-up set overlaps the taken ones by
  >= overlap_max, until top_k boards.
Leaders, main-board eligible stocks only: strong today (limit-up, or +5% with CLV >= 0.7), member of a
mainline, rank <= leader_rank_max inside it by (board height, turnover rate). 空间板: the tallest
strong main-board stock(s) with >= space_min_height boards, whatever their theme.
Point-in-time caveat: board membership is today's snapshot (see themes.py).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import store


def a_share_panel(bars: pd.DataFrame) -> dict:
    """All SSE/SZSE A shares (incl. ChiNext/STAR) with board-specific limit ratios, for theme stats."""
    code = bars["code"]
    num = code.str[3:]
    keep = (code.str.startswith("sh.") & num.str.startswith(("60", "68"))) | \
           (code.str.startswith("sz.") & num.str.startswith(("00", "30")))
    bars = bars[keep]
    w = store.wide(bars, ("close", "preclose", "amount", "volume", "turn"), {"_st": "isST"})
    st = w.pop("_st")
    from .strategy import limit_ratio
    ratio = limit_ratio(w["close"].index, w["close"].columns, st)
    up =np.floor(w["preclose"] * (1 + ratio) * 100 + 0.5) / 100
    traded = (w["volume"] > 0) & (w["close"] > 0)
    w["limit_up"] = traded & (w["close"] >= up - 1e-9)
    w["amount"] = w["amount"].where(traded)
    w["turn"] = w["turn"].where(traded)
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
    traded = aw["amount"].notna().to_numpy(dtype=np.float32)
    n = pd.DataFrame(traded @ M.T, index=days, columns=names).where(exists)
    lu_all, tr_all = lu_m.sum(axis=1), traded.sum(axis=1)
    rate3 = pd.Series(lu_all, index=days).rolling(3, min_periods=1).sum() / \
        pd.Series(tr_all, index=days).rolling(3, min_periods=1).sum()
    return dict(lu=lu, lu3=lu.rolling(3, min_periods=1).sum(), share=share, M=M, names=names, cols=cols,
                n=n, n3=n.rolling(3, min_periods=1).sum(), rate3=rate3, limit_up=aw["limit_up"])


def concentration(ts: dict, t: int) -> pd.Series:
    """V5 board score z3: 3-session limit-ups against the market limit-up rate (binomial z)."""
    day = ts["lu"].index[t]
    k, n, r = ts["lu3"].loc[day], ts["n3"].loc[day], float(ts["rate3"].loc[day])
    return (k - n * r) / np.sqrt(n * r * (1 - r)).replace(0, np.nan)


SPACE = "空间板"


def mainlines(ts: dict, t: int, p: dict) -> list[str]:
    day = ts["lu"].index[t]
    frame = pd.DataFrame({"lu": ts["lu"].loc[day], "lu3": ts["lu3"].loc[day], "n": ts["n"].loc[day],
                          "z": concentration(ts, t)}).dropna()
    frame = frame[(frame["lu3"] >= p["min_theme_lu3"]) & (frame["lu"] >= 1) & (frame["n"] >= 5)]
    lu3_set = ts["limit_up"].iloc[max(0, t - 2):t + 1].fillna(False).astype(bool).any() \
        .reindex(ts["cols"]).fillna(False).to_numpy(dtype=bool)
    taken, covered = [], np.zeros(len(ts["cols"]), dtype=bool)
    for board in frame.sort_values("z", ascending=False).index:
        members = (ts["M"][ts["names"].index(board)] > 0) & lu3_set
        if members.sum() == 0 or (members & covered).sum() >= p["overlap_max"] * members.sum():
            continue
        taken.append(board)
        covered |= members
        if len(taken) >= p["top_k"]:
            break
    return taken


def signals(w: dict, aw: dict, ts: dict, pool: pd.Series, t: int, p: dict) -> pd.DataFrame:
    """Leader candidates (one row per code): mainline leaders plus the 空间板 class."""
    lines = mainlines(ts, t, p)
    turn_today = aw["turn"].iloc[t]
    turn_rank = turn_today.rank(ascending=False, method="min")
    raw = {c: w[c].iloc[t] for c in ("close", "preclose", "high", "low", "amount")}
    ret1 = raw["close"] / raw["preclose"] - 1
    rng = raw["high"] - raw["low"]
    clv = ((raw["close"] - raw["low"]) / rng).where(rng > 0, 1.0)
    strong = w["limit_up"].iloc[t].fillna(False).astype(bool) | ((ret1 >= 0.05) & (clv >= 0.7))
    height = w["height"].iloc[t]
    ok_all = pool.reindex(height.index).fillna(False) & strong.reindex(height.index).fillna(False)
    day = w["close"].index[t]

    def row(code, board, board_lu, rank):
        return dict(code=code, board=board, board_lu=board_lu, leader_rank=rank, height=int(height[code]),
                    amount=float(raw["amount"][code]), turn=float(turn_today.get(code, np.nan)),
                    turn_rank=float(turn_rank.get(code, np.nan)), ret1=float(ret1.get(code, np.nan)))

    rows = []
    cols = list(ts["cols"])
    for board in lines:
        index = pd.Index([cols[j] for j in np.flatnonzero(ts["M"][ts["names"].index(board)])]) \
            .intersection(w["close"].columns)
        index = index[ok_all.reindex(index).fillna(False).to_numpy(dtype=bool)]
        if not len(index):
            continue
        cand = pd.DataFrame({"height": height.reindex(index), "act": turn_today.reindex(index)})
        rank = cand.sort_values(["height", "act"], ascending=False).assign(r=range(1, len(cand) + 1))["r"]
        for code in rank.index[rank <= p["leader_rank_max"]]:
            rows.append(row(code, board, float(ts["lu"].loc[day, board]), int(rank[code])))
    h = height[ok_all].dropna()
    if len(h) and h.max() >= p["space_min_height"]:
        top = turn_today.reindex(h[h == h.max()].index).sort_values(ascending=False).index[:2]
        rows += [row(code, SPACE, 0.0, 1) for code in top]
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    frame["space"] = frame["board"] == SPACE
    frame = frame.sort_values(["space", "board_lu", "leader_rank"], ascending=[False, False, True]).drop(columns="space")
    return frame.drop_duplicates("code").set_index("code")
