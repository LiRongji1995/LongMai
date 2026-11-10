"""Offline tests for 龙脉 V8 (swing) outcomes, score and selection (synthetic frames)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from sf import swing

DAYS = [f"2026-01-{d:02d}" for d in range(1, 31)]


def _w(paths: dict[str, list[tuple[float, float, float]]]):
    """paths: code -> list of (low, high, close) per session; adjustment factor 1."""
    cols = list(paths)
    low = pd.DataFrame({c: [x[0] for x in v] for c, v in paths.items()}, index=DAYS[:len(next(iter(paths.values())))])
    high = pd.DataFrame({c: [x[1] for x in v] for c, v in paths.items()}, index=low.index)
    close = pd.DataFrame({c: [x[2] for x in v] for c, v in paths.items()}, index=low.index)
    return dict(low=low, high=high, close=close, adj=close.copy(), traded=close.notna(), _cols=cols)


def flat(n, price=10.0):
    return [(price, price, price)] * n


def test_win10_barrier_order_and_same_day_loss():
    up_first = flat(1) + [(10.0, 11.6, 11.5)] + [(9.0, 9.0, 9.0)] * 20      # +16% on day 1, then -10%
    down_first = flat(1) + [(9.1, 10.0, 9.2)] + [(12.0, 12.0, 12.0)] * 20   # -9% first
    both_same_day = flat(1) + [(9.0, 11.8, 10.0)] + flat(20)                 # +18% and -10% same day
    neither = flat(22)
    w = _w(dict(a=up_first, b=down_first, c=both_same_day, d=neither))
    out = swing.outcomes(w)
    day0 = DAYS[0]
    assert out["win10"].loc[day0].to_dict() == dict(a=1.0, b=0.0, c=0.0, d=0.0)
    assert out["loss10"].loc[day0].to_dict() == dict(a=0.0, b=1.0, c=1.0, d=0.0)
    assert round(out["max10"].loc[day0, "a"], 3) == 0.16 and round(out["min5"].loc[day0, "b"], 3) == -0.09


def test_outcomes_pending_until_window_is_stored():
    w = _w(dict(a=flat(15)))
    out = swing.outcomes(w)
    assert out["win10"]["a"].iloc[:5].notna().all()       # sessions 0..4 have 10 later sessions
    assert out["win10"]["a"].iloc[5:].isna().all()        # later ones are pending, not misses
    assert out["max20"]["a"].isna().all()


def test_score_equal_weight_percentiles_and_missing_feature_unscored():
    f = pd.DataFrame({"ret20": [0.3, 0.1, 0.0], "ret60": [0.5, 0.2, 0.1], "smooth20": [0.8, 0.5, 0.1],
                      "trend": [1.0, 1.0, 0.0], "near_high": [1.0, 0.9, 0.7], "vol_ratio": [2.0, 1.0, 0.5],
                      "lu20": [3, 1, 0], "theme_z": [4.0, 0.0, np.nan], "amount5": [2e8] * 3}, index=["x", "y", "z"])
    s = swing.score(f, pd.Series(True, index=f.index))
    assert s["x"] > s["y"] and np.isnan(s["z"])  # z misses theme_z: unscored, not imputed


def test_select_top_n_per_day_within_liquidity_pool():
    rows = []
    for day in ("2026-01-05", "2026-01-06"):
        for i in range(6):
            rows.append(dict(obs=day, code=f"c{i}", ret20=i, ret60=i, smooth20=i, trend=1.0, near_high=i, vol_ratio=i,
                             lu20=i, theme_z=float(i), amount5=2e8 if i != 5 else 0.5e8, high_vol=False))
    picks = swing.select(pd.DataFrame(rows), top_n=2, min_amount5=1e8)
    assert picks.groupby("obs")["code"].apply(list).to_dict() == {"2026-01-05": ["c4", "c3"], "2026-01-06": ["c4", "c3"]}


def test_segments_are_the_preregistered_dates():
    assert [swing.segment(d) for d in ("2024-12-31", "2025-01-02", "2025-12-31", "2026-01-05")] == \
        ["train", "test1", "test1", "test2"]
