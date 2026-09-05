"""Content-verified model registration. Artifact paths are never model identifiers."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import field_validator

from .common import CoreModel, UnavailableError, canonical_json, digest, utc_now


def file_hash(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def safe_id(value: str) -> str:
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', value) or '..' in value:
        raise ValueError('identifier must contain only letters, digits, dot, underscore or dash')
    return value


def implementation_identity() -> dict[str, str]:
    package = Path(__file__).resolve().parent
    names = ('model_registry.py', 'quant.py', 'quant_worker.py', 'experiments.py', 'backtest.py')
    hashes = {name: file_hash(package / name) for name in names}
    try:
        completed = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=package.parent,
                                   capture_output=True, text=True, timeout=5, check=True)
        commit = completed.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        commit = 'UNAVAILABLE'
    return {'git_commit': commit, 'source_code_hash': digest(hashes)}


def write_once(path: str | Path, value) -> None:
    """A final record is exclusive-create; retries cannot overwrite history."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_json(value).encode('utf-8')
    with path.open('xb') as out:
        out.write(payload)
        out.flush()
        os.fsync(out.fileno())


class ModelArtifact(CoreModel):
    model_id: str
    model_version: str
    format: Literal['qlib_alpha158_lightgbm', 'linear_v1']
    checkpoint_hash: str
    artifact_path: str
    dataset_path: str | None = None
    dataset_hash: str | None = None
    training_window: tuple[datetime, datetime]
    validation_window: tuple[datetime, datetime]
    test_window: tuple[datetime, datetime] | None = None
    label_definition: str
    max_label_horizon: int
    feature_schema: tuple[str, ...]
    feature_schema_hash: str
    qlib_version: str
    python_environment: dict[str, str]
    git_commit: str
    source_code_hash: str | None = None
    data_version: str
    universe_version: str
    created_at: datetime
    training_information_cutoff: datetime | None = None
    status: Literal['RESEARCH_ONLY', 'CLEAN_SPLIT'] = 'RESEARCH_ONLY'
    oos_validated: bool = False
    evaluation_artifact: str | None = None
    reasons: tuple[str, ...] = ()

    @field_validator('model_id', 'model_version')
    @classmethod
    def valid_id(cls, value):
        return safe_id(value)

    @field_validator('created_at', 'training_information_cutoff')
    @classmethod
    def aware(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError('model timestamps must be timezone aware')
        return value

    @field_validator('training_window', 'validation_window', 'test_window')
    @classmethod
    def windows(cls, value):
        if value is not None and (any(v.tzinfo is None for v in value) or value[0] > value[1]):
            raise ValueError('invalid model window')
        return value


class ModelRegistry:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def register(self, artifact: ModelArtifact) -> ModelArtifact:
        self.verify(artifact)
        directory = self.root / artifact.model_id / artifact.model_version
        directory.mkdir(parents=True, exist_ok=True)
        write_once(directory / 'model.json', artifact)
        return artifact

    def get(self, model_id: str, model_version: str | None = None) -> ModelArtifact:
        model_id = safe_id(model_id)
        if model_version is None:
            versions = self.list(model_id)
            if len(versions) != 1:
                raise UnavailableError('MODEL_VERSION_REQUIRED' if versions else 'MODEL_UNAVAILABLE', 'quant')
            return self.get(model_id, versions[0].model_version)
        path = self.root / model_id / safe_id(model_version) / 'model.json'
        if not path.is_file():
            raise UnavailableError('MODEL_UNAVAILABLE', 'quant')
        model = ModelArtifact.model_validate_json(path.read_text(encoding='utf-8'))
        if model.model_id != model_id or model.model_version != model_version:
            raise UnavailableError('MODEL_REGISTRY_IDENTITY_MISMATCH', 'quant')
        self.verify(model)
        return model

    def list(self, model_id: str | None = None) -> tuple[ModelArtifact, ...]:
        parent = self.root / safe_id(model_id) if model_id else self.root
        paths = parent.glob('*/model.json') if model_id else parent.glob('*/*/model.json')
        return tuple(ModelArtifact.model_validate_json(p.read_text(encoding='utf-8')) for p in sorted(paths))

    @staticmethod
    def verify(model: ModelArtifact) -> None:
        try:
            if file_hash(model.artifact_path) != model.checkpoint_hash:
                raise UnavailableError('MODEL_HASH_MISMATCH', 'quant')
            if model.dataset_path and file_hash(model.dataset_path) != model.dataset_hash:
                raise UnavailableError('MODEL_DATASET_HASH_MISMATCH', 'quant')
        except OSError as exc:
            raise UnavailableError('MODEL_ARTIFACT_MISSING', 'quant') from exc

    def health(self) -> dict:
        try:
            models = self.list()
            for model in models:
                self.verify(model)
            return {'status': 'healthy' if models else 'unavailable', 'models': len(models),
                    'production_validated': sum(m.oos_validated for m in models)}
        except Exception:
            return {'status': 'unavailable', 'reason': 'REGISTRY_INTEGRITY_FAILURE'}

    def import_legacy(self, model_dir: str | Path, *, expected_checkpoint_hash: str,
                      model_id: str = 'csi300-alpha158-legacy') -> ModelArtifact:
        """Administrative import of an explicitly trusted release; never OOS approval.

        Parse only safe YAML/text/opcodes here. Pickle loading occurs solely inside
        the isolated Qlib worker after hashes have been checked a second time.
        """
        import pickletools
        import yaml
        model_dir = Path(model_dir).resolve()
        fold_dir = model_dir / 'model_predict/walk_forward/2024-06-12/model_runs/lightgbm'
        checkpoint = fold_dir / 'mlflow_run/artifacts/params.pkl'
        dataset = checkpoint.parent / 'dataset'
        if file_hash(checkpoint) != expected_checkpoint_hash:
            raise UnavailableError('UNTRUSTED_LEGACY_CHECKPOINT', 'quant')
        if file_hash(dataset) != '8c47e706b2668cf2aebca3e5f47739f64c132213b506dc94f774ff63e3bf4172':
            raise UnavailableError('UNTRUSTED_LEGACY_DATASET', 'quant')
        cfg = yaml.safe_load((fold_dir / 'workflow_config_practice.yaml').read_text(encoding='utf-8'))
        seg = cfg['task']['dataset']['kwargs']['segments']
        tokens = [v for _, v, _ in pickletools.genops(dataset.read_bytes()) if isinstance(v, str)]
        label = next((v for v in tokens if 'Ref($close, -22)' in v), None)
        if label is None:
            raise UnavailableError('LEGACY_LABEL_SCHEMA_UNKNOWN', 'quant')
        from zoneinfo import ZoneInfo
        tz = ZoneInfo('Asia/Shanghai')
        def window(name):
            return tuple(datetime.fromisoformat(v).replace(hour=15, tzinfo=tz) for v in seg[name])
        version = expected_checkpoint_hash[:16]
        dest = self.root / safe_id(model_id) / version
        dest.mkdir(parents=True, exist_ok=True)
        for source, name in [(checkpoint, 'checkpoint.pkl'), (dataset, 'dataset.pkl')]:
            target = dest / name
            if target.exists():
                if file_hash(target) != file_hash(source):
                    raise UnavailableError('REGISTRY_ARTIFACT_COLLISION', 'quant')
            else:
                with source.open('rb') as src, target.open('xb') as out:
                    shutil.copyfileobj(src, out)
        from qlib.contrib.data.loader import Alpha158DL
        expressions, schema = Alpha158DL.get_feature_config({
            'kbar': {}, 'price': {'windows': [0], 'feature': ['OPEN', 'HIGH', 'LOW', 'VWAP']}, 'rolling': {}})
        schema = tuple(schema)
        artifact = ModelArtifact(model_id=model_id, model_version=version, format='qlib_alpha158_lightgbm',
            checkpoint_hash=expected_checkpoint_hash, artifact_path=str(dest / 'checkpoint.pkl'),
            dataset_path=str(dest / 'dataset.pkl'), dataset_hash=file_hash(dataset),
            training_window=window('train'), validation_window=window('valid'), test_window=window('test'),
            label_definition=label, max_label_horizon=22, feature_schema=schema,
            feature_schema_hash=digest({'expressions': list(expressions), 'names': list(schema)}),
            qlib_version='0.9.7', python_environment={'python': sys.version.split()[0]},
            git_commit='legacy_release_not_reproducibly_bound', data_version='legacy_unknown',
            universe_version='legacy_csi300_unknown', created_at=utc_now(), status='RESEARCH_ONLY',
            reasons=('UNPURGED_VALIDATION_LABELS', 'LEGACY_DATA_VERSION_UNKNOWN', 'NOT_OOS_VALIDATED'))
        existing = dest / 'model.json'
        if existing.exists():
            return self.get(model_id, version)
        return self.register(artifact)
