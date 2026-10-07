"""Deterministic nearest neighbours with normalization fit on eligible history.

The complete candidate fit universe, normalization and selected sample count
are retained in each horizon. There is no random split or shared global fit.
"""
from __future__ import annotations

import math
import json
import hashlib
import zlib
from dataclasses import dataclass
from datetime import datetime
from statistics import fmean
from types import MappingProxyType

from ..common import LiveError, aware, canonical_json, digest
from ..contracts.forecast import (ForecastConfig, HistoricalState, HorizonForecast,
                                  MatureOutcome, SimilarNeighbor)
from ..contracts.quality import effective_pit_status
from .regime import empirical_regime

QUANTILES = {"q10": .1, "q25": .25, "q50": .5, "q75": .75, "q90": .9}


@dataclass(frozen=True)
class _PreparedState:
    """Detached state with immutable scalar features and copy-on-read context."""
    state_id: str
    instrument_id: str
    decision_at: datetime
    available_at: datetime
    current_price: float
    feature_version: str
    pit_status: str
    features: object
    _configuration_hash: str
    _payload: str
    _context: str

    @classmethod
    def from_state(cls, state):
        return cls(state.state_id, state.instrument_id, state.decision_at, state.available_at,
                   state.current_price, state.feature_version, state.pit_status,
                   MappingProxyType(dict(state.features)), canonical_json(state.context.get("configuration_hash")),
                   canonical_json(state.model_dump(mode="json")),
                   canonical_json(state.model_dump(mode="json")["context"]))

    @property
    def context(self):
        return json.loads(self._context)

    def model_dump(self, **_kwargs):
        return json.loads(self._payload)


@dataclass(frozen=True, slots=True)
class _PreparedOutcome:
    """Validated scalars plus lossless compressed canonical evidence.

    Millions of PathPoint objects are useful only for the displayed neighbours.
    Inference reads the scalar outcome; the exact full path is retained for
    hashing and reconstructed as a typed contract only when requested.
    """
    state_id: str
    instrument_id: str
    decision_at: datetime
    horizon: str
    target_at: datetime
    available_at: datetime
    anchor_price: float
    target_price: float
    regime: str | None
    pit_status: str
    path_point_count: int
    _payload: bytes
    fingerprint: str

    @classmethod
    def from_outcome(cls, label):
        payload = canonical_json(label.model_dump(mode="json")).encode("utf-8")
        return cls(label.state_id, label.instrument_id, label.decision_at, label.horizon,
                   label.target_at, label.available_at, label.anchor_price, label.target_price,
                   label.regime, label.pit_status, len(label.future_path), zlib.compress(payload, level=1),
                   hashlib.sha256(payload).hexdigest())

    @property
    def return_fraction(self):
        return self.target_price / self.anchor_price - 1.

    @property
    def future_path(self):
        return self.as_model().future_path

    @property
    def source_ids(self):
        return tuple(json.loads(self.canonical_bytes())["source_ids"])

    def canonical_bytes(self):
        return zlib.decompress(self._payload)

    def model_dump(self, *, mode="python", **kwargs):
        if mode == "json" and not kwargs:
            return json.loads(self.canonical_bytes())
        return self.as_model().model_dump(mode=mode, **kwargs)

    def as_model(self):
        return MatureOutcome.model_validate_json(self.canonical_bytes())


class PreparedHistory:
    """One startup validation and index, independent of any inference cutoff.

    Future labels may exist in the trusted archive loader. Selection still
    checks their actual availability, strict prior session and exact decision
    time on every query. The full archive fingerprint never enters a forecast
    hash; changing an unavailable label cannot change a visible prediction.
    Inputs are detached from caller-owned mutable feature/context dictionaries.
    """

    __slots__ = ("_by_time", "_labels", "_fit_cache", "_normalization_cache", "_calendar_version",
                 "_state_count", "_outcome_count", "_history_hash")

    def __init__(self, states, outcomes):
        indexed, state_ids, labels, label_hashes, decision_ids = {}, {}, {}, {}, {}
        for raw in states:
            state = HistoricalState.model_validate(raw.model_dump())
            immutable = _PreparedState.from_state(state)
            previous = state_ids.get(state.state_id)
            if previous is not None:
                if previous._payload != immutable._payload:
                    raise LiveError("STATE_ID_CONFLICT")
                continue
            state_ids[state.state_id] = immutable
            business_key = (state.instrument_id, state.decision_at, state.feature_version, immutable._configuration_hash)
            if business_key in decision_ids:
                raise LiveError("STATE_DECISION_CONFLICT")
            decision_ids[business_key] = state.state_id
            key = (state.instrument_id, state.decision_at.time(), state.feature_version, immutable._configuration_hash)
            indexed.setdefault(key, []).append(immutable)
        for raw in outcomes:
            label = MatureOutcome.model_validate(raw.model_dump())
            immutable = _PreparedOutcome.from_outcome(label)
            key = (immutable.state_id, immutable.horizon)
            fingerprint = immutable.fingerprint
            if key in labels:
                if label_hashes[key] != fingerprint:
                    raise LiveError("OUTCOME_ID_CONFLICT")
                continue
            labels[key] = immutable
            label_hashes[key] = fingerprint
        self._by_time = {key: tuple(sorted(values, key=lambda s: (s.decision_at, s.state_id)))
                         for key, values in indexed.items()}
        self._labels = labels
        self._fit_cache = {}
        self._normalization_cache = {}
        self._calendar_version = None
        self._state_count, self._outcome_count = len(state_ids), len(labels)
        self._history_hash = digest({"states": [(key, digest(value.model_dump()))
                                               for key, value in sorted(state_ids.items())],
                                    "outcomes": [(key, value) for key, value in sorted(label_hashes.items())]})

    @property
    def state_count(self):
        return self._state_count

    @property
    def outcome_count(self):
        return self._outcome_count

    @property
    def history_hash(self):
        return self._history_hash

    def validate_targets(self, calendar, targets_for):
        """Bind one immutable archive to the calendar before intraday use."""
        version = calendar.version
        if self._calendar_version == version:
            return
        if self._calendar_version is not None:
            raise LiveError("PREPARED_HISTORY_CALENDAR_MISMATCH")
        expected = {}
        for label in self._labels.values():
            if label.decision_at not in expected:
                expected[label.decision_at] = targets_for(calendar, label.decision_at)
            if expected[label.decision_at].get(label.horizon) != label.target_at:
                raise LiveError("OUTCOME_HORIZON_MISMATCH")
        self._calendar_version = version

    def _eligible(self, query, *, as_of, horizon, feature_names):
        candidates = []
        for state in self.prior_states(query, as_of=as_of):
            if any(state.features.get(name) is None for name in feature_names):
                continue
            label = self._labels.get((state.state_id, horizon))
            if label is None or label.available_at > as_of:
                continue
            if (label.instrument_id != state.instrument_id or label.decision_at != state.decision_at
                    or label.anchor_price != state.current_price):
                raise LiveError("OUTCOME_STATE_MISMATCH")
            candidates.append((state, label))
        return candidates

    def prior_states(self, query, *, as_of):
        """Visible same-instrument/version/time states, strictly earlier dates."""
        as_of = aware(as_of)
        if query.decision_at > as_of or query.available_at > as_of:
            raise LiveError("FUTURE_FEATURE_ACCESS_DENIED")
        key = (query.instrument_id, query.decision_at.time(), query.feature_version,
               canonical_json(query.context.get("configuration_hash")))
        return tuple(state for state in self._by_time.get(key, ())
                     if state.decision_at.date() < query.decision_at.date() and state.available_at <= as_of)

    def visible_label(self, state_id, horizon, *, as_of):
        label = self._labels.get((state_id, horizon))
        return label if label is not None and label.available_at <= aware(as_of) else None

    def _fit(self, candidates, features, config, horizon):
        identities = tuple(state.state_id for state, _ in candidates)
        fit_key = (identities, features, config.configuration_hash, horizon)
        previous = self._fit_cache.get(fit_key)
        if previous is None:
            norm_key = (identities, features)
            norm = self._normalization_cache.get(norm_key)
            if norm is None:
                norm = _normalization(candidates, features)
                self._normalization_cache[norm_key] = norm
            means, scales = norm
            model_hash = _model_hash(candidates, features, config, horizon, means, scales)
            previous = (means, scales, model_hash)
            self._fit_cache[fit_key] = previous
        means, scales, model_hash = previous
        return dict(means), dict(scales), model_hash


def _normalization(candidates, features):
    means = {name: fmean(s.features[name] for s, _ in candidates) for name in features} if candidates else {}
    scales = {name: math.sqrt(fmean((s.features[name] - means[name]) ** 2
                                   for s, _ in candidates)) or 1. for name in features} if candidates else {}
    return means, scales


def _model_hash(candidates, features, config, horizon, means, scales):
    # Exactly canonical_json(dict(...))'s sorted key order and array bytes,
    # streamed to SHA256 so full candidate paths never become a second list
    # of decoded dictionaries. Both raw and prepared models retain old hashes.
    result = hashlib.sha256()
    def update(value):
        result.update(canonical_json(value).encode("utf-8"))
    result.update(b'{"config":')
    update(config.model_dump(mode="json"))
    result.update(b',"features":')
    update(features)
    result.update(b',"horizon":')
    update(horizon)
    result.update(b',"labels":[')
    for position, (_, label) in enumerate(candidates):
        if position:
            result.update(b',')
        if isinstance(label, _PreparedOutcome):
            result.update(label.canonical_bytes())
        else:
            update(label.model_dump(mode="json"))
    result.update(b'],"mean":')
    update(means)
    result.update(b',"scale":')
    update(scales)
    result.update(b',"training_states":[')
    for position, (state, _) in enumerate(candidates):
        if position:
            result.update(b',')
        if isinstance(state, _PreparedState):
            result.update(state._payload.encode("utf-8"))
        else:
            update(state.model_dump(mode="json"))
    result.update(b']}')
    return result.hexdigest()


def quantile(values, probability):
    if not 0 <= probability <= 1:
        raise ValueError("quantile probability outside [0,1]")
    values = sorted(values)
    if not values:
        return None
    index = (len(values) - 1) * probability
    lo, hi = math.floor(index), math.ceil(index)
    return values[lo] + (values[hi] - values[lo]) * (index - lo)


def eligible_history(query: HistoricalState, states, outcomes, *, as_of: datetime,
                     horizon: str, feature_names: tuple[str, ...]):
    """Match identity, decision time, mature label and actual visibility clocks."""
    as_of = aware(as_of)
    if query.decision_at > as_of or query.available_at > as_of:
        raise LiveError("FUTURE_FEATURE_ACCESS_DENIED")
    if isinstance(states, PreparedHistory):
        if outcomes is not None and tuple(outcomes):
            raise LiveError("PREPARED_HISTORY_ARGUMENT_CONFLICT")
        return states._eligible(query, as_of=as_of, horizon=horizon, feature_names=feature_names)
    labels = {}
    for raw in outcomes:
        # Exclude unavailable rows before reading their actual return/path.
        if (aware(raw.available_at) > as_of or raw.horizon != horizon
                or raw.instrument_id != query.instrument_id
                or aware(raw.decision_at).time() != query.decision_at.time()
                or aware(raw.decision_at).date() >= query.decision_at.date()):
            continue
        label = MatureOutcome.model_validate(raw.model_dump())
        if label.horizon != horizon:
            continue
        key = label.state_id
        if key in labels and labels[key] != label:
            raise LiveError("OUTCOME_ID_CONFLICT")
        labels[key] = label
    candidates, seen = [], {}
    for raw in states:
        if (aware(raw.available_at) > as_of or raw.instrument_id != query.instrument_id
                or raw.feature_version != query.feature_version
                or raw.context.get("configuration_hash") != query.context.get("configuration_hash")
                or aware(raw.decision_at).date() >= query.decision_at.date()
                or aware(raw.decision_at).time() != query.decision_at.time()):
            continue
        state = HistoricalState.model_validate(raw.model_dump())
        if state.state_id in seen and seen[state.state_id] != state:
            raise LiveError("STATE_ID_CONFLICT")
        if state.state_id in seen:
            continue
        seen[state.state_id] = state
        if (state.instrument_id != query.instrument_id or state.feature_version != query.feature_version
                or state.context.get("configuration_hash") != query.context.get("configuration_hash")
                or state.decision_at.date() >= query.decision_at.date()
                or state.decision_at.time() != query.decision_at.time()):
            continue
        if any(state.features.get(name) is None for name in feature_names):
            continue
        label = labels.get(state.state_id)
        if label is None:
            continue
        if (label.instrument_id != state.instrument_id or label.decision_at != state.decision_at
                or label.anchor_price != state.current_price):
            raise LiveError("OUTCOME_STATE_MISMATCH")
        candidates.append((state, label))
    candidates.sort(key=lambda pair: (pair[0].decision_at, pair[0].state_id))
    return candidates


class SimilarityModel:
    def __init__(self, config: ForecastConfig | None = None):
        self.config = ForecastConfig.model_validate((config or ForecastConfig()).model_dump())

    def predict(self, query: HistoricalState, states, outcomes=(), *, as_of: datetime,
                horizon: str, target_at: datetime) -> HorizonForecast:
        query = HistoricalState.model_validate(query.model_dump())
        as_of, target_at = aware(as_of), aware(target_at)
        if query.decision_at > as_of or query.available_at > as_of:
            raise LiveError("FUTURE_FEATURE_ACCESS_DENIED")
        config = self.config
        if query.feature_version != config.feature_version:
            raise LiveError("FEATURE_VERSION_MISMATCH")
        features = config.feature_names or tuple(sorted(k for k, v in query.features.items() if v is not None))
        warnings = ["MODEL_PARAMETERS_UNVALIDATED"]
        missing = tuple(k for k in query.features if query.features[k] is None)
        if missing:
            warnings.append("FEATURES_PARTIALLY_UNAVAILABLE")
        valid_features = bool(features) and all(query.features.get(k) is not None for k in features)
        candidates = eligible_history(query, states, outcomes, as_of=as_of,
                                      horizon=horizon, feature_names=features) if valid_features else []
        n = len(candidates)
        # All fit rows affect normalization, including rows outside the top K
        # and fits with insufficient labels. Synthetic provenance cannot mix.
        provenance = effective_pit_status([query.pit_status] + [item.pit_status
            for state, label in candidates for item in (state, label)])
        if isinstance(states, PreparedHistory):
            means, scales, model_hash = states._fit(candidates, features, config, horizon)
        else:
            means, scales = _normalization(candidates, features)
            model_hash = _model_hash(candidates, features, config, horizon, means, scales)
        common = dict(horizon=horizon, target_at=target_at, available_sample_size=n,
                      feature_names=features, normalization_mean=means, normalization_scale=scales,
                      training_state_ids=tuple(s.state_id for s, _ in candidates), model_hash=model_hash,
                      pit_status=provenance)
        if not valid_features:
            warnings.append("QUERY_FEATURES_UNAVAILABLE")
        if n < config.min_samples:
            warnings.extend(("INSUFFICIENT_MATURE_SAMPLES", "MODEL_LOW_CONFIDENCE"))
            return HorizonForecast(**common, sample_size=0, status="INSUFFICIENT",
                                   confidence="INSUFFICIENT", warnings=tuple(warnings))
        distances = [(math.sqrt(fmean(((query.features[name] - state.features[name]) / scales[name]) ** 2
                                      for name in features)), state, label)
                     for state, label in candidates]
        distances.sort(key=lambda entry: (entry[0], entry[1].decision_at, entry[1].state_id))
        selected = distances[:config.k]
        returns = [label.return_fraction for _, _, label in selected]
        quantiles = {name: quantile(returns, p) for name, p in QUANTILES.items()}
        mean_distance = fmean(distance for distance, _, _ in selected)
        width = quantiles["q90"] - quantiles["q10"]
        probabilities, regime_n = empirical_regime(label.regime for _, _, label in selected)
        confidence = "HIGH" if len(selected) >= 200 else "MEDIUM" if len(selected) >= 50 else "LOW"
        if len(selected) < config.k:
            warnings.append("K_EXCEEDS_AVAILABLE_SAMPLES")
            confidence = "LOW"
        if config.k < 50 or config.min_samples < 50:
            warnings.append("SMALL_SAMPLE_CONFIGURATION")
            confidence = "LOW"
        if provenance != "VERIFIED_PIT":
            warnings.append("PIT_EVIDENCE_LIMITED")
            confidence = "LOW"
        if mean_distance > config.max_mean_distance:
            warnings.append("SIMILARITY_DISTANCE_HIGH")
            confidence = "LOW"
        if width > config.wide_interval_fraction:
            warnings.append("PREDICTION_INTERVAL_WIDE")
            confidence = "LOW"
        if regime_n < len(selected):
            warnings.append("REGIME_LABELS_PARTIALLY_UNAVAILABLE")
            confidence = "LOW"
        if probabilities and max(probabilities.values()) < .5:
            warnings.append("REGIME_DISAGREEMENT")
        if missing or query.warnings:
            confidence = "LOW"
        if confidence == "LOW":
            warnings.append("MODEL_LOW_CONFIDENCE")
        return HorizonForecast(**common, sample_size=len(selected), status="AVAILABLE", confidence=confidence,
            quantiles=quantiles, price_quantiles={k: query.current_price * (1. + v) for k, v in quantiles.items()},
            up_probability=sum(value > 0 for value in returns) / len(returns),
            regime_probabilities=probabilities, regime_sample_size=regime_n,
            mean_distance=mean_distance, interval_width=width,
            neighbors=tuple(SimilarNeighbor(state_id=state.state_id, decision_at=state.decision_at,
                distance=distance, context=state.context,
                outcome=label.as_model() if isinstance(label, _PreparedOutcome) else label)
                for distance, state, label in selected[:10]), warnings=tuple(warnings))
