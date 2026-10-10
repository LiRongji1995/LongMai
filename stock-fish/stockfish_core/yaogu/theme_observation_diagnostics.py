"""Versioned report diagnostics for theme blind spots and first-day signals.

No stock, threshold or FSM state is changed. Fresh native capabilities must
authenticate both the source-qualified parent and the frozen stock selection.
Missing themes cannot be reported as absence of a market mainline.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
from pathlib import Path

from .history_artifacts import digest_json_stream, digest_market_input
from .theme_evolution import exact
from .visibility import stamp

VERSION = 'ACTIVE_MAINLINE_EARLY_SIGNAL_AND_DIVERGENCE_DISPLAY_V4'
FEATURE_VERSION = 'THEME_COVERAGE_PENDING_AND_ACTIVE_DIVERGENCE_DIAGNOSTICS_V2'


def source_identity():
    root = Path(__file__).resolve().parents[2]
    paths = (Path(__file__), root / 'docs/THEME_OBSERVATION_DIAGNOSTICS_V1.md')
    hashes = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in paths}
    return {'implementation_version': VERSION, 'feature_version': FEATURE_VERSION,
            'source_file_hashes': hashes, 'source_hash': digest_json_stream(hashes)}


def build_theme_observation_diagnostics(bundle, theme_context, *, cutoff, selected_ids,
        _theme_context_validator, _selection_validator):
    """Describe authenticated observation-day themes without changing selection."""
    cutoff = stamp(cutoff)
    input_hash = digest_market_input(bundle)
    if not callable(_theme_context_validator) or not callable(_selection_validator):
        raise ValueError('THEME_DISPLAY_NATIVE_PARENT_AND_SELECTION_CAPABILITIES_REQUIRED')
    if (theme_context.get('source_input_hash') != input_hash
            or str(theme_context.get('observation_date')) != str(bundle.observation_date)
            or stamp(theme_context.get('decision_cutoff')) != cutoff
            or theme_context.get('coverage_type') != bundle.coverage_type
            or theme_context.get('formal_focus') is not False
            or theme_context.get('core_pass') is not False
            or theme_context.get('official_pit_validation_countable') is not False
            or digest_json_stream({key: value for key, value in theme_context.items()
                                   if key != 'context_content_hash'}) != theme_context.get('context_content_hash')):
        raise ValueError('THEME_DISPLAY_PARENT_BINDING_MISMATCH')
    if _theme_context_validator(theme_context, bundle=bundle, cutoff=cutoff) is False:
        raise ValueError('THEME_DISPLAY_NATIVE_PARENT_AUTHENTICATION_FAILED')
    selected = tuple(sorted(selected_ids))
    if (not all(isinstance(iid, str) and iid for iid in selected)
            or len(set(selected)) != len(selected)
            or not set(selected).issubset({row.instrument_id for row in bundle.universe.securities})):
        raise ValueError('THEME_DISPLAY_FROZEN_SELECTION_INVALID')
    if _selection_validator(selected, bundle=bundle, cutoff=cutoff) is False:
        raise ValueError('THEME_DISPLAY_FROZEN_SELECTION_AUTHENTICATION_FAILED')
    rows = [row for row in theme_context.get('items', ())
            if str(row.get('session')) == str(bundle.observation_date)]
    if len({row.get('theme_id') for row in rows}) != len(rows):
        raise ValueError('THEME_DISPLAY_DUPLICATE_OBSERVATION_THEME')
    confirmed, pending, divergence, incomplete = [], [], [], []
    matched_ids = set()
    reasons = []
    if not rows:
        reasons.append('THEME_INPUT_HAS_NO_MEMBERSHIP_RECORDS' if not bundle.themes else
                       'THEME_SOURCE_QUALIFIED_OBSERVATION_ROWS_UNAVAILABLE')
        reasons.append('MAINLINE_IDENTIFICATION_UNAVAILABLE_NOT_NO_MAINLINE')
    elif not any(row.get('membership_count', 0) for row in rows):
        reasons.extend(('THEME_OBSERVATION_VISIBLE_MEMBERSHIP_UNAVAILABLE',
                        'MAINLINE_IDENTIFICATION_UNAVAILABLE_NOT_NO_MAINLINE'))
    elif not any(exact(row.get('market_amount_cny')) is not None for row in rows):
        reasons.extend(('THEME_OBSERVATION_COMPLETE_MARKET_SOURCE_UNAVAILABLE',
                        'MAINLINE_IDENTIFICATION_UNAVAILABLE_NOT_NO_MAINLINE'))
    for row in rows:
        intersection = sorted(set(row.get('member_ids', ())) & set(selected))
        if not intersection:
            continue
        candidates, confirmations = row.get('candidate_states') or {}, row.get('confirmation_states') or {}
        active = any(candidates.get(key) == 'PASS' for key in ('emerging', 'strengthening'))
        # Emerging uses RS3; strengthening uses RS5. Requiring both would add
        # an accidental gate and hide a legitimate first-day emerging signal.
        available_axes = all(exact(row.get(key)) is not None
                             for key in ('expansion', 'breadth', 'theme_amount_cny', 'market_amount_cny'))
        complete = available_axes and ((candidates.get('emerging') == 'PASS'
            and exact(row.get('rs3')) is not None) or (candidates.get('strengthening') == 'PASS'
            and exact(row.get('rs5')) is not None))
        known_confirmation = any(confirmations.get(key) == 'PASS' for key in ('emerging', 'strengthening'))
        observation = {'theme_id': row['theme_id'], 'name': row.get('name'),
            'session': row['session'], 'stage': row.get('stage'), 'selected_ids': intersection,
            'candidate_states': candidates, 'confirmation_states': confirmations,
            'data_role': row.get('data_role'), 'membership_mode': row.get('membership_mode'),
            'cohort_captured_at': row.get('cohort_captured_at'), 'source_refs': row.get('source_refs', ()),
            'reason_codes': row.get('reason_codes', ()),
            'high_risk': deepcopy(row.get('high_risk')),
            'source_feature_axes': {key: deepcopy(row.get(key)) for key in
                ('theme_amount_cny', 'market_amount_cny', 'share', 'expansion', 'breadth', 'rs3', 'rs5')},
            'historical_membership_verified': row.get('historical_membership_verified') is True,
            'official_pit_validation_countable': False}
        if complete and active and known_confirmation and row.get('stage') in ('EMERGING', 'STRENGTHENING'):
            confirmed.append({**observation, 'display_role': 'CONFIRMED_ACTIVE_MAINLINE'})
            matched_ids.update(intersection)
        elif complete and active and row.get('stage') in ('EMERGING_PENDING', 'STRENGTHENING_PENDING'):
            pending.append({**observation, 'display_role': 'ACTIVE_THEME_FERMENTATION_WATCH_UNCONFIRMED'})
            matched_ids.update(intersection)
        elif complete and active and known_confirmation and row.get('stage') == 'DIVERGENCE':
            # The native FSM can flag divergence while activity and its two-day
            # confirmation still pass. Keep that fact visible with its risk;
            # neither erase the mainline nor relabel it as strengthening.
            divergence.append({**observation, 'display_role': 'ACTIVE_THEME_DIVERGENCE_CONFIRMED'})
            matched_ids.update(intersection)
        elif not available_axes or all(exact(row.get(key)) is None for key in ('rs3', 'rs5')):
            incomplete.append({**observation, 'display_role': 'DATA_INCOMPLETE_NOT_WEAK_THEME',
                               'partial_facts_are_not_confirmed_mainline': True})
    # Reuse the existing stage and exact axes; no fitted weights or new gates.
    by_id = {row['theme_id']: row for row in rows}
    def strength(row):
        source = by_id[row['theme_id']]
        rs5 = exact(source.get('rs5'))
        return (source.get('stage') in ('STRENGTHENING', 'STRENGTHENING_PENDING'),
                exact(source.get('expansion')), rs5 is not None, rs5 if rs5 is not None else 0,
                exact(source.get('breadth')))
    confirmed.sort(key=strength, reverse=True)
    pending.sort(key=strength, reverse=True)
    divergence.sort(key=strength, reverse=True)
    result = {'schema_version': VERSION, **source_identity(), 'source_input_hash': input_hash,
        'decision_cutoff': cutoff.isoformat(), 'observation_date': str(bundle.observation_date),
        'theme_context_hash': theme_context['context_content_hash'],
        'frozen_selected_ids': list(selected), 'frozen_selected_count': len(selected),
        'stock_selection_changed': False, 'stage_or_threshold_changed': False,
        'theme_input_record_count': len(bundle.themes), 'observation_theme_row_count': len(rows),
        'mainline_identification_status': 'UNKNOWN' if reasons else 'AVAILABLE_WITH_PER_AXIS_UNKNOWN',
        'report_banner_required': bool(reasons), 'reason_codes': reasons,
        'report_banner': '主线识别不可用：缺少题材来源；本次个股名单不能证明主线覆盖。' if reasons else None,
        'confirmed_mainlines': confirmed, 'fermentation_watch': pending,
        'active_divergence_mainlines': divergence,
        'qualified_theme_data_incomplete': incomplete,
        'model_only_selected_ids': sorted(set(selected) - matched_ids),
        'missing_reason_counts': dict(Counter(reason for row in rows for reason in row.get('reason_codes', ()))),
        'report_scope': 'QUALIFIED_THEME_SOURCE_DIAGNOSTICS_NOT_A_COMPLETE_MAINLINE_REPORT',
        'automatically_publish_all_pending_labels': False,
        'independent_mainline_scope_required_for_user_report': True,
        'membership_coverage_scope': 'SOURCE_DEFINED_COHORT_ONLY',
        'economic_theme_taxonomy_completeness_status': 'UNKNOWN',
        'cohort_transport_completeness_is_not_economic_theme_completeness': True,
        'absence_from_provider_cohort_does_not_disprove_theme_link': True,
        'historical_mainline_coverage_claimed': False, 'formal_focus': False,
        'official_pit_validation_count': 0, 'market_edge': 'NOT_ESTABLISHED'}
    result['diagnostic_content_hash'] = digest_json_stream(result)
    return result
