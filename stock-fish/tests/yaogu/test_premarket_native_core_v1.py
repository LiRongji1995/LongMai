"""Isolated TEST_FIXTURE originals; no network, DB, SDK or real market work."""
from datetime import date, datetime, timedelta, timezone
from copy import deepcopy
import csv
import gzip
import hashlib
import io
import json
from pathlib import Path
import pickle
from unittest.mock import patch

import tempfile
import unittest

from stockfish_core.yaogu import premarket_native_core_v1 as core
from stockfish_core.yaogu import premarket_theme_context_v1 as themes
from stockfish_core.yaogu.history_artifacts import digest_json_stream
from tests.support.saved_r2_v3_fixture import physical_source, write


class FixtureBudget:
    def __init__(self):
        self.metrics = {'returned_read_bytes': 0}

    def check(self, stage):
        pass

    def read(self, path, expected=None, limit=None):
        path = Path(path).resolve(strict=True)
        raw = path.read_bytes()
        if limit is not None and len(raw) > limit:
            raise RuntimeError('TEST_FIXTURE_READ_LIMIT')
        stat = path.stat()
        ref = dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw),
            fp={key: getattr(stat, key, 0) for key in ('st_dev', 'st_ino', 'st_mode',
                'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_flags')})
        if isinstance(expected, str) and ref['sha256'] != expected:
            raise ValueError('TEST_FIXTURE_SHA_CHANGED')
        if isinstance(expected, dict) and any(ref[key] != value for key, value in expected.items()):
            raise ValueError('TEST_FIXTURE_IDENTITY_CHANGED')
        self.metrics['returned_read_bytes'] += len(raw)
        return raw, ref


def captures(tmp_path, *, sessions=60, history=False):
    observed = date(2026, 9, 30)
    target = observed + timedelta(days=1)
    seen = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
    start = observed-timedelta(days=sessions-1)
    items = []
    def add(endpoint, rows, parameters=None, day=None, version='TEST_FIXTURE_PROVIDER_V1'):
        if endpoint == 'adjustment_factors':
            from stockfish_core.yaogu import providers
            class Clock(datetime):
                @classmethod
                def now(cls, tz=None):
                    return seen.astimezone(tz or timezone.utc)
            root = tmp_path/'capture'
            payload = dict(status='COMPLETE', pagination_complete=True, field_version=version,
                first_seen_at=seen.isoformat(), rows=rows, coverage={}, gaps=[])
            with patch.object(providers, 'datetime', Clock):
                receipt = providers.archive_receipt(root/'cache/raw', 'baostock', endpoint,
                    parameters, payload, status='COMPLETE', pagination_complete=True,
                    field_version=version, first_seen_at=seen.isoformat(),
                    source_url='https://TEST_FIXTURE.invalid/original')
            rid = receipt['receipt_id']
            result = dict(**payload, provider='baostock', endpoint=endpoint,
                parameters=parameters, request_receipt_id=rid, receipts=[receipt],
                started_at=seen.isoformat(), completed_at=seen.isoformat())
            item = dict(archive_root=str(root), result=write(root/'results'/(rid+'.json'), result),
                raw={'path': str(root/'cache/raw'/(receipt['content_hash']+'.raw.json')),
                     'sha256': receipt['content_hash']},
                receipt={'path': str(root/'cache/raw'/(rid+'.receipt.json')),
                    'sha256': hashlib.sha256((root/'cache/raw'/(rid+'.receipt.json')).read_bytes()).hexdigest()})
        else:
            item = physical_source(tmp_path/'capture', endpoint, rows, seen,
                day=day, parameters=parameters, version=version, include_result=True)
        items.append(item)
    add('calendar', [dict(calendar_date=str(start+timedelta(days=n)), is_trading_day='1')
        for n in range(sessions+1)], parameters={'start_date': str(start), 'end_date': str(target)},
        version='TEST_FIXTURE_CALENDAR_V1')
    add('mainboard_sse', [{'证券代码': '600001', '证券简称': 'TEST_FIXTURE_SH', '上市日期': '2010-01-01'}], {})
    add('master_szse', [dict(A股代码=code, A股简称='TEST_FIXTURE_SZ',
        A股上市日期='2010-01-01', 板块='主板')
        for code in (('000001', '000002') if history else ('000001',))], {})
    add('delisting_sse', [], {})
    for day in (tuple(start+timedelta(days=n) for n in range(sessions)) if history else (observed,)):
        codes = ('sh.600001', 'sz.000001', 'sz.000002') if history else ('sh.600001', 'sz.000001')
        add('master_all', [dict(code=code, tradeStatus='1', code_name='TEST_FIXTURE')
            for code in codes], {'date': str(day)}, day,
            version='BAOSTOCK_00.9.30_R03_V1')
        add('market_daily', [dict(code=code, date=str(day), adjustflag='3',
            open='10', high='11' if day == observed and code != 'sz.000002' else '10.1', low='9.9',
            close='11' if day == observed and code != 'sz.000002' else '10', preclose='10',
            amount='500000000' if day == observed else '100000000',
            volume='1000', turn='6' if history else '3', tradestatus='1', isST='0')
            for code in codes], {'date': str(day)}, day,
            version='BAOSTOCK_00.9.30_R03_V1')
    if history:
        add('adjustment_factors', [dict(code=code, dividOperateDate='2010-01-01',
            backAdjustFactor='1', foreAdjustFactor='1', adjustFactor='1')
            for code in codes], parameters={
                'codes': list(codes), 'start_date': '1990-01-01',
                'end_date': str(observed)}, day=observed,
            version='BAOSTOCK_00.9.30_R03_V1')
    for item in items:
        for role in ('result', 'raw', 'receipt'):
            item[role] = FixtureBudget().read(item[role]['path'], expected=item[role]['sha256'])[1]
    return items, observed, target, seen+timedelta(hours=1)


def execute(tmp_path, **kwargs):
    items, observed, target, cutoff = captures(tmp_path)
    owner = core.run_core(captures=items, observation_date=observed, target_session=target,
        cutoff=cutoff, output_dir=tmp_path/'core', budget=FixtureBudget(),
        coverage_type='TEST_FIXTURE', **kwargs)
    return owner, items, cutoff


class NativeBackendFixtureTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='stockfish-TEST_FIXTURE-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        import socket
        import sqlite3
        self.setattr(socket.socket, 'connect', lambda *a, **k: self.fail('TEST_FIXTURE_NETWORK'))
        self.setattr(sqlite3, 'connect', lambda *a, **k: self.fail('TEST_FIXTURE_DB'))

    def setattr(self, target, name, value):
        guard = patch.object(target, name, value)
        guard.start()
        self.addCleanup(guard.stop)

    def assert_canonical_and_five_exports(self, owner):
        from stockfish_core.yaogu.candidate_service import CandidateService
        artifacts = owner.artifacts
        canonical = json.loads(gzip.decompress(Path(artifacts['canonical-run.json.gz']['path']).read_bytes()))
        expected = digest_json_stream({key: value for key, value in canonical.items() if key != 'content_hash'})
        self.assertEqual(canonical['content_hash'], expected)
        manifest = json.loads(Path(artifacts['manifest.json']['path']).read_bytes())
        self.assertEqual(manifest['canonical_content_hash'], expected)
        self.assertEqual(set(manifest['official_exports']),
            {'all.csv','observation.csv','focus.csv','unselected.csv','observation.json'})
        for filename, value in manifest['official_exports'].items():
            self.assertEqual(value['source_content_hash'], expected)
            self.assertEqual(value['reference'], artifacts[filename])
        class OriginalEncoder(CandidateService):
            def strategy_experiment(inner, run_id):
                self.assertEqual(run_id, canonical['run_id'])
                return canonical
        encoder = object.__new__(OriginalEncoder)
        for selection in ('all','observation','focus','unselected'):
            actual = Path(artifacts[selection+'.csv']['path']).read_bytes()
            regenerated = encoder.export_strategy_experiment_csv(canonical['run_id'],
                strategy_id=core.STRATEGY, selection=selection).encode()
            self.assertEqual(actual, regenerated)
            self.assertTrue(all(row['run_id'] == canonical['run_id'] for row in
                csv.DictReader(io.StringIO(actual.decode()))))
        observation = json.loads(Path(artifacts['observation.json']['path']).read_bytes())
        self.assertEqual(observation['export_view']['source_content_hash'], expected)
        self.assertEqual(observation, json.loads(encoder.export_strategy_experiment_json(
            canonical['run_id'], strategy_id=core.STRATEGY, selection='observation')))

    def test_real_r2_once_exports_entire_eligible_scope_and_owner_is_not_restorable(self):
        tmp_path, monkeypatch = self.root, self
        from stockfish_core.yaogu import strategy_experiments
        native, calls = strategy_experiments.run_short_horizon_strategy, []
        def counted(*args, **kwargs):
            calls.append(kwargs['strategy_id'])
            return native(*args, **kwargs)
        monkeypatch.setattr(strategy_experiments, 'run_short_horizon_strategy', counted)
        owner, _, _ = execute(tmp_path)
        assert calls == ['SHORT_HORIZON_STRENGTH_R2']
        run = owner.run
        result = run['results'][core.STRATEGY]
        assert len(result['eligible_pool_ids']) == len(result['items']) == 2
        assert set(owner.artifacts) == {'canonical-run.json.gz', 'input.json.gz', 'manifest.json',
            'observation.json', 'observation.csv', 'all.csv', 'focus.csv', 'unselected.csv'}
        assert json.loads(gzip.decompress(Path(owner.artifacts['canonical-run.json.gz']['path']).read_bytes())) == run
        assert run['target_calendar_evidence']['verified_next_session'] == '2026-10-01'
        assert run['coverage_type'] == 'TEST_FIXTURE'
        assert run['market_edge'] == 'NOT_ESTABLISHED'
        assert owner.validate() is True
        self.assert_canonical_and_five_exports(owner)
        with self.assertRaisesRegex(TypeError, 'NOT_SERIALIZABLE'):
            pickle.dumps(owner)
        with self.assertRaisesRegex(ValueError, 'MINTED'):
            core.NativeCore()
        with self.assertRaisesRegex(ValueError, 'GENUINE_LIVE_OWNER'):
            themes.attach_theme_context(json.loads(json.dumps(run)), context_cutoff=run['decision_cutoff'],
                output_dir=tmp_path/'forged', budget=FixtureBudget())
        assert not (tmp_path/'forged').exists()

    def test_existing_native_content_hash_is_excluded_from_new_canonical_hash(self):
        from stockfish_core.yaogu import strategy_experiments
        native, parent_hashes = strategy_experiments.run_short_horizon_strategy, []
        def hashed(*args, **kwargs):
            value = native(*args, **kwargs)
            value['content_hash'] = digest_json_stream({key: item for key, item in value.items()
                if key != 'content_hash'})
            parent_hashes.append(value['content_hash'])
            return value
        self.setattr(strategy_experiments, 'run_short_horizon_strategy', hashed)
        owner, _, _ = execute(self.root)
        self.assertEqual(len(parent_hashes), 1)
        self.assertNotEqual(owner.run['content_hash'], parent_hashes[0])
        self.assert_canonical_and_five_exports(owner)

    def test_real_cache_legacy_import_metadata_enters_core_with_persistent_directories(self):
        from stockfish_core.yaogu import premarket_capture_cache_v1 as cache_module
        from stockfish_core.yaogu import providers
        from tests.yaogu.test_premarket_capture_cache_v1 import FixtureExecutor
        observed, target = '2026-09-30', '2026-10-01'
        requests = [('calendar', {'start_date': '2026-06-01', 'end_date': target}),
            ('mainboard_sse', {}), ('master_szse', {}), ('delisting_sse', {}),
            ('master_all', {'date': observed}), ('market_daily', {'date': observed})]
        executor, originals, results = FixtureExecutor(), [], []
        for index, (endpoint, parameters) in enumerate(requests):
            direct = self.root/'legacy'/('request-'+str(index))
            result = executor(endpoint, parameters, direct)
            physical = result['receipts'][0]
            if endpoint == 'calendar':
                payload = json.loads((direct/'raw'/(physical['content_hash']+'.raw.json')).read_bytes())
                payload['field_version'] = 'TEST_FIXTURE_CALENDAR_V1'
                physical = providers.archive_receipt(direct/'raw', 'baostock', endpoint, parameters,
                    payload, status='COMPLETE', pagination_complete=True,
                    field_version=payload['field_version'], first_seen_at=payload['first_seen_at'])
                result.update(provider='baostock', field_version=payload['field_version'],
                    request_receipt_id=physical['receipt_id'], receipts=[physical],
                    completed_at=datetime.now(timezone.utc).isoformat())
                (direct/'request-TEST_FIXTURE.json').write_text(json.dumps(result), encoding='utf-8')
            originals.append(dict(archive_root=str(direct), result=str(direct/'request-TEST_FIXTURE.json'),
                raw=str(direct/'raw'/(physical['content_hash']+'.raw.json')),
                receipt=str(direct/'raw'/(physical['receipt_id']+'.receipt.json'))))
            results.append(result)
        seed = self.root/'legacy-seed.json'
        seed.write_text(json.dumps({'sources': originals}), encoding='utf-8')
        budget = FixtureBudget()
        cache = cache_module.CaptureCache(self.root/'real-cache', budget,
            config={'offline': True, 'allow_test_fixture': True},
            executor=lambda *a, **k: self.fail('TEST_FIXTURE_OFFLINE_REDOWNLOAD'))
        self.assertEqual(cache.import_seed(seed)['captures'], len(requests))
        records = []
        for result in results:
            params = result['parameters']
            session = params.get('date') or params.get('end_date') or datetime.fromisoformat(
                result['receipts'][0]['first_seen_at']).astimezone(cache_module.SH).date().isoformat()
            spec = cache_module.RequestSpec(result['endpoint'], params, session,
                current=not bool(params.get('date') or params.get('end_date')))
            record = cache.capture_one(spec)
            cache.verify(record)
            records.append(cache.metadata(record))
        self.assertTrue(all(set(item['verification_locator']) ==
            {'path','target','fp','archive_root','owned','directories'} for item in records))
        cutoff = datetime.now(timezone.utc)
        owner = core.run_core(captures=records, observation_date=observed, target_session=target,
            cutoff=cutoff, output_dir=self.root/'legacy-core', budget=budget, coverage_type='TEST_FIXTURE')
        self.assertTrue(owner.validate())
        self.assert_canonical_and_five_exports(owner)
        bad = deepcopy(records)
        directories = bad[0]['verification_locator']['directories']
        directories[next(iter(directories))]['st_ino'] += 1
        with self.assertRaisesRegex(ValueError, 'LOCATOR_DIRECTORY_CHANGED'):
            core.run_core(captures=bad, observation_date=observed, target_session=target,
                cutoff=cutoff, output_dir=self.root/'changed-directory-core', budget=budget,
                coverage_type='TEST_FIXTURE')
        old = deepcopy(records)
        del old[0]['verification_locator']['directories']
        with self.assertRaisesRegex(ValueError, 'COMPLETE_LOCATOR_PHYSICAL_IDENTITY_REQUIRED'):
            core.run_core(captures=old, observation_date=observed, target_session=target,
                cutoff=cutoff, output_dir=self.root/'incomplete-legacy-core', budget=budget,
                coverage_type='TEST_FIXTURE')


    def test_mutated_original_rejected_before_normalization_or_strategy(self):
        tmp_path, monkeypatch = self.root, self
        items, observed, target, cutoff = captures(tmp_path)
        Path(items[-1]['raw']['path']).write_bytes(b'{}')
        monkeypatch.setattr(core, 'normalize_capture_with_report', lambda *a, **k: self.fail('NORMALIZED_TAMPERED_SOURCE'))
        with self.assertRaises(ValueError):
            core.run_core(captures=items, observation_date=observed, target_session=target,
                cutoff=cutoff, output_dir=tmp_path/'core', budget=FixtureBudget(), coverage_type='TEST_FIXTURE')
        assert not (tmp_path/'core').exists()


    def test_live_owner_rechecks_sources_and_run_property_is_only_copy(self):
        tmp_path = self.root
        owner, items, _ = execute(tmp_path)
        view = owner.run
        view['run_id'] = 'TEST_FIXTURE_FORGED'
        assert owner.run['run_id'] != view['run_id']
        Path(items[-1]['receipt']['path']).write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError, 'CHANGED'):
            owner.validate()

    def test_unchanged_sha_with_changed_caller_physical_identity_is_not_rebased(self):
        tmp_path = self.root
        items, observed, target, cutoff = captures(tmp_path)
        source = Path(items[-1]['raw']['path'])
        source.write_bytes(source.read_bytes())
        with self.assertRaisesRegex(ValueError, 'IDENTITY_CHANGED'):
            core.run_core(captures=items, observation_date=observed, target_session=target,
                cutoff=cutoff, output_dir=tmp_path/'core', budget=FixtureBudget(), coverage_type='TEST_FIXTURE')
        self.assertFalse((tmp_path/'core').exists())


    def test_calendar_target_and_full_natural_day_scope_are_required(self):
        tmp_path = self.root
        items, observed, _, cutoff = captures(tmp_path)
        with self.assertRaisesRegex(ValueError, 'NOT_VERIFIED_NEXT'):
            core.run_core(captures=items, observation_date=observed, target_session=observed+timedelta(days=2),
                cutoff=cutoff, output_dir=tmp_path/'core', budget=FixtureBudget(), coverage_type='TEST_FIXTURE')


    def test_theme_phase_genuine_issuance_with_short_calendar_is_unknown_and_core_unchanged(self):
        tmp_path = self.root
        owner, _, cutoff = execute(tmp_path)
        before = owner.run
        context = themes.attach_theme_context(owner, context_cutoff=cutoff,
            output_dir=tmp_path/'theme', budget=FixtureBudget())
        document = context.to_dict()
        assert document['source_verified'] is True
        assert document['status'] == 'UNKNOWN'
        assert document['diagnostic'] is None
        assert document['reason_codes'] == ['VERIFIED_CALENDAR_SHORTER_THAN_67_SESSIONS']
        assert document['historical_PIT_claimed'] is False
        assert document['event_confirmation'] == 'UNKNOWN_NOT_ISSUED'
        assert owner.run == before
        assert owner.validate() is True


    def test_theme_issuer_failure_keeps_core_and_unknown_confirmation(self):
        tmp_path = self.root
        owner, _, cutoff = execute(tmp_path)
        bad = dict(raw={'path': str(tmp_path/'absent'), 'sha256': 'f'*64}, receipt={}, typed_receipt={})
        context = themes.attach_theme_context(owner, context_cutoff=cutoff,
            output_dir=tmp_path/'theme', budget=FixtureBudget(), raw_receipt_sources=[bad])
        value = context.to_dict()
        assert value['status'] == 'FAILED' and value['diagnostic'] is None
        assert value['native_confirmation'] == 'UNKNOWN' and value['native_mainline_count'] is None
        assert value['source_verified'] is False
        assert owner.validate() is True

    def test_industry_scope_above_native_bound_stays_complete_unknown_not_first32(self):
        owner, _, cutoff = execute(self.root)
        nodes = tuple('hangye_TEST_FIXTURE_'+str(index) for index in range(33))
        result = themes.attach_theme_context(owner, context_cutoff=cutoff,
            output_dir=self.root/'theme', budget=FixtureBudget(), requested_industry_nodes=nodes)
        value = result.to_dict()
        self.assertEqual(value['source_context']['counts']['requested'], 33)
        self.assertEqual(value['source_context']['counts']['unknown'], 33)
        self.assertEqual(value['source_context']['industry']['requested_nodes'], list(nodes))
        self.assertEqual(len(value['source_context']['industry']['unknown']), 33)
        self.assertIsNone(value['diagnostic'])

    def test_explicit_empty_requested_scope_does_not_infer_every_captured_node(self):
        owner, _, cutoff = execute(self.root)
        seen, symbol = cutoff-timedelta(minutes=1), 'gn_TEST_FIXTURE_BATTERY'
        common = dict(provider='sina', version='TEST_FIXTURE_SINA_V1', include_result=True)
        originals = [physical_source(self.root/'contexts', 'concept_list_sina',
            [{'label': symbol, '板块': 'TEST_FIXTURE电池', '公司家数': 1}], seen, parameters={}, **common),
            physical_source(self.root/'contexts', 'concept_members_sina',
                [{'code': '600001', 'symbol': 'sh600001', 'name': 'TEST_FIXTURE'}],
                seen, parameters={'symbol': symbol}, **common)]
        for item in originals:
            for role in ('raw','receipt','result'):
                item[role] = FixtureBudget().read(item[role]['path'], expected=item[role]['sha256'])[1]
        result = themes.attach_theme_context(owner, context_cutoff=cutoff,
            context_captures=originals, requested_industry_nodes=(), requested_concept_symbols=(),
            output_dir=self.root/'theme', budget=FixtureBudget())
        value = result.to_dict()
        self.assertEqual(value['source_context']['counts']['requested'], 0)
        self.assertEqual(value['source_context']['themes'], [])
        saved = json.loads(Path(result.artifacts['sources-manifest.json']['path']).read_bytes())
        self.assertEqual(len(saved['captures']), len(owner.source_records)+2)

    def test_non_fixture_profile_uses_owned_physical_terminal_after_real_v2_joint(self):
        """Production coverage control, entirely synthetic TEST_FIXTURE files."""
        from stockfish_core.yaogu import provider_requests
        from stockfish_core.yaogu.premarket_capture_cache_v1 import PRODUCERS
        items, observed, target, cutoff = captures(self.root, sessions=67, history=True)
        prior = json.loads(Path(items[0]['result']['path']).read_bytes())
        # Keep the real native calendar event/visibility contract. This is an
        # isolated producer-schema fixture, not a claim about captured markets.
        items[0] = physical_source(self.root/'native-calendar-profile', 'calendar',
            prior['rows'], datetime.fromisoformat(prior['first_seen_at']), day=target,
            parameters=prior['parameters'], version='BAOSTOCK_00.9.30_R03_V1', include_result=True)
        producers = [FixtureBudget().read(Path(provider_requests.__file__).with_name(name))[1]
            for name in PRODUCERS]
        for item in items:
            result_path = Path(item['result']['path'])
            result = json.loads(result_path.read_bytes())
            result['implementation_source_hashes'] = {Path(ref['path']).name: ref['sha256'] for ref in producers}
            result_path.write_text(json.dumps(result), encoding='utf-8')
            item['producer_files'] = deepcopy(producers)
            for role in ('raw','receipt','result'):
                item[role] = FixtureBudget().read(item[role]['path'])[1]
        budget = FixtureBudget()
        owner = core.run_core(captures=items, observation_date=observed, target_session=target,
            cutoff=cutoff, output_dir=self.root/'non-fixture-core', budget=budget,
            coverage_type='RECONSTRUCTED_HISTORICAL')
        self.assertEqual(owner.run['results'][core.STRATEGY]['research_selected_ids'], ['000001.SZ','600001.SH'])
        symbol, seen = 'gn_TEST_FIXTURE_TERMINAL', cutoff-timedelta(minutes=1)
        common = dict(provider='sina', version='TEST_FIXTURE_SINA_V1', include_result=True)
        context_captures = [physical_source(self.root/'contexts', 'concept_list_sina',
            [{'label': symbol, '板块': 'TEST_FIXTURE终验', '公司家数': 2}], seen, parameters={}, **common),
            physical_source(self.root/'contexts', 'concept_members_sina',
                [{'code':'600001','symbol':'sh600001','name':'TEST_FIXTURE'},
                 {'code':'000001','symbol':'sz000001','name':'TEST_FIXTURE'}],
                seen, parameters={'symbol':symbol}, **common)]
        for item in context_captures:
            result_path = Path(item['result']['path'])
            result = json.loads(result_path.read_bytes())
            result['implementation_source_hashes'] = {Path(ref['path']).name: ref['sha256'] for ref in producers}
            result_path.write_text(json.dumps(result), encoding='utf-8')
            item['producer_files'] = deepcopy(producers)
            for role in ('raw','receipt','result'):
                item[role] = budget.read(item[role]['path'])[1]
        native, terminal_calls = themes.run_theme_evolution_stream, []
        def observed_native(bundle, **kwargs):
            self.assertNotEqual(bundle.coverage_type, 'TEST_FIXTURE')
            terminal = kwargs.get('_source_terminal_validator')
            self.assertTrue(callable(terminal))
            def observed_terminal():
                before = budget.metrics['returned_read_bytes']
                value = terminal()  # Actual owner/registry/issuer; no True stub.
                terminal_calls.append(budget.metrics['returned_read_bytes']-before)
                return value
            kwargs['_source_terminal_validator'] = observed_terminal
            return native(bundle, **kwargs)
        self.setattr(themes, 'run_theme_evolution_stream', observed_native)
        context = themes.attach_theme_context(owner, context_cutoff=cutoff,
            context_captures=context_captures, output_dir=self.root/'non-fixture-theme', budget=budget)
        self.assertEqual(context.to_dict()['status'], 'AVAILABLE', context.to_dict().get('failure'))
        self.assertEqual(len(terminal_calls), 1)
        self.assertGreater(terminal_calls[0], 0)
        parent = json.loads(gzip.decompress(Path(context.artifacts['native-theme-parent.json.gz']['path']).read_bytes()))
        self.assertEqual(parent['coverage_type'], 'RECONSTRUCTED_HISTORICAL')
        self.assertEqual(parent['execution_provenance']['source_terminal_callback_status'], 'LIVE_CALLBACK_COMPLETED')
        self.assertTrue(context.validate())


    def test_real_v2_last67_theme_parent_uses_whole_market_and_preserves_selection(self):
        tmp_path = self.root
        items, observed, target, cutoff = captures(tmp_path, sessions=67, history=True)
        budget = FixtureBudget()
        owner = core.run_core(captures=items, observation_date=observed, target_session=target,
            cutoff=cutoff, output_dir=tmp_path/'core', budget=budget, coverage_type='TEST_FIXTURE')
        before = owner.run
        assert before['results'][core.STRATEGY]['research_selected_ids'] == ['000001.SZ', '600001.SH']
        symbol = 'gn_TEST_FIXTURE_BATTERY'
        original = dict(provider='sina', version='TEST_FIXTURE_SINA_V1', include_result=True)
        seen = cutoff-timedelta(minutes=1)
        context_captures = [
            physical_source(tmp_path/'context', 'concept_list_sina',
                [{'label': symbol, '板块': 'TEST_FIXTURE电池', '公司家数': 2}],
                seen, parameters={}, **original),
            physical_source(tmp_path/'context', 'concept_members_sina',
                [{'code': '600001', 'symbol': 'sh600001', 'name': 'TEST_FIXTURE'},
                 {'code': '000001', 'symbol': 'sz000001', 'name': 'TEST_FIXTURE'}],
                seen, parameters={'symbol': symbol}, **original)]
        for item in context_captures:
            for role in ('result', 'raw', 'receipt'):
                item[role] = budget.read(item[role]['path'], expected=item[role]['sha256'])[1]
        context = themes.attach_theme_context(owner, context_cutoff=cutoff,
            context_captures=context_captures, output_dir=tmp_path/'theme', budget=budget,
            parent_acquisition={'status': 'FAILED', 'complete': 2, 'partial': 1})
        value = context.to_dict()
        assert value['status'] == 'AVAILABLE', value.get('failure')
        assert value['source_verified'] is True
        assert value['source_context']['counts'] == {'requested': 1, 'verified': 1, 'unknown': 0, 'modeled': 1}
        assert value['scope']['selected_ids'] == ['000001.SZ', '600001.SH']
        assert value['parent_acquisition']['status'] == 'FAILED'
        assert value['diagnostic']['frozen_selected_ids'] == ['000001.SZ', '600001.SH']
        parent = json.loads(gzip.decompress(Path(context.artifacts['native-theme-parent.json.gz']['path']).read_bytes()))
        assert parent['schema_version'] == 'THEME_EVOLUTION_STREAMED_CONTEXT_V2'
        assert len(parent['computed_exchange_sessions']) == 67
        final = next(row for row in parent['items'] if row['session'] == str(observed))
        assert final['market_amount_cny'] == {'numerator': 1500000000, 'denominator': 1}
        assert final['theme_amount_cny'] == {'numerator': 1000000000, 'denominator': 1}
        for category in ('confirmed_mainlines', 'fermentation_watch',
                         'active_divergence_mainlines', 'qualified_theme_data_incomplete'):
            for row in value['diagnostic'][category]:
                assert row['high_risk'] == final['high_risk']
        assert owner.run == before
        assert context.validate() is True
        journal = next(path for path in (tmp_path/'theme/price-journal').iterdir()
            if path.name != 'manifest.json')
        journal.write_bytes(journal.read_bytes()+b' ')
        with self.assertRaisesRegex(ValueError, 'CHANGED'):
            context.validate()
