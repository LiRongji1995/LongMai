"""Offline tests for the V11 candidate exam (synthetic data only, no network)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sf import lateline


def test_positions_need_250_sessions_and_mark_below():
    days = pd.Index([f"d{i:03d}" for i in range(260)])
    up = np.linspace(10, 20, 260)
    adj = pd.DataFrame({"a": up, "b": up[::-1]}, index=days)
    pos = lateline.positions({"adj": adj})
    assert pos["below"]["a"].iloc[:249].isna().all()  # unknown, never "no"
    assert pos["below"].iloc[-1].tolist() == [0.0, 1.0]
    assert pos["dd"]["a"].iloc[-1] == pytest.approx(0.0) and pos["dd"]["b"].iloc[-1] < -0.4


def test_leader1_ranks_by_height_then_turnover():
    codes = ["x", "y", "z"]
    one = lambda vals: pd.DataFrame([vals], columns=codes)  # noqa: E731
    w = dict(close=one([11.0, 11.0, 10.2]), preclose=one([10.0, 10.0, 10.0]), high=one([11.0, 11.0, 10.2]),
             low=one([10.0, 10.0, 10.0]), limit_up=one([True, True, False]), height=one([2, 3, 0]))
    aw = dict(turn=one([30.0, 5.0, 50.0]))
    ts = dict(cols=codes, names=["板"], M=np.array([[1.0, 1.0, 1.0]]))
    pool = pd.Series(True, index=codes)
    assert lateline.leader1(w, aw, ts, pool, 0, "板") == "y"  # 3 boards beats higher turnover
    assert lateline.leader1(w, aw, ts, pool.where(pool.index != "y", False), 0, "板") == "x"
    assert lateline.leader1(w, aw, ts, pd.Series(False, index=codes), 0, "板") is None


def _boards(gap=0.3, n_per_seg=(400, 160, 160)):
    rows = []
    days = {"train": "2024-01-0", "test1": "2025-06-0", "test2": "2026-03-0"}
    for seg, n in zip(lateline.SEGS, n_per_seg):
        for i in range(n):
            old, below = i % 2 == 0, i % 4 < 2  # old rows: half below, half above
            fade = float((i % 10) < (3 + (10 * gap if below and old else 0)))
            rows.append(dict(obs=f"{days[seg]}{i % 9 + 1}", day=f"{days[seg]}{i % 9 + 1}", board=f"b{i}",
                             age=15 if old else 2, seg=seg, below=float(below), dd=-0.5 if below else -0.1,
                             fade3=fade, still3=1.0, l1_lu1=0.0, l1_bigloss1=0.0))
    return pd.DataFrame(rows)


def test_q1_holds_only_with_a_replicated_gap():
    assert lateline.q1(_boards(0.3), q=0.6)["verdict"]["holds"]
    assert not lateline.q1(_boards(0.0), q=0.6)["verdict"]["holds"]
    assert lateline.q1(_boards(0.3, (40, 160, 160)), q=0.6)["verdict"]["reason"] == "样本不足"


def test_q2_flag_ignores_young_boards_and_space_rows():
    l = pd.DataFrame(dict(age=[15, 15, 2, np.nan], below=[1.0, 0.0, 1.0, 1.0], dd=-0.1))
    f = lateline.q2_flag(l, 10)
    assert f[0] == 1.0 and f[1] == 0.0 and np.isnan(f[2]) and np.isnan(f[3])


def test_compare_moves_flagged_leaders_and_report_needs_a_rule(monkeypatch):
    l = pd.DataFrame(dict(obs="2024-01-02", seg="train", lu1=[1.0, 0.0], out5=[1.0, 0.0], bigloss1=[0.0, 1.0],
                          max3=[0.2, -0.1]))
    cmp = lateline.compare(l, pd.Series([0.0, 1.0]))
    assert cmp.loc[("V9", "train"), "bigloss1"] == 0.5 and cmp.loc[("V11", "train"), "bigloss1"] == 0.0
    monkeypatch.setattr(lateline, "GROUP_RULE", None)
    with pytest.raises(ValueError):
        lateline.report(_boards(), l)


def test_build_marks_fade_and_pending():
    days = pd.Index([f"2024-01-{i:02d}" for i in range(2, 8)])
    lu = pd.DataFrame({"板": [5.0, 3.0, 1.0, 0.0, 0.0, 0.0]}, index=days)
    ts = dict(lu=lu, lu3=lu.rolling(3, min_periods=1).sum())
    adj = pd.DataFrame({"x": np.ones(6)}, index=days)
    w = dict(adj=adj, high=adj, low=adj, close=adj, limit_up=adj.astype(bool) & False,
             traded=adj.astype(bool))
    boards = pd.DataFrame(dict(obs=[days[1], days[4]], day=[days[2], days[5]], board="板", age=[3, 6], still3=np.nan,
                               leader1=["x", "x"]))
    leaders = pd.DataFrame(dict(obs=[days[1]], day=[days[2]], code=["x"], board=["板"], rank=[1]))
    b, l = lateline.build(boards, leaders, w, ts)
    assert b["fade3"].iloc[0] == 1.0  # 1 + 0 + 0 <= 0.5 x 8
    assert np.isnan(b["fade3"].iloc[1])  # t+1..t+3 not all stored: pending
    assert l["age"].iloc[0] == 3 and np.isnan(l["below"].iloc[0])  # < 250 sessions: unknown
