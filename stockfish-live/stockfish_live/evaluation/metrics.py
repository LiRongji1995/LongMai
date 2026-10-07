"""Auditable regression, distribution and six-regime diagnostics."""
from __future__ import annotations

import math
from statistics import fmean

from ..contracts.forecast import REGIMES
from ..models.similarity import QUANTILES
from .calibration import calibration_table


def _rank(values):
    order = sorted(range(len(values)), key=values.__getitem__)
    result = [0.] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        for index in order[start:end]:
            result[index] = (start + end - 1) / 2. + 1.
        start = end
    return result


def spearman(predictions, actuals):
    if len(predictions) != len(actuals):
        raise ValueError("arrays must align")
    if len(predictions) < 2:
        return None
    x, y = _rank(predictions), _rank(actuals)
    mx, my = fmean(x), fmean(y)
    denominator = math.sqrt(sum((v - mx) ** 2 for v in x) * sum((v - my) ** 2 for v in y))
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / denominator if denominator else None


def regression_metrics(predictions, actuals) -> dict:
    predictions, actuals = list(predictions), list(actuals)
    if len(predictions) != len(actuals):
        raise ValueError("arrays must align")
    pairs = [(p, y) for p, y in zip(predictions, actuals) if p is not None and y is not None]
    if any(not math.isfinite(v) for pair in pairs for v in pair):
        raise ValueError("nonfinite metric input")
    n = len(pairs)
    missing = len(actuals) - n
    def sign(v):
        return 1 if v > 0 else -1 if v < 0 else 0
    return {"n": n, "missing_or_unavailable": missing,
        "mae": fmean(abs(p - y) for p, y in pairs) if n else None,
        "rmse": math.sqrt(fmean((p - y) ** 2 for p, y in pairs)) if n else None,
        "direction_accuracy": fmean(sign(p) == sign(y) for p, y in pairs) if n else None,
        "spearman": spearman([p for p, _ in pairs], [y for _, y in pairs])}


def probability_metrics(probabilities, events) -> dict:
    probabilities, events = list(probabilities), list(events)
    if len(probabilities) != len(events):
        raise ValueError("arrays must align")
    pairs = [(p, y) for p, y in zip(probabilities, events) if p is not None and y is not None]
    calibration = calibration_table([p for p, _ in pairs], [y for _, y in pairs])
    n = len(pairs)
    clipped = [(min(max(p, 1e-15), 1. - 1e-15), y) for p, y in pairs]
    return {"n": n, "brier": fmean((p - y) ** 2 for p, y in pairs) if n else None,
        "log_loss": -fmean(y * math.log(p) + (1 - y) * math.log(1 - p) for p, y in clipped) if n else None,
        "calibration": calibration}


def distribution_metrics(rows) -> dict:
    """Rows: {actual, quantiles, up_probability}; missing outcomes excluded."""
    rows = list(rows)
    eligible = [r for r in rows if r.get("actual") is not None and r.get("quantiles")]
    if any(set(r["quantiles"]) != set(QUANTILES) or
           any(not math.isfinite(v) for v in (*r["quantiles"].values(), r["actual"]))
           or list(r["quantiles"][q] for q in QUANTILES) != sorted(r["quantiles"][q] for q in QUANTILES)
           for r in eligible):
        raise ValueError("invalid forecast distribution")
    regression = regression_metrics([r.get("quantiles", {}).get("q50") for r in rows],
                                    [r.get("actual") for r in rows])
    probabilities = probability_metrics([r.get("up_probability") for r in rows],
        [int(r["actual"] > 0) if r.get("actual") is not None else None for r in rows])
    losses = {}
    for name, probability in QUANTILES.items():
        residuals = [r["actual"] - r["quantiles"][name] for r in eligible]
        losses[name] = fmean(max(probability * e, (probability - 1) * e) for e in residuals) if residuals else None
    def coverage(lower, upper):
        return fmean(r["quantiles"][lower] <= r["actual"] <= r["quantiles"][upper] for r in eligible) if eligible else None
    return {**regression, "direction_probability": probabilities, "pinball_loss": losses,
            "coverage_50": coverage("q25", "q75"), "coverage_80": coverage("q10", "q90"),
            "mean_width_50": fmean(r["quantiles"]["q75"] - r["quantiles"]["q25"] for r in eligible) if eligible else None,
            "mean_width_80": fmean(r["quantiles"]["q90"] - r["quantiles"]["q10"] for r in eligible) if eligible else None}


def regime_metrics(probabilities, actuals) -> dict:
    probabilities, actuals = list(probabilities), list(actuals)
    if len(probabilities) != len(actuals):
        raise ValueError("arrays must align")
    pairs = [(p, y) for p, y in zip(probabilities, actuals) if p and y is not None]
    if any(set(p) != set(REGIMES) or y not in REGIMES or
           any(not math.isfinite(v) or not 0 <= v <= 1 for v in p.values()) or
           not math.isclose(sum(p.values()), 1., abs_tol=1e-10) for p, y in pairs):
        raise ValueError("invalid regime probabilities")
    confusion = {actual: dict.fromkeys(REGIMES, 0) for actual in REGIMES}
    for p, actual in pairs:
        predicted = max(REGIMES, key=p.get)
        confusion[actual][predicted] += 1
    return {"n": len(pairs), "confusion_matrix_actual_rows": confusion,
        "accuracy": sum(confusion[label][label] for label in REGIMES) / len(pairs) if pairs else None,
        "brier_multiclass": fmean(sum((p[label] - int(label == y)) ** 2 for label in REGIMES)
                                  for p, y in pairs) if pairs else None,
        "log_loss": -fmean(math.log(max(p[y], 1e-15)) for p, y in pairs) if pairs else None,
        "calibration": {label: calibration_table([p[label] for p, _ in pairs], [int(y == label) for _, y in pairs])
                        for label in REGIMES}}
