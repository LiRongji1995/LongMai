"""Offline tests for threads, descriptive context, journal, page and schedule (synthetic data, no network)."""
from __future__ import annotations

import json
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from sf import context, journal, page, schedule, store, themes, threads


# ------------------------------------------------------------------ threads

def _ts(days=8):
    idx = pd.Index([f"2026-01-{i + 2:02d}" for i in range(days)])
    cols = ["a1", "a2", "a3", "b1", "b2"]
    lu = pd.DataFrame(False, index=idx, columns=cols)
    lu.loc[idx[0], ["a1", "a2"]] = True
    lu.loc[idx[1], ["a2", "a3"]] = True
    lu.loc[idx[2], ["a3"]] = True
    lu.loc[idx[5], ["b1", "b2"]] = True
    names = ["传媒", "影视", "光伏"]
    M = np.array([[1, 1, 0, 0, 0], [0, 1, 1, 0, 0], [0, 0, 0, 1, 1]], dtype=float)
    return dict(lu=pd.DataFrame(0.0, index=idx, columns=names), limit_up=lu, M=M, names=names, cols=cols)


def test_threads_link_renamed_boards_and_end_after_a_gap():
    ts = _ts()
    lines = {0: ["传媒"], 1: ["影视"], 2: ["影视"], 5: ["光伏"]}
    at2 = threads.build(ts, 2, {}, lambda k: lines.get(k, []))
    assert len(at2) == 1 and at2[0]["boards"] == ["传媒", "影视"] and at2[0]["age"] == 3
    assert at2[0]["peak"] == 2 and at2[0]["peak_age"] == 1
    at5 = threads.build(ts, 5, {}, lambda k: lines.get(k, []))
    assert [t["boards"] for t in at5] == [["光伏"]]  # the media thread ended (no link for > MAX_GAP sessions)
    assert "第3个交易日" in threads.describe(at2[0])


# ------------------------------------------------------------------ context

def test_position_unknown_without_250_sessions_and_high_fate():
    idx = pd.Index([f"d{i:03d}" for i in range(260)])
    adj = pd.DataFrame({"x": np.linspace(10, 20, 260), "y": np.linspace(20, 10, 260)}, index=idx)
    ma, hi = context.position({"adj": adj}, 259)
    assert ma["x"] > 0 > ma["y"] and context.position_text(ma["x"], hi["x"]).startswith("年线上")
    ma0, _ = context.position({"adj": adj}, 100)
    assert context.position_text(ma0["x"], np.nan) == "年线未知（不足250日）"
    days = pd.Index(["t0", "t1", "t2", "t3", "t4", "t5"])
    up = pd.DataFrame({"s": [True] * 5 + [False], "z": [False] * 6}, index=days)
    aw = dict(limit_up=up, close=pd.DataFrame({"s": [1.0] * 5 + [0.9], "z": 1.0}, index=days),
              preclose=pd.DataFrame({"s": 1.0, "z": 1.0}, index=days))
    fate = context.high_fate(aw, 5, pd.Series({"s": "甲"}))
    assert fate[0]["height"] == 5 and fate[0]["fate"].startswith("断板，收-10.0%") and "大面" in fate[0]["fate"]


# ------------------------------------------------------------------ journal

BASIC = pd.DataFrame({"code": ["sh.600001", "sz.000002"], "code_name": ["甲股份", "乙科技"]})
SESSIONS = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]


def test_judgment_basis_and_write_once(tmp_path, monkeypatch):
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    evening = datetime(2026, 1, 6, 20, 0, tzinfo=store.SH)
    noon = datetime(2026, 1, 7, 10, 0, tzinfo=store.SH)
    assert journal.basis(evening, SESSIONS) == ("2026-01-06", False)
    assert journal.basis(noon, SESSIONS) == ("2026-01-06", True)
    rec = journal.add_judgment("甲明天涨停", "stock_lu", "600001", now=evening, sessions=SESSIONS, basic=BASIC)
    assert rec["code"] == "sh.600001" and rec["observation"] == "2026-01-06"
    with pytest.raises(ValueError):
        journal.add_judgment("x", "theme_fade", "不存在", now=evening, sessions=SESSIONS, boards={"传媒"})
    with pytest.raises(FileExistsError):
        journal._write_once("judgments", rec)


def test_judgment_scoring_pending_and_negation(tmp_path, monkeypatch):
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    evening = datetime(2026, 1, 6, 20, 0, tzinfo=store.SH)
    journal.add_judgment("甲涨停", "stock_lu", "甲股份", now=evening, sessions=SESSIONS, basic=BASIC)
    journal.add_judgment("乙不会大面", "stock_bigloss", "sz.000002", expect=False, now=evening, sessions=SESSIONS,
                         basic=BASIC)
    journal.add_judgment("随便记", now=evening, sessions=SESSIONS, basic=BASIC)
    idx = pd.Index(SESSIONS[:3])
    lu = pd.DataFrame({"sh.600001": [False, False, True], "sz.000002": False}, index=idx)
    w = dict(limit_up=lu, traded=lu | True, close=lu.astype(float))
    out = dict(bigloss1=pd.DataFrame({"sh.600001": np.nan, "sz.000002": [0.0, 1.0, np.nan]}, index=idx),
               out5=pd.DataFrame(np.nan, index=idx, columns=lu.columns))
    s = journal.score(journal.judgments(), w, out).set_index("text")
    assert s.loc["甲涨停", "result"] == "对" and s.loc["乙不会大面", "result"] == "错"
    assert s.loc["随便记", "result"] == "不评分"
    assert journal.judgment_summary(s.reset_index())["n"].sum() == 2


def test_trades_pair_first_in_first_out(tmp_path, monkeypatch):
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    now = datetime(2026, 1, 8, 20, 0, tzinfo=store.SH)
    journal.add_trade("2026-01-06", "600001", "buy", 10.0, 200, "龙头", now=now, basic=BASIC)
    journal.add_trade("2026-01-07", "600001", "sell", 11.0, 100, now=now, basic=BASIC)
    closed, open_lots = journal.round_trips(journal.trades())
    assert len(closed) == 1 and closed["ret"].iloc[0] == pytest.approx(0.1) and open_lots["qty"].iloc[0] == 100
    rep = journal.trade_report(journal.trades(), lambda d, c: dict(high=10.5, low=9.0))
    assert rep["closed"]["group"].iloc[0] == "无清单" and any("不在当日" in x for x in rep["warnings"])
    with pytest.raises(ValueError):
        journal.add_trade("2026-01-06", "600001", "hold", 10.0, 1, now=now, basic=BASIC)


# ------------------------------------------------------------------ page

def test_page_puts_data_gaps_first(tmp_path, monkeypatch):
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    run = tmp_path / "lists" / "2026-11-13" / "r1"
    run.mkdir(parents=True)
    summary = dict(observation="2026-11-12", target="2026-11-13", strategy_version="SF_LONGMAI_V9",
                   gate=dict(slots=2, mood_tier="中", mood_level=0.36, reg_tier="中", szse_cases=1), mainlines=[],
                   seats="not captured", threads=[dict(first="2026-11-02", age=9, boards=["传媒"], today=2, peak=46,
                                                       peak_age=1, listed=["吉视传媒"], lead_position="年线下-6%",
                                                       mainline_today=True, stocks=10)],
                   high_fate=[dict(name="大东方", height=4, fate="晋级5板")])
    (run / "summary.json").write_text(json.dumps(summary, ensure_ascii=False))
    pd.DataFrame([dict(分组="主线龙头", 名称="大东方<b>", 角色="空间板(市场最高板)·5板", 成交额="1亿", 位置="年线上+70%",
                       参与建议="候选（由你决定）：看竞价", 风险提示="", 昨日分时="", 历史同类="")]).to_csv(
        run / "盘前清单.csv", index=False, encoding="utf-8-sig")
    text = page.render(run)
    assert text.index("先看这里") < text.index("仓位内的候选") and "龙虎榜席位未采集" in text
    assert "大东方&lt;b&gt;" in text and "大东方<b>" not in text  # escaped
    path = page.write(run)
    assert path.exists() and path.suffix == ".html"


# ------------------------------------------------------------------ schedule and theme snapshots

def test_schedule_skips_and_records(tmp_path, monkeypatch):
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    monkeypatch.setattr(store, "calendar", lambda *a, **k: ["2026-11-13"])
    monkeypatch.setattr(schedule, "notify", lambda *a: None)
    assert schedule.should_run("auction", datetime(2026, 11, 14, 9, 26), ["2026-11-13"]) == "非交易日"
    assert "不在" in schedule.should_run("auction", datetime(2026, 11, 13, 13, 0), ["2026-11-13"])
    assert schedule.should_run("auction", datetime(2026, 11, 13, 9, 26), ["2026-11-13"]) is None
    calls = []
    code = schedule.run("premarket", now=datetime(2026, 11, 13, 18, 40, tzinfo=store.SH),
                        runner=lambda args, out: calls.append(args) or (1 if args == ["page"] else 0))
    assert code == 1 and calls == [["premarket"], ["page"]]
    st = json.loads((tmp_path / "schedule" / "status" / "premarket.json").read_text())
    assert st["exit"] == 1 and st["log"].endswith(".log")
    p = schedule.plist("intraday")
    assert len(p["StartCalendarInterval"]) == 10 and p["ProgramArguments"][-2:] == ["run", "intraday"]


def test_theme_snapshot_index(tmp_path, monkeypatch):
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    (tmp_path / "meta").mkdir()
    themes.record_snapshot("2026-11-07T03:00:00+00:00", {"theme_members.parquet": "a1"}, "2026-11-07")
    themes.record_snapshot("2026-11-14T03:00:00+00:00", {"theme_members.parquet": "b2"}, "2026-11-14")
    assert themes.snapshot_for("2026-11-10")["archive"]["theme_members.parquet"] == "a1"
    assert themes.snapshot_for("2026-11-01") is None


# ------------------------------------------------------------------ stop-loss exam

def test_stoploss_touch_uses_sellable_days_and_gap_through():
    from sf import stoploss
    days = [f"2024-01-{i:02d}" for i in range(2, 12)]  # t = days[0], T = days[1], first sellable day = days[2]
    close = pd.DataFrame({"x": [10.0, 10.0, 9.5, 9.6, 9.8, 9.9, 10.0, 10.0, 10.0, 10.0],
                          "y": [10.0, 10.0, 9.0, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7]}, index=days)
    low, opn = close.copy(), close.copy()
    low.loc[days[1], "x"] = 8.0      # T itself (not sellable) is ignored
    low.loc[days[2], "x"] = 9.2      # touches the 9.3 line on T+1, opened above it
    opn.loc[days[2], "y"] = 9.0      # opens below the line: sold at the open
    w = dict(open=opn, high=close * 1.01, low=low, close=close, traded=close.notna(), one_price_up=close.isna())
    panel = pd.DataFrame(dict(obs=days[0], day=days[1], code=["x", "y"], role="ladder", seg="train"))
    t = stoploss.touches(panel, w).set_index("code")
    assert t.loc["x", "touch"] == 1.0 and t.loc["x", "gap_through"] == 0.0
    assert t.loc["x", "drift1"] == pytest.approx(9.6 / 9.3 - 1) and t.loc["x", "drift3"] == pytest.approx(9.9 / 9.3 - 1)
    assert t.loc["y", "touch"] == 1.0 and t.loc["y", "gap_through"] == 1.0
    assert t.loc["y", "drift1"] == pytest.approx(9.1 / 9.0 - 1)


def test_schedule_converts_shanghai_times_to_local():
    from zoneinfo import ZoneInfo
    london = ZoneInfo("Europe/London")
    d, h, m = schedule.local_slot(1, 9, 26, london)  # Monday 09:26 Shanghai
    assert (h, m) in ((1, 26), (2, 26)) and d == 1
    assert schedule.local_slot(1, 8, 40, ZoneInfo("America/New_York"))[0] == 0  # Sunday evening in New York
    p = schedule.plist("auction", london)
    assert all(x["Hour"] in (1, 2) for x in p["StartCalendarInterval"])


def test_intraday_missing_codes_after_a_partial_capture(tmp_path, monkeypatch):
    from sf import intraday
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    day = "2026-11-12"
    bars = pd.DataFrame({"code": "sh.600001", "minute": range(intraday.BARS_PER_DAY), "date": day})
    bars.to_parquet(intraday.minute_path(day), index=False)
    pd.DataFrame({"code": ["sh.600001", "sh.600002"], "reasons": "limit_up", "date": day}).to_parquet(
        intraday._dir("universe") / f"{day}.parquet", index=False)
    assert intraday.missing_codes(day) == ["sh.600002"]
    calls = []
    monkeypatch.setattr(intraday, "supplement", lambda d, codes, **k: calls.append((d, codes, k["reason"])) or {})
    intraday.retry_missing([day])
    assert calls == [(day, ["sh.600002"], "retry")]


def test_page_index_lists_days_newest_first(tmp_path, monkeypatch):
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    for day, stamps in (("2026-11-12", ["20261111T120000Z"]), ("2026-11-13", ["20261112T100000Z", "20261112T170000Z"])):
        (tmp_path / "pages" / day).mkdir(parents=True)
        for s in stamps:
            (tmp_path / "pages" / day / f"{s}.html").write_text("x")
    text = page.write_index().read_text()
    assert text.index("2026-11-13") < text.index("2026-11-12") and "共 2 份" in text
    assert "2026-11-13/20261112T170000Z.html" in (tmp_path / "pages" / "latest.html").read_text()
