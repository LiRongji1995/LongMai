"""Append-only outcome and research-cycle records for R05 experiments."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from .contracts import MarketInput, digest
from .events import TRUE, limit_event
from .visibility import EvidenceResolver

SH = ZoneInfo("Asia/Shanghai")
DEFAULT_RESEARCH_HORIZONS = (1, 2, 3, 4, 5, 10, 20)
POST_EXIT_HORIZONS = (5, 10)
OUTCOME_VERSION = "R05_STRATEGY_OUTCOME_OBSERVATION_V1"
TRACKING_VERSION = "R05_RESEARCH_EPISODE_TRACKING_V1"
REVIEW_VERSION = "R05_RESEARCH_FEEDBACK_V1"


def _day(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except Exception:
        return None
    return result if result.is_finite() else None


def _num(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value.quantize(Decimal("0.000001")))


def _sessions(bundle: MarketInput) -> list[date]:
    return [_day(session) for session in bundle.sessions]


def _visible_bars(bundle: MarketInput, evaluated_at: datetime) -> dict[tuple[str, date], Any]:
    resolver = EvidenceResolver(bundle.receipts, cutoff=evaluated_at, coverage_type=bundle.coverage_type, sessions=bundle.sessions)
    bars: dict[tuple[str, date], Any] = {}
    for bar in sorted(bundle.bars, key=lambda item: item.ingested_at):
        day = _day(bar.session)
        if not resolver.is_visible(bar, instrument_id=bar.instrument_id, session=day, require_session=True):
            continue
        bars[(bar.instrument_id, day)] = bar
    return bars


def _strategy_items(run: dict[str, Any], strategy_id: str = "PV_OBSERVATION_V1") -> list[dict[str, Any]]:
    return list(((run.get("results") or {}).get(strategy_id) or {}).get("items") or [])


def _focus_ids(run: dict[str, Any]) -> set[str]:
    focus = (run.get("selection_results") or {}).get("PV_FOCUS_PREVIEW_V0.1") or {}
    return set(focus.get("selected_ids") or [])


def _reference_close(item: dict[str, Any]) -> Decimal | None:
    return _dec(item.get("comparable_close")) or _dec(item.get("close"))


def evaluate_strategy_experiment_outcomes(
    run: dict[str, Any],
    bundle: MarketInput,
    evaluated_at: datetime,
    *,
    horizons: tuple[int, ...] = DEFAULT_RESEARCH_HORIZONS,
) -> list[dict[str, Any]]:
    if evaluated_at.tzinfo is None:
        raise ValueError("evaluated_at requires timezone")
    if any(h <= 0 or h > 60 for h in horizons):
        raise ValueError("unsupported research horizon")
    observation_date = _day(run["observation_date"])
    calendar = _sessions(bundle)
    if observation_date not in calendar:
        raise ValueError("experiment observation date missing from outcome calendar")
    future = calendar[calendar.index(observation_date) + 1:]
    bars = _visible_bars(bundle, evaluated_at)
    focus_ids = _focus_ids(run)
    values: list[dict[str, Any]] = []
    for item in _strategy_items(run):
        iid = item["instrument_id"]
        reference_close = _reference_close(item)
        reference_basis = item.get("comparable_basis")
        reference_missing = reference_close is None or reference_close <= 0
        for horizon in tuple(dict.fromkeys(horizons)):
            days = future[:horizon]
            end = days[-1] if len(days) == horizon else None
            mature = end is not None and datetime.combine(end, time(15), SH) <= evaluated_at.astimezone(SH)
            states: list[dict[str, Any]] = []
            reasons: list[str] = []
            observed = []
            for day in days:
                bar = bars.get((iid, day))
                if datetime.combine(day, time(15), SH) > evaluated_at.astimezone(SH):
                    states.append({"session": day.isoformat(), "status": "NOT_MATURED", "close_limit_up": None})
                    reasons.append("NOT_MATURED")
                    continue
                if bar is None or bar.close is None or bar.quality != "VALID":
                    states.append({"session": day.isoformat(), "status": "MISSING_PRICE", "close_limit_up": None})
                    reasons.append("MISSING_PRICE")
                    continue
                event = limit_event(bar)
                if reference_basis and bar.comparable_basis != reference_basis:
                    status = "COMPANY_ACTION_OR_COMPARABILITY_UNSUPPORTED"
                    reasons.append(status)
                elif event.close_limit_up not in {TRUE, "FALSE"}:
                    status = "LIMIT_EVENT_UNVERIFIED"
                    reasons.append(status)
                else:
                    status = "OBSERVED"
                    observed.append(bar)
                states.append(
                    {
                        "session": day.isoformat(),
                        "status": status,
                        "close": str(bar.close) if bar.close is not None else None,
                        "comparable_close": str(bar.comparable_close) if bar.comparable_close is not None else None,
                        "close_limit_up": True if event.close_limit_up == TRUE else False if event.close_limit_up == "FALSE" else None,
                        "close_limit_down": True if event.close_limit_down == TRUE else False if event.close_limit_down == "FALSE" else None,
                    }
                )
            metrics = {
                "close_reference_change": None,
                "next_open_gap": None,
                "mfe_bound": None,
                "mae_bound": None,
                "any_close_limit_up": None,
                "any_close_limit_down": None,
            }
            if reference_missing:
                reasons.append("REFERENCE_CLOSE_UNAVAILABLE")
            if mature and not reference_missing and len(observed) == horizon:
                last = observed[-1]
                first = observed[0]
                if last.comparable_close is not None:
                    metrics["close_reference_change"] = _num(last.comparable_close / reference_close - Decimal("1"))
                if first.comparable_open is not None:
                    metrics["next_open_gap"] = _num(first.comparable_open / reference_close - Decimal("1"))
                if all(bar.comparable_high is not None and bar.comparable_low is not None for bar in observed):
                    metrics["mfe_bound"] = _num(max(bar.comparable_high for bar in observed) / reference_close - Decimal("1"))
                    metrics["mae_bound"] = _num(min(bar.comparable_low for bar in observed) / reference_close - Decimal("1"))
            if mature and states:
                ups = [row["close_limit_up"] for row in states]
                downs = [row["close_limit_down"] for row in states]
                metrics["any_close_limit_up"] = True if True in ups else False if all(v is False for v in ups) else None
                metrics["any_close_limit_down"] = True if True in downs else False if all(v is False for v in downs) else None
            status = "NOT_MATURED" if not mature else "MATURE" if not reasons else "CENSORED"
            if mature and states and all(row["status"] == "MISSING_PRICE" for row in states):
                status = "UNAVAILABLE"
            value = {
                "schema_version": OUTCOME_VERSION,
                "run_id": run["run_id"],
                "input_id": getattr(bundle, "input_id", None) or run.get("input_id"),
                "strategy_id": "PV_OBSERVATION_V1",
                "selection_policy_id": run.get("results", {}).get("PV_OBSERVATION_V1", {}).get("selection_policy_id"),
                "instrument_id": iid,
                "selected_focus": iid in focus_ids,
                "parent_rank": item.get("rank"),
                "parent_score": item.get("S_PV"),
                "horizon": horizon,
                "horizon_unit": "exchange_trading_sessions",
                "observation_date": observation_date.isoformat(),
                "label_end": end.isoformat() if end else None,
                "evaluated_at": evaluated_at.isoformat(),
                "coverage_type": bundle.coverage_type,
                "status": status,
                "metrics": metrics,
                "session_states": states,
                "reason_codes": sorted(set(reasons)),
                "input_hash": digest(bundle),
                "market_edge": "NOT_ESTABLISHED",
                "reference_method": "COMPARABLE_MARKED_PRICES_NOT_ASSUMED_FILLS",
            }
            value["observation_id"] = "strategy_outcome_" + digest(value)
            values.append(value)
    return values


def build_strategy_tracking(
    run: dict[str, Any],
    bundle: MarketInput,
    evaluated_at: datetime,
    *,
    horizons: tuple[int, ...] = DEFAULT_RESEARCH_HORIZONS,
) -> dict[str, Any]:
    observation_date = _day(run["observation_date"])
    calendar = _sessions(bundle)
    bars = _visible_bars(bundle, evaluated_at)
    focus_ids = _focus_ids(run)
    items = [item for item in _strategy_items(run) if item["instrument_id"] in focus_ids]
    episodes: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    for item in items:
        iid = item["instrument_id"]
        episode_payload = {
            "schema_version": TRACKING_VERSION,
            "run_id": run["run_id"],
            "instrument_id": iid,
            "strategy_id": "PV_OBSERVATION_V1",
            "selection_policy_id": run.get("results", {}).get("PV_OBSERVATION_V1", {}).get("selection_policy_id"),
            "tracking_policy_id": "R05_FOCUS_VARIABLE_RESEARCH_CYCLE_V1",
            "observation_date": observation_date.isoformat(),
            "pending_horizons": list(horizons),
            "post_exit_horizons": list(POST_EXIT_HORIZONS),
            "started_at": evaluated_at.isoformat(),
            "market_edge": "NOT_ESTABLISHED",
        }
        episode_id = "research_episode_" + digest(episode_payload)
        episode_payload["episode_id"] = episode_id
        episodes.append(episode_payload)
        reference_close = _reference_close(item)
        seen_sessions = [day for day in calendar if day >= observation_date and datetime.combine(day, time(15), SH) <= evaluated_at.astimezone(SH)]
        close_series: list[Decimal] = []
        volume_peak: Decimal | None = None
        last_three: list[dict[str, Any]] = []
        below_ma20_streak = 0
        state = "ACTIVE"
        reasons: list[str] = []
        for day in seen_sessions:
            bar = bars.get((iid, day))
            if bar is None or bar.close is None or bar.quality != "VALID":
                record = {"session": day.isoformat(), "status": "MISSING_PRICE", "state": state}
                observations.append({"episode_id": episode_id, "run_id": run["run_id"], "instrument_id": iid, **record})
                continue
            close = bar.comparable_close or bar.close
            volume = bar.volume_shares
            close_series.append(close)
            volume_peak = volume if volume is not None and (volume_peak is None or volume > volume_peak) else volume_peak
            volume_ratio = volume / volume_peak if volume is not None and volume_peak and volume_peak > 0 else None
            r5 = None
            if len(close_series) >= 6:
                base = close_series[-6]
                if base > 0:
                    r5 = close / base - Decimal("1")
            ma20 = sum(close_series[-20:], Decimal("0")) / Decimal(20) if len(close_series) >= 20 else None
            if ma20 is not None and close < ma20:
                below_ma20_streak += 1
            else:
                below_ma20_streak = 0
            if below_ma20_streak >= 2:
                state = "THESIS_INVALIDATED"
                reasons.append("TWO_CONSECUTIVE_CLOSES_BELOW_OWN_MA20")
            last_three.append({"volume_ratio": volume_ratio, "r5": r5})
            last_three = last_three[-3:]
            if (
                state == "ACTIVE"
                and len(last_three) == 3
                and all(row["volume_ratio"] is not None and row["volume_ratio"] <= Decimal("0.30") for row in last_three)
                and all(row["r5"] is not None and row["r5"] <= 0 for row in last_three)
            ):
                state = "COOLING"
                reasons.append("THREE_SESSION_VOLUME_SHRINK_AND_R5_NON_POSITIVE")
            observations.append(
                {
                    "episode_id": episode_id,
                    "run_id": run["run_id"],
                    "instrument_id": iid,
                    "session": day.isoformat(),
                    "status": "OBSERVED",
                    "state": state,
                    "close": str(bar.close),
                    "comparable_close": str(close),
                    "reference_change": _num(close / reference_close - Decimal("1")) if reference_close and reference_close > 0 else None,
                    "volume_ratio_since_signal_peak": _num(volume_ratio),
                    "r5": _num(r5),
                    "ma20": _num(ma20),
                    "reason_codes": sorted(set(reasons)),
                }
            )
    tracking = {
        "tracking_run_id": "strategy_tracking_" + digest(
            {"run_id": run["run_id"], "input_hash": digest(bundle), "evaluated_at": evaluated_at.isoformat(), "horizons": horizons}
        ),
        "schema_version": TRACKING_VERSION,
        "run_id": run["run_id"],
        "input_id": run.get("input_id"),
        "evaluated_at": evaluated_at.isoformat(),
        "observation_date": observation_date.isoformat(),
        "episode_count": len(episodes),
        "observation_count": len(observations),
        "episodes": episodes,
        "observations": observations,
        "horizons": list(horizons),
        "market_edge": "NOT_ESTABLISHED",
        "status": "TRACKED" if episodes else "NO_FOCUS_SELECTIONS",
    }
    return tracking


def build_strategy_feedback_report(
    run: dict[str, Any],
    outcomes: list[dict[str, Any]],
    tracking_runs: list[dict[str, Any]],
    evaluated_at: datetime,
) -> dict[str, Any]:
    matured = [row for row in outcomes if row.get("status") == "MATURE"]
    censored = [row for row in outcomes if row.get("status") in {"CENSORED", "UNAVAILABLE"}]
    pending = [row for row in outcomes if row.get("status") == "NOT_MATURED"]
    by_horizon: dict[str, dict[str, int]] = {}
    for row in outcomes:
        key = str(row.get("horizon"))
        bucket = by_horizon.setdefault(key, {"MATURE": 0, "CENSORED": 0, "UNAVAILABLE": 0, "NOT_MATURED": 0})
        bucket[row.get("status", "UNAVAILABLE")] = bucket.get(row.get("status", "UNAVAILABLE"), 0) + 1
    latest_tracking = tracking_runs[-1] if tracking_runs else None
    value = {
        "review_id": "strategy_review_" + digest(
            {"run_id": run["run_id"], "evaluated_at": evaluated_at.isoformat(), "outcomes": len(outcomes), "tracking": len(tracking_runs)}
        ),
        "schema_version": REVIEW_VERSION,
        "run_id": run["run_id"],
        "observation_date": run.get("observation_date"),
        "evaluated_at": evaluated_at.isoformat(),
        "status": "PENDING_MARKET_VALIDATION" if not matured else "HAS_MATURED_OBSERVATIONS",
        "market_edge": "NOT_ESTABLISHED",
        "counts": {
            "outcome_observations": len(outcomes),
            "mature": len(matured),
            "censored_or_unavailable": len(censored),
            "not_matured": len(pending),
            "focus_episode_count": latest_tracking.get("episode_count") if latest_tracking else 0,
        },
        "by_horizon": by_horizon,
        "latest_tracking_run_id": latest_tracking.get("tracking_run_id") if latest_tracking else None,
        "interpretation": {
            "market_validation": "NOT_ESTABLISHED",
            "daily_review": "Future or missing observations are excluded from mature denominators.",
            "periodic_review": "Use matured, uncensored horizons only; this report does not claim edge.",
        },
    }
    return value
