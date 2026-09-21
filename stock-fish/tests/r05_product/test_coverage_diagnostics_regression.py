"""Coverage diagnostics regressions for sparse real-market windows."""
from __future__ import annotations

from stockfish_core.yaogu.coverage_diagnostics import _same_comparable_window


def test_same_comparable_window_treats_missing_bar_as_not_computable():
    window = [
        None,
        {"comparable_basis": "BAOSTOCK_HFQ_RAW_X_BACK_FACTOR_TEST", "comparable_close": "10.0"},
    ]

    assert _same_comparable_window(window) is False
