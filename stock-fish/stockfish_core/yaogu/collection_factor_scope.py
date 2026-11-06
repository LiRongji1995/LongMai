"""Reuse exact original factor windows without regrouping failed requests.

This planner describes requests, not authenticated factor availability. Readers
still verify original bytes, completion and visibility. It never composes an
old snapshot with a tail or replaces the provider's immutable left anchor.
"""
from __future__ import annotations

from datetime import date


def plan_factor_requests(cache, *, codes, observed, batch_size, entries=None):
    """Retain matching original batches, then request only uncovered codes.

    Matching failed and future originals are deliberately retained too: their
    original readers must report FAILED/NOT_VISIBLE rather than reclassifying
    a new grouping as a missing request. Extra codes in an old batch are kept.
    No index claim here is promoted to source authority.
    """
    from .premarket_capture_cache_v1 import CODE, RequestSpec, _need

    observed = date.fromisoformat(str(observed)).isoformat()
    required = list(codes)
    _need(len(required) == len(set(required))
          and all(isinstance(code, str) and CODE.fullmatch(code) for code in required),
          'FACTOR_PLAN_UNIQUE_ASCII_CODES_REQUIRED')
    _need(type(batch_size) is int and 1 <= batch_size <= 100, 'PROVIDER_BATCH_SIZE_BOUND')
    if not required:
        return []
    required = set(required)
    originals, covered = {}, set()
    for value in cache._factor_ledger_entries() if entries is None else entries:
        if value['endpoint'] != 'adjustment_factors':
            continue
        spec = RequestSpec(**value['request_spec'])
        _need(spec.key == value['request_key'], 'FACTOR_PLAN_ORIGINAL_REQUEST_IDENTITY_CHANGED')
        params = spec.parameters
        if (params['start_date'] == '1990-01-01' and params['end_date'] == observed
                and required.intersection(params['codes'])):
            originals[spec.key] = spec
            covered.update(params['codes'])
    planned = [originals[key] for key in sorted(originals)]
    missing = sorted(required - covered)
    planned.extend(RequestSpec('adjustment_factors', dict(codes=missing[offset:offset+batch_size],
        start_date='1990-01-01', end_date=observed), observed, role='FULL_ANCHORED_FACTOR_WINDOW')
        for offset in range(0, len(missing), batch_size))
    return planned


def factor_overlap_blockers(cache, spec, *, entries=None):
    """Inspect the existing ledger for failures hidden by a changed batch key.

    A request with an intersecting code and date interval cannot retry an old
    failure or uncommitted attempt. Exact keys remain governed by lookup.
    Completed seed/cache originals need no newly introduced claim file.
    """
    from .premarket_capture_cache_v1 import RequestSpec, VERSION, _json, _need

    if spec.endpoint != 'adjustment_factors':
        return []
    params, blocked, by_key = spec.parameters, {}, {}
    codes = set(params['codes'])

    def overlaps(prior):
        old = prior.parameters
        return (prior.endpoint == 'adjustment_factors' and prior.key != spec.key
                and bool(codes.intersection(old['codes']))
                and old['start_date'] <= params['end_date']
                and params['start_date'] <= old['end_date'])

    def block(key, prior, status):
        blocked[key] = dict(request_key=key,
            request_spec=prior.to_dict() if prior is not None else None, status=status)

    for value in cache._factor_ledger_entries() if entries is None else entries:
        prior = RequestSpec(**value['request_spec'])
        _need(prior.key == value['request_key'], 'CACHE_FACTOR_PRIOR_REQUEST_IDENTITY_CHANGED')
        by_key.setdefault(prior.key, []).append(value)
        if overlaps(prior) and (value['status'] != 'COMPLETE' or not value['pagination_complete']):
            block(prior.key, prior, 'OVERLAPPING_INCOMPLETE_NO_IMPLICIT_RETRY')
    folders = sorted((cache.root / 'raw').iterdir())
    _need(len(folders) <= cache.max_entries, 'CACHE_FACTOR_ATTEMPT_COUNT_BOUND')
    count = 0
    for folder in folders:
        _need(folder.is_dir() and not folder.is_symlink(), 'CACHE_FACTOR_ATTEMPT_DIRECTORY_REQUIRED')
        if folder.name == spec.key or folder.name in blocked:
            continue
        indexed = by_key.get(folder.name, ())
        if indexed and not any(overlaps(RequestSpec(**value['request_spec'])) for value in indexed):
            continue
        root = folder / 'raw'
        _need(not root.is_symlink(), 'CACHE_FACTOR_ATTEMPT_DIRECTORY_REQUIRED')
        attempts = sorted(root.iterdir()) if root.is_dir() else []
        count += len(attempts)
        _need(count <= cache.max_entries, 'CACHE_FACTOR_ATTEMPT_COUNT_BOUND')
        if not attempts and not indexed and any(folder.iterdir()):
            block(folder.name, None, 'UNKNOWN_PRIOR_REQUEST_SCOPE_NO_IMPLICIT_RETRY')
        for attempt in attempts:
            _need(attempt.is_dir() and not attempt.is_symlink(), 'CACHE_FACTOR_ATTEMPT_DIRECTORY_REQUIRED')
            committed = any(value.get('capture_attempt_dir') == str(attempt)
                and value['status'] == 'COMPLETE' and value['pagination_complete'] for value in indexed)
            failed = attempt / 'attempt-failed.json'
            if committed and not failed.exists():
                continue
            if failed.exists():
                cache._read(failed, limit=2 * 1024**2)
            claim = attempt / 'request-claim.json'
            if not claim.exists():
                prior = RequestSpec(**indexed[0]['request_spec']) if indexed else None
                block(folder.name, prior, 'OVERLAPPING_UNFINISHED_NO_IMPLICIT_RETRY' if prior else
                      'UNKNOWN_PRIOR_REQUEST_SCOPE_NO_IMPLICIT_RETRY')
                continue
            raw, _ = cache._read(claim, limit=2 * 1024**2)
            declared = _json(raw)
            prior = RequestSpec(**declared['request_spec'])
            _need(declared['schema_version'] == VERSION and prior.key == folder.name,
                  'CACHE_FACTOR_PRIOR_CLAIM_IDENTITY_CHANGED')
            if overlaps(prior) and (not committed or failed.exists()):
                block(prior.key, prior, 'OVERLAPPING_UNFINISHED_NO_IMPLICIT_RETRY')
    return [blocked[key] for key in sorted(blocked)]
