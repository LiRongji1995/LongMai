"""Read-only scope proof for already registered capture-recovery markers.

Recovery JSON is a locator, never source authority. A successful resolution
authenticates current original captures and binds them to the old request and
all original attempts. It does not import, rewrite, retry or restore a run.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re

from stockfish_core.file_integrity import content_reference_equal


EPOCH_SCHEMA = 'CODEX_CAPTURE_CURRENT_RECERTIFICATION_EPOCH_V1'
_SHA = re.compile(r'[0-9a-f]{64}', re.ASCII)


def inherited_attempt_scope(cache, folder, *, entries=None, retain_known_terminal_failures=False):
    """Return a verified complete old scope, or an explicit retained unknown.

    Only the exact existing inherited-marker layout is supported. Unknown
    layouts, missing links and uncommitted attempts remain blockers. The
    explicit inventory-only mode may retain authenticated committed failures;
    it never declares them complete or authorizes another provider attempt.
    Every read is metered and included in the cache's terminal SHA
    verification. No supplier or bookkeeping mutation is performed.
    """
    from .premarket_capture_cache_v1 import RequestSpec, VERSION, SH, _canonical, _json, _need, _stamp

    folder = Path(folder)
    inherited = folder / 'inherited-original-attempt'
    if not inherited.exists():
        return None
    unknown = dict(status='UNKNOWN_RETAINED_NO_IMPLICIT_RETRY', request_spec=None,
                   source_capability_issued=False)

    def read(path, expected=None):
        raw, reference = cache._read(expected if expected is not None else path, limit=2 * 1024**2)
        return _json(raw), reference

    def terminal(value):
        return (value.get('status') == 'COMPLETE' and value.get('pagination_complete') is True
                or retain_known_terminal_failures and value.get('status') in ('FETCH_FAILED', 'PARTIAL')
                and value.get('pagination_complete') is False)

    try:
        _need(type(retain_known_terminal_failures) is bool, 'INHERITED_EXPLICIT_INVENTORY_POLICY_REQUIRED')
        _need(folder.parent == cache.root / 'raw' and _SHA.fullmatch(folder.name)
              and not folder.is_symlink() and not inherited.is_symlink()
              and set(path.name for path in folder.iterdir()) == {'inherited-original-attempt'}
              and set(path.name for path in inherited.iterdir()) == {'request-claim.json'},
              'INHERITED_MARKER_EXACT_LAYOUT_REQUIRED')
        marker, _ = read(inherited / 'request-claim.json')
        claim, _ = read(cache.root / 'epoch-claim.json')
        complete, _ = read(cache.root / 'epoch-complete.json')
        plan = marker.get('source_plan_sha256')
        _need(isinstance(plan, str) and _SHA.fullmatch(plan)
              and all(row.get('schema_version') == EPOCH_SCHEMA for row in (marker, claim, complete))
              and marker.get('request_key') == folder.name and marker.get('indexed') is True
              and marker.get('status') == 'INHERITED_ORIGINAL_ATTEMPT_NO_IMPLICIT_RETRY'
              and claim.get('status') == 'STARTED_NO_IMPLICIT_RETRY'
              and complete.get('status') == 'CURRENT_ORIGINALS_RECERTIFIED_NEW_METADATA_EPOCH'
              and claim.get('plan_sha256') == complete.get('plan_sha256') == plan,
              'INHERITED_EPOCH_PLAN_OR_STATUS_BINDING_INVALID')
        _need(_stamp(claim['actual_started_at']) <= _stamp(complete['actual_completed_at'])
              <= datetime.now(timezone.utc), 'INHERITED_EPOCH_CLOCK_BINDING_INVALID')
        source_cache = Path(claim['source_cache'])
        _need(source_cache.is_absolute() and not source_cache.is_symlink()
              and source_cache.resolve(strict=True) == source_cache
              and complete.get('source_cache') == str(source_cache)
              and complete.get('cache_root') == str(cache.root)
              and source_cache != cache.root
              and complete.get('network_calls') == 0
              and complete.get('new_market_data') is False
              and complete.get('old_records_rewritten') is False
              and complete.get('source_clocks_rewritten') is False,
              'INHERITED_EPOCH_ROOT_OR_MUTATION_BINDING_INVALID')
        source_attempt = source_cache / 'raw' / folder.name
        _need(marker.get('source_attempt_directory') == str(source_attempt)
              and source_attempt.is_dir() and not source_attempt.is_symlink(),
              'INHERITED_SOURCE_ATTEMPT_BINDING_INVALID')
        mappings = complete.get('request_key_mappings')
        _need(isinstance(mappings, list) and all(isinstance(row, dict) for row in mappings),
              'INHERITED_REQUEST_MAPPING_REQUIRED')
        mappings = [row for row in mappings if row.get('old_request_key') == folder.name]
        _need(mappings and len({row['receipt_id'] for row in mappings}) == len(mappings),
              'INHERITED_REQUEST_MAPPING_AMBIGUOUS')
        by_receipt = {row['receipt_id']: row for row in mappings}
        paths = sorted((source_cache / 'index' / folder.name).glob('*.json'))
        _need(0 < len(paths) <= cache.max_entries, 'INHERITED_OLD_INDEX_REQUIRED')
        originals = []
        for path in paths:
            old, reference = read(path)
            prior = RequestSpec(**old['request_spec'])
            _need(reference['sha256'] == path.stem and old.get('schema_version') == VERSION
                  and old.get('request_key') == prior.key == folder.name
                  and old.get('endpoint') == prior.endpoint and old.get('parameters') == prior.parameters,
                  'INHERITED_OLD_REQUEST_IDENTITY_CHANGED')
            _need(terminal(old),
                  'INHERITED_INCOMPLETE_ORIGINAL_RETAINED')
            originals.append((old, prior))
        _need(set(by_receipt) == {old['receipt_id'] for old, _ in originals},
              'INHERITED_MAPPING_DOES_NOT_COVER_ALL_ORIGINALS')
        values = cache._factor_ledger_entries() if entries is None else entries
        receipt_ids, attempts, accounting = set(), set(), {}
        now = datetime.now(timezone.utc)
        for old, prior in originals:
            mapping = by_receipt[old['receipt_id']]
            keys = mapping.get('current_request_keys')
            _need(isinstance(keys, list) and keys and len(keys) == len(set(keys))
                  and all(isinstance(key, str) and _SHA.fullmatch(key) for key in keys),
                  'INHERITED_CURRENT_REQUEST_KEYS_INVALID')
            for key in keys:
                matching = [value for value in values if value['request_key'] == key
                            and value['receipt_id'] == old['receipt_id']]
                _need(len(matching) == 1, 'INHERITED_CURRENT_INDEX_OR_RECEIPT_UNAVAILABLE')
                current = matching[0]
                index_path = cache.root / 'index' / key / (hashlib.sha256(_canonical(current)).hexdigest() + '.json')
                indexed, _ = read(index_path)
                _need(indexed == current, 'INHERITED_CURRENT_CONTENT_ADDRESSED_INDEX_CHANGED')
                spec = RequestSpec(**current['request_spec'])
                _need(spec.key == key and spec.endpoint == prior.endpoint
                      and spec.parameters == prior.parameters,
                      'INHERITED_CURRENT_REQUEST_IDENTITY_CHANGED')
                _need(terminal(current) and current.get('status') == old.get('status')
                      and current.get('pagination_complete') == old.get('pagination_complete'),
                      'INHERITED_INCOMPLETE_ORIGINAL_RETAINED')
                _need(max(_stamp(current[field]) for field in
                      ('first_seen_at', 'ingested_at', 'completed_at')) <= now,
                      'INHERITED_ORIGINAL_NOT_YET_VISIBLE')
                seed_ref = (current.get('source_provenance') or {}).get('seed')
                _need(isinstance(seed_ref, dict) and seed_ref.get('path') == str(cache.root / 'epoch-seed.json'),
                      'INHERITED_ORIGINAL_SEED_REFERENCE_REQUIRED')
                seed, _ = read(cache.root / 'epoch-seed.json', seed_ref)
                seed_sources = seed.get('sources')
                _need(seed.get('schema_version') == EPOCH_SCHEMA and isinstance(seed_sources, list)
                      and all(isinstance(row, dict) for row in seed_sources) and any(
                    all(content_reference_equal(row.get(role), current[role]) for role in ('raw', 'receipt', 'result'))
                    for row in seed_sources), 'INHERITED_ORIGINAL_NOT_IN_BOUND_SEED')
                _need(all(content_reference_equal(old[role], current[role])
                          for role in ('raw', 'receipt', 'result')),
                      'INHERITED_ORIGINAL_BYTES_OR_LOCATOR_CHANGED')
                # The genuine cache reader, not the epoch JSON, authenticates
                # result/receipt/RAW and their producer, identity and time facts.
                result = cache.verify(current)
                _need(result['endpoint'] == prior.endpoint and result['parameters'] == prior.parameters
                      and result['status'] == old['status']
                      and result['pagination_complete'] == old['pagination_complete']
                      and result['request_receipt_id'] == old['receipt_id'],
                      'INHERITED_AUTHENTICATED_RESULT_SCOPE_CHANGED')
                if retain_known_terminal_failures:
                    coverage = result.get('coverage') or {}
                    actual = {field: coverage.get(field) for field in ('sdk_operations', 'http_attempts')}
                    known = all(type(value) is int and value >= 0 for value in actual.values())
                    # This is older inventory, not a request made by the parent
                    # operation. Preserve unknown counts verbatim (missing is
                    # None); do not manufacture zero or refund any budget.
                    accounting[old['receipt_id']] = dict(actual_counts=actual,
                        actual_counts_status='KNOWN' if known else 'UNKNOWN_RETAINED')
                imported_day = (prior.parameters.get('date') or prior.parameters.get('end_date')
                    or _stamp(current['first_seen_at']).astimezone(SH).date().isoformat())
                _need(spec.business_session == imported_day and spec.current ==
                      (not bool(prior.parameters.get('date') or prior.parameters.get('end_date'))),
                      'INHERITED_IMPORTED_REQUEST_SCOPE_CHANGED')
            attempt = Path(old['capture_attempt_dir'])
            _need(attempt.parent == source_attempt / 'raw' and attempt.is_dir()
                  and not attempt.is_symlink() and not (attempt / 'attempt-failed.json').exists()
                  and all(Path(old[role]['path']).is_relative_to(attempt) for role in ('raw', 'receipt', 'result')),
                  'INHERITED_ORIGINAL_ATTEMPT_NOT_COMPLETE')
            original_claim = attempt / 'request-claim.json'
            if original_claim.exists():
                declaration, _ = read(original_claim)
                claimed = RequestSpec(**declaration['request_spec'])
                _need(declaration.get('schema_version') == VERSION and claimed.key == prior.key,
                      'INHERITED_ORIGINAL_CLAIM_SCOPE_CHANGED')
            committed, _ = read(attempt / 'committed.json')
            _need(committed.get('request_key') == folder.name
                  and committed.get('receipt_id') == old['receipt_id'],
                  'INHERITED_ORIGINAL_COMMIT_BINDING_INVALID')
            attempts.add(attempt)
            receipt_ids.add(old['receipt_id'])
        _need(not (source_attempt / 'raw').is_symlink()
              and set((source_attempt / 'raw').iterdir()) == attempts
              and set(path.name for path in source_attempt.iterdir()) == {'raw'},
              'INHERITED_UNMAPPED_OR_UNFINISHED_ATTEMPT_RETAINED')
        all_complete = all(old['status'] == 'COMPLETE' and old['pagination_complete'] is True
                           for old, _ in originals)
        counts_known = all(value['actual_counts_status'] == 'KNOWN' for value in accounting.values())
        result = dict(status='VERIFIED_COMPLETE_ORIGINAL' if all_complete and counts_known else 'VERIFIED_RETAINED_ORIGINAL',
                    request_spec=originals[0][1].to_dict(),
                    receipt_ids=sorted(receipt_ids), source_capability_issued=False,
                    statuses=[dict(receipt_id=old['receipt_id'], status=old['status'],
                                   pagination_complete=old['pagination_complete'],
                                   **accounting.get(old['receipt_id'], {})) for old, _ in originals],
                    retry_authorized=False,
                    purpose='PRIOR_REQUEST_SCOPE_ONLY_NOT_SOURCE_OR_RETRY_AUTHORITY')
        if retain_known_terminal_failures:
            result.update(inventory_only=True,
                actual_counts_status='KNOWN' if counts_known else 'UNKNOWN_RETAINED',
                actual_counts_affect_parent_budget=False)
        return result
    except (ValueError, TypeError, KeyError, OSError) as error:
        return dict(unknown, reason=str(error)[:512])
