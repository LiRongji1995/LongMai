"""One local command: capture originals, run unchanged R2, attach themes, export.

This explicit research entry does not alter DAILY_PRIMARY_FAST or SAM V2.
It requires neither the Codex application nor a running HTTP service or LLM.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone, time as wall_time
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid
from zoneinfo import ZoneInfo

from .premarket_runtime_v1 import (Budget, BudgetExceeded, DEFAULT_LIMITS, GIB,
    JobLock, atomic_json, directory_bytes, fingerprint, json_bytes, strict_json, utc_now)

VERSION = 'STANDALONE_PREMARKET_WORKFLOW_V1'
SH = ZoneInfo('Asia/Shanghai')
SEAL_BYTES = 16384
STRATEGY = 'SHORT_HORIZON_STRENGTH_R2'


def _ref(path, read_meter=None):
    path = Path(path).resolve(strict=True)
    initial = fingerprint(path)
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024*1024), b''):
            if read_meter is not None: read_meter(len(block))
            digest.update(block)
    terminal = fingerprint(path)
    if initial != terminal:
        raise ValueError('FILE_CHANGED_DURING_AUTHENTICATION')
    return dict(path=str(path), sha256=digest.hexdigest(), bytes=terminal['st_size'], fp=terminal)


def _verify(reference, read_meter=None):
    if set(reference) != {'path','sha256','bytes','fp'} or _ref(reference['path'],read_meter) != reference:
        raise ValueError('ARCHIVED_REFERENCE_CHANGED')


def _read_control(path, read_meter=None):
    # Control files have their own small bound and never contain model weights.
    if Path(path).stat().st_size > 16*1024*1024:
        raise ValueError('CONTROL_SIZE_LIMIT_EXCEEDED')
    raw = Path(path).read_bytes()
    if read_meter is not None:
        read_meter(len(raw))
    return strict_json(raw)


def _seal(path, value):
    raw = json_bytes(value)
    if len(raw)+1 > SEAL_BYTES:
        raise ValueError('FINAL_CONTROL_SIZE_LIMIT_EXCEEDED')
    raw += b' '*(SEAL_BYTES-len(raw)-1)+b'\n'
    with Path(path).open('r+b') as handle:
        handle.seek(0); handle.write(raw); handle.truncate(SEAL_BYTES)
        handle.flush(); os.fsync(handle.fileno())


def _code_refs(read_meter=None):
    # Whole package closure, not only files already imported by this process.
    package = Path(__file__).resolve().parents[1]
    application = package.parent
    paths = {*package.rglob('*.py'), application/'scripts/stockfish.py',
        application/'premarket', application/'premarket.ps1'}
    return [_ref(path,read_meter) for path in sorted(paths)]


def _latest_gate(calendar_rows, expected_observation, expected_target, now=None):
    """Use original complete natural-day calendar, including exchange holidays."""
    from datetime import date, timedelta
    local = (now or datetime.now(timezone.utc)).astimezone(SH)
    days = {}
    for row in calendar_rows:
        day, flag = date.fromisoformat(row['calendar_date']), row['is_trading_day']
        if flag not in ('0','1') or day in days:
            raise ValueError('INVALID_CURRENT_CALENDAR_ROWS')
        days[day] = flag
    if not days or local.date() not in days:
        raise ValueError('CURRENT_CALENDAR_DOES_NOT_COVER_TODAY')
    natural = {min(days)+timedelta(days=index) for index in range((max(days)-min(days)).days+1)}
    if set(days) != natural:
        raise ValueError('CURRENT_CALENDAR_NATURAL_DAY_GAP')
    sessions = sorted(day for day, flag in days.items() if flag == '1')
    # Conservative provider release gate already used by daily research.
    completed = [day for day in sessions if datetime.combine(day, wall_time(18), SH) <= local]
    if not completed:
        raise ValueError('NO_VERIFIED_COMPLETED_SESSION')
    observation = completed[-1]
    following = [day for day in sessions if day > observation]
    if not following:
        raise ValueError('NEXT_SESSION_NOT_COVERED_BY_ORIGINAL_CALENDAR')
    if str(observation) != str(expected_observation) or str(following[0]) != str(expected_target):
        raise ValueError('LATEST_COMPLETED_SESSION_CHANGED_DURING_EXECUTION')
    return dict(observation_date=str(observation), target_session=str(following[0]),
        checked_at=local.isoformat(), session_release_gate='18:00 Asia/Shanghai',
        original_calendar_used=True)


def default_state_root():
    if os.environ.get('STOCKFISH_PREMARKET_STATE'):
        return Path(os.environ['STOCKFISH_PREMARKET_STATE']).expanduser().absolute()
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA', Path.home()/'AppData'/'Local'))/'StockFish'/'premarket'
    return Path(os.environ.get('XDG_STATE_HOME', Path.home()/'.local'/'state'))/'stockfish'/'premarket'


def load_config(path=None, overrides=None):
    value = _read_control(path) if path else {}
    allowed = {'schema_version','state_root','minimum_amount_cny','limits','capture','news','seed_sources'}
    if set(value)-allowed or value.get('schema_version',VERSION) != VERSION:
        raise ValueError('UNSUPPORTED_PREMARKET_CONFIG')
    config = dict(schema_version=VERSION, state_root=str(default_state_root()),
        minimum_amount_cny=300000000, limits=dict(DEFAULT_LIMITS), capture={}, news={}, seed_sources=[])
    config.update({key: item for key,item in value.items() if key != 'limits'})
    config['limits'].update(value.get('limits',{}))
    for key, item in (overrides or {}).items():
        if item is not None:
            if key == 'limits': config['limits'].update(item)
            elif key == 'capture': config['capture'].update(item)
            else: config[key] = item
    config['state_root'] = str(Path(config['state_root']).expanduser().absolute())
    if type(config['minimum_amount_cny']) is not int or config['minimum_amount_cny'] < 0:
        raise ValueError('NONNEGATIVE_INTEGER_DISPLAY_THRESHOLD_REQUIRED')
    if not isinstance(config['seed_sources'],list) or not all(isinstance(p,str) for p in config['seed_sources']):
        raise ValueError('SEED_SOURCE_PATH_LIST_REQUIRED')
    if not isinstance(config['news'],dict) or not isinstance(config['capture'],dict):
        raise ValueError('NEWS_AND_CAPTURE_CONFIG_OBJECTS_REQUIRED')
    Budget(config['limits'])  # Validate before creating any work or making a request.
    return config


def _completion(job, *, status, phases, budget, **fields):
    value = dict(schema_version=VERSION, status=status, phases=phases,
        completed_at=utc_now(), metrics=budget.metrics, strategy_id=STRATEGY,
        parameter_status='UNVALIDATED', market_edge='NOT_ESTABLISHED',
        DAILY_V1='UNKNOWN_NOT_EXECUTED', business_database_written=False,
        source_capability_serialized=False, **fields)
    atomic_json(job/'worker-completion.json', value)
    return value


def execute_workflow(config, job_dir, *, now=None, adapters=None):
    """Worker implementation. Adapter injection exists only for isolated tests."""
    from .premarket_capture_cache_v1 import CaptureCache, capture_collect, capture_theme_sources
    from .premarket_native_core_v1 import run_core
    from .premarket_theme_context_v1 import attach_theme_context
    from .premarket_presentation_v1 import render_premarket
    adapters = adapters or {}
    Cache = adapters.get('cache',CaptureCache)
    collect = adapters.get('collect',capture_collect)
    theme_sources = adapters.get('theme_sources',capture_theme_sources)
    core_runner = adapters.get('core',run_core)
    theme_runner = adapters.get('theme',attach_theme_context)
    renderer = adapters.get('presentation',render_premarket)
    if adapters and config.get('capture',{}).get('allow_test_fixture') is not True:
        raise ValueError('ADAPTERS_REQUIRE_EXPLICIT_TEST_FIXTURE')
    job, state = Path(job_dir).resolve(), Path(config['state_root']).resolve()
    budget = Budget(config['limits'], job_dir=job, cache_dir=state/'cache')
    phases = {name:'NOT_RUN' for name in ('capture','core','news','theme_sources','theme','presentation')}
    collection, owner, theme, display, result, news = None, None, None, None, None, None
    phase_failures = {}
    core_verified = False
    calendar_rows = []
    try:
        with budget.phase('capture'), budget.meter_files():
            phases['capture'] = 'RUNNING'
            cache = Cache(state/'cache',budget,config=config.get('capture',{}))
            for seed in config['seed_sources']:
                cache.import_seed(seed)
            collection = collect(cache,now=now or datetime.now(timezone.utc), config=config.get('capture',{}))
            if adapters and collection.coverage_type != 'TEST_FIXTURE':
                raise ValueError('ADAPTER_COLLECTION_MUST_BE_TEST_FIXTURE')
            if not collection.ready_for_core:
                raise ValueError('CORE_SOURCE_COVERAGE_INCOMPLETE_SEE_CAPTURE_FAILURES')
            calendar_rows = collection.calendar_rows
            gate = _latest_gate(calendar_rows, collection.observation_date, collection.target_session, now)
            phases['capture'] = 'COMPLETE' if collection.completeness == 'COMPLETE' else 'PARTIAL'
            atomic_json(job/'capture-summary.json', collection.to_dict())
        cutoff = datetime.now(timezone.utc).isoformat()
        with budget.phase('core'), budget.meter_files():
            phases['core'] = 'RUNNING'
            owner = core_runner(captures=[cache.metadata(item) for item in collection.core_records],
                observation_date=collection.observation_date, target_session=collection.target_session,
                cutoff=cutoff, output_dir=job/'core', budget=budget,
                coverage_type=collection.coverage_type)
            owner.validate()
            core_verified = True
            phases['core'] = 'COMPLETE'
        # Independent news capture never upgrades an article mention into a
        # direction or EVENT capability. Offline is inherited, not overridden.
        try:
            news_collector = adapters.get('news')
            if news_collector is None:
                from .premarket_news_v1 import collect_reported_news
                news_collector = collect_reported_news
            with budget.phase('capture'), budget.meter_files():
                phases['news'] = 'RUNNING'
                news_config = dict(config.get('news', {}))
                news_config['offline'] = config.get('capture', {}).get('offline', False)
                news = news_collector(core_run=owner.run, output_dir=job/'news',
                    budget=budget, config=news_config)
                summary = news['summary']
                if 'acquisition_complete' in summary:
                    acquired = summary['acquisition_complete']
                    if acquired is not None and type(acquired) is not bool:
                        raise ValueError('NEWS_ACQUISITION_COMPLETE_TRI_STATE_REQUIRED')
                    phases['news'] = 'COMPLETE' if acquired is True else 'PARTIAL' if acquired is False else 'UNKNOWN'
                else:
                    phases['news'] = ('COMPLETE' if summary.get('status') in
                        ('COMPLETE', 'AVAILABLE') else 'UNKNOWN')
        except BudgetExceeded:
            raise
        except Exception as error:
            phases['news'] = 'FAILED'
            phase_failures['news'] = dict(type=type(error).__name__, reason=str(error)[:2000])
        # Selection affects only which genuine whole membership nodes to read.
        # Its full original N is used; the 3E display filter is applied last.
        try:
            with budget.phase('capture'), budget.meter_files():
                phases['theme_sources'] = 'RUNNING'
                sources = theme_sources(cache,core_run=owner.run,collection=collection,
                    config=config.get('capture',{}))
                atomic_json(job/'theme-source-summary.json',sources.to_dict())
                phases['theme_sources'] = ('COMPLETE' if sources.parent_acquisition.get('status') ==
                    'COMPLETE_CAPTURE_NOT_THEME_READY' else 'PARTIAL')
            with budget.phase('theme'), budget.meter_files():
                phases['theme'] = 'RUNNING'
                result = theme_runner(owner,context_captures=sources.context_captures,
                    raw_receipt_sources=sources.raw_receipt_sources,
                    context_cutoff=datetime.now(timezone.utc).isoformat(),output_dir=job/'theme',budget=budget,
                    requested_industry_nodes=sources.requested_industry_nodes,
                    requested_concept_symbols=sources.requested_concept_symbols,
                    parent_acquisition=sources.parent_acquisition)
                theme = result.to_dict()
                phases['theme'] = 'COMPLETE' if theme['status'] == 'AVAILABLE' else theme['status']
                result.validate()
        except BudgetExceeded:
            raise
        except Exception as error:
            running = 'theme_sources' if phases['theme_sources'] == 'RUNNING' else 'theme'
            phases[running] = 'FAILED'
            phase_failures[running] = dict(type=type(error).__name__, reason=str(error)[:2000])
            theme = None
        with budget.phase('presentation'), budget.meter_files():
            phases['presentation'] = 'RUNNING'
            gate = _latest_gate(calendar_rows,collection.observation_date,collection.target_session,now)
            artifacts = owner.artifacts
            provenance = dict(canonical_run_ref=artifacts['canonical-run.json.gz'],
                original_exports={key:artifacts[name] for key,name in dict(all='all.csv',
                    observation='observation.csv',focus='focus.csv',unselected='unselected.csv',
                    observation_json='observation.json').items()},
                source_role_summary=collection.source_role_summary,
                calendar_currentness=gate,DAILY_V1='UNKNOWN_NOT_EXECUTED',
                news_metadata_ref=None if news is None else news['metadata'],
                news_summary=None if news is None else news['summary'])
            display = renderer(owner.run,theme,job/'display',
                minimum_amount_cny=config['minimum_amount_cny'],provenance=provenance)
            phases['presentation'] = 'COMPLETE'
        # Terminal validation has the original phase allocation and remains
        # cumulative. It cannot borrow the presentation budget or mint a parent.
        with budget.phase('theme' if result is not None else 'core'), budget.meter_files():
            if result is not None:
                result.validate()
            else:
                owner.validate()
            if hasattr(cache, 'terminal'):
                cache.terminal()
        gate = _latest_gate(calendar_rows,collection.observation_date,collection.target_session,now)
        validation_refs = {}
        for reference in [*owner.validation_references,
                *(result.validation_references if result is not None else []),
                *(news.get('validation_references', []) if news is not None else [])]:
            prior = validation_refs.setdefault(reference['path'], reference)
            if prior != reference:
                raise ValueError('TERMINAL_SOURCE_REFERENCE_CONFLICT')
        complete = all(value == 'COMPLETE' for value in phases.values())
        return _completion(job,status='COMPLETE' if complete else 'COMPLETED_WITH_UNKNOWNS',
            phases=phases,budget=budget,core_verified=core_verified,run_id=owner.run['run_id'],
            observation_date=str(collection.observation_date),target_session=str(collection.target_session),
            calendar_rows=calendar_rows,calendar_currentness=gate,display=display,
            theme_artifacts={} if result is None else result.artifacts, news=news,
            phase_failures=phase_failures, validation_references=list(validation_refs.values()),
            core_artifacts=owner.artifacts,theme_status='UNKNOWN' if theme is None else theme['status'])
    except Exception as error:
        running = next((key for key,value in phases.items() if value == 'RUNNING'),None)
        if running: phases[running] = 'FAILED'
        # Sticky exhausted budgets never authorize extra reads or a new source
        # issuer. Previously sealed core files remain available for inspection.
        return _completion(job,status='BUDGET_EXCEEDED' if isinstance(error,BudgetExceeded) else 'FAILED',
            phases=phases,budget=budget,core_verified=core_verified,
            error=dict(type=type(error).__name__,reason=str(error)[:2000]),
            calendar_rows=calendar_rows,display=display,
            core_artifacts=owner.artifacts if owner is not None and core_verified else None,
            theme_confirmation='UNKNOWN' if phases['theme'] != 'COMPLETE' else 'SEE_ORIGINAL_CONTEXT')


def _tree_sample(pid, read_meter=None):
    """Actual resident bytes of this worker and its live descendants."""
    if os.name == 'posix':
        result = subprocess.run(['ps','-axo','pid=,ppid=,rss='],check=True,
            stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=5)
        if read_meter is not None:
            read_meter(len(result.stdout.encode('utf-8')))
        rows = [tuple(map(int,line.split())) for line in result.stdout.splitlines() if line.strip()]
        descendants = {pid}
        while True:
            expanded = descendants | {child for child,parent,_ in rows if parent in descendants}
            if expanded == descendants: break
            descendants = expanded
        if not any(child == pid and rss > 0 for child,_,rss in rows):
            raise RuntimeError('ACTIVE_WORKER_RSS_UNAVAILABLE')
        return sum(rss*1024 for child,_,rss in rows if child in descendants)
    try:
        import psutil
        parent = psutil.Process(pid)
        processes = [parent]+parent.children(recursive=True)
        return sum(process.memory_info().rss for process in processes if process.is_running())
    except ImportError:
        raise RuntimeError('WINDOWS_TREE_RSS_REQUIRES_PROJECT_REQUIREMENTS_REVIEW_PSUTIL') from None


def _owned_group_members(pid, read_meter=None):
    if os.name != 'posix':
        return []
    result = subprocess.run(['ps','-axo','pid=,pgid=,stat='],check=True,
        stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=5)
    if read_meter is not None:
        read_meter(len(result.stdout.encode('utf-8')))
    return [int(parts[0]) for line in result.stdout.splitlines()
        if len(parts := line.split()) >= 3 and int(parts[1]) == pid
        and not parts[2].startswith('Z')]


def _stop_owned(process):
    if process.poll() is not None and os.name != 'posix': return
    if os.name == 'posix':
        try: os.killpg(process.pid,signal.SIGTERM)
        except ProcessLookupError: pass
    else:
        import psutil
        for child in psutil.Process(process.pid).children(recursive=True): child.terminate()
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == 'posix': os.killpg(process.pid,signal.SIGKILL)
        else:
            import psutil
            for child in psutil.Process(process.pid).children(recursive=True): child.kill()
            process.kill()
        process.wait(timeout=5)
    if os.name == 'posix':
        # The worker owns this new session. A worker that exits first must not
        # leave an SDK descendant writing into the cache after the lock drops.
        try: os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError: pass


def supervise(config, *, progress=True, worker_command=None):
    state = Path(config['state_root']).resolve()
    state.mkdir(parents=True,exist_ok=True)
    with JobLock(state/'premarket.lock'):
        job = state/'jobs'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:12])
        job.mkdir(parents=True,exist_ok=False)
        with (job/'final-control.json').open('xb') as handle: handle.write(b' '*SEAL_BYTES)
        _seal(job/'final-control.json',dict(schema_version=VERSION,status='UNSEALED',created_at=utc_now()))
        started, peak, violation = time.monotonic(), 0, None
        supervisor_read = 0
        def charge(count):
            nonlocal supervisor_read
            supervisor_read += count
            if supervisor_read > config['limits']['supervisor_read_bytes']:
                raise BudgetExceeded('SUPERVISOR_READ_LIMIT_EXCEEDED')
        command = worker_command or [sys.executable,'-B','-m',__spec__.name,
            '_worker','--job',str(job)]
        environment = os.environ.copy()
        package_root = str(Path(__file__).resolve().parents[2])
        environment['PYTHONPATH'] = package_root+(os.pathsep+environment['PYTHONPATH']
            if environment.get('PYTHONPATH') else '')
        process = None
        try:
            code = _code_refs(charge)
            atomic_json(job/'configuration.json',config)
            atomic_json(job/'code-registry.json',code)
            configuration_ref = _ref(job/'configuration.json',charge)
            code_registry_ref = _ref(job/'code-registry.json',charge)
            with (job/'worker-console.txt').open('xb') as log:
                process = subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,
                    env=environment,cwd=package_root,start_new_session=os.name=='posix')
                last = 0
                while process.poll() is None:
                    try: peak = max(peak,_tree_sample(process.pid,charge))
                    except Exception:
                        if process.poll() is None: raise
                    elapsed = time.monotonic()-started
                    if peak > config['limits']['tree_rss_bytes']: violation='TREE_RSS_LIMIT_EXCEEDED'
                    if config['limits']['max_seconds'] is not None and elapsed > config['limits']['max_seconds']:
                        violation='TOTAL_TIME_LIMIT_EXCEEDED'
                    if directory_bytes(job) > config['limits']['output_bytes']: violation='OUTPUT_LIMIT_EXCEEDED'
                    if (job/'worker-console.txt').stat().st_size > 524288: violation='CONSOLE_LIMIT_EXCEEDED'
                    if violation:
                        _stop_owned(process); break
                    if progress and elapsed-last >= 10:
                        phase = _read_control(job/'metrics.json',charge).get('phase','starting') if (job/'metrics.json').exists() else 'starting'
                        print(f'[{phase}] 已运行 {elapsed:.0f}秒；任务 {job.name}',flush=True)
                        last = elapsed
                    time.sleep(.5)
                actual_exit = process.wait()
            if violation: raise BudgetExceeded(violation)
            if _owned_group_members(process.pid,charge):
                raise RuntimeError('OWNED_DESCENDANTS_REMAIN_AFTER_WORKER_EXIT')
            if actual_exit not in (0,2,3,4): raise RuntimeError('WORKER_EXTERNAL_EXIT_'+str(actual_exit))
            completion = _read_control(job/'worker-completion.json',charge)
            expected_exit = dict(COMPLETE=0,COMPLETED_WITH_UNKNOWNS=2,FAILED=3,BUDGET_EXCEEDED=4)[completion['status']]
            if actual_exit != expected_exit: raise RuntimeError('WORKER_STATUS_EXIT_MISMATCH')
            if completion.get('schema_version') != VERSION:
                raise RuntimeError('WORKER_COMPLETION_SCHEMA_MISMATCH')
            if actual_exit in (0,2) and (completion.get('core_verified') is not True or not completion.get('display')):
                raise RuntimeError('SUCCESS_REQUIRES_VERIFIED_CORE_AND_DISPLAY')
            if completion.get('display'):
                required = {'canonical-run.json.gz','input.json.gz','observation.json',
                    'observation.csv','all.csv','focus.csv','unselected.csv','manifest.json'}
                if set(completion.get('core_artifacts') or {}) != required or not completion.get('validation_references'):
                    raise RuntimeError('SUCCESS_REQUIRES_FULL_CORE_AND_ORIGINAL_REFERENCE_BINDINGS')
                if not isinstance(completion['display'].get('artifacts'),dict) or len(completion['display']['artifacts']) != 3:
                    raise RuntimeError('SUCCESS_REQUIRES_THREE_DISPLAY_ARTIFACTS')
                for reference in code: _verify(reference,charge)
                for reference in completion['display']['artifacts'].values(): _verify(reference,charge)
                for reference in completion['core_artifacts'].values(): _verify(reference,charge)
                for reference in completion.get('theme_artifacts',{}).values(): _verify(reference,charge)
                _latest_gate(completion['calendar_rows'],completion['observation_date'],completion['target_session'])
            unique_sources = {}
            for reference in completion.get('validation_references',[]):
                previous = unique_sources.setdefault(reference['path'],reference)
                if previous != reference: raise RuntimeError('WORKER_SOURCE_REFERENCE_CONFLICT')
            for reference in unique_sources.values(): _verify(reference,charge)
            if completion.get('news') is not None:
                _verify(completion['news']['metadata'],charge)
            for reference in code: _verify(reference,charge)
            _verify(configuration_ref,charge)
            _verify(code_registry_ref,charge)
            completion_ref = _ref(job/'worker-completion.json',charge)
            result = dict(schema_version=VERSION,status=completion['status'],
                external_worker_exit_code=actual_exit,external_worker_reaped=True,
                peak_tree_rss_bytes=peak,elapsed_seconds=round(time.monotonic()-started,3),
                supervisor_returned_read_bytes=supervisor_read,
                implementation_epoch_verified=True,
                source_reference_count=len(unique_sources),
                source_terminal_binding_verified=True if unique_sources else None,
                supervisor_accounting_scope='EXPLICIT_CODE_SOURCE_ARTIFACT_INPUT_CONTROL_READS_AND_PS_UTF8_OUTPUT; FINAL_SELF_REFERENCE_EXCLUDED; NOT_ALL_KERNEL_IO',
                configuration=configuration_ref,code_registry=code_registry_ref,
                completion=completion_ref,
                display=completion.get('display'),core_verified=completion.get('core_verified',False),
                final_sealed_at=utc_now(),job=str(job),source_capability_restored=False)
            _seal(job/'final-control.json',result)
            # A final independent clock check prevents a cross-close stale pass.
            if completion.get('display'):
                _latest_gate(completion['calendar_rows'],completion['observation_date'],completion['target_session'])
            for reference in code: _verify(reference,charge)
            _verify(configuration_ref,charge)
            _verify(code_registry_ref,charge)
            # Full SHA reads above and immutable seven-field identities here
            # surround the seal without reading a large journal a second time.
            terminal = [*unique_sources.values(), completion_ref]
            terminal += list((completion.get('core_artifacts') or {}).values())
            terminal += list((completion.get('theme_artifacts') or {}).values())
            terminal += list((completion.get('display') or {}).get('artifacts',{}).values())
            if completion.get('news') is not None:
                terminal.append(completion['news']['metadata'])
            for reference in terminal:
                if (str(Path(reference['path']).resolve(strict=True)) != reference['path']
                        or fingerprint(reference['path']) != reference['fp']):
                    raise ValueError('FINAL_SOURCE_OR_ARTIFACT_PHYSICAL_IDENTITY_CHANGED')
            result['supervisor_returned_read_bytes'] = supervisor_read
            _seal(job/'final-control.json',result)
            atomic_json(state/'latest-job.json',dict(job=str(job),final=_ref(job/'final-control.json')))
            return actual_exit,result
        except BaseException as error:
            cleanup_error = None
            if process is not None:
                try: _stop_owned(process)
                except Exception as cleanup:
                    cleanup_error = dict(type=type(cleanup).__name__,reason=str(cleanup)[:1000])
            result = dict(schema_version=VERSION,status='BUDGET_EXCEEDED' if isinstance(error,BudgetExceeded) else 'FAILED',error=dict(type=type(error).__name__,
                reason=str(error)[:1500]),job=str(job),external_worker_exit_code=process.returncode if process else None,
                external_worker_reaped=process is None or process.poll() is not None,
                peak_tree_rss_bytes=peak,final_sealed_at=utc_now(),display=None,cleanup_error=cleanup_error,
                supervisor_returned_read_bytes=supervisor_read)
            _seal(job/'final-control.json',result)
            if isinstance(error,KeyboardInterrupt): return 130,result
            return (4 if isinstance(error,BudgetExceeded) else 3),result



def verify_job(path):
    job = Path(path).expanduser().absolute()
    final = _read_control(job/'final-control.json')
    if final['schema_version'] != VERSION or final['status'] == 'UNSEALED':
        raise ValueError('JOB_NOT_TERMINALLY_SEALED')
    completion = None
    if final.get('completion'):
        _verify(final['completion'])
        completion = _read_control(final['completion']['path'])
        for role in ('core_artifacts','theme_artifacts'):
            for reference in (completion.get(role) or {}).values(): _verify(reference)
        if completion.get('news') is not None: _verify(completion['news']['metadata'])
    for role in ('configuration','code_registry'):
        if final.get(role): _verify(final[role])
    if final.get('display'):
        for reference in final['display']['artifacts'].values(): _verify(reference)
    return dict(schema_version=VERSION,status=final['status'],job=str(job),
        archived_bytes_verified=True,currentness_recomputed=False,
        source_authority_restored=False,display=final.get('display'))


def parser():
    result = argparse.ArgumentParser(description='独立本地R2盘前研究；不需要Codex或Web服务')
    commands = result.add_subparsers(dest='command',required=True)
    run = commands.add_parser('premarket',help='一次执行采集、R2、67日题材与3亿元展示')
    run.add_argument('--config',help='私有JSON配置；不读取应用LLM或API凭据')
    run.add_argument('--state-root',help='私有缓存与产物目录')
    run.add_argument('--seed-sources',action='append',help='导入既有原件清单，可多次指定')
    run.add_argument('--offline',action='store_true',default=None,help='只用已认证缓存，缺资料明确失败')
    run.add_argument('--retry-failed',action='store_true',default=None,help='显式允许失败逻辑请求本次新增一次attempt')
    run.add_argument('--theme-read-gib',type=int,help='本次题材实际返回读取上限，默认4；全67日可能需12')
    run.add_argument('--max-seconds',type=int,help='总秒数；0显式表示不设总时间上限；单接口仍最多90秒')
    run.add_argument('--max-requests',type=int,help='本次新逻辑请求上限；不自动扩大')
    run.add_argument('--minimum-amount-cny',type=int,help='仅展示筛选，默认300000000')
    run.add_argument('--quiet',action='store_true',help='只打印终态JSON')
    status = commands.add_parser('premarket-status',help='读取任务终态；不授予来源能力')
    status.add_argument('job',help='原任务目录')
    verify = commands.add_parser('premarket-verify',help='核验归档展示原字节；不伪装最新结果')
    verify.add_argument('job')
    worker = commands.add_parser('_worker',help=argparse.SUPPRESS)
    worker.add_argument('--job',required=True)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == '_worker':
            job=Path(args.job)
            value=execute_workflow(_read_control(job/'configuration.json'),job)
            return dict(COMPLETE=0,COMPLETED_WITH_UNKNOWNS=2,FAILED=3,BUDGET_EXCEEDED=4)[value['status']]
        if args.command == 'premarket-status':
            print(json.dumps(_read_control(Path(args.job)/'final-control.json'),ensure_ascii=False))
            return 0
        if args.command == 'premarket-verify':
            print(json.dumps(verify_job(args.job),ensure_ascii=False)); return 0
        limits = {}
        if args.theme_read_gib is not None: limits['theme_read_bytes']=args.theme_read_gib*GIB
        if args.max_seconds is not None:
            if args.max_seconds < 0: raise ValueError('NONNEGATIVE_TOTAL_SECONDS_REQUIRED')
            limits['max_seconds']=args.max_seconds or None
        if args.max_requests is not None: limits['max_requests']=args.max_requests
        capture = {key:getattr(args,key) for key in ('offline','retry_failed') if getattr(args,key) is not None}
        config=load_config(args.config,dict(state_root=args.state_root,seed_sources=args.seed_sources,
            minimum_amount_cny=args.minimum_amount_cny,limits=limits,capture=capture))
        code,value=supervise(config,progress=not args.quiet)
        print(json.dumps(value,ensure_ascii=False)); return code
    except Exception as error:
        print(json.dumps(dict(status='FAILED_BEFORE_EXECUTION',error=type(error).__name__,
            reason=str(error)[:1500]),ensure_ascii=False),file=sys.stderr)
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
