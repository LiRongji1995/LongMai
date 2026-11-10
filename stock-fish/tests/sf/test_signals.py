"""Offline tests for seats (LHB seat detail), mood (sentiment switch) and abnormal (异动) accounting."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sf import abnormal, mood, seats, strategy

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from test_sf import DAYS, flat, make_bars  # noqa: E402


# ------------------------------------------------------------------ seats

def test_code_mapping_by_exchange():
    codes = seats.to_code(pd.Series(["600000", "000001", "300750", "688001", "920405", "430047"]))
    assert codes.tolist() == ["sh.600000", "sz.000001", "sz.300750", "sh.688001", "bj.920405", "bj.430047"]


def test_seat_types():
    names = pd.Series(["机构专用", "深股通专用", "东方财富证券股份有限公司拉萨团结路第二证券营业部", "华鑫证券上海分公司"])
    assert seats.seat_type(names).tolist() == ["INSTITUTION", "NORTHBOUND", "RETAIL", "BROKER"]


def _rows(events):
    return pd.DataFrame([dict(date=d, code=c, seat_code=s, seat="营业部" + s, seat_type="BROKER", reason="r",
                              amount=1e8, buy=max(n, 0), sell=max(-n, 0), net=n, trade_id=f"{d}{c}")
                         for d, c, s, n in events])


def test_seat_skill_uses_only_known_outcomes():
    sessions = DAYS[:6]
    rows = _rows([(DAYS[0], "sh.600001", "A", 1e6), (DAYS[1], "sh.600002", "A", 1e6)])
    outcome = pd.Series([0.05, -0.01], index=pd.MultiIndex.from_tuples(
        [(DAYS[0], "sh.600001"), (DAYS[1], "sh.600002")], names=["date", "code"]))
    skill = seats.seat_skill(rows, outcome, sessions, lag=2).set_index("date")
    # The day-0 event's outcome (open d1 -> open d2) is first usable on day 2, the day-1 event on day 3.
    assert DAYS[1] not in skill.index
    assert skill.loc[DAYS[2], "skill_avg"] == pytest.approx(0.05)
    assert skill.loc[DAYS[3], "skill_n"] == 2
    assert skill.loc[DAYS[3], "skill_avg"] == pytest.approx(0.02)


def test_seat_skill_ignores_net_sellers_and_attach_is_as_of():
    sessions = DAYS[:8]
    rows = _rows([(DAYS[0], "sh.600001", "A", 1e6), (DAYS[0], "sh.600001", "B", -1e6),
                  (DAYS[5], "sh.600003", "A", 2e6)])
    outcome = pd.Series([0.04], index=pd.MultiIndex.from_tuples([(DAYS[0], "sh.600001")], names=["date", "code"]))
    skill = seats.seat_skill(rows, outcome, sessions)
    assert set(skill["seat_code"]) == {"A"}
    attached = seats.attach_skill(rows, skill)
    later = attached[(attached["date"] == DAYS[5]) & (attached["seat_code"] == "A")].iloc[0]
    first = attached[(attached["date"] == DAYS[0]) & (attached["seat_code"] == "A")].iloc[0]
    assert later["skill_avg"] == pytest.approx(0.04)
    assert pd.isna(first["skill_avg"])  # nothing known yet on the event day itself


def test_stock_features_split_by_seat_type():
    rows = _rows([(DAYS[0], "sh.600001", "A", 3e6), (DAYS[0], "sh.600001", "B", 1e6)]).assign(trade_id="1")
    rows.loc[1, "seat_type"] = "INSTITUTION"
    rows["skill_n"], rows["skill_avg"] = [20, np.nan], [0.01, np.nan]
    f = seats.stock_features(rows).iloc[0]
    assert f["skilled_buyers"] == 1 and f["inst_buyers"] == 1
    assert f["inst_net_ratio"] == pytest.approx(0.01)
    assert f["buy_concentration"] == pytest.approx(0.75)


# ------------------------------------------------------------------ abnormal

def test_trigger3_resets_after_trigger():
    dev = pd.DataFrame({"x": [0.09, 0.09, 0.09, 0.09, 0.09, 0.09, 0.0]}, index=DAYS[:7])
    cum, trig, room = abnormal.trigger3(dev, 0.20)
    assert trig["x"].tolist() == [False, False, True, False, False, True, False]
    assert cum["x"].iloc[3] == pytest.approx(0.09)  # counting restarted after day 2
    assert room["x"].iloc[1] == pytest.approx(0.20 - 0.18)


def test_deviation_uses_exchange_benchmark():
    pct = pd.DataFrame({"sh.600001": [0.05], "sz.000001": [0.05]}, index=[DAYS[0]])
    idx = pd.DataFrame({"sh.000001": [0.01], "sz.399001": [0.02]}, index=[DAYS[0]])
    dev = abnormal.deviation(pct, idx)
    assert dev.loc[DAYS[0], "sh.600001"] == pytest.approx(0.04)
    assert dev.loc[DAYS[0], "sz.000001"] == pytest.approx(0.03)


# ------------------------------------------------------------------ mood

def test_mood_indicators_relay_premium_and_promotion():
    up = flat()
    up[-2], up[-1] = 11.0, 12.1  # limit-up, then limit-up again (promoted)
    fade = flat()
    fade[-2], fade[-1] = 11.0, 10.34  # limit-up, then -6% (big loss)
    w = strategy.panel(make_bars({"sh.600001": up, "sh.600002": fade, "sh.600003": flat()}))
    ind = mood.indicators(w)
    last = ind.iloc[-1]
    assert last["promo_rate"] == pytest.approx(0.5)
    assert last["big_loss"] == 1
    assert last["prem_lu"] == pytest.approx((0.10 + (10.34 / 11 - 1)) / 2)
    assert ind.iloc[-2]["lu_count"] == 2


def test_mood_composite_tiers_need_history():
    pct = pd.DataFrame({k: np.linspace(0, 1, 30) for k in mood.SIGNS}, index=DAYS[:30])
    comp = mood.composite(pct)
    assert set(comp["tier"]) <= {"低", "中", "高", "UNKNOWN"}
    nan = mood.composite(pct.iloc[:3] * np.nan)
    assert (nan["tier"] == "UNKNOWN").all()


def test_one_listing_per_stock_day_and_institutions_kept():
    rows = _rows([(DAYS[0], "sh.600001", "INST", 1e6), (DAYS[0], "sh.600001", "INST", 2e6)])
    rows["trade_id"] = "day"
    three = rows.assign(trade_id="3d", reason="连续三个交易日内，涨幅偏离值累计达到20%的证券")
    kept = seats.stock_seat_rows(pd.concat([rows, three], ignore_index=True))
    assert len(kept) == 2 and set(kept["trade_id"]) == {"day"}
