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


def _load() -> tuple[dict, dict]:
    stored = sorted(p.stem for p in (store.home() / "bars").glob("*.parquet"))
    w = strategy.panel(store.load_window(stored))
    return w, evaluate.stock_outcomes(w)


def collect(since: str | None = None, loaded: tuple[dict, dict] | None = None) -> pd.DataFrame:
    runs = [r for r in listing.all_runs() if since is None or r.parent.name >= since]
    if not runs:
        return pd.DataFrame()
    w, out = loaded or _load()
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


# ------------------------------------------------------------------ V9 forward record against V7

FORWARD_FROM = "2026-11-12"  # target session of the first V9 list (docs/STRATEGY_LONGMAI_V9.md)
MIN_FORWARD_DAYS = 60
# hit rate vs big loss (user decision 2026-11-11, AGENTS.md): out5 at least +2 points, big losses up by at most
# half of the out5 gain and never more than +2 points
TRADEOFF = dict(min_out5_gain=0.02, loss_share=0.5, max_loss_gain=0.02)


def acceptable(d_out5: float, d_bigloss: float, rule: dict | None = None) -> bool:
    rule = rule or TRADEOFF
    return bool(d_out5 >= rule["min_out5_gain"]
                and d_bigloss <= min(rule["loss_share"] * d_out5, rule["max_loss_gain"]))


def observe_group(facts: pd.DataFrame, leaders: set, params: dict) -> pd.Index:
    """The whole observation group under `params`: R2-selected, not a mainline leader, not 2板+ (the display
    limit of 10 is not applied, as in the V9 exam)."""
    f = strategy.evaluate(facts, params)
    keep = f["r2_selected"].fillna(False).astype(bool) & ~f.index.isin(list(leaders)) & \
        ~(f["board_height"].fillna(0) >= 2)
    return f.index[keep.to_numpy()]


def forward_rows(since: str = FORWARD_FROM, loaded: tuple[dict, dict] | None = None) -> pd.DataFrame:
    """Per saved list from `since`: the observation group recomputed from its saved fact table under the V7 and
    the V9 stage rules, with outcomes. Only facts saved at list time are used (plus that day's stored bars for
    the 5-session turnover mean that V7-era fact tables lack)."""
    runs = [r for r in listing.all_runs() if r.parent.name >= since]
    if not runs:
        return pd.DataFrame()
    w, out = loaded or _load()
    index = list(w["close"].index)
    turn = w["turn"].where(w["traded"].fillna(False).astype(bool))
    v9, v7 = strategy.params(), strategy.params(strategy.V7_OVERRIDES)
    frames = []
    for run in runs:
        summary = json.loads((run / "summary.json").read_text())
        obs = summary["observation"]
        if obs not in index:
            continue
        t = index.index(obs)
        facts = pd.read_parquet(run / "facts.parquet")
        facts["turn_prev5"] = turn.iloc[t - 5:t].mean(skipna=False).reindex(facts.index)
        leaders = set(pd.read_csv(run / "leaders.csv")["code"]) if (run / "leaders.csv").exists() else set()
        a, b = observe_group(facts, leaders, v7), observe_group(facts, leaders, v9)
        codes = a.union(b)
        frame = pd.DataFrame({"code": codes, "in_v7": codes.isin(a), "in_v9": codes.isin(b),
                              "obs": obs, "target": summary["target"]})
        for k in METRICS:
            frame[k] = out[k].loc[obs].reindex(codes).to_numpy()
        frames.append(frame)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def forward_report(rows: pd.DataFrame) -> dict:
    if rows.empty:
        return dict(days=0)
    table = {}
    for name, col in (("V7", "in_v7"), ("V9", "in_v9")):
        part = rows[rows[col]]
        table[name] = {**part[METRICS].mean().round(4).to_dict(), "n": len(part),
                       "per_day": round(len(part) / rows["obs"].nunique(), 1),
                       "pending_out5": int(part["out5"].isna().sum())}
    table = pd.DataFrame(table).T
    mature = rows[rows["out5"].notna()]
    days = int(mature["obs"].nunique())
    m = {name: mature[mature[col]][["out5", "bigloss1"]].mean() for name, col in (("V7", "in_v7"), ("V9", "in_v9"))}
    d_out5 = float(m["V9"]["out5"] - m["V7"]["out5"]) if days else float("nan")
    d_loss = float(m["V9"]["bigloss1"] - m["V7"]["bigloss1"]) if days else float("nan")
    if days < MIN_FORWARD_DAYS:
        status = f"前向样本 {days} 个交易日（满 {MIN_FORWARD_DAYS} 天才按取舍规则判断），只作描述"
    elif acceptable(d_out5, d_loss):
        status = "V9 相对 V7 在可接受范围内（走出来 ≥ +2 个百分点，大面增幅 ≤ 一半且 ≤ +2 个百分点）"
    else:
        status = "V9 相对 V7 不满足取舍规则：考虑回到 V7 的形态规则（版本变更）"
    return dict(days=days, lists=int(rows["obs"].nunique()), table=table, d_out5=d_out5, d_bigloss1=d_loss,
                status=status)


# ------------------------------------------------------------------ forward checks of the descriptive layers

def baserate_calibration(since: str | None = None, loaded: tuple[dict, dict] | None = None) -> pd.DataFrame:
    """Per saved list that used a 历史同类 table: the table's rates for each listed stock next to what happened.
    One row per (list, stock); outcomes not yet stored stay NaN (pending)."""
    from . import baserates
    runs = [r for r in listing.all_runs() if since is None or r.parent.name >= since]
    w, out = loaded or _load()
    frames = []
    role_of = {"leader": "leader", "avoid": "leader", "ladder": "ladder", "watch": "observe"}
    for run in runs:
        summary = json.loads((run / "summary.json").read_text())
        rid = (summary.get("baserates") or {}).get("run")
        obs = summary["observation"]
        path = store.home() / "research" / "baserates" / str(rid) / "table.parquet"
        if not rid or not path.exists() or obs not in w["close"].index:
            continue
        tab = pd.read_parquet(path)
        facts = pd.read_parquet(run / "facts.parquet")
        facts = facts[facts["lm_group"].isin(list(role_of))]
        for code, f in facts.iterrows():
            r = baserates.lookup(tab, role_of[f["lm_group"]], f.get("board_height"), f.get("one_price"))
            if r is None:
                continue
            frames.append(dict(obs=obs, code=code, role=role_of[f["lm_group"]], cell=baserates._cell_name(r),
                               p_lu1=r["lu1"], p_out5=r["out5"], p_bigloss1=r["bigloss1"],
                               lu1=out["lu1"][code].get(obs) if code in out["lu1"] else float("nan"),
                               out5=out["out5"][code].get(obs) if code in out["out5"] else float("nan"),
                               bigloss1=out["bigloss1"][code].get(obs) if code in out["bigloss1"] else float("nan")))
    return pd.DataFrame(frames)


def calibration_report(rows: pd.DataFrame) -> pd.DataFrame:
    """Per role: predicted (table) vs realized rates over the stocks whose outcome is known."""
    if rows.empty:
        return pd.DataFrame()
    out = {}
    for role, g in rows.groupby("role"):
        rec = {}
        for k in ("lu1", "out5", "bigloss1"):
            known = g[g[k].notna()]
            rec[f"n_{k}"] = len(known)
            rec[f"历史_{k}"] = round(float(known[f"p_{k}"].mean()), 3) if len(known) else None
            rec[f"实际_{k}"] = round(float(known[k].mean()), 3) if len(known) else None
        out[role] = rec
    return pd.DataFrame(out).T


def auction_forward(loaded: tuple[dict, dict] | None = None) -> pd.DataFrame:
    """Every saved auction day (first snapshot per day), relabelled with the pre-registered cuts of the adopted roles,
    with the open-anchored outcomes of that day (NaN = pending)."""
    from . import auction
    root = store.home() / "auction"
    if not root.exists():
        return pd.DataFrame()
    w, _ = loaded or _load()
    outcomes = auction.auction_outcomes(w)
    frames = []
    for day_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        snaps = sorted(p for p in day_dir.iterdir() if (p / "snapshot.parquet").exists())
        if not snaps or day_dir.name not in w["close"].index:
            continue
        s = pd.read_parquet(snaps[0] / "snapshot.parquet")
        s = s[s["role"].isin(list(auction.ADOPTED))]
        for role, a in auction.ADOPTED.items():
            m = s["role"] == role
            if m.any():
                s.loc[m, "class"] = auction.classify(s.loc[m, "gap"], s.loc[m, "open_limit"], *a["cuts"]).to_numpy()
        for k in ("a_lu", "a_out5", "a_bigloss"):
            s[k] = [outcomes[k][c].get(day_dir.name) if c in outcomes[k] else float("nan") for c in s.index]
        frames.append(s.assign(day=day_dir.name).rename_axis("code").reset_index())
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def auction_forward_report(rows: pd.DataFrame) -> pd.DataFrame:
    if rows.empty or "class" not in rows:
        return pd.DataFrame()
    g = rows.groupby(["role", "class"])
    rep = g.agg(n=("code", "size"), days=("day", "nunique"), a_lu=("a_lu", "mean"), a_bigloss=("a_bigloss", "mean"),
                a_out5=("a_out5", "mean"), pending_out5=("a_out5", lambda x: int(x.isna().sum())))
    return rep.round(3)
