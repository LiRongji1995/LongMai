"""TEST_FIXTURE current calendar; immutable real archive grammar, no network."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import codex_current_calendar_v1 as calendar
from scripts import codex_runtime_recovery_v1 as recovery
from stockfish_core.yaogu import premarket_capture_cache_v1 as native
from stockfish_core.yaogu import provider_requests, providers
from stockfish_core.yaogu.premarket_runtime_v1 import Budget


class FixtureRuntime(recovery.RecoveryRuntime):
    def __init__(self, root):
        self.profile=dict(name='TEST_FIXTURE_calendar',backend='premarket',source_root=str(calendar.ROOT),
            python=sys.executable,config=str(root/'configuration.json'),state_root=str(root/'state'),
            storage=dict(expected_growth_bytes=1024))
        self.recovery_plan={'test_fixture':False}
        self.directory=root/'scope'
        self.directory.mkdir()
        self.registry_root=root/'registry'
        self.scope='TEST_FIXTURE_calendar_scope'
        self.locks=0

    def check_binding(self): pass

    @contextmanager
    def exclusive(self):
        self.locks+=1
        try: yield
        finally: self.locks-=1

    def preflight(self,**_kwargs): return dict(allowed=True)


@pytest.fixture
def fixture(tmp_path,monkeypatch):
    root=tmp_path/'TEST_FIXTURE_current_calendar'
    root.mkdir()
    runtime=FixtureRuntime(root)
    config=dict(schema_version='STANDALONE_PREMARKET_WORKFLOW_V1',state_root=runtime.profile['state_root'],
        limits=dict(calendar.bounded.CAPS),capture=dict(retry_failed=False,offline=False),seed_sources=[])
    Path(runtime.profile['config']).write_text(json.dumps(config))
    cache_root=Path(runtime.profile['state_root'])/'cache'
    for name in ('index','raw','raw-index','locators'):
        (cache_root/name).mkdir(parents=True)
    controls={'calls':0,'failure':False,'supervisor_failed':False,'wrong_context':False}
    snapshots=[calendar.files.read(Path(runtime.profile['config']))[1],calendar.files.read(Path(calendar.__file__))[1]]
    monkeypatch.setattr(calendar,'_sources',lambda _r:dict(references=snapshots,prepared={'TEST_FIXTURE':True}))
    monkeypatch.setattr(calendar.daily,'_plan',lambda _r:dict(status='BLOCKED',reasons=['TEST_FIXTURE_OLD_DIAGNOSTIC']))
    monkeypatch.setattr(calendar.recovery,'load_runtime',lambda *_a,**_k:runtime)

    def denied(*_args,**_kwargs):
        raise AssertionError('TEST_FIXTURE_NETWORK_FORBIDDEN')
    monkeypatch.setattr(socket.socket,'connect',denied)
    monkeypatch.setattr(socket.socket,'connect_ex',denied)
    monkeypatch.setattr(socket,'create_connection',denied)

    def executor(endpoint,params,output,**_kwargs):
        controls['calls']+=1
        assert runtime.locks==1
        start=datetime.now(timezone.utc).isoformat()
        first=date.fromisoformat(params['start_date']);last=date.fromisoformat(params['end_date'])
        rows=[dict(calendar_date=str(first+timedelta(days=n)),
            is_trading_day='1' if (first+timedelta(days=n)).weekday()<5 else '0')
            for n in range((last-first).days+1)]
        if controls['failure']: rows=rows[:-1]
        seen=datetime.now(timezone.utc).isoformat()
        payload=dict(rows=rows,status='COMPLETE',pagination_complete=True,gaps=[],coverage={},
            first_seen_at=seen,field_version='TEST_FIXTURE_CURRENT_CALENDAR')
        receipt=providers.archive_receipt(Path(output)/'raw','baostock',endpoint,params,payload,
            status='COMPLETE',pagination_complete=True,field_version=payload['field_version'],first_seen_at=seen)
        result=dict(endpoint=endpoint,provider='baostock',parameters=params,**payload,
            receipts=[receipt],request_receipt_id=receipt['receipt_id'],started_at=start,
            completed_at=datetime.now(timezone.utc).isoformat(),diagnostics={},
            implementation_source_hashes={name:hashlib.sha256(Path(provider_requests.__file__).with_name(name).read_bytes()).hexdigest()
                for name in native.PRODUCERS})
        (Path(output)/'request-TEST_FIXTURE.json').write_text(json.dumps(result))
        return result

    original_cache=calendar._cache
    def fixture_cache(runtime,budget,*,readonly):
        if readonly:return original_cache(runtime,budget,readonly=True)
        return native.CaptureCache(cache_root,budget,dict(offline=False,retry_failed=False,
            request_timeout_seconds=min(90,budget.limits['request_timeout_seconds'])),executor=executor)
    monkeypatch.setattr(calendar,'_cache',fixture_cache)
    supervisor=SimpleNamespace(__file__=__file__)
    def validate(directory):
        if controls['wrong_context']:raise ValueError('TEST_FIXTURE_NO_INHERITED_PIPE')
        claim=json.loads((Path(directory).parent/'claim.json').read_bytes())
        return dict(test_fixture=True,worker_context_returned_read_bytes=0,limits=claim['supervisor_limits'])
    supervisor.validate_worker_context=validate
    def supervised(command,**kwargs):
        assert kwargs['accepted_exit_codes']==(0,) and runtime.locks==1
        parsed={command[i]:command[i+1] for i in range(command.index('--plan'),len(command),2)}
        with monkeypatch.context() as patch:
            patch.setattr(calendar.os,'getppid',lambda:os.getpid())
            patch.setattr(calendar.sys,'pycache_prefix',str(Path(parsed['--plan']).parent/'absent-bytecode'))
            try:
                calendar._worker(parsed['--plan'],parsed['--plan-sha256'],parsed['--claim'],
                    parsed['--claim-sha256'],parsed['--supervisor-control'])
                code=0
            except Exception:
                code=3
        return dict(status='FAILED' if controls['supervisor_failed'] or code else 'COMPLETE',
            child_exit_code=code,root_reaped=True,all_observed_owned_exited=True,
            all_observed_descendants_gone=True,terminal_supervisor_returned_read_bytes=0,
            elapsed_seconds=0,test_fixture=True)
    supervisor.run_supervised=supervised
    monkeypatch.setitem(sys.modules,'scripts.codex_sdk_tree_supervisor_v1',supervisor)
    import scripts
    monkeypatch.setattr(scripts,'codex_sdk_tree_supervisor_v1',supervisor,raising=False)
    return SimpleNamespace(runtime=runtime,controls=controls,root=root,cache_root=cache_root,executor=executor)


def test_prepare_calls_original_plan_and_does_not_write_or_collect(fixture):
    before=calendar.inventory._inventory(fixture.root)
    plan=calendar.prepare(fixture.runtime)
    assert plan['action']=='NEW_CALENDAR_ONCE'
    assert plan['planned_new_calls']==dict(requests=1,sdk_reserved=3,http_reserved=0)
    assert plan['original_daily_plan']['original_result']['reasons']==['TEST_FIXTURE_OLD_DIAGNOSTIC']
    day=date.fromisoformat(plan['local_day'])
    assert plan['request_spec']['parameters']==dict(start_date=str(day-timedelta(days=800)),end_date=str(day+timedelta(days=32)))
    assert calendar.inventory._inventory(fixture.root)==before and fixture.controls['calls']==0


def test_supervised_capture_native_archive_and_budget_receipt(fixture):
    plan=calendar.prepare(fixture.runtime)
    result=calendar.execute(fixture.runtime,plan)
    assert result['status']=='COMPLETE_CURRENT_CALENDAR_SOURCE' and fixture.controls['calls']==1
    assert result['worker_accounting']['metrics']['calls']['requests']==1
    assert result['worker_accounting']['metrics']['calls']['sdk_reserved']==3
    assert result['parent_terminal_accounting']['metrics']['returned_read_bytes']['capture']>0
    assert result['output_bytes']>0 and result['complete_daily_delivery'] is False
    assert result['cumulative_accounting']['calls']['sdk_reserved']==3
    assert result['cumulative_accounting']['calls']['sdk_actual'] is None
    assert result['cumulative_accounting']['returned_read_bytes']['capture'] >= result['worker_accounting']['metrics']['returned_read_bytes']['capture']
    assert result['source_capability_issued'] is False
    assert result['calendar_gate']['original_calendar_used'] is True
    with pytest.raises(ValueError,match='EXISTING_DAY_CLAIM_NO_RETRY'):
        calendar.execute(fixture.runtime,plan)
    assert fixture.controls['calls']==1


def test_outer_failed_cannot_upgrade_inner_success(fixture):
    plan=calendar.prepare(fixture.runtime)
    fixture.controls['supervisor_failed']=True
    with pytest.raises(ValueError,match='OUTER_SUPERVISION_FAILED'):
        calendar.execute(fixture.runtime,plan)
    slot=Path(plan['slot'])
    assert (slot/'worker/result.json').exists() and (slot/'failure.json').exists()
    assert not (slot/'completion.json').exists()
    with pytest.raises(ValueError,match='EXISTING_DAY_CLAIM_NO_RETRY'):
        calendar.prepare(fixture.runtime)


def test_truncated_provider_calendar_fails_and_attempt_is_retained(fixture):
    fixture.controls['failure']=True
    plan=calendar.prepare(fixture.runtime)
    with pytest.raises(ValueError,match='OUTER_SUPERVISION_FAILED'):
        calendar.execute(fixture.runtime,plan)
    assert fixture.controls['calls']==1 and (Path(plan['slot'])/'failure.json').exists()
    assert (Path(plan['slot'])/'worker/failure.json').exists()
    assert len(list((fixture.cache_root/'index'/plan['request_key']).glob('*.json')))==1


def test_missing_owned_context_never_reaches_sdk(fixture):
    fixture.controls['wrong_context']=True
    plan=calendar.prepare(fixture.runtime)
    with pytest.raises(ValueError,match='OUTER_SUPERVISION_FAILED'):
        calendar.execute(fixture.runtime,plan)
    assert fixture.controls['calls']==0


def test_caller_limits_cannot_expand_registered_budget(fixture):
    limits=dict(calendar.bounded.CAPS,max_seconds=10801)
    with pytest.raises(ValueError,match='LIMIT_MUST_NOT_INCREASE:max_seconds'):
        calendar.prepare(fixture.runtime,limits=limits)


def test_mutated_plan_or_old_source_is_rejected_before_claim(fixture):
    plan=calendar.prepare(fixture.runtime)
    changed=deepcopy(plan);changed['request_spec']['parameters']['end_date']='2099-01-01'
    with pytest.raises(ValueError,match='PLAN_HASH_CHANGED'):
        calendar.execute(fixture.runtime,changed)
    p=fixture.cache_root/'index/TEST_FIXTURE_original.json'
    p.write_text('{}')
    plan=calendar.prepare(fixture.runtime)
    p.write_text('{"changed":true}')
    with pytest.raises(ValueError,match='ORIGINAL_CACHE_CHANGED'):
        calendar.execute(fixture.runtime,plan)
    assert fixture.controls['calls']==0


def test_exact_unindexed_attempt_blocks_new_bootstrap(fixture):
    plan=calendar.prepare(fixture.runtime)
    attempt=fixture.cache_root/'raw'/plan['request_key']/'raw/TEST_FIXTURE_failed'
    attempt.mkdir(parents=True)
    with pytest.raises(ValueError,match='UNFINISHED_CAPTURE_NO_IMPLICIT_RETRY'):
        calendar.prepare(fixture.runtime)


def test_diagnostic_failure_is_preserved_and_read_charged(fixture,monkeypatch):
    p=fixture.root/'TEST_FIXTURE_diagnostic.txt';p.write_text('original diagnostic')
    def old_plan(_runtime):
        assert p.read_text()=='original diagnostic'
        raise ValueError('PREFLIGHT_READONLY_CACHE_LIMIT_EXCEEDED')
    monkeypatch.setattr(calendar.daily,'_plan',old_plan)
    plan=calendar.prepare(fixture.runtime)
    assert plan['original_daily_plan']['reason']=='PREFLIGHT_READONLY_CACHE_LIMIT_EXCEEDED'
    assert plan['planning']['metrics']['returned_read_bytes']['capture']>=len(p.read_bytes())


def test_existing_complete_calendar_reuses_native_record_and_clocks(fixture):
    plan=calendar.prepare(fixture.runtime)
    with fixture.runtime.exclusive():
        cache=native.CaptureCache(fixture.cache_root,Budget(calendar.bounded.CAPS),executor=fixture.executor)
        original=cache.capture_one(native.RequestSpec(**plan['request_spec'])).to_dict()
    plan=calendar.prepare(fixture.runtime)
    assert plan['action']=='REUSE_COMPLETE' and plan['original_record']==original
    result=calendar.execute(fixture.runtime,plan)
    assert result['record']==original and fixture.controls['calls']==1
    assert result['cumulative_accounting']['calls']['requests']==0


def test_cumulative_read_and_outer_wall_exhaustion_are_sticky(fixture):
    plan=calendar.prepare(fixture.runtime)
    empty=Budget(plan['limits']).metrics
    part=dict(metrics=empty,actual_elapsed_seconds=0)
    report=dict(terminal_supervisor_returned_read_bytes=plan['limits']['supervisor_read_bytes'])
    with pytest.raises(ValueError,match='CUMULATIVE_READ_LIMIT'):
        calendar._cumulative(plan,part,part,report,0)
    part=dict(metrics=empty,actual_elapsed_seconds=plan['limits']['max_seconds'])
    with pytest.raises(ValueError,match='CUMULATIVE_TIME_OR_OUTPUT_LIMIT'):
        calendar._cumulative(plan,part,part,dict(terminal_supervisor_returned_read_bytes=0),0)


def test_post_publication_source_failure_revokes_only_own_completion(fixture,monkeypatch):
    plan=calendar.prepare(fixture.runtime)
    previous=calendar._same_sources
    def changed(runtime,expected):
        if (Path(plan['slot'])/'completion.json').exists():
            raise ValueError('TEST_FIXTURE_TERMINAL_SOURCE_CHANGED')
        return previous(runtime,expected)
    monkeypatch.setattr(calendar,'_same_sources',changed)
    with pytest.raises(ValueError,match='TEST_FIXTURE_TERMINAL_SOURCE_CHANGED'):
        calendar.execute(fixture.runtime,plan)
    assert (Path(plan['slot'])/'rejected-completion.json').exists()
    assert not (Path(plan['slot'])/'completion.json').exists()
    assert (Path(plan['slot'])/'failure.json').exists()


def test_execute_accepts_only_lower_outer_remaining_limits(fixture):
    plan=calendar.prepare(fixture.runtime)
    remaining=dict(calendar.bounded.CAPS,max_seconds=120,max_requests=1,max_sdk_calls=3)
    result=calendar.execute(fixture.runtime,plan,limits=remaining)
    claim=json.loads(Path(result['claim']['path']).read_bytes())
    assert claim['worker_limits']['max_seconds']<=120
    assert claim['worker_limits']['max_requests']==1 and claim['worker_limits']['max_sdk_calls']==3
    assert claim['worker_limits']['supervisor_read_bytes']+claim['supervisor_limits']['supervisor_read_bytes'] <= plan['limits']['supervisor_read_bytes']


def test_foreign_completion_replacement_is_preserved_and_failure_is_still_written(fixture,monkeypatch):
    plan=calendar.prepare(fixture.runtime)
    previous=calendar._same_sources
    foreign=b'{"TEST_FIXTURE":"foreign replacement"}'
    def changed(runtime,expected):
        path=Path(plan['slot'])/'completion.json'
        if path.exists():
            path.write_bytes(foreign)
        return previous(runtime,expected)
    monkeypatch.setattr(calendar,'_same_sources',changed)
    with pytest.raises(ValueError,match='CONTROL_REFERENCE_CHANGED'):
        calendar.execute(fixture.runtime,plan)
    assert (Path(plan['slot'])/'completion.json').read_bytes()==foreign
    failure=json.loads((Path(plan['slot'])/'failure.json').read_bytes())
    assert failure['status']=='FAILED_OR_UNKNOWN_RETAINED_NO_RETRY'
    assert failure['completion_revocation'].startswith('FOREIGN_OR_MISSING')


def test_real_hidden_cli_cannot_run_with_json_arguments_without_live_pipe(tmp_path):
    root=tmp_path/'TEST_FIXTURE_unowned_worker';root.mkdir()
    command=[sys.executable,'-I','-B','-X','pycache_prefix='+str(root/'absent'),
        str(Path(calendar.__file__).absolute()),'--worker','--plan',str(root/'plan.json'),
        '--plan-sha256','a'*64,'--claim',str(root/'claim.json'),'--claim-sha256','b'*64,
        '--supervisor-control',str(root/'supervision')]
    environment={key:value for key,value in os.environ.items() if not key.startswith('STOCKFISH_SDK_TREE_')}
    result=subprocess.run(command,capture_output=True,text=True,timeout=30,env=environment)
    assert result.returncode==3 and 'LIVE_WORKER_PIPE_REQUIRED' in result.stdout
    assert list(root.iterdir())==[]


@pytest.mark.parametrize('mutation',[lambda rows:rows.pop(),lambda rows:rows.append(rows[0]),
    lambda rows:rows[0].update(is_trading_day=True),lambda rows:rows[0].update(extra='not-calendar')])
def test_independent_natural_day_contract_rejects_malformed_rows(fixture,mutation):
    plan=calendar.prepare(fixture.runtime)
    runtime=fixture.runtime
    with runtime.exclusive():
        cache=native.CaptureCache(fixture.cache_root,Budget(calendar.bounded.CAPS),executor=fixture.executor)
        record=cache.capture_one(native.RequestSpec(**plan['request_spec']))
    response=cache.verify(record)
    mutation(response['rows'])
    fake=SimpleNamespace(verify=lambda _record:response)
    with pytest.raises(ValueError,match='CURRENT_CALENDAR_'):
        calendar.validate_calendar(fake,record,now=datetime.now(timezone.utc))
