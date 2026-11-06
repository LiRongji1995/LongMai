"""Conservative factor request pruning using the unchanged dated qualification.

The caller must authenticate each result with CaptureCache.verify first. This
helper issues no source capability, computes no strategy and cannot replace
the final native core's authentication. Unknown qualification retains a code.
"""
from __future__ import annotations

from datetime import date, datetime
import re

from .provider_normalization import normalize_capture_with_report
from .strategy_experiments import _prepare_context


VERSION = 'COLLECTION_QUALIFICATION_V1'
_CODE = re.compile(r'(sh|sz|bj)\.([0-9]{6})', re.ASCII)
_IDENTITY_ENDPOINTS = frozenset(('calendar', 'mainboard_sse', 'master_szse',
    'delisting_sse', 'market_daily', 'master_all', 'stock_basic', 'stock_basic_all'))


def _day(value):
    text = str(value)
    parsed = date.fromisoformat(text)
    if parsed.isoformat() != text:
        raise ValueError('COLLECTION_QUALIFICATION_ISO_DATE_REQUIRED')
    return parsed


def _clock(value):
    parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.utcoffset() is None:
        raise ValueError('COLLECTION_QUALIFICATION_AWARE_CLOCK_REQUIRED')
    return parsed


def factor_query_scope(results, *, requested_codes, observation_date, cutoff,
                       sessions, coverage_type='RECONSTRUCTED_HISTORICAL'):
    """Drop only codes the original, source-qualified rule definitely excludes.

    Dates, incomplete parents or unavailable qualification never authorize a
    smaller request. The original normalizer and qualification resolver own
    every security/status rule; no rule or numerical threshold is copied here.
    All input result bytes must already have passed the cache's authentication.
    """
    codes = list(requested_codes)
    if (any(not isinstance(code, str) or not _CODE.fullmatch(code) for code in codes)
            or len(codes) != len(set(codes))):
        raise ValueError('COLLECTION_QUALIFICATION_UNIQUE_SECURITY_CODES_REQUIRED')
    observed, decision = _day(observation_date), _clock(cutoff)
    days = tuple(_day(day) for day in sessions)
    if not days or days != tuple(sorted(set(days))) or days[-1] != observed:
        raise ValueError('COLLECTION_QUALIFICATION_CALENDAR_SCOPE_REQUIRED')
    output = dict(version=VERSION, status='UNKNOWN_NO_PRUNING',
        observation_date=str(observed), decision_cutoff=decision.isoformat(),
        requested_codes=codes, factor_query_codes=list(codes), skipped_factor_codes=[],
        eligible_ids=[], unknown_ids=[], known_excluded_records=[], qualification=None,
        source_refs=[], reason_codes=[], normalizer_calls=0, strategy_execution_calls=0,
        source_authentication='CALLER_CACHE_VERIFY_REQUIRED',
        qualification_scope='CURRENT_DAY_COLLECTION_PREVIEW_NOT_FINAL_CORE_QUALIFICATION',
        strategy_selection_unchanged=True, source_capabilities_issued=0)
    sources = [row for row in results if row.get('endpoint') in _IDENTITY_ENDPOINTS]

    def unknown(reason):
        output['reason_codes'] = [reason]
        return output

    required = {'calendar', 'mainboard_sse', 'master_szse', 'delisting_sse'}
    if not required.issubset({row.get('endpoint') for row in sources}):
        return unknown('QUALIFICATION_REQUIRED_PARENT_MISSING')
    current = {row.get('endpoint') for row in sources
               if row.get('endpoint') in ('market_daily', 'master_all')
               and row.get('parameters', {}).get('date') == str(observed)}
    if current != {'market_daily', 'master_all'}:
        return unknown('QUALIFICATION_CURRENT_MARKET_PARENT_MISSING')
    ids = set()
    for result in sources:
        if result.get('status') != 'COMPLETE' or result.get('pagination_complete') is not True:
            return unknown('QUALIFICATION_PARENT_INCOMPLETE')
        rid = result.get('request_receipt_id')
        receipts = [row for row in result.get('receipts', ()) if row.get('receipt_id') == rid]
        if not isinstance(rid, str) or not rid or rid in ids or len(receipts) != 1:
            return unknown('QUALIFICATION_PARENT_RECEIPT_UNAVAILABLE_OR_DUPLICATE')
        ids.add(rid)
        receipt = receipts[0]
        try:
            started = _clock(result['started_at'])
            seen = _clock(receipt['first_seen_at'])
            ingested = _clock(receipt['ingested_at'])
            completed = _clock(result['completed_at'])
            if not started <= seen <= ingested <= completed <= decision:
                return unknown('QUALIFICATION_PARENT_NOT_VISIBLE_AT_CUTOFF')
            if result['endpoint'] in ('market_daily', 'master_all'):
                business_day = _day(result['parameters']['date'])
                if business_day not in days:
                    return unknown('QUALIFICATION_PARENT_BUSINESS_DATE_OUTSIDE_SCOPE')
                if result['endpoint'] == 'market_daily' and any(
                        row.get('date') != str(business_day) for row in result['rows']):
                    return unknown('QUALIFICATION_RAW_BUSINESS_DATE_MISMATCH')
        except (KeyError, TypeError, ValueError):
            return unknown('QUALIFICATION_PARENT_CLOCK_OR_DATE_INVALID')
    output['source_refs'] = sorted(ids)
    # This is the same calendar check used by the native core, not a weekday
    # approximation or a source-free calendar supplied by the caller.
    from .premarket_native_core_v1 import _calendar_sessions
    try:
        if _calendar_sessions(sources, observed) != days:
            return unknown('QUALIFICATION_CALENDAR_DIFFERS_FROM_ORIGINAL')
    except (KeyError, TypeError, ValueError):
        return unknown('QUALIFICATION_CALENDAR_ORIGINAL_INVALID')
    # Qualification examines the current dated security state. Historical
    # price bars are still mandatory for the final core, but need not be
    # normalized a second time merely to establish this request scope. Old
    # outcome-only identities may be absent from this preview; requested codes
    # without a definite exclusion below are always retained.
    current_sources = [row for row in sources
        if row['endpoint'] not in ('market_daily', 'master_all')
        or row['parameters']['date'] == str(observed)]
    bundle, _ = normalize_capture_with_report(current_sources, observed, decision, days,
                                             coverage_type=coverage_type)
    output['normalizer_calls'] = 1
    context = _prepare_context(bundle, decision)
    excluded = {row['instrument_id']: row for row in
                context.qualification['known_excluded_records']}
    requested_ids = {code[3:] + '.' + code[:2].upper(): code for code in codes}
    skipped = {requested_ids[iid] for iid in excluded if iid in requested_ids}
    output.update(status='COMPLETE',
        factor_query_codes=[code for code in codes if code not in skipped],
        skipped_factor_codes=[code for code in codes if code in skipped],
        eligible_ids=list(context.eligible_ids),
        unknown_ids=[iid for iid in requested_ids
                     if iid not in excluded and iid not in context.eligible_ids],
        known_excluded_records=[row for iid, row in excluded.items() if iid in requested_ids],
        qualification=context.qualification,
        reason_codes=['ONLY_ORIGINAL_KNOWN_EXCLUSIONS_PRUNED'])
    return output
