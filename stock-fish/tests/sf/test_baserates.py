"""Offline tests for the historical base rates (synthetic data only, no network)."""
from __future__ import annotations

import json
from datetime import datetime

import numpy as np
import pandas as pd

from sf import auction, baserates, store


def _panel(n=180):
    rows = []
    for i in range(n):
        lim = i % 3 == 0
        rows.append(dict(role="ladder", obs="2024-01-02", day="2024-01-03", code=f"c{i}", hb="2板",
                         one="一字" if i < n // 2 else "非一字", cls="deny" if i % 2 else "other",
                         gap=0.10 if lim else -0.02, open_limit=1.0 if lim else 0.0,
                         c_lu1=float(i % 4 == 0), c_out5=float(i % 5 == 0), c_bigloss1=float(i % 10 == 0),
                         a_lu=float(lim), a_bigloss=float(not lim and i % 2 == 1), hi=0.03, lo=-0.04,
                         cl=-0.01 if i % 2 else 0.01))
    return pd.DataFrame(rows)


def test_table_lookup_falls_back_when_a_cell_is_small():
    t = baserates.table(_panel())
    r = baserates.lookup(t, "ladder", 2, 1.0)
    assert r["level"] == 3 and r["one"] == "一字" and r["n"] == 90
    assert baserates.lookup(t, "ladder", 2, np.nan)["level"] == 2  # unknown one-price: (role x height)
    small = baserates.table(_panel(40))
    assert baserates.lookup(small, "ladder", 2, 1.0)["level"] == 2  # 20 rows < MIN_N: fall back
    assert baserates.lookup(small, "observe", 0, 0.0) is None
    text = baserates.describe(r)
    assert text.startswith("连板梯队·2板·一字 90次") and "未涨停开时开盘后大面" in text
    assert baserates.describe(None) == "同类样本不足"
    d = baserates.lookup(t, "ladder", 2, None, "deny")
    assert d is not None and "竞价否定" in baserates.describe_bands(d) and "最低中位-4.0%" in baserates.describe_bands(d)


def test_latest_respects_the_cutoff(tmp_path, monkeypatch):
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    t = baserates.table(_panel())
    old = baserates.save(t, dict(version=baserates.VERSION, cutoff="2026-11-10"))
    (old.parent / "29991231T000000Z").mkdir()  # a newer table built after the observation date
    (old.parent / "29991231T000000Z" / "summary.json").write_text(json.dumps(dict(cutoff="2026-11-20")))
    tab, meta = baserates.latest("2026-11-11")
    assert meta["run"] == old.name and len(tab) == len(t)
    assert baserates.latest("2026-11-09") == (None, None)


def test_auction_shows_bands_for_buyable_listed_stocks(tmp_path, monkeypatch):
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    monkeypatch.setattr(auction, "ADOPTED", {"ladder": dict(cuts=(-0.01, 0.03), confirm=False, deny=True)})
    run = tmp_path / "lists" / "2026-11-12" / "r1"
    run.mkdir(parents=True)
    facts = pd.DataFrame({"eligible": True, "close": [10.0, 20.0], "amount": [1e8, 1e8], "limit_up": True,
                          "board_height": [2, 2], "one_price": [True, True], "lm_group": ["ladder", "ladder"],
                          "name": ["甲", "乙"]}, index=pd.Index(["sh.600001", "sh.600002"], name="code"))
    facts.to_parquet(run / "facts.parquet")
    (run / "summary.json").write_text(json.dumps({"observation": "2026-11-11", "target": "2026-11-12"}))
    baserates.save(baserates.table(_panel()), dict(version=baserates.VERSION, cutoff="2026-11-10"))
    quotes = pd.DataFrame({"code": ["sh.600001", "sh.600002"], "open": [9.5, 22.0], "preclose": [10.0, 20.0],
                           "price": 1.0, "volume": 1, "amount": 1e6})
    snap = auction.snapshot(quotes=quotes, now=datetime(2026, 11, 12, 9, 26, tzinfo=store.SH))
    s = snap["listed"]
    assert s.loc["sh.600001", "label"] == "deny" and "竞价否定" in s.loc["sh.600001", "bands"]
    assert s.loc["sh.600002", "bands"] == ""  # opened at the limit: not buyable, no bands
    assert any("同类开盘后" in x for x in auction.lines(snap))
