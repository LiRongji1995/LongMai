"""Rule research with honest out-of-sample statistics.

Builds one dataset row per (observation day, eligible main-board stock) with every feature known
before the target-session open (plus the 09:25 auction gap, flagged as auction-time information)
and outcome labels. Rules are pandas query expressions evaluated on that dataset.

Statistics are computed per DAY (equal-weight basket of the day's matches), because stocks on the
same day are strongly correlated; t-stats use the number of days. Time is split into three
consecutive segments (default 60/20/20): `train`, `test1`, `test2`. A rule is "OOS-valid" only if
both test segments are positive with t >= 1.5 each and at least 20 days per test segment.

Columns available in rules (selection):
  ret1 ret3 ret5 ret10 ret20 rs1 rs5 amount turn A clv float_cap board_height limit_up one_price
  STRONG_START STRONG_CONTINUATION FIRST_PULLBACK r1_match r2_selected near_miss
  regime m_limit_up m_limit_down m_broken_rate m_max_height m_yesterday_limit_up_avg_ret m_promotion_rate
  gap (09:25 auction info)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import backtest, outcomes, store, strategy

MOOD_FIELDS = ("limit_up", "limit_down", "broken_rate", "max_height", "yesterday_limit_up_avg_ret",
               "promotion_rate", "up_ratio", "median_pct")
PRESETS = {
    "r2": "r2_selected",
    "r2_hot": "r2_selected and regime == 'HOT'",
    "r2_not_hot": "r2_selected and regime != 'HOT'",
    "cold_all": "regime == 'COLD'",
    "cold_flat": "regime == 'COLD' and 0 <= ret1 <= 0.03",
    "cold_liquid": "regime == 'COLD' and 3e8 <= amount <= 3e9",
    "first_board": "limit_up and board_height == 1",
    "second_board": "limit_up and board_height == 2",
}


def dataset(refresh: bool = False, log=print) -> pd.DataFrame:
    stored = sorted(p.stem for p in (store.home() / "bars").glob("*.parquet"))
    path = store.home() / "research" / f"dataset-{stored[0]}-{stored[-1]}-{strategy.STRATEGY_VERSION}.parquet"
    if path.exists() and not refresh:
        return pd.read_parquet(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    days = backtest.prepare(log=log)
    p = strategy.params()
    frames = []
    for d in days:
        f = strategy.evaluate(d["feat"], p)
        f = f[f["eligible"]].join(d["label"])
        f["obs"], f["target"], f["regime"] = d["obs"], d["target"], d["regime"]
        for k in MOOD_FIELDS:
            f["m_" + k] = d["mood"][k]
        frames.append(f.rename_axis("code").reset_index())
    data = pd.concat(frames, ignore_index=True)
    for c in data.columns:
        if data[c].dtype == object and c not in ("code", "obs", "target", "regime", "stages", "near_miss", "industry"):
            data[c] = pd.to_numeric(data[c], errors="coerce")
    data.to_parquet(path, index=False)
    log(f"  dataset {len(data)} rows saved to {path.name}")
    return data


def segments(data: pd.DataFrame, split=(0.6, 0.8)) -> pd.Series:
    days = sorted(data["obs"].unique())
    c1, c2 = days[int(len(days) * split[0])], days[int(len(days) * split[1])]
    return pd.Series(np.select([data["obs"] < c1, data["obs"] < c2], ["train", "test1"], "test2"),
                     index=data.index), (c1, c2)


def evaluate(data: pd.DataFrame, rule: str, ret: str = "r_n1c", cost: float = outcomes.DEFAULT_COST,
             seg: pd.Series | None = None) -> dict:
    seg = seg if seg is not None else segments(data)[0]
    mask = data.eval(rule).fillna(False).astype(bool) & ~data["unbuyable"] & data[ret].notna()
    sub = data[mask]
    daily = (sub[ret] - cost).groupby([seg[mask], sub["obs"]]).mean()
    out = dict(rule=rule, exit=ret, picks_per_day=round(len(sub) / max(data["obs"].nunique(), 1), 2))
    valid = True
    for name in ("train", "test1", "test2"):
        x = daily[name] if name in daily.index.get_level_values(0) else pd.Series(dtype=float)
        t = x.mean() / x.std(ddof=1) * np.sqrt(len(x)) if len(x) > 2 and x.std(ddof=1) > 0 else np.nan
        out[f"{name}_days"], out[f"{name}_avg"], out[f"{name}_t"] = len(x), x.mean() if len(x) else np.nan, t
        if name != "train":
            valid &= len(x) >= 20 and pd.notna(t) and t >= 1.5 and x.mean() > 0
    out["oos_valid"] = bool(valid)
    return out


def scan(data: pd.DataFrame, ret: str = "r_n1c", cost: float = outcomes.DEFAULT_COST, min_days: int = 20) -> pd.DataFrame:
    """Single and pairwise bucket rules; reports only those passing the OOS criterion."""
    buckets = {
        "board_height": [(0, 0), (1, 1), (2, 2), (3, 99)],
        "ret1": [(-1, -0.05), (-0.05, 0), (0, 0.03), (0.03, 0.07), (0.07, 0.095), (0.095, 1)],
        "gap": [(-1, -0.03), (-0.03, 0), (0, 0.03), (0.03, 0.07), (0.07, 1)],
        "amount": [(0, 3e8), (3e8, 1e9), (1e9, 3e9), (3e9, 1e13)],
        "turn": [(0, 3), (3, 8), (8, 15), (15, 100)],
        "m_limit_up": [(0, 30), (30, 60), (60, 100), (100, 10000)],
    }
    atoms = [f"{c} >= {lo} and {c} < {hi}" if c != "board_height" else f"board_height >= {lo} and board_height <= {hi}"
             for c, ranges in buckets.items() for lo, hi in ranges]
    atoms += [f"regime == '{r}'" for r in ("COLD", "NEUTRAL", "WARM", "HOT")] + ["r2_selected"]
    seg = segments(data)[0]
    rules = atoms + [f"({a}) and ({b})" for i, a in enumerate(atoms) for b in atoms[i + 1:]
                     if a.split()[0] != b.split()[0]]
    rows = [evaluate(data, r, ret, cost, seg) for r in rules]
    table = pd.DataFrame(rows)
    table = table[(table["test1_days"] >= min_days) & (table["test2_days"] >= min_days)]
    return table.sort_values("oos_valid", ascending=False).sort_values(["oos_valid", "test2_avg"], ascending=False)
