"""Content-verified model registration. Artifact paths are never model identifiers."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import subprocess
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import field_validator

from .common import CoreModel, UnavailableError, canonical_json, digest, utc_now
from .storage import SQLiteStore, checked_payload


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
    def __init__(self, root: str | Path, *, store: SQLiteStore | None = None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = store or SQLiteStore(self.root / 'publication.sqlite3')
        self._pending_job = ContextVar('pending_model_publication_' + str(id(self)), default=None)

    def bind_store(self, store: SQLiteStore) -> None:
        """Bind the same authority database as the application's durable jobs."""
        self.store = store

    @contextmanager
    def pending_job(self, job_id: str):
        token = self._pending_job.set(job_id)
        try:
            yield
        finally:
            self._pending_job.reset(token)

    @staticmethod
    def _publication_id(model):
        return model.model_id + '/' + model.model_version

    def prepare(self, artifact: ModelArtifact) -> ModelArtifact:
        """Save verified immutable files; this creates no inference authorization."""
        self.verify(artifact)
        directory = self.root / artifact.model_id / artifact.model_version
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / 'model.json'
        if path.exists():
            original = self.get_prepared(artifact.model_id, artifact.model_version)
            if (original.checkpoint_hash, original.data_version, original.feature_schema_hash) != (
                    artifact.checkpoint_hash, artifact.data_version, artifact.feature_schema_hash):
                raise UnavailableError('MODEL_VERSION_PROVENANCE_COLLISION', 'quant')
            return original
        write_once(path, artifact)
        return artifact

    def _authorize(self, connection, model, *, kind, run_id=None, job_id=None):
        identity = self._publication_id(model)
        existing = connection.execute('SELECT * FROM documents WHERE namespace=? AND document_id=?',
                                      ('model_publications', identity)).fetchone()
        if existing:
            original = checked_payload(existing)
            if original['metadata_hash'] != digest(model):
                raise UnavailableError('MODEL_PUBLICATION_METADATA_MISMATCH', 'quant')
            # Repeated training must not replace or revoke a successful publication.
            return
        self.store._put_document(connection, 'model_publications', identity, {
            'model_id': model.model_id, 'model_version': model.model_version,
            'metadata_hash': digest(model), 'checkpoint_hash': model.checkpoint_hash,
            'kind': kind, 'training_run_id': run_id, 'job_id': job_id,
            'published_at': utc_now().isoformat()})

    def publish_training(self, result, *, connection=None, job_id=None):
        """Authorize only complete training, alongside a durable successful job.

        With a job, the caller MUST use JobContext.complete's connection. Files
        are prepared earlier; SQLite commits only the authority and job state.
        """
        if connection is None and self._pending_job.get() is not None:
            return  # A cancellable worker has prepared diagnostics only.
        if job_id is not None and connection is None:
            raise ValueError('job publication requires the terminal transaction connection')
        model = self.get_prepared(result.model.model_id, result.model.model_version)
        if model != result.model:
            raise UnavailableError('TRAINING_MODEL_METADATA_MISMATCH', 'quant')
        completed = Path(result.artifact_path) / 'result.json'
        if not completed.is_file() or json.loads(completed.read_text(encoding='utf-8')) != result.model_dump(mode='json'):
            raise UnavailableError('TRAINING_RESULT_NOT_PREPARED', 'quant')
        def publish(conn):
            if job_id:
                job = conn.execute('SELECT state,cancel_requested FROM jobs WHERE job_id=?', (job_id,)).fetchone()
                if job is None or job['state'] != 'RUNNING' or job['cancel_requested']:
                    raise UnavailableError('MODEL_JOB_PUBLICATION_NOT_AUTHORIZED', 'quant')
            self.store._put_document(conn, 'training_runs', result.run_id, result.model_dump(mode='json'))
            self._authorize(conn, model, kind='JOB_TRAINING' if job_id else 'SYNCHRONOUS_TRAINING',
                            run_id=result.run_id, job_id=job_id)
        if connection is not None:
            publish(connection)
        else:
            with self.store.transaction() as conn:
                publish(conn)

    def register(self, artifact: ModelArtifact) -> ModelArtifact:
        """Explicit local administrative import; never upgrades OOS validation.

        This method is not exposed through HTTP. Training uses prepare instead.
        """
        artifact = self.prepare(artifact)
        with self.store.transaction() as connection:
            self._authorize(connection, artifact, kind='ADMINISTRATIVE_RESEARCH_IMPORT')
        return artifact

    def get(self, model_id: str, model_version: str | None = None) -> ModelArtifact:
        model_id = safe_id(model_id)
        if model_version is None:
            versions = self.list(model_id)
            if len(versions) != 1:
                raise UnavailableError('MODEL_VERSION_REQUIRED' if versions else 'MODEL_UNAVAILABLE', 'quant')
            return self.get(model_id, versions[0].model_version)
        model = self.get_prepared(model_id, model_version)
        self._check_authorized(model)
        return model

    def get_prepared(self, model_id: str, model_version: str) -> ModelArtifact:
        """Diagnostic-only lookup; never use this in model consumers."""
        model_id, model_version = safe_id(model_id), safe_id(model_version)
        path = self.root / model_id / model_version / 'model.json'
        if not path.is_file():
            raise UnavailableError('MODEL_UNAVAILABLE', 'quant')
        model = ModelArtifact.model_validate_json(path.read_text(encoding='utf-8'))
        if model.model_id != model_id or model.model_version != model_version:
            raise UnavailableError('MODEL_REGISTRY_IDENTITY_MISMATCH', 'quant')
        self.verify(model)
        return model

    def list(self, model_id: str | None = None) -> tuple[ModelArtifact, ...]:
        if model_id:
            safe_id(model_id)
        # The authority is the inventory. Orphaned or malformed diagnostics
        # must not become discoverable models or poison unrelated publication.
        with self.store.transaction(write=False) as connection:
            rows = connection.execute('SELECT * FROM documents WHERE namespace=? ORDER BY document_id',
                                      ('model_publications',)).fetchall()
            entries = [checked_payload(row) for row in rows]
        return tuple(self.get(item['model_id'], item['model_version']) for item in entries
                     if model_id is None or item['model_id'] == model_id)

    def list_prepared(self, model_id: str | None = None) -> tuple[ModelArtifact, ...]:
        parent = self.root / safe_id(model_id) if model_id else self.root
        paths = parent.glob('*/model.json') if model_id else parent.glob('*/*/model.json')
        return tuple(ModelArtifact.model_validate_json(p.read_text(encoding='utf-8')) for p in sorted(paths))

    def _check_authorized(self, model):
        with self.store.transaction(write=False) as connection:
            row = connection.execute('SELECT * FROM documents WHERE namespace=? AND document_id=?',
                                     ('model_publications', self._publication_id(model))).fetchone()
            if row is None:
                raise UnavailableError('MODEL_NOT_PUBLISHED', 'quant')
            authority = checked_payload(row)
            if authority['metadata_hash'] != digest(model) or authority['checkpoint_hash'] != model.checkpoint_hash:
                raise UnavailableError('MODEL_PUBLICATION_METADATA_MISMATCH', 'quant')
            if authority['training_run_id']:
                training = connection.execute('SELECT * FROM documents WHERE namespace=? AND document_id=?',
                    ('training_runs', authority['training_run_id'])).fetchone()
                if training is None or checked_payload(training)['model'] != model.model_dump(mode='json'):
                    raise UnavailableError('MODEL_TRAINING_AUTHORITY_MISSING', 'quant')
            if authority['job_id']:
                job = connection.execute('SELECT state,result FROM jobs WHERE job_id=?', (authority['job_id'],)).fetchone()
                if job is None or job['state'] != 'SUCCEEDED':
                    raise UnavailableError('MODEL_SUCCESSFUL_JOB_AUTHORITY_MISSING', 'quant')
                terminal = json.loads(job['result']) if job['result'] else {}
                if terminal.get('run_id') != authority['training_run_id']:
                    raise UnavailableError('MODEL_JOB_RESULT_MISMATCH', 'quant')

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
        checked_at = utc_now().isoformat()
        try:
            models = self.list()
            prepared = self.list_prepared()
            for model in prepared:
                self.verify(model)
            return {'status': 'healthy' if models else 'unavailable', 'models': len(models),
                    'production_validated': sum(m.oos_validated for m in models), 'prepared_models': len(prepared),
                    'quarantined_models': len(prepared) - len(models), 'checked_at': checked_at,
                    'scope': ['metadata', 'publication_authority', 'checkpoint_bytes', 'dataset_bytes'],
                    'integrity_guarantee': 'LOCAL_CORRUPTION_DETECTION_NOT_FULL_DISK_WRITER_AUTHENTICATION'}
        except Exception as exc:
            return {'status': 'unavailable', 'reason': 'REGISTRY_INTEGRITY_FAILURE', 'checked_at': checked_at,
                    'detail_code': getattr(exc, 'reason', type(exc).__name__),
                    'scope': ['metadata', 'publication_authority', 'checkpoint_bytes', 'dataset_bytes']}

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
            return self.register(self.get_prepared(model_id, version))
        return self.register(artifact)
