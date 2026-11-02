"""One supervised current-calendar bootstrap on the original recovery runtime.

V2 selects the kernel-wait supervisor. The version-independent calendar slot
continues to reject every previous claim, including failed V1 acquisitions.

This is a source stage, never a complete premarket or historical PIT claim.
The owning full workflow must subtract its planning/execution accounting from
the same registered limits. Existing attempts and source clocks are immutable.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sys
import time
import builtins

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import build_premarket_source_bundle_v3 as files
from scripts import codex_daily_v2 as daily
from scripts import codex_recovered_premarket_v1 as bounded
from scripts import codex_runtime_recovery_v1 as recovery
from scripts import codex_full_factor_recovery_v1 as inventory
from stockfish_core.yaogu import premarket_capture_cache_v1 as native
from stockfish_core.yaogu import premarket_workflow_v1 as workflow
from stockfish_core.yaogu.premarket_runtime_v1 import Budget

VERSION = 'CODEX_CURRENT_CALENDAR_BOOTSTRAP_V2'
SH = timezone(timedelta(hours=8))
MAX_FILE = 16 * 1024**2


def need(value, reason):
    if not value:
        raise ValueError('CURRENT_CALENDAR_' + reason)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _now():
    return datetime.now(timezone.utc)


def _clock(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    need(result.tzinfo is not None and result.utcoffset() is not None, 'AWARE_CLOCK_REQUIRED')
    return result


def _runtime(runtime, allow_test_fixture=False):
    need(isinstance(runtime, recovery.RecoveryRuntime), 'ORIGINAL_RECOVERY_RUNTIME_REQUIRED')
    fixture = runtime.recovery_plan.get('test_fixture') is True
    need(not fixture or allow_test_fixture, 'TEST_FIXTURE_NOT_PRODUCTION')
    need(Path(runtime.profile['source_root']).absolute() == ROOT
         and Path(runtime.profile['python']).absolute() == Path(sys.executable).absolute(),
         'REGISTERED_SOURCE_AND_INTERPRETER_REQUIRED')
    runtime.check_binding()
    return fixture


def _limits(runtime, requested=None):
    config = bounded.configuration(runtime, bounded.PROFILE)
    limits = dict(config['limits'])
    if requested is not None:
        need(type(requested) is dict and set(requested) == set(limits), 'EXACT_LIMIT_FIELDS')
        for key, value in requested.items():
            need(type(value) in (int, float) and math.isfinite(value) and
                 0 < value <= limits[key], 'LIMIT_MUST_NOT_INCREASE:' + key)
        limits = dict(requested)
    limits['capture_read_bytes'] = min(limits['capture_read_bytes'], limits['supervisor_read_bytes'])
    return config, limits


def _budget(limits):
    result = Budget(limits)
    result.utc_started_at = _now()
    original = result.check

    def checked(stage, *, force=False):
        elapsed = (_now() - result.utc_started_at).total_seconds()
        need(0 <= elapsed <= result.limits['max_seconds'], 'UTC_TIME_BUDGET_EXCEEDED')
        original(stage, force=force)

    result.check = checked
    return result


@contextmanager
def _diagnostic_reads(budget):
    """Count the old diagnostic without nesting its exclusive Budget meter.

    Its own smaller diagnostic limits still apply. This outer counter charges
    every returned byte again to the owning workflow's cumulative allocation.
    It does not replace the old diagnostic's globals or change its limits.
    """
    original_open, original_io, original_read = builtins.open, io.open, os.read

    def charge(value):
        if isinstance(value, (bytes, str)):
            budget.charge_read(len(value.encode('utf-8')) if isinstance(value, str) else len(value))
        return value

    class Reader:
        def __init__(self, handle): self.handle = handle
        def __getattr__(self, name): return getattr(self.handle, name)
        def __enter__(self): self.handle.__enter__(); return self
        def __exit__(self, *args): return self.handle.__exit__(*args)
        def read(self, *args, **kwargs): return charge(self.handle.read(*args, **kwargs))
        def readline(self, *args, **kwargs): return charge(self.handle.readline(*args, **kwargs))
        def readlines(self, *args, **kwargs): return [charge(v) for v in self.handle.readlines(*args, **kwargs)]
        def __iter__(self): return self
        def __next__(self): return charge(next(self.handle))

    def opening(original, *args, **kwargs):
        handle = original(*args, **kwargs)
        return Reader(handle)

    builtins.open = lambda *a, **k: opening(original_open, *a, **k)
    io.open = lambda *a, **k: opening(original_io, *a, **k)
    os.read = lambda *a, **k: charge(original_read(*a, **k))
    try:
        yield
    finally:
        builtins.open, io.open, os.read = original_open, original_io, original_read


def _diagnostic(runtime, budget):
    with _diagnostic_reads(budget):
        try:
            value = daily._plan(runtime)
            return dict(status='RETURNED', original_result=value)
        except Exception as error:
            # This preserves the original diagnostic, not an execution waiver.
            value = dict(status='BLOCKED', reason_type=type(error).__name__, reason=str(error)[:500])
    budget.check('original_daily_diagnostic_return', force=True)
    return value


def _sources(runtime):
    prepared = daily.original._prepared(runtime.profile)
    refs = daily._workflow().code_refs()
    extra = [__file__, recovery.__file__, bounded.__file__, inventory.__file__, files.__file__,
             files.previous.__file__, daily.__file__, runtime.profile['config']]
    from scripts import codex_sdk_tree_supervisor_v2 as supervisor
    extra.append(supervisor.__file__)
    refs += [files.read(Path(path).absolute())[1] for path in extra]
    merged = {}
    for reference in refs:
        prior = merged.setdefault(reference['path'], reference)
        need(prior == reference, 'SOURCE_REFERENCE_CONFLICT')
    return dict(prepared=daily._snapshot(prepared), references=sorted(merged.values(), key=lambda r:r['path']))


def _same_sources(runtime, expected):
    need(_sources(runtime) == expected, 'SOURCE_OR_CONFIGURATION_CHANGED')


def _read(path, budget=None, expected=None):
    raw, reference = files.read(Path(path).absolute(), MAX_FILE)
    if budget is not None and not budget._meter_active:
        budget.charge_read(len(raw))
    need(expected is None or reference == expected or reference['sha256'] == expected,
         'CONTROL_REFERENCE_CHANGED')
    return native._json(raw), reference


def _write(path, value):
    """Publish only a previously absent owned control; durable and no aliases."""
    path = Path(path).absolute()
    raw = canonical(value)
    need(len(raw) <= MAX_FILE, 'CONTROL_SIZE_BOUND')
    with files._directory_fd(path.parent) as directory:
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        try:
            position = 0
            while position < len(raw):
                position += os.write(fd, raw[position:])
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(directory)
    return files.read(path, MAX_FILE)[1]


def _slot(runtime, local_day):
    need(date.fromisoformat(local_day).isoformat() == local_day, 'CANONICAL_BUSINESS_DAY')
    return runtime.directory / 'current-calendar' / local_day


def _fresh_slot(runtime, local_day):
    slot = _slot(runtime, local_day)
    need(not os.path.lexists(slot), 'EXISTING_DAY_CLAIM_NO_RETRY')
    return slot


def _cache(runtime, budget, *, readonly):
    root = Path(runtime.profile['state_root']) / 'cache'
    for name in ('', 'index', 'raw', 'raw-index', 'locators'):
        with files._directory_fd(root / name):
            pass
    options = dict(offline=readonly, retry_failed=False, request_timeout_seconds=min(
        90, budget.limits['request_timeout_seconds']), max_http_requests_per_request=1)
    if not readonly:
        return native.CaptureCache(root, budget, options)
    # The read-only path must not call mkdir, even with exist_ok=True.
    import threading
    value = object.__new__(native.CaptureCache)
    value.root, value.budget, value.config = root, budget, native._config(options)
    value._owner = (os.getpid(), threading.get_ident())
    value._known, value._attempted_keys = {}, set()
    value.timeout, value.max_http, value.max_entries = options['request_timeout_seconds'], 1, 8192
    value.executor = lambda *_a, **_k: (_ for _ in ()).throw(ValueError('READ_ONLY_CALENDAR'))
    return value


def _spec(config, local_day):
    local = date.fromisoformat(local_day)
    back = config['capture'].get('calendar_lookback_days', 800)
    ahead = config['capture'].get('calendar_lookahead_days', 32)
    need(type(back) is int and 90 <= back <= 1500 and type(ahead) is int and 1 <= ahead <= 366,
         'ORIGINAL_FINITE_CALENDAR_WINDOW')
    return native.RequestSpec('calendar', dict(start_date=str(local-timedelta(days=back)),
        end_date=str(local+timedelta(days=ahead))), local_day, role='CALENDAR')


def validate_calendar(cache, record, *, now, exact_spec=None):
    result = cache.verify(record)
    metadata = record.to_dict() if isinstance(record, native.CaptureRecord) else record
    need(result['endpoint'] == 'calendar' and result['provider'] == 'baostock'
         and result['status'] == 'COMPLETE' and result['pagination_complete'] is True,
         'COMPLETE_ORIGINAL_CALENDAR_REQUIRED')
    need(not result.get('gaps') and not result.get('error_code') and not result.get('failure_category'),
         'COMPLETE_CALENDAR_FAILURE_CONFLICT')
    if exact_spec is not None:
        need(metadata['request_key'] == exact_spec.key and result['parameters'] == exact_spec.parameters,
             'CALENDAR_EXACT_REQUEST_REQUIRED')
    start, end = (date.fromisoformat(result['parameters'][key]) for key in ('start_date','end_date'))
    need(0 < (end-start).days <= 1866, 'FINITE_RESPONSE_WINDOW')
    days = {}
    for row in result['rows']:
        need(type(row) is dict and set(row) == {'calendar_date','is_trading_day'}, 'CALENDAR_EXACT_FIELDS')
        day = date.fromisoformat(row['calendar_date'])
        need(str(day) == row['calendar_date'] and day not in days and
             type(row['is_trading_day']) is str and row['is_trading_day'] in ('0','1'), 'CALENDAR_ROW_INVALID')
        days[day] = row['is_trading_day']
    need(set(days) == {start+timedelta(days=n) for n in range((end-start).days+1)},
         'CALENDAR_SERVER_WINDOW_TRUNCATED_OR_DUPLICATE')
    local = now.astimezone(SH)
    need(local.date() in days, 'CALENDAR_TODAY_NOT_COVERED')
    sessions = sorted(day for day, flag in days.items() if flag == '1')
    completed = [day for day in sessions if datetime.combine(day, datetime.min.time(), SH)
                 + timedelta(hours=18) <= local]
    need(len(completed) >= 67, 'CALENDAR_67_COMPLETED_SESSIONS_REQUIRED')
    future = [day for day in sessions if day > completed[-1]]
    need(future, 'CALENDAR_NEXT_SESSION_REQUIRED')
    gate = workflow._latest_gate(result['rows'], str(completed[-1]), str(future[0]), now)
    need(_clock(metadata['started_at']) <= _clock(metadata['first_seen_at']) <=
         _clock(metadata['ingested_at']) <= _clock(metadata['completed_at']) <= now,
         'ORIGINAL_CALENDAR_CLOCK_ORDER')
    return gate


def _prior_files(plan):
    root = Path(plan['cache_root'])
    actual = {r['path']:r for r in inventory._inventory(root)}
    for row in plan['original_cache_inventory']:
        need(actual.get(row['path']) == row, 'ORIGINAL_CACHE_CHANGED')


def _metrics(budget):
    budget.check('calendar_accounting', force=True)
    return dict(metrics=budget.metrics, actual_elapsed_seconds=max(time.monotonic()-budget.started,
        (_now()-budget.utc_started_at).total_seconds()), utc_started_at=budget.utc_started_at.isoformat())


def _cumulative(plan, parent, worker, report, output_bytes):
    parts = [plan['planning']['metrics'], parent['metrics'], worker['metrics']]
    reads = {phase:sum(item['returned_read_bytes'][phase] for item in parts)
             for phase in ('capture','core','theme','presentation')}
    calls = {}
    for name in set().union(*(item['calls'] for item in parts)):
        values = [item['calls'].get(name) for item in parts]
        need(all(value is None or type(value) is int and value >= 0 for value in values), 'CALL_ACCOUNTING_INVALID')
        calls[name] = None if any(value is None for value in values) else sum(values)
    supervisor_read = report['terminal_supervisor_returned_read_bytes']
    need(type(supervisor_read) is int and supervisor_read >= 0, 'SUPERVISOR_READ_ACCOUNTING_REQUIRED')
    limits = plan['limits']
    for counter, limit in (('requests','max_requests'),('sdk_reserved','max_sdk_calls'),('http_reserved','max_http_attempts')):
        need(type(calls[counter]) is int and calls[counter] <= limits[limit], 'CUMULATIVE_CALL_LIMIT')
    need(all(reads[phase] <= limits[phase+'_read_bytes'] for phase in reads)
         and sum(reads.values()) + supervisor_read <= limits['supervisor_read_bytes'], 'CUMULATIVE_READ_LIMIT')
    elapsed = plan['planning']['actual_elapsed_seconds'] + parent['actual_elapsed_seconds']
    need(elapsed <= limits['max_seconds'] and output_bytes <= limits['output_bytes'], 'CUMULATIVE_TIME_OR_OUTPUT_LIMIT')
    return dict(calls=calls,returned_read_bytes=reads,output_bytes=output_bytes,
        supervisor_returned_read_bytes=supervisor_read,actual_elapsed_seconds=elapsed,
        time_scope='PLANNING_PLUS_EXECUTE_PARENT; WORKER_AND_SUPERVISOR_ARE_WITHIN_PARENT',
        read_scope='PLANNING_PARENT_WORKER_PLUS_SEPARATE_SUPERVISOR; FINALIZATION_RESERVE_INCLUDED')


def prepare(runtime, *, limits=None, allow_test_fixture=False):
    fixture = _runtime(runtime, allow_test_fixture)
    config, selected = _limits(runtime, limits)
    budget = _budget(selected)
    budget.charge_read(Path(runtime.profile['config']).stat().st_size)
    local_day = str(_now().astimezone(SH).date())
    with runtime.exclusive():
        _fresh_slot(runtime, local_day)
        original = _diagnostic(runtime, budget)
        with budget.meter_files():
            sources = _sources(runtime)
            cache = _cache(runtime, budget, readonly=True)
            record = cache.calendar_reference(_now())
            spec = _spec(config, local_day)
            if record is None:
                # Reject failed/unindexed exact attempts before any new claim.
                record = cache.lookup(spec)
            gate = validate_calendar(cache, record, now=_now()) if record is not None else None
            need(record is not None or (selected['max_requests'] >= 1 and selected['max_sdk_calls'] >= 3),
                 'ONE_CALENDAR_THREE_SDK_BUDGET_REQUIRED')
            need(config['capture'].get('offline', False) is False or record is not None, 'OFFLINE_CALENDAR_MISSING')
            capacity = runtime.preflight(expected_growth_bytes=runtime.profile['storage']['expected_growth_bytes'])
            need(capacity['allowed'], 'ORIGINAL_CAPACITY_BLOCKED')
            original_files = inventory._inventory(cache.root)
            need(inventory._size(original_files) <= selected['cache_bytes'], 'ORIGINAL_CACHE_BUDGET_EXCEEDED')
            references = cache.terminal()
            _same_sources(runtime, sources)
            runtime.check_binding()
        need(local_day == str(_now().astimezone(SH).date()), 'BUSINESS_DAY_CHANGED')
    value = dict(schema_version=VERSION, runtime_name=runtime.profile['name'], registry_root=str(runtime.registry_root),
        original_scope=runtime.scope, source_root=str(ROOT), local_day=local_day, slot=str(_slot(runtime,local_day)),
        cache_root=str(cache.root), configuration=config, limits=selected, sources=sources,
        request_spec=spec.to_dict(), request_key=spec.key,
        action='REUSE_COMPLETE' if record is not None else 'NEW_CALENDAR_ONCE',
        original_record=None if record is None else record.to_dict(), calendar_gate=gate,
        original_daily_plan=original, source_references=references, original_cache_inventory=original_files,
        planning=_metrics(budget), planned_new_calls=dict(requests=0 if record else 1,sdk_reserved=0 if record else 3,http_reserved=0),
        source_capability_issued=False, complete_daily_delivery=False, historical_pit_restored=False,
        test_fixture=fixture, provider_future_window='MUST_BE_PROVEN_BY_COMPLETE_RETURNED_NATURAL_DAYS')
    value['plan_id'] = digest(value)
    return value


def _validate_plan(runtime, plan, budget, allow_test_fixture=False):
    fixture = _runtime(runtime, allow_test_fixture)
    need(plan.get('schema_version') == VERSION and plan.get('plan_id') == digest({k:v for k,v in plan.items() if k!='plan_id'}), 'PLAN_HASH_CHANGED')
    need(plan['test_fixture'] is fixture and plan['original_scope'] == runtime.scope and
         plan['runtime_name'] == runtime.profile['name'] and plan['registry_root'] == str(runtime.registry_root)
         and plan['source_root'] == str(ROOT)
         and plan['cache_root'] == str(Path(runtime.profile['state_root'])/'cache'), 'PLAN_RUNTIME_CHANGED')
    need(all(plan[name] is False for name in ('source_capability_issued','complete_daily_delivery','historical_pit_restored')),
         'PLAN_AUTHORITY_CHANGED')
    config, limits = _limits(runtime, plan['limits'])
    need(config == plan['configuration'] and limits == plan['limits'], 'PLAN_CONFIGURATION_CHANGED')
    need(plan['local_day'] == str(_now().astimezone(SH).date()) and plan['slot'] == str(_slot(runtime,plan['local_day'])), 'PLAN_DAY_CHANGED')
    spec = _spec(config,plan['local_day'])
    need(spec.to_dict() == plan['request_spec'] and spec.key == plan['request_key'], 'PLAN_REQUEST_CHANGED')
    _same_sources(runtime, plan['sources'])
    _prior_files(plan)
    budget.check('calendar_plan_checked', force=True)
    return spec


def _deduct(limits, accounting):
    remaining = dict(limits)
    metrics = accounting['metrics']
    remaining['max_seconds'] -= math.ceil(max(accounting['actual_elapsed_seconds'], metrics['elapsed_seconds']))
    for limit, name in (('max_requests','requests'),('max_sdk_calls','sdk_reserved'),('max_http_attempts','http_reserved')):
        value = metrics['calls'][name]
        need(type(value) is int and value >= 0, 'ACCOUNTING_COUNTER_INVALID')
        remaining[limit] -= value
    for phase, count in metrics['returned_read_bytes'].items():
        need(type(count) is int and count >= 0, 'ACCOUNTING_READ_INVALID')
        remaining[phase+'_read_bytes'] -= count
        remaining['supervisor_read_bytes'] -= count
    need(all(type(v) in (int,float) and math.isfinite(v) and v > 0 for v in remaining.values()), 'CUMULATIVE_BUDGET_EXHAUSTED')
    remaining['capture_read_bytes'] = min(remaining['capture_read_bytes'],remaining['supervisor_read_bytes'])
    remaining['request_timeout_seconds'] = min(remaining['request_timeout_seconds'], remaining['max_seconds'])
    return remaining


@contextmanager
def _worker_failure(slot,budget):
    try:
        yield
    except BaseException as error:
        failure=dict(schema_version=VERSION,status='FAILED_OR_UNKNOWN_RETAINED_NO_RETRY',
            reason_type=type(error).__name__,reason=str(error)[:500],metrics=budget.metrics,
            source_completion='UNKNOWN_NOT_SUCCESS',actual_counts_complete=not bool(budget.metrics['pending_request_results']),
            unknown_pending_calls_must_not_be_read_as_zero=True,source_capability_issued=False)
        try:
            _write(Path(slot)/'worker/failure.json',failure)
        except Exception:
            pass  # The parent still has the durable claim and supervised failure.
        raise


def _worker(plan_path, plan_sha, claim_path, claim_sha, control_dir):
    from scripts import codex_sdk_tree_supervisor_v2 as supervisor
    context = supervisor.validate_worker_context(Path(control_dir))
    plan, plan_ref = _read(plan_path, expected=plan_sha)
    claim, claim_ref = _read(claim_path, expected=claim_sha)
    need(sys.flags.isolated and sys.dont_write_bytecode and sys.pycache_prefix ==
         str(Path(plan['slot'])/'absent-bytecode') and not os.path.lexists(sys.pycache_prefix),
         'WORKER_FRESH_ISOLATED_INTERPRETER_REQUIRED')
    runtime = recovery.load_runtime(plan['runtime_name'], Path(plan['registry_root']))
    budget = _budget(claim['worker_limits'])
    budget.charge_read(plan_ref['bytes']+claim_ref['bytes'])
    context_bytes = context['worker_context_returned_read_bytes']
    need(type(context_bytes) is int and context_bytes >= 0, 'OWNED_CONTEXT_READ_ACCOUNTING_REQUIRED')
    budget.charge_read(context_bytes)
    need(claim['schema_version'] == VERSION and claim['plan'] == plan_ref and claim['plan_id'] == plan['plan_id']
         and claim['slot'] == plan['slot'] and Path(claim_path) == Path(plan['slot'])/'claim.json'
         and claim['parent_pid'] == os.getppid(), 'OWNED_WORKER_CLAIM_MISMATCH')
    need(context['limits'] == claim['supervisor_limits'] and
         set(claim['worker_limits']) == set(plan['limits']) and all(
             0 < value <= plan['limits'][name] for name,value in claim['worker_limits'].items())
         and claim['worker_limits']['max_requests'] <= 1 and claim['worker_limits']['max_sdk_calls'] <= 3,
         'OWNED_WORKER_BUDGET_BINDING_CHANGED')
    with _worker_failure(plan['slot'],budget), budget.meter_files():
        spec = _validate_plan(runtime, plan, budget)
        cache = _cache(runtime, budget, readonly=False)
        budget.cache_dir = cache.root
        budget.job_dir = Path(plan['slot'])/'worker'
        record = cache.lookup(spec) if plan['original_record'] is None else native.CaptureRecord.from_metadata(plan['original_record'])
        if plan['action'] == 'NEW_CALENDAR_ONCE':
            need(record is None, 'PLANNED_NEW_CALENDAR_ALREADY_EXISTS')
            remaining = budget.limits['max_seconds'] - max(time.monotonic()-budget.started,
                (_now()-budget.utc_started_at).total_seconds())
            need(remaining > 0, 'WORKER_UTC_BUDGET_EXHAUSTED')
            cache.timeout = min(cache.timeout, remaining)
            record = cache.capture_one(spec)
            need(_clock(record.to_dict()['started_at']) >= _clock(claim['claimed_at']), 'NEW_SOURCE_CLOCK_BEFORE_CLAIM')
        else:
            need(plan['action'] == 'REUSE_COMPLETE' and record.to_dict() == plan['original_record'], 'REUSE_RECORD_CHANGED')
        gate = validate_calendar(cache, record, now=_now(), exact_spec=spec if plan['action']=='NEW_CALENDAR_ONCE' else None)
        references = cache.terminal()
        _same_sources(runtime, plan['sources'])
        _prior_files(plan)
        _read(plan_path,budget,plan_ref);_read(claim_path,budget,claim_ref)
        budget.check('calendar_worker_terminal',force=True)
        # The sealed result is read once by _write; reserve its bounded final
        # read before taking the last serializable accounting snapshot.
        final_read_reserve = 65536
        budget.charge_read(final_read_reserve)
        before_publication_reads = budget.metrics['returned_read_bytes']['capture']
        result = dict(schema_version=VERSION,status='COMPLETE_CURRENT_CALENDAR_SOURCE',plan_id=plan['plan_id'],
            request_key=plan['request_key'],record=record.to_dict(),calendar_gate=gate,source_references=references,
            source_capability_issued=False,complete_daily_delivery=False,historical_pit_restored=False,
            accounting=_metrics(budget),accounting_finalization_read_reserve_bytes=final_read_reserve)
        _write(Path(plan['slot'])/'worker/result.json',result)
        budget.check('calendar_worker_result_published',force=True)
        need(budget.metrics['returned_read_bytes']['capture']-before_publication_reads <= final_read_reserve,
             'WORKER_FINALIZATION_READ_RESERVE_EXCEEDED')
    return result


def execute(runtime, plan, *, limits=None, allow_test_fixture=False):
    """Own the real lock and supervision; never accept an inner success alone."""
    from scripts import codex_sdk_tree_supervisor_v2 as supervisor
    _runtime(runtime, allow_test_fixture)
    available = _deduct(plan['limits'], plan['planning'])
    if limits is not None:
        _, requested = _limits(runtime,limits)
        available = {name:min(value,requested[name]) for name,value in available.items()}
    limits = available
    budget = _budget(available)
    slot = Path(plan['slot'])
    report = None
    completion_ref = None
    with runtime.exclusive():
        with budget.meter_files():
            _validate_plan(runtime,plan,budget,allow_test_fixture)
            _fresh_slot(runtime,plan['local_day'])
            capacity = runtime.preflight(expected_growth_bytes=runtime.profile['storage']['expected_growth_bytes'])
            need(capacity['allowed'], 'ORIGINAL_CAPACITY_BLOCKED')
            parent = slot.parent
            with files._directory_fd(parent, create=True) as fd:
                os.mkdir(slot.name,0o700,dir_fd=fd)
                os.fsync(fd)
            with files._directory_fd(slot) as fd:
                os.mkdir('worker',0o700,dir_fd=fd);os.fsync(fd)
            plan_ref = _write(slot/'plan.json',plan)
            worker_limits = _deduct(limits,_metrics(budget))
            worker_limits['output_bytes'] -= inventory._size(inventory._inventory(slot))
            need(worker_limits['output_bytes'] > 0, 'CONTROL_OUTPUT_BUDGET_EXHAUSTED')
            supervisor_limits = dict(worker_limits)
            supervisor_limits['supervisor_read_bytes'] = min(512*1024**2,worker_limits['supervisor_read_bytes']//4)
            supervisor_limits['output_bytes'] = min(8*1024**2,worker_limits['output_bytes']//4)
            need(supervisor_limits['supervisor_read_bytes'] > 0 and supervisor_limits['output_bytes'] > 0,
                 'SUPERVISOR_RESERVE_REQUIRED')
            worker_limits['supervisor_read_bytes'] -= supervisor_limits['supervisor_read_bytes']
            worker_limits['capture_read_bytes'] = min(worker_limits['capture_read_bytes'],worker_limits['supervisor_read_bytes'])
            worker_limits['output_bytes'] -= supervisor_limits['output_bytes']
            worker_limits['max_requests'] = min(1,worker_limits['max_requests'])
            worker_limits['max_sdk_calls'] = min(3,worker_limits['max_sdk_calls'])
            worker_limits['max_http_attempts'] = min(1,worker_limits['max_http_attempts'])
            claim = dict(schema_version=VERSION,slot=str(slot),plan=plan_ref,plan_id=plan['plan_id'],
                parent_pid=os.getpid(),claimed_at=_now().isoformat(),worker_limits=worker_limits,
                supervisor_limits=supervisor_limits)
            claim_ref = _write(slot/'claim.json',claim)
        pycache = slot/'absent-bytecode'
        need(not os.path.lexists(pycache), 'FRESH_ABSENT_BYTECODE_PATH_REQUIRED')
        command = [str(runtime.profile['python']),'-I','-B','-X','pycache_prefix='+str(pycache),
            str(Path(__file__).absolute()),'--worker',
            '--plan',str(slot/'plan.json'),'--plan-sha256',plan_ref['sha256'],
            '--claim',str(slot/'claim.json'),'--claim-sha256',claim_ref['sha256'],
            '--supervisor-control',str(slot/'supervision')]
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        environment.pop('PYTHONPATH',None);environment.pop('PYTHONHOME',None)
        try:
            report = supervisor.run_supervised(command,cwd=ROOT,env=environment,limits=supervisor_limits,
                control_dir=slot/'supervision',source_references=plan['sources']['references']+[plan_ref,claim_ref],
                accepted_exit_codes=(0,),test_fixture=plan['test_fixture'])
            need(report.get('status') == 'COMPLETE' and report.get('child_exit_code') == 0
                 and report.get('root_reaped') is True and report.get('all_observed_owned_exited') is True
                 and report.get('all_observed_descendants_gone') is True,
                 'OUTER_SUPERVISION_FAILED')
            with budget.meter_files():
                inner, inner_ref = _read(slot/'worker/result.json',budget)
                need(inner['status']=='COMPLETE_CURRENT_CALENDAR_SOURCE' and inner['plan_id']==plan['plan_id'], 'WORKER_RESULT_PARENT_CHANGED')
                cache = _cache(runtime,budget,readonly=True)
                record = native.CaptureRecord.from_metadata(inner['record'])
                need(cache.lookup(native.RequestSpec(**record.to_dict()['request_spec'])).to_dict()==record.to_dict(), 'RETURNED_RECORD_NOT_INDEXED')
                gate = validate_calendar(cache,record,now=_now())
                cache.terminal();_same_sources(runtime,plan['sources']);_prior_files(plan)
                _read(slot/'plan.json',budget,plan_ref);_read(slot/'claim.json',budget,claim_ref)
            diagnostic = _diagnostic(runtime,budget)
            # Reserve the final receipt read and its source recheck before
            # sealing accounting. This is conservative, explicitly labelled;
            # the actual post-snapshot reads must fit inside the reserve.
            finalization_reserve = sum(ref['bytes'] for ref in plan['sources']['references'])*4 + MAX_FILE
            budget.charge_read(finalization_reserve)
            before_finalization_reads = budget.metrics['returned_read_bytes']['capture']
            accounting = _metrics(budget)
            result = dict(schema_version=VERSION,status='COMPLETE_CURRENT_CALENDAR_SOURCE',plan_id=plan['plan_id'],
                record=inner['record'],calendar_gate=gate,original_daily_plan_after=diagnostic,
                planning=plan['planning'],parent_accounting=accounting,worker_accounting=inner['accounting'],
                supervisor=report,worker_result=inner_ref,claim=claim_ref,plan=plan_ref,
                original_limits=plan['configuration']['limits'],selected_limits=plan['limits'],
                cumulative_time_scope='PLANNING_PLUS_PARENT_ELAPSED; WORKER_AND_SUPERVISOR_OVERLAP_PARENT',
                cumulative_read_scope='ADD_PLANNING_PARENT_WORKER_AND_SUPERVISOR_RETURNED_READ_BYTES',
                output_bytes=inventory._size(inventory._inventory(slot)),
                parent_finalization_read_reserve_bytes=finalization_reserve,
                source_capability_issued=False,complete_daily_delivery=False,historical_pit_restored=False)
            result['parent_terminal_accounting'] = accounting
            output_before = result['output_bytes']
            for _ in range(8):
                result['cumulative_accounting'] = _cumulative(plan,accounting,
                    inner['accounting'],report,result['output_bytes'])
                size = len(canonical(result))
                if result['output_bytes'] == output_before+size:
                    break
                result['output_bytes'] = output_before+size
            need(result['output_bytes'] == output_before+len(canonical(result)), 'OUTPUT_ACCOUNTING_FIXED_POINT')
            with budget.meter_files():
                completion_ref = _write(slot/'completion.json',result)
                _same_sources(runtime,plan['sources']);_prior_files(plan)
                _read(slot/'completion.json',budget,completion_ref)
                budget.check('calendar_parent_completion_published',force=True)
            need(budget.metrics['returned_read_bytes']['capture']-before_finalization_reads <= finalization_reserve,
                 'PARENT_FINALIZATION_READ_RESERVE_EXCEEDED')
            need(inventory._size(inventory._inventory(slot)) == result['output_bytes'], 'FINAL_OUTPUT_ACCOUNTING_CHANGED')
            need(plan['planning']['actual_elapsed_seconds']+_metrics(budget)['actual_elapsed_seconds'] <= plan['limits']['max_seconds'],
                 'FINAL_CUMULATIVE_TIME_EXCEEDED')
            return result
        except BaseException as error:
            revocation = 'NO_COMPLETION_PUBLISHED'
            if completion_ref is not None:
                # Keep the failed candidate as evidence, but remove its success
                # filename only if it is still precisely our own publication.
                try:
                    need(files.read(slot/'completion.json')[1] == completion_ref, 'FAILED_COMPLETION_REPLACED')
                    with files._directory_fd(slot) as fd:
                        need(not os.path.lexists(slot/'rejected-completion.json'), 'REJECTED_COMPLETION_EXISTS')
                        os.rename('completion.json','rejected-completion.json',src_dir_fd=fd,dst_dir_fd=fd)
                        os.fsync(fd)
                    revocation = 'OWNED_COMPLETION_RETAINED_AS_REJECTED'
                except Exception as revocation_error:
                    # An alien replacement is never deleted; the failed claim
                    # must still be recorded and must never authorize a retry.
                    revocation = 'FOREIGN_OR_MISSING_COMPLETION_PRESERVED:' + type(revocation_error).__name__
            failure = dict(schema_version=VERSION,status='FAILED_OR_UNKNOWN_RETAINED_NO_RETRY',plan_id=plan['plan_id'],
                reason_type=type(error).__name__,reason=str(error)[:500],supervisor=report,
                completion_revocation=revocation,parent_metrics=budget.metrics,
                worker_completion_not_accepted=True,source_capability_issued=False,complete_daily_delivery=False)
            if not os.path.lexists(slot/'failure.json'):
                try:
                    _write(slot/'failure.json',failure)
                except Exception:
                    pass  # Durable slot/claim remains authoritative UNKNOWN.
            raise


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    parser.add_argument('--worker',action='store_true')
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--plan-sha256',required=True)
    parser.add_argument('--claim',type=Path,required=True)
    parser.add_argument('--claim-sha256',required=True)
    parser.add_argument('--supervisor-control',type=Path,required=True)
    args=parser.parse_args(argv)
    need(args.worker,'ONLY_OWNED_SUPERVISED_WORKER_CLI')
    try:
        value=_worker(args.plan,args.plan_sha256,args.claim,args.claim_sha256,args.supervisor_control)
        print(json.dumps(dict(status=value['status'],plan_id=value['plan_id']),ensure_ascii=False))
        return 0
    except BaseException as error:
        print(json.dumps(dict(status='FAILED_OR_UNKNOWN_RETAINED_NO_RETRY',reason_type=type(error).__name__,reason=str(error)[:500]),ensure_ascii=False))
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
