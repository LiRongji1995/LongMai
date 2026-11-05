"""Compose authenticated daily observations for the frozen outcome evaluator.

This is an outcome-only input, not a historical strategy input or a PIT claim.
No selection, normalization, price adjustment, source clock, or missing value is
changed. Live native archive capabilities are the only accepted source owners.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta

from stockfish_core.yaogu import short_horizon_archive_bridge_v1 as bridge
from stockfish_core.yaogu.contracts import MarketInput, UniverseSnapshot

VERSION = "ACTIVE_OUTCOME_INPUT_V1"
_CLOCKS = ("event_time", "source_published_at", "first_seen_at", "ingested_at")


def _need(value, reason):
    if not value:
        raise ValueError(VERSION + "_" + reason)


def _merge_calendar(bundles):
    """Preserve the signal prefix; verify every overlapping calendar interval.

    Native archives may use rolling history windows. An absent old prefix is
    not a conflicting date, but an omitted session *inside* an overlap is.
    """
    signal = bundles[0]
    merged = set(signal.sessions)
    for index, bundle in enumerate(bundles):
        sessions = bundle.sessions
        _need(sessions and sessions[-1] == bundle.observation_date,
              "CALENDAR_OBSERVATION_BOUNDARY")
        if index:
            _need(sessions[0] <= max(merged) and sessions[-1] >= min(merged),
                  "CALENDAR_DISCONNECTED")
        for prior in bundles[:index]:
            low, high = max(sessions[0], prior.sessions[0]), min(sessions[-1], prior.sessions[-1])
            if low <= high:
                _need(tuple(day for day in sessions if low <= day <= high)
                      == tuple(day for day in prior.sessions if low <= day <= high),
                      "CALENDAR_OVERLAP_CONFLICT")
        merged.update(day for day in sessions if day > signal.observation_date)
    result = tuple(sorted(merged))
    _need(tuple(day for day in result if day <= signal.observation_date) == signal.sessions,
          "SIGNAL_CALENDAR_PREFIX_CHANGED")
    return result


def _unique_receipts(bundles, checkpoint):
    receipts = {}
    hashes = {}
    for bundle in bundles:
        for receipt in bundle.receipts:
            checkpoint()
            checksum = bridge.digest_json_stream(receipt.model_dump(mode="json"), checkpoint=checkpoint)
            prior = hashes.get(receipt.receipt_id)
            _need(prior is None or prior == checksum, "RECEIPT_ID_CONFLICT")
            receipts[receipt.receipt_id], hashes[receipt.receipt_id] = receipt, checksum
    return tuple(receipts[key] for key in sorted(receipts))


def _current_rows(bundle, checkpoint):
    day = bundle.observation_date
    securities, bars = {}, {}
    for row in bundle.universe.securities:
        checkpoint()
        if row.effective_from != day:
            continue
        # Never extend a day's identity into an absent later observation. Do
        # not trim a source interval and thereby rewrite its dated evidence.
        _need(row.effective_to == day + timedelta(days=1), "NON_DAILY_SECURITY_INTERVAL")
        key = row.instrument_id
        _need(key not in securities or securities[key] == row, "SECURITY_REVISION_CONFLICT")
        securities[key] = row
    for row in bundle.bars:
        checkpoint()
        if row.session != day:
            continue
        key = row.instrument_id
        _need(key not in bars or bars[key] == row, "BAR_REVISION_CONFLICT")
        bars[key] = row
    return tuple(securities[key] for key in sorted(securities)), tuple(bars[key] for key in sorted(bars))


def _source(capability, session, role):
    state = bridge._state(capability)
    _need(state["session"] is session, "SAME_LIVE_VERIFICATION_SESSION_REQUIRED")
    bundle = state.get("retained_input")
    bridge._inline_typed_input(bundle)
    session.check()
    _need(bridge.digest_market_input(bundle, checkpoint=session.check,
              encoding_profile=session.encoding_profile) == state["input_hash"],
          "RETAINED_INPUT_CHANGED")
    run = bridge._verified_run(state)
    audit = state["audit"]
    _need(str(bundle.observation_date) == run["observation_date"] == audit["observation_date"],
          "ARCHIVE_OBSERVATION_CONFLICT")
    _need(run["input_id"] == "market_" + state["input_hash"] == audit["input_id"],
          "ARCHIVE_INPUT_ID_CONFLICT")
    record = dict(role=role, observation_date=run["observation_date"],
        target_session=audit["target_session"], run_id=run["run_id"], input_id=run["input_id"],
        input_hash=state["input_hash"], run_document_hash=state["run_hash"],
        archive_job=audit["archive_job"], archive_final_sha256=audit["archive_final_sha256"],
        original_decision_cutoff=audit["original_decision_cutoff"],
        original_generated_at=audit["original_generated_at"],
        original_final_sealed_at=audit["original_final_sealed_at"],
        coverage_type=bundle.coverage_type,
        original_input_reference=deepcopy(state["input_reference"]),
        original_run_reference=deepcopy(state["run_reference"]))
    return bundle, run, record


def compose(run, signal_capability, daily_capabilities, session):
    """Return ``(MarketInput, deterministic_manifest)`` without publishing.

    Caller owns the one session's cumulative read/time/RSS budget, final
    authority checks and the frozen evaluator call. Only daily input IDs enter
    the OUTCOME business identity; the immutable run ID already binds signal.
    ``manifest_digest`` covers all other manifest fields. This manifest is
    provenance, never a serialized capability or vendor authentication.
    """
    _need(type(session) is bridge.ArchiveVerificationSession, "NATIVE_SESSION_REQUIRED")
    _need(isinstance(daily_capabilities, (tuple, list)) and daily_capabilities,
          "DAILY_CAPABILITIES_REQUIRED")
    signal, original_run, signal_ref = _source(signal_capability, session, "SIGNAL")
    _need(bridge.digest_json_stream(run, checkpoint=session.check)
          == bridge.digest_json_stream(original_run, checkpoint=session.check), "SIGNAL_RUN_CHANGED")
    identity = {key: original_run.get(key) for key in (*bridge._IDENTITY, "strategy_id", "execution_profile")}
    by_day = {}
    for capability in daily_capabilities:
        bundle, daily_run, ref = _source(capability, session, "DAILY")
        _need(bundle.observation_date > signal.observation_date, "DAILY_MUST_FOLLOW_SIGNAL")
        _need(bundle.coverage_type == signal.coverage_type, "COVERAGE_TYPE_CONFLICT")
        _need({key: daily_run.get(key) for key in identity} == identity, "STRATEGY_IDENTITY_CONFLICT")
        prior = by_day.get(bundle.observation_date)
        _need(prior is None or prior[1] == ref, "DATE_ARCHIVE_CONFLICT")
        by_day[bundle.observation_date] = (bundle, ref)
    daily = [by_day[day] for day in sorted(by_day)]
    bundles = [signal, *(item[0] for item in daily)]
    refs = [item[1] for item in daily]
    sessions = _merge_calendar(bundles)
    receipts = _unique_receipts(bundles, session.check)
    receipt_ids = {row.receipt_id for row in receipts}
    securities, bars, selected = [], [], []
    for bundle in bundles:
        current_securities, current_bars = _current_rows(bundle, session.check)
        securities.extend(current_securities)
        bars.extend(current_bars)
        for row in (*current_securities, *current_bars):
            _need(row.source_refs and set(row.source_refs) <= receipt_ids, "SOURCE_REFERENCE_MISSING")
        selected.append(dict(observation_date=str(bundle.observation_date),
            security_count=len(current_securities), bar_count=len(current_bars),
            securities_hash=bridge.digest_json_stream([row.model_dump(mode="json") for row in current_securities], checkpoint=session.check),
            bars_hash=bridge.digest_json_stream([row.model_dump(mode="json") for row in current_bars], checkpoint=session.check)))
    universe_refs = tuple(sorted({ref for bundle in bundles for ref in bundle.universe.source_refs}))
    _need(universe_refs and set(universe_refs) <= receipt_ids, "UNIVERSE_SOURCE_REFERENCE_MISSING")
    manifest = dict(schema=VERSION, signal=signal_ref, daily_archives=refs,
        source_input_ids=sorted({ref["input_id"] for ref in refs}),
        all_input_ids=sorted({signal_ref["input_id"], *(ref["input_id"] for ref in refs)}),
        signal_observation_date=str(signal.observation_date), observation_date=str(bundles[-1].observation_date),
        selected_daily_rows=selected, sessions=[str(day) for day in sessions],
        coverage_type=signal.coverage_type, original_strategy_identity=deepcopy(identity),
        scope="AUTHENTICATED_CURRENT_DAY_ROWS_ONLY_FOR_FROZEN_OUTCOME_EVALUATOR",
        source_clocks_changed=False, missing_days_filled=False,
        historical_rows_promoted=False, prospective_authority_created=False,
        universe_clock_policy="DERIVED_ENVELOPE_MAX_OF_ORIGINAL_ENVELOPES_SOURCE_ROWS_UNCHANGED")
    # A new envelope never borrows an original snapshot identity for new rows.
    universe = UniverseSnapshot(snapshot_id="active_outcome_" + bridge.digest_json_stream(manifest, checkpoint=session.check),
        securities=tuple(securities), source_refs=universe_refs,
        complete=all(bundle.universe.complete for bundle in bundles),
        expected_count=len({row.instrument_id for row in securities}),
        **{key: max(getattr(bundle.universe, key) for bundle in bundles) for key in _CLOCKS})
    bundle = MarketInput(observation_date=bundles[-1].observation_date, sessions=sessions,
        universe=universe, bars=tuple(bars), receipts=receipts, coverage_type=signal.coverage_type)
    checksum = bridge.digest_market_input(bundle, checkpoint=session.check, encoding_profile=session.encoding_profile)
    manifest.update(input_id="market_" + checksum, input_hash=checksum)
    manifest["manifest_digest"] = bridge.digest_json_stream(manifest, checkpoint=session.check)
    session.terminal()
    return bundle, manifest
