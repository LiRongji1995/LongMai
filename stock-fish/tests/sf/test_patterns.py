"""Offline tests for the V9 pattern split (synthetic data only, no network)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from sf import patterns, strategy

DAYS = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2024-01-01", periods=70)]


def _bars(paths: dict[str, list[float]], amounts: dict[str, list[float]] | None = None, lows=None, skip=None):
    rows = []
    for code, closes in paths.items():
        prev = closes[0]
        for i, (day, close) in enumerate(zip(DAYS, closes)):
            if skip and (code, i) in skip:
                continue  # absent bar: missing data, not a quiet day
            amt = amounts[code][i] if amounts and code in amounts else 1e8
            low = (lows or {}).get((code, i), close)
            rows.append(dict(date=day, code=code, open=close, high=close, low=low, close=close,
                             preclose=prev if i else close, volume=1e6, amount=amt, turn=amt / 1e8 * 5,
                             pctChg=0.0, tradestatus="1", isST="0"))
            prev = close
    return pd.DataFrame(rows)


def test_pullback_flags_from_daily_bars():
    a = [10.0] * 66 + [11.0, 11.5, 12.0, 11.4]  # limit-up, two more up days, then a pullback
    amounts = {"sh.600001": [1e8] * 66 + [3e8, 3e8, 3e8, 2e8]}
    w = strategy.panel(_bars({"sh.600001": a, "sh.600002": a}, amounts | {"sh.600002": amounts["sh.600001"]},
                             lows={("sh.600002", 69): 11.0}, skip={("sh.600002", 67)}))
    f = patterns.flags(w, len(DAYS) - 1)
    r = f.loc["sh.600001"]
    assert r["P1"] == 1.0 and r["P2"] == 0.0 and r["P3"] == 1.0  # 2e8 <= 0.8 x 3e8; low 11.4 > 12 x 0.93
    assert r["P1@0.7"] == 1.0 and r["P1@0.9"] == 1.0
    b = f.loc["sh.600002"]
    assert b["P2"] == 1.0  # low 11.0 <= 11.16
    assert np.isnan(b["P3"]) and np.isnan(b["P1"])  # a missing up day makes both unknown, never "no"


def test_start_and_continuation_flags():
    ramp = [10.0] * 64 + [10.5, 11.0, 12.0, 13.0, 14.0, 15.4]  # +40% over the last 5 sessions, last day limit-up
    amounts = {c: [1e8] * 69 + [3e8] for c in ("sh.600001", "sh.600002")}
    w = strategy.panel(_bars({"sh.600001": ramp, "sh.600002": ramp}, amounts, lows={("sh.600001", 69): 14.2}))
    f = patterns.flags(w, len(DAYS) - 1)
    r = f.loc["sh.600001"]
    assert r["S1"] == 1.0 and r["S1@60"] == 1.0
    assert r["S2"] == 1.0 and f.loc["sh.600002", "S2"] == 0.0  # traded limit-up vs one-price limit-up
    assert r["S3"] == 0.0 and r["S3@1.5"] == 0.0  # 15.4 / 10 > 1.5
    assert r["C1"] == 1.0  # turnover 15 vs prior mean 5
    assert r["C2"] == 1.0 and r["C2@0.5"] == 0.0
    assert r["C3"] == 1.0 and r["C3@3e+08"] == 1.0


def _panel(flag, yes_rate, no_rate, n=(400, 100, 100), stage="FIRST_PULLBACK", loss=(0.1, 0.1)):
    rows = []
    for seg, k, day in zip(("train", "test1", "test2"), n, ("2024-06-03", "2025-06-02", "2026-03-02")):
        for arm, rate, bl in ((1.0, yes_rate, loss[0]), (0.0, no_rate, loss[1])):
            hits = int(round(k * rate))
            losses = int(round(k * bl))
            for i in range(k):
                rows.append({"obs": day, "code": f"c{i}", "seg": seg, flag: arm, "v7_group": "observe",
                             "out5": float(i < hits), "bigloss1": float(i < losses), "lu1": 0.0, "max3": 0.0,
                             **{s: s == stage for s in strategy.STAGES}})
    return pd.DataFrame(rows)


def test_verdict_adopts_only_a_replicated_gap():
    p = _panel("P1", 0.40, 0.30)
    v = patterns.verdict(patterns.split_stats(p, ["P1"]), "P1")
    assert v["adopted"] and v["reason"] == "三段均满足"
    weak = _panel("P1", 0.33, 0.30)
    assert patterns.verdict(patterns.split_stats(weak, ["P1"]), "P1")["reason"].startswith("train 差")
    few = _panel("P1", 0.40, 0.30, n=(150, 100, 100))
    assert patterns.verdict(patterns.split_stats(few, ["P1"]), "P1")["reason"] == "样本不足"


def test_verdict_secondary_guard_and_test_replication():
    worse = _panel("P1", 0.40, 0.30, loss=(0.20, 0.10))  # better out5 but 10pp more big losses
    assert "反向恶化" in patterns.verdict(patterns.split_stats(worse, ["P1"]), "P1")["reason"]
    p = _panel("P1", 0.40, 0.30)
    p.loc[(p["seg"] == "test2") & (p["P1"] == 1.0), "out5"] = 0.0  # gap reverses in test2
    assert patterns.verdict(patterns.split_stats(p, ["P1"]), "P1")["reason"].startswith("test2 未复现")
    bad = _panel("P2", 0.20, 0.30)  # P2 expects lower out5 for yes
    assert patterns.verdict(patterns.split_stats(bad, ["P2"]), "P2")["adopted"]


def test_adopted_flags_reshape_stages_and_unknown_handling():
    panel = pd.DataFrame({"FIRST_PULLBACK": [True, True, True, True], "STRONG_START": False,
                          "STRONG_CONTINUATION": [False, False, True, True],
                          "P1": [1.0, 0.0, np.nan, 1.0], "C2": [np.nan, np.nan, 1.0, np.nan]})
    st = patterns.v9_stages(panel, ["P1", "C2"])
    assert st["FIRST_PULLBACK"].tolist() == [True, False, False, True]  # must be met; unknown = not met
    assert st["STRONG_CONTINUATION"].tolist() == [False, False, False, True]  # excluded; unknown = kept


def test_report_needs_a_flag_and_no_worse_observe_group():
    p = _panel("P1", 0.40, 0.30)
    for f in patterns.FLAGS:
        if f != "P1":
            p[f] = np.nan
    rep = patterns.report(p)
    assert rep["adopted"] == ["P1"] and rep["passed"]
    assert rep["compare"].loc[("V9", "test1"), "out5"] > rep["compare"].loc[("V7", "test1"), "out5"]
    assert rep["verdicts"]["C1"]["reason"] == "样本不足"
