"""Explicit TEST_FIXTURE originals only; no network, database, or real archive writes."""
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import json
import multiprocessing
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))
from scripts import codex_core_archive as archive
from scripts import codex_workspace as workspace
from stockfish_core.yaogu import premarket_native_core_v1 as native
from stockfish_core.yaogu import premarket_workflow_v1 as workflow
from stockfish_core.yaogu import provider_normalization
from tests.yaogu.test_premarket_native_core_v1 import captures, FixtureBudget


def _held_lock(root,ready,release):
    runtime = workspace.load_runtime('TEST_FIXTURE-archive',root)
    with runtime.exclusive():
        ready.set()
        release.wait(10)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='stockfish-archive-TEST_FIXTURE-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.report = self.root/'report'
        self.state = self.root/'state'
        self.report.mkdir(); self.state.mkdir()
        self.registry = self.root/'registry'
        config = self.root/'config.json'
        config.write_text(json.dumps(dict(state_root=str(self.state))))
        profile = dict(schema_version=workspace.PROFILE_VERSION,name='TEST_FIXTURE-archive',backend='premarket',
            state_root=str(self.state),report_root=str(self.report),source_root=str(ROOT),
            python=sys.executable,config=str(config),storage=dict(
                roots=[dict(name='fixture',path=str(self.root),role='runtime')],target_root='fixture',
                min_free_bytes=0,max_managed_bytes=512*archive.MIB,expected_growth_bytes=1024))
        profile_path = self.root/'profile.json'
        profile_path.write_text(json.dumps(profile))
        workspace.register_profile(profile_path,self.registry)
        self.runtime = workspace.load_runtime(profile['name'],self.registry)
        self.worker = self.report/'bootstrap/core-context-TEST_FIXTURE'
        self.supervisor = self.report/'bootstrap/supervisor-TEST_FIXTURE'
        self.worker.mkdir(parents=True); self.supervisor.mkdir()
        self.items, observed,target,cutoff = captures(self.root/'originals',sessions=60,history=True)
        from datetime import timedelta
        oldest = str(observed-timedelta(days=24))
        self.items = [item for item in self.items if (lambda result: result['endpoint'] not in ('market_daily','master_all')
            or result['parameters']['date'] >= oldest)(json.loads(Path(item['result']['path']).read_text()))]
        # The old native fixture predates the per-security factor envelope.
        # Build a new explicit fixture receipt with the same real contract;
        # never amend real sources or relax production factor validation.
        for item in self.items:
            result = json.loads(Path(item['result']['path']).read_text())
            if result['endpoint'] != 'adjustment_factors':
                continue
            coverage = dict(requested_codes=result['parameters']['codes'],security_statuses=[
                dict(code=code,status='COMPLETE',cursor_exhausted=True,
                     row_count=sum(row['code']==code for row in result['rows']))
                for code in result['parameters']['codes']])
            raw = json.loads(Path(item['raw']['path']).read_text())
            raw['coverage'] = coverage
            raw_bytes = json.dumps(raw,ensure_ascii=False).encode()
            physical = json.loads(Path(item['receipt']['path']).read_text())
            physical.pop('receipt_id');physical['content_hash']=hashlib.sha256(raw_bytes).hexdigest()
            rid = hashlib.sha256(json.dumps(physical,sort_keys=True).encode()).hexdigest()
            physical['receipt_id'] = rid
            result.update(coverage=coverage,request_receipt_id=rid,receipts=[physical])
            folder = Path(item['raw']['path']).parent
            raw_path = folder/(physical['content_hash']+'.raw.json')
            raw_path.write_bytes(raw_bytes)
            item['raw'] = workflow._ref(raw_path)
            item['receipt'] = self.save(folder/(rid+'.receipt.json'),physical)
            item['result'] = self.save(Path(item['result']['path']).parent/(rid+'.json'),result)
        self.request_path = self.report/'bootstrap/request.json'
        self.request = dict(schema_version='CODEX_FROZEN_CORE_REQUEST_V1',captures=self.items,
            observation_date=str(observed),target_session=str(target),cutoff=cutoff.isoformat(),coverage_type='TEST_FIXTURE')
        self.save(self.request_path,self.request)
        self.invoked = datetime.now(timezone.utc).isoformat()
        self.owner = native.run_core(captures=self.items,observation_date=observed,target_session=target,
            cutoff=cutoff,output_dir=self.worker/'core',budget=FixtureBudget(),coverage_type='TEST_FIXTURE')
        self.owner.validate()
        self.artifacts = self.owner.artifacts
        manifest = json.loads(Path(self.artifacts['manifest.json']['path']).read_text())
        references = self.owner.validation_references + list(self.artifacts.values()) + [workflow._ref(self.request_path)]
        unique = {ref['path']:ref for ref in references}
        references = list(unique.values())
        refs = self.save(self.worker/'core-validation-references.json',dict(schema_version=archive.PRIVATE_VERSION,references=references))
        terminal = self.save(self.worker/'terminal-validation-references.json',dict(schema_version=archive.PRIVATE_VERSION,references=references))
        self.ready = dict(schema_version=archive.PRIVATE_VERSION,status='VERIFIED_ORIGINAL_R2_CORE',strategy_id=archive.STRATEGY,
            source_capability_serialized=False,full_premarket_ready=False,DAILY_V1='UNKNOWN_NOT_EXECUTED',
            core_verified_at=datetime.now(timezone.utc).isoformat(),original_artifacts=self.artifacts,
            validation_references_reference=refs,validation_reference_count=len(references),
            normalization_gap_count=len(manifest['normalization_report'].get('gaps',[])),
            **{key:manifest[key] for key in ('run_id','input_hash','canonical_content_hash','counts','observation_date','target_session','decision_cutoff')})
        ready_ref = self.save(self.worker/'core-ready.json',self.ready)
        display = {}
        (self.worker/'display').mkdir()
        for role,extension in (('text','txt'),('json','json'),('csv','csv')):
            path = self.worker/'display'/('premarket.'+extension)
            path.write_text('TEST_FIXTURE_DISPLAY')
            display[role] = workflow._ref(path)
        self.completion = dict(schema_version=archive.PRIVATE_VERSION,status='COMPLETED_WITH_UNKNOWNS',
            output_directory=str(self.worker),core_verified=True,database_written=False,source_capability_serialized=False,
            full_premarket_ready=False,with_theme=False,phase_failures={},
            phases={key:'COMPLETE' for key in ('input','core','presentation','terminal')},
            core_ready_reference=ready_ref,terminal_validation_reference=terminal,display=dict(artifacts=display))
        completion_ref = self.save(self.worker/'completion.json',self.completion)
        self.helper = self.root/'fixture-helper.py'
        self.helper.write_bytes(b'TEST_FIXTURE_PRIVATE_SUPERVISOR\n')
        self.save(self.worker/'invocation.json',dict(schema_version=archive.PRIVATE_VERSION,
            manifest_path=str(self.request_path),source_root=str(ROOT),interpreter=sys.executable,with_theme=False,
            actual_started_at=self.invoked,script_sha256=workflow._ref(self.helper)['sha256']))
        self.save(self.supervisor/'invocation.json',dict(schema_version=archive.PRIVATE_VERSION,
            worker_job=str(self.worker),request_manifest=str(self.request_path),actual_started_at=self.invoked,
            implementation_references=workflow._code_refs()+[workflow._ref(self.helper)]))
        self.parent = dict(schema_version=archive.PRIVATE_VERSION,status='COMPLETED_WITH_UNKNOWNS',
            worker_job=str(self.worker),worker_completion=completion_ref,core_ready_reference=ready_ref,
            external_worker_exit_code=0,external_worker_reaped=True,core_verified=True,core_terminal_binding_verified=True,
            source_capability_restored_from_JSON=False,full_premarket_ready=False,display=self.completion['display'])
        self.save(self.supervisor/'completion.json',self.parent)
        for target,name in ((socket.socket,'connect'),(sqlite3,'connect')):
            guard = patch.object(target,name,side_effect=AssertionError('TEST_FIXTURE_NO_NETWORK_OR_DB'))
            guard.start();self.addCleanup(guard.stop)
        guard = patch('stockfish_core.yaogu.strategy_experiments.run_short_horizon_strategy',side_effect=AssertionError('TEST_FIXTURE_NO_SELECTOR'))
        guard.start();self.addCleanup(guard.stop)

    def save(self,path,value):
        path.write_text(json.dumps(value,ensure_ascii=False))
        return workflow._ref(path)

    def register(self,**kwargs):
        return archive.register_archive(self.runtime,self.request_path,self.worker,self.supervisor,
            profile=archive.PROFILE,allow_test_fixture=True,**kwargs)

    def reuse(self,first,**kwargs):
        return archive.reuse_archive(self.runtime,first['archive_id'],profile=archive.PROFILE,
            allow_test_fixture=True,**kwargs)

    def test_full_register_then_reload_has_no_normalizer_selector_or_file_growth(self):
        with patch.object(provider_normalization,'normalize_capture_with_report',wraps=provider_normalization.normalize_capture_with_report) as normalizer:
            first = self.register()
            self.assertEqual(normalizer.call_count,1)
            before = {str(path):workflow._ref(path) for path in self.root.rglob('*') if path.is_file()}
            self.runtime = workspace.load_runtime('TEST_FIXTURE-archive',self.registry)
            second = self.reuse(first)
            again = self.register()
            self.assertEqual(normalizer.call_count,1)
        self.assertEqual(first['normalizer_calls'],1)
        self.assertEqual(second['normalizer_calls'],0)
        self.assertEqual(again['normalizer_calls'],0)
        self.assertTrue(second['reused'])
        self.assertFalse(second['source_authority_restored'])
        self.assertEqual(first['summary'],second['summary'])
        self.assertEqual(before,{str(path):workflow._ref(path) for path in self.root.rglob('*') if path.is_file()})
        self.assertEqual(archive.archive_status(self.runtime)['archives'][0]['archive_id'],first['archive_id'])

    def test_failed_parent_never_registers_and_retry_is_not_automatic(self):
        self.parent['external_worker_exit_code'] = 3
        self.save(self.supervisor/'completion.json',self.parent)
        with self.assertRaisesRegex(archive.ArchiveError,'SUCCESSFUL_PARENT_TERMINAL'):
            self.register()
        self.parent['external_worker_exit_code'] = 0
        self.save(self.supervisor/'completion.json',self.parent)
        with self.assertRaisesRegex(workspace.WorkspaceError,'STATE_UNKNOWN_RECONCILE_NO_RETRY'):
            self.register()

    def test_archive_rejects_partial_scope_even_with_matching_saved_core(self):
        self.items.pop()
        self.save(self.request_path,self.request)
        with self.assertRaises(Exception):
            self.register()

    def test_future_request_clock_rejected(self):
        self.request['cutoff'] = '2999-01-01T00:00:00+00:00'
        self.save(self.request_path,self.request)
        with self.assertRaisesRegex(archive.ArchiveError,'FUTURE_CLOCK'):
            self.register()

    def test_same_bytes_replacement_changes_physical_identity(self):
        first = self.register()
        path = Path(self.items[0]['raw']['path'])
        replacement = path.with_suffix('.replacement')
        replacement.write_bytes(path.read_bytes()); replacement.replace(path)
        with self.assertRaisesRegex(ValueError,'PHYSICAL_IDENTITY_CHANGED'):
            self.reuse(first)

    def test_export_mutation_rejected_without_recompute(self):
        first = self.register()
        Path(self.artifacts['observation.csv']['path']).write_text('TEST_FIXTURE_CORRUPTED')
        with self.assertRaises(ValueError):
            self.reuse(first)

    def test_located_evidence_tamper_is_rejected(self):
        first = self.register()
        path = Path(first['receipt']['evidence']['path'])
        value = json.loads(path.read_text()); value['summary']['counts']['watch']=999
        self.save(path,value)
        with self.assertRaises(ValueError):
            self.reuse(first)

    def edit_evidence_and_record(self,first,edit):
        path = Path(first['receipt']['evidence']['path'])
        evidence = json.loads(path.read_text())
        edit(evidence)
        self.save(path,evidence)
        op = self.runtime.directory/'operations'/(first['archive_id']+'.json')
        record = workspace._read(op)
        record['receipt']['evidence'] = workflow._ref(path)
        record['receipt']['summary'] = deepcopy(evidence['summary'])
        workspace._write(op,record,replace=True)

    def test_resealed_evidence_cannot_omit_raw_dependency(self):
        first = self.register()
        missing = self.items[0]['raw']['path']
        self.edit_evidence_and_record(first,lambda value: value.update(
            references=[ref for ref in value['references'] if ref['path'] != missing]))
        with self.assertRaisesRegex(archive.ArchiveError,'REQUIRED_ORIGINAL_CLOSURE_MISSING'):
            self.reuse(first)

    def test_resealed_evidence_cannot_invent_summary(self):
        first = self.register()
        self.edit_evidence_and_record(first,lambda value: value['summary']['counts'].update(watch=9876))
        with self.assertRaisesRegex(archive.ArchiveError,'EVIDENCE_DIFFERS_FROM_ORIGINAL_RESULT'):
            self.reuse(first)

    def test_reuse_still_requires_registered_interpreter(self):
        first = self.register()
        with patch.object(archive.sys,'executable',str(self.root/'other-python')):
            with self.assertRaisesRegex(archive.ArchiveError,'REGISTERED_INTERPRETER_REQUIRED'):
                self.reuse(first)

    def test_query_date_cutoff_and_manifest_must_match(self):
        first = self.register()
        for kwargs in (dict(observation_date='2026-09-29'),dict(target_session='2026-10-02'),
                       dict(cutoff='2026-09-30T14:00:00+00:00')):
            with self.subTest(kwargs=kwargs),self.assertRaisesRegex(archive.ArchiveError,'EXACT_QUERY_CONTEXT_MISMATCH'):
                self.reuse(first,**kwargs)
        other = self.root/'other-request.json'
        self.save(other,self.request)
        self.assertTrue(self.reuse(first,manifest_path=other)['reused'])
        changed = deepcopy(self.request); changed['captures']=changed['captures'][:-1]
        self.save(other,changed)
        with self.assertRaisesRegex(archive.ArchiveError,'EXACT_QUERY_SOURCE_MANIFEST_MISMATCH'):
            self.reuse(first,manifest_path=other)

    def test_tight_read_budget_never_normalizes_or_creates_big_file(self):
        with patch.object(provider_normalization,'normalize_capture_with_report',side_effect=AssertionError('NO_NORMALIZER')):
            with self.assertRaisesRegex(ValueError,'READ_BUDGET'):
                self.register(limits=dict(max_total_read_bytes=1))
        self.assertFalse((self.report/'core-archives').exists())

    def test_profile_is_explicit_and_limit_cannot_expand(self):
        with self.assertRaisesRegex(archive.ArchiveError,'EXPLICIT_READONLY_PROFILE'):
            archive.register_archive(self.runtime,self.request_path,self.worker,self.supervisor,allow_test_fixture=True)
        with self.assertRaisesRegex(archive.ArchiveError,'ONLY_TIGHTEN'):
            self.register(limits=dict(max_total_read_bytes=5*archive.GIB))

    def test_api_fixture_flag_requires_boolean(self):
        with self.assertRaisesRegex(archive.ArchiveError,'FIXTURE_FLAG_MUST_BE_BOOLEAN'):
            archive.register_archive(self.runtime,self.request_path,self.worker,self.supervisor,
                profile=archive.PROFILE,allow_test_fixture='false')

    def test_original_factor_contract_rejects_missing_per_code_exhaustion(self):
        results = [json.loads(Path(item['result']['path']).read_text()) for item in self.items]
        factor = next(value for value in results if value['endpoint']=='adjustment_factors')
        factor['coverage']['security_statuses'][0]['cursor_exhausted'] = False
        from datetime import date
        with self.assertRaisesRegex(ValueError,'FACTOR_COMPLETE_FRAGMENT_NOT_EXHAUSTED'):
            archive._full_scope(results,date.fromisoformat(self.request['observation_date']))

    def test_fixture_cannot_be_loaded_as_real(self):
        with self.assertRaisesRegex(archive.ArchiveError,'FIXTURE_MUST_BE_EXPLICIT'):
            archive.register_archive(self.runtime,self.request_path,self.worker,self.supervisor,profile=archive.PROFILE)

    def test_symlink_parent_is_not_original(self):
        link = self.root/'alias';link.symlink_to(self.worker.parent,target_is_directory=True)
        with self.assertRaisesRegex(archive.ArchiveError,'PATH_ALIAS_FORBIDDEN'):
            archive.register_archive(self.runtime,self.request_path,link/self.worker.name,self.supervisor,
                profile=archive.PROFILE,allow_test_fixture=True)

    def test_other_process_lock_blocks_registration_without_compute(self):
        context = multiprocessing.get_context('fork')
        ready,release = context.Event(),context.Event()
        process = context.Process(target=_held_lock,args=(self.registry,ready,release))
        process.start()
        try:
            self.assertTrue(ready.wait(5))
            with self.assertRaisesRegex(workspace.WorkspaceError,'BUSY_FOLLOW_EXISTING'):
                self.register()
        finally:
            release.set();process.join(5)
            if process.is_alive():process.terminate();process.join()
        self.assertEqual(process.exitcode,0)

    def test_source_closure_omission_is_rejected(self):
        path = self.supervisor/'invocation.json'
        value=json.loads(path.read_text());value['implementation_references'].pop(0)
        self.save(path,value)
        with self.assertRaisesRegex(archive.ArchiveError,'COMPLETE_ORIGINAL_IMPLEMENTATION_CLOSURE'):
            self.register()


    def reseal_core(self):
        """Deliberately forged fixture controls: validation must go beyond hashes."""
        manifest_path = Path(self.artifacts['manifest.json']['path'])
        manifest = json.loads(manifest_path.read_text())
        manifest['artifacts'] = {key: value for key,value in self.artifacts.items() if key != 'manifest.json'}
        for key in manifest['official_exports']:
            manifest['official_exports'][key] = dict(source_content_hash=manifest['canonical_content_hash'],reference=self.artifacts[key])
        self.artifacts['manifest.json'] = self.save(manifest_path,manifest)
        refs_path = self.worker/'core-validation-references.json'
        refs = json.loads(refs_path.read_text())
        replacements = {value['path']:value for value in self.artifacts.values()}
        refs['references'] = [replacements.get(value['path'],value) for value in refs['references']]
        refs_ref = self.save(refs_path,refs)
        terminal = self.save(self.worker/'terminal-validation-references.json',refs)
        self.ready.update(original_artifacts=self.artifacts,validation_references_reference=refs_ref,
            **{key:manifest[key] for key in ('input_hash','canonical_content_hash','counts')})
        ready = self.save(self.worker/'core-ready.json',self.ready)
        self.completion.update(core_ready_reference=ready,terminal_validation_reference=terminal)
        completion = self.save(self.worker/'completion.json',self.completion)
        self.parent.update(core_ready_reference=ready,worker_completion=completion)
        self.save(self.supervisor/'completion.json',self.parent)

    def test_rehashed_and_resealed_input_still_requires_fresh_source_semantics(self):
        from stockfish_core.yaogu.short_horizon_archive_bridge_v1 import digest_json_stream
        path = Path(self.artifacts['input.json.gz']['path'])
        value = json.loads(gzip.decompress(path.read_bytes()))
        value['bars'][0]['amount_cny'] = '987654321'
        new_hash = digest_json_stream(value)
        path.write_bytes(gzip.compress(json.dumps(value).encode(),mtime=0))
        self.artifacts['input.json.gz'] = workflow._ref(path)
        run_path = Path(self.artifacts['canonical-run.json.gz']['path'])
        run = json.loads(gzip.decompress(run_path.read_bytes()))
        run['input_hash'] = new_hash
        run['input_id'] = 'market_'+new_hash
        run['results'][archive.STRATEGY]['input_hash'] = new_hash
        run['content_hash'] = digest_json_stream({key:value for key,value in run.items() if key != 'content_hash'})
        run_path.write_bytes(gzip.compress(json.dumps(run).encode(),mtime=0))
        self.artifacts['canonical-run.json.gz'] = workflow._ref(run_path)
        manifest_path = Path(self.artifacts['manifest.json']['path'])
        manifest = json.loads(manifest_path.read_text())
        manifest.update(input_hash=new_hash,canonical_content_hash=run['content_hash'])
        self.save(manifest_path,manifest)
        self.reseal_core()
        with self.assertRaisesRegex(archive.ArchiveError,'FRESH_NORMALIZATION_DIFFERS_FROM_ORIGINAL'):
            self.register()

    def test_rehashed_consistent_pool_clipping_is_rejected(self):
        from stockfish_core.yaogu.short_horizon_archive_bridge_v1 import digest_json_stream
        path = Path(self.artifacts['canonical-run.json.gz']['path'])
        run = json.loads(gzip.decompress(path.read_bytes()))
        result = run['results'][archive.STRATEGY]
        self.assertGreater(len(result['eligible_pool_ids']),0)
        omitted = result['eligible_pool_ids'].pop()
        result['items'] = [row for row in result['items'] if row['instrument_id'] != omitted]
        result['counts']['eligible'] -= 1
        result['counts']['evaluated'] -= 1
        run['content_hash'] = digest_json_stream({key:value for key,value in run.items() if key != 'content_hash'})
        path.write_bytes(gzip.compress(json.dumps(run).encode(),mtime=0))
        self.artifacts['canonical-run.json.gz'] = workflow._ref(path)
        manifest_path = Path(self.artifacts['manifest.json']['path'])
        manifest = json.loads(manifest_path.read_text())
        manifest.update(counts=result['counts'],canonical_content_hash=run['content_hash'])
        self.save(manifest_path,manifest)
        self.reseal_core()
        with self.assertRaisesRegex(archive.ArchiveError,'COMPLETE_ORIGINAL_ELIGIBILITY_MISMATCH'):
            self.register()

    def test_summary_pass_is_insufficient_for_forged_input(self):
        # A changed compressed input cannot be blessed by only updating its SHA
        # in a view; exact original manifest/terminal references remain required.
        path = Path(self.artifacts['input.json.gz']['path'])
        value=json.loads(gzip.decompress(path.read_bytes()));value['sessions']=value['sessions'][:-1]
        path.write_bytes(gzip.compress(json.dumps(value).encode(),mtime=0))
        with self.assertRaises(ValueError):
            self.register()


if __name__ == '__main__':
    unittest.main()
