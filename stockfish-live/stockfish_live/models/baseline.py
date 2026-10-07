"""Naive comparators, fit/selected from the same visible historical universe."""
from __future__ import annotations

from statistics import fmean

from ..contracts.forecast import ForecastConfig, HistoricalState
from .regime import empirical_regime
from .similarity import QUANTILES, eligible_history, quantile

BASELINES = ("ZERO_RETURN", "CURRENT_TREND", "TIME_OF_DAY_MEAN", "PREVIOUS_DAY_PATH", "EMPIRICAL_PRIOR")


def baseline_predictions(query: HistoricalState, states, outcomes, *, as_of, horizon: str,
                         target_at, config: ForecastConfig | None = None) -> dict:
    """No missing baseline is replaced by ZERO_RETURN.

    CURRENT_TREND extrapolates observed return_5m_pct over remaining trading
    minutes (constant slope initial hypothesis). PREVIOUS_DAY_PATH means the
    most recent *eligible, visible* same-time previous session path.
    """
    config = config or ForecastConfig()
    candidates = eligible_history(query, states, outcomes, as_of=as_of,
                                   horizon=horizon, feature_names=())
    returns = [label.return_fraction for _, label in candidates]
    minutes = ((target_at.hour * 60 + target_at.minute) - (query.decision_at.hour * 60 + query.decision_at.minute))
    if query.decision_at.hour < 12 and target_at.hour >= 13:
        minutes -= 90
    trend = query.features.get("return_5m_pct")
    previous = candidates[-1][1].return_fraction if candidates else None
    predictions = {"ZERO_RETURN": 0., "CURRENT_TREND": trend / 100. * minutes / 5. if trend is not None else None,
                   "TIME_OF_DAY_MEAN": fmean(returns) if returns else None,
                   "PREVIOUS_DAY_PATH": previous, "EMPIRICAL_PRIOR": quantile(returns, .5)}
    probabilities, regime_n = empirical_regime(label.regime for _, label in candidates)
    result = {}
    for name, prediction in predictions.items():
        entry = {"status": "AVAILABLE" if prediction is not None else "UNAVAILABLE",
                 "prediction": prediction, "sample_size": len(returns) if name not in ("ZERO_RETURN", "CURRENT_TREND") else 0,
                 "quantiles": {q: prediction for q in QUANTILES} if prediction is not None else {},
                 "up_probability": (1. if prediction > 0 else 0.) if prediction is not None else None,
                 "regime_probabilities": {}, "regime_sample_size": 0}
        if name == "EMPIRICAL_PRIOR" and returns:
            entry.update(quantiles={q: quantile(returns, p) for q, p in QUANTILES.items()},
                         up_probability=sum(v > 0 for v in returns) / len(returns),
                         regime_probabilities=probabilities, regime_sample_size=regime_n)
        result[name] = entry
    return result
