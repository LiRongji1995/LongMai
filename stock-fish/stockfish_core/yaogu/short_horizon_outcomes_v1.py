"""Versioned R1/R2 marked-price observations; never fills or SAM episodes.

Only registered input sessions are counted. Actual row AND parent capture
clocks must precede evaluation even for reconstructed historical inputs.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time
from fractions import Fraction
from pathlib import Path
from zoneinfo import ZoneInfo
import hashlib
import json

from .contracts import digest
from .history_artifacts import digest_market_input as _reference_digest_market_input
from .history_artifacts import history_resolver, iter_history_securities
from .outcomes import _bar_map, _price_valid
from .short_horizon_archive_bridge_v1 import digest_json_stream, digest_market_input, validate_encoding_profile
from .visibility import stamp

VERSION = 'SHORT_HORIZON_OUTCOME_V1'
HORIZONS = (1, 2, 3, 5, 10)
# Existing typed provider_normalization endpoints that actually carry prices.
# Adjustment-factor and prepublished limit parents remain visibility-checked,
# but cannot substitute for a final daily-price response.
PRICE_PARENT_DATASETS = frozenset({'history', 'market_daily', 'history_adjusted', 'tushare_daily'})
NAMESPACES = {
    'outcomes': 'short_horizon_outcome_revisions_v1',
    'tracking': 'short_horizon_tracking_revisions_v1',
    'review': 'short_horizon_review_revisions_v1',
}
SH = ZoneInfo('Asia/Shanghai')


def _outcome_input_hash(bundle, *, checkpoint, encoding_profile=None):
    # This optimization is for inline inputs. Historical storage keeps its
    # original digest and owner/resolver behavior, including fail-closed reads.
    if encoding_profile is not None:
        validate_encoding_profile(encoding_profile)
    if bundle.history_artifact_ref or bundle.history_artifact_hash or bundle._history_store is not None:
        checkpoint()
        value = _reference_digest_market_input(bundle)
        checkpoint()
        return value
    options = {'encoding_profile': encoding_profile} if encoding_profile is not None else {}
    return digest_market_input(bundle, checkpoint=checkpoint, **options)


def validated_horizons(horizons=None):
    values = HORIZONS if horizons is None else tuple(horizons)
    if not values or any(type(h) is not int or h not in HORIZONS for h in values):
        raise ValueError('SHORT_HORIZON_OUTCOME_REQUIRES_H1_H2_H3_H5_H10')
    return tuple(sorted(set(values)))


def capability(run_id, kind):
    return {'schema_version': VERSION, 'run_id': run_id, 'product': kind,
        'capability_status': 'IMPLEMENTED', 'status': 'PENDING', 'items': [],
        'returns': None, 'revision_count': 0, 'full_report_available': False,
        'reason_codes': ['SHORT_HORIZON_OBSERVATION_NOT_RUN'],
        'market_edge': 'NOT_ESTABLISHED', 'execution_status': 'NOT_VERIFIED'}


def _fraction(value):
    if value is None:
        return None
    if isinstance(value, dict):
        return Fraction(value['numerator'], value['denominator'])
    return Fraction(str(value))


def _statistics(rows):
    groups = []
    for horizon in sorted({r['horizon'] for r in rows}):
        for cohort in ('ALL_EVALUATED', 'RESEARCH_SELECTED'):
            selected = [r for r in rows if r['horizon'] == horizon
                        and (cohort == 'ALL_EVALUATED' or r['research_selected'])]
            observed = [r['metrics']['close_reference_change'] for r in selected if r['status'] == 'MATURE']
            counts = Counter(r['status'] for r in selected)
            groups.append({'horizon': horizon, 'cohort': cohort, 'eligible': len(selected),
                'mature': counts['MATURE'], 'pending': counts['PENDING'], 'unknown': counts['UNKNOWN'],
                'distinct_instruments': len({r['instrument_id'] for r in selected}),
                'mean_close_reference_change': sum(observed) / len(observed) if observed else None,
                'positive_reference_change_fraction': sum(v > 0 for v in observed) / len(observed) if observed else None,
                'statistical_role': 'DESCRIPTIVE_SINGLE_SIGNAL_DATE_DEPENDENT_OBSERVATIONS',
                'inference_status': 'INSUFFICIENT_INDEPENDENT_DATES_NO_MARKET_EDGE_TEST'})
    return groups


def _calendar(bundle, signal_day, evaluated_at):
    from ..observed_data import exchange_calendar_2026
    path = Path(__file__).resolve().parents[1] / 'exchange_calendar_2026.json'
    raw = path.read_bytes()
    metadata = json.loads(raw)
    schedule = exchange_calendar_2026()
    evidence = {'policy': 'REGISTERED_AXIS_CHECKED_AGAINST_FINITE_OFFICIAL_2026_SCHEDULE',
        'source_hash': hashlib.sha256(raw).hexdigest(), 'sources': metadata['sources'],
        'verified_at': metadata['verified_at'], 'coverage_start': str(schedule.coverage_start),
        'coverage_end': str(schedule.coverage_end), 'test_fixture_clock_exception': bundle.coverage_type == 'TEST_FIXTURE'}
    if signal_day not in bundle.sessions:
        return (), evidence, 'SIGNAL_SESSION_ABSENT_FROM_REGISTERED_CALENDAR'
    if signal_day not in schedule.sessions:
        return (), evidence, 'SIGNAL_SESSION_OUTSIDE_VERIFIED_CALENDAR'
    if bundle.coverage_type != 'TEST_FIXTURE' and evaluated_at.astimezone(SH).date() < date.fromisoformat(metadata['verified_at']):
        return (), evidence, 'LOCAL_CALENDAR_VERIFICATION_NOT_AVAILABLE_AT_EVALUATION'
    return schedule.sessions[schedule.sessions.index(signal_day) + 1:], evidence, None


def _security_at(records, iid, day, resolver, *, outer_visible):
    if not outer_visible:
        return None
    matches = [s for s in records if s.instrument_id == iid and s.effective_from <= day
        and (s.effective_to is None or day < s.effective_to)
        and resolver.is_visible(s, instrument_id=iid, session=day, require_complete=True)]
    if not matches:
        return None
    latest = max(s.ingested_at for s in matches)
    tied = [s for s in matches if s.ingested_at == latest]
    return tied[0] if len({digest(s) for s in tied}) == 1 else None


def _daily_close_published(bar, session):
    close_time = datetime.combine(session, time(15), SH)
    return bar.event_time >= close_time and bar.source_published_at >= close_time


def _daily_price_parent_reason(bar, session, resolver):
    parents = [resolver.receipts[ref] for ref in bar.source_refs
               if ref in resolver.receipts and resolver.receipts[ref].dataset in PRICE_PARENT_DATASETS]
    if not parents:
        return 'DAILY_PRICE_PARENT_ROLE_UNVERIFIED'
    close_time = datetime.combine(session, time(15), SH)
    if any(parent.first_seen_at < close_time or parent.ingested_at < close_time for parent in parents):
        return 'PRICE_PARENT_CAPTURE_PRECEDES_DAILY_CLOSE'
    return None


def evaluate(run, bundle, evaluated_at: datetime, *, horizons=None, checkpoint=None, encoding_profile=None):
    """Return three immutable products sharing a content-derived revision ID.

    The identity excludes wall-clock evaluation time: repeated evaluation with
    unchanged inputs, visibility and maturity is a no-op. A newly visible row,
    maturity transition, revised registered input or evaluator source changes it.
    """
    horizons = validated_horizons(horizons)
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    if encoding_profile is not None:
        validate_encoding_profile(encoding_profile)
    evaluated_at = stamp(evaluated_at)
    if bundle.coverage_type not in {'TEST_FIXTURE', 'PROSPECTIVE_CAPTURED', 'RECONSTRUCTED_HISTORICAL'}:
        raise ValueError('SHORT_HORIZON_UNVERIFIABLE_OUTCOME_COVERAGE')
    if run.get('execution_profile') != 'SHORT_HORIZON_RESEARCH_SINGLE' or run.get('strategy_id') not in {
            'SHORT_HORIZON_STRENGTH_R1', 'SHORT_HORIZON_STRENGTH_R2'}:
        raise ValueError('SHORT_HORIZON_FROZEN_RUN_REQUIRED')
    if run.get('coverage_type') not in {'TEST_FIXTURE', 'PROSPECTIVE_CAPTURED', 'RECONSTRUCTED_HISTORICAL'}:
        raise ValueError('SHORT_HORIZON_UNVERIFIABLE_SIGNAL_COVERAGE')
    frozen_hash = digest_json_stream({k: v for k, v in run.items() if k != 'content_hash'}, checkpoint=checkpoint)
    if run.get('content_hash') != frozen_hash:
        raise ValueError('SHORT_HORIZON_RUN_CONTENT_HASH_MISMATCH')
    if evaluated_at < max(stamp(run['decision_cutoff']), stamp(run.get('generated_at', run['decision_cutoff']))):
        raise ValueError('SHORT_HORIZON_EVALUATION_PRECEDES_SIGNAL')
    signal_day = date.fromisoformat(run['observation_date'])
    future, calendar_evidence, calendar_reason = _calendar(bundle, signal_day, evaluated_at)
    result = run['results'][run['strategy_id']]
    items = result.get('items') or []
    if len({item['instrument_id'] for item in items}) != len(items):
        raise ValueError('SHORT_HORIZON_DUPLICATE_INSTRUMENT')
    resolver = history_resolver(bundle, cutoff=evaluated_at, allow_future_sessions=True)
    # Outcome availability is actual knowledge at evaluation, never the relaxed
    # historical reconstruction rule used to study old signal dates.
    resolver.coverage_type = 'PROSPECTIVE_CAPTURED'
    bars = _bar_map(bundle, evaluated_at, resolver)
    outer_visible = resolver.is_visible(bundle.universe.model_dump(mode='json', exclude={'securities'}), require_complete=True)
    security_index = {}
    if not bundle.history_artifact_ref:
        for security in bundle.universe.securities:
            security_index.setdefault(security.instrument_id, []).append(security)
    rows, tracking = [], []
    for item in items:
        checkpoint()
        iid = item['instrument_id']
        securities = (tuple(iter_history_securities(bundle, instrument_id=iid))
                      if bundle.history_artifact_ref else security_index.get(iid, ()))
        frozen_close = _fraction((item.get('exact_features') or {}).get('close_comparable'))
        basis = item.get('comparable_basis')
        reference = bars.get((iid, signal_day))
        reference_parent_reason = _daily_price_parent_reason(reference, signal_day, resolver) if reference else None
        reference_ok = (reference is not None and reference.quality == 'VALID'
            and _daily_close_published(reference, signal_day)
            and reference_parent_reason is None
            and resolver.is_visible(reference, instrument_id=iid, session=signal_day,
                                    require_session=True, require_complete=True)
            and frozen_close is not None and frozen_close > 0 and isinstance(basis, str) and bool(basis.strip())
            and reference.comparable_basis == basis and _fraction(reference.comparable_close) == frozen_close)
        sessions = []
        for day in future[:max(horizons)]:
            bar = bars.get((iid, day))
            state = _security_at(securities, iid, day, resolver, outer_visible=outer_visible)
            status, reason = 'UNKNOWN', 'MISSING_OR_NOT_VISIBLE_PRICE'
            if day not in bundle.sessions:
                reason = 'REGISTERED_CALENDAR_SESSION_MISSING'
            elif datetime.combine(day, time(15), SH) > evaluated_at:
                status, reason = 'PENDING', 'SESSION_NOT_CLOSED'
            elif state is None or state.suspended is None or state.st is None:
                reason = 'DATED_SECURITY_STATE_UNKNOWN'
            elif state.listed_on > day or (state.delisted_on is not None and state.delisted_on <= day):
                reason = 'NOT_LISTED_OR_DELISTED'
            elif state.suspended:
                reason = 'SUSPENDED_NO_ASSUMED_FILL'
            elif state.exchange not in {'SSE', 'SZSE'} or state.board != 'MAIN' or state.security_type != 'A_COMMON':
                reason = 'SECURITY_IDENTITY_UNSUPPORTED'
            elif not reference_ok:
                reason = 'FROZEN_REFERENCE_MISSING_NOT_VISIBLE_OR_REVISED'
            elif bar is not None:
                if not resolver.is_visible(bar, instrument_id=iid, session=day,
                                           require_session=True, require_complete=True):
                    reason = 'PRICE_PARENT_NOT_COMPLETE_OR_VISIBLE'
                elif not _daily_close_published(bar, day):
                    reason = 'DAILY_CLOSE_NOT_FINAL_AS_OF_SESSION'
                elif (parent_reason := _daily_price_parent_reason(bar, day, resolver)) is not None:
                    reason = parent_reason
                elif bar.quality != 'VALID' or not _price_valid(bar):
                    reason = 'INVALID_OR_PARTIAL_OHLC'
                elif bar.comparable_basis != basis or any(getattr(bar, 'comparable_' + p) is None
                                                          for p in ('open', 'high', 'low', 'close')):
                    reason = 'COMPARABILITY_UNSUPPORTED'
                else:
                    status, reason = 'OBSERVED', None
            sessions.append({'session': str(day), 'status': status, 'reason_codes': [reason] if reason else [],
                'bar_hash': digest(bar) if status == 'OBSERVED' else None,
                'security_state_hash': digest(state) if state is not None else None,
                'source_refs': sorted(set((bar.source_refs if status == 'OBSERVED' else ())
                                         + (state.source_refs if state is not None else ()))),
                'close_reference_change': float(_fraction(bar.comparable_close) / frozen_close - 1)
                    if status == 'OBSERVED' else None})
        descriptor = {'run_id': run['run_id'], 'instrument_id': iid, 'strategy_id': run['strategy_id'],
            'research_selected': item.get('research_selected') is True, 'formal_focus': False,
            'primary_stage': item.get('primary_stage'), 'decision_status': item.get('decision_status'),
            'matched_arms': item.get('matched_arms', []), 'selection_role': 'RESEARCH_OBSERVATION_NOT_FORMAL_FOCUS'}
        tracking.append({**descriptor, 'sessions': sessions,
            'coverage': dict(Counter(s['status'] for s in sessions)),
            'tracking_semantics': 'PER_SESSION_OBSERVATION_NO_EPISODE_OR_TRADE'})
        for horizon in horizons:
            window = sessions[:horizon]
            candidate_end = future[horizon - 1] if len(future) >= horizon else None
            calendar_ok = (candidate_end is not None and not calendar_reason
                and tuple(d for d in bundle.sessions if signal_day < d <= candidate_end) == future[:horizon])
            end = candidate_end if calendar_ok else None
            mature = datetime.combine(end, time(15), SH) <= evaluated_at if end is not None else None
            complete = mature and reference_ok and len(window) == horizon and all(s['status'] == 'OBSERVED' for s in window)
            status = 'MATURE' if complete else 'PENDING' if mature is False else 'UNKNOWN'
            reasons = sorted({r for s in window for r in s['reason_codes']})
            if end is None:
                reasons.append('REGISTERED_CALENDAR_HORIZON_UNAVAILABLE')
            if calendar_reason:
                reasons.append(calendar_reason)
            if not reference_ok:
                reasons.append('FROZEN_REFERENCE_MISSING_NOT_VISIBLE_OR_REVISED')
                if reference_parent_reason:
                    reasons.append(reference_parent_reason)
            metrics = {'close_reference_change': None, 'next_open_gap': None,
                'next_open_reference_change': None, 'mfe_bound': None, 'mae_bound': None}
            if complete:
                observed = [bars[(iid, day)] for day in future[:horizon]]
                first, last = observed[0], observed[-1]
                metrics = {'close_reference_change': float(_fraction(last.comparable_close) / frozen_close - 1),
                    'next_open_gap': float(_fraction(first.comparable_open) / frozen_close - 1),
                    'next_open_reference_change': float(_fraction(last.comparable_close) / _fraction(first.comparable_open) - 1),
                    'mfe_bound': float(max(_fraction(b.comparable_high) for b in observed) / frozen_close - 1),
                    'mae_bound': float(min(_fraction(b.comparable_low) for b in observed) / frozen_close - 1)}
            rows.append({**descriptor, 'horizon': horizon, 'label_end': str(end) if end else None,
                'status': status, 'calendar_mature': mature, 'observed_sessions': sum(s['status'] == 'OBSERVED' for s in window),
                'pending_sessions': sum(s['status'] == 'PENDING' for s in window),
                'unknown_sessions': sum(s['status'] == 'UNKNOWN' for s in window),
                'required_sessions': horizon, 'reason_codes': sorted(set(reasons)), 'metrics': metrics,
                'session_states': window, 'reference_hash': digest(reference) if reference_ok else None,
                'frozen_reference': {'comparable_close': (item.get('exact_features') or {}).get('close_comparable'),
                                     'comparable_basis': basis}})
        tracking[-1]['horizon_states'] = [{k: r[k] for k in ('horizon', 'status', 'calendar_mature',
            'observed_sessions', 'pending_sessions', 'unknown_sessions', 'required_sessions', 'reason_codes')}
            for r in rows[-len(horizons):]]
    checkpoint()
    encoding_options = {'encoding_profile': encoding_profile} if encoding_profile is not None else {}
    identity = {'schema_version': VERSION, 'run_id': run['run_id'], 'signal_content_hash': frozen_hash,
        'signal_input_id': run['input_id'], 'outcome_input_id': 'market_' + _outcome_input_hash(
            bundle, checkpoint=checkpoint, **encoding_options),
        'horizons': list(horizons), 'calendar_hash': digest(bundle.sessions),
        'calendar_evidence': calendar_evidence,
        'evaluator_source_hash': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'dependency_source_hashes': {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in ('outcomes.py', 'visibility.py', 'history_artifacts.py', 'contracts.py',
                         'short_horizon_archive_bridge_v1.py')},
        'calendar_implementation_hashes': {name: hashlib.sha256((Path(__file__).resolve().parents[1] / name).read_bytes()).hexdigest()
            for name in ('observed_data.py', 'data.py')},
        'effective_evidence': resolver.effective_dependencies(),
        'rows': rows}
    revision_id = 'short_outcome_' + digest_json_stream(identity, checkpoint=checkpoint)
    common = {k: v for k, v in identity.items() if k != 'rows'}
    common.update(revision_id=revision_id, evaluated_at=evaluated_at.isoformat(),
        observation_date=str(signal_day), strategy_id=run['strategy_id'],
        signal_coverage_type=run.get('coverage_type'), outcome_coverage_type=bundle.coverage_type,
        capability_status='IMPLEMENTED', status='AVAILABLE', market_edge='NOT_ESTABLISHED',
        horizon_unit='exchange_trading_sessions', calendar_authority=calendar_evidence['policy'],
        execution_status='NOT_VERIFIED', reference_method='COMPARABLE_MARKED_PRICES_NOT_ASSUMED_FILLS',
        path_order='DAILY_OHLC_DOES_NOT_IDENTIFY_INTRADAY_ORDER',
        t_plus_one_note='Next-open to same-day-close is a price reference, not realizable T+1 profit.',
        account_cost_capacity_portfolio_status='NOT_IMPLEMENTED_BY_THIS_OBSERVATION_POLICY',
        signal_policy_mutated=False, official_v2_validation_countable=False)
    counts = dict(Counter(row['status'] for row in rows))
    review = {**common, 'product': 'review', 'counts': {'mature': counts.get('MATURE', 0),
        'pending': counts.get('PENDING', 0), 'unknown': counts.get('UNKNOWN', 0), 'outcome_observations': len(rows),
        'distinct_signal_dates': 1, 'distinct_instruments': len(items)},
        'by_horizon_and_selection': _statistics(rows), 'returns': None,
        'reason_codes': ['SINGLE_RUN_DESCRIPTIVE_OBSERVATION_NO_CAUSAL_OR_MARKET_EDGE_INFERENCE'],
        'full_report_available': True, 'rolling_windows': {}}
    return {'outcomes': {**common, 'product': 'outcomes', 'items': rows, 'counts': counts, 'returns': None},
            'tracking': {**common, 'product': 'tracking', 'items': tracking,
                'tracking_semantics': 'PER_SESSION_OBSERVATION_NO_EPISODE_OR_TRADE'},
            'review': review}
