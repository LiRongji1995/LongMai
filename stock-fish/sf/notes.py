"""Hype notes (炒作题材): why a stock is really being traded, taken from news and announcements.

Board membership says what a stock belongs to, not why it rises. Neither board-based ranking works for
attribution (concentration favours tiny boards, limit-up counts favour umbrella boards), and the
membership snapshot misses new stories: on 2026-11-10 腾达科技 and 泰尔股份 traded as 商业航天 but are not
members of the Eastmoney 商业航天 board. So the theme is recorded by hand (the person or the AI
assistant after its daily news search) with `sf note`, and the list shows it before the board.

File: meta/hype_notes.csv with columns date (news / observation session), code, theme, source.
A note is used for lists whose observation session is on or after its date, for `MAX_AGE` sessions.
"""
from __future__ import annotations

import pandas as pd

from . import store

COLUMNS = ["date", "code", "theme", "source"]
MAX_AGE = 10  # sessions


def path():
    return store.home() / "meta" / "hype_notes.csv"


def load() -> pd.DataFrame:
    p = path()
    if not p.exists():
        return pd.DataFrame(columns=COLUMNS)
    return pd.read_csv(p, dtype=str).fillna("")


def add(date: str, code: str, theme: str, source: str = "") -> pd.DataFrame:
    """Append one note (never edits earlier rows; the newest note for a code wins)."""
    frame = pd.concat([load(), pd.DataFrame([dict(date=date, code=code, theme=theme, source=source)])],
                      ignore_index=True)
    path().parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path(), index=False, encoding="utf-8")
    return frame


def active(observation: str, sessions: list[str], frame: pd.DataFrame | None = None) -> dict[str, dict]:
    """code -> newest note dated within the last MAX_AGE sessions up to `observation`."""
    frame = load() if frame is None else frame
    past = [s for s in sessions if s <= observation][-MAX_AGE:]
    if frame.empty or not past:
        return {}
    ok = frame[(frame["date"] >= past[0]) & (frame["date"] <= observation)]
    return {code: dict(theme=r["theme"], date=r["date"], source=r["source"])
            for code, r in ok.sort_values("date", kind="stable").groupby("code").tail(1).set_index("code").iterrows()}


def tag(code: str, board: str | None, notes: dict[str, dict], unknown: str = "") -> str:
    """Role suffix: the noted theme first, the membership board second."""
    n = notes.get(code)
    if n:
        extra = f"·板块{board}" if board and board != n["theme"] else ""
        return f"·炒作{n['theme']}(新闻{n['date'][5:]})" + extra
    return f"·板块{board}" if board else unknown
