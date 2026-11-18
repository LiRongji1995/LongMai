"""Offline tests for the Sina 1-minute backup layer and the auction quote fallback (synthetic data, no network)."""
from __future__ import annotations

import json
from datetime import datetime

import pandas as pd
import pytest

from sf import auction, intraday, sina1, store

DAY = "2026-11-12"


def _times():
    morning = pd.date_range("09:31", "11:30", freq="1min").strftime("%H:%M").tolist()
    afternoon = pd.date_range("13:01", "15:00", freq="1min").strftime("%H:%M").tolist()
    return [t for t in morning + afternoon if t not in ("14:58", "14:59")]  # 238 bars, like the source


def _body(closes, days=(DAY,), vol=1000):
    rows = []
    for day in days:
        for t, c in zip(_times(), closes):
            rows.append(dict(day=f"{day} {t}:00", open=f"{c:.3f}", high=f"{c:.3f}", low=f"{c:.3f}", close=f"{c:.3f}",
                             volume=str(vol), amount=f"{c * vol:.4f}"))
    return "/*<script>*/\n=(" + json.dumps(rows) + ");"


def _daily(home, codes=("sh.600001", "sh.600002")):
    (home / "bars").mkdir(parents=True, exist_ok=True)
    pd.DataFrame([dict(date=DAY, code=c, open=10.0, high=11.0, low=10.0, close=11.0, preclose=10.0, volume=1e6,
                       amount=1e8, turn=1.0, pctChg=10.0, tradestatus="1", isST="0") for c in codes]) \
        .to_parquet(store.bar_path(DAY), index=False)


def test_parse_numbers_bars_like_eastmoney():
    f = sina1.parse("sh.600001", _body([10.0] * 238, days=("2026-11-11", DAY)))
    d = f[f["date"] == DAY]
    assert len(d) == sina1.BARS_PER_DAY and d["minute"].iloc[0] == 1 and d["minute"].iloc[-1] == 240
    assert d.loc[d["time"] == "13:01", "minute"].item() == 121 and (d["avg"] - 10.0).abs().max() < 1e-9
    assert sina1.parse("sh.600001", "null").empty


def test_features_use_minute_numbers_not_positions(tmp_path, monkeypatch):
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    _daily(tmp_path)
    closes = [10.5] * 9 + [11.0] * 229  # sealed from the 10th bar = 09:40
    sina1.capture(DAY, ["sh.600001"], log=lambda *_: None, fetcher=lambda c: _body(closes), pause=0)
    f = sina1.features(DAY)
    r = f.loc["sh.600001"]
    assert r["first_seal_min"] == 10 and r["closed_at_limit"] and r["complete"]
    assert pd.isna(r["auction_vol_share"])  # no separate auction bar in this source
    assert intraday.clock(r["first_seal_min"]) == "09:40"


def test_capture_writes_once_logs_failures_and_gives_up(tmp_path, monkeypatch):
    monkeypatch.setenv("SF_HOME", str(tmp_path))

    def fetcher(code):
        if code == "sh.600003":
            raise ConnectionError("dropped")
        return _body([10.0] * 238) if code == "sh.600001" else _body([10.0] * 238, days=("2026-11-11",))
    e = sina1.capture(DAY, ["sh.600001", "sh.600002", "sh.600003"], log=lambda *_: None, fetcher=fetcher, pause=0)
    assert e["ok"] == 1 and e["absent"] == ["sh.600002"] and list(e["failed"]) == ["sh.600003"]
    assert sina1.covered(DAY) == {"sh.600001"}
    again = sina1.capture(DAY, ["sh.600001"], log=lambda *_: None, fetcher=fetcher, pause=0)
    assert again["requested"] == 0  # stored stocks are never fetched or written again
    calls = []

    def refuse(code):
        calls.append(code)
        raise ConnectionError("refused")
    codes = [f"sz.{i:06d}" for i in range(30)]
    e = sina1.capture(DAY, codes, log=lambda *_: None, fetcher=refuse, pause=0)
    assert len(calls) == sina1.GIVE_UP_AFTER and len(e["failed"]) == 30
    assert sum(v == sina1.NOT_REQUESTED for v in e["failed"].values()) == 30 - sina1.GIVE_UP_AFTER


def test_list_notes_prefer_sina_over_five_minute_bars(tmp_path, monkeypatch):
    from sf import minute5
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    _daily(tmp_path)
    sina1.capture(DAY, ["sh.600001"], log=lambda *_: None, fetcher=lambda c: _body([11.0] * 238), pause=0)
    monkeypatch.setattr(minute5, "notes", lambda day, codes: {c: "5分钟线（粗）" for c in codes})
    notes, status, _ = intraday.list_notes(DAY, ["sh.600001", "sh.600002"], members=pd.DataFrame())
    assert notes["sh.600001"] == "新浪1分钟线：一字封死" and notes["sh.600002"] == "5分钟线（粗）"
    assert status["sina"] == 1 and status["coarse"] == 1 and status["missing"] == 0


def _q(codes, opens):
    return pd.DataFrame({"code": codes, "open": opens, "preclose": [10.0] * len(codes), "price": opens,
                         "volume": [1.0] * len(codes), "amount": [1e7] * len(codes)})


def test_auction_quotes_fall_back_to_sina_for_missing_codes():
    asked = []

    def backup(codes):
        asked.append(codes)
        return _q(codes + ["sz.000009"], [10.2] * (len(codes) + 1))
    q = auction.fetch_quotes(["sh.600001", "sz.000002"], primary=lambda c: _q(["sh.600001"], [10.5]),
                             backup=backup, log=lambda *_: None)
    assert asked == [["sz.000002"]] and list(q["code"]) == ["sh.600001", "sz.000002"]  # only the asked codes
    assert q.set_index("code")["source"].to_dict() == {"sh.600001": "eastmoney", "sz.000002": "sina"}

    def down(codes):
        raise ConnectionError("dropped")
    q = auction.fetch_quotes(["sh.600001"], primary=down, backup=down, log=lambda *_: None)
    assert q.empty and set(q.attrs["errors"]) == {"eastmoney", "sina"}  # missing, never filled in


def test_parse_sina_spot_codes_units_and_unopened():
    s = pd.DataFrame({"代码": ["sh600001", "sz000002", "bj830001"], "名称": ["甲", "乙", "丙"], "最新价": [10.5, 0, 1],
                      "昨收": [10.0, 20.0, 1], "今开": [10.5, 0.0, 1], "成交量": [12300, 0, 1], "成交额": [1.3e5, 0, 1]})
    q = auction.parse_sina(s, ["sh.600001", "sz.000002"])
    assert list(q["code"]) == ["sh.600001", "sz.000002"] and q["volume"].iloc[0] == 123
    assert pd.isna(q["open"].iloc[1])  # not opened yet: unknown, never 0


def test_snapshot_header_names_the_backup_source(tmp_path, monkeypatch):
    from tests.sf.test_auction import _saved_list
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    monkeypatch.setattr(auction, "ADOPTED", {})
    _saved_list(tmp_path)
    q = _q(["sh.600001", "sz.000002"], [11.0, 10.0]).assign(source=["eastmoney", "sina"])
    q.attrs["errors"] = {}
    snap = auction.snapshot(quotes=q, now=datetime(2026, 11, 12, 9, 26, tzinfo=store.SH))
    assert snap["sources"]["counts"] == {"eastmoney": 1, "sina": 1}
    assert any(x.startswith("行情源：东方财富 1 只，新浪 1 只") for x in auction.lines(snap))


def test_page_does_not_flag_gaps_that_sina_filled(tmp_path, monkeypatch):
    from sf import page
    from tests.sf import test_intraday as ti
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    ti._store_day(tmp_path, monkeypatch, ["sh.600001", "sz.000002"])

    def fetch(code):
        if code == "sz.000002":
            raise ConnectionError("dropped")
        return ti._body([11.0] * 241)
    monkeypatch.setattr(intraday, "fetch", fetch)
    intraday.capture([ti.DAY], log=lambda *_: None)
    assert intraday.missing_codes(ti.DAY) == ["sz.000002"]
    assert any("还缺 1 只" in a for a in page.alerts({}, []))
    sina1.capture(ti.DAY, ["sz.000002"], log=lambda *_: None, fetcher=lambda c: _body([11.0] * 238, days=(ti.DAY,)),
                  pause=0)
    assert not any("盘中分时" in a for a in page.alerts({}, []))
