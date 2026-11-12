"""Offline tests for the call-auction layer SF_AUCTION_V1 (synthetic data only, no network)."""
from __future__ import annotations

import json
from datetime import datetime

import numpy as np
import pandas as pd

from sf import auction, store, strategy

DAYS = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2024-01-01", periods=12)]


def _bars(rows):
    out = []
    for code, series in rows.items():
        prev = series[0][3]
        for day, (o, h, l, c) in zip(DAYS, series):
            out.append(dict(date=day, code=code, open=o, high=h, low=l, close=c, preclose=prev, volume=1e6,
                            amount=1e8, turn=1.0, pctChg=0.0, tradestatus="1", isST="0"))
            prev = c
    return pd.DataFrame(out)


def test_outcomes_are_anchored_at_the_open():
    flat = [(10.0, 10.0, 10.0, 10.0)] * 12
    gap_fade = flat[:5] + [(10.8, 10.9, 9.9, 10.1)] + [(10.1, 10.1, 10.1, 10.1)] * 6  # +8% open, close -6.5% vs open
    limit_open = flat[:5] + [(11.0, 11.0, 11.0, 11.0), (12.1, 12.1, 12.1, 12.1)] + [(12.1,) * 4] * 5
    w = strategy.panel(_bars({"sh.600001": gap_fade, "sh.600002": limit_open, "sh.600003": flat}))
    a = auction.auction_outcomes(w)
    T = DAYS[5]
    assert abs(a["gap"].loc[T, "sh.600001"] - 0.08) < 1e-9
    assert a["a_bigloss"].loc[T, "sh.600001"] == 1.0 and a["a_bigloss"].loc[T, "sh.600003"] == 0.0
    assert a["open_limit"].loc[T, "sh.600002"] == 1.0 and a["open_limit"].loc[T, "sh.600001"] == 0.0
    assert a["a_out5"].loc[T, "sh.600002"] == 1.0  # limit-ups on T and T+1 count (after 09:25)
    assert a["a_out5"].loc[T, "sh.600001"] == 0.0
    assert np.isnan(a["a_out5"].loc[DAYS[-1], "sh.600003"])  # pending, never a miss


def _panel(role="leader", n=(300, 90, 90), confirm=(0.40, 0.10), base=(0.25, 0.10), deny=(0.10, 0.10)):
    rows = []
    for seg, k, day in zip(auction.SEGS, n, ("2024-06-03", "2025-06-02", "2026-03-02")):
        for gap, (o5, bl) in ((0.08, confirm), (0.02, base), (-0.04, deny)):
            for i in range(k):
                rows.append(dict(role=role, seg=seg, day=day, obs=day, code=f"c{i}", gap=gap + i * 1e-6,
                                 open_limit=0.0, a_out5=float(i < k * o5), a_bigloss=float(i < k * bl),
                                 a_lu=0.0, a_max3=0.0))
    return pd.DataFrame(rows)


def test_cuts_come_from_train_and_verdict_follows_tradeoff():
    p = _panel()
    p.loc[p["seg"] == "test1", "gap"] += 0.5  # test gaps must not move the cut points
    lo, hi = auction.fit_cuts(p)["leader"]
    assert -0.04 < lo < 0.02 < hi < 0.08
    rep = auction.report(_panel())
    assert rep["verdicts"]["leader"]["confirm"]["adopted"]  # +15pp breakout, no more big losses
    assert rep["verdicts"]["leader"]["deny"]["adopted"]
    risky = auction.report(_panel(confirm=(0.40, 0.20)))  # +10pp more big losses > half of the gain
    assert not risky["verdicts"]["leader"]["confirm"]["adopted"]
    few = auction.report(_panel(n=(150, 90, 90)))
    assert few["verdicts"]["leader"]["confirm"]["reason"].startswith("train 样本不足")


def test_limit_open_and_unknown_are_never_confirm_or_deny():
    lab = auction.classify(pd.Series([0.10, np.nan, 0.05, -0.05, 0.0]), pd.Series([1.0, np.nan, 0.0, 0.0, 0.0]),
                           -0.01, 0.03)
    assert lab.tolist() == ["limit_open", "unknown", "confirm", "deny", "neutral"]


def _saved_list(tmp_path):
    run = tmp_path / "lists" / "2026-11-12" / "20261111T200000Z"
    run.mkdir(parents=True)
    facts = pd.DataFrame({"eligible": True, "close": [10.0, 20.0, 5.0, 8.0],
                          "amount": [1e9, 5e8, 1e8, 2e8], "limit_up": [True, True, False, True],
                          "lm_group": ["leader", "ladder", "watch", None], "name": ["甲", "乙", "丙", "丁"]},
                         index=pd.Index(["sh.600001", "sz.000002", "sh.600003", "sh.600004"], name="code"))
    facts.to_parquet(run / "facts.parquet")
    (run / "summary.json").write_text(json.dumps({"observation": "2026-11-11", "target": "2026-11-12",
                                                  "version": "SF_LONGMAI_V9"}))
    (run.parent / "LATEST").write_text(run.name)
    return run


def test_snapshot_describes_the_list_only(tmp_path, monkeypatch):
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    monkeypatch.setattr(auction, "ADOPTED", {})
    _saved_list(tmp_path)
    quotes = pd.DataFrame({"code": ["sh.600001", "sz.000002", "sh.600004"], "open": [11.0, 19.0, 8.4],
                           "preclose": [10.0, 20.0, 8.0], "price": [11.0, 19.0, 8.4], "volume": [1, 1, 1],
                           "amount": [1e8, 2e7, 1e7]})
    snap = auction.snapshot(quotes=quotes, now=datetime(2026, 11, 12, 9, 26, tzinfo=store.SH))
    s = snap["listed"]
    assert list(s.index) == ["sh.600001", "sz.000002", "sh.600003"]  # 600004 is a prior limit-up, not listed
    assert s.loc["sh.600001", "open_limit"] == 1.0 and abs(s.loc["sz.000002", "gap"] + 0.05) < 1e-9
    assert abs(s.loc["sh.600001", "amount_share"] - 0.1) < 1e-9
    assert np.isnan(s.loc["sh.600003", "gap"]) and snap["missing"] == ["sh.600003"]  # no quote: missing, not 0
    assert (s["label"] == "description").all()  # nothing adopted yet
    assert snap["market"]["prev_limit_ups"] == 3 and snap["market"]["open_limit"] == 1
    t = auction.table(snap)
    assert t.set_index("名称").loc["甲", "竞价标签"] == "涨停开" and t.set_index("名称").loc["丙", "竞价缺口"] == "缺失"
    late = auction.snapshot(quotes=quotes, now=datetime(2026, 11, 12, 9, 40, tzinfo=store.SH))
    assert late["listed"]["auction_amount"].isna().all()  # after 09:30 the amount is no longer the auction's
    assert any("竞价成交额无法单独取得" in x for x in auction.lines(late))
    out = auction.save(snap)
    assert (out / "snapshot.parquet").exists() and json.loads((out / "summary.json").read_text())["in_auction"]


def test_adopted_deny_label_only_for_adopted_roles(tmp_path, monkeypatch):
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    _saved_list(tmp_path)
    monkeypatch.setattr(auction, "ADOPTED", {"leader": dict(cuts=(-0.01, 0.03), confirm=False, deny=True),
                                             "ladder": dict(cuts=(-0.01, 0.03), confirm=False, deny=True)})
    quotes = pd.DataFrame({"code": ["sh.600001", "sz.000002", "sh.600003"], "open": [10.5, 19.0, 4.5],
                           "preclose": [10.0, 20.0, 5.0], "price": 1.0, "volume": 1, "amount": 1e7})
    s = auction.snapshot(quotes=quotes, now=datetime(2026, 11, 12, 9, 26, tzinfo=store.SH))["listed"]
    assert s.loc["sh.600001", "label"] == "neutral"  # +5% would be confirm, but confirm was not adopted
    assert s.loc["sz.000002", "label"] == "deny"
    assert s.loc["sh.600003", "label"] == "description"  # observe role: nothing adopted
