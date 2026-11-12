"""Theme threads (题材线索): mainline boards linked across days by the stocks that carry them. Description only.

V9 mainlines (top 3 boards by 3-session limit-up concentration) rotate almost daily: one wave of speculation shows up
as 传媒 one day, 虚拟现实 the next and AI 影视 after that, so "consecutive days as a mainline" cannot say how long a
wave has run (the V11 exam failed on exactly this). A thread joins those boards:

  carriers(b, t)  stocks of board b that closed limit-up within t-2..t (all SSE/SZSE A shares, as the mainline stats)
  a mainline board at t joins the thread whose recent carriers (the last LINK_WINDOW sessions) cover
  >= LINK_SHARE of its own carriers; otherwise it starts a new thread
  a thread with no linked board for more than MAX_GAP sessions ends

Per thread: first day, age in sessions, boards seen, today's limit-ups among its carriers and the peak so far, and a
day-by-day record (`days`: the mainline boards linked that day and the thread's limit-up stocks with their streaks).
The heat board (`heat`) lists boards by limit-up count instead: concentration favours small boards, so a broad
theme such as 机器人概念 (51 limit-ups in 3 days on 2026-11-12, but 783 members) never became a mainline. Heat boards
do not seed threads (umbrella boards such as 人工智能 would glue unrelated threads together).
Uses bars and board membership up to t only (membership is today's snapshot, the known caveat of themes.py).
Never changes a group or an order.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import mainline

VERSION = "SF_THREADS_V1"
HEAT_VERSION = "SF_HEAT_V1"
LOOKBACK = 60
LINK_SHARE = 0.3
LINK_WINDOW = 5
MAX_GAP = 2


HEAT_HISTORY = 20
HEAT_SHOW = 8


def heat(ts: dict, t: int, p: dict, limit: int = HEAT_SHOW, history: bool = True) -> list[dict]:
    """Boards with the most limit-ups over the last 3 sessions (count, not concentration), skipping a board whose
    3-session limit-up stocks are mostly (>= p["overlap_max"]) inside boards already listed. With `history`, each
    board also gets its daily limit-ups over the last HEAT_HISTORY sessions and `streak`: consecutive sessions up to t
    on which it was on that day's heat board. Description only."""
    day = ts["lu"].index[t]
    frame = pd.DataFrame({"lu": ts["lu"].loc[day], "lu3": ts["lu3"].loc[day], "n": ts["n"].loc[day],
                          "z": mainline.concentration(ts, t)})
    frame = frame[(frame["n"] >= 5) & (frame["lu3"] >= 1)].sort_values(["lu3", "lu"], ascending=False)
    lu3_set = ts["limit_up"].iloc[max(0, t - 2):t + 1].fillna(False).astype(bool).any() \
        .reindex(ts["cols"]).fillna(False).to_numpy(dtype=bool)
    out, covered = [], np.zeros(len(ts["cols"]), dtype=bool)
    for board, r in frame.iterrows():
        members = (ts["M"][ts["names"].index(board)] > 0) & lu3_set
        if members.sum() == 0 or (members & covered).sum() >= p.get("overlap_max", 0.6) * members.sum():
            continue
        covered |= members
        out.append(dict(board=board, lu3=int(r["lu3"]), lu=int(r["lu"]), n=int(r["n"]),
                        share=float(r["lu3"]) / float(r["n"]), z=None if pd.isna(r["z"]) else round(float(r["z"]), 2)))
        if len(out) >= limit:
            break
    if history and out:
        lo = max(0, t - HEAT_HISTORY + 1)
        boards_on = {k: {h["board"] for h in heat(ts, k, p, limit, history=False)} for k in range(lo, t)}
        boards_on[t] = {h["board"] for h in out}
        days = list(ts["lu"].index)
        for h in out:
            streak = 0
            for k in range(t, lo - 1, -1):
                if h["board"] not in boards_on[k]:
                    break
                streak += 1
            h["streak"] = streak
            h["streak_capped"] = streak == t - lo + 1
            h["series"] = [int(x) for x in ts["lu"][h["board"]].iloc[lo:t + 1].fillna(0).to_numpy()]
            h["first_day"] = days[lo]
    return out


def _carriers(ts: dict, t: int, board: str) -> set[str]:
    cols = ts["cols"]
    lu3 = ts["limit_up"].iloc[max(0, t - 2):t + 1].fillna(False).astype(bool).any().reindex(cols).fillna(False)
    member = ts["M"][ts["names"].index(board)] > 0
    return {c for c, m, hit in zip(cols, member, lu3.to_numpy(bool)) if m and hit}


def build(ts: dict, t: int, p: dict, lines_at=None) -> list[dict]:
    """Threads alive at t (oldest first). `lines_at(k)` gives the mainlines of session k (default: recomputed)."""
    lines_at = lines_at or (lambda k: mainline.mainlines(ts, k, p))
    days = list(ts["lu"].index)
    lu = ts["limit_up"].fillna(False).astype(bool)
    threads: list[dict] = []
    for k in range(max(0, t - LOOKBACK + 1), t + 1):
        alive = [th for th in threads if k - th["last"] <= MAX_GAP + 1]
        for board in lines_at(k):
            car = _carriers(ts, k, board)
            if not car:
                continue
            best, share = None, 0.0
            for th in alive:
                recent = set().union(*[s for d, s in th["carriers"] if k - d < LINK_WINDOW]) if th["carriers"] else set()
                s = len(car & recent) / len(car)
                if s > share:
                    best, share = th, s
            if best is None or share < LINK_SHARE:
                best = dict(first=k, last=k, boards=[], carriers=[], stocks=set(), by_day={})
                threads.append(best)
                alive.append(best)
            best["last"] = k
            if board not in best["boards"]:
                best["boards"].append(board)
            best["carriers"].append((k, car))
            best["by_day"].setdefault(k, []).append(board)
            best["stocks"] |= car
    out = []
    for th in threads:
        if t - th["last"] > MAX_GAP:
            continue
        stocks = sorted(th["stocks"])
        daily = lu.iloc[th["first"]:t + 1].reindex(columns=stocks).fillna(False).sum(axis=1)
        out.append(dict(first=days[th["first"]], age=t - th["first"] + 1, boards=th["boards"],
                        today=int(daily.iloc[-1]), peak=int(daily.max()), peak_day=str(daily.idxmax()),
                        peak_age=int(np.argmax(daily.to_numpy())) + 1, stocks=len(stocks),
                        mainline_today=th["last"] == t, codes=stocks,
                        series=[int(x) for x in daily.to_numpy()],
                        days=[dict(day=days[k], age=k - th["first"] + 1, boards=th["by_day"].get(k, []),
                                   lu=_streaks(lu, k, stocks)) for k in range(th["first"], t + 1)]))
    return sorted(out, key=lambda x: x["first"])


def _streaks(lu: pd.DataFrame, k: int, stocks: list[str]) -> list[tuple[str, int]]:
    """(code, consecutive limit-ups ending at session k) for the stocks limit-up at k, tallest first."""
    out = []
    for c in stocks:
        if c not in lu.columns or not lu[c].iloc[k]:
            continue
        n = 0
        while k - n >= 0 and lu[c].iloc[k - n]:
            n += 1
        out.append((c, n))
    return sorted(out, key=lambda x: (-x[1], x[0]))


def describe(th: dict) -> str:
    names = "/".join(th["boards"][:4]) + ("等" if len(th["boards"]) > 4 else "")
    since = "今日为主线" if th["mainline_today"] else "今日不在前3主线"
    return (f"{names}：第{th['age']}个交易日（{th['first'][5:]}起，{since}），今日线索内涨停{th['today']}家，"
            f"峰值{th['peak']}家（第{th['peak_age']}天）")
