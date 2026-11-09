"""Filesystem-only native R2 execution for the explicit premarket command.

The returned owner is an invocation-local capability. Its JSON exports are
research records and cannot reconstruct that owner or authorize a theme parent.
"""
from __future__ import annotations

from stockfish_core.file_integrity import content_equal, content_reference_equal, content_fingerprint, verify_content_reference

from copy import deepcopy
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import threading
from types import SimpleNamespace
import weakref

from .contracts import SourceReceipt
from .short_horizon_archive_bridge_v1 import digest_json_stream, digest_market_input
from .provider_normalization import normalize_capture_with_report, _clock
from .research_context_artifacts import SourceArtifactReader
from .saved_r2_delivery import _authenticate_capture
from .visibility import stamp

VERSION = 'PREMARKET_NATIVE_CORE_V1'
STRATEGY = 'SHORT_HORIZON_STRENGTH_R2'
_OWNERS = weakref.WeakKeyDictionary()
_MINT = object()


def _require(condition, reason):
    if not condition:
        raise ValueError('PREMARKET_CORE_' + reason)


def _json(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            _require(key not in value, 'DUPLICATE_JSON_KEY')
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('NONFINITE_JSON')))


def _two(reference):
    return {key: reference[key] for key in ('path', 'sha256')}


class _LocatorBinding:
    def __init__(self, declaration):
        _require(set(declaration) == {'path','target','fp','archive_root','owned','directories'}
            and set(declaration['fp']) == {'st_dev','st_ino','st_mode','st_size',
                'st_mtime_ns','st_ctime_ns','st_flags'}
            and all(type(value) is int for value in declaration['fp'].values())
            and type(declaration['owned']) is bool
            and all(Path(declaration[key]).is_absolute() for key in ('path','target','archive_root')),
            'COMPLETE_LOCATOR_SCHEMA_REQUIRED')
        self.declaration = deepcopy(declaration)
        path = Path(declaration['path'])
        expected = {str(item) for item in (path.parent, path.parent.parent,
            Path(declaration['archive_root']), Path(declaration['target']))}
        directories = declaration['directories']
        _require(isinstance(directories, dict) and set(directories) == expected
            and all(isinstance(value, dict) and set(value) == {'st_dev','st_ino','st_mode'}
                and all(type(number) is int for number in value.values())
                for value in directories.values()), 'COMPLETE_LOCATOR_DIRECTORY_BASELINE_REQUIRED')
        self.directories = deepcopy(directories)
        self.validate()

    @staticmethod
    def _directory(path):
        value = Path(path).lstat()
        _require(stat.S_ISDIR(value.st_mode), 'LOCATOR_DIRECTORY_REQUIRED')
        return {key: getattr(value, key) for key in ('st_dev','st_ino','st_mode')}

    def validate(self):
        value = self.declaration
        path = Path(value['path'])
        current = path.lstat()
        fp = {key: getattr(current, key, 0) for key in value['fp']}
        _require(stat.S_ISLNK(current.st_mode)
            and path == Path(value['archive_root'])/'cache/raw'
            and os.readlink(path) == value['target']
            and path.resolve(strict=True) == Path(value['target']), 'LOCATOR_CHANGED')
        _require(all(Path(path).is_dir() for path in self.directories), 'LOCATOR_DIRECTORY_CHANGED')


class _BudgetRegistry:
    """Adapter for the mature original-trio authenticator, retaining ref4."""
    def __init__(self, budget):
        self.budget, self.references = budget, {}
        self._capture_bytes = None
        self._capture_owner = None

    def _capture_objects(self, references):
        from .premarket_runtime_v1 import fingerprint
        _require(self._capture_owner == (os.getpid(), threading.get_ident()),
            'CAPTURE_AUTHENTICATION_OWNER_CHANGED')
        self.budget.check('core_authentication_objects')
        for reference in references:
            path = Path(reference['path'])
            _require(str(path.resolve(strict=True)) == reference['path']
                and fingerprint(path) == reference['fp'],
                'ORIGINAL_CHANGED_DURING_AUTHENTICATION')

    @contextmanager
    def authentication(self):
        """Reuse bytes only inside one freshly authenticated capture.

        The cache is discarded before normalization or strategy evaluation.
        Every later authentication and terminal check reads the originals again.
        Physical identities guard this transaction only, never a saved source.
        """
        _require(self._capture_bytes is None, 'NESTED_CAPTURE_AUTHENTICATION')
        self._capture_bytes = {}
        self._capture_owner = (os.getpid(), threading.get_ident())
        try:
            yield
            self._capture_objects([ref for _, ref in self._capture_bytes.values()])
        finally:
            self._capture_bytes = None
            self._capture_owner = None

    @contextmanager
    def mature_verifier(self, references):
        """Supply the unchanged original verifier with this transaction's bytes."""
        from .premarket_capture_cache_v1 import _VERIFY_LOCK
        allowed = {str(Path(ref['path']).resolve(strict=True)) for ref in references}
        _require(self._capture_bytes is not None and len(allowed) == len(references)
            and allowed.issubset(self._capture_bytes), 'EXACT_CAPTURE_SNAPSHOTS_REQUIRED')

        def get(path):
            resolved = str(Path(path).resolve(strict=True))
            _require(resolved in allowed, 'UNREGISTERED_MATURE_CAPTURE_READ')
            raw, reference = self._capture_bytes[resolved]
            self._capture_objects((reference,))
            return raw

        def read_bytes(path):
            return get(path)

        def read_text(path, encoding=None, errors=None):
            return io.TextIOWrapper(io.BytesIO(get(path)), encoding=encoding, errors=errors).read()

        with _VERIFY_LOCK:
            old_bytes, old_text = Path.read_bytes, Path.read_text
            Path.read_bytes, Path.read_text = read_bytes, read_text
            try:
                yield
                self._capture_objects([self._capture_bytes[path][1] for path in allowed])
            finally:
                Path.read_bytes, Path.read_text = old_bytes, old_text

    def read(self, path, sha256=None, limit=None):
        if self._capture_bytes is not None:
            canonical = str(Path(path).resolve(strict=True))
            cached = self._capture_bytes.get(canonical)
            if cached is not None:
                raw, ref = cached
                # Retain Budget.read's input contract even when no second file
                # read is needed. Malformed requests remain sticky failures for
                # the real budget, just as they are on the fresh-read path.
                from .premarket_runtime_v1 import FP_KEYS
                def need(condition, reason):
                    if not condition:
                        if callable(getattr(self.budget, '_fail', None)):
                            self.budget._fail(reason)
                        _require(False, reason)
                if isinstance(sha256, dict):
                    need(set(sha256) == {'path', 'sha256', 'bytes', 'fp'}
                        and isinstance(sha256['path'], str)
                        and Path(sha256['path']).is_absolute() and sha256['path'] == canonical
                        and isinstance(sha256['fp'], dict) and set(sha256['fp']) == set(FP_KEYS)
                        and all(type(value) is int for value in sha256['fp'].values())
                        and type(sha256['bytes']) is int and sha256['bytes'] >= 0
                        and sha256['fp']['st_size'] == sha256['bytes'], 'INVALID_FILE_REFERENCE_SCHEMA')
                    declared_sha = sha256['sha256']
                else:
                    need(sha256 is None or isinstance(sha256, str), 'INVALID_FILE_REFERENCE')
                    declared_sha = sha256
                need(declared_sha is None or isinstance(declared_sha, str)
                    and re.fullmatch('[0-9a-f]{64}', declared_sha) is not None,
                    'INVALID_SHA256_REFERENCE')
                need(limit is None or type(limit) is int and limit >= 0, 'INVALID_FILE_READ_LIMIT')
                self._capture_objects((ref,))
                need(limit is None or len(raw) <= limit, 'FILE_READ_LIMIT_EXCEEDED')
                if isinstance(sha256, str):
                    need(ref['sha256'] == sha256, 'FILE_SHA256_CHANGED')
                if isinstance(sha256, dict):
                    need(content_reference_equal(ref, sha256), 'FILE_REFERENCE_CHANGED')
                return raw
        raw, ref = self.budget.read(path, expected=sha256, limit=limit)
        previous = self.references.setdefault(ref['path'], ref)
        _require(content_reference_equal(previous, ref), 'ORIGINAL_SOURCE_CONTENT_CHANGED')
        if self._capture_bytes is not None:
            _require(type(raw) is bytes and len(raw) == ref['bytes']
                and hashlib.sha256(raw).hexdigest() == ref['sha256'],
                'ORIGINAL_SOURCE_CONTENT_CHANGED')
            self._capture_bytes[ref['path']] = (raw, deepcopy(ref))
        return raw

    def registered(self, declaration, base):
        _require(set(declaration) == {'path', 'sha256'}, 'PHYSICAL_LOCATOR_SCHEMA')
        path = Path(declaration['path'])
        if not path.is_absolute():
            path = Path(base) / path
        path = path.resolve(strict=True)
        self.read(path, declaration['sha256'])
        return SimpleNamespace(path=path, sha256=declaration['sha256'])

    def terminal(self):
        _require(self._capture_bytes is None, 'TERMINAL_REQUIRES_FRESH_ORIGINAL_READS')
        # Snapshot first: validation cannot append to the iteration scope.
        for expected in tuple(self.references.values()):
            _, actual = self.budget.read(expected['path'], expected=expected)
            _require(content_reference_equal(actual, expected), 'ORIGINAL_SOURCE_CONTENT_CHANGED')


class _BudgetSourceReader(SourceArtifactReader):
    def __init__(self, receipts, paths, registry):
        super().__init__(receipts, paths)
        self._registry = registry

    def __call__(self, receipt_id):
        _require(receipt_id in self._receipts and receipt_id in self._paths,
            'SOURCE_CAPABILITY_UNAVAILABLE')
        return self._registry.read(self._paths[receipt_id],
            self._receipts[receipt_id].content_hash)


def _pin_capture_aux(item, result, registry, coverage_type, locators):
    producers = item.get('producer_files', ())
    if coverage_type != 'TEST_FIXTURE' or producers:
        expected = result.get('implementation_source_hashes')
        names = {'provider_requests.py', 'provider_requests_worker.py', 'provider_budget.py'}
        _require(isinstance(expected, dict) and set(expected) == names
            and len(producers) == 3 and {Path(ref['path']).name for ref in producers} == names,
            'ORIGINAL_THREE_PRODUCER_FILES_REQUIRED')
        for producer in producers:
            raw = registry.read(producer['path'], producer)
            _require(hashlib.sha256(raw).hexdigest() == expected[Path(producer['path']).name],
                'ORIGINAL_PRODUCER_CHANGED')
    if item.get('verification_locator') is not None:
        locators.append(_LocatorBinding(item['verification_locator']))


def _typed(result, physical):
    params = result['parameters']
    end = params.get('end_date') or params.get('date') or params.get('trade_date')
    day = date.fromisoformat(str(end)) if end is not None else None
    coverage = result.get('coverage', {})
    return SourceReceipt(receipt_id=result['request_receipt_id'], provider=result['provider'],
        dataset=result['endpoint'], content_hash=physical['content_hash'],
        field_version=result['field_version'], row_count=len(result['rows']),
        status=result['status'], pagination_complete=result['pagination_complete'],
        parameters={key: value if isinstance(value, (str, int, float, bool)) or value is None
                    else str(value) for key, value in params.items()},
        source_url=physical.get('source_url'),
        instrument_ids=tuple(sorted(str(value) for value in coverage.get('returned_instrument_ids', ()) if value)),
        business_sessions=tuple(sorted({date.fromisoformat(str(value))
            for value in coverage.get('business_sessions', ())})), **_clock(physical, day))


def _calendar_sessions(results, observation):
    days = {}
    for result in results:
        if result['endpoint'] != 'calendar' or result['status'] != 'COMPLETE' or not result['pagination_complete']:
            continue
        start = date.fromisoformat(result['parameters']['start_date'])
        end = date.fromisoformat(result['parameters']['end_date'])
        local = {}
        for row in result['rows']:
            day = date.fromisoformat(row['calendar_date'])
            flag = row['is_trading_day']
            _require(flag in ('0', '1') and day not in local, 'INVALID_CALENDAR_ROW')
            local[day] = flag
        expected = {start + timedelta(days=n) for n in range((end-start).days+1)}
        _require(set(local) == expected, 'INCOMPLETE_NATURAL_DAY_CALENDAR')
        for day, flag in local.items():
            _require(day not in days or days[day] == flag, 'CONFLICTING_CALENDAR')
            days[day] = flag
    sessions = tuple(sorted(day for day, flag in days.items() if flag == '1' and day <= observation))
    _require(sessions and sessions[-1] == observation, 'OBSERVATION_NOT_VERIFIED_SESSION')
    return sessions


class NativeCore:
    """Only ``run_core`` mints an owner; serialization is deliberately absent."""
    __slots__ = ('__weakref__',)

    def __new__(cls, token=None):
        _require(token is _MINT, 'LIVE_OWNER_MUST_BE_MINTED_BY_RUN_CORE')
        return super().__new__(cls)

    def __reduce__(self):
        raise TypeError('NATIVE_CORE_OWNER_NOT_SERIALIZABLE')

    @property
    def run(self):
        return deepcopy(_owner_state(self)['run'])

    @property
    def artifacts(self):
        return deepcopy(_owner_state(self)['artifacts'])

    @property
    def source_records(self):
        return deepcopy(_owner_state(self)['captures'])

    @property
    def validation_references(self):
        return deepcopy(list(_owner_state(self)['registry'].references.values()))

    def validate(self):
        from .short_horizon_participation import short_horizon_source_identity
        state = _owner_state(self)
        checkpoint = lambda: state['registry'].budget.check('core_identity')
        _require(digest_market_input(state['bundle'], checkpoint=checkpoint) == state['input_hash']
            and digest_json_stream(state['run'], checkpoint=checkpoint) == state['run_hash']
            and short_horizon_source_identity() == state['strategy_identity'], 'LIVE_OWNER_CHANGED')
        state['registry'].terminal()
        for locator in state['locators']:
            locator.validate()
        return True


def _owner_state(owner):
    _require(type(owner) is NativeCore and owner in _OWNERS, 'GENUINE_LIVE_OWNER_REQUIRED')
    return _OWNERS[owner]


def run_core(*, captures, observation_date, target_session, cutoff, output_dir, budget,
             coverage_type='PROSPECTIVE_CAPTURED'):
    """Authenticate all originals, normalize inline, and run unchanged R2 once.

    ``captures`` is an iterable of cache metadata dictionaries (or records with
    ``metadata`` attributes). RAW-only historical sources belong to phase two.
    Missing factors remain the existing normalizer's UNKNOWN; no receipt or
    stock identity is synthesized to fill a source gap.
    """
    from .candidate_service import CandidateService
    from .strategy_experiments import run_short_horizon_strategy
    from .short_horizon_participation import short_horizon_source_identity
    decision, observed = stamp(cutoff), date.fromisoformat(str(observation_date))
    _require(decision <= datetime.now(timezone.utc), 'FUTURE_CUTOFF')
    directory = Path(output_dir).resolve()
    _require(not directory.exists(), 'OUTPUT_MUST_BE_NEW')
    registry, results, declarations, by_id, paths = _BudgetRegistry(budget), [], [], {}, {}
    # Use the already retained equivalent encoders, with the same live resource
    # guards. Hash every current object afresh; no saved or mutable hash cache.
    checkpoint = lambda: budget.check('core_identity')
    locators = []
    budget.check('core_authenticate')
    for capture in captures:
        item = capture if isinstance(capture, dict) else capture.metadata
        with registry.authentication():
            documents = {key: _json(registry.read(item[key]['path'], item[key]))
                         for key in ('result', 'raw', 'receipt')}
            result, physical = documents['result'], documents['receipt']
            _pin_capture_aux(item, result, registry, coverage_type, locators)
            declared = _typed(result, physical)
            registration = dict(archive_root=str(item['archive_root']),
                **{key: _two(item[key]) for key in ('result', 'raw', 'receipt')},
                typed_receipt=declared.model_dump(mode='json'))
            with registry.mature_verifier((item['raw'], item['receipt'])):
                authenticated, raw_path, audit = _authenticate_capture(registry, registration, Path.cwd())
        rid = authenticated.receipt_id
        _require(rid not in by_id, 'DUPLICATE_RECEIPT')
        _require(max(authenticated.source_published_at, authenticated.first_seen_at,
                     authenticated.ingested_at) <= decision, 'CAPTURE_AFTER_CUTOFF')
        _require(audit['completed_at'] is None or stamp(audit['completed_at']) <= decision,
            'CAPTURE_NOT_COMPLETED_AT_CUTOFF')
        by_id[rid], paths[rid] = authenticated, raw_path
        declarations.append(registration)
        results.append(result)
        budget.check('core_authenticate')
    sessions = _calendar_sessions(results, observed)
    reader = _BudgetSourceReader(tuple(by_id.values()), paths, registry)
    calendar = reader.target_calendar_evidence(observation_date=observed,
        target_session=target_session, cutoff=decision, coverage_type=coverage_type)
    _require(calendar['status'] == 'AVAILABLE', 'NEXT_TARGET_CALENDAR_UNAVAILABLE')
    budget.check('core_normalize')
    bundle, normalization = normalize_capture_with_report(results, observed, decision, sessions,
        coverage_type=coverage_type)
    _require({row.receipt_id: row for row in bundle.receipts} == by_id,
        'NORMALIZED_RECEIPTS_DIFFER_FROM_AUTHENTICATED_ORIGINALS')
    registry.terminal()
    budget.check('core_strategy')
    strategy_identity = short_horizon_source_identity()
    run = run_short_horizon_strategy(bundle, cutoff=decision, strategy_id=STRATEGY,
        _source_payload_reader=reader, _source_receipts=reader.receipts,
        _target_calendar_context=calendar,
        _target_calendar_validator=reader.validate_target_calendar_context)
    run.update(generated_at=datetime.now(timezone.utc).isoformat(),
        artifact_role='NATIVE_READ_ONLY_RESEARCH_ARTIFACT_NOT_BUSINESS_DB_REGISTRATION',
        source_database_written=False)
    run['content_hash'] = digest_json_stream({key: value for key, value in run.items()
        if key != 'content_hash'}, checkpoint=checkpoint)
    result = run['results'][STRATEGY]
    eligible, rows = result['eligible_pool_ids'], result['items']
    _require(len(eligible) == len(set(eligible)) and set(eligible) ==
        {row['instrument_id'] for row in rows} and len(rows) == len(eligible), 'FULL_ELIGIBLE_POOL_REQUIRED')
    registry.terminal()
    budget.check('core_exports')

    class Encoder(CandidateService):
        def strategy_experiment(self, run_id):
            _require(run_id == run['run_id'], 'EXPORT_RUN_MISMATCH')
            return run

    encoder = object.__new__(Encoder)
    directory.mkdir(parents=True, exist_ok=False)
    artifacts = {}

    def save(name, raw):
        with (directory / name).open('xb') as handle:
            handle.write(raw)
        _, artifacts[name] = budget.read(directory / name,
            expected=hashlib.sha256(raw).hexdigest())

    save('canonical-run.json.gz', gzip.compress(json.dumps(run, ensure_ascii=False,
        allow_nan=False).encode(), mtime=0))
    save('input.json.gz', gzip.compress(json.dumps(bundle.model_dump(mode='json'),
        ensure_ascii=False, allow_nan=False).encode(), mtime=0))
    for selection in ('observation', 'all', 'focus', 'unselected'):
        save(selection + '.csv', encoder.export_strategy_experiment_csv(run['run_id'],
            strategy_id=STRATEGY, selection=selection).encode())
    save('observation.json', encoder.export_strategy_experiment_json(run['run_id'],
        strategy_id=STRATEGY, selection='observation').encode())
    manifest = dict(schema_version=VERSION, status='PASS', run_id=run['run_id'],
        observation_date=str(observed), target_session=str(target_session),
        decision_cutoff=decision.isoformat(), input_hash=digest_market_input(bundle, checkpoint=checkpoint),
        counts=deepcopy(result['counts']), artifacts=deepcopy(artifacts),
        canonical_content_hash=run['content_hash'],
        official_exports={name: dict(source_content_hash=run['content_hash'],
            reference=deepcopy(artifacts[name])) for name in
            ('observation.csv','all.csv','focus.csv','unselected.csv','observation.json')},
        source_receipts=[row.model_dump(mode='json') for row in by_id.values()],
        normalization_report=normalization, source_database_written=False,
        parameter_status='UNVALIDATED', market_edge='NOT_ESTABLISHED',
        daily_v1_readiness='UNKNOWN_NOT_EXECUTED', source_capability_serialized=False)
    save('manifest.json', json.dumps(manifest, ensure_ascii=False, allow_nan=False).encode())
    registry.terminal()
    budget.check('core_complete')
    owner = NativeCore(_MINT)
    _OWNERS[owner] = dict(bundle=bundle, run=run, registry=registry,
        reader=reader, captures=declarations, artifacts=artifacts,
        input_hash=digest_market_input(bundle, checkpoint=checkpoint),
        run_hash=digest_json_stream(run, checkpoint=checkpoint),
        strategy_identity=strategy_identity, locators=locators)
    return owner
