"""Offline tests for intraday capture and features (synthetic responses, no network)."""
from __future__ import annotations

import json
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from sf import intraday, store

DAY = "2026-11-10"


def _times():
    morning = pd.date_range("09:30", "11:30", freq="1min").strftime("%H:%M").tolist()
    afternoon = pd.date_range("13:01", "15:00", freq="1min").strftime("%H:%M").tolist()
    return morning + afternoon  # 121 + 120 = 241


def _body(closes, day=DAY, vol=10, first_open="0.00"):
    """Eastmoney trends2 body: ts, open, close, high, low, volume(手), amount, avg."""
    trends, cum_v, cum_a = [], 0, 0.0
    for i, (t, c) in enumerate(zip(_times(), closes)):
        amount = c * vol * 100
        cum_v, cum_a = cum_v + vol * 100, cum_a + amount
        trends.append(f"{day} {t},{first_open if i == 0 else '0.00'},{c:.2f},{c:.2f},{c:.2f},{vol},{amount:.2f},{cum_a / cum_v:.3f}")
    return json.dumps({"data": {"code": "600001", "trends": trends}})


def _daily(code="sh.600001", pre=10.0, close=11.0, high=11.0, st="0"):
    return pd.DataFrame([dict(date=DAY, code=code, open=10.0, high=high, low=9.9, close=close, preclose=pre,
                              volume=1e6, amount=1e8, turn=5.0, pctChg=10.0, tradestatus="1", isST=st)])


def test_parse_units_missing_open_and_minutes():
    closes = [10.0] * 241
    f = intraday.parse("sh.600001", _body(closes))
    assert len(f) == 241 and f["minute"].tolist() == list(range(241))
    assert f["time"].iloc[0] == "09:30" and f["time"].iloc[121] == "13:01" and f["time"].iloc[-1] == "15:00"
    assert f["open"].isna().all()  # 0.00 opens are missing, not zero
    assert f["volume"].iloc[0] == 1000  # 10 手 = 1000 shares
    assert intraday.parse("sh.600001", json.dumps({"data": None})).empty


def test_features_seal_minutes_reopen_and_type():
    closes = [10.5] * 10 + [11.0] * 20 + [10.8] * 5 + [11.0] * 206  # first seal at minute 10, one reopen
    f = intraday.features(DAY, intraday.parse("sh.600001", _body(closes)), _daily())
    r = f.loc["sh.600001"]
    assert r["first_seal_min"] == 10 and r["reopen_count"] == 1 and r["last_seal_min"] == 35
    assert bool(r["closed_at_limit"]) and r["seal_type"] == "回封" and bool(r["complete"])
    one_word = intraday.features(DAY, intraday.parse("sh.600001", _body([11.0] * 241)), _daily())
    assert one_word.loc["sh.600001", "seal_type"] == "一字" and one_word.loc["sh.600001", "first_seal_min"] == 0
    broken = intraday.features(DAY, intraday.parse("sh.600001", _body([10.5] * 100 + [11.0] + [10.6] * 140)),
                               _daily(close=10.6))
    assert broken.loc["sh.600001", "seal_type"] == "炸板"


def test_features_st_limit_follows_date_rule():
    # ST main board: +5% is not a limit-up on 2026-11-10 (10% rule since 2026-07-06)
    f = intraday.features(DAY, intraday.parse("sh.600001", _body([10.5] * 241)), _daily(close=10.5, high=10.5, st="1"))
    assert f.loc["sh.600001", "up_limit"] == 11.0 and not bool(f.loc["sh.600001", "touched_limit"])


def test_lead_rank_inside_hot_board():
    f = pd.DataFrame({"closed_at_limit": [True, True, True, True], "first_seal_min": [30, 5, 60, 1]},
                     index=["sh.600001", "sh.600002", "sh.600003", "sh.600004"])
    members = pd.DataFrame({"board": ["A", "A", "A", "B"], "code": ["sh.600001", "sh.600002", "sh.600003", "sh.600004"]})
    r = intraday.lead_ranks(f, members)
    assert r.loc["sh.600002", "lead_rank"] == 1 and r.loc["sh.600003", "lead_rank"] == 3
    assert "sh.600004" not in r.index  # board B has fewer than 3 sealed members


def test_capture_logs_failures_and_never_rewrites(tmp_path, monkeypatch):
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    store.bar_path(DAY).parent.mkdir(parents=True, exist_ok=True)
    pd.concat([_daily("sh.600001"), _daily("sz.000002")]).to_parquet(store.bar_path(DAY), index=False)
    monkeypatch.setattr(intraday, "listed_codes", lambda day: {"list_target": [], "list_obs": []})
    monkeypatch.setattr(intraday.store, "now_sh", lambda: datetime(2026, 11, 10, 19, 0, tzinfo=store.SH))

    def fake_fetch(code):
        if code == "sz.000002":
            raise ConnectionError("timeout")
        return _body([11.0] * 241)
    monkeypatch.setattr(intraday, "fetch", fake_fetch)
    res = intraday.capture([DAY], log=lambda *_: None)[DAY]
    assert res["status"] == "partial" and res["ok"] == 1 and list(res["failed"]) == ["sz.000002"]
    log = json.loads((tmp_path / "intraday" / "capture" / f"{DAY}.json").read_text())
    assert log["failed"] == {"sz.000002": "timeout"}
    assert set(pd.read_parquet(intraday.minute_path(DAY))["code"]) == {"sh.600001"}
    assert len(list((tmp_path / "intraday" / "raw").glob("*.jsonl.gz"))) == 1
    assert intraday.capture([DAY], log=lambda *_: None)[DAY]["status"] == "cached"  # written once


def test_capture_refuses_a_session_that_has_not_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    monkeypatch.setattr(intraday.store, "now_sh", lambda: datetime(2026, 11, 10, 14, 0, tzinfo=store.SH))
    assert intraday.capture([DAY], log=lambda *_: None)[DAY]["status"].startswith("FAILED")
    assert not intraday.captured(DAY)
