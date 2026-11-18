"""Offline tests for the page charts (synthetic values only)."""
from __future__ import annotations

import math

import pandas as pd

from sf import charts, page


def test_trend_tile_skips_unknown_days_and_needs_two_points():
    one = charts.trend_tile("情绪", "26%", "低档", [("11-18", 0.26)], "t-mood")
    assert "<svg" not in one and "26%" in one
    two = charts.trend_tile("情绪", "26%", "低档", [("11-16", 0.3), ("11-17", None), ("11-18", 0.26)], "t-mood")
    assert two.count("<title>") == 2 and "近3日" in two  # the unknown day is a gap, not a zero


def test_ladder_colours_listed_stocks_and_counts_first_boards():
    html = charts.ladder({4: [("甲", "g-lead")], 2: [("乙", None)]}, [("丙", "g-watch"), ("丁", None), ("戊", None)])
    assert html.index("4板") < html.index("2板") < html.index("首板")
    assert 'class="lc g-lead">甲' in html and 'class="lc">乙' in html and "其余 2 只" in html


def test_thread_strip_marks_missing_days_and_today():
    rows = [dict(name="算力", age=2, live=True, counts={"2026-11-17": 4, "2026-11-18": 16}, key="th0"),
            dict(name="百货", age=1, live=False, counts={"2026-11-17": 0}, key=None)]
    html = charts.thread_strip(rows, ["2026-11-17", "2026-11-18"])
    assert 'data-d="th0"' in html and html.count('class="sc none"') == 1 and "today" in html
    assert charts.thread_strip([], ["2026-11-18"]) == ""


def test_intraday_chart_draws_limit_and_says_its_source():
    bars = pd.DataFrame({"minute": range(241), "close": [10.0] * 120 + [11.0] * 121, "volume": [100.0] * 241})
    html = charts.intraday_chart(bars, 10.0, 11.0, "11-18 新浪1分钟线")
    assert 'class="lim"' in html and "涨停 +10%" in html and "收盘 +10.0% · 11-18 新浪1分钟线" in html
    assert "无分时数据" in charts.intraday_chart(None, 10.0, 11.0, "x")
    assert "无分时数据" in charts.intraday_chart(bars, math.nan, 11.0, "x")


def test_position_bar_places_price_and_ma():
    html = charts.position_bar(-0.2, 0.1)
    assert "距一年高 -20% · 年线上 10%" in html and 'class="ma"' in html
    assert "位置未知" in charts.position_bar(None, 0.1)


def test_rate_bars_and_gap_bars_show_missing_as_words():
    html = charts.rate_bars([("主线龙头", "g-lead", None, 0.5, 11)], (0.14, 0.26))
    assert "待定" in html and "50%" in html and "走出来 14%，大面 26%" in html
    gaps = charts.gap_bars([("甲", 0.046, ""), ("乙", -0.04, "竞价否定"), ("丙", None, "")])
    assert "+4.6%" in gaps and "竞价否定" in gaps and "缺失" in gaps


def test_review_chart_uses_the_same_rows_as_the_table():
    rows = pd.DataFrame(dict(group=["leader", "leader", None, None], baseline=["True", "False", "True", "True"],
                             out5=[1.0, 0.0, 0.0, 0.0], bigloss1=[0.0, 1.0, 0.0, 1.0]))
    html = page.rate_chart(rows)
    assert "2 次" in html and "50%" in html  # a listed limit-up is also in the baseline and still counted
