"""Offline tests for the data-integrity rules: price limits by date, missing vs empty captures,
snapshot completeness, theme refresh failures and the run manifest (synthetic data, no network)."""
from __future__ import annotations

import sys
import types

import numpy as np
import pandas as pd
import pytest

from sf import listing, longmai, manifest, pools, seats, store, strategy, themes


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    return tmp_path


# ------------------------------------------------------------------ price limits

def test_st_limit_ratio_switches_on_2026_07_06():
    days = ["2026-07-03", "2026-07-06"]
    cols = ["sh.600001", "sz.000001", "sz.300001", "sh.688001"]
    st = np.array([[True, False, True, True]] * 2)
    ratio = strategy.limit_ratio(days, cols, st)
    assert ratio[0].tolist() == [0.05, 0.10, 0.20, 0.20]
    assert ratio[1].tolist() == [0.10, 0.10, 0.20, 0.20]


def test_st_main_board_plus_five_is_not_limit_up_after_switch():
    rows = []
    for day, close in (("2026-07-02", 10.0), ("2026-07-03", 10.5), ("2026-07-06", 11.03)):
        rows.append(dict(date=day, code="sh.600001", open=close, high=close, low=close, close=close,
                         preclose=10.0 if day == "2026-07-02" else rows[-1]["close"], volume=1e6, amount=1e8,
                         turn=5.0, tradestatus="1", isST="1"))
    w = strategy.panel(pd.DataFrame(rows))
    assert w["limit_up"]["sh.600001"].tolist() == [False, True, False]  # +5% is a limit-up only before 07-06


# ------------------------------------------------------------------ missing vs empty

def _seat_frame(codes):
    return pd.DataFrame(dict(symbol=[c[3:] for c in codes], seat_code="1", seat="某营业部", reason="日涨幅偏离值达7%",
                             pct=10.0, amount=1e8, buy=1e7, sell=0.0, net=1e7, trade_id="1", code=codes,
                             seat_type="BROKER", date="2026-11-10", in_buy=True, in_sell=False))


def test_seat_capture_refuses_empty_or_one_exchange(home, monkeypatch):
    day = "2026-11-10"
    monkeypatch.setattr(seats, "fetch", lambda d: _seat_frame([]))
    with pytest.raises(RuntimeError, match="incomplete"):
        seats.capture(day)
    monkeypatch.setattr(seats, "fetch", lambda d: _seat_frame(["sz.000001"]))
    with pytest.raises(RuntimeError, match="no sh"):
        seats.capture(day)
    assert not seats.path(day).exists() and seats.load(day) is None
    monkeypatch.setattr(seats, "fetch", lambda d: _seat_frame(["sz.000001", "sh.600001"]))
    assert seats.capture(day) == "2 rows"
    assert seats.capture(day) == "cached" and seats.captured_days() == [day]


def test_legacy_empty_seat_file_is_not_captured(home):
    day = "2026-11-09"
    pd.DataFrame(columns=["code"]).to_parquet(seats.path(day))
    assert not seats.is_captured(day)
    assert seats.load(day) is None and seats.day_features(day, pd.DataFrame()) is None
    assert seats.captured_days() == []


def test_pool_capture_keeps_empty_limit_up_pool_uncaptured(home, monkeypatch):
    monkeypatch.setattr(pools, "_fetch", lambda kind, day: pd.DataFrame())
    status = pools.capture("2026-11-10", kinds=("zt", "dtgc"), log=lambda *_: None)
    assert status["zt"].startswith("FAILED") and pools.load("zt", "2026-11-10") is None
    assert status["dtgc"] == "0 rows" and pools.load("dtgc", "2026-11-10").empty


def test_abnormal_unknown_is_a_note():
    days = ["2026-01-05", "2026-01-06"]
    code = "sh.600001"
    frame = pd.DataFrame({code: [9.0, 9.0]}, index=days)
    ctx = dict(severe=dict(events_left=frame), abnormal_unknown=pd.Series([False, True], index=days))
    assert longmai.notes(ctx, 0, code, None) == []
    assert any("异动状态未知" in n for n in longmai.notes(ctx, 1, code, None))


def test_header_names_missing_inputs():
    result = dict(gate=dict(slots=0, mood_tier="中", mood_level=0.5, reg_tier="中", szse_cases=1),
                  observation="2026-11-10", target="2026-11-11", strategy_version=longmai.VERSION, mainlines=[],
                  seats="not captured", abnormal_index_ok=False)
    line = next(x for x in listing.header(result) if x.startswith("数据缺失"))
    assert "龙虎榜席位未采集" in line and "指数行情不全" in line


# ------------------------------------------------------------------ snapshot completeness

def _snapshot(n, missing_close=0):
    close = [10.0] * n
    for i in range(missing_close):
        close[i] = ""
    return pd.DataFrame(dict(code=[f"sh.{600000 + i}" for i in range(n)], open=10.0, high=10.0, low=10.0,
                             close=close, preclose=10.0, volume=1e5, amount=1e6, turn=1.0, pctChg=0.0,
                             tradestatus="1", isST="0"))


def test_partial_snapshot_is_not_stored(home):
    store.write_session("2026-11-09", _snapshot(1000), "t", "TEST")
    with pytest.raises(ValueError, match="partial"):
        store.write_session("2026-11-10", _snapshot(900), "t", "TEST")
    assert not store.have("2026-11-10")
    with pytest.raises(ValueError, match="no close"):
        store.write_session("2026-11-10", _snapshot(1000, missing_close=50), "t", "TEST")
    store.write_session("2026-11-10", _snapshot(990), "t", "TEST")  # small changes (delistings) are fine
    assert store.have("2026-11-10")


# ------------------------------------------------------------------ themes

def _fake_akshare(fail: set[str]):
    def cons(name):
        if name in fail:
            raise ConnectionError("boom")
        return pd.DataFrame({"代码": ["600001", "000002"]})
    ak = types.SimpleNamespace(
        stock_board_industry_name_em=lambda: pd.DataFrame({"板块名称": ["A", "B", "C"], "板块代码": ["1", "2", "3"]}),
        stock_board_industry_cons_em=cons,
        stock_board_industry_hist_em=lambda **k: pd.DataFrame({"日期": ["2020-01-02"]}),
        stock_board_concept_name_em=None, stock_board_concept_cons_em=None, stock_board_concept_hist_em=None)
    return ak


def test_theme_refresh_carries_previous_members_on_failure(home, monkeypatch):
    monkeypatch.setattr(themes.time, "sleep", lambda s: None)
    (home / "meta").mkdir(exist_ok=True)
    pd.DataFrame(dict(board=["B"], kind=["industry"], board_code=["2"], first_date=["2019-01-02"], n_members=[1],
                      captured_at=["old"])).to_parquet(home / "meta" / "theme_boards.parquet")
    pd.DataFrame(dict(board=["B"], kind=["industry"], code=["sh.600009"])).to_parquet(home / "meta" / "theme_members.parquet")
    monkeypatch.setitem(sys.modules, "akshare", _fake_akshare(fail={"B", "C"}))
    result = themes.refresh(kinds=("industry",), log=lambda *_: None)
    assert result["carried"] == [("industry", "B")] and [f[1] for f in result["failures"]] == ["C"]
    boards, members = themes.load()
    b = boards.set_index("board")
    assert b.loc["B", "source"] == "carried" and b.loc["B", "captured_at"] == "old" and "C" not in b.index
    assert members.loc[members["board"] == "B", "code"].tolist() == ["sh.600009"]
    assert len(list((home / "meta" / "archive").glob("theme_members-*.parquet"))) == 1


# ------------------------------------------------------------------ manifest

def test_manifest_archives_mutable_inputs(home):
    notes = home / "meta" / "hype_notes.csv"
    notes.parent.mkdir(parents=True)
    notes.write_text("date,code,theme,source\n2026-11-10,sz.001379,商业航天,\n")
    first = manifest.build(["2026-11-09", "2026-11-10"])
    assert first["versions"]["limits"] == strategy.LIMITS_VERSION and first["bars"]["sessions"] == 2
    assert first["inputs"]["meta/theme_members.parquet"] is None  # absent input is stated, not invented
    notes.write_text("date,code,theme,source\n")
    second = manifest.build(["2026-11-10"])
    ids = {first["inputs"]["meta/hype_notes.csv"], second["inputs"]["meta/hype_notes.csv"]}
    assert len(ids) == 2
    archived = sorted(p.name for p in (home / "meta" / "archive").iterdir())
    assert archived == sorted(f"hype_notes-{i}.csv" for i in ids)
