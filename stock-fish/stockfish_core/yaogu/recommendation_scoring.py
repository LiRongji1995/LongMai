"""Daily Recommendation Score V1.

This module is an evaluation consumer.  It reads immutable strategy signal
runs plus append-only outcome labels and never feeds values back into signal
calculation or strategy configuration.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from itertools import combinations
from math import comb
import random
from statistics import median
from typing import Any

from .contracts import digest
from .stage_aware_momentum import STAGE_AWARE_ID
from .strategy_tracking import authoritative_strategy_items, select_label_revisions

SCORE_SPEC_ID = "DAILY_RECOMMENDATION_SCORE_V1"
SCORE_IMPLEMENTATION_VERSION = "R11_VALIDATION_SCORING"
SELECTION_RETURN_POLICY_ID = "SELECTION_CLOSE_TO_CLOSE_EQUAL_WEIGHT_RETURN_V1"
BENCHMARK_POLICY_ID = "SAME_SIGNAL_DAY_FROZEN_ELIGIBLE_UNIVERSE_EQUAL_WEIGHT_V1"
SAMPLING_POLICY_ID = "SAME_M_WITHOUT_REPLACEMENT_DETERMINISTIC_RANDOM_BASKETS_V1"
ROLLING_POLICY_ID = "SIGNAL_DAY_EQUAL_WEIGHT_VALID_COMPLETE_SCORES_V1"
DEFAULT_HORIZONS = (1, 3, 5)
DEFAULT_RANDOM_BASKETS = 10_000
DEFAULT_BENCHMARK_MIN_COVERAGE = 0.98
DEFAULT_EXACT_ENUMERATION_LIMIT = 50_000

FOCUS_SCORE = "FOCUS_SCORE"
WATCH_RESEARCH_SCORE = "WATCH_RESEARCH_SCORE"
OFFICIAL_PROSPECTIVE = "OFFICIAL_PROSPECTIVE"
RECONSTRUCTED_HISTORICAL = "RECONSTRUCTED_HISTORICAL"
PRELIMINARY_RESEARCH = "PRELIMINARY_RESEARCH"

SETUP_IDS = ("START", "ACCELERATION", "HIGH_LEVEL_REPAIR", "TREND_PULLBACK")


def _dt(value: Any) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _coverage(valid: int, expected: int) -> float | None:
    return valid / expected if expected else None


def _metric(value: float | None) -> float | None:
    return round(value, 10) if value is not None else None


def _score_value(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def _deterministic_seed(identity: dict[str, Any]) -> int:
    return int(digest(identity)[:16], 16)


def _percentile_score(observed: float, basket_returns: list[float]) -> float:
    lower = sum(value < observed for value in basket_returns)
    equal = sum(value == observed for value in basket_returns)
    return 100.0 * (lower + 0.5 * equal) / len(basket_returns)


def _basket_returns(population: list[float], m: int, seed_identity: dict[str, Any], *,
                    random_basket_count: int, exact_enumeration_limit: int) -> tuple[list[float], dict[str, Any]]:
    if m <= 0:
        raise ValueError("basket size must be positive")
    if len(population) < m:
        raise ValueError("benchmark population smaller than selected cohort")
    total = comb(len(population), m)
    if total <= exact_enumeration_limit:
        values = [sum(population[index] for index in indexes) / m for indexes in combinations(range(len(population)), m)]
        return values, {
            "sampling_mode": "EXACT_ENUMERATION",
            "random_basket_count": len(values),
            "total_combination_count": total,
            "sample_member_count": m,
            "replacement": "WITHOUT_REPLACEMENT",
            "seed": None,
        }
    rng = random.Random(_deterministic_seed(seed_identity))
    values = []
    for _ in range(random_basket_count):
        indexes = rng.sample(range(len(population)), m)
        values.append(sum(population[index] for index in indexes) / m)
    return values, {
        "sampling_mode": "DETERMINISTIC_MONTE_CARLO",
        "random_basket_count": random_basket_count,
        "total_combination_count": total,
        "sample_member_count": m,
        "replacement": "WITHOUT_REPLACEMENT",
        "seed": _deterministic_seed(seed_identity),
    }


@dataclass(frozen=True)
class Cohort:
    selection_tier: str
    setup_id: str | None
    evidence_role: str
    ids: tuple[str, ...]
    official_recommendation_score: bool


def evidence_type_for_run(run: dict[str, Any]) -> str:
    prospective = run.get("prospective_validation") or {}
    if prospective.get("official_primary_signal_countable") is True:
        return OFFICIAL_PROSPECTIVE
    if run.get("coverage_type") == RECONSTRUCTED_HISTORICAL:
        return RECONSTRUCTED_HISTORICAL
    return PRELIMINARY_RESEARCH


def _stage_aware_items(run: dict[str, Any]) -> list[dict[str, Any]]:
    for sid, items in authoritative_strategy_items(run):
        if sid == STAGE_AWARE_ID:
            return list(items)
    return []


def _cohorts(items: list[dict[str, Any]]) -> list[Cohort]:
    focus = tuple(row["instrument_id"] for row in items if row.get("decision_status") == "FOCUS")
    watch = tuple(row["instrument_id"] for row in items if row.get("decision_status") == "WATCH")
    result = [
        Cohort(FOCUS_SCORE, None, "OFFICIAL_RECOMMENDATION", focus, True),
        Cohort(WATCH_RESEARCH_SCORE, None, "WATCH_RESEARCH_ONLY_NOT_FORMAL_RECOMMENDATION", watch, False),
    ]
    for tier, score_name in (("FOCUS", "FOCUS"), ("WATCH", "WATCH")):
        for setup in SETUP_IDS:
            ids = tuple(row["instrument_id"] for row in items
                        if row.get("decision_status") == tier and row.get("primary_stage") == setup)
            result.append(Cohort(f"{score_name}:{setup}", setup, f"{score_name}_SETUP_BREAKDOWN", ids, tier == "FOCUS"))
    weakening = tuple(row["instrument_id"] for row in items
                      if row.get("decision_status") == "WATCH"
                      and row.get("observation_bucket") == "WEAKENING_HIGH_ACTIVITY_OBSERVATION")
    result.append(Cohort("WATCH:WEAKENING_HIGH_ACTIVITY_OBSERVATION", "WEAKENING_HIGH_ACTIVITY_OBSERVATION",
                         "OBSERVATION_COHORT_NOT_FORMAL_SETUP", weakening, False))
    for tier in ("REJECTED", "UNKNOWN"):
        ids = tuple(row["instrument_id"] for row in items if row.get("decision_status") == tier)
        result.append(Cohort(tier, None, "DIAGNOSTIC_COHORT_NOT_RECOMMENDATION_SCORE", ids, False))
    return result


def _latest_labels(outcomes: list[dict[str, Any]], evaluated_at: datetime) -> list[dict[str, Any]]:
    return [
        row for row in select_label_revisions(outcomes, evaluated_at)
        if row.get("strategy_id") == STAGE_AWARE_ID and row.get("label_kind", "SIGNAL") == "SIGNAL"
    ]


def _label_return(row: dict[str, Any] | None) -> float | None:
    if not row or row.get("status") == "NOT_MATURED":
        return None
    value = (row.get("metrics") or {}).get("close_reference_change")
    return float(value) if value is not None else None


def _status_for_selected(selected_labels: list[dict[str, Any] | None], selected_returns: list[float], m: int) -> str | None:
    if m == 0:
        return "N_A_NO_SELECTION"
    if len(selected_returns) == m:
        return None
    statuses = {row.get("status") if row else "MISSING_OUTCOME_LABEL" for row in selected_labels}
    if statuses <= {"NOT_MATURED", "MISSING_OUTCOME_LABEL"} or "NOT_MATURED" in statuses:
        return "NOT_MATURED"
    return "PARTIAL_SELECTED_OUTCOME_INCOMPLETE"


def compute_score_records(run: dict[str, Any], outcomes: list[dict[str, Any]], evaluated_at: datetime | str, *,
                          horizons: tuple[int, ...] = DEFAULT_HORIZONS,
                          random_basket_count: int = DEFAULT_RANDOM_BASKETS,
                          benchmark_min_coverage: float = DEFAULT_BENCHMARK_MIN_COVERAGE,
                          exact_enumeration_limit: int = DEFAULT_EXACT_ENUMERATION_LIMIT) -> list[dict[str, Any]]:
    evaluated_at = _dt(evaluated_at)
    horizons = tuple(dict.fromkeys(int(h) for h in horizons))
    if any(h <= 0 for h in horizons):
        raise ValueError("score horizons must be positive")
    items = _stage_aware_items(run)
    item_ids = tuple(row["instrument_id"] for row in items)
    labels = _latest_labels(outcomes, evaluated_at)
    by_key = {(row["instrument_id"], int(row["horizon"])): row for row in labels}
    outcome_identity_rows = [{
        "observation_id": row.get("observation_id"),
        "logical_label_id": row.get("logical_label_id"),
        "run_id": row.get("run_id"),
        "instrument_id": row.get("instrument_id"),
        "horizon": row.get("horizon"),
        "status": row.get("status"),
        "return": (row.get("metrics") or {}).get("close_reference_change"),
    } for row in labels]
    outcome_identity_rows.sort(key=lambda row: (
        str(row.get("run_id") or ""),
        str(row.get("instrument_id") or ""),
        int(row.get("horizon") or 0),
        str(row.get("observation_id") or ""),
    ))
    outcome_revision_identity = digest({
        "evaluated_at": evaluated_at.isoformat(),
        "labels": outcome_identity_rows,
    })
    records = []
    evidence_type = evidence_type_for_run(run)
    for horizon in horizons:
        eligible_labels = [by_key.get((iid, horizon)) for iid in item_ids]
        eligible_returns = [_label_return(row) for row in eligible_labels]
        benchmark_returns = [value for value in eligible_returns if value is not None]
        benchmark_n = len(item_ids)
        benchmark_valid_n = len(benchmark_returns)
        benchmark_coverage = _coverage(benchmark_valid_n, benchmark_n)
        market_return = _mean(benchmark_returns)
        for cohort in _cohorts(items):
            selected_labels = [by_key.get((iid, horizon)) for iid in cohort.ids]
            selected_returns = [_label_return(row) for row in selected_labels]
            selected_returns = [value for value in selected_returns if value is not None]
            m = len(cohort.ids)
            valid_m = len(selected_returns)
            selected_coverage = _coverage(valid_m, m)
            score_status = _status_for_selected(selected_labels, selected_returns, m)
            baskets: list[float] = []
            sampling = {
                "sampling_mode": None,
                "random_basket_count": random_basket_count,
                "total_combination_count": None,
                "sample_member_count": m,
                "replacement": "WITHOUT_REPLACEMENT",
                "seed": None,
            }
            seed_identity = {
                "score_spec_id": SCORE_SPEC_ID,
                "signal_run_id": run.get("run_id"),
                "selection_tier": cohort.selection_tier,
                "setup_id": cohort.setup_id,
                "horizon": horizon,
                "outcome_revision_identity": outcome_revision_identity,
                "benchmark_policy_id": BENCHMARK_POLICY_ID,
                "sampling_policy_id": SAMPLING_POLICY_ID,
            }
            observed = _mean(selected_returns)
            score = None
            if score_status is None and (benchmark_coverage is None or benchmark_coverage < benchmark_min_coverage):
                score_status = "INSUFFICIENT_BENCHMARK_COVERAGE"
            if score_status is None and len(benchmark_returns) < m:
                score_status = "INSUFFICIENT_BENCHMARK_POPULATION"
            if score_status is None and observed is not None:
                baskets, sampling = _basket_returns(
                    benchmark_returns,
                    m,
                    seed_identity,
                    random_basket_count=random_basket_count,
                    exact_enumeration_limit=exact_enumeration_limit,
                )
                score = _percentile_score(observed, baskets)
                score_status = "COMPLETE"
            best = max(selected_returns) if selected_returns else None
            worst = min(selected_returns) if selected_returns else None
            median_return = median(selected_returns) if selected_returns else None
            score_identity = {
                **seed_identity,
                "benchmark_coverage_min": benchmark_min_coverage,
                "selection_return_policy_id": SELECTION_RETURN_POLICY_ID,
            }
            records.append({
                "score_id": "score_" + digest(score_identity),
                "score_spec_id": SCORE_SPEC_ID,
                "score_implementation_version": SCORE_IMPLEMENTATION_VERSION,
                "strategy_id": STAGE_AWARE_ID,
                "signal_run_id": run.get("run_id"),
                "signal_observation_date": run.get("observation_date"),
                "evidence_type": evidence_type,
                "selection_tier": cohort.selection_tier,
                "setup_id": cohort.setup_id,
                "evidence_role": cohort.evidence_role,
                "official_recommendation_score": cohort.official_recommendation_score and evidence_type == OFFICIAL_PROSPECTIVE,
                "horizon": horizon,
                "horizon_label": f"D+{horizon}",
                "score": _score_value(score),
                "score_status": score_status,
                "equal_weight_return": _metric(observed),
                "eligible_market_return": _metric(market_return),
                "excess_return": _metric(observed - market_return) if observed is not None and market_return is not None else None,
                "median_return": _metric(median_return),
                "hit_rate": _metric(sum(value > 0 for value in selected_returns) / valid_m) if valid_m else None,
                "outperform_market_rate": _metric(sum(value > market_return for value in selected_returns) / valid_m) if valid_m and market_return is not None else None,
                "best_return": _metric(best),
                "worst_return": _metric(worst),
                "M": m,
                "valid_M": valid_m,
                "selected_outcome_coverage": _coverage(valid_m, m),
                "benchmark_N": benchmark_n,
                "benchmark_valid_N": benchmark_valid_n,
                "benchmark_coverage": benchmark_coverage,
                "benchmark_min_coverage": benchmark_min_coverage,
                "random_basket_count": sampling["random_basket_count"],
                "random_seed_identity": seed_identity,
                "random_seed_hash": digest(seed_identity),
                "sampling_policy": sampling,
                "benchmark_policy_id": BENCHMARK_POLICY_ID,
                "selection_return_policy_id": SELECTION_RETURN_POLICY_ID,
                "outcome_revision_identity": outcome_revision_identity,
                "source_outcome_observation_ids": sorted(row.get("observation_id") for row in selected_labels if row),
                "market_edge": "NOT_ESTABLISHED",
                "strategy_adjustment_policy": "SCORE_IS_HEALTH_EVIDENCE_NOT_OPTIMIZATION_LOSS",
            })
    return records


def build_score_revision(run: dict[str, Any], outcomes: list[dict[str, Any]], evaluated_at: datetime | str, *,
                         existing_revisions: list[dict[str, Any]] | None = None,
                         horizons: tuple[int, ...] = DEFAULT_HORIZONS,
                         random_basket_count: int = DEFAULT_RANDOM_BASKETS,
                         benchmark_min_coverage: float = DEFAULT_BENCHMARK_MIN_COVERAGE,
                         exact_enumeration_limit: int = DEFAULT_EXACT_ENUMERATION_LIMIT) -> dict[str, Any]:
    evaluated_at = _dt(evaluated_at)
    records = compute_score_records(
        run,
        outcomes,
        evaluated_at,
        horizons=horizons,
        random_basket_count=random_basket_count,
        benchmark_min_coverage=benchmark_min_coverage,
        exact_enumeration_limit=exact_enumeration_limit,
    )
    outcome_revision_identity = digest(sorted({row["outcome_revision_identity"] for row in records}))
    score_run_identity = {
        "score_spec_id": SCORE_SPEC_ID,
        "strategy_id": STAGE_AWARE_ID,
        "signal_run_id": run.get("run_id"),
        "outcome_revision_identity": outcome_revision_identity,
        "benchmark_policy_id": BENCHMARK_POLICY_ID,
        "sampling_policy_id": SAMPLING_POLICY_ID,
        "horizons": list(horizons),
    }
    score_run_id = "recommendation_score_" + digest(score_run_identity)
    existing_revisions = list(existing_revisions or [])
    for existing in existing_revisions:
        if existing.get("score_run_id") == score_run_id:
            return existing
    group_id = "recommendation_score_group_" + digest({
        "score_spec_id": SCORE_SPEC_ID,
        "strategy_id": STAGE_AWARE_ID,
        "signal_run_id": run.get("run_id"),
    })
    existing_group = [row for row in existing_revisions if row.get("score_revision_group_id") == group_id]
    headline = next((row for row in records if row["selection_tier"] == FOCUS_SCORE and row["horizon"] == 1), None)
    value = {
        "schema_version": "R11_DAILY_RECOMMENDATION_SCORE_REVISION_V1",
        "score_run_id": score_run_id,
        "score_revision_group_id": group_id,
        "revision": len(existing_group) + 1,
        "generated_at": evaluated_at.isoformat(),
        "maturity_status": "COMPLETE" if headline and headline["score_status"] == "COMPLETE" else (headline or {}).get("score_status", "UNKNOWN"),
        "score_spec_id": SCORE_SPEC_ID,
        "score_implementation_version": SCORE_IMPLEMENTATION_VERSION,
        "strategy_id": STAGE_AWARE_ID,
        "signal_run_id": run.get("run_id"),
        "signal_observation_date": run.get("observation_date"),
        "decision_cutoff": run.get("decision_cutoff") or run.get("cutoff"),
        "evidence_type": evidence_type_for_run(run),
        "official_primary_signal_countable": ((run.get("prospective_validation") or {}).get("official_primary_signal_countable") is True),
        "headline_horizon": 1,
        "headline_score": (headline or {}).get("score"),
        "headline_status": (headline or {}).get("score_status"),
        "headline": headline,
        "records": records,
        "horizons": list(horizons),
        "outcome_revision_identity": outcome_revision_identity,
        "source_outcome_ids_hash": digest(sorted({oid for row in records for oid in row.get("source_outcome_observation_ids", [])})),
        "benchmark_policy_id": BENCHMARK_POLICY_ID,
        "sampling_policy_id": SAMPLING_POLICY_ID,
        "selection_return_policy_id": SELECTION_RETURN_POLICY_ID,
        "rolling_policy_id": ROLLING_POLICY_ID,
        "strategy_adjustment_governance": {
            "score_role": "STRATEGY_HEALTH_EVIDENCE",
            "not_strategy_feature": True,
            "not_optimization_objective": True,
            "automatic_tuning_forbidden": True,
        },
        "market_edge": "NOT_ESTABLISHED",
    }
    return value


def flatten_score_documents(documents: list[dict[str, Any]], *, tier: str | None = None,
                            setup: str | None = None, horizon: int | None = None,
                            evidence_type: str | None = None) -> list[dict[str, Any]]:
    rows = []
    for document in documents:
        if evidence_type is not None and document.get("evidence_type") != evidence_type:
            continue
        for row in document.get("records") or []:
            if tier is not None and row.get("selection_tier") != tier:
                continue
            if setup is not None and row.get("setup_id") != setup:
                continue
            if horizon is not None and int(row.get("horizon")) != int(horizon):
                continue
            rows.append({**row, "score_run_id": document.get("score_run_id"), "revision": document.get("revision"),
                         "generated_at": document.get("generated_at")})
    return rows


def summarize_score_history(documents: list[dict[str, Any]], *, evidence_type: str = OFFICIAL_PROSPECTIVE) -> dict[str, Any]:
    scoped = [row for row in documents if row.get("evidence_type") == evidence_type]
    by_day: dict[str, dict[str, Any]] = {}
    for document in sorted(scoped, key=lambda row: (row.get("signal_observation_date") or "", row.get("revision") or 0, row.get("generated_at") or "")):
        day = document.get("signal_observation_date")
        if day:
            by_day[day] = document
    daily = []
    for day, document in by_day.items():
        focus = next((row for row in document.get("records", []) if row["selection_tier"] == FOCUS_SCORE and row["horizon"] == 1), None)
        watch = next((row for row in document.get("records", []) if row["selection_tier"] == WATCH_RESEARCH_SCORE and row["horizon"] == 1), None)
        daily.append({
            "signal_observation_date": day,
            "score_run_id": document.get("score_run_id"),
            "focus_score": focus.get("score") if focus else None,
            "focus_status": focus.get("score_status") if focus else None,
            "watch_score": watch.get("score") if watch else None,
            "watch_status": watch.get("score_status") if watch else None,
            "focus_M": focus.get("M") if focus else 0,
            "focus_valid_M": focus.get("valid_M") if focus else 0,
            "benchmark_coverage": focus.get("benchmark_coverage") if focus else None,
            "focus_excess_return": focus.get("excess_return") if focus else None,
            "watch_excess_return": watch.get("excess_return") if watch else None,
            "focus_watch_score_gap": focus.get("score") - watch.get("score") if focus and watch and focus.get("score") is not None and watch.get("score") is not None else None,
            "focus_watch_excess_gap": focus.get("excess_return") - watch.get("excess_return") if focus and watch and focus.get("excess_return") is not None and watch.get("excess_return") is not None else None,
        })
    valid_focus = [row for row in daily if row["focus_score"] is not None]
    valid_excess = [row for row in daily if row["focus_excess_return"] is not None]
    focus_nonempty_days = sum((row.get("focus_M") or 0) > 0 for row in daily)
    m_values = [row.get("focus_M") or 0 for row in daily]

    def rolling(length: int) -> dict[str, Any]:
        selected_scores = valid_focus[-length:]
        selected_excess = valid_excess[-length:]
        return {
            "window_signal_days": length,
            "valid_score_days": len(selected_scores),
            "mean_score": _score_value(_mean([row["focus_score"] for row in selected_scores])) if selected_scores else None,
            "mean_excess_return": _metric(_mean([row["focus_excess_return"] for row in selected_excess])) if selected_excess else None,
            "median_daily_return": _metric(median([row["focus_excess_return"] for row in selected_excess])) if selected_excess else None,
            "policy": ROLLING_POLICY_ID,
        }

    return {
        "schema_version": "R11_RECOMMENDATION_SCORE_HISTORY_SUMMARY_V1",
        "score_spec_id": SCORE_SPEC_ID,
        "evidence_type": evidence_type,
        "official_signal_days": len(daily),
        "focus_nonempty_days": focus_nonempty_days,
        "focus_signal_coverage": focus_nonempty_days / len(daily) if daily else None,
        "mean_M": _metric(_mean(m_values)) if m_values else None,
        "median_M": median(m_values) if m_values else None,
        "rolling": {"20D": rolling(20), "60D": rolling(60)},
        "daily": daily,
        "market_edge": "NOT_ESTABLISHED",
    }
