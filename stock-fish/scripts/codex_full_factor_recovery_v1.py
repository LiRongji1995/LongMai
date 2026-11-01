"""Explicit historical factor recovery; no strategy, current-day gate or retry loop.

The caller owns the registered recovery slot and its durable operation ledger.
This module appends fresh attempts and indexes to the same registered cache,
only for the plan's exact keys. Old files and the original failed job stay intact.
"""
from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timezone
from copy import deepcopy
import hashlib
import importlib.util
import inspect
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import build_premarket_source_bundle_v3 as bundle
from stockfish_core.yaogu import premarket_capture_cache_v1 as native

VERSION = 'CODEX_FULL_FACTOR_HISTORICAL_RECOVERY_V1'
OBSERVATION = '2026-10-28'
TARGET = '2026-10-29'
MAX_FILES = 100000
MAX_CONTROL = 16 * 1024**2
OUTER_WALL_RESERVE_SECONDS = 60


def _metered(function):
    def call(*args, **kwargs):
        budget = inspect.signature(function).bind(*args, **kwargs).arguments['budget']
        with (nullcontext() if getattr(budget, '_meter_active', False) else budget.meter_files()):
            return function(*args, **kwargs)
    return call


def need(condition, reason):
    if not condition:
        raise ValueError('FACTOR_RECOVERY_' + reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':')).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _clock(value):
    need(type(value) is str, 'AWARE_CLOCK_REQUIRED')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    need(result.tzinfo is not None and result.utcoffset() is not None, 'AWARE_CLOCK_REQUIRED')
    return result


def _number(value, label, *, integer=False):
    need(type(value) in ((int,) if integer else (int, float)) and math.isfinite(value)
         and value >= 0, 'FINITE_NONNEGATIVE_' + label)
    return value


def _read(path, refs, budget=None, expected=None):
    raw, reference = bundle.read(Path(path), MAX_CONTROL)
    if budget is not None and not getattr(budget, '_meter_active', False):
        budget.charge_read(len(raw))
    if expected is not None:
        need(reference == expected, 'SOURCE_REFERENCE_CHANGED')
    previous = refs.get(reference['path'])
    need(previous is None or previous == reference, 'SOURCE_CHANGED_DURING_CALL')
    refs[reference['path']] = reference
    return native._json(raw)


def _verify_refs(references, budget=None):
    refs = {}
    for expected in references:
        raw, current = bundle.read(Path(expected['path']), MAX_CONTROL)
        if budget is not None and not getattr(budget, '_meter_active', False):
            budget.charge_read(len(raw))
        need(current == expected, 'SOURCE_REFERENCE_CHANGED')
        refs[current['path']] = current
    return refs


def _inventory(root):
    """Bounded physical inventory, without following the native locator links."""
    root = Path(root).absolute()
    with bundle._directory_fd(root):
        pass
    rows, pending = [], [(root, 0)]
    while pending:
        directory, depth = pending.pop()
        need(depth <= 64, 'TREE_DEPTH_BOUND')
        with bundle._directory_fd(directory) as fd:
            for name in sorted(os.listdir(fd)):
                path = directory / name
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                need(len(rows) < MAX_FILES, 'TREE_COUNT_BOUND')
                if stat.S_ISDIR(info.st_mode):
                    rows.append(dict(path=str(path), kind='directory', identity=[info.st_dev, info.st_ino, info.st_mode]))
                    pending.append((path, depth + 1))
                elif stat.S_ISREG(info.st_mode):
                    need(info.st_nlink == 1 and not getattr(info, 'st_flags', 0) & 0x40000000, 'TREE_FILE_ALIAS')
                    rows.append(dict(path=str(path), kind='file', bytes=info.st_size, fp=bundle._fp(info)))
                elif stat.S_ISLNK(info.st_mode):
                    rows.append(dict(path=str(path), kind='symlink', target=os.readlink(name, dir_fd=fd), fp=bundle._fp(info)))
                else:
                    raise ValueError('FACTOR_RECOVERY_SPECIAL_FILE')
    return sorted(rows, key=lambda r: r['path'])


def _size(rows):
    return sum(row.get('bytes', 0) for row in rows)


def budget_baseline(runtime, old_job):
    """Read actual original accounting; missing counters never become zero."""
    runtime.check_binding()
    refs = {}
    profile = runtime.profile
    job = Path(old_job).absolute()
    need(job.parent == Path(profile['state_root']) / 'jobs', 'ORIGINAL_JOB_LOCATION')
    configuration = _read(profile['config'], refs)
    job_config = _read(job / 'configuration.json', refs)
    final = _read(job / 'final-control.json', refs)
    completion = _read(job / 'worker-completion.json', refs, expected=final['completion'])
    registry = _read(job / 'code-registry.json', refs, expected=final['code_registry'])
    need(type(registry) is list and registry, 'ORIGINAL_CODE_REGISTRY_REQUIRED')
    need(final['status'] == completion['status'] == 'FAILED' and final['job'] == str(job)
         and final['external_worker_reaped'] is True, 'FAILED_REAPED_ORIGINAL_JOB_REQUIRED')
    need(completion['phases']['capture'] == 'FAILED' and completion['phases']['core'] == 'NOT_RUN'
         and completion['expected_sessions'][:2] == [OBSERVATION, TARGET], 'ORIGINAL_CAPTURE_FAILURE_SCOPE')
    need(completion['error']['reason'] == 'ORIGINAL_RAW_FIRST_SEEN_REQUIRED_NOT_BACKFILLED', 'ORIGINAL_FAILURE_REASON')
    operations = []
    for path in sorted((runtime.directory / 'operations').glob('*.json')):
        value = _read(path, refs)
        identity = value.get('identity', {})
        if (identity.get('observation_date'), identity.get('target_session')) == (OBSERVATION, TARGET):
            operations.append((path, value))
    need(len(operations) == 1, 'EXACTLY_ONE_ORIGINAL_OPERATION')
    operation_path, operation = operations[0]
    need(operation['phase'] == 'FAILED_OR_UNKNOWN' and operation['identity']['backend'] == 'premarket'
         and operation['identity']['execution_engine'] == 'original', 'ORIGINAL_OPERATION_NOT_FAILED')
    need(operation['identity']['configuration'] == refs[str(Path(profile['config']).absolute())], 'ORIGINAL_CONFIGURATION_BINDING')
    limits = configuration['limits']
    metrics = completion['metrics']
    from stockfish_core.yaogu.premarket_runtime_v1 import DEFAULT_LIMITS
    need(type(limits) is dict and set(limits) == set(DEFAULT_LIMITS), 'EXACT_ORIGINAL_LIMIT_FIELDS')
    need(limits == job_config['limits'] == metrics['limits'], 'ORIGINAL_LIMITS_DISAGREE')
    for name, value in limits.items():
        _number(value, 'LIMIT_' + name, integer=name not in ('max_seconds', 'request_timeout_seconds'))
        need(value > 0, 'POSITIVE_LIMIT_REQUIRED')
    measured_wall = (_clock(operation['finished_at']) - _clock(operation['started_at'])).total_seconds()
    _number(measured_wall, 'ORIGINAL_UTC_WALL')
    measured = max(measured_wall, _number(final['elapsed_seconds'], 'SUPERVISOR_ELAPSED'),
                   _number(metrics['elapsed_seconds'], 'WORKER_ELAPSED'))
    calls = metrics['calls']
    consumed = {name: _number(calls[name], name, integer=True)
                for name in ('requests', 'sdk_reserved', 'http_reserved')}
    consumed.update(wall_seconds=math.ceil(measured) + OUTER_WALL_RESERVE_SECONDS,
        capture_read_bytes=_number(metrics['returned_read_bytes']['capture'], 'CAPTURE_READ', integer=True),
        supervisor_read_bytes=_number(final['supervisor_returned_read_bytes'], 'SUPERVISOR_READ', integer=True),
        output_bytes=_size(_inventory(job)))
    remaining = dict(limits)
    for limit, usage in [('max_requests', 'requests'), ('max_sdk_calls', 'sdk_reserved'),
                         ('max_http_attempts', 'http_reserved'), ('max_seconds', 'wall_seconds'),
                         ('capture_read_bytes', 'capture_read_bytes'),
                         ('supervisor_read_bytes', 'supervisor_read_bytes'), ('output_bytes', 'output_bytes')]:
        remaining[limit] -= consumed[usage]
        need(remaining[limit] > 0, 'ORIGINAL_BUDGET_EXHAUSTED:' + limit)
    remaining['capture_read_bytes'] = min(remaining['capture_read_bytes'], remaining['supervisor_read_bytes'])
    remaining['request_timeout_seconds'] = min(remaining['request_timeout_seconds'], remaining['max_seconds'])
    runtime.check_binding()
    _verify_refs(list(refs.values()))
    return dict(schema_version=VERSION, original_limits=limits, consumed=consumed,
        remaining_limits=remaining, references=sorted(refs.values(), key=lambda r: r['path']),
        old_operation=dict(path=str(operation_path), value=operation), old_job=str(job),
        original_metrics=metrics, measured_wall_seconds=measured_wall,
        conservative_outer_reserve_seconds=OUTER_WALL_RESERVE_SECONDS,
        preserved_unknown_actual_counts={k: calls.get(k) for k in ('sdk_actual', 'http_actual',
            'sdk_actual_known_subtotal', 'http_actual_known_subtotal', 'actual_unknown')})


class _ReadOnlyCache(native.CaptureCache):
    def _save(self, *_args, **_kwargs):
        raise ValueError('FACTOR_RECOVERY_READ_ONLY_CACHE')
    def _mkdir_owned(self, *_args, **_kwargs):
        raise ValueError('FACTOR_RECOVERY_READ_ONLY_CACHE')
    def _writer(self):
        raise ValueError('FACTOR_RECOVERY_READ_ONLY_CACHE')


def _cache(root, budget):
    root = Path(root).absolute()
    for name in ('', 'index', 'raw', 'raw-index', 'locators'):
        with bundle._directory_fd(root / name):
            pass
    return _ReadOnlyCache(root, budget, dict(offline=True, retry_failed=False))


def _entry(cache, value):
    return cache.verify(value)


def _scope(sources):
    master, sse, szse = (sources[name]['rows'] for name in ('master_all', 'mainboard_sse', 'master_szse'))
    for name, result in sources.items():
        native._identity_rows(name, result['rows'], result['parameters'])
    roster_ids = ['sh.' + row['证券代码'] for row in sse] + ['sz.' + row['A股代码'] for row in szse]
    need(len(set(roster_ids)) == len(roster_ids), 'CONFLICTING_CURRENT_OFFICIAL_IDENTITY')
    roster = {'sh.' + row['证券代码'] for row in sse}
    roster |= {'sz.' + row['A股代码'] for row in szse if row.get('板块') == '主板'}
    return sorted({row['code'] for row in master if row['code'] in roster and native.ORDINARY.fullmatch(row['code'])})


def _batch_specs(codes):
    return [native.RequestSpec('adjustment_factors', dict(codes=codes[i:i+50],
        start_date='1990-01-01', end_date=OBSERVATION), OBSERVATION, role='FULL_ANCHORED_FACTOR_WINDOW')
        for i in range(0, len(codes), 50)]


@_metered
def plan(runtime, bundle_directory, manifest_sha256, old_job, *, budget):
    baseline = budget_baseline(runtime, old_job)
    for name, maximum in baseline['remaining_limits'].items():
        need(budget.limits[name] <= maximum, 'BUDGET_EXCEEDS_REMAINING:' + name)
    refs = {r['path']: r for r in baseline['references']}
    verification = bundle.verify(bundle_directory, manifest_sha256)
    manifest = _read(Path(bundle_directory) / bundle.MANIFEST, refs, budget)
    guard_refs = [manifest['files'][name] for name in manifest['files']]
    for ref in guard_refs:
        if not getattr(budget, '_meter_active', False):
            budget.charge_read(ref['bytes'])
    root = Path(runtime.profile['state_root']) / 'cache'
    tree = _inventory(root)
    need(_size(tree) <= baseline['original_limits']['cache_bytes'], 'ORIGINAL_CACHE_CAPACITY')
    cache = _cache(root, budget)
    entries = []
    for path in sorted((root / 'index').glob('*/*.json')):
        value = _read(path, refs, budget)
        need(path.stem == digest(value) and path.parent.name == value['request_key'], 'INDEX_CONTENT_ADDRESS')
        entries.append(value)
    selected = {}
    scope_metadata = {}
    for endpoint in ('master_all', 'mainboard_sse', 'master_szse'):
        candidates = [v for v in entries if v['endpoint'] == endpoint and
            v['parameters'] == ({'date': OBSERVATION} if endpoint == 'master_all' else {}) and
            v['request_spec']['business_session'] == (OBSERVATION if endpoint == 'master_all' else TARGET)]
        need(len(candidates) == 1, 'EXACT_ORIGINAL_SCOPE_SOURCE:' + endpoint)
        item = candidates[0]
        result = _entry(cache, item)
        need(result['status'] == 'COMPLETE' and result['pagination_complete'] is True, 'COMPLETE_SCOPE_SOURCE')
        need(_clock(baseline['old_operation']['value']['started_at']) <= _clock(item['started_at'])
             <= _clock(item['completed_at']) <= _clock(baseline['old_operation']['value']['finished_at']), 'SCOPE_OUTSIDE_ORIGINAL_JOB')
        selected[endpoint], scope_metadata[endpoint] = result, item
    codes = _scope(selected)
    need(codes, 'EMPTY_ORIGINAL_FULL_SCOPE')
    batches, incomplete_evidence = [], []
    for ordinal, spec in enumerate(_batch_specs(codes), 1):
        candidates = [v for v in entries if v['request_key'] == spec.key]
        need(len(candidates) <= 1, 'CONFLICTING_ORIGINAL_FACTOR_ENTRIES')
        old = candidates[0] if candidates else None
        if old is not None:
            need(old['request_spec'] == spec.to_dict(), 'ORIGINAL_FACTOR_SPEC_CHANGED')
            result = _entry(cache, old)
            action = 'REUSE_COMPLETE' if result['status'] == 'COMPLETE' else 'RETRY_PARTIAL_ONCE'
            need(action == 'REUSE_COMPLETE' or result['status'] == 'PARTIAL', 'UNREGISTERED_FAILURE_KIND')
        else:
            action = 'NEW_ONCE'
        attempt_root = root / 'raw' / spec.key
        attempt_files = []
        if attempt_root.exists():
            for row in _inventory(attempt_root):
                if row['kind'] == 'file':
                    path = Path(row['path'])
                    if path.suffix == '.json':
                        value = _read(path, refs, budget)
                        attempt_files.append(refs[str(path)])
                        if path.name == 'attempt-failed.json':
                            incomplete_evidence.append(dict(request_key=spec.key, failure=value))
            if old is None:
                failure = [r for r in incomplete_evidence if r['request_key'] == spec.key]
                need(len(failure) == 1 and failure[0]['failure']['reason'] ==
                    'ORIGINAL_RAW_FIRST_SEEN_REQUIRED_NOT_BACKFILLED', 'UNINDEXED_ATTEMPT_NEEDS_ORIGINAL_FAILURE')
                action = 'RETRY_UNAUTHENTICATED_ONCE'
        need(old is None or attempt_files, 'ORIGINAL_ATTEMPT_EVIDENCE_REQUIRED')
        batches.append(dict(ordinal=ordinal, request_key=spec.key, request_spec=spec.to_dict(),
            action=action, original_record=old, old_attempt_references=attempt_files))
    wanted = [b for b in batches if b['action'] != 'REUSE_COMPLETE']
    retry_keys = [b['request_key'] for b in wanted if b['action'].startswith('RETRY_')]
    need(len(retry_keys) == 2, 'EXACT_TWO_ORIGINAL_INCOMPLETE_REQUESTS')
    expected = dict(requests=len(wanted), sdk_reserved=sum(len(b['request_spec']['parameters']['codes'])+2 for b in wanted), http_reserved=0)
    for name, limit in [('requests', 'max_requests'), ('sdk_reserved', 'max_sdk_calls'), ('http_reserved', 'max_http_attempts')]:
        need(expected[name] <= budget.limits[limit], 'WHOLE_STAGE_BUDGET_REQUIRED:' + name)
    for ref in cache.terminal():
        refs[ref['path']] = ref
    need(_inventory(root) == tree, 'ORIGINAL_CACHE_TREE_CHANGED')
    _verify_refs(list(refs.values()), budget)
    bundle.verify(bundle_directory, manifest_sha256)
    runtime.check_binding()
    value = dict(schema_version=VERSION, observation_date=OBSERVATION, target_session=TARGET,
        purpose='EXPLICIT_HISTORICAL_LATE_FACTOR_RECOVERY_ONLY', source_root=runtime.profile['source_root'],
        cache_root=str(root), old_job=str(Path(old_job).absolute()), budget_baseline=baseline,
        bundle_directory=str(Path(bundle_directory).absolute()), bundle_manifest_sha256=manifest_sha256,
        bundle_verification=verification, bundle_references=guard_refs, original_cache_inventory=tree,
        scope_sources=scope_metadata, requested_codes=codes, requested_codes_count=len(codes),
        scope_sha256=digest(codes), batches=batches, retry_keys=retry_keys, planned_new_calls=expected,
        incomplete_original_evidence=incomplete_evidence, references=sorted(refs.values(), key=lambda r: r['path']),
        strategy_executed=False, source_capability_issued=False, historical_pit_restored=False)
    value['plan_hash'] = digest(value)
    return value


def _original_inventory(value, *, appended=False):
    current = _inventory(value['cache_root'])
    if not appended:
        need(current == value['original_cache_inventory'], 'ORIGINAL_CACHE_CHANGED')
        return
    by_path = {r['path']:r for r in current}
    previous = {r['path']:r for r in value['original_cache_inventory']}
    need(all(by_path.get(path) == old for path, old in previous.items()), 'ORIGINAL_CACHE_CHANGED')
    allowed = [Path(value['cache_root']) / kind / r['request_key'] for r in value['batches']
               if r['action'] != 'REUSE_COMPLETE' for kind in ('raw', 'index')]
    need(all(any(Path(path).is_relative_to(root) for root in allowed) for path in set(by_path)-set(previous)),
         'UNAUTHORIZED_CACHE_APPEND')


def _validate_appends(value, records):
    """Each planned key owns one fresh native attempt and one exact new index."""
    root = Path(value['cache_root'])
    old = {row['path'] for row in value['original_cache_inventory']}
    attempts, indexes, keys = [], set(), set()
    for record in records:
        key = record['request_key']
        path = Path(record['capture_attempt_dir'])
        need(key not in keys and path.parent == root / 'raw' / key / 'raw'
             and re.fullmatch('[0-9a-f]{32}', path.name) and str(path) not in old,
             'EXACTLY_ONE_FRESH_ATTEMPT_PER_KEY')
        keys.add(key)
        attempts.append(path)
        indexes.add(root / 'index' / key / (digest(record) + '.json'))
    for row in _inventory(root):
        if row['path'] in old:
            continue
        path = Path(row['path'])
        owned = path in indexes or any(path.is_relative_to(attempt) for attempt in attempts)
        ancestor = row['kind'] == 'directory' and any(target.is_relative_to(path) for target in [*attempts, *indexes])
        need(owned or ancestor, 'APPEND_OUTSIDE_EXACT_NEW_ATTEMPT_OR_INDEX')


def _validate_plan(value, budget, *, appended=False):
    need(value.get('schema_version') == VERSION and value.get('plan_hash') == digest({k:v for k,v in value.items() if k != 'plan_hash'}), 'PLAN_HASH')
    need(value['observation_date'] == OBSERVATION and value['target_session'] == TARGET, 'HISTORICAL_SCOPE_ONLY')
    for name, maximum in value['budget_baseline']['remaining_limits'].items():
        need(budget.limits[name] <= maximum, 'BUDGET_EXCEEDS_PLAN_REMAINDER')
    _verify_refs(value['references'], budget)
    _original_inventory(value, appended=appended)
    cache = _cache(value['cache_root'], budget)
    results = {name: _entry(cache, record) for name, record in value['scope_sources'].items()}
    codes = _scope(results)
    need(codes == value['requested_codes'] and len(codes) == value['requested_codes_count'] and digest(codes) == value['scope_sha256'], 'EXACT_FULL_SCOPE_REQUIRED')
    specs = _batch_specs(codes)
    need(len(specs) == len(value['batches']) and all(spec.key == row['request_key'] and spec.to_dict() == row['request_spec']
         for spec, row in zip(specs, value['batches'])), 'ORIGINAL_BATCHES_REQUIRED')
    for row in value['batches']:
        old = row['original_record']
        files = [Path(r['path']) for r in value['references'] if Path(r['path']).parent ==
                 Path(value['cache_root']) / 'index' / row['request_key']]
        need(len(files) <= 1, 'CONFLICTING_ORIGINAL_FACTOR_ENTRIES')
        actual = _read(files[0], {}, budget) if files else None
        need(actual == old, 'ORIGINAL_CACHE_ENTRY_CHANGED')
        if old is not None:
            result = _entry(cache, old)
            need(row['action'] == ('REUSE_COMPLETE' if result['status'] == 'COMPLETE' else 'RETRY_PARTIAL_ONCE'), 'ACTION_DISAGREES_WITH_ORIGINAL')
        else:
            attempted = any(Path(r['path']).is_relative_to(Path(value['cache_root']) / 'raw' / row['request_key'])
                            for r in value['original_cache_inventory'])
            need(row['action'] == ('RETRY_UNAUTHENTICATED_ONCE' if attempted else 'NEW_ONCE'), 'INVALID_ACTION')
    wanted = [r for r in value['batches'] if r['action'] != 'REUSE_COMPLETE']
    need(value['retry_keys'] == [r['request_key'] for r in wanted if r['action'].startswith('RETRY_')]
         and len(value['retry_keys']) == 2, 'EXACT_RETRY_KEYS_REQUIRED')
    need(value['planned_new_calls'] == dict(requests=len(wanted),
        sdk_reserved=sum(len(r['request_spec']['parameters']['codes'])+2 for r in wanted), http_reserved=0), 'PLANNED_CALLS_CHANGED')
    bundle.verify(value['bundle_directory'], value['bundle_manifest_sha256'])
    cache.terminal()
    return cache


def _parent(value):
    name = 'stockfish_core.yaogu._explicit_factor_recovery_' + value['bundle_manifest_sha256'][:16]
    path = Path(value['bundle_directory']) / 'stockfish_core/yaogu/provider_requests.py'
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _remaining_seconds(budget):
    budget.check('RECOVERY_REAL_WALL_DEADLINE')
    monotonic_remaining = budget.limits['max_seconds'] - (time.monotonic() - budget.started)
    if callable(getattr(budget, 'remaining_seconds', None)):
        actual_remaining = budget.remaining_seconds()
    else:
        started = getattr(budget, 'utc_started_at', None)
        need(isinstance(started, datetime) and started.tzinfo is not None and started.utcoffset() is not None,
             'ACTUAL_UTC_BUDGET_START_REQUIRED')
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        need(elapsed >= 0, 'UTC_CLOCK_MOVED_BACKWARD')
        actual_remaining = budget.limits['max_seconds'] - elapsed
    need(type(actual_remaining) in (int, float) and math.isfinite(actual_remaining), 'FINITE_UTC_REMAINDER_REQUIRED')
    remaining = min(monotonic_remaining, actual_remaining)
    need(remaining > 0, 'RECOVERY_REAL_WALL_DEADLINE_EXCEEDED')
    return remaining


@_metered
def collect(source_plan, job, budget, *, checkpoint):
    need(callable(checkpoint), 'DURABLE_CHECKPOINT_CALLBACK_REQUIRED')
    job = Path(job).absolute()
    with bundle._directory_fd(job):
        pass
    old = _validate_plan(source_plan, budget)
    _remaining_seconds(budget)
    budget.cache_dir = Path(source_plan['cache_root'])
    budget.check('ORIGINAL_SHARED_CACHE_CAPACITY', force=True)
    parent = _parent(source_plan)
    producer_refs = [r for r in source_plan['bundle_references'] if Path(r['path']).name in native.PRODUCERS]
    need(len(producer_refs) == 3, 'THREE_BUNDLE_PRODUCERS_REQUIRED')
    allow = {row['request_key']:row for row in source_plan['batches'] if row['action'] != 'REUSE_COMPLETE'}
    attempted, claimed_at = set(), datetime.now(timezone.utc).isoformat()
    checkpoint(dict(event='SOURCE_COLLECTION_START', plan_hash=source_plan['plan_hash'], claimed_at=claimed_at,
                    requested_codes_count=source_plan['requested_codes_count']))
    def execute(endpoint, parameters, output, **kwargs):
        spec = native.RequestSpec(endpoint, parameters, OBSERVATION, role='FULL_ANCHORED_FACTOR_WINDOW')
        need(spec.key in allow and spec.key not in attempted, 'REQUEST_NOT_IN_EXPLICIT_ONE_ATTEMPT_PLAN')
        remaining = _remaining_seconds(budget)
        kwargs['timeout'] = min(kwargs['timeout'], remaining)
        need(_size(_inventory(source_plan['cache_root']))
             + 16 * 1024**2 <= source_plan['budget_baseline']['original_limits']['cache_bytes'], 'COMBINED_CACHE_CAPACITY')
        checkpoint(dict(event='BEFORE_REQUEST', request_key=spec.key, request_spec=spec.to_dict(),
                        action=allow[spec.key]['action'], output=str(output), actual_started_at=datetime.now(timezone.utc).isoformat()))
        attempted.add(spec.key)
        return parent.execute_request(endpoint, parameters, output, **kwargs)
    class Cache(native.CaptureCache):
        def _producer_files(self, result, declared=()):
            return super()._producer_files(result, declared or producer_refs)
        def capture_one(self, spec):
            need(spec.key in allow and spec.key not in attempted, 'EXPLICIT_RECOVERY_KEY_ONLY')
            self.config['retry_failed'] = spec.key in source_plan['retry_keys']
            try:
                return super().capture_one(spec)
            finally:
                self.config['retry_failed'] = False
    cache = Cache(source_plan['cache_root'], budget, config=dict(offline=False, retry_failed=False,
        request_timeout_seconds=min(90, budget.limits['request_timeout_seconds']), max_http_requests_per_request=1), executor=execute)
    records, new_records = [], []
    for row in source_plan['batches']:
        budget.check('EXPLICIT_FACTOR_BATCH')
        _remaining_seconds(budget)
        if row['action'] == 'REUSE_COMPLETE':
            _entry(old, row['original_record'])
            records.append(row['original_record'])
            continue
        checkpoint(dict(event='BEFORE_CAPTURE_ATTEMPT', request_key=row['request_key'],
                        request_spec=row['request_spec'], action=row['action']))
        record = cache.capture_one(native.RequestSpec(**row['request_spec']))
        result = cache.verify(record)
        _remaining_seconds(budget)
        metadata = record.to_dict()
        need(_clock(metadata['first_seen_at']) >= _clock(claimed_at), 'NEW_CAPTURE_CLOCK_PRECEDES_RECOVERY')
        records.append(metadata)
        new_records.append(metadata)
        checkpoint(dict(event='AFTER_RESULT', request_key=row['request_key'], status=result['status'],
                        receipt_id=record.receipt_id, original_record=metadata))
    old.terminal()
    new_refs = cache.terminal()
    references = {r['path']:r for r in source_plan['references'] + new_refs}
    complete = sorted({code for record in records for code in
        (record.get('factor_envelope') or {}).get('independent_complete_codes', [])})
    parent_complete = len(records) == len(source_plan['batches']) and all(r['status'] == 'COMPLETE' for r in records)
    answer = dict(schema_version=VERSION, plan_hash=source_plan['plan_hash'], job=str(job),
        status='COMPLETE_SOURCE_RECOVERY_LATE' if parent_complete else 'INCOMPLETE_SOURCE_RECOVERY_LATE',
        source_complete=parent_complete and complete == source_plan['requested_codes'],
        requested_codes_count=source_plan['requested_codes_count'], complete_codes_count=len(complete),
        complete_codes=complete, source_references=sorted(references.values(), key=lambda r:r['path']),
        original_complete_reused=sum(r['action']=='REUSE_COMPLETE' for r in source_plan['batches']),
        new_attempts=len(attempted), records=records, new_records=new_records, claimed_at=claimed_at,
        acquisition_actual_cutoff=datetime.now(timezone.utc).isoformat(),
        historical_pit_restored=False, source_capability_issued=False, strategy_executed=False,
        completeness_scope='QUERY_COMPLETENESS_ONLY', adjustment_ready_not_established=True)
    verify_result(answer, source_plan, job, budget)
    return answer


@_metered
def verify_result(result, source_plan, job, budget):
    old = _validate_plan(source_plan, budget, appended=True)
    _verify_refs(result['source_references'], budget)
    need(result['plan_hash'] == source_plan['plan_hash'] and result['job'] == str(Path(job).absolute()), 'RESULT_PARENT_BINDING')
    new_cache = _cache(source_plan['cache_root'], budget)
    records = result['records']
    for name in ('new_attempts','original_complete_reused','requested_codes_count','complete_codes_count'):
        _number(result[name], 'RESULT_' + name, integer=True)
    need(len(records) == len(source_plan['batches']), 'WHOLE_FACTOR_RESULT_REQUIRED')
    complete = set()
    for expected, record in zip(source_plan['batches'], records):
        need(record['request_key'] == expected['request_key'] and record['request_spec'] == expected['request_spec'], 'RESULT_BATCH_SCOPE')
        if expected['action'] == 'REUSE_COMPLETE':
            need(record == expected['original_record'], 'ORIGINAL_RECORD_CHANGED')
            _entry(old, record)
        else:
            root = Path(source_plan['cache_root']) / 'raw' / record['request_key'] / 'raw'
            need(Path(record['capture_attempt_dir']).parent == root and
                 not any(r['path'] == record['capture_attempt_dir'] for r in source_plan['original_cache_inventory']), 'NEW_CAPTURE_ATTEMPT_OWNER')
            _entry(new_cache, record)
            need(_clock(record['first_seen_at']) >= _clock(result['claimed_at']), 'NEW_CAPTURE_CLOCK')
        complete.update((record.get('factor_envelope') or {}).get('independent_complete_codes', []))
    expected_complete = sorted(complete) == source_plan['requested_codes'] and all(r['status']=='COMPLETE' for r in records)
    need(result['source_complete'] is expected_complete and result['complete_codes']==sorted(complete)
         and result['complete_codes_count']==len(complete) and result['requested_codes_count']==len(source_plan['requested_codes']), 'RESULT_COMPLETE_COVERAGE')
    need(result['status'] == ('COMPLETE_SOURCE_RECOVERY_LATE' if expected_complete else 'INCOMPLETE_SOURCE_RECOVERY_LATE')
         and all(result[name] is False for name in ('historical_pit_restored','source_capability_issued','strategy_executed')),
         'RESULT_AUTHORITY_OR_STATUS_CHANGED')
    expected_new = [record for row, record in zip(source_plan['batches'], records) if row['action'] != 'REUSE_COMPLETE']
    need(result['new_records'] == expected_new and result['new_attempts'] == len(expected_new)
         and result['original_complete_reused'] == len(records)-len(expected_new), 'EXACT_NEW_ATTEMPTS_REQUIRED')
    _validate_appends(source_plan, expected_new)
    need(result['completeness_scope'] == 'QUERY_COMPLETENESS_ONLY' and result['adjustment_ready_not_established'] is True,
         'QUERY_COMPLETENESS_NOT_ADJUSTMENT_READINESS')
    need(_clock(result['claimed_at']) <= _clock(result['acquisition_actual_cutoff']) <= datetime.now(timezone.utc)
         and all(_clock(result['claimed_at']) <= _clock(record['first_seen_at']) <= _clock(result['acquisition_actual_cutoff'])
                 for record in expected_new), 'RECOVERY_ACTUAL_CLOCK_ORDER')
    old.terminal()
    new_cache.terminal()
    budget.check('EXPLICIT_FACTOR_RESULT_TERMINAL', force=True)
    return dict(status='VERIFIED_LATE_SOURCE_REFERENCES', source_complete=expected_complete,
                historical_pit_restored=False, source_capability_issued=False)
