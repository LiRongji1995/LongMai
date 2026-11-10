"""Entry-free evaluation (评估): did the screened stocks really break out, and did the market
temperature calls come true? No entry price, no P&L: the person trading picks entries intraday.

Screening (per observation session t, outcomes from t's close onward, adjusted prices):
  lu1       limit-up on t+1 (continuation)
  max3      highest high over t+1..t+3 relative to t's close
  out5      "走出来": >= 2 limit-ups within t+1..t+5, or max high within t+1..t+5 >= +20%
  bigloss1  "大面" on t+1: low <= -7% or close <= -5% versus t's close
Groups: candidate (rule leaders that are not vetoed / crowded), crowded, vetoed, and the baseline of
all main-board limit-ups that day. Recall: of the stocks that were limit-up on t and then made >= 3
limit-ups within t+1..t+5 (the realized 妖股), the share that were listed (any group) on t.

Temperature nodes (from the SF_MOOD_V1 composite level and its 5-session direction), each a
prediction about the next sessions, scored against the unconditional rate of the same outcome:
  冰点 ICE       level < ice                  -> clear rebound: level(t+3) > level(t) + 0.1
  退潮 EBB       ice <= level < 0.5, falling  -> loss effect tomorrow: premium of today's limit-ups < 0
  升温 WARM      ice <= level < peak, rising  -> profit effect tomorrow: premium of today's limit-ups > 0
  分歧 SPLIT     0.5 <= level < peak, falling -> cooling: level(t+3) < level(t)
  高潮 PEAK      level >= peak                -> clear cooling: level(t+3) < level(t) - 0.1
"premium of today's limit-ups" = mean pct change on t+1 of t's main-board limit-ups (prem_lu at t+1).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import longmai, mood, store, strategy

NODES = ("冰点", "退潮", "升温", "分歧", "高潮")
NODE_PARAMS = dict(ice=0.2, peak=0.8, trend=5)


# ------------------------------------------------------------------ outcomes

def stock_outcomes(w: dict) -> dict[str, pd.DataFrame]:
    adj = w["adj"]
    close_adj = adj
    high_adj = w["high"] * adj / w["close"]
    low_adj = w["low"] * adj / w["close"]
    lu = w["limit_up"].fillna(False).astype(bool)
    fwd_high = lambda k: pd.concat([high_adj.shift(-i) for i in range(1, k + 1)]).groupby(level=0).max() \
        .reindex(close_adj.index)
    max3 = fwd_high(3) / close_adj - 1
    max5 = fwd_high(5) / close_adj - 1
    lu5 = sum(lu.shift(-i, fill_value=False).astype(int) for i in range(1, 6))
    nxt_ok = w["traded"].shift(-1).fillna(False).astype(bool)
    out5 = ((lu5 >= 2) | (max5 >= 0.20)).where(max5.notna())
    bigloss = ((low_adj.shift(-1) / close_adj - 1 <= -0.07) | (close_adj.shift(-1) / close_adj - 1 <= -0.05)).where(nxt_ok)
    return dict(lu1=lu.shift(-1).where(nxt_ok).astype(float), max3=max3, out5=out5.astype(float),
                bigloss1=bigloss.astype(float), lu5=lu5.where(max5.notna()))


def screening_panel(log=print) -> pd.DataFrame:
    """One row per (observation session, listed stock) with group and outcomes, plus baseline rows."""
    stored = sorted(x.stem for x in (store.home() / "bars").glob("*.parquet"))
    ctx = longmai.context(stored, log=log)
    w = ctx["w"]
    out = stock_outcomes(w)
    p = longmai.params()
    sp = strategy.params()
    index = list(w["close"].index)
    rows = []
    for t in range(max(strategy.MIN_HISTORY, 60), len(index) - 1):
        if pd.isna(ctx["mood"]["level"].iloc[t]):
            continue
        day = index[t]
        pool = strategy.eligible(w, ctx["basic"], ctx["calendar"], t, sp)
        d = longmai.decide(ctx, t, index[t + 1], p, longmai._seat_day(ctx, day), pool)
        listed = set()
        for code, s in d["leaders"].iterrows():
            group = "vetoed" if s["vetoes"] else "crowded" if s.get("crowded") else "candidate"
            rows.append(dict(obs=day, code=code, group=group, board=s["board"], height=s["height"]))
            listed.add(code)
        lu_t = w["limit_up"].iloc[t].fillna(False).astype(bool) & pool.reindex(w["close"].columns).fillna(False)
        for code in lu_t.index[lu_t.to_numpy()]:
            rows.append(dict(obs=day, code=code, group="baseline", board=None,
                             height=float(w["height"].iloc[t].get(code, np.nan)), listed=code in listed))
    panel = pd.DataFrame(rows)
    for k, frame in out.items():
        stacked = frame.stack()
        stacked.index.names = ["obs", "code"]
        panel = panel.join(stacked.rename(k), on=["obs", "code"])
    return panel


def screening_report(panel: pd.DataFrame, cuts=("2025-02-06", "2025-12-17")) -> dict:
    panel = panel.assign(seg=np.where(panel["obs"] < cuts[0], "train", np.where(panel["obs"] < cuts[1], "test1", "test2")))
    stats = panel.groupby(["group", "seg"])[["lu1", "max3", "out5", "bigloss1"]].mean().round(3)
    stats["n"] = panel.groupby(["group", "seg"]).size()
    base = panel[panel["group"] == "baseline"]
    winners = base[base["lu5"] >= 3]
    recall = winners.groupby("seg")["listed"].mean().round(3)
    return dict(stats=stats, recall=recall, winners=winners.groupby("seg").size())


# ------------------------------------------------------------------ temperature

def nodes(level: pd.Series, direction: pd.Series, ice: float = 0.2, peak: float = 0.8) -> pd.Series:
    rising = direction > 0
    out = np.select([level < ice, level >= peak, (level < 0.5) & ~rising, rising, (level >= 0.5) & ~rising],
                    ["冰点", "高潮", "退潮", "升温", "分歧"], "")
    return pd.Series(out, index=level.index).where(level.notna() & direction.notna(), "")


def temperature_panel(w: dict | None = None, ice: float = 0.2, peak: float = 0.8, trend: int = 5) -> pd.DataFrame:
    if w is None:
        stored = sorted(x.stem for x in (store.home() / "bars").glob("*.parquet"))
        w = strategy.panel(store.load_window(stored))
    tab = mood.table(w, trend=trend)
    df = tab[["level", "direction", "prem_lu", "promo_rate", "broken_rate", "big_loss", "lu_count"]].copy()
    df["node"] = nodes(df["level"], df["direction"], ice, peak)
    lvl3 = df["level"].shift(-3)
    prem1 = df["prem_lu"].shift(-1)
    df["next_prem"], df["next_big_loss"], df["next_promo"] = prem1, df["big_loss"].shift(-1), df["promo_rate"].shift(-1)
    df["rebound"] = (lvl3 > df["level"] + 0.1).where(lvl3.notna())
    df["cool"] = (lvl3 < df["level"]).where(lvl3.notna())
    df["cool_clear"] = (lvl3 < df["level"] - 0.1).where(lvl3.notna())
    df["profit"] = (prem1 > 0).where(prem1.notna())
    df["loss"] = (prem1 < 0).where(prem1.notna())
    return df


TARGET = {"冰点": "rebound", "退潮": "loss", "升温": "profit", "分歧": "cool", "高潮": "cool_clear"}


def temperature_report(df: pd.DataFrame, cuts=("2025-02-06", "2025-12-17")) -> pd.DataFrame:
    df = df[df["node"] != ""].copy()
    df["seg"] = np.where(df.index < cuts[0], "train", np.where(df.index < cuts[1], "test1", "test2"))
    rows = []
    for seg, part in df.groupby("seg"):
        for node in NODES:
            col = TARGET[node]
            sub = part[part["node"] == node][col].dropna()
            base = part[col].dropna()
            rows.append(dict(seg=seg, node=node, days=len(sub), hit=sub.mean() if len(sub) else np.nan,
                             base=base.mean(), lift=(sub.mean() - base.mean()) if len(sub) else np.nan,
                             next_prem=part.loc[part["node"] == node, "next_prem"].mean(),
                             next_big_loss=part.loc[part["node"] == node, "next_big_loss"].mean()))
    rep = pd.DataFrame(rows)
    order = {"train": 0, "test1": 1, "test2": 2}
    return rep.sort_values(["seg", "node"], key=lambda s: s.map(order) if s.name == "seg" else s.map(NODES.index)) \
        .set_index(["seg", "node"]).round(3)
