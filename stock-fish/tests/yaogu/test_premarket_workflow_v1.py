"""Standalone controls and one real TEST_FIXTURE core/theme/display pipeline."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import os
import socket
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from stockfish_core.yaogu import premarket_workflow_v1 as workflow
from scripts import codex_premarket_shared_v1 as active_workflow
from stockfish_core.yaogu.premarket_capture_cache_v1 import (
    CaptureCollection, CaptureRecord, ThemeSourceCollection)
from stockfish_core.yaogu.premarket_runtime_v1 import strict_json
from tests.yaogu.test_premarket_native_core_v1 import captures, FixtureBudget
from tests.support.saved_r2_v3_fixture import physical_source


class WorkflowFixtureTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='stockfish-workflow-TEST_FIXTURE-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.config = workflow.load_config(overrides={'state_root': str(self.root/'state'),
            'capture': {'offline': True, 'allow_test_fixture': True}})

    def _adapters(self):
        items, observed, target, cutoff = captures(self.root/'originals', sessions=67, history=True)
        records = []
        for item in items:
            result = strict_json(Path(item['result']['path']).read_bytes())
            records.append(CaptureRecord.from_metadata(dict(item, endpoint=result['endpoint'],
                parameters=result['parameters'], status=result['status'],
                receipt_id=result['request_receipt_id'], first_seen_at=result['first_seen_at'],
                ingested_at=result['first_seen_at'])))
        calendar = strict_json(Path(items[0]['result']['path']).read_bytes())['rows']
        sessions = tuple(row['calendar_date'] for row in calendar if row['calendar_date'] <= str(observed))
        collection = CaptureCollection(str(observed), str(target), sessions, sessions[-25:],
            tuple(records), (), (), (), (), {'current_snapshot_session': str(observed)},
            True, tuple(calendar), 'TEST_FIXTURE')
        symbol = 'gn_TEST_FIXTURE_BATTERY'
        seen = cutoff-timedelta(minutes=1)
        common = dict(provider='sina', version='TEST_FIXTURE_SINA_V1', include_result=True)
        contexts = [physical_source(self.root/'contexts', 'concept_list_sina',
            [{'label': symbol, '板块': 'TEST_FIXTURE电池', '公司家数': 2}], seen,
            parameters={}, **common), physical_source(self.root/'contexts', 'concept_members_sina',
            [{'code': '600001', 'symbol': 'sh600001', 'name': 'TEST_FIXTURE'},
             {'code': '000001', 'symbol': 'sz000001', 'name': 'TEST_FIXTURE'}], seen,
            parameters={'symbol': symbol}, **common)]
        for item in contexts:
            for role in ('raw','receipt','result'):
                item[role] = FixtureBudget().read(item[role]['path'], expected=item[role]['sha256'])[1]
        sources = ThemeSourceCollection(tuple(contexts), (), (), (symbol,),
            {'status': 'COMPLETE_CAPTURE_NOT_THEME_READY', 'fixture': 'TEST_FIXTURE'})

        class FixtureCache:
            def __init__(self, *args, **kwargs):
                pass
            def metadata(self, record):
                return record.to_dict()
            def terminal(self):
                return True

        def news(*, core_run, output_dir, budget, config):
            self.assertTrue(config['offline'])
            Path(output_dir).mkdir()
            ref = budget.write_json(Path(output_dir)/'metadata.json',
                {'schema_version': 'TEST_FIXTURE_NEWS_NOT_COLLECTED', 'status': 'UNKNOWN'})
            return {'summary': {'status': 'UNKNOWN', 'parsed_articles': None}, 'metadata': ref}

        return dict(cache=FixtureCache, collect=lambda *a, **k: collection,
            theme_sources=lambda *a, **k: sources, news=news), cutoff

    def _execute(self, adapters, now):
        job = self.root/'job'
        job.mkdir()
        with patch.object(socket.socket, 'connect', side_effect=AssertionError('TEST_FIXTURE_NETWORK')), \
             patch.object(sqlite3, 'connect', side_effect=AssertionError('TEST_FIXTURE_DB')):
            return workflow.execute_workflow(self.config, job, now=now, adapters=adapters)

    def test_in_process_real_core_v2_theme_and_formatter_contract(self):
        adapters, now = self._adapters()
        result = self._execute(adapters, now)
        self.assertEqual(result['status'], 'COMPLETED_WITH_UNKNOWNS', result.get('error'))
        self.assertTrue(result['core_verified'])
        self.assertEqual(result['phases']['core'], 'COMPLETE')
        self.assertEqual(result['phases']['theme'], 'COMPLETE', result.get('phase_failures'))
        self.assertEqual(result['phases']['presentation'], 'COMPLETE')
        self.assertEqual(result['phases']['news'], 'UNKNOWN')
        self.assertEqual(result['display']['counts']['displayed'], 2)
        self.assertEqual(result['display']['counts']['original_selected'], 2)
        self.assertGreater(len(result['validation_references']), 100)
        self.assertTrue(Path(result['core_artifacts']['observation.json']['path']).is_file())
        self.assertEqual(result['metrics']['calls']['requests'], 0)
        self.assertGreater(result['metrics']['returned_read_bytes']['theme'], 0)

    def test_theme_failure_is_unknown_display_and_preserves_independent_core(self):
        adapters, now = self._adapters()
        def fail(*args, **kwargs):
            raise ValueError('TEST_FIXTURE_NATIVE_THEME_FAILURE')
        adapters['theme'] = fail
        result = self._execute(adapters, now)
        self.assertEqual(result['status'], 'COMPLETED_WITH_UNKNOWNS')
        self.assertTrue(result['core_verified'])
        self.assertEqual(result['phases']['theme'], 'FAILED')
        self.assertEqual(result['theme_status'], 'UNKNOWN')
        self.assertEqual(result['display']['counts']['direction_unknown'], 2)
        self.assertEqual(result['phase_failures']['theme']['reason'], 'TEST_FIXTURE_NATIVE_THEME_FAILURE')

    def test_real_news_collector_completes_sampling_without_economic_confirmation(self):
        from stockfish_core.yaogu import premarket_news_v1 as news
        from tests.yaogu.test_premarket_news_v1 import Opener, Response
        adapters, now = self._adapters()
        self.config['capture']['offline'] = False
        article = dict(id=101, title='TEST_FIXTURE 焦点复盘',
            content='财联社9月30日讯，TEST_FIXTURE 报道材料。',
            ctime=int(datetime(2026, 9, 30, 12, tzinfo=timezone.utc).timestamp()))
        raw = ('<script id="__NEXT_DATA__" type="application/json">' +
            json.dumps({'props': {'pageProps': {'articleDetail': article}}}, ensure_ascii=False) +
            '</script>').encode()
        opener = Opener({news.HOMES[0]: Response('<a href="/detail/101">焦点复盘 TEST_FIXTURE</a>'.encode()),
            news.HOMES[1]: Response(b'NO MATCHING ANCHORS TEST_FIXTURE'),
            'https://www.cls.cn/detail/101': Response(raw)})
        adapters['news'] = news.collect_reported_news
        with patch.object(news, '_make_opener', return_value=opener):
            result = self._execute(adapters, now)
        self.assertEqual(result['status'], 'COMPLETE', result.get('error'))
        self.assertEqual(result['phases']['news'], 'COMPLETE')
        self.assertIs(result['news']['summary']['acquisition_complete'], True)
        self.assertEqual(result['news']['summary']['economic_attribution'], 'UNKNOWN')
        self.assertEqual(result['news']['summary']['EVENT_confirmation'], 'UNKNOWN_NOT_ISSUED')
        self.assertEqual(len(opener.calls), 3)

    def test_sticky_theme_budget_failure_is_non_success_and_keeps_saved_core(self):
        adapters, now = self._adapters()
        self.config['limits']['theme_read_bytes'] = 1
        result = self._execute(adapters, now)
        self.assertEqual(result['status'], 'BUDGET_EXCEEDED')
        self.assertTrue(result['core_verified'])
        self.assertIsNone(result['display'])
        self.assertIn('READ_LIMIT_EXCEEDED:theme', result['error']['reason'])
        self.assertTrue(Path(result['core_artifacts']['canonical-run.json.gz']['path']).is_file())

    def test_latest_gate_rejects_cross_release_and_calendar_gap(self):
        rows = [{'calendar_date': '2026-10-14', 'is_trading_day': '1'},
            {'calendar_date': '2026-10-15', 'is_trading_day': '1'},
            {'calendar_date': '2026-10-16', 'is_trading_day': '1'}]
        before = datetime.fromisoformat('2026-10-15T17:59:59+08:00')
        self.assertEqual(workflow._latest_gate(rows, '2026-10-14', '2026-10-15', before)['observation_date'], '2026-10-14')
        with self.assertRaisesRegex(ValueError, 'LATEST_COMPLETED_SESSION_CHANGED'):
            workflow._latest_gate(rows, '2026-10-14', '2026-10-15', before+timedelta(seconds=1))
        with self.assertRaisesRegex(ValueError, 'NATURAL_DAY_GAP'):
            workflow._latest_gate(rows[::2], '2026-10-14', '2026-10-16', before-timedelta(days=1))

    def _supervise(self, script, limits=None):
        self.config['limits']['max_seconds'] = 120
        if limits:
            self.config['limits'].update(limits)
        return active_workflow.supervise(self.config, progress=False,
            _fixture_worker_command=[sys.executable, '-I', '-B', '-c', script])

    def test_supervisor_retains_actual_subprocess_nonzero_and_reaps(self):
        code, result = self._supervise('raise SystemExit(9)')
        self.assertEqual(code, 3)
        self.assertEqual(result['external_worker_exit_code'], 9)
        self.assertTrue(result['external_worker_reaped'])
        self.assertIn('WORKER_EXTERNAL_EXIT_9', result['error']['reason'])

    def test_supervisor_rejects_exit_status_mismatch_and_fake_success(self):
        def writer(status, exit_code):
            value = {'schema_version': active_workflow.VERSION, 'status': status,
                'core_verified': False, 'display': None}
            return ("import pathlib,json; job=next(pathlib.Path("+repr(str(self.root/'state/jobs'))+
                ").iterdir()); (job/'worker-completion.json').write_text("+repr(json.dumps(value))+
                "); raise SystemExit("+str(exit_code)+")")
        code, result = self._supervise(writer('COMPLETE', 2))
        self.assertEqual(code, 3)
        self.assertIn('STATUS_EXIT_MISMATCH', result['error']['reason'])
        # A separate owned state avoids selecting the previous job in the fixture child.
        self.config['state_root'] = str(self.root/'second-state')
        value = {'schema_version': active_workflow.VERSION, 'status': 'COMPLETE', 'core_verified': True,
                 'display': {'artifacts': {}}}
        script = ("import pathlib; job=next(pathlib.Path("+repr(str(self.root/'second-state/jobs'))+
            ").iterdir()); (job/'worker-completion.json').write_text("+repr(json.dumps(value))+
            "); raise SystemExit(0)")
        code, result = self._supervise(script)
        self.assertEqual(code, 3)
        self.assertIn('FULL_CORE_AND_ORIGINAL_REFERENCE_BINDINGS', result['error']['reason'])

    def test_supervisor_timeout_is_real_nonzero_and_owned_worker_reaped(self):
        code, result = self._supervise('import time;time.sleep(30)', {'max_seconds': .15})
        self.assertEqual(code, 4)
        self.assertEqual(result['status'], 'BUDGET_EXCEEDED')
        self.assertTrue(result['external_worker_reaped'])
        self.assertNotEqual(result['external_worker_exit_code'], 0)
        self.assertIn('TOTAL_TIME_LIMIT_EXCEEDED', result['error']['reason'])

    def test_supervisor_read_budget_refuses_before_spawning_worker(self):
        code, result = self._supervise('raise SystemExit(0)', {'supervisor_read_bytes': 1})
        self.assertEqual(code, 4)
        self.assertEqual(result['status'], 'BUDGET_EXCEEDED')
        self.assertIsNone(result['external_worker_exit_code'])
        self.assertIn('SUPERVISOR_READ_LIMIT', result['error']['reason'])

    @unittest.skipUnless(os.name == 'posix', 'POSIX_SESSION_FIXTURE')
    def test_supervisor_rejects_exited_worker_with_live_owned_descendant(self):
        pidfile = self.root/'grandchild.pid'
        script = ('import subprocess,sys,pathlib,time; p=subprocess.Popen([sys.executable,"-c",'
            '"import time;time.sleep(30)"]); pathlib.Path('+repr(str(pidfile))+
            ').write_text(str(p.pid)); time.sleep(1.5); raise SystemExit(0)')
        code, result = self._supervise(script)
        self.assertEqual(code, 3)
        self.assertIn('OWNED_DESCENDANTS_REMAIN', result['error']['reason'])
        self.assertTrue(result['external_worker_reaped'])
        self.assertTrue(result['native_inner_supervision']['cleanup']['all_observed_descendants_gone'])
