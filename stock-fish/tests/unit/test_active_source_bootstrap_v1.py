"""TEST_FIXTURE native source collection; no live SDK/network or real registry.

Generated active modules, physical original RAW receipts, calendar validation,
Budget and SQLite authority are real. Provider rows/transport calls are explicit
fixtures. These source-stage tests do not claim a full market delivery.
"""
from copy import deepcopy
import ast
import hashlib
import inspect
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap

import pytest
from scripts import active_runtime_v1 as build
from scripts import active_source_bootstrap_v1 as source
from scripts import business_authority_v1 as authority
from scripts.codex_runtime_recovery_v2 import reference
from tests.unit.test_active_runtime_v1 import active, child
from tests.yaogu.test_premarket_capture_cache_v1 import FixtureExecutor


def fresh(active, tmp_path):
    root=(tmp_path/'TEST_FIXTURE_source_bootstrap').resolve();root.mkdir()
    state=root/'state';state.mkdir()
    owner=authority.Authority.initialize(root/'authority.sqlite3','TEST_FIXTURE_SOURCE',
        onboarding={'mode':'EMPTY_NEW_WORKSPACE','state_roots':[str(state)]})
    destination=root/'active'
    build.build(active['base'],destination,authority_binding={'database':str(owner.path),'workspace_id':owner.workspace_id})
    registry=root/'registry';registry.mkdir();(registry/'resources').mkdir()
    plan={'profile':{'source_root':str(destination)},'inventory':{'registry_roots':[str(registry)]},
          'active_source_manifest':reference(destination/'ACTIVE_RUNTIME_MANIFEST.json')}
    with owner._connect() as db:
        db.execute('INSERT INTO epochs VALUES (?,?,?,?,?)',('TEST_FIXTURE_epoch','[]',json.dumps(plan),'ACTIVE',authority.stamp()))
    return dict(root=root,state=state,source=destination,base=active['base'],owner=owner)


def configuration():
    from stockfish_core.yaogu.premarket_workflow_v1 import load_config
    return load_config(overrides={'state_root':'/TEST_FIXTURE_STATE',
        'capture':{'offline':False,'retry_failed':False},'limits':{'max_seconds':120}})


def missing_plan(config):
    return dict(execution_admitted=False,status='BLOCKED',observation_date=None,target_session=None,
        reasons=['AUTHENTICATED_CURRENT_CALENDAR_REQUIRED','WHOLE_SOURCE_GRAPH_NOT_AUTHENTICATED'],
        original_writer_limits=deepcopy(config['limits']),capacity={'allowed':True},
        stages=[{'name':'CALENDAR','requests':[{'status':'MISSING'}]}],network_calls=0)


@pytest.mark.parametrize('change,blocked',[
    ({},False),({'capture':{'offline':True}},True),({'capture':{'retry_failed':True}},True),
    ({'seed_sources':['TEST_FIXTURE_seed']},True),({'limits':{'max_seconds':None}},True)])
def test_bootstrap_admission_preserves_original_facts_and_limits(change,blocked):
    config=configuration()
    for key,value in change.items():
        if isinstance(value,dict):config[key].update(value)
        else:config[key]=value
    plan=missing_plan(config);before=deepcopy(plan)
    result=source.annotate_preflight(plan,config,'FRESH_EMPTY_WORKSPACE_NO_LEGACY_SOURCE_EFFECTS')
    assert plan==before and result['execution_admitted'] is False
    assert result['source_bootstrap_admitted'] is (not blocked)
    assert source.admitted(result) is (not blocked)
    assert result['full_workflow_ready'] is False and result['network_calls']==0


def test_ready_but_unbounded_is_blocked_before_claim_and_failed_rows_never_admit():
    config=configuration();config['limits']['max_seconds']=None
    plan=missing_plan(config);plan.update(execution_admitted=True,reasons=[])
    result=source.annotate_preflight(plan,config,'FRESH_EMPTY_WORKSPACE_NO_LEGACY_SOURCE_EFFECTS')
    assert result['execution_admitted'] is True and result['full_workflow_ready'] is False
    assert not source.admitted(result) and 'FINITE_ORIGINAL_LIMITS_REQUIRED' in result['active_execution_blockers']
    config=configuration()
    for status in ['INCOMPLETE_NO_IMPLICIT_RETRY','UNFINISHED_NO_IMPLICIT_RETRY','NOT_VISIBLE_AT_CUTOFF']:
        plan=missing_plan(config);plan['stages'][0]['requests'][0]['status']=status
        assert not source.admitted(source.annotate_preflight(plan,config,'FRESH_EMPTY_WORKSPACE_NO_LEGACY_SOURCE_EFFECTS'))


def test_ready_offline_reuse_remains_legal_but_fixture_cannot_use_real_state():
    config=configuration();config['capture']['offline']=True
    plan=missing_plan(config);plan.update(execution_admitted=True,reasons=[],observation_date='2026-11-04',target_session='2026-11-05')
    plan['stages'][0]['requests'][0]['status']='AVAILABLE'
    result=source.annotate_preflight(plan,config,'FRESH_EMPTY_WORKSPACE_NO_LEGACY_SOURCE_EFFECTS')
    assert source.admission_allowed(result,{'observation_date':'2026-11-04','target_session':'2026-11-05'})
    assert result['full_workflow_ready'] and not result['source_bootstrap_admitted']
    config['capture']['retry_failed']=True
    result=source.annotate_preflight(plan,config,'FRESH_EMPTY_WORKSPACE_NO_LEGACY_SOURCE_EFFECTS')
    assert not source.admitted(result) and 'FAILED_SOURCE_RETRY_FORBIDDEN' in result['active_execution_blockers']
    config['capture']['retry_failed']=False
    config['state_root']='/real-state';config['capture']['allow_test_fixture']=True
    result=source.annotate_preflight(plan,config,'FRESH_EMPTY_WORKSPACE_NO_LEGACY_SOURCE_EFFECTS')
    assert not source.admitted(result) and result['test_fixture'] is True


SETUP=r'''
from types import SimpleNamespace
from datetime import datetime,timedelta,timezone
import hashlib
from scripts import active_business_gateway_v1 as g
from scripts import business_authority_v1 as a
from scripts import codex_premarket_preflight_v1 as preflight
from scripts import active_source_bootstrap_v1 as bootstrap
from scripts import durable_stage_metrics_v1 as metrics
from stockfish_core.yaogu import premarket_capture_cache_v1 as native
from stockfish_core.yaogu import premarket_workflow_v1 as workflow
from stockfish_core.yaogu import provider_requests
from stockfish_core.yaogu.providers import archive_receipt
from stockfish_core.yaogu.premarket_runtime_v1 import Budget
cache_module=native
with g._control():
 owner=a.Authority(Path(sys.argv[2])/'authority.sqlite3','TEST_FIXTURE_SOURCE')
 state=Path(sys.argv[2])/'state';config_path=state/'configuration.json'
 config=workflow.load_config(overrides={'state_root':str(state),
  'capture':{'offline':False,'retry_failed':False,'allow_test_fixture':True,'request_http_attempts':1},
  'limits':{'max_seconds':120,'max_requests':200,'max_sdk_calls':1000,'max_http_attempts':1000,'request_http_attempts':1}})
 if MODE=='budget':config['limits']['max_requests']=1
 config_path.write_text(json.dumps(config))
 profile={'name':'TEST_FIXTURE_SOURCE','backend':'premarket','state_root':str(state),
  'source_root':sys.argv[1],'python':sys.executable,'config':str(config_path),'storage':{'expected_growth_bytes':1024}}
 def checked():
  with g._control():g._bound_authority(owner)
 runtime=SimpleNamespace(profile=profile,authority=owner,epoch_id='TEST_FIXTURE_epoch',check_binding=checked,preflight=lambda **kwargs:{'allowed':True})
os.environ['STOCKFISH_AUTHORITY_DATABASE']=str(owner.path)
os.environ['STOCKFISH_AUTHORITY_WORKSPACE']=owner.workspace_id
'''


@pytest.mark.parametrize('layout',['absent','empty','partial_nonempty'])
def test_generated_plan_empty_cache_is_readonly_and_nonempty_damage_is_not_empty(active,tmp_path,layout):
    fixture=fresh(active,tmp_path)
    code="MODE='plan'\nLAYOUT="+repr(layout)+"\n"+SETUP+r'''
with g._control():
 if LAYOUT!='absent':
  for name in (['index'] if LAYOUT=='partial_nonempty' else ['index','raw','raw-index','locators']):
   (state/'cache'/name).mkdir(parents=True)
  if LAYOUT=='partial_nonempty':(state/'cache'/'index'/'TEST_FIXTURE_partial').write_text('UNKNOWN')
 before={str(p):p.read_bytes() for p in state.rglob('*') if p.is_file()}
 paths=sorted(str(p) for p in state.rglob('*'))
try:
 result=preflight.plan(runtime,allow_test_fixture=True)
except ValueError as error:
 assert LAYOUT=='partial_nonempty' and 'PARTIAL_CACHE_NOT_EMPTY' in str(error),str(error)
else:
 assert LAYOUT!='partial_nonempty'
 assert result['execution_admitted'] is False and result['source_bootstrap_admitted'] is True,result
 assert result['full_workflow_ready'] is False and result['network_calls']==0
 assert bootstrap.admission_allowed(result,{'observation_date':'2026-11-04','target_session':'2026-11-05'})
with g._control():
 assert before=={str(p):p.read_bytes() for p in state.rglob('*') if p.is_file()}
 assert paths==sorted(str(p) for p in state.rglob('*'))
 with owner._connect(readonly=True) as db:assert db.execute('SELECT COUNT(*) FROM requests').fetchone()[0]==0
print('TEST_FIXTURE_READONLY_EMPTY_PLAN_OK')
'''
    result=child(fixture,code)
    assert result.returncode==0,result.stderr
    assert 'TEST_FIXTURE_READONLY_EMPTY_PLAN_OK' in result.stdout


def executor_source(*, native_versions=False):
    text=inspect.getsource(FixtureExecutor)
    text=text.replace("archive_receipt(Path(output)/'raw','TEST_FIXTURE',endpoint,params,payload,",
        "archive_receipt(Path(output)/'raw',provider_requests.ENDPOINTS[endpoint]['provider'],endpoint,params,payload,")
    text=text.replace("provider='TEST_FIXTURE',parameters=params", "provider=provider_requests.ENDPOINTS[endpoint]['provider'],parameters=params")
    text=text.replace("payload = dict(status=status", "if endpoint=='calendar' and MODE=='truncated':rows=rows[:-1]\n        payload = dict(status=status")
    if native_versions:
        # Exercise the frozen parsers with their actual schema, while every
        # archive/run remains explicitly isolated TEST_FIXTURE data.
        versions="{'baostock':'BAOSTOCK_00.9.30_R03_V1','akshare':'AKSHARE_1.18.94_R03_V1','sse':'SSE_RISK_PLATE_MAIN_P0_R03_V1'}[provider_requests.ENDPOINTS[endpoint]['provider']]"
        text=text.replace("'TEST_FIXTURE_PROVIDER_ROWS_V1'",versions)
    return text


@pytest.mark.parametrize('mode',['empty','cached_calendar','truncated','wrong_claim','budget'])
def test_generated_original_collector_uses_one_claim_calendar_first_and_same_budget(active,tmp_path,mode):
    fixture=fresh(active,tmp_path)
    code='MODE='+repr(mode)+'\n'+SETUP+'\n'+executor_source()+r'''
now=datetime.now(timezone.utc);local=now.astimezone(native.SH)
observed=local.date() if local.hour>=18 else local.date()-timedelta(days=1)
while observed.weekday()>=5:observed-=timedelta(days=1)
target=observed+timedelta(days=1)
while target.weekday()>=5:target+=timedelta(days=1)
claimed_observed=observed-timedelta(days=1) if MODE=='wrong_claim' else observed
with g._control():
 claim=owner.claim({'operation':'PREMARKET','observation_date':str(claimed_observed),'target_session':str(target)},
  epoch_id='TEST_FIXTURE_epoch',limits={'provider_requests':200,'sdk_calls':1000,'http_attempts':1000,
   **{k:v for k,v in config['limits'].items() if k.endswith('_read_bytes')}})
 owner.start(claim);cap=owner.derive_capability(claim,['worker','storage','metrics','provider','network'])
 reservation=owner.consume_once(cap,stage='worker',effect_kind='WORKER_START',stable_effect_id='TEST_FIXTURE_SOURCE_WORKER')
token=g._CURRENT.set({'owner':owner,'claim':claim,'capability':cap})
job=state/'TEST_FIXTURE_source_job';job.mkdir()
journal=metrics.StageJournal(job/'events',claim,test_fixture=True)
jtoken=g._JOURNAL.set(journal)
executor=FixtureExecutor()
def execute(endpoint,params,output,**kwargs):
 request={'provider':provider_requests.ENDPOINTS[endpoint]['provider'],'endpoint':endpoint,'parameters':params}
 with g.provider_scope(endpoint,params):
  count=(len(params['codes'])+2 if endpoint in ('adjustment_factors','stock_basic','history','history_adjusted') else 3) if request['provider']=='baostock' else 1
  for index in range(count):
   with g.network_scope():g.reserve_transport(request,'SDK' if request['provider']=='baostock' else 'HTTP',index)
  answer=executor(endpoint,params,output,**kwargs)
  return g.provider_result(answer)
budget=Budget(config['limits'],job_dir=job,cache_dir=state/'cache')
cache=native.CaptureCache(state/'cache',budget,config=config['capture'],executor=execute)
failure=None
try:
 with budget.phase('capture'),budget.meter_files():
  if MODE=='cached_calendar':
   bootstrap.bootstrap_capture(cache,now,config['capture'])
   assert [e for e,_ in executor.calls]==['calendar']
   calendar_count=budget.metrics['calls']['requests']
  collection=native.capture_collect(cache,now=datetime.now(timezone.utc),config=config['capture'])
  assert collection.ready_for_core and collection.coverage_type=='TEST_FIXTURE'
  assert collection.observation_date==str(observed) and collection.target_session==str(target)
  assert [e for e,_ in executor.calls].count('calendar')==1
  assert budget.metrics['calls']['requests']==len(executor.calls)>1
  assert getattr(cache,'_active_calendar_binding')['budget_id']==id(budget)
except BaseException as error:
 failure=error
finally:
 writer=journal.finish('FAILED' if failure else 'COMPLETE')
 with g._control():
  if failure is None:owner.complete_effect(cap,reservation,receipt={'TEST_FIXTURE':'source stage only'})
  owner.finish(claim,success=failure is None,receipt={'TEST_FIXTURE':True,'writer':writer},budget_known=failure is None)
 g._JOURNAL.reset(jtoken);g._CURRENT.reset(token)
if MODE in {'truncated','wrong_claim','budget'}:
 assert failure is not None
 assert [e for e,_ in executor.calls]==['calendar'],(type(failure).__name__,str(failure),executor.calls)
 assert budget.metrics['calls']['requests']==1
 with g._control():
  for engine in ['original','shared']:
   try:owner.claim(claim['business'],epoch_id='TEST_FIXTURE_epoch',request_metadata={'request_key':'renamed','engine':engine})
   except ValueError as error:assert 'RECONCILE_NO_RETRY' in str(error)
   else:raise AssertionError('failed source business reopened')
else:
 assert failure is None,(type(failure).__name__,str(failure))
with g._control():
 saved=owner.inspect(claim['business_id'])
 assert saved['state']==('FAILED_OR_UNKNOWN' if failure else 'RECORDED')
 with owner._connect(readonly=True) as db:
  assert db.execute("SELECT COUNT(*) FROM effects WHERE effect_kind='WORKER_START'").fetchone()[0]==1
print(json.dumps({'TEST_FIXTURE':True,'mode':MODE,'provider_calls':len(executor.calls),'calendar_calls':sum(ep=='calendar' for ep,_ in executor.calls),'failure':str(failure) if failure else None,'declared_requests_spent':budget.metrics['calls']['requests']}))
'''
    result=child(fixture,code)
    (fixture['root']/'child.stdout').write_text(result.stdout)
    (fixture['root']/'child.stderr').write_text(result.stderr)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['calendar_calls']==1



TEST_FIXTURE_AUDIT = """import sys
# TEST_FIXTURE: installed before project imports in every native child.
def _TEST_FIXTURE_deny_network(event, args):
    if event in {'socket.connect', 'socket.getaddrinfo', 'socket.gethostbyname', 'socket.gethostbyaddr'}:
        raise RuntimeError('TEST_FIXTURE_EARLY_NETWORK_FORBIDDEN:' + event)
sys.addaudithook(_TEST_FIXTURE_deny_network)
sys._TEST_FIXTURE_NETWORK_DENIED = True
"""


def sandbox_command(command):
    if sys.platform == 'darwin':
        sandbox = shutil.which('sandbox-exec')
        assert sandbox, 'TEST_FIXTURE requires inherited OS network denial'
        return [sandbox, '-p', '(version 1)(allow default)(deny network*)', *command]
    # Linux CI uses its explicitly synthetic executor and early Python guard.
    # Do not claim this fallback is a kernel network namespace.
    return command


def test_native_fixture_os_network_denial_and_earliest_audit(tmp_path):
    if sys.platform == 'darwin':
        probe = "import socket,errno\ns=socket.socket()\ntry:s.connect(('127.0.0.1',9))\nexcept OSError as e:assert e.errno==errno.EPERM,e\nelse:raise AssertionError('OS network denial absent')"
        result=subprocess.run(sandbox_command([sys.executable,'-I','-B','-c',probe]),capture_output=True,text=True,timeout=10)
        assert result.returncode==0,result.stderr
    probe = TEST_FIXTURE_AUDIT + """
import socket
for attempt in (lambda:socket.getaddrinfo('TEST_FIXTURE.invalid',443),lambda:socket.socket().connect(('127.0.0.1',9))):
 try:attempt()
 except RuntimeError as error:assert str(error).startswith('TEST_FIXTURE_EARLY_NETWORK_FORBIDDEN:')
 else:raise AssertionError('early Python network denial absent')
print('TEST_FIXTURE_EARLY_NETWORK_DENIAL_PROVEN')
"""
    result=subprocess.run(sandbox_command([sys.executable,'-I','-B','-c',probe]),capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    assert 'TEST_FIXTURE_EARLY_NETWORK_DENIAL_PROVEN' in result.stdout


def native_fixture(active,tmp_path):
    """Only fixture provider/news adapters are added to a private frozen copy."""
    root=(tmp_path/'TEST_FIXTURE_native_source').resolve();root.mkdir()
    base=root/'frozen';shutil.copytree(active['base'],base)
    body="""from datetime import datetime, timedelta, timezone
import hashlib, json, sys
from pathlib import Path
from scripts import active_business_gateway_v1 as g
from stockfish_core.yaogu import premarket_capture_cache_v1 as cache_module
from stockfish_core.yaogu import provider_requests
from stockfish_core.yaogu.providers import archive_receipt
from stockfish_core.yaogu.premarket_news_v1 import collect_reported_news
MODE='native_complete'
assert getattr(sys,'_TEST_FIXTURE_NETWORK_DENIED',False), 'TEST_FIXTURE_EARLY_GUARD_MISSING'
job=Path(job_dir)
if sys.platform=='darwin':
 import socket,ctypes,errno
 sock=socket.socket();libc=ctypes.CDLL(None,use_errno=True)
 address=ctypes.create_string_buffer(bytes([16,socket.AF_INET,0,9,127,0,0,1])+bytes(8))
 result=libc.connect(sock.fileno(),address,16);error=ctypes.get_errno();sock.close()
 assert result==-1 and error==errno.EPERM, 'TEST_FIXTURE_WORKER_OS_NETWORK_DENIAL_MISSING'
 (job/'TEST_FIXTURE_kernel_network_denied.json').write_text(json.dumps({'kernel_connect_errno':error,'python_hook_installed':True}))
"""+executor_source(native_versions=True)+r'''
executor=FixtureExecutor()
def execute(endpoint,params,output,**kwargs):
 request={'provider':provider_requests.ENDPOINTS[endpoint]['provider'],'endpoint':endpoint,'parameters':params}
 with g.provider_scope(endpoint,params):
  count=(len(params['codes'])+2 if endpoint in ('adjustment_factors','stock_basic','history','history_adjusted') else 3) if request['provider']=='baostock' else 1
  for ordinal in range(count):
   with g.network_scope():g.reserve_transport(request,'SDK' if request['provider']=='baostock' else 'HTTP',ordinal)
  value=executor(endpoint,params,output,**kwargs)
  (job/'TEST_FIXTURE_provider_calls.json').write_text(json.dumps(executor.calls))
  return g.provider_result(value)
class FixtureCache(cache_module.CaptureCache):
 def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs,executor=execute)
def unknown_news(**kwargs):
 kwargs['config']={**kwargs['config'],'offline':True}
 return collect_reported_news(**kwargs)
return {'cache':FixtureCache,'news':unknown_news}
'''
    helper='def _TEST_FIXTURE_source_adapters(job_dir):\n'+textwrap.indent(body,'    ')+'\n\n'
    relative='stockfish_core/yaogu/premarket_workflow_v1.py';path=base/relative;original=path.read_text()
    needle="    adapters = adapters or {}"
    assert original.count(needle)==1
    changed=original.replace('def execute_workflow(',helper+'def execute_workflow(').replace(needle,
        "    adapters = _TEST_FIXTURE_source_adapters(job_dir)")
    # Shared clones this very function: adapter selection is at the actual
    # capture responsibility layer, while its imported collect/theme behavior
    # and all original calculations remain unchanged.
    restored=changed.replace("    adapters = _TEST_FIXTURE_source_adapters(job_dir)",needle)
    for name in ['execute_workflow','supervise','_latest_gate']:
        node=lambda text:ast.dump(next(n for n in ast.parse(text).body if isinstance(n,ast.FunctionDef) and n.name==name),include_attributes=False)
        assert node(original)==node(restored)
    path.write_text(changed)
    # An unintended default executor must fail before any SDK even if fixture
    # adapter selection regresses. This is private TEST_FIXTURE source only.
    provider_relative='stockfish_core/yaogu/provider_requests.py'
    provider_path=base/provider_relative;provider_text=provider_path.read_text()
    function=next(n for n in ast.parse(provider_text).body if isinstance(n,ast.FunctionDef) and n.name=='execute_request')
    position=function.body[0].lineno-1
    lines=provider_text.splitlines(keepends=True)
    lines.insert(position,"    raise AssertionError('TEST_FIXTURE_DEFAULT_PROVIDER_FORBIDDEN')\n")
    provider_path.write_text(''.join(lines))
    extensions=root/'TEST_FIXTURE_extensions'
    for name in build.EXTENSIONS:
        target=extensions/name;target.parent.mkdir(parents=True,exist_ok=True)
        raw=(build.ROOT/name).read_text()
        if name=='scripts/active_worker_bootstrap_v1.py':
            needle='from __future__ import annotations\n'
            assert raw.count(needle)==1
            raw=raw.replace(needle,needle+'\n'+TEST_FIXTURE_AUDIT,1)
        if name=='scripts/active_business_gateway_v1.py' and sys.platform=='darwin':
            needle='    if args:\n        args=(command,*args[1:])'
            assert raw.count(needle)==1
            raw=raw.replace(needle,'''    # TEST_FIXTURE: fixed compute worker only; supervisor retains native ps.
    if '--module' in command:
        position=command.index('--module')
        if command[position:position+4]==['--module','scripts.active_delivery_bridge_v1','--','worker']:
            command=['/usr/bin/sandbox-exec','-p','(version 1)(allow default)(deny network*)',*command]
'''+needle)
        target.write_text(raw)
    manifest=json.loads(build.BASE_MANIFEST.read_bytes())
    for row in manifest['files']:
        if row['path'] in {relative,provider_relative}:
            changed_path=base/row['path'];row.update(bytes=changed_path.stat().st_size,sha256=hashlib.sha256(changed_path.read_bytes()).hexdigest())
    manifest_path=root/'TEST_FIXTURE_base_manifest.json';manifest_path.write_text(json.dumps(manifest))
    state=root/'state';state.mkdir()
    owner=authority.Authority.initialize(root/'authority.sqlite3','TEST_FIXTURE_SOURCE',
        onboarding={'mode':'EMPTY_NEW_WORKSPACE','state_roots':[str(state)]})
    destination=root/'active'
    build.build(base,destination,extension_root=extensions,base_manifest=manifest_path,authority_binding={'database':str(owner.path),'workspace_id':owner.workspace_id})
    registry=root/'registry';registry.mkdir();(registry/'resources').mkdir()
    plan={'profile':{'source_root':str(destination)},'inventory':{'registry_roots':[str(registry)]},
        'active_source_manifest':reference(destination/'ACTIVE_RUNTIME_MANIFEST.json')}
    with owner._connect() as db:
        db.execute('INSERT INTO epochs VALUES (?,?,?,?,?)',('TEST_FIXTURE_epoch','[]',json.dumps(plan),'ACTIVE',authority.stamp()))
    return dict(root=root,state=state,source=destination,owner=owner)



def test_generated_native_bootstrap_early_guard_covers_child_imports(active,tmp_path):
    fixture=native_fixture(active,tmp_path)
    bootstrap=fixture['source']/'scripts/active_worker_bootstrap_v1.py'
    command=sandbox_command([sys.executable,'-I','-B','-c',"""import runpy,sys
runpy.run_path(sys.argv[1],run_name='TEST_FIXTURE_BOOTSTRAP_PROBE')
assert sys._TEST_FIXTURE_NETWORK_DENIED is True
import socket
for call in (lambda:socket.getaddrinfo('TEST_FIXTURE.invalid',443),lambda:socket.socket().connect(('127.0.0.1',9))):
 try:call()
 except RuntimeError as error:assert 'TEST_FIXTURE_EARLY_NETWORK_FORBIDDEN' in str(error)
 else:raise AssertionError('native bootstrap allowed network')
print('TEST_FIXTURE_ACTUAL_BOOTSTRAP_DENIED')
""",str(bootstrap)])
    result=subprocess.run(command,cwd=fixture['root'],capture_output=True,text=True,timeout=30)
    (fixture['root']/'network-denial.stdout').write_text(result.stdout)
    (fixture['root']/'network-denial.stderr').write_text(result.stderr)
    assert result.returncode==0,result.stderr
    assert 'TEST_FIXTURE_ACTUAL_BOOTSTRAP_DENIED' in result.stdout


def test_empty_source_actual_daily_entry_native_worker_retains_unknown_and_binds_final(active,tmp_path):
    fixture=native_fixture(active,tmp_path)
    code=r'''
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from scripts import active_business_gateway_v1 as g
from scripts import business_authority_v1 as a
from scripts import codex_workspace as w
from scripts import codex_daily_v2 as daily
from scripts import codex_premarket_preflight_v1 as preflight
from stockfish_core.yaogu import premarket_workflow_v1 as workflow
with g._control():
 owner=a.Authority(Path(sys.argv[2])/'authority.sqlite3','TEST_FIXTURE_SOURCE')
 state=Path(sys.argv[2])/'state';reports=state/'reports';reports.mkdir()
 config=workflow.load_config(overrides={'state_root':str(state),
  'capture':{'offline':False,'retry_failed':False,'allow_test_fixture':True,'request_http_attempts':1},
  'limits':{'max_seconds':180,'max_requests':200,'max_sdk_calls':1000,'max_http_attempts':1000,'request_http_attempts':1}})
 config_path=state/'configuration.json';config_path.write_text(json.dumps(config))
 profile={'schema_version':w.PROFILE_VERSION,'name':'TEST_FIXTURE_SOURCE','backend':'premarket',
  'state_root':str(state),'report_root':str(reports),'source_root':sys.argv[1],'python':sys.executable,'config':str(config_path),
  'storage':{'roots':[{'name':'state','path':str(state),'role':'runtime'}],
   'target_root':'state','expected_growth_bytes':1024,'min_free_bytes':0,'max_managed_bytes':3*1024**3}}
 runtime=w.Runtime(Path(sys.argv[2])/'registry',profile,w._binding(profile))
 runtime.authority=owner;runtime.epoch_id='TEST_FIXTURE_epoch'
 original_binding=runtime.check_binding
 def checked():
  with g._control():original_binding();g._bound_authority(owner)
 runtime.check_binding=checked
os.environ['STOCKFISH_AUTHORITY_DATABASE']=str(owner.path)
os.environ['STOCKFISH_AUTHORITY_WORKSPACE']=owner.workspace_id
before=sorted(str(p) for p in state.rglob('*'))
plan=preflight.plan(runtime,allow_test_fixture=True)
assert plan['source_bootstrap_admitted'] is True and plan['execution_admitted'] is False,plan
assert sorted(str(p) for p in state.rglob('*'))==before and not (state/'cache').exists()
now=datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8)))
observed=now.date() if now.hour>=18 else now.date()-timedelta(days=1)
while observed.weekday()>=5:observed-=timedelta(days=1)
target=observed+timedelta(days=1)
while target.weekday()>=5:target+=timedelta(days=1)
args=SimpleNamespace(observation_date=observed,target_session=target,request_key='TEST_FIXTURE_original_key',engine='original')
code,result=daily._premarket(runtime,args)
assert code==2 and result['status']=='RECORDED',result
receipt=result['receipt'];job=Path(receipt['job'])
assert receipt['status']=='COMPLETED_WITH_UNKNOWNS'
completion=json.loads((job/'worker-completion.json').read_bytes())
assert completion['core_verified'] is True and completion['phases']['capture']=='COMPLETE'
assert completion['phases']['news']=='UNKNOWN' and completion['phases']['presentation']=='COMPLETE'
if sys.platform=='darwin':
 assert json.loads((job/'TEST_FIXTURE_kernel_network_denied.json').read_bytes())['kernel_connect_errno']==1
calls=json.loads((job/'TEST_FIXTURE_provider_calls.json').read_bytes())
assert calls[0][0]=='calendar' and sum(ep=='calendar' for ep,_ in calls)==1
with g._control():
 saved=owner.inspect(result['business_id'])
 assert saved['state']=='RECORDED' and saved['receipt']['gateway_receipt']==receipt
 with owner._connect(readonly=True) as db:
  assert db.execute('SELECT COUNT(*) FROM requests').fetchone()[0]==1
  assert db.execute("SELECT COUNT(*) FROM effects WHERE effect_kind='WORKER_START'").fetchone()[0]==1
native=json.loads((job/'authority-supervisor/completion.json').read_bytes())
assert native['root_reaped'] and native['all_observed_descendants_gone']
from scripts import durable_stage_metrics_v1 as metrics
audit=metrics.audit_journal(Path(saved['receipt']['journal_directory']))
assert audit['status']=='COMPLETE' and audit['test_fixture']
assert any(stage['stage']=='source_calendar_bootstrap' for stage in audit['stages'])
assert saved['receipt']['identity']['attempt_id']==saved['attempt_id']
print(json.dumps({'TEST_FIXTURE':True,'status':receipt['status'],'provider_calls':len(calls),'same_business_id':result['business_id'],'worker_count':1,'native_cleanup':True}))
'''
    env={k:v for k,v in os.environ.items() if k not in {'STOCKFISH_AUTHORITY_DATABASE','STOCKFISH_AUTHORITY_WORKSPACE','STOCKFISH_WORKER_CAPABILITY','STOCKFISH_BUSINESS_ID'}}
    command=[sys.executable,'-I','-B','-c',TEST_FIXTURE_AUDIT+'import os,json\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[1])\n'+code,str(fixture['source']),str(fixture['root'])]
    result=subprocess.run(command,cwd=fixture['root'],env=env,capture_output=True,text=True,timeout=240)
    (fixture['root']/'native.stdout').write_text(result.stdout)
    (fixture['root']/'native.stderr').write_text(result.stderr)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['native_cleanup'] is True
