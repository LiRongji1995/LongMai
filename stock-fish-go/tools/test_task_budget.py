"""TEST_FIXTURE regressions for durable budget admission; never real research."""
import copy
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import task_budget as budget


class BudgetTest(unittest.TestCase):
    def test_registered_existing_database_growth_is_not_hidden_by_unrelated_deletion(self):
        with tempfile.TemporaryDirectory(prefix='TEST_FIXTURE_existing_growth_') as tmp:
            root=pathlib.Path(tmp);ledger=root/'task/budget.json';ledger.parent.mkdir()
            database=root/'existing.sqlite3';database.write_bytes(b'x'*4096)
            baseline=database.stat()
            data={'root':str(root),'initial_project_bytes':10**9,'existing_file_growth':[
                {'path':database.name,'baseline_allocated_bytes':baseline.st_blocks*512,
                 'must_exist':True,'original_identity':[baseline.st_dev,baseline.st_ino]}]}
            database.write_bytes(b'x'*65536)
            expected=database.stat().st_blocks*512-baseline.st_blocks*512
            with patch.object(budget,'size',return_value=0):
                self.assertEqual(budget.new_storage_bytes(data,ledger,project=1),expected)
                data['existing_data_reserved_bytes']=expected+4096
                self.assertEqual(budget.new_storage_bytes(data,ledger,project=1),expected+4096)
                data['existing_data_reserved_bytes']=-1
                with self.assertRaisesRegex(ValueError,'RESERVATION'):
                    budget.new_storage_bytes(data,ledger,project=1)
                data['existing_data_reserved_bytes']=0
            replacement=root/'replacement';replacement.write_bytes(b'x'*4096);replacement.replace(database)
            with self.assertRaisesRegex(ValueError,'REPLACED'):
                budget.existing_growth_bytes(data,[])
            database.unlink()
            with self.assertRaisesRegex(ValueError,'MISSING'):
                budget.existing_growth_bytes(data,[])

    def test_growth_registration_refuses_outside_paths_and_duplicate_or_negative_baselines(self):
        with tempfile.TemporaryDirectory(prefix='TEST_FIXTURE_growth_registration_') as tmp:
            data={'root':tmp,'existing_file_growth':[{'path':'../outside','baseline_allocated_bytes':0}]}
            with self.assertRaises(ValueError):budget.existing_growth_paths(data)
            data['existing_file_growth']=[{'path':'db','baseline_allocated_bytes':-1}]
            with self.assertRaises(ValueError):budget.existing_growth_paths(data)
            data['existing_file_growth']=[{'path':'db','baseline_allocated_bytes':0}]*2
            with self.assertRaises(ValueError):budget.existing_growth_paths(data)

    def test_storage_churn_retries_complete_measurement_not_partial_subtotal(self):
        missing=subprocess.CompletedProcess(['du'],1,'999999\tTEST_FIXTURE\n',
                                            'du: TEST_FIXTURE/removed: No such file or directory\n')
        complete=subprocess.CompletedProcess(['du'],0,'64\tTEST_FIXTURE\n','')
        with patch.object(budget.subprocess,'run',side_effect=[missing,complete]) as calls:
            self.assertEqual(budget.size(pathlib.Path('TEST_FIXTURE')),65536)
            self.assertEqual(calls.call_count,2)

    def test_storage_permission_error_never_accepts_partial_or_retries(self):
        failed=subprocess.CompletedProcess(['du'],1,'1\tTEST_FIXTURE\n',
                                           'du: TEST_FIXTURE: Permission denied\n')
        with patch.object(budget.subprocess,'run',return_value=failed) as calls:
            with self.assertRaises(subprocess.CalledProcessError):
                budget.size(pathlib.Path('TEST_FIXTURE'))
            self.assertEqual(calls.call_count,1)

    def test_repeated_storage_churn_stays_unverified(self):
        failed=subprocess.CompletedProcess(['du'],1,'1\tTEST_FIXTURE\n',
                                           'du: TEST_FIXTURE/removed: No such file or directory\n')
        with patch.object(budget.subprocess,'run',return_value=failed) as calls:
            with self.assertRaises(subprocess.CalledProcessError):
                budget.size(pathlib.Path('TEST_FIXTURE'))
            self.assertEqual(calls.call_count,3)

    def ledger(self):
        return {'attempts': [], 'limits': copy.deepcopy(budget.LIMITS),
                'known_engineering_source_and_admission_seconds': 20,
                'source_check_reservation': {'seconds': 60},
                'readonly_preflight_used_seconds': 7}

    def test_resume_preserves_external_cost_and_dependency_shared_budget(self):
        data=self.ledger()
        data['attempts']=[{'kind':'dependency','elapsed_seconds':35,'reserved_seconds':100}]
        self.assertEqual(budget.used_seconds(data,'engineering'),115)
        self.assertEqual(budget.used_seconds(data,'readonly'),7)
        # Persist/reload is a real resume. Imported usage must not disappear or
        # double count just because historical summary fields are still present.
        data=json.loads(json.dumps(data))
        self.assertEqual(budget.used_seconds(data,'engineering'),115)
        budget.add_external_charge(data,'engineering',5,'next-source-check')
        self.assertEqual(budget.used_seconds(data,'engineering'),120)
        with self.assertRaisesRegex(ValueError,'ALREADY_RECORDED'):
            budget.add_external_charge(data,'engineering',5,'next-source-check')

    def test_task_time_caps_cannot_increase_and_stricter_ledger_wins(self):
        data=self.ledger()
        data['limits']['engineering']=[9000,2500,None]
        self.assertEqual(budget.effective_limits(data,'engineering'),(5400,1800,None))
        data['limits']['engineering']=[100,30,None]
        self.assertEqual(budget.effective_limits(data,'engineering'),(100,30,None))
        data['limits']['research']=[3600,1800,0]
        self.assertEqual(budget.effective_limits(data,'research'),(3600,1800,0))

    def engineering_authorization(self):
        return {'task_id':'TEST_FIXTURE_ENGINEERING_GRANT',
                'previous_total_seconds':5400,'total_seconds':6000,
                'user_instruction':'TEST_FIXTURE explicit engineering-only exception'}

    def test_explicit_engineering_grant_preserves_stricter_ledger_and_single_run_cap(self):
        data=self.ledger();data['task_id']='TEST_FIXTURE_ENGINEERING_GRANT'
        data['engineering_limit_authorization']=self.engineering_authorization()
        # Recording permission alone does not replace the durable ledger limit.
        self.assertEqual(budget.effective_limits(data,'engineering'),(5400,1800,None))
        data['limits']['engineering']=[6000,1800,None]
        self.assertEqual(budget.effective_limits(data,'engineering'),(6000,1800,None))
        data['limits']['engineering']=[9000,2500,None]
        self.assertEqual(budget.effective_limits(data,'engineering'),(6000,1800,None))
        data['limits']['engineering']=[100,30,0]
        self.assertEqual(budget.effective_limits(data,'engineering'),(100,30,0))

    def test_engineering_grant_cannot_relax_other_scope_limits(self):
        data=self.ledger();data['task_id']='TEST_FIXTURE_ENGINEERING_GRANT'
        data['engineering_limit_authorization']=self.engineering_authorization()
        for kind in ('readonly','research','online','dependency'):
            with self.subTest(kind=kind):
                data['limits'][kind]=[9000,9000,None]
                self.assertEqual(budget.effective_limits(data,kind),budget.LIMITS[kind])

    def test_engineering_grant_rejects_cross_task_and_invalid_authorization(self):
        valid=self.engineering_authorization()
        invalid=[None,[],{},dict(valid,task_id='TEST_FIXTURE_OTHER_TASK'),
                 dict(valid,previous_total_seconds=5000),dict(valid,previous_total_seconds=5400.0),
                 dict(valid,total_seconds=9000),dict(valid,total_seconds=6000.0),
                 dict(valid,user_instruction=''),dict(valid,user_instruction='  '),
                 dict(valid,user_instruction=True)]
        for authorization in invalid:
            with self.subTest(authorization=authorization):
                data=self.ledger();data['task_id']='TEST_FIXTURE_ENGINEERING_GRANT'
                data['engineering_limit_authorization']=authorization
                data['limits']['engineering']=[6000,1800,None]
                for kind in ('engineering','dependency','research'):
                    with self.assertRaisesRegex(ValueError,'ENGINEERING_TIME_EXCEPTION_NOT_AUTHORIZED'):
                        budget.effective_limits(data,kind)
        data=self.ledger();data['engineering_limit_authorization']=valid
        with self.assertRaisesRegex(ValueError,'ENGINEERING_TIME_EXCEPTION_NOT_AUTHORIZED'):
            budget.effective_limits(data,'engineering')

    def test_engineering_grant_resume_never_resets_prior_attempts_or_shared_usage(self):
        data=self.ledger();data['task_id']='TEST_FIXTURE_ENGINEERING_GRANT'
        data['attempts']=[{'kind':'engineering','reserved_seconds':1800,
                           'elapsed_seconds':1700,'status':'FAIL'} for _ in range(3)]
        data['attempts'].append({'kind':'dependency','reserved_seconds':600,
                                'elapsed_seconds':300,'status':'PASS'})
        self.assertEqual(budget.used_seconds(data,'engineering'),5480)
        previous_attempts=copy.deepcopy(data['attempts'])
        data['engineering_limit_authorization']=self.engineering_authorization()
        data['limits']['engineering']=[6000,1800,None]
        resumed=json.loads(json.dumps(data))
        self.assertEqual(budget.effective_limits(resumed,'engineering'),(6000,1800,None))
        self.assertEqual(budget.used_seconds(resumed,'engineering'),5480)
        self.assertEqual(budget.used_seconds(resumed,'dependency'),300)
        self.assertEqual(resumed['attempts'],previous_attempts)
        self.assertEqual(budget.effective_limits(resumed,'dependency'),(600,600,1))

    def test_nonfinite_or_negative_usage_fails_closed(self):
        for bad in (float('nan'),float('inf'),-1):
            with self.subTest(value=bad):
                data=self.ledger()
                data['known_engineering_source_and_admission_seconds']=bad
                with self.assertRaises(ValueError):
                    budget.used_seconds(data,'engineering')

    def test_denied_resume_never_starts_command_and_keeps_existing_failures(self):
        with tempfile.TemporaryDirectory(prefix='TEST_FIXTURE_budget_resume_') as tmp:
            root=pathlib.Path(tmp)
            ledger=root/'evidence/budget.json';ledger.parent.mkdir()
            data=self.ledger()
            data.update(root=str(root),task_id='TEST_FIXTURE_BUDGET',
                        initial_project_bytes=0,project_limit_bytes=1024**3,
                        new_bytes_limit=1024**3,required_free_bytes=10*budget.GIB,
                        rss_limit_bytes=1024**3,
                        admission_attempts=[{'decision':'DENIED','reason':'PRESERVED_FAILURE'}])
            data['known_engineering_source_and_admission_seconds']=5370
            data['source_check_reservation']['seconds']=10
            ledger.write_text(json.dumps(data))
            marker=root/'command-started'
            command=[sys.executable,str(pathlib.Path(budget.__file__)),
                     '--ledger',str(ledger),'--kind','engineering','--label','blocked',
                     '--seconds','30','--reserve-bytes','0','--',sys.executable,'-c',
                     'import pathlib; pathlib.Path('+repr(str(marker))+').write_text("bad")']
            result=subprocess.run(command,capture_output=True,text=True,timeout=10)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('BUDGET_ADMISSION_DENIED',result.stderr)
            self.assertFalse(marker.exists())
            saved=json.loads(ledger.read_text())
            self.assertEqual(saved['task_id'],'TEST_FIXTURE_BUDGET')
            self.assertEqual(saved['attempts'],[])
            self.assertEqual(saved['admission_attempts'][0]['reason'],'PRESERVED_FAILURE')
            self.assertFalse(saved['admission_attempts'][-1]['process_started'])
            self.assertGreater(budget.used_seconds(saved,'engineering'),5380)

    def test_new_artifacts_count_even_when_project_net_growth_is_negative(self):
        with tempfile.TemporaryDirectory(prefix='TEST_FIXTURE_budget_storage_') as tmp:
            root=pathlib.Path(tmp)
            ledger=root/'evidence/budget.json';ledger.parent.mkdir()
            (ledger.parent/'fixture-artifact').write_bytes(b'x'*65536)
            data={'root':str(root),'initial_project_bytes':10**9,
                  'task_new_storage_source_files_bound_bytes':4096}
            self.assertGreaterEqual(budget.new_storage_bytes(data,ledger,1),65536+4096)

    def test_partial_import_and_individual_negative_usage_fail_closed(self):
        data=self.ledger();data['external_budget_charges']=[]
        with self.assertRaisesRegex(ValueError,'INCOMPLETE_PREVIOUS_BUDGET_IMPORT'):
            budget.used_seconds(data,'engineering')
        data=self.ledger();budget.initialize_accounting(data)
        data['external_budget_charges'].append({'kind':'engineering','label':'bad','seconds':-1})
        with self.assertRaisesRegex(ValueError,'INVALID_BUDGET_CONSUMPTION'):
            budget.used_seconds(data,'engineering')
        data=self.ledger()
        data['attempts']=[{'kind':'engineering','elapsed_seconds':-1,'reserved_seconds':10},
                          {'kind':'engineering','elapsed_seconds':20,'reserved_seconds':20}]
        with self.assertRaisesRegex(ValueError,'INVALID_BUDGET_CONSUMPTION'):
            budget.used_seconds(data,'engineering')

    def test_resource_limits_require_explicit_capacity_exception(self):
        valid={'project_limit_bytes':50*budget.GIB,'new_bytes_limit':2*budget.GIB,
               'required_free_bytes':10*budget.GIB,'rss_limit_bytes':2*budget.GIB}
        for key,bad in [('new_bytes_limit',3*budget.GIB),('rss_limit_bytes',float('nan')),
                        ('required_free_bytes',9*budget.GIB),('project_limit_bytes',57*budget.GIB)]:
            with self.subTest(key=key):
                data=dict(valid);data[key]=bad
                with self.assertRaises(ValueError):budget.storage_limits(data)
        valid['project_limit_bytes']=57*budget.GIB
        valid['storage_limit_authorization']={'project_limit_bytes':57*budget.GIB,
                                             'user_instruction':'TEST_FIXTURE explicit exception'}
        self.assertEqual(budget.storage_limits(valid)[0],57*budget.GIB)

    def test_final_ledger_and_output_reservation_stays_in_shared_time_budget(self):
        data=self.ledger()
        data['attempts']=[{'kind':'dependency','reserved_seconds':20,
                          'elapsed_seconds':10,'budget_charge_seconds':11,
                          'terminal_seconds_reserved':1}]
        self.assertEqual(budget.used_seconds(data,'engineering'),91)
        data['attempts'][0]['budget_charge_seconds']=9
        with self.assertRaisesRegex(ValueError,'CANNOT_REFUND_MEASURED_RUNTIME'):
            budget.used_seconds(data,'engineering')

    def test_registered_external_artifacts_count_even_if_project_shrinks(self):
        with tempfile.TemporaryDirectory(prefix='TEST_FIXTURE_budget_roots_') as tmp:
            root=pathlib.Path(tmp);ledger=root/'project/evidence/budget.json'
            ledger.parent.mkdir(parents=True)
            output=root/'explicit_output';output.mkdir()
            (output/'fixture-artifact').write_bytes(b'x'*65536)
            data={'root':str(root/'project'),'initial_project_bytes':10**9,
                  'managed_artifact_roots':[str(output)]}
            self.assertGreaterEqual(budget.new_storage_bytes(data,ledger,1),65536)

    def test_independent_sdk_process_group_is_stopped_after_parent_exits(self):
        process=None;owned=None
        try:
            # Same session, separate PGID matches runtime.ConfigureCommand's
            # real SDK boundary. Ownership survives reparenting after root exit.
            code=('import os,subprocess,sys,time; '
                  'subprocess.Popen([sys.executable,"-c","import time; time.sleep(20)"],'
                  'preexec_fn=os.setpgrp); time.sleep(.5)')
            process=subprocess.Popen([sys.executable,'-c',code],start_new_session=True)
            owned=budget.TaskTree(process.pid)
            import time
            until=time.monotonic()+2;discovered=False
            while time.monotonic()<until:
                rows=owned.sample()
                if any(row['group']!=process.pid for row in rows.values()):
                    discovered=True;break
                time.sleep(.02)
            self.assertTrue(discovered,'fixture SDK child was not observed')
            process.wait(timeout=3)
            self.assertTrue(owned.stop())
            self.assertEqual(owned.sample(),{})
        finally:
            if owned:
                try:owned.stop()
                except Exception:owned.emergency_stop()
            if process and process.poll() is None:
                process.kill();process.wait(timeout=2)

    def test_short_successful_command_exceeding_storage_is_not_reported_pass(self):
        with tempfile.TemporaryDirectory(prefix='TEST_FIXTURE_budget_short_storage_') as tmp:
            root=pathlib.Path(tmp);ledger=root/'evidence/budget.json';ledger.parent.mkdir()
            data=self.ledger()
            data.update(root=str(root),task_id='TEST_FIXTURE_BUDGET',initial_project_bytes=0,
                        project_limit_bytes=budget.GIB,new_bytes_limit=65536,
                        required_free_bytes=10*budget.GIB,rss_limit_bytes=budget.GIB,
                        managed_artifact_roots=[str(ledger.parent)])
            ledger.write_text(json.dumps(data))
            artifact=ledger.parent/'fixture-growth'
            command=[sys.executable,str(pathlib.Path(budget.__file__)),'--ledger',str(ledger),
                     '--kind','engineering','--label','short-growth','--seconds','15',
                     '--reserve-bytes','0','--',sys.executable,'-c',
                     'import pathlib; pathlib.Path('+repr(str(artifact))+').write_bytes(b"x"*1048576)']
            result=subprocess.run(command,capture_output=True,text=True,timeout=20)
            self.assertNotEqual(result.returncode,0)
            saved=json.loads(ledger.read_text());record=saved['attempts'][-1]
            self.assertEqual(record['exit_code'],0)
            self.assertEqual(record['status'],'FAIL')
            self.assertEqual(record['stop_reason'],'FINAL_STORAGE_LIMIT')
            self.assertGreater(record['final_new_bytes'],data['new_bytes_limit'])


if __name__=='__main__':
    unittest.main()
