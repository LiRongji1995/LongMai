"""Forward review of saved pre-market lists.

For every saved list (latest run per target session), label every eligible stock with the outcomes
the list would actually have produced (entry at the target-session open), then aggregate by cohort
(selected / near-miss / other), tier, stage, regime and score quintile. Pending outcomes (future
sessions not yet in the store) are counted separately, never as zero.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import outcomes, premarket, store, strategy


def collect(since: str | None = None) -> pd.DataFrame:
    runs = premarket.all_runs()
    if since:
        runs = [r for r in runs if (r.parent.name if r.parent.name != "lists" else r.name) >= since]
    if not runs:
        return pd.DataFrame()
    stored = sorted(p.stem for p in (store.home() / "bars").glob("*.parquet"))
    w = strategy.panel(store.load_window(stored))
    frames = []
    for run in runs:
        summary = json.loads((run / "summary.json").read_text())
        facts = pd.read_parquet(run / "facts.parquet")
        facts = facts[facts["eligible"]] if "eligible" in facts else facts
        selected = facts.get("r2_selected", pd.Series(False, index=facts.index)).fillna(False).astype(bool)
        near = facts.get("near_miss", pd.Series("", index=facts.index)).fillna("")
        labels = outcomes.label(w, summary["target"], facts.index)
        if "lm_group" in facts:  # 龙脉 lists: leader / ladder / watch / avoid groups
            cohort = facts["lm_group"].map({"leader": "selected", "ladder": "ladder", "watch": "watch", "avoid": "avoid"}).fillna("other")
        else:
            cohort = np.where(selected, "selected", np.where(near != "", "near_miss", "other"))
        frame = pd.DataFrame({
            "cohort": cohort,
            "tier": facts.get("tier"), "score": facts.get("score"), "rank": facts.get("rank"),
            "stages": facts.get("stages"), "near_miss": near, "name": facts.get("name"),
            "board_height": facts.get("board_height"), "theme_heat": facts.get("theme_heat"),
        }, index=facts.index).join(labels)
        frame["obs"], frame["target"] = summary["observation"], summary["target"]
        frame["regime"] = summary.get("regime")
        frame["strategy_version"] = summary.get("strategy_version", "SF_R2_LEAN_V0")
        frames.append(frame.rename_axis("code").reset_index())
    return pd.concat(frames, ignore_index=True)


def report(rows: pd.DataFrame, ret: str = outcomes.PRIMARY, cost: float = outcomes.DEFAULT_COST) -> dict:
    mature = rows[rows["mature"] | rows[ret].notna()]
    pending = rows[~rows.index.isin(mature.index)]
    sel = mature[mature["cohort"] == "selected"].copy()
    if len(sel) >= 5 and sel["score"].notna().any():
        sel["score_q"] = pd.qcut(sel["score"].rank(method="first"), 5, labels=["Q1低", "Q2", "Q3", "Q4", "Q5高"])
    else:
        sel["score_q"] = "NA"
    per_day = []
    for target, part in sel.groupby("target"):
        s = outcomes.stats(part, ret, cost)
        per_day.append(dict(target=target, regime=part["regime"].iloc[0], **s))
    detail = sel.sort_values(["target", "rank"])[["target", "rank", "code", "name", "tier", "score", "gap",
                                                    "unbuyable", "r_oc", "r_n1o", "r_n1c", "r_n2c", "mfe2",
                                                    "mae2", "limit_up_T", "broken_T"]]
    curve = outcomes.equity(sel, ret, cost)
    return dict(
        pending_targets=sorted(pending["target"].unique().tolist()),
        cohorts=outcomes.grouped(mature, "cohort", ret, cost),
        tier=outcomes.grouped(sel, "tier", ret, cost),
        stage=outcomes.grouped(sel, "stages", ret, cost),
        regime=outcomes.grouped(sel, "regime", ret, cost),
        score_q=outcomes.grouped(sel, "score_q", ret, cost),
        per_day=pd.DataFrame(per_day).set_index("target") if per_day else pd.DataFrame(),
        detail=detail, equity=curve, equity_summary=outcomes.summary_line(curve),
    )


def save(result: dict, meta: dict) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = store.home() / "reviews" / stamp
    out.mkdir(parents=True, exist_ok=False)
    for key, table in result.items():
        if isinstance(table, pd.DataFrame) and not table.empty:
            table.to_csv(out / f"{key}.csv", encoding="utf-8-sig")
    (out / "meta.json").write_text(json.dumps(
        {**meta, "pending_targets": result["pending_targets"], "equity_summary": result["equity_summary"]},
        ensure_ascii=False, indent=2, default=str))
    return out
