"""龙脉 V11 candidate: late-stage mainlines led by stocks below the annual line (docs/STRATEGY_LONGMAI_V11_PREREG.md).

The user's experience: when a theme has run for a while and its leader is a low-position catch-up stock below the
250-session moving average (年线), the mainline is near its end. Two questions, judged separately:

Q1 (mainline level), one row per (observation t, mainline board):
  old    board was a mainline on >= cut of the last AGE_WINDOW sessions (cut = 2/3 quantile of train board-days)
  below  the board's 龙1 (rank 1 by board height, turnover, as `mainline.signals`) closes below its MA250
  fade3  the board's limit-ups over t+1..t+3 <= half of its 3-session limit-ups at t     (primary)
  still3 the board is still a mainline at t+3; 龙1's lu1 / bigloss1                       (description)
  Holds if, among old boards, fade3 is higher when 龙1 is below the annual line, by >= 10pp in train and by
  >= half that in both tests.
Q2 (stock level), one row per (t, mainline leader) of the replayed V9 list (空间板 rows kept, never flagged):
  flag   old board and the leader itself below its MA250
  Holds like V10 (lu1 lower by >= 5pp in train, >= half that in both tests); if it holds, moving flagged leaders
  out of 主线龙头 must pass the user's group rule (quality.GROUP_RULES) in all three segments.
Inputs use bars up to t's close only; MA250 needs 250 stored sessions (NaN = unknown, never "no").
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import auction, evaluate, longmai, mainline, quality, store, strategy

VERSION = "SF_LONGMAI_V11"  # candidate; the live list stays V9 unless the exam adopts a change
SEGS = auction.SEGS
AGE_WINDOW = 20
AGE_QUANT = 2 / 3
NEIGHBOUR_QUANT = (0.5, 0.75)      # reported, never chosen
DRAWDOWNS = (0.3, 0.4, 0.5)        # alternative "low position": close >= x below the 250-session high; reported only
RULE_Q1 = dict(min_train=60, min_test=20, train_gap=0.10, test_share=0.5)
RULE_Q2 = dict(min_train=100, min_test=30, train_gap=0.05, test_share=0.5)
GROUP_RULE = "A"  # chosen by the user on 2026-11-12, before the exam ran


def positions(w: dict) -> dict[str, pd.DataFrame]:
    """below: adj close < MA250 (1/0, NaN without 250 sessions); dd: adj close vs the 250-session high - 1."""
    adj = w["adj"]
    ma = adj.rolling(250, min_periods=250).mean()
    hi = adj.rolling(250, min_periods=250).max()
    below = (adj < ma).astype(float).where(ma.notna() & adj.notna())
    return dict(below=below, dd=(adj / hi - 1).where(hi.notna()))


def leader1(w: dict, aw: dict, ts: dict, pool: pd.Series, t: int, board: str) -> str | None:
    """The board's rank-1 leader as in `mainline.signals` (strong eligible member, by height then turnover),
    computed per board so that a stock leading two boards (or the 空间板) does not hide it."""
    c, pc, hi, lo = (w[k].iloc[t] for k in ("close", "preclose", "high", "low"))
    rng = hi - lo
    clv = ((c - lo) / rng).where(rng > 0, 1.0)
    strong = w["limit_up"].iloc[t].fillna(False).astype(bool) | ((c / pc - 1 >= 0.05) & (clv >= 0.7))
    cols = list(ts["cols"])
    members = pd.Index([cols[j] for j in np.flatnonzero(ts["M"][ts["names"].index(board)])]).intersection(w["close"].columns)
    ok = pool.reindex(members).fillna(False).to_numpy(bool) & strong.reindex(members).fillna(False).to_numpy(bool)
    members = members[ok]
    if not len(members):
        return None
    cand = pd.DataFrame({"height": w["height"].iloc[t].reindex(members), "act": aw["turn"].iloc[t].reindex(members)})
    return str(cand.sort_values(["height", "act"], ascending=False).index[0])


def replay(sessions: list[str], start: int = 60, log=print) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """(board-day rows, leader rows, panel w) over the stored history."""
    ctx = longmai.context(sessions, log=log)
    w, aw, ts = ctx["w"], ctx["aw"], ctx["ts"]
    sp, lp = strategy.params(), longmai.params()
    index = list(w["close"].index)
    lines = {t: mainline.mainlines(ts, t, lp) for t in range(max(0, start - AGE_WINDOW + 1), len(index))}
    boards, leaders = [], []
    for t in range(start, len(index) - 1):
        pool = strategy.eligible(w, ctx["basic"], ctx["calendar"], t, sp)
        for board in lines[t]:
            age = sum(board in lines[k] for k in range(t - AGE_WINDOW + 1, t + 1))
            still = float(board in lines[t + 3]) if t + 3 < len(index) else np.nan
            boards.append(dict(obs=index[t], day=index[t + 1], board=board, age=age, still3=still,
                               leader1=leader1(w, aw, ts, pool, t, board)))
        sig = longmai.decide(ctx, t, index[t + 1], lp, None, pool)["leaders"]
        for code, r in sig.iterrows():
            leaders.append(dict(obs=index[t], day=index[t + 1], code=code, board=r["board"],
                                rank=int(r["leader_rank"])))
        if (t - start) % 100 == 0:
            log(f"  replay {index[t]} ({t - start + 1}/{len(index) - 1 - start})")
    return pd.DataFrame(boards), pd.DataFrame(leaders), dict(w=w, ts=ts)


def _join(frame: pd.DataFrame, wide: pd.DataFrame, name: str, left=("obs", "code")) -> pd.DataFrame:
    s = wide.stack(future_stack=True)
    s.index.names = ["obs", "code"]
    return frame.join(s.rename(name), on=list(left))


def build(boards: pd.DataFrame, leaders: pd.DataFrame, w: dict, ts: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    pos = positions(w)
    out = evaluate.stock_outcomes(w)
    lu, lu3 = ts["lu"], ts["lu3"]
    fwd = lu.shift(-1) + lu.shift(-2) + lu.shift(-3)  # NaN while t+1..t+3 are not all stored
    fade = (fwd <= 0.5 * lu3).astype(float).where(fwd.notna() & lu3.notna())
    b = boards.copy()
    fs = fade.stack(future_stack=True)
    fs.index.names = ["obs", "board"]
    b = b.join(fs.rename("fade3"), on=["obs", "board"])
    b["code"] = b["leader1"]
    for k in ("below", "dd"):
        b = _join(b, pos[k], k)
    for k in ("lu1", "bigloss1"):
        b = _join(b, out[k], f"l1_{k}")
    b = b.drop(columns="code")
    b["seg"] = b["day"].map(auction.segment)
    ages = b.set_index(["obs", "board"])["age"]
    ages = ages[~ages.index.duplicated()]
    l = leaders.copy()
    l = l.join(ages.rename("age"), on=["obs", "board"])  # 空间板 rows: no age (never flagged)
    for k in ("below", "dd"):
        l = _join(l, pos[k], k)
    for k in ("lu1", "out5", "bigloss1", "max3"):
        l = _join(l, out[k], k)
    l["seg"] = l["day"].map(auction.segment)
    return b, l


def age_cut(b: pd.DataFrame, q: float) -> float:
    return float(b.loc[b["seg"] == "train", "age"].quantile(q))


def low_flag(frame: pd.DataFrame, dd: float | None = None) -> pd.Series:
    """1/0/NaN: below MA250, or (neighbour) >= dd below the 250-session high."""
    if dd is None:
        return frame["below"]
    return (frame["dd"] <= -dd).astype(float).where(frame["dd"].notna())


def _gap_verdict(yes: dict, no: dict, gap_sign: int, rule: dict, what: str) -> dict:
    """gap = sign x (yes - no) per segment; holds if >= train_gap in train and >= half that in both tests."""
    if yes["train"]["n"] < rule["min_train"] or min(yes[s]["n"] for s in ("test1", "test2")) < rule["min_test"] \
            or min(no[s]["n"] for s in SEGS) < rule["min_test"]:
        return dict(holds=False, reason="样本不足")
    g = {s: gap_sign * (yes[s]["m"] - no[s]["m"]) for s in SEGS}
    if not g["train"] >= rule["train_gap"]:
        return dict(holds=False, reason=f"train {what} 差 {g['train'] * 100:+.1f} 个百分点（需 ≥ {rule['train_gap'] * 100:.0f}）")
    for s in ("test1", "test2"):
        if not g[s] >= rule["test_share"] * g["train"]:
            return dict(holds=False, reason=f"{s} 未复现（{g[s] * 100:+.1f}，需 ≥ {rule['test_share'] * g['train'] * 100:.1f}）")
    return dict(holds=True, reason="三段均满足")


def _split(frame: pd.DataFrame, flag: pd.Series, outcome: str) -> tuple[dict, dict]:
    yes, no = {}, {}
    for s in SEGS:
        part = frame[frame["seg"] == s]
        f = flag.reindex(part.index)
        for d, m in ((yes, f == 1.0), (no, f == 0.0)):
            x = part.loc[m, outcome].dropna()
            d[s] = dict(n=len(x), m=x.mean() if len(x) else np.nan)
    return yes, no


def q1(b: pd.DataFrame, q: float = AGE_QUANT, dd: float | None = None) -> dict:
    old = b[b["age"] >= age_cut(b, q)]
    yes, no = _split(old, low_flag(old, dd), "fade3")
    return dict(verdict=_gap_verdict(yes, no, +1, RULE_Q1, "fade3"), yes=yes, no=no)


def q2_flag(l: pd.DataFrame, cut: float, dd: float | None = None) -> pd.Series:
    """1 = old board and the leader is low; 0 = old board, not low; NaN = 空间板 / young board / unknown."""
    return low_flag(l, dd).where(l["age"] >= cut)


def q2(l: pd.DataFrame, cut: float, dd: float | None = None) -> dict:
    yes, no = _split(l, q2_flag(l, cut, dd), "lu1")
    return dict(verdict=_gap_verdict(yes, no, -1, RULE_Q2, "lu1"), yes=yes, no=no)


def compare(l: pd.DataFrame, flag: pd.Series) -> pd.DataFrame:
    """The leader group as in V9 versus V11 (flagged leaders moved out; unknown = kept)."""
    moved = flag.eq(1.0)
    rows = []
    for name, part in (("V9", l), ("V11", l[~moved])):
        for s in SEGS:
            x = part[part["seg"] == s]
            days = l.loc[l["seg"] == s, "obs"].nunique()
            rows.append(dict(version=name, seg=s, n=int(x["out5"].notna().sum()), per_day=len(x) / days if days else np.nan,
                             **{k: x[k].mean() for k in ("lu1", "out5", "bigloss1", "max3")}))
    return pd.DataFrame(rows).set_index(["version", "seg"])


def describe(b: pd.DataFrame, cut: float) -> pd.DataFrame:
    """Description only: fade3 / still3 / 龙1 outcomes by (old, below) and segment."""
    x = b.assign(old=np.where(b["age"] >= cut, "old", "young"),
                 low=b["below"].map({1.0: "below", 0.0: "above"}).fillna("unknown"))
    return x.groupby(["old", "low", "seg"])[["fade3", "still3", "l1_lu1", "l1_bigloss1"]].agg(["mean", "count"]).round(3)


def report(b: pd.DataFrame, l: pd.DataFrame, which: str | None = None) -> dict:
    which = which or GROUP_RULE
    if which not in quality.GROUP_RULES:
        raise ValueError("GROUP_RULE not set: the user picks A or B in the pre-registration before the exam")
    cut = age_cut(b, AGE_QUANT)
    r1 = q1(b)
    r2 = q2(l, cut)
    flag = q2_flag(l, cut)
    cmp = compare(l, flag)
    deltas = {s: (float(cmp.loc[("V11", s), "out5"] - cmp.loc[("V9", s), "out5"]),
                  float(cmp.loc[("V11", s), "bigloss1"] - cmp.loc[("V9", s), "bigloss1"])) for s in SEGS}
    passed2 = r2["verdict"]["holds"] and all(quality.group_accept(*deltas[s], which) for s in SEGS)
    neighbours = {}
    for q in NEIGHBOUR_QUANT:
        c = age_cut(b, q)
        neighbours[f"age@{q:.2f}(≥{c:g})"] = dict(q1=q1(b, q)["verdict"], q2=q2(l, c)["verdict"])
    for dd in DRAWDOWNS:
        neighbours[f"dd{int(dd * 100)}"] = dict(q1=q1(b, dd=dd)["verdict"], q2=q2(l, cut, dd)["verdict"])
    return dict(rule=which, cut=cut, q1=r1, q2=r2, compare=cmp, deltas=deltas, q1_passed=r1["verdict"]["holds"],
                q2_passed=bool(passed2), neighbours=neighbours, describe=describe(b, cut),
                first=str(b["obs"].min()), last=str(b["obs"].max()), boards=len(b), leaders=len(l))


def run(log=print):
    stored = sorted(x.stem for x in (store.home() / "bars").glob("*.parquet"))
    boards, leaders, env = replay(stored, log=log)
    b, l = build(boards, leaders, env["w"], env["ts"])
    return b, l, report(b, l)
