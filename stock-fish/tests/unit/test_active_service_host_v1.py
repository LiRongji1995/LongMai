"""TEST_FIXTURE actual generated Flask/SQLite/JobService lifecycle, no listener.

Only the V2 recovery fixture is preassembled. Service construction, HTTP routes,
research handlers, job threads, authority and read-only database isolation are
the actual active implementation. Synthetic inputs are explicit TEST_FIXTURE.
"""
import json
from pathlib import Path
import runpy

import pytest


@pytest.fixture(scope="module")
def active_host(tmp_path_factory):
    source = runpy.run_path(str(Path(__file__).with_name("test_active_runtime_v1.py")))
    active = source["active"].__wrapped__(tmp_path_factory)
    from tests.r05_product.test_daily_research_outcomes import case
    snapshot, bundle, _ = case(3)
    (active["root"] / "TEST_FIXTURE_input.json").write_text(json.dumps(dict(
        snapshot=snapshot, bundle=bundle.model_dump(mode="json"))))
    return active, source["child"]


SETUP = r'''
from types import SimpleNamespace
from scripts import active_business_gateway_v1 as g
from scripts import active_service_host_v1 as host
from scripts import active_runtime_v1 as builder
from scripts import business_authority_v1 as a
from scripts import codex_workspace as w
from scripts.codex_runtime_recovery_v2 import reference
import time
case_name=CASE
with g._control():
 owner=a.Authority(Path(sys.argv[2])/'authority.sqlite3','TEST_FIXTURE_ACTIVE')
 state=Path(sys.argv[2])/'state'/case_name;state.mkdir()
 reports=state/'reports';reports.mkdir()
 database=state/'TEST_FIXTURE.sqlite3';database.touch()
 config=state/'TEST_FIXTURE_config.json';config.write_text('{}')
 registry=Path(sys.argv[2])/'registry'
 registration=registry/(case_name+'.json');registration.write_text('{}')
 policy={'roots':[{'name':'state','path':str(state),'role':'runtime'}],
         'target_root':'state','expected_growth_bytes':1024,'min_free_bytes':0,'max_managed_bytes':64*1024**2}
 profile={'schema_version':w.PROFILE_VERSION,'name':case_name,'backend':'premarket',
   'state_root':str(state),'report_root':str(reports),'source_root':sys.argv[1],
   'python':sys.executable,'config':str(config),'storage':policy}
 def checked_execution():
  assert owner.active_epoch()['epoch_id']=='TEST_FIXTURE_epoch'
  builder.verify(sys.argv[1])
 execution=SimpleNamespace(authority=owner,epoch_id='TEST_FIXTURE_epoch',profile=profile,
   registry_ref=reference(registration),registry_root=registry,check_binding=checked_execution)
 api={k:v for k,v in profile.items() if k not in {'source_root','python','config'}}
 api.update(name=case_name+'-api',backend='api',base_url='http://127.0.0.1:'+str(globals().get('TEST_PORT',9999)),db_path=str(database))
 before=list(database.parent.iterdir())
 descriptor=host.plan_host(execution,api,authorization_id='TEST_FIXTURE_AUTHORIZED')
 assert list(database.parent.iterdir())==before
 runtime=host.ApiRuntime(execution,descriptor)
os.environ['STOCKFISH_AUTHORITY_DATABASE']=str(owner.path)
os.environ['STOCKFISH_AUTHORITY_WORKSPACE']=owner.workspace_id
g.install()
if globals().get('SEED_LEGACY'):
 with g._control():
  seed=owner.claim({'operation':'MAINTENANCE','subject':'TEST_FIXTURE_PREEXISTING_DB'},epoch_id='TEST_FIXTURE_epoch')
  owner.start(seed);seed_cap=owner.derive_capability(seed,['storage'])
  legacy=owner.claim({'operation':'CORE_COMPUTE','input_id':'TEST_FIXTURE_LEGACY_INPUT','strategy_id':'SHORT_HORIZON_STRENGTH_R2'},epoch_id='TEST_FIXTURE_epoch')
  owner.finish(legacy,success=False,budget_known=False)
 seed_context=g._CURRENT.set(dict(owner=owner,claim=seed,capability=seed_cap))
 try:
  from stockfish_core.storage import SQLiteStore
  seed_store=SQLiteStore(database)
  with seed_store.transaction() as db:
   db.execute("INSERT INTO jobs(job_id,kind,state,payload,data,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
    ('TEST_FIXTURE_legacy','strategy_experiments','QUEUED',json.dumps({'input_id':'TEST_FIXTURE_LEGACY_INPUT','strategy_id':'SHORT_HORIZON_STRENGTH_R2'}),'{}','2026-01-01','2026-01-01'))
  with g._control():owner.finish(seed,success=True,receipt={'TEST_FIXTURE':'preexisting database'})
 finally:g._CURRENT.reset(seed_context)
app=host.bootstrap_app(runtime,authorization_id='TEST_FIXTURE_AUTHORIZED',authorize_bootstrap=True,allow_test_fixture=True)
core=app.extensions['stockfish_core'];client=app.test_client()
def claim(operation,**parameters):
 with g._control():
  c=owner.claim(dict(operation=operation,**parameters),epoch_id='TEST_FIXTURE_epoch')
  owner.start(c)
  cap=owner.derive_capability(c,sorted(a.ALLOWED_STAGES[operation]))
 return c,cap
def headers(c,cap):
 return {'X-Stockfish-Worker-Capability':cap,'X-Stockfish-Business-Id':c['business_id']}
def wait(job_id):
 deadline=time.monotonic()+30
 while time.monotonic()<deadline:
  job=core.jobs.get(job_id)
  business=core.jobs._authority_contexts[job_id]['claim']['business_id']
  with g._control():
   terminal=owner.inspect(business);status=terminal['state']
  if job['state'] in {'SUCCEEDED','FAILED','INTERRUPTED','CANCELLED'} and status not in a.ACTIVE:
   from scripts.durable_stage_metrics_v1 import audit_journal
   from scripts import codex_p0 as io
   import hashlib
   receipt=terminal['receipt']
   audit=audit_journal(receipt['journal_directory'],expected_identity=terminal)
   assert audit['status']==('COMPLETE' if job['state']=='SUCCEEDED' else 'FAILED'),audit
   ref=receipt['writer_receipt_reference'];io.verify_snapshots([ref])
   raw=Path(ref['path']).read_bytes()
   assert hashlib.sha256(raw).hexdigest()==ref['sha256']
   assert json.loads(raw)==receipt['writer_receipt']
   assert receipt['sql_returned_read_bytes'] is None and receipt['kernel_io_bytes'] is None
   assert receipt['performance_measurement_complete'] is False
   assert receipt['process_measurement_scope']=='PROCESS_SHARED_WITH_OTHER_SERVICE_THREADS'
   return job
  time.sleep(.01)
 raise AssertionError('TEST_FIXTURE job/authority completion timeout')
'''


def test_host_bootstrap_revokes_token_and_queries_use_real_readonly_connections(active_host):
    active, child = active_host
    result = child(active, "CASE='TEST_FIXTURE_host_read'\n" + SETUP + r'''
try:
 assert g._CURRENT.get() is None
 claim0=app.extensions['stockfish_active_host']['bootstrap_claim']
 with g._control():
  saved=owner.inspect(claim0['business_id'])
  assert saved['state']=='RECORDED' and saved['receipt']['listener_started'] is False
  with owner._connect(readonly=True) as db:
   assert db.execute('SELECT COUNT(*) FROM capabilities WHERE business_id=? AND revoked=0',(claim0['business_id'],)).fetchone()[0]==0
 assert client.get('/health/live').status_code==200
 assert client.get('/api/jobs').status_code==200
 assert client.get('/api/strategy-experiments/specs').status_code==200
 assert core.store.readiness()['status']=='healthy'
 assert core.store.health()['status']=='healthy',core.store.health()
 with core.store.transaction(write=False) as db:
  assert db.execute('PRAGMA query_only').fetchone()[0]==1
  try:db.execute("INSERT INTO documents VALUES ('x','y','{}','z','now')")
  except Exception:pass
  else:raise AssertionError('read connector accepted mutation')
 try:core.store.put_document('TEST_FIXTURE','forbidden',{})
 except ValueError:pass
 else:raise AssertionError('unclaimed store write succeeded')
 try:host.bootstrap_app(runtime,authorization_id='TEST_FIXTURE_AUTHORIZED',authorize_bootstrap=True)
 except ValueError as error:assert 'ALREADY_EXECUTED' in str(error)
 else:raise AssertionError('bootstrap repeated')
 assert core.jobs._futures=={}
 os.environ['STOCKFISH_WORKER_CAPABILITY']='TEST_FIXTURE_NOT_A_VALID_TOKEN'
 try:
  try:host.bootstrap_app(runtime,authorization_id='TEST_FIXTURE_AUTHORIZED',authorize_bootstrap=True)
  except ValueError as error:assert 'INHERITED_BUSINESS_CAPABILITY' in str(error)
  else:raise AssertionError('service inherited worker authority')
 finally:del os.environ['STOCKFISH_WORKER_CAPABILITY']
 # Even a valid business capability cannot silently follow a replaced DB inode.
 changed,changed_cap=claim('MAINTENANCE',subject='TEST_FIXTURE_DATABASE_REPLACEMENT')
 context=g._CURRENT.set(dict(owner=owner,claim=changed,capability=changed_cap))
 backup=database.with_suffix('.original')
 try:
  with g._control():
   database.rename(backup);database.write_bytes(backup.read_bytes())
  try:core.store.put_document('TEST_FIXTURE','replaced_database',{})
  except ValueError as error:assert 'PHYSICAL_IDENTITY_CHANGED' in str(error)
  else:raise AssertionError('valid token followed replaced database')
 finally:
  with g._control():
   database.unlink();backup.rename(database)
   owner.finish(changed,success=True,receipt={'TEST_FIXTURE':'inode change blocked'})
  g._CURRENT.reset(context)
 print('TEST_FIXTURE_HOST_READ_ONLY_OK')
finally:host.close_app(app)
''')
    assert result.returncode == 0, result.stderr
    assert "TEST_FIXTURE_HOST_READ_ONLY_OK" in result.stdout


def test_actual_core_and_outcome_routes_finish_same_authority_and_reject_cross_token(active_host):
    active, child = active_host
    result = child(active, "CASE='TEST_FIXTURE_host_business'\n" + SETUP + r'''
try:
 # Fixture ingestion is an explicit storage-only administration operation.
 c,cap=claim('MAINTENANCE',subject='TEST_FIXTURE_INPUT_INGESTION')
 context=g._CURRENT.set(dict(owner=owner,claim=c,capability=cap))
 try:
  from stockfish_core.yaogu.contracts import MarketInput,digest
  fixture=json.loads((Path(sys.argv[2])/'TEST_FIXTURE_input.json').read_bytes())
  bundle=MarketInput.model_validate(fixture['bundle'])
  input_id=core.candidates.register_input(bundle,activate=False)
  snapshot=fixture['snapshot'];snapshot['snapshot_hash']=digest(snapshot)
  run_id=snapshot['identity']['run_id']
  core.store.put_document('daily_research_snapshots',run_id,snapshot)
  core.store.put_document('strategy_experiment_summaries',run_id,{'run_id':run_id,
    'results':{'STAGE_AWARE_MOMENTUM_V2':{}},'coverage_type':'TEST_FIXTURE'})
  with g._control():owner.finish(c,success=True,receipt={'TEST_FIXTURE':True})
 finally:g._CURRENT.reset(context)
 strategy='SHORT_HORIZON_STRENGTH_R2'
 c,cap=claim('CORE_COMPUTE',input_id=input_id,strategy_id=strategy)
 response=client.post('/api/strategy-experiments/run',json={'input_id':input_id,'strategy_id':strategy},headers=headers(c,cap))
 assert response.status_code==202,response.json
 job=wait(response.json['job_id']);assert job['state']=='SUCCEEDED',job
 with g._control():assert owner.inspect(c['business_id'])['state']=='RECORDED'
 assert core.store.get_document('strategy_experiment_summaries',job['result']['run_id']) is not None
 out,out_cap=claim('OUTCOME',input_id=input_id,run_id=run_id)
 wrong=client.post('/api/strategy-experiments/run',json={'input_id':input_id,'strategy_id':strategy},headers=headers(out,out_cap))
 assert wrong.status_code==400 and wrong.json['error']=='INVALID_REQUEST',wrong.json
 response=client.post('/api/daily-research/runs/'+run_id+'/outcomes',json={'input_id':input_id},headers=headers(out,out_cap))
 assert response.status_code==202,response.json
 job=wait(response.json['job_id']);assert job['state']=='SUCCEEDED',job
 with g._control():assert owner.inspect(out['business_id'])['state']=='RECORDED'
 assert client.get('/api/jobs/'+job['job_id']).status_code==200
 print('TEST_FIXTURE_ACTUAL_CORE_AND_OUTCOME_SUCCEEDED')
finally:host.close_app(app)
''')
    assert result.returncode == 0, result.stderr
    assert "TEST_FIXTURE_ACTUAL_CORE_AND_OUTCOME_SUCCEEDED" in result.stdout


def test_other_business_cannot_start_queued_job_and_failed_job_stays_blocked(active_host):
    active, child = active_host
    result = child(active, "CASE='TEST_FIXTURE_host_failure'\n" + SETUP + r'''
try:
 a1,cap1=claim('CORE_COMPUTE',input_id='TEST_FIXTURE_INPUT_A',strategy_id='STAGE_AWARE_MOMENTUM_V2')
 b,capb=claim('CORE_COMPUTE',input_id='TEST_FIXTURE_INPUT_B',strategy_id='STAGE_AWARE_MOMENTUM_V2')
 context=g._CURRENT.set(dict(owner=owner,claim=a1,capability=cap1))
 try:job=core.jobs.create('strategy_experiments',{'input_id':'TEST_FIXTURE_INPUT_A','strategy_id':'STAGE_AWARE_MOMENTUM_V2'})
 finally:g._CURRENT.reset(context)
 context=g._CURRENT.set(dict(owner=owner,claim=b,capability=capb))
 try:
  try:core.jobs.start(job['job_id'])
  except ValueError as error:assert 'THIS_ATTEMPT' in str(error)
  else:raise AssertionError('wrong business started queued job')
  assert core.jobs.get(job['job_id'])['state']=='QUEUED'
  with g._control():owner.finish(b,success=False,budget_known=False)
 finally:g._CURRENT.reset(context)
 context=g._CURRENT.set(dict(owner=owner,claim=a1,capability=cap1))
 try:core.jobs.start(job['job_id'])
 finally:g._CURRENT.reset(context)
 final=wait(job['job_id']);assert final['state']=='FAILED',final
 with g._control():
  assert owner.inspect(a1['business_id'])['state']=='FAILED_OR_UNKNOWN'
  try:owner.claim(a1['business'],epoch_id='TEST_FIXTURE_epoch',request_metadata={'request_key':'changed','engine':'shared','entry_version':'new'})
  except ValueError as error:assert 'RECONCILE_NO_RETRY' in str(error)
  else:raise AssertionError('failed business retried')
 print('TEST_FIXTURE_CROSS_JOB_AND_FAILURE_BLOCKED')
finally:host.close_app(app)
''')
    assert result.returncode == 0, result.stderr
    assert "TEST_FIXTURE_CROSS_JOB_AND_FAILURE_BLOCKED" in result.stdout


def test_bootstrap_only_marks_old_jobs_interrupted_and_preserves_failed_authority(active_host):
    active, child = active_host
    result = child(active, "CASE='TEST_FIXTURE_host_restart'\nSEED_LEGACY=True\n" + SETUP + r'''
try:
 job=core.jobs.get('TEST_FIXTURE_legacy')
 assert job['state']=='INTERRUPTED' and job['attempt']==1 and job['error']['code']=='PROCESS_RESTART',job
 assert not core.jobs._futures
 with g._control():
  saved=owner.inspect(legacy['business_id'])
  assert saved['state']=='FAILED_OR_UNKNOWN' and not saved['budget_known']
  try:owner.claim(legacy['business'],epoch_id='TEST_FIXTURE_epoch',request_metadata={'request_key':'new'})
  except ValueError as error:assert 'RECONCILE_NO_RETRY' in str(error)
  else:raise AssertionError('bootstrap erased prior failure')
 assert core.store.get_document('strategy_experiment_summaries','TEST_FIXTURE_legacy') is None
 print('TEST_FIXTURE_RESTART_NO_RETRY')
finally:host.close_app(app)
''')
    assert result.returncode == 0, result.stderr
    assert "TEST_FIXTURE_RESTART_NO_RETRY" in result.stdout


def test_failed_bootstrap_cannot_be_renamed_reauthorized_or_moved_to_new_epoch(tmp_path_factory):
    helpers=runpy.run_path(str(Path(__file__).with_name("test_active_runtime_v1.py")))
    active=helpers["active"].__wrapped__(tmp_path_factory)
    result=helpers["child"](active,"CASE='TEST_FIXTURE_host_boot_failure'\n"+SETUP+r'''
host.close_app(app)
with g._control():
 next_descriptor=host.plan_host(execution,api,authorization_id='TEST_FIXTURE_RESTART')
 assert next_descriptor['maintenance_sequence']==2
 assert next_descriptor['bootstrap_parent']['attempt_id']==app.extensions['stockfish_active_host']['bootstrap_claim']['attempt_id']
 next_runtime=host.ApiRuntime(execution,next_descriptor)
 database.write_bytes(b'TEST_FIXTURE intentionally invalid SQLite carrier')
try:host.bootstrap_app(next_runtime,authorization_id='TEST_FIXTURE_RESTART',authorize_bootstrap=True,allow_test_fixture=True)
except Exception:pass
else:raise AssertionError('corrupt carrier bootstrap unexpectedly succeeded')
with g._control():
 fixed_id=a.business_id(owner.workspace_id,host._business())
 failed=owner.inspect(fixed_id)
 assert failed['state']=='FAILED_OR_UNKNOWN' and not failed['budget_known']
 try:runtime.check_binding()
 except ValueError as error:assert 'SERVICE_GENERATION_NOT_CURRENT' in str(error)
 else:raise AssertionError('old host stayed active after a newer maintenance attempt')
 for authorization in ('TEST_FIXTURE_changed_authorization','TEST_FIXTURE_another'):
  renamed={**api,'name':authorization}
  try:host.plan_host(execution,renamed,authorization_id=authorization)
  except ValueError as error:assert 'RECONCILE_NO_RETRY' in str(error)
  else:raise AssertionError('bootstrap failure bypassed by name/authorization')
 with owner._connect() as db:db.execute("UPDATE epochs SET epoch_id='TEST_FIXTURE_next_epoch' WHERE epoch_id='TEST_FIXTURE_epoch'")
 newer=SimpleNamespace(**execution.__dict__)
 newer.epoch_id='TEST_FIXTURE_next_epoch';newer.check_binding=lambda:builder.verify(sys.argv[1])
 try:host.plan_host(newer,{**api,'name':'TEST_FIXTURE_new_epoch_api'},authorization_id='TEST_FIXTURE_new_epoch_authorization')
 except ValueError as error:assert 'RECONCILE_NO_RETRY' in str(error)
 else:raise AssertionError('bootstrap failure bypassed by new active epoch')
 assert owner.inspect(fixed_id)['attempt_id']==failed['attempt_id']
print('TEST_FIXTURE_BOOTSTRAP_FAILURE_ALIAS_BLOCKED')
''')
    assert result.returncode==0,result.stderr
    assert "TEST_FIXTURE_BOOTSTRAP_FAILURE_ALIAS_BLOCKED" in result.stdout


def test_bootstrap_capability_failure_records_terminal_before_service_construction(tmp_path_factory):
    helpers=runpy.run_path(str(Path(__file__).with_name("test_active_runtime_v1.py")))
    active=helpers["active"].__wrapped__(tmp_path_factory)
    result=helpers["child"](active,"CASE='TEST_FIXTURE_host_capability_failure'\n"+SETUP+r'''
host.close_app(app)
with g._control():
 next_descriptor=host.plan_host(execution,api,authorization_id='TEST_FIXTURE_NEXT_BOOTSTRAP')
 next_runtime=host.ApiRuntime(execution,next_descriptor)
 original=owner.derive_capability
 def unavailable(*args,**kwargs):raise RuntimeError('TEST_FIXTURE capability publication failed')
 owner.derive_capability=unavailable
try:
 try:host.bootstrap_app(next_runtime,authorization_id='TEST_FIXTURE_NEXT_BOOTSTRAP',authorize_bootstrap=True,allow_test_fixture=True)
 except RuntimeError as error:assert str(error)=='TEST_FIXTURE capability publication failed'
 else:raise AssertionError('bootstrap capability fault hidden')
finally:
 owner.derive_capability=original
with g._control():
 saved=owner.inspect(a.business_id(owner.workspace_id,host._business()))
 assert saved['state']=='FAILED_OR_UNKNOWN' and not saved['budget_known'],saved
 assert saved['receipt'] is None and not next_runtime.service_started
 with owner._connect(readonly=True) as db:
  assert db.execute('SELECT COUNT(*) FROM capabilities WHERE attempt_id=?',(saved['attempt_id'],)).fetchone()[0]==0
  assert db.execute('SELECT COUNT(*) FROM effects WHERE attempt_id=?',(saved['attempt_id'],)).fetchone()[0]==0
 try:host.plan_host(execution,{**api,'name':'TEST_FIXTURE_new_name'},authorization_id='TEST_FIXTURE_changed')
 except ValueError as error:assert 'RECONCILE_NO_RETRY' in str(error)
 else:raise AssertionError('capability failure was bypassed by another host plan')
print('TEST_FIXTURE_BOOTSTRAP_CAPABILITY_FAILURE_TERMINAL')
''')
    assert result.returncode==0,result.stderr
    assert "TEST_FIXTURE_BOOTSTRAP_CAPABILITY_FAILURE_TERMINAL" in result.stdout


HTTP_SERVER = r'''
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit,parse_qs
import threading
POSTS=[];CONNECTIONS=[]
class FixtureHandler(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*args):pass
 def do_GET(self):
  CONNECTIONS.append(id(self.connection))
  if MODE=='legacy':
   status,content,extra=404,b'{}',{}
  elif MODE=='forged':
   nonce=parse_qs(urlsplit(self.path).query)['nonce'][0]
   content=json.dumps({**host._attestation_fields(runtime,nonce),'signature':'0'*64}).encode()
   status,extra=200,{}
  else:
   answer=app.test_client().get(self.path,headers=dict(self.headers))
   status,content,extra=answer.status_code,answer.data,{}
  self.send_response(status);self.send_header('Content-Type','application/json')
  self.send_header('Content-Length',str(len(content)))
  if MODE=='close':self.send_header('Connection','close');self.close_connection=True
  self.end_headers();self.wfile.write(content)
 def do_POST(self):
  POSTS.append(self.path);CONNECTIONS.append(id(self.connection))
  body=self.rfile.read(int(self.headers.get('Content-Length','0')))
  response=app.test_client().post(self.path,data=body,headers=dict(self.headers))
  self.send_response(response.status_code);self.send_header('Content-Type','application/json')
  self.send_header('Content-Length',str(len(response.data)));self.end_headers();self.wfile.write(response.data)
server=ThreadingHTTPServer(('127.0.0.1',0),FixtureHandler)
TEST_PORT=server.server_address[1]
server_thread=threading.Thread(target=server.serve_forever,daemon=True);server_thread.start()
'''


@pytest.mark.parametrize("mode",["legacy","forged","close"])
def test_original_client_never_posts_to_legacy_forged_or_reconnected_service(active_host,mode):
    active,child=active_host
    result=child(active,"CASE='TEST_FIXTURE_attestation_"+mode+"'\nMODE="+repr(mode)+"\n"+HTTP_SERVER+SETUP+r'''
try:
 from scripts.codex_chat import Client
 from scripts.codex_research import Client as HistoricalClient
 c,cap=claim('CORE_COMPUTE',input_id='TEST_FIXTURE_HTTP_'+MODE,strategy_id='SHORT_HORIZON_STRENGTH_R2')
 context=g._CURRENT.set(dict(owner=owner,claim=c,capability=cap))
 caller=SimpleNamespace(runtime=runtime,base_url=runtime.profile['base_url'],timeout=5,key='')
 try:
  for actual_client in (Client,HistoricalClient):
   try:actual_client.raw(caller,'/api/strategy-experiments/run',{'input_id':'TEST_FIXTURE_HTTP_'+MODE,'strategy_id':'SHORT_HORIZON_STRENGTH_R2'})
   except ValueError as error:assert str(error).startswith('HOST_'),str(error)
   else:raise AssertionError('unattested or closed service accepted POST')
 finally:
  with g._control():owner.finish(c,success=False,budget_known=False)
  g._CURRENT.reset(context)
 assert POSTS==[],POSTS
 assert len(CONNECTIONS)==2,CONNECTIONS
 print('TEST_FIXTURE_LEGACY_SERVER_POSTS_ZERO')
finally:
 host.close_app(app);server.shutdown();server.server_close();server_thread.join(timeout=3)
''')
    assert result.returncode==0,result.stderr
    assert "TEST_FIXTURE_LEGACY_SERVER_POSTS_ZERO" in result.stdout


def test_original_client_posts_over_attested_same_socket_to_real_flask_job(active_host):
    active,child=active_host
    result=child(active,"CASE='TEST_FIXTURE_attested_http_positive'\nMODE='valid'\n"+HTTP_SERVER+SETUP+r'''
try:
 from scripts.codex_chat import Client
 from stockfish_core.yaogu.contracts import MarketInput
 fixture=json.loads((Path(sys.argv[2])/'TEST_FIXTURE_input.json').read_bytes())
 c,cap=claim('MAINTENANCE',subject='TEST_FIXTURE_HTTP_INPUT_INGESTION')
 context=g._CURRENT.set(dict(owner=owner,claim=c,capability=cap))
 try:
  input_id=core.candidates.register_input(MarketInput.model_validate(fixture['bundle']),activate=False)
  with g._control():owner.finish(c,success=True,receipt={'TEST_FIXTURE':True})
 finally:g._CURRENT.reset(context)
 strategy='SHORT_HORIZON_STRENGTH_R1'
 c,cap=claim('CORE_COMPUTE',input_id=input_id,strategy_id=strategy)
 context=g._CURRENT.set(dict(owner=owner,claim=c,capability=cap))
 caller=SimpleNamespace(runtime=runtime,base_url=runtime.profile['base_url'],timeout=15,key='')
 try:raw=Client.raw(caller,'/api/strategy-experiments/run',{'input_id':input_id,'strategy_id':strategy})
 finally:g._CURRENT.reset(context)
 result=json.loads(raw)
 job=wait(result['job_id']);assert job['state']=='SUCCEEDED',job
 assert POSTS==['/api/strategy-experiments/run'],POSTS
 assert len(CONNECTIONS)==2 and CONNECTIONS[0]==CONNECTIONS[1],CONNECTIONS
 with g._control():assert owner.inspect(c['business_id'])['state']=='RECORDED'
 print('TEST_FIXTURE_SIGNED_SAME_SOCKET_CORE_SUCCEEDED')
finally:
 host.close_app(app);server.shutdown();server.server_close();server_thread.join(timeout=3)
''')
    assert result.returncode==0,result.stderr
    assert "TEST_FIXTURE_SIGNED_SAME_SOCKET_CORE_SUCCEEDED" in result.stdout
