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
    p = longmai.params(version=3)
    code = "sh.600001"
    assert longmai.vetoes(_ctx(), 1, code, p, None) == []
    assert longmai.vetoes(_ctx(trig=True), 1, code, p, None) == []  # V2/V3: no 3-day veto
    assert longmai.vetoes(_ctx(trig=True), 1, code, longmai.params(version=1), None) == ["今日触发3日异动"]
    assert "严重异动后第3日" in longmai.vetoes(_ctx(since=3.0), 1, code, p, None)
    assert any("距严重异动" in v for v in longmai.vetoes(_ctx(left=0.2), 1, code, p, None))
    seat = pd.DataFrame({"retail_net_ratio": [0.05]}, index=[code])
    assert longmai.vetoes(_ctx(), 1, code, p, seat) == ["拉萨散户席位大额净买"]


def test_name_links_post_list():
    from sf import namethemes
    names = pd.Series({"sh.600001": "东方海洋", "sh.600002": "大东方", "sh.600003": "东方创业",
                       **{f"sz.{i:06d}": f"公司{chr(0x4e00 + 500 + i)}{chr(0x4e00 + 900 + i)}" for i in range(1, 200)}})
    strong = pd.Index(["sh.600001", "sh.600002", "sh.600003", "sz.000005"])
    links = namethemes.links(["sh.600001"], names, strong, names.index)
    assert links and links[0]["kind"] == "词" and links[0]["key"] == "东方"
    assert set(links[0]["partners"]) == {"大东方", "东方创业"}


def test_v3_crowding_rules():
    code = "sh.600001"
    days = [f"2026-01-{d:02d}" for d in range(5, 12)]
    ctx = {"aw": {"turn": pd.DataFrame({code: [5.0, 5.0, 5.0, 5.0, 5.0, 12.0, 31.0]}, index=days)}}
    p = longmai.params(version=3)
    assert longmai.crowded(ctx, 4, code, p) == []
    assert longmai.crowded(ctx, 5, code, p) == ["换手为前5日均值2.4倍"]
    assert longmai.crowded(ctx, 6, code, p)[0].startswith("换手31.0%")
    assert longmai.crowded(ctx, 6, code, longmai.params(version=2)) == []


def test_crowding_spec_share_and_theme_heat():
    import numpy as np
    import pandas as pd
    from sf import crowding
    days = [f"2026-01-{d:02d}" for d in range(1, 31)]
    amount = pd.DataFrame({"a": 10.0, "b": 30.0}, index=days)
    lu = pd.DataFrame({"a": False, "b": False}, index=days)
    lu.iloc[10, 0] = True
    s = crowding.spec_share(dict(amount=amount, limit_up=lu))
    assert s.iloc[9] == 0 and s.iloc[10] == 0.25 and s.iloc[14] == 0.25 and s.iloc[15] == 0
    share = pd.DataFrame({"X": np.linspace(0.1, 0.3, 30)}, index=days)
    sh, pct = crowding.theme_heat(dict(share=share), 29, "X")
    assert abs(sh - 0.3) < 1e-9 and pct == 1.0 >= crowding.HOT_PCT
    assert np.isnan(crowding.theme_heat(dict(share=share), 5, "X")[1])


def test_forward_version_is_v1():
    assert longmai.params()["version"] == "SF_LONGMAI_V1" == longmai.VERSION
    assert longmai.crowded({}, 0, "x", longmai.params()) == []  # V1 has no crowding block
