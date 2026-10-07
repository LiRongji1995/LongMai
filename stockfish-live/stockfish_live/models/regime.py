"""MARKET_REGIME_V1: initial hypotheses, not validated market thresholds."""
from __future__ import annotations

import math

from ..contracts.forecast import REGIMES


def classify_regime(anchor_price: float, target_price: float, high_price: float,
                    low_price: float, *, gap_pct: float | None = None) -> str:
    """Classify the *mature future window*, not an unavailable current truth.

    Returns and range use fractional units; gap_pct uses percentage points.
    TREND_UP/DOWN require +/-0.3% and final position >=.7/<=.3. A positive
    opening gap >=.3% followed by negative window return is HIGH_OPEN_FADE.
    Remaining +/-0.1% windows are RANGE_STRONG/WEAKENING, otherwise neutral.
    These thresholds are frozen initial hypotheses and remain UNVALIDATED.
    """
    values = (anchor_price, target_price, high_price, low_price)
    if any(isinstance(v, bool) or not math.isfinite(v) or v <= 0 for v in values):
        raise ValueError("finite positive regime prices required")
    if not low_price <= min(anchor_price, target_price) <= max(anchor_price, target_price) <= high_price:
        raise ValueError("invalid regime price range")
    if gap_pct is not None and (isinstance(gap_pct, bool) or not math.isfinite(gap_pct)):
        raise ValueError("invalid gap")
    result = target_price / anchor_price - 1.
    position = ((target_price - low_price) / (high_price - low_price)
                if high_price > low_price else .5)
    if gap_pct is not None and gap_pct >= .3 and result < 0:
        return "HIGH_OPEN_FADE"
    if result >= .003 and position >= .7:
        return "TREND_UP"
    if result <= -.003 and position <= .3:
        return "TREND_DOWN"
    if result >= .001:
        return "RANGE_STRONG"
    if result <= -.001:
        return "WEAKENING"
    return "RANGE_NEUTRAL"


def empirical_regime(labels) -> tuple[dict[str, float], int]:
    """Unknown labels are excluded from the explicit probability denominator."""
    counts = dict.fromkeys(REGIMES, 0)
    for label in labels:
        if label is None:
            continue
        if label not in counts:
            raise ValueError("unknown regime label")
        counts[label] += 1
    n = sum(counts.values())
    return ({label: count / n for label, count in counts.items()} if n else {}), n
