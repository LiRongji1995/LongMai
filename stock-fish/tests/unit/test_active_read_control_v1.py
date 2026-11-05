"""TEST_FIXTURE fixed active read dispatch, original SQLite authority, no collection."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from scripts import active_read_control_v1 as read
from scripts import codex_active_v1 as driver
from scripts import codex_runtime_recovery_v1 as legacy
from scripts import codex_runtime_recovery_v2 as recovery
from scripts import daily_delivery_contract_v1 as delivery
from tests.unit.test_active_runtime_v1 import active, child
from tests.unit.test_active_source_bootstrap_v1 import (
    TEST_FIXTURE_AUDIT, sandbox_command,
    test_empty_source_actual_daily_entry_native_worker_retains_unknown_and_binds_final as native_delivery)
from tests.unit.test_active_environment_acceptance_v1 import prepared, accepted


def options(operation, root):
    base=['--source-root',str(root/'active'),'--runtime','TEST_FIXTURE_read',
          '--registry-root',str(root/'registry'),'--workspace-id','TEST_FIXTURE_read']
    if operation=='delivery-audit':
        return base+['--authority-db',str(root/'authority.sqlite3'),'--business-id','TEST_FIXTURE_unknown',
                     '--job',str(root/'job'),'--journal',str(root/'journal')]
    return base+['--authority-database',str(root/'authority.sqlite3'),'--protocol',str(root/'protocol.json'),
                '--protocol-sha256','a'*64,'--requests',str(root/'requests.json'),'--requests-sha256','b'*64]


@pytest.mark.parametrize('operation',read.OPERATIONS)
def test_read_cli_requires_explicit_binding_and_rejects_duplicate_or_mutation_flags(tmp_path,operation):
    argv=options(operation,tmp_path)
    assert read.arguments(operation,argv).runtime=='TEST_FIXTURE_read'
    with pytest.raises(ValueError,match='DUPLICATE'):
        read.arguments(operation,argv+['--runtime','renamed'])
    with pytest.raises(SystemExit):read.arguments(operation,argv+['--authorize-freeze'])
    with pytest.raises(ValueError,match='FINITE'):
        read.arguments(operation,argv+['--max-seconds','nan'])
    with pytest.raises(ValueError,match='OPERATION_FORBIDDEN'):
        read.arguments('freeze',argv)


def test_public_routes_only_fixed_read_dispatch_preserving_plan_and_freeze(monkeypatch):
    calls=[]
    original=driver._module
    monkeypatch.setattr(driver,'_module',lambda name:SimpleNamespace(dispatch=lambda operation,args:
        calls.append((operation,args)) or 7) if name=='active_read_control_v1' else original(name))
    assert driver.main(['delivery-audit','TEST_FIXTURE'])==7
    assert driver.main(['delivery-protocol','inspect-sequence','TEST_FIXTURE'])==7
    assert calls==[('delivery-audit',['TEST_FIXTURE']),('inspect-sequence',['TEST_FIXTURE'])]



def test_parent_report_cannot_be_published_inside_control_source_or_active_archive(tmp_path):
    for name in ('active','registry','job','journal'):
        (tmp_path/name).mkdir()
    args=read.arguments('delivery-audit',options('delivery-audit',tmp_path))
    for output in (Path(read.__file__).parent/'TEST_FIXTURE_FORBIDDEN_REPORT.json',
                   tmp_path/'active'/'report.json',tmp_path/'job'/'report.json'):
        args.output=output
        with pytest.raises(ValueError,match='PROTECTED'):
            read._output_path(args,'delivery-audit',None)
        assert not output.exists()


def test_generated_readonly_authority_never_exposes_control_or_writable_sqlite(active):
    code=TEST_FIXTURE_AUDIT+r'''
from scripts import active_business_gateway_v1 as g
from scripts import active_read_control_v1 as read
from scripts.business_authority_v1 import Authority
read.install_read_boundary(Path(sys.argv[2])/'authority.sqlite3')
owner=g.readonly_authority()
assert type(owner) is Authority and not g._CONTROL.get()
assert owner.active_epoch()['epoch_id']=='TEST_FIXTURE_epoch'
with owner._connect(readonly=True) as db:
 assert not g._CONTROL.get()
 assert db.execute('SELECT COUNT(*) FROM requests').fetchone()[0]==0
 for sql in ['CREATE TABLE forbidden(value)',"ATTACH DATABASE ':memory:' AS other",'PRAGMA query_only=OFF',
             "INSERT INTO meta VALUES ('TEST_FIXTURE_FORBIDDEN','x')"]:
  try:db.execute(sql)
  except Exception:pass
  else:raise AssertionError('read-only SQL allowed mutation: '+sql)
for action in [lambda:owner.claim({'operation':'CORE_COMPUTE','run_ids':['TEST_FIXTURE']}),
               lambda:owner._connect().__enter__(),
               lambda:Path(sys.argv[2],'TEST_FIXTURE_FORBIDDEN').write_text('x'),
               lambda:__import__('socket').getaddrinfo('TEST_FIXTURE.invalid',443),
               lambda:__import__('subprocess').run([sys.executable,'--version']),
               lambda:g.readonly_authority(Path(sys.argv[2])/'foreign.sqlite3')]:
 try:action()
 except Exception:pass
 else:raise AssertionError('read boundary allowed an ungranted effect')
assert owner.inspect('TEST_FIXTURE_missing') is None
assert not Path(sys.argv[2],'TEST_FIXTURE_FORBIDDEN').exists()
print('TEST_FIXTURE_READ_ONLY_NATIVE_AUTHORITY_OK')
'''
    before=active['owner'].path.read_bytes()
    result=child(active,code)
    assert result.returncode==0,result.stderr
    assert 'TEST_FIXTURE_READ_ONLY_NATIVE_AUTHORITY_OK' in result.stdout
    assert active['owner'].path.read_bytes()==before


def test_actual_v2_registration_cli_returns_twenty_unknown_days_without_mutation(prepared,accepted):
    # Actual seven-step environment receipt and real recovery, not a fake loader.
    if accepted is None:
        assert sys.platform!='darwin'
        return  # The environment contract itself proves unsupported OS fails closed.
    from scripts import codex_workspace as workspace
    from datetime import date,timedelta
    c=prepared;root=c['root'];owner=c['owner'];registry=root/'registry';registry.mkdir()
    reports=root/'reports';reports.mkdir();config=root/'TEST_FIXTURE_config.json'
    config.write_text(json.dumps(dict(state_root=str(root/'state'))))
    profile={**c['profile'],'schema_version':workspace.PROFILE_VERSION,'name':'TEST_FIXTURE_read',
        'backend':'premarket','state_root':str(root/'state'),'report_root':str(reports),'config':str(config),
        'storage':dict(roots=[dict(name='fixture',path=str(root),role='runtime')],target_root='fixture',
            expected_growth_bytes=1024,min_free_bytes=0,max_managed_bytes=256*1024**2)}
    plan=recovery.plan_recovery(owner,[registry],profile,accepted,purpose='TEST_FIXTURE fixed read-only audit')
    recovery.apply_recovery(owner,plan,activation_authorization='TEST_FIXTURE_AUTHORIZED_READ_INTEGRATION')
    recovery.load_runtime(profile['name'],registry).check_binding()
    start=date(2026,11,1)
    rows=[dict(calendar_date=str(start+timedelta(days=n)),
        is_trading_day='1' if (start+timedelta(days=n)).weekday()<5 else '0') for n in range(70)]
    calendar_path=root/'TEST_FIXTURE_calendar.json';calendar_path.write_text(json.dumps({'rows':rows}))
    protocol=delivery.plan_protocol(rows,first_target_session='2026-11-09',strategy_id='SHORT_HORIZON_STRENGTH_R2',
        calendar_reference={**legacy.reference(calendar_path),'TEST_FIXTURE':True},session_count=20)
    pp=root/'TEST_FIXTURE_protocol.json';pp.write_text(json.dumps(protocol))
    batch=root/'TEST_FIXTURE_requests.json';batch.write_text(json.dumps({'requests':[]}))
    output=reports/'TEST_FIXTURE_read_audit.json'
    argv=['delivery-protocol','inspect-sequence','--source-root',str(c['source']),'--runtime',profile['name'],
        '--registry-root',str(registry),'--authority-database',str(owner.path),'--workspace-id',owner.workspace_id,
        '--protocol',str(pp),'--protocol-sha256',hashlib.sha256(pp.read_bytes()).hexdigest(),
        '--requests',str(batch),'--requests-sha256',hashlib.sha256(batch.read_bytes()).hexdigest(),
        '--output',str(output)]
    before=legacy.reference(owner.path)
    command=sandbox_command([sys.executable,'-I','-B',str(Path(driver.__file__)),*argv])
    result=subprocess.run(command,cwd=root,env={'PATH':os.defpath,'PYTHONDONTWRITEBYTECODE':'1'},
        capture_output=True,text=True,timeout=240)
    (root/'read-cli.stdout').write_text(result.stdout);(root/'read-cli.stderr').write_text(result.stderr)
    assert result.returncode==4,result.stdout+result.stderr
    saved=json.loads(output.read_bytes())
    assert saved['schema_version']==read.VERSION and saved['source_root']==str(c['source'])
    assert saved['result']['accepted_sessions']==0 and saved['result']['workflow_status_counts']=={'NOT_RUN':20}
    assert saved['result']['status']=='NOT_ESTABLISHED' and not saved['collection_started']
    assert legacy.reference(owner.path)==before
    with owner._connect(readonly=True) as db:assert db.execute('SELECT COUNT(*) FROM requests').fetchone()[0]==0


def test_actual_native_delivery_reaudited_in_same_active_tree_without_capability(active,tmp_path):
    native_delivery(active,tmp_path)
    root=(tmp_path/'TEST_FIXTURE_native_source').resolve();source=root/'active'
    code=TEST_FIXTURE_AUDIT+r'''
from scripts import active_business_gateway_v1 as g
from scripts import active_read_control_v1 as read
from types import SimpleNamespace
owner=g.readonly_authority()
with owner._connect(readonly=True) as db:
 rows=db.execute('SELECT business_id FROM requests').fetchall()
assert len(rows)==1
snapshot=owner.inspect(rows[0][0]);binding=snapshot['receipt']
job=Path(binding['job_final']['path']).parent
args=read.arguments('delivery-audit',['--source-root',sys.argv[1],'--runtime','TEST_FIXTURE_source',
 '--registry-root',str(Path(sys.argv[2])/'registry'),'--authority-db',str(owner.path),'--workspace-id',owner.workspace_id,
 '--business-id',snapshot['business_id'],'--job',str(job),'--journal',binding['journal_directory'],
 '--run',binding['canonical_run']['path'],'--run-sha256',binding['canonical_run']['sha256'],
 '--reauthenticate-signal','--allow-test-fixture'])
runtime=SimpleNamespace(profile={'source_root':sys.argv[1]},authority=owner,check_binding=lambda:g.readonly_authority())
read.install_read_boundary(owner.path)
code,result=read.execute_read('delivery-audit',args,runtime)
assert code==2 and result['result']['status']=='COMPLETED_WITH_UNKNOWNS'
assert result['result']['native_source_evidence']['status']=='VERIFIED'
assert result['result']['authority_terminal_bound'] and result['result']['native_process_cleanup']['verified']
assert result['result']['test_fixture'] is True and not g._CONTROL.get()
assert owner.inspect(snapshot['business_id'])==snapshot
print(json.dumps({'TEST_FIXTURE':True,'source_reverified':True,'terminal_bound':True,'no_capability':True}))
'''
    command=sandbox_command([sys.executable,'-I','-B','-c',
        'import sys,os,json\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[1])\n'+code,str(source),str(root)])
    before=(root/'authority.sqlite3').read_bytes()
    result=subprocess.run(command,cwd=root,env={'PATH':os.defpath,'PYTHONDONTWRITEBYTECODE':'1'},
        capture_output=True,text=True,timeout=180)
    (root/'read-native.stdout').write_text(result.stdout);(root/'read-native.stderr').write_text(result.stderr)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['source_reverified'] is True
    assert (root/'authority.sqlite3').read_bytes()==before
