"""TEST_FIXTURE direct native worker, authority binding and retained failures."""
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time
import pytest
from scripts import active_delivery_bridge_v1 as bridge
from scripts import daily_delivery_contract_v1 as contract
from scripts import durable_stage_metrics_v1 as metrics
from tests.unit.test_business_authority_v1 import authority
from tests.unit.test_codex_delivery_audit_v1 import evidence
from tests.unit.test_codex_operational_report_v1 import attach_run


def configured(evidence, authority):
    run,path,sha=attach_run(evidence)
    config=dict(capture={'allow_test_fixture':True},limits={
        'tree_rss_bytes':512*1024**2,'self_rss_bytes':2*1024**3,'max_seconds':10.,
        'output_bytes':4*1024**2,'supervisor_read_bytes':64*1024**2})
    evidence.final['configuration']=evidence.write(evidence.job/'configuration.json',config)
    evidence.seal()
    claim=authority.claim({'operation':'PREMARKET',**{key:run[key] for key in
        ('observation_date','target_session','strategy_id')}},epoch_id='TEST_FIXTURE_epoch')
    authority.start(claim)
    return config,claim,path,sha


def native_worker(evidence,claim,*,fail=False):
    script=evidence.root/'TEST_FIXTURE_native_delivery_worker.py'
    script.write_text("""import json,os,pathlib,signal,sys,time
sys.path.insert(0,sys.argv[1])
from scripts.durable_stage_metrics_v1 import StageJournal
from scripts.codex_sdk_tree_supervisor_v3 import validate_worker_context
from scripts.active_delivery_bridge_v1 import publish_writer_receipt,CONTROL
job=pathlib.Path(sys.argv[2]);claim=json.loads(sys.argv[3])
validate_worker_context(job/CONTROL)
j=StageJournal(job/('authority-metrics-worker-'+claim['attempt_id']),claim,test_fixture=True)
with j.stage('workflow'):
    time.sleep(.3)
    if sys.argv[4]=='FAIL': os.kill(os.getpid(),signal.SIGKILL)
    with j.stage('core'):
        pass
receipt=j.finish('COMPLETE')
publish_writer_receipt(job,receipt)
time.sleep(.3)
""")
    command=[sys.executable,'-I','-B',str(script),str(bridge.ROOT),str(evidence.job.resolve()),json.dumps(claim),'FAIL' if fail else 'COMPLETE']
    return bridge.run_supervised_worker(evidence.job.resolve(),command,source_root=bridge.ROOT,
        environment={'PATH':'/usr/bin:/bin','PYTHONDONTWRITEBYTECODE':'1'},
        source_references=[bridge.supervisor.reference(script.resolve())[1]],test_fixture=True)


def original_receipt(evidence):
    return json.loads(evidence.operation().read_text())['receipt']


@pytest.mark.parametrize('workflow_status',['COMPLETE','COMPLETED_WITH_UNKNOWNS'])
def test_native_direct_worker_bound_to_original_job_and_authority(authority,evidence,workflow_status):
    _,claim,path,sha=configured(evidence,authority)
    if workflow_status=='COMPLETED_WITH_UNKNOWNS':
        evidence.completion.update(status=workflow_status,theme_status='UNKNOWN')
        evidence.completion['phases']['theme']='UNKNOWN'
        evidence.final.update(status=workflow_status,external_worker_exit_code=2)
        evidence.seal()
    report=native_worker(evidence,claim)
    assert report['status']=='COMPLETE'
    measured=report['bridge_control_cost_receipt']
    assert measured['native_supervisor_budget_charge_bytes']==report['terminal_supervisor_returned_read_bytes']
    assert measured['returned_read_bytes_by_scope']['configuration_initial_and_terminal']==2*(evidence.job/'configuration.json').stat().st_size
    assert measured['total_read_budget_charge_bytes']>report['terminal_supervisor_returned_read_bytes']
    receipt=original_receipt(evidence)
    bound=bridge.prepare_authority_receipt(receipt,claim)
    assert bound['gateway_receipt']==receipt and bound['schema_version']==contract.VERSION+'_BINDING'
    assert bound['writer_receipt']['publication_cost']['fsync_calls']>0
    assert bound['authority_receipt_validation_resources']['returned_read_bytes']>bound['binding_audit_resources']['returned_read_bytes']>0
    assert bound['bridge_supervision_cost']['native_terminal']['terminal_supervisor_returned_read_bytes']==report['terminal_supervisor_returned_read_bytes']
    assert bound['bridge_supervision_cost']['final_self_publication_and_post_verify_cost'].startswith('UNKNOWN')
    authority.finish(claim,success=True,receipt=bound)
    result=contract.audit_delivery(authority,claim['business_id'],job=evidence.job.resolve(),
        journal_directory=Path(bound['journal_directory']),run_path=path.resolve(),run_sha256=sha)
    assert result['status']==workflow_status and result['authority_terminal_bound']
    assert result['workflow']['archived_workflow_complete'] is True
    if workflow_status=='COMPLETED_WITH_UNKNOWNS':
        assert result['workflow']['data_completeness']=='KNOWN_STAGE_GAPS'
        assert result['workflow']['phases']['theme']=='UNKNOWN'
    assert result['native_process_cleanup']['root_identity']==result['stage_metrics']['writer_identity']
    assert result['test_fixture'] and result['market_edge']=='NOT_ESTABLISHED'
    assert result['bridge_supervision_cost']==bound['bridge_supervision_cost']
    (evidence.job/bridge.SUPERVISOR_RETURN).unlink()
    with pytest.raises((ValueError,OSError)):
        contract.audit_delivery(authority,claim['business_id'],job=evidence.job.resolve(),
            journal_directory=Path(bound['journal_directory']),run_path=path.resolve(),run_sha256=sha)
    # A later native failure overrides the earlier saved completion.
    (evidence.job/bridge.CONTROL/'failure.json').write_text('{"TEST_FIXTURE":true}')
    with pytest.raises(ValueError,match='TERMINAL_FAILURE'):
        bridge.prepare_authority_receipt(receipt,claim)


def test_killed_worker_keeps_journal_tail_and_cannot_attach_success(authority,evidence):
    _,claim,_,_=configured(evidence,authority)
    report=native_worker(evidence,claim,fail=True)
    assert report['status']=='FAILED' and report['root_reaped']
    journal=evidence.job/('authority-metrics-worker-'+claim['attempt_id'])
    assert metrics.audit_journal(journal)['status']=='UNKNOWN'
    with pytest.raises(ValueError,match='METRICS_NOT_COMPLETE'):
        bridge.prepare_authority_receipt(original_receipt(evidence),claim)
    assert not (evidence.job/bridge.WORKER_RECEIPT).exists()


@pytest.mark.parametrize('bad',[None,0,-1,float('inf'),True])
def test_unbounded_budget_rejected_before_launch(authority,evidence,monkeypatch,bad):
    config,claim,_,_=configured(evidence,authority)
    config['limits']['max_seconds']=bad
    (evidence.job/'configuration.json').write_text(json.dumps(config))
    called=[]
    monkeypatch.setattr(bridge.supervisor,'run_supervised',lambda *a,**k:called.append(True))
    with pytest.raises(ValueError):
        bridge.run_supervised_worker(evidence.job.resolve(),['/bin/false'],source_root=bridge.ROOT,
            environment={},test_fixture=True)
    assert called==[] and not (evidence.job/bridge.CONTROL).exists()


def test_shared_engine_and_all_expected_sessions_survive_command(evidence):
    job=evidence.job.resolve()
    args=['_worker','--job',str(job),'--engine','shared','--observation-date','2026-10-26',
        '--target-session','2026-10-27','--planning-local-date','2026-10-27']
    outer=bridge.supervisor_command(bridge.ROOT,job,entry='shared',worker_args=args)
    inner=bridge.worker_command(bridge.ROOT,job,entry='shared',worker_args=args)
    assert outer[-len(args):]==inner[-len(args):]==args
    assert outer[1:3]==inner[1:3]==['-I','-B']
    assert outer[3:5]==inner[3:5]==['-X','pycache_prefix='+str(job/'authority-bytecode-must-remain-absent')]
    assert 'supervise' in outer and 'worker' in inner
    assert bridge.checked_worker_args('original',job)==['_worker','--job',str(job)]
    for malformed in (args+['--engine','original'],args[:-2],args+['--retry','yes']):
        with pytest.raises(ValueError): bridge.checked_worker_args('shared',job,malformed)
    with pytest.raises(ValueError): bridge.checked_worker_args('original',job,args)


def test_writer_receipt_is_never_overwritten(authority,evidence):
    _,claim,_,_=configured(evidence,authority)
    native_worker(evidence,claim)
    path=evidence.job/bridge.WORKER_RECEIPT
    original=path.read_bytes()
    with pytest.raises((ValueError,OSError)):
        bridge.publish_writer_receipt(evidence.job,json.loads(original))
    assert path.read_bytes()==original


def test_supervisor_term_runs_native_cleanup(authority,evidence):
    configured(evidence,authority)
    script=evidence.root/'TEST_FIXTURE_waiting_worker.py'
    script.write_text('import time\ntime.sleep(30)\n')
    command=[sys.executable,'-I','-B',str(script.resolve())]
    timer=threading.Timer(.6,lambda:os.kill(os.getpid(),signal.SIGTERM))
    timer.start()
    try:
        report=bridge.run_supervised_worker(evidence.job.resolve(),command,source_root=bridge.ROOT,
            environment={'PATH':'/usr/bin:/bin'},test_fixture=True)
    finally:
        timer.cancel();timer.join(timeout=2)
    assert report['status']=='FAILED' and report['root_reaped'] is True
    assert report['all_observed_descendants_gone'] is True
    assert report['cleanup']['remaining']==[]


def test_config_read_cost_alone_can_exhaust_budget_before_fork(authority,evidence,monkeypatch):
    config,_,_,_=configured(evidence,authority)
    config['limits']['supervisor_read_bytes']=1
    (evidence.job/'configuration.json').write_text(json.dumps(config))
    calls=[]
    monkeypatch.setattr(bridge.supervisor,'run_supervised',lambda *a,**k:calls.append(True))
    with pytest.raises(ValueError,match='CUMULATIVE_READ_LIMIT'):
        bridge.run_supervised_worker(evidence.job.resolve(),['/bin/false'],source_root=bridge.ROOT,
            environment={},test_fixture=True)
    assert not calls and not (evidence.job/bridge.CONTROL).exists()


def test_saved_prepublication_snapshot_cannot_replace_terminal_cost(authority,evidence):
    _,claim,_,_=configured(evidence,authority)
    native_worker(evidence,claim)
    path=evidence.job/bridge.SUPERVISOR_RETURN
    value=json.loads(path.read_text())
    value['native_terminal']['terminal_supervisor_returned_read_bytes']=0
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='TERMINAL_COST_UNDERSTATED'):
        bridge.prepare_authority_receipt(original_receipt(evidence),claim)
