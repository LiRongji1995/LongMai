"""Bounded execution of the frozen, exact descriptive cycle mathematics.

Only authenticated native streaming parents and an independently authenticated
current display scope are accepted. Full market price objects live for at most
the current and preceding exchange session; missing calendar dates stay present.
This producer neither reconstructs historical mainline eligibility nor establishes
emotion cycles, fund flows, causal effects or investment edge.
"""
from __future__ import annotations

from collections import defaultdict, deque
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
from types import SimpleNamespace

from . import theme_cycle_validation as legacy
from . import theme_evolution as theme
from .contracts import digest
from .evidence_audit_spool import SpoolingEvidenceResolver
from .history_artifacts import digest_json_stream, digest_market_input
from .theme_market_stream import SourceMarketStream
from .visibility import stamp

STRATEGY_ID = 'THEME_CYCLE_STREAMING_R1'
SCHEMA_VERSION = 'AUTHENTICATED_THEME_CYCLE_STREAMING_REPORT_V1'
FEATURE_VERSION = 'THEME_EXCLUSIVE_MARKET_RESIDUAL_STREAMED_EXACT_V1'
IMPLEMENTATION_VERSION = 'THEME_CYCLE_STREAMING_R1_BOUNDED_DAILY_PRICE_OBJECTS_V1'
SPEC_REVISION = 'THEME_CYCLE_STREAMING_R1_SPEC_1'
PARAMETERS = dict(legacy.PARAMETERS)


def source_identity():
    """Bind the frozen numeric implementation and actual streaming sources."""
    from . import theme_evolution_streaming as producer
    hashes = {**legacy.source_identity()['source_file_hashes'],
              **producer.source_identity()['source_file_hashes']}
    for name in ('theme_cycle_streaming.py', 'theme_market_stream.py', 'evidence_audit_spool.py'):
        path = Path(__file__).with_name(name)
        hashes[str(path.relative_to(theme.ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {'strategy_id': STRATEGY_ID, 'schema_version': SCHEMA_VERSION,
            'feature_version': FEATURE_VERSION, 'implementation_version': IMPLEMENTATION_VERSION,
            'spec_revision': SPEC_REVISION, 'source_file_hashes': hashes, 'source_hash': digest(hashes),
            'numeric_rule_source_identity': legacy.source_identity(),
            'parameters': dict(PARAMETERS), 'parameters_hash': digest(PARAMETERS)}


def _validate_parent(bundle, cutoff, stream, result, validator):
    from . import theme_evolution_streaming as producer
    if not callable(validator):
        raise ValueError('THEME_CYCLE_STREAMING_FRESH_NATIVE_PARENT_CAPABILITY_REQUIRED')
    if not isinstance(result, dict):
        raise ValueError('THEME_CYCLE_STREAMING_NATIVE_PARENT_REQUIRED')
    identity = producer.source_identity()
    if (digest_json_stream({key: value for key, value in result.items()
                            if key != 'context_content_hash'}) != result.get('context_content_hash')
            or result.get('strategy_id') != producer.STRATEGY_ID
            or result.get('schema_version') != producer.SCHEMA_VERSION
            or any(result.get(key) != identity.get(key) for key in
                   ('feature_version', 'implementation_version', 'spec_revision',
                    'source_hash', 'source_file_hashes', 'config_hash'))
            or result.get('numeric_rule_source_identity') != theme.source_identity()
            or result.get('source_input_hash') != digest_market_input(bundle)
            or str(result.get('observation_date')) != str(bundle.observation_date)
            or stamp(result.get('decision_cutoff')) != cutoff
            or result.get('coverage_type') != bundle.coverage_type
            or result.get('market_stream_descriptor_hash') != stream.descriptor_hash
            or result.get('market_stream_descriptor') != stream.descriptor
            or result.get('market_pack_hash') is not None
            or result.get('formal_focus') is not False or result.get('core_pass') is not False
            or result.get('official_pit_validation_countable') is not False):
        raise ValueError('THEME_CYCLE_STREAMING_NATIVE_PARENT_BINDING_MISMATCH')
    if result.get('membership_mode') not in ('DATED_ONLY', 'CURRENT_COHORT_BACKVIEW'):
        raise ValueError('THEME_CYCLE_STREAMING_PARENT_MEMBERSHIP_MODE_UNSUPPORTED')
    # This external capability binds the live producer object and archived
    # bytes, not a JSON authentication flag or a self-computed content hash.
    if validator(result, bundle=bundle, cutoff=cutoff, market_stream=stream) is False:
        raise ValueError('THEME_CYCLE_STREAMING_NATIVE_PARENT_AUTHENTICATION_FAILED')
    sessions = list(result.get('computed_exchange_sessions', ()))
    calendar = list(stream.calendar)
    if not sessions or sessions != [day for day in calendar if day in set(sessions)]:
        raise ValueError('THEME_CYCLE_STREAMING_PARENT_CALENDAR_INVALID')
    if sessions != calendar[calendar.index(sessions[0]):calendar.index(sessions[-1]) + 1]:
        raise ValueError('THEME_CYCLE_STREAMING_PARENT_CALENDAR_HAS_GAPS')
    by_day, seen = defaultdict(list), set()
    for row in result.get('items', ()):
        key = (row.get('theme_id'), str(row.get('session')))
        if not key[0] or key in seen or key[1] not in sessions:
            raise ValueError('THEME_CYCLE_STREAMING_PARENT_ROW_IDENTITY_INVALID')
        seen.add(key)
        by_day[key[1]].append(row)
    return sessions, by_day, sorted({key[0] for key in seen})


def _scope(ids, pairs, eligible, background, provenance, validator, **binding):
    if not callable(validator):
        raise ValueError('THEME_CYCLE_STREAMING_CURRENT_MAINLINE_SCOPE_CAPABILITY_REQUIRED')
    def checked(values, label):
        values = list(values or ())
        if (not all(isinstance(value, str) and value for value in values)
                or len(values) != len(set(values)) or not set(values).issubset(ids)):
            raise ValueError('THEME_CYCLE_STREAMING_' + label + '_SCOPE_INVALID')
        return set(values)
    eligible = checked(eligible, 'ELIGIBLE_THEME')
    background = checked(background, 'BACKGROUND_THEME')
    if not eligible or eligible & background:
        raise ValueError('THEME_CYCLE_STREAMING_DISTINCT_CURRENT_MAINLINE_SCOPE_REQUIRED')
    requested, seen = [], set()
    for pair in pairs or ():
        if (not isinstance(pair, (list, tuple)) or len(pair) != 2
                or not all(isinstance(value, str) and value for value in pair)
                or pair[0] == pair[1] or not set(pair).issubset(ids)):
            raise ValueError('THEME_CYCLE_STREAMING_PAIR_SCOPE_INVALID')
        key = tuple(sorted(pair))
        if not set(key).issubset(eligible | background) or not set(key) & eligible:
            raise ValueError('THEME_CYCLE_STREAMING_PAIR_OUTSIDE_AUTHENTICATED_SCOPE')
        if key in seen:
            raise ValueError('THEME_CYCLE_STREAMING_PAIR_SCOPE_DUPLICATE')
        seen.add(key)
        requested.append(key)
    if not requested:
        raise ValueError('THEME_CYCLE_STREAMING_EXPLICIT_PAIR_SCOPE_REQUIRED')
    scope = {'eligible_theme_ids': sorted(eligible), 'background_theme_ids': sorted(background),
             'theme_pairs': [list(pair) for pair in sorted(requested)],
             'pair_scope_provenance': deepcopy(dict(provenance or {}))}
    if validator(scope, **binding) is False:
        raise ValueError('THEME_CYCLE_STREAMING_CURRENT_MAINLINE_SCOPE_AUTHENTICATION_FAILED')
    return sorted(requested), eligible, background, scope


def _window_fields(row, prior):
    """The original prior-60 formula, with only compact pair scalars retained."""
    previous = prior[-1] if prior else None
    stable_previous = bool(previous and all(row[key] == previous[key] for key in
        ('left_exclusive_member_ids', 'right_exclusive_member_ids', 'return_basis')))
    row['market_complement_membership_changed'] = (
        row['market_complement_membership_hash'] != previous['market_complement_membership_hash']
        if previous and row['market_scope_membership_hash'] is not None
        and previous['market_scope_membership_hash'] is not None else None)
    for field in ('left_amount_cny', 'right_amount_cny', 'market_complement_amount_cny'):
        current, old = row[field], (previous or {}).get(field)
        row[field + '_change'] = current - old if stable_previous and current is not None and old is not None else None
    shares = [(row.get(key), (previous or {}).get(key)) for key in ('left_share', 'right_share')]
    row['closed_share_opposite_without_absolute_amount_opposition'] = (
        (shares[0][0] - shares[0][1]) * (shares[1][0] - shares[1][1]) < 0
        and row['left_amount_cny_change'] * row['right_amount_cny_change'] >= 0
        if stable_previous and all(a is not None and b is not None for a, b in shares)
        and row['left_amount_cny_change'] is not None and row['right_amount_cny_change'] is not None else None)
    stable_training = len(prior) == PARAMETERS['training_exchange_sessions'] and all(
        all(item[key] == row[key] for key in
            ('left_exclusive_member_ids', 'right_exclusive_member_ids', 'return_basis')) for item in prior)
    row['training_sessions'] = [item['session'] for item in prior]
    for side in ('left', 'right'):
        for kind, xkey, ykey in (
                ('amount', 'market_complement_amount_cny_change', side + '_amount_cny_change'),
                ('price', 'market_complement_return_1d', side + '_return_1d')):
            row[side + '_' + kind + '_market_residual'] = legacy._ols_residual(
                [(item[xkey], item[ykey]) for item in prior], (row[xkey], row[ykey])) if stable_training else {
                    'status': 'UNKNOWN', 'residual': None,
                    'reason_codes': ['PRIOR60_STABLE_EXCLUSIVE_COHORT_OR_BASIS_UNAVAILABLE']}
    return row


def _day_bars(day, market, inline, resolver):
    choices = defaultdict(list)
    for row in inline:
        choices[row.instrument_id].append(row)
    for iid, row in (market or {}).get('selected', {}).items():
        if row is not None:
            choices[iid].append(row)
    return {(iid, day): theme._select([row for row in rows if resolver.resolve(row,
                instrument_id=iid, session=day, require_session=True, require_complete=True).allowed])
            for iid, rows in choices.items()}


def run_theme_cycle_streaming(bundle, theme_result, *, cutoff, market_stream, evidence_directory,
        _theme_result_validator, _pair_scope_validator, theme_pairs, eligible_theme_ids,
        background_theme_ids=(), pair_scope_provenance=None, cycle_registry=(), analysis_at=None,
        _cycle_registry_validator=None):
    """Run exact descriptive comparisons without retaining an annual price panel.

    Validators are trusted capabilities supplied by the native caller. They must
    bind the fresh parent archive/object and independently verified current
    mainline scope. Neither callback is inferred from JSON claims.
    """
    cutoff = stamp(cutoff)
    analysis_at = stamp(analysis_at) if analysis_at is not None else datetime.now(timezone.utc)
    if analysis_at < cutoff:
        raise ValueError('THEME_CYCLE_STREAMING_ANALYSIS_BEFORE_SOURCE_CUTOFF')
    if not isinstance(market_stream, SourceMarketStream):
        raise ValueError('THEME_CYCLE_STREAMING_AUTHENTICATED_SOURCE_STREAM_REQUIRED')
    market_stream.validate_binding(bundle, cutoff)
    identity = source_identity()
    sessions, parent_by_day, ids = _validate_parent(bundle, cutoff, market_stream,
                                                   theme_result, _theme_result_validator)
    pairs, eligible, background, scope = _scope(ids, theme_pairs, eligible_theme_ids,
        background_theme_ids, pair_scope_provenance, _pair_scope_validator,
        bundle=bundle, cutoff=cutoff, market_stream=market_stream, theme_result=theme_result)
    registry, emotion_authenticated = legacy._registry(cycle_registry, analysis_at,
        _cycle_registry_validator, bundle=bundle, cutoff=cutoff)
    calendar = list(market_stream.calendar)
    inline = defaultdict(list)
    for row in bundle.bars:
        inline[str(row.session)].append(row)  # References to the original input, not copied price objects.
    daily = {pair: [] for pair in pairs}
    training = {pair: deque(maxlen=PARAMETERS['training_exchange_sessions']) for pair in pairs}
    previous_bars = {}
    resolver = SpoolingEvidenceResolver(tuple(bundle.receipts) + tuple(market_stream.receipts),
        cutoff=cutoff, coverage_type=bundle.coverage_type, sessions=calendar,
        observation_date=bundle.observation_date, evidence_directory=Path(evidence_directory))
    try:
        if resolver.conflicts:
            raise ValueError('THEME_CYCLE_STREAMING_SOURCE_RECEIPT_CONFLICT')
        for index, day in enumerate(sessions):
            panel = market_stream.panel(day)  # Re-authenticate original bytes for this day.
            market = theme._session_market(panel, resolver, calendar) if panel is not None else None
            current_bars = _day_bars(day, market, inline.get(day, ()), resolver)
            bars = {**previous_bars, **current_bars}
            prior_day = sessions[index - 1] if index else None
            returns = {iid: theme._return1(iid, day, prior_day, bars, resolver, market=market)
                for iid in market['trading_ids']} if market and market['status'] == 'COMPLETE' else {}
            rows = legacy._validated_rows(bundle, cutoff, SimpleNamespace(themes=market_stream.themes),
                {'membership_mode': theme_result['membership_mode'], 'computed_exchange_sessions': [day],
                 'items': parent_by_day.get(day, ())}, resolver, {day: market})
            for pair in pairs:
                row = legacy._daily_pair(*pair, [day], rows, {day: market}, {day: returns},
                    retain_complement_ids=False, parent_fsm_authenticated=True)[0]
                _window_fields(row, training[pair])
                daily[pair].append(row)
                training[pair].append(row)
            previous_bars = current_bars
            # No market, panel, returns or merged full-market price map survives
            # into the next day. Only preceding selected prices support the
            # original comparable-basis path; reference-chain prices need none.
            del panel, market, bars, returns, rows, current_bars
        resolver.close()  # Seal every allowed source row before hashing the audit.
        audit = resolver.audit_manifest()
        dependency_hash = digest_json_stream(resolver.effective_dependencies())
    finally:
        resolver.close()
    output_pairs = []
    for pair in pairs:
        observations = daily[pair]
        intervals = legacy._intervals(observations, registry, emotion_authenticated, calendar)
        valid = [row for row in intervals if row['countable_emotion_cycle']]
        reasons = [] if len(valid) >= PARAMETERS['minimum_authenticated_emotion_cycles'] else [
            'FEWER_THAN_THREE_VALID_AUTHENTICATED_EMOTION_CYCLES']
        signs = {kind: [math.copysign(1, row['statistics']['exclusive_' + kind + '_market_residual_correlation']['value'])
            if row['statistics']['exclusive_' + kind + '_market_residual_correlation']['value'] != 0 else 0
            for row in valid] for kind in ('amount', 'price')}
        output_pairs.append({'left_theme_id': pair[0], 'right_theme_id': pair[1],
            'pair_display_role': 'PRIMARY_CANDIDATE_THEME_PAIR' if set(pair).issubset(eligible) else
                'PRIMARY_THEME_WITH_EXPLICIT_NECESSARY_ROTATION_BACKGROUND',
            'status': 'DESCRIPTIVE_CYCLE_COMPARISON_AVAILABLE' if not reasons else 'UNKNOWN',
            'valid_authenticated_emotion_cycle_count': len(valid),
            'per_cycle_residual_correlation_signs': signs,
            'same_sign_across_observed_cycles': {key: len(set(values)) == 1 if len(values) >= 3 else None
                                                for key, values in signs.items()},
            'intervals': intervals, 'observations': theme.encoded(observations), 'reason_codes': reasons,
            'price_association_does_not_identify_fund_flow': True,
            'share_closure_used_for_relationship_conclusion': False,
            'causal_interpretation': 'NOT_ESTABLISHED', 'statistical_stability': 'NOT_ESTABLISHED'})
    backview = theme_result['membership_mode'] == 'CURRENT_COHORT_BACKVIEW'
    result = {**identity, 'analysis_at': analysis_at.isoformat(), 'decision_cutoff': cutoff.isoformat(),
        'source_input_hash': digest_market_input(bundle), 'theme_result_content_hash': theme_result['context_content_hash'],
        'market_pack_hash': None, 'market_stream_descriptor_hash': market_stream.descriptor_hash,
        'cycle_registry': registry, 'cycle_registry_hash': digest_json_stream(registry),
        'registration_claim_scope': 'BEFORE_THIS_ANALYSIS_NOT_CLAIMED_BEFORE_HISTORICAL_INTERVALS',
        'computed_exchange_sessions': sessions, 'full_observed_calendar': calendar,
        'pair_count': len(output_pairs), 'pairs': output_pairs,
        'pair_scope': scope['theme_pairs'], 'pair_scope_hash': digest(pairs),
        'pair_scope_role': 'CALLER_EXPLICIT_RESEARCH_PAIR_SCOPE_NOT_FROM_RESULT_DIRECTION',
        'pair_scope_provenance': scope['pair_scope_provenance'],
        'pair_scope_provenance_hash': digest_json_stream(scope['pair_scope_provenance']),
        'eligible_theme_ids': sorted(eligible), 'necessary_background_theme_ids': sorted(background),
        'theme_qualification_scope_hash': digest({'eligible_theme_ids': sorted(eligible),
                                                  'necessary_background_theme_ids': sorted(background)}),
        'theme_qualification_scope_role': 'AUTHENTICATED_CURRENT_MAINLINE_DISPLAY_SCOPE_NOT_HISTORICAL_QUALIFICATION',
        'current_mainline_scope_authentication_status': 'AUTHENTICATED_BY_NATIVE_CAPABILITY',
        'historical_mainline_eligibility_status': 'UNKNOWN',
        'historical_mainline_eligibility_reason_codes': ['NO_AUTHENTICATED_DATED_MAINLINE_RULE_AND_QUALIFICATION_EVIDENCE'],
        'parent_fsm_authentication_status': 'AUTHENTICATED_BY_NATIVE_CAPABILITY',
        'parent_fsm_display_policy': 'AUTHENTICATED_NATIVE_STAGE_DISPLAY_ONLY_NOT_REGRESSION_INPUT',
        'sample_selection_bias': 'CURRENT_SELECTED_CANDIDATE_AND_ACTIVE_THEME_SCOPE_SURVIVORSHIP_SELECTION_BIAS'
            if scope['pair_scope_provenance'].get('scope_basis') == 'CURRENT_SELECTED_CANDIDATES_AND_ACTIVE_THEMES'
            else 'CURRENT_COHORT_RETROSPECTIVE_MEMBERSHIP_SELECTION_BIAS' if backview else
            'DATED_SOURCE_QUALIFIED_COHORT_NOT_INDEPENDENT_PREDICTION_VALIDATION',
        'historical_opportunity_set_claimed': False,
        'multiple_comparison_adjustment': 'NOT_PERFORMED_NO_SIGNIFICANCE_OR_EDGE_CLAIM',
        'data_role': 'CURRENT_COHORT_RETROSPECTIVE' if backview else 'DATED_COHORT_SOURCE_QUALIFIED_DESCRIPTIVE_RESEARCH',
        'membership_mode': theme_result['membership_mode'], 'official_pit_validation_count': 0,
        'status': 'DESCRIPTIVE_COMPARISON_AVAILABLE' if output_pairs and all(row['status'] != 'UNKNOWN'
                    for row in output_pairs) else 'UNKNOWN',
        'formal_focus': False, 'core_pass': False, 'selection_role': 'RESEARCH_PANEL_VALIDATION_NOT_SELECTION',
        'fund_flow_role': 'TRADE_VALUE_NOT_NET_INFLOW', 'causal_interpretation': 'NOT_ESTABLISHED',
        'statistical_stability': 'NOT_ESTABLISHED', 'market_edge': 'NOT_ESTABLISHED',
        'missing_policy': 'UNKNOWN_NOT_ZERO_NO_SESSION_COMPRESSION',
        'overlap_policy': 'DATED_EXCLUSIVE_MEMBERS_AND_EX_UNION_MARKET_COMPLEMENT',
        'regression_policy': 'EXACT_PRECEDING60_OLS_FIRST_DIFFERENCE_AMOUNT_AND_DAILY_RETURN_CURRENT_HELD_OUT',
        'market_factor_membership_policy': 'COMPLETE_SAME_DATE_DYNAMIC_MARKET_COMPLEMENT_CHANGES_DISCLOSED',
        'execution_provenance': {'price_object_retention': 'CURRENT_AND_PRECEDING_SESSION_ONLY',
            'training_scalar_window_sessions': PARAMETERS['training_exchange_sessions'],
            'allowed_source_rows_audit': audit, 'effective_source_dependencies_hash': dependency_hash},
        'reason_codes': []}
    market_stream.validate_binding(bundle, cutoff)
    if source_identity() != identity:
        raise ValueError('THEME_CYCLE_STREAMING_IMPLEMENTATION_CHANGED_DURING_EVALUATION')
    result['report_content_hash'] = digest_json_stream(result)
    return result
