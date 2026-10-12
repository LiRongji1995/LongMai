"""Explicit theme-stream V3 with complete compressed evidence and logical audit.

All numerical/FSM/current-cohort helpers are the frozen V1 implementation.
Only execution, failure propagation and audit finalization have new identity.
This module does not select stocks or wire a production entry point.
"""
from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import MutableMapping
from datetime import date
from fractions import Fraction
import hashlib
from pathlib import Path

from . import theme_evolution as base
from . import theme_evolution_streaming as legacy
from .contracts import MarketInput, digest
from .evidence_audit_spool_v3 import (SpoolingEvidenceResolverV3, VERSION as JOURNAL_VERSION,
    source_identity as journal_source_identity)
from .history_artifacts import digest_json_stream, digest_market_input
from .visibility import SH, POLICY_ID, stamp

STRATEGY_ID = 'THEME_EVOLUTION_STREAMING_R3'
IMPLEMENTATION_VERSION = 'THEME_EVOLUTION_STREAMING_EXACT_COMPRESSED_JOINT_AUDIT_V3'
FEATURE_VERSION = 'THEME_EVOLUTION_STREAMED_EXACT_CONTEXT_V3'
SPEC_REVISION = 'THEME_EVOLUTION_STREAMING_R3_SPEC_1'
SCHEMA_VERSION = 'THEME_EVOLUTION_STREAMED_CONTEXT_V3'
SPEC_PATH = base.ROOT / 'docs' / 'THEME_EVOLUTION_STREAMING_V3.md'
HISTORY_RETAINED_SESSIONS = legacy.HISTORY_RETAINED_SESSIONS
_binding = legacy._binding
_selected_bars = legacy._selected_bars
_cohorts = legacy._cohorts
_features = legacy._features


def source_identity():
    """Pin the new execution/spec, real journal, and every old helper source."""
    hashes = dict(legacy.source_identity()['source_file_hashes'])
    journal_identity = journal_source_identity()
    hashes.update(journal_identity['source_file_hashes'])
    for path in (Path(__file__), SPEC_PATH, Path(__file__).with_name('evidence_audit_spool_v3.py'),
                 Path(__file__).with_name('evidence_audit_spool_v2.py')):
        hashes[str(path.relative_to(base.ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {'implementation_version': IMPLEMENTATION_VERSION,
            'feature_version': FEATURE_VERSION, 'spec_revision': SPEC_REVISION,
            'source_file_hashes': hashes, 'source_hash': digest(hashes),
            'config_hash': digest(base.CONFIG),
            'numeric_rule_source_identity': base.source_identity(),
            'legacy_helper_source_identity': legacy.source_identity(),
            'journal_source_identity': journal_identity,
            'journal_schema_version': JOURNAL_VERSION}


class _JournalPrimary(RuntimeError):
    """Carry an I/O primary through the resolver's envelope-format catch."""
    def __init__(self, primary):
        super().__init__('THEME_STREAM_V3_FATAL_JOURNAL_OPERATION')
        self.primary = primary


class _FatalRowBoundary(MutableMapping):
    """No authority or cache: delegate to the genuine current-owned journal."""
    def __init__(self, journal):
        self._journal = journal

    def __getattr__(self, name):
        return getattr(self._journal, name)

    def __len__(self):
        return len(self._journal)

    def __iter__(self):
        return iter(self._journal)

    def __delitem__(self, key):
        del self._journal[key]

    def __getitem__(self, key):
        try:
            return self._journal[key]
        except (ValueError, TypeError, KeyError) as primary:
            raise _JournalPrimary(primary) from primary

    def __setitem__(self, key, value):
        try:
            self._journal[key] = value
        except (ValueError, TypeError, KeyError) as primary:
            raise _JournalPrimary(primary) from primary


class _RecordingResolver(SpoolingEvidenceResolverV3):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rows = _FatalRowBoundary(self.rows)

    def resolve(self, *args, **kwargs):
        try:
            return super().resolve(*args, **kwargs)
        except _JournalPrimary as error:
            # Outside EvidenceResolver's format handler: preserve the actual
            # primary object rather than returning INVALID_EVIDENCE_ENVELOPE.
            raise error.primary from error


def _cleanup_after_failure(resolver, primary):
    resolver.rows.failures.append({'phase': 'THEME_STREAM_PRIMARY',
        'error': type(primary).__name__ + ':' + str(primary)})
    try:
        resolver.close()
    except BaseException as cleanup:
        resolver.rows.failures.append({'phase': 'THEME_STREAM_CLEANUP',
            'error': type(cleanup).__name__ + ':' + str(cleanup)})
    # Exception metadata contains copies only, not a source validator or a
    # restorable journal. Preserve primary even if an unusual exception object
    # refuses attributes; the genuine partial files and journal remain intact.
    try:
        primary.theme_stream_failure_provenance = resolver.failure_provenance
        primary.theme_stream_journal_read_metrics = resolver.read_metrics
    except BaseException:
        pass


def _terminal_binding(bundle, cutoff, stream, input_hash, descriptor, identity, validator):
    """Post-joint original input/stream/code plus owned physical terminal."""
    final, _, _ = _binding(bundle, cutoff, stream, input_hash)
    if (digest_market_input(bundle) != input_hash or final != descriptor or source_identity() != identity):
        raise ValueError('THEME_STREAM_INPUT_SOURCE_OR_DESCRIPTOR_CHANGED_DURING_RUN')
    if validator is not None:
        # No return flag creates capability; failures propagate. The original
        # SourceMarketStream remains mandatory for every non-fixture input.
        validator()
    final, _, _ = _binding(bundle, cutoff, stream, input_hash)
    if (digest_market_input(bundle) != input_hash or final != descriptor or source_identity() != identity):
        raise ValueError('THEME_STREAM_INPUT_SOURCE_OR_DESCRIPTOR_CHANGED_DURING_TERMINAL')


def run_theme_evolution_stream(bundle: MarketInput, *, cutoff, market_stream,
        evidence_directory, membership_mode='CURRENT_COHORT_BACKVIEW',
        output_history=True, history_scope='ALL_OBSERVED_SESSIONS_RESEARCH',
        _source_terminal_validator=None, _logical_read_callback=None):
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
    if (_source_terminal_validator is not None and not callable(_source_terminal_validator)):
        raise ValueError('THEME_STREAM_V3_SOURCE_TERMINAL_CAPABILITY_REQUIRED')
    if (_logical_read_callback is not None and not callable(_logical_read_callback)):
        raise ValueError('THEME_STREAM_V3_LOGICAL_READ_CALLBACK_REQUIRED')
    input_hash, identity = digest_market_input(bundle), source_identity()
    descriptor, receipts, extra_themes = _binding(bundle, cutoff, market_stream, input_hash)
    if bundle.coverage_type != 'TEST_FIXTURE' and not callable(_source_terminal_validator):
        raise ValueError('THEME_STREAM_V3_PHYSICAL_SOURCE_TERMINAL_REQUIRED')
    if bundle.coverage_type != 'TEST_FIXTURE' and not callable(_logical_read_callback):
        raise ValueError('THEME_STREAM_V3_LIVE_LOGICAL_READ_ACCOUNTING_REQUIRED')
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
    resolver = _RecordingResolver(tuple(bundle.receipts) + receipts,
        evidence_directory=evidence_directory, cutoff=cutoff, coverage_type=bundle.coverage_type,
        sessions=sessions, observation_date=bundle.observation_date,
        logical_read_callback=_logical_read_callback)
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
        # This is one owned joint operation, not a caller-supplied audit flag.
        joint = resolver.joint_dependency_digest_and_audit_manifest()
        _terminal_binding(bundle, cutoff, market_stream, input_hash, descriptor, identity,
                          _source_terminal_validator)
    except BaseException as primary:
        _cleanup_after_failure(resolver, primary)
        raise
    dependency_hash, audit = joint['dependency_hash'], joint['audit_manifest']
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
            'dependency_policy_id': POLICY_ID,
            'journal_schema_version': JOURNAL_VERSION,
            'joint_validation': joint['validation'],
            'journal_read_metrics': joint['read_metrics'],
            'logical_read_accounting_status': 'LIVE_CALLBACK_COMPLETED'
                if _logical_read_callback is not None else 'ISOLATED_TEST_FIXTURE_LOCAL_METRICS',
            'logical_read_callback_role': 'ACCOUNTING_ONLY_NOT_SOURCE_CAPABILITY',
            'source_terminal_callback_status': 'LIVE_CALLBACK_COMPLETED'
                if _source_terminal_validator is not None else 'ISOLATED_TEST_FIXTURE_BINDING',
            'outer_original_registry_authentication':
                'CALLER_RESPONSIBILITY_NOT_CERTIFIED_BY_THIS_MODULE',
            'full_observed_calendar_count': len(descriptor['full_observed_calendar']),
            'retained_feature_sessions_per_theme_limit': HISTORY_RETAINED_SESSIONS,
            'selected_bar_cache_session_limit': 2, 'market_panels_live_limit': 1,
            'missing_sessions_not_compressed': True, 'window_fsm_stitching_used': False,
            'input_unchanged': True, 'business_database_reads': False}}
    result['context_content_hash'] = digest_json_stream(result)
    return result
