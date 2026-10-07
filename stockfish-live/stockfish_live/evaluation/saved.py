"""Mature posterior of immutable saved forecasts, without any model refit.

Inputs must already have passed ResearchStore and original parent-source
validation. This function additionally checks identity, anchor and actual
clocks; it never fills a missing prediction or outcome with zero.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ..common import LiveError, aware, digest
from ..contracts.forecast import ForecastResult, HistoricalState, MatureOutcome
from ..contracts.quality import effective_pit_status
from ..models.similarity import PreparedHistory
from .metrics import distribution_metrics, regime_metrics


def evaluate_saved_forecasts(forecasts, states, outcomes, *, as_of: datetime) -> dict:
    as_of = aware(as_of)
    state_index, outcome_index, forecast_index = {}, {}, {}
    skipped = []
    for raw in states:
        if aware(raw.available_at) > as_of:
            continue
        state = HistoricalState.model_validate(raw.model_dump())
        if state.state_id in state_index and state_index[state.state_id] != state:
            raise LiveError("STATE_ID_CONFLICT")
        state_index[state.state_id] = state
    prepared = outcomes if isinstance(outcomes, PreparedHistory) else None
    for raw in () if prepared is not None else outcomes:
        # Unavailable actual values never enter report rows, metrics or hash.
        if aware(raw.available_at) > as_of:
            continue
        outcome = MatureOutcome.model_validate(raw.model_dump())
        key = (outcome.state_id, outcome.horizon)
        if key in outcome_index and outcome_index[key] != outcome:
            raise LiveError("OUTCOME_ID_CONFLICT")
        outcome_index[key] = outcome
    for raw in forecasts:
        if aware(raw.as_of) > as_of:
            skipped.append({"forecast_id": raw.forecast_id, "reason": "FORECAST_NOT_YET_AVAILABLE"})
            continue
        forecast = ForecastResult.model_validate(raw.model_dump())
        if forecast.forecast_id in forecast_index and forecast_index[forecast.forecast_id] != forecast:
            raise LiveError("FORECAST_ID_CONFLICT")
        forecast_index[forecast.forecast_id] = forecast
    rows = []
    for forecast in sorted(forecast_index.values(), key=lambda f: (f.decision_at, f.instrument_id, f.forecast_id)):
        base = {"forecast_id": forecast.forecast_id, "state_id": forecast.state_id,
                "instrument_id": forecast.instrument_id, "decision_at": forecast.decision_at.isoformat()}
        state = state_index.get(forecast.state_id)
        if state is None:
            skipped.append({**base, "reason": "FEATURE_STATE_UNAVAILABLE"})
            continue
        if (forecast.instrument_id != state.instrument_id or forecast.decision_at != state.decision_at
                or forecast.current_price != state.current_price or forecast.feature_version != state.feature_version
                or (forecast.input_hash is not None and state.context.get("input_hash") is not None
                    and forecast.input_hash != state.context["input_hash"])):
            raise LiveError("FORECAST_STATE_MISMATCH")
        # A nominal completed-minute label can have a real sub-minute capture
        # delay. A capture in a later minute cannot be called its old decision.
        if not forecast.decision_at <= forecast.as_of < forecast.decision_at + timedelta(minutes=1):
            skipped.append({**base, "actual_as_of": forecast.as_of.isoformat(), "reason": "STALE_DECISION_CAPTURE"})
            continue
        if state.available_at > forecast.as_of:
            skipped.append({**base, "reason": "FEATURE_NOT_AVAILABLE_AT_FORECAST"})
            continue
        if not forecast.horizons:
            skipped.append({**base, "reason": "FORECAST_HAS_NO_FUTURE_HORIZONS"})
        for horizon in forecast.horizons:
            lineage = {**base, "horizon": horizon.horizon, "target_at": horizon.target_at.isoformat()}
            if horizon.status != "AVAILABLE":
                skipped.append({**lineage, "reason": "PREDICTION_INSUFFICIENT"})
                continue
            outcome = (prepared.visible_label(state.state_id, horizon.horizon, as_of=as_of)
                       if prepared is not None else outcome_index.get((state.state_id, horizon.horizon)))
            if outcome is None:
                skipped.append({**lineage, "reason": "OUTCOME_UNAVAILABLE_OR_UNMATURED"})
                continue
            if (outcome.instrument_id != state.instrument_id or outcome.decision_at != state.decision_at
                    or outcome.anchor_price != state.current_price or outcome.target_at != horizon.target_at):
                raise LiveError("OUTCOME_STATE_MISMATCH")
            if outcome.target_at <= forecast.as_of:
                skipped.append({**lineage, "reason": "OUTCOME_TARGET_NOT_FUTURE_AT_FORECAST"})
                continue
            provenance = effective_pit_status((forecast.pit_status, horizon.pit_status, state.pit_status, outcome.pit_status))
            evidence = "REAL_VERIFIED_PIT" if provenance == "VERIFIED_PIT" else "TEST_FIXTURE" if provenance == "TEST_FIXTURE" else "PIT_UNVERIFIED_RESEARCH"
            rows.append({**lineage, "decision_time": forecast.decision_at.strftime("%H:%M"),
                "actual_as_of": forecast.as_of.isoformat(), "feature_available_at": state.available_at.isoformat(),
                "outcome_available_at": outcome.available_at.isoformat(), "configuration_hash": forecast.configuration_hash,
                "model_hash": horizon.model_hash, "model_version": forecast.model_version,
                "feature_version": forecast.feature_version, "input_hash": forecast.input_hash,
                "data_cutoff": forecast.data_cutoff.isoformat() if forecast.data_cutoff is not None else None,
                "actual": outcome.return_fraction, "actual_regime": outcome.regime,
                "anchor_price": forecast.current_price, "actual_target_price": outcome.target_price,
                "quantiles": horizon.quantiles, "price_quantiles": horizon.price_quantiles,
                "up_probability": horizon.up_probability, "regime_probabilities": horizon.regime_probabilities,
                "confidence": horizon.confidence, "sample_size": horizon.sample_size,
                "source_ids": list(outcome.source_ids), "pit_status": provenance, "evidence": evidence})
    def summarize(group):
        return {"distribution_metrics": distribution_metrics(group),
            "regime_metrics": regime_metrics([r["regime_probabilities"] for r in group],
                                              [r["actual_regime"] for r in group])}
    real = [row for row in rows if row["evidence"] == "REAL_VERIFIED_PIT"]
    fixture = [row for row in rows if row["evidence"] == "TEST_FIXTURE"]
    research = [row for row in rows if row["evidence"] == "PIT_UNVERIFIED_RESEARCH"]
    groups = {}
    for row in rows:
        key = f'{row["evidence"]}|{row["instrument_id"]}|{row["decision_time"]}|{row["horizon"]}'
        groups.setdefault(key, []).append(row)
    payload = {"schema_version": "SAVED_FORECAST_EVALUATION_V1", "method": "IMMUTABLE_SAVED_FORECAST_POSTERIOR",
        "as_of": as_of.isoformat(), "retrained": False, "recomputed_predictions": False,
        "baseline_status": "NOT_EVALUATED_NO_SAVED_BASELINE", "market_edge": "NOT_ESTABLISHED",
        "investment_evaluation": "REAL_SAVED_FORECAST_DIAGNOSTICS_ONLY" if real else "NOT_EVALUATED",
        "incremental_edge": "NO_EVIDENCE_OF_INCREMENTAL_EDGE", "parameters_status": "UNVALIDATED",
        "real_oos_sample_count": len(real), "fixture_sample_count": len(fixture),
        "pit_unverified_sample_count": len(research),
        "real_oos_metrics": summarize(real) if real else None,
        "synthetic_engineering_metrics": summarize(fixture) if fixture else None,
        "pit_unverified_research_metrics": summarize(research) if research else None,
        "by_instrument_time_horizon": {key: summarize(group) for key, group in sorted(groups.items())},
        "rows": rows, "skipped": sorted(skipped, key=lambda row: (row["forecast_id"], row.get("horizon", ""), row["reason"])),
        "limitations": ["SAVED_FORECAST_LINEAGE_NOT_INDEPENDENTLY_AUTHENTICATED_BY_THIS_FUNCTION",
                        "INITIAL_PARAMETERS_UNVALIDATED", "OVERLAPPING_HORIZONS_NOT_INDEPENDENT",
                        "NO_TRANSACTION_COST_OR_EXECUTION_MODEL", "NO_SAVED_BASELINE_COMPARISON",
                        "NO_PROSPECTIVE_LONG_TERM_VALIDATION"]}
    return {"evaluation_id": digest(payload), **payload}
