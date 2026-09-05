"""Business-only QuantEngine and immutable, content-verified Qlib execution."""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, time
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator

from .common import CoreModel, UnavailableError, digest, utc_now
from .model_registry import ModelRegistry, ModelArtifact, file_hash, safe_id, write_once, implementation_identity


def aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('quant timestamps must be timezone aware')
    return value


def canonical_instrument(value: str) -> str:
    import re
    if not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', value):
        raise ValueError('canonical instrument_id required, e.g. 600519.SH')
    return value


class FeatureRow(CoreModel):
    instrument_id: str
    available_at: datetime
    values: tuple[float, ...] = Field(min_length=1, max_length=512)

    _id = field_validator('instrument_id')(canonical_instrument)
    _time = field_validator('available_at')(aware)


class QuantRequest(CoreModel):
    model_id: str
    model_version: str | None = None
    as_of: datetime
    data_version: str
    universe_version: str
    policy_version: str
    instruments: tuple[str, ...] = Field(max_length=5000)
    data_snapshot_id: str | None = None
    run_id: str | None = None
    features: tuple[FeatureRow, ...] = Field(default=(), max_length=5000)

    _time = field_validator('as_of')(aware)

    @field_validator('instruments')
    @classmethod
    def instruments_valid(cls, values):
        for value in values:
            canonical_instrument(value)
        if len(values) != len(set(values)):
            raise ValueError('duplicate instruments')
        return tuple(sorted(values))

    @model_validator(mode='after')
    def versioned(self):
        if not self.data_version or not self.universe_version or not self.policy_version:
            raise ValueError('quant inputs must be versioned')
        if any(row.available_at > self.as_of for row in self.features):
            raise ValueError('future feature input')
        if len({row.instrument_id for row in self.features}) != len(self.features):
            raise ValueError('duplicate feature rows')
        return self


class QuantScore(CoreModel):
    instrument_id: str
    raw_score: float
    rank: int = Field(ge=1)
    score_type: Literal['MODEL_RAW'] = 'MODEL_RAW'


class QuantRejection(CoreModel):
    instrument_id: str
    reason_codes: tuple[str, ...]


class QuantResult(CoreModel):
    run_id: str
    model_id: str
    model_version: str
    as_of: datetime
    execution_time: datetime
    data_snapshot_id: str
    data_version: str
    universe_version: str
    feature_schema_hash: str
    checkpoint_hash: str
    policy_version: str
    scores: tuple[QuantScore, ...] = ()
    rejected: tuple[QuantRejection, ...] = ()
    errors: tuple[str, ...] = ()
    quality: Literal['VALID', 'RESEARCH_ONLY', 'UNAVAILABLE']
    model_status: str
    artifact_path: str
    result_hash: str
    source_code_hash: str | None = None
    git_commit: str | None = None

    _times = field_validator('as_of', 'execution_time')(aware)


class QuantEngine(ABC):
    @abstractmethod
    def rank(self, request: QuantRequest, *, allow_research: bool = False) -> QuantResult: ...
    def predict(self, request: QuantRequest, *, allow_research: bool = False) -> QuantResult:
        return self.rank(request, allow_research=allow_research)
    @abstractmethod
    def train(self, request): ...
    @abstractmethod
    def backtest(self, *args, **kwargs): ...
    @abstractmethod
    def health(self) -> dict: ...


class QlibQuantEngine(QuantEngine):
    def __init__(self, registry: ModelRegistry, root: str | Path, data_dir: str | Path | None = None,
                 *, timeout: float = 300., predictor=None):
        self.registry = registry
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.data_dir = Path(data_dir).resolve() if data_dir else None
        self.timeout = timeout
        self._predictor = predictor  # Explicit dependency injection for offline tests only.

    def inspect_data(self) -> dict:
        if self.data_dir is None or not (self.data_dir / 'calendars/day.txt').is_file():
            raise UnavailableError('QLIB_DATA_UNAVAILABLE', 'quant')
        from .data import LocalQlibDataAdapter
        data_manifest = LocalQlibDataAdapter(self.data_dir).manifest()
        files = tuple(f.model_dump(mode='json') for f in data_manifest.files)
        universe_file = self.data_dir / 'instruments/csi300.txt'
        if not universe_file.is_file() or not any(f['path'].startswith('features/') for f in files):
            raise UnavailableError('QLIB_DATA_INCOMPLETE', 'quant')
        return {'data_version': data_manifest.digest, 'universe_version': file_hash(universe_file),
                'data_path': str(self.data_dir), 'files': files}

    def _freeze_data(self, request: QuantRequest) -> Path:
        if len(request.data_version) != 64 or any(c not in '0123456789abcdef' for c in request.data_version):
            raise ValueError('native data_version must be a SHA256 digest')
        parent = self.root / 'data_snapshots'
        target = parent / request.data_version
        if (target / '_manifest.json').is_file():
            self._verify_snapshot(target, request)
            return target
        manifest = self.inspect_data()
        if manifest['data_version'] != request.data_version or manifest['universe_version'] != request.universe_version:
            raise UnavailableError('DATA_OR_UNIVERSE_VERSION_MISMATCH', 'quant')
        parent.mkdir(exist_ok=True)
        target = parent / manifest['data_version']
        # Exclusive directory claim. An incomplete copy is never a readable snapshot.
        if not (target / '_manifest.json').is_file():
            temp = parent / ('staging-' + uuid.uuid4().hex)
            temp.mkdir()
            for item in manifest['files']:
                source = self.data_dir / item['path']
                dest = temp / item['path']
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, dest)
                if file_hash(dest) != item['sha256']:
                    raise UnavailableError('DATA_CHANGED_DURING_SNAPSHOT', 'quant')
            write_once(temp / '_manifest.json', manifest)
            try:
                temp.rename(target)
            except FileExistsError:
                # Another process completed the identical content-addressed snapshot.
                pass
        self._verify_snapshot(target, request)
        return target

    @staticmethod
    def _verify_snapshot(target: Path, request: QuantRequest) -> None:
        saved = json.loads((target / '_manifest.json').read_text(encoding='utf-8'))
        if (saved['data_version'] != request.data_version or digest(saved['files']) != request.data_version
                or saved['universe_version'] != request.universe_version):
            raise UnavailableError('SNAPSHOT_MANIFEST_CORRUPT', 'quant')
        for item in saved['files']:
            source = (target / item['path']).resolve()
            if not source.is_relative_to(target.resolve()) or source.is_symlink() or file_hash(source) != item['sha256']:
                raise UnavailableError('SNAPSHOT_CONTENT_CORRUPT', 'quant')

    def rank(self, request: QuantRequest, *, allow_research: bool = False) -> QuantResult:
        run_id = safe_id(request.run_id) if request.run_id else 'quant-' + uuid.uuid4().hex
        run_dir = self.root / 'runs' / run_id
        try:
            run_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise ValueError('RUN_ID_ALREADY_EXISTS') from exc
        write_once(run_dir / 'request.json', request)
        execution_time = utc_now()
        implementation = implementation_identity()
        write_once(run_dir / 'implementation.json', implementation)
        try:
            model = self.registry.get(request.model_id, request.model_version)
            if model.status == 'RESEARCH_ONLY' and not allow_research:
                raise UnavailableError('MODEL_RESEARCH_ONLY', 'quant')
            if model.status == 'CLEAN_SPLIT' and model.training_information_cutoff is None:
                raise UnavailableError('MODEL_INFORMATION_CUTOFF_UNAVAILABLE', 'quant')
            information_cutoff = model.training_information_cutoff or model.validation_window[1]
            if request.as_of < information_cutoff:
                raise UnavailableError('MODEL_NOT_AVAILABLE_AS_OF', 'quant')
            if request.as_of > execution_time:
                raise UnavailableError('AS_OF_IN_FUTURE', 'quant')
            self.registry.verify(model)
            raw, rejected = self._predict(request, model, run_dir)
            if implementation_identity()['source_code_hash'] != implementation['source_code_hash']:
                raise UnavailableError('IMPLEMENTATION_CHANGED_DURING_RUN', 'quant')
            expected = set(request.instruments)
            returned = [i for i, _ in raw]
            if len(returned) != len(set(returned)) or not set(returned).issubset(expected):
                raise UnavailableError('INVALID_PREDICTION_UNIVERSE', 'quant')
            rejected_ids = {r.instrument_id for r in rejected}
            rejected.extend(QuantRejection(instrument_id=i, reason_codes=('FEATURES_UNAVAILABLE',))
                            for i in sorted(expected - set(returned) - rejected_ids))
            if any(not math.isfinite(float(v)) for _, v in raw):
                raise UnavailableError('NONFINITE_PREDICTION', 'quant')
            scores = tuple(QuantScore(instrument_id=i, raw_score=float(v), rank=k + 1)
                           for k, (i, v) in enumerate(sorted(raw, key=lambda pair: (-pair[1], pair[0]))))
            quality = 'UNAVAILABLE' if rejected else ('RESEARCH_ONLY' if model.status == 'RESEARCH_ONLY' else 'VALID')
            payload = dict(run_id=run_id, model_id=model.model_id, model_version=model.model_version,
                as_of=request.as_of, execution_time=execution_time,
                data_snapshot_id=request.data_snapshot_id or request.data_version, data_version=request.data_version,
                universe_version=request.universe_version, feature_schema_hash=model.feature_schema_hash,
                checkpoint_hash=model.checkpoint_hash, policy_version=request.policy_version, scores=scores,
                rejected=tuple(rejected), errors=(), quality=quality, model_status=model.status,
                artifact_path=str(run_dir), **implementation)
            result = QuantResult(**payload, result_hash=digest(payload))
            write_once(run_dir / 'result.json', result)
            write_once(run_dir / 'artifacts.json', {'run_id': run_id, 'files': [
                {'path': p.name, 'sha256': file_hash(p)} for p in sorted(run_dir.iterdir()) if p.is_file()]})
            return result
        except Exception as exc:
            write_once(run_dir / 'failure.json', {'run_id': run_id, 'quality': 'UNAVAILABLE',
                       'error': getattr(exc, 'reason', type(exc).__name__), 'execution_time': execution_time})
            raise

    def _predict(self, request, model, run_dir):
        if self._predictor is not None:
            return self._predictor(request, model), []
        if model.format == 'linear_v1':
            if digest([r.model_dump(mode='json') for r in request.features]) != request.data_version:
                raise UnavailableError('FEATURE_SNAPSHOT_HASH_MISMATCH', 'quant')
            if not request.instruments:
                return [], []
            import hashlib
            content = Path(model.artifact_path).read_bytes()
            if hashlib.sha256(content).hexdigest() != model.checkpoint_hash:
                raise UnavailableError('MODEL_HASH_MISMATCH', 'quant')
            document = json.loads(content)
            names = tuple(document['feature_schema'])
            if (names != model.feature_schema or digest(names) != model.feature_schema_hash
                    or len(document['coefficients']) != len(names)):
                raise UnavailableError('FEATURE_SCHEMA_MISMATCH', 'quant')
            rows = {r.instrument_id: r for r in request.features}
            predictions = []
            for instrument in request.instruments:
                if instrument not in rows:
                    continue
                values = rows[instrument].values
                if len(values) != len(names):
                    raise UnavailableError('FEATURE_SHAPE_MISMATCH', 'quant')
                predictions.append((instrument, document['intercept'] + sum(v * c for v, c in zip(values, document['coefficients']))))
            return predictions, []
        local_time = request.as_of.astimezone(ZoneInfo('Asia/Shanghai'))
        if local_time.time() < time(15):
            raise UnavailableError('DAILY_CLOSE_NOT_AVAILABLE', 'quant')
        frozen_data = self._freeze_data(request)
        worker_request = {'request': request.model_dump(mode='json'), 'model': model.model_dump(mode='json'),
                          'data_path': str(frozen_data), 'output': str(run_dir / 'worker_result.json')}
        write_once(run_dir / 'worker_request.json', worker_request)
        env = os.environ.copy()
        env.update(QLIB_KERNELS='1', PYTHONUTF8='1', PYTHONUNBUFFERED='1', PYTHONDONTWRITEBYTECODE='1')
        command = [sys.executable, '-B', '-X', 'utf8', '-m', 'stockfish_core.quant_worker', str(run_dir / 'worker_request.json')]
        # communicate/run owns the whole deadline, unlike stdout iteration then wait(timeout).
        try:
            completed = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                timeout=self.timeout, cwd=str(Path(__file__).resolve().parents[1]), env=env)
        except subprocess.TimeoutExpired as exc:
            raise UnavailableError('QUANT_TIMEOUT', 'quant') from exc
        (run_dir / 'worker.log').write_text(completed.stdout + completed.stderr, encoding='utf-8')
        if completed.returncode != 0:
            raise UnavailableError('QLIB_WORKER_FAILED', 'quant')
        self._verify_snapshot(frozen_data, request)
        worker = json.loads((run_dir / 'worker_result.json').read_text(encoding='utf-8'))
        if worker['feature_schema_hash'] != model.feature_schema_hash:
            raise UnavailableError('FEATURE_SCHEMA_MISMATCH', 'quant')
        return [(r['instrument_id'], r['raw_score']) for r in worker['scores']], [
            QuantRejection(**r) for r in worker['rejected']]

    def train(self, request):
        from .experiments import train_linear
        return train_linear(request, self.registry, self.root / 'training')

    def finetune(self, request, *, base_model_id: str, base_model_version: str):
        """Versioned clean CPU refit. Native legacy warm starts remain experimental.

        Refitting the full clean window avoids carrying unknown training samples
        from an older checkpoint into a purported out-of-sample fold.
        """
        from .experiments import finetune_linear
        return finetune_linear(request, base_model_id=base_model_id, base_model_version=base_model_version,
                               registry=self.registry, root=self.root / 'training')

    def backtest(self, request, bars, policy, *, calendar, tradability):
        from .backtest import BacktestEngine
        return BacktestEngine(calendar, tradability, self.root / 'backtests').run(request, bars, policy)

    def health(self) -> dict:
        health = self.registry.health()
        formats = {m.format for m in self.registry.list()} if health['status'] == 'healthy' else set()
        data_available = bool(self.data_dir and (self.data_dir / 'calendars/day.txt').is_file()
                              and (self.data_dir / 'instruments/csi300.txt').is_file()
                              and (self.data_dir / 'features').is_dir())
        from importlib.metadata import PackageNotFoundError, version
        try:
            native_version = version('pyqlib')
        except PackageNotFoundError:
            native_version = None
        native_ready = data_available and native_version == '0.9.7'
        ready = health['status'] == 'healthy' and ('linear_v1' in formats or native_ready)
        return {'status': 'healthy' if ready else 'unavailable', 'registry': health, 'native': 'CSI300_ALPHA158_ONLY',
                'training': 'LINEAR_CPU_CLEAN_SPLIT', 'legacy_docker': 'EXPERIMENTAL_DISABLED',
                'data_available': data_available, 'native_runtime_version': native_version,
                'native_status': 'ready_for_content_verification' if native_ready else 'unavailable',
                'integrity': 'FULL_CONTENT_HASH_VERIFIED_ON_EVERY_RANK',
                'production_validated': health.get('production_validated', 0)}
