"""A small deterministic CPU training procedure with purged, auditable windows.

The JSON ridge model is a testable baseline, not an investment-performance claim.
All trials are registered before fitting; validation alone selects a trial.
"""
from __future__ import annotations

import json
import math
import platform
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np
from pydantic import Field, field_validator, model_validator

from .common import CoreModel, UnavailableError, digest, utc_now
from .model_registry import ModelArtifact, ModelRegistry, file_hash, safe_id, write_once, implementation_identity
from .quant import aware, canonical_instrument


class TrainingSample(CoreModel):
    instrument_id: str
    as_of: datetime
    feature_available_at: datetime
    label_end: datetime
    values: tuple[float, ...]
    label: float

    _instrument = field_validator('instrument_id')(canonical_instrument)
    _time = field_validator('as_of', 'feature_available_at', 'label_end')(aware)

    @model_validator(mode='after')
    def causal(self):
        if self.feature_available_at > self.as_of or self.label_end <= self.as_of:
            raise ValueError('training feature/label time is not causal')
        return self


class TrainRequest(CoreModel):
    model_id: str
    samples: tuple[TrainingSample, ...] = Field(min_length=5, max_length=10000)
    feature_schema: tuple[str, ...] = Field(min_length=1, max_length=512)
    training_window: tuple[datetime, datetime]
    validation_window: tuple[datetime, datetime]
    test_window: tuple[datetime, datetime]
    evaluation_cutoff: datetime
    label_definition: str
    max_label_horizon: int = Field(ge=1, le=252, strict=True)
    data_version: str
    universe_version: str
    git_commit: str
    ridge_alpha: float = Field(default=1., gt=0, le=100000)
    run_id: str | None = None

    _time = field_validator('evaluation_cutoff')(aware)

    @model_validator(mode='after')
    def validate_input(self):
        safe_id(self.model_id)
        for window in (self.training_window, self.validation_window, self.test_window):
            if any(aware(t) != t for t in window) or window[0] > window[1]:
                raise ValueError('invalid training window')
        if not (self.training_window[1] < self.validation_window[0] <= self.validation_window[1] < self.test_window[0]):
            raise ValueError('train/validation/test windows must be disjoint and ordered')
        if self.test_window[1] > self.evaluation_cutoff:
            raise ValueError('test window exceeds evaluation cutoff')
        if not self.feature_schema or len(set(self.feature_schema)) != len(self.feature_schema):
            raise ValueError('feature schema required and unique')
        if any(not name.strip() or len(name) > 128 for name in self.feature_schema):
            raise ValueError('feature names must contain 1..128 characters')
        if len(self.samples) * len(self.feature_schema) > 1000000:
            raise ValueError('CPU_TRAINING_CELL_BUDGET_EXCEEDED')
        if any(len(s.values) != len(self.feature_schema) for s in self.samples):
            raise ValueError('feature shape mismatch')
        keys = [(s.instrument_id, s.as_of) for s in self.samples]
        if len(set(keys)) != len(keys):
            raise ValueError('duplicate training samples')
        if digest([s.model_dump(mode='json') for s in self.samples]) != self.data_version:
            raise ValueError('training data hash mismatch')
        return self


class TrainingResult(CoreModel):
    run_id: str
    model: ModelArtifact
    validation_mse: float
    split_counts: dict[str, int]
    purged_counts: dict[str, int]
    artifact_path: str


def purged_split(request: TrainRequest) -> tuple[dict[str, tuple[TrainingSample, ...]], dict[str, int]]:
    """The label must be fully visible strictly before the next split begins.

    Using explicit label_end handles calendar holidays and mixed horizons without
    mistaking calendar days for trading sessions.
    """
    partitions, purged = {}, {}
    for name, window, cutoff, strict in [
        ('train', request.training_window, request.validation_window[0], True),
        ('valid', request.validation_window, request.test_window[0], True),
        ('test', request.test_window, request.evaluation_cutoff, False),
    ]:
        raw = [s for s in request.samples if window[0] <= s.as_of <= window[1]]
        keep = [s for s in raw if (s.label_end < cutoff if strict else s.label_end <= cutoff)]
        partitions[name] = tuple(sorted(keep, key=lambda s: (s.as_of, s.instrument_id)))
        purged[name] = len(raw) - len(keep)
    if len(partitions['train']) < 3 or not partitions['valid'] or not partitions['test']:
        raise UnavailableError('INSUFFICIENT_PURGED_TRAIN_VALID_TEST', 'quant')
    return partitions, purged


def train_linear(request: TrainRequest, registry: ModelRegistry, root: str | Path) -> TrainingResult:
    run_id = safe_id(request.run_id) if request.run_id else 'train-' + uuid.uuid4().hex
    directory = Path(root) / run_id
    directory.mkdir(parents=True, exist_ok=False)
    write_once(directory / 'request.json', request)
    implementation = implementation_identity()
    write_once(directory / 'implementation.json', implementation)
    try:
        parts, purged = purged_split(request)
        x = np.asarray([s.values for s in parts['train']], dtype=float)
        y = np.asarray([s.label for s in parts['train']], dtype=float)
        # Fit centering and coefficients exclusively on train.
        center, ymean = x.mean(axis=0), float(y.mean())
        centered = x - center
        coefficients = np.linalg.solve(centered.T @ centered + request.ridge_alpha * np.eye(x.shape[1]),
                                       centered.T @ (y - ymean))
        intercept = ymean - float(center @ coefficients)
        predictions = lambda rows: np.asarray([intercept + np.asarray(s.values) @ coefficients for s in rows])
        valid_mse = float(np.mean((predictions(parts['valid']) - [s.label for s in parts['valid']]) ** 2))
        content = {'format': 'linear_v1', 'feature_schema': request.feature_schema,
                   'coefficients': coefficients.tolist(), 'intercept': intercept,
                   'procedure': 'ridge-purged-v2', 'ridge_alpha': request.ridge_alpha}
        write_once(directory / 'model.json', content)
        checkpoint_hash = file_hash(directory / 'model.json')
        if implementation_identity()['source_code_hash'] != implementation['source_code_hash']:
            raise UnavailableError('IMPLEMENTATION_CHANGED_DURING_TRAINING', 'quant')
        split_record = {name: [{'instrument_id': s.instrument_id, 'as_of': s.as_of, 'label_end': s.label_end}
                               for s in rows] for name, rows in parts.items()}
        write_once(directory / 'splits.json', {'partitions': split_record, 'purged_counts': purged})
        write_once(directory / 'validation.json', {'metric': 'PREDICTION_MSE_NOT_STRATEGY_RETURN',
                   'validation_mse': valid_mse, 'selection_input': 'validation_only', 'test_used_for_fit': False})
        # Persist test predictions separately; they do not enter fitting/selection.
        write_once(directory / 'test_predictions.json', [
            {'instrument_id': s.instrument_id, 'as_of': s.as_of, 'prediction': float(p), 'label': s.label,
             'label_end': s.label_end} for s, p in zip(parts['test'], predictions(parts['test']))])
        model_version = digest({'checkpoint': checkpoint_hash, 'data_version': request.data_version,
                                'schema': request.feature_schema, 'splits': split_record,
                                'label_definition': request.label_definition, 'max_label_horizon': request.max_label_horizon,
                                'universe_version': request.universe_version,
                                'implementation': implementation})[:24]
        model = ModelArtifact(model_id=request.model_id, model_version=model_version, format='linear_v1',
            checkpoint_hash=checkpoint_hash, artifact_path=str((directory / 'model.json').resolve()),
            training_window=request.training_window, validation_window=request.validation_window,
            test_window=request.test_window, label_definition=request.label_definition,
            max_label_horizon=request.max_label_horizon, feature_schema=request.feature_schema,
            feature_schema_hash=digest(request.feature_schema), qlib_version='not_required_for_linear_v1',
            python_environment={'python': platform.python_version(), 'numpy': np.__version__},
            **implementation, data_version=request.data_version, universe_version=request.universe_version,
            created_at=utc_now(), status='CLEAN_SPLIT', oos_validated=False,
            training_information_cutoff=max(s.label_end for group in ('train', 'valid') for s in parts[group]),
            evaluation_artifact=str((directory / 'validation.json').resolve()),
            reasons=('CLEAN_TEMPORAL_SPLIT', 'PREDICTION_BASELINE_NOT_INVESTMENT_APPROVAL'))
        # Identical data/procedure produce identical immutable model version.
        path = registry.root / model.model_id / model.model_version / 'model.json'
        if path.exists():
            registered = registry.get_prepared(model.model_id, model.model_version)
            if registered.data_version != model.data_version:
                raise UnavailableError('MODEL_VERSION_PROVENANCE_COLLISION', 'quant')
            model = registered
        else:
            registry.prepare(model)
        result = TrainingResult(run_id=run_id, model=model, validation_mse=valid_mse,
            split_counts={name: len(rows) for name, rows in parts.items()}, purged_counts=purged,
            artifact_path=str(directory.resolve()))
        write_once(directory / 'result.json', result)
        registry.publish_training(result)
        return result
    except Exception as exc:
        write_once(directory / 'failure.json', {'run_id': run_id, 'reason': getattr(exc, 'reason', type(exc).__name__)})
        raise


class ExperimentResult(CoreModel):
    experiment_id: str
    procedure_version: str
    policy_version: str
    trials: tuple[dict, ...]
    selected_models: tuple[str, ...]
    artifact_path: str


def finetune_linear(request: TrainRequest, *, base_model_id: str, base_model_version: str,
                    registry: ModelRegistry, root: str | Path) -> TrainingResult:
    base = registry.get(base_model_id, base_model_version)
    if base.format != 'linear_v1' or base.feature_schema != request.feature_schema:
        raise UnavailableError('CLEAN_REFIT_BASE_SCHEMA_UNSUPPORTED', 'quant')
    result = train_linear(request, registry, root)
    write_once(Path(result.artifact_path) / 'refit_provenance.json', {
        'base_model_id': base.model_id, 'base_model_version': base.model_version,
        'base_checkpoint_hash': base.checkpoint_hash, 'method': 'FULL_CLEAN_WINDOW_REFIT',
        'warm_start': False, 'reason': 'Prevent inherited unknown samples from contaminating temporal split',
    })
    return result


def walk_forward(requests: tuple[TrainRequest, ...], alphas: tuple[float, ...], *,
                 registry: ModelRegistry, root: str | Path, policy_version: str,
                 experiment_id: str | None = None) -> ExperimentResult:
    """Fixed expanding/rolling request sequence; log every trial, including failures.

    Selection is min validation MSE then alpha. No test statistic is consulted.
    Portfolio evaluation is deliberately the shared-policy BacktestEngine layer.
    """
    if not requests or not alphas or len(set(alphas)) != len(alphas):
        raise ValueError('nonempty, unique preregistered trials required')
    if len(requests) > 12 or len(alphas) > 8 or len(requests) * len(alphas) > 48:
        raise ValueError('EXPERIMENT_TRIAL_BUDGET_EXCEEDED')
    if sum(len(r.samples) * len(r.feature_schema) for r in requests) * len(alphas) > 4000000:
        raise ValueError('EXPERIMENT_CELL_BUDGET_EXCEEDED')
    if any(a <= 0 or a > 100000 or not math.isfinite(a) for a in alphas):
        raise ValueError('invalid ridge alpha')
    for prev, current in zip(requests, requests[1:]):
        if prev.test_window[1] >= current.test_window[0]:
            raise ValueError('walk-forward test windows overlap')
    identity = safe_id(experiment_id) if experiment_id else 'experiment-' + uuid.uuid4().hex
    directory = Path(root) / identity
    directory.mkdir(parents=True, exist_ok=False)
    write_once(directory / 'procedure.json', {'procedure_version': 'purged-walk-forward-v1',
        'policy_version': policy_version, 'alpha_grid': alphas, 'selection': 'min_validation_mse_then_alpha',
        'requests': requests, 'trial_count': len(requests) * len(alphas)})
    trials, selected, failed_folds = [], [], []
    for fold_index, request in enumerate(requests):
        successful = []
        for trial_index, alpha in enumerate(alphas):
            trial_id = f'{identity}-f{fold_index}-t{trial_index}'
            modified = request.model_copy(update={'ridge_alpha': alpha, 'run_id': trial_id})
            try:
                result = train_linear(modified, registry, directory / 'trials')
                trial = {'trial_id': trial_id, 'fold': fold_index, 'alpha': alpha, 'state': 'SUCCEEDED',
                         'validation_mse': result.validation_mse, 'model_version': result.model.model_version}
                successful.append(trial)
            except Exception as exc:
                trial = {'trial_id': trial_id, 'fold': fold_index, 'alpha': alpha, 'state': 'FAILED',
                         'error': getattr(exc, 'reason', type(exc).__name__)}
            trials.append(trial)
            write_once(directory / f'{trial_id}.json', trial)
        if not successful:
            failed_folds.append(fold_index)
            continue
        winner = min(successful, key=lambda r: (r['validation_mse'], r['alpha']))
        selected.append(winner['model_version'])
    if failed_folds:
        write_once(directory / 'failure.json', {'reason': 'ALL_FOLD_TRIALS_FAILED',
            'failed_folds': failed_folds, 'trials': trials, 'selected_models': selected})
        raise UnavailableError('ALL_FOLD_TRIALS_FAILED', 'quant')
    result = ExperimentResult(experiment_id=identity, procedure_version='purged-walk-forward-v1',
        policy_version=policy_version, trials=tuple(trials), selected_models=tuple(selected),
        artifact_path=str(directory.resolve()))
    write_once(directory / 'result.json', result)
    return result
