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
    days = at2[0]["days"]
    assert [d["boards"] for d in days] == [["传媒"], ["影视"], ["影视"]]
    assert days[1]["lu"] == [("a2", 2), ("a3", 1)] and days[2]["lu"] == [("a3", 2)]   # streaks, tallest first
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
                   high_fate=[dict(name="大东方", height=4, fate="晋级5板")],
                   heat=[dict(board="机器人概念", lu3=51, lu=4, n=783, share=0.065, z=3.25, streak=4,
                              streak_capped=False, series=[1, 2, 4])],
                   name_waves=[dict(group="字·洋", limit_ups=4, expected=0.16, members=23, leaders=["东方海洋"])],
                   xuan=dict(kind="xuan", first="2026-11-11", age=2, boards=["名字带「东方」"], today=4, peak=4,
                             peak_age=2, stocks=29, mainline_today=True, series=[1, 4], listed=[],
                             groups=[dict(group="词·东方", label="名字带「东方」", members=29, window_limit_ups=41,
                                          window_expected=7.2, today=4, expected_today=0.21, hot_today=True)],
                             days=[dict(day="2026-11-11", age=1, boards=[], lu=[]),
                                   dict(day="2026-11-12", age=2, boards=["名字带「东方」"],
                                        lu=[dict(name="大东方", height=5, listed=True)])]))
    (run / "summary.json").write_text(json.dumps(summary, ensure_ascii=False))
    pd.DataFrame([dict(分组="主线龙头", 名称="大东方<b>", 角色="空间板(市场最高板)·5板", 成交额="1亿", 位置="年线上+70%",
                       参与建议="候选（由你决定）：看竞价", 风险提示="", 昨日分时="", 历史同类="")]).to_csv(
        run / "盘前清单.csv", index=False, encoding="utf-8-sig")
    text = page.render(run)
    assert text.index("先看这里") < text.index("仓位内的候选") and "龙虎榜席位未采集" in text
    assert "大东方&lt;b&gt;" in text and "大东方<b>" not in text  # escaped
    assert "名字带「东方」" in text and "class=xuan" in text and "今日活跃" in text and "20日涨停 41 次" in text
    assert "名字带「洋」" not in text      # the one-day row is replaced by the merged card
    assert "题材热度榜" in text and "机器人概念" in text and ">51<" in text
    assert "未达异常" in text and "当天异常的名字组" in text
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
    assert text.index("2026-11-13") < text.index("2026-11-12") and "清单缺失" in text
    assert "2026-11-13/20261112T170000Z.html" in text and "20261112T100000Z" not in text
    assert "2026-11-13/20261112T170000Z.html" in (tmp_path / "pages" / "latest.html").read_text()
    go = tmp_path / "pages" / "go"
    assert "url=../2026-11-13/20261112T170000Z.html" in (go / "next-2026-11-12.html").read_text()
    assert "url=../2026-11-12/20261111T120000Z.html" in (go / "prev-2026-11-13.html").read_text()
    assert "refresh" not in (go / "next-2026-11-13.html").read_text()   # newest day: a note, not a redirect
    page.write_index()                                                    # go/ itself is never listed as a day
    assert not (go / "next-go.html").exists() and ">go<" not in (tmp_path / "pages" / "index.html").read_text()


def _run(tmp_path, target, stamp, version="SF_LONGMAI_V9"):
    run = tmp_path / "lists" / target / stamp
    run.mkdir(parents=True)
    (run / "summary.json").write_text(json.dumps(dict(target=target, strategy_version=version)))
    (run / "picks.csv").write_text("code\n")
    return run


def test_valid_run_is_last_list_saved_before_0925(tmp_path, monkeypatch):
    from sf import listing
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    a = _run(tmp_path, "2026-11-12", "20261111T120000Z")
    b = _run(tmp_path, "2026-11-12", "20261112T011000Z")   # 09:10 Shanghai: still before the open
    c = _run(tmp_path, "2026-11-12", "20261112T033000Z")   # 11:30 Shanghai: after the open
    (c.parent / "LATEST").write_text(c.name)
    before = datetime(2026, 11, 12, 9, 0, tzinfo=store.SH)
    after = datetime(2026, 11, 12, 15, 0, tzinfo=store.SH)
    assert listing.valid_run("2026-11-12", before) == c   # pre-open: the newest is the candidate
    assert listing.valid_run("2026-11-12", after) == b
    assert listing.after_open(c) and not listing.after_open(b) and not listing.after_open(a)
    assert listing.latest_run("2026-11-12") == b           # LATEST marker no longer wins over the valid run


def test_prune_moves_other_runs_snapshots_and_pages_to_trash(tmp_path, monkeypatch):
    from sf import listing, prune
    monkeypatch.setenv("SF_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(prune, "trash_root", lambda: tmp_path / "trash")
    home = tmp_path / "home"
    old = _run(home, "2026-11-12", "20261111T120000Z", "SF_LONGMAI_V7")
    keep = _run(home, "2026-11-12", "20261112T011000Z")
    late = _run(home, "2026-11-12", "20261112T033000Z")
    pending = [_run(home, "2026-11-13", "20261112T120000Z"), _run(home, "2026-11-13", "20261112T130000Z")]
    for stamp, ref in (("20261112T012600Z", keep.name), ("20261112T033100Z", late.name)):
        snap = home / "auction" / "2026-11-12" / stamp
        snap.mkdir(parents=True)
        (snap / "summary.json").write_text(json.dumps(dict(list_run=ref)))
    (home / "pages" / "2026-11-12").mkdir(parents=True)
    (home / "pages" / "2026-11-12" / "20261112T034000Z.html").write_text("x")
    now = datetime(2026, 11, 12, 20, 0, tzinfo=store.SH)
    items = prune.plan(now)
    assert [i["target"] for i in items] == ["2026-11-12"]                # 11-13 is still before its cutoff
    assert set(p.name for p in items[0]["remove"]) == {old.name, late.name, "20261112T033100Z", "20261112T034000Z.html"}
    rendered = []
    trash = prune.apply(items, render=rendered.append)
    assert rendered == [keep] and keep.exists() and not old.exists() and not late.exists()
    assert (trash / "lists" / "2026-11-12" / old.name / "summary.json").exists()
    assert (home / "auction" / "2026-11-12" / "20261112T012600Z").exists()
    assert (keep.parent / "LATEST").read_text() == keep.name
    assert all(p.exists() for p in pending)
    log = [json.loads(x) for x in (home / "meta" / "prune.jsonl").read_text().splitlines()]
    assert log[0]["kept"] == keep.name and len(log[0]["moved"]) == 4
    assert prune.plan(now)[0]["remove"] == []                             # idempotent


def test_prune_keeps_everything_without_a_preopen_list(tmp_path, monkeypatch):
    from sf import prune
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    _run(tmp_path, "2026-11-12", "20261112T033000Z")
    _run(tmp_path, "2026-11-12", "20261112T040000Z")
    items = prune.plan(datetime(2026, 11, 12, 20, 0, tzinfo=store.SH))
    assert items[0]["keep"] is None and items[0]["remove"] == []


def test_review_html_shows_rates_and_pending_in_words():
    rows = pd.DataFrame([
        dict(code="a", group="leader", baseline=True, lu1=1.0, bigloss1=0.0, max3=None, out5=None, target="2026-11-12"),
        dict(code="b", group="leader", baseline=True, lu1=0.0, bigloss1=1.0, max3=None, out5=None, target="2026-11-12"),
        dict(code="c", group=None, baseline=True, lu1=0.0, bigloss1=0.0, max3=None, out5=None, target="2026-11-12"),
        dict(code="d", group="watch", baseline=False, lu1=None, bigloss1=None, max3=None, out5=None, target="2026-11-13"),
    ])
    text = page.review_html(rows, "R1")
    assert "次日涨停" in text and "主线龙头" in text and "对照：当天全部主板涨停股" in text
    assert "50%<small> 1/2</small>" in text                         # leader next-day limit-up: 1 of 2 known
    assert "待定（0/2）" in text and "待定（0/1）" in text            # pending outcomes are not misses
    assert "覆盖 2 份清单（交易日 11-12、11-13）" in text


def test_thread_dialog_lists_days_newest_first():
    th = dict(age=2, first="2026-11-11", boards=["传媒", "影视"], stocks=3, days=[
        dict(day="2026-11-11", age=1, boards=["传媒"], lu=[dict(name="甲", height=1, listed=False)]),
        dict(day="2026-11-12", age=2, boards=[], lu=[dict(name="甲", height=2, listed=True), dict(name="乙<", height=1, listed=False)]),
    ])
    html = page.thread_dialog("th0", th)
    assert html.index(">11-12<") < html.index(">11-11<")
    assert '<span class="in">甲<i>2板</i></span>' in html and "乙&lt;" in html and "不在前 3 主线" in html



def test_xuan_merges_name_groups_and_catches_slow_hype():
    from sf import namethemes
    n_days = 30
    idx = pd.Index([f"2026-01-{i + 1:02d}" if i < 31 else f"x{i}" for i in range(n_days)])
    cols = [f"y{i}" for i in range(8)] + [f"d{i}" for i in range(4)] + ["f0"] + [f"o{i}" for i in range(3000)]
    lu = pd.DataFrame(False, index=idx, columns=cols)
    lu.loc[:, "o0"] = True                                    # market limit-up rate above zero
    for i in range(10, n_days):                               # slow hype: one or two 'y' limit-ups every day
        lu.loc[idx[i], [f"y{i % 8}"] + ([f"y{(i + 3) % 8}"] if i % 2 else [])] = True
    lu.loc[idx[-1], ["d0", "d1", "d2"]] = True                # a one-day burst of another name
    lu.loc[idx[15:], "f0"] = True                             # one stock's long streak: never a name wave
    lu.loc[idx[14:17], "y7"] = True                          # the leader's streak before the name wave is confirmed
    traded = pd.DataFrame(True, index=idx, columns=cols)
    grp = {"音·yang(洋阳)": [f"y{i}" for i in range(8)], "字·洋": [f"y{i}" for i in range(4)],
           "词·东方": [f"d{i}" for i in range(4)], "词·福莱": ["f0", "o1", "o2"], "字·东": [f"d{i}" for i in range(4)] + ["o3"]}
    assert "音·yang(洋阳)" not in set(namethemes.scan_all(lu, traded, grp)["group"])   # no single day looks abnormal
    x = namethemes.xuan(lu, traded, grp, n_days - 1)
    labels = [g["group"] for g in x["groups"]]
    assert labels[0] == "音·yang(洋阳)"                        # caught by the cumulative test
    assert "词·东方" in labels or "字·东" in labels             # the one-day burst, once
    assert not ("词·东方" in labels and "字·东" in labels)      # 东 inside 东方 is the same hype
    assert "字·洋" not in labels and "词·福莱" not in labels    # subset of yang; a single stock's streak
    assert x["kind"] == "xuan" and x["mainline_today"] and x["days"][-1]["boards"]
    assert x["confirmed"] > x["first"] and x["origin"]["code"] == "y7" and x["origin"]["day"] == x["first"]
    assert x["origin"]["height"] >= 3
    assert x["days"][0]["prelude"] and not x["days"][-1]["prelude"]
    assert namethemes.label_text("音·yang(洋阳)") == "谐音「洋阳」" and namethemes.label_text("字·洋") == "名字带「洋」"


def test_heat_board_ranks_by_count_and_skips_overlapping_boards():
    ts = _ts()
    ts["lu"].loc[:, :] = 0.0
    ts["lu"].iloc[:, 0] = [2, 2, 1, 0, 0, 0, 0, 0]      # 传媒
    ts["lu"].iloc[:, 1] = [1, 2, 1, 0, 0, 0, 0, 0]      # 影视 (its stocks are mostly 传媒's)
    ts["lu3"] = ts["lu"].rolling(3, min_periods=1).sum()
    ts["n"] = pd.DataFrame(10, index=ts["lu"].index, columns=ts["names"])
    ts["n3"] = ts["n"].rolling(3, min_periods=1).sum()
    ts["rate3"] = pd.Series(0.01, index=ts["lu"].index)
    out = threads.heat(ts, 2, {"overlap_max": 0.6})
    assert [h["board"] for h in out][0] == "传媒" and out[0]["lu3"] == 5
    assert all(h["board"] != "光伏" for h in out)       # no limit-ups in 3 sessions
    assert out[0]["series"] == [2, 2, 1] and out[0]["streak"] == 3 and out[0]["streak_capped"]


def test_page_text_is_chinese_only():
    text = page.zh("龙脉V12 版本 20261112T171117Z · SF_LONGMAI_V9 · SF_AUCTION_V1 · 光洋股份·STRONG_CONTINUATION·"
                   "名字扩散(音·yang(洋阳)) 谐音 yang（洋阳） 运行 sf intraday --backfill 补抓，见 docs/STRATEGY_LONGMAI_V12.md；"
                   "watch 中性 *ST正平 TOPCon电池")
    assert "龙脉第12版" in text and "11-13 01:11" in text and "龙脉第9版" in text and "第1版" in text
    assert "强势延续" in text and "谐音「洋阳」" in text and "分时补抓命令" in text and "说明文档" in text and "观察" in text
    assert "*ST正平" in text and "TOPCon电池" in text      # stock and board names are kept as they are
    assert not any(w in text for w in ("SF_", "STRONG", "yang", "sf ", "docs/", "watch"))


def test_full_review_report_is_folded_below_the_table(tmp_path):
    rev = tmp_path / "R1"
    rev.mkdir()
    (rev / "report.json").write_text(json.dumps(dict(lists=2, first="2026-11-10", last="2026-11-11", recall=None,
                                                     winners=0, missed=[])))
    (rev / "forward_v9_v7.json").write_text(json.dumps(dict(status="前向样本 0 个交易日", table=dict(
        lu1=dict(V7=0.07, V9=0.09), bigloss1=dict(V7=0.27, V9=0.24), out5=dict(V7=None, V9=None),
        n=dict(V7=62, V9=51), per_day=dict(V7=31.0, V9=25.5)))))
    pd.DataFrame([dict(obs="2026-11-11", code="a", role="leader", cell="x", p_lu1=0.4, p_out5=0.4, p_bigloss1=0.3,
                       lu1=1.0, out5=None, bigloss1=0.0)]).to_csv(rev / "baserate_calibration.csv", index=False)
    pd.DataFrame([dict(code="a", role="ladder", **{"class": "deny"}, day="2026-11-12", a_lu=0.0, a_bigloss=1.0,
                       a_out5=None)]).to_csv(rev / "auction_forward.csv", index=False)
    html_ = page.review_report_html(rev)
    assert html_.startswith('<details class="legend fullrep"><summary>多日命中统计明细（点开查看）')
    assert "妖股召回：还没有走完" in html_ and "第9版" in html_ and "第7版" in html_ and "9%" in html_
    assert "主线龙头" in html_ and "40% → 100%" in html_ and "竞价否定" in html_ and "连板梯队" in html_
    assert "V9" not in html_ and "leader" not in html_


def test_daily_report_summarises_the_day_and_what_to_watch():
    summary = dict(observation="2026-11-12", target="2026-11-13",
                   gate=dict(mood_tier="中", mood_level=0.36, reg_tier="中", slots=2),
                   temperature=dict(name="退潮", loss_rate=0.39, text="水温：冷+亏钱效应强【退潮】"),
                   market=dict(today=dict(lu_count=60, ld_count=6, up_ratio=0.3, up_ratio_pct=0.08, promo_rate=0.15,
                                          promo_rate_pct=0.07, broken_rate=0.3, broken_rate_pct=0.6, big_loss=12,
                                          big_loss_pct=0.87, prem_lu=-0.01, prem_lu_pct=0.2),
                               prev=dict(lu_count=76, ld_count=4), amount=1.93e12, amount_prev=2.0e12,
                               recent=[dict(day="2026-11-11", lu_count=76, ld_count=4, promo_rate=0.3, big_loss=5),
                                       dict(day="2026-11-12", lu_count=60, ld_count=6, promo_rate=0.15, big_loss=12)],
                               ladder={"1": 50, "5": 2}, tall=[dict(name="大东方", height=5)]),
                   mainlines=[dict(board="传媒", lu=1, lu3=23, days=2, share=0.056)],
                   heat=[dict(board="机器人概念", lu3=51)],
                   threads=[dict(boards=["传媒"], age=9, today=2, peak=46)],
                   high_fate=[dict(name="大东方", height=4, fate="晋级5板")], to_check=["大东方"])
    table = pd.DataFrame([dict(分组="主线龙头", 名称="大东方", 角色="空间板·5板", 成交额="22.9亿·换手38%",
                               风险提示="放量：成交额22.9亿，为前5日均值6.8亿的3.3倍；10日偏离66%，再触发2次3日异动即严重异动")])
    html_ = page.daily_report_html(summary, table)
    assert html_.startswith('<details class="legend fullrep daily"><summary>当日复盘报告（点开查看）')
    for part in ("一、市场概况", "主板（不含 ST）涨停 60 家（较前日-16）", "两市成交 1.93 万亿（较前日-4%）", "晋级率", "（极低）",
                 "大面", "二、情绪与水温", "三、连板梯队与高标", "5板 2 家", "大东方 5板",
                 "四、主线与题材", "涨停最多但不在主线：机器人概念", "只剩峰值（46 家）的 4%", "五、龙头", "22.9亿",
                 "六、明日关注", "约 39%", "接力环境偏弱", "亏钱效应还没消退", "处于后期", "接近严重异动", "大东方 放量：成交额22.9亿", "炒作逻辑待查"):
        assert part in html_, part
    old = page.daily_report_html(dict(observation="2026-11-10", target="2026-11-11"), pd.DataFrame())
    assert "旧版清单" in old and "六、明日关注" in old


def test_market_day_ladder_and_percentiles():
    idx = pd.Index(["2026-01-01", "2026-01-02"])
    mood_tab = pd.DataFrame(dict(lu_count=[70, 60], ld_count=[4, 6], promo_rate=[0.3, 0.15], promo_rate_pct=[0.5, 0.07],
                                 big_loss=[5, 12], broken_rate=[0.2, 0.3]), index=idx)
    w = dict(limit_up=pd.DataFrame({"a": [True, True], "b": [False, True], "c": [False, False]}, index=idx),
             traded=pd.DataFrame(True, index=idx, columns=["a", "b", "c"]),
             height=pd.DataFrame({"a": [4, 5], "b": [0, 1], "c": [0, 0]}, index=idx))
    aw = dict(amount=pd.DataFrame({"a": [1e9, 2e9], "b": [1e9, 1e9], "c": [np.nan, 1e9]}, index=idx))
    m = context.market_day(dict(mood=mood_tab, w=w, aw=aw), 1, pd.Series({"a": "大东方"}))
    assert m["today"]["lu_count"] == 60 and m["prev"]["lu_count"] == 70 and m["today"]["promo_rate_pct"] == 0.07
    assert m["today"]["up_ratio"] is None                      # not in the table: unknown, not zero
    assert m["ladder"] == {"1": 1, "5": 1} and m["tall"] == [dict(name="大东方", height=5)]
    assert m["amount"] == 4e9 and m["amount_prev"] == 2e9 and len(m["recent"]) == 2
