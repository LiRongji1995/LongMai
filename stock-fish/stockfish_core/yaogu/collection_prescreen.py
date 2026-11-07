"""Pure optional-collection routing over an authenticated native R2 run.

The caller authenticates the run, exchange calendar and original LHB bytes.
This module neither authenticates a caller's JSON nor changes strategy facts,
selection, ranking or full-pool benchmark inputs. LHB is an existence clue,
not verified seat identity, a funds score or an investment recommendation.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime
import re

from . import short_horizon_strength as r1


VERSION = 'COLLECTION_PRESCREEN_V1'
STRATEGY = 'SHORT_HORIZON_STRENGTH_R2'
WINDOW = 5


def _lhb_disclosure_session(value):
    """Accept only the original date or provider's explicit midnight encoding."""
    if type(value) is not str:
        return None
    match = re.fullmatch(r'([0-9]{4}-[0-9]{2}-[0-9]{2})(?:T00:00:00\.000)?', value, flags=re.ASCII)
    if match is None:
        return None
    try:
        return date.fromisoformat(match.group(1)).isoformat()
    except ValueError:
        return None


def project_lhb_summary(*, eligible_pool_ids, window_sessions, authenticated_sources):
    """Project caller-authenticated originals without issuing source authority.

    ``authenticated_sources`` contains at most one ``(result, metadata)`` pair
    authenticated by the original cache reader. This pure function does not
    authenticate caller JSON. Disclosure dates never replace visibility clocks.
    """
    sessions = list(window_sessions)
    if (len(sessions) != WINDOW or sessions != sorted(set(sessions))
            or any(_lhb_disclosure_session(day) != day for day in sessions)):
        raise ValueError('PRESCREEN_FIVE_ORDERED_WINDOW_SESSIONS_REQUIRED')
    sources = list(authenticated_sources)
    if len(sources) > 1:
        raise ValueError('PRESCREEN_SINGLE_WINDOW_SOURCE_REQUIRED')
    summary = dict(status='UNKNOWN', pagination_complete=False,
                   window_sessions=sessions, source_refs=[], records=[])
    code_ids = {iid.split('.')[0]: iid for iid in eligible_pool_ids}
    for result, metadata in sources:
        if (result['endpoint'] != 'lhb_summary' or result['parameters'] !=
                dict(start_date=sessions[0], end_date=sessions[-1])):
            raise ValueError('PRESCREEN_SOURCE_WINDOW_MISMATCH')
        clocks = [_clock(metadata[key]) for key in ('first_seen_at', 'ingested_at', 'completed_at')]
        if any(clock is None for clock in clocks):
            raise ValueError('PRESCREEN_AWARE_PARENT_CLOCK_REQUIRED')
        visible, refs = max(clocks), [metadata['receipt_id']]
        summary.update(status=result['status'], pagination_complete=result['pagination_complete'],
                       source_refs=refs, visible_at=visible.isoformat())
        if result['status'] == 'COMPLETE' and result['pagination_complete']:
            for row in result['rows']:
                code = row.get('代码') if isinstance(row, dict) else None
                day = _lhb_disclosure_session(row.get('上榜日')) if isinstance(row, dict) else None
                if type(code) is not str or not re.fullmatch(r'[0-9]{6}', code) or day not in sessions:
                    summary.update(status='UNKNOWN', reason='MALFORMED_OR_OUT_OF_WINDOW_DISCLOSURE', records=[])
                    break
                if code in code_ids:
                    summary['records'].append(dict(instrument_id=code_ids[code], session=day,
                        evidence_status='VALID', source_refs=refs, visible_at=visible.isoformat()))
    return summary


def _clock(value):
    try:
        result = datetime.fromisoformat(str(value))
        return result if result.utcoffset() is not None else None
    except (TypeError, ValueError):
        return None


def _refs(value):
    return (list(dict.fromkeys(value)) if isinstance(value, list)
            and all(isinstance(ref, str) and ref for ref in value) else [])


def _window(calendar_sessions, observed):
    sessions = [str(value) for value in calendar_sessions]
    try:
        valid = all(date.fromisoformat(value).isoformat() == value for value in sessions)
    except (TypeError, ValueError):
        valid = False
    if not valid or sessions != sorted(set(sessions)) or observed not in sessions:
        raise ValueError('COLLECTION_PRESCREEN_CALENDAR_INVALID')
    return [value for value in sessions if value <= observed][-WINDOW:]


def _lhb(lhb_summary, window, cutoff):
    """Validate the narrow projection; never infer absence from partial data."""
    summary = lhb_summary or {}
    refs = _refs(summary.get('source_refs'))
    visible = _clock(summary.get('visible_at'))
    complete = (len(window) == WINDOW and summary.get('window_sessions') == window
                and summary.get('status') == 'COMPLETE'
                and summary.get('pagination_complete') is True and bool(refs)
                and visible is not None and visible <= cutoff)
    records = summary.get('records')
    if not isinstance(records, list):
        complete = False
        records = []
    matched = {}
    # The authenticated parent must be complete even for positive clues.
    if complete:
        for row in records:
            if not isinstance(row, dict):
                complete = False
                continue
            row_refs = _refs(row.get('source_refs'))
            clock = _clock(row.get('visible_at'))
            iid = row.get('instrument_id')
            if (row.get('evidence_status') != 'VALID' or not isinstance(iid, str) or not iid
                    or row.get('session') not in window or not row_refs
                    or not set(row_refs).issubset(refs) or clock is None
                    or clock > cutoff or clock > visible):
                complete = False
                continue
            matched.setdefault(iid, []).append({
                'session': row['session'], 'source_refs': row_refs,
                'visible_at': clock.isoformat()})
    # Invalid projected records also prevent a complete-no-record assertion.
    return complete, matched if complete else {}, refs


def _amount_channel(item, observed):
    liquidity = item.get('liquidity') or {}
    evidence = liquidity.get('evidence') or {}
    try:
        amount = r1.exact(liquidity.get('value'))
        reference = r1.exact(liquidity.get('reference_cny'))
    except (ValueError, TypeError, KeyError, ZeroDivisionError, OverflowError):
        amount = reference = None
    amount_known = (liquidity.get('status') == 'AVAILABLE'
                    and evidence.get('evidence_status') == 'VALID'
                    and evidence.get('session') == observed
                    and bool(_refs(evidence.get('source_refs')))
                    and amount is not None and amount >= 0
                    and reference is not None and reference > 0)
    amount_state = ('PASS' if amount >= reference else 'FAIL') if amount_known else 'UNKNOWN'
    participation = item.get('participation') or {}
    participation_state = participation.get('status')
    if participation_state not in ('PASS', 'FAIL', 'UNKNOWN'):
        participation_state = 'UNKNOWN'
    combined = r1.combine({'amount': {'status': amount_state},
                           'participation': {'status': participation_state}})['status']
    source_refs = _refs(evidence.get('source_refs'))
    for row in participation.get('observations') or []:
        if isinstance(row, dict) and row.get('status') == 'VALID':
            source_refs.extend(_refs(row.get('source_refs')))
    return {'status': combined, 'amount_status': amount_state,
            'participation_status': participation_state,
            'amount_cny': r1.encode(amount) if amount_known else None,
            'reference_cny': r1.encode(reference) if amount_known else None,
            'source_refs': list(dict.fromkeys(source_refs))}


def build_collection_prescreen(core_run, *, calendar_sessions, lhb_summary):
    """Return every eligible R2 row and its optional deep-collection route.

    ``calendar_sessions`` is the caller-authenticated exchange calendar, not
    natural dates. ``lhb_summary`` is a verified projection containing status,
    pagination_complete, window_sessions, source_refs, visible_at and records.
    Each record contains instrument_id, session, evidence_status, source_refs
    and visible_at. COMPLETE requires the original parent and pagination to be
    complete; PARTIAL/failed/missing sources never imply a positive or no LHB.
    No net-buy value, reason text or post-listing return is read.
    """
    result = (core_run.get('results') or {}).get(STRATEGY)
    if not isinstance(result, dict) or result.get('strategy_id') != STRATEGY:
        raise ValueError('COLLECTION_PRESCREEN_NATIVE_R2_REQUIRED')
    observed = str(core_run.get('observation_date'))
    cutoff = _clock(core_run.get('decision_cutoff'))
    if (cutoff is None or result.get('observation_date') != observed
            or _clock(result.get('decision_cutoff')) != cutoff):
        raise ValueError('COLLECTION_PRESCREEN_R2_SCOPE_MISMATCH')
    eligible, items = result.get('eligible_pool_ids'), result.get('items')
    if (not isinstance(eligible, list) or not isinstance(items, list)
            or any(not isinstance(iid, str) or not iid for iid in eligible)
            or len(eligible) != len(set(eligible))
            or any(not isinstance(item, dict) for item in items)):
        raise ValueError('COLLECTION_PRESCREEN_FULL_POOL_REQUIRED')
    ids = [item.get('instrument_id') for item in items]
    if (result.get('full_eligible_pool_retained') is not True
            or any(not isinstance(iid, str) for iid in ids)
            or len(ids) != len(set(ids)) or set(ids) != set(eligible)
            or result.get('research_selected_ids') != [item['instrument_id'] for item in items
                                                       if item.get('research_selected') is True]):
        raise ValueError('COLLECTION_PRESCREEN_FULL_POOL_REQUIRED')
    window = _window(calendar_sessions, observed)
    complete, lhb_records, lhb_refs = _lhb(lhb_summary, window, cutoff)
    rows = []
    for item in items:
        iid = item['instrument_id']
        records = lhb_records.get(iid, [])
        lhb_state = 'PASS' if records else 'FAIL' if complete else 'UNKNOWN'
        amount = _amount_channel(item, observed)
        route = r1.combine({'lhb': {'status': lhb_state}, 'amount': amount}, any_pass=True)['status']
        rows.append({'instrument_id': iid,
            'status': {'PASS': 'ADMITTED', 'FAIL': 'NOT_PRIORITIZED', 'UNKNOWN': 'UNKNOWN'}[route],
            'admitted_by': (['LHB_CLUE'] if lhb_state == 'PASS' else [])
                           + (['STRONG_AMOUNT_AND_PARTICIPATION'] if amount['status'] == 'PASS' else []),
            'original_r2_selected': item.get('research_selected') is True,
            'original_r2_decision_status': item.get('decision_status'),
            'lhb': {'status': lhb_state, 'records': records, 'source_refs': list(lhb_refs)},
            'strong_amount': amount})
    counts = Counter(row['status'] for row in rows)
    return {'version': VERSION, 'purpose': 'OPTIONAL_DEEP_COLLECTION_ROUTING_ONLY',
        'core_run_id': core_run.get('run_id'), 'r2_source_hash': result.get('source_hash'),
        'observation_date': observed, 'decision_cutoff': cutoff.isoformat(),
        'window_sessions': window, 'lhb_window_complete': complete,
        'lhb_source_status': (lhb_summary or {}).get('status', 'UNKNOWN'),
        'lhb_source_refs': list(lhb_refs),
        'market_reference_source_refs': _refs((result.get('market_turnover_context') or {}).get('source_refs')),
        'parameter_status': 'UNVALIDATED_COLLECTION_ROUTING', 'market_edge': 'NOT_ESTABLISHED',
        'source_authentication': 'REQUIRED_FROM_CALLER_ORIGINAL_READERS',
        'strategy_selection_unchanged': True, 'full_pool_inputs_required': True,
        'not_prioritized_is_strategy_rejection': False,
        'original_r2_selected_ids': list(result['research_selected_ids']),
        'eligible_pool_ids': list(eligible), 'items': rows,
        'admitted_ids': [row['instrument_id'] for row in rows if row['status'] == 'ADMITTED'],
        'not_prioritized_ids': [row['instrument_id'] for row in rows if row['status'] == 'NOT_PRIORITIZED'],
        'unknown_ids': [row['instrument_id'] for row in rows if row['status'] == 'UNKNOWN'],
        'counts': {state: counts[state] for state in ('ADMITTED', 'NOT_PRIORITIZED', 'UNKNOWN')}}
