"""Offline tests for the lean `sf` package (synthetic data only, no network)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sf import evaluate, listing, review, store, strategy

DAYS = [f"2026-01-{d:02d}" for d in range(1, 31)] + [f"2026-02-{d:02d}" for d in range(1, 11)]


def make_bars(paths: dict[str, list[float]], amount=2e8, turn=6.0, st=None, days=DAYS) -> pd.DataFrame:
    """paths: code -> list of closes; open=high=low=close unless overridden later."""
    rows = []
    for code, closes in paths.items():
        prev = closes[0]
        for i, (day, close) in enumerate(zip(days, closes)):
            rows.append(dict(date=day, code=code, open=close, high=close, low=close, close=close,
                             preclose=prev if i else close, volume=1e6, amount=amount, turn=turn,
                             pctChg=(close / prev - 1) * 100 if i else 0.0, tradestatus="1",
                             isST="1" if st and code in st else "0"))
            prev = close
    return pd.DataFrame(rows)


def flat(n=len(DAYS), price=10.0):
    return [price] * n


# ------------------------------------------------------------------ panel / prices

def test_limit_price_rounding_and_flags():
    closes = flat()
    closes[-1] = 11.0  # +10% from 10.00 -> limit-up
    bars = make_bars({"sh.600001": closes, "sh.600002": flat(), "sh.600003": closes}, st={"sh.600003"})
    w = strategy.panel(bars)
    last = DAYS[-1]
    assert w["up_limit"].loc[last, "sh.600001"] == pytest.approx(11.0)
    assert bool(w["limit_up"].loc[last, "sh.600001"])
    assert w["up_limit"].loc[last, "sh.600003"] == pytest.approx(10.5)  # ST: 5%
    assert bool(w["one_price_up"].loc[last, "sh.600001"])  # open=high=low=close


def test_adjusted_chain_ignores_ex_rights_drop():
    closes = flat()
    bars = make_bars({"sh.600001": closes})
    # Ex-rights on day 20: close halves but exchange preclose is also halved -> no real return.
    bars.loc[bars.date >= DAYS[20], ["open", "high", "low", "close"]] = 5.0
    bars.loc[bars.date == DAYS[20], "preclose"] = 5.0
    bars.loc[bars.date > DAYS[20], "preclose"] = 5.0
    w = strategy.panel(bars)
    adj = w["adj"]["sh.600001"]
    assert adj.iloc[-1] / adj.iloc[0] == pytest.approx(1.0)


def test_missing_row_is_unknown_not_zero():
    bars = make_bars({"sh.600001": flat(), "sh.600002": flat()})
    bars = bars[~((bars.code == "sh.600002") & (bars.date == DAYS[-3]))]
    w = strategy.panel(bars)
    pool = pd.Series(True, index=w["close"].columns)
    feat = strategy.features(w, len(DAYS) - 1, pool)
    assert np.isnan(feat.loc["sh.600002", "ret5"])  # window touches a missing session
    assert feat.loc["sh.600001", "ret5"] == pytest.approx(0.0)


def test_board_height_counts_consecutive_limit_ups():
    closes = flat()
    closes[-3:] = [11.0, 12.1, 13.31]
    w = strategy.panel(make_bars({"sh.600001": closes}))
    assert w["height"].loc[DAYS[-1], "sh.600001"] == 3
    assert w["height"].loc[DAYS[-4], "sh.600001"] == 0


# ------------------------------------------------------------------ rules

def test_strong_start_gate_diagnostics():
    peers = {f"sh.6000{i:02d}": flat() for i in range(10, 30)}
    start = flat()
    start[-1] = 10.8  # +8%
    bars = make_bars({"sh.600001": start, **peers})
    bars.loc[(bars.code == "sh.600001") & (bars.date == DAYS[-1]), ["low", "amount"]] = [10.0, 5e8]
    w = strategy.panel(bars)
    pool = pd.Series(True, index=w["close"].columns)
    f = strategy.evaluate(strategy.features(w, len(DAYS) - 1, pool), strategy.DEFAULT_PARAMS)
    row = f.loc["sh.600001"]
    assert bool(row["STRONG_START"]) and row["STRONG_START_failed"] == 0
    assert bool(row["r2_selected"])  # turnover 6% >= 5%
    # raise the ret1 threshold -> exactly one failed gate -> near miss
    p = strategy.params({"STRONG_START": {"ret1_min": 0.09}})
    g = strategy.evaluate(strategy.features(w, len(DAYS) - 1, pool, p), p).loc["sh.600001"]
    assert not g["STRONG_START"] and g["near_miss"] == "STRONG_START:ret1"


def test_low_turnover_fails_r2_participation():
    start = flat()
    start[-1] = 10.8
    bars = make_bars({"sh.600001": start, **{f"sh.6000{i:02d}": flat() for i in range(10, 30)}}, turn=1.0)
    bars.loc[(bars.code == "sh.600001") & (bars.date == DAYS[-1]), ["low", "amount"]] = [10.0, 5e8]
    w = strategy.panel(bars)
    f = strategy.evaluate(strategy.features(w, len(DAYS) - 1, pd.Series(True, index=w["close"].columns)))
    assert bool(f.loc["sh.600001", "r1_match"]) and not bool(f.loc["sh.600001", "r2_selected"])


# ------------------------------------------------------------------ wide panels

def test_wide_matches_pivot_including_missing_cells():
    bars = make_bars({"sh.600001": flat(), "sz.000001": flat(price=5.0)})
    bars = bars.drop(bars.index[(bars.code == "sz.000001") & (bars.date == DAYS[3])])
    bars.loc[bars.index[0], "isST"] = "1"
    w = store.wide(bars, ("close", "amount"), {"st": "isST"})
    ref = bars.pivot(index="date", columns="code", values="close").sort_index()
    pd.testing.assert_frame_equal(w["close"], ref, check_names=False)
    assert np.isnan(w["close"].loc[DAYS[3], "sz.000001"]) and not w["st"].loc[DAYS[3], "sz.000001"]
    assert w["st"].iloc[0, 0] and w["st"].to_numpy().sum() == 1


# ------------------------------------------------------------------ entry-free outcomes

def test_stock_outcomes_from_close_and_pending():
    closes = flat()
    closes[-5:] = [11.0, 12.1, 13.31, 12.0, 11.0]   # three limit-ups after DAYS[-6], then down
    bars = make_bars({"sh.600001": closes, "sh.600002": flat()})
    w = strategy.panel(bars)
    out = evaluate.stock_outcomes(w)
    t = DAYS[-6]
    assert out["lu1"].loc[t, "sh.600001"] == 1.0 and out["lu5"].loc[t, "sh.600001"] == 3
    assert out["max3"].loc[t, "sh.600001"] == pytest.approx(0.331)
    assert out["out5"].loc[t, "sh.600001"] == 1.0 and out["out5"].loc[t, "sh.600002"] == 0.0
    assert out["bigloss1"].loc[DAYS[-3], "sh.600001"] == 1.0          # 13.31 -> 12.0 is -9.8%
    assert np.isnan(out["out5"].loc[DAYS[-2], "sh.600001"])           # outcomes not yet known: pending


def test_saved_runs_and_review_recall(tmp_path, monkeypatch):
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    assert listing.latest_run() is None and listing.all_runs() == []
    run = tmp_path / "lists" / "2026-02-10" / "20260209T120000Z"
    run.mkdir(parents=True)
    (run.parent / "LATEST").write_text(run.name)
    assert listing.latest_run() == run and listing.all_runs() == []   # no fact table yet
    rows = pd.DataFrame({"code": ["a", "b", "c"], "group": ["leader", None, "ladder"],
                         "baseline": [True, True, False], "name": ["A", "B", "C"],
                         "lu1": [1.0, 0.0, 0.0], "max3": [0.3, 0.0, 0.1], "out5": [1.0, 1.0, 0.0],
                         "bigloss1": [0.0, 0.0, 1.0], "lu5": [3, 4, 0], "obs": "2026-02-09"})
    rep = review.report(rows)
    assert rep["winners"] == 2 and rep["recall"] == 0.5 and rep["missed"][0]["name"] == "B"
    assert rep["stats"].loc["leader", "lu1"] == 1.0 and rep["stats"].loc["baseline", "n"] == 2


def test_hype_notes_window_and_tag(tmp_path, monkeypatch):
    from sf import notes
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    assert notes.active(DAYS[-1], DAYS) == {}
    notes.add(DAYS[20], "sz.001379", "旧题材")
    notes.add(DAYS[25], "sz.001379", "商业航天", "https://example.com")
    notes.add(DAYS[-1], "sh.600001", "未来新闻")          # dated after the observation: not visible
    act = notes.active(DAYS[26], DAYS)
    assert act["sz.001379"]["theme"] == "商业航天" and "sh.600001" not in act
    assert notes.active(DAYS[25 + notes.MAX_AGE], DAYS) == {}   # expired after MAX_AGE sessions
    assert notes.tag("sz.001379", "建筑节能", act) == f"·炒作商业航天(新闻{DAYS[25][5:]})·板块建筑节能"
    assert notes.tag("sz.000002", "建筑节能", act) == "·板块建筑节能"
    assert notes.tag("sz.000002", None, act, "·题材未明") == "·题材未明"
