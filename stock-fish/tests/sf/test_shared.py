"""Offline tests for the shared liquidity-floor path, per-version review, schedule retries, theme freshness, the
intraday give-up rule and the folded list notes (synthetic data, no network)."""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from sf import intraday, listing, longmai, page, review, schedule, store, themes


# ------------------------------------------------------------------ liquidity floor inside decide

def _decide(monkeypatch, floor: bool):
    days = pd.Index(["d0", "d1"])
    w = dict(close=pd.DataFrame(1.0, index=days, columns=["a", "b", "c"]))
    ctx = dict(w=w, aw={}, ts=dict(lu3=pd.DataFrame(5.0, index=days, columns=["板"])))
    sig = pd.DataFrame({"board": "板", "leader_rank": [1, 2, 3], "turn_rank": [1, 2, 3]}, index=["a", "b", "c"])
    monkeypatch.setattr(longmai.mainline, "signals", lambda *a, **k: sig.copy())
    monkeypatch.setattr(longmai, "gate", lambda *a, **k: {})
    for name in ("vetoes", "notes", "crowded"):
        monkeypatch.setattr(longmai, name, lambda *a, **k: [])
    monkeypatch.setattr(longmai, "liquidity_floor",
                        lambda ctx, t: dict(ok=pd.Series({"a": True, "b": False, "c": True}), threshold=1e9))
    p = longmai.params(None if floor else longmai.V9_OVERRIDES)
    return longmai.decide(ctx, 1, "d2", p, None, pool=pd.Series(True, index=["a", "b", "c"]))


def test_decide_applies_the_floor_after_ranking_and_reports_dropped_leaders(monkeypatch):
    d = _decide(monkeypatch, floor=True)
    assert list(d["leaders"].index) == ["a", "c"] and d["thin"] == ["b"]
    assert list(d["leaders"]["leader_rank"]) == [1, 3]          # nobody is promoted into the dropped slot
    assert longmai.passes_floor(d["liq"], "a") and not longmai.passes_floor(d["liq"], "b")
    assert not longmai.passes_floor(d["liq"], "unknown")       # a stock the floor does not know never passes


def test_v9_replays_switch_the_floor_off(monkeypatch):
    d = _decide(monkeypatch, floor=False)
    assert list(d["leaders"].index) == ["a", "b", "c"] and d["thin"] == [] and d["liq"] is None
    assert longmai.passes_floor(None, "anything")
    assert longmai.params()["liq_floor"] is True               # the live list keeps the floor


# ------------------------------------------------------------------ review per strategy version

def test_review_by_version_keeps_versions_apart_and_pending_unknown():
    rows = pd.DataFrame(dict(
        obs=["d1", "d1", "d2", "d2", "d3"], group=["leader", None, "leader", "ladder", "leader"],
        baseline=[True, True, False, True, True], strategy_version=["V9", "V9", "V14", "V14", "V14"],
        lu1=[1.0, 0.0, 0.0, 1.0, 1.0], max3=[0.1, 0.0, 0.0, 0.2, np.nan], out5=[1.0, 0.0, np.nan, np.nan, np.nan],
        bigloss1=[0.0, 0.0, 1.0, 0.0, 0.0]))
    t = review.by_version(rows)
    assert t.loc[("V9", "leader"), "n"] == 1 and t.loc[("V9", "baseline"), "n"] == 2
    assert t.loc[("V14", "leader"), "lists"] == 2 and t.loc[("V14", "leader"), "lu1"] == 0.5
    assert t.loc[("V14", "leader"), "pending_out5"] == 2 and np.isnan(t.loc[("V14", "leader"), "out5"])
    assert ("V9", "ladder") not in t.index
    assert review.by_version(pd.DataFrame()).empty


def test_review_html_states_lists_per_version():
    rows = pd.DataFrame(dict(obs=["d1", "d2"], target=["2026-11-12", "2026-11-13"], group=["leader", "leader"],
                             baseline=["False", "False"], strategy_version=["SF_LONGMAI_V9", "SF_LONGMAI_V14"],
                             lu1=[1.0, 0.0], max3=[0.1, 0.0], out5=[np.nan, np.nan], bigloss1=[0.0, 0.0]))
    text = page.review_html(rows, "x")
    assert "龙脉第9版 1 份" in text and "龙脉第14版 1 份" in text and "60 份" in text


# ------------------------------------------------------------------ schedule: retry days

def test_weekly_jobs_retry_only_after_a_failed_run():
    sunday, saturday = datetime(2026, 11, 15, 11, 0, tzinfo=store.SH), datetime(2026, 11, 14, 11, 0, tzinfo=store.SH)
    assert schedule.should_run("themes", saturday, [], last=dict(exit=0)) is None       # its own day: always
    assert schedule.should_run("themes", sunday, [], last=dict(exit=1)) is None         # failed: retry
    assert "不需要重试" in schedule.should_run("themes", sunday, [], last=dict(exit=0))
    tuesday, wednesday = datetime(2026, 11, 17, 11, 0, tzinfo=store.SH), datetime(2026, 11, 18, 11, 0, tzinfo=store.SH)
    assert schedule.should_run("themes", tuesday, [], last=dict(exit=1)) is None
    assert 3 not in schedule.JOBS["themes"]["retry_days"] and wednesday.isoweekday() == 3


def test_retry_status_is_read_from_the_status_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    sunday = datetime(2026, 11, 15, 11, 0, tzinfo=store.SH)
    assert schedule.should_run("themes", sunday, []) is not None                        # never ran: wait for Saturday
    schedule._status("themes", dict(job="themes", exit=1))
    assert schedule.should_run("themes", sunday, []) is None
    ran = []
    monkeypatch.setattr(schedule, "notify", lambda *a: None)
    assert schedule.run("themes", now=sunday, runner=lambda args, out: ran.append(args) or 0) == 0
    assert ran == [["themes"]] and schedule.last_status("themes")["exit"] == 0
    assert schedule.should_run("themes", sunday, []) is not None                        # fixed: no further retry


def test_plist_has_a_slot_for_every_retry_day(tmp_path, monkeypatch):
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    slots = schedule.plist("themes", tz=store.SH)["StartCalendarInterval"]
    assert sorted(s["Weekday"] for s in slots) == [0, 1, 2, 6]                          # Sun, Mon, Tue, Sat (launchd)
    assert len(schedule.plist("auction", tz=store.SH)["StartCalendarInterval"]) == 5
    row = next(s for s in schedule.status() if s["job"] == "themes")
    assert "失败后周日、周一、周二重试" in row["days"]


# ------------------------------------------------------------------ theme freshness

def test_theme_freshness_flags_an_old_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    assert themes.freshness("2026-11-17") is None                                       # nothing stored
    meta = tmp_path / "meta"
    meta.mkdir()
    pd.DataFrame(dict(board=["甲", "乙"], kind="concept", captured_at=["2026-11-01T03:00:00+00:00",
                                                                     "2026-11-10T03:00:00+00:00"],
                      source=["carried", "fresh"])).to_parquet(meta / "theme_boards.parquet")
    pd.DataFrame(dict(board=["甲"], kind="concept", code=["sh.600001"])).to_parquet(meta / "theme_members.parquet")
    f = themes.freshness("2026-11-17")
    assert f == dict(captured="2026-11-10", age_days=7, carried=1, stale=False)
    assert themes.freshness("2026-11-25")["stale"] is True
    old = dict(gate=dict(mood_tier="低", mood_level=0.1, reg_tier="中", szse_cases=1, slots=0), observation="2026-11-24",
               target="2026-11-25", mainlines=[], themes=themes.freshness("2026-11-25"))
    head = listing.header(old)
    assert head[1].startswith("【数据缺失】板块成分已 15 天未刷新（最近 2026-11-10）")
    assert any(line.startswith("主线：无") and "板块成分 11-10 采集" in line for line in head)


# ------------------------------------------------------------------ intraday: give up when the source refuses

def test_fetch_gives_up_after_the_first_requests_all_fail(tmp_path, monkeypatch):
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    calls = []

    def refuse(code):
        calls.append(code)
        raise ConnectionError("dropped")
    monkeypatch.setattr(intraday, "fetch", refuse)
    codes = [f"sh.6{i:05d}" for i in range(60)]
    _, _, frames, errors = intraday._fetch_all(codes, workers=1, log=lambda *_: None)
    assert len(calls) == intraday.GIVE_UP_AFTER and not frames
    assert set(errors) == set(codes)                                                    # every code is reported
    assert sum(e == intraday.NOT_REQUESTED for e in errors.values()) == 60 - intraday.GIVE_UP_AFTER


def test_fetch_keeps_going_once_any_request_succeeds(tmp_path, monkeypatch):
    monkeypatch.setenv("SF_HOME", str(tmp_path))

    def flaky(code):
        if code.endswith("3"):
            return '{"data": {"trends": []}}'
        raise ConnectionError("dropped")
    monkeypatch.setattr(intraday, "fetch", flaky)
    codes = [f"sh.6{i:05d}" for i in range(40)]
    _, _, frames, errors = intraday._fetch_all(codes, workers=1, log=lambda *_: None)
    assert len(frames) == 4 and len(errors) == 36
    assert intraday.NOT_REQUESTED not in errors.values()


# ------------------------------------------------------------------ folded notes (display only)

def test_fold_repeats_shows_a_repeated_long_text_once():
    long_a, long_b = "梯队候选：历史上2板+约12-21%在5日内再涨停3次", "只观察：不在当前前3主线或非题材内前2"
    frame = pd.DataFrame({"名称": ["甲", "乙", "丙", "丁"], "参与建议": [long_a, long_a, long_b, "短"],
                          "历史同类": ["同类样本不足", "同类样本不足", None, long_b]})
    out, notes = listing.fold_repeats(frame)
    assert list(out["参与建议"]) == ["见注1", "见注1", long_b, "短"]                      # unique or short: unchanged
    assert notes == [f"注1：{long_a}"]
    assert list(out["历史同类"]) == ["同类样本不足", "同类样本不足", "", long_b]          # short repeats stay in place
    assert frame["参与建议"].iloc[0] == long_a                                           # the input is not modified
    assert page._notes(notes).count("注1") == 1 and page._notes([]) == ""
