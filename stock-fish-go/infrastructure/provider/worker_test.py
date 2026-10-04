"""Offline TEST_FIXTURE checks; no SDK call or network request is made."""
import contextlib
import importlib.util
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def worker():
    spec = importlib.util.spec_from_file_location('fixed_worker_fixture', HERE/'daily_refresh_worker.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FixedWorkerTest(unittest.TestCase):
    def test_partial_frozen_uses_actual_capture_clock_and_never_strategy(self):
        class Pipeline:
            def __init__(self, *args, **kwargs):
                self.budget = kwargs['budget']
            def capture(self, output, now, **kwargs):
                self.assert_contract = kwargs['purpose'] == 'DAILY_PRIMARY_CRITICAL' and kwargs['fast_decision'] is False
                assert self.assert_contract
                assert self.budget.max_requests == 100 and self.budget.max_seconds == 600
                kwargs['checkpoint']()
                output.mkdir()
                (output/'market-input.json').write_text(json.dumps({'coverage_type':'TEST_FIXTURE','observation_date':'2026-09-30'}))
                return {'actual_daily_ready':False}
        names = {
            'stockfish_core':types.ModuleType('stockfish_core'),
            'stockfish_core.yaogu':types.ModuleType('stockfish_core.yaogu'),
            'stockfish_core.yaogu.config':types.SimpleNamespace(StrategyConfig=lambda: object()),
            'stockfish_core.yaogu.data_pipeline':types.SimpleNamespace(MainboardPipeline=Pipeline,CAPTURE_PURPOSE_DAILY_PRIMARY_CRITICAL='DAILY_PRIMARY_CRITICAL'),
            'stockfish_core.yaogu.request_queue':types.SimpleNamespace(RequestBudget=lambda **kw:types.SimpleNamespace(**kw)),
        }
        with tempfile.TemporaryDirectory(prefix='TEST_FIXTURE_') as root:
            request={'contract':'FIXED_MAINBOARD_PUBLIC_V1','output':root,'observation_date':'2026-09-30','allow_cold_capture':True}
            out=io.StringIO()
            module=worker()
            checked={'status':'INSTALLED_NOT_ONLINE_VERIFIED','history_reuse':{'status':'BLOCKED_GO_V2_HISTORY_ADAPTER_UNAVAILABLE'}}
            with patch.dict(sys.modules,names), patch.object(module,'dependency_check',return_value=checked), patch('sys.stdin',io.StringIO(json.dumps(request))), contextlib.redirect_stdout(out):
                module.main()
            result=json.loads(out.getvalue());self.assertEqual(result['status'],'PARTIAL')
            frozen=json.loads((pathlib.Path(root)/'frozen.json').read_text());self.assertGreaterEqual(frozen['cutoff'],result['started_at']);self.assertEqual(result['ai_dependencies'],[])

    def test_missing_dependency_is_failed_without_frozen_input(self):
        with tempfile.TemporaryDirectory(prefix='TEST_FIXTURE_') as root:
            request={'contract':'FIXED_MAINBOARD_PUBLIC_V1','output':root,'observation_date':'2026-09-30'}
            result=subprocess.run([sys.executable,'-S',str(HERE/'daily_refresh_worker.py')],input=json.dumps(request),text=True,capture_output=True,timeout=10)
            self.assertEqual(result.returncode,1);self.assertEqual(json.loads(result.stdout)['status'],'FAILED');self.assertFalse((pathlib.Path(root)/'frozen.json').exists())

    def test_check_actual_import_failure_extension_version_and_no_network(self):
        module=worker()
        versions={distribution:version for _,distribution,version in module.SDK_IDENTITIES}
        def importer(name):
            if name=='pydantic':
                raise ModuleNotFoundError('fixture native core absent', name='pydantic_core._pydantic_core')
            if name=='numpy':
                raise ModuleNotFoundError('fixture absent', name='numpy')
            if name=='akshare':
                module.socket.create_connection(('example.invalid',443))
            return types.SimpleNamespace()
        def version(name):
            return 'UNREGISTERED_TEST_FIXTURE' if name=='pandas' else versions[name]
        with patch.object(module,'minimal_call',return_value=None):
            result=module.dependency_check(importer,version)
        categories=result['failure_categories']
        self.assertIn('COMPILED_EXTENSION_OR_IMPORT_DEPENDENCY_MISSING',categories)
        self.assertIn('DEPENDENCY_MISSING',categories)
        self.assertIn('VERSION_MISMATCH',categories)
        self.assertIn('NETWORK_ATTEMPT_REJECTED',categories)
        self.assertFalse(result['network_probe'])
        self.assertTrue(result['network_calls_forbidden'])

    def test_check_uses_native_extension_and_minimal_call(self):
        module=worker()
        def missing_core(name):
            raise ModuleNotFoundError('fixture extension absent',name=name)
        with self.assertRaises(ModuleNotFoundError):
            module.minimal_call('pydantic',types.SimpleNamespace(),missing_core)
        calls=[]
        core=types.SimpleNamespace(SchemaValidator=lambda schema:types.SimpleNamespace(validate_python=lambda value:calls.append(value) or value))
        class Probe:
            @classmethod
            def model_validate(cls,value):return types.SimpleNamespace(model_dump=lambda:value)
        module.minimal_call('pydantic',types.SimpleNamespace(BaseModel=Probe),lambda name:core)
        self.assertEqual(calls,[7])

    def test_requested_go_history_reuse_fails_closed_before_capture(self):
        module=worker()
        checked={'status':'INSTALLED_NOT_ONLINE_VERIFIED','history_reuse':{'status':'BLOCKED_GO_V2_HISTORY_ADAPTER_UNAVAILABLE'}}
        with tempfile.TemporaryDirectory(prefix='TEST_FIXTURE_') as root:
            request={'contract':'FIXED_MAINBOARD_PUBLIC_V1','output':root,'observation_date':'2026-09-30','history_reuse':{'history_ref':'TEST_FIXTURE_GO_REF','history_present':True},'allow_cold_capture':True}
            out=io.StringIO()
            with patch.object(module,'dependency_check',return_value=checked),patch('sys.stdin',io.StringIO(json.dumps(request))),contextlib.redirect_stdout(out):
                with self.assertRaises(SystemExit) as failed:module.main()
            self.assertEqual(failed.exception.code,1)
            self.assertEqual(json.loads(out.getvalue())['reason'],'GO_V2_HISTORY_ADAPTER_UNAVAILABLE')
            self.assertFalse((pathlib.Path(root)/'capture').exists())

    def test_cold_capture_requires_explicit_opt_in_after_dependency_check(self):
        module=worker()
        checked={'status':'INSTALLED_NOT_ONLINE_VERIFIED','history_reuse':{'status':'BLOCKED_GO_V2_HISTORY_ADAPTER_UNAVAILABLE'}}
        with tempfile.TemporaryDirectory(prefix='TEST_FIXTURE_') as root:
            request={'contract':'FIXED_MAINBOARD_PUBLIC_V1','output':root,'observation_date':'2026-09-30'}
            out=io.StringIO()
            with patch.object(module,'dependency_check',return_value=checked),patch('sys.stdin',io.StringIO(json.dumps(request))),contextlib.redirect_stdout(out):
                with self.assertRaises(SystemExit):module.main()
            self.assertEqual(json.loads(out.getvalue())['reason'],'COLD_HISTORY_CAPTURE_EXPLICIT_OPT_IN_REQUIRED')
            self.assertFalse((pathlib.Path(root)/'capture').exists())

    def test_check_subprocess_has_classified_result_without_network(self):
        result=subprocess.run([sys.executable,'-S',str(HERE/'daily_refresh_worker.py'),'--check'],text=True,capture_output=True,timeout=10)
        self.assertEqual(result.returncode,1)
        checked=json.loads(result.stdout)
        self.assertEqual(checked['check_contract'],'OFFLINE_ACTUAL_IMPORT_AND_LOCAL_CALL_V1')
        self.assertFalse(checked['network_probe'])
        self.assertIn('DEPENDENCY_MISSING',checked['failure_categories'])

    def test_real_http_ledger_charges_before_io_and_enforces_rate_limit(self):
        spec=importlib.util.spec_from_file_location('ledger',ROOT.parent/'stock-fish/stockfish_core/yaogu/provider_budget.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(prefix='TEST_FIXTURE_') as root:
            path=pathlib.Path(root)/'http.db';key=module.initialize(path,2,minimum_interval=.01)
            module.charge(path,key,'TEST_FIXTURE_ATTEMPT1','fixture','fixture')
            module.charge(path,key,'TEST_FIXTURE_RETRY2','fixture','fixture')
            with self.assertRaises(module.HttpBudgetExhausted):module.charge(path,key,'TEST_FIXTURE_OVER_LIMIT','fixture','fixture')
            self.assertEqual(module.counts(path,key),{'HTTP':2})

if __name__=='__main__':unittest.main()
