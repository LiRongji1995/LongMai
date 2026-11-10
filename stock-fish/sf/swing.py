"""龙脉 V8 (`SF_LONGMAI_V8`), 5-10 session swing screening, and its exam. Pre-registration:
docs/STRATEGY_LONGMAI_V8_PREREG.md (written and committed before this code). Not adopted yet: V7 stays the
live list until V8 passes the pre-registered rule on train, test1 and test2 and then a forward record.

Outcomes (entry-free, from the observation close, adjusted prices, NaN = pending):
  max10, min5, ret10, max20, win10 (+15% high before -8% low within 10 sessions; same day = loss), loss10.
Score: equal-weight mean of cross-sectional percentiles of eight point-in-time features; top 15 per day.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import abnormal, longmai, mainline, store, strategy

VERSION = "SF_LONGMAI_V8"
PARAMS = dict(version=VERSION, top_n=15, min_amount5=1e8, win=0.15, stop=-0.08, horizon=10,
              swing_winner=0.50, swing_window=20, dev30_near=1.70, events_near=3, post_severe_days=10)
FEATURES = ("rs20", "rs60", "smooth20", "trend", "near_high", "vol_ratio", "lu20", "theme_z")
SEGMENTS = (("train", None, "2024-12-31"), ("test1", "2025-01-01", "2025-12-31"), ("test2", "2026-01-01", None))


def segment(day: str) -> str:
    for name, lo, hi in SEGMENTS:
        if (lo is None or day >= lo) and (hi is None or day <= hi):
            return name
    return ""


# ------------------------------------------------------------------ the exam

def outcomes(w: dict, p: dict | None = None) -> dict[str, pd.DataFrame]:
    p = p or PARAMS
    traded = w["traded"].fillna(False).astype(bool)
    close = w["adj"]
    high = (w["high"] * w["adj"] / w["close"]).where(traded)
    low = (w["low"] * w["adj"] / w["close"]).where(traded)
    n = len(close.index)
    pos = pd.Series(np.arange(n), index=close.index)

    def ready(k):  # all k later sessions are in the store; otherwise pending
        return pos + k < n

    def fwd(frame, k, how):
        shifted = [frame.shift(-i) for i in range(1, k + 1)]
        stacked = np.stack([s.to_numpy() for s in shifted])
        import warnings
        with warnings.catch_warnings():  # all-NaN columns (suspended / not listed) stay NaN
            warnings.simplefilter("ignore", RuntimeWarning)
            agg = np.nanmax(stacked, axis=0) if how == "max" else np.nanmin(stacked, axis=0)
        return pd.DataFrame(agg, index=frame.index, columns=frame.columns).where(ready(k), axis=0)

    out = {"max10": fwd(high, 10, "max") / close - 1, "min5": fwd(low, 5, "min") / close - 1,
           "max20": fwd(high, p["swing_window"], "max") / close - 1,
           "ret10": (close.shift(-10) / close - 1).where(ready(10), axis=0)}
    state = pd.DataFrame(0.0, index=close.index, columns=close.columns)  # 0 open, 1 win, -1 loss
    for k in range(1, p["horizon"] + 1):
        up = (high.shift(-k) / close - 1) >= p["win"]
        down = (low.shift(-k) / close - 1) <= p["stop"]
        open_ = state == 0
        state = state.mask(open_ & down, -1.0).mask(open_ & ~down & up, 1.0)
    done = ready(p["horizon"])
    out["win10"] = (state == 1).astype(float).where(done, axis=0)
    out["loss10"] = (state == -1).astype(float).where(done, axis=0)
    return out


# ------------------------------------------------------------------ features and score

def features(w: dict, ts: dict, t: int) -> pd.DataFrame:
    """The eight V8 features for every main-board stock at session index t (known after t's close)."""
    adj, present = w["adj"], w["close"].notna()

    def ret(k):
        return (adj.iloc[t] / adj.iloc[t - k] - 1).where(present.iloc[t - k:t + 1].all())

    daily = adj.iloc[t - 20:t + 1].pct_change(fill_method=None).iloc[1:]
    ma20 = adj.iloc[t - 19:t + 1].mean(skipna=False)
    ma20_prev = adj.iloc[t - 24:t - 4].mean(skipna=False)
    amount = w["amount"].where(w["traded"].fillna(False).astype(bool))
    z = mainline.concentration(ts, t).reindex(ts["names"]).to_numpy(dtype=float)
    zmat = np.where(ts["M"] > 0, np.nan_to_num(z, nan=-np.inf)[:, None], -np.inf)
    theme = pd.Series(np.clip(zmat.max(axis=0), 0, None), index=ts["cols"])  # no hot board = 0
    r20 = ret(20)
    return pd.DataFrame({
        "ret20": r20, "ret60": ret(60),
        "smooth20": r20 / daily.abs().sum(skipna=False),
        "trend": ((adj.iloc[t] > ma20) & (ma20 > ma20_prev)).astype(float).where(ma20.notna() & ma20_prev.notna()),
        "near_high": adj.iloc[t] / adj.iloc[t - 59:t + 1].max(skipna=False),
        "vol_ratio": amount.iloc[t - 4:t + 1].mean() / amount.iloc[t - 59:t + 1].mean(),
        "lu20": w["limit_up"].iloc[t - 19:t + 1].fillna(False).astype(int).sum(),
        "theme_z": theme.reindex(adj.columns).fillna(0.0),
        "amount5": amount.iloc[t - 4:t + 1].mean(),
    })


def score(f: pd.DataFrame, pool: pd.Series, use=FEATURES) -> pd.Series:
    """Equal-weight mean of within-pool percentiles; a stock missing any feature is unscored (NaN)."""
    g = f[pool.reindex(f.index).fillna(False).astype(bool)].copy()
    med = g[["ret20", "ret60"]].median()
    g["rs20"], g["rs60"] = g["ret20"] - med["ret20"], g["ret60"] - med["ret60"]
    ranks = g[list(use)].rank(pct=True)
    return ranks.mean(axis=1, skipna=False)


def high_vol(severe: dict, trig_events: pd.DataFrame, t: int, codes, p: dict | None = None) -> pd.Series:
    p = p or PARAMS
    since = severe["days_since"].iloc[t].reindex(codes)
    return (severe["flag"].iloc[t].reindex(codes).fillna(False).astype(bool)
            | (since <= p["post_severe_days"]).fillna(False)
            | (severe["dev30"].iloc[t].reindex(codes) >= p["dev30_near"]).fillna(False)
            | (trig_events.iloc[t].reindex(codes) >= p["events_near"]).fillna(False))


# ------------------------------------------------------------------ replay (train / tests)

def replay(sessions: list[str], start: int = 60, log=print) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Per session: all scored stocks with features and score (for V8 and its neighbours) and the V7 list rows.
    Returns (scored, v7) long frames keyed by (obs, code)."""
    ctx = longmai.context(sessions, log=log)
    w, ts = ctx["w"], ctx["ts"]
    sp, lp = strategy.params(), longmai.params()
    index = list(w["close"].index)
    scored, v7 = [], []
    for t in range(start, len(index)):
        day = index[t]
        pool = strategy.eligible(w, ctx["basic"], ctx["calendar"], t, sp)
        f = features(w, ts, t)
        base = pool & (f["amount5"] >= 0.5e8).reindex(pool.index).fillna(False)  # superset for the neighbours
        keep = f[base.reindex(f.index).fillna(False).astype(bool)].copy()
        keep["high_vol"] = high_vol(ctx["severe"], ctx["severe"]["events10"], t, keep.index).to_numpy()
        scored.append(keep.rename_axis("code").reset_index().assign(obs=day))
        if t + 1 < len(index):  # V7 list for the same observation day (candidate + ladder, plus flagged rows)
            d = longmai.decide(ctx, t, index[t + 1], lp, None, pool)
            listed = set()
            for code, s in d["leaders"].iterrows():
                v7.append(dict(obs=day, code=code, group="vetoed" if s["vetoes"] else "crowded" if s.get("crowded") else "candidate"))
                listed.add(code)
            h = w["height"].iloc[t]
            for code in h[(h >= 2) & pool.reindex(h.index).fillna(False)].index:
                if code not in listed:
                    v = longmai.vetoes(ctx, t, code, lp, None)
                    v7.append(dict(obs=day, code=code, group="vetoed" if v else "crowded" if longmai.crowded(ctx, t, code, lp) else "ladder"))
        if (t - start) % 100 == 0:
            log(f"  replay {day} ({t - start + 1}/{len(index) - start})")
    scored = pd.concat(scored, ignore_index=True)
    for c in scored.columns.difference(["obs", "code", "high_vol"]):
        scored[c] = scored[c].astype("float32")  # ~2M rows over four years: keep memory modest
    return scored, pd.DataFrame(v7), w


def select(scored: pd.DataFrame, top_n: int = 15, min_amount5: float = 1e8, use=FEATURES) -> pd.DataFrame:
    """V8 picks per observation day from the replay frame (re-ranking within the chosen pool)."""
    out = []
    for day, g in scored.groupby("obs", sort=True):
        pool = g.set_index("code")["amount5"] >= min_amount5
        s = score(g.set_index("code"), pool, use).dropna()
        out.append(pd.DataFrame({"obs": day, "code": s.nlargest(top_n).index, "score": s.nlargest(top_n).to_numpy()}))
    return pd.concat(out, ignore_index=True)


def evaluate(scored: pd.DataFrame, v7: pd.DataFrame, w: dict, p: dict | None = None) -> dict:
    """The pre-registered comparison per segment, plus the robustness neighbours (reported, not used to pick)."""
    p = p or PARAMS
    out = outcomes(w, p)
    labels = ["win10", "loss10", "max10", "min5", "ret10"]

    def attach(frame):
        frame = frame.copy()
        for k in labels + ["max20"]:
            stacked = out[k].stack()
            stacked.index.names = ["obs", "code"]
            frame = frame.join(stacked.rename(k), on=["obs", "code"])
        frame["seg"] = frame["obs"].map(segment)
        return frame

    eligible = attach(scored[scored["amount5"] >= p["min_amount5"]][["obs", "code", "high_vol"]])
    winners = eligible[eligible["max20"] >= p["swing_winner"]][["obs", "code", "seg"]]

    def stats(frame, name):
        g = frame.groupby("seg")
        t = g[labels].mean()
        t["n"], t["per_day"] = g.size(), g.size() / g["obs"].nunique()
        listed = winners.merge(frame[["obs", "code"]].drop_duplicates(), on=["obs", "code"], how="left", indicator=True)
        t["recall20"] = listed.assign(hit=listed["_merge"].eq("both")).groupby("seg")["hit"].mean()
        return t.assign(group=name).reset_index().set_index(["group", "seg"])

    variants = {"V8": dict(), "V8 前10": dict(top_n=10), "V8 前20": dict(top_n=20),
                "V8 去theme_z": dict(use=tuple(f for f in FEATURES if f != "theme_z")),
                "V8 去lu20": dict(use=tuple(f for f in FEATURES if f != "lu20")),
                "V8 门槛0.5亿": dict(min_amount5=0.5e8), "V8 门槛2亿": dict(min_amount5=2e8)}
    tables, picks_main = [], None
    for name, kw in variants.items():
        picks = attach(select(scored, **{"top_n": p["top_n"], "min_amount5": p["min_amount5"], **kw}))
        if name == "V8":
            picks_main = picks.merge(eligible[["obs", "code", "high_vol"]], on=["obs", "code"], how="left")
        tables.append(stats(picks, name))
    tables.append(stats(eligible, "全部合格股"))
    v7a = attach(v7)
    tables.append(stats(v7a[v7a["group"].isin(["candidate", "ladder"])], "V7 候选+梯队"))
    tables.append(stats(v7a, "V7 全部列出"))
    hv = picks_main[picks_main["high_vol"].fillna(False).astype(bool)]
    if len(hv):
        tables.append(stats(hv, "V8 其中高波动"))
    table = pd.concat(tables).round(3)
    verdict = {}
    for seg in ("train", "test1", "test2"):
        try:
            v8, base, v7c = table.loc[("V8", seg)], table.loc[("全部合格股", seg)], table.loc[("V7 候选+梯队", seg)]
            v7all = table.loc[("V7 全部列出", seg)]
        except KeyError:
            verdict[seg] = None
            continue
        verdict[seg] = dict(win_vs_base=bool(v8["win10"] >= base["win10"] + 0.05), win_vs_v7=bool(v8["win10"] > v7c["win10"]),
                            loss_vs_v7=bool(v8["loss10"] <= v7c["loss10"]), recall_vs_v7=bool(v8["recall20"] > v7all["recall20"]))
    passed = all(v is not None and all(v.values()) for v in verdict.values())
    return dict(table=table, verdict=verdict, passed=passed, picks=picks_main)
