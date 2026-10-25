"""TEST_FIXTURE subprocess contracts; no provider, real market data or database.

The fixture worker fabricates explicitly labelled tiny artifacts. Archive
verification and the current-calendar gate use the unchanged original reader
copied from this checkout, through actual isolated Python subprocesses.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import venv

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("codex_premarket_under_test", ROOT / "scripts/codex_premarket.py")
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)

FIXTURE_WORKER = r'''
import json,sys,uuid
from datetime import datetime,timedelta,timezone
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from stockfish_core.yaogu import premarket_workflow_v1 as w
args=sys.argv[1:]
if args[0]=='premarket-verify':
    print(json.dumps(w.verify_job(args[1])))
    raise SystemExit(0)
config_path=Path(args[args.index('--config')+1])
config=w.load_config(config_path)
assert config['capture']['allow_test_fixture'] is True
state=Path(config['state_root'])
with (state/'TEST_FIXTURE_calls.jsonl').open('a') as f:
    f.write(json.dumps({'command':'premarket'})+'\n')
scenario=json.loads((state/'TEST_FIXTURE_scenario.json').read_text())
if scenario.get('failure'):
    print('TEST_FIXTURE_private_console_must_not_escape')
    raise SystemExit(3)
if scenario.get('reuse_old'):
    old=json.loads((state/'latest-job.json').read_text())
    final=w._read_control(Path(old['job'])/'final-control.json')
    print(json.dumps(final))
    raise SystemExit(final['external_worker_exit_code'])
job=state/'jobs'/('TEST_FIXTURE_'+uuid.uuid4().hex)
job.mkdir(parents=True)
configuration=job/'configuration.json'
configuration.write_text(json.dumps(config))
code_registry=job/'code-registry.json'
code_registry.write_text(json.dumps(w._code_refs()))
core={}
for name in ('canonical-run.json.gz','input.json.gz','observation.json','observation.csv',
             'all.csv','focus.csv','unselected.csv','manifest.json'):
    path=job/name
    path.write_text('TEST_FIXTURE_ARTIFACT_'+name)
    core[name]=w._ref(path)
if scenario.get('bad_hash'):
    core['all.csv']['sha256']='0'*64
if scenario.get('outside_reference'):
    outside=state/'TEST_FIXTURE_outside.csv'
    outside.write_text('TEST_FIXTURE_OUTSIDE')
    core['all.csv']=w._ref(outside)
display={'artifacts':{}}
for name in ('report.md','display.json','display.csv'):
    path=job/name
    path.write_text('TEST_FIXTURE_DISPLAY_'+name)
    display['artifacts'][name]=w._ref(path)
now=datetime.now(timezone(timedelta(hours=8)))
today=now.date()
observation=today if now.hour>=18 else today-timedelta(days=1)
if scenario.get('stale_calendar'):
    observation-=timedelta(days=1)
target=observation+timedelta(days=1)
calendar=[{'calendar_date':str(today+timedelta(days=i)),'is_trading_day':'1'} for i in range(-3,4)]
status='COMPLETED_WITH_UNKNOWNS' if scenario.get('unknowns') else 'COMPLETE'
completion={'schema_version':w.VERSION,'status':status,'core_verified':True,
    'run_id':'TEST_FIXTURE_run','observation_date':str(observation),'target_session':str(target),
    'calendar_rows':calendar,'display':display,'core_artifacts':core,'theme_artifacts':{},
    'news':None,'strategy_id':'SHORT_HORIZON_STRENGTH_R2','coverage_type':'TEST_FIXTURE',
    'theme_status':'UNKNOWN' if scenario.get('unknowns') else 'AVAILABLE'}
completion_path=job/'worker-completion.json'
completion_path.write_text(json.dumps(completion))
final={'schema_version':w.VERSION,'status':status,'core_verified':True,
    'external_worker_exit_code':2 if scenario.get('unknowns') else 0,'external_worker_reaped':True,
    'implementation_epoch_verified':True,'source_terminal_binding_verified':True,
    'configuration':w._ref(configuration),'code_registry':w._ref(code_registry),
    'completion':w._ref(completion_path),'display':display,'job':str(job),
    'final_sealed_at':datetime.now(timezone.utc).isoformat()}
final_path=job/'final-control.json'
final_path.write_bytes(b' '*w.SEAL_BYTES)
w._seal(final_path,final)
(state/'latest-job.json').write_text(json.dumps({'job':str(job),'final':w._ref(final_path)}))
if scenario.get('overflow'):
    print('X'*(1024*1024+1))
print(json.dumps(final))
raise SystemExit(final['external_worker_exit_code'])
'''


class FixtureCoordinator:
    """Small durable TEST_FIXTURE coordinator, with fail-closed unknown claims."""
    def __init__(self, path):
        self.path = path

    def run_once(self, identity, action, validate):
        key = hashlib.sha256(adapter._canonical(identity)).hexdigest()
        values = json.loads(self.path.read_text()) if self.path.exists() else {}
        if key in values:
            row = values[key]
            if row['status'] != 'COMPLETE':
                raise adapter.PremarketError('TEST_FIXTURE_UNKNOWN_NO_RETRY')
            checked = validate(row['receipt'])
            if checked is not True and not (isinstance(checked, dict) and checked.get('verified') is True):
                raise adapter.PremarketError('TEST_FIXTURE_RECEIPT_VERIFICATION_REQUIRED')
            return {'status': 'REUSED', 'reused': True, 'receipt': row['receipt'], 'validation': checked}
        values[key] = {'status': 'UNKNOWN'}
        self.path.write_text(json.dumps(values))
        receipt = action()
        checked = validate(receipt)
        if checked is not True and not (isinstance(checked, dict) and checked.get('verified') is True):
            raise adapter.PremarketError('TEST_FIXTURE_RECEIPT_VERIFICATION_REQUIRED')
        values[key] = {'status': 'COMPLETE', 'receipt': receipt}
        self.path.write_text(json.dumps(values))
        return {'status': 'COMPLETED', 'reused': False, 'receipt': receipt, 'validation': checked}


class PremarketAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='TEST_FIXTURE_codex_premarket_')
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'source'
        core = self.source / 'stockfish_core/yaogu'
        core.mkdir(parents=True)
        (self.source / 'stockfish_core/__init__.py').write_text('')
        (core / '__init__.py').write_text('')
        for name in ('premarket_workflow_v1.py', 'premarket_runtime_v1.py'):
            (core / name).write_bytes((ROOT / 'stockfish_core/yaogu' / name).read_bytes())
        (self.source / 'scripts').mkdir()
        (self.source / 'scripts/stockfish.py').write_text(FIXTURE_WORKER)
        (self.source / 'premarket').write_text('TEST_FIXTURE_LAUNCHER')
        (self.source / 'premarket.ps1').write_text('TEST_FIXTURE_LAUNCHER')
        for name in adapter.STRATEGY_SUPPORT:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('TEST_FIXTURE_FROZEN_STRATEGY_SUPPORT')
        self.state = self.root / 'state'
        self.state.mkdir()
        self.config = self.root / 'private-config.json'
        self.config.write_text(json.dumps({'state_root': str(self.state), 'capture': {'allow_test_fixture': True}}))
        self.scenario({})
        self.profile = {'name': 'TEST_FIXTURE_first_chat', 'backend': 'premarket',
                        'source_root': str(self.source), 'state_root': str(self.state),
                        'python': sys.executable, 'config': str(self.config)}
        now = datetime.now(timezone(timedelta(hours=8)))
        observation = now.date() if now.hour >= 18 else now.date() - timedelta(days=1)
        self.observation = str(observation)
        self.target = str(observation + timedelta(days=1))
        self.journal = self.root / 'TEST_FIXTURE_coordinator.json'

    def tearDown(self):
        self.temp.cleanup()

    def scenario(self, value):
        (self.state / 'TEST_FIXTURE_scenario.json').write_text(json.dumps(value))

    def execute(self, **changes):
        args = dict(observation_date=self.observation, target_session=self.target,
                    request_key='TEST_FIXTURE_shared_request', coordinator=FixtureCoordinator(self.journal))
        args.update(changes)
        return adapter.execute_premarket(self.profile, **args)

    def calls(self):
        path = self.state / 'TEST_FIXTURE_calls.jsonl'
        return len(path.read_text().splitlines()) if path.exists() else 0

    def registered_runtime(self, *, maximum_bytes=32 * 1024 * 1024):
        from scripts import codex_workspace as workspace
        profile = dict(self.profile, schema_version=workspace.PROFILE_VERSION,
                       report_root=str(self.state), storage={
                           'roots': [{'name': 'TEST_FIXTURE_state', 'path': str(self.state), 'role': 'runtime'}],
                           'target_root': 'TEST_FIXTURE_state', 'expected_growth_bytes': 65536,
                           'min_free_bytes': 0, 'max_managed_bytes': maximum_bytes})
        path = self.root / 'TEST_FIXTURE_runtime.json'
        path.write_text(json.dumps(profile))
        registry = self.root / 'registry'
        workspace.register_profile(path, registry, make_default=True)
        return workspace, registry, workspace.load_runtime(registry_root=registry)

    def test_real_runtime_registration_reload_and_adapter_share_one_verified_job(self):
        workspace, registry, runtime = self.registered_runtime()
        first = adapter.execute_premarket(runtime.profile, observation_date=self.observation,
                target_session=self.target, request_key='TEST_FIXTURE_real_coordinator', coordinator=runtime)
        before = {str(p.relative_to(self.state)): p.read_bytes() for p in self.state.rglob('*') if p.is_file()}
        reloaded = workspace.load_runtime(registry_root=registry)
        second = adapter.execute_premarket(reloaded.profile, observation_date=self.observation,
                target_session=self.target, request_key='TEST_FIXTURE_real_coordinator', coordinator=reloaded)
        after = {str(p.relative_to(self.state)): p.read_bytes() for p in self.state.rglob('*') if p.is_file()}
        self.assertTrue(first['validation']['verified'])
        self.assertTrue(second['validation']['verified'])
        self.assertFalse(first['reused'])
        self.assertTrue(second['reused'])
        self.assertEqual(second['new_action_calls'], 0)
        self.assertEqual(second['new_data_copy_bytes'], 0)
        self.assertEqual(self.calls(), 1)
        self.assertEqual(before, after)

    def test_real_runtime_capacity_failure_happens_before_any_external_execution(self):
        workspace, _, runtime = self.registered_runtime(maximum_bytes=1)
        with self.assertRaisesRegex(workspace.WorkspaceError, 'CAPACITY_PREFLIGHT_BLOCKED'):
            adapter.execute_premarket(runtime.profile, observation_date=self.observation,
                target_session=self.target, request_key='TEST_FIXTURE_no_capacity', coordinator=runtime)
        self.assertEqual(self.calls(), 0)
        self.assertFalse((self.state / 'jobs').exists())

    @unittest.skipUnless(os.name == 'posix', 'Runtime directory-handle protocol requires POSIX')
    def test_real_venv_invocation_survives_registration_and_retarget_is_refused(self):
        environment = self.root / 'TEST_FIXTURE_empty_venv'
        # stdlib-only isolated test environment: no pip, packages or network.
        venv.EnvBuilder(with_pip=False, symlinks=True).create(environment)
        invocation = environment / 'bin/python'
        self.assertTrue(invocation.is_symlink())
        self.profile['python'] = str(invocation)
        workspace, registry, runtime = self.registered_runtime()
        self.assertEqual(runtime.profile['python'], str(invocation))
        prepared = adapter._prepared(runtime.profile)
        snapshot = adapter._snapshot(prepared)
        self.assertEqual(prepared['python'], invocation)
        code, raw, _ = adapter._run([runtime.profile['python'], '-I', '-B', '-c',
            'import sys,json;print(json.dumps({"prefix":sys.prefix,"base_prefix":sys.base_prefix}))'], prepared)
        actual = json.loads(raw)
        self.assertEqual(code, 0)
        self.assertEqual(Path(actual['prefix']), environment)
        self.assertNotEqual(actual['prefix'], actual['base_prefix'])
        first = adapter.execute_premarket(runtime.profile, observation_date=self.observation,
                target_session=self.target, request_key='TEST_FIXTURE_venv', coordinator=runtime)
        self.assertTrue(first['validation']['verified'])
        self.assertEqual(self.calls(), 1)
        replacement = self.root / 'TEST_FIXTURE_retarget_not_executed'
        replacement.write_text('TEST_FIXTURE_NOT_AN_INTERPRETER')
        replacement.chmod(0o700)
        invocation.unlink()
        invocation.symlink_to(replacement)
        with self.assertRaisesRegex(workspace.WorkspaceError, 'PHYSICAL_IDENTITY_CHANGED'):
            workspace.load_runtime(registry_root=registry)
        with self.assertRaisesRegex(adapter.PremarketError, 'PYTHON_TARGET_CHANGED'):
            adapter._same_snapshot(prepared, snapshot)
        self.assertEqual(self.calls(), 1)

    def test_independent_chat_instances_reuse_one_external_execution_and_original_bytes(self):
        first = self.execute()
        before = {str(p.relative_to(self.state)): p.read_bytes() for p in self.state.rglob('*') if p.is_file()}
        self.profile['name'] = 'TEST_FIXTURE_other_chat_same_state'
        second = self.execute()
        after = {str(p.relative_to(self.state)): p.read_bytes() for p in self.state.rglob('*') if p.is_file()}
        self.assertFalse(first['reused'])
        self.assertTrue(second['reused'])
        self.assertEqual(self.calls(), 1)
        self.assertEqual(first['receipt']['job'], second['receipt']['job'])
        self.assertEqual(before, after)
        self.assertEqual(second['receipt']['result_role'], 'ARCHIVED_REUSE_NOT_NEW_CAPTURE')
        self.assertFalse(second['receipt']['source_authority_restored'])
        self.assertTrue(second['validation']['currentness']['original_calendar_used'])
        self.assertFalse(list(self.state.rglob('*.db')))

    def test_unknown_attachment_and_exit_two_remain_unknown_on_reuse(self):
        self.scenario({'unknowns': True})
        first = self.execute()
        second = self.execute()
        self.assertEqual(first['receipt']['status'], 'COMPLETED_WITH_UNKNOWNS')
        self.assertEqual(second['receipt']['execution_exit_code'], 2)
        self.assertEqual(self.calls(), 1)

    def test_corrupt_core_artifact_rejects_reuse_without_rerunning(self):
        receipt = self.execute()['receipt']
        (Path(receipt['job']) / 'all.csv').write_text('TEST_FIXTURE_CORRUPTION')
        with self.assertRaisesRegex(adapter.PremarketError, 'PHYSICAL_IDENTITY_CHANGED'):
            self.execute()
        self.assertEqual(self.calls(), 1)

    def test_original_reader_detects_wrong_hash_even_when_file_identity_matches(self):
        self.scenario({'bad_hash': True})
        with self.assertRaisesRegex(adapter.PremarketError, 'ORIGINAL_ARCHIVE_VERIFICATION_FAILED'):
            self.execute()
        self.assertEqual(self.calls(), 1)
        with self.assertRaisesRegex(adapter.PremarketError, 'UNKNOWN_NO_RETRY'):
            self.execute()
        self.assertEqual(self.calls(), 1)

    def test_final_control_change_rejects_reuse(self):
        receipt = self.execute()['receipt']
        path = Path(receipt['job']) / 'final-control.json'
        path.write_bytes(path.read_bytes().replace(b'COMPLETE', b'FAILED  ', 1))
        with self.assertRaisesRegex(adapter.PremarketError, 'FINAL_CONTROL_CHANGED'):
            self.execute()
        self.assertEqual(self.calls(), 1)

    def test_receipt_cannot_upgrade_authority_or_redirect_display(self):
        receipt = self.execute()['receipt']
        prepared = adapter._prepared(self.profile)
        snapshot = adapter._snapshot(prepared)
        receipt['source_authority_restored'] = True
        with self.assertRaisesRegex(adapter.PremarketError, 'RECEIPT_CLAIMS_CHANGED'):
            adapter._validate_archive(prepared, snapshot, receipt, self.observation, self.target)
        receipt['source_authority_restored'] = False
        receipt['display_artifacts']['report.md'] = str(self.root / 'TEST_FIXTURE_elsewhere')
        with self.assertRaisesRegex(adapter.PremarketError, 'RECEIPT_DISPLAY_PATH_CHANGED'):
            adapter._validate_archive(prepared, snapshot, receipt, self.observation, self.target)
        self.assertEqual(self.calls(), 1)

    def test_stale_calendar_cannot_be_relabeled_as_current(self):
        self.scenario({'stale_calendar': True})
        obs = str(datetime.fromisoformat(self.observation).date() - timedelta(days=1))
        target = self.observation
        with self.assertRaisesRegex(adapter.PremarketError, 'ORIGINAL_CURRENTNESS_OR_CONFIG_CHECK_FAILED'):
            self.execute(observation_date=obs, target_session=target)
        self.assertEqual(self.calls(), 1)

    def test_unchanged_latest_pointer_is_not_a_new_success(self):
        self.execute()
        self.scenario({'reuse_old': True})
        with self.assertRaisesRegex(adapter.PremarketError, 'LATEST_JOB_NOT_UPDATED'):
            self.execute(request_key='TEST_FIXTURE_another_request')
        self.assertEqual(self.calls(), 2)

    def test_failed_process_keeps_uncertain_claim_and_never_retries(self):
        self.scenario({'failure': True})
        with self.assertRaisesRegex(adapter.PremarketError, 'EXECUTION_FAILED_OR_UNKNOWN') as caught:
            self.execute()
        self.assertNotIn('private_console', str(caught.exception))
        with self.assertRaisesRegex(adapter.PremarketError, 'UNKNOWN_NO_RETRY'):
            self.execute()
        self.assertEqual(self.calls(), 1)

    def test_explicit_same_state_is_required_before_any_process(self):
        self.config.write_text(json.dumps({'capture': {'allow_test_fixture': True}}))
        with self.assertRaisesRegex(adapter.PremarketError, 'EXPLICIT_STATE_ROOT_REQUIRED'):
            self.execute()
        self.assertEqual(self.calls(), 0)
        self.config.write_text(json.dumps({'state_root': str(self.root)}))
        with self.assertRaisesRegex(adapter.PremarketError, 'CONFIG_STATE_ROOT_MISMATCH'):
            self.execute()
        self.assertEqual(self.calls(), 0)

    def test_reference_outside_registered_job_is_refused(self):
        self.scenario({'outside_reference': True})
        with self.assertRaisesRegex(adapter.PremarketError, 'REFERENCE_OUTSIDE_JOB'):
            self.execute()
        self.assertEqual(self.calls(), 1)

    def test_console_is_bounded_and_private_output_does_not_escape(self):
        self.scenario({'overflow': True})
        with self.assertRaisesRegex(adapter.PremarketError, 'CONSOLE_LIMIT_EXCEEDED') as caught:
            self.execute()
        self.assertLess(len(str(caught.exception)), 100)
        self.assertEqual(self.calls(), 1)

    def test_source_and_config_mutations_during_validation_are_refused(self):
        receipt = self.execute()['receipt']
        prepared = adapter._prepared(self.profile)
        snapshot = adapter._snapshot(prepared)
        path = self.source / 'stockfish_core/yaogu/__init__.py'
        path.write_text('# TEST_FIXTURE_CHANGED\n')
        with self.assertRaisesRegex(adapter.PremarketError, 'IMPLEMENTATION_OR_CONFIG_CHANGED'):
            adapter._validate_archive(prepared, snapshot, receipt, self.observation, self.target)
        self.assertEqual(self.calls(), 1)

    def test_original_gate_checks_effective_configuration_not_only_declared_dates(self):
        receipt = self.execute()['receipt']
        prepared = adapter._prepared(self.profile)
        config = json.loads(self.config.read_text())
        config['minimum_amount_cny'] = 123
        self.config.write_text(json.dumps(config))
        snapshot = adapter._snapshot(prepared)
        with self.assertRaisesRegex(adapter.PremarketError, 'ORIGINAL_CURRENTNESS_OR_CONFIG_CHECK_FAILED'):
            adapter._validate_archive(prepared, snapshot, receipt, self.observation, self.target)
        self.assertEqual(self.calls(), 1)

    def test_original_gate_rejects_archive_bound_to_another_source_epoch(self):
        receipt = self.execute()['receipt']
        prepared = adapter._prepared(self.profile)
        (self.source / 'stockfish_core/yaogu/__init__.py').write_text('# TEST_FIXTURE_NEW_SOURCE\n')
        snapshot = adapter._snapshot(prepared)
        with self.assertRaisesRegex(adapter.PremarketError, 'ORIGINAL_CURRENTNESS_OR_CONFIG_CHECK_FAILED'):
            adapter._validate_archive(prepared, snapshot, receipt, self.observation, self.target)
        self.assertEqual(self.calls(), 1)

    def test_strategy_support_files_are_bound_without_scanning_private_configs(self):
        prepared = adapter._prepared(self.profile)
        snapshot = adapter._snapshot(prepared)
        (self.source / 'config/TEST_FIXTURE_private.env').write_text('TEST_FIXTURE_SECRET=not-read')
        self.assertEqual(adapter._snapshot(prepared), snapshot)
        (self.source / 'config/short_horizon_strength_r2.yaml').write_text('TEST_FIXTURE_CHANGED_STRATEGY')
        with self.assertRaisesRegex(adapter.PremarketError, 'IMPLEMENTATION_OR_CONFIG_CHANGED'):
            adapter._same_snapshot(prepared, snapshot)

    def test_verification_timeout_is_finite_without_an_automatic_second_process(self):
        prepared = adapter._prepared(self.profile)
        marker = self.root / 'TEST_FIXTURE_timeout_calls'
        command = [sys.executable, '-I', '-B', '-c',
                   'from pathlib import Path;import time;Path(' + repr(str(marker)) + ').write_text("once");time.sleep(10)']
        with self.assertRaisesRegex(adapter.PremarketError, 'ARCHIVE_VERIFICATION_TIMEOUT'):
            adapter._run(command, prepared, timeout=0.2)
        self.assertEqual(marker.read_text(), 'once')

    def test_relative_config_state_is_rejected_before_launch(self):
        self.config.write_text(json.dumps({'state_root': 'state', 'capture': {'allow_test_fixture': True}}))
        with self.assertRaisesRegex(adapter.PremarketError, 'ABSOLUTE_CONFIG_STATE_REQUIRED'):
            self.execute()
        self.assertEqual(self.calls(), 0)

    def test_missing_interpreter_never_installs_or_creates_runtime(self):
        self.profile['python'] = str(self.root / 'TEST_FIXTURE_missing_python')
        with self.assertRaisesRegex(adapter.PremarketError, 'PROFILE_PATH_UNAVAILABLE'):
            self.execute()
        self.assertEqual(self.calls(), 0)
        self.assertFalse(Path(self.profile['python']).exists())

    def test_python_path_environment_is_removed_in_real_subprocess(self):
        prepared = adapter._prepared(self.profile)
        old = os.environ.get('PYTHONPATH')
        os.environ['PYTHONPATH'] = 'TEST_FIXTURE_untrusted_pythonpath'
        try:
            code, raw, _ = adapter._run([sys.executable, '-I', '-B', '-c',
                'import os,json;print(json.dumps({"path":os.environ.get("PYTHONPATH"),"home":os.environ.get("PYTHONHOME")}))'], prepared)
        finally:
            if old is None:
                os.environ.pop('PYTHONPATH', None)
            else:
                os.environ['PYTHONPATH'] = old
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(raw), {'path': None, 'home': None})


if __name__ == '__main__':
    unittest.main()
