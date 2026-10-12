"""Explicit, bounded current-day delivery of one complete saved R2 run.

This stage does not collect data, submit strategy jobs, or certify the upstream
selector. A new process authenticates originals and calls the existing V3 once.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as day_time, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import resource
import signal
import stat
import subprocess
import sys
import time

VERSION = 'SAVED_R2_DAILY_COORDINATION_V1'
FEATURE_VERSION = 'AUTHENTICATED_CURRENT_DAY_COMPLETE_SAVED_R2_DELIVERY_V1'
STRATEGY = 'SHORT_HORIZON_STRENGTH_R2'
SMALL = 8 * 1024**2
LARGE = 256 * 1024**2
TOTAL_SOURCE = 2 * 1024**3
SEAL_BYTES = 16 * 1024
FP_KEYS = ('st_dev', 'st_ino', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_flags')
HOOKS = {'delivery': ('saved_r2_stream_delivery_v3', 'deliver_saved_r2_stream'),
         'issuer': ('saved_r2_sources_v2', 'issue_saved_r2_source_reader'),
         'stream': ('theme_market_stream', 'SourceMarketStream.from_reader'),
         'theme': ('theme_evolution_streaming', 'run_theme_evolution_stream'),
         'economic': ('economic_direction_explanation_v1', 'build_economic_direction_explanation'),
         'display': ('economic_direction_display', 'save_archived_economic_direction_display')}
_DEADLINE = None
_RSS_LIMIT = None


def _guard():
    if _DEADLINE is not None and time.monotonic() >= _DEADLINE:
        raise TimeoutError('REGISTERED_CORE_WALL_BUDGET_EXCEEDED')
    if _RSS_LIMIT is not None and _rss() > _RSS_LIMIT:
        raise MemoryError('REGISTERED_PROCESS_RSS_BUDGET_EXCEEDED')


@dataclass(frozen=True)
class Limits:
    wall_seconds: float = 1800
    output_bytes: int = 512 * 1024**2
    per_output_bytes: int = LARGE
    process_rss_bytes: int = 5 * 1024**3 // 2
    tree_rss_bytes: int = 3 * 1024**3
    reserve_bytes: int = 2 * 1024**2

    def validate(self):
        ceiling = type(self)()
        for name in ('wall_seconds', 'output_bytes', 'per_output_bytes',
                     'process_rss_bytes', 'tree_rss_bytes', 'reserve_bytes'):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= getattr(ceiling, name):
                raise ValueError('REGISTERED_BUDGET_CANNOT_BE_EXPANDED')
            if name != 'wall_seconds' and type(value) is not int:
                raise ValueError('REGISTERED_BYTE_BUDGET_REQUIRES_INTEGER')
        if self.reserve_bytes >= self.output_bytes or self.per_output_bytes > self.output_bytes:
            raise ValueError('REGISTERED_BUDGET_INVALID')
        if self.reserve_bytes < SEAL_BYTES * 2 or self.per_output_bytes < SEAL_BYTES:
            raise ValueError('REGISTERED_FAILURE_SEAL_RESERVE_REQUIRED')
        return self


def _now():
    return datetime.now(timezone.utc)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _fp(path):
    s = Path(path).lstat()
    return {key: getattr(s, key, 0) for key in FP_KEYS}


def _path(value, base=None):
    p = Path(value)
    if not p.is_absolute():
        if base is None:
            raise ValueError('EXPLICIT_ABSOLUTE_PATH_REQUIRED')
        p = Path(base) / p
    if p.is_symlink():
        raise ValueError('SOURCE_SYMLINK_FORBIDDEN')
    return p.resolve(strict=True)


def _pin(path, expected=None, *, limit=SMALL):
    _guard()
    path = _path(path)
    before = _fp(path)
    if not stat.S_ISREG(before['st_mode']) or not 0 < before['st_size'] <= limit:
        raise ValueError('SOURCE_NONREGULAR_EMPTY_OR_OVERSIZE')
    h, count = hashlib.sha256(), 0
    with path.open('rb') as handle:
        while True:
            _guard()
            raw = handle.read(64 * 1024)
            if not raw:
                break
            count += len(raw)
            if count > limit:
                raise ValueError('SOURCE_GREW_PAST_BYTE_LIMIT')
            h.update(raw)
    if before != _fp(path) or count != before['st_size']:
        raise ValueError('SOURCE_CHANGED_DURING_WHOLE_READ')
    if expected is not None and h.hexdigest() != expected:
        raise ValueError('SOURCE_WHOLE_SHA_MISMATCH')
    return {'path': str(path), 'sha256': h.hexdigest(), 'limit': limit, 'fingerprint': before}


def _read(record):
    _guard()
    path = Path(record['path'])
    if _fp(path) != record['fingerprint']:
        raise ValueError('SOURCE_PRE_READ_FINGERPRINT_CHANGED')
    with path.open('rb') as handle:
        raw = handle.read(record['limit'] + 1)
    if len(raw) != record['fingerprint']['st_size'] or len(raw) > record['limit']:
        raise ValueError('SOURCE_BOUNDED_READ_SIZE_CHANGED')
    if _sha(raw) != record['sha256'] or _fp(path) != record['fingerprint']:
        raise ValueError('SOURCE_SHA_OR_FINGERPRINT_CHANGED')
    from stockfish_core.yaogu.saved_r2_delivery import _json
    return _json(raw)


def _imports():
    # The established import order avoids the old theme/strategy circular import.
    importlib.import_module('stockfish_core.yaogu.strategy_experiments')
    return {name: importlib.import_module('stockfish_core.yaogu.' + name)
            for name in ('saved_r2_delivery', 'saved_r2_stream_delivery_v3',
                         'saved_r2_sources_v2', 'research_context_artifacts')}


def source_identity():
    modules = _imports()
    identity = modules['saved_r2_stream_delivery_v3'].source_identity()
    root = Path(__file__).resolve().parents[2]
    hashes = dict(identity['source_file_hashes'])
    for p in (Path(__file__), root / 'docs/SAVED_R2_DAILY_COORDINATION_V1.md'):
        hashes[str(p.relative_to(root))] = _pin(p)['sha256']
    return {'version': VERSION, 'feature_version': FEATURE_VERSION,
            'native_delivery_identity': identity, 'source_file_hashes': hashes,
            'source_hash': _sha(_canonical(hashes)), 'parameter_status': 'UNVALIDATED',
            'market_edge': 'NOT_ESTABLISHED', 'default_service_changed': False}


def _runtime():
    # Keep the venv executable spelling for launch; resolve only its byte identity.
    executable = Path(sys.executable).absolute()
    files = [_pin(executable.resolve(strict=True), limit=64 * 1024**2)]
    cfg = Path(sys.prefix) / 'pyvenv.cfg'
    if cfg.is_file():
        files.append(_pin(cfg))
    import pydantic_core
    core = Path(pydantic_core._pydantic_core.__file__)
    files.append(_pin(core, limit=64 * 1024**2))
    return {'executable': str(executable), 'executable_lstat': _fp(executable),
            'prefix': sys.prefix, 'base_prefix': sys.base_prefix,
            'version': sys.version, 'files': files}


def _source_refs(value):
    if isinstance(value, dict):
        if set(value) == {'path', 'sha256'}:
            yield value
        else:
            for item in value.values():
                yield from _source_refs(item)
    elif isinstance(value, list):
        for item in value:
            yield from _source_refs(item)


def _scan_output(root, limits, *, reserve=True):
    _guard()
    total, largest, files = 0, 0, 0
    pending = [(Path(root), 0)]
    while pending:
        _guard()
        directory, depth = pending.pop()
        if depth > 32:
            raise ValueError('OUTPUT_DEPTH_EXCEEDED')
        for path in directory.iterdir():
            s = path.lstat()
            if stat.S_ISDIR(s.st_mode):
                pending.append((path, depth + 1))
            elif stat.S_ISREG(s.st_mode):
                files += 1
                total += s.st_size
                largest = max(largest, s.st_size)
                if files > 8192 or s.st_size > limits.per_output_bytes:
                    raise ValueError('OUTPUT_FILE_OR_COUNT_LIMIT_EXCEEDED')
            else:
                raise ValueError('OUTPUT_SYMLINK_OR_NONREGULAR_FORBIDDEN')
    ceiling = limits.output_bytes - (limits.reserve_bytes if reserve else 0)
    if total > ceiling:
        raise ValueError('OUTPUT_TOTAL_LIMIT_EXCEEDED')
    return {'bytes': total, 'largest_bytes': largest, 'file_count': files}


def _save(path, value, limits, *, diagnostic=False):
    raw = _canonical(value)
    if len(raw) > SMALL:
        raise ValueError('CONTROL_BYTE_LIMIT_EXCEEDED')
    root = Path(path).parent
    current = _scan_output(root, limits, reserve=not diagnostic)['bytes']
    ceiling = limits.output_bytes if diagnostic else limits.output_bytes - limits.reserve_bytes
    if current + len(raw) > ceiling:
        raise ValueError('CONTROL_RESERVE_EXHAUSTED')
    with Path(path).open('xb') as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    return _pin(path)


def _seal(path, value, record=None):
    """A preallocated final authority survives an output overshoot without growth."""
    raw = _canonical(value)
    if len(raw) > SEAL_BYTES:
        raise ValueError('FINAL_SEAL_CONTROL_LIMIT_EXCEEDED')
    if record is not None:
        current = _fp(path)
        if any(current[k] != record['fingerprint'][k] for k in ('st_dev', 'st_ino', 'st_mode', 'st_size')):
            raise ValueError('FINAL_SEAL_FILE_IDENTITY_CHANGED')
    with Path(path).open('xb' if record is None else 'r+b') as handle:
        handle.write(raw + b' ' * (SEAL_BYTES - len(raw)))
        handle.flush()
        os.fsync(handle.fileno())
    return _pin(path)


def prepare_daily_saved_r2(*, run_path, run_sha256, input_path, input_sha256,
                          sources_manifest_path, sources_manifest_sha256,
                          output_dir, membership_mode='DATED_ONLY', test_fixture=False,
                          limits=Limits()):
    """Pin a new plan. No reader capability, selector, or large JSON parse occurs."""
    global _DEADLINE, _RSS_LIMIT
    limits.validate()
    if membership_mode not in ('DATED_ONLY', 'CURRENT_COHORT_BACKVIEW'):
        raise ValueError('MEMBERSHIP_MODE_INVALID')
    job = Path(output_dir).absolute()
    if job.is_symlink() or job.exists():
        raise ValueError('OUTPUT_MUST_BE_NEW')
    job.parent.resolve(strict=True)
    job.mkdir(mode=0o700)
    job = job.resolve(strict=True)
    started = time.monotonic()
    previous_guard = (_DEADLINE, _RSS_LIMIT)
    _DEADLINE, _RSS_LIMIT = started + 118, 256 * 1024**2
    try:
        identity, runtime = source_identity(), _runtime()
        records = {'run': _pin(run_path, run_sha256, limit=LARGE),
                   'input': _pin(input_path, input_sha256, limit=LARGE),
                   'sources': _pin(sources_manifest_path, sources_manifest_sha256)}
        sources = _read(records['sources'])
        registry = {v['path']: v for v in records.values()}
        for ref in _source_refs(sources):
            path = _path(ref['path'], Path(records['sources']['path']).parent)
            record = _pin(path, ref['sha256'])
            prior = registry.get(record['path'])
            if prior is not None and prior != record:
                raise ValueError('SOURCE_REGISTRY_CONFLICT')
            registry[record['path']] = record
        if sum(v['fingerprint']['st_size'] for v in registry.values()) > TOTAL_SOURCE:
            raise ValueError('UNIQUE_SOURCE_LIMIT_EXCEEDED')
        if len(registry) > 2048:
            raise ValueError('SOURCE_FILE_COUNT_LIMIT_EXCEEDED')
        if any(Path(p).is_relative_to(job) for p in registry):
            raise ValueError('SOURCE_AND_OUTPUT_SCOPE_OVERLAP')
        root = Path(__file__).resolve().parents[2]
        implementation = [_pin(root / name, sha) for name, sha in identity['source_file_hashes'].items()]
        if time.monotonic() - started > 120 or _rss() > 256 * 1024**2:
            raise ValueError('PREPARATION_BUDGET_EXCEEDED')
        plan = {'schema_version': VERSION, 'status': 'REGISTERED_NOT_EXECUTED',
                'registered_at': _now().isoformat(), 'records': records,
                'source_registry': list(registry.values()), 'implementation_registry': implementation,
                'source_identity': identity, 'runtime': runtime, 'output_dir': str(job),
                'membership_mode': membership_mode, 'test_fixture': bool(test_fixture),
                'limits': asdict(limits), 'source_capability_issued': False,
                'upstream_capture_and_selector_executed': False,
                'freshness_and_complete_pool_not_yet_verified': True,
                'large_run_input_parsed_by_preparation': False}
        for record in [*registry.values(), *implementation]:
            if _pin(record['path'], record['sha256'], limit=record['limit']) != record:
                raise ValueError('PREPARATION_TERMINAL_SOURCE_CHANGED')
        if source_identity() != identity or _runtime() != runtime:
            raise ValueError('PREPARATION_IMPLEMENTATION_OR_RUNTIME_CHANGED')
        record = _save(job / 'registered-plan.json', plan, limits)
        if time.monotonic() - started > 120 or _rss() > 256 * 1024**2:
            raise ValueError('PREPARATION_FINAL_WRITE_BUDGET_EXCEEDED')
        return record
    except BaseException as error:
        _DEADLINE, _RSS_LIMIT = None, None
        _save(job / 'prepare-failure.json', {'status': 'FAILED', 'at': _now().isoformat(),
            'error_type': type(error).__name__, 'reason': _reason(error),
            'native_completion': 'UNKNOWN', 'upstream_capture_and_selector_executed': False},
            limits, diagnostic=True)
        raise
    finally:
        _DEADLINE, _RSS_LIMIT = previous_guard


def _binding_from_run(run, base):
    from stockfish_core.yaogu.history_artifacts import digest_json_stream
    if digest_json_stream({k: v for k, v in run.items() if k != 'content_hash'}) != run.get('content_hash'):
        raise ValueError('RUN_CONTENT_HASH_MISMATCH')
    if set(run.get('results', {})) != {STRATEGY}:
        raise ValueError('SINGLE_COMPLETE_R2_REQUIRED')
    result = run['results'][STRATEGY]
    rows = result.get('items')
    if not isinstance(rows, list) or result.get('full_eligible_pool_retained') is not True:
        raise ValueError('COMPLETE_POOL_ROWS_REQUIRED')
    count = Counter(row.get('decision_status') for row in rows)
    if set(count) - {'FOCUS', 'WATCH', 'REJECTED', 'UNKNOWN'}:
        raise ValueError('POOL_DECISION_STATE_INVALID')
    target = run.get('target_session')
    if not isinstance(target, str) or str(date.fromisoformat(target)) != target:
        raise ValueError('SAVED_RUN_TARGET_REQUIRED_NO_POSTHOC_TARGET_INSERTION')
    if 'target_session' in result and result['target_session'] != target:
        raise ValueError('SAVED_INNER_TARGET_CONFLICT')
    prospective = run.get('prospective_validation')
    if isinstance(prospective, dict) and 'target_session' in prospective and prospective['target_session'] != target:
        raise ValueError('SAVED_PROSPECTIVE_TARGET_CONFLICT')
    selected = [row for row in rows if row.get('research_selected') is True]
    counts = {'eligible': len(rows), **{k: count[k.upper()] for k in ('focus', 'watch', 'rejected', 'unknown')}}
    return {'strategy_id': STRATEGY, 'run_id': run['run_id'], 'input_id': run['input_id'],
            'input_hash': run['input_hash'], 'observation_date': run['observation_date'],
            'target_session': target, 'original_decision_cutoff': run['decision_cutoff'],
            'source_hash': run['source_hash'], 'counts': counts,
            'native_counts': result['counts'], 'selected_ids': [row['instrument_id'] for row in selected],
            'selection_policy_id': result['selection_policy_id'],
            'original_strategy_identity': {k: run[k] for k in base.IDENTITY_FIELDS},
            'selected_original_items_hash': digest_json_stream(selected)}


def _calendar_guard(bundle, binding, document, source_base, registry, cutoff):
    """Authenticate real calendar trios; its future coverage end is metadata."""
    from stockfish_core.yaogu import saved_r2_delivery as base
    from stockfish_core.yaogu.research_context_artifacts import SourceArtifactReader
    from stockfish_core.yaogu.visibility import SH, stamp
    receipts, paths, audits = [], {}, []
    for registration in document['captures']:
        if registration.get('typed_receipt', {}).get('dataset') != 'calendar':
            continue
        receipt, path, audit = base._authenticate_capture(registry, registration, source_base)
        receipts.append(receipt)
        paths[receipt.receipt_id] = path
        audits.append(audit)
    instant = stamp(cutoff)
    current = instant.astimezone(SH)
    candidates = [r for r in receipts if r.provider.lower() == 'baostock' and r.status == 'COMPLETE'
        and r.pagination_complete and max(r.source_published_at, r.first_seen_at, r.ingested_at) <= instant
        and str(r.parameters.get('start_date', '')) <= str(bundle.observation_date)
        and str(r.parameters.get('end_date', '')) > str(bundle.observation_date)
        and all(a['completed_at'] is None or stamp(a['completed_at']) <= instant
                for a in audits if a['receipt_id'] == r.receipt_id)]
    if not candidates:
        raise ValueError('CURRENT_COMPLETE_ORIGINAL_CALENDAR_UNAVAILABLE')
    revision = lambda r: (r.source_published_at, r.ingested_at, r.first_seen_at)
    latest = max(map(revision, candidates))
    tied = [r for r in candidates if revision(r) == latest]
    identities = {(r.content_hash, r.field_version, _canonical(r.parameters)) for r in tied}
    if len(identities) != 1:
        raise ValueError('CURRENT_CALENDAR_SAME_CLOCK_CONFLICT')
    parent = min(tied, key=lambda r: r.receipt_id)
    if not str(parent.parameters['start_date']) <= str(current.date()) <= str(parent.parameters['end_date']):
        raise ValueError('LATEST_CALENDAR_DOES_NOT_COVER_CURRENT_NATURAL_DATE')
    reader = SourceArtifactReader([parent], {parent.receipt_id: paths[parent.receipt_id]})
    evidence = reader.target_calendar_evidence(observation_date=bundle.observation_date,
        target_session=binding['target_session'], cutoff=instant, coverage_type=bundle.coverage_type)
    if evidence['status'] != 'AVAILABLE' or evidence['verified_next_session'] != binding['target_session']:
        raise ValueError('CURRENT_NEXT_SESSION_NOT_VERIFIED')
    payload = base._json(reader(parent.receipt_id))
    completed = [date.fromisoformat(row['calendar_date']) for row in payload['rows']
        if row['is_trading_day'] == '1' and
        datetime.combine(date.fromisoformat(row['calendar_date']), day_time(15), SH) <= instant]
    if not completed or max(completed) != bundle.observation_date:
        raise ValueError('SAVED_OBSERVATION_IS_NOT_CURRENT_LAST_COMPLETED_SESSION')
    return {'schema_version': FEATURE_VERSION, 'verified_at': instant.isoformat(),
            'coverage_type': bundle.coverage_type,
            'last_completed_session': str(max(completed)), 'next_session': binding['target_session'],
            'calendar_evidence': evidence, 'calendar_authentication_audits': audits,
            'original_run_cutoff_unchanged': binding['original_decision_cutoff'],
            'new_context_only_not_original_run_PIT': True,
            'ordinary_JSON_issued_calendar_capability': False}


def _reason(error):
    text = str(error).split(':')[0]
    return text if text and all(c.isupper() or c.isdigit() or c == '_' for c in text) else 'COORDINATION_ERROR'


def _rss(who=resource.RUSAGE_SELF):
    value = resource.getrusage(who).ru_maxrss
    return int(value if sys.platform == 'darwin' else value * 1024)


def _terminal(plan, plan_record, claim_record=None, additional=()):
    records = [plan_record, *plan['source_registry'], *plan['implementation_registry'],
               *plan['runtime']['files']]
    if claim_record:
        records.append(claim_record)
    records.extend(additional)
    checks = []
    for record in records:
        try:
            current = _pin(record['path'], record['sha256'], limit=record['limit'])
        except BaseException as error:
            terminal = _fp(record['path']) if Path(record['path']).exists() else None
            checks.append({'path': record['path'], 'initial': record['fingerprint'], 'terminal': terminal,
                'changed_fields': [k for k in FP_KEYS if terminal is None or terminal[k] != record['fingerprint'][k]],
                'sha256': None, 'whole_SHA_verification': 'FAILED', 'reason': _reason(error)})
            error.identity_checks = checks
            raise
        changed = [k for k in FP_KEYS if current['fingerprint'][k] != record['fingerprint'][k]]
        checks.append({'path': record['path'], 'initial': record['fingerprint'],
                       'terminal': current['fingerprint'], 'changed_fields': changed,
                       'sha256': current['sha256']})
        if changed:
            error = ValueError('REGISTERED_SOURCE_OR_CONTROL_FINGERPRINT_CHANGED')
            error.identity_checks = checks
            raise error
    if source_identity() != plan['source_identity'] or _runtime() != plan['runtime']:
        raise ValueError('REGISTERED_IMPLEMENTATION_OR_RUNTIME_CHANGED')
    return checks


def _fingerprints_only(plan, plan_record, claim_record, additional=()):
    """Close the last seal write window; whole SHA already closed just before it."""
    records = [plan_record, claim_record, *plan['source_registry'], *plan['implementation_registry'],
               *plan['runtime']['files'], *additional]
    for record in records:
        current = _snapshot(record)
        if current['changed_fields']:
            error = ValueError('FINAL_SEAL_SOURCE_OR_CONTROL_FINGERPRINT_CHANGED')
            error.identity_checks = [current]
            raise error


def _snapshot(record):
    try:
        terminal = _fp(record['path'])
    except OSError:
        terminal = None
    return {'path': record['path'], 'initial': record['fingerprint'], 'terminal': terminal,
        'changed_fields': [k for k in FP_KEYS if terminal is None or terminal[k] != record['fingerprint'][k]]}


@dataclass(frozen=True)
class _CalendarProjection:
    observation_date: date
    coverage_type: str


def _outer_calendar(plan, worker):
    """Reauthenticate only calendar originals, not saved JSON capabilities."""
    coverage = worker['calendar']['coverage_type']
    if (coverage == 'TEST_FIXTURE') != plan['test_fixture']:
        raise ValueError('OUTER_CALENDAR_COVERAGE_CONFLICT')
    projection = _CalendarProjection(date.fromisoformat(worker['binding']['observation_date']), coverage)
    return _calendar_guard(projection, worker['binding'], _read(plan['records']['sources']),
        Path(plan['records']['sources']['path']).parent, _imports()['saved_r2_delivery']._Registry(), _now())


def _worker(plan, plan_record, claim_record):
    global _DEADLINE, _RSS_LIMIT
    from stockfish_core.yaogu import saved_r2_delivery as base
    from stockfish_core.yaogu import saved_r2_stream_delivery_v3 as native
    limits, job = Limits(**plan['limits']).validate(), Path(plan['output_dir'])
    started = time.monotonic()
    _DEADLINE = started + max(0.01, limits.wall_seconds - min(2, limits.wall_seconds / 2))
    _RSS_LIMIT = limits.process_rss_bytes
    attempts, returns, unwinds, codes = ({k: 0 for k in HOOKS}, {k: 0 for k in HOOKS},
                                      {k: 0 for k in HOOKS}, {})
    denied_attempts = 0
    monitor = sys.monitoring
    tool = 3
    error, result, binding, calendar, checks = None, None, None, None, []
    ledger = (job / 'native-events.jsonl').open('xb')

    def event(value):
        raw = _canonical({'at': _now().isoformat(), **value}) + b'\n'
        if len(raw) > 64 * 1024 or _scan_output(job, limits)['bytes'] + len(raw) > limits.output_bytes - limits.reserve_bytes:
            raise ValueError('NATIVE_EVENT_OR_OUTPUT_LIMIT_EXCEEDED')
        ledger.write(raw); ledger.flush(); os.fsync(ledger.fileno())

    def on_start(code, offset):
        nonlocal denied_attempts
        key = codes.get(code)
        if key is None:
            raise ValueError('UNREGISTERED_NATIVE_MONITOR_EVENT')
        if key == 'FORBIDDEN_SELECTOR':
            denied_attempts += 1
            event({'event': 'FORBIDDEN_SELECTOR_ENTERED', 'attempt': denied_attempts, 'completion': 'UNKNOWN'})
            raise ValueError('DELIVERY_STAGE_SELECTOR_EXECUTION_FORBIDDEN')
        attempts[key] += 1
        event({'event': 'NATIVE_ENTERED', 'key': key, 'attempt': attempts[key], 'completion': 'UNKNOWN'})
        if attempts[key] > 1:
            raise ValueError('NATIVE_ENTRY_MORE_THAN_ONCE')

    def on_return(code, offset, value):
        key = codes[code]
        returns[key] += 1
        event({'event': 'NATIVE_RETURNED', 'key': key, 'return': returns[key]})

    def on_unwind(code, offset, exception):
        key = codes.get(code)
        if key is None or key == 'FORBIDDEN_SELECTOR':
            return
        unwinds[key] += 1
        event({'event': 'NATIVE_UNWOUND', 'key': key, 'reason': _reason(exception)})
        if key != 'display' or str(exception) != 'ECONOMIC_DISPLAY_ARCHIVE_CHANGED':
            raise ValueError('UNEXPECTED_NATIVE_UNWIND')

    registered = False
    try:
        checks = _terminal(plan, plan_record, claim_record)
        run = _read(plan['records']['run'])
        binding = _binding_from_run(run, base)
        run_coverage = run.get('coverage_type')
        fixture = run.get('coverage_type') == 'TEST_FIXTURE'
        if fixture != plan['test_fixture'] or (not fixture and run.get('coverage_type') not in ('PROSPECTIVE_CAPTURED', 'RECONSTRUCTED_HISTORICAL')):
            raise ValueError('EXPLICIT_TEST_FIXTURE_OR_REAL_COVERAGE_REQUIRED')
        manifest = {'schema_version': base.RUN_SCHEMA,
                    'run_sha256': plan['records']['run']['sha256'],
                    'input_sha256': plan['records']['input']['sha256'],
                    'sources_manifest_sha256': plan['records']['sources']['sha256'], 'binding': binding}
        del run
        manifest_record = _save(job / 'prepared-run-manifest.json', manifest, limits)
        cutoff = _now()
        registry = base._Registry()
        bundle, frozen, selected, exports, visible, original_calendar = base._freeze_run(registry,
            plan['records']['run']['path'], manifest_record['path'], plan['records']['input']['path'],
            plan['records']['sources']['path'], cutoff)
        if (bundle.coverage_type == 'TEST_FIXTURE') != plan['test_fixture'] or bundle.coverage_type != run_coverage:
            raise ValueError('INPUT_COVERAGE_MODE_CONFLICT')
        from stockfish_core.yaogu.visibility import SH, stamp
        if visible > cutoff:
            raise ValueError('ORIGINAL_RUN_GENERATED_NOT_YET_VISIBLE')
        if stamp(frozen['original_decision_cutoff']) < datetime.combine(bundle.observation_date, day_time(15), SH):
            raise ValueError('ORIGINAL_DAILY_RUN_CUTOFF_PRECEDES_OBSERVATION_CLOSE')
        document = _read(plan['records']['sources'])
        calendar = _calendar_guard(bundle, frozen, document, Path(plan['records']['sources']['path']).parent,
                                   registry, cutoff)
        _save(job / 'current-day-preflight.json', {'binding': frozen, 'calendar': calendar,
            'original_run_generated_visible_at': visible.isoformat(), 'original_calendar_evidence': original_calendar,
            'all_pool_and_selected_rows_verified': True, 'selector_reauthenticated': False}, limits)
        del selected, exports, document
        for key, (module_name, dotted) in HOOKS.items():
            obj = importlib.import_module('stockfish_core.yaogu.' + module_name)
            for name in dotted.split('.'):
                obj = getattr(obj, name)
            codes[getattr(obj, '__func__', obj).__code__] = key
        for module_name, function in (('strategy_experiments', 'run_short_horizon_strategy'),
                ('short_horizon_strength', 'run_short_horizon_strength'),
                ('short_horizon_participation', 'run_short_horizon_strength')):
            obj = getattr(importlib.import_module('stockfish_core.yaogu.' + module_name), function)
            codes[obj.__code__] = 'FORBIDDEN_SELECTOR'
        monitor.use_tool_id(tool, VERSION)
        registered = True
        monitor.register_callback(tool, monitor.events.PY_START, on_start)
        monitor.register_callback(tool, monitor.events.PY_RETURN, on_return)
        monitor.register_callback(tool, monitor.events.PY_UNWIND, on_unwind)
        # CPython 3.12 permits PY_UNWIND only as a global event; callback filters
        # it to the registered native code objects, with no trace of other code.
        monitor.set_events(tool, monitor.events.PY_UNWIND)
        for code in codes:
            monitor.set_local_events(tool, code, monitor.events.PY_START | monitor.events.PY_RETURN)
        result = native.deliver_saved_r2_stream(run_path=plan['records']['run']['path'],
            run_manifest_path=manifest_record['path'], input_path=plan['records']['input']['path'],
            sources_manifest_path=plan['records']['sources']['path'], output=job / 'delivery',
            context_cutoff=cutoff, membership_mode=plan['membership_mode'])
        actual = _read(_pin(job / 'delivery/delivery-manifest.json'))
        if (actual['run_id'] != frozen['run_id'] or actual['input_hash'] != frozen['input_hash']
            or actual['target_session'] != frozen['target_session'] or actual['observation_date'] != frozen['observation_date']
            or actual['counts'] != frozen['native_counts'] or actual['count_summary'] != frozen['counts']
            or actual['frozen_selected_ids'] != frozen['selected_ids']
            or actual['selected_original_items_hash'] != frozen['selected_original_items_hash']
            or actual['original_decision_cutoff'] != frozen['original_decision_cutoff']
            or actual['stock_selection_changed'] is not False or actual['score_or_FOCUS_changed'] is not False
            or actual['source_capability_restored_from_JSON'] is not False
            or actual['source_identity'] != plan['source_identity']['native_delivery_identity']):
            raise ValueError('ACTUAL_COMPLETE_DELIVERY_BINDING_MISMATCH')
        expected = {'delivery': 1, 'issuer': 1, 'stream': actual['native_market_stream_calls'],
            'theme': actual['native_theme_producer_calls'], 'economic': actual['economic_explanation_producer_calls'],
            'display': 1 if frozen['selected_ids'] else 0}
        expected_returns = {**expected, 'display': expected['display'] - unwinds['display']}
        if (attempts != expected or returns != expected_returns or unwinds['display'] > 1
            or any(v for key, v in unwinds.items() if key != 'display')):
            raise ValueError('ACTUAL_NATIVE_ENTRY_OR_RETURN_COUNT_MISMATCH')
        if monitor.get_tool(tool) != VERSION or monitor.get_events(tool) != monitor.events.PY_UNWIND:
            raise ValueError('NATIVE_MONITOR_IDENTITY_CHANGED')
        for kind, callback in ((monitor.events.PY_START, on_start), (monitor.events.PY_RETURN, on_return),
                               (monitor.events.PY_UNWIND, on_unwind)):
            if monitor.register_callback(tool, kind, callback) is not callback:
                raise ValueError('NATIVE_MONITOR_CALLBACK_CHANGED')
        for code in codes:
            if monitor.get_local_events(tool, code) != (monitor.events.PY_START | monitor.events.PY_RETURN):
                raise ValueError('NATIVE_MONITOR_EVENT_MASK_CHANGED')
        # A new completed session during execution makes this an old-day result.
        _calendar_guard(bundle, frozen, _read(plan['records']['sources']),
            Path(plan['records']['sources']['path']).parent, base._Registry(), _now())
        checks = _terminal(plan, plan_record, claim_record)
        _scan_output(job, limits)
        if _rss() > limits.process_rss_bytes or time.monotonic() - started > limits.wall_seconds:
            raise ValueError('WORKER_TERMINAL_RESOURCE_LIMIT_EXCEEDED')
        return 0
    except BaseException as failure:
        error = {'type': type(failure).__name__, 'reason': _reason(failure)}
        checks = getattr(failure, 'identity_checks', checks)
        return 1
    finally:
        _DEADLINE, _RSS_LIMIT = None, None
        if registered:
            monitor.set_events(tool, 0)
            for code in codes:
                monitor.set_local_events(tool, code, 0)
            monitor.register_callback(tool, monitor.events.PY_START, None)
            monitor.register_callback(tool, monitor.events.PY_RETURN, None)
            monitor.register_callback(tool, monitor.events.PY_UNWIND, None)
            monitor.free_tool_id(tool)
        ledger.close()
        _save(job / 'worker-result.json', {'status': 'PASS' if error is None and result is not None else 'FAILED',
            'at': _now().isoformat(), 'pid': os.getpid(), 'parent_pid': os.getppid(),
            'error': error, 'result': result, 'calendar': calendar,
            'binding': binding, 'initial_terminal_checks': checks,
            'native_attempts_lower_bound': attempts, 'native_returns_lower_bound': returns,
            'native_unwinds_lower_bound': unwinds,
            'denied_selector_attempts_lower_bound': denied_attempts,
            'native_completion': returns if error is None and result is not None else 'UNKNOWN',
            'OS_peak_RSS_bytes': _rss(), 'elapsed_seconds': time.monotonic() - started,
            'upstream_capture_and_selector_executed': False, 'historical_PIT_revalidated': False,
            'continuous_unattended_delivery_validated': False}, limits, diagnostic=True)


def _tree_rss(pid):
    process = subprocess.run(['/bin/ps', '-A', '-o', 'pid=,ppid=,pgid=,rss='],
        capture_output=True, text=True, timeout=2, check=True)
    rows = []
    for line in process.stdout.splitlines():
        parts = line.split()
        if len(parts) != 4:
            raise ValueError('PS_PROCESS_TABLE_FORMAT_UNKNOWN')
        try:
            row = tuple(map(int, parts))
        except ValueError as failure:
            raise ValueError('PS_PROCESS_TABLE_FORMAT_UNKNOWN') from failure
        if row[0] <= 0 or any(v < 0 for v in row[1:]):
            raise ValueError('PS_PROCESS_TABLE_FORMAT_UNKNOWN')
        rows.append(row)
    if not rows or len({p for p, _, _, _ in rows}) != len(rows):
        raise ValueError('PS_PROCESS_TABLE_EMPTY_OR_DUPLICATE_UNKNOWN')
    if pid not in {p for p, _, _, _ in rows}:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            pass  # A reaped leader may be absent; its process group still matters.
        else:
            raise ValueError('PS_LIVE_WORKER_RSS_UNAVAILABLE_UNKNOWN')
    selected = {pid}
    while True:
        extended = selected | {p for p, parent, group, _ in rows if parent in selected or group == pid}
        if extended == selected:
            break
        selected = extended
    sizes = [rss * 1024 for p, _, _, rss in rows if p in selected]
    return sum(sizes), max(sizes, default=0), selected


def _kill_reap(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    return process.wait(timeout=5)


def execute_daily_saved_r2(*, plan_path, plan_sha256):
    """One exclusive claim and one supervised V3 process; never retries."""
    global _DEADLINE, _RSS_LIMIT
    plan_record = _pin(plan_path, plan_sha256)
    plan = _read(plan_record)
    if plan.get('schema_version') != VERSION or plan.get('status') != 'REGISTERED_NOT_EXECUTED':
        raise ValueError('REGISTERED_DAILY_COORDINATION_PLAN_REQUIRED')
    limits = Limits(**plan['limits']).validate()
    job = Path(plan['output_dir']).resolve(strict=True)
    if Path(plan_record['path']).parent != job:
        raise ValueError('PLAN_AND_OUTPUT_NAMESPACE_CONFLICT')
    if (job / 'prepare-failure.json').exists():
        raise ValueError('FAILED_PREPARATION_NOT_EXECUTABLE')
    claim = _save(job / 'execution-claim.json', {'at': _now().isoformat(), 'pid': os.getpid(),
        'plan_sha256': plan_record['sha256'], 'maximum_execute_calls': 1}, limits)
    seal_path = job / 'execution-seal.json'
    seal_record = _seal(seal_path, {'status': 'CLAIMED_COMPLETION_UNKNOWN',
        'plan_sha256': plan_record['sha256'], 'pid': os.getpid(), 'native_completion': 'UNKNOWN'})
    started = time.monotonic()
    previous_guard = (_DEADLINE, _RSS_LIMIT)
    _DEADLINE = started + max(0.01, limits.wall_seconds - min(2, limits.wall_seconds / 2))
    _RSS_LIMIT = limits.process_rss_bytes
    process, worker, worker_record, checks, reason, child_exit = None, None, None, [], None, None
    child_peak_before = _rss(resource.RUSAGE_CHILDREN)
    max_tree = max_process = 0
    try:
        _terminal(plan, plan_record, claim)
        argv = [plan['runtime']['executable'], '-I', '-B', str(Path(__file__).resolve()),
                '--mode', 'worker', '--plan', str(plan_record['path']), '--plan-sha256', plan_record['sha256']]
        with (job / 'worker-stdout.txt').open('xb') as stdout, (job / 'worker-stderr.txt').open('xb') as stderr:
            process = subprocess.Popen(argv, stdout=stdout, stderr=stderr, start_new_session=True)
            while process.poll() is None:
                if time.monotonic() - started > limits.wall_seconds:
                    raise TimeoutError('SUPERVISOR_WALL_BUDGET_EXCEEDED')
                tree, largest, _ = _tree_rss(process.pid)
                max_tree, max_process = max(max_tree, tree), max(max_process, largest)
                if tree > limits.tree_rss_bytes or largest > limits.process_rss_bytes:
                    raise MemoryError('SUPERVISOR_PROCESS_TREE_BUDGET_EXCEEDED')
                _scan_output(job, limits)
                time.sleep(0.1)
            child_exit = process.wait(timeout=5)
        # Terminal size checks also catch a last write followed by exit(0).
        _scan_output(job, limits)
        _, _, surviving = _tree_rss(process.pid)
        if surviving != {process.pid}:
            raise ValueError('WORKER_DESCENDANT_REMAINS_AFTER_EXIT')
        worker_record = _pin(job / 'worker-result.json')
        worker = _read(worker_record)
        if (child_exit != 0 or worker['status'] != 'PASS' or worker['pid'] != process.pid
            or worker['parent_pid'] != os.getpid() or worker['OS_peak_RSS_bytes'] > limits.process_rss_bytes
            or time.monotonic() - started > limits.wall_seconds
            or _rss(resource.RUSAGE_CHILDREN) > limits.process_rss_bytes):
            raise ValueError('REAL_WORKER_SUCCESS_AND_RESOURCES_REQUIRED')
        checks = _terminal(plan, plan_record, claim, additional=[worker_record])
        _outer_calendar(plan, worker)
        _guard()
        _scan_output(job, limits)
    except BaseException as failure:
        _DEADLINE, _RSS_LIMIT = None, None
        reason = {'type': type(failure).__name__, 'reason': _reason(failure)}
        checks = getattr(failure, 'identity_checks', checks)
        if process is not None:
            try:
                child_exit = _kill_reap(process)
            except BaseException as cleanup:
                reason['kill_reap_reason'] = _reason(cleanup)
    status = 'PASS' if reason is None and worker is not None else 'FAILED'
    claim_snapshot = _snapshot(claim)
    if claim_snapshot['changed_fields']:
        status = 'FAILED'
        reason = reason or {'type': 'ValueError', 'reason': 'CLAIM_MISSING_OR_FINGERPRINT_CHANGED'}
    actual = {'status': status, 'actual_exit_code': 0 if status == 'PASS' else 1,
        'actual_child_exit_code': child_exit, 'at': _now().isoformat(), 'pid': os.getpid(),
        'child_pid': process.pid if process else None, 'elapsed_seconds': time.monotonic() - started,
        'reason': reason, 'worker': worker, 'initial_terminal_checks': checks,
        'claim_initial': claim, 'claim_terminal_fingerprint': claim_snapshot['terminal'],
        'claim_changed_fields': claim_snapshot['changed_fields'],
        'sampled_tree_peak_RSS_bytes': max_tree, 'sampled_process_peak_RSS_bytes': max_process,
        'supervisor_OS_peak_RSS_bytes': _rss(),
        'parent_children_ru_maxrss_before_bytes': child_peak_before,
        'parent_children_ru_maxrss_terminal_bytes': _rss(resource.RUSAGE_CHILDREN),
        'parent_children_ru_maxrss_scope': 'ALL_REAPED_PARENT_CHILDREN_INCLUDING_PS_CONSERVATIVE_NOT_UNIQUE_WORKER',
        'physical_disk_IO_bytes': None, 'upstream_capture_and_selector_executed': False,
        'continuous_unattended_delivery_validated': False, 'historical_PIT_revalidated': False,
        'source_capability_restored_from_JSON': False, 'no_retry': True}
    _DEADLINE, _RSS_LIMIT = None, None
    actual_record, final_output, seal_error, final_calendar, final_identity_failure = None, None, None, None, None
    try:
        try:
            actual_record = _save(job / 'actual-execution.json', actual, limits, diagnostic=True)
            _DEADLINE, _RSS_LIMIT = started + limits.wall_seconds, limits.process_rss_bytes
            checks = _terminal(plan, plan_record, claim, additional=[r for r in (worker_record, actual_record) if r])
            if status == 'PASS':
                final_calendar = _outer_calendar(plan, worker)
            final_output = _scan_output(job, limits, reserve=False)
            if (time.monotonic() - started > limits.wall_seconds or _rss() > limits.process_rss_bytes
                or _rss(resource.RUSAGE_CHILDREN) > limits.process_rss_bytes):
                raise ValueError('SUPERVISOR_FINAL_SEAL_WALL_OR_RSS_EXCEEDED')
        except BaseException as failure:
            seal_error = {'type': type(failure).__name__, 'reason': _reason(failure)}
            failed_checks = getattr(failure, 'identity_checks', [])
            final_identity_failure = failed_checks[-1] if failed_checks else None
            actual = {**actual, 'status': 'FAILED', 'actual_exit_code': 1,
                      'final_seal_error': seal_error}
        final = {'status': actual['status'], 'actual_exit_code': actual['actual_exit_code'],
            'at': _now().isoformat(), 'plan_sha256': plan_record['sha256'],
            'actual_record': actual_record, 'actual_child_exit_code': child_exit,
            'pid': os.getpid(), 'child_pid': process.pid if process else None,
            'reason': reason, 'final_seal_error': seal_error, 'output': final_output,
            'terminal_identity_check_count': len(checks), 'final_identity_failure': final_identity_failure,
            'calendar_last_completed_at_final_check': final_calendar['last_completed_session'] if final_calendar else None,
            'calendar_final_verified_at': final_calendar['verified_at'] if final_calendar else None,
            'elapsed_seconds': time.monotonic() - started, 'supervisor_OS_peak_RSS_bytes': _rss(),
            'sampled_tree_peak_RSS_bytes': max_tree, 'sampled_process_peak_RSS_bytes': max_process,
            'parent_children_ru_maxrss_terminal_bytes': _rss(resource.RUSAGE_CHILDREN),
            'parent_children_ru_maxrss_scope': actual['parent_children_ru_maxrss_scope'],
            'continuous_unattended_delivery_validated': False, 'native_completion': 'UNKNOWN' if actual['status'] != 'PASS' else 'COMPLETE',
            'final_authority': 'execution-seal.json', 'prior_actual_is_final_authority': False}
        _DEADLINE, _RSS_LIMIT = None, None
        _seal(seal_path, final, seal_record)
        # The authority file has fixed size, but its last fsync may cross wall/RSS.
        try:
            _scan_output(job, limits, reserve=False)
            _fingerprints_only(plan, plan_record, claim, additional=[r for r in (worker_record, actual_record) if r])
            if final['status'] == 'PASS':
                _DEADLINE, _RSS_LIMIT = started + limits.wall_seconds, limits.process_rss_bytes
                last_calendar = _outer_calendar(plan, worker)
                if last_calendar['last_completed_session'] != final['calendar_last_completed_at_final_check']:
                    raise ValueError('CURRENT_COMPLETED_SESSION_CHANGED_DURING_FINAL_SEAL')
                _fingerprints_only(plan, plan_record, claim, additional=[r for r in (worker_record, actual_record) if r])
            if (time.monotonic() - started > limits.wall_seconds or _rss() > limits.process_rss_bytes
                or _rss(resource.RUSAGE_CHILDREN) > limits.process_rss_bytes):
                raise ValueError('SUPERVISOR_FINAL_SEAL_WALL_OR_RSS_EXCEEDED')
        except BaseException as failure:
            _DEADLINE, _RSS_LIMIT = None, None
            failed_checks = getattr(failure, 'identity_checks', [])
            final.update(status='FAILED', actual_exit_code=1,
                         final_identity_failure=failed_checks[-1] if failed_checks else final_identity_failure,
                         final_seal_error={'type': type(failure).__name__, 'reason': _reason(failure)})
            _seal(seal_path, final, seal_record)
        return {**actual, 'status': final['status'], 'actual_exit_code': final['actual_exit_code'],
                'final_authority': str(seal_path), 'final_seal_error': final['final_seal_error']}
    finally:
        _DEADLINE, _RSS_LIMIT = previous_guard


def _install_worker_audit(job):
    def audit(name, args):
        if name in ('socket.connect', 'socket.connect_ex', 'socket.__new__', 'socket.sendto',
                    'socket.getaddrinfo', 'sqlite3.connect', 'os.system', 'os.fork', 'subprocess.Popen',
                    'os.remove', 'os.rmdir'):
            raise RuntimeError('WORKER_NETWORK_DB_PROCESS_OR_DELETE_FORBIDDEN')
        if name == 'import' and any(args[0] == p or args[0].startswith(p + '.') for p in ('baostock', 'akshare', 'tushare')):
            raise RuntimeError('WORKER_SDK_IMPORT_FORBIDDEN')
        if name == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
            flags = args[2]
            if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                path = Path(os.fsdecode(args[0])).resolve()
                if (not path.is_relative_to(job) or path in {job / name for name in
                        ('registered-plan.json', 'execution-claim.json', 'execution-seal.json', 'actual-execution.json')}):
                    raise RuntimeError('WORKER_WRITE_OUTSIDE_OWNED_OUTPUT_FORBIDDEN')
    sys.addaudithook(audit)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('prepare', 'execute', 'worker'), required=True)
    parser.add_argument('--run', type=Path); parser.add_argument('--run-sha256')
    parser.add_argument('--input', type=Path); parser.add_argument('--input-sha256')
    parser.add_argument('--sources-manifest', type=Path); parser.add_argument('--sources-manifest-sha256')
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--plan', type=Path); parser.add_argument('--plan-sha256')
    parser.add_argument('--membership-mode', choices=('DATED_ONLY', 'CURRENT_COHORT_BACKVIEW'), default='DATED_ONLY')
    parser.add_argument('--test-fixture', action='store_true')
    args = parser.parse_args(argv)
    sys.dont_write_bytecode = True
    try:
        if args.mode == 'prepare':
            if any(v is None for v in (args.run, args.run_sha256, args.input, args.input_sha256,
                                      args.sources_manifest, args.sources_manifest_sha256, args.output_dir)):
                parser.error('prepare requires exact run/input/source paths and SHA plus a new output directory')
            result = prepare_daily_saved_r2(run_path=args.run, run_sha256=args.run_sha256,
                input_path=args.input, input_sha256=args.input_sha256,
                sources_manifest_path=args.sources_manifest,
                sources_manifest_sha256=args.sources_manifest_sha256, output_dir=args.output_dir,
                membership_mode=args.membership_mode, test_fixture=args.test_fixture)
        elif args.mode == 'execute':
            if args.plan is None or args.plan_sha256 is None:
                parser.error('execute requires exact registered plan path and SHA')
            result = execute_daily_saved_r2(plan_path=args.plan, plan_sha256=args.plan_sha256)
        else:
            record = _pin(args.plan, args.plan_sha256)
            plan = _read(record)
            job = Path(plan['output_dir']).resolve(strict=True)
            claim = _pin(job / 'execution-claim.json')
            claim_value = _read(claim)
            if claim_value['pid'] != os.getppid() or claim_value['plan_sha256'] != record['sha256']:
                raise ValueError('WORKER_EXACT_PARENT_CLAIM_REQUIRED')
            _install_worker_audit(job)
            signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError('WORKER_WALL_BUDGET_EXCEEDED')))
            signal.setitimer(signal.ITIMER_REAL, plan['limits']['wall_seconds'])
            return _worker(plan, record, claim)
        print(json.dumps({'status': result.get('status', 'REGISTERED_NOT_EXECUTED'),
                          'plan': result if args.mode == 'prepare' else None,
                          'actual_exit_code': result.get('actual_exit_code')}, ensure_ascii=False))
        return result.get('actual_exit_code', 0)
    except (OSError, ValueError, RuntimeError, TimeoutError) as error:
        print(json.dumps({'status': 'FAILED', 'reason': _reason(error)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    # Direct -I worker execution still uses the explicitly selected source tree.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    raise SystemExit(main())
