"""Theme threads (题材线索): mainline boards linked across days by the stocks that carry them. Description only.

V9 mainlines (top 3 boards by 3-session limit-up concentration) rotate almost daily: one wave of speculation shows up
as 传媒 one day, 虚拟现实 the next and AI 影视 after that, so "consecutive days as a mainline" cannot say how long a
wave has run (the V11 exam failed on exactly this). A thread joins those boards:

  carriers(b, t)  stocks of board b that closed limit-up within t-2..t (all SSE/SZSE A shares, as the mainline stats)
  a mainline board at t joins the thread whose recent carriers (the last LINK_WINDOW sessions) cover
  >= LINK_SHARE of its own carriers; otherwise it starts a new thread
  a thread with no linked board for more than MAX_GAP sessions ends

Per thread: first day, age in sessions, boards seen, today's limit-ups among its carriers and the peak so far.
Uses bars and board membership up to t only (membership is today's snapshot, the known caveat of themes.py).
Never changes a group or an order.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import mainline

VERSION = "SF_THREADS_V1"
LOOKBACK = 60
LINK_SHARE = 0.3
LINK_WINDOW = 5
MAX_GAP = 2


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
                best = dict(first=k, last=k, boards=[], carriers=[], stocks=set())
                threads.append(best)
                alive.append(best)
            best["last"] = k
            if board not in best["boards"]:
                best["boards"].append(board)
            best["carriers"].append((k, car))
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
                        series=[int(x) for x in daily.to_numpy()]))
    return sorted(out, key=lambda x: x["first"])


def describe(th: dict) -> str:
    names = "/".join(th["boards"][:4]) + ("等" if len(th["boards"]) > 4 else "")
    since = "今日为主线" if th["mainline_today"] else "今日不在前3主线"
    return (f"{names}：第{th['age']}个交易日（{th['first'][5:]}起，{since}），今日线索内涨停{th['today']}家，"
            f"峰值{th['peak']}家（第{th['peak_age']}天）")
