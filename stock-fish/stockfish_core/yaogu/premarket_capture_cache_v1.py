"""Immutable provider originals and a JSON request index for local R2 research.

This module does not open a business database, normalize inputs, select stocks,
or issue source capabilities.  The index contains references, never stock rows.
The caller owns resource policy through the small ``budget`` protocol below.
An unsuccessful/unfinished request is retained and is never retried implicitly.
"""
from __future__ import annotations

from stockfish_core.file_integrity import content_equal, content_reference_equal, content_fingerprint, verify_content_reference

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import threading
import uuid

from . import historical_replay, provider_requests
from .provider_requests import execute_request, validate_request

VERSION = 'PREMARKET_CAPTURE_CACHE_V1'
COLLECTION_VERSION = 'PREMARKET_CAPTURE_COLLECTION_V1'
FP_KEYS = ('st_dev', 'st_ino', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_flags')
SH = timezone(timedelta(hours=8))
CODE = re.compile(r'(?:sh|sz|bj)\.[0-9]{6}', re.ASCII)
ORDINARY = re.compile(r'(?:sh\.(?:600|601|603|605)|sz\.(?:000|001|002|003))[0-9]{3}', re.ASCII)
PRODUCERS = ('provider_requests.py', 'provider_requests_worker.py', 'provider_budget.py')
_VERIFY_LOCK = threading.RLock()
CONFIG_KEYS = {'request_timeout_seconds', 'max_http_requests_per_request', 'request_http_attempts',
    'max_cache_entries', 'source_file_bytes', 'index_file_bytes', 'budget_path', 'budget_id',
    'calendar_lookback_days', 'calendar_lookahead_days', 'batch_size', 'basic_all_first',
    'basic_batch_fallback', 'offline', 'retry_failed', 'include_industries', 'include_concepts',
    'max_theme_nodes', 'allow_test_fixture'}


def _config(value):
    value = dict(value or {})
    _need(not set(value) - CONFIG_KEYS, 'UNKNOWN_CAPTURE_CONFIGURATION:' + ','.join(sorted(set(value)-CONFIG_KEYS)))
    for key in ('basic_all_first', 'basic_batch_fallback', 'offline', 'retry_failed',
                'include_industries', 'include_concepts', 'allow_test_fixture'):
        if key in value:
            _need(type(value[key]) is bool, 'BOOLEAN_CAPTURE_CONFIGURATION:' + key)
    return value


def _need(value, reason):
    if not value:
        raise ValueError(reason)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            _need(key not in result, 'DUPLICATE_JSON_KEY')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('NONFINITE_JSON')))


def _stamp(value):
    result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    _need(result.tzinfo is not None and result.utcoffset() is not None, 'AWARE_SOURCE_CLOCK_REQUIRED')
    return result


def _fp(path, *, directory=False, link=False):
    value = Path(path).lstat()
    _need((stat.S_ISLNK(value.st_mode) if link else stat.S_ISDIR(value.st_mode)
           if directory else stat.S_ISREG(value.st_mode)), 'REGISTERED_PHYSICAL_FILE_TYPE')
    if not directory and not link:
        _need(not getattr(value, 'st_flags', 0) & 0x40000000 and
              (not value.st_size or value.st_blocks > 0), 'NONRESIDENT_ORIGINAL_FORBIDDEN')
    return {key: getattr(value, key, 0) for key in FP_KEYS}


def _listing(value):
    try:
        text = str(value)
        if re.fullmatch('[0-9]{8}', text, re.ASCII):
            text = text[:4] + '-' + text[4:6] + '-' + text[6:]
        return date.fromisoformat(text[:10])
    except (ValueError, TypeError):
        return None


@dataclass(frozen=True, init=False)
class RequestSpec:
    """Exact validated provider request; callers cannot mutate nested parameters."""
    endpoint: str
    _parameters_json: str
    business_session: str | None
    role: str
    current: bool

    def __init__(self, endpoint, parameters, business_session=None, role='SOURCE', current=False):
        params = validate_request(endpoint, parameters)
        if 'codes' in params:
            _need(all(isinstance(code, str) and CODE.fullmatch(code) for code in params['codes']),
                  'ASCII_REQUEST_CODES_REQUIRED')
        if business_session is not None:
            business_session = date.fromisoformat(str(business_session)).isoformat()
        _need(type(current) is bool and isinstance(role, str) and role, 'REQUEST_SCOPE_REQUIRED')
        _need(not current or business_session is not None, 'UNDATED_CURRENT_REQUEST_NEEDS_REAL_SESSION')
        object.__setattr__(self, 'endpoint', endpoint)
        object.__setattr__(self, '_parameters_json', _canonical(params).decode())
        object.__setattr__(self, 'business_session', business_session)
        object.__setattr__(self, 'role', role)
        object.__setattr__(self, 'current', current)

    @property
    def parameters(self):
        return json.loads(self._parameters_json)

    @property
    def key(self):
        # Role is descriptive, not a reason to download an identical request twice.
        return hashlib.sha256(_canonical(dict(version=VERSION, endpoint=self.endpoint,
            parameters=self.parameters, business_session=self.business_session,
            current=self.current))).hexdigest()

    def to_dict(self):
        return dict(endpoint=self.endpoint, parameters=self.parameters,
                    business_session=self.business_session, role=self.role, current=self.current)


def lhb_request_spec(price_sessions):
    """One bounded request covering the last five authenticated trade sessions.

    The caller supplies the actual calendar selection. This function does not
    infer trading sessions from weekdays or certify source completeness.
    """
    _need(isinstance(price_sessions, (list, tuple)) and len(price_sessions) >= 5,
          'LHB_FIVE_ORDERED_TRADE_SESSIONS_REQUIRED')
    sessions = tuple(str(day) for day in price_sessions)
    _need(all(date.fromisoformat(day).isoformat() == day for day in sessions)
          and all(left < right for left, right in zip(sessions, sessions[1:])),
          'LHB_FIVE_ORDERED_TRADE_SESSIONS_REQUIRED')
    return RequestSpec('lhb_summary', dict(start_date=sessions[-5], end_date=sessions[-1]),
                       sessions[-1], role='PRESCREEN_LHB_5_SESSIONS')


@dataclass(frozen=True)
class CaptureRecord:
    """Immutable JSON metadata, not a reader or a restored native capability."""
    _json: str

    @classmethod
    def from_metadata(cls, value):
        return cls(_canonical(value).decode())

    def to_dict(self):
        return json.loads(self._json)

    @property
    def metadata(self):
        return self.to_dict()

    @property
    def endpoint(self):
        return self.to_dict()['endpoint']

    @property
    def parameters(self):
        return self.to_dict()['parameters']

    @property
    def status(self):
        return self.to_dict()['status']

    @property
    def receipt_id(self):
        return self.to_dict()['receipt_id']

    @property
    def archive_root(self):
        return self.to_dict()['archive_root']


@dataclass(frozen=True)
class CaptureCollection:
    observation_date: str
    target_session: str
    sessions: tuple[str, ...]
    price_sessions: tuple[str, ...]
    core_records: tuple[CaptureRecord, ...]
    context_records: tuple[CaptureRecord, ...]
    raw_context_sources: tuple[dict, ...]
    failures: tuple[dict, ...]
    request_plan: tuple[dict, ...]
    identity_coverage: dict
    ready_for_core: bool
    calendar_rows: tuple[dict, ...] = ()
    coverage_type: str = 'RECONSTRUCTED_HISTORICAL'
    prescreen_records: tuple[CaptureRecord, ...] = ()

    @property
    def completeness(self):
        return 'COMPLETE' if self.ready_for_core and not self.failures else 'PARTIAL'

    @property
    def source_role_summary(self):
        return dict(observation_price_session=self.observation_date,
            current_snapshot_session=self.identity_coverage['current_snapshot_session'],
            core_ready=self.ready_for_core, basic_role='POSITIVE_IPO_OUTDATE_AND_OUTCOME_IDENTITY_ONLY',
            reference_membership_or_status_certified=False,
            basic_sources=[{k:r.to_dict()[k] for k in ('endpoint','receipt_id','first_seen_at','ingested_at')}
                for r in self.core_records if r.endpoint in ('stock_basic','stock_basic_all')],
            actual_source_clocks_rewritten=False, coverage_type=self.coverage_type)

    def to_dict(self):
        return self.metadata()

    def metadata(self):
        return dict(schema_version=COLLECTION_VERSION, observation_date=self.observation_date,
            target_session=self.target_session, sessions=list(self.sessions),
            price_sessions=list(self.price_sessions), core_records=[r.to_dict() for r in self.core_records],
            context_records=[r.to_dict() for r in self.context_records],
            prescreen_records=[r.to_dict() for r in self.prescreen_records],
            raw_context_sources=deepcopy(list(self.raw_context_sources)), failures=deepcopy(list(self.failures)),
            request_plan=deepcopy(list(self.request_plan)), identity_coverage=deepcopy(self.identity_coverage),
            ready_for_core=self.ready_for_core, completeness=self.completeness,
            calendar_rows=deepcopy(list(self.calendar_rows)), source_role_summary=self.source_role_summary,
            coverage_type=self.coverage_type,
            eligibility_inferred=False, source_capabilities_issued=0,
            normalizer_calls=(self.identity_coverage.get('factor_scope') or {}).get('normalizer_calls', 0),
            selector_calls=0)


class CaptureCache:
    """A crash-released single writer and append-only JSON reference index.

    budget.check(stage), budget.read(path, expected=None, limit=None)->(bytes,ref4),
    budget.preclaim_request(endpoint, params), budget.record_request(result) are
    supplied by the orchestrator. Native verify_capture reads are metered too.
    """
    def __init__(self, root, budget, config=None, executor=execute_request, *, recovery_context=None):
        self.root = Path(root).absolute()
        _need(not self.root.is_symlink(), 'CACHE_ROOT_SYMLINK_FORBIDDEN')
        self.root.mkdir(parents=True, exist_ok=True)
        self.root = self.root.resolve(strict=True)
        self.budget, self.config, self.executor = budget, _config(config), executor
        self.recovery_context = deepcopy(recovery_context)
        self._owner = (os.getpid(), threading.get_ident())
        self._known = {}
        self._factor_index_memo = {}
        self._attempted_keys = set()
        for name in ('index', 'raw', 'raw-index', 'locators'):
            path = self.root / name
            _need(not path.is_symlink(), 'CACHE_DIRECTORY_SYMLINK_FORBIDDEN')
            path.mkdir(exist_ok=True)
        limits = getattr(budget, 'limits', {})
        self.timeout = float(self.config.get('request_timeout_seconds', limits.get('request_timeout_seconds', 90)))
        self.max_http = self.config.get('max_http_requests_per_request',
            self.config.get('request_http_attempts', limits.get('request_http_attempts', 64)))
        self.max_entries = self.config.get('max_cache_entries', 8192)
        _need(0 < self.timeout <= min(90, limits.get('request_timeout_seconds', 90))
              and type(self.max_http) is int and 0 < self.max_http <= limits.get('request_http_attempts', 64),
              'BOUNDED_PROVIDER_POLICY_REQUIRED')
        _need(type(self.max_entries) is int and 0 < self.max_entries <= 65536, 'CACHE_INDEX_BOUND')

    def _check(self, stage):
        _need(self._owner == (os.getpid(), threading.get_ident()), 'CACHE_OWNER_PROCESS_THREAD_CHANGED')
        self.budget.check(stage)

    def _read(self, declaration, limit=None):
        self._check('CAPTURE_CACHE_READ')
        expected = declaration if isinstance(declaration, dict) else None
        path = Path(expected['path'] if expected else declaration).absolute()
        _fp(path)  # Reject a file symlink before resolving parent directory aliases.
        path = path.resolve(strict=True)
        if expected and 'fp' in expected:
            _need(expected['path'] == str(path), 'ORIGINAL_DECLARED_CANONICAL_PATH_CHANGED')
        before = _fp(path)
        raw, ref = self.budget.read(path, expected=expected.get('sha256') if expected else None,
                                    limit=limit or self.config.get('source_file_bytes', 16 * 1024**2))
        actual = dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw), fp=before)
        _need(before['st_size'] == len(raw) and content_reference_equal(ref, actual),
              'BUDGET_READ_CONTENT_REFERENCE_MISMATCH')
        if expected:
            for key in ('sha256', 'bytes'):
                if key in expected:
                    _need(expected[key] == actual[key], 'ORIGINAL_' + key.upper() + '_CHANGED')
        prior = self._known.get(str(path))
        _need(prior is None or content_reference_equal(prior, actual), 'ORIGINAL_7FP_OR_SHA_CHANGED')
        self._known[str(path)] = actual
        return raw, actual

    @contextmanager
    def _writer(self):
        from stockfish_core.jobs import _ServiceLock
        _need(not (self.root / 'writer.lock').is_symlink(), 'CACHE_LOCK_SYMLINK_FORBIDDEN')
        lock = _ServiceLock(self.root / 'writer.lock')
        try:
            self._check('CAPTURE_CACHE_WRITER')
            yield
        finally:
            lock.close()

    def _mkdir_owned(self, path):
        path = Path(path).absolute()
        _need(path.is_relative_to(self.root), 'ONLY_CACHE_OWNED_DIRECTORIES')
        current = self.root
        for part in path.relative_to(self.root).parts:
            current = current / part
            _need(not current.is_symlink(), 'CACHE_WRITE_PARENT_SYMLINK_FORBIDDEN')
            if current.exists():
                _need(current.is_dir(), 'CACHE_WRITE_PARENT_NOT_DIRECTORY')
            else:
                current.mkdir()

    def _save(self, path, value):
        self._check('CAPTURE_CACHE_WRITE')
        path = Path(path)
        _need(path.is_relative_to(self.root), 'ONLY_CACHE_OWNED_WRITES')
        self._mkdir_owned(path.parent)
        _need(not path.is_symlink(), 'CACHE_OUTPUT_FILE_SYMLINK_FORBIDDEN')
        raw = _canonical(value)
        _need(len(raw) <= self.config.get('index_file_bytes', 2 * 1024**2), 'CACHE_METADATA_SIZE_BOUND')
        with path.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        return self._read(path, limit=max(len(raw), 1))[1]

    def _entries(self, key=None, raw_only=False):
        folder = self.root / ('raw-index' if raw_only else 'index')
        paths = sorted((folder / key).glob('*.json')) if key else sorted(folder.glob('*/*.json'))
        _need(len(paths) <= self.max_entries, 'CACHE_ENTRY_COUNT_BOUND')
        for path in paths:
            raw, _ = self._read(path, limit=2 * 1024**2)
            value = _json(raw)
            _need(value['schema_version'] == VERSION and hashlib.sha256(raw).hexdigest() == path.stem,
                  'CACHE_INDEX_CONTENT_ADDRESS_CHANGED')
            _need(value['request_key'] == path.parent.name, 'CACHE_INDEX_REQUEST_PATH_CHANGED')
            yield value

    def _commit(self, value, raw_only=False):
        raw = _canonical(value)
        path = self.root / ('raw-index' if raw_only else 'index') / value['request_key'] / (hashlib.sha256(raw).hexdigest() + '.json')
        if path.exists():
            _need(self._read(path, limit=2 * 1024**2)[0] == raw, 'CACHE_INDEX_COLLISION')
        else:
            self._save(path, value)
        return CaptureRecord.from_metadata(value) if not raw_only else deepcopy(value)

    def _factor_ledger_entries(self):
        """Invocation-local index decoding; new entries are always discovered.

        Stat equality only avoids repeated reads of an already SHA-validated
        object within this cache instance. Changes cause a fresh content read,
        not a persistent device-identity gate. The ordinary terminal validator
        still hashes every source/index recorded in ``_known``.
        """
        self._check('CAPTURE_FACTOR_LEDGER')
        paths = sorted((self.root / 'index').glob('*/*.json'))
        _need(len(paths) <= self.max_entries, 'CACHE_ENTRY_COUNT_BOUND')
        current = set(paths)
        _need(set(self._factor_index_memo) <= current, 'CACHE_FACTOR_LEDGER_BOUND_ENTRY_REMOVED')
        values = []
        for path in paths:
            before = _fp(path)
            saved = self._factor_index_memo.get(path)
            if saved is None or saved['fp'] != before:
                raw, reference = self._read(path, limit=2 * 1024**2)
                value = _json(raw)
                _need(value['schema_version'] == VERSION and reference['sha256'] == path.stem,
                      'CACHE_INDEX_CONTENT_ADDRESS_CHANGED')
                _need(value['request_key'] == path.parent.name, 'CACHE_INDEX_REQUEST_PATH_CHANGED')
                _need(before == _fp(path), 'CACHE_FACTOR_LEDGER_CHANGED_DURING_READ')
                saved = dict(fp=before, value=value)
                self._factor_index_memo[path] = saved
            values.append(deepcopy(saved['value']))
        return values

    def _validate_locator(self, locator):
        if locator is None:
            return
        path = Path(locator['path'])
        _need((path.is_relative_to(self.root / 'locators') if locator['owned'] else
               path == Path(locator['archive_root']) / 'cache' / 'raw') and path.is_symlink()
              and os.readlink(path) == locator['target']
              and path.resolve(strict=True) == Path(locator['target']), 'ORIGINAL_LOCATOR_CHANGED')
        expected = {str(item) for item in (path.parent, path.parent.parent,
                    Path(locator['archive_root']), Path(locator['target']))}
        _need(set(locator['directories']) == expected and all(Path(directory).is_dir()
              for directory in locator['directories']), 'ORIGINAL_LOCATOR_DIRECTORY_CHANGED')

    @staticmethod
    def _directory_identity(path):
        value = Path(path).lstat()
        _need(stat.S_ISDIR(value.st_mode), 'REGISTERED_LOCATOR_DIRECTORY_REQUIRED')
        return {key: getattr(value, key) for key in ('st_dev','st_ino','st_mode')}

    def _locator(self, root, link, target):
        return dict(path=str(link), target=str(target), fp=_fp(link, link=True), archive_root=str(root),
            owned=link.is_relative_to(self.root / 'locators'),
            directories={str(item):self._directory_identity(item) for item in
                (link.parent, link.parent.parent, root, Path(target))})

    def _native_verify(self, archive_root, result, refs):
        # The mature function uses Path.read_text/read_bytes. Route those exact
        # original paths through the caller's reader; restore even on failure.
        allowed = {str(Path(ref['path']).resolve(strict=True)): ref for ref in refs}
        original_bytes, original_text = Path.read_bytes, Path.read_text
        reading = False
        def get(path):
            nonlocal reading
            resolved = str(Path(path).resolve(strict=True))
            _need(resolved in allowed, 'UNREGISTERED_MATURE_CAPTURE_READ')
            reading = True
            try:
                return self._read(allowed[resolved])[0]
            finally:
                reading = False
        def read_bytes(path):
            return original_bytes(path) if reading else get(path)
        def read_text(path, encoding=None, errors=None):
            if reading:
                return original_text(path, encoding=encoding, errors=errors)
            return io.TextIOWrapper(io.BytesIO(get(path)), encoding=encoding, errors=errors).read()
        with _VERIFY_LOCK:
            Path.read_bytes, Path.read_text = read_bytes, read_text
            try:
                authenticated = historical_replay.verify_capture(archive_root, result)
            finally:
                Path.read_bytes, Path.read_text = original_bytes, original_text
        return authenticated

    def _archive_root(self, requested, result, rawref, receiptref):
        rid = result['request_receipt_id']
        candidates = [Path(requested).resolve(strict=True)] if requested else []
        candidates.extend(Path(rawref['path']).parents)
        for root in candidates:
            fixed = root / 'cache' / 'raw' / (rid + '.receipt.json')
            located = fixed.is_file() and fixed.resolve() == Path(receiptref['path'])
            if not located and (root / 'raw').is_dir():
                located = any(p.resolve() == Path(receiptref['path']) for p in
                              (root / 'raw').glob('**/raw/' + rid + '.receipt.json'))
            if located:
                link = root / 'cache' / 'raw'
                locator = None
                if link.is_symlink():
                    locator = self._locator(root, link, link.resolve(strict=True))
                    self._validate_locator(locator)
                return str(root), locator
        # Legacy direct request/raw seeds need a locator, not rewritten source
        # files or fabricated result wrappers. Only this owned directory link
        # is new; verifier must resolve back to the original physical files.
        _need(Path(rawref['path']).parent == Path(receiptref['path']).parent, 'TRIO_PARENT_DIRECTORY_CONFLICT')
        root = self.root / 'locators' / rid
        link = root / 'cache' / 'raw'
        if not link.exists() and not link.is_symlink():
            self._mkdir_owned(link.parent)
            link.symlink_to(Path(rawref['path']).parent, target_is_directory=True)
        locator = self._locator(root, link, Path(rawref['path']).parent)
        self._validate_locator(locator)
        return str(root), locator

    def _producer_files(self, result, declared=()):
        expected = result.get('implementation_source_hashes')
        _need(isinstance(expected, dict) and set(expected) == set(PRODUCERS), 'ORIGINAL_PRODUCER_THREE_HASHES_REQUIRED')
        offered = {Path(r['path']).name: r for r in declared if isinstance(r, dict) and 'path' in r}
        records = []
        for name in PRODUCERS:
            path = Path(provider_requests.__file__).with_name(name)
            declaration = offered.get(name, dict(path=str(path), sha256=expected[name]))
            raw, ref = self._read(declaration)
            _need(hashlib.sha256(raw).hexdigest() == expected[name], 'ORIGINAL_CAPTURE_PRODUCER_CHANGED')
            records.append(ref)
        return records

    def _authenticate(self, trio, archive_root=None, producer_files=(), source=None):
        docs, refs = {}, {}
        for role in ('result', 'raw', 'receipt'):
            raw, refs[role] = self._read(trio[role])
            docs[role] = _json(raw)
        result, payload, receipt = (docs[k] for k in ('result', 'raw', 'receipt'))
        _need(result['provider'] == provider_requests.ENDPOINTS[result['endpoint']]['provider'] or
              self.config.get('allow_test_fixture', False) and result['provider'] == 'TEST_FIXTURE',
              'EXPLICIT_SUPPORTED_PROVIDER_OR_TEST_FIXTURE_REQUIRED')
        _need(result['rows'] == payload['rows'] and isinstance(result['rows'], list), 'WHOLE_RESULT_RAW_ROWS_DIFFER')
        _need(result['field_version'] == receipt['field_version'] and
              (payload.get('field_version') == result['field_version'] or
               'field_version' not in payload and result['status'] not in ('COMPLETE','PARTIAL')),
              'ORIGINAL_FIELD_VERSION_CHANGED')
        _need(receipt['content_hash'] == refs['raw']['sha256'], 'ORIGINAL_RAW_CONTENT_ADDRESS')
        rid = receipt['receipt_id']
        _need(hashlib.sha256(json.dumps({k:v for k,v in receipt.items() if k != 'receipt_id'}, sort_keys=True).encode()).hexdigest() == rid,
              'PHYSICAL_RECEIPT_PREIMAGE_CHANGED')
        _need(_stamp(result['started_at']) <= _stamp(receipt['first_seen_at']) <=
              _stamp(receipt['ingested_at']) <= _stamp(result['completed_at']), 'ORIGINAL_CAPTURE_CLOCK_ORDER')
        raw_clock_known = payload.get('first_seen_at') is not None
        _need((raw_clock_known and _stamp(payload['first_seen_at']) == _stamp(receipt['first_seen_at']))
              or not raw_clock_known and result['status'] not in ('COMPLETE', 'PARTIAL'),
              'ORIGINAL_RAW_FIRST_SEEN_REQUIRED_NOT_BACKFILLED')
        _need(receipt['integrity_scope'] == 'LOCAL_SHA256_NOT_SECURITY_AUTHENTICATION', 'ORIGINAL_INTEGRITY_SCOPE')
        if result['status'] == 'COMPLETE' and result['endpoint'] in ('market_daily', 'master_all', 'mainboard_sse', 'master_szse', 'stock_basic_all'):
            _need(bool(result['rows']), 'COMPLETE_CORE_SCOPE_CANNOT_BE_EMPTY')
        _need(receipt['row_count'] is None or type(receipt['row_count']) is int and receipt['row_count'] == len(result['rows']),
              'ORIGINAL_PHYSICAL_ROW_COUNT_CONFLICT')
        _identity_rows(result['endpoint'], result['rows'], result['parameters'])
        factor = factor_contract(result) if result['endpoint'] == 'adjustment_factors' else None
        producer = self._producer_files(result, producer_files)
        root, locator = self._archive_root(archive_root, result, refs['raw'], refs['receipt'])
        auth = self._native_verify(root, result, (refs['raw'], refs['receipt']))
        _need(auth['raw_path'] == refs['raw']['path'] and auth['receipt_path'] == refs['receipt']['path']
              and auth['raw_file_sha256'] == refs['raw']['sha256']
              and auth['receipt_file_sha256'] == refs['receipt']['sha256'], 'NATIVE_VERIFIER_MUST_RETURN_ORIGINAL_FILES')
        return result, dict(archive_root=root, **refs, producer_files=producer, verification_locator=locator,
            receipt_id=rid, endpoint=result['endpoint'], parameters=deepcopy(result['parameters']), status=result['status'],
            pagination_complete=result['pagination_complete'], rows=len(result['rows']),
            physical_row_count=receipt['row_count'], first_seen_at=receipt['first_seen_at'],
            ingested_at=receipt['ingested_at'], started_at=result['started_at'], completed_at=result['completed_at'],
            factor_envelope=factor, raw_first_seen_present=raw_clock_known,
            raw_field_version_present='field_version' in payload,
            native_original_verification=auth, source_provenance=deepcopy(source),
            integrity_scope='LOCAL_SHA256_NOT_SECURITY_AUTHENTICATION', source_capability_issued=False)

    def verify(self, record):
        value = record.to_dict() if isinstance(record, CaptureRecord) else deepcopy(record)
        self._validate_locator(value.get('verification_locator'))
        result, current = self._authenticate(value, value['archive_root'], value['producer_files'], value.get('source_provenance'))
        spec = RequestSpec(**value['request_spec'])
        _need(spec.key == value['request_key'] and spec.endpoint == result['endpoint']
              and spec.parameters == result['parameters'], 'CACHE_REQUEST_DOES_NOT_MATCH_ORIGINAL_RESULT')
        if spec.current:
            _need(spec.business_session == _stamp(current['first_seen_at']).astimezone(SH).date().isoformat(),
                  'CURRENT_CACHE_SOURCE_ACTUAL_FIRST_SEEN_SESSION_CHANGED')
        for key in current:
            if key == 'verification_locator':
                # Locator stat fields describe the historical filesystem only.
                continue
            _need(content_equal(current[key], value[key]), 'CACHED_CAPTURE_METADATA_CHANGED:' + key)
        self._validate_locator(value.get('verification_locator'))
        return result

    def metadata(self, record):
        return record.to_dict() if isinstance(record, CaptureRecord) else deepcopy(record)

    def lookup(self, spec):
        self._check('CAPTURE_CACHE_LOOKUP')
        values = list(self._entries(spec.key))
        for value in values:
            _need(RequestSpec(**value['request_spec']).key == spec.key, 'CACHE_REQUEST_SPEC_CHANGED')
            self.verify(value)
        if not values:
            attempts = self.root / 'raw' / spec.key
            _need(not attempts.exists() or not any(attempts.iterdir()), 'UNFINISHED_CAPTURE_NO_IMPLICIT_RETRY')
            return None
        latest = max(_stamp(v['completed_at']) for v in values)
        tied = [v for v in values if _stamp(v['completed_at']) == latest]
        _need(len({v['receipt_id'] for v in tied}) == 1, 'SAME_CLOCK_CAPTURE_CONFLICT')
        return CaptureRecord.from_metadata(tied[0])

    def lhb_overlap_blockers(self, spec, *, entries=None):
        """Retain overlapping LHB failures even when a new range changes key.

        This is an invocation-local inspection of the existing cache and
        claims, not a new retry ledger. Exact-key handling remains unchanged.
        """
        if spec.endpoint != 'lhb_summary':
            return []
        values = list(self._entries()) if entries is None else entries
        by_key, blocked = {}, {}
        start, end = spec.parameters['start_date'], spec.parameters['end_date']

        def overlaps(prior):
            return (prior.endpoint == 'lhb_summary' and prior.key != spec.key
                    and prior.parameters['start_date'] <= end
                    and start <= prior.parameters['end_date'])

        for value in values:
            prior = RequestSpec(**value['request_spec'])
            _need(prior.key == value['request_key'], 'CACHE_LHB_PRIOR_REQUEST_IDENTITY_CHANGED')
            by_key.setdefault(prior.key, []).append(value)
            if overlaps(prior) and (value['status'] != 'COMPLETE' or not value['pagination_complete']):
                blocked[prior.key] = dict(request_key=prior.key, request_spec=prior.to_dict(),
                                         status='OVERLAPPING_INCOMPLETE_NO_IMPLICIT_RETRY')
        folders = sorted((self.root / 'raw').iterdir())
        _need(len(folders) <= self.max_entries, 'CACHE_LHB_ATTEMPT_COUNT_BOUND')
        claim_count = 0
        for folder in folders:
            _need(folder.is_dir() and not folder.is_symlink(), 'CACHE_LHB_ATTEMPT_DIRECTORY_REQUIRED')
            if folder.name == spec.key or folder.name in blocked:
                continue
            indexed = by_key.get(folder.name, ())
            if indexed and not any(overlaps(RequestSpec(**value['request_spec'])) for value in indexed):
                continue
            attempts_root = folder / 'raw'
            _need(not attempts_root.is_symlink(), 'CACHE_LHB_ATTEMPT_DIRECTORY_REQUIRED')
            attempts = sorted(attempts_root.iterdir()) if attempts_root.is_dir() else []
            claim_count += len(attempts)
            _need(claim_count <= self.max_entries, 'CACHE_LHB_ATTEMPT_COUNT_BOUND')
            if not attempts and not indexed and any(folder.iterdir()):
                inherited = self.inherited_attempt_scope(folder, entries=values)
                if inherited and inherited['status'] == 'VERIFIED_COMPLETE_ORIGINAL':
                    continue
                blocked[folder.name] = dict(request_key=folder.name, request_spec=None,
                    status='UNKNOWN_PRIOR_REQUEST_SCOPE_NO_IMPLICIT_RETRY',
                    inherited_scope=inherited)
            for attempt in attempts:
                _need(attempt.is_dir() and not attempt.is_symlink(), 'CACHE_LHB_ATTEMPT_DIRECTORY_REQUIRED')
                committed = any(value.get('capture_attempt_dir') == str(attempt)
                                and value['status'] == 'COMPLETE' and value['pagination_complete']
                                for value in indexed)
                failed = attempt / 'attempt-failed.json'
                if committed and not failed.exists():
                    # Older successful originals need no newly introduced
                    # claim artifact to retain their existing authority.
                    continue
                if failed.exists():
                    self._read(failed, limit=2 * 1024**2)
                claim = attempt / 'request-claim.json'
                if not claim.exists():
                    prior_spec = indexed[0]['request_spec'] if indexed else None
                    blocked[folder.name] = dict(request_key=folder.name, request_spec=prior_spec,
                        status='OVERLAPPING_UNFINISHED_NO_IMPLICIT_RETRY' if prior_spec else
                               'UNKNOWN_PRIOR_REQUEST_SCOPE_NO_IMPLICIT_RETRY')
                    continue
                raw, _ = self._read(claim, limit=2 * 1024**2)
                declared = _json(raw)
                prior = RequestSpec(**declared['request_spec'])
                _need(declared['schema_version'] == VERSION and prior.key == folder.name,
                      'CACHE_LHB_PRIOR_CLAIM_IDENTITY_CHANGED')
                if overlaps(prior) and (not committed or failed.exists()):
                    blocked[prior.key] = dict(request_key=prior.key, request_spec=prior.to_dict(),
                        status='OVERLAPPING_UNFINISHED_NO_IMPLICIT_RETRY')
        return [blocked[key] for key in sorted(blocked)]

    def inherited_attempt_scope(self, folder, *, entries=None):
        from .collection_inherited_scope import inherited_attempt_scope
        return inherited_attempt_scope(self, folder, entries=entries)

    def factor_overlap_blockers(self, spec, *, entries=None):
        from .collection_factor_scope import factor_overlap_blockers
        return factor_overlap_blockers(self, spec, entries=entries)

    def explicit_recovered_attempt(self, attempt, prior, *, entries=None):
        from .collection_explicit_recovery import explicit_recovered_attempt
        return explicit_recovered_attempt(self, attempt, prior, entries=entries)

    def capture_one(self, spec):
        with self._writer():
            blockers = self.lhb_overlap_blockers(spec)
            _need(not blockers, 'LHB_OVERLAPPING_FAILURE_NO_IMPLICIT_RETRY:' +
                  ','.join(row['request_key'] for row in blockers))
            try:
                cached = self.lookup(spec)
            except ValueError as error:
                if str(error) != 'UNFINISHED_CAPTURE_NO_IMPLICIT_RETRY' or not self.config.get('retry_failed', False):
                    raise
                cached = None
            if cached is not None and (cached.status == 'COMPLETE' or
                    not self.config.get('retry_failed', False) or spec.key in self._attempted_keys):
                return cached
            blockers = self.factor_overlap_blockers(spec)
            _need(not blockers, 'FACTOR_OVERLAPPING_FAILURE_NO_IMPLICIT_RETRY:' +
                  ','.join(row['request_key'] for row in blockers))
            _need(not self.config.get('offline', False), 'OFFLINE_SOURCE_MISSING_OR_FAILED_NO_NETWORK')
            _need(spec.key not in self._attempted_keys, 'ONE_ATTEMPT_PER_REQUEST_PER_INVOCATION')
            self.budget.preclaim_request(spec.endpoint, spec.parameters)
            self._attempted_keys.add(spec.key)
            # A request-local native archive avoids globbing the whole cache on
            # each mature verification: BASE/raw/<attempt>/raw is native grammar.
            request_archive = self.root / 'raw' / spec.key
            output = request_archive / 'raw' / uuid.uuid4().hex
            self._mkdir_owned(output)
            self._mkdir_owned(self.root / 'index' / spec.key)
            self._save(output / 'request-claim.json', dict(schema_version=VERSION,
                request_spec=spec.to_dict(), actual_started_at=datetime.now(timezone.utc).isoformat(), retries=0))
            try:
                kwargs = dict(timeout=self.timeout, max_http_requests=self.max_http)
                for key in ('budget_path', 'budget_id'):
                    if key in self.config:
                        kwargs[key] = self.config[key]
                result = self.executor(spec.endpoint, spec.parameters, output, **kwargs)
                self.budget.record_request(result)
                rid = result['request_receipt_id']
                receipt = output / 'raw' / (rid + '.receipt.json')
                physical, _ = self._read(receipt)
                raw = output / 'raw' / (_json(physical)['content_hash'] + '.raw.json')
                candidates = list(output.glob('request-*.json'))
                candidates = [p for p in candidates if p.name != 'request-claim.json']
                _need(len(candidates) == 1, 'EXACTLY_ONE_REAL_RESULT_WRAPPER_REQUIRED')
                loaded, _ = self._read(candidates[0])
                _need(_json(loaded) == result and result['endpoint'] == spec.endpoint and result['parameters'] == spec.parameters,
                      'ACTUAL_EXECUTOR_RESULT_SCOPE_CHANGED')
                _, value = self._authenticate(dict(raw=str(raw), receipt=str(receipt), result=str(candidates[0])), str(request_archive))
                value.update(schema_version=VERSION, request_key=spec.key, request_spec=spec.to_dict(),
                             origin='ACTUAL_PROVIDER_CAPTURE', capture_attempt_dir=str(output))
                record = self._commit(value)
                self._save(output / 'committed.json', dict(request_key=spec.key, receipt_id=record.receipt_id))
                return record
            except BaseException as exc:
                self._save(output / 'attempt-failed.json', dict(schema_version=VERSION, status='FAILED_RETAINED_NO_RETRY',
                    error_type=type(exc).__name__[:128], reason=str(exc)[:2048], source_completion='UNKNOWN'))
                raise

    def import_seed(self, path, expected_sha256=None):
        """Authenticate real trios / RAW recipes. Never manufacture a result."""
        with self._writer():
            raw, seedref = self._read(dict(path=str(Path(path).resolve(strict=True)), sha256=expected_sha256)
                                      if expected_sha256 else Path(path).resolve(strict=True), limit=16 * 1024**2)
            seed = _json(raw)
            sources = seed.get('sources', seed.get('captures', seed.get('trios', [])))
            if not sources and isinstance(seed.get('records'), list):
                sources = [dict(r['trio'], archive_root=r.get('archive_root')) for r in seed['records'] if isinstance(r.get('trio'), dict)]
            _need(isinstance(sources, list) and isinstance(seed.get('raw_receipt_sources', []), list), 'SEED_SOURCE_LIST_REQUIRED')
            count = 0
            for item in sources:
                result, value = self._authenticate(item, item.get('archive_root'), item.get('producer_files', ()),
                    dict(seed=seedref, original_control_completion_not_restored=True))
                params = result['parameters']
                session = params.get('date') or params.get('end_date') or _stamp(value['first_seen_at']).astimezone(SH).date().isoformat()
                current = not bool(params.get('date') or params.get('end_date'))
                spec = RequestSpec(result['endpoint'], params, session, role='IMPORTED_ORIGINAL', current=current)
                value.update(schema_version=VERSION, request_key=spec.key, request_spec=spec.to_dict(), origin='AUTHENTICATED_SEED_TRIO')
                self._commit(value); count += 1
            recipes = 0
            for item in seed.get('raw_receipt_sources', []):
                value = self._authenticate_recipe(item)
                value.update(schema_version=VERSION, source_provenance=dict(seed=seedref), raw_only=True)
                spec = RequestSpec(value['endpoint'], value['parameters'], value['parameters'].get('date'), role='CONTEXT_RAW_ONLY')
                value.update(request_key=spec.key, request_spec=spec.to_dict())
                self._commit(value, raw_only=True); recipes += 1
            return dict(status='IMPORTED_ORIGINAL_REFERENCES_NO_CAPABILITY', seed=seedref,
                        captures=count, raw_recipes=recipes, source_parent_completion_not_restored=True)

    def _authenticate_recipe(self, item):
        raw, rawref = self._read(item['raw']); payload = _json(raw)
        data, receiptref = self._read(item['receipt']); receipt = _json(data)
        typed = deepcopy(item['typed_receipt'])
        from .contracts import SourceReceipt
        parent = SourceReceipt.model_validate(typed)
        _need(receipt['content_hash'] == rawref['sha256'] == parent.content_hash and
              parent.receipt_id == receipt['receipt_id'] and parent.dataset == receipt['dataset']
              and parent.parameters == receipt['parameters'] and parent.provider == receipt['provider']
              and parent.field_version == receipt['field_version'] == payload['field_version'], 'RAW_RECIPE_PHYSICAL_TYPED_BINDING')
        _need(hashlib.sha256(json.dumps({k:v for k,v in receipt.items() if k != 'receipt_id'}, sort_keys=True).encode()).hexdigest() == parent.receipt_id,
              'RAW_RECIPE_RECEIPT_PREIMAGE')
        _need(payload['rows'] is not None and isinstance(payload['rows'], list) and parent.row_count == len(payload['rows'])
              and (receipt['row_count'] is None or receipt['row_count'] == len(payload['rows']))
              and parent.status == receipt['status'] == payload['status']
              and parent.pagination_complete == receipt['pagination_complete'] == payload['pagination_complete'], 'RAW_RECIPE_STATUS_ROWS_NOT_UPGRADED')
        _need(payload.get('first_seen_at') is not None and _stamp(payload['first_seen_at']) == parent.first_seen_at == _stamp(receipt['first_seen_at'])
              and parent.ingested_at == _stamp(receipt['ingested_at'])
              and parent.source_published_at == parent.first_seen_at, 'RAW_RECIPE_ORIGINAL_CLOCKS')
        session = parent.parameters.get('date') or parent.parameters.get('end_date')
        event = datetime.combine(date.fromisoformat(session), time(15), SH) if session else parent.first_seen_at
        _need(parent.event_time == event, 'RAW_RECIPE_BUSINESS_CLOCK_NOT_REWRITTEN')
        _identity_rows(parent.dataset, payload['rows'], parent.parameters)
        return dict(raw=rawref, receipt=receiptref, typed_receipt=typed, endpoint=parent.dataset,
                    parameters=deepcopy(receipt['parameters']), status=parent.status, receipt_id=parent.receipt_id)

    def context_recipe(self, endpoint, session):
        spec = RequestSpec(endpoint, {'date': str(session)}, str(session))
        values = list(self._entries(spec.key, raw_only=True))
        for value in values:
            current = self._authenticate_recipe(value)
            _need(all(current[k] == value[k] for k in current), 'RAW_ONLY_CACHE_CHANGED')
        if not values:
            return None
        latest = max(values, key=lambda v: _stamp(v['typed_receipt']['ingested_at']))
        return deepcopy(latest)

    def prior_basic_references(self, cutoff):
        values = []
        for value in self._entries():
            if value['endpoint'] in ('stock_basic', 'stock_basic_all') and value['status'] == 'COMPLETE' and value['pagination_complete']:
                self.verify(value)
                if _stamp(value['completed_at']) <= cutoff:
                    values.append(CaptureRecord.from_metadata(value))
        return sorted(values, key=lambda r: _stamp(r.to_dict()['completed_at']))

    def calendar_reference(self, cutoff):
        natural = cutoff.astimezone(SH).date()
        candidates = []
        for value in self._entries():
            if value['endpoint'] != 'calendar' or value['status'] != 'COMPLETE' or not value['pagination_complete']:
                continue
            params = value['parameters']
            if not (params['start_date'] <= str(natural) < params['end_date'] and _stamp(value['completed_at']) <= cutoff):
                continue
            result = self.verify(value)
            days = [date.fromisoformat(r['calendar_date']) for r in result['rows'] if r.get('is_trading_day') == '1']
            completed = [d for d in days if datetime.combine(d, time(18), SH) <= cutoff]
            if completed and len([d for d in days if d <= max(completed)]) >= 67 and any(d > max(completed) for d in days):
                candidates.append(value)
        if not candidates:
            return None
        return CaptureRecord.from_metadata(max(candidates, key=lambda v:_stamp(v['completed_at'])))

    def terminal(self):
        """Finite original registry snapshot, including native-verifier reads."""
        for ref in tuple(self._known.values()):
            self._read(ref, limit=max(ref['bytes'], 1))
        return list(deepcopy(self._known).values())


def _identity_rows(endpoint, rows, params):
    member = endpoint in ('industry_members_sina', 'concept_members_sina')
    field = {'mainboard_sse':'证券代码', 'master_szse':'A股代码', 'delisting_sse':'INSTRUMENT_ID'}.get(endpoint, 'code')
    code_endpoints = {'mainboard_sse', 'master_szse', 'delisting_sse', 'market_daily', 'master_all',
                      'stock_basic', 'stock_basic_all', 'adjustment_factors', 'industry_members_sina', 'concept_members_sina'}
    if endpoint not in code_endpoints:
        return
    seen = set()
    for row in rows:
        code = row.get(field, row.get('代码')) if member else row.get(field)
        _need(isinstance(code, str) and (re.fullmatch('[0-9]{6}', code, re.ASCII)
              if field != 'code' or member else CODE.fullmatch(code)), 'ALL_ORIGINAL_ROWS_REQUIRE_ASCII_IDENTITY')
        if endpoint != 'adjustment_factors':
            _need(code not in seen, 'DUPLICATE_ORIGINAL_SECURITY_IDENTITY')
        seen.add(code)
        if endpoint == 'market_daily':
            _need(row.get('date') == params['date'] and str(row.get('adjustflag')) == '3', 'RAW_BUSINESS_DATE_OR_ADJUSTMENT_CHANGED')
        if endpoint in ('stock_basic', 'adjustment_factors'):
            _need(code in params['codes'], 'UNREQUESTED_SECURITY_IN_ORIGINAL_RESPONSE')


def factor_contract(result):
    """Envelope only: no factor arithmetic, selection, or status promotion."""
    requested = result['parameters']['codes']
    _need(len(requested) == len(set(requested)) and all(CODE.fullmatch(c) for c in requested), 'EXACT_FACTOR_REQUEST_CODES')
    if result['status'] not in ('COMPLETE', 'PARTIAL'):
        _need(not result['rows'], 'FAILED_FACTOR_WITH_ROWS_REQUIRES_EXPLICIT_PARTIAL_CONTRACT')
        return dict(requested_codes=list(requested), independent_complete_codes=[],
                    parent_status=result['status'], eligibility_inferred=False)
    coverage = result.get('coverage', {})
    _need(coverage.get('requested_codes') == requested, 'FACTOR_REQUESTED_COVERAGE_DIFFERS')
    statuses = coverage.get('security_statuses')
    _need(isinstance(statuses, list) and len(statuses) == len(requested), 'ONE_FACTOR_STATUS_PER_REQUESTED_SECURITY')
    counts = {}
    for row in result['rows']:
        code = row.get('code')
        _need(code in requested and isinstance(code, str) and CODE.fullmatch(code), 'FACTOR_ROW_OUTSIDE_REQUEST')
        counts[code] = counts.get(code, 0) + 1
    seen, complete = set(), []
    for status in statuses:
        code = status.get('code')
        _need(code in requested and code not in seen and status.get('status') in
              ('COMPLETE', 'FETCH_FAILED', 'PARTIAL', 'UNKNOWN', 'INVALID', 'NOT_PUBLISHED'), 'FACTOR_STATUS_IDENTITY_OR_STATUS')
        seen.add(code)
        if status['status'] == 'COMPLETE':
            _need(status.get('cursor_exhausted') is True and type(status.get('row_count')) is int
                  and status['row_count'] == counts.get(code, 0), 'FACTOR_COMPLETE_FRAGMENT_NOT_EXHAUSTED')
            complete.append(code)
    if result['status'] == 'COMPLETE':
        _need(result['pagination_complete'] is True and len(complete) == len(requested), 'COMPLETE_FACTOR_PARENT_HAS_INCOMPLETE_SECURITY')
    return dict(requested_codes=list(requested), independent_complete_codes=complete,
                parent_status=result['status'], eligibility_inferred=False)


def _collection_policy(cache, value):
    # A function-level option must not silently change a cache's registered
    # transport/offline policy. Configure these at CaptureCache construction.
    value = _config(value)
    actual = dict(offline=cache.config.get('offline',False), retry_failed=cache.config.get('retry_failed',False),
        request_timeout_seconds=cache.timeout, max_http_requests_per_request=cache.max_http,
        request_http_attempts=cache.max_http, max_cache_entries=cache.max_entries,
        source_file_bytes=cache.config.get('source_file_bytes',16*1024**2),
        index_file_bytes=cache.config.get('index_file_bytes',2*1024**2),
        allow_test_fixture=cache.config.get('allow_test_fixture',False),
        budget_path=cache.config.get('budget_path'), budget_id=cache.config.get('budget_id'))
    for key in set(value) & set(actual):
        _need(value[key] == actual[key], 'CACHE_CONSTRUCTION_POLICY_MISMATCH:'+key)
    return _config(dict(cache.config, **value))


def capture_collect(cache, *, now, config=None):
    """Collect source facts for one real latest close, never a recommendation.

    Prior positive listing references are allowed only after full RAW identity
    coverage is checked. A registered stock_basic_all failure is retained; a
    separately declared stock_basic fallback requests only unresolved IDs.
    """
    policy = _collection_policy(cache, config)
    instant = _stamp(now)
    local = instant.astimezone(SH)
    lookback = int(policy.get('calendar_lookback_days', 800))
    lookahead = int(policy.get('calendar_lookahead_days', 32))
    _need(90 <= lookback <= 1500 and 1 <= lookahead <= 366, 'FINITE_CALENDAR_REQUEST_WINDOW')
    plans, core, context, recipes, failures = [], [], [], [], []
    def take(spec):
        plans.append(spec.to_dict())
        record = cache.capture_one(spec)
        if record.status != 'COMPLETE' or not record.to_dict()['pagination_complete']:
            failures.append(dict(request_spec=spec.to_dict(), original_capture=record.to_dict(), status=record.status))
        return record
    calendar = cache.calendar_reference(instant)
    if calendar is None:
        calendar = take(RequestSpec('calendar', dict(start_date=str(local.date()-timedelta(days=lookback)),
            end_date=str(local.date()+timedelta(days=lookahead))), str(local.date()), role='CALENDAR'))
    else:
        plans.append(calendar.to_dict()['request_spec'])
    result = cache.verify(calendar)
    _need(result['status'] == 'COMPLETE' and result['pagination_complete'], 'COMPLETE_REAL_CALENDAR_REQUIRED')
    start, end = (date.fromisoformat(result['parameters'][k]) for k in ('start_date', 'end_date'))
    flags = {}
    for row in result['rows']:
        day = date.fromisoformat(row['calendar_date'])
        _need(day not in flags and row['is_trading_day'] in ('0', '1'), 'CALENDAR_DUPLICATE_OR_UNKNOWN_DAY')
        flags[day] = row['is_trading_day']
    _need(set(flags) == {start+timedelta(days=n) for n in range((end-start).days+1)}, 'CALENDAR_NATURAL_DAY_GAP')
    sessions = sorted(day for day, flag in flags.items() if flag == '1')
    completed = [day for day in sessions if datetime.combine(day, time(18), SH) <= instant]
    _need(completed, 'NO_COMPLETED_EXCHANGE_SESSION')
    observed = completed[-1]
    following = [day for day in sessions if day > observed]
    _need(following and len([d for d in sessions if d <= observed]) >= 67, 'CALENDAR_NEXT_OR_LAST67_UNAVAILABLE')
    target = following[0]
    historical = [day for day in sessions if day <= observed]
    price = historical[-25:]
    core.append(calendar)
    official_specs = [RequestSpec(ep, {}, str(local.date()), role='CURRENT_OFFICIAL', current=True)
                      for ep in ('mainboard_sse', 'master_szse', 'delisting_sse')]
    market_specs = [RequestSpec(endpoint, {'date':str(day)}, str(day), role='FULL_MARKET_CONTEXT')
                   for day in price for endpoint in ('market_daily','master_all')]
    # Only the original core's 25-session market window is a network dependency.
    # The additional 42 sessions are optional report context, read from existing
    # originals only after the collection screen has produced its targets.
    _preflight_requests(cache, official_specs + market_specs)
    official = [take(spec) for spec in official_specs]
    core.extend(official)
    raw_union, current_master = set(), None
    for day in price:
        for endpoint in ('market_daily', 'master_all'):
            spec = RequestSpec(endpoint, {'date':str(day)}, str(day), role='FULL_MARKET_CONTEXT')
            record = take(spec)
            context.append(record)
            if day in price:
                core.append(record)
                captured = cache.verify(record)
                if endpoint == 'market_daily':
                    raw_union.update(r['code'] for r in captured['rows'])
            if endpoint == 'master_all' and day == observed:
                current_master = record
    refs = cache.prior_basic_references(instant)
    basic, basic_sources = {}, []
    for reference in refs:
        result = cache.verify(reference)
        for row in result['rows']:
            basic[row['code']] = row
        basic_sources.append(reference)
    roster, mainboard = {}, set()
    for record in official[:2]:
        result = cache.verify(record)
        if result['status'] != 'COMPLETE' or not result['pagination_complete']:
            continue
        for row in result['rows']:
            is_sse = record.endpoint == 'mainboard_sse'
            code = ('sh.'+row['证券代码']) if is_sse else ('sz.'+row['A股代码'])
            _need(code not in roster, 'CONFLICTING_CURRENT_OFFICIAL_IDENTITY')
            roster[code] = row
            if is_sse or row.get('板块') == '主板':
                mainboard.add(code)
    def listing(code):
        row = roster.get(code, {})
        return _listing(row.get('上市日期') if code.startswith('sh.') else row.get('A股上市日期')) or _listing(basic.get(code, {}).get('ipoDate'))
    missing = sorted(code for code in raw_union if listing(code) is None)
    if missing and policy.get('basic_all_first', True):
        spec = RequestSpec('stock_basic_all', {}, str(local.date()), role='CURRENT_BASIC_ATTEMPT', current=True)
        _preflight_requests(cache, [spec])
        full = take(spec)
        if full.status == 'COMPLETE' and full.to_dict()['pagination_complete']:
            basic_sources.append(full)
            for row in cache.verify(full)['rows']:
                basic[row['code']] = row
        missing = sorted(code for code in raw_union if listing(code) is None)
    _need(type(policy.get('basic_batch_fallback', True)) is bool, 'EXPLICIT_BASIC_FALLBACK_POLICY')
    batch_size = int(policy.get('batch_size', 50))
    _need(1 <= batch_size <= 100, 'PROVIDER_BATCH_SIZE_BOUND')
    if missing and policy.get('basic_batch_fallback', True):
        specs = [RequestSpec('stock_basic', {'codes':missing[offset:offset+batch_size]}, str(local.date()),
                role='REGISTERED_BASIC_FALLBACK', current=True) for offset in range(0,len(missing),batch_size)]
        _preflight_requests(cache, specs)
        for spec in specs:
            record = take(spec)
            if record.status == 'COMPLETE' and record.to_dict()['pagination_complete']:
                basic_sources.append(record)
                for row in cache.verify(record)['rows']:
                    basic[row['code']] = row
    core.extend(basic_sources)
    missing = sorted(code for code in raw_union if listing(code) is None)
    future = sorted(code for code in raw_union if listing(code) is not None and listing(code) > observed)
    outdate_conflict = sorted(code for code in raw_union & set(roster) if
        _listing(basic.get(code, {}).get('outDate')) is not None and _listing(basic[code]['outDate']) <= observed)
    # Supplemental LHB evidence never enters the core's price source graph.
    prescreen_specs = [lhb_request_spec(tuple(map(str, price)))]
    _preflight_requests(cache, prescreen_specs)
    prescreen = [take(spec) for spec in prescreen_specs]
    factor_codes = []
    if current_master is not None:
        result = cache.verify(current_master)
        factor_codes = sorted({r['code'] for r in result['rows'] if r['code'] in mainboard and ORDINARY.fullmatch(r['code'])})
    from .collection_qualification import factor_query_scope
    from .collection_factor_scope import plan_factor_requests
    qualification_records = {r.receipt_id:r for r in core if r.endpoint not in ('market_daily', 'master_all')
                             or r.parameters['date'] == str(observed)}
    factor_scope = factor_query_scope(
        [cache.verify(record) for record in qualification_records.values()],
        requested_codes=factor_codes, observation_date=str(observed),
        cutoff=datetime.now(timezone.utc), sessions=tuple(map(str, historical)),
        coverage_type='TEST_FIXTURE' if cache.config.get('allow_test_fixture') else 'RECONSTRUCTED_HISTORICAL')
    factor_codes = factor_scope['factor_query_codes']
    factor_specs = plan_factor_requests(cache, codes=factor_codes, observed=str(observed), batch_size=batch_size)
    _preflight_requests(cache, factor_specs)
    core.extend(take(spec) for spec in factor_specs)
    # Membership in this request scope is not strategy eligibility. PARTIAL
    # factors remain available to the native normalizer on its own terms.
    required = [r for r in core if r.endpoint in ('calendar','mainboard_sse','master_szse','delisting_sse','market_daily','master_all')]
    ready = all(r.status == 'COMPLETE' and r.to_dict()['pagination_complete'] for r in required) and not (missing or future or outdate_conflict)
    cache.terminal()
    unique = {r.receipt_id:r for r in core}
    return CaptureCollection(str(observed), str(target), tuple(map(str, historical)), tuple(map(str, price)),
        tuple(unique.values()), tuple(context), tuple(recipes), tuple(failures), tuple(plans),
        dict(RAW_union_count=len(raw_union), missing_positive_listing_codes=missing,
             future_listing_codes=future, current_roster_positive_outDate_conflicts=outdate_conflict,
             factor_query_codes=factor_codes, factor_scope=factor_scope, query_scope_not_eligibility=True,
             core_market_sessions=len(price), optional_market_sessions_not_requested=42,
             prior_basic_reference_clocks_preserved=True, current_snapshot_session=str(local.date())), ready,
        tuple(deepcopy(result_row) for result_row in cache.verify(calendar)['rows']),
        prescreen_records=tuple(prescreen))


@dataclass(frozen=True)
class ThemeSourceCollection:
    context_captures: tuple[dict, ...]
    raw_receipt_sources: tuple[dict, ...]
    requested_industry_nodes: tuple[str, ...]
    requested_concept_symbols: tuple[str, ...]
    parent_acquisition: dict

    def to_dict(self):
        return dict(schema_version='PREMARKET_THEME_SOURCE_COLLECTION_V1',
            context_capture_count=len(self.context_captures), raw_receipt_source_count=len(self.raw_receipt_sources),
            context_captures=deepcopy(list(self.context_captures)),
            raw_receipt_sources=deepcopy(list(self.raw_receipt_sources)),
            requested_industry_nodes=list(self.requested_industry_nodes),
            requested_concept_symbols=list(self.requested_concept_symbols),
            parent_acquisition=deepcopy(self.parent_acquisition), source_capabilities_issued=0)


def _lookup_for_policy(cache, spec):
    try:
        return cache.lookup(spec)
    except ValueError as error:
        if str(error) != 'UNFINISHED_CAPTURE_NO_IMPLICIT_RETRY' or not cache.config.get('retry_failed', False):
            raise
        return None


def _preflight_requests(cache, specs):
    """Refuse a known whole stage before issuing its first missing request."""
    missing = []
    for spec in specs:
        blockers = cache.lhb_overlap_blockers(spec) if spec.endpoint == 'lhb_summary' else []
        _need(not blockers, 'LHB_OVERLAPPING_FAILURE_NO_IMPLICIT_RETRY:' +
              ','.join(row['request_key'] for row in blockers))
        current = _lookup_for_policy(cache, spec)
        if current is None or current.status != 'COMPLETE' and cache.config.get('retry_failed', False):
            blockers = cache.factor_overlap_blockers(spec) if spec.endpoint == 'adjustment_factors' else []
            _need(not blockers, 'FACTOR_OVERLAPPING_FAILURE_NO_IMPLICIT_RETRY:' +
                  ','.join(row['request_key'] for row in blockers))
            missing.append(spec)
    if not missing:
        return
    _need(not cache.config.get('offline', False), 'OFFLINE_SOURCE_MISSING_OR_FAILED_NO_NETWORK')
    limits = getattr(cache.budget, 'limits', {})
    metrics = getattr(cache.budget, 'metrics', {})
    calls = metrics.get('calls', {})
    sdk = sum(len(s.parameters.get('codes', ()))+2 if s.endpoint in
        ('adjustment_factors','stock_basic','history','history_adjusted') else 3 for s in missing
        if provider_requests.ENDPOINTS[s.endpoint]['provider'] == 'baostock')
    http = sum(cache.max_http for s in missing if provider_requests.ENDPOINTS[s.endpoint]['provider'] != 'baostock')
    for prior, extra, maximum in (('requests',len(missing),'max_requests'),
                                 ('sdk_reserved',sdk,'max_sdk_calls'),
                                 ('http_reserved',http,'max_http_attempts')):
        if maximum in limits:
            _need(calls.get(prior, 0)+extra <= limits[maximum], 'COMPLETE_STAGE_REQUEST_BUDGET_REQUIRED:' + maximum)


def capture_theme_sources(cache, *, core_run, collection, config=None, target_ids=None):
    """Read optional current-cohort context from existing originals only.

    A screen chooses which existing whole nodes are worth modeling. It never
    changes a node's members or the native reader's original selected cohort.
    Missing/partial context remains UNKNOWN, with no request or fallback.
    """
    policy = _collection_policy(cache, config)
    arm = core_run['results']['SHORT_HORIZON_STRENGTH_R2']
    selected = [r['instrument_id'] for r in arm['items'] if r['research_selected']]
    _need(selected == arm['research_selected_ids'] and len(selected) == len(set(selected)), 'WHOLE_ORIGINAL_SELECTION_REQUIRED')
    targets = selected if target_ids is None else list(target_ids)
    _need(len(targets) == len(set(targets)) and all(isinstance(iid, str) for iid in targets)
          and set(targets) <= set(arm.get('eligible_pool_ids', selected)), 'PRESCREEN_THEME_TARGET_SCOPE_INVALID')
    selected_targets = set(selected) & set(targets)
    if not selected_targets:
        return ThemeSourceCollection((), (), (), (), dict(schema_version='PREMARKET_THEME_ACQUISITION_FACTS_V1',
            status='NO_PRESCREEN_TARGET_CONTEXT_NOT_REQUESTED', original_selected_count=len(selected),
            all_requested_industry_nodes=[], all_requested_concept_symbols=[], records=[],
            requested_members=0, native_confirmation='UNKNOWN_NOT_COMPUTED',
            scope='NO_ADMITTED_ORIGINAL_SELECTION_NOT_NO_MARKET_MAINLINE', display_amount_filter_applied=False,
            context_ready=False, network_collection_allowed=False, missing_source_is_not_empty=True))
    snapshot = datetime.now(SH).date().isoformat()
    families = []
    if policy.get('include_industries', True):
        families.append(('industry_list_sina','industry_members_sina','industry',r'hangye_[A-Za-z0-9_]+'))
    if policy.get('include_concepts', True):
        families.append(('concept_list_sina','concept_members_sina','concept',r'gn_[A-Za-z0-9_]+'))
    _need(families, 'EXPLICIT_THEME_SOURCE_FAMILY_REQUIRED')
    requests, directories, facts = [], [], []
    instant = datetime.now(timezone.utc)
    def cached(spec, record=None):
        record = record if record is not None else cache.lookup(spec)
        if record is None:
            facts.append(dict(endpoint=spec.endpoint, parameters=spec.parameters,
                              status='UNKNOWN_NOT_COLLECTED'))
            return None
        metadata = cache.metadata(record)
        _need(max(_stamp(metadata[key]) for key in ('first_seen_at', 'ingested_at', 'completed_at')) <= instant,
              'OPTIONAL_CONTEXT_NOT_VISIBLE_AT_CUTOFF')
        if record.status != 'COMPLETE' or not metadata['pagination_complete']:
            facts.append(dict(endpoint=spec.endpoint, parameters=spec.parameters,
                              status=record.status, actual_capture=metadata,
                              reason='INCOMPLETE_ORIGINAL_NOT_RETRIED'))
            return None
        return record
    nodes = dict(industry=[], concept=[])
    core_ids = {r.receipt_id for r in collection.core_records}
    captures = {}
    recipes = []
    for directory_ep, member_ep, family, pattern in families:
        spec = RequestSpec(directory_ep, {}, snapshot, role='CURRENT_THEME_DIRECTORY', current=True)
        directory = cached(spec)
        if directory is None:
            continue
        directories.append(directory)
        captures[directory.receipt_id] = cache.metadata(directory)
        result = cache.verify(directory)
        if result['status'] != 'COMPLETE' or not result['pagination_complete']:
            facts.append(dict(endpoint=directory_ep,status=result['status'],reason='DIRECTORY_NOT_COMPLETE_NO_MEMBER_SCOPE_FABRICATED'))
            continue
        entries = set()
        for row in result['rows']:
            node = row.get('label') or row.get('板块代码')
            _need(isinstance(node, str) and re.fullmatch(pattern, node, re.ASCII) and node not in entries,
                  'ORIGINAL_THEME_DIRECTORY_NODE_INVALID_OR_DUPLICATE')
            entries.add(node); nodes[family].append(node)
            requests.append(RequestSpec(member_ep, {'symbol':node}, snapshot, role='WHOLE_CURRENT_THEME_MEMBERS', current=True))
        _need(entries, 'COMPLETE_THEME_DIRECTORY_EMPTY_NOT_KNOWN_EMPTY_ECONOMY')
    maximum = policy.get('max_theme_nodes', 2048)
    _need(type(maximum) is int and 0 < maximum <= 8192 and len(requests) <= maximum, 'DECLARED_THEME_NODE_COUNT_BOUND_NO_TRUNCATION')
    potential = dict(industry=[], concept=[])
    for spec in requests:
        record = cached(spec)
        if record is None:
            continue
        result = cache.verify(record)
        literal_ids = []
        for row in result['rows']:
            code = row.get('code', row.get('代码')); symbol = row.get('symbol')
            if isinstance(symbol, str) and re.fullmatch(r'(?:sh|sz|bj)[0-9]{6}', symbol, re.ASCII) and symbol[2:] == code:
                literal_ids.append(code+'.'+symbol[:2].upper())
        intersection = [iid for iid in selected if iid in set(literal_ids)]
        admitted_intersection = selected_targets & set(literal_ids)
        family = 'industry' if spec.endpoint == 'industry_members_sina' else 'concept'
        if admitted_intersection:
            potential[family].append(spec.parameters['symbol'])
            captures[record.receipt_id] = cache.metadata(record)
        facts.append(dict(endpoint=spec.endpoint,parameters=spec.parameters,status=result['status'],
            pagination_complete=result['pagination_complete'],rows=len(result['rows']),
            actual_capture=cache.metadata(record), literal_intersection_with_full_original_selected=intersection,
            potential_reader_request=bool(admitted_intersection),
            omitted_from_potential_scope_reason=None if intersection else
                'NO_LITERAL_INTERSECTION_OBSERVED' if result['status']=='COMPLETE' and result['pagination_complete']
                else 'INCOMPLETE_SOURCE_CANNOT_PROVE_ABSENCE_OF_MEMBERSHIP',
            literal_intersection_is_not_authenticated_membership=True))
    # The old 42 sessions are read only for a cached node with an admitted
    # member. These originals remain complete market-wide observations.
    history_complete = True
    if any(potential.values()):
        for day in collection.sessions[-67:-25]:
            for endpoint in ('market_daily', 'master_all'):
                spec = RequestSpec(endpoint, {'date': day}, day, role='FULL_MARKET_CONTEXT')
                record = cache.lookup(spec)
                if record is not None:
                    record = cached(spec, record)
                    if record is None:
                        history_complete = False
                    elif record.receipt_id not in core_ids:
                        captures[record.receipt_id] = cache.metadata(record)
                else:
                    recipe = cache.context_recipe(endpoint, day)
                    if recipe is None:
                        history_complete = False
                        facts.append(dict(endpoint=endpoint, parameters=spec.parameters,
                                          status='UNKNOWN_NOT_COLLECTED'))
                    else:
                        parent = recipe['typed_receipt']
                        _need(max(_stamp(parent[key]) for key in ('source_published_at', 'first_seen_at', 'ingested_at')) <= instant,
                              'OPTIONAL_CONTEXT_NOT_VISIBLE_AT_CUTOFF')
                        if parent['status'] != 'COMPLETE' or not parent['pagination_complete']:
                            history_complete = False
                        recipes.append({k:deepcopy(recipe[k]) for k in ('raw','receipt','typed_receipt')})
    cache.terminal()
    statuses = [r['status'] for r in facts]
    return ThemeSourceCollection(tuple(captures.values()), tuple(recipes), tuple(potential['industry']),
        tuple(potential['concept']), dict(schema_version='PREMARKET_THEME_ACQUISITION_FACTS_V1',
            status='COMPLETE_CAPTURE_NOT_THEME_READY' if statuses and all(s == 'COMPLETE' for s in statuses)
                and selected_targets == set(selected) and history_complete else 'CACHE_ONLY_PARTIAL_OR_UNKNOWN',
            records=facts, directory_captures=[cache.metadata(r) for r in directories],
            requested_members=sum(len(v) for v in potential.values()), original_selected_count=len(selected),
            network_collection_allowed=False, missing_source_is_not_empty=True,
            context_ready=bool(any(potential.values()) and history_complete and len(collection.sessions) >= 67),
            context_history_complete=history_complete, admitted_selected_count=len(selected_targets),
            all_requested_industry_nodes=nodes['industry'], all_requested_concept_symbols=nodes['concept'],
            potential_industry_nodes=potential['industry'], potential_concept_symbols=potential['concept'],
            excluded_from_potential_scope_count=len(requests)-sum(len(v) for v in potential.values()),
            potential_scope_is_not_ready_or_source_authority=True,
            scope='CACHED_WHOLE_NODES_AFTER_PRESCREEN_WITH_FULL_ORIGINAL_SELECTION_DENOMINATOR',
            display_amount_filter_applied=False, native_authority='NOT_ISSUED_BY_CAPTURE_CACHE'))
