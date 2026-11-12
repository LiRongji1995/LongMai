"""Offline tests for the V10 candidate low-amount / one-price exam (synthetic data only, no network)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sf import quality, strategy

DAYS = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2024-01-01", periods=64)]


def _bars(paths, amounts, lows=None):
    rows = []
    for code, closes in paths.items():
        prev = closes[0]
        for i, (day, close) in enumerate(zip(DAYS, closes)):
            low = (lows or {}).get((code, i), close)
            rows.append(dict(date=day, code=code, open=close, high=close, low=low, close=close, preclose=prev,
                             volume=1e6, amount=amounts[code], turn=1.0, pctChg=0.0, tradestatus="1", isST="0"))
            prev = close
    return pd.DataFrame(rows)


def test_flags_from_daily_bars():
    two = [10.0] * 62 + [11.0, 12.1]  # two limit-up closes from a 60-session base
    w = strategy.panel(_bars({"sh.600001": two, "sh.600002": two}, {"sh.600001": 4e7, "sh.600002": 5e8},
                             lows={("sh.600002", 63): 11.5}))
    f = {k: v.iloc[-1] for k, v in quality.flag_frames(w).items()}
    assert f["L1"]["sh.600001"] == 1.0 and f["L1"]["sh.600002"] == 0.0
    assert f["L1@5e+07"]["sh.600001"] == 1.0 and f["L1@3e+08"]["sh.600002"] == 0.0
    assert f["L2"]["sh.600001"] == 1.0 and f["L2"]["sh.600002"] == 0.0  # low == close: one-price limit-up
    assert f["L3"]["sh.600001"] == 1.0 and f["L3@1.4"]["sh.600001"] == 1.0  # 12.1 / 10 = 1.21
    assert f["L12"]["sh.600001"] == 1.0 and f["L13"]["sh.600002"] == 0.0
    early = {k: v.iloc[30] for k, v in quality.flag_frames(w).items()}
    assert np.isnan(early["L3"]["sh.600001"]) and np.isnan(early["L13"]["sh.600001"])  # < 60 sessions: unknown


def _panel(role="ladder", n=(400, 120, 120), share=0.25, yes=(0.10, 0.10, 0.40), no=(0.30, 0.25, 0.20)):
    """yes / no = (lu1, out5, bigloss1) rates for flagged and other rows."""
    rows = []
    for seg, k, day in zip(quality.SEGS, n, ("2024-06-03", "2025-06-02", "2026-03-02")):
        for i in range(k):
            flag = i < k * share
            lu, o5, bl = yes if flag else no
            j = i if flag else i - int(k * share)
            m = int(k * share) if flag else k - int(k * share)
            rows.append(dict(role=role, seg=seg, obs=day, code=f"c{i}", L1=float(flag), L2=np.nan, L3=1.0,
                             L12=np.nan, L13=float(flag), lu1=float(j < m * lu), out5=float(j < m * o5),
                             bigloss1=float(j < m * bl), max3=0.0, open_limit=0.0, a_bigloss=0.0))
    return pd.DataFrame(rows)


def test_verdict_and_group_rules(monkeypatch):
    p = _panel()
    monkeypatch.setattr(quality, "GROUP_RULE", None)
    with pytest.raises(ValueError):
        quality.report(p)  # the user must pick A or B first
    rep = quality.report(p, "B")["roles"]["ladder"]
    assert rep["verdicts"]["L1"]["holds"] and rep["holding"] == ["L1", "L13"]
    assert rep["verdicts"]["L2"]["reason"] == "样本不足"
    assert rep["passed"]  # big losses fall by 5pp, breakout only rises
    assert quality.report(p, "A")["roles"]["ladder"]["passed"]  # out5 21.25% -> 25%, big losses down
    same_out5 = _panel(yes=(0.10, 0.25, 0.40))  # removing them cuts big losses but leaves out5 unchanged
    assert quality.report(same_out5, "B")["roles"]["ladder"]["passed"]
    assert not quality.report(same_out5, "A")["roles"]["ladder"]["passed"]
    weak = quality.report(_panel(yes=(0.27, 0.25, 0.20)), "B")["roles"]["ladder"]
    assert not weak["verdicts"]["L1"]["holds"]


def test_group_accept_boundaries():
    assert quality.group_accept(0.03, 0.015, "A") and not quality.group_accept(0.03, 0.016, "A")
    assert quality.group_accept(-0.01, -0.02, "B") and not quality.group_accept(-0.011, -0.02, "B")
    assert not quality.group_accept(0.05, -0.019, "B")


def test_build_joins_flags_and_max3():
    two = [10.0] * 62 + [11.0, 12.1]
    w = strategy.panel(_bars({"sh.600001": two}, {"sh.600001": 4e7}))
    panel = pd.DataFrame({"code": ["sh.600001"], "role": ["ladder"], "obs": [DAYS[60]], "day": [DAYS[61]],
                          "c_lu1": [0.0], "c_out5": [1.0], "c_bigloss1": [0.0]})
    p = quality.build(panel, w)
    assert {"max3", "lu1", "L1", "L13"} <= set(p.columns) and abs(p["max3"].iloc[0] - 0.21) < 1e-9
