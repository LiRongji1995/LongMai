"""09:25 call-auction re-rank of the latest pre-market list.

Run between 09:25 and 09:30 Shanghai time. Uses one full-market spot snapshot (Eastmoney via AKShare):
after 09:25 the "open" field is the auction match price and the turnover is the auction amount.
Thresholds are the DAILY_V1 AUCTION_CONDITIONS_V1 values (unvalidated):
- gap <= -3%                       -> weak (放弃)
- auction amount / prior amount < 0.5%  -> weak participation
- ratio >= 2% and gap > -3%        -> participation confirmed
- gap >= 8% or open at limit-up    -> entry uncertain (likely no fill)
Theme check: if < 30% of same-industry list members gap up and their median gap < -2%, the theme
thesis is weakened.
"""
from __future__ import annotations

import json
from datetime import datetime, time, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import pools, premarket, store

AUCTION_VERSION = "SF_AUCTION_V1"
TH = dict(gap_weak=-0.03, amount_weak=0.005, amount_confirmed=0.02, high_gap=0.08,
          theme_positive_fraction=0.30, theme_median_gap=-0.02)


def snapshot() -> pd.DataFrame:
    import akshare as ak
    frame = store._retry(ak.stock_zh_a_spot_em, attempts=2, pause=1.0)
    frame["code"] = pools.to_code(frame["代码"])
    return frame.set_index("code")


def window_status(now: datetime | None = None) -> str:
    now = (now or store.now_sh()).astimezone(store.SH)
    if now.time() < time(9, 25):
        return "TOO_EARLY"  # auction not matched yet: prices are indicative, not final
    if now.time() <= time(9, 30):
        return "AUCTION_WINDOW"
    return "AFTER_OPEN"  # turnover now includes continuous trading


def rerank(run: Path, quotes: pd.DataFrame) -> pd.DataFrame:
    picks = pd.read_csv(run / "picks.csv", index_col=0)
    q = quotes.reindex(picks.index)
    open_px = pd.to_numeric(q["今开"], errors="coerce")
    prev = pd.to_numeric(q["昨收"], errors="coerce")
    amount = pd.to_numeric(q["成交额"], errors="coerce")
    out = picks[["rank", "tier", "name", "industry", "amount", "close"]].copy()
    out["gap"] = open_px / prev - 1
    out["auction_amount"] = amount
    out["ratio"] = amount / out["amount"]
    up_limit = np.floor(prev * 1.1 * 100 + 0.5) / 100
    out["at_limit"] = open_px >= up_limit - 1e-9
    theme = out.groupby("industry")["gap"].agg(pos=lambda g: (g > 0).mean(), med="median", n="size")
    weak_theme = out["industry"].map(
        (theme["pos"] < TH["theme_positive_fraction"]) & (theme["med"] < TH["theme_median_gap"]) & (theme["n"] >= 2))
    out["theme_weak"] = weak_theme.fillna(False)

    def decide(r):
        if pd.isna(r["gap"]):
            return "未知", "无竞价数据（停牌或未取到）"
        if r["at_limit"] or r["gap"] >= TH["high_gap"]:
            return "观察", f"高开{r['gap']:.1%}，大概率买不进/追高风险大"
        if r["gap"] <= TH["gap_weak"]:
            return "放弃", f"低开{r['gap']:.1%}，弱于预期"
        if pd.notna(r["ratio"]) and r["ratio"] < TH["amount_weak"]:
            return "放弃", f"竞价额仅为昨日{r['ratio']:.2%}，承接弱"
        if r["theme_weak"]:
            return "观察", "同板块竞价普遍偏弱"
        confirmed = pd.notna(r["ratio"]) and r["ratio"] >= TH["amount_confirmed"]
        if r["tier"] == "A":
            ok = 0.02 <= r["gap"] <= 0.07 and confirmed
        elif r["tier"] == "B":
            ok = r["gap"] >= 0.02 and confirmed
        else:
            ok = -0.01 <= r["gap"] <= 0.05 and confirmed
        if ok:
            return "参与", f"高开{r['gap']:.1%}，竞价额为昨日{r['ratio']:.1%}，符合计划"
        return "观察", f"开{r['gap']:+.1%}，竞价额{r['ratio']:.1%}，未完全符合计划"

    decisions = out.apply(decide, axis=1, result_type="expand")
    out["decision"], out["reason"] = decisions[0], decisions[1]
    order = {"参与": 0, "观察": 1, "放弃": 2, "未知": 3}
    out["_o"] = out["decision"].map(order)
    return out.sort_values(["_o", "rank"]).drop(columns="_o")


def run(target: str | None = None, quotes: pd.DataFrame | None = None) -> dict:
    run_dir = premarket.latest_run(target)
    if run_dir is None:
        raise FileNotFoundError("no saved pre-market list; run `sf premarket` first")
    status = window_status()
    quotes = quotes if quotes is not None else snapshot()
    table = rerank(run_dir, quotes)
    simple = pd.DataFrame({"名称": table["name"].to_numpy(),
                           "竞价涨幅": table["gap"].map(lambda g: f"{g:+.2%}" if pd.notna(g) else "-").to_numpy(),
                           "竞价额(万)": (table["auction_amount"] / 1e4).round(0).to_numpy(),
                           "建议": (table["decision"] + "：" + table["reason"]).to_numpy()})
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    simple.to_csv(run_dir / f"竞价清单-{stamp}.csv", index=False, encoding="utf-8-sig")
    table.to_csv(run_dir / f"auction-{stamp}.csv", encoding="utf-8-sig")
    (run_dir / f"auction-{stamp}.json").write_text(json.dumps(
        dict(version=AUCTION_VERSION, thresholds=TH, window=status, captured_at=stamp), ensure_ascii=False))
    return dict(run=run_dir, window=status, table=table, simple=simple)
