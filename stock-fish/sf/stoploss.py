"""Exam of the list's reference stop line (参考止损 = observation close x (1 - 7%)); docs/STOPLOSS_V1_PREREG.md.

Entry-free: no entry price is assumed. Question: once a listed stock trades down to its reference stop line on a
session when a holder may sell (A shares are T+1: bought on T = t+1, sellable from T+1), does it tend to keep falling
(selling at the line helps) or to recover (selling at the line hurts)?

Universe: every stock of the replayed V9 list (`auction.replay`), whose T was traded and not a one-price limit-up
(it could be bought on T). Touch: the first session d in T+1..T+3 whose low <= stop; the sale price is the stop, or
d's open if it opened below the stop (gap through the line). After-touch drift from the sale price:
  drift1 = close of d+1 / sale - 1      drift3 = close of d+3 / sale - 1      (primary: drift3)
  rec3   = highest high of d+1..d+3 >= sale x 1.05 (recovered at least +5% after the sale)
Stop widths 5% and 10% are reported only (neighbours). NaN = pending / not traded, never zero.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import auction, store, strategy

VERSION = "SF_STOPLOSS_V1"
SEGS = auction.SEGS
ROLES = auction.ROLES
STOP = 0.07
NEIGHBOURS = (0.05, 0.10)
CHECK_DAYS = 3        # T+1..T+3 checked for the first touch
MIN_ROWS = dict(train=200, test=50)


def touches(panel: pd.DataFrame, w: dict, stop: float = STOP) -> pd.DataFrame:
    """One row per panel row with the first touch day (offset from T) and the after-touch outcomes."""
    index = {d: i for i, d in enumerate(w["close"].index)}
    o, h, lo, c = (w[k] for k in ("open", "high", "low", "close"))
    traded = w["traded"].fillna(False).astype(bool)
    one_up = w["one_price_up"].fillna(False).astype(bool)
    n = len(index)
    rows = []
    for r in panel.itertuples(index=False):
        t, T = index.get(r.obs), index.get(r.day)
        code = r.code
        rec = dict(obs=r.obs, day=r.day, code=code, role=r.role, seg=r.seg, buyable=np.nan, touch=np.nan,
                   gap_through=np.nan, drift1=np.nan, drift3=np.nan, rec3=np.nan)
        if t is None or T is None or code not in c.columns:
            rows.append(rec)
            continue
        if not traded[code].iloc[T]:
            rows.append(rec)
            continue
        rec["buyable"] = float(not one_up[code].iloc[T])
        if not rec["buyable"]:
            rows.append(rec)
            continue
        line = c[code].iloc[t] * (1 - stop)
        if T + CHECK_DAYS >= n:  # the check window is not complete yet: pending
            rows.append(rec)
            continue
        rec["touch"] = 0.0
        for k in range(1, CHECK_DAYS + 1):
            d = T + k
            if not traded[code].iloc[d]:
                continue
            if lo[code].iloc[d] <= line:
                sale = min(line, o[code].iloc[d])
                rec.update(touch=float(k), gap_through=float(o[code].iloc[d] <= line))
                if d + 3 < n:
                    rec["drift1"] = c[code].iloc[d + 1] / sale - 1
                    rec["drift3"] = c[code].iloc[d + 3] / sale - 1
                    rec["rec3"] = float(h[code].iloc[d + 1:d + 4].max() >= sale * 1.05)
                break
        rows.append(rec)
    return pd.DataFrame(rows)


def stats(t: pd.DataFrame) -> pd.DataFrame:
    hit = t[t["touch"] > 0]
    rows = []
    for role in ROLES:
        for seg in SEGS:
            base = t[(t["role"] == role) & (t["seg"] == seg) & t["touch"].notna()]
            x = hit[(hit["role"] == role) & (hit["seg"] == seg) & hit["drift3"].notna()]
            rows.append(dict(role=role, seg=seg, rows=len(base), touch_rate=(base["touch"] > 0).mean() if len(base) else np.nan,
                             n=len(x), gap_through=x["gap_through"].mean(), drift1_mean=x["drift1"].mean(),
                             drift3_mean=x["drift3"].mean(), drift3_median=x["drift3"].median(), rec3=x["rec3"].mean()))
    return pd.DataFrame(rows).set_index(["role", "seg"])


def verdict(s: pd.DataFrame, role: str) -> dict:
    """helps: drift3 mean and median < 0 in all three segments; hurts: both > 0 in all three; else unclear."""
    seg = {k: s.loc[(role, k)] for k in SEGS}
    if seg["train"]["n"] < MIN_ROWS["train"] or min(seg[k]["n"] for k in ("test1", "test2")) < MIN_ROWS["test"]:
        return dict(call="样本不足")
    neg = all(seg[k]["drift3_mean"] < 0 and seg[k]["drift3_median"] < 0 for k in SEGS)
    pos = all(seg[k]["drift3_mean"] > 0 and seg[k]["drift3_median"] > 0 for k in SEGS)
    return dict(call="止损线有用（触及后继续走弱）" if neg else "止损线有害（触及后多反弹）" if pos else "不明确")


def run(log=print) -> dict:
    stored = sorted(x.stem for x in (store.home() / "bars").glob("*.parquet"))
    panel = auction.replay(stored, log=log)
    w = strategy.panel(store.load_window(stored))
    out = {}
    for width in (STOP,) + NEIGHBOURS:
        t = touches(panel, w, width)
        s = stats(t)
        out[width] = dict(table=t, stats=s, verdicts={r: verdict(s, r) for r in ROLES})
    return out
