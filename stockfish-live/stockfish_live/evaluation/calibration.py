"""Calibration diagnostics only; no test-window fit or post-hoc adjustment."""
from __future__ import annotations

import math
from statistics import fmean


def calibration_table(probabilities, events, *, bins: int = 10) -> dict:
    if not isinstance(bins, int) or isinstance(bins, bool) or bins < 1:
        raise ValueError("positive bin count required")
    probabilities, events = list(probabilities), list(events)
    if len(probabilities) != len(events):
        raise ValueError("calibration arrays must align")
    groups = [[] for _ in range(bins)]
    for probability, event in zip(probabilities, events):
        if probability is None or event is None:
            continue
        if not math.isfinite(probability) or not 0 <= probability <= 1 or event not in (0, 1, False, True):
            raise ValueError("invalid calibration probability/event")
        groups[min(int(probability * bins), bins - 1)].append((probability, int(event)))
    n = sum(map(len, groups))
    rows = []
    for index, group in enumerate(groups):
        predicted = fmean(p for p, _ in group) if group else None
        observed = fmean(y for _, y in group) if group else None
        rows.append({"lower": index / bins, "upper": (index + 1) / bins, "n": len(group),
                     "mean_probability": predicted, "observed_frequency": observed})
    ece = sum(row["n"] * abs(row["mean_probability"] - row["observed_frequency"])
              for row in rows if row["n"]) / n if n else None
    return {"n": n, "expected_calibration_error": ece, "bins": rows}
