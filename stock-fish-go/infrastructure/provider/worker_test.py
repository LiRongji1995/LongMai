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
            request={'contract':'FIXED_MAINBOARD_PUBLIC_V1','output':root,'observation_date':'2026-09-30'}
            out=io.StringIO()
            with patch.dict(sys.modules,names), patch('sys.stdin',io.StringIO(json.dumps(request))), contextlib.redirect_stdout(out):
                worker().main()
            result=json.loads(out.getvalue());self.assertEqual(result['status'],'PARTIAL')
            frozen=json.loads((pathlib.Path(root)/'frozen.json').read_text());self.assertGreaterEqual(frozen['cutoff'],result['started_at']);self.assertEqual(result['ai_dependencies'],[])

    def test_missing_dependency_is_failed_without_frozen_input(self):
        with tempfile.TemporaryDirectory(prefix='TEST_FIXTURE_') as root:
            request={'contract':'FIXED_MAINBOARD_PUBLIC_V1','output':root,'observation_date':'2026-09-30'}
            result=subprocess.run([sys.executable,'-S',str(HERE/'daily_refresh_worker.py')],input=json.dumps(request),text=True,capture_output=True,timeout=10)
            self.assertEqual(result.returncode,1);self.assertEqual(json.loads(result.stdout)['status'],'FAILED');self.assertFalse((pathlib.Path(root)/'frozen.json').exists())

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
