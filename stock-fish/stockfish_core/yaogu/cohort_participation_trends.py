"""Exact descriptive mathematics over caller-validated daily eligible facts.

No source reader, selector, qualification issuer or capability is constructed.
Content hashes bind declarations only; they do not authenticate their sources.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from fractions import Fraction
import hashlib
import json
from pathlib import Path
from typing import Iterable, Mapping, Sequence

RESEARCH_ID = FEATURE_VERSION = 'COHORT_PARTICIPATION_TRENDS_V1'
IMPLEMENTATION_VERSION = 'COHORT_PARTICIPATION_TRENDS_V1_EXACT_MATH'
SPEC_REVISION = 'COHORT_PARTICIPATION_TRENDS_V1_SPEC_1'
PARAMETER_STATUS = 'INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED'
PARAMETER_STATUS_LABEL_ZH = '尚未验证'
MARKET_EDGE = 'NOT_ESTABLISHED'
_ROOT = Path(__file__).resolve().parents[2]
_SPEC = _ROOT / 'docs/COHORT_PARTICIPATION_TRENDS_V1.md'
_STATUSES = frozenset(('FOCUS', 'WATCH', 'REJECTED', 'UNKNOWN'))


def _text(value, field):
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f'COHORT_{field}_NONEMPTY_STRING_REQUIRED')
    return value


def _day(value):
    # datetime is a date subclass; accepting it would silently discard clocks.
    if type(value) is not date:
        raise ValueError('COHORT_BUSINESS_DATE_REQUIRED')
    return value


@dataclass(frozen=True)
class CohortRecord:
    """One row in a caller-declared complete eligible pool, not qualification."""
    instrument_id: str
    decision_status: str
    research_selected: bool
    amount_cny: Fraction | None

    def __post_init__(self):
        _text(self.instrument_id, 'INSTRUMENT_ID')
        if self.decision_status not in _STATUSES:
            raise ValueError('COHORT_DECISION_STATUS_INVALID')
        if type(self.research_selected) is not bool:
            raise ValueError('COHORT_SELECTED_EXPLICIT_BOOL_REQUIRED')
        if self.research_selected and self.decision_status not in ('WATCH', 'FOCUS'):
            raise ValueError('COHORT_SELECTED_WITH_UNKNOWN_OR_REJECTED_DECISION')
        if self.amount_cny is not None:
            # Reject floats, Decimal, int and bool rather than guessing units or
            # recovering precision already lost by a caller's float conversion.
            if type(self.amount_cny) is not Fraction:
                raise ValueError('COHORT_AMOUNT_EXACT_FRACTION_OR_NONE_REQUIRED')
            if self.amount_cny < 0:
                raise ValueError('COHORT_AMOUNT_NEGATIVE')

    @property
    def selection_state(self):
        # Native saved UNKNOWN rows often contain bool False. It is not a known
        # negative selection observation and must not produce an observed exit.
        return None if self.decision_status == 'UNKNOWN' else self.research_selected

    @property
    def watch_state(self):
        return None if self.decision_status == 'UNKNOWN' else self.decision_status == 'WATCH'


@dataclass(frozen=True)
class DailySnapshot:
    """Immutable mathematical declaration; its completeness is caller asserted."""
    session: date
    records: tuple[CohortRecord, ...]
    comparison_basis: str
    eligible_pool_complete: bool
    coverage_type: str = 'CALLER_DECLARED_RESEARCH_FACTS'

    def __post_init__(self):
        _day(self.session)
        _text(self.comparison_basis, 'COMPARISON_BASIS')
        _text(self.coverage_type, 'COVERAGE_TYPE')
        if self.eligible_pool_complete is not True:
            raise ValueError('COHORT_COMPLETE_ELIGIBLE_POOL_DECLARATION_REQUIRED')
        records = tuple(self.records)
        if any(type(row) is not CohortRecord for row in records):
            raise ValueError('COHORT_TYPED_RECORD_REQUIRED')
        ids = [row.instrument_id for row in records]
        if len(ids) != len(set(ids)):
            raise ValueError('COHORT_DUPLICATE_INSTRUMENT_ID')
        object.__setattr__(self, 'records', tuple(sorted(records, key=lambda row: row.instrument_id)))


def encode_exact(value):
    """JSON-ready exact values; no float projection or source authority added."""
    if isinstance(value, Fraction):
        return {'numerator': value.numerator, 'denominator': value.denominator}
    if type(value) is date:
        return value.isoformat()
    if isinstance(value, Mapping):
        return {key: encode_exact(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode_exact(item) for item in value]
    return value


def _hash(value):
    raw = json.dumps(encode_exact(value), sort_keys=True, separators=(',', ':'),
                     ensure_ascii=False, allow_nan=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def snapshot_declaration(snapshot):
    if type(snapshot) is not DailySnapshot:
        raise ValueError('COHORT_TYPED_SNAPSHOT_REQUIRED')
    return {'session': snapshot.session, 'comparison_basis': snapshot.comparison_basis,
        'eligible_pool_complete': snapshot.eligible_pool_complete,
        'coverage_type': snapshot.coverage_type,
        'records': [{'instrument_id': row.instrument_id, 'decision_status': row.decision_status,
            'research_selected': row.research_selected, 'selection_state': row.selection_state,
            'watch_state': row.watch_state, 'amount_cny': row.amount_cny} for row in snapshot.records]}


def snapshot_content_hash(snapshot):
    """Declaration hash only. Matching hashes cannot restore any capability."""
    return _hash(snapshot_declaration(snapshot))


def source_identity():
    paths = (Path(__file__), _SPEC)
    hashes = {path.relative_to(_ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in paths}
    return {'research_id': RESEARCH_ID, 'feature_version': FEATURE_VERSION,
        'implementation_version': IMPLEMENTATION_VERSION, 'spec_revision': SPEC_REVISION,
        'source_file_hashes': hashes, 'source_hash': _hash(hashes)}


def _calendar(calendar):
    sessions = tuple(calendar)
    if not sessions or any(type(day) is not date for day in sessions):
        raise ValueError('COHORT_EXPLICIT_BUSINESS_DATE_CALENDAR_REQUIRED')
    if any(a >= b for a, b in zip(sessions, sessions[1:])):
        raise ValueError('COHORT_CALENDAR_NOT_STRICTLY_INCREASING')
    return sessions


def _sequence(snapshots, calendar):
    snapshots = tuple(snapshots)
    if not snapshots or any(type(item) is not DailySnapshot for item in snapshots):
        raise ValueError('COHORT_NONEMPTY_TYPED_SNAPSHOTS_REQUIRED')
    if any(a.session >= b.session for a, b in zip(snapshots, snapshots[1:])):
        raise ValueError('COHORT_SNAPSHOTS_NOT_STRICTLY_INCREASING')
    if any(item.session not in calendar for item in snapshots):
        raise ValueError('COHORT_SNAPSHOT_NOT_IN_DECLARED_TRADING_CALENDAR')
    return snapshots


def _ids(values):
    ids = tuple(values)
    for iid in ids:
        _text(iid, 'COHORT_INSTRUMENT_ID')
    if len(ids) != len(set(ids)):
        raise ValueError('COHORT_FIXED_COHORT_DUPLICATE_INSTRUMENT_ID')
    return tuple(sorted(ids))


def _ratio(numerator, denominator, *, reasons=()):
    reasons = list(reasons)
    if not denominator:
        reasons.append('EMPTY_DENOMINATOR')
    return {'status': 'UNKNOWN' if reasons else 'AVAILABLE',
            'value': None if reasons else Fraction(numerator, denominator),
            'numerator_count': numerator, 'denominator_count': denominator,
            'reason_codes': reasons}


def _transition(left, right, common, *, attribute, comparable, observation_role):
    buckets = {key: [] for key in ('retained', 'newly_selected', 'exited', 'negative_both', 'unknown')}
    before, after = [], []
    for iid in common:
        a, b = getattr(left[iid], attribute), getattr(right[iid], attribute)
        if a is True:
            before.append(iid)
        if b is True:
            after.append(iid)
        key = ('unknown' if a is None or b is None else 'retained' if a and b
               else 'newly_selected' if b else 'exited' if a else 'negative_both')
        buckets[key].append(iid)
    reasons = ([] if comparable else ['COMPARISON_BASIS_DECLARATIONS_DIFFER'])
    if not common:
        reasons.append('EMPTY_COMMON_ELIGIBLE_POOL')
    if buckets['unknown']:
        reasons.append('COMMON_ELIGIBLE_DECISION_UNKNOWN')
    # Changed declarations do not authorize even a known setup transition.
    claims = {key: (ids if comparable else []) for key, ids in buckets.items() if key != 'unknown'}
    if not comparable:
        buckets['unknown'] = list(common)
    retained = len(claims['retained'])
    entered, exited = len(claims['newly_selected']), len(claims['exited'])
    return {'status': 'UNKNOWN' if reasons else 'AVAILABLE', 'reason_codes': reasons,
        'transition_observation_role': observation_role,
        'population': 'SAME_IID_COMMON_ELIGIBLE_POOL', 'instrument_ids': list(common),
        'previous_positive_known_ids': before, 'current_positive_known_ids': after,
        'known_transition_ids': claims, 'unknown_ids': buckets['unknown'],
        'retention_fraction': _ratio(retained, len(before), reasons=reasons),
        'newly_selected_fraction_of_current': _ratio(entered, len(after), reasons=reasons),
        'exit_fraction_of_previous': _ratio(exited, len(before), reasons=reasons),
        'jaccard': _ratio(retained, retained + entered + exited, reasons=reasons),
        'observed_known_transition_counts': {key: len(ids) for key, ids in claims.items()}}


def _fixed_amounts(left, right, ids, comparable):
    observations, reasons, known_both = [], [], []
    if not ids:
        reasons.append('EMPTY_FIXED_COHORT')
    if not comparable:
        reasons.append('COMPARISON_BASIS_DECLARATIONS_DIFFER')
    for iid in ids:
        a, b = left.get(iid), right.get(iid)
        row_reasons = []
        if a is None or b is None:
            row_reasons.append('FIXED_COHORT_ELIGIBILITY_BOUNDARY')
        if a is not None and a.amount_cny is None:
            row_reasons.append('PREVIOUS_AMOUNT_UNKNOWN')
        if b is not None and b.amount_cny is None:
            row_reasons.append('CURRENT_AMOUNT_UNKNOWN')
        if not row_reasons:
            known_both.append(iid)
        reasons.extend(row_reasons)
        observations.append({'instrument_id': iid, 'previous_eligible': a is not None,
            'current_eligible': b is not None, 'previous_amount_cny': a.amount_cny if a else None,
            'current_amount_cny': b.amount_cny if b else None, 'reason_codes': row_reasons})
    reasons = sorted(set(reasons))
    available = not reasons
    previous = sum((left[iid].amount_cny for iid in ids), Fraction(0)) if available else None
    current = sum((right[iid].amount_cny for iid in ids), Fraction(0)) if available else None
    growth_reasons = list(reasons)
    if available and previous == 0:
        growth_reasons.append('ZERO_BASE_AMOUNT_GROWTH_UNDEFINED')
    return {'status': 'AVAILABLE' if available else 'UNKNOWN', 'reason_codes': reasons,
        'instrument_ids': list(ids), 'observations': observations, 'unit': 'CNY',
        'previous_amount_cny': previous, 'current_amount_cny': current,
        'growth': {'status': 'UNKNOWN' if growth_reasons else 'AVAILABLE',
            'value': None if growth_reasons else current / previous - 1,
            'reason_codes': growth_reasons},
        'known_both_ids': known_both,
        'known_both_previous_amount_cny': (sum((left[iid].amount_cny for iid in known_both), Fraction(0))
                                          if known_both else None),
        'known_both_current_amount_cny': (sum((right[iid].amount_cny for iid in known_both), Fraction(0))
                                         if known_both else None),
        'known_both_subtotal_is_full_cohort': available,
        'interpretation': 'SAME_FIXED_IID_TRADING_VALUE_CHANGE_NOT_NET_CAPITAL_FLOW'}


def _moving_total(snapshot):
    selected = [row for row in snapshot.records if row.selection_state is True]
    unknown_ids = [row.instrument_id for row in snapshot.records if row.selection_state is None]
    missing = [row.instrument_id for row in selected if row.amount_cny is None]
    reasons = []
    if not selected:
        reasons.append('EMPTY_KNOWN_SELECTED_POOL')
    if unknown_ids:
        reasons.append('SELECTED_POPULATION_UNDETERMINED')
    if missing:
        reasons.append('SELECTED_AMOUNT_UNKNOWN')
    return {'session': snapshot.session, 'status': 'UNKNOWN' if reasons else 'AVAILABLE',
        'reason_codes': reasons, 'selected_known_ids': [row.instrument_id for row in selected],
        'selection_unknown_ids': unknown_ids, 'amount_unknown_ids': missing,
        'amount_cny': None if reasons else sum((row.amount_cny for row in selected), Fraction(0)),
        'interpretation': 'CHANGING_SELECTED_POOL_TOTAL_NOT_SAME_IID_TREND_OR_NET_FLOW'}


def compare_snapshots(previous: DailySnapshot, current: DailySnapshot, *,
                      calendar: Sequence[date], fixed_cohorts: Mapping[str, Iterable[str]] | None = None):
    """Endpoint facts. Missing exchange sessions are disclosed, never filled."""
    calendar = _calendar(calendar)
    previous, current = _sequence((previous, current), calendar)
    left = {row.instrument_id: row for row in previous.records}
    right = {row.instrument_id: row for row in current.records}
    common = tuple(sorted(left.keys() & right.keys()))
    comparable = previous.comparison_basis == current.comparison_basis
    start, end = calendar.index(previous.session), calendar.index(current.session)
    gaps = calendar[start+1:end]
    transition_role = ('ENDPOINT_MEMBERSHIP_CHANGE_NOT_CONTINUOUS_RETENTION' if gaps
                       else 'ADJACENT_EXCHANGE_SESSION_MEMBERSHIP_CHANGE')
    cohorts = {'common_eligible': common}
    for label, ids in (fixed_cohorts or {}).items():
        _text(label, 'FIXED_COHORT_LABEL')
        if label in cohorts:
            raise ValueError('COHORT_FIXED_COHORT_LABEL_RESERVED')
        cohorts[label] = _ids(ids)
    all_ids = sorted(left.keys() | right.keys())
    return {'previous_session': previous.session, 'current_session': current.session,
        'previous_snapshot_content_hash': snapshot_content_hash(previous),
        'current_snapshot_content_hash': snapshot_content_hash(current),
        'hash_role': 'DECLARATION_BINDING_ONLY_NOT_SOURCE_AUTHENTICATION',
        'comparison_basis_declarations_equal': comparable,
        'comparison_basis_is_authenticated': False,
        'comparison_kind': 'ADJACENT_EXCHANGE_SESSIONS' if not gaps else 'ENDPOINT_COMPARISON_ACROSS_MISSING_SESSIONS',
        'intermediate_exchange_sessions': list(gaps),
        'eligibility_scope': {'previous_ids': sorted(left), 'current_ids': sorted(right),
            'common_ids': list(common), 'entering_ids': sorted(right.keys() - left.keys()),
            'leaving_ids': sorted(left.keys() - right.keys()),
            'interpretation': 'DECLARED_ELIGIBLE_SCOPE_CHANGE_NOT_SETUP_ENTRY_OR_EXIT'},
        'instrument_observations': [{'instrument_id': iid, 'previous_eligible': iid in left,
            'current_eligible': iid in right,
            'previous_decision_status': left[iid].decision_status if iid in left else None,
            'current_decision_status': right[iid].decision_status if iid in right else None,
            'previous_research_selected': left[iid].research_selected if iid in left else None,
            'current_research_selected': right[iid].research_selected if iid in right else None,
            'previous_selection_state': left[iid].selection_state if iid in left else None,
            'current_selection_state': right[iid].selection_state if iid in right else None,
            'previous_watch_state': left[iid].watch_state if iid in left else None,
            'current_watch_state': right[iid].watch_state if iid in right else None,
            'previous_amount_cny': left[iid].amount_cny if iid in left else None,
            'current_amount_cny': right[iid].amount_cny if iid in right else None,
            'reason_codes': (['ELIGIBILITY_BOUNDARY'] if iid not in left or iid not in right else [])
                + (['DECISION_UNKNOWN'] if any(row and row.decision_status == 'UNKNOWN'
                   for row in (left.get(iid), right.get(iid))) else [])} for iid in all_ids],
        'selected': _transition(left, right, common, attribute='selection_state', comparable=comparable,
                                observation_role=transition_role),
        'watch': _transition(left, right, common, attribute='watch_state', comparable=comparable,
                             observation_role=transition_role),
        'fixed_cohort_amounts': {label: _fixed_amounts(left, right, ids, comparable)
                                for label, ids in cohorts.items()},
        'moving_selected_totals': [_moving_total(previous), _moving_total(current)],
        'moving_selected_total_growth_computed': False}


def observed_selected_runs(snapshots: Sequence[DailySnapshot], *, calendar: Sequence[date]):
    """Observed contiguous positives, with both censoring edges and all gaps."""
    calendar = _calendar(calendar)
    snapshots = _sequence(snapshots, calendar)
    first, last = calendar.index(snapshots[0].session), calendar.index(snapshots[-1].session)
    sessions = calendar[first:last+1]
    by_day = {item.session: {row.instrument_id: row for row in item.records} for item in snapshots}
    bases = {item.session: item.comparison_basis for item in snapshots}
    all_ids = sorted({row.instrument_id for item in snapshots for row in item.records})
    tracks = []
    for iid in all_ids:
        observations = []
        for session in sessions:
            records = by_day.get(session)
            row = records.get(iid) if records is not None else None
            reason = ('SNAPSHOT_MISSING' if records is None else 'ELIGIBILITY_BOUNDARY'
                      if row is None else 'DECISION_UNKNOWN' if row.selection_state is None else None)
            observations.append({'session': session, 'state': row.selection_state if row else None,
                'eligible': None if records is None else row is not None,
                'decision_status': row.decision_status if row else None,
                'comparison_basis': bases.get(session), 'reason_codes': [reason] if reason else []})
        episodes, active = [], []

        def finish(next_index):
            if not active:
                return
            begin, stop = active[0], active[-1]
            before = observations[begin-1] if begin else None
            after = observations[next_index] if next_index is not None else None
            left_reasons = (['OBSERVATION_BOUNDARY'] if before is None else
                before['reason_codes'] if before['state'] is None else [])
            right_reasons = (['OBSERVATION_BOUNDARY'] if after is None else
                after['reason_codes'] if after['state'] is None else [])
            if before and before['comparison_basis'] is not None and before['comparison_basis'] != observations[begin]['comparison_basis']:
                left_reasons = [*left_reasons, 'COMPARISON_BASIS_BOUNDARY']
            if after and after['comparison_basis'] is not None and after['comparison_basis'] != observations[stop]['comparison_basis']:
                right_reasons = [*right_reasons, 'COMPARISON_BASIS_BOUNDARY']
            episodes.append({'start_session': sessions[begin], 'end_session': sessions[stop],
                'observed_consecutive_selected_sessions': len(active),
                'sessions': [sessions[index] for index in active],
                'left_censored': bool(left_reasons), 'right_censored': bool(right_reasons),
                'left_reason_codes': sorted(set(left_reasons)), 'right_reason_codes': sorted(set(right_reasons)),
                'complete_cycle_certified': False})
            active.clear()

        for index, row in enumerate(observations):
            basis_break = bool(active and row['comparison_basis'] != observations[active[-1]]['comparison_basis'])
            if row['state'] is not True or basis_break:
                finish(index)
            if row['state'] is True:
                active.append(index)
        finish(None)
        tracks.append({'instrument_id': iid, 'observations': observations, 'observed_runs': episodes,
            'observed_longest_consecutive_selected_sessions': (max(
                episode['observed_consecutive_selected_sessions'] for episode in episodes) if episodes
                else None if any(row['state'] is None for row in observations) else 0),
            'observed_maximum_is_complete_duration': False,
            'complete_continuous_eligible_observation_coverage': all(row['state'] is not None for row in observations),
            'missing_snapshot_sessions': [row['session'] for row in observations if 'SNAPSHOT_MISSING' in row['reason_codes']],
            'eligibility_boundary_sessions': [row['session'] for row in observations if 'ELIGIBILITY_BOUNDARY' in row['reason_codes']],
            'unknown_decision_sessions': [row['session'] for row in observations if 'DECISION_UNKNOWN' in row['reason_codes']],
            'interpretation': 'OBSERVED_SELECTED_RUNS_NOT_FULL_SETUP_DURATION_OR_EMOTION_CYCLES'})
    return {'sessions': list(sessions), 'instrument_ids': all_ids, 'tracks': tracks,
            'calendar_role': 'CALLER_DECLARED_EXCHANGE_CALENDAR_NOT_REAUTHENTICATED'}


def run_cohort_participation_trends(snapshots: Sequence[DailySnapshot], *,
                                  calendar: Sequence[date],
                                  fixed_cohorts: Mapping[str, Iterable[str]] | None = None):
    """Explicit standalone research calculation; never a default rule entry."""
    calendar = _calendar(calendar)
    snapshots = _sequence(snapshots, calendar)
    # Freeze generators once; the identical caller cohort is used in every pair.
    cohorts = {label: _ids(ids) for label, ids in (fixed_cohorts or {}).items()}
    for label in cohorts:
        _text(label, 'FIXED_COHORT_LABEL')
        if label == 'common_eligible':
            raise ValueError('COHORT_FIXED_COHORT_LABEL_RESERVED')
    result = {'research_id': RESEARCH_ID, 'feature_version': FEATURE_VERSION,
        'implementation_version': IMPLEMENTATION_VERSION, 'spec_revision': SPEC_REVISION,
        'parameter_status': PARAMETER_STATUS, 'parameter_status_label_zh': PARAMETER_STATUS_LABEL_ZH,
        'market_edge': MARKET_EDGE, 'source_identity': source_identity(),
        'snapshot_declarations': [snapshot_declaration(item) for item in snapshots],
        'snapshot_content_hashes': [snapshot_content_hash(item) for item in snapshots],
        'calendar': list(calendar), 'calendar_content_hash': _hash(calendar),
        'pair_comparisons': [compare_snapshots(a, b, calendar=calendar, fixed_cohorts=cohorts)
                             for a, b in zip(snapshots, snapshots[1:])],
        'continuity': observed_selected_runs(snapshots, calendar=calendar),
        'source_authentication_performed': False, 'selector_run': False,
        'historical_qualification_authenticated': False, 'source_capability_restored': False,
        'official_pit_validation_count': 0, 'certified_emotion_cycle_count': 0,
        'prediction_weight_or_selection_threshold_added': False,
        'default_service_integration': False,
        'selection_definition_mechanical_statistics': [
            'RETENTION_ENTRY_EXIT_AND_JACCARD_DEPEND_ON_FROZEN_SELECTION_DEFINITION',
            'SELECTED_COHORT_AMOUNT_IS_CONDITIONED_ON_SELECTION',
            'OBSERVED_RUN_LENGTH_DEPENDS_ON_SNAPSHOT_COVERAGE_AND_QUALIFICATION_BOUNDARIES'],
        'limitations': ['CALLER_DECLARATIONS_NOT_SOURCE_AUTHORITY',
            'TRADING_VALUE_NOT_NET_CAPITAL_FLOW', 'GAPS_AND_UNKNOWN_CANNOT_ESTABLISH_CONTINUITY',
            'NO_PREDICTIVE_OR_INDEPENDENT_CYCLE_VALIDATION']}
    result['declarative_content_hash'] = _hash(result)
    return result
