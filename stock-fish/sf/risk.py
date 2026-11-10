"""Risk context appended to the pre-market advice: sentiment tier, abnormal-volatility status, LHB seats.

These are risk filters, not return predictors. Evidence (per-day, out of sample, see CHANGELOG):
- mood tier: the top tercile beat the bottom tercile in both test segments, all tiers negative for relay;
- a stock that triggered the 3-day abnormal-volatility rule today did worse the next day;
- stocks heavily net-bought by Lhasa retail seats were the worst LHB group in every segment;
- limit-ups close to a severe-abnormal-volatility trigger (10-day deviation within 30% of +100%) did worse;
- limit-ups did worse when the regulatory temperature (SZSE weekly abnormal-trading cases per unit of
  market turnover, top tercile of the last 52 reports) was high.
"""
from __future__ import annotations

import pandas as pd

from . import abnormal, mood, regulation, seats, store, strategy

MOOD_SESSIONS = 320  # 250-session percentiles + warm-up


def context(observation: str, codes, target: str | None = None, log=print) -> dict:
    sessions = sorted(p.stem for p in (store.home() / "bars").glob("*.parquet"))
    window = [s for s in sessions if s <= observation][-MOOD_SESSIONS:]
    w = strategy.panel(store.load_window(window))
    tab = mood.table(w)
    today = tab.iloc[-1]
    out = dict(mood=dict(version=mood.VERSION, level=None if pd.isna(today["level"]) else round(float(today["level"]), 3),
                         tier=today["tier"], advice=today["advice"]))
    notes = pd.Series("", index=pd.Index(codes))
    # regulatory temperature known before the target open
    try:
        regulation.update_szse(log=lambda *_: None)
    except Exception as error:
        out["regulation_update_error"] = str(error)[:120]
    tr_all = w["traded"].fillna(False).astype(bool)
    amount = w["amount"].where(tr_all).sum(axis=1)
    reg = regulation.daily(window + ([target] if target else []), amount)
    if len(reg) and "szse_per_turnover_pct" in reg and pd.notna(reg["szse_per_turnover_pct"].iloc[-1]):
        q = float(reg["szse_per_turnover_pct"].iloc[-1])
        out["regulation"] = dict(szse_cases=float(reg["szse_cases"].iloc[-1]), pct=round(q, 3),
                                 tier="高" if q > 2 / 3 else "低" if q < 1 / 3 else "中")
    else:
        out["regulation"] = dict(tier="UNKNOWN")
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
        sv = abnormal.severe(dev, trig)
        dev10, left10 = sv["dev10"].iloc[-1], sv["dev10_left"].iloc[-1]
        events_left, since, flag = sv["events_left"].iloc[-1], sv["days_since"].iloc[-1], sv["flag"].iloc[-1]
        limit = (w["up_limit"] / w["preclose"] - 1).iloc[-1]
        for code in notes.index:
            parts = []
            if bool(trig.iloc[-1].get(code, False)):
                parts.append("今日触发3日异动(次日统计偏弱)")
            elif room.iloc[-1].get(code, 1) <= limit.get(code, 0.1):
                r = room.iloc[-1][code]
                parts.append(f"明日相对大盘涨超{r:.0%}即触发异动" if r > 0 else f"明日相对大盘跌幅不超{-r:.0%}即触发异动")
            if bool(flag.get(code, False)):
                parts.append("今日达严重异动标准(可能停牌核查)")
            else:
                if 0 < since.get(code, 99) <= 10:
                    parts.append(f"严重异动后第{int(since[code])}日(监控门槛可减半)")
                if left10.get(code, 1) < 0.3:
                    parts.append(f"10日偏离{dev10[code]:.0%}，距严重异动不足30%(历史偏弱)")
                elif events_left.get(code, 9) == 1 and bool(trig.iloc[-1].get(code, False)) is False:
                    parts.append("再触发1次3日异动即严重异动")
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
