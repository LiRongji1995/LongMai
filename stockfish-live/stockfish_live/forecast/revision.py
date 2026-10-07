"""Revision deltas share the same absolute future target and price anchor."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..common import LiveError, aware, digest
from ..contracts.forecast import ForecastResult


def _trading_distance(earlier, later):
    if earlier.date() != later.date():
        return None
    def minute_index(value):
        minute = value.hour * 60 + value.minute
        return minute - 570 if minute <= 690 else 120 + minute - 780
    return minute_index(later) - minute_index(earlier)


@dataclass(frozen=True, slots=True)
class _RevisionHorizon:
    horizon: str
    target_at: datetime
    expected_price: float
    remaining_return: float


@dataclass(frozen=True, slots=True)
class _RevisionPoint:
    forecast_id: str
    instrument_id: str
    decision_at: datetime
    as_of: datetime
    current_price: float
    horizons: tuple[_RevisionHorizon, ...]

    @classmethod
    def from_validated(cls, forecast: ForecastResult):
        # No caller-owned model, dictionary, neighbor or future path is retained.
        return cls(forecast.forecast_id, forecast.instrument_id, forecast.decision_at,
            forecast.as_of, forecast.current_price,
            tuple(_RevisionHorizon(h.horizon, h.target_at, h.price_quantiles["q50"], h.quantiles["q50"])
                  for h in forecast.horizons if h.status == "AVAILABLE"))


def _compare_points(current: _RevisionPoint, priors: list[_RevisionPoint]) -> dict:
    """Shared arithmetic and canonical payload for strict and prepared inputs."""
    comparisons = []
    warnings = []
    # A common 15:00 target measures expectation of the same future close.
    now = next((h for h in current.horizons if h.horizon == "15:00"), None)
    ordered = sorted(priors, key=lambda p: p.decision_at, reverse=True)
    for lookback in (5, 15, 30):
        previous = next((p for p in ordered
                         if _trading_distance(p.decision_at, current.decision_at) == lookback), None)
        if previous is None or now is None:
            continue
        old = next((h for h in previous.horizons if h.target_at == now.target_at), None)
        if old is None:
            continue
        delta = now.expected_price / old.expected_price - 1.
        comparisons.append({"lookback_minutes": lookback,
            "previous_forecast_id": previous.forecast_id, "target_at": now.target_at.isoformat(),
            "previous_expected_price": old.expected_price,
            "current_expected_price": now.expected_price,
            "delta_q50_price_fraction": delta,
            "realized_anchor_change_fraction": current.current_price / previous.current_price - 1.,
            "delta_remaining_return_q50": now.remaining_return - old.remaining_return,
            "direction": "IMPROVING" if delta > 1e-12 else "DETERIORATING" if delta < -1e-12 else "UNCHANGED"})
    # Multiple overlapping lookbacks do not prove continuous improvement.
    # Require three consecutive five-trading-minute changes of the same
    # future-close price expectation before emitting a persistence warning.
    sequence = []
    if now is not None:
        recent = sorted((p for p in priors if p.decision_at.date() == current.decision_at.date()),
                        key=lambda p: p.decision_at)[-3:] + [current]
        if len(recent) == 4 and all(_trading_distance(a.decision_at, b.decision_at) == 5
                                   for a, b in zip(recent, recent[1:])):
            for item in recent:
                common = next((h for h in item.horizons if h.target_at == now.target_at), None)
                if common is None:
                    break
                sequence.append(common.expected_price)
    if len(sequence) == 4 and all(b > a for a, b in zip(sequence, sequence[1:])):
        warnings.append("FORECAST_CONTINUOUS_IMPROVEMENT")
    if len(sequence) == 4 and all(b < a for a, b in zip(sequence, sequence[1:])):
        warnings.append("FORECAST_CONTINUOUS_DETERIORATION")
    payload = {"schema_version": "FORECAST_REVISION_V1", "current_forecast_id": current.forecast_id,
        "comparison_anchor": "COMMON_FUTURE_TARGET_PRICE", "comparisons": comparisons,
        "status": "AVAILABLE" if comparisons else "INSUFFICIENT_HISTORY", "warnings": warnings}
    return {"revision_id": digest(payload), **payload}


class RevisionEngine:
    def compare(self, current: ForecastResult, history) -> dict:
        """Strict compatibility boundary for arbitrary caller-owned history."""
        current = ForecastResult.model_validate(current.model_dump())
        priors = [ForecastResult.model_validate(p.model_dump()) for p in history
                  if p.instrument_id == current.instrument_id and p.decision_at < current.decision_at
                  and p.as_of <= current.as_of]
        return _compare_points(_RevisionPoint.from_validated(current),
                               [_RevisionPoint.from_validated(p) for p in priors])


class PreparedRevisionHistory:
    """Pipeline-owned append-only scalar history, with one full check per accept.

    The full forecast is still persisted by the pipeline. This separate index
    holds only detached, immutable revision inputs. It neither caches object
    identity nor trusts mutable nested quantile/context dictionaries later.
    All points remain ordered by their actual decision timestamps: 11:30 and
    13:00 share a trading-minute position but must both remain distinguishable.
    """
    def __init__(self):
        self._by_instrument: dict[str, list[_RevisionPoint]] = {}

    def accept(self, current: ForecastResult, *, as_of: datetime) -> dict:
        cutoff = aware(as_of)
        if aware(current.as_of) > cutoff or aware(current.decision_at) > cutoff:
            raise LiveError("FUTURE_FORECAST_ACCESS_DENIED")
        validated = ForecastResult.model_validate(current.model_dump())
        point = _RevisionPoint.from_validated(validated)
        if point.as_of > cutoff or point.decision_at > cutoff:
            raise LiveError("FUTURE_FORECAST_ACCESS_DENIED")
        history = self._by_instrument.get(point.instrument_id, ())
        priors = [p for p in history if p.decision_at < point.decision_at and p.as_of <= point.as_of]
        result = _compare_points(point, priors)
        self._by_instrument.setdefault(point.instrument_id, []).append(point)
        return result
