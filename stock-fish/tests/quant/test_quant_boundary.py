import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from stockfish_core.common import UnavailableError, digest
from stockfish_core.experiments import TrainRequest, TrainingSample, purged_split, walk_forward
from stockfish_core.model_registry import ModelArtifact, ModelRegistry, file_hash, write_once
from stockfish_core.quant import FeatureRow, QlibQuantEngine, QuantRequest

UTC = timezone.utc
def t(day):
    return datetime(2025, 1, day, 8, tzinfo=UTC)


def training_request(**changes):
    samples = tuple(TrainingSample(instrument_id='600000.SH', as_of=t(d),
                    feature_available_at=t(d), label_end=t(d + 2), values=(float(d),), label=d * .1)
                    for d in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13))
    params = dict(model_id='cpu', samples=samples, feature_schema=('momentum',),
        training_window=(t(1), t(5)), validation_window=(t(6), t(9)), test_window=(t(10), t(13)),
        evaluation_cutoff=t(14), label_definition='fixture_forward_two_sessions', max_label_horizon=2,
        data_version=digest([s.model_dump(mode='json') for s in samples]),
        universe_version=digest(('600000.SH',)), git_commit='fixture-explicit')
    return TrainRequest(**(params | changes))


def engine(tmp_path):
    registry = ModelRegistry(tmp_path / 'registry')
    return QlibQuantEngine(registry, tmp_path / 'quant'), registry


def test_purge_uses_label_realization_not_observation_date():
    parts, purged = purged_split(training_request())
    assert [s.as_of.day for s in parts['train']] == [1, 2, 3]
    assert [s.as_of.day for s in parts['valid']] == [6, 7]
    assert [s.as_of.day for s in parts['test']] == [10, 11, 12]
    assert purged == {'train': 2, 'valid': 2, 'test': 1}
    assert all(s.label_end < t(6) for s in parts['train'])
    assert all(s.label_end < t(10) for s in parts['valid'])


def test_test_labels_and_features_do_not_change_fitted_checkpoint(tmp_path):
    quant, registry = engine(tmp_path)
    request = training_request()
    first = quant.train(request)
    altered = tuple(s.model_copy(update={'label': 9000., 'values': (777.,)}) if s.as_of >= t(10) else s
                    for s in request.samples)
    second_request = training_request(samples=altered,
        data_version=digest([s.model_dump(mode='json') for s in altered]))
    second = quant.train(second_request)
    assert first.model.checkpoint_hash == second.model.checkpoint_hash
    assert first.validation_mse == second.validation_mse
    assert first.model.model_version != second.model.model_version  # provenance still differs
    assert not first.model.oos_validated
    assert first.model.status == 'CLEAN_SPLIT'
    assert Path(first.artifact_path, 'test_predictions.json').is_file()


def test_fixed_trials_and_deterministic_clean_refit(tmp_path):
    quant, registry = engine(tmp_path)
    request = training_request()
    first, second = quant.train(request), quant.train(request)
    assert first.model.model_version == second.model.model_version
    result = quant.finetune(request, base_model_id=first.model.model_id, base_model_version=first.model.model_version)
    provenance = json.loads(Path(result.artifact_path, 'refit_provenance.json').read_text())
    assert provenance['warm_start'] is False
    exp = walk_forward((request,), (.1, 1., 10.), registry=registry, root=tmp_path / 'experiments', policy_version='policy-a')
    assert len(exp.trials) == 3
    assert all(r['state'] == 'SUCCEEDED' for r in exp.trials)
    procedure = json.loads(Path(exp.artifact_path, 'procedure.json').read_text())
    assert procedure['trial_count'] == 3
    assert len(list(Path(exp.artifact_path).glob('experiment-*-f*-t*.json'))) == 3


def test_linear_rank_persists_all_scores_and_refuses_reuse_or_mutation(tmp_path):
    quant, registry = engine(tmp_path)
    trained = quant.train(training_request())
    features = tuple(FeatureRow(instrument_id=i, available_at=t(20), values=(7.,))
                     for i in ('600002.SH', '600001.SH'))
    request = QuantRequest(model_id='cpu', model_version=trained.model.model_version, as_of=t(20),
        data_version=digest([r.model_dump(mode='json') for r in features]), universe_version='fixture-universe',
        policy_version='policy-a', instruments=('600002.SH', '600001.SH', '600003.SH'), features=features,
        run_id='score-once')
    result = quant.rank(request)
    assert [s.instrument_id for s in result.scores] == ['600001.SH', '600002.SH']
    assert [s.rank for s in result.scores] == [1, 2]
    assert result.rejected[0].instrument_id == '600003.SH'
    assert result.quality == 'UNAVAILABLE'  # partial requested coverage is diagnostic, never a complete successful run
    assert json.loads(Path(result.artifact_path, 'result.json').read_text())['scores'][1]['rank'] == 2
    with pytest.raises(ValueError, match='RUN_ID_ALREADY_EXISTS'):
        quant.rank(request)
    Path(trained.model.artifact_path).write_text('{}')
    with pytest.raises(UnavailableError, match='MODEL_HASH_MISMATCH'):
        registry.get('cpu', trained.model.model_version)


@pytest.mark.parametrize('case', ['future', 'shape', 'hash'])
def test_bad_feature_inputs_fail_closed(tmp_path, case):
    quant, registry = engine(tmp_path)
    trained = quant.train(training_request())
    row = FeatureRow(instrument_id='600000.SH', available_at=t(21) if case == 'future' else t(20),
                     values=(1., 2.) if case == 'shape' else (1.,))
    kwargs = dict(model_id='cpu', model_version=trained.model.model_version, as_of=t(20),
        data_version='wrong' if case == 'hash' else digest([row.model_dump(mode='json')]),
        universe_version='fixture', policy_version='policy', instruments=('600000.SH',), features=(row,))
    with pytest.raises((ValueError, UnavailableError)):
        quant.rank(QuantRequest(**kwargs))


def test_research_gate_and_invalid_predictions(tmp_path):
    quant, registry = engine(tmp_path)
    trained = quant.train(training_request())
    research = trained.model.model_copy(update={'model_id': 'research', 'status': 'RESEARCH_ONLY'})
    registry.register(research)
    base = dict(model_id='research', model_version=research.model_version, as_of=t(20), data_version='fixture',
                universe_version='fixture', policy_version='policy', instruments=('600000.SH',))
    with pytest.raises(UnavailableError, match='MODEL_RESEARCH_ONLY'):
        quant.rank(QuantRequest(**base))
    for predictions, error in [([('600001.SH', 1)], 'INVALID_PREDICTION_UNIVERSE'),
                               ([('600000.SH', float('nan'))], 'NONFINITE_PREDICTION')]:
        injected = QlibQuantEngine(registry, tmp_path / 'quant', predictor=lambda *_: predictions)
        with pytest.raises(UnavailableError, match=error):
            injected.rank(QuantRequest(**base), allow_research=True)


def test_empty_universe_is_explicit_empty_not_fallback_buy(tmp_path):
    quant, registry = engine(tmp_path)
    trained = quant.train(training_request())
    result = quant.rank(QuantRequest(model_id='cpu', model_version=trained.model.model_version, as_of=t(20),
        data_version=digest([]), universe_version='empty-fixture', policy_version='policy', instruments=()))
    assert result.scores == ()
    assert result.rejected == ()
    assert result.quality == 'VALID'


def test_missing_entire_requested_universe_is_unavailable_not_a_valid_empty(tmp_path):
    quant, registry = engine(tmp_path)
    trained = quant.train(training_request())
    result = quant.rank(QuantRequest(model_id='cpu', model_version=trained.model.model_version, as_of=t(20),
        data_version=digest([]), universe_version='fixture', policy_version='policy', instruments=('600000.SH',)))
    assert result.scores == ()
    assert result.quality == 'UNAVAILABLE'
    assert result.rejected[0].reason_codes == ('FEATURES_UNAVAILABLE',)


def test_naive_time_and_inconsistent_training_hash_are_rejected():
    with pytest.raises(ValueError):
        training_request(data_version='invented')
    with pytest.raises(ValueError):
        training_request(training_window=(datetime(2025, 1, 1), t(5)))


def test_cpu_training_and_experiment_resource_budgets_reject_before_fit(tmp_path):
    with pytest.raises(ValueError):
        training_request(feature_schema=tuple(f'x{i}' for i in range(513)))
    with pytest.raises(ValueError):
        training_request(feature_schema=('',))
    with pytest.raises(ValueError):
        training_request(max_label_horizon=True)
    _, registry = engine(tmp_path)
    with pytest.raises(ValueError, match='EXPERIMENT_TRIAL_BUDGET_EXCEEDED'):
        walk_forward((training_request(),), tuple(range(1, 10)), registry=registry, root=tmp_path / 'experiments', policy_version='p')
    assert not (tmp_path / 'experiments').exists()


def test_model_not_available_until_validation_labels_are_realized(tmp_path):
    quant, _ = engine(tmp_path)
    trained = quant.train(training_request(validation_window=(t(6), t(7))))
    assert trained.model.training_information_cutoff == t(9)
    with pytest.raises(UnavailableError, match='MODEL_NOT_AVAILABLE_AS_OF'):
        quant.rank(QuantRequest(model_id='cpu', model_version=trained.model.model_version, as_of=t(8),
            data_version='empty', universe_version='fixture', policy_version='policy', instruments=()))


def test_all_failed_trials_are_recorded_without_a_successful_experiment(tmp_path):
    _, registry = engine(tmp_path)
    with pytest.raises(UnavailableError, match='ALL_FOLD_TRIALS_FAILED'):
        walk_forward((training_request(training_window=(t(1), t(2))),), (.1, 1., 10.),
            registry=registry, root=tmp_path / 'experiments', policy_version='policy', experiment_id='failed-grid')
    directory = tmp_path / 'experiments/failed-grid'
    failed = json.loads((directory / 'failure.json').read_text())
    assert len(failed['trials']) == 3
    assert all(t['state'] == 'FAILED' for t in failed['trials'])
    assert not (directory / 'result.json').exists()


@pytest.mark.parametrize('failure,reason', [('timeout', 'QUANT_TIMEOUT'), ('exit', 'QLIB_WORKER_FAILED')])
def test_native_child_failure_is_not_a_successful_quant_result(tmp_path, monkeypatch, failure, reason):
    import subprocess
    quant, registry = engine(tmp_path)
    trained = quant.train(training_request())
    native = trained.model.model_copy(update={'model_id': 'native-fixture', 'format': 'qlib_alpha158_lightgbm'})
    registry.register(native)
    monkeypatch.setattr(quant, '_freeze_data', lambda _: tmp_path)
    original_run = subprocess.run
    def fail_worker(command, **kwargs):
        if command[0] == 'git':
            return original_run(command, **kwargs)
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(command, .01)
        return subprocess.CompletedProcess(command, 1, stdout='', stderr='fixture child error')
    monkeypatch.setattr(subprocess, 'run', fail_worker)
    with pytest.raises(UnavailableError, match=reason):
        quant.rank(QuantRequest(model_id='native-fixture', model_version=native.model_version, as_of=t(20),
            data_version='fixture', universe_version='fixture', policy_version='p', instruments=('600000.SH',), run_id=failure))
    assert (quant.root / 'runs' / failure / 'failure.json').is_file()
    assert not (quant.root / 'runs' / failure / 'result.json').exists()
