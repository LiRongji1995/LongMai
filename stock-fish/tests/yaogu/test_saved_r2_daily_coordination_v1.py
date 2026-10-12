"""Small isolated TEST_FIXTURE only; no observed RAW, DB or strategy jobs."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.dont_write_bytecode = True
from stockfish_core.yaogu import strategy_experiments  # established import order
from stockfish_core.yaogu import saved_r2_daily_coordination_v1 as daily
from stockfish_core.yaogu import saved_r2_delivery as base
from stockfish_core.yaogu.contracts import MarketInput
from stockfish_core.yaogu.history_artifacts import digest_json_stream, digest_market_input
from tests.support.saved_r2_v3_fixture import fixture, physical_source, write

SH = ZoneInfo('Asia/Shanghai')


def calendar(root, observed, target, instant, *, flags=None, rows=None, end=None, seen=None):
    """A labelled synthetic original trio, never a claimed exchange schedule."""
    start, end = observed - timedelta(days=2), end or target + timedelta(days=2)
    flags = flags or {}
    if rows is None:
        rows = [{'calendar_date': str(start + timedelta(days=i)),
                 'is_trading_day': flags.get(start + timedelta(days=i),
                     '1' if (start + timedelta(days=i)).weekday() < 5 else '0')}
                for i in range((end - start).days + 1)]
    return physical_source(root, 'calendar', rows, seen or instant - timedelta(seconds=2),
        day=end, parameters={'start_date': str(start), 'end_date': str(end)}, include_result=True)


def changed_bundle(bundle, day):
    value = bundle.model_dump(mode='json')
    value['observation_date'] = str(day)
    value['sessions'] = sorted(set([d for d in value['sessions'] if d <= str(day)] + [str(day)]))
    return MarketInput.model_validate(value)


def current_fixture(root, *, pool=4, selected=3):
    bundle, _, run, sources, _, paths = fixture(root, pool_count=pool,
        selected_count=selected, sessions_count=0, include_themes=False)
    instant = daily._now().astimezone(SH)
    observed = instant.date()
    if instant.time() < time(15):
        observed -= timedelta(days=1)
    while observed.weekday() >= 5:
        observed -= timedelta(days=1)
    target = observed + timedelta(days=1)
    while target.weekday() >= 5:
        target += timedelta(days=1)
    bundle = changed_bundle(bundle, observed)
    ih = digest_market_input(bundle)
    run.update(input_hash=ih, observation_date=str(observed), target_session=str(target),
        coverage_type='TEST_FIXTURE', decision_cutoff=(instant-timedelta(seconds=5)).isoformat(),
        generated_at=(instant-timedelta(seconds=4)).isoformat(),
        prospective_validation={'target_session': str(target)})
    run['results'][daily.STRATEGY]['input_hash'] = ih
    run['content_hash'] = digest_json_stream({k: v for k, v in run.items() if k != 'content_hash'})
    sources['captures'].append(calendar(root/'calendar-original', observed, target, instant))
    write(paths['run'], run)
    write(paths['input'], bundle.model_dump(mode='json'))
    write(paths['sources'], sources)
    return bundle, run, sources, paths


class DailyCoordinationFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='stockfish-daily-TEST_FIXTURE-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def test_dynamic_full_pool_counts_and_unchanged_selection(self):
        for pool, selected in ((0, 0), (2, 1), (17, 13)):
            with self.subTest(pool=pool):
                _, run, _, paths = current_fixture(self.root/str(pool), pool=pool, selected=selected)
                binding = daily._binding_from_run(run, base)
                self.assertEqual(binding['counts']['eligible'], pool)
                self.assertEqual(binding['counts']['watch'], selected)
                self.assertEqual(len(binding['selected_ids']), selected)
                before = paths['run'].read_bytes()
                manifest = {'schema_version': base.RUN_SCHEMA,
                    'run_sha256': hashlib.sha256(before).hexdigest(),
                    'input_sha256': hashlib.sha256(paths['input'].read_bytes()).hexdigest(),
                    'sources_manifest_sha256': hashlib.sha256(paths['sources'].read_bytes()).hexdigest(),
                    'binding': binding}
                path = self.root/str(pool)/'dynamic-manifest.json'
                write(path, manifest)
                _, frozen, retained, _, _, _ = base._freeze_run(base._Registry(), paths['run'],
                    path, paths['input'], paths['sources'], daily._now())
                self.assertEqual(frozen['counts'], binding['counts'])
                self.assertEqual([r['instrument_id'] for r in retained], binding['selected_ids'])
                self.assertEqual(paths['run'].read_bytes(), before)

    def test_target_hash_and_inner_binding_rejections(self):
        _, run, _, _ = current_fixture(self.root/'original')
        run['target_session'] = str(date.fromisoformat(run['target_session'])+timedelta(days=1))
        with self.assertRaisesRegex(ValueError, 'RUN_CONTENT_HASH_MISMATCH'):
            daily._binding_from_run(run, base)
        run['content_hash'] = digest_json_stream({k:v for k,v in run.items() if k != 'content_hash'})
        with self.assertRaisesRegex(ValueError, 'SAVED_PROSPECTIVE_TARGET_CONFLICT'):
            daily._binding_from_run(run, base)
        run['prospective_validation']['target_session'] = run['target_session']
        run['results'][daily.STRATEGY]['target_session'] = '2026-01-01'
        run['content_hash'] = digest_json_stream({k:v for k,v in run.items() if k != 'content_hash'})
        with self.assertRaisesRegex(ValueError, 'SAVED_INNER_TARGET_CONFLICT'):
            daily._binding_from_run(run, base)

    def guard(self, observed, target, instant, *, registrations=None, flags=None, rows=None, end=None):
        bundle, _, _, _, _, _ = fixture(self.root/'base', sessions_count=0, include_themes=False)
        bundle = changed_bundle(bundle, observed)
        source = registrations or [calendar(self.root/'calendar', observed, target, instant,
            flags=flags, rows=rows, end=end)]
        binding = {'target_session': str(target), 'original_decision_cutoff': '2026-01-01T00:00:00Z'}
        return daily._calendar_guard(bundle, binding, {'captures': source}, self.root,
                                     base._Registry(), instant)

    def test_shanghai_close_friday_monday_and_holiday(self):
        friday, monday = date(2026, 10, 9), date(2026, 10, 12)
        value = self.guard(friday, monday, datetime(2026,10,12,14,59,59,tzinfo=SH))
        self.assertEqual(value['last_completed_session'], str(friday))
        self.assertTrue(value['new_context_only_not_original_run_PIT'])
        with self.assertRaisesRegex(ValueError, 'SAVED_OBSERVATION_IS_NOT_CURRENT_LAST_COMPLETED_SESSION'):
            self.guard(friday, monday, datetime(2026,10,12,15,tzinfo=SH))
        holidays = {date(2026,10,i): '0' for i in range(1,9)}
        self.assertEqual(self.guard(date(2026,9,30), friday, datetime(2026,10,8,20,tzinfo=SH),
            flags=holidays)['next_session'], str(friday))

    def test_calendar_scope_duplicate_missing_and_nontrading(self):
        observed, target = date(2026,10,9), date(2026,10,12)
        instant = datetime(2026,10,10,12,tzinfo=SH)
        original = calendar(self.root/'baseline', observed, target, instant)
        rows = json.loads(Path(original['raw']['path']).read_bytes())['rows']
        for altered in (rows + [rows[0]], rows[1:]):
            with self.subTest(rows=len(altered)), self.assertRaisesRegex(ValueError, 'CONTEXT_CALENDAR_'):
                self.guard(observed, target, instant, rows=altered)
        with self.assertRaisesRegex(ValueError, 'CONTEXT_CALENDAR_FULL_DATE_SCOPE_UNVERIFIED'):
            self.guard(observed, target, instant, flags={observed:'0'})
        with self.assertRaisesRegex(ValueError, 'CURRENT_COMPLETE_ORIGINAL_CALENDAR_UNAVAILABLE'):
            self.guard(observed, target, instant, end=observed)
        with self.assertRaisesRegex(ValueError, 'CONTEXT_TARGET_IS_NOT_VERIFIED_NEXT_TRADING_SESSION'):
            self.guard(observed, target+timedelta(days=1), instant)

    def test_same_clock_conflict_and_late_calendar_rejection(self):
        observed, target = date(2026,10,9), date(2026,10,12)
        instant = datetime(2026,10,10,12,tzinfo=SH)
        a = calendar(self.root/'a', observed, target, instant)
        b = calendar(self.root/'b', observed, target, instant, flags={target:'0'})
        with self.assertRaisesRegex(ValueError, 'CURRENT_CALENDAR_SAME_CLOCK_CONFLICT'):
            self.guard(observed, target, instant, registrations=[a,b])
        future = calendar(self.root/'late', observed, target, instant, seen=instant+timedelta(seconds=1))
        with self.assertRaisesRegex(ValueError, 'CURRENT_COMPLETE_ORIGINAL_CALENDAR_UNAVAILABLE'):
            self.guard(observed, target, instant, registrations=[future])

    def test_budget_and_bounded_reads(self):
        with self.assertRaisesRegex(ValueError, 'CANNOT_BE_EXPANDED'):
            replace(daily.Limits(), wall_seconds=1801).validate()
        p = self.root/'bounded.json'
        p.write_bytes(b'{}')
        record = daily._pin(p, limit=16)
        p.write_bytes(b' ' * 17)
        with self.assertRaisesRegex(ValueError, 'FINGERPRINT_CHANGED'):
            daily._read(record)
        with self.assertRaisesRegex(ValueError, 'OVERSIZE'):
            daily._pin(p, limit=16)

    def prepare(self, paths, job, **extra):
        kwargs = {key+'_path': paths[key] for key in ('run', 'input')}
        kwargs.update({key+'_sha256': hashlib.sha256(paths[key].read_bytes()).hexdigest()
                       for key in ('run', 'input')})
        return daily.prepare_daily_saved_r2(**kwargs, sources_manifest_path=paths['sources'],
            sources_manifest_sha256=hashlib.sha256(paths['sources'].read_bytes()).hexdigest(),
            output_dir=job, test_fixture=True, **extra)

    def test_real_small_v3_child_complete_and_repeated_claim_rejected(self):
        _, _, _, paths = current_fixture(self.root/'original', pool=17, selected=13)
        record = self.prepare(paths, self.root/'job')
        actual = daily.execute_daily_saved_r2(plan_path=record['path'], plan_sha256=record['sha256'])
        self.assertEqual(actual['status'], 'PASS', (actual.get('reason'),
            (actual.get('worker') or {}).get('error')))
        self.assertEqual(actual['worker']['binding']['counts']['eligible'], 17)
        self.assertEqual(len(actual['worker']['binding']['selected_ids']), 13)
        self.assertEqual(actual['worker']['native_attempts_lower_bound']['delivery'], 1)
        self.assertEqual(actual['worker']['denied_selector_attempts_lower_bound'], 0)
        self.assertFalse(actual['continuous_unattended_delivery_validated'])
        self.assertEqual(json.loads((self.root/'job/execution-seal.json').read_bytes())['status'], 'PASS')
        snapshots = {p: (p.read_bytes(), daily._fp(p)) for p in (self.root/'job').rglob('*') if p.is_file()}
        with self.assertRaises(FileExistsError):
            daily.execute_daily_saved_r2(plan_path=record['path'], plan_sha256=record['sha256'])
        self.assertTrue(all((p.read_bytes(),daily._fp(p)) == v for p,v in snapshots.items()))

    def test_stale_rehashed_saved_target_rejected_without_native_or_mutation(self):
        _, run, _, paths = current_fixture(self.root/'original')
        run['target_session'] = str(date.fromisoformat(run['target_session'])+timedelta(days=1))
        run['prospective_validation']['target_session'] = run['target_session']
        run['content_hash'] = digest_json_stream({k:v for k,v in run.items() if k != 'content_hash'})
        write(paths['run'], run)
        before = paths['run'].read_bytes()
        record = self.prepare(paths, self.root/'job')
        actual = daily.execute_daily_saved_r2(plan_path=record['path'], plan_sha256=record['sha256'])
        self.assertEqual(actual['status'], 'FAILED')
        worker = json.loads((self.root/'job/worker-result.json').read_bytes())
        self.assertEqual(worker['native_attempts_lower_bound']['delivery'], 0)
        self.assertEqual(worker['error']['reason'], 'CONTEXT_TARGET_IS_NOT_VERIFIED_NEXT_TRADING_SESSION')
        self.assertEqual(paths['run'].read_bytes(), before)
        with self.assertRaises(FileExistsError):
            daily.execute_daily_saved_r2(plan_path=record['path'], plan_sha256=record['sha256'])

    def test_original_generated_visibility_and_daily_close_cutoff(self):
        for key, reason in (('generated', 'ORIGINAL_RUN_GENERATED_NOT_YET_VISIBLE'),
                            ('close', 'ORIGINAL_DAILY_RUN_CUTOFF_PRECEDES_OBSERVATION_CLOSE')):
            with self.subTest(key=key):
                _, run, _, paths = current_fixture(self.root/key, pool=2, selected=1)
                if key == 'generated':
                    run['generated_at'] = (daily._now()+timedelta(hours=1)).isoformat()
                else:
                    run['decision_cutoff'] = datetime.combine(date.fromisoformat(run['observation_date']),
                                                             time(14,59,59), SH).isoformat()
                run['content_hash'] = digest_json_stream({k:v for k,v in run.items() if k != 'content_hash'})
                write(paths['run'], run)
                record = self.prepare(paths, self.root/(key+'-job'))
                actual = daily.execute_daily_saved_r2(plan_path=record['path'], plan_sha256=record['sha256'])
                self.assertEqual(actual['status'], 'FAILED')
                worker = json.loads((self.root/(key+'-job')/'worker-result.json').read_bytes())
                self.assertEqual(worker['error']['reason'], reason)
                self.assertEqual(worker['native_attempts_lower_bound']['delivery'], 0)

    def test_concurrent_execute_has_one_real_child_and_one_delivery(self):
        _, _, _, paths = current_fixture(self.root/'original', pool=2, selected=1)
        record = self.prepare(paths, self.root/'job')
        argv = [sys.executable, '-I', '-B', str(Path(daily.__file__).resolve()),
            '--mode','execute','--plan',record['path'],'--plan-sha256',record['sha256']]
        processes = [subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
        outputs = [p.communicate(timeout=20) for p in processes]
        self.assertEqual(sorted(p.returncode for p in processes), [0,1], outputs)
        events = [json.loads(row) for row in (self.root/'job/native-events.jsonl').read_text().splitlines()]
        self.assertEqual(sum(e['event']=='NATIVE_ENTERED' and e['key']=='delivery' for e in events), 1)
        self.assertEqual(json.loads((self.root/'job/execution-seal.json').read_bytes())['status'], 'PASS')

    def test_control_fingerprint_change_is_explicit_in_failure(self):
        p = self.root/'control.json'
        p.write_bytes(b'{}')
        record = daily._pin(p)
        os.utime(p, ns=(p.stat().st_atime_ns, p.stat().st_mtime_ns+1000))
        plan = {'source_registry': [], 'implementation_registry': [], 'runtime': {'files': []}}
        with self.assertRaisesRegex(ValueError, 'FINGERPRINT_CHANGED') as caught:
            daily._terminal(plan, record)
        self.assertIn('st_mtime_ns', caught.exception.identity_checks[0]['changed_fields'])

    def test_outer_reauthenticates_original_calendar_across_new_close(self):
        observed, target = date(2026,10,9), date(2026,10,12)
        before = datetime(2026,10,12,14,59,59,tzinfo=SH)
        source = calendar(self.root/'outer-calendar', observed, target, before)
        p = self.root/'outer-sources.json'
        write(p, {'captures':[source]})
        plan = {'test_fixture':True, 'records':{'sources':daily._pin(p)}}
        worker = {'calendar':{'coverage_type':'TEST_FIXTURE'}, 'binding':{
            'observation_date':str(observed),'target_session':str(target),
            'original_decision_cutoff':datetime(2026,10,9,16,tzinfo=SH).isoformat()}}
        with patch.object(daily, '_now', return_value=before):
            self.assertEqual(daily._outer_calendar(plan,worker)['last_completed_session'], str(observed))
        with patch.object(daily, '_now', return_value=datetime(2026,10,12,15,tzinfo=SH)):
            with self.assertRaisesRegex(ValueError, 'SAVED_OBSERVATION_IS_NOT_CURRENT_LAST_COMPLETED_SESSION'):
                daily._outer_calendar(plan,worker)

    def test_prepare_post_write_budget_failure_cannot_execute_registered_plan(self):
        _, _, _, paths = current_fixture(self.root/'original', pool=2, selected=1)
        clock = [0.0]
        save = daily._save
        def last_write(*args, **kwargs):
            value = save(*args, **kwargs)
            if Path(args[0]).name == 'registered-plan.json':
                clock[0] = 121.0
            return value
        with patch.object(daily.time, 'monotonic', side_effect=lambda:clock[0]), \
                patch.object(daily, '_save', side_effect=last_write):
            with self.assertRaisesRegex(ValueError, 'PREPARATION_FINAL_WRITE_BUDGET_EXCEEDED'):
                self.prepare(paths,self.root/'job')
        self.assertEqual(json.loads((self.root/'job/prepare-failure.json').read_bytes())['status'], 'FAILED')
        record = daily._pin(self.root/'job/registered-plan.json')
        with self.assertRaisesRegex(ValueError, 'FAILED_PREPARATION_NOT_EXECUTABLE'):
            daily.execute_daily_saved_r2(plan_path=record['path'], plan_sha256=record['sha256'])

    def supervisor_fixture(self, suffix, producer, *, wall=5, ps_failure=False, late_growth=False,
                           late_claim_change=False, late_seal_change=False,
                           missed_sample=False, process_limit=None):
        """Real tiny child; replacing argv exercises outer resource boundary only."""
        job = self.root/suffix
        job.mkdir()
        limits = replace(daily.Limits(), wall_seconds=wall, output_bytes=256*1024,
                         per_output_bytes=128*1024, reserve_bytes=64*1024)
        if process_limit is not None:
            limits = replace(limits, process_rss_bytes=process_limit)
        runtime = {'executable': sys.executable, 'files': []}
        plan = {'schema_version': daily.VERSION, 'status':'REGISTERED_NOT_EXECUTED',
                'limits': vars(limits), 'output_dir':str(job), 'source_registry':[],
                'implementation_registry':[], 'runtime':runtime, 'source_identity':{}}
        write(job/'registered-plan.json', plan)
        record = daily._pin(job/'registered-plan.json')
        real_popen = subprocess.Popen
        original_save = daily._save
        original_seal = daily._seal
        def seal(*args, **kwargs):
            record = original_seal(*args, **kwargs)
            if late_seal_change and args[1].get('status') == 'PASS':
                p = job/'execution-claim.json'
                os.utime(p, ns=(p.stat().st_atime_ns,p.stat().st_mtime_ns+1000000))
            return record
        def save(*args, **kwargs):
            result = original_save(*args, **kwargs)
            if late_growth and Path(args[0]).name == 'actual-execution.json':
                (job/'last-growth.jsonl').write_bytes(b'x'*150000)
            if late_claim_change and Path(args[0]).name == 'actual-execution.json':
                p = job/'execution-claim.json'
                os.utime(p, ns=(p.stat().st_atime_ns,p.stat().st_mtime_ns+1000000))
            return result
        code = ('import os,json,time,resource,sys\nfrom pathlib import Path\n'
                'job=Path('+repr(str(job))+')\n'+producer+'\n'
                'rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n'
                'rss=rss if sys.platform=="darwin" else rss*1024\n'
                'v={"status":"PASS","pid":os.getpid(),"parent_pid":os.getppid(),"OS_peak_RSS_bytes":rss}\n'
                '(job/"worker-result.json").write_text(json.dumps(v))\n')
        def launch(*args, **kwargs):
            if args[0][0] == '/bin/ps':
                return real_popen(*args, **kwargs)
            return real_popen([sys.executable, '-I', '-B', '-c', code], **kwargs)
        with patch.object(daily, '_runtime', return_value=runtime), patch.object(daily, 'source_identity', return_value={}), \
                patch.object(subprocess, 'Popen', side_effect=launch), patch.object(daily, '_save', side_effect=save), \
                patch.object(daily, '_seal', side_effect=seal), \
                patch.object(daily, '_outer_calendar', return_value={'last_completed_session':'TEST_FIXTURE',
                    'verified_at':'RESOURCE_OUTER_ONLY_NOT_CALENDAR_AUTHENTICATION'}):
            if ps_failure:
                with patch.object(daily, '_tree_rss', side_effect=TimeoutError('TEST_FIXTURE_PS_TIMEOUT')):
                    result = daily.execute_daily_saved_r2(plan_path=record['path'], plan_sha256=record['sha256'])
            elif missed_sample:
                with patch.object(daily, '_tree_rss', side_effect=lambda pid:(1024,1024,{pid})):
                    result = daily.execute_daily_saved_r2(plan_path=record['path'], plan_sha256=record['sha256'])
            else:
                result = daily.execute_daily_saved_r2(plan_path=record['path'], plan_sha256=record['sha256'])
        seal = json.loads((job/'execution-seal.json').read_bytes())
        self.assertEqual(seal['status'], result['status'])
        self.assertEqual((job/'execution-seal.json').stat().st_size, daily.SEAL_BYTES)
        return result, job

    def test_terminal_output_overshoot_still_sealed_failed(self):
        actual, job = self.supervisor_fixture('overshoot', '(job/"journal.jsonl").write_bytes(b"x"*150000)')
        self.assertEqual(actual['status'], 'FAILED')
        self.assertEqual(json.loads((job/'execution-seal.json').read_bytes())['actual_exit_code'], 1)

    def test_growth_after_detailed_actual_cannot_leave_final_pass(self):
        actual, job = self.supervisor_fixture('final-growth', 'pass', late_growth=True)
        self.assertEqual(actual['status'], 'FAILED')
        self.assertEqual(json.loads((job/'actual-execution.json').read_bytes())['status'], 'PASS')
        self.assertEqual(json.loads((job/'execution-seal.json').read_bytes())['status'], 'FAILED')

    def test_missing_claim_sealed_and_late_claim_change_rejected(self):
        actual, job = self.supervisor_fixture('claim-missing', '(job/"execution-claim.json").unlink()')
        self.assertEqual(actual['status'], 'FAILED')
        self.assertIsNone(actual['claim_terminal_fingerprint'])
        self.assertEqual(json.loads((job/'execution-seal.json').read_bytes())['status'], 'FAILED')
        actual, job = self.supervisor_fixture('claim-late-change', 'pass', late_claim_change=True)
        self.assertEqual(actual['status'], 'FAILED')
        seal = json.loads((job/'execution-seal.json').read_bytes())
        self.assertIn('st_mtime_ns', seal['final_identity_failure']['changed_fields'])
        actual, job = self.supervisor_fixture('claim-after-seal', 'pass', late_seal_change=True)
        self.assertEqual(actual['status'], 'FAILED')
        seal = json.loads((job/'execution-seal.json').read_bytes())
        self.assertIn('st_mtime_ns', seal['final_identity_failure']['changed_fields'])

    def test_growing_journal_is_monitored_before_producer_finishes(self):
        producer = ('with (job/"producer-journal.jsonl").open("wb") as h:\n'
                    ' for i in range(20):\n  h.write(b"x"*20000)\n  h.flush()\n  time.sleep(.1)')
        actual, job = self.supervisor_fixture('live-journal-growth', producer)
        self.assertEqual(actual['status'], 'FAILED')
        self.assertLess(actual['actual_child_exit_code'], 0)
        self.assertLess((job/'producer-journal.jsonl').stat().st_size, 400000)

    def test_missed_final_rss_sample_uses_conservative_reaped_children_peak(self):
        limit = 48*1024**2
        producer = ('rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n'
            'rss=rss if sys.platform=="darwin" else rss*1024\n'
            '(job/"worker-result.json").write_text(json.dumps({"status":"PASS","pid":os.getpid(),'
            '"parent_pid":os.getppid(),"OS_peak_RSS_bytes":rss}))\n'
            'memory=bytearray(64*1024**2)\nos._exit(0)')
        actual, _ = self.supervisor_fixture('rss-after-result', producer,
            missed_sample=True, process_limit=limit)
        self.assertEqual(actual['status'], 'FAILED')
        self.assertEqual(actual['actual_child_exit_code'], 0)
        self.assertGreater(actual['parent_children_ru_maxrss_terminal_bytes'], limit)
        self.assertIn('NOT_UNIQUE_WORKER', actual['parent_children_ru_maxrss_scope'])

    def test_strict_ps_table_rejects_empty_malformed_and_live_missing(self):
        for stdout in ('', 'bad row\n', '1 0 1 junk\n', '1 0 1 1\n1 0 1 1\n'):
            with self.subTest(stdout=stdout), patch.object(subprocess, 'run', return_value=type('P',(),{'stdout':stdout})()):
                with self.assertRaisesRegex(ValueError, 'PS_PROCESS_TABLE_'):
                    daily._tree_rss(os.getpid())
        with patch.object(subprocess, 'run', return_value=type('P',(),{'stdout':'1 0 1 1\n'})()):
            with self.assertRaisesRegex(ValueError, 'PS_LIVE_WORKER_RSS_UNAVAILABLE_UNKNOWN'):
                daily._tree_rss(os.getpid())

    def test_ps_failure_kill_reap_and_wall_failure(self):
        actual, _ = self.supervisor_fixture('ps-failed', 'time.sleep(3)', ps_failure=True)
        self.assertEqual(actual['status'], 'FAILED')
        self.assertLess(actual['actual_child_exit_code'], 0)
        actual, _ = self.supervisor_fixture('wall-failed', 'time.sleep(3)', wall=.3)
        self.assertEqual(actual['status'], 'FAILED')
        self.assertLess(actual['actual_child_exit_code'], 0)

    @unittest.skipUnless(hasattr(os,'fork'), 'requires POSIX process groups')
    def test_residual_descendant_is_failed_and_reaped(self):
        actual, _ = self.supervisor_fixture('residual', 'p=os.fork()\nif p==0:\n time.sleep(3)\n os._exit(0)')
        self.assertEqual(actual['status'], 'FAILED')
        self.assertEqual(actual['reason']['reason'], 'WORKER_DESCENDANT_REMAINS_AFTER_EXIT')


if __name__ == '__main__':
    unittest.main()
