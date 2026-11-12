"""龙脉 V10 candidate: pre-registered exam of low-amount / one-price flags (docs/STRATEGY_LONGMAI_V10_PREREG.md).

Universe: every stock of the live V9 list replayed per observation session t, by role (`auction.replay`):
leader (主线龙头 + 高波动), ladder (连板梯队), observe (观察). Flags use bars up to t's close only (NaN = unknown):
  L1   amount_t < 1e8                     (neighbours 0.5e8, 2e8, 3e8: reported, never chosen)
  L2   one-price limit-up on t
  L3   low position: adj close_t / lowest adj close of t-60..t-1 <= 1.5   (neighbours 1.4, 1.6)
  L12  L1 and L2;  L13  L1 and L3
Outcomes from t's close (`evaluate.stock_outcomes`): lu1 (晋级), out5, bigloss1, max3. Primary outcome per role:
lu1 for leader / ladder, out5 for observe. The next session's auction facts (open at limit, big loss after the
open) are description only.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import auction, evaluate, store, strategy

VERSION = "SF_LONGMAI_V10"  # candidate; the live list stays V9 unless the exam adopts a change
SEGS = auction.SEGS
ROLES = auction.ROLES
PRIMARY = {"leader": "lu1", "ladder": "lu1", "observe": "out5"}
FLAGS = ("L1", "L2", "L3", "L12", "L13")
BASE = dict(L1=1e8, L3=1.5)
NEIGHBOURS = {"L1": (0.5e8, 2e8, 3e8), "L3": (1.4, 1.6)}
RULE = dict(min_train=100, min_test=30, train_gap=0.05, test_share=0.5)
# group-level acceptance; "A" = the user's default rule (AGENTS.md), "B" = its risk-reduction mirror
GROUP_RULES = {
    "A": dict(min_out5_gain=0.02, loss_share=0.5, max_loss_gain=0.02),
    "B": dict(min_loss_cut=0.02, out5_share=0.5),
}
GROUP_RULE = "B"  # chosen by the user on 2026-11-12, before the exam ran


def _name(flag: str, value) -> str:
    return flag if value == BASE[flag] else f"{flag}@{value:g}"


def flag_frames(w: dict) -> dict[str, pd.DataFrame]:
    """Session x code frames of every flag and neighbour (1.0 / 0.0 / NaN)."""
    traded = w["traded"].fillna(False).astype(bool)
    amt = w["amount"].where(traded)
    low60 = w["adj"].shift(1).rolling(60, min_periods=60).min()
    pos = w["adj"] / low60
    out = {}
    for c in (BASE["L1"],) + NEIGHBOURS["L1"]:
        out[_name("L1", c)] = (amt < c).astype(float).where(amt.notna())
    out["L2"] = w["one_price_up"].astype(float).where(traded)
    for c in (BASE["L3"],) + NEIGHBOURS["L3"]:
        out[_name("L3", c)] = (pos <= c).astype(float).where(pos.notna() & traded)
    out["L12"] = out["L1"] * out["L2"]  # NaN if either is unknown
    out["L13"] = out["L1"] * out["L3"]
    return out


def build(panel: pd.DataFrame, w: dict) -> pd.DataFrame:
    """An `auction.replay` panel with every flag joined on (obs, code)."""
    out = panel.copy()
    for name, frame in flag_frames(w).items():
        s = frame.stack(future_stack=True)
        s.index.names = ["obs", "code"]
        out = out.join(s.rename(name), on=["obs", "code"])
    s = evaluate.stock_outcomes(w)["max3"].stack()  # max3 from t's close (the replay panel only carries lu1/out5/bigloss1)
    s.index.names = ["obs", "code"]
    out = out.join(s.rename("max3"), on=["obs", "code"])
    return out.rename(columns={"c_lu1": "lu1", "c_out5": "out5", "c_bigloss1": "bigloss1"})


def split_stats(p: pd.DataFrame) -> pd.DataFrame:
    names = [c for c in p.columns if c.split("@")[0] in FLAGS]
    rows = []
    for role in ROLES:
        prim = PRIMARY[role]
        for name in names:
            for seg in SEGS:
                s = p[(p["role"] == role) & (p["seg"] == seg)]
                yes, no = s[s[name] == 1.0], s[s[name] == 0.0]
                row = dict(role=role, flag=name, seg=seg, unknown=int(s[name].isna().sum()),
                           n_yes=int(yes[prim].notna().sum()), n_no=int(no[prim].notna().sum()))
                for k in ("lu1", "out5", "bigloss1", "open_limit", "a_bigloss"):
                    row[f"{k}_yes"], row[f"{k}_no"] = yes[k].mean(), no[k].mean()
                    row[f"d_{k}"] = row[f"{k}_yes"] - row[f"{k}_no"]
                rows.append(row)
    return pd.DataFrame(rows).set_index(["role", "flag", "seg"])


def verdict(stats: pd.DataFrame, role: str, flag: str, rule: dict | None = None) -> dict:
    """A flag holds for a role if its yes-group's primary outcome is lower by >= 5pp in train and by >= half that
    in both tests, with enough samples."""
    rule = rule or RULE
    prim = PRIMARY[role]
    s = {seg: stats.loc[(role, flag, seg)] for seg in SEGS}
    if s["train"]["n_yes"] < rule["min_train"] or min(s[x]["n_yes"] for x in ("test1", "test2")) < rule["min_test"] \
            or min(s[x]["n_no"] for x in SEGS) < rule["min_test"]:
        return dict(holds=False, reason="样本不足")
    gap = -s["train"][f"d_{prim}"]
    if not gap >= rule["train_gap"]:
        return dict(holds=False, reason=f"train {prim} 差 {gap * 100:+.1f} 个百分点（需 ≥ 5）")
    for seg in ("test1", "test2"):
        g = -s[seg][f"d_{prim}"]
        if not g >= rule["test_share"] * gap:
            return dict(holds=False, reason=f"{seg} 未复现（{g * 100:+.1f}，需 ≥ {rule['test_share'] * gap * 100:.1f}）")
    return dict(holds=True, reason="三段均满足")


def group_accept(d_out5: float, d_loss: float, which: str) -> bool:
    r = GROUP_RULES[which]
    if which == "A":
        return bool(d_out5 >= r["min_out5_gain"] and d_loss <= min(r["loss_share"] * d_out5, r["max_loss_gain"]))
    return bool(d_loss <= -r["min_loss_cut"] and -d_out5 <= r["out5_share"] * -d_loss)


def compare(p: pd.DataFrame, role: str, flags: list[str]) -> pd.DataFrame:
    """One role's group as in V9 versus V10 (rows flagged by any holding flag moved out; unknown = kept)."""
    g = p[p["role"] == role]
    moved = g[flags].eq(1.0).any(axis=1) if flags else pd.Series(False, index=g.index)
    rows = []
    for name, part in (("V9", g), ("V10", g[~moved])):
        for seg in SEGS:
            s = part[part["seg"] == seg]
            days = g.loc[g["seg"] == seg, "obs"].nunique()
            rows.append(dict(version=name, seg=seg, n=int(s["out5"].notna().sum()),
                             per_day=len(s) / days if days else np.nan,
                             **{k: s[k].mean() for k in ("lu1", "out5", "bigloss1", "max3")}))
    return pd.DataFrame(rows).set_index(["version", "seg"])


def report(p: pd.DataFrame, which: str | None = None) -> dict:
    which = which or GROUP_RULE
    if which not in GROUP_RULES:
        raise ValueError("GROUP_RULE not set: the user picks A or B in the pre-registration before the exam")
    stats = split_stats(p)
    names = stats.index.get_level_values("flag").unique()
    res = {}
    for role in ROLES:
        verdicts = {f: verdict(stats, role, f) for f in FLAGS}
        neighbours = {n: verdict(stats, role, n) for n in names if "@" in n}
        holding = [f for f in FLAGS if verdicts[f]["holds"]]
        cmp = compare(p, role, holding)
        deltas = {seg: (float(cmp.loc[("V10", seg), "out5"] - cmp.loc[("V9", seg), "out5"]),
                        float(cmp.loc[("V10", seg), "bigloss1"] - cmp.loc[("V9", seg), "bigloss1"])) for seg in SEGS}
        passed = bool(holding) and all(group_accept(*deltas[seg], which) for seg in SEGS)
        res[role] = dict(verdicts=verdicts, neighbours=neighbours, holding=holding, compare=cmp, deltas=deltas,
                         passed=passed)
    return dict(stats=stats, roles=res, rule=which, first=str(p["obs"].min()), last=str(p["obs"].max()), rows=len(p))


def run(log=print) -> tuple[pd.DataFrame, dict]:
    stored = sorted(x.stem for x in (store.home() / "bars").glob("*.parquet"))
    panel = auction.replay(stored, log=log)
    w = strategy.panel(store.load_window(stored))
    p = build(panel, w)
    return p, report(p)
