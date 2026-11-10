"""Pre-market list for the next session: R2 matches + hot-money context, tiered and scored.

Saved under lists/<target>/<run_id>/ (never overwritten):
- picks.csv       ranked R2 selections with plan text
- facts.parquet   every main-board stock with all features, gate results, near-miss labels
- summary.json    sentiment, regime, counts, versions, data coverage
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import pools, ranking, risk, store, strategy

HISTORY_SESSIONS = 30


def industry() -> pd.DataFrame:
    path = store.home() / "meta" / "industry.parquet"
    if not path.exists():
        with store.BaoStock() as bao:
            frame = bao._rows(bao.bs.query_stock_industry())
        frame.to_parquet(path, index=False)
    return pd.read_parquet(path)


def enrich(facts: pd.DataFrame, observation: str, window: list[str]) -> tuple[pd.DataFrame, dict]:
    basic = store.stock_basic().drop_duplicates("code").set_index("code")
    ind = industry().drop_duplicates("code").set_index("code")["industry"].str.replace(r"^[A-Z]\d+", "", regex=True)
    facts = facts.copy()
    facts["name"] = basic["code_name"].reindex(facts.index)
    facts["industry"] = ind.reindex(facts.index).fillna("")
    coverage = {}
    seal = pools.seal_facts(observation)
    coverage["zt_pool"] = seal is not None
    if seal is not None:
        facts = facts.join(seal)
    heat_key = facts["em_industry"].fillna(facts["industry"]) if "em_industry" in facts else facts["industry"]
    facts["heat_key"] = heat_key
    lu = facts["limit_up"].fillna(False).astype(bool) & facts["eligible"]
    facts["theme_heat"] = heat_key.map(heat_key[lu].value_counts()).fillna(0).astype(int)
    lhb = pools.lhb_window(window[-5:])
    coverage["lhb_5d"] = lhb is not None
    if lhb is not None:
        facts = facts.join(lhb)
        facts["lhb_times"] = facts["lhb_times"].fillna(0).astype(int)
    else:
        facts["lhb_times"] = pd.NA  # unknown, not zero
    return facts, coverage


def build(observation: str | None = None, weights: dict | None = None) -> dict:
    sessions = store.calendar()
    observation = observation or store.latest_complete_session(sessions)
    target = store.next_session(sessions, observation)
    window = [s for s in sessions if s <= observation][-HISTORY_SESSIONS:]
    w = strategy.panel(store.load_window(window))
    t = len(window) - 1
    p = strategy.params()
    pool = strategy.eligible(w, store.stock_basic(), sessions, t, p)
    facts = strategy.evaluate(strategy.features(w, t, pool, p), p)
    mood = strategy.sentiment(w, t, pool)
    state, advice = strategy.regime(mood)
    facts, coverage = enrich(facts, observation, window)
    facts["tier"] = ranking.tier(facts)
    facts["score"], parts = ranking.score(facts.assign(lhb_times=facts["lhb_times"].astype("Float64")), weights)
    facts = facts.join(parts)
    picks = facts[facts["r2_selected"]].sort_values(["tier", "score"], ascending=[True, False]).copy()
    picks["rank"] = range(1, len(picks) + 1)
    picks["plan"] = picks.apply(ranking.plan, axis=1)
    ctx = risk.context(observation, picks.index)
    notes = ctx.pop("notes")
    picks["risk"] = notes.reindex(picks.index).fillna("")
    mood_note = f"情绪{ctx['mood']['tier']}档：{ctx['mood']['advice']}"
    picks["plan"] = picks["plan"] + "｜" + mood_note + picks["risk"].map(lambda x: f"；{x}" if x else "")
    facts["rank"] = picks["rank"].reindex(facts.index)
    return dict(
        observation=observation, target=target, generated_at=datetime.now(timezone.utc).isoformat(),
        strategy_version=strategy.STRATEGY_VERSION, ranking_version=ranking.RANKING_VERSION,
        params=p, weights=weights or ranking.WEIGHTS, regime=state, advice=advice, sentiment=mood,
        coverage=coverage, risk=ctx, counts=dict(eligible=int(facts["eligible"].sum()), r1=int(facts["r1_match"].sum()),
                                       r2=int(len(picks)), near_miss=int((facts["near_miss"] != "").sum())),
        picks=picks, facts=facts)


PICK_COLUMNS = ["rank", "tier", "score", "name", "close", "ret1", "board_height", "stages", "industry",
                "theme_heat", "first_seal", "breaks", "seal_amount", "float_cap", "amount", "turn", "A", "rs1",
                "ret5", "ret10", "clv", "lhb_times", "lhb_net", "risk", "plan"]


def simple(picks: pd.DataFrame) -> pd.DataFrame:
    """The user-facing list: name, amount, participation advice only."""
    return pd.DataFrame({"名称": picks["name"].to_numpy(),
                         "成交额(亿)": (picks["amount"] / 1e8).round(2).to_numpy(),
                         "参与建议": picks["plan"].to_numpy()})


def save(result: dict) -> Path:
    run_id = datetime.fromisoformat(result["generated_at"]).strftime("%Y%m%dT%H%M%SZ")
    out = store.home() / "lists" / result["target"] / run_id
    out.mkdir(parents=True, exist_ok=False)
    picks = result["picks"]
    simple(picks).to_csv(out / "盘前清单.csv", index=False, encoding="utf-8-sig")
    picks[[c for c in PICK_COLUMNS if c in picks]].to_csv(out / "picks.csv", encoding="utf-8-sig")
    result["facts"].to_parquet(out / "facts.parquet")
    meta = {k: v for k, v in result.items() if k not in ("picks", "facts")}
    (out / "summary.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str))
    (out.parent / "LATEST").write_text(run_id)
    return out


def latest_run(target: str | None = None) -> Path | None:
    root = store.home() / "lists"
    if not root.exists():
        return None
    targets = sorted(p.name for p in root.iterdir() if p.is_dir())
    if not targets:
        return None
    day = target or targets[-1]
    marker = root / day / "LATEST"
    if marker.exists():
        return root / day / marker.read_text().strip()
    runs = sorted(p for p in (root / day).iterdir() if p.is_dir()) if (root / day).exists() else []
    return runs[-1] if runs else (root / day if (root / day / "picks.csv").exists() else None)


def all_runs() -> list[Path]:
    """Latest run per target session, oldest first."""
    root = store.home() / "lists"
    if not root.exists():
        return []
    runs = [latest_run(p.name) for p in sorted(root.iterdir()) if p.is_dir()]
    return [r for r in runs if r is not None and (r / "facts.parquet").exists()]
