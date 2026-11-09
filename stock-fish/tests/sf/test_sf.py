"""Offline tests for the lean `sf` package (synthetic data only, no network)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sf import mainline, outcomes, research, strategy

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


# ------------------------------------------------------------------ outcomes

def test_outcomes_entry_at_open_and_pending():
    closes = flat()
    bars = make_bars({"sh.600001": closes, "sh.600002": closes})
    target = DAYS[-3]
    bars.loc[(bars.code == "sh.600001") & (bars.date == target), "open"] = 10.2
    bars.loc[(bars.code == "sh.600001") & (bars.date == DAYS[-2]), ["open", "close", "high"]] = [10.71, 10.5, 10.71]
    bars.loc[(bars.code == "sh.600001") & (bars.date == DAYS[-1]), "preclose"] = 10.5
    bars.loc[(bars.code == "sh.600002") & (bars.date == target), ["open", "high"]] = 11.0  # opens at limit
    w = strategy.panel(bars)
    lab = outcomes.label(w, target, ["sh.600001", "sh.600002"])
    assert lab.loc["sh.600001", "r_n1o"] == pytest.approx(10.71 / 10.2 - 1)
    assert not lab.loc["sh.600001", "unbuyable"] and lab.loc["sh.600002", "unbuyable"]
    pending = outcomes.label(w, DAYS[-1], ["sh.600001"])
    assert np.isnan(pending.loc["sh.600001", "r_n1o"]) and not pending.loc["sh.600001", "mature"]


def test_stats_exclude_unbuyable_and_apply_cost():
    frame = pd.DataFrame({"unbuyable": [False, False, True], "r_n1o": [0.02, -0.01, 0.10],
                          "limit_up_T": [False] * 3, "broken_T": [False] * 3, "mae2": [0.0] * 3})
    s = outcomes.stats(frame, cost=0.0)
    assert s["buyable"] == 2 and s["avg"] == pytest.approx(0.005) and s["hit_rate"] == pytest.approx(0.5)


# ------------------------------------------------------------------ research

def test_research_day_level_statistics():
    rows = []
    for i, day in enumerate(DAYS):
        for j in range(5):  # five correlated stocks per day count as one day
            rows.append(dict(obs=day, code=f"c{j}", flag=True, unbuyable=False, r_n1c=0.01 + 0.001 * (i % 3)))
    data = pd.DataFrame(rows)
    out = research.evaluate(data, "flag", ret="r_n1c", cost=0.0)
    assert out["train_days"] + out["test1_days"] + out["test2_days"] == len(DAYS)
    assert out["train_avg"] > 0.0


# ------------------------------------------------------------------ mainline

def test_mainline_trade_exits_on_trend_break():
    n = len(DAYS)
    leader = flat()
    leader[-8:] = [11.0, 12.1, 13.31, 14.64, 14.0, 13.0, 12.0, 11.5]
    bars = make_bars({"sh.600001": leader, "sh.600002": flat()})
    entry_day = DAYS[n - 5]
    bars.loc[(bars.code == "sh.600001") & (bars.date == entry_day), ["open", "low"]] = 13.6
    w = strategy.panel(bars)
    ts = {"lu": pd.DataFrame({"T": [5.0] * n}, index=DAYS)}
    t = n - 6  # signal on the 3rd limit-up day, entry next open
    trade = mainline.simulate(w, ts, t, "sh.600001", "T", 5.0, mainline.params(), cost=0.0)
    assert trade["status"] == "CLOSED"
    assert trade["exit_reason"] in ("TREND", "STOP")
    assert trade["ret"] < 0.10


def test_mainline_skips_open_at_limit():
    leader = flat()
    leader[-3:] = [11.0, 12.1, 13.31]
    bars = make_bars({"sh.600001": leader})
    w = strategy.panel(bars)
    ts = {"lu": pd.DataFrame({"T": [5.0] * len(DAYS)}, index=DAYS)}
    trade = mainline.simulate(w, ts, len(DAYS) - 3, "sh.600001", "T", 5.0, mainline.params())
    assert trade == {"status": "NO_ENTRY", "reason": "OPEN_AT_LIMIT"}
