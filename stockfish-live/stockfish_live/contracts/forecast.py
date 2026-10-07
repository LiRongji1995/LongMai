"""Versioned, independently serializable forecast inputs and outputs.

Returns in this contract are fractions (0.01 = 1%); market feature names ending
in ``_pct`` retain their feature contract's percentage-point units.
"""
from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from ..common import FrozenModel, aware, canonical_instrument, digest
from .quality import PITStatus

Regime = Literal["TREND_UP", "RANGE_STRONG", "RANGE_NEUTRAL", "HIGH_OPEN_FADE", "WEAKENING", "TREND_DOWN"]
REGIMES = ("TREND_UP", "RANGE_STRONG", "RANGE_NEUTRAL", "HIGH_OPEN_FADE", "WEAKENING", "TREND_DOWN")
Horizon = Literal["30m", "60m", "11:30", "14:00", "15:00"]
Confidence = Literal["HIGH", "MEDIUM", "LOW", "INSUFFICIENT"]


class HistoricalState(FrozenModel):
    schema_version: Literal["SIMILARITY_STATE_V1"] = "SIMILARITY_STATE_V1"
    state_id: str = Field(min_length=1)
    instrument_id: str
    decision_at: datetime
    available_at: datetime
    current_price: float = Field(gt=0)
    features: dict[str, float | None]
    feature_version: str = "MARKET_FEATURES_V1"
    pit_status: PITStatus
    context: dict[str, Any] = Field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    @field_validator("instrument_id")
    @classmethod
    def identity(cls, value):
        if canonical_instrument(value) != value:
            raise ValueError("canonical instrument required")
        return value

    @field_validator("decision_at", "available_at")
    @classmethod
    def times(cls, value):
        return aware(value)

    @field_validator("current_price", mode="before")
    @classmethod
    def nonboolean_price(cls, value):
        if isinstance(value, bool):
            raise ValueError("boolean price")
        return value

    @field_validator("features", mode="before")
    @classmethod
    def numbers(cls, value):
        if not isinstance(value, dict) or any(isinstance(v, bool) for v in value.values()):
            raise ValueError("features must be finite numbers or unknown")
        return value

    @model_validator(mode="after")
    def clocks(self):
        if self.available_at < self.decision_at:
            raise ValueError("state availability precedes decision")
        if self.decision_at.second or self.decision_at.microsecond:
            raise ValueError("decision must be an exact minute")
        return self


class PathPoint(FrozenModel):
    timestamp: datetime
    price: float = Field(gt=0)

    @field_validator("timestamp")
    @classmethod
    def time(cls, value):
        return aware(value)

    @field_validator("price", mode="before")
    @classmethod
    def nonboolean_price(cls, value):
        if isinstance(value, bool):
            raise ValueError("boolean price")
        return value


class MatureOutcome(FrozenModel):
    """An actual outcome, never an input feature. Missing labels have no row."""
    schema_version: Literal["MATURE_OUTCOME_V1"] = "MATURE_OUTCOME_V1"
    state_id: str
    instrument_id: str
    decision_at: datetime
    horizon: Horizon
    target_at: datetime
    available_at: datetime
    anchor_price: float = Field(gt=0)
    target_price: float = Field(gt=0)
    regime: Regime | None = None
    regime_version: Literal["MARKET_REGIME_V1"] = "MARKET_REGIME_V1"
    future_path: tuple[PathPoint, ...] = ()
    pit_status: PITStatus
    source_ids: tuple[str, ...] = ()

    @field_validator("instrument_id")
    @classmethod
    def identity(cls, value):
        if canonical_instrument(value) != value:
            raise ValueError("canonical instrument required")
        return value

    @field_validator("decision_at", "target_at", "available_at")
    @classmethod
    def times(cls, value):
        return aware(value)

    @field_validator("anchor_price", "target_price", mode="before")
    @classmethod
    def nonboolean_price(cls, value):
        if isinstance(value, bool):
            raise ValueError("boolean price")
        return value

    @field_validator("source_ids")
    @classmethod
    def source_hashes(cls, value):
        if len(value) != len(set(value)) or any(not re.fullmatch(r"[0-9a-f]{64}", item) for item in value):
            raise ValueError("unique source hashes required")
        return value

    @model_validator(mode="after")
    def clocks(self):
        if not self.decision_at < self.target_at <= self.available_at:
            raise ValueError("outcome is not mature")
        times = [p.timestamp for p in self.future_path]
        if times != sorted(set(times)) or any(not self.decision_at < t <= self.target_at for t in times):
            raise ValueError("outcome path outside label window or duplicate")
        if self.future_path and (self.future_path[-1].timestamp != self.target_at
                                 or self.future_path[-1].price != self.target_price):
            raise ValueError("outcome path endpoint mismatch")
        return self

    @property
    def return_fraction(self) -> float:
        return self.target_price / self.anchor_price - 1.


class ForecastConfig(FrozenModel):
    schema_version: Literal["SIMILARITY_CONFIG_V1"] = "SIMILARITY_CONFIG_V1"
    model_version: Literal["SIMILARITY_MODEL_V1"] = "SIMILARITY_MODEL_V1"
    feature_version: str = "MARKET_FEATURES_V1"
    k: int = Field(default=50, ge=1)
    k_candidates: tuple[int, ...] = (50, 100, 200)
    min_samples: int = Field(default=50, ge=1)
    feature_names: tuple[str, ...] = ()
    max_mean_distance: float = Field(default=3., gt=0)
    wide_interval_fraction: float = Field(default=.03, gt=0)
    freshness_minutes: int = Field(default=5, ge=0)
    parameters_status: Literal["UNVALIDATED"] = "UNVALIDATED"

    @model_validator(mode="after")
    def valid(self):
        if not self.k_candidates or any(isinstance(k, bool) or k < 1 for k in self.k_candidates):
            raise ValueError("positive K candidates required")
        if len(set(self.k_candidates)) != len(self.k_candidates):
            raise ValueError("duplicate K candidates")
        if len(set(self.feature_names)) != len(self.feature_names):
            raise ValueError("duplicate feature names")
        return self

    @property
    def configuration_hash(self) -> str:
        return digest(self.model_dump(mode="json"))


class SimilarNeighbor(FrozenModel):
    state_id: str
    decision_at: datetime
    distance: float = Field(ge=0)
    context: dict[str, Any]
    outcome: MatureOutcome

    @field_validator("decision_at")
    @classmethod
    def time(cls, value):
        return aware(value)


class HorizonForecast(FrozenModel):
    horizon: Horizon
    target_at: datetime
    sample_size: int = Field(ge=0)
    available_sample_size: int = Field(ge=0)
    status: Literal["AVAILABLE", "INSUFFICIENT"]
    confidence: Confidence
    pit_status: PITStatus = "RESEARCH_ONLY"
    quantiles: dict[str, float] = Field(default_factory=dict)
    price_quantiles: dict[str, float] = Field(default_factory=dict)
    up_probability: float | None = Field(default=None, ge=0, le=1)
    regime_probabilities: dict[str, float] = Field(default_factory=dict)
    regime_sample_size: int = Field(default=0, ge=0)
    mean_distance: float | None = Field(default=None, ge=0)
    interval_width: float | None = Field(default=None, ge=0)
    feature_names: tuple[str, ...] = ()
    normalization_mean: dict[str, float] = Field(default_factory=dict)
    normalization_scale: dict[str, float] = Field(default_factory=dict)
    training_state_ids: tuple[str, ...] = ()
    neighbors: tuple[SimilarNeighbor, ...] = ()
    model_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    warnings: tuple[str, ...] = ()

    @field_validator("target_at")
    @classmethod
    def time(cls, value):
        return aware(value)

    @model_validator(mode="after")
    def distribution(self):
        names = ("q10", "q25", "q50", "q75", "q90")
        if self.status == "AVAILABLE":
            if set(self.quantiles) != set(names) or set(self.price_quantiles) != set(names):
                raise ValueError("complete five quantiles required")
            for values in (self.quantiles, self.price_quantiles):
                ordered = [values[n] for n in names]
                if ordered != sorted(ordered):
                    raise ValueError("nonmonotone quantiles")
            if self.sample_size < 1 or self.up_probability is None:
                raise ValueError("available distribution needs samples")
            if self.confidence == "INSUFFICIENT":
                raise ValueError("available forecast requires confidence")
            if any(v <= -1 for v in self.quantiles.values()) or any(v <= 0 for v in self.price_quantiles.values()):
                raise ValueError("distribution implies a nonpositive price")
        elif self.quantiles or self.price_quantiles or self.up_probability is not None:
            raise ValueError("insufficient forecast cannot fabricate distribution")
        elif self.sample_size or self.confidence != "INSUFFICIENT":
            raise ValueError("insufficient forecast cannot claim sample confidence")
        if self.sample_size > self.available_sample_size or self.regime_sample_size > self.sample_size:
            raise ValueError("invalid sample denominator")
        if self.regime_probabilities:
            if set(self.regime_probabilities) != set(REGIMES):
                raise ValueError("complete regime probabilities required")
            if any(p < 0 or p > 1 for p in self.regime_probabilities.values()):
                raise ValueError("invalid probability")
            if not math.isclose(sum(self.regime_probabilities.values()), 1., abs_tol=1e-10):
                raise ValueError("probabilities must sum to one")
            if self.regime_sample_size == 0:
                raise ValueError("regime denominator missing")
        return self


class ForecastResult(FrozenModel):
    schema_version: Literal["INTRADAY_PATH_V1"] = "INTRADAY_PATH_V1"
    model_version: Literal["SIMILARITY_MODEL_V1"] = "SIMILARITY_MODEL_V1"
    feature_version: str = "MARKET_FEATURES_V1"
    forecast_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    instrument_id: str
    decision_at: datetime
    as_of: datetime
    current_price: float = Field(gt=0)
    state_id: str
    input_hash: str | None = None
    data_cutoff: datetime | None = None
    configuration_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    horizons: tuple[HorizonForecast, ...]
    confidence: Confidence
    status: Literal["AVAILABLE", "PARTIAL", "INSUFFICIENT", "NOT_SCHEDULED"]
    warnings: tuple[str, ...]
    pit_status: PITStatus
    parameters_status: Literal["UNVALIDATED"] = "UNVALIDATED"
    market_edge: Literal["NOT_ESTABLISHED"] = "NOT_ESTABLISHED"

    @field_validator("decision_at", "as_of", "data_cutoff")
    @classmethod
    def time(cls, value):
        return aware(value) if value is not None else value

    @model_validator(mode="after")
    def clocks(self):
        if self.decision_at > self.as_of:
            raise ValueError("future prediction")
        if self.data_cutoff is not None and self.data_cutoff > self.as_of:
            raise ValueError("future data cutoff")
        if any(h.target_at <= self.decision_at for h in self.horizons):
            raise ValueError("expired forecast horizon")
        for horizon in self.horizons:
            if any(not math.isclose(horizon.price_quantiles[k], self.current_price * (1 + v), rel_tol=1e-10)
                   for k, v in horizon.quantiles.items()):
                raise ValueError("forecast price/return anchor mismatch")
            for neighbor in horizon.neighbors:
                if neighbor.outcome.available_at > self.as_of:
                    raise ValueError("future neighbor outcome")
        return self
