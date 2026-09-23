"""Append-only daily research price observations; never a trading or signal engine.

The saved recommendation supplies the reference price and research identity.
Only cutoff-visible, source-backed observations may describe what followed it.
This module has no storage, network, scheduler, or strategy mutation side effects.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from statistics import mean, median
from zoneinfo import ZoneInfo

from .contracts import digest
from .history_artifacts import digest_market_input, history_resolver, iter_bars, iter_history_securities
from .outcomes import _price_valid


POLICY_ID = "DAILY_RESEARCH_PRICE_PATH_V1"
SCHEMA_VERSION = "DAILY_RESEARCH_OUTCOME_V1"
SUMMARY_VERSION = "DAILY_RESEARCH_OUTCOME_SUMMARY_V1"
HORIZONS = tuple(range(1, 21))
STATUSES = ("NORMAL", "MISSING", "SUSPENDED", "UNAVAILABLE", "NOT_MATURED")
SH = ZoneInfo("Asia/Shanghai")


def _stamp(value):
    value = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("daily outcome clock requires an explicit timezone")
    return value


def _day(value):
    return value.date() if isinstance(value, datetime) else value if isinstance(value, date) else date.fromisoformat(str(value))


def _decimal(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result > 0 else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _number(value):
    return round(float(value), 10) if value is not None else None


def _snapshot_hash(snapshot):
    result = digest({key: value for key, value in snapshot.items() if key != "snapshot_hash"})
    if snapshot.get("snapshot_hash") not in (None, result):
        raise ValueError("daily recommendation snapshot hash mismatch")
    return result


def _identity(snapshot):
    value = dict(snapshot.get("identity") or {})
    if not value.get("run_id") or not value.get("observation_date"):
        raise ValueError("daily recommendation requires run_id and observation_date")
    return value


def _snapshot_times(snapshot):
    identity = snapshot.get("identity") or {}
    prospective = snapshot.get("prospective_validation") or {}
    return [_stamp(owner[key]) for owner, keys in (
        (identity, ("data_cutoff", "generated_at", "published_at")),
        (snapshot, ("data_cutoff", "decision_cutoff", "generated_at", "published_at")),
        (prospective, ("actual_decision_cutoff", "capture_completed_at")),
    ) for key in keys if owner.get(key) is not None]


def snapshot_available_as_of(snapshot, as_of):
    """All captured availability bounds must be known by the requested instant.

    Missing generation time does not waive a known later cutoff/publication.
    With no captured clock at all, historical availability cannot be proven.
    This does not prevent an unqualified read of the original saved snapshot.
    """
    cutoff = _stamp(as_of)
    clocks = _snapshot_times(snapshot)
    observation = _identity(snapshot)["observation_date"]
    return bool(clocks) and max(clocks) <= cutoff and _day(observation) <= cutoff.astimezone(SH).date()


def _items(snapshot):
    selected, seen = [], set()
    for item in snapshot.get("items", ()):
        if item.get("decision_status") not in ("FOCUS", "WATCH"):
            continue
        iid = item.get("instrument_id")
        if not iid or iid in seen:
            raise ValueError("daily recommendation selected instrument identity is missing or duplicated")
        if item.get("run_id") not in (None, _identity(snapshot)["run_id"]):
            raise ValueError("daily recommendation item run mismatch")
        seen.add(iid)
        selected.append(item)
    return selected


def _sessions(sessions):
    result = tuple(_day(value) for value in sessions)
    if result != tuple(sorted(set(result))):
        raise ValueError("daily outcome calendar must contain unique ascending exchange sessions")
    return result


def _future_sessions(observation_date, sessions):
    if observation_date not in sessions:
        return ()
    return sessions[sessions.index(observation_date) + 1:sessions.index(observation_date) + 21]


def _mature(session, cutoff):
    return session is not None and datetime.combine(session, time(15), SH) <= cutoff


def _select_evidence(rows):
    if not rows:
        return None, []
    key = lambda row: (row.source_published_at, row.ingested_at, row.first_seen_at)
    latest = max(map(key, rows))
    choices = [row for row in rows if key(row) == latest]
    if len({digest(row) for row in choices}) != 1:
        return None, ["CONFLICTING_EVIDENCE_REVISION"]
    return choices[0], []


def _instrument_evidence(bundle, iid, start, end, resolver):
    """Resolve only this frozen instrument, including names outside today's pool."""
    visible, rejected = defaultdict(list), defaultdict(list)
    for bar in iter_bars(bundle, instrument_id=iid, start=start, end=end):
        proof = resolver.resolve(bar, instrument_id=iid, session=bar.session, require_session=True)
        if proof.allowed:
            visible[bar.session].append(bar)
        else:
            rejected[bar.session].extend(proof.reason_codes)
    bars, issues = {}, {}
    for session in set(visible) | set(rejected):
        bar, conflict = _select_evidence(visible[session])
        bars[session] = bar
        issues[session] = conflict or ([] if bar is not None else sorted(set(rejected[session])))
    securities = list(iter_history_securities(bundle, instrument_id=iid, start=start, end=end))
    return bars, issues, securities


def _security_state(bundle, iid, session, records, resolver):
    # Keep the existing outcome contract's outer and dated security evidence gate.
    outer = bundle.universe.model_dump(mode="json", exclude={"securities"})
    proof = resolver.resolve(outer, require_complete=True)
    if not proof.allowed:
        return None, list(proof.reason_codes)
    if not bundle.universe.complete:
        return None, ["SECURITY_UNIVERSE_EVIDENCE_INCOMPLETE"]
    visible, rejected = [], []
    for state in records:
        if state.effective_from > session or (state.effective_to is not None and session >= state.effective_to):
            continue
        proof = resolver.resolve(state, instrument_id=iid, session=session, require_complete=True)
        if proof.allowed:
            visible.append(state)
        else:
            rejected.extend(proof.reason_codes)
    result, conflict = _select_evidence(visible)
    return result, conflict or ([] if result else sorted(set(rejected)) or ["DATED_SECURITY_STATUS_UNAVAILABLE"])


def _reference(item, bars, issues, observation_date):
    reference, basis = _decimal(item.get("comparable_close")), item.get("comparable_basis")
    reasons = []
    if reference is None or not isinstance(basis, str) or not basis:
        reasons.append("FROZEN_REFERENCE_UNAVAILABLE")
    # Never replace a missing frozen reference with a price downloaded afterwards.
    current = bars.get(observation_date)
    if issues.get(observation_date):
        reasons.append("REFERENCE_EVIDENCE_UNAVAILABLE")
    elif current is not None and (
        current.quality != "VALID" or not _price_valid(current)
        or current.comparable_basis != basis or current.comparable_close != reference
    ):
        reasons.append("REFERENCE_PRICE_REVISION_MISMATCH")
    return reference, basis, reasons


def _daily_state(bundle, item, session, cutoff, resolver, bars, issues, securities, reference, basis, reference_issues):
    row = {"session": str(session) if session else None, "status": "UNAVAILABLE", "matured": False,
           "close_price": None, "comparable_close": None, "close_reference_change": None,
           "comparable_high": None, "comparable_low": None, "reason_codes": [], "source_refs": []}
    if session is None:
        row["reason_codes"] = ["EXCHANGE_CALENDAR_HORIZON_UNAVAILABLE"]
        return row
    if not _mature(session, cutoff):
        row.update(status="NOT_MATURED", reason_codes=["SESSION_NOT_MATURED"])
        return row
    row["matured"] = True
    iid = item["instrument_id"]
    security, reasons = _security_state(bundle, iid, session, securities, resolver)
    if security is None or security.suspended is None:
        row["reason_codes"] = reasons or ["SUSPENSION_STATE_UNKNOWN"]
        return row
    row["source_refs"] = list(security.source_refs)
    if security.delisted_on is not None and session >= security.delisted_on:
        row["reason_codes"] = ["DELISTED"]
        return row
    if security.suspended:
        row.update(status="SUSPENDED", reason_codes=["VERIFIED_SUSPENSION"])
        return row
    bar = bars.get(session)
    if bar is None:
        row.update(status="UNAVAILABLE" if issues.get(session) else "MISSING",
                   reason_codes=issues.get(session) or ["PRICE_OBSERVATION_MISSING"])
        return row
    row["source_refs"] = sorted(set(row["source_refs"]) | set(bar.source_refs))
    if bar.quality != "VALID" or not _price_valid(bar):
        row["reason_codes"] = ["INVALID_OR_INCOMPLETE_PRICE"]
        return row
    row["close_price"] = str(bar.close)
    if reference_issues:
        row["reason_codes"] = list(reference_issues)
        return row
    if bar.comparable_basis != basis or bar.comparable_close is None:
        row["reason_codes"] = ["COMPARABLE_PRICE_BASIS_UNAVAILABLE"]
        return row
    if ((bar.comparable_low is not None and bar.comparable_low > bar.comparable_close)
            or (bar.comparable_high is not None and bar.comparable_high < bar.comparable_close)):
        row["reason_codes"] = ["INVALID_COMPARABLE_PRICE_RANGE"]
        return row
    row.update(status="NORMAL", comparable_close=str(bar.comparable_close),
               close_reference_change=_number(bar.comparable_close / reference - 1),
               comparable_high=str(bar.comparable_high) if bar.comparable_high is not None else None,
               comparable_low=str(bar.comparable_low) if bar.comparable_low is not None else None)
    if bar.comparable_high is None or bar.comparable_low is None:
        row["reason_codes"] = ["COMPARABLE_HIGH_LOW_UNAVAILABLE"]
    return row


def _binding(snapshot, item, offset, snapshot_hash):
    identity = _identity(snapshot)
    return {"run_id": identity["run_id"], "snapshot_hash": snapshot_hash,
            "source_run_hash": snapshot.get("source_run_hash"), "instrument_id": item["instrument_id"],
            "strategy_version": identity.get("strategy_version"), "config_version": identity.get("config_version"),
            "validation_epoch_id": snapshot.get("validation_epoch_id"),
            "coverage_type": snapshot.get("coverage_type"),
            "evidence_type": snapshot.get("evidence_type") or _evidence_type(snapshot),
            "decision_status": item["decision_status"], "primary_setup": item.get("primary_setup"),
            "observation_date": identity["observation_date"], "offset": offset, "policy_id": POLICY_ID}


def _evidence_type(snapshot):
    coverage = snapshot.get("coverage_type")
    prospective = snapshot.get("prospective_validation") or {}
    if coverage == "TEST_FIXTURE":
        return "TEST_FIXTURE"
    if coverage == "RECONSTRUCTED_HISTORICAL" or prospective.get("coverage_type") == "RECONSTRUCTED_HISTORICAL":
        return "RECONSTRUCTED_HISTORICAL"
    # Only the frozen run may claim officialness; this observer never upgrades it.
    if coverage == "PROSPECTIVE_CAPTURED" and prospective.get("official_validation_signal_countable") is True:
        return "OFFICIAL_PROSPECTIVE"
    return "PRELIMINARY_RESEARCH"


def evaluate_daily_research_outcomes(snapshot, bundle, evaluated_at, *, sessions=None):
    """Produce all twenty immutable logical positions for each saved FOCUS/WATCH.

    ``sessions`` must be an already verified exchange calendar supplied by the
    caller. Without it, only the registered MarketInput calendar is used.
    Missing dates are unavailable, never guessed from weekdays.
    """
    cutoff, identity = _stamp(evaluated_at), _identity(snapshot)
    if not snapshot_available_as_of(snapshot, cutoff):
        raise ValueError("daily outcome evaluation precedes original recommendation availability or availability is unknown")
    snapshot_hash = _snapshot_hash(snapshot)
    calendar = _sessions(bundle.sessions if sessions is None else sessions)
    observation_date = _day(identity["observation_date"])
    future = _future_sessions(observation_date, calendar)
    input_hash = digest_market_input(bundle)
    resolver = history_resolver(bundle, cutoff=cutoff, allow_future_sessions=True)
    values = []
    for item in _items(snapshot):
        bars, issues, securities = _instrument_evidence(bundle, item["instrument_id"], observation_date,
                                                       future[-1] if future else observation_date, resolver)
        reference, basis, reference_issues = _reference(item, bars, issues, observation_date)
        path = []
        for offset in HORIZONS:
            session = future[offset - 1] if offset <= len(future) else None
            state = _daily_state(bundle, item, session, cutoff, resolver, bars, issues, securities,
                                 reference, basis, reference_issues)
            path.append(state)
            complete = all(row["status"] == "NORMAL" and row["comparable_high"] is not None
                           and row["comparable_low"] is not None for row in path)
            high = max(_decimal(row["comparable_high"]) for row in path) if complete else None
            low = min(_decimal(row["comparable_low"]) for row in path) if complete else None
            binding = _binding(snapshot, item, offset, snapshot_hash)
            logical_id = "daily_research_logical_" + digest(binding)
            row = {"schema_version": SCHEMA_VERSION, **binding, **state,
                   "target_session": identity.get("target_session"), "data_cutoff": identity.get("data_cutoff"),
                   "horizon_unit": "exchange_trading_sessions", "evaluated_at": cutoff.isoformat(),
                   "input_hash": input_hash, "input_id": "market_" + input_hash,
                   "outcome_coverage_type": bundle.coverage_type,
                   "reference_close": str(reference) if reference is not None else None, "reference_basis": basis,
                   "cumulative_high": str(high) if high is not None else None,
                   "cumulative_low": str(low) if low is not None else None,
                   "mfe_bound": _number(high / reference - 1) if high is not None else None,
                   "mae_bound": _number(low / reference - 1) if low is not None else None,
                   "price_status": "VALID" if state["close_reference_change"] is not None else state["status"],
                   "path_status": "COMPLETE" if complete else "NOT_MATURED" if state["status"] == "NOT_MATURED" else "INCOMPLETE",
                   "path_reason_codes": sorted({reason for entry in path for reason in entry["reason_codes"]}),
                   "path_source_refs": sorted({ref for entry in path for ref in entry["source_refs"]}),
                   "path_observed_sessions": sum(entry["status"] == "NORMAL" for entry in path),
                   "expectation_status": "NOT_DEFINED", "market_edge": "NOT_ESTABLISHED",
                   "reference_method": "FROZEN_COMPARABLE_MARKED_PRICE_NOT_ASSUMED_FILL",
                   "logical_observation_id": logical_id}
            row["observation_id"] = "daily_research_observation_" + digest(row)
            values.append(row)
    return values


def select_daily_outcome_revisions(rows, as_of):
    """Select latest factual revision per frozen identity, including invalidations."""
    cutoff, groups = _stamp(as_of), {}
    for row in rows:
        if row.get("policy_id") != POLICY_ID or not row.get("logical_observation_id") or not row.get("observation_id"):
            continue
        if _stamp(row["evaluated_at"]) > cutoff:
            continue
        key = (row["logical_observation_id"], row.get("snapshot_hash"))
        order = (_stamp(row["evaluated_at"]), row["observation_id"])
        previous = groups.get(key)
        if previous is None or order > (_stamp(previous["evaluated_at"]), previous["observation_id"]):
            groups[key] = row
    return sorted(groups.values(), key=lambda row: (row["run_id"], row["instrument_id"], row["offset"], row["snapshot_hash"]))


def _distribution(values):
    values = sorted(values)
    def quantile(q):
        if not values:
            return None
        index = (len(values) - 1) * q
        low, high = int(index), min(int(index) + 1, len(values) - 1)
        return _number(values[low] + (values[high] - values[low]) * (index - low))
    return {"count": len(values), "mean": _number(mean(values)) if values else None,
            "min": values[0] if values else None, "p10": quantile(.1),
            "median": _number(median(values)) if values else None,
            "p90": quantile(.9), "max": values[-1] if values else None}


def summarize_daily_research_outcomes(snapshots, rows, as_of, *, sessions=None):
    """Descriptive groups with all saved signals in denominators, never ranking."""
    cutoff = _stamp(as_of)
    calendar = _sessions(sessions) if sessions is not None else ()
    selected = select_daily_outcome_revisions(rows, cutoff)
    indexed = {(row["run_id"], row["instrument_id"], row["offset"], row["snapshot_hash"]): row for row in selected}
    groups, seen = {}, set()
    availability = {"included_snapshots": 0, "excluded_not_yet_available": 0, "excluded_availability_unknown": 0}
    group_keys = ("strategy_version", "config_version", "validation_epoch_id", "coverage_type", "evidence_type",
                  "decision_status", "primary_setup", "offset")
    for snapshot in snapshots:
        identity, snapshot_hash = _identity(snapshot), _snapshot_hash(snapshot)
        if snapshot_hash in seen:
            continue
        seen.add(snapshot_hash)
        if not snapshot_available_as_of(snapshot, cutoff):
            availability["excluded_not_yet_available" if _snapshot_times(snapshot) else "excluded_availability_unknown"] += 1
            continue
        availability["included_snapshots"] += 1
        future = _future_sessions(_day(identity["observation_date"]), calendar)
        for item in _items(snapshot):
            for offset in HORIZONS:
                binding = _binding(snapshot, item, offset, snapshot_hash)
                key = tuple(binding[name] for name in group_keys)
                group = groups.setdefault(key, {"identity": {name: binding[name] for name in group_keys}, "samples": []})
                row = indexed.get((identity["run_id"], item["instrument_id"], offset, snapshot_hash))
                # Stored input cannot relabel a snapshot, tier, setup, version or policy.
                if row and any(row.get(name) != value for name, value in binding.items()):
                    row = None
                if row and row.get("logical_observation_id") != "daily_research_logical_" + digest(binding):
                    row = None
                if row and offset <= len(future) and row.get("session") != str(future[offset - 1]):
                    row = None
                session = future[offset - 1] if offset <= len(future) else _day(row["session"]) if row and row.get("session") else None
                mature = _mature(session, cutoff) if session else None
                group["samples"].append({"run_id": identity["run_id"], "instrument_id": item["instrument_id"],
                                          "observation_date": identity["observation_date"], "row": row, "mature": mature})
    output = []
    for key in sorted(groups, key=lambda value: tuple("" if entry is None else str(entry) for entry in value[:-1]) + (value[-1],)):
        group, samples = groups[key], groups[key]["samples"]
        rows = [sample["row"] for sample in samples if sample["row"] is not None]
        valid = [row for row in rows if row.get("close_reference_change") is not None and row.get("status") == "NORMAL"]
        paths = [row for row in rows if row.get("path_status") == "COMPLETE"]
        returns = [row["close_reference_change"] for row in valid]
        matured = sum(sample["mature"] is True for sample in samples)
        positive = sum(value > 0 for value in returns)
        counts = {"signals": len(samples), "unique_instruments": len({sample["instrument_id"] for sample in samples}),
                  "signal_dates": len({sample["observation_date"] for sample in samples}),
                  "matured": matured, "not_matured": sum(sample["mature"] is False for sample in samples),
                  "calendar_unavailable": sum(sample["mature"] is None for sample in samples),
                  "missing_labels": len(samples) - len(rows), "valid_return": len(valid), "valid_path": len(paths),
                  "due_without_valid_return": sum(sample["mature"] is True and (sample["row"] is None or sample["row"].get("close_reference_change") is None) for sample in samples),
                  "due_without_complete_path": sum(sample["mature"] is True and (sample["row"] is None or sample["row"].get("path_status") != "COMPLETE") for sample in samples),
                  "stale_not_matured_labels": sum(sample["mature"] is True and sample["row"] is not None and sample["row"].get("status") == "NOT_MATURED" for sample in samples),
                  "positive": positive, "zero": sum(value == 0 for value in returns),
                  "negative": sum(value < 0 for value in returns),
                  "status_counts": {status: sum(row.get("status") == status for row in rows) for status in STATUSES}}
        output.append({**group["identity"], "counts": counts,
                       "maturity_rate": matured / len(samples) if samples and counts["calendar_unavailable"] == 0 else None,
                       "valid_outcome_rate": len(valid) / matured if matured else None,
                       "up_rate": positive / len(valid) if valid else None,
                       "return_distribution": _distribution(returns),
                       "mfe_distribution": _distribution([row["mfe_bound"] for row in paths if row.get("mfe_bound") is not None]),
                       "mae_distribution": _distribution([row["mae_bound"] for row in paths if row.get("mae_bound") is not None])})
    return {"schema_version": SUMMARY_VERSION, "policy_id": POLICY_ID, "as_of": cutoff.isoformat(),
            "groups": output, "horizons": list(HORIZONS), "ranked": False, "snapshot_availability": availability,
            "distribution_quantile_policy": "LINEAR_INTERPOLATION_AT_N_MINUS_ONE_TIMES_Q",
            "maturity_policy": "EXCHANGE_SESSION_CLOSE_REACHED_INDEPENDENT_OF_PRICE_AVAILABILITY",
            "missing_policy": "KEEP_ALL_FROZEN_SIGNALS_NO_ZERO_FILL", "sample_unit": "RECOMMENDATION_SIGNAL_NOT_INDEPENDENT_TRADE",
            "validation_status": "NOT_VERIFIED", "market_edge": "NOT_ESTABLISHED"}
