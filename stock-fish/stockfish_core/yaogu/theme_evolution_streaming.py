"""Exact theme context from authenticated daily panels and a complete audit.

This explicit research producer advances the original exchange calendar once.
It keeps two selected price days and sixty feature days per theme, while every
allowed evidence row is retained in the native audit journal. It does not
activate a strategy, select stocks, compress missing sessions, or reconstruct
past membership from a current snapshot. Numerical rules belong to the
unchanged :mod:`theme_evolution` producer.
"""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import date
from fractions import Fraction
import hashlib
from pathlib import Path

from . import theme_evolution as base
from .contracts import MarketInput, SourceReceipt, ThemeSnapshot, digest
from .evidence_audit_spool import SpoolingEvidenceResolver
from .history_artifacts import digest_json_stream, digest_market_input
from .theme_market_stream import SourceMarketStream, VERSION as MARKET_STREAM_SCHEMA
from .visibility import SH, clock_visible, stamp

STRATEGY_ID = 'THEME_EVOLUTION_STREAMING_R1'
IMPLEMENTATION_VERSION = 'THEME_EVOLUTION_STREAMING_EXACT_COMPLETE_AUDIT_V1'
FEATURE_VERSION = 'THEME_EVOLUTION_STREAMED_EXACT_CONTEXT_V1'
SPEC_REVISION = 'THEME_EVOLUTION_STREAMING_R1_SPEC_1'
SCHEMA_VERSION = 'THEME_EVOLUTION_STREAMED_CONTEXT_V1'
HISTORY_RETAINED_SESSIONS = max(base.CONFIG['expansion_prior_sessions'],
    base.CONFIG['high_risk']['share_prior_sessions'],
    base.CONFIG['high_risk']['breadth_peak_prior_sessions'], 5)


def source_identity():
    """Bind execution code as well as the unchanged exact numerical rules."""
    from .short_horizon_participation import short_horizon_source_identity
    hashes = dict(short_horizon_source_identity()['source_file_hashes'])
    paths = (Path(__file__), Path(base.__file__), base.CONFIG_PATH, base.SPEC_PATH,
             Path(__file__).with_name('theme_market_stream.py'),
             Path(__file__).with_name('evidence_audit_spool.py'),
             Path(__file__).with_name('history_artifacts.py'),
             Path(__file__).with_name('visibility.py'),
             Path(__file__).with_name('contracts.py'))
    for path in paths:
        # Missing implementation dependencies fail; a partial identity grants
        # no capability to run this producer.
        hashes[str(path.relative_to(base.ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {'implementation_version': IMPLEMENTATION_VERSION,
        'feature_version': FEATURE_VERSION, 'spec_revision': SPEC_REVISION,
        'source_file_hashes': hashes, 'source_hash': digest(hashes),
        'config_hash': digest(base.CONFIG),
        'numeric_rule_source_identity': base.source_identity()}


def _binding(bundle, cutoff, stream, input_hash):
    """Require a native source capability, not descriptor verification flags."""
    if (stream is None or not callable(getattr(stream, 'validate_binding', None))
            or not callable(getattr(stream, 'panel', None))):
        raise ValueError('THEME_STREAM_VALIDATOR_CAPABILITY_REQUIRED')
    # Isolated mathematical fixtures may supply a pack-backed capability. The
    # production path is the native original-byte reader, never a JSON object.
    if bundle.coverage_type != 'TEST_FIXTURE' and not isinstance(stream, SourceMarketStream):
        raise ValueError('THEME_STREAM_AUTHENTICATED_NATIVE_CAPABILITY_REQUIRED')
    stream.validate_binding(bundle, cutoff)
    descriptor = stream.descriptor
    calendar = tuple(str(day) for day in bundle.sessions if day <= bundle.observation_date)
    requested, available = tuple(stream.requested_sessions), tuple(stream.available_sessions)
    receipts = tuple(SourceReceipt.model_validate(row) for row in stream.receipts)
    themes = tuple(ThemeSnapshot.model_validate(row) for row in stream.themes)
    if (not isinstance(descriptor, dict) or descriptor.get('schema_version') != MARKET_STREAM_SCHEMA
            or digest_json_stream(descriptor) != stream.descriptor_hash
            or descriptor.get('source_input_hash') != input_hash
            or descriptor.get('observation_date') != str(bundle.observation_date)
            or stamp(descriptor.get('decision_cutoff')) != cutoff
            or stamp(stream.cutoff) != cutoff
            or descriptor.get('coverage_type') != bundle.coverage_type
            or tuple(stream.calendar) != calendar
            or descriptor.get('full_observed_calendar') != list(calendar)
            or descriptor.get('requested_sessions') != list(requested)
            or descriptor.get('available_sessions') != list(available)
            or len(set(requested)) != len(requested)
            or requested != tuple(day for day in calendar if day in set(requested))
            or len(set(available)) != len(available)
            or available != tuple(day for day in requested if day in set(available))
            or set(descriptor.get('panels', {})) != set(available)
            or set(descriptor.get('missing_sessions', {})) != set(requested) - set(available)
            or descriptor.get('source_receipt_identity_hash') != digest_json_stream(
                [row.model_dump(mode='json') for row in receipts])
            or descriptor.get('theme_snapshots') != [row.model_dump(mode='json') for row in themes]
            or descriptor.get('official_pit_validation_countable') is not False):
        raise ValueError('THEME_STREAM_INPUT_OR_DESCRIPTOR_BINDING_MISMATCH')
    return descriptor, receipts, themes


def _selected_bars(day, inline, market, resolver):
    """Match the batch producer's visibility-before-revision selection."""
    grouped = defaultdict(list)
    for row in inline:
        grouped[row.instrument_id].append(row)
    if market is not None:
        for iid, row in market['selected'].items():
            if row is not None:
                grouped[iid].append(row)
    selected = {}
    for iid, choices in grouped.items():
        visible = [row for row in choices if resolver.resolve(row,
            instrument_id=iid, session=day, require_session=True, require_complete=True).allowed]
        selected[(iid, day)] = base._select(visible)
    return selected


def _cohorts(themes, theme_ids, resolver, cutoff):
    cohorts = {}
    decision_day = cutoff.astimezone(SH).date()
    for theme_id in theme_ids:
        choices = [row for row in themes if row.theme_id == theme_id
            and row.theme_type in base.CONFIG['allowed_theme_types']
            and row.effective_from <= decision_day and clock_visible(row, cutoff)
            and all(clock_visible(resolver.receipts.get(ref), cutoff) for ref in row.source_refs)
            and resolver.resolve(row, session=row.effective_from, require_complete=True).allowed]
        cohorts[theme_id] = base._select(choices)
    return cohorts


def _features(theme_id, theme, day, prior_day, market, read_return, mode):
    """The batch feature formulas, including unknown and dated-absence axes."""
    reasons = []
    members = sorted(set(theme.member_ids)) if theme is not None else []
    if theme is None:
        reasons.append('THEME_DATED_MEMBERSHIP_MISSING_OR_CONFLICTING')
    if market is None or market['status'] != 'COMPLETE':
        reasons.append('THEME_COMPLETE_MARKET_AMOUNT_UNAVAILABLE')
    dated_eligibility = (mode == 'CURRENT_COHORT_BACKVIEW' and market is not None
        and market['status'] == 'COMPLETE' and bool(members)
        and all(base._a_common_id(iid) for iid in members))
    if mode == 'CURRENT_COHORT_BACKVIEW' and members and not all(base._a_common_id(iid) for iid in members):
        reasons.append('THEME_CURRENT_COHORT_SECURITY_SCOPE_UNVERIFIED')
    if market and members and not set(members).issubset(market['expected_ids']) and not dated_eligibility:
        reasons.append('THEME_MEMBER_OUTSIDE_VERIFIED_A_COMMON_SCOPE')
    eligible = sorted(set(members) & market['expected_ids']) if dated_eligibility else members
    absent = sorted(set(members) - market['expected_ids']) if dated_eligibility else None
    active = sorted(set(eligible) & market['trading_ids']) if market else eligible
    if theme is not None and not active:
        reasons.append('THEME_NO_ACTIVE_DATED_ELIGIBLE_MEMBERS')
    amounts = [market['amounts'].get(iid) for iid in active] if market else []
    total = (sum(amounts, Fraction(0)) if active and amounts
        and all(value is not None for value in amounts) and not reasons else None)
    share = total / market['amount_cny'] if total is not None else None
    concentration = ((len(active) * sum((value / total) ** 2 for value in amounts) - 1)
        / (len(active) - 1) if total is not None and total > 0 and len(active) > 1 else None)
    returns = [read_return(iid, day, prior_day) for iid in active] if theme else []
    mean = (sum(returns, Fraction(0)) / len(returns) if returns
        and all(value is not None for value in returns) and not reasons else None)
    breadth = Fraction(sum(value > 0 for value in returns), len(returns)) if mean is not None else None
    return {'session': day, 'previous_exchange_session': prior_day, 'theme_id': theme_id,
        'name': theme.name if theme else None, 'theme_type': theme.theme_type if theme else None,
        'membership_mode': mode,
        'data_role': 'DAILY_DATED_MEMBERSHIP_HISTORY' if mode == 'DATED_ONLY'
            else 'CURRENT_COHORT_BACKVIEW_ASOF_DECISION',
        'cohort_captured_at': max(theme.first_seen_at, theme.ingested_at).isoformat() if theme else None,
        'cohort_effective_from': str(theme.effective_from) if theme else None,
        'historical_membership_verified': theme is not None and mode == 'DATED_ONLY',
        'official_pit_validation_countable': False,
        'membership_count': len(members), 'active_membership_count': len(active), 'member_ids': members,
        'membership_scope': 'CURRENT_COHORT_WITH_DATED_ELIGIBILITY'
            if mode == 'CURRENT_COHORT_BACKVIEW' else 'DAILY_DATED_MEMBERSHIP_HISTORY',
        'dated_eligibility_verified': dated_eligibility,
        'eligible_member_ids': eligible, 'eligible_membership_count': len(eligible),
        'absent_from_dated_market_ids': absent,
        'absent_from_dated_market_count': len(absent) if absent is not None else None,
        'theme_amount_cny': total, 'market_amount_cny': market['amount_cny'] if market else None,
        'share': share, 'concentration_adjusted_hhi': concentration, 'breadth': breadth,
        'theme_return_1d': mean, 'market_return_1d': market['market_return_1d'] if market else None,
        'price_return_basis': (market.get('return_basis') if market else None)
            or 'SAME_COMPARABLE_BASIS_DAILY_RETURN',
        'source_refs': sorted(set((theme.source_refs if theme else ())
            + tuple(market['source_refs'] if market else ()))), 'reason_codes': reasons}


def run_theme_evolution_stream(bundle: MarketInput, *, cutoff, market_stream,
        evidence_directory, membership_mode='CURRENT_COHORT_BACKVIEW',
        output_history=True, history_scope='ALL_OBSERVED_SESSIONS_RESEARCH'):
    """Produce native research context with one live authenticated market day.

    Source streams, output manifests and the complete evidence journal belong
    to the caller's ordinary research artifact. No history store is opened.
    """
    cutoff = stamp(cutoff)
    if membership_mode not in ('DATED_ONLY', 'CURRENT_COHORT_BACKVIEW'):
        raise ValueError('THEME_MEMBERSHIP_MODE_UNSUPPORTED')
    if not isinstance(output_history, bool):
        raise ValueError('THEME_OUTPUT_HISTORY_MUST_BE_BOOLEAN')
    if history_scope not in base.CONFIG['allowed_history_scopes']:
        raise ValueError('THEME_HISTORY_SCOPE_UNSUPPORTED')
    if bundle.observation_date > cutoff.astimezone(SH).date():
        raise ValueError('THEME_FUTURE_OBSERVATION_DATE')
    input_hash, identity = digest_market_input(bundle), source_identity()
    descriptor, receipts, extra_themes = _binding(bundle, cutoff, market_stream, input_hash)
    descriptor_hash = digest_json_stream(descriptor)
    sessions = list(descriptor['full_observed_calendar'])
    if history_scope == 'LATEST_67':
        sessions = sessions[-base.CONFIG['history_exchange_sessions']:]
    session_set, available = set(sessions), set(descriptor['available_sessions'])
    themes = tuple(bundle.themes) + extra_themes
    theme_ids = sorted({row.theme_id for row in themes if row.theme_type in base.CONFIG['allowed_theme_types']})
    inline = defaultdict(list)
    for row in bundle.bars:
        if str(row.session) in session_set:
            inline[str(row.session)].append(row)
    resolver = SpoolingEvidenceResolver(tuple(bundle.receipts) + receipts,
        evidence_directory=evidence_directory, cutoff=cutoff, coverage_type=bundle.coverage_type,
        sessions=sessions, observation_date=bundle.observation_date)
    outputs = {theme_id: [] for theme_id in theme_ids}
    histories = {theme_id: deque(maxlen=HISTORY_RETAINED_SESSIONS) for theme_id in theme_ids}
    states = {theme_id: None for theme_id in theme_ids}
    try:
        backview = _cohorts(themes, theme_ids, resolver, cutoff) if membership_mode == 'CURRENT_COHORT_BACKVIEW' else {}
        previous_bars = {}
        for index, day in enumerate(sessions):
            prior_day = sessions[index - 1] if index else None
            panel = market_stream.panel(day)
            if (panel is None) != (day not in available):
                raise ValueError('THEME_STREAM_AVAILABLE_PANEL_MISMATCH')
            if panel is not None:
                if (not isinstance(panel, base.ThemeMarketSession) or str(panel.session) != day
                        or digest_json_stream(panel.model_dump(mode='json')) != descriptor['panels'][day]['panel_hash']):
                    raise ValueError('THEME_STREAM_PANEL_ORIGINAL_SOURCE_MISMATCH')
                market = base._session_market(panel, resolver, sessions)
            else:
                market = None
            # The panel is released before advancing to the following day.
            del panel
            current_bars = _selected_bars(day, inline.get(day, ()), market, resolver)
            bars = {**previous_bars, **current_bars}
            daily_return = base._return_reader(bars, resolver, {day: market} if market is not None else {})
            if market is not None:
                values = [daily_return(iid, day, prior_day) for iid in sorted(market['trading_ids'])] if market['status'] == 'COMPLETE' else []
                market['market_return_1d'] = (sum(values, Fraction(0)) / len(values)
                    if values and all(value is not None for value in values) else None)
            market_compound = base._market_compound_reader()
            for theme_id in theme_ids:
                # Preserve the batch producer's dated candidate resolutions,
                # even when explicit current-cohort mode chooses another row.
                candidates = [row for row in themes if row.theme_id == theme_id
                    and row.theme_type in base.CONFIG['allowed_theme_types']
                    and row.effective_from <= date.fromisoformat(day)
                    and (row.effective_to is None or date.fromisoformat(day) < row.effective_to)
                    and resolver.resolve(row, session=day, require_complete=True).allowed]
                theme = base._select(candidates) if membership_mode == 'DATED_ONLY' else backview.get(theme_id)
                history, prior_state = list(histories[theme_id]), states[theme_id]
                feature = _features(theme_id, theme, day, prior_day, market, daily_return, membership_mode)
                shares = [row['share'] for row in history[-base.CONFIG['expansion_prior_sessions']:]]
                baseline = (base.median(shares) if len(shares) == base.CONFIG['expansion_prior_sessions']
                    and all(value is not None for value in shares) else None)
                feature['share_prior20_median'] = baseline
                feature['expansion'] = (feature['share'] / baseline
                    if feature['share'] is not None and baseline is not None and baseline > 0 else None)
                for count in (3, 5):
                    theme_return = base._compound([*history, feature], 'theme_return_1d', count)
                    market_return = market_compound([*history, feature], count)
                    feature[f'theme_return_{count}d'] = theme_return
                    feature[f'rs{count}'] = theme_return - market_return if theme_return is not None and market_return is not None else None
                if feature['theme_return_1d'] is None:
                    feature['reason_codes'].append('THEME_MEMBER_RETURN_UNAVAILABLE_OR_BASIS_UNVERIFIED')
                if feature['market_return_1d'] is None:
                    feature['reason_codes'].append('THEME_SAME_SCOPE_MARKET_RETURN_UNAVAILABLE')
                if feature['expansion'] is None:
                    feature['reason_codes'].append('THEME_PRIOR20_SHARE_BASELINE_UNAVAILABLE')
                previous = {**history[-1], 'candidate_states': prior_state['candidate_states']} if history and prior_state else None
                state = base.evaluate_day(feature, previous, history, prior_state['stage'] if prior_state else None)
                histories[theme_id].append(feature)
                states[theme_id] = state
                if output_history or index >= len(sessions) - base.CONFIG['output_exchange_sessions']:
                    outputs[theme_id].append(base.encoded({**feature, **state, 'market_scope': base.MARKET_SCOPE,
                        'return_basis': base.CONFIG['reference_return_role']
                            if feature['price_return_basis'] == base.CONFIG['reference_return_basis']
                            else 'DAILY_REBALANCED_DATED_COHORT_EQUAL_WEIGHT'
                            if membership_mode == 'DATED_ONLY' else 'FIXED_CURRENT_COHORT_EQUAL_WEIGHT_BACKVIEW',
                        'benchmark_scope': base.MARKET_SCOPE}))
            previous_bars = current_bars
            # Closures include the current market; discard them and all stock
            # return values before opening the next original-byte panel.
            del daily_return, market_compound, bars, current_bars, market
        final_descriptor, _, _ = _binding(bundle, cutoff, market_stream, input_hash)
        if (digest_market_input(bundle) != input_hash or final_descriptor != descriptor
                or source_identity() != identity):
            raise ValueError('THEME_STREAM_INPUT_SOURCE_OR_DESCRIPTOR_CHANGED_DURING_RUN')
        resolver.flush()
    finally:
        # Faults also seal the genuine partial journal; never replace it with
        # an empty audit or silently ignore corruption.
        resolver.close()
    dependencies = resolver.effective_dependencies()
    dependency_hash = digest_json_stream(dependencies)
    audit = resolver.audit_manifest()
    items = [row for theme_id in theme_ids for row in outputs[theme_id]]
    result = {'schema_version': SCHEMA_VERSION, 'strategy_id': STRATEGY_ID, **identity,
        'source_input_hash': input_hash, 'observation_date': str(bundle.observation_date),
        'decision_cutoff': cutoff.isoformat(), 'coverage_type': bundle.coverage_type,
        'market_pack_hash': None, 'market_stream_descriptor_hash': descriptor_hash,
        'market_stream_descriptor': descriptor, 'exact_numeric_encoding': base.EXACT_NUMERIC_ENCODING,
        'requested_output_scope': 'ALL_BOUNDED_HISTORY' if output_history else 'LAST_TWO_EXCHANGE_SESSIONS',
        'requested_history_scope': history_scope,
        'analysis_role': 'RETROSPECTIVE_CONTEXT_ANALYSIS'
            if history_scope == 'ALL_OBSERVED_SESSIONS_RESEARCH' else base.CONFIG['product_role'],
        'computed_exchange_sessions': sessions, 'membership_mode': membership_mode,
        'data_role': 'DAILY_DATED_MEMBERSHIP_HISTORY' if membership_mode == 'DATED_ONLY'
            else 'CURRENT_COHORT_BACKVIEW_ASOF_DECISION',
        'historical_membership_verified': bool(items) and all(row['historical_membership_verified'] for row in items),
        'official_pit_validation_countable': False,
        'source_refs': sorted(resolver.used), 'effective_input_hash': dependency_hash,
        'items': items, 'status': 'UNKNOWN' if not items else 'AVAILABLE_WITH_PER_AXIS_UNKNOWN',
        'formal_focus': False, 'core_pass': False,
        'selection_role': 'RETROSPECTIVE_CONTEXT_ANALYSIS'
            if history_scope == 'ALL_OBSERVED_SESSIONS_RESEARCH' else base.CONFIG['product_role'],
        'parameter_status': base.CONFIG['parameter_status'], 'market_edge': base.CONFIG['market_edge'],
        'source_policy': 'ORIGINAL_INPUT_PLUS_AUTHENTICATED_DAILY_SOURCE_STREAM_WITH_COMPLETE_AUDIT_NO_DB_READS',
        'association_policy': base.CONFIG['evidence']['association_policy'],
        'execution_provenance': {'execution_schema': SCHEMA_VERSION,
            'complete_allowed_row_audit': True, 'audit_manifest': audit,
            'dependency_policy_id': dependencies['policy_id'],
            'full_observed_calendar_count': len(descriptor['full_observed_calendar']),
            'retained_feature_sessions_per_theme_limit': HISTORY_RETAINED_SESSIONS,
            'selected_bar_cache_session_limit': 2, 'market_panels_live_limit': 1,
            'missing_sessions_not_compressed': True, 'window_fsm_stitching_used': False,
            'input_unchanged': True, 'business_database_reads': False}}
    result['context_content_hash'] = digest_json_stream(result)
    return result
