"""R05 strategy experiments.

The experiment layer is deliberately read-only: it scores candidates from a
published market input bundle and records enough evidence to replay every rank.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from statistics import median
from typing import Any, Iterable

from .contracts import DailyBar, MarketInput, digest
from .events import TRUE, limit_event

PV_OBSERVATION_ID = "PV_OBSERVATION_V1"
DNA_REVERSAL_ID = "DNA_REVERSAL_V1"
THEME_LIMITUP_ID = "THEME_LIMITUP_V1"
FOCUS_POLICY_ID = "PV_FOCUS_PREVIEW_V0.1"
IMPLEMENTATION_VERSION = "R05_STRATEGY_EXPERIMENTS_V2"

PV_WEIGHTS = {
    "A": Decimal("0.40"),
    "R": Decimal("0.30"),
    "H": Decimal("0.30"),
}
DNA_WEIGHTS = {
    "LB_PREMIUM5": Decimal("0.20"),
    "LB_FIRST_RED": Decimal("0.25"),
    "LB_FIRST_SEAL": Decimal("0.25"),
    "LIMIT_COUNT_250D": Decimal("0.15"),
    "S_PV_REBASED": Decimal("0.15"),
}
THEME_WEIGHTS = {
    "PEER_RET5_MEDIAN": Decimal("0.40"),
    "PEER_UP_FRACTION": Decimal("0.30"),
    "PEER_LIMIT_COUNT": Decimal("0.30"),
}
DNA_HISTORY_SESSIONS = 252
DNA_MATURE_EVENT_SESSIONS = 250
PV_QUOTE_COVERAGE_MIN = Decimal("0.98")
THEME_PEER_COVERAGE_MIN = Decimal("0.95")
FOCUS_POLICY = {
    "policy_id": FOCUS_POLICY_ID,
    "selection_session": "T+1_PREVIEW",
    "rank_window": [1, 30],
    "factor_thresholds": {
        "A_min": "2.0",
        "R_min": "0.06",
        "H_min": "0.035",
        "close_location_min": "0.70",
    },
    "max_count": 30,
    "notes": [
        "Selection is a deterministic preview filter over the full PV experiment, not an investment recommendation.",
        "Rejected rows remain in outcome denominators and exports.",
    ],
}


def _stable_id(prefix: str, payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(data).hexdigest()[:16]}"


def _as_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        result = value
    else:
        try:
            result = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None
    if not result.is_finite():
        return None
    return result


def _decimal_or_zero(value: Any) -> Decimal:
    return _as_decimal(value) or Decimal("0")


def _number(value: Decimal | float | int | None, *, places: int = 6) -> float | None:
    dec = _as_decimal(value)
    if dec is None:
        return None
    quant = Decimal("1").scaleb(-places)
    return float(dec.quantize(quant))


def _pct(value: Decimal | float | int | None) -> float | None:
    return _number(value, places=4)


def percentile_rank_midpoint(value: float, values: Iterable[float]) -> float:
    """Return midpoint percentile rank on a 0-100 scale."""

    result = _percentile_rank_decimal(value, values)
    return float(result) if result is not None else 0.0


def _percentile_rank_decimal(value: Any, values: Iterable[Any]) -> Decimal | None:
    target = _as_decimal(value)
    population = [v for v in (_as_decimal(item) for item in values) if v is not None]
    if target is None or not population:
        return None
    lower = sum(1 for item in population if item < target)
    equal = sum(1 for item in population if item == target)
    return (Decimal(lower) + (Decimal(equal) / Decimal(2))) * Decimal(100) / Decimal(len(population))


def _score_from_decimals(weights: dict[str, Decimal], factors: dict[str, Decimal]) -> Decimal:
    score = Decimal("0")
    for key, weight in weights.items():
        score += weight * factors.get(key, Decimal("0"))
    return score


def _score(weights: dict[str, float], factors: dict[str, float]) -> float:
    decimal_weights = {key: Decimal(str(weight)) for key, weight in weights.items()}
    decimal_factors = {key: Decimal(str(factors.get(key, 0.0))) for key in weights}
    return float(_score_from_decimals(decimal_weights, decimal_factors))


def _parse_bar(bar: DailyBar | dict[str, Any] | None) -> dict[str, Any] | None:
    if bar is None:
        return None
    return bar.model_dump() if hasattr(bar, "model_dump") else dict(bar)


def _bar_decimal(bar: dict[str, Any] | DailyBar | None, field: str) -> Decimal | None:
    parsed = _parse_bar(bar)
    if not parsed:
        return None
    return _as_decimal(parsed.get(field))


def _bar_limit_event(bar: dict[str, Any] | DailyBar | None, iid: str, session: str):
    parsed = _parse_bar(bar)
    if not parsed:
        return None
    modelish = SimpleNamespace(**parsed)
    return limit_event(modelish, iid, date.fromisoformat(_session_text(session)), suspended=False)


def _known_limit_flag(value: Any) -> bool:
    return value in {TRUE, "FALSE"}


@dataclass(frozen=True)
class _ExperimentContext:
    bundle: MarketInput
    observation_date: str
    sessions: list[str]
    eligible_ids: list[str]
    bars_by_session: dict[str, dict[str, dict[str, Any]]]
    universe_complete: bool


def _session_text(value: Any) -> str:
    return str(value)[:10]


def _prepare_context(bundle: MarketInput, cutoff: Any | None = None) -> _ExperimentContext:
    requested_date = _session_text(bundle.observation_date)
    if cutoff is not None:
        cutoff_date = _session_text(getattr(cutoff, "date", lambda: cutoff)())
        requested_date = min(requested_date, cutoff_date)
    sessions = sorted([str(session) for session in bundle.sessions if str(session) <= requested_date])
    observation_date = sessions[-1] if sessions else requested_date
    bars_by_session: dict[str, dict[str, dict[str, Any]]] = {}
    if hasattr(bundle, "bars_by_session"):
        for session, bars in bundle.bars_by_session.items():  # type: ignore[attr-defined]
            parsed: dict[str, dict[str, Any]] = {}
            for iid, bar in bars.items():
                parsed[iid] = _parse_bar(bar) or {}
            bars_by_session[_session_text(session)] = parsed
    else:
        for raw_bar in bundle.bars:
            parsed = _parse_bar(raw_bar) or {}
            session = _session_text(parsed.get("session"))
            bars_by_session.setdefault(session, {})[str(parsed.get("instrument_id"))] = parsed
    instruments = getattr(bundle, "instruments", None)
    if instruments is None:
        instruments = tuple(getattr(bundle.universe, "securities", ()))
    obs_date = __import__("datetime").date.fromisoformat(observation_date)
    eligible_ids: list[str] = []
    for instrument in instruments:
        iid = getattr(instrument, "instrument_id", None)
        if not iid:
            continue
        is_st = getattr(instrument, "is_st", getattr(instrument, "st", False))
        suspended = getattr(instrument, "suspended", False)
        listed_days = getattr(instrument, "listed_days", None)
        listed_on = getattr(instrument, "listed_on", None)
        if listed_days is None and listed_on is not None:
            listed_days = (obs_date - listed_on).days
        if is_st is False and int(listed_days or 0) >= 60 and suspended is False and not getattr(instrument, "delisting_period", False):
            eligible_ids.append(str(iid))
    metadata = getattr(bundle, "metadata", None)
    universe_complete = bool(
        getattr(metadata, "universe_complete", False)
        if metadata is not None
        else getattr(getattr(bundle, "universe", None), "complete", False)
    )
    return _ExperimentContext(
        bundle=bundle,
        observation_date=observation_date,
        sessions=sessions,
        eligible_ids=sorted(eligible_ids),
        bars_by_session=bars_by_session,
        universe_complete=universe_complete,
    )


def _bar(ctx: _ExperimentContext, session: str, iid: str) -> dict[str, Any] | None:
    return ctx.bars_by_session.get(session, {}).get(iid)


def _prior_sessions(ctx: _ExperimentContext, count: int) -> list[str]:
    if len(ctx.sessions) < count:
        return []
    return ctx.sessions[-count:]


def _amount_ratio(current: dict[str, Any], previous: list[dict[str, Any]]) -> Decimal | None:
    current_amount = _as_decimal(current.get("amount_cny"))
    amounts = [_as_decimal(item.get("amount_cny")) for item in previous]
    amounts = [item for item in amounts if item is not None and item > 0]
    if current_amount is None or current_amount <= 0 or len(amounts) != 5:
        return None
    mean_amount = sum(amounts, Decimal("0")) / Decimal(len(amounts))
    if mean_amount <= 0:
        return None
    return current_amount / mean_amount


def _comparable_return(current: dict[str, Any], previous: dict[str, Any]) -> Decimal | None:
    if current.get("comparable_basis") != previous.get("comparable_basis"):
        return None
    current_close = _as_decimal(current.get("comparable_close"))
    previous_close = _as_decimal(previous.get("comparable_close"))
    if current_close is None or previous_close is None or previous_close <= 0:
        return None
    return current_close / previous_close - Decimal("1")


def _high_low_range(current: dict[str, Any]) -> Decimal | None:
    high = _as_decimal(current.get("high"))
    low = _as_decimal(current.get("low"))
    close = _as_decimal(current.get("close"))
    if high is None or low is None or close is None or close <= 0:
        return None
    return (high - low) / close


def _complete_pv_row(ctx: _ExperimentContext, iid: str, window: list[str]) -> tuple[dict[str, Any] | None, str | None]:
    current = _bar(ctx, window[-1], iid)
    previous = [_bar(ctx, session, iid) for session in window[:-1]]
    if current is None or any(item is None for item in previous):
        return None, "PV_6_SESSION_WINDOW_INCOMPLETE"
    basis_values = [current.get("comparable_basis"), *(item.get("comparable_basis") for item in previous)]  # type: ignore[union-attr]
    if any(value != basis_values[0] for value in basis_values):
        return None, "PV_COMPARABLE_BASIS_WINDOW_MISMATCH"
    amount_ratio = _amount_ratio(current, previous)  # type: ignore[arg-type]
    comparable_return = _comparable_return(current, previous[0])  # type: ignore[arg-type]
    high_low = _high_low_range(current)
    if amount_ratio is None:
        return None, "PV_AMOUNT_MEAN5_UNAVAILABLE"
    if comparable_return is None:
        return None, "PV_COMPARABLE_RETURN_T_MINUS_5_UNAVAILABLE"
    if high_low is None:
        return None, "PV_HIGH_LOW_RANGE_UNAVAILABLE"
    mean_amount = sum((_as_decimal(item.get("amount_cny")) or Decimal("0")) for item in previous) / Decimal(5)  # type: ignore[union-attr]
    row = {
        "instrument_id": iid,
        "observation_date": ctx.observation_date,
        "A": _number(amount_ratio),
        "R": _number(comparable_return),
        "H": _number(high_low),
        "open": _number(_as_decimal(current.get("open")), places=4),
        "high": _number(_as_decimal(current.get("high")), places=4),
        "low": _number(_as_decimal(current.get("low")), places=4),
        "close": _number(_as_decimal(current.get("close")), places=4),
        "amount_cny": _number(_as_decimal(current.get("amount_cny")), places=2),
        "mean_prior5_amount_cny": _number(mean_amount, places=2),
        "volume_shares": _number(_as_decimal(current.get("volume_shares")), places=2),
        "comparable_basis": current.get("comparable_basis", "UNSPECIFIED"),
        "comparable_close": _number(_as_decimal(current.get("comparable_close")), places=6),
        "comparable_close_T_minus_5": _number(_as_decimal(previous[0].get("comparable_close")), places=6),  # type: ignore[union-attr]
        "_A": amount_ratio,
        "_R": comparable_return,
        "_H": high_low,
    }
    return row, None


def run_pv_observation(bundle: MarketInput, *, top_n: int = 100, cutoff: Any | None = None) -> dict[str, Any]:
    ctx = _prepare_context(bundle, cutoff=cutoff)
    window = _prior_sessions(ctx, 6)
    incomplete: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    if len(window) < 6:
        return {
            "strategy_id": PV_OBSERVATION_ID,
            "implementation_version": IMPLEMENTATION_VERSION,
            "parameter_status": "INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED",
            "status": "UNAVAILABLE",
            "scope_label": "LIMITED_SCOPE",
            "reason": "PV_6_EXCHANGE_SESSIONS_REQUIRED",
            "observation_date": ctx.observation_date,
            "counts": {
                "eligible": len(ctx.eligible_ids),
                "evaluated": 0,
                "missing": len(ctx.eligible_ids),
                "field_coverage": 0.0,
                "quote_coverage_min": float(PV_QUOTE_COVERAGE_MIN),
                "universe_complete": ctx.universe_complete,
            },
            "items": [],
            "top_ids": [],
            "incomplete": [{"instrument_id": iid, "reason": "PV_6_EXCHANGE_SESSIONS_REQUIRED"} for iid in ctx.eligible_ids],
            "missing": [{"instrument_id": iid, "reason": "PV_6_EXCHANGE_SESSIONS_REQUIRED"} for iid in ctx.eligible_ids],
        }

    for iid in ctx.eligible_ids:
        row, reason = _complete_pv_row(ctx, iid, window)
        if row is None:
            incomplete.append({"instrument_id": iid, "reason": reason})
            continue
        rows.append(row)

    amount_values = [row["_A"] for row in rows]
    return_values = [row["_R"] for row in rows]
    range_values = [row["_H"] for row in rows]
    for row in rows:
        pr_a = _percentile_rank_decimal(row["_A"], amount_values) or Decimal("0")
        pr_r = _percentile_rank_decimal(row["_R"], return_values) or Decimal("0")
        pr_h = _percentile_rank_decimal(row["_H"], range_values) or Decimal("0")
        score = _score_from_decimals(PV_WEIGHTS, {"A": pr_a, "R": pr_r, "H": pr_h})
        row["rank_percentiles"] = {"A": _pct(pr_a), "R": _pct(pr_r), "H": _pct(pr_h)}
        row["S_PV"] = _number(score, places=6)
        row["_S_PV"] = score

    ranked = sorted(rows, key=lambda row: (-row["_S_PV"], row["instrument_id"]))
    for rank, row in enumerate(ranked, 1):
        row["rank"] = rank

    eligible_count = len(ctx.eligible_ids)
    coverage = (Decimal(len(ranked)) / Decimal(eligible_count)) if eligible_count else Decimal("0")
    scope_label = "FULL_SCOPE" if coverage >= PV_QUOTE_COVERAGE_MIN and ctx.universe_complete else "LIMITED_SCOPE"
    status = "COMPLETE" if ranked and scope_label == "FULL_SCOPE" else "LIMITED_SCOPE" if ranked else "UNAVAILABLE"
    public_rows = [
        {key: value for key, value in row.items() if not key.startswith("_")}
        for row in ranked
    ]
    return {
        "strategy_id": PV_OBSERVATION_ID,
        "implementation_version": IMPLEMENTATION_VERSION,
        "parameter_status": "INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED",
        "status": status,
        "scope_label": scope_label,
        "observation_date": ctx.observation_date,
        "formula": "0.40*PR_mid(A) + 0.30*PR_mid(R) + 0.30*PR_mid(H)",
        "tie_break": "S_PV desc, instrument_id asc",
        "counts": {
            "eligible": eligible_count,
            "evaluated": len(public_rows),
            "filter_passed": len(public_rows),
            "missing": len(incomplete),
            "field_coverage": _pct(coverage),
            "quote_coverage_min": float(PV_QUOTE_COVERAGE_MIN),
            "universe_complete": ctx.universe_complete,
        },
        "items": public_rows,
        "top_ids": [row["instrument_id"] for row in public_rows[:top_n]],
        "incomplete": sorted(incomplete, key=lambda row: row["instrument_id"]),
        "missing": sorted(incomplete, key=lambda row: row["instrument_id"]),
    }


def _close_location(item: dict[str, Any]) -> Decimal | None:
    close = _as_decimal(item.get("close"))
    high = _as_decimal(item.get("high"))
    low = _as_decimal(item.get("low"))
    if close is None or high is None or low is None:
        return None
    span = high - low
    if span <= 0:
        return None
    return (close - low) / span


def run_pv_focus_preview(pv_result: dict[str, Any], *, max_count: int = 30) -> dict[str, Any]:
    selected: list[dict[str, Any]] = []
    all_items: list[dict[str, Any]] = []
    thresholds = FOCUS_POLICY["factor_thresholds"]
    a_min = Decimal(str(thresholds["A_min"]))
    r_min = Decimal(str(thresholds["R_min"]))
    h_min = Decimal(str(thresholds["H_min"]))
    close_location_min = Decimal(str(thresholds["close_location_min"]))
    for item in pv_result.get("items", []):
        reasons: list[str] = []
        rank = int(item.get("rank") or 0)
        a = _decimal_or_zero(item.get("A"))
        r = _decimal_or_zero(item.get("R"))
        h = _decimal_or_zero(item.get("H"))
        close_location = _close_location(item)
        if rank < 1 or rank > 30:
            reasons.append("OUTSIDE_PV_RANK_WINDOW")
        if a < a_min:
            reasons.append("A_BELOW_2_0")
        if r < r_min:
            reasons.append("R_BELOW_0_06")
        if h < h_min:
            reasons.append("H_BELOW_0_035")
        if close_location is None:
            reasons.append("CLOSE_LOCATION_UNAVAILABLE_OR_ZERO_RANGE")
        elif close_location < close_location_min:
            reasons.append("CLOSE_LOCATION_BELOW_0_70")
        public = {
            "instrument_id": item["instrument_id"],
            "observation_date": item.get("observation_date"),
            "parent_rank": rank,
            "parent_score": item.get("S_PV"),
            "A": item.get("A"),
            "R": item.get("R"),
            "H": item.get("H"),
            "close_location": _number(close_location) if close_location is not None else None,
            "selected": not reasons,
            "reason_codes": reasons or ["SELECTED_BY_POLICY"],
        }
        all_items.append(public)
        if public["selected"]:
            selected.append(public)
    selected = sorted(selected, key=lambda row: (row["parent_rank"], row["instrument_id"]))[:max_count]
    selected_ids = {row["instrument_id"] for row in selected}
    for row in all_items:
        if row["instrument_id"] not in selected_ids and row["selected"]:
            row["selected"] = False
            row["reason_codes"] = ["FOCUS_MAX_COUNT_TRUNCATED"]
    selected = [row for row in all_items if row["selected"]]
    policy_hash = hashlib.sha256(json.dumps(FOCUS_POLICY, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return {
        "strategy_id": FOCUS_POLICY_ID,
        "implementation_version": IMPLEMENTATION_VERSION,
        "status": "COMPLETE" if pv_result.get("items") else "UNAVAILABLE",
        "selection_policy_id": FOCUS_POLICY_ID,
        "selection_policy": FOCUS_POLICY,
        "selection_policy_hash": policy_hash,
        "parent_strategy_id": PV_OBSERVATION_ID,
        "observation_date": pv_result.get("observation_date"),
        "counts": {
            "parent_evaluated": len(pv_result.get("items", [])),
            "selected": len(selected),
            "rejected": len(all_items) - len(selected),
            "max_count": max_count,
        },
        "selected_ids": [row["instrument_id"] for row in selected],
        "selected": selected,
        "items": all_items,
    }


def _wilson_lower_bound(successes: int, n: int, *, z: float = 1.959963984540054) -> float:
    if n <= 0:
        return 0.0
    p = successes / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return max(0.0, (centre - margin) / denom)


def _pv_population_scores(pv_items: dict[str, dict[str, Any]], ids: Iterable[str], output_field: str) -> dict[str, Decimal]:
    ids = [iid for iid in ids if iid in pv_items]
    if not ids:
        return {}
    values_a = [_decimal_or_zero(pv_items[iid].get("A")) for iid in ids]
    values_r = [_decimal_or_zero(pv_items[iid].get("R")) for iid in ids]
    values_h = [_decimal_or_zero(pv_items[iid].get("H")) for iid in ids]
    scores: dict[str, Decimal] = {}
    for iid in ids:
        item = pv_items[iid]
        pr_a = _percentile_rank_decimal(item.get("A"), values_a) or Decimal("0")
        pr_r = _percentile_rank_decimal(item.get("R"), values_r) or Decimal("0")
        pr_h = _percentile_rank_decimal(item.get("H"), values_h) or Decimal("0")
        scores[iid] = _score_from_decimals(PV_WEIGHTS, {"A": pr_a, "R": pr_r, "H": pr_h})
    return scores


def run_dna_reversal(bundle: MarketInput, pv_result: dict[str, Any] | None = None, *, top_n: int = 100, cutoff: Any | None = None) -> dict[str, Any]:
    ctx = _prepare_context(bundle, cutoff=cutoff)
    pv_result = pv_result or run_pv_observation(bundle, top_n=top_n, cutoff=cutoff)
    pv_items = {item["instrument_id"]: item for item in pv_result.get("items", [])}
    entrant_ids = [iid for iid in ctx.eligible_ids if iid in pv_items]
    if len(ctx.sessions) < DNA_HISTORY_SESSIONS:
        return {
            "strategy_id": DNA_REVERSAL_ID,
            "implementation_version": IMPLEMENTATION_VERSION,
            "base_strategy_id": PV_OBSERVATION_ID,
            "parameter_status": "INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED",
            "status": "INSUFFICIENT_COVERAGE",
            "reason": "DNA_252_SESSION_HISTORY_REQUIRED",
            "history_sessions_required": DNA_HISTORY_SESSIONS,
            "history_sessions_available": len(ctx.sessions),
            "observation_date": ctx.observation_date,
            "counts": {"eligible": len(entrant_ids), "evaluated": 0, "insufficient": len(entrant_ids)},
            "items": [
                {"instrument_id": iid, "status": "INSUFFICIENT_COVERAGE", "reason": "DNA_252_SESSION_HISTORY_REQUIRED"}
                for iid in entrant_ids
            ],
            "top_ids": [],
        }

    history = ctx.sessions[-DNA_HISTORY_SESSIONS:]
    mature_sessions = history[-(DNA_MATURE_EVENT_SESSIONS + 1):-1]
    session_index = {session: idx for idx, session in enumerate(ctx.sessions)}
    raw_rows: list[dict[str, Any]] = []
    insufficient: list[dict[str, Any]] = []
    all_open_premiums: list[Decimal] = []
    for iid in entrant_ids:
        if any(_bar(ctx, session, iid) is None for session in history):
            insufficient.append({"instrument_id": iid, "status": "INSUFFICIENT_COVERAGE", "reason": "DNA_HISTORY_BARS_INCOMPLETE"})
            continue
        limit_count = 0
        limit_event_known = True
        close_limit_n = 0
        close_limit_success = 0
        first_attempt_n = 0
        first_attempt_success = 0
        first_sealed_n = 0
        first_sealed_red = 0
        first_open_premiums: list[Decimal] = []
        for session in mature_sessions:
            idx = session_index[session]
            previous_session = ctx.sessions[idx - 1] if idx > 0 else None
            next_session = ctx.sessions[idx + 1] if idx + 1 < len(ctx.sessions) else None
            if previous_session is None or next_session is None:
                continue
            current = _bar(ctx, session, iid)
            previous = _bar(ctx, previous_session, iid)
            nxt = _bar(ctx, next_session, iid)
            current_event = _bar_limit_event(current, iid, session)
            previous_event = _bar_limit_event(previous, iid, previous_session)
            if current_event is None or previous_event is None or not _known_limit_flag(current_event.close_limit_up):
                limit_event_known = False
                continue
            if current_event.close_limit_up == TRUE:
                limit_count += 1
                close_limit_n += 1
                current_close = _bar_decimal(current, "comparable_close")
                next_close = _bar_decimal(nxt, "comparable_close")
                if current_close and next_close and current_close > 0 and (next_close / current_close - Decimal("1")) > Decimal("0.05"):
                    close_limit_success += 1
            touched = current_event.touched_limit_up == TRUE
            previous_close_limit = previous_event.close_limit_up == TRUE if _known_limit_flag(previous_event.close_limit_up) else None
            if touched and previous_close_limit is False:
                first_attempt_n += 1
                if current_event.close_limit_up == TRUE:
                    first_attempt_success += 1
                    first_sealed_n += 1
                    current_close = _bar_decimal(current, "comparable_close")
                    next_close = _bar_decimal(nxt, "comparable_close")
                    next_open = _bar_decimal(nxt, "comparable_open")
                    if current_close and next_close and current_close > 0 and (next_close / current_close - Decimal("1")) > 0:
                        first_sealed_red += 1
                    if current_close and next_open and current_close > 0:
                        premium = next_open / current_close - Decimal("1")
                        first_open_premiums.append(premium)
                        all_open_premiums.append(premium)
        raw_first_seal_rate = (Decimal(first_attempt_success) / Decimal(first_attempt_n)) if first_attempt_n else Decimal("0")
        if not limit_event_known or first_attempt_n < 3 or raw_first_seal_rate <= Decimal("0.70") or close_limit_n <= 0 or first_sealed_n <= 0:
            insufficient.append(
                {
                    "instrument_id": iid,
                    "status": "INSUFFICIENT_COVERAGE",
                    "reason": "DNA_RELIABILITY_GATE_NOT_MET",
                    "close_limit_n": close_limit_n,
                    "first_attempt_n": first_attempt_n,
                    "first_sealed_n": first_sealed_n,
                    "raw_first_seal_rate": _number(raw_first_seal_rate),
                }
            )
            continue
        raw_rows.append(
            {
                "instrument_id": iid,
                "status": "EVALUATED",
                "limit_count_250d": limit_count,
                "close_limit_success": close_limit_success,
                "close_limit_n": close_limit_n,
                "first_attempt_success": first_attempt_success,
                "first_attempt_n": first_attempt_n,
                "first_sealed_red": first_sealed_red,
                "first_sealed_n": first_sealed_n,
                "first_open_premiums": first_open_premiums,
                "raw_first_seal_rate": raw_first_seal_rate,
            }
        )

    if not raw_rows:
        return {
            "strategy_id": DNA_REVERSAL_ID,
            "implementation_version": IMPLEMENTATION_VERSION,
            "base_strategy_id": PV_OBSERVATION_ID,
            "parameter_status": "INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED",
            "status": "INSUFFICIENT_COVERAGE",
            "reason": "DNA_RELIABILITY_GATE_NOT_MET",
            "history_sessions_required": DNA_HISTORY_SESSIONS,
            "history_sessions_available": len(ctx.sessions),
            "observation_date": ctx.observation_date,
            "counts": {"eligible": len(entrant_ids), "evaluated": 0, "insufficient": len(insufficient)},
            "items": sorted(insufficient, key=lambda row: row["instrument_id"]),
            "top_ids": [],
        }

    pool_mean = sum(all_open_premiums, Decimal("0")) / Decimal(len(all_open_premiums)) if all_open_premiums else Decimal("0")
    limit_values = [row["limit_count_250d"] for row in raw_rows]
    pv_rebased = _pv_population_scores(pv_items, [row["instrument_id"] for row in raw_rows], "S_PV_GENE_COHORT")
    enriched: list[dict[str, Any]] = []
    for row in raw_rows:
        n_open = len(row["first_open_premiums"])
        mean_open = sum(row["first_open_premiums"], Decimal("0")) / Decimal(n_open) if n_open else Decimal("0")
        m_adj = (Decimal(n_open) * mean_open + Decimal(5) * pool_mean) / Decimal(n_open + 5)
        lb_premium5 = max(Decimal("0"), min(Decimal("1"), Decimal(str(m_adj))))
        lb_first_red = Decimal(str(_wilson_lower_bound(row["first_sealed_red"], row["first_sealed_n"])))
        lb_first_seal = Decimal(str(_wilson_lower_bound(row["first_attempt_success"], row["first_attempt_n"])))
        pr_limit = _percentile_rank_decimal(row["limit_count_250d"], limit_values) or Decimal("0")
        pv_score = pv_rebased.get(row["instrument_id"], Decimal("0"))
        factors = {
            "LB_PREMIUM5": lb_premium5 * Decimal(100),
            "LB_FIRST_RED": lb_first_red * Decimal(100),
            "LB_FIRST_SEAL": lb_first_seal * Decimal(100),
            "LIMIT_COUNT_250D": pr_limit,
            "S_PV_REBASED": pv_score,
        }
        gene_score = _score_from_decimals(DNA_WEIGHTS, factors)
        final_score = Decimal("0.60") * pv_score + Decimal("0.40") * gene_score
        item = {
            "instrument_id": row["instrument_id"],
            "status": "EVALUATED",
            "original_S_PV": pv_items.get(row["instrument_id"], {}).get("S_PV"),
            "S_PV_GENE_COHORT": _number(pv_score),
            "G": _number(gene_score),
            "S_DNA": _number(final_score),
            "factors": {
                "LB_PREMIUM5": _number(lb_premium5),
                "LB_FIRST_RED": _number(lb_first_red),
                "LB_FIRST_SEAL": _number(lb_first_seal),
                "LIMIT_COUNT_250D": row["limit_count_250d"],
                "LIMIT_COUNT_250D_PR": _number(pr_limit),
                "S_PV_REBASED": _number(pv_score),
            },
            "sample_counts": {
                "close_limit_n": row["close_limit_n"],
                "first_attempt_n": row["first_attempt_n"],
                "first_sealed_n": row["first_sealed_n"],
                "open_premium_n": n_open,
            },
            "_score": final_score,
        }
        enriched.append(item)

    ranked = sorted(enriched, key=lambda row: (-row["_score"], row["instrument_id"]))
    for rank, row in enumerate(ranked, 1):
        row["rank"] = rank
    public_ranked = [{key: value for key, value in row.items() if not key.startswith("_")} for row in ranked]
    return {
        "strategy_id": DNA_REVERSAL_ID,
        "implementation_version": IMPLEMENTATION_VERSION,
        "base_strategy_id": PV_OBSERVATION_ID,
        "parameter_status": "INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED",
        "status": "COMPLETE" if not insufficient else "LIMITED_SCOPE",
        "history_sessions_required": DNA_HISTORY_SESSIONS,
        "history_sessions_available": len(ctx.sessions),
        "observation_date": ctx.observation_date,
        "formula": "0.60*S_PV_GENE_COHORT + 0.40*G",
        "counts": {
            "eligible": len(entrant_ids),
            "evaluated": len(public_ranked),
            "insufficient": len(insufficient),
        },
        "items": public_ranked + sorted(insufficient, key=lambda row: row["instrument_id"]),
        "top_ids": [row["instrument_id"] for row in public_ranked[:top_n]],
    }


def run_dna_rerank(bundle: MarketInput, pv_result: dict[str, Any] | None = None, *, top_n: int = 100, cutoff: Any | None = None) -> dict[str, Any]:
    result = run_dna_reversal(bundle, pv_result, top_n=top_n, cutoff=cutoff)
    return {**result, "strategy_id": "DNA_RERANK_V1", "canonical_strategy_id": DNA_REVERSAL_ID}


def _theme_snapshots(bundle: MarketInput, session: str) -> list[dict[str, Any]]:
    snapshots = []
    source = getattr(bundle, "theme_snapshots", None)
    if source is None:
        source = getattr(bundle, "themes", ())
    for theme in source or []:
        data = theme.model_dump() if hasattr(theme, "model_dump") else dict(theme)
        effective_date = data.get("effective_date") or data.get("effective_from") or data.get("trade_date") or session
        if _session_text(effective_date) <= session:
            snapshots.append(data)
    return snapshots


def run_theme_limitup(bundle: MarketInput, pv_result: dict[str, Any] | None = None, *, top_n: int = 100, cutoff: Any | None = None) -> dict[str, Any]:
    ctx = _prepare_context(bundle, cutoff=cutoff)
    pv_result = pv_result or run_pv_observation(bundle, top_n=top_n, cutoff=cutoff)
    pv_items = {item["instrument_id"]: item for item in pv_result.get("items", [])}
    entrant_ids = [iid for iid in ctx.eligible_ids if iid in pv_items]
    window = _prior_sessions(ctx, 6)
    if len(window) < 6:
        return {
            "strategy_id": THEME_LIMITUP_ID,
            "implementation_version": IMPLEMENTATION_VERSION,
            "base_strategy_id": PV_OBSERVATION_ID,
            "parameter_status": "INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED",
            "status": "UNAVAILABLE",
            "reason": "THEME_6_SESSION_WINDOW_REQUIRED",
            "observation_date": ctx.observation_date,
            "counts": {"eligible": len(entrant_ids), "evaluated": 0, "insufficient": len(entrant_ids)},
            "items": [{"instrument_id": iid, "status": "INSUFFICIENT_COVERAGE", "reason": "THEME_6_SESSION_WINDOW_REQUIRED"} for iid in entrant_ids],
            "top_ids": [],
        }
    themes = [
        theme
        for theme in _theme_snapshots(bundle, ctx.observation_date)
        if str(theme.get("theme_type", "EVENT")).upper() == "EVENT"
    ]
    if not themes:
        return {
            "strategy_id": THEME_LIMITUP_ID,
            "implementation_version": IMPLEMENTATION_VERSION,
            "base_strategy_id": PV_OBSERVATION_ID,
            "parameter_status": "INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED",
            "status": "UNAVAILABLE",
            "reason": "THEME_EVENT_SNAPSHOT_REQUIRED",
            "observation_date": ctx.observation_date,
            "counts": {"eligible": len(entrant_ids), "evaluated": 0, "insufficient": len(entrant_ids)},
            "items": [{"instrument_id": iid, "status": "INSUFFICIENT_COVERAGE", "reason": "THEME_EVENT_SNAPSHOT_REQUIRED"} for iid in entrant_ids],
            "top_ids": [],
        }
    eligible_set = set(entrant_ids)
    valid_choices: list[dict[str, Any]] = []
    insufficient_by_iid: dict[str, list[str]] = {iid: [] for iid in entrant_ids}
    for iid in entrant_ids:
        for theme in themes:
            theme_id = str(theme.get("theme_id") or theme.get("id") or "")
            members = sorted(set(theme.get("member_ids") or []) & eligible_set)
            if iid not in members:
                continue
            if len(members) < 5:
                insufficient_by_iid[iid].append(f"{theme_id}:THEME_EFFECTIVE_MEMBER_COUNT_LT_5")
                continue
            peers = [peer for peer in members if peer != iid]
            peer_expected = len(peers)
            peer_returns: list[Decimal] = []
            peer_up = 0
            peer_limit = 0
            invalid_reason: str | None = None
            for peer in peers:
                current = _bar(ctx, window[-1], peer)
                start = _bar(ctx, window[0], peer)
                if current is None or start is None:
                    continue
                current_close = _bar_decimal(current, "comparable_close")
                start_close = _bar_decimal(start, "comparable_close")
                event = _bar_limit_event(current, peer, window[-1])
                if current_close is None or start_close is None or start_close <= 0 or event is None:
                    continue
                if not _known_limit_flag(event.close_limit_up):
                    invalid_reason = "THEME_PEER_LIMIT_EVENT_UNVERIFIED"
                    break
                ret5 = current_close / start_close - Decimal("1")
                peer_returns.append(ret5)
                if ret5 > 0:
                    peer_up += 1
                if event.close_limit_up == TRUE:
                    peer_limit += 1
            if invalid_reason:
                insufficient_by_iid[iid].append(f"{theme_id}:{invalid_reason}")
                continue
            peer_observed = len(peer_returns)
            coverage = (Decimal(peer_observed) / Decimal(peer_expected)) if peer_expected else Decimal("0")
            if peer_expected <= 0 or coverage < THEME_PEER_COVERAGE_MIN:
                insufficient_by_iid[iid].append(f"{theme_id}:THEME_PEER_COVERAGE_BELOW_95_PERCENT")
                continue
            valid_choices.append(
                {
                    "instrument_id": iid,
                    "theme_id": theme_id,
                    "theme_name": theme.get("theme_name") or theme.get("name") or theme_id,
                    "member_count": len(members),
                    "peer_expected": peer_expected,
                    "peer_observed": peer_observed,
                    "peer_coverage": coverage,
                    "peer_ret5_median": median(peer_returns),
                    "peer_up_fraction": Decimal(peer_up) / Decimal(peer_observed) if peer_observed else Decimal("0"),
                    "peer_limit_count": peer_limit,
                }
            )

    if not valid_choices:
        return {
            "strategy_id": THEME_LIMITUP_ID,
            "implementation_version": IMPLEMENTATION_VERSION,
            "base_strategy_id": PV_OBSERVATION_ID,
            "parameter_status": "INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED",
            "status": "INSUFFICIENT_COVERAGE",
            "reason": "THEME_EFFECTIVE_MEMBER_OR_LIMIT_COVERAGE_GATE_NOT_MET",
            "observation_date": ctx.observation_date,
            "counts": {"eligible": len(entrant_ids), "evaluated": 0, "insufficient": len(entrant_ids)},
            "items": [
                {
                    "instrument_id": iid,
                    "status": "INSUFFICIENT_COVERAGE",
                    "reason": "THEME_EFFECTIVE_MEMBER_OR_LIMIT_COVERAGE_GATE_NOT_MET",
                    "details": reasons,
                }
                for iid, reasons in sorted(insufficient_by_iid.items())
            ],
            "top_ids": [],
        }

    ret_values = [row["peer_ret5_median"] for row in valid_choices]
    up_values = [row["peer_up_fraction"] for row in valid_choices]
    limit_values = [row["peer_limit_count"] for row in valid_choices]
    for row in valid_choices:
        pr_ret = _percentile_rank_decimal(row["peer_ret5_median"], ret_values) or Decimal("0")
        pr_up = _percentile_rank_decimal(row["peer_up_fraction"], up_values) or Decimal("0")
        pr_limit = _percentile_rank_decimal(row["peer_limit_count"], limit_values) or Decimal("0")
        row["T"] = _score_from_decimals(
            THEME_WEIGHTS,
            {
                "PEER_RET5_MEDIAN": pr_ret,
                "PEER_UP_FRACTION": pr_up,
                "PEER_LIMIT_COUNT": pr_limit,
            },
        )
        row["factor_percentiles"] = {
            "PEER_RET5_MEDIAN": _number(pr_ret),
            "PEER_UP_FRACTION": _number(pr_up),
            "PEER_LIMIT_COUNT": _number(pr_limit),
        }

    chosen_by_iid: dict[str, dict[str, Any]] = {}
    for row in valid_choices:
        current = chosen_by_iid.get(row["instrument_id"])
        if current is None or (-row["T"], row["theme_id"]) < (-current["T"], current["theme_id"]):
            chosen_by_iid[row["instrument_id"]] = row

    pv_rebased = _pv_population_scores(pv_items, chosen_by_iid.keys(), "S_PV_THEME_COHORT")
    evaluated: list[dict[str, Any]] = []
    for iid, row in chosen_by_iid.items():
        pv_score = pv_rebased.get(iid, Decimal("0"))
        final_score = Decimal("0.75") * pv_score + Decimal("0.25") * row["T"]
        evaluated.append(
            {
                "instrument_id": iid,
                "status": "EVALUATED",
                "theme_id": row["theme_id"],
                "theme_name": row["theme_name"],
                "member_count": row["member_count"],
                "peer_expected": row["peer_expected"],
                "peer_observed": row["peer_observed"],
                "peer_coverage": _number(row["peer_coverage"]),
                "peer_ret5_median": _number(row["peer_ret5_median"]),
                "peer_up_fraction": _number(row["peer_up_fraction"]),
                "peer_limit_count": row["peer_limit_count"],
                "factor_percentiles": row["factor_percentiles"],
                "original_S_PV": pv_items.get(iid, {}).get("S_PV"),
                "S_PV_THEME_COHORT": _number(pv_score),
                "T": _number(row["T"]),
                "S_THEME": _number(final_score),
                "_score": final_score,
            }
        )

    ranked = sorted(evaluated, key=lambda row: (-row["_score"], row["instrument_id"]))
    for rank, row in enumerate(ranked, 1):
        row["rank"] = rank
    insufficient_rows = [
        {
            "instrument_id": iid,
            "status": "INSUFFICIENT_COVERAGE",
            "reason": "THEME_EFFECTIVE_MEMBER_OR_LIMIT_COVERAGE_GATE_NOT_MET",
            "details": insufficient_by_iid.get(iid, []),
        }
        for iid in entrant_ids
        if iid not in chosen_by_iid
    ]
    public_ranked = [{key: value for key, value in row.items() if not key.startswith("_")} for row in ranked]
    return {
        "strategy_id": THEME_LIMITUP_ID,
        "implementation_version": IMPLEMENTATION_VERSION,
        "base_strategy_id": PV_OBSERVATION_ID,
        "parameter_status": "INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED",
        "status": "COMPLETE" if not insufficient_rows else "LIMITED_SCOPE",
        "observation_date": ctx.observation_date,
        "formula": "0.75*S_PV_THEME_COHORT + 0.25*T",
        "counts": {
            "eligible": len(entrant_ids),
            "evaluated": len(public_ranked),
            "insufficient": len(insufficient_rows),
            "theme_snapshot_count": len(themes),
        },
        "items": public_ranked + sorted(insufficient_rows, key=lambda row: row["instrument_id"]),
        "top_ids": [row["instrument_id"] for row in public_ranked[:top_n]],
    }


def run_theme_context(bundle: MarketInput, pv_result: dict[str, Any] | None = None, *, top_n: int = 100, cutoff: Any | None = None) -> dict[str, Any]:
    result = run_theme_limitup(bundle, pv_result, top_n=top_n, cutoff=cutoff)
    return {**result, "strategy_id": "THEME_CONTEXT_V1", "canonical_strategy_id": THEME_LIMITUP_ID}


def strategy_comparison(
    daily_summary: dict[str, Any] | None,
    pv: dict[str, Any],
    dna: dict[str, Any],
    theme: dict[str, Any],
    focus: dict[str, Any] | None = None,
) -> dict[str, Any]:
    candidate_sets = {
        "PV": set(pv.get("top_ids", [])),
        "DNA": set(dna.get("top_ids", [])),
        "THEME": set(theme.get("top_ids", [])),
    }
    overlaps = {
        "pv_dna": sorted(candidate_sets["PV"] & candidate_sets["DNA"]),
        "pv_theme": sorted(candidate_sets["PV"] & candidate_sets["THEME"]),
        "dna_theme": sorted(candidate_sets["DNA"] & candidate_sets["THEME"]),
        "all_three": sorted(candidate_sets["PV"] & candidate_sets["DNA"] & candidate_sets["THEME"]),
    }
    if daily_summary:
        published_top = set(daily_summary.get("top_ids") or [])
        overlaps["published_pv"] = sorted(published_top & candidate_sets["PV"])
        overlaps["published_dna"] = sorted(published_top & candidate_sets["DNA"])
        overlaps["published_theme"] = sorted(published_top & candidate_sets["THEME"])
    dna_display_top = dna.get("top_ids", []) or pv.get("top_ids", [])
    theme_display_top = theme.get("top_ids", []) or pv.get("top_ids", [])
    return {
        "comparison_version": "R05_STRATEGY_COMPARISON_V1",
        "parameter_status": "INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED",
        "strategies": [
            {
                "strategy_id": pv.get("strategy_id"),
                "status": pv.get("status"),
                "scope_label": pv.get("scope_label"),
                "top_ids": pv.get("top_ids", []),
                "count": len(pv.get("top_ids", [])),
            },
            {
                "strategy_id": dna.get("strategy_id"),
                "status": dna.get("status"),
                "top_ids": dna_display_top,
                "actual_top_ids": dna.get("top_ids", []),
                "top_ids_source": "ACTUAL_STRATEGY" if dna.get("top_ids") else "BASE_PV_REFERENCE_WHILE_UNMATURED",
                "count": len(dna_display_top),
            },
            {
                "strategy_id": theme.get("strategy_id"),
                "status": theme.get("status"),
                "top_ids": theme_display_top,
                "actual_top_ids": theme.get("top_ids", []),
                "top_ids_source": "ACTUAL_STRATEGY" if theme.get("top_ids") else "BASE_PV_REFERENCE_WHILE_UNMATURED",
                "count": len(theme_display_top),
            },
        ],
        "focus": {
            "strategy_id": FOCUS_POLICY_ID,
            "selected_ids": (focus or {}).get("selected_ids", []),
            "count": len((focus or {}).get("selected_ids", [])),
        },
        "overlaps": overlaps,
        "published_reference": daily_summary.get("run_id") if daily_summary else None,
    }


def run_strategy_experiments(
    bundle: MarketInput,
    daily_summary: dict[str, Any] | None = None,
    *,
    top_n: int = 100,
    cutoff: Any | None = None,
    config: Any | None = None,
) -> dict[str, Any]:
    pv = run_pv_observation(bundle, top_n=top_n, cutoff=cutoff)
    focus = run_pv_focus_preview(pv, max_count=FOCUS_POLICY["max_count"])
    pv["selection_policy_id"] = FOCUS_POLICY_ID
    pv["focus_count"] = len(focus.get("selected_ids", []))
    pv["focus_ids"] = focus.get("selected_ids", [])
    dna = run_dna_reversal(bundle, pv, top_n=top_n, cutoff=cutoff)
    theme = run_theme_limitup(bundle, pv, top_n=top_n, cutoff=cutoff)
    legacy_dna = {**dna, "strategy_id": "DNA_RERANK_V1", "canonical_strategy_id": DNA_REVERSAL_ID}
    legacy_theme = {**theme, "strategy_id": "THEME_CONTEXT_V1", "canonical_strategy_id": THEME_LIMITUP_ID}
    input_id = getattr(bundle, "input_id", None) or ("market_input_" + digest(bundle)[:16])
    payload = {
        "strategy_family": "R05",
        "input_id": input_id,
        "observation_date": pv.get("observation_date"),
        "implementation_version": IMPLEMENTATION_VERSION,
        "parameter_status": "INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED",
        "results": {
            pv["strategy_id"]: pv,
            dna["strategy_id"]: dna,
            theme["strategy_id"]: theme,
            legacy_dna["strategy_id"]: legacy_dna,
            legacy_theme["strategy_id"]: legacy_theme,
        },
        "selection_results": {
            FOCUS_POLICY_ID: focus,
        },
    }
    payload["comparison"] = strategy_comparison(daily_summary, pv, dna, theme, focus)
    payload["run_id"] = _stable_id(
        "strategy_experiment",
        {
            "input_id": input_id,
            "observation_date": payload["observation_date"],
            "implementation_version": IMPLEMENTATION_VERSION,
            "top_n": top_n,
        },
    )
    return payload


def strategy_specs() -> dict[str, Any]:
    return {
        "task_id": "R05",
        "implementation_version": IMPLEMENTATION_VERSION,
        "strategies": [
            {
                "strategy_id": "DAILY_V1",
                "description": "Published daily candidate baseline used only as a comparison reference for R05 experiments.",
                "formula": "Existing production candidate score.",
                "market_validation_status": "SEPARATE_PRODUCTION_BASELINE",
            },
            {
                "strategy_id": PV_OBSERVATION_ID,
                "description": "PV observation experiment using exact midpoint percentile ranks for volume expansion, 5-session comparable return, and high-low range.",
                "formula": "S_PV=0.40*PR_mid(A)+0.30*PR_mid(R)+0.30*PR_mid(H)",
                "coverage_min": float(PV_QUOTE_COVERAGE_MIN),
            },
            {
                "strategy_id": DNA_REVERSAL_ID,
                "description": "Event-DNA reversal experiment requiring 252 exchange sessions and Wilson lower-bound reliability gates.",
                "formula": "S_DNA=0.60*S_PV_GENE_COHORT+0.40*G",
                "history_sessions_required": DNA_HISTORY_SESSIONS,
            },
            {
                "strategy_id": THEME_LIMITUP_ID,
                "description": "Theme peer contagion experiment requiring effective theme size and verified peer limit-up event coverage.",
                "formula": "S_THEME=0.75*S_PV_THEME_COHORT+0.25*T",
                "peer_coverage_min": float(THEME_PEER_COVERAGE_MIN),
            },
            {
                "strategy_id": FOCUS_POLICY_ID,
                "description": "Deterministic focus preview policy over PV results; rejected rows remain exportable and evaluable.",
                "selection_policy": FOCUS_POLICY,
            },
        ],
    }


STRATEGY_SPECS = strategy_specs()["strategies"]
from types import SimpleNamespace
