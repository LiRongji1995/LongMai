"""Descriptive theme relationship validation, never a selection or flow model.

Authentic native panels and snapshots are the only numeric authority. Closed
turnover shares are diagnostic; trading value does not identify net inflow.
An unauthenticated emotion label cannot establish an emotion cycle.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import math
from pathlib import Path

from . import theme_evolution as theme
from .contracts import digest
from .history_artifacts import digest_json_stream, digest_market_input
from .visibility import EvidenceResolver, clock_visible, stamp

STRATEGY_ID = 'THEME_CYCLE_VALIDATION_R1'
FEATURE_VERSION = 'THEME_EXCLUSIVE_MARKET_RESIDUAL_VALIDATION_V1'
IMPLEMENTATION_VERSION = 'THEME_CYCLE_VALIDATION_R1_EXACT_HELD_OUT_RESIDUALS'
SPEC_REVISION = 'THEME_CYCLE_VALIDATION_R1_SPEC_1'
PARAMETERS = {'training_exchange_sessions': 60, 'minimum_valid_test_sessions': 10,
              'minimum_authenticated_emotion_cycles': 3,
              'parameter_status': 'INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED'}


def source_identity():
    identity = theme.source_identity()
    paths = [Path(__file__), *(Path(__file__).with_name(name) for name in
        ('visibility.py', 'contracts.py', 'history_artifacts.py', 'research_context_artifacts.py',
         'short_horizon_participation.py', 'short_horizon_strength.py')), theme.ROOT / 'config/short_horizon_strength_r2.yaml']
    hashes = {**identity['source_file_hashes'], **{str(path.relative_to(theme.ROOT)):
        hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}}
    return {'strategy_id': STRATEGY_ID, 'feature_version': FEATURE_VERSION,
            'implementation_version': IMPLEMENTATION_VERSION, 'spec_revision': SPEC_REVISION,
            'source_file_hashes': hashes, 'source_hash': digest(hashes),
            'parameters': dict(PARAMETERS), 'parameters_hash': digest(PARAMETERS)}


def _correlation(observations, minimum):
    """Exact centered covariance; only the bounded correlation is displayed."""
    pairs = [(theme.exact(left), theme.exact(right)) for left, right in observations]
    pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
    result = {'status': 'UNKNOWN', 'value': None, 'valid_session_count': len(pairs),
              'minimum_valid_session_count': minimum, 'reason_codes': []}
    if len(pairs) < minimum:
        result['reason_codes'].append('VALID_TEST_SESSION_COVERAGE_BELOW_INITIAL_MINIMUM')
        return result
    mean_a = sum(a for a, _ in pairs) / len(pairs)
    mean_b = sum(b for _, b in pairs) / len(pairs)
    cov = sum((a - mean_a) * (b - mean_b) for a, b in pairs)
    va = sum((a - mean_a) ** 2 for a, _ in pairs)
    vb = sum((b - mean_b) ** 2 for _, b in pairs)
    result.update(centered_cross_product=theme.encoded(cov),
                  left_centered_sum_squares=theme.encoded(va), right_centered_sum_squares=theme.encoded(vb))
    if not va or not vb:
        result['reason_codes'].append('CONSTANT_SERIES_CORRELATION_UNDEFINED')
        return result
    result.update(status='AVAILABLE', value=math.sqrt(float(cov * cov / (va * vb))) * (-1 if cov < 0 else 1))
    return result


def _ols_residual(prior, current):
    """Fit on exactly sixty preceding observations; current is never fitted."""
    count = PARAMETERS['training_exchange_sessions']
    if len(prior) != count or current[0] is None or current[1] is None:
        return {'status': 'UNKNOWN', 'residual': None, 'reason_codes': ['PRIOR60_TRAINING_OR_CURRENT_EVIDENCE_UNAVAILABLE']}
    values = [(theme.exact(x), theme.exact(y)) for x, y in prior]
    x_now, y_now = map(theme.exact, current)
    if x_now is None or y_now is None or any(x is None or y is None for x, y in values):
        return {'status': 'UNKNOWN', 'residual': None, 'reason_codes': ['PRIOR60_TRAINING_OR_CURRENT_EVIDENCE_UNAVAILABLE']}
    x_mean = sum(x for x, _ in values) / count
    y_mean = sum(y for _, y in values) / count
    variance = sum((x - x_mean) ** 2 for x, _ in values)
    if not variance:
        return {'status': 'UNKNOWN', 'residual': None, 'reason_codes': ['PRIOR60_MARKET_FACTOR_VARIANCE_ZERO']}
    beta = sum((x - x_mean) * (y - y_mean) for x, y in values) / variance
    intercept = y_mean - beta * x_mean
    return {'status': 'AVAILABLE', 'beta': beta, 'intercept': intercept,
            'residual': y_now - intercept - beta * x_now,
            'training_observation_count': count, 'current_in_training': False, 'reason_codes': []}


def _registry(registry, analysis_at, validator, *, bundle, cutoff):
    registry = deepcopy(list(registry or ()))
    seen, ranges = set(), []
    for record in registry:
        if not isinstance(record, dict) or not record.get('cycle_id') or record['cycle_id'] in seen:
            raise ValueError('THEME_VALIDATION_CYCLE_REGISTRY_ID_INVALID_OR_DUPLICATE')
        seen.add(record['cycle_id'])
        start, end = str(record.get('start_session')), str(record.get('end_session'))
        try:
            from datetime import date
            date.fromisoformat(start)
            date.fromisoformat(end)
            registered = stamp(record['registered_at'])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError('THEME_VALIDATION_CYCLE_REGISTRY_DATE_OR_REGISTRATION_REQUIRED') from error
        if start > end or registered > analysis_at or not record.get('classification_basis'):
            raise ValueError('THEME_VALIDATION_CYCLE_REGISTRY_SCOPE_OR_BASIS_INVALID')
        if record.get('interval_kind') not in ('CALENDAR_WINDOW', 'MARKET_EMOTION_CYCLE'):
            raise ValueError('THEME_VALIDATION_CYCLE_REGISTRY_KIND_REQUIRED')
        if record['interval_kind'] == 'MARKET_EMOTION_CYCLE' and not record.get('emotion_label'):
            raise ValueError('THEME_VALIDATION_CYCLE_REGISTRY_EMOTION_LABEL_REQUIRED')
        ranges.append((start, end))
    ordered = sorted(ranges)
    if any(left[1] >= right[0] for left, right in zip(ordered, ordered[1:])):
        raise ValueError('THEME_VALIDATION_CYCLE_REGISTRY_OVERLAPPING_INTERVALS')
    authenticated = callable(validator)
    if authenticated:
        # Like the native source adapter, the verifier is a trusted capability
        # that validates the independent label evidence or raises. JSON flags
        # such as ``verified=True`` are never consulted.
        outcome = validator(registry, bundle=bundle, cutoff=cutoff, analysis_at=analysis_at)
        if outcome is False:
            raise ValueError('THEME_VALIDATION_CYCLE_REGISTRY_AUTHENTICATION_FAILED')
    return registry, authenticated


def _validate_parent(bundle, cutoff, pack, result):
    if not isinstance(result, dict):
        raise ValueError('THEME_VALIDATION_NATIVE_THEME_RESULT_REQUIRED')
    content = {key: value for key, value in result.items() if key != 'context_content_hash'}
    identity = theme.source_identity()
    if (digest_json_stream(content) != result.get('context_content_hash')
            or result.get('strategy_id') != theme.STRATEGY_ID
            or result.get('source_hash') != identity['source_hash']
            or result.get('source_file_hashes') != identity['source_file_hashes']
            or result.get('config_hash') != identity['config_hash']
            or result.get('feature_version') != identity['feature_version']
            or result.get('source_input_hash') != digest_market_input(bundle)
            or str(result.get('observation_date')) != str(bundle.observation_date)
            or stamp(result.get('decision_cutoff')) != cutoff
            or result.get('market_pack_hash') != digest_json_stream(pack)
            or result.get('formal_focus') is not False or result.get('core_pass') is not False
            or result.get('official_pit_validation_countable') is not False):
        raise ValueError('THEME_VALIDATION_NATIVE_PARENT_BINDING_MISMATCH')
    if result.get('membership_mode') not in ('DATED_ONLY', 'CURRENT_COHORT_BACKVIEW'):
        raise ValueError('THEME_VALIDATION_PARENT_MEMBERSHIP_MODE_UNSUPPORTED')


def _validated_rows(bundle, cutoff, pack, result, resolver, markets):
    snapshots = tuple(bundle.themes) + tuple(pack.themes)
    by_theme = defaultdict(list)
    for snapshot in snapshots:
        by_theme[snapshot.theme_id].append(snapshot)
    rows, seen = {}, set()
    for item in result.get('items', ()):
        key = (item.get('theme_id'), str(item.get('session')))
        if not key[0] or key in seen or key[1] not in result.get('computed_exchange_sessions', ()):
            raise ValueError('THEME_VALIDATION_PARENT_ROW_IDENTITY_OR_SESSION_INVALID')
        seen.add(key)
        if result['membership_mode'] == 'CURRENT_COHORT_BACKVIEW':
            choices = [row for row in by_theme[key[0]] if row.theme_type in theme.CONFIG['allowed_theme_types']
                and row.effective_from <= cutoff.astimezone(theme.SH).date()
                and clock_visible(row, cutoff)
                and all(clock_visible(resolver.receipts.get(ref), cutoff) for ref in row.source_refs)
                and resolver.resolve(row, session=row.effective_from, require_complete=True).allowed]
        else:
            from datetime import date
            day = date.fromisoformat(key[1])
            choices = [row for row in by_theme[key[0]] if row.theme_type in theme.CONFIG['allowed_theme_types']
                and row.effective_from <= day
                and (row.effective_to is None or day < row.effective_to)
                and resolver.resolve(row, session=day, require_complete=True).allowed]
        snapshot = theme._select(choices)
        members = sorted(set(snapshot.member_ids)) if snapshot is not None else []
        market = markets.get(key[1])
        dated_eligibility = (result['membership_mode'] == 'CURRENT_COHORT_BACKVIEW'
            and market is not None and market['status'] == 'COMPLETE' and bool(members)
            and all(theme._a_common_id(iid) for iid in members))
        eligible = sorted(set(members) & market['expected_ids']) if dated_eligibility else members
        absent = sorted(set(members) - market['expected_ids']) if dated_eligibility else None
        if (item.get('member_ids') != members or item.get('membership_count') != len(members)
                or item.get('membership_mode') != result['membership_mode']
                or item.get('historical_membership_verified') is not (snapshot is not None and result['membership_mode'] == 'DATED_ONLY')
                or item.get('dated_eligibility_verified') is not dated_eligibility
                or item.get('eligible_member_ids') != eligible
                or item.get('absent_from_dated_market_ids') != absent):
            raise ValueError('THEME_VALIDATION_PARENT_DATED_MEMBERSHIP_MISMATCH')
        rows[key] = item
    return rows


def _group(members, market, returns):
    if not members or market is None or market['status'] != 'COMPLETE' or not members.issubset(market['expected_ids']):
        return None, None
    active = sorted(members & market['trading_ids'])
    amounts = [market['amounts'].get(iid) for iid in active]
    amount = sum(amounts, Fraction(0)) if all(value is not None for value in amounts) else None
    values = [returns.get(iid) for iid in active]
    price = sum(values, Fraction(0)) / len(values) if values and all(value is not None for value in values) else None
    return amount, price


def _daily_pair(left_id, right_id, sessions, rows, markets, returns, *, retain_complement_ids=True,
                parent_fsm_authenticated=False):
    observations = []
    for session in sessions:
        left, right = rows.get((left_id, session)), rows.get((right_id, session))
        market = markets.get(session)
        reasons = []
        a, b = (set((row or {}).get('eligible_member_ids', (row or {}).get('member_ids', ()))) for row in (left, right))
        overlap, a_only, b_only = a & b, a - b, b - a
        complement = market['expected_ids'] - (a | b) if market else set()
        if left is None or right is None or not a or not b:
            reasons.append('THEME_DATED_OR_CURRENT_COHORT_MEMBERSHIP_UNAVAILABLE')
        if not a_only or not b_only:
            reasons.append('PAIR_EXCLUSIVE_COHORT_EMPTY')
        if not complement:
            reasons.append('PAIR_MARKET_COMPLEMENT_EMPTY')
        if market is None or market['status'] != 'COMPLETE':
            reasons.append('SAME_DATE_COMPLETE_MARKET_PANEL_UNAVAILABLE')
        if market is not None and not (a | b).issubset(market['expected_ids']):
            reasons.append('THEME_MEMBER_OUTSIDE_AUTHENTICATED_MARKET_SCOPE')
        member_returns = returns.get(session, {})
        av, ar = _group(a_only, market, member_returns)
        bv, br = _group(b_only, market, member_returns)
        mv, mr = _group(complement, market, member_returns)
        total = (market or {}).get('amount_cny')
        if total is None or total <= 0:
            reasons.append('SAME_DATE_MARKET_TRADE_VALUE_DENOMINATOR_UNKNOWN')
        basis = (market or {}).get('return_basis') or 'SAME_COMPARABLE_BASIS_DAILY_RETURN'
        if reasons:
            av = ar = bv = br = mv = mr = None
        if ar is None or br is None or mr is None:
            reasons.append('PAIR_PRICE_RETURN_OR_BASIS_UNAVAILABLE')
        observations.append({'session': session,
            'left_exclusive_member_ids': sorted(a_only), 'right_exclusive_member_ids': sorted(b_only),
            'overlap_member_ids': sorted(overlap),
            **({'market_complement_member_ids': sorted(complement)} if retain_complement_ids else {}),
            'market_scope_membership_hash': digest(sorted(market['expected_ids'])) if market is not None else None,
            'market_scope_member_count': len(market['expected_ids']) if market is not None else None,
            'market_complement_membership_hash': digest(sorted(complement)) if market is not None else None,
            'market_complement_member_count': len(complement) if market is not None else None,
            'market_complement_definition': 'SAME_DATE_MARKET_SCOPE_MINUS_LEFT_OR_RIGHT_ELIGIBLE_MEMBERS',
            'left_absent_from_dated_market_ids': (left or {}).get('absent_from_dated_market_ids'),
            'right_absent_from_dated_market_ids': (right or {}).get('absent_from_dated_market_ids'),
            'left_nontrading_excluded_member_ids': sorted(a_only & (market or {}).get('nontrading_ids', set())),
            'right_nontrading_excluded_member_ids': sorted(b_only & (market or {}).get('nontrading_ids', set())),
            'left_amount_cny': av, 'right_amount_cny': bv, 'market_complement_amount_cny': mv,
            'market_amount_cny': total, 'left_return_1d': ar, 'right_return_1d': br,
            'market_complement_return_1d': mr, 'return_basis': basis,
            'left_share': av / total if av is not None and total is not None and total > 0 else None,
            'right_share': bv / total if bv is not None and total is not None and total > 0 else None,
            'left_unit_price_rs': ar - mr if ar is not None and mr is not None else None,
            'right_unit_price_rs': br - mr if br is not None and mr is not None else None,
            'left_fsm_stage': (left or {}).get('stage') if parent_fsm_authenticated else None,
            'right_fsm_stage': (right or {}).get('stage') if parent_fsm_authenticated else None,
            'source_refs': sorted(set((left or {}).get('source_refs', ())) | set((right or {}).get('source_refs', ()))),
            'reason_codes': sorted(set(reasons))})
    count = PARAMETERS['training_exchange_sessions']
    for index, row in enumerate(observations):
        previous = observations[index - 1] if index else None
        stable_previous = bool(previous and all(row[key] == previous[key] for key in
            ('left_exclusive_member_ids', 'right_exclusive_member_ids', 'return_basis')))
        row['market_complement_membership_changed'] = (
            row['market_complement_membership_hash'] != previous['market_complement_membership_hash']
            if previous and row['market_scope_membership_hash'] is not None
            and previous['market_scope_membership_hash'] is not None else None)
        for field in ('left_amount_cny', 'right_amount_cny', 'market_complement_amount_cny'):
            current, prior = row[field], (previous or {}).get(field)
            row[field + '_change'] = current - prior if stable_previous and current is not None and prior is not None else None
        share_changes = [(row.get(key), (previous or {}).get(key)) for key in ('left_share', 'right_share')]
        row['closed_share_opposite_without_absolute_amount_opposition'] = (
            (share_changes[0][0] - share_changes[0][1]) * (share_changes[1][0] - share_changes[1][1]) < 0
            and row['left_amount_cny_change'] * row['right_amount_cny_change'] >= 0
            if stable_previous and all(a is not None and b is not None for a, b in share_changes)
            and row['left_amount_cny_change'] is not None and row['right_amount_cny_change'] is not None else None)
        prior = observations[max(0, index-count):index]
        stable_training = len(prior) == count and all(all(item[key] == row[key] for key in
            ('left_exclusive_member_ids', 'right_exclusive_member_ids', 'return_basis')) for item in prior)
        row['training_sessions'] = [item['session'] for item in prior]
        for side in ('left', 'right'):
            for kind, xkey, ykey in (
                ('amount', 'market_complement_amount_cny_change', side + '_amount_cny_change'),
                ('price', 'market_complement_return_1d', side + '_return_1d')):
                fit = _ols_residual([(item[xkey], item[ykey]) for item in prior], (row[xkey], row[ykey])) if stable_training else {
                    'status': 'UNKNOWN', 'residual': None, 'reason_codes': ['PRIOR60_STABLE_EXCLUSIVE_COHORT_OR_BASIS_UNAVAILABLE']}
                row[side + '_' + kind + '_market_residual'] = fit
    return observations


def _intervals(observations, registry, authenticated, calendar):
    intervals = []
    minimum = PARAMETERS['minimum_valid_test_sessions']
    metrics = {
        'exclusive_absolute_amount_level_correlation': ('left_amount_cny', 'right_amount_cny'),
        'exclusive_absolute_amount_change_correlation': ('left_amount_cny_change', 'right_amount_cny_change'),
        'exclusive_raw_return_correlation': ('left_return_1d', 'right_return_1d'),
        'exclusive_price_unit_rs_correlation': ('left_unit_price_rs', 'right_unit_price_rs'),
        'closed_share_correlation_diagnostic_only': ('left_share', 'right_share')}
    for record in registry:
        selected = [row for row in observations if record['start_session'] <= row['session'] <= record['end_session']]
        expected = [day for day in calendar if record['start_session'] <= day <= record['end_session']]
        scope_complete = bool(expected) and [row['session'] for row in selected] == expected
        correlations = {name: _correlation([(row[a], row[b]) for row in selected], minimum)
                        for name, (a, b) in metrics.items()}
        for kind in ('amount', 'price'):
            correlations['exclusive_' + kind + '_market_residual_correlation'] = _correlation([
                (row['left_' + kind + '_market_residual']['residual'], row['right_' + kind + '_market_residual']['residual'])
                for row in selected], minimum)
        eligible = scope_complete and all(correlations['exclusive_' + kind + '_market_residual_correlation']['status'] == 'AVAILABLE'
                       and correlations['exclusive_' + kind + '_market_residual_correlation']['valid_session_count'] == len(expected)
                       for kind in ('amount', 'price'))
        emotion = authenticated and record['interval_kind'] == 'MARKET_EMOTION_CYCLE'
        share_diagnostics = [row['closed_share_opposite_without_absolute_amount_opposition'] for row in selected]
        verified_share_diagnostics = [value for value in share_diagnostics if value is not None]
        excluded = {name: [row['session'] for row in selected if row[a] is None or row[b] is None]
            for name, (a, b) in metrics.items()}
        for kind in ('amount', 'price'):
            excluded['exclusive_' + kind + '_market_residual_correlation'] = [row['session'] for row in selected
                if any(row[side + '_' + kind + '_market_residual']['residual'] is None for side in ('left', 'right'))]
        intervals.append({**record, 'observed_exchange_session_count': len(selected),
            'expected_exchange_session_count': len(expected), 'interval_calendar_scope_complete': scope_complete,
            'statistics': correlations,
            'statistics_coverage_status': 'AVAILABLE' if eligible else 'UNKNOWN',
            'independent_emotion_label_authentication_status': 'AUTHENTICATED_BY_NATIVE_CAPABILITY' if emotion else 'UNKNOWN',
            'countable_emotion_cycle': bool(eligible and emotion),
            'share_closure_diagnostic_session_count': sum(value is True for value in verified_share_diagnostics)
                if verified_share_diagnostics else None,
            'share_closure_diagnostic_verified_session_count': len(verified_share_diagnostics),
            'share_closure_diagnostic_unknown_session_count': len(share_diagnostics) - len(verified_share_diagnostics),
            'excluded_sessions_by_axis': excluded,
            'reason_codes': ([] if eligible else ['INTERVAL_PRICE_OR_AMOUNT_RESIDUAL_COVERAGE_UNAVAILABLE'])
                + ([] if emotion else ['CALENDAR_INTERVAL_IS_NOT_AUTHENTICATED_EMOTION_CYCLE'])})
    return intervals


def run_theme_cycle_validation(bundle, theme_result, *, cutoff, market_pack, _market_pack_validator,
                               cycle_registry=(), analysis_at=None, _cycle_registry_validator=None,
                               theme_pairs=None, pair_scope_provenance=None, eligible_theme_ids=None,
                               background_theme_ids=(), _theme_result_validator=None):
    """Validate descriptive relationships on authenticated data, without DBs."""
    cutoff = stamp(cutoff)
    analysis_at = stamp(analysis_at) if analysis_at is not None else datetime.now(timezone.utc)
    identity = source_identity()
    if analysis_at < cutoff:
        raise ValueError('THEME_VALIDATION_ANALYSIS_BEFORE_SOURCE_CUTOFF')
    pack = theme._validate_pack(bundle, cutoff, market_pack, _market_pack_validator)
    _validate_parent(bundle, cutoff, market_pack, theme_result)
    parent_fsm_authenticated = callable(_theme_result_validator)
    if parent_fsm_authenticated and _theme_result_validator(theme_result, bundle=bundle,
            cutoff=cutoff, market_pack=market_pack) is False:
        raise ValueError('THEME_VALIDATION_NATIVE_PARENT_AUTHENTICATION_FAILED')
    registry, authenticated = _registry(cycle_registry, analysis_at, _cycle_registry_validator,
                                        bundle=bundle, cutoff=cutoff)
    sessions = list(theme_result['computed_exchange_sessions'])
    calendar = [str(day) for day in bundle.sessions if day <= bundle.observation_date]
    if not sessions or sessions != [day for day in calendar if day in set(sessions)]:
        raise ValueError('THEME_VALIDATION_PARENT_CALENDAR_INVALID')
    # Missing exchange sessions cannot be compressed out of a training window.
    if sessions != calendar[calendar.index(sessions[0]):calendar.index(sessions[-1])+1]:
        raise ValueError('THEME_VALIDATION_PARENT_CALENDAR_HAS_GAPS')
    resolver = EvidenceResolver(tuple(bundle.receipts) + tuple(pack.receipts), cutoff=cutoff,
        coverage_type=bundle.coverage_type, sessions=calendar, observation_date=bundle.observation_date)
    if resolver.conflicts:
        raise ValueError('THEME_VALIDATION_SOURCE_RECEIPT_CONFLICT')
    markets = {str(panel.session): theme._session_market(panel, resolver, calendar) for panel in pack.sessions}
    rows = _validated_rows(bundle, cutoff, pack, theme_result, resolver, markets)
    grouped = defaultdict(list)
    for row in bundle.bars:
        grouped[(row.instrument_id, str(row.session))].append(row)
    for market in markets.values():
        for iid, row in market['selected'].items():
            if row is not None:
                grouped[(iid, market['session'])].append(row)
    bars = {key: theme._select([row for row in choices if resolver.resolve(row, instrument_id=key[0],
        session=key[1], require_session=True, require_complete=True).allowed]) for key, choices in grouped.items()}
    returns = {}
    for index, day in enumerate(sessions):
        market = markets.get(day)
        prior = sessions[index - 1] if index else None
        returns[day] = {iid: theme._return1(iid, day, prior, bars, resolver, market=market)
            for iid in market['trading_ids']} if market and market['status'] == 'COMPLETE' else {}
    pairs = []
    ids = sorted({key[0] for key in rows})
    def scope_ids(values, label):
        values = list(values)
        if (not all(isinstance(item, str) and item for item in values)
                or len(values) != len(set(values)) or not set(values).issubset(ids)):
            raise ValueError('THEME_VALIDATION_' + label + '_SCOPE_INVALID')
        return set(values)
    eligible = scope_ids(eligible_theme_ids, 'ELIGIBLE_THEME') if eligible_theme_ids is not None else None
    background = scope_ids(background_theme_ids, 'BACKGROUND_THEME')
    if background and (eligible is None or eligible & background):
        raise ValueError('THEME_VALIDATION_BACKGROUND_REQUIRES_DISTINCT_EXPLICIT_ELIGIBLE_SCOPE')
    requested = list(theme_pairs or ())
    pair_scope, seen_pairs = [], set()
    for pair in requested:
        if (not isinstance(pair, (list, tuple)) or len(pair) != 2
                or not all(isinstance(item, str) and item for item in pair)
                or pair[0] == pair[1] or not set(pair).issubset(ids)):
            raise ValueError('THEME_VALIDATION_PAIR_SCOPE_INVALID')
        key = tuple(sorted(pair))
        if eligible is not None and (not set(key).issubset(eligible | background) or not set(key) & eligible):
            raise ValueError('THEME_VALIDATION_PAIR_OUTSIDE_ELIGIBLE_OR_NECESSARY_BACKGROUND_SCOPE')
        if key in seen_pairs:
            raise ValueError('THEME_VALIDATION_PAIR_SCOPE_DUPLICATE')
        seen_pairs.add(key)
        pair_scope.append(key)
    for left, right in sorted(pair_scope):
        daily = _daily_pair(left, right, sessions, rows, markets, returns, retain_complement_ids=False,
            parent_fsm_authenticated=parent_fsm_authenticated)
        intervals = _intervals(daily, registry, authenticated, calendar)
        valid = [row for row in intervals if row['countable_emotion_cycle']]
        reasons = [] if len(valid) >= PARAMETERS['minimum_authenticated_emotion_cycles'] else ['FEWER_THAN_THREE_VALID_AUTHENTICATED_EMOTION_CYCLES']
        signs = {kind: [math.copysign(1, row['statistics']['exclusive_' + kind + '_market_residual_correlation']['value'])
                       if row['statistics']['exclusive_' + kind + '_market_residual_correlation']['value'] != 0 else 0
                       for row in valid] for kind in ('amount', 'price')}
        pairs.append({'left_theme_id': left, 'right_theme_id': right,
            'pair_display_role': 'EXPLICIT_RESEARCH_PAIR_QUALIFICATION_UNAUTHENTICATED' if eligible is None else
                'PRIMARY_CANDIDATE_THEME_PAIR' if set((left, right)).issubset(eligible) else
                'PRIMARY_THEME_WITH_EXPLICIT_NECESSARY_ROTATION_BACKGROUND',
            'status': 'DESCRIPTIVE_CYCLE_COMPARISON_AVAILABLE' if not reasons else 'UNKNOWN',
            'valid_authenticated_emotion_cycle_count': len(valid),
            'per_cycle_residual_correlation_signs': signs,
            'same_sign_across_observed_cycles': {key: len(set(values)) == 1 if len(values) >= 3 else None for key, values in signs.items()},
            'intervals': intervals, 'observations': theme.encoded(daily), 'reason_codes': reasons,
            'price_association_does_not_identify_fund_flow': True,
            'share_closure_used_for_relationship_conclusion': False,
            'causal_interpretation': 'NOT_ESTABLISHED', 'statistical_stability': 'NOT_ESTABLISHED'})
    backview = theme_result['membership_mode'] == 'CURRENT_COHORT_BACKVIEW'
    result = {**identity, 'analysis_at': analysis_at.isoformat(),
        'decision_cutoff': cutoff.isoformat(), 'source_input_hash': digest_market_input(bundle),
        'theme_result_content_hash': theme_result['context_content_hash'], 'market_pack_hash': digest_json_stream(market_pack),
        'cycle_registry': registry, 'cycle_registry_hash': digest_json_stream(registry),
        'registration_claim_scope': 'BEFORE_THIS_ANALYSIS_NOT_CLAIMED_BEFORE_HISTORICAL_INTERVALS',
        'computed_exchange_sessions': sessions, 'pair_count': len(pairs), 'pairs': pairs,
        'pair_scope': [list(pair) for pair in sorted(pair_scope)], 'pair_scope_hash': digest(sorted(pair_scope)),
        'pair_scope_role': 'EXPLICIT_RESEARCH_PAIR_SCOPE_NOT_SUPPLIED' if theme_pairs is None else
            'CALLER_EXPLICIT_RESEARCH_PAIR_SCOPE_NOT_FROM_RESULT_DIRECTION',
        'pair_scope_provenance': deepcopy(dict(pair_scope_provenance or {})),
        'pair_scope_provenance_hash': digest_json_stream(pair_scope_provenance or {}),
        'eligible_theme_ids': sorted(eligible) if eligible is not None else None,
        'necessary_background_theme_ids': sorted(background),
        'theme_qualification_scope_hash': digest({'eligible_theme_ids': sorted(eligible) if eligible is not None else None,
            'necessary_background_theme_ids': sorted(background)}),
        'theme_qualification_scope_role': 'CALLER_EXPLICIT_RESEARCH_DISPLAY_SCOPE_NOT_HISTORICAL_QUALIFICATION_PROOF',
        'historical_mainline_eligibility_status': 'UNKNOWN',
        'historical_mainline_eligibility_reason_codes': ['NO_AUTHENTICATED_DATED_MAINLINE_RULE_AND_QUALIFICATION_EVIDENCE'],
        'parent_fsm_authentication_status': 'AUTHENTICATED_BY_NATIVE_CAPABILITY' if parent_fsm_authenticated else 'UNKNOWN',
        'parent_fsm_display_policy': 'AUTHENTICATED_NATIVE_STAGE_DISPLAY_ONLY_NOT_REGRESSION_INPUT'
            if parent_fsm_authenticated else 'UNAUTHENTICATED_PARENT_STAGE_LABELS_NOT_DISPLAYED',
        'sample_selection_bias': 'CURRENT_SELECTED_CANDIDATE_AND_ACTIVE_THEME_SCOPE_SURVIVORSHIP_SELECTION_BIAS'
            if (pair_scope_provenance or {}).get('scope_basis') == 'CURRENT_SELECTED_CANDIDATES_AND_ACTIVE_THEMES'
            else 'CURRENT_COHORT_RETROSPECTIVE_MEMBERSHIP_SELECTION_BIAS' if backview else
            'EXPLICIT_PAIR_SCOPE_SELECTION_MECHANISM_NOT_AUTHENTICATED' if theme_pairs is not None else
            'DATED_SOURCE_QUALIFIED_COHORT_NOT_INDEPENDENT_PREDICTION_VALIDATION',
        'historical_opportunity_set_claimed': False,
        'multiple_comparison_adjustment': 'NOT_PERFORMED_NO_SIGNIFICANCE_OR_EDGE_CLAIM',
        'data_role': 'CURRENT_COHORT_RETROSPECTIVE' if backview else 'DATED_COHORT_SOURCE_QUALIFIED_DESCRIPTIVE_RESEARCH',
        'membership_mode': theme_result['membership_mode'], 'official_pit_validation_count': 0,
        'status': 'DESCRIPTIVE_COMPARISON_AVAILABLE' if pairs and all(row['status'] != 'UNKNOWN' for row in pairs) else 'UNKNOWN',
        'formal_focus': False, 'core_pass': False, 'selection_role': 'RESEARCH_PANEL_VALIDATION_NOT_SELECTION',
        'fund_flow_role': 'TRADE_VALUE_NOT_NET_INFLOW', 'causal_interpretation': 'NOT_ESTABLISHED',
        'statistical_stability': 'NOT_ESTABLISHED', 'market_edge': 'NOT_ESTABLISHED',
        'missing_policy': 'UNKNOWN_NOT_ZERO_NO_SESSION_COMPRESSION',
        'overlap_policy': 'DATED_EXCLUSIVE_MEMBERS_AND_EX_UNION_MARKET_COMPLEMENT',
        'regression_policy': 'EXACT_PRECEDING60_OLS_FIRST_DIFFERENCE_AMOUNT_AND_DAILY_RETURN_CURRENT_HELD_OUT',
        'market_factor_membership_policy': 'COMPLETE_SAME_DATE_DYNAMIC_MARKET_COMPLEMENT_CHANGES_DISCLOSED',
        'reason_codes': ['NO_EXPLICIT_RESEARCH_PAIR_SCOPE_AVAILABLE'] if not pairs else []}
    if source_identity() != identity:
        raise ValueError('THEME_VALIDATION_IMPLEMENTATION_CHANGED_DURING_EVALUATION')
    result['report_content_hash'] = digest_json_stream(result)
    return result
