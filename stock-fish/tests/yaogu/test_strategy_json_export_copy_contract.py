"""TEST_FIXTURE byte and ownership contract for the existing JSON exporter.

The reference below intentionally retains the pre-optimization public formula.
No database, selector, original archive, network or service is needed.
"""
import copy
import json
import pickle

import pytest

from stockfish_core.yaogu import candidate_service as module
from stockfish_core.yaogu.candidate_service import CandidateService
from stockfish_core.yaogu.contracts import digest
from stockfish_core.yaogu.short_horizon_profiles import SHORT_HORIZON_IDS


def legacy_export(service, run_id, *, strategy_id=None, selection=None):
    """Frozen old export formula; compare complete output bytes, not just JSON."""
    run = service.strategy_experiment(run_id)
    if strategy_id is not None or selection is not None:
        strategy_id = strategy_id or 'PV_OBSERVATION_V1'
        original_result, items, ranked, _, focus_ids = service._strategy_export_rows(run, strategy_id, selection)
        source_content_hash = run.get('content_hash') or digest(run)
        run = copy.deepcopy(run)
        result = copy.deepcopy(original_result)
        if strategy_id in SHORT_HORIZON_IDS:
            result.pop('diagnostics', None)
            result.pop('coverage_diagnostics', None)
            result.update(diagnostics_reference='items', diagnostics_count=len(items),
                          diagnostics_detail_policy='ALL_SELECTED_ROW_DETAILS_RETAINED_ONCE_IN_ITEMS',
                          coverage_diagnostics_ref='items', coverage_diagnostics_count=len(items))
        else:
            ranked_ids = {row['instrument_id'] for row in ranked}
            diagnostics = [row for row in original_result.get('items', [])
                           if row.get('instrument_id') not in ranked_ids]
            result['coverage_diagnostics'] = copy.deepcopy(
                [row for row in diagnostics if row.get('research_selected') is True]
                if selection == 'observation' else service._selected_strategy_rows(diagnostics, focus_ids, selection))
        result['items'] = copy.deepcopy(items)
        run['results'] = {strategy_id: result}
        run.pop('evaluation_baselines', None)
        run.pop('content_hash', None)
        run['export_view'] = {'strategy_id': strategy_id, 'selection': selection or 'all',
                              'source_content_hash': source_content_hash,
                              'payload_role': 'SAVED_RANKING_SELECTION_VIEW',
                              'ranked_total': len(ranked), 'filtered_total': len(items)}
        run['view_content_hash'] = digest(run)
    return json.dumps(run, ensure_ascii=False, indent=2, allow_nan=False)


def fixture(strategy_id):
    rows = [{'instrument_id': code, 'decision_status': state, 'research_selected': selected,
             'details': {'z': ['中文😀', -0.0, 1e-7], 'a': {'TEST_FIXTURE': code}}}
            for code, state, selected in (('600001.SH', 'FOCUS', True),
                ('600002.SH', 'WATCH', True), ('600003.SH', 'REJECTED', False))]
    result = {'z_first': {'TEST_FIXTURE': ['retained']}, 'items': rows,
              'diagnostics_reference': 'OLD_TEST_FIXTURE', 'diagnostics': copy.deepcopy(rows),
              'coverage_diagnostics': copy.deepcopy(rows), 'strategy_id': strategy_id,
              'full_eligible_pool_retained': True, 'counts': {'eligible': len(rows)},
              'a_last': {'TEST_FIXTURE': True}}
    run = {'z_first': {'TEST_FIXTURE': ['metadata']}, 'results': {strategy_id: result},
           'evaluation_baselines': {'TEST_FIXTURE_OTHER': {'items': copy.deepcopy(rows)}},
           'run_id': 'TEST_FIXTURE_RUN', 'selection_results': {},
           'content_hash': 'TEST_FIXTURE_FROZEN_SOURCE_HASH', 'a_last': {'TEST_FIXTURE': [1, 2]}}
    class Encoder(CandidateService):
        def strategy_experiment(self, run_id):
            assert run_id == run['run_id']
            return run
    return object.__new__(Encoder), run


@pytest.mark.parametrize('strategy_id', [*SHORT_HORIZON_IDS, 'STAGE_AWARE_MOMENTUM_V2', 'PV_OBSERVATION_V1'])
@pytest.mark.parametrize('selection', [None, 'all', 'focus', 'unselected'])
def test_export_preserves_complete_legacy_bytes_and_parent(strategy_id, selection):
    service, run = fixture(strategy_id)
    before = pickle.dumps(run)
    expected = legacy_export(service, run['run_id'], strategy_id=strategy_id, selection=selection).encode()
    actual = service.export_strategy_experiment_json(run['run_id'], strategy_id=strategy_id, selection=selection).encode()
    assert actual == expected
    assert pickle.dumps(run) == before


@pytest.mark.parametrize('strategy_id', SHORT_HORIZON_IDS)
def test_observation_selection_and_whole_run_default_keep_legacy_bytes(strategy_id):
    service, run = fixture(strategy_id)
    for options in ({}, {'strategy_id': strategy_id, 'selection': 'observation'}):
        expected = legacy_export(service, run['run_id'], **options)
        actual = service.export_strategy_experiment_json(run['run_id'], **options)
        assert actual.encode() == expected.encode()
    selected = json.loads(actual)['results'][strategy_id]['items']
    assert [row['instrument_id'] for row in selected] == ['600001.SH', '600002.SH']


def mutable_ids(value):
    if isinstance(value, dict):
        return {id(value)}.union(*(mutable_ids(item) for item in value.values()))
    if isinstance(value, list):
        return {id(value)}.union(*(mutable_ids(item) for item in value))
    return set()


def test_short_export_retains_no_mutable_alias_to_parent(monkeypatch):
    strategy_id = SHORT_HORIZON_IDS[-1]
    service, run = fixture(strategy_id)
    before = pickle.dumps(run)
    original = json.dumps
    captured = []
    def observe(value, *args, **kwargs):
        if kwargs.get('indent') == 2:
            assert not mutable_ids(value) & mutable_ids(run)
            captured.append(value)
        return original(value, *args, **kwargs)
    monkeypatch.setattr(module.json, 'dumps', observe)
    service.export_strategy_experiment_json(run['run_id'], strategy_id=strategy_id, selection='observation')
    assert len(captured) == 1
    captured[0]['z_first']['TEST_FIXTURE'].append('changed exported metadata')
    captured[0]['results'][strategy_id]['items'][0]['details']['a']['TEST_FIXTURE'] = 'changed exported row'
    assert pickle.dumps(run) == before


def test_short_export_does_not_deepcopy_discarded_full_pools():
    strategy_id = SHORT_HORIZON_IDS[-1]
    service, run = fixture(strategy_id)
    copied = []
    class ObservedList(list):
        def __init__(self, values, label):
            super().__init__(values)
            self.label = label
        def __deepcopy__(self, memo):
            copied.append(self.label)
            return copy.deepcopy(list(self), memo)
    result = run['results'][strategy_id]
    result['items'] = ObservedList(result['items'], 'full_items')
    result['diagnostics'] = ObservedList(result['diagnostics'], 'discarded_diagnostics')
    result['coverage_diagnostics'] = ObservedList(result['coverage_diagnostics'], 'discarded_coverage')
    run['evaluation_baselines']['TEST_FIXTURE_OTHER']['items'] = ObservedList([], 'discarded_baseline')
    result['items'][2]['details']['TEST_FIXTURE_UNSELECTED'] = ObservedList([], 'unselected_item')
    result['items'][0]['details']['TEST_FIXTURE_SELECTED'] = ObservedList([], 'selected_item')
    service.export_strategy_experiment_json(run['run_id'], strategy_id=strategy_id, selection='observation')
    assert copied == ['selected_item']


@pytest.mark.parametrize('failure_stage', ['selection', 'nonfinite', 'serialization'])
def test_short_export_failure_does_not_modify_parent(monkeypatch, failure_stage):
    strategy_id = SHORT_HORIZON_IDS[-1]
    service, run = fixture(strategy_id)
    selection = 'observation'
    failure = RuntimeError('TEST_FIXTURE_SERIALIZATION_FAILED')
    if failure_stage == 'selection':
        selection = 'TEST_FIXTURE_INVALID'
    elif failure_stage == 'nonfinite':
        run['results'][strategy_id]['items'][0]['details']['TEST_FIXTURE_NAN'] = float('nan')
    else:
        original = json.dumps
        def reject(value, *args, **kwargs):
            if kwargs.get('indent') == 2:
                assert not mutable_ids(value) & mutable_ids(run)
                raise failure
            return original(value, *args, **kwargs)
        monkeypatch.setattr(module.json, 'dumps', reject)
    before = pickle.dumps(run)
    with pytest.raises(RuntimeError if failure_stage == 'serialization' else ValueError) as caught:
        service.export_strategy_experiment_json(run['run_id'], strategy_id=strategy_id, selection=selection)
    if failure_stage == 'serialization':
        assert caught.value is failure
    assert pickle.dumps(run) == before
