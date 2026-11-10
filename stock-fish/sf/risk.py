"""Risk context appended to the pre-market advice: sentiment tier, abnormal-volatility status, LHB seats.

These are risk filters, not return predictors. Evidence (per-day, out of sample, see CHANGELOG):
- mood tier: the top tercile beat the bottom tercile in both test segments, all tiers negative for relay;
- a stock that triggered the 3-day abnormal-volatility rule today did worse the next day;
- stocks heavily net-bought by Lhasa retail seats were the worst LHB group in every segment.
"""
from __future__ import annotations

import pandas as pd

from . import abnormal, mood, seats, store, strategy

MOOD_SESSIONS = 320  # 250-session percentiles + warm-up


def context(observation: str, codes, log=print) -> dict:
    sessions = sorted(p.stem for p in (store.home() / "bars").glob("*.parquet"))
    window = [s for s in sessions if s <= observation][-MOOD_SESSIONS:]
    w = strategy.panel(store.load_window(window))
    tab = mood.table(w)
    today = tab.iloc[-1]
    out = dict(mood=dict(version=mood.VERSION, level=None if pd.isna(today["level"]) else round(float(today["level"]), 3),
                         tier=today["tier"], advice=today["advice"]))
    notes = pd.Series("", index=pd.Index(codes))
    # abnormal volatility
    try:
        idx = abnormal.index_returns(update=True)
        coverage_ok = observation in idx.index
    except Exception as error:  # reported in summary.json; notes stay silent rather than guessing
        idx, coverage_ok = None, False
        out["abnormal_error"] = str(error)[:120]
    if coverage_ok:
        tr = w["traded"].fillna(False).astype(bool)
        pct = (w["close"] / w["preclose"] - 1).where(tr)
        dev = abnormal.deviation(pct, idx).where(tr)
        _, trig, room = abnormal.trigger3(dev)
        dev10 = dev.fillna(0).rolling(10, min_periods=1).sum().iloc[-1]
        limit = (w["up_limit"] / w["preclose"] - 1).iloc[-1]
        for code in notes.index:
            parts = []
            if bool(trig.iloc[-1].get(code, False)):
                parts.append("今日触发3日异动(次日统计偏弱)")
            elif room.iloc[-1].get(code, 1) <= limit.get(code, 0.1):
                r = room.iloc[-1][code]
                parts.append(f"明日相对大盘涨超{r:.0%}即触发异动" if r > 0 else f"明日相对大盘跌幅不超{-r:.0%}即触发异动")
            if dev10.get(code, 0) >= 0.8:
                parts.append(f"10日偏离{dev10[code]:.0%}，逼近严重异动")
            notes[code] = "；".join(parts)
    out["abnormal_checked"] = coverage_ok
    # LHB seats
    try:
        seats.capture(observation)
        feats = seats.day_features(observation, seats.skill_table(log=lambda *_: None))
    except Exception as error:
        feats = None
        out["seats_error"] = str(error)[:120]
    out["seats_checked"] = feats is not None
    if feats is not None and len(feats):
        for code in notes.index.intersection(feats.index):
            f, parts = feats.loc[code], []
            if f["retail_net_ratio"] > 0.02:
                parts.append("拉萨散户席位大额净买(历史最差)")
            if f["skilled_buyers"] >= 1:
                parts.append(f"有效游资买入{int(f['skilled_buyers'])}家")
            if f["inst_net_ratio"] > 0.02:
                parts.append("机构净买")
            if f["seat_net_ratio"] < -0.02:
                parts.append("榜单净卖")
            notes[code] = "；".join([n for n in (notes[code], *parts) if n])
    out["notes"] = notes
    return out
