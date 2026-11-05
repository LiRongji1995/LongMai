"""TEST_FIXTURE actual active SQLite JobService thread and metrics failures.

The small handlers exercise job lifecycle only, not investment correctness.
The service-host suite separately invokes the real CORE and OUTCOME handlers.
"""
import pytest
from tests.unit.test_active_service_host_v1 import active_host, SETUP


@pytest.mark.parametrize("mode", ["success", "failed", "metrics_failed"])
def test_actual_job_thread_retains_terminal_and_durable_metrics(active_host, mode):
    active, child = active_host
    result = child(active, "CASE='TEST_FIXTURE_job_metrics_"+mode+"'\nMODE="+repr(mode)+"\n"+SETUP+r'''
from scripts import durable_stage_metrics_v1 as metrics
from scripts import codex_delivery_audit_v1 as audit
try:
 c,cap=claim('CORE_COMPUTE',input_id='TEST_FIXTURE_'+MODE,strategy_id='STAGE_AWARE_MOMENTUM_V2')
 def handler(context):
  time.sleep(.03)
  if MODE=='failed':raise RuntimeError('TEST_FIXTURE safe synthetic error')
  return {'TEST_FIXTURE':True,'lifecycle_only':True}
 if MODE=='metrics_failed':
  def broken_finish(self,*args,**kwargs):raise OSError('TEST_FIXTURE final journal write failure')
  metrics.StageJournal.finish=broken_finish
 context=g._CURRENT.set(dict(owner=owner,claim=c,capability=cap))
 try:
  job=core.jobs.create('strategy_experiments',{'input_id':'TEST_FIXTURE_'+MODE,'strategy_id':'STAGE_AWARE_MOMENTUM_V2'})
  core.jobs.start(job['job_id'],handler)
 finally:g._CURRENT.reset(context)
 deadline=time.monotonic()+30
 while time.monotonic()<deadline:
  final=core.jobs.get(job['job_id'])
  with g._control():saved=owner.inspect(c['business_id'])
  if final['state'] in {'SUCCEEDED','FAILED'} and saved['state'] not in a.ACTIVE:break
  time.sleep(.01)
 else:raise AssertionError('TEST_FIXTURE job/authority did not terminate')
 assert final['state']==('FAILED' if MODE=='failed' else 'SUCCEEDED'),final
 assert saved['state']==('RECORDED' if MODE=='success' else 'FAILED_OR_UNKNOWN'),saved
 receipt=saved['receipt']
 assert receipt['job_id']==job['job_id']
 assert receipt['process_measurement_scope']=='PROCESS_SHARED_WITH_OTHER_SERVICE_THREADS'
 assert receipt['sql_returned_read_bytes'] is None and receipt['kernel_io_bytes'] is None
 assert receipt['performance_measurement_complete'] is False
 report=metrics.audit_journal(Path(receipt['journal_directory']),expected_identity=metrics.identity(c))
 assert report['test_fixture'] is True
 assert report['status']=={'success':'COMPLETE','failed':'FAILED','metrics_failed':'UNKNOWN'}[MODE],report
 assert report['stages'][0]['event']==('STAGE_FAILED' if MODE=='failed' else 'STAGE_END')
 assert report['stages'][0]['returned_read_bytes'] is None
 assert report['stages'][0]['wall_seconds']>=.03
 if MODE!='metrics_failed':
  reader=audit._Reader(1024**2,10)
  writer,_=reader.read(receipt['writer_receipt_reference']['path'],expected=receipt['writer_receipt_reference'],control=True)
  reader.terminal()
  assert writer==receipt['writer_receipt'] and writer['terminal_event_hash']==report['terminal_event_hash']
  assert writer['publication_cost']['fsync_calls']>0
  assert receipt['writer_receipt_verification_read_bytes']>0
 else:
  assert receipt['metrics_terminal_error_type']=='OSError'
  assert not (Path(receipt['journal_directory']).parent/'writer-receipt.json').exists()
  assert final['result']=={'TEST_FIXTURE':True,'lifecycle_only':True}
 if MODE=='failed':assert final['error']['code']=='WORKER_FAILED'
 if MODE!='success':
  with g._control():
   try:owner.claim(c['business'],epoch_id='TEST_FIXTURE_epoch',request_metadata={'request_key':'renamed'})
   except ValueError as error:assert 'RECONCILE_NO_RETRY' in str(error)
   else:raise AssertionError('unknown/failed business retried')
 print('TEST_FIXTURE_JOB_METRICS_'+MODE+'_OK')
finally:host.close_app(app)
''')
    assert result.returncode == 0, result.stderr
    assert "TEST_FIXTURE_JOB_METRICS_"+mode+"_OK" in result.stdout
