"""Isolated TEST_FIXTURE presentation contracts; no real sources or strategy jobs."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from stockfish_core.yaogu import premarket_presentation_v1 as presentation


def item(number, amount, *, stage='STRONG_START'):
    return dict(instrument_id=f'{number:06d}.SZ', name=f'TEST_FIXTURE_STOCK_{number}',
                research_selected=True, decision_status='WATCH', formal_focus=False,
                primary_stage=stage, amount_cny=amount, repair_status='UNCONFIRMED',
                unknown_fact=None, rational={'numerator': 1, 'denominator': 3},
                exact_features={'market_return': -0.07, 'missing_axis': None})


def core(rows):
    return dict(run_id='TEST_FIXTURE_run', strategy_id='SHORT_HORIZON_STRENGTH_R2',
                observation_date='2026-01-05', target_session='2026-01-06',
                decision_cutoff='2026-01-05T08:00:00+00:00',
                generated_at='2026-01-05T08:00:01+00:00', coverage_type='TEST_FIXTURE',
                parameter_status='INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED',
                market_edge='NOT_ESTABLISHED',
                results={'SHORT_HORIZON_STRENGTH_R2': {
                    'items': deepcopy(rows), 'counts': {'focus': 0, 'watch': len(rows)}}})


def theme(rows, *, verified=True, flag=True, stage='STRENGTHENING_PENDING'):
    ids = [row['instrument_id'] for row in rows]
    risk = dict(flag=flag, status='PASS' if flag is True else 'UNKNOWN',
                independent_of_stage=True,
                conditions={'high_share': True, 'breadth_decline': None,
                            'price_or_rs_weakening': False})
    source = dict(theme_id='TEST_FIXTURE:industry', name='TEST_FIXTURE_广播影视行业',
                  theme_type='INDUSTRY_PROXY', member_ids=ids,
                  source_refs=['TEST_FIXTURE_receipt'], effective_from='2026-01-05',
                  first_seen_at='2026-01-05T08:02:00+00:00')
    diagnostic = {category: [] for category in presentation.CATEGORIES}
    diagnostic['fermentation_watch'] = [dict(theme_id=source['theme_id'], name=source['name'],
        selected_ids=ids, stage=stage, high_risk=risk)]
    diagnostic.update(report_banner_required=False, report_banner=None)
    return dict(schema_version=presentation.THEME_SCHEMA, run_id='TEST_FIXTURE_run',
                observation_date='2026-01-05', target_session='2026-01-06',
                context_cutoff='2026-01-05T08:03:00+00:00', source_verified=verified,
                source_context=dict(themes=[source], unknown=[],
                                    counts=dict(requested=1, verified=1, unknown=0, modeled=1)),
                diagnostic=diagnostic, parent_acquisition=None, status='AVAILABLE',
                scope=dict(kind='INTERSECTION_WITH_FULL_ORIGINAL_SELECTION', selected_ids=ids,
                           included_theme_ids=[source['theme_id']]), historical_PIT_claimed=False)


class PremarketPresentationFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='premarket-presentation-TEST_FIXTURE-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def read(self, result):
        refs = result['artifacts']
        return (Path(refs['text']['path']).read_text(),
                json.loads(Path(refs['json']['path']).read_bytes()),
                Path(refs['csv']['path']).read_text())

    def originals(self):
        refs = {}
        for role in presentation.OFFICIAL_EXPORTS | {'canonical'}:
            path = self.root / (role + '.TEST_FIXTURE.original')
            raw = ('TEST_FIXTURE original ' + role).encode()
            path.write_bytes(raw)
            refs[role] = dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
        return dict(canonical_run_ref=refs.pop('canonical'), original_exports=refs,
                    DAILY_V1=None, source_role_summary={
                        'price_observation': '2026-01-05',
                        'basic_reference_capture': '2026-01-04',
                        'basic_current_identity_certified': False})

    def test_zero_selection_is_not_unknown_model_zero(self):
        result = presentation.render_premarket(core([]), None, self.root / 'zero')
        text, report, _ = self.read(result)
        self.assertEqual(result['counts'], dict(original_selected=0, displayed=0,
            below_threshold=0, unknown_amount=0, FOCUS=0, direction_unknown=0))
        self.assertIsNone(report['theme_context']['category_counts'])
        self.assertIsNone(report['theme_context']['source_verified'])
        self.assertIn('原生主线确认未知', text)

    def test_prescreen_provenance_keeps_original_counts_and_hidden_lhb_candidate_private(self):
        from stockfish_core.yaogu import premarket_news_v1 as news
        from stockfish_core.yaogu.premarket_runtime_v1 import Budget
        original = core([item(1, 200000000), item(2, 400000000)])
        reported = news.collect_reported_news(core_run=original, output_dir=self.root/'news',
            budget=Budget(), config={'offline': True}, target_ids=['000001.SZ'])
        provenance = self.originals()
        provenance.update(news_summary=reported['summary'], news_metadata_ref=reported['metadata'],
            collection_prescreen_counts={'admitted_ids':1,'not_prioritized_ids':1,'unknown_ids':0})
        result = presentation.render_premarket(original, None, self.root/'prescreen', provenance=provenance)
        text, report, csv_text = self.read(result)
        self.assertEqual(result['counts'], report['counts'])
        self.assertEqual(result['counts']['original_selected'], 2)
        self.assertEqual(result['counts']['displayed'], 1)
        self.assertIn('详细资料采集初筛：入围1', text)
        for payload in (text, json.dumps(report), csv_text):
            self.assertNotIn('000001.SZ', payload)
        self.assertEqual(json.loads(Path(reported['metadata']['path']).read_bytes())['target_ids'], ['000001.SZ'])

    def test_inclusive_raw_money_unknown_and_full_original_retention(self):
        rows = [item(1, 299999999), item(2, 300000000), item(3, 300000001), item(4, None)]
        original = core(rows)
        before = deepcopy(original)
        provenance = self.originals()
        saved_bytes = {ref['path']: Path(ref['path']).read_bytes()
                       for ref in [provenance['canonical_run_ref'], *provenance['original_exports'].values()]}
        result = presentation.render_premarket(original, None, self.root / 'boundary', provenance=provenance)
        text, report, csv_text = self.read(result)
        self.assertEqual(result['counts']['displayed'], 2)
        self.assertEqual(result['counts']['below_threshold'], 1)
        self.assertEqual(result['counts']['unknown_amount'], 1)
        self.assertEqual(original, before)
        self.assertEqual(result['original_selected_items_hash'], presentation._digest(rows))
        self.assertEqual(result['original_selected_order_hash'], presentation._digest([r['instrument_id'] for r in rows]))
        self.assertEqual(report['retained_originals']['original_exports'], provenance['original_exports'])
        self.assertEqual(report['original_selection_retention'], 'LINKED_CANONICAL_AND_OFFICIAL_EXPORTS')
        self.assertIsNone(report['DAILY_V1'])
        self.assertIsNone(report['groups'][0]['items'][0]['original_values']['unknown_fact'])
        for hidden in (rows[0], rows[3]):
            for payload in (text, json.dumps(report), csv_text):
                self.assertNotIn(hidden['name'], payload)
                self.assertNotIn(hidden['instrument_id'], payload)
        for path, raw in saved_bytes.items():
            self.assertEqual(Path(path).read_bytes(), raw)

    def test_unknown_group_last_and_within_group_original_order(self):
        rows = [item(11, 400000000), item(12, 500000000), item(13, 600000000), item(14, 700000000)]
        context = theme(rows)
        context['source_context']['themes'][0]['member_ids'] = [rows[1]['instrument_id'], rows[3]['instrument_id']]
        context['diagnostic']['fermentation_watch'][0]['selected_ids'] = [rows[1]['instrument_id'], rows[3]['instrument_id']]
        result = presentation.render_premarket(core(rows), context, self.root / 'groups')
        _, report, _ = self.read(result)
        self.assertEqual([g['basis_kind'] for g in report['groups']], ['CURRENT_VERIFIED_INDUSTRY_PROXY', 'UNKNOWN'])
        self.assertEqual([i['original_values'] for i in report['groups'][0]['items']], [rows[1], rows[3]])
        self.assertEqual([i['original_values'] for i in report['groups'][1]['items']], [rows[0], rows[2]])
        self.assertEqual(report['groups'][0]['name'], context['source_context']['themes'][0]['name'])
        self.assertEqual(result['counts']['FOCUS'], 0)

    def test_high_risk_negative_market_does_not_rewrite_stage_or_facts(self):
        rows = [item(21, 500000000, stage='FIRST_PULLBACK'), item(22, 100000000)]
        context = theme(rows)
        context['parent_acquisition'] = dict(parent_capture_status='FAILED', COMPLETE=6, PARTIAL=1)
        old_context, old_core = deepcopy(context), core(rows)
        result = presentation.render_premarket(old_core, context, self.root / 'risk')
        text, report, _ = self.read(result)
        summary = report['theme_context']['summaries'][0]
        self.assertEqual(summary['stage'], 'STRENGTHENING_PENDING')
        self.assertEqual(summary['high_risk'], context['diagnostic']['fermentation_watch'][0]['high_risk'])
        self.assertIsNone(summary['high_risk']['conditions']['breadth_decline'])
        self.assertEqual(summary['displayed_selected_ids'], [rows[0]['instrument_id']])
        self.assertEqual(summary['original_selected_count'], 2)
        self.assertNotIn(rows[1]['instrument_id'], json.dumps(report))
        self.assertIn('高风险', text)
        self.assertIn('修复未确认', text)
        self.assertIn('FAILED', text)
        self.assertEqual(report['groups'][0]['items'][0]['original_values']['exact_features']['market_return'], -0.07)
        self.assertEqual(context, old_context)
        self.assertEqual(old_core, core(rows))

    def test_unverified_sources_never_turn_missing_confirmation_into_zero(self):
        rows = [item(31, 400000000)]
        for verified in (False, None):
            for flag in (True, False, None):
                with self.subTest(source_verified=verified, risk=flag):
                    context = theme(rows, verified=verified, flag=flag)
                    result = presentation.render_premarket(core(rows), context,
                        self.root / f'unknown-{verified}-{flag}')
                    _, report, _ = self.read(result)
                    self.assertEqual(report['groups'][0]['basis_kind'], 'UNKNOWN')
                    self.assertIs(report['theme_context']['source_verified'], verified)
                    self.assertIsNone(report['theme_context']['category_counts'])
                    provided = report['theme_context']['provided_summaries'][0]
                    self.assertIs(provided['high_risk']['flag'], flag)
                    self.assertIsNone(provided['high_risk']['conditions']['breadth_decline'])
                    self.assertEqual(report['groups'][0]['items'][0]['native_relations'], [])

    def test_missing_failed_or_unavailable_diagnostic_is_unknown_not_negative(self):
        rows = [item(33, 400000000)]
        failed = theme(rows)
        failed.update(status='FAILED', diagnostic=None)
        unavailable = theme(rows)
        for category in presentation.CATEGORIES:
            unavailable['diagnostic'][category] = []
        unavailable['diagnostic'].update(report_banner_required=True,
            report_banner='TEST_FIXTURE 主线识别不可用')
        contexts = [None, failed, theme(rows, verified=False),
                    theme(rows, verified=None), unavailable]
        for ordinal, context in enumerate(contexts):
            with self.subTest(context=ordinal):
                result = presentation.render_premarket(core(rows), context,
                    self.root / f'unknown-diagnostic-{ordinal}')
                text, report, _ = self.read(result)
                self.assertIn('原生关联：原生诊断关联未知', text)
                self.assertNotIn('当前未列入活跃诊断候选', text)
                self.assertEqual(report['groups'][0]['items'][0]['native_relations'], [])
                if context is None or context['diagnostic'] is None or context['source_verified'] is not True:
                    self.assertIsNone(report['theme_context']['category_counts'])
        available = theme(rows)
        for category in presentation.CATEGORIES:
            available['diagnostic'][category] = []
        result = presentation.render_premarket(core(rows), available,
            self.root / 'verified-no-active-candidate')
        text, report, _ = self.read(result)
        self.assertIn('原生关联：当前未列入活跃诊断候选', text)
        self.assertNotIn('原生诊断关联未知', text)
        self.assertEqual(report['theme_context']['category_counts'],
            {category: 0 for category in presentation.CATEGORIES})

    def test_multiple_native_relations_label_each_original_theme_stage_and_risk(self):
        rows = [item(34, 400000000)]
        original = core(rows)
        context = theme(rows)
        second = deepcopy(context['source_context']['themes'][0])
        second.update(theme_id='TEST_FIXTURE:concept', name='TEST_FIXTURE_第二题材',
            theme_type='CONCEPT')
        context['source_context']['themes'].append(second)
        relation = deepcopy(context['diagnostic']['fermentation_watch'][0])
        relation.update(theme_id=second['theme_id'], name=second['name'], stage='DIVERGENCE')
        relation['high_risk'].update(flag=False, status='FAIL')
        context['diagnostic']['active_divergence_mainlines'].append(relation)
        before = deepcopy(context)
        result = presentation.render_premarket(original, context, self.root / 'multiple-relations')
        text, report, csv_text = self.read(result)
        relations = report['groups'][0]['items'][0]['native_relations']
        first_name = context['source_context']['themes'][0]['name']
        self.assertIn(first_name + '：STRENGTHENING_PENDING·高风险', text)
        self.assertIn(second['name'] + '：DIVERGENCE·本模型高风险未触发', text)
        self.assertEqual([row['name'] for row in relations], [first_name, second['name']])
        self.assertEqual([row['high_risk'] for row in relations],
            [context['diagnostic']['fermentation_watch'][0]['high_risk'], relation['high_risk']])
        self.assertEqual(text.count(rows[0]['instrument_id']), 1)
        self.assertEqual(result['counts']['displayed'], 1)
        self.assertEqual(report['groups'][0]['items'][0]['original_values'], rows[0])
        self.assertIn(second['name'], csv_text)
        self.assertEqual(context, before)
        self.assertEqual(original, core(rows))

    def test_bad_or_missing_diagnostic_and_cross_run_rejected(self):
        rows = [item(41, 400000000)]
        for mutation, expected in ((lambda c: c['diagnostic'].pop('confirmed_mainlines'), 'CATEGORY_LISTS'),
                                   (lambda c: c.update(run_id='TEST_FIXTURE_other'), 'RUN_DATE_BINDING'),
                                   (lambda c: c['scope'].update(selected_ids=[]), 'SCOPE_BINDING')):
            context = theme(rows)
            mutation(context)
            with self.assertRaisesRegex(ValueError, expected):
                presentation.render_premarket(core(rows), context, self.root / 'invalid')
            self.assertFalse((self.root / 'invalid').exists())

    def test_hidden_name_in_metadata_fails_before_any_output(self):
        rows = [item(51, 400000000), item(52, None)]
        context = theme(rows)
        with self.assertRaisesRegex(ValueError, 'MUST_NOT_LEAK'):
            presentation.render_premarket(core(rows), context, self.root / 'leak',
                provenance={'source_role_summary': {'unbounded_body': rows[1]['name']}})
        self.assertFalse((self.root / 'leak').exists())

    def test_parent_full_selection_capture_facts_stay_private_but_failure_survives(self):
        rows = [item(71, 400000000), item(72, 100000000)]
        context = theme(rows)
        context['parent_acquisition'] = dict(status='PARTIAL_OR_FAILED_RETAINED',
            COMPLETE=None, PARTIAL=1, records=[dict(status='PARTIAL',
                literal_intersection_with_full_original_selected=[row['instrument_id'] for row in rows],
                private_body=rows[1]['name'])])
        result = presentation.render_premarket(core(rows), context, self.root / 'parent')
        text, report, csv_text = self.read(result)
        summary = report['theme_context']['parent_acquisition']
        self.assertEqual(summary['status'], 'PARTIAL_OR_FAILED_RETAINED')
        self.assertIsNone(summary['COMPLETE'])
        self.assertEqual(summary['record_status_counts'], {'PARTIAL': 1})
        self.assertEqual(summary['complete_original_metadata_hash'], presentation._digest(context['parent_acquisition']))
        for payload in (text, json.dumps(report), csv_text):
            self.assertNotIn(rows[1]['instrument_id'], payload)
            self.assertNotIn(rows[1]['name'], payload)

    def test_fresh_output_and_actual_preopen_afteropen_disclosure(self):
        rows = [item(61, 400000000)]
        class FixtureClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 1, 6, 1, 30, tzinfo=timezone.utc)
        with patch.object(presentation, 'datetime', FixtureClock):
            result = presentation.render_premarket(core(rows), None, self.root / 'once')
        text, report, _ = self.read(result)
        self.assertTrue(report['generated_at_or_after_target_open'])
        self.assertIn('开盘后生成，仍用2026-01-05收盘基准', text)
        original_bytes = Path(result['artifacts']['json']['path']).read_bytes()
        with self.assertRaises(FileExistsError):
            presentation.render_premarket(core(rows), None, self.root / 'once')
        self.assertEqual(Path(result['artifacts']['json']['path']).read_bytes(), original_bytes)


if __name__ == '__main__':
    unittest.main()
