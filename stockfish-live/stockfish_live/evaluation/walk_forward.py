"""Expanding, chronological, multiple-decision-time evaluation.

K tuning uses earlier validation decisions with outcomes already visible at
the outer decision. Each inner prediction refits only its own earlier window.
The outer holdout is never a hyperparameter or normalization fitting input.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from statistics import fmean

from ..common import LiveError, aware, digest
from ..contracts.forecast import ForecastConfig, HistoricalState, MatureOutcome
from ..forecast.engine import ForecastEngine, horizon_targets
from ..models.baseline import BASELINES, baseline_predictions
from ..models.similarity import PreparedHistory, SimilarityModel, eligible_history
from .metrics import distribution_metrics, regime_metrics


def decision_clock(query):
    """A minute label may have a real capture delay within that same minute.

    A missed minute is never retroactively presented as its original decision.
    Longer delays need a separately versioned decision policy.
    """
    if (query.available_at.date() != query.decision_at.date()
        or query.available_at >= query.decision_at + timedelta(minutes=1)):
        return None
    return query.available_at


def select_k_candidates(query, states, outcomes, calendar, config, *, min_validation_samples=10,
                        max_validation_samples=20, prepared_history=None):
    """Causal inner expanding validation of the common future close.

    Feasible candidates are compared on exactly the same validation states.
    A single feasible K is reported explicitly, without a comparative win.
    """
    decision = decision_clock(query)
    if decision is None:
        raise LiveError("STALE_DECISION_CAPTURE")
    prepared = prepared_history or ForecastEngine(calendar, config).prepare_history(states, outcomes)
    validation = sorted((s for s in prepared.prior_states(query, as_of=decision)
        if decision_clock(s) is not None),
        key=lambda s: (s.decision_at, s.state_id))[-max_validation_samples:]
    losses = {k: {} for k in config.k_candidates}
    for inner in validation:
        actual = prepared.visible_label(inner.state_id, "15:00", as_of=decision)
        target = horizon_targets(calendar, inner.decision_at).get("15:00")
        if actual is None or target is None or actual.target_at != target:
            continue
        for k in config.k_candidates:
            candidate = ForecastConfig.model_validate(config.model_dump() | {"k": k})
            model = SimilarityModel(candidate)
            forecast = model.predict(inner, prepared, as_of=decision_clock(inner),
                                     horizon="15:00", target_at=target)
            if forecast.status == "AVAILABLE" and forecast.sample_size >= k:
                losses[k][inner.state_id] = abs(forecast.quantiles["q50"] - actual.return_fraction)
    feasible = [k for k in config.k_candidates if len(losses[k]) >= min_validation_samples]
    common = set.intersection(*(set(losses[k]) for k in feasible)) if feasible else set()
    # Larger K may have fewer mature training days. Drop the largest candidate
    # until the shared validation denominator is sufficient; record eligibility.
    while len(feasible) > 1 and len(common) < min_validation_samples:
        feasible.remove(max(feasible))
        common = set.intersection(*(set(losses[k]) for k in feasible))
    scores = {str(k): fmean(losses[k][state_id] for state_id in sorted(common)) for k in feasible} if common else {}
    selected = min(feasible, key=lambda k: (scores[str(k)], k)) if scores else config.k
    return {"selected_k": selected, "status": "SELECTED" if len(scores) > 1 else
            "SINGLE_FEASIBLE_CANDIDATE" if scores else "INSUFFICIENT_INNER_VALIDATION",
            "validation_sample_size": len(common), "mae_by_k": scores,
            "candidate_validation_counts": {str(k): len(losses[k]) for k in config.k_candidates},
            "validation_state_ids": sorted(common), "outer_decision_at": query.decision_at.isoformat(),
            "outer_actual_as_of": decision.isoformat()}


def expanding_walk_forward(states, outcomes, calendar, config: ForecastConfig | None = None, *,
                           as_of: datetime, decision_times=None, min_train_sessions: int | None = None,
                           tune_k: bool = True, min_validation_samples: int = 10) -> dict:
    """Evaluate only mature visible labels; return unambiguous evidence status.

    ``decision_times=None`` evaluates every recorded scheduled decision time;
    optional times are HH:MM strings. Synthetic diagnostics are separate from
    real VERIFIED_PIT OOS metrics and can never establish incremental edge.
    """
    config = ForecastConfig.model_validate((config or ForecastConfig()).model_dump())
    as_of = aware(as_of)
    min_train_sessions = config.min_samples if min_train_sessions is None else min_train_sessions
    if isinstance(min_train_sessions, bool) or min_train_sessions < 1:
        raise ValueError("positive training session count required")
    if min_validation_samples < 1:
        raise ValueError("positive validation sample count required")
    states = tuple(HistoricalState.model_validate(s.model_dump()) for s in states if aware(s.available_at) <= as_of)
    if len({s.state_id for s in states}) != len(states):
        raise LiveError("EVALUATION_DUPLICATE_STATE")
    if isinstance(outcomes, PreparedHistory):
        # The CLI streams proven labels into lossless compressed history. Only
        # displayed neighbours hydrate paths; evaluation actuals are scalars.
        prepared = outcomes
        prepared.validate_targets(calendar, horizon_targets)
    else:
        outcomes = tuple(MatureOutcome.model_validate(y.model_dump()) for y in outcomes if aware(y.available_at) <= as_of)
        labels = set()
        for outcome in outcomes:
            key = (outcome.state_id, outcome.horizon)
            if key in labels:
                raise LiveError("EVALUATION_DUPLICATE_OUTCOME")
            labels.add(key)
        prepared = ForecastEngine(calendar, config).prepare_history(states, outcomes)
    engines = {}
    ordered = sorted(states, key=lambda s: (s.decision_at, s.instrument_id, s.state_id))
    rows, skipped, selections = [], [], []
    for query in ordered:
        if query.decision_at > as_of:
            continue
        if decision_times is not None and query.decision_at.strftime("%H:%M") not in decision_times:
            continue
        actual_clock = decision_clock(query)
        if actual_clock is None:
            skipped.append({"state_id": query.state_id, "reason": "STALE_DECISION_CAPTURE"})
            continue
        history = prepared.prior_states(query, as_of=actual_clock)
        if len({s.decision_at.date() for s in history}) < min_train_sessions:
            skipped.append({"state_id": query.state_id, "reason": "INSUFFICIENT_TRAIN_SESSIONS"})
            continue
        chosen = config
        if tune_k:
            selection = select_k_candidates(query, states, outcomes, calendar, config,
                                            min_validation_samples=min_validation_samples, prepared_history=prepared)
            selections.append({"state_id": query.state_id, **selection})
            chosen = ForecastConfig.model_validate(config.model_dump() | {"k": selection["selected_k"]})
        key = chosen.configuration_hash
        if key not in engines:
            engines[key] = ForecastEngine(calendar, chosen)
        forecast = engines[key].predict(query, prepared, as_of=actual_clock)
        for horizon in forecast.horizons:
            actual = prepared.visible_label(query.state_id, horizon.horizon, as_of=as_of)
            if actual is None:
                skipped.append({"state_id": query.state_id, "horizon": horizon.horizon,
                                "reason": "OUTCOME_UNAVAILABLE_OR_UNMATURED"})
                continue
            if (actual.instrument_id != query.instrument_id or actual.decision_at != query.decision_at
                or actual.anchor_price != query.current_price or actual.target_at != horizon.target_at):
                raise LiveError("OUTCOME_STATE_MISMATCH")
            if horizon.status != "AVAILABLE":
                skipped.append({"state_id": query.state_id, "horizon": horizon.horizon,
                                "reason": "INSUFFICIENT_MATURE_TRAIN_LABELS"})
                continue
            baselines = baseline_predictions(query, prepared, (), as_of=actual_clock,
                horizon=horizon.horizon, target_at=horizon.target_at, config=chosen)
            real = (forecast.pit_status == actual.pit_status == "VERIFIED_PIT")
            rows.append({"state_id": query.state_id, "instrument_id": query.instrument_id,
                "decision_at": query.decision_at.isoformat(), "decision_time": query.decision_at.strftime("%H:%M"),
                "actual_as_of": actual_clock.isoformat(),
                "horizon": horizon.horizon, "target_at": horizon.target_at.isoformat(),
                "outcome_available_at": actual.available_at.isoformat(), "actual": actual.return_fraction,
                "actual_regime": actual.regime, "quantiles": horizon.quantiles,
                "up_probability": horizon.up_probability, "regime_probabilities": horizon.regime_probabilities,
                "sample_size": horizon.sample_size, "configuration_hash": forecast.configuration_hash,
                "model_hash": horizon.model_hash, "selected_k": chosen.k,
                "training_state_ids": list(horizon.training_state_ids),
                "evidence": "REAL_VERIFIED_PIT" if real else "TEST_FIXTURE" if forecast.pit_status == "TEST_FIXTURE"
                            else "PIT_UNVERIFIED_RESEARCH", "baselines": baselines})
    def summarize(group):
        models = {"SIMILARITY_MODEL_V1": distribution_metrics(group)}
        for baseline in BASELINES:
            aligned = [{"actual": row["actual"], **row["baselines"][baseline]} for row in group]
            models[baseline] = distribution_metrics(aligned)
        return {"model_metrics": models,
                "regime_metrics": regime_metrics([r["regime_probabilities"] for r in group],
                                                  [r["actual_regime"] for r in group])}
    real_rows = [r for r in rows if r["evidence"] == "REAL_VERIFIED_PIT"]
    fixture_rows = [r for r in rows if r["evidence"] == "TEST_FIXTURE"]
    research_rows = [r for r in rows if r["evidence"] == "PIT_UNVERIFIED_RESEARCH"]
    groups = {}
    for row in rows:
        key = f'{row["evidence"]}|{row["instrument_id"]}|{row["decision_time"]}|{row["horizon"]}'
        groups.setdefault(key, []).append(row)
    payload = {"schema_version": "VALIDATION_REPORT_V1", "method": "EXPANDING_WALK_FORWARD",
        "configuration_hash": config.configuration_hash, "as_of": as_of.isoformat(),
        "random_split": False, "normalization_fit": "ELIGIBLE_TRAINING_WINDOW_ONLY",
        "parameters_status": "UNVALIDATED", "market_edge": "NOT_ESTABLISHED",
        "investment_evaluation": "REAL_OOS_DIAGNOSTICS_ONLY" if real_rows else "NOT_EVALUATED",
        "incremental_edge": "NO_EVIDENCE_OF_INCREMENTAL_EDGE",
        "real_oos_sample_count": len(real_rows), "fixture_sample_count": len(fixture_rows),
        "pit_unverified_sample_count": len(research_rows),
        "real_oos_metrics": summarize(real_rows) if real_rows else None,
        "synthetic_engineering_metrics": summarize(fixture_rows) if fixture_rows else None,
        "pit_unverified_research_metrics": summarize(research_rows) if research_rows else None,
        "by_instrument_time_horizon": {key: summarize(group) for key, group in sorted(groups.items())},
        "rows": rows, "skipped": skipped, "k_selections": selections,
        "limitations": ["INITIAL_PARAMETERS_UNVALIDATED", "OVERLAPPING_HORIZONS_NOT_INDEPENDENT",
                        "NO_PROSPECTIVE_LIVE_VALIDATION", "NO_TRANSACTION_COST_OR_EXECUTION_MODEL"]}
    return {"evaluation_id": digest(payload), **payload}
