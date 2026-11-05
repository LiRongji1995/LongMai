"""TEST_FIXTURE: native ACTIVE archive -> authority outcome -> readonly audit.

Exact-hashed local provider adapters run through the original daily entry and
V3 supervision. The actual compute worker and synchronous outcome process have
OS network denial. The supervisor retains native ps with an earliest Python
network guard; a default provider call is forbidden in the private source tree.
"""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys

import pytest

from tests.unit.test_active_runtime_v1 import active
from tests.unit.test_active_source_bootstrap_v1 import native_fixture


PRELUDE = r'''
import sys
def _TEST_FIXTURE_early_audit(event,args):
    if event in {'socket.connect','socket.getaddrinfo','socket.gethostbyname','socket.gethostbyaddr'}:
        raise RuntimeError('TEST_FIXTURE_EARLY_NETWORK_FORBIDDEN:'+event)
sys.addaudithook(_TEST_FIXTURE_early_audit)
sys._TEST_FIXTURE_NETWORK_DENIED=True
import socket, os, json
from pathlib import Path
def _TEST_FIXTURE_no_network(*a, **k):
    raise AssertionError('TEST_FIXTURE_NETWORK_FORBIDDEN')
socket.socket.connect=_TEST_FIXTURE_no_network
socket.socket.connect_ex=_TEST_FIXTURE_no_network
socket.create_connection=_TEST_FIXTURE_no_network
socket.getaddrinfo=_TEST_FIXTURE_no_network
sys.path.insert(0,sys.argv[1])
root=Path(sys.argv[2])
'''


FIXTURE_CLOCK = r'''
# Synthetic business clocks are installed before either original artifact is
# created. Never alter a sealed source, calendar, receipt, evaluator or result.
import builtins as _fixture_builtins, datetime as _fixture_datetime, os as _fixture_os
if 'TEST_FIXTURE_UTC_ORIGIN' in _fixture_os.environ:
    _fixture_real_datetime=_fixture_datetime.datetime
    _fixture_offset=(_fixture_real_datetime.fromisoformat(_fixture_os.environ['TEST_FIXTURE_UTC_ORIGIN'])
                     -_fixture_real_datetime.fromisoformat(_fixture_os.environ['TEST_FIXTURE_REAL_ORIGIN']))
    class _TEST_FIXTURE_ClockMeta(type):
        def __instancecheck__(cls,value):return isinstance(value,_fixture_real_datetime)
    class _TEST_FIXTURE_DateTime(_fixture_real_datetime,metaclass=_TEST_FIXTURE_ClockMeta):
        @classmethod
        def now(cls,tz=None):return _fixture_real_datetime.now(tz)+_fixture_offset
        @classmethod
        def utcnow(cls):return _fixture_real_datetime.utcnow()+_fixture_offset
    sys._TEST_FIXTURE_datetime=_TEST_FIXTURE_DateTime
    _fixture_import=_fixture_builtins.__import__
    def _TEST_FIXTURE_import(name,globals=None,locals=None,fromlist=(),level=0):
        result=_fixture_import(name,globals,locals,fromlist,level)
        package=(globals or {}).get('__package__','') or ''
        if name.startswith(('scripts','stockfish_core')) or package.startswith(('scripts','stockfish_core')):
            # Patch runtime module clocks after model annotations were built
            # with the real datetime type. No global datetime module mutation.
            for module_name,module in tuple(sys.modules.items()):
                if module_name.startswith(('scripts.','stockfish_core.')) and module is not None:
                    if (not getattr(getattr(module,'__spec__',None),'_initializing',False)
                            and vars(module).get('datetime') is _fixture_real_datetime):
                        module.datetime=_TEST_FIXTURE_DateTime
        return result
    _fixture_builtins.__import__=_TEST_FIXTURE_import
'''


def _child(tree, program, name, *, claim=None, timeout=240, os_sandbox=True, fixture_clock=None):
    sandbox=shutil.which('sandbox-exec')
    assert sandbox, 'TEST_FIXTURE_OS_NETWORK_SANDBOX_REQUIRED_NO_DOWNGRADE'
    environment=dict(os.environ)
    for key in ('STOCKFISH_AUTHORITY_DATABASE','STOCKFISH_AUTHORITY_WORKSPACE',
                'STOCKFISH_WORKER_CAPABILITY','STOCKFISH_BUSINESS_ID'):
        environment.pop(key,None)
    if fixture_clock is not None:
        environment['TEST_FIXTURE_UTC_ORIGIN']=fixture_clock
        # One running offset is inherited by the actual supervisor/worker;
        # monotonic clocks, native process births and OS timestamps stay real.
        from datetime import datetime, timezone
        environment['TEST_FIXTURE_REAL_ORIGIN']=datetime.now(timezone.utc).isoformat()
    if claim is not None:
        owner=tree['owner']
        token=owner.derive_capability(claim,['storage','worker','metrics','provider','network'])
        environment.update(STOCKFISH_AUTHORITY_DATABASE=str(owner.path),
            STOCKFISH_AUTHORITY_WORKSPACE=owner.workspace_id,
            STOCKFISH_WORKER_CAPABILITY=token,STOCKFISH_BUSINESS_ID=claim['business_id'])
    command=[sys.executable,'-I','-B','-c',PRELUDE+(FIXTURE_CLOCK if fixture_clock else '')+program,
             str(tree['source']),str(tree['root'])]
    if os_sandbox:
        command=[sandbox,'-p','(version 1)(allow default)(deny network*)',*command]
    result=subprocess.run(command,cwd=tree['root'],env=environment,capture_output=True,text=True,timeout=timeout)
    (tree['root']/(name+'.stdout')).write_text(result.stdout)
    (tree['root']/(name+'.stderr')).write_text(result.stderr)
    # Do not serialize environment capabilities. Exact public argv/program and
    # exit are retained to make test boundaries independently reviewable.
    (tree['root']/(name+'.command.json')).write_text(json.dumps({'argv':command,'exit_code':result.returncode},indent=2))
    assert result.returncode==0,result.stderr
    return json.loads(result.stdout)


def _prepared(active,tmp_path):
    # The supervisor needs the native setuid ps. The shared private fixture
    # wraps only the real compute worker with inherited OS denial, and forbids
    # default provider execution before SDK import. The controller retains its
    # earliest Python audit denial; the worker verifies libc connect gets EPERM.
    tree=native_fixture(active,tmp_path)
    saved=_child(tree,r'''
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from scripts import active_business_gateway_v1 as gateway
from scripts import business_authority_v1 as a
from scripts import codex_workspace as old
from scripts import codex_daily_v2 as daily
from stockfish_core.yaogu import premarket_workflow_v1 as workflow
with gateway._control():
    owner=a.Authority(root/'authority.sqlite3','TEST_FIXTURE_SOURCE')
    state=root/'state';reports=state/'reports';reports.mkdir()
    config=workflow.load_config(overrides={'state_root':str(state),
        'capture':{'offline':False,'retry_failed':False,'allow_test_fixture':True,'request_http_attempts':1},
        'limits':{'max_seconds':180,'max_requests':200,'max_sdk_calls':1000,'max_http_attempts':1000,'request_http_attempts':1}})
    config_path=state/'configuration.json';config_path.write_text(json.dumps(config))
    profile={'schema_version':old.PROFILE_VERSION,'name':'TEST_FIXTURE_SOURCE','backend':'premarket',
        'state_root':str(state),'report_root':str(reports),'source_root':sys.argv[1],
        'python':sys.executable,'config':str(config_path),
        'storage':{'roots':[{'name':'state','path':str(state),'role':'runtime'}],
            'target_root':'state','expected_growth_bytes':1024,'min_free_bytes':0,'max_managed_bytes':3*1024**3}}
    runtime=old.Runtime(root/'registry',profile,old._binding(profile))
    runtime.authority=owner;runtime.epoch_id='TEST_FIXTURE_epoch'
    bound=runtime.check_binding
    def check():
        with gateway._control():bound();gateway._bound_authority(owner)
    runtime.check_binding=check
os.environ['STOCKFISH_AUTHORITY_DATABASE']=str(owner.path)
os.environ['STOCKFISH_AUTHORITY_WORKSPACE']=owner.workspace_id
now=datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8)))
observed=now.date() if now.hour>=18 else now.date()-timedelta(days=1)
while observed.weekday()>=5:observed-=timedelta(days=1)
target=observed+timedelta(days=1)
while target.weekday()>=5:target+=timedelta(days=1)
args=SimpleNamespace(observation_date=observed,target_session=target,request_key='TEST_FIXTURE_premarket',engine='original')
code,result=daily._premarket(runtime,args)
assert code==2 and result['status']=='RECORDED',result
job=Path(result['receipt']['job'])
assert json.loads((job/'TEST_FIXTURE_kernel_network_denied.json').read_text())['kernel_connect_errno']==1
with gateway._control():
    saved=owner.inspect(result['business_id'])
    assert saved['state']=='RECORDED'
    binding=saved['receipt']
    completion=json.loads((job/'worker-completion.json').read_text())
    assert completion['core_verified'] is True and completion['phases']['news']=='UNKNOWN'
print(json.dumps({'parent_business_id':result['business_id'],'job':str(job),'binding':binding,
                  'journal':binding['journal_directory']}))
''','native-premarket',os_sandbox=False)
    (tree['root']/'TEST_FIXTURE_parent.json').write_text(json.dumps(saved))
    return tree


RUNTIME = r'''
from scripts import active_business_gateway_v1 as gateway
from scripts import business_authority_v1 as authority_module
from scripts import active_runtime_v1 as builder
from scripts import active_outcome_pipeline_v1 as pipeline
from scripts import codex_workspace as old
from scripts import daily_delivery_contract_v1 as contract
with gateway._control():
    owner=authority_module.Authority(root/'authority.sqlite3','TEST_FIXTURE_SOURCE')
    info=json.loads((root/'TEST_FIXTURE_parent.json').read_text())
    state=root/'state'; reports=state/'reports';reports.mkdir(exist_ok=True)
    config=state/'TEST_FIXTURE_outcome_config.json'
    config.write_text(json.dumps({'state_root':str(state),'capture':{'offline':True,'allow_test_fixture':True},
                                 'limits':{'max_seconds':180}}))
    profile={'schema_version':old.PROFILE_VERSION,'name':'TEST_FIXTURE_outcome','backend':'premarket',
        'state_root':str(state),'report_root':str(reports),'source_root':sys.argv[1],
        'python':sys.executable,'config':str(config),
        'storage':{'roots':[{'name':'state','path':str(state),'role':'runtime'}],
            'target_root':'state','expected_growth_bytes':1024,'min_free_bytes':0,'max_managed_bytes':2*1024**3}}
    runtime=old.Runtime(root/'registry',profile,old._binding(profile))
    runtime.authority=owner;runtime.epoch_id='TEST_FIXTURE_epoch'
    bound=runtime.check_binding
    def check():
        with gateway._control():bound();gateway._bound_authority(owner)
    runtime.check_binding=check
    runtime.check_binding();assert runtime.preflight()['allowed']
arguments=dict(parent_business_id=info['parent_business_id'],signal_job=info['job'],
    signal_final_sha256=info['binding']['job_final']['sha256'],outcome_job=info['job'],
    outcome_final_sha256=info['binding']['job_final']['sha256'],destination=state/'TEST_FIXTURE_outcome',request_key='first')
'''


def test_native_active_pipeline_unknown_product_binds_authority_and_readonly_audit(active,tmp_path):
    tree=_prepared(active,tmp_path)
    result=_child(tree,RUNTIME+r'''
answer=pipeline.execute(runtime,**arguments)
assert answer['status']=='RECORDED' and answer['new_action_calls']==1,answer
binding=answer['receipt']
assert binding['source_authority_restored'] is False and binding['historical_pit_verified'] is False
assert binding['market_edge']=='NOT_ESTABLISHED'
with gateway._control():
    original_product=json.loads(Path(binding['product']['path']).read_text())
    report=contract.audit_delivery(owner,info['parent_business_id'],job=info['job'],
        journal_directory=info['journal'],run_path=info['binding']['canonical_run']['path'],
        run_sha256=info['binding']['canonical_run']['sha256'],outcomes_path=binding['product']['path'],
        outcomes_sha256=binding['product']['sha256'],outcome_business_id=answer['business_id'],allow_test_fixture=True)
assert report['outcome_authority']['status']=='VERIFIED'
assert report['outcome_authority']['native_evaluator_recomputed'] is True
assert report['source_reauthenticated_now'] is True and report['test_fixture'] is True
assert report['historical_feature_pit_established'] is False
assert report['status']=='COMPLETED_WITH_UNKNOWNS' and report['workflow']['phases']['news']=='UNKNOWN'
assert contract._label_maturity(report['horizon_cells'])['status']=='NOT_ESTABLISHED'
expected=report['horizon_cells']['expected']
assert expected>0 and original_product['counts']=={'UNKNOWN':expected}
assert all(item['status']=='UNKNOWN' and 'REGISTERED_CALENDAR_HORIZON_UNAVAILABLE' in item['reason_codes']
           for item in original_product['items'])
second=pipeline.execute(runtime,**{**arguments,'request_key':'changed','destination':state/'TEST_FIXTURE_new_output'})
assert second['status']=='REUSED' and second['new_action_calls']==0
assert not (state/'TEST_FIXTURE_new_output').exists()
with gateway._control():
    # An attacker changes saved content; fresh SHA/JSON fields cannot replace
    # the native immutable terminal reference. Preserve this failed evidence.
    path=Path(binding['product']['path'])
    changed=dict(original_product,source_authority=True)
    path.write_text(json.dumps(changed))
try:pipeline.execute(runtime,**{**arguments,'request_key':'third'})
except ValueError:pass
else:raise AssertionError('TEST_FIXTURE_TAMPERED_PRODUCT_REUSED')
print(json.dumps({'outcome_status':report['outcome_authority']['status'],
    'unknown':original_product['counts']['UNKNOWN'],'expected':expected,'test_fixture':report['test_fixture'],
    'market_edge':report['market_edge'],'accepted_independent_labels':0}))
''','native-outcome')
    assert result['outcome_status']=='VERIFIED' and result['unknown']==result['expected']>0
    assert result['test_fixture'] is True and result['market_edge']=='NOT_ESTABLISHED'
    assert result['accepted_independent_labels']==0


def test_failed_outcome_same_business_cannot_change_key_or_output_to_retry(active,tmp_path):
    tree=_prepared(active,tmp_path)
    result=_child(tree,RUNTIME+r'''
with gateway._control():arguments['destination'].mkdir()
try:pipeline.execute(runtime,**arguments)
except ValueError as error:assert 'OUTPUT_EXISTS_PRESERVED' in str(error),str(error)
else:raise AssertionError('TEST_FIXTURE_EXISTING_OUTPUT_WAS_REPLACED')
for key in ('second','third'):
    try:pipeline.execute(runtime,**{**arguments,'request_key':key,'destination':state/('TEST_FIXTURE_'+key)})
    except ValueError as error:assert 'RECONCILE_NO_RETRY' in str(error),str(error)
    else:raise AssertionError('TEST_FIXTURE_FAILED_OUTCOME_RETRIED')
with gateway._control():
    with owner._connect(readonly=True) as connection:
        rows=connection.execute("SELECT state,receipt,budget_known FROM requests WHERE json_extract(identity,'$.operation')='OUTCOME'").fetchall()
assert len(rows)==1 and rows[0]['state']=='FAILED_OR_UNKNOWN' and not rows[0]['budget_known']
assert rows[0]['receipt'] is None
assert not (state/'TEST_FIXTURE_second').exists() and not (state/'TEST_FIXTURE_third').exists()
assert not list(arguments['destination'].iterdir())
print(json.dumps({'attempts':len(rows),'state':rows[0]['state'],'product_files':0,'network_calls':0}))
''','native-outcome-failed')
    assert result=={'attempts':1,'state':'FAILED_OR_UNKNOWN','product_files':0,'network_calls':0}


def _two_dated_archives(active, tmp_path, monkeypatch, *, later_date='2026-10-29'):
    """Native files are born under two declared synthetic clocks, never resealed.

    Both dates use the same exact source tree and authority. The second original
    calendar keeps the first finite prefix; its longer lookback is explicit.
    Two mainboard securities are observed, with one actual provider-row absence
    on the last horizon day. No post-normalization input/result editing occurs.
    """
    from tests.unit import test_active_source_bootstrap_v1 as fixture_module
    from datetime import date, timedelta
    original_executor = fixture_module.executor_source

    def executor_source(*, native_versions=False):
        raw = original_executor(native_versions=native_versions)
        raw = 'datetime = sys._TEST_FIXTURE_datetime\n' + raw
        raw = raw.replace("'sh.688001'", "'sh.600002'")
        needle = "rows = [{'证券代码':'600001','证券简称':'TEST_FIXTURE_MAIN','上市日期':'2000-01-01'}]"
        assert raw.count(needle) == 1
        raw = raw.replace(needle, "rows = [{'证券代码':code,'证券简称':'TEST_FIXTURE_MAIN','上市日期':'2000-01-01'} for code in ('600001','600002')]")
        needle = '        payload = dict(status=status'
        assert raw.count(needle) == 1
        raw = raw.replace(needle, "        if endpoint == 'market_daily' and params['date'] == " + repr(later_date) + ":\n"
                          "            rows = [row for row in rows if row['code'] != 'sh.600002']\n" + needle)
        return raw

    with monkeypatch.context() as patch:
        patch.setattr(fixture_module, 'TEST_FIXTURE_AUDIT', fixture_module.TEST_FIXTURE_AUDIT + FIXTURE_CLOCK)
        patch.setattr(fixture_module, 'executor_source', executor_source)
        tree = fixture_module.native_fixture(active, tmp_path)
    later = date.fromisoformat(later_date)
    next_session = later + timedelta(days=1)
    while next_session.weekday() >= 5:
        next_session += timedelta(days=1)
    for role, observed, target, lookback in (
        ('signal', '2026-10-15', '2026-10-16', 800),
        ('later', later_date, str(next_session), 800 + (later-date(2026,10,15)).days),
    ):
        program = 'ROLE,OBSERVED,TARGET,LOOKBACK=' + repr((role, observed, target, lookback)) + '\n' + r'''
from datetime import date
from types import SimpleNamespace
from scripts import active_business_gateway_v1 as gateway
from scripts import business_authority_v1 as a
from scripts import codex_workspace as old
from scripts import codex_daily_v2 as daily
from stockfish_core.yaogu import premarket_workflow_v1 as workflow
assert a.datetime is sys._TEST_FIXTURE_datetime
with gateway._control():
    owner=a.Authority(root/'authority.sqlite3','TEST_FIXTURE_SOURCE')
    state=root/'state';reports=state/'reports';reports.mkdir(exist_ok=True)
    config=workflow.load_config(overrides={'state_root':str(state),
        'capture':{'offline':False,'retry_failed':False,'allow_test_fixture':True,'request_http_attempts':1,
                   'calendar_lookback_days':LOOKBACK},
        'limits':{'max_seconds':180,'max_requests':200,'max_sdk_calls':1000,'max_http_attempts':1000,'request_http_attempts':1}})
    config_path=state/('TEST_FIXTURE_'+ROLE+'_configuration.json');config_path.write_text(json.dumps(config))
    profile={'schema_version':old.PROFILE_VERSION,'name':'TEST_FIXTURE_SOURCE_'+ROLE,'backend':'premarket',
        'state_root':str(state),'report_root':str(reports),'source_root':sys.argv[1],
        'python':sys.executable,'config':str(config_path),
        'storage':{'roots':[{'name':'state','path':str(state),'role':'runtime'}],
            'target_root':'state','expected_growth_bytes':1024,'min_free_bytes':0,'max_managed_bytes':3*1024**3}}
    runtime=old.Runtime(root/'registry',profile,old._binding(profile))
    runtime.authority=owner;runtime.epoch_id='TEST_FIXTURE_epoch'
    bound=runtime.check_binding
    def check():
        with gateway._control():bound();gateway._bound_authority(owner)
    runtime.check_binding=check
os.environ['STOCKFISH_AUTHORITY_DATABASE']=str(owner.path)
os.environ['STOCKFISH_AUTHORITY_WORKSPACE']=owner.workspace_id
args=SimpleNamespace(observation_date=date.fromisoformat(OBSERVED),target_session=date.fromisoformat(TARGET),
                     request_key='TEST_FIXTURE_premarket_'+ROLE,engine='original')
code,result=daily._premarket(runtime,args)
assert code==2 and result['status']=='RECORDED',result
job=Path(result['receipt']['job'])
assert json.loads((job/'TEST_FIXTURE_kernel_network_denied.json').read_text())['kernel_connect_errno']==1
with gateway._control():
    saved=owner.inspect(result['business_id'])
    assert saved['state']=='RECORDED' and saved['business']['observation_date']==OBSERVED
    binding=saved['receipt']
    completion=json.loads((job/'worker-completion.json').read_text())
    assert completion['core_verified'] is True and completion['phases']['news']=='UNKNOWN'
print(json.dumps({'parent_business_id':result['business_id'],'job':str(job),'binding':binding,
                  'journal':binding['journal_directory'],'observation_date':OBSERVED}))
'''
        saved = _child(tree, program, 'native-' + role, os_sandbox=False,
                       fixture_clock=observed + 'T12:00:00+00:00')
        path = tree['root']/('TEST_FIXTURE_parent.json' if role == 'signal' else 'TEST_FIXTURE_later.json')
        path.write_text(json.dumps(saved))
    return tree


def _audit_two_dates(tree, *, adjacent, composite=False):
    return _child(tree, 'ADJACENT,COMPOSITE=' + repr((adjacent,composite)) + '\n' + RUNTIME + r'''
with gateway._control():
    later=json.loads((root/'TEST_FIXTURE_later.json').read_text())
assert info['parent_business_id']!=later['parent_business_id'] and info['job']!=later['job']
arguments.update(outcome_job=later['job'],outcome_final_sha256=later['binding']['job_final']['sha256'],
                 outcome_business_id=later['parent_business_id'])
if COMPOSITE:
    arguments['daily_archives']=[{'business_id':later['parent_business_id'],'job':later['job'],
                                 'final_sha256':later['binding']['job_final']['sha256']}]
answer=pipeline.execute(runtime,**arguments)
assert answer['status']=='RECORDED' and answer['new_action_calls']==1,answer
binding=answer['receipt']
assert binding['parent_business_id']==info['parent_business_id']
assert binding['outcome_source_business_id']==later['parent_business_id']
assert binding['signal_archive']['job']==info['job'] and binding['outcome_archive']['job']==later['job']
if COMPOSITE:
    assert binding['composite_input_manifest']['source_input_ids']==binding['source_input_ids']
    assert len(binding['source_input_ids'])==1 and len(binding['composite_input_manifest']['all_input_ids'])==2
    assert binding['outcome_daily_archives'][0]['business_id']==later['parent_business_id']
    assert binding['outcome_daily_archives'][0]['input_id']==binding['source_input_ids'][0]
with gateway._control():
    product=json.loads(Path(binding['product']['path']).read_text())
    report=contract.audit_delivery(owner,info['parent_business_id'],job=info['job'],
        journal_directory=info['journal'],run_path=info['binding']['canonical_run']['path'],
        run_sha256=info['binding']['canonical_run']['sha256'],outcomes_path=binding['product']['path'],
        outcomes_sha256=binding['product']['sha256'],outcome_business_id=answer['business_id'],allow_test_fixture=True)
assert report['outcome_authority']['status']=='VERIFIED'
assert report['outcome_authority']['outcome_source_business_id']==later['parent_business_id']
assert report['outcome_authority']['native_evaluator_recomputed'] is True
assert report['test_fixture'] is True and report['historical_feature_pit_established'] is False
assert report['horizon_cells']['expected']==10 and report['horizon_cells']['full_cartesian_product_provided'] is True
assert sorted({item['horizon'] for item in product['items']})==[1,2,3,5,10]
unknown=[item for item in product['items'] if item['status']=='UNKNOWN']
assert all(item['metrics']['close_reference_change'] is None for item in unknown)
if ADJACENT:
    assert product['counts']=={'MATURE':1,'UNKNOWN':9},product['counts']
    mature=[item for item in product['items'] if item['status']=='MATURE']
    assert len(mature)==1 and mature[0]['horizon']==1 and mature[0]['instrument_id']=='600001.SH'
    assert mature[0]['label_end']=='2026-10-16' and mature[0]['metrics']['close_reference_change']==0
    missing=next(item for item in unknown if item['horizon']==1)
    assert missing['instrument_id']=='600002.SH' and 'DATED_SECURITY_STATE_UNKNOWN' in missing['reason_codes']
    assert all('REGISTERED_CALENDAR_HORIZON_UNAVAILABLE' in item['reason_codes']
               for item in unknown if item['horizon']>1)
else:
    # Two endpoint archives cannot establish the intervening nine daily board
    # identities. The original normalizer deliberately keeps those UNKNOWN.
    # The earlier 9-MATURE expectation failed and its full evidence is retained.
    assert product['counts']=={'UNKNOWN':10},product['counts']
    assert all('SECURITY_IDENTITY_UNSUPPORTED' in item['reason_codes'] for item in unknown)
    h10=next(item for item in unknown if item['horizon']==10 and item['instrument_id']=='600001.SH')
    assert h10['label_end']=='2026-10-29' and h10['calendar_mature'] is True
    assert [cell['status'] for cell in h10['session_states']]==['UNKNOWN']*9+['OBSERVED']
    missing=next(item for item in unknown if item['horizon']==10 and item['instrument_id']=='600002.SH')
    assert missing['session_states'][-1]['status']=='UNKNOWN'
    assert 'DATED_SECURITY_STATE_UNKNOWN' in missing['session_states'][-1]['reason_codes']
assert contract._label_maturity(report['horizon_cells'])['status']=='NOT_ESTABLISHED'
assert binding['source_authority_restored'] is False and binding['historical_pit_verified'] is False
assert report['market_edge']=='NOT_ESTABLISHED'
if COMPOSITE:
    # A change of composition mode must not manufacture another attempt for B.
    legacy_arguments={key:value for key,value in arguments.items() if key!='daily_archives'}
    legacy_arguments.update(request_key='TEST_FIXTURE_legacy_mode',destination=state/'TEST_FIXTURE_legacy_mode')
    reused=pipeline.execute(runtime,**legacy_arguments)
    assert reused['status']=='REUSED' and reused['new_action_calls']==0
    assert reused['business_id']==answer['business_id'] and reused['receipt']==binding
    assert not legacy_arguments['destination'].exists()
print(json.dumps({'source_businesses':2,'outcome_status':report['outcome_authority']['status'],
    'counts':product['counts'],'expected':10,'test_fixture':True,'historical_feature_pit_established':False,
    'market_edge':report['market_edge'],'formal_account_return':product['returns']}))
''', 'native-two-date-outcome')


def test_two_native_dates_cannot_backfill_intervening_daily_qualification(active,tmp_path,monkeypatch):
    tree = _two_dated_archives(active,tmp_path,monkeypatch)
    result = _audit_two_dates(tree, adjacent=False)
    assert result['counts']=={'UNKNOWN':10}
    assert result['source_businesses']==2 and result['formal_account_return'] is None


def test_adjacent_native_dates_mature_h1_and_preserve_missing_and_later_horizons(active,tmp_path,monkeypatch):
    tree = _two_dated_archives(active,tmp_path,monkeypatch,later_date='2026-10-16')
    # First execute the new compose path; the same B in the old single-input
    # mode must then reuse the immutable product, not execute another algorithm.
    result = _audit_two_dates(tree, adjacent=True, composite=True)
    assert result['counts']=={'MATURE':1,'UNKNOWN':9}
    assert result['source_businesses']==2 and result['formal_account_return'] is None
