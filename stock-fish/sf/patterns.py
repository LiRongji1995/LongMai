"""龙脉 V9 (`SF_LONGMAI_V9`): pre-registered split of the three V7 stage patterns by ten fixed daily-bar flags.
Pre-registration: docs/STRATEGY_LONGMAI_V9_PREREG.md (written and confirmed before this code ran on any data).
The exam replays the V7 stage rules (`strategy.V7_OVERRIDES`). Its verdict was negative; the user adopted V9
as the live list anyway on 2026-11-11 (docs/STRATEGY_LONGMAI_V9.md).

Per observation session t (from the 61st stored session, the last one is not replayed):
- rows: every R2-selected stock (V7 eligibility + a stage match + turnover gate, not limit-down) with its stage
  matches, its V7 group (leader / ladder / observe) and every flag; flags are NaN when an input is missing
- outcomes: lu1, max3, out5, bigloss1 from `evaluate.stock_outcomes` (entry-free, NaN = pending)
Flags only use bars up to t's close.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import evaluate, longmai, store, strategy

VERSION = "SF_LONGMAI_V9"
STAGES = strategy.STAGES
CUTS = ("2025-02-06", "2025-12-17")  # train / test1 / test2, as `sf evaluate`
OUTCOMES = ("lu1", "max3", "out5", "bigloss1")

# flag: (stage, primary outcome, expected sign of yes - no). "good" flags must be met, "bad" flags exclude.
FLAGS = {
    "P1": ("FIRST_PULLBACK", "out5", +1),       # shrinking volume: amount_t <= 0.8 x mean of the 3 up days
    "P2": ("FIRST_PULLBACK", "out5", -1),       # intraday big loss: low_t <= preclose_t x 0.93
    "P3": ("FIRST_PULLBACK", "out5", +1),       # a limit-up close among the 3 up days
    "P4": ("FIRST_PULLBACK", "out5", +1),       # low_t holds MA5
    "S1": ("STRONG_START", "out5", +1),         # close above the highest close of the prior 20 sessions
    "S2": ("STRONG_START", "out5", +1),         # limit-up close, not one-price
    "S3": ("STRONG_START", "out5", +1),         # low position: close / prior-60 lowest close <= 1.3
    "C1": ("STRONG_CONTINUATION", "bigloss1", +1),  # turnover_t >= 2 x prior-5 mean
    "C2": ("STRONG_CONTINUATION", "bigloss1", +1),  # 5-session return >= 40%
    "C3": ("STRONG_CONTINUATION", "out5", +1),  # amount_t >= 200m
}
BASE = dict(P1=0.8, S1=20, S3=1.3, C2=0.40, C3=2e8)
NEIGHBOURS = {"P1": (0.7, 0.9), "S1": (10, 60), "S3": (1.2, 1.5), "C2": (0.30, 0.50), "C3": (1e8, 3e8)}
RULE = dict(min_train=200, min_test=50, train_gap=0.05, secondary=0.02, test_share=0.5)


def segment(day: str, cuts=CUTS) -> str:
    return "train" if day < cuts[0] else "test1" if day < cuts[1] else "test2"


def is_good(flag: str) -> bool:
    _, outcome, sign = FLAGS[flag]
    return outcome == "out5" and sign > 0


# ------------------------------------------------------------------ flags

def flags(w: dict, t: int) -> pd.DataFrame:
    """All flags (and the robustness neighbours, named like `P1@0.7`) for every code at session t.
    1.0 = yes, 0.0 = no, NaN = unknown (an input bar is missing or the stock did not trade)."""
    adj, present = w["adj"], w["close"].notna()
    traded = w["traded"].fillna(False).astype(bool)
    amt = w["amount"].where(traded)
    turn = w["turn"].where(traded)
    ok_t = traded.iloc[t]

    def flag(cond: pd.Series, valid: pd.Series) -> pd.Series:
        return cond.astype(float).where(valid & ok_t)

    def complete(a: int, b: int) -> pd.Series:  # sessions t-a .. t-b all present
        return present.iloc[t - a:t - b + 1].all()

    a_t, prev3 = amt.iloc[t], amt.iloc[t - 3:t].mean(skipna=False)
    low_adj = w["low"].iloc[t] * adj.iloc[t] / w["close"].iloc[t]
    ma5 = adj.iloc[t - 4:t + 1].mean(skipna=False)
    lu = w["limit_up"].fillna(False).astype(bool)
    ret5 = adj.iloc[t] / adj.iloc[t - 5] - 1
    turn5 = turn.iloc[t - 5:t].mean(skipna=False)
    out = {}
    for c in (BASE["P1"],) + NEIGHBOURS["P1"]:
        out[_name("P1", c)] = flag(a_t <= c * prev3, a_t.notna() & prev3.notna())
    out["P2"] = flag(w["low"].iloc[t] <= w["preclose"].iloc[t] * 0.93, w["low"].iloc[t].notna())
    out["P3"] = flag(lu.iloc[t - 3:t].any(), complete(3, 1))
    out["P4"] = flag(low_adj >= ma5, low_adj.notna() & ma5.notna())
    for k in (BASE["S1"],) + NEIGHBOURS["S1"]:
        prior = adj.iloc[t - k:t].max(skipna=False)
        out[_name("S1", k)] = flag(adj.iloc[t] > prior, prior.notna())
    out["S2"] = flag(lu.iloc[t] & ~w["one_price_up"].iloc[t].fillna(False).astype(bool), present.iloc[t])
    low60 = adj.iloc[t - 60:t].min(skipna=False)
    for c in (BASE["S3"],) + NEIGHBOURS["S3"]:
        out[_name("S3", c)] = flag(adj.iloc[t] / low60 <= c, low60.notna())
    out["C1"] = flag(turn.iloc[t] >= 2 * turn5, turn.iloc[t].notna() & turn5.notna())
    for c in (BASE["C2"],) + NEIGHBOURS["C2"]:
        out[_name("C2", c)] = flag(ret5 >= c, complete(5, 0))
    for c in (BASE["C3"],) + NEIGHBOURS["C3"]:
        out[_name("C3", c)] = flag(a_t >= c, a_t.notna())
    return pd.DataFrame(out)


def _name(flag: str, value) -> str:
    return flag if value == BASE[flag] else f"{flag}@{value:g}"


# ------------------------------------------------------------------ replay

def replay(sessions: list[str], start: int = 60, log=print) -> pd.DataFrame:
    """One row per (observation session, R2-selected stock) with stage matches, V7 group, flags and outcomes."""
    ctx = longmai.context(sessions, log=log)
    w = ctx["w"]
    sp, lp = strategy.params(strategy.V7_OVERRIDES), longmai.params()  # the exam split the V7 stages
    index = list(w["close"].index)
    rows = []
    for t in range(start, len(index) - 1):
        day = index[t]
        pool = strategy.eligible(w, ctx["basic"], ctx["calendar"], t, sp)
        facts = strategy.evaluate(strategy.features(w, t, pool, sp), sp)
        sel = facts[facts["r2_selected"]]
        if sel.empty:
            continue
        leaders = set(longmai.decide(ctx, t, index[t + 1], lp, None, pool)["leaders"].index)
        h = w["height"].iloc[t]
        ladder = set(h[(h >= 2) & pool.reindex(h.index).fillna(False)].index)
        f = flags(w, t).reindex(sel.index)
        part = sel[list(STAGES)].astype(bool).join(f)
        part["v7_group"] = ["leader" if c in leaders else "ladder" if c in ladder else "observe" for c in part.index]
        rows.append(part.rename_axis("code").reset_index().assign(obs=day))
        if (t - start) % 100 == 0:
            log(f"  replay {day} ({t - start + 1}/{len(index) - 1 - start})")
    panel = pd.concat(rows, ignore_index=True)
    out = evaluate.stock_outcomes(w)
    for k in OUTCOMES:
        stacked = out[k].stack()
        stacked.index.names = ["obs", "code"]
        panel = panel.join(stacked.rename(k), on=["obs", "code"])
    panel["seg"] = panel["obs"].map(segment)
    return panel


# ------------------------------------------------------------------ statistics and verdict

def split_stats(panel: pd.DataFrame, names=None) -> pd.DataFrame:
    """Per flag (and neighbour) and segment: yes / no means of the outcomes inside the flag's stage, sample
    sizes (resolved out5), unknown flags and the yes - no differences."""
    names = names or [c for c in panel.columns if c.split("@")[0] in FLAGS]
    rows = []
    for name in names:
        stage = FLAGS[name.split("@")[0]][0]
        g = panel[panel[stage]]
        for seg in ("train", "test1", "test2"):
            s = g[g["seg"] == seg]
            yes, no = s[s[name] == 1.0], s[s[name] == 0.0]
            row = dict(flag=name, stage=stage, seg=seg, unknown=int(s[name].isna().sum()),
                       n_yes=int(yes["out5"].notna().sum()), n_no=int(no["out5"].notna().sum()))
            for k in OUTCOMES:
                row[f"{k}_yes"], row[f"{k}_no"] = yes[k].mean(), no[k].mean()
                row[f"d_{k}"] = row[f"{k}_yes"] - row[f"{k}_no"]
            rows.append(row)
    return pd.DataFrame(rows).set_index(["flag", "seg"])


def verdict(stats: pd.DataFrame, flag: str, rule: dict | None = None) -> dict:
    """The written adoption rule for one flag (or neighbour): sample sizes, train gap, secondary guard,
    test replication. Returns the reasons and whether it is adopted."""
    rule = rule or RULE
    _, primary, sign = FLAGS[flag.split("@")[0]]
    secondary = "bigloss1" if primary == "out5" else "out5"
    # direction of the secondary that counts as "worse": yes - no above +2pp, except P2 (below -2pp)
    worse = -1 if (primary == "out5" and sign < 0) else +1
    s = {seg: stats.loc[(flag, seg)] for seg in ("train", "test1", "test2")}
    if min(s["train"]["n_yes"], s["train"]["n_no"]) < rule["min_train"] or \
            min(min(s[x]["n_yes"], s[x]["n_no"]) for x in ("test1", "test2")) < rule["min_test"]:
        return dict(adopted=False, reason="样本不足")
    gap = sign * s["train"][f"d_{primary}"]
    if not gap >= rule["train_gap"]:
        return dict(adopted=False, reason=f"train 差 {gap * 100:+.1f} 个百分点（需 ≥ 5）")
    if worse * s["train"][f"d_{secondary}"] > rule["secondary"]:
        return dict(adopted=False, reason=f"train {secondary} 反向恶化 {s['train'][f'd_{secondary}'] * 100:+.1f} 个百分点")
    for seg in ("test1", "test2"):
        g = sign * s[seg][f"d_{primary}"]
        if not g >= rule["test_share"] * gap:
            return dict(adopted=False, reason=f"{seg} 未复现（{g * 100:+.1f}，需 ≥ {rule['test_share'] * gap * 100:.1f}）")
    return dict(adopted=True, reason="三段均满足")


def v9_stages(panel: pd.DataFrame, adopted: list[str]) -> pd.DataFrame:
    """Stage matches after the adopted flags: good flags must be met (unknown = not met), bad flags exclude
    (unknown = kept)."""
    out = panel[list(STAGES)].astype(bool).copy()
    for flag in adopted:
        stage = FLAGS[flag][0]
        if is_good(flag):
            out[stage] &= panel[flag].eq(1.0)
        else:
            out[stage] &= ~panel[flag].eq(1.0)
    return out


def observe_compare(panel: pd.DataFrame, adopted: list[str]) -> pd.DataFrame:
    """The observe group replayed under V7 and under V9 (adopted flags), per segment."""
    obs = panel[panel["v7_group"] == "observe"]
    v9 = v9_stages(obs, adopted).any(axis=1)
    rows = []
    for name, part in (("V7", obs), ("V9", obs[v9])):
        for seg in ("train", "test1", "test2"):
            s = part[part["seg"] == seg]
            days = obs.loc[obs["seg"] == seg, "obs"].nunique()
            rows.append(dict(version=name, seg=seg, n=int(s["out5"].notna().sum()),
                             per_day=len(s) / days if days else np.nan,
                             **{k: s[k].mean() for k in OUTCOMES}))
    return pd.DataFrame(rows).set_index(["version", "seg"])


def report(panel: pd.DataFrame) -> dict:
    stats = split_stats(panel)
    base = list(FLAGS)
    verdicts = {f: verdict(stats, f) for f in base}
    neighbours = {n: verdict(stats, n) for n in stats.index.get_level_values(0).unique() if "@" in n}
    adopted = [f for f in base if verdicts[f]["adopted"]]
    compare = observe_compare(panel, adopted)
    passed = bool(adopted) and all(
        compare.loc[("V9", seg), "out5"] >= compare.loc[("V7", seg), "out5"]
        and compare.loc[("V9", seg), "bigloss1"] <= compare.loc[("V7", seg), "bigloss1"]
        for seg in ("test1", "test2"))
    return dict(stats=stats, verdicts=verdicts, neighbours=neighbours, adopted=adopted, compare=compare,
                passed=passed, first=str(panel["obs"].min()), last=str(panel["obs"].max()), rows=len(panel))


def run(log=print) -> tuple[pd.DataFrame, dict]:
    stored = sorted(x.stem for x in (store.home() / "bars").glob("*.parquet"))
    panel = replay(stored, log=log)
    return panel, report(panel)
