"""Forward review of saved pre-market lists with the entry-free hit rates of `evaluate.py`.

For the latest run per target session, every stock in the saved fact table gets its outcomes from the
observation close (lu1, max3, out5, bigloss1; see evaluate.stock_outcomes), grouped by its list group
(leader / ladder / watch / avoid) against the baseline of all main-board limit-ups that day. Recall: of
the realized 妖股 (limit-up on the observation day, then >= 3 limit-ups within 5 sessions), the share
that were on the list. Outcomes not yet in the store are counted as pending, never as misses.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import evaluate, listing, manifest, store, strategy

METRICS = ["lu1", "max3", "out5", "bigloss1"]


def collect(since: str | None = None) -> pd.DataFrame:
    runs = [r for r in listing.all_runs() if since is None or r.parent.name >= since]
    if not runs:
        return pd.DataFrame()
    stored = sorted(p.stem for p in (store.home() / "bars").glob("*.parquet"))
    w = strategy.panel(store.load_window(stored))
    out = evaluate.stock_outcomes(w)
    frames = []
    for run in runs:
        summary = json.loads((run / "summary.json").read_text())
        obs = summary["observation"]
        facts = pd.read_parquet(run / "facts.parquet")
        if obs not in w["close"].index:
            continue
        group = facts["lm_group"] if "lm_group" in facts else pd.Series(index=facts.index, dtype=object)
        lu_t = w["limit_up"].loc[obs].reindex(facts.index).fillna(False).astype(bool)
        frame = pd.DataFrame({"group": group, "baseline": lu_t & facts.get("eligible", True),
                              "name": facts.get("name")}, index=facts.index)
        frame = frame[frame["group"].notna() | frame["baseline"]]
        for k in METRICS + ["lu5"]:
            frame[k] = out[k].loc[obs].reindex(frame.index)
        frame["obs"], frame["target"] = obs, summary["target"]
        frame["strategy_version"] = summary.get("strategy_version")
        frames.append(frame.rename_axis("code").reset_index())
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def report(rows: pd.DataFrame) -> dict:
    if rows.empty:
        return dict(lists=0)
    mature = rows[rows["out5"].notna()]
    parts = {g: rows[rows["group"] == g] for g in ("leader", "ladder", "watch", "avoid")}
    parts["baseline"] = rows[rows["baseline"]]
    stats = pd.DataFrame({g: {**part[METRICS].mean().round(3).to_dict(), "n": len(part),
                               "pending_out5": int(part["out5"].isna().sum())} for g, part in parts.items()}).T
    base = mature[mature["baseline"]]
    winners = base[base["lu5"] >= 3]
    listed = winners["group"].notna()
    return dict(lists=int(rows["obs"].nunique()), first=rows["obs"].min(), last=rows["obs"].max(),
                stats=stats, recall=round(float(listed.mean()), 3) if len(winners) else None,
                winners=len(winners), missed=winners.loc[~listed, ["obs", "code", "name"]].to_dict("records"))


def save(rows: pd.DataFrame, rep: dict) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = store.home() / "reviews" / stamp
    out.mkdir(parents=True, exist_ok=False)
    rows.to_csv(out / "rows.csv", index=False, encoding="utf-8-sig")
    if "stats" in rep:
        rep["stats"].to_csv(out / "stats.csv", encoding="utf-8-sig")
    stored = sorted(p.stem for p in (store.home() / "bars").glob("*.parquet"))
    meta = {k: v for k, v in rep.items() if k != "stats"} | {"manifest": manifest.build(stored)}
    (out / "report.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str))
    return out
