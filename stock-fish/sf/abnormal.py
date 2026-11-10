"""Abnormal-volatility (异动) accounting: how close each stock is to an exchange trigger.

Deviation for a session = stock pct change - benchmark index pct change. The exchanges publish
trigger rules; the defaults below are the commonly used main-board thresholds and are configurable
because the rules change. `calibrate()` checks the computed 3-day trigger against the actual LHB
listing reasons, which are the exchange's own record of who triggered.

Per (session, stock) facts (known after the close of that session):
  dev1, dev3, dev10, dev30   cumulative deviation over the last 1/3/10/30 sessions
  room3        how much more deviation tomorrow can add before the 3-day trigger (uses the last 2 days)
  lu_trips3    True if a limit-up tomorrow would reach the 3-day trigger
  room10, lu_trips10, room30, lu_trips30   same for the severe (10/30-day) thresholds
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import store

VERSION = "SF_ABNORMAL_V1"
DEFAULTS = dict(dev3=0.20, dev10=1.00, dev30=2.00)
# Candidate benchmarks; calibrate() picks the one that matches LHB reasons best.
INDEXES = {"sh.000001": "上证指数", "sz.399001": "深证成指", "sz.399107": "深证A指", "sh.000002": "上证A指"}


def _path():
    return store.home() / "meta" / "indexes.parquet"


def refresh_indexes(start: str = "2021-01-01", end: str = "2099-12-31") -> pd.DataFrame:
    with store.BaoStock() as bao:
        frames = [bao.index_daily(code, start, end) for code in INDEXES]
    frame = pd.concat(frames, ignore_index=True)
    frame["pct"] = pd.to_numeric(frame["pctChg"], errors="coerce") / 100
    frame = frame[["date", "code", "pct"]]
    frame.to_parquet(_path(), index=False)
    return frame


def index_returns(update: bool = False) -> pd.DataFrame:
    """Day x index-code frame of daily returns."""
    if update or not _path().exists():
        refresh_indexes()
    return pd.read_parquet(_path()).pivot(index="date", columns="code", values="pct").sort_index()


def deviation(pct: pd.DataFrame, idx: pd.DataFrame, sh_index="sh.000001", sz_index="sz.399001") -> pd.DataFrame:
    """pct: day x code stock returns (NaN when not traded). Benchmark by exchange."""
    bench = pd.DataFrame(np.where(pct.columns.str.startswith("sh.")[None, :],
                                  idx[sh_index].reindex(pct.index).to_numpy()[:, None],
                                  idx[sz_index].reindex(pct.index).to_numpy()[:, None]),
                         index=pct.index, columns=pct.columns)
    return pct - bench


def facts(pct: pd.DataFrame, limit_pct: pd.DataFrame, idx: pd.DataFrame, p: dict | None = None,
          sh_index="sh.000001", sz_index="sz.399001") -> dict:
    """limit_pct: day x code price-limit ratio (0.10 main board, 0.05 ST, 0.20 ChiNext/STAR).

    Suspended days count as zero deviation (a simplification; the exchanges count trading days)."""
    p = {**DEFAULTS, **(p or {})}
    dev = deviation(pct, idx, sh_index, sz_index).fillna(0.0)
    out = {"dev1": dev}
    for n in (3, 10, 30):
        out[f"dev{n}"] = dev.rolling(n, min_periods=1).sum()
        prior = dev.rolling(n - 1, min_periods=1).sum()  # the sessions that stay in tomorrow's window
        bench_guess = 0.0  # tomorrow's index move is unknown; assume flat
        out[f"room{n}"] = p[f"dev{n}"] - prior
        out[f"lu_trips{n}"] = (prior + limit_pct - bench_guess) >= p[f"dev{n}"] - 1e-9
    return out


def calibrate(dev3: pd.DataFrame, listed: pd.DataFrame, threshold: float = 0.20) -> dict:
    """Compare computed 3-day triggers with actual LHB reasons.

    listed: rows (date, code) whose LHB reason is the 3-day 20% deviation rule."""
    trig = dev3.stack(future_stack=True)
    trig = trig[trig >= threshold - 1e-9]
    computed = set(map(tuple, trig.index.to_frame(index=False)[["date", "code"]].astype(str).to_numpy()))
    actual = set(map(tuple, listed[["date", "code"]].astype(str).to_numpy()))
    hit = computed & actual
    return dict(actual=len(actual), computed=len(computed), matched=len(hit),
                recall=len(hit) / len(actual) if actual else np.nan,
                precision=len(hit) / len(computed) if computed else np.nan)


def trigger3(dev: pd.DataFrame, threshold: float = 0.20) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """3-session cumulative deviation with the exchange reset: after a trigger, counting restarts
    the next session. Returns (cum3, triggered, room_next) where room_next is how much deviation the
    next session may add before triggering (based on the sessions that stay in its window)."""
    d = dev.fillna(0.0).to_numpy()
    n, m = d.shape
    cum = np.zeros((n, m))
    trig = np.zeros((n, m), dtype=bool)
    room = np.zeros((n, m))
    a1 = np.zeros(m)  # deviation of t-1 counted since the last reset
    a2 = np.zeros(m)  # deviation of t-2 counted since the last reset
    for t in range(n):
        s = a2 + a1 + d[t]
        cum[t] = s
        hit = s >= threshold - 1e-9
        trig[t] = hit
        a2, a1 = np.where(hit, 0.0, a1), np.where(hit, 0.0, d[t])
        room[t] = threshold - (a2 + a1)
    mk = lambda x: pd.DataFrame(x, index=dev.index, columns=dev.columns)
    return mk(cum), mk(trig), mk(room)
