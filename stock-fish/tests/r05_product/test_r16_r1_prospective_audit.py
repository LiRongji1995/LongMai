"""TEST_FIXTURE: isolated in-memory SQLite and generated score metadata only.

No real database, service construction, score recomputation, provider access,
or tracked source modifications. Run with stdlib unittest.
"""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import unittest

HERE = Path(__file__).resolve().parent
ROOT = next(path for path in HERE.parents if (path / 'stock-fish/stockfish_core').is_dir())
sys.path.insert(0, str(ROOT / 'stock-fish'))
from stockfish_core.yaogu.validation_identity import (
    official_primary_capture_for_run, validation_identity_for_run,
)

# Set AUDIT_SOURCE_UNDER_TEST to exercise the original tracked script for the
# required fail-before evidence. In the repository, the default tests the
# repository script; this private draft uses its adjacent script when present.
import os
DEFAULT_TARGET = HERE / 'r16_r1_prospective_audit.py'
if not DEFAULT_TARGET.is_file():
    DEFAULT_TARGET = ROOT / 'stock-fish/scripts/r16_r1_prospective_audit.py'
TARGET = Path(os.environ.get('AUDIT_SOURCE_UNDER_TEST', DEFAULT_TARGET))
spec = importlib.util.spec_from_file_location('prospective_audit_under_test', TARGET)
audit_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_module)


def memory_audit():
    audit = audit_module.Audit.__new__(audit_module.Audit)
    audit.outputs = {}
    audit.write_json = lambda key, value: audit.outputs.__setitem__(key, deepcopy(value))
    audit.check_budget = lambda: None
    audit.progress = lambda *args, **kwargs: None
    audit.projections = {}
    audit.detail_cache = {}
    return audit


def parent(run_id='TEST_FIXTURE_PARENT', frozen=False):
    run = {'run_id': run_id, 'observation_date': '2026-09-07',
           'implementation_version': 'TEST_FIXTURE_PARENT_V1', 'source_hash': 'test_parent_source',
           'config_hash': 'test_parent_config', 'spec_hash': 'test_parent_spec',
           'results': {audit_module.STAGE: {
               'canonical_strategy_id': audit_module.STAGE,
               'stage_aware_implementation_version': 'TEST_FIXTURE_STAGE_V1',
               'stage_aware_source_hash': 'test_stage_source', 'stage_aware_config_hash': 'test_stage_config',
               'stage_aware_spec_revision': 'test_stage_spec', 'items': []}},
           'coverage_type': 'PROSPECTIVE_CAPTURED',
           'prospective_validation': {'coverage_type': 'PROSPECTIVE_CAPTURED', 'validation_role': 'PROSPECTIVE_PRIMARY',
               'market_session': 'EVENING', 'signal_observation_date': '2026-09-07',
               'stage_aware_strategy_id': audit_module.STAGE, 'official_primary_capture': True}}
    if frozen:
        run['prospective_validation'].update(validation_identity_for_run(run))
    return run


def check(run):
    # Countability itself is deliberately false: these metadata-only fixtures
    # are never promoted to real evaluable official signals.
    return {'run_id': run['run_id'], 'frozen_identity': validation_identity_for_run(run),
            'rechecked_official_signal_countable': False}


def score(run, epoch, score_id, revision=1, horizon=1, status='N_A_NO_SELECTION'):
    return {'score_run_id': score_id, 'score_revision_group_id': 'TEST_FIXTURE_GROUP',
            'revision': revision, 'generated_at': '2026-09-08T00:00:00+00:00',
            'signal_run_id': run['run_id'], 'validation_epoch_id': epoch,
            'official_validation_signal_countable': False,
            'records': [{'score_id': score_id + '_record', 'signal_run_id': run['run_id'],
                         'validation_epoch_id': epoch, 'horizon': horizon, 'score_status': status,
                         'M': 0, 'valid_M': 0, 'selected_outcome_coverage': None,
                         'benchmark_N': 4, 'benchmark_valid_N': 0, 'benchmark_coverage': 0.0,
                         'score': None, 'official_recommendation_score': False}]}


def inspect(run, documents):
    audit = memory_audit()
    result = audit.inspect_scores({'recommendation_score_revisions': documents}, {run['run_id']: run}, [check(run)])
    return audit, result


class EpochTests(unittest.TestCase):
    def test_legacy_without_saved_epoch_uses_production_computed_epoch(self):
        run = parent()
        epoch = validation_identity_for_run(run)['validation_epoch_id']
        self.assertNotIn('validation_epoch_id', run)
        _, result = inspect(run, [score(run, epoch, 'TEST_FIXTURE_SCORE')])
        rows = [row for row in result['horizon_inventory'] if row['horizon'] == 1]
        self.assertEqual([(row['validation_epoch_id'], row['saved_score_record_count']) for row in rows], [(epoch, 1)])

    def test_same_run_different_production_epochs_are_not_merged(self):
        run = parent(frozen=True)
        changed = deepcopy(run)
        changed['results'][audit_module.STAGE]['stage_aware_source_hash'] = 'test_stage_source_revision_two'
        first = validation_identity_for_run(run)['validation_epoch_id']
        second = validation_identity_for_run(changed)['validation_epoch_id']
        self.assertNotEqual(first, second)
        _, result = inspect(run, [score(run, first, 'TEST_FIXTURE_A', status='N_A_NO_SELECTION'),
                                  score(run, second, 'TEST_FIXTURE_B', revision=2, status='NOT_MATURED')])
        rows = {row['validation_epoch_id']: row for row in result['horizon_inventory'] if row['horizon'] == 1}
        self.assertEqual(set(rows), {first, second})
        self.assertEqual(rows[first]['score_status_counts'], {'N_A_NO_SELECTION': 1})
        self.assertEqual(rows[second]['score_status_counts'], {'NOT_MATURED': 1})
        self.assertEqual(set(result['latest_group_score_run_ids']), {'TEST_FIXTURE_A', 'TEST_FIXTURE_B'})

    def test_no_score_preserves_rechecked_epoch_and_all_default_horizons(self):
        run = parent(frozen=True)
        epoch = validation_identity_for_run(run)['validation_epoch_id']
        _, result = inspect(run, [])
        rows = result['horizon_inventory']
        self.assertEqual({row['horizon'] for row in rows}, set(audit_module.HORIZONS))
        self.assertEqual({row['validation_epoch_id'] for row in rows}, {epoch})
        self.assertEqual({row['status'] for row in rows}, {'NO_SAVED_SCORE'})
        self.assertTrue(all(row['saved_score_record_count'] == 0 for row in rows))

    def test_document_record_epoch_conflict_is_explicit_not_counted(self):
        run = parent(frozen=True)
        epoch = validation_identity_for_run(run)['validation_epoch_id']
        document = score(run, epoch, 'TEST_FIXTURE_BAD_BINDING')
        document['records'][0]['validation_epoch_id'] = 'TEST_FIXTURE_UNBOUND_EPOCH'
        audit, result = inspect(run, [document])
        row = next(row for row in result['horizon_inventory'] if row['horizon'] == 1)
        self.assertEqual(row['saved_score_record_count'], 0)
        self.assertEqual(row['raw_saved_score_record_count'], 1)
        self.assertEqual(row['status'], 'NOT_VERIFIED_SCORE_RECORD_BINDING')
        self.assertTrue(result['issues'])
        raw = audit.outputs['all_saved_score_records.json']['records'][0]
        self.assertFalse(raw['document_record_identity_matches'])
        self.assertEqual(raw['validation_epoch_id'], 'TEST_FIXTURE_UNBOUND_EPOCH')

    def test_zero_and_na_score_values_remain_saved_values(self):
        run = parent(frozen=True)
        epoch = validation_identity_for_run(run)['validation_epoch_id']
        audit, _ = inspect(run, [score(run, epoch, 'TEST_FIXTURE_ZERO', horizon=20)])
        row = audit.outputs['all_saved_score_records.json']['records'][0]
        self.assertEqual((row['M'], row['valid_M'], row['benchmark_N'], row['benchmark_valid_N']), (0, 0, 4, 0))
        self.assertIsNone(row['selected_outcome_coverage'])
        self.assertIsNone(row['score'])
        self.assertEqual(row['benchmark_coverage'], 0.0)
        self.assertEqual(row['score_status'], 'N_A_NO_SELECTION')

    def test_other_epoch_scores_do_not_hide_no_score_in_parent_epoch(self):
        run = parent(frozen=True)
        epoch = validation_identity_for_run(run)['validation_epoch_id']
        changed = deepcopy(run)
        changed['source_hash'] = 'TEST_FIXTURE_OTHER_PARENT_SOURCE'
        other_epoch = validation_identity_for_run(changed)['validation_epoch_id']
        _, result = inspect(run, [score(run, other_epoch, 'TEST_FIXTURE_ONLY_OTHER')])
        rows = {row['validation_epoch_id']: row for row in result['horizon_inventory'] if row['horizon'] == 1}
        self.assertEqual(rows[epoch]['status'], 'NO_SAVED_SCORE')
        self.assertEqual(rows[epoch]['saved_score_record_count'], 0)
        self.assertEqual(rows[other_epoch]['status'], 'SAVED_RECORDS')
        self.assertEqual(rows[other_epoch]['saved_score_record_count'], 1)

    def test_document_record_parent_conflict_is_explicit_not_counted(self):
        run = parent(frozen=True)
        epoch = validation_identity_for_run(run)['validation_epoch_id']
        document = score(run, epoch, 'TEST_FIXTURE_WRONG_PARENT')
        document['records'][0]['signal_run_id'] = 'TEST_FIXTURE_ANOTHER_PARENT'
        audit, result = inspect(run, [document])
        row = next(row for row in result['horizon_inventory'] if row['horizon'] == 1)
        self.assertEqual(row['saved_score_record_count'], 0)
        self.assertEqual(row['status'], 'NOT_VERIFIED_SCORE_RECORD_BINDING')
        raw = audit.outputs['all_saved_score_records.json']['records'][0]
        self.assertIn('SCORE_DOCUMENT_RECORD_SIGNAL_RUN_MISMATCH', raw['document_record_binding_reasons'])


class ProjectionTests(unittest.TestCase):
    def project(self, run):
        audit = memory_audit()
        audit.con = sqlite3.connect(':memory:')
        self.addCleanup(audit.con.close)
        audit.con.row_factory = sqlite3.Row
        audit.con.execute('CREATE TABLE documents(namespace TEXT, document_id TEXT, payload TEXT, content_hash TEXT)')
        raw = json.dumps(run, ensure_ascii=False)
        audit.con.execute('INSERT INTO documents VALUES(?,?,?,?)', (audit_module.RUNS_NS, run['run_id'], raw,
                          hashlib.sha256(raw.encode('utf-8')).hexdigest()))
        audit.con.commit()
        audit.con.execute('PRAGMA query_only=ON')
        return audit.project_run(run['run_id'])

    def test_canonical_only_missing_strategy_id_retains_production_capture(self):
        run = parent()
        self.assertTrue(official_primary_capture_for_run(run))
        projected = self.project(run)
        self.assertTrue(official_primary_capture_for_run(projected))
        self.assertNotIn('strategy_id', projected['results'][audit_module.STAGE])
        self.assertNotIn('audit_mode', projected)

    def test_explicit_null_does_not_activate_production_default(self):
        run = parent()
        run['results'][audit_module.STAGE]['strategy_id'] = None
        run['audit_mode'] = None
        self.assertFalse(official_primary_capture_for_run(run))
        projected = self.project(run)
        self.assertFalse(official_primary_capture_for_run(projected))
        self.assertIn('strategy_id', projected['results'][audit_module.STAGE])
        self.assertIsNone(projected['results'][audit_module.STAGE]['strategy_id'])
        self.assertIn('audit_mode', projected)

    def test_absent_and_null_stage_remain_distinct(self):
        for results in ({}, {audit_module.STAGE: None}):
            run = parent()
            run['results'] = deepcopy(results)
            projected = self.project(run)
            self.assertEqual(projected['results'], results)


if __name__ == '__main__':
    unittest.main(verbosity=2)
