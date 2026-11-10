"""Offline tests for 龙脉 V7 screening, the list helpers and the temperature (synthetic frames)."""
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
    assert longmai.vetoes(_ctx(trig=True), 1, code, p, None) == []  # no 3-session abnormal veto
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


def test_relative_crowding_and_leaders_by_height_then_turnover():
    import numpy as np
    from sf import mainline
    code = "sh.600001"
    days = [f"2026-01-{d:02d}" for d in range(5, 12)]
    turn = pd.DataFrame({code: [5.0, 5.0, 5.0, 5.0, 5.0, 12.0, 40.0]}, index=days)
    sealed = pd.DataFrame({code: [False] * 5 + [False, True]}, index=days)
    ctx = {"aw": {"turn": turn, "limit_up": sealed}}
    p = longmai.params()
    assert longmai.crowded(ctx, 4, code, p) == []
    assert longmai.crowded(ctx, 5, code, p)[0].startswith("放量分歧")   # 2.4x, not sealed
    assert longmai.crowded(ctx, 6, code, p) == []                         # 40% but sealed: full hand-over
    d3 = ["2026-01-05", "2026-01-06", "2026-01-07"]
    codes = ["sh.600001", "sh.600002", "sh.600003"]
    frame = lambda vals: pd.DataFrame([vals] * 3, index=d3, columns=codes)
    amount = frame([2e7, 9e8, 6e8])   # no amount threshold: the small stock still leads by height
    w = dict(close=frame([11.0] * 3), preclose=frame([10.0] * 3), high=frame([11.0] * 3), low=frame([10.0] * 3),
             amount=amount, limit_up=frame([True] * 3), height=frame([3, 1, 1]))
    aw = dict(amount=amount, turn=frame([5.0, 10.0, 20.0]))
    lu = pd.DataFrame({"B": [3.0] * 3}, index=d3)
    ts = dict(lu=lu, lu3=lu * 3, n=lu * 0 + 30, n3=lu * 0 + 90, rate3=pd.Series(0.02, index=d3),
              limit_up=frame([True] * 3), M=np.ones((1, 3), dtype=np.float32), names=["B"], cols=codes)
    sig = mainline.signals(w, aw, ts, pd.Series(True, index=codes), 2, p)
    assert sig["leader_rank"].to_dict() == {"sh.600001": 1, "sh.600003": 2}

def test_concentration_mainlines_dedupe():
    import numpy as np
    from sf import mainline
    days = ["2026-01-05", "2026-01-06", "2026-01-07"]
    codes = [f"sh.6000{i:02d}" for i in range(40)]
    lu_row = [i < 6 for i in range(40)]                 # 6 limit-ups: codes 0-5
    limit_up = pd.DataFrame([lu_row] * 3, index=days, columns=codes)
    amount = pd.DataFrame(1e8, index=days, columns=codes)
    names = ["BIG", "NARROW", "SAME"]
    M = np.zeros((3, 40), dtype=np.float32)
    M[0, :] = 1          # umbrella board: all 40, 6 limit-ups
    M[1, :8] = 1         # narrow board: 8 members, 6 limit-ups -> most concentrated
    M[2, :10] = 1        # overlaps NARROW's limit-ups entirely -> skipped as the same wave
    boards = pd.DataFrame({"board": names, "first_date": ["2000-01-01"] * 3})
    members = pd.DataFrame([(names[b], codes[c]) for b in range(3) for c in range(40) if M[b, c]], columns=["board", "code"])
    aw = dict(close=amount, amount=amount, limit_up=limit_up)
    ts = mainline.theme_stats(aw, boards, members)
    assert mainline.mainlines(ts, 2, longmai.params()) == ["NARROW"]


def test_radar_home_board_and_high_flyers():
    import numpy as np
    from sf import mainline, radar
    days = ["2026-01-05", "2026-01-06", "2026-01-07"]
    codes = [f"sh.6000{i:02d}" for i in range(40)]
    limit_up = pd.DataFrame(False, index=days, columns=codes)
    limit_up.iloc[:, 0] = True                       # 3 boards in a row
    limit_up.iloc[2, 1:5] = True                     # four first boards today
    amount = pd.DataFrame(1e8, index=days, columns=codes)
    names = ["BIG", "ICE"]
    members = pd.DataFrame([("BIG", c) for c in codes] + [("ICE", c) for c in codes[:6]], columns=["board", "code"])
    boards = pd.DataFrame({"board": names, "first_date": ["2000-01-01"] * 2})
    aw = dict(close=amount, amount=amount, limit_up=limit_up)
    ts = mainline.theme_stats(aw, boards, members)
    assert radar.home_board(ts, 2, codes[0]) == "ICE"          # the concentrated board, not the umbrella
    assert [r["board"] for r in radar.radar(ts, 2, min_lu3=3)] == ["ICE"]   # BIG covers the same wave
    assert radar.high_flyers(aw, ts, 2) == [codes[0]]          # tallest level when nothing reaches 4 boards


def test_three_session_mainlines_and_space_board():
    import numpy as np
    from sf import mainline
    days = ["2026-01-05", "2026-01-06", "2026-01-07"]
    codes = [f"sh.6000{i:02d}" for i in range(40)]
    limit_up = pd.DataFrame(False, index=days, columns=codes)
    limit_up.iloc[0, 0:4] = True
    limit_up.iloc[1, 0:3] = True
    limit_up.iloc[2, 0] = True          # ICE: 8 limit-ups over 3 sessions, only 1 today (a split day)
    amount = pd.DataFrame(1e8, index=days, columns=codes)
    members = pd.DataFrame([("BIG", c) for c in codes] + [("ICE", c) for c in codes[:8]], columns=["board", "code"])
    boards = pd.DataFrame({"board": ["BIG", "ICE"], "first_date": ["2000-01-01"] * 2})
    ts = mainline.theme_stats(dict(close=amount, amount=amount, limit_up=limit_up), boards, members)
    p6 = longmai.params()
    assert mainline.mainlines(ts, 2, p6) == ["ICE"]                       # 8 over 3 sessions, 1 today
    assert mainline.mainlines(ts, 2, dict(p6, min_theme_lu3=9)) == []
    # 空间板: tallest eligible main-board stock, outside any mainline
    d3, cs = days, ["sh.600001", "sh.600002"]
    frame = lambda vals: pd.DataFrame([vals] * 3, index=d3, columns=cs)
    w = dict(close=frame([11.0, 11.0]), preclose=frame([10.0, 10.0]), high=frame([11.0, 11.0]), low=frame([10.0, 10.0]),
             amount=frame([9e8, 9e8]), limit_up=frame([True, True]), height=frame([6, 1]))
    aw = dict(amount=frame([9e8, 9e8]), turn=frame([30.0, 10.0]))
    lu = pd.DataFrame({"X": [0.0] * 3}, index=d3)
    ts2 = dict(lu=lu, lu3=lu, share=lu, n=lu + 10, n3=lu + 30, rate3=pd.Series(0.02, index=d3),
               limit_up=frame([True, True]), M=np.zeros((1, 2), dtype=np.float32), names=["X"], cols=cs)
    sig = mainline.signals(w, aw, ts2, pd.Series(True, index=cs), 2, p6)
    assert list(sig.index) == ["sh.600001"] and sig.loc["sh.600001", "board"] == mainline.SPACE
    assert mainline.signals(w, aw, ts2, pd.Series(True, index=cs), 2, dict(p6, space_min_height=7)).empty


def test_evaluate_nodes():
    from sf import evaluate
    level = pd.Series([0.1, 0.3, 0.3, 0.6, 0.6, 0.9, None])
    direction = pd.Series([0.1, -0.1, 0.1, 0.1, -0.1, 0.0, 0.1])
    assert evaluate.nodes(level, direction).tolist() == ["冰点", "退潮", "升温", "升温", "分歧", "高潮", ""]


def test_v7_default_and_temperature_cells():
    from sf import temperature
    p = longmai.params()
    assert p["version"] == "SF_LONGMAI_V7" == longmai.VERSION and "amount_min" not in p
    assert longmai.params({"top_k": 5})["top_k"] == 5 and longmai.PARAMS["top_k"] == 3
    days = [f"d{i:03d}" for i in range(80)]
    tab = pd.DataFrame({"heat": 0.9, "pain": 0.1, "hot": "热", "sore": "顺", "cell": "热顺",
                        **{k + "_o": 0.5 for k in temperature.HEAT + temperature.PAIN}}, index=days)
    tab.loc[days[-1], "promo_rate_o"] = 0.95
    r = temperature.reading(tab, 79)
    assert r["name"] == "高潮延续" and r["sources"] == ["晋级率(分位95%)"] and 0.4 < r["loss_rate"] < 0.45
