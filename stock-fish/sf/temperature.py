"""Short-term market temperature (水温) on two axes, from the entry-free evaluation (CHANGELOG
2026-11-10, `sf/evaluate.py`):

  heat (热度)   mean 250-session percentile of: limit-up count, promotion rate, max height,
                short-term share (amount of stocks with a limit-up in 5 sessions / market amount)
  pain (亏钱效应) mean percentile of: 大面 count (yesterday's limit-ups down >= 5%), limit-down count,
                1 - failed-board premium, 1 - up ratio

A day is 热 when heat >= HEAT_MID and 痛 when pain >= PAIN_MID (train medians). Next-day loss-effect
rate by cell (prem_lu below and 大面 above their trailing medians), train / test1 / test2:
  热痛 54 / 50 / 57%   热顺 38 / 41 / 46%   冷痛 24 / 41 / 51%   冷顺 17 / 17 / 27%   (all days 33 / 37 / 48%)
Signs were chosen on the train segment; the test segments had been inspected before.
"""
from __future__ import annotations

import pandas as pd

from . import crowding, mood

HEAT = ("lu_count", "promo_rate", "max_height", "spec_share")
PAIN = ("big_loss", "ld_count", "broken_prem", "up_ratio")      # the last two enter as 1 - percentile
PAIN_INVERTED = ("broken_prem", "up_ratio")
HEAT_MID, PAIN_MID = 0.47, 0.50
CELLS = {
    ("热", "痛"): ("高位分歧", "压力最大", (0.54, 0.50, 0.57)),
    ("热", "顺"): ("高潮延续", "压力积累", (0.38, 0.41, 0.46)),
    ("冷", "痛"): ("退潮", "亏钱效应未止", (0.24, 0.41, 0.51)),
    ("冷", "顺"): ("修复升温", "最安全", (0.17, 0.17, 0.27)),
}
BASE = (0.33, 0.37, 0.48)
LABELS = {"lu_count": "涨停家数", "promo_rate": "晋级率", "max_height": "最高板", "spec_share": "短线资金占比",
          "big_loss": "大面家数", "ld_count": "跌停家数", "broken_prem": "炸板股次日表现弱", "up_ratio": "上涨家数少"}


def table(w: dict, aw: dict, window: int = 250, ind: pd.DataFrame | None = None) -> pd.DataFrame:
    """`ind`: precomputed `mood.indicators(w)` (or `mood.table(w)`, which contains them) to skip the recompute."""
    ind = (mood.indicators(w) if ind is None else ind)[[k for k in HEAT + PAIN if k != "spec_share"]].copy()
    ind["spec_share"] = crowding.spec_share(aw).reindex(ind.index)
    pct = ind.rolling(window, min_periods=60).rank(pct=True)
    oriented = pct.copy()
    for k in PAIN_INVERTED:
        oriented[k] = 1 - pct[k]
    out = pd.DataFrame(index=ind.index)
    out["heat"] = oriented[list(HEAT)].mean(axis=1)
    out["pain"] = oriented[list(PAIN)].mean(axis=1)
    out["hot"] = (out["heat"] >= HEAT_MID).map({True: "热", False: "冷"})
    out["sore"] = (out["pain"] >= PAIN_MID).map({True: "痛", False: "顺"})
    known = out["heat"].notna() & out["pain"].notna()
    out["cell"] = (out["hot"] + out["sore"]).where(known, "")
    return out.join(oriented.add_suffix("_o"))


def reading(tab: pd.DataFrame, t: int) -> dict:
    row = tab.iloc[t]
    if not row["cell"]:
        return dict(cell="", text="水温：数据不足（需约60个交易日历史）")
    name, note, rates = CELLS[(row["hot"], row["sore"])]
    avg, base = sum(rates) / 3, sum(BASE) / 3
    sources = [LABELS[k] + f"(分位{row[k + '_o']:.0%})" for k in HEAT + PAIN
               if pd.notna(row.get(k + "_o")) and row[k + "_o"] >= 0.8]
    prev = tab["heat"].iloc[max(0, t - 3):t].mean()
    trend = "升" if pd.notna(prev) and row["heat"] > prev + 0.05 else "降" if pd.notna(prev) and row["heat"] < prev - 0.05 else "平"
    text = (f"水温：{row['hot']}+{'亏钱效应强' if row['sore'] == '痛' else '亏钱效应弱'}【{name}，{note}】"
            f"热度{row['heat']:.2f}(较前3日{trend}) 亏钱{row['pain']:.2f}；历史上次日出现亏钱效应约{avg:.0%}（平时约{base:.0%}）"
            + (f"；压力来源：{'、'.join(sources)}" if sources else ""))
    return dict(cell=row["cell"], name=name, heat=round(float(row["heat"]), 3), pain=round(float(row["pain"]), 3),
                loss_rate=round(avg, 2), sources=sources, trend=trend, text=text)
