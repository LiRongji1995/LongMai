"""TEST_FIXTURE: actual generated responsibility layers and negative side effects."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import active_runtime_v1 as build
from scripts import business_authority_v1 as authority_module


@pytest.fixture(scope="module")
def active(tmp_path_factory):
    root=tmp_path_factory.mktemp("TEST_FIXTURE_active")
    state=root/"state";state.mkdir()
    owner=authority_module.Authority.initialize(root/"authority.sqlite3","TEST_FIXTURE_ACTIVE",
        onboarding={"mode":"EMPTY_NEW_WORKSPACE","state_roots":[str(state)]})
    manifest=json.loads(build.BASE_MANIFEST.read_bytes())
    # The development tree may contain preserved user edits. Copy only exact
    # reviewed source bytes; the existing publication checkout is the fallback.
    base=root/"frozen_source";base.mkdir()
    publication=build.ROOT.parents[1]/"publish"/build.ROOT.parent.name/"stock-fish"
    for item in manifest["files"]:
        found=None
        for candidate in (build.ROOT,publication):
            path=candidate/item["path"]
            if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==item["sha256"]:
                found=path.read_bytes();break
        assert found is not None,"exact published preimage missing: "+item["path"]
        path=base/item["path"];path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(found)
    destination=root/"active"
    result=build.build(base,destination,authority_binding={"database":str(owner.path),"workspace_id":owner.workspace_id})
    registry=root/"registry";registry.mkdir();(registry/"resources").mkdir()
    from scripts.codex_runtime_recovery_v2 import reference
    plan={"profile":{"source_root":str(destination)},"inventory":{"registry_roots":[str(registry)]},
          "active_source_manifest":reference(destination/"ACTIVE_RUNTIME_MANIFEST.json")}
    with owner._connect() as db:
        db.execute("INSERT INTO epochs VALUES (?,?,?,?,?)",("TEST_FIXTURE_epoch","[]",json.dumps(plan),"ACTIVE",authority_module.stamp()))
    return dict(root=root,base=base,source=destination,owner=owner,manifest=result,state=state)


def child(active, code, *, claim=None):
    env=dict(os.environ)
    for key in ("STOCKFISH_WORKER_CAPABILITY","STOCKFISH_BUSINESS_ID","STOCKFISH_AUTHORITY_DATABASE","STOCKFISH_AUTHORITY_WORKSPACE"):
        env.pop(key,None)
    if claim is not None:
        owner=active["owner"]
        token=owner.derive_capability(claim,["worker","storage","network","provider","metrics"])
        env.update(STOCKFISH_AUTHORITY_DATABASE=str(owner.path),STOCKFISH_AUTHORITY_WORKSPACE=owner.workspace_id,
                   STOCKFISH_WORKER_CAPABILITY=token,STOCKFISH_BUSINESS_ID=claim["business_id"])
    prelude="import sys,os,json\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[1])\n"
    return subprocess.run([sys.executable,"-I","-B","-c",prelude+code,str(active["source"]),str(active["root"])],
        cwd=active["root"],env=env,capture_output=True,text=True,timeout=60)


def new_claim(active, day, operation=None):
    from stockfish_core.yaogu.premarket_runtime_v1 import DEFAULT_LIMITS
    business=operation or dict(backend="premarket",observation_date=f"2026-11-{day:02d}",target_session=f"2026-11-{day+1:02d}")
    claim=active["owner"].claim(business,epoch_id="TEST_FIXTURE_epoch",limits={
        "provider_requests":4,"http_attempts":4,"sdk_calls":4,
        **{p+"_read_bytes":DEFAULT_LIMITS[p+"_read_bytes"] for p in ("capture","core","theme","presentation","supervisor")}})
    active["owner"].start(claim)
    return claim


def test_build_preserves_exact_inputs_and_no_unreviewed_module(active):
    result=build.verify(active["source"])
    assert result["activation_authorized"] is False
    assert "stockfish_core/yaogu/native_r2_upstream_v1.py" not in result["output_sha256"]
    for item in json.loads(build.BASE_MANIFEST.read_bytes())["files"]:
        assert hashlib.sha256((active["base"]/item["path"]).read_bytes()).hexdigest()==item["sha256"]
    assert "Runtime.run_once" in result["hooks"]["scripts/codex_workspace.py"]


def test_original_bypass_entry_points_cannot_write_or_launch_without_capability(active):
    result=child(active,"""
from scripts import codex_workspace
from stockfish_core.storage import SQLiteStore
from stockfish_core.yaogu import premarket_workflow_v1 as workflow
from scripts import active_business_gateway_v1 as guard
import sqlite3
blocked=[]
for name,call in [
 ('legacy-register',lambda:codex_workspace.register_profile('unused')),
 ('sqlite-name-trick',lambda:sqlite3.connect(Path(sys.argv[2])/'unauthorized?mode=ro.sqlite3')),
 ('sqlite-uri-looking',lambda:sqlite3.connect('file:unauthorized?mode=ro')),
 ('store',lambda:SQLiteStore(Path(sys.argv[2])/'forbidden.sqlite3')),
 ('standalone-supervisor',lambda:workflow.supervise({'state_root':str(Path(sys.argv[2])/'forbidden-worker')}))]:
 try:call()
 except Exception as error:blocked.append(name)
 else:raise AssertionError('UNGUARDED:'+name)
print(json.dumps(blocked))
"""
    )
    assert result.returncode==0,result.stderr
    assert len(json.loads(result.stdout))==5
    assert not (active["root"]/'forbidden.sqlite3').exists()
    assert not (active["root"]/'forbidden-worker').exists()


def test_generated_runtime_keeps_failed_business_across_keys_versions_and_engines(active):
    result=child(active,"""
from scripts import codex_workspace as old
from scripts.business_authority_v1 import Authority
from types import SimpleNamespace
from scripts import active_business_gateway_v1 as guard
with guard._control(): owner=Authority(str(Path(sys.argv[2])/'authority.sqlite3'),'TEST_FIXTURE_ACTIVE')
config=Path(sys.argv[2])/'config.json'
from scripts import active_business_gateway_v1 as guard
with guard._control(): config.write_text(json.dumps({'limits':{'max_requests':3,'max_http_attempts':3,'max_sdk_calls':3}}))
runtime=SimpleNamespace(authority=owner,epoch_id='TEST_FIXTURE_epoch',profile={'config':str(config)},
 check_binding=lambda:None,preflight=lambda **kw:{'allowed':True},_validated=old.Runtime._validated)
from scripts import codex_premarket_preflight_v1 as preflight
preflight.plan=lambda runtime:{'execution_admitted':True,'observation_date':'2026-12-01','target_session':'2026-12-02'}
calls=[]
def failed():
 calls.append(1)
 raise RuntimeError('TEST_FIXTURE_ORIGINAL_FAILURE')
for key,engine,version in [('a','original','V1'),('b','original','V1'),('b','shared','V2')]:
 try:
  old.Runtime.run_once(runtime,dict(backend='premarket',observation_date='2026-12-01',target_session='2026-12-02',request_key=key,execution_engine=engine,schema_version=version),failed,lambda _:True)
 except Exception: pass
 else:raise AssertionError('failure unexpectedly succeeded')
assert calls==[1],calls
print(json.dumps({'worker_calls':len(calls),'network_calls':0}))
"""
    )
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)=={"worker_calls":1,"network_calls":0}


def test_dictionary_validation_contract_reuse_and_changed_metadata(active):
    result=child(active,"""
from scripts import codex_workspace as old
from scripts.business_authority_v1 import Authority
from types import SimpleNamespace
from scripts import active_business_gateway_v1 as guard
with guard._control(): owner=Authority(str(Path(sys.argv[2])/'authority.sqlite3'),'TEST_FIXTURE_ACTIVE')
runtime=SimpleNamespace(authority=owner,epoch_id='TEST_FIXTURE_epoch',profile={},check_binding=lambda:None,
 preflight=lambda **kw:{'allowed':True},_validated=old.Runtime._validated)
from scripts import codex_premarket_preflight_v1 as preflight
preflight.plan=lambda runtime:{'execution_admitted':True,'observation_date':'2026-12-03','target_session':'2026-12-04'}
calls=[]
def action():calls.append(1);return {'TEST_FIXTURE':'original'}
identity=dict(path='/api/strategy-experiments/run',payload={'input_id':'TEST_FIXTURE_INPUT','strategy_id':'TEST_FIXTURE_STRATEGY'},request_key='first')
first=old.Runtime.run_once(runtime,identity,action,lambda r:{'verified':True,'evidence':r})
second=old.Runtime.run_once(runtime,{**identity,'request_key':'changed'},action,lambda r:{'verified':True,'evidence':r})
assert first['status']=='RECORDED' and second['status']=='REUSED' and len(calls)==1
assert second['new_action_calls']==0 and second['validation']['verified'] is True
print('TEST_FIXTURE_OK')
"""
    )
    assert result.returncode==0,result.stderr


def test_source_inventory_preflight_blocks_before_claim_and_capability_failure_is_terminal(active):
    result=child(active,"""
from types import SimpleNamespace
from scripts import codex_workspace as old
from scripts import active_business_gateway_v1 as g
from scripts.business_authority_v1 import Authority,business_id
with g._control():
 owner=Authority(str(Path(sys.argv[2])/'authority.sqlite3'),'TEST_FIXTURE_ACTIVE')
 config=Path(sys.argv[2])/'TEST_FIXTURE_admission_config.json'
 config.write_text(json.dumps({'state_root':str(Path(sys.argv[2])/'TEST_FIXTURE_admission_state'),
                              'capture':{'allow_test_fixture':True}}))
runtime=SimpleNamespace(authority=owner,epoch_id='TEST_FIXTURE_epoch',profile={'config':str(config)},check_binding=lambda:None,
 preflight=lambda **kw:{'allowed':True},_validated=old.Runtime._validated)
original=owner.source_scope_status
owner.source_scope_status=lambda:'LEGACY_SOURCE_SCOPES_UNRECONCILED'
identity={'backend':'premarket','observation_date':'2026-12-07','target_session':'2026-12-08'}
calls=[]
admission=g.preflight_result(runtime,{'execution_admitted':True,'status':'ADMITTED_FOR_BOUNDED_EXECUTION','reasons':['EXISTING_REASON']})
assert admission['execution_admitted'] is False and admission['status']=='BLOCKED'
assert admission['reasons']==['EXISTING_REASON','LEGACY_SOURCE_SCOPES_UNRECONCILED']
try:old.Runtime.run_once(runtime,identity,lambda:calls.append(1),lambda _:True)
except ValueError as error:assert str(error)=='LEGACY_SOURCE_SCOPES_UNRECONCILED'
else:raise AssertionError('unreconciled sources admitted')
with g._control():assert owner.inspect(business_id(owner.workspace_id,g.request_business(identity))) is None
owner.source_scope_status=original
identity={'path':'/api/strategy-experiments/run','payload':{'input_id':'TEST_FIXTURE_CAP_FAILURE','strategy_id':'TEST_FIXTURE_STRATEGY'}}
def unavailable(*a,**k):raise RuntimeError('TEST_FIXTURE capability publication failed')
owner.derive_capability=unavailable
try:old.Runtime.run_once(runtime,identity,lambda:calls.append(1),lambda _:True)
except RuntimeError:pass
else:raise AssertionError('capability failure hidden')
with g._control():state=owner.inspect(business_id(owner.workspace_id,g.request_business(identity)))
assert state['state']=='FAILED_OR_UNKNOWN' and not state['budget_known'],state
assert calls==[]
print('TEST_FIXTURE_ADMISSION_FAILURE_PERSISTED')
""")
    assert result.returncode==0,result.stderr


def test_old_partial_queue_never_reaches_executor(active):
    claim=new_claim(active,10)
    result=child(active,"""
from stockfish_core.yaogu.request_queue import RequestQueue
from types import SimpleNamespace
rows=[{'state':'PARTIAL','attempts':1,'failure_category':'INTERRUPTED','request_id':'old'}]
queue=SimpleNamespace(requested_ids={'old'},rows=lambda ids:rows)
calls=[]
try:RequestQueue.drain(queue,lambda *a,**k:calls.append(1))
except ValueError as e:assert 'HISTORICAL_QUEUE_ATTEMPT' in str(e)
else:raise AssertionError('old partial was retried')
assert calls==[]
print('TEST_FIXTURE_OK')
""",claim=claim)
    assert result.returncode==0,result.stderr


def test_transport_scope_expires_and_cross_business_window_is_same(active):
    claim=new_claim(active,12)
    result=child(active,"""
from scripts import active_business_gateway_v1 as g
g.install()
request={'provider':'baostock','endpoint':'market_daily','parameters':{'date':'2026-11-12'}}
with g.network_scope():
 g.reserve_transport(request,'HTTP',0)
 assert g._NETWORK.get() is True
assert g._NETWORK.get() is False
try:g._audit('socket.connect',(None,('203.0.113.1',443)))
except ValueError as e:assert 'WITHOUT_TRANSPORT' in str(e)
else:raise AssertionError('expired network reservation')
assert g.source_window({'operation':'PREMARKET','observation_date':'2026-11-12'}, {})==g.source_window({'operation':'CAPTURE','parameters':{'window_start':'2026-11-12','window_end':'2026-11-12'}},{})
print('TEST_FIXTURE_OK')
""",claim=claim)
    assert result.returncode==0,result.stderr


def test_diagnostic_budget_remains_read_only_without_authority(active):
    result=child(active,"""
from stockfish_core.yaogu.premarket_runtime_v1 import Budget
b=Budget()
with b.phase('core'): b.charge_read(12)
assert b.metrics['returned_read_bytes']['core']==12
print('TEST_FIXTURE_OK')
"""
    )
    assert result.returncode==0,result.stderr


def test_transport_aliases_share_one_reservation(active):
    claim=new_claim(active,18)
    result=child(active,"""
from scripts import active_business_gateway_v1 as g
first={'provider':'eastmoney','endpoint':'daily_limits_eastmoney','parameters':{'date':'20261118','codes':['sh.600000','sz.000001']}}
second={'provider':'eastmoney','endpoint':'daily_limits_eastmoney','parameters':{'date':'2026-11-18','codes':['000001.SZ','600000.SH']}}
with g.network_scope():g.reserve_transport(first,'HTTP',0)
try:
 with g.network_scope():g.reserve_transport(second,'HTTP',0)
except ValueError as e:assert 'ALREADY_RESERVED' in str(e)
else:raise AssertionError('date/code aliases bypassed network reservation')
print('TEST_FIXTURE_OK')
""",claim=claim)
    assert result.returncode==0,result.stderr


def test_failed_provider_batch_cannot_be_retried_as_smaller_capture(active):
    first=new_claim(active,24)
    failed=child(active,"""
from scripts import active_business_gateway_v1 as g
calls=[]
try:
 with g.provider_scope('daily_limits_eastmoney',{'date':'2026-11-24','codes':['sh.600000','sz.000001']}):
  calls.append(1)
  raise RuntimeError('TEST_FIXTURE original provider failed')
except RuntimeError:pass
assert calls==[1]
print('TEST_FIXTURE_FAILED_ONCE')
""",claim=first)
    assert failed.returncode==0,failed.stderr
    active['owner'].finish(first,success=False,budget_known=False)
    second=new_claim(active,26,dict(operation='CAPTURE',provider='eastmoney',dataset='daily_limits_eastmoney',
        window_start='2026-11-24',window_end='2026-11-24',codes=['600000.SH']))
    blocked=child(active,"""
from scripts import active_business_gateway_v1 as g
calls=[]
try:
 with g.provider_scope('daily_limits_eastmoney',{'date':'2026-11-24','codes':['600000.SH']}):calls.append(1)
except ValueError as error:assert 'OVERLAP' in str(error),str(error)
else:raise AssertionError('smaller CAPTURE bypassed failed PREMARKET source')
assert calls==[]
print('TEST_FIXTURE_SECOND_PROVIDER_CALLS_ZERO')
""",claim=second)
    assert blocked.returncode==0,blocked.stderr
    assert active['owner'].inspect(second['business_id'])['cumulative_spent'].get('provider_requests',0)==0


@pytest.mark.parametrize('entry',['original','shared'])
def test_real_supervisor_bridge_keeps_failed_worker_and_native_cleanup(active,entry):
    claim=new_claim(active,20 if entry=='original' else 22)
    program="""
from stockfish_core.yaogu import premarket_workflow_v1 as original
from scripts import codex_premarket_shared_v1 as shared
from scripts import durable_stage_metrics_v1 as metrics
config=original.load_config(overrides={'state_root':str(Path(sys.argv[2])/'TEST_FIXTURE_supervisor_ENTRY'),
 'capture':{'offline':True,'allow_test_fixture':True},'limits':{'max_seconds':30}})
workflow=original if 'ENTRY'=='original' else shared
code,final=workflow.supervise(config,progress=False)
assert code==3 and final['status']=='FAILED',final
job=next((Path(config['state_root'])/'jobs').iterdir())
native=json.loads((job/'authority-supervisor/completion.json').read_text())
assert native['root_reaped'] is True,native
journals=list(job.glob('authority-metrics-worker-*'))
assert len(journals)==1
assert metrics.audit_journal(journals[0])['status']=='FAILED'
assert (job/'authority-writer-receipt.json').is_file()
print(json.dumps({'status':final['status'],'root_reaped':native['root_reaped'],'worker_journals':len(journals)}))
""".replace('ENTRY',entry)
    result=child(active,program,claim=claim)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)=={'status':'FAILED','root_reaped':True,'worker_journals':1}


def test_component_identity_display_does_not_change_strategy():
    files={"stockfish_core/yaogu/short_horizon_strength.py":b'from .contracts import digest\nx=1\n',
           "stockfish_core/yaogu/contracts.py":b'def digest(x): return x\n',
           "stockfish_core/yaogu/premarket_presentation_v1.py":b'label="one"\n',
           "config/short_horizon_strength_r1.yaml":b'threshold: 1\n'}
    first=build.component_identities(files)[0]
    changed=deepcopy(files);changed['stockfish_core/yaogu/premarket_presentation_v1.py']=b'label="two"\n'
    second=build.component_identities(changed)[0]
    assert first['strategy']==second['strategy']
    assert first['presentation']!=second['presentation']
    changed['stockfish_core/yaogu/contracts.py']=b'def digest(x): return str(x)\n'
    assert build.component_identities(changed)[0]['strategy']!=first['strategy']
    changed=deepcopy(files);changed['config/short_horizon_strength_r1.yaml']=b'threshold: 2\n'
    assert build.component_identities(changed)[0]['strategy']!=first['strategy']


def test_undeclared_or_changed_dependency_refused(active,tmp_path):
    import shutil
    copy=tmp_path/'TEST_FIXTURE_copy';shutil.copytree(active['source'],copy)
    unknown=copy/'stockfish_core/unknown_dynamic.py';unknown.write_text('TEST_FIXTURE=True\n')
    with pytest.raises(ValueError,match='UNDECLARED_SOURCE_DEPENDENCY'):
        build.verify(copy)
    unknown.unlink()
    source=copy/'stockfish_core/yaogu/short_horizon_strength.py';source.write_bytes(source.read_bytes()+b'\n# changed\n')
    with pytest.raises(ValueError,match='SOURCE_CHANGED'):
        build.verify(copy)


def _native_differential_fixture(active, tmp_path):
    """Exact-hashed synthetic inputs; original algorithm and native gates retained.

    The production source manifest is never edited. Only this isolated fixture's
    frozen workflow main selects the already-existing TEST_FIXTURE adapters.
    Both arms execute identical unmodified core/theme/display algorithm bodies.
    """
    import ast
    from datetime import datetime, timedelta, timezone
    import inspect
    import shutil
    import textwrap
    from zoneinfo import ZoneInfo
    from tests.support.saved_r2_v3_fixture import physical_source, write
    from tests.yaogu.test_premarket_native_core_v1 import FixtureBudget, captures
    from tests.yaogu.test_premarket_workflow_v1 import WorkflowFixtureTests
    from scripts.codex_runtime_recovery_v2 import reference

    root=(tmp_path/'TEST_FIXTURE_native_differential').resolve()
    root.mkdir()
    base=root/'frozen_source'
    shutil.copytree(active['base'],base)
    # The real supervisor repeats the latest-session gate using its actual clock.
    # Synthetic every-natural-day sessions are explicitly TEST_FIXTURE, never a
    # market calendar. Freeze their boundary once for both comparison arms.
    cutoff=datetime.now(timezone.utc)
    local=cutoff.astimezone(ZoneInfo('Asia/Shanghai'))
    observed=local.date() if local.hour >= 18 else local.date()-timedelta(days=1)
    target=observed+timedelta(days=1)
    capture_source=inspect.getsource(captures)
    assert capture_source.count('observed = date(2026, 9, 30)')==1
    assert capture_source.count('seen = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)')==1
    capture_source=capture_source.replace('observed = date(2026, 9, 30)',
        'observed = date.fromisoformat('+repr(str(observed))+')').replace(
        'seen = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)',
        'seen = datetime.fromisoformat('+repr((cutoff-timedelta(hours=1)).isoformat())+')')
    adapters=textwrap.dedent(inspect.getsource(WorkflowFixtureTests._adapters))
    helper_body="""from datetime import date, datetime, timedelta, timezone
import hashlib, json, socket, sys, unittest
from pathlib import Path
from unittest.mock import patch
from stockfish_core.yaogu.contracts import SourceReceipt
from stockfish_core.yaogu.provider_normalization import _clock
from stockfish_core.yaogu.premarket_capture_cache_v1 import CaptureCollection, CaptureRecord, ThemeSourceCollection
from stockfish_core.yaogu.premarket_runtime_v1 import strict_json
workflow=sys.modules[__name__]
"""
    helper_body+='\n'.join(inspect.getsource(item) for item in (write,physical_source,FixtureBudget))
    helper_body+='\n'+capture_source+'\nclass ExplicitTEST_FIXTURE(unittest.TestCase):\n'+textwrap.indent(adapters,'    ')
    helper_body+="""
fixture=ExplicitTEST_FIXTURE(methodName='runTest')
fixture.root=job/'TEST_FIXTURE_inputs'
fixture.config=config
adapters,now=fixture._adapters()
with patch.object(socket.socket,'connect',side_effect=AssertionError('TEST_FIXTURE_NETWORK')):
    return execute_workflow(config,job,now=now,adapters=adapters)
"""
    helper='def _TEST_FIXTURE_execute(config,job):\n'+textwrap.indent(helper_body,'    ')+'\n\n'
    relative='stockfish_core/yaogu/premarket_workflow_v1.py'
    path=base/relative
    original=path.read_text()
    old_call="value=execute_workflow(_read_control(job/'configuration.json'),job)"
    assert original.count(old_call)==1 and original.count('def main(argv=None):')==1
    changed=original.replace('def main(argv=None):',helper+'def main(argv=None):').replace(
        old_call,"value=_TEST_FIXTURE_execute(_read_control(job/'configuration.json'),job)")
    before,after=ast.parse(original),ast.parse(changed)
    for name in ('execute_workflow','supervise','_latest_gate'):
        body=lambda tree: ast.dump(next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name),include_attributes=False)
        assert body(before)==body(after),name
    path.write_text(changed)
    manifest=json.loads(build.BASE_MANIFEST.read_bytes())
    manifest['published_commit']='TEST_FIXTURE_ORIGINAL_ALGORITHMS_NATIVE_INPUT_ADAPTER'
    for item in manifest['files']:
        if item['path']==relative:
            raw=path.read_bytes()
            item.update(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
    fixture_manifest=root/'TEST_FIXTURE_BASE_MANIFEST.json'
    fixture_manifest.write_text(json.dumps(manifest,indent=2))
    state=root/'state';state.mkdir()
    owner=authority_module.Authority.initialize(root/'authority.sqlite3','TEST_FIXTURE_NATIVE_DIFFERENTIAL',
        onboarding={'mode':'EMPTY_NEW_WORKSPACE','state_roots':[str(state)]})
    destination=root/'active'
    build.build(base,destination,base_manifest=fixture_manifest,
        authority_binding={'database':str(owner.path),'workspace_id':owner.workspace_id})
    registry=root/'registry';registry.mkdir();(registry/'resources').mkdir()
    plan={'profile':{'source_root':str(destination)},'inventory':{'registry_roots':[str(registry)]},
        'active_source_manifest':reference(destination/'ACTIVE_RUNTIME_MANIFEST.json')}
    with owner._connect() as db:
        db.execute('INSERT INTO epochs VALUES (?,?,?,?,?)',('TEST_FIXTURE_epoch','[]',json.dumps(plan),'ACTIVE',authority_module.stamp()))
    return dict(root=root,base=base,source=destination,owner=owner,state=state,
        observed=str(observed),target=str(target))


def test_actual_core_theme_display_unknown_and_failure_differential(active,tmp_path):
    """Real native live-pipe worker vs unchanged in-process algorithm oracle."""
    fixture=_native_differential_fixture(active,tmp_path)
    fixture_root=fixture['root']
    program="""
import csv,time,resource
from stockfish_core.yaogu import premarket_workflow_v1 as workflow
root=Path(sys.argv[2]); mode=sys.argv[3]
config=workflow.load_config(overrides={'state_root':str(root/(mode+'-state')),
 'capture':{'offline':True,'allow_test_fixture':True},'limits':{'max_seconds':180}})
started=time.monotonic();cpu=time.process_time()
if mode=='original':
 job=root/'original';job.mkdir()
 result=workflow._TEST_FIXTURE_execute(config,job)
else:
 code,final=workflow.supervise(config,progress=False)
 assert code==2 and final['status']=='COMPLETED_WITH_UNKNOWNS',final
 job=next((Path(config['state_root'])/'jobs').iterdir())
 result=json.loads(Path(final['completion']['path']).read_bytes())
 native=json.loads((job/'authority-supervisor/completion.json').read_bytes())
 assert native['root_reaped'] is True and native['all_observed_descendants_gone'] is True,native
 assert native['cleanup']['errors']==[] and native['cleanup']['remaining']==[],native
assert result['status']=='COMPLETED_WITH_UNKNOWNS',result.get('error')
assert result['core_verified'] is True
assert result['metrics']['calls']['requests']==0
assert result['phases']['core']=='COMPLETE'
assert result['phases']['theme']=='COMPLETE'
assert result['phases']['presentation']=='COMPLETE'
assert result['phases']['news']=='UNKNOWN'
assert result['display']['counts']['displayed']==2
assert result['display']['counts']['original_selected']==2
summary={k:result[k] for k in ('status','phases','theme_status')}
summary['counts']=result['display']['counts']
summary['csv']={name:list(csv.DictReader(Path(result['core_artifacts'][name]['path']).open()))
 for name in ('all.csv','observation.csv','focus.csv','unselected.csv')}
summary['measurement']={'wall_seconds':time.monotonic()-started,'controller_cpu_seconds':time.process_time()-cpu,
 'terminated_children_cpu_seconds':resource.getrusage(resource.RUSAGE_CHILDREN).ru_utime+resource.getrusage(resource.RUSAGE_CHILDREN).ru_stime,
 'returned_read_bytes':result['metrics']['returned_read_bytes'],'job_bytes':workflow.directory_bytes(job),
 'controller_peak_rss':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'scope':'TEST_FIXTURE_ONLY',
 'comparison_boundary':'UNCHANGED_ALGORITHM_INPROCESS_VS_COMPLETE_NATIVE_WORKFLOW_CHAIN',
 'full_request_preflight_and_final_authority_audit_included':False}
summary['job']=str(job)
print(json.dumps(summary))
"""
    prelude="import sys,os,json\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[1])\n"
    command=[sys.executable,'-I','-B','-c',prelude+program]
    env=dict(os.environ)
    for key in ('STOCKFISH_AUTHORITY_DATABASE','STOCKFISH_AUTHORITY_WORKSPACE','STOCKFISH_WORKER_CAPABILITY','STOCKFISH_BUSINESS_ID'):
        env.pop(key,None)
    original=subprocess.run([*command,str(fixture['base']),str(fixture_root),'original'],
        cwd=fixture_root,env=env,capture_output=True,text=True,timeout=240)
    (fixture_root/'original.stdout').write_text(original.stdout)
    (fixture_root/'original.stderr').write_text(original.stderr)
    assert original.returncode==0,original.stderr
    claim=new_claim(fixture,16,dict(backend='premarket',observation_date=fixture['observed'],target_session=fixture['target']))
    owner=fixture['owner'];token=owner.derive_capability(claim,['storage','worker','metrics','provider','network'])
    env.update(STOCKFISH_AUTHORITY_DATABASE=str(owner.path),STOCKFISH_AUTHORITY_WORKSPACE=owner.workspace_id,
         STOCKFISH_WORKER_CAPABILITY=token,STOCKFISH_BUSINESS_ID=claim['business_id'])
    transformed=subprocess.run([*command,str(fixture['source']),str(fixture_root),'active'],
        cwd=fixture_root,env=env,capture_output=True,text=True,timeout=240)
    (fixture_root/'active.stdout').write_text(transformed.stdout)
    (fixture_root/'active.stderr').write_text(transformed.stderr)
    assert transformed.returncode==0,transformed.stderr
    old,new=json.loads(original.stdout),json.loads(transformed.stdout)
    (fixture_root/'comparison.json').write_text(json.dumps({'frozen':old['measurement'],'active':new['measurement']},indent=2))
    old.pop('measurement');new.pop('measurement')
    old.pop('job');job=Path(new.pop('job'))
    # Every numerical, eligibility, reason, UNKNOWN, phase and count assertion
    # remains exact. Only run_id is expected to differ between actual runs.
    for name,rows in old['csv'].items():
        assert len(rows)==len(new['csv'][name])
        for first,second in zip(rows,new['csv'][name]):
            assert first.pop('run_id')!=second.pop('run_id')
    assert old==new
    journals=list(job.glob('authority-metrics-worker-*'))
    assert len(journals)==1
    from scripts.durable_stage_metrics_v1 import audit_journal
    audit=audit_journal(journals[0])
    assert audit['status']=='COMPLETE'
    # The authority receipt is constructed from actual saved native evidence,
    # then independently reread; no JSON declaration replaces a live pipe.
    from scripts import active_delivery_bridge_v1 as bridge
    from scripts import daily_delivery_contract_v1 as contract
    from scripts import codex_delivery_audit_v1 as delivery
    reader=delivery._Reader(64*1024**2,60)
    _,final_ref=reader.read(job/'final-control.json',control=True)
    reader.terminal()
    binding=bridge.prepare_authority_receipt({'job':str(job),'final':final_ref},claim)
    owner.charge(claim,{'supervisor_read_bytes':binding['authority_receipt_validation_resources']['returned_read_bytes']})
    owner.finish(claim,success=True,receipt=binding)
    report=contract.audit_delivery(owner,claim['business_id'],job=job,journal_directory=journals[0],
        run_path=binding['canonical_run']['path'],run_sha256=binding['canonical_run']['sha256'])
    assert report['status']=='COMPLETED_WITH_UNKNOWNS'
    assert report['workflow']['archived_workflow_complete'] is True
    assert report['workflow']['phases']['news']=='UNKNOWN'
    assert report['authority_terminal_bound'] is True
    assert report['native_process_cleanup']['verified'] is True
    assert report['test_fixture'] is True
    assert report['source_reauthenticated_now'] is False
    assert report['user_delivery_status']=='UNKNOWN_NOT_OBSERVED'
    assert report['market_edge']=='NOT_ESTABLISHED'
    (fixture_root/'binding-audit.json').write_text(json.dumps(report,indent=2))
