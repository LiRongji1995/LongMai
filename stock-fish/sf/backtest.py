"""Historical replay of the pre-market list with walk-forward parameter checks.

For each observation session O in the store, compute features using rows <= O only, select with the
given parameters, then label outcomes on the next session T. Hot-money pool facts (seal times, LHB)
are not available historically, so replay ranks with bar-derived components only; this is stated in
the output.

Parameter grid: features are computed once per day; each parameter set only re-runs `evaluate`.
Walk-forward: sessions are split into consecutive folds; for each test fold the parameter set with the
best train expectancy (all prior folds) is chosen, and only its test-fold result is reported.
"""
from __future__ import annotations

import itertools
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import outcomes, ranking, store, strategy


def _industry_static() -> pd.Series:
    path = store.home() / "meta" / "industry.parquet"
    if not path.exists():
        return pd.Series(dtype=str)
    frame = pd.read_parquet(path).drop_duplicates("code").set_index("code")
    return frame["industry"].str.replace(r"^[A-Z]\d+", "", regex=True)


def prepare(start: str | None = None, end: str | None = None, log=print):
    """Load the store, build the panel, compute per-day features and regimes once."""
    sessions = store.calendar()
    stored = sorted(p.stem for p in (store.home() / "bars").glob("*.parquet"))
    w = strategy.panel(store.load_window(stored))
    index = list(w["close"].index)
    basic = store.stock_basic()
    p = strategy.params()
    industry = _industry_static()
    days = []
    started = time.monotonic()
    for t in range(strategy.MIN_HISTORY, len(index) - 1):
        obs = index[t]
        if (start and obs < start) or (end and obs > end):
            continue
        pool = strategy.eligible(w, basic, sessions, t, p)
        feat = strategy.features(w, t, pool, p)
        mood = strategy.sentiment(w, t, pool)
        feat["industry"] = industry.reindex(feat.index).fillna("")
        lu = feat["limit_up"].fillna(False).astype(bool) & feat["eligible"]
        feat["theme_heat"] = feat["industry"].map(feat["industry"][lu].value_counts()).fillna(0).astype(int)
        target = index[t + 1]
        label = outcomes.label(w, target, feat.index[feat["eligible"]])
        days.append(dict(obs=obs, target=target, feat=feat, label=label, regime=strategy.regime(mood)[0], mood=mood))
    log(f"  prepared {len(days)} sessions in {time.monotonic() - started:.1f}s")
    return days


def select(day: dict, p: dict) -> pd.DataFrame:
    f = strategy.evaluate(day["feat"], p)
    f = f[f["eligible"]]
    f["tier"] = ranking.tier(f)
    f["first_seal"] = None  # pool facts unknown historically
    f["breaks"] = np.nan
    f["seal_amount"] = np.nan
    f["lhb_times"] = np.nan
    f["score"], _ = ranking.score(f)
    f["cohort"] = np.where(f["r2_selected"], "selected", np.where(f["near_miss"] != "", "near_miss", "other"))
    cols = ["cohort", "tier", "score", "stages", "near_miss", "board_height", "ret1", "amount", "float_cap",
            "industry", "theme_heat"]
    out = f[cols].join(day["label"])
    out["obs"], out["target"], out["regime"] = day["obs"], day["target"], day["regime"]
    return out


def run(days: list[dict], p: dict) -> pd.DataFrame:
    return pd.concat([select(d, p) for d in days], ignore_index=False).rename_axis("code").reset_index()


def report(rows: pd.DataFrame, ret: str = outcomes.PRIMARY, cost: float = outcomes.DEFAULT_COST) -> dict:
    sel = rows[rows["cohort"] == "selected"].copy()
    sel["score_q"] = pd.qcut(sel["score"].rank(method="first"), 5, labels=["Q1低", "Q2", "Q3", "Q4", "Q5高"]) if len(sel) >= 5 else "NA"
    sel["gap_bucket"] = pd.cut(sel["gap"], [-1, -0.03, 0, 0.03, 0.07, 1], labels=["<-3%", "-3~0%", "0~3%", "3~7%", ">7%"])
    near = rows[rows["cohort"] == "near_miss"].copy()
    near["gate"] = near["near_miss"]
    curve = outcomes.equity(sel, ret, cost)
    return dict(
        cohorts=outcomes.grouped(rows, "cohort", ret, cost),
        tier=outcomes.grouped(sel, "tier", ret, cost),
        stage=outcomes.grouped(sel, "stages", ret, cost),
        regime=outcomes.grouped(sel, "regime", ret, cost),
        score_q=outcomes.grouped(sel, "score_q", ret, cost),
        gap=outcomes.grouped(sel, "gap_bucket", ret, cost),
        near_miss_gate=outcomes.grouped(near, "gate", ret, cost).sort_values("n", ascending=False).head(15),
        equity=curve, equity_summary=outcomes.summary_line(curve),
    )


def parse_grid(specs: list[str]) -> list[dict]:
    """['STRONG_START.ret1_min=0.05,0.06,0.07', ...] -> list of override dicts (cartesian product)."""
    axes = []
    for spec in specs:
        key, values = spec.split("=", 1)
        section, name = key.split(".", 1)
        axes.append([(section, name, float(v)) for v in values.split(",")])
    combos = []
    for combo in itertools.product(*axes) if axes else [()]:
        override = {}
        for section, name, value in combo:
            override.setdefault(section, {})[name] = value
        combos.append(override)
    return combos


def label_of(override: dict) -> str:
    return ";".join(f"{s}.{k}={v}" for s, d in override.items() for k, v in d.items()) or "default"


def grid(days: list[dict], overrides: list[dict], ret=outcomes.PRIMARY, cost=outcomes.DEFAULT_COST) -> pd.DataFrame:
    rows = []
    for override in overrides:
        sel = run(days, strategy.params(override))
        sel = sel[sel["cohort"] == "selected"]
        s = outcomes.stats(sel, ret, cost)
        s.update(outcomes.summary_line(outcomes.equity(sel, ret, cost)))
        rows.append({"params": label_of(override), **s})
    return pd.DataFrame(rows).set_index("params").sort_values("avg", ascending=False)


def walk_forward(days: list[dict], overrides: list[dict], folds: int = 4, ret=outcomes.PRIMARY,
                 cost=outcomes.DEFAULT_COST) -> pd.DataFrame:
    chunks = np.array_split(np.arange(len(days)), folds + 1)
    cache = {label_of(o): run(days, strategy.params(o)) for o in overrides}
    out = []
    for k in range(1, folds + 1):
        train_days = {days[i]["obs"] for c in chunks[:k] for i in c}
        test_days = {days[i]["obs"] for i in chunks[k]}
        best, best_avg = None, -np.inf
        for name, rows in cache.items():
            train = rows[(rows["cohort"] == "selected") & rows["obs"].isin(train_days)]
            avg = outcomes.stats(train, ret, cost)["avg"]
            if pd.notna(avg) and avg > best_avg:
                best, best_avg = name, avg
        rows = cache[best] if best else cache["default"]
        test = rows[(rows["cohort"] == "selected") & rows["obs"].isin(test_days)]
        default_test = cache["default"]
        default_test = default_test[(default_test["cohort"] == "selected") & default_test["obs"].isin(test_days)] \
            if "default" in cache else test.iloc[0:0]
        out.append(dict(fold=k, test_from=min(test_days), test_to=max(test_days), chosen=best,
                        train_avg=round(best_avg, 4), test_avg=outcomes.stats(test, ret, cost)["avg"],
                        test_hit=outcomes.stats(test, ret, cost)["hit_rate"],
                        default_test_avg=outcomes.stats(default_test, ret, cost)["avg"]))
    return pd.DataFrame(out).set_index("fold")


def save(name: str, tables: dict, meta: dict) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = store.home() / "backtests" / f"{stamp}-{name}"
    out.mkdir(parents=True, exist_ok=False)
    for key, table in tables.items():
        if isinstance(table, pd.DataFrame) and not table.empty:
            table.to_csv(out / f"{key}.csv", encoding="utf-8-sig")
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str))
    return out
