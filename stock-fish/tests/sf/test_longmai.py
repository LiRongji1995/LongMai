"""Offline tests for 龙脉 V1 gate and vetoes (synthetic frames)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from sf import longmai

DAYS = ["2026-01-05", "2026-01-06", "2026-01-07"]


def _ctx(tier="高", reg_pct=0.5, trig=False, flag=False, since=np.nan, left=0.8):
    code = "sh.600001"
    frame = lambda v: pd.DataFrame({code: [v] * len(DAYS)}, index=DAYS)
    return dict(
        mood=pd.DataFrame({"tier": [tier] * 3, "level": [0.8] * 3}, index=DAYS),
        reg=pd.DataFrame({"szse_per_turnover_pct": [reg_pct] * 3, "szse_cases": [100] * 3}, index=DAYS),
        trig=frame(trig), room=frame(0.2),
        severe=dict(flag=frame(flag), days_since=frame(since), dev10_left=frame(left), dev10=frame(1 - left),
                    events_left=frame(3)),
        w={"up_limit": frame(11.0), "preclose": frame(10.0)},
    )


def test_gate_budget_and_regulation_cut():
    p = longmai.params()
    assert longmai.gate(_ctx("高"), 1, DAYS[2], p)["slots"] == 4
    assert longmai.gate(_ctx("中"), 1, DAYS[2], p)["slots"] == 2
    assert longmai.gate(_ctx("低"), 1, DAYS[2], p)["slots"] == 0
    hot = longmai.gate(_ctx("高", reg_pct=0.9), 1, DAYS[2], p)
    assert hot["reg_tier"] == "高" and hot["slots"] == 2
    assert longmai.gate(_ctx("中", reg_pct=0.9), 1, DAYS[2], p)["slots"] == 1


def test_vetoes():
    p = longmai.params()
    code = "sh.600001"
    assert longmai.vetoes(_ctx(), 1, code, p, None) == []
    assert longmai.vetoes(_ctx(trig=True), 1, code, p, None) == ["今日触发3日异动"]
    assert "严重异动后第3日" in longmai.vetoes(_ctx(since=3.0), 1, code, p, None)
    assert any("距严重异动" in v for v in longmai.vetoes(_ctx(left=0.2), 1, code, p, None))
    seat = pd.DataFrame({"retail_net_ratio": [0.05]}, index=[code])
    assert longmai.vetoes(_ctx(), 1, code, p, seat) == ["拉萨散户席位大额净买"]
