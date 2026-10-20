"""Fresh, bounded authentication of sealed native R2 archives for local import.

This contract never restores a NativeCore or historical-owner capability and
never invokes a selector. A pinned local seal authenticates archive integrity,
not the market-data vendor. Files remain necessary for subsequent daily work.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timezone
import gzip
import gc
import hashlib
import io
from itertools import islice
import json
import math
import os
from pathlib import Path
import re
try:
    import resource
except ImportError:  # Windows uses the already declared daily psutil dependency.
    resource = None
import stat
import sys
import tempfile
import time
import weakref

from pydantic import TypeAdapter

from .contracts import DailyBar, MarketInput, SecurityRecord, SourceReceipt, UniverseSnapshot
from .history_artifacts import digest_market_input as _reference_digest_market_input
from .visibility import stamp

VERSION = 'SHORT_HORIZON_ARCHIVE_BRIDGE_V1'
STRATEGY = 'SHORT_HORIZON_STRENGTH_R2'
CERTIFICATES = 'short_horizon_archive_bridge_certificates_v1'
RUN_BINDINGS = 'short_horizon_archive_run_bindings_v1'
INPUT_BINDINGS = 'short_horizon_archive_input_bindings_v1'
SOURCES = 'short_horizon_archive_sources_v1'
CSV_EXPORTS = 'short_horizon_archive_csv_exports_v1'
CSV_SELECTIONS = ('all', 'observation', 'focus', 'unselected')
_MINT = object()
_CAPABILITIES = weakref.WeakKeyDictionary()
_FP = ('st_dev', 'st_ino', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_flags')
_ARTIFACTS = frozenset({'canonical-run.json.gz', 'input.json.gz', 'manifest.json',
    'observation.csv', 'all.csv', 'focus.csv', 'unselected.csv', 'observation.json'})
_EXPORTS = _ARTIFACTS - {'canonical-run.json.gz', 'input.json.gz', 'manifest.json'}
_IDENTITY = ('implementation_version', 'spec_revision', 'feature_version', 'source_hash',
    'config_hash', 'spec_hash', 'selection_policy_hash', 'source_file_hashes')
_RUN_METADATA_FIELDS = ('run_id', 'strategy_id', 'execution_profile', 'content_hash',
    'input_id', 'observation_date', 'decision_cutoff', 'generated_at', 'coverage_type')
# Only this unchanged current implementation may reproduce the saved inline
# normalization. Archived Python is verified as bytes and NEVER executed.
_NORMALIZER_FILES = tuple('stockfish_core/yaogu/' + name for name in (
    'provider_normalization.py', 'provider_comparability.py', 'contracts.py',
    'rules.py', 'provider_funds.py', 'e2e_tracing.py', 'provider_requests.py',
    'history_artifacts.py', 'premarket_native_core_v1.py'))


def _require(value, reason):
    if not value:
        raise ValueError('R2_ARCHIVE_BRIDGE_' + reason)


def _inline_typed_input(bundle):
    # PrivateAttr is deliberately absent from the canonical JSON hash. A
    # retained object must never acquire an unauthenticated history owner.
    _require(type(bundle) is MarketInput and bundle.history_artifact_ref is None
        and bundle.history_artifact_hash is None and bundle._history_store is None,
        'INLINE_TYPED_INPUT_WITHOUT_HISTORY_OWNER_REQUIRED')


def _canonical_chunks(value, *, checkpoint=None):
    """Preserve the frozen JSON bytes using C encoding and bounded UTF-8 copies.

    The C encoder materializes Unicode text; this is NOT a bounded-text encoder.
    Its allocation and elapsed time remain subject to the invocation's existing
    RSS/time checks. Only temporary UTF-8 copies are bounded to one MiB. Keep
    exactly the frozen encoder options, including strict UTF-8 errors, and do
    not cache hashes of borrowed mutable objects.
    """
    if checkpoint is not None:
        checkpoint()
    encoder = json.JSONEncoder(sort_keys=True, ensure_ascii=False, separators=(',', ':'),
        default=str, allow_nan=False)
    for text in encoder.iterencode(value, _one_shot=True):
        for start in range(0, len(text), 256 * 1024):
            if checkpoint is not None:
                checkpoint()
            yield text[start:start + 256 * 1024].encode('utf-8')
    if checkpoint is not None:
        checkpoint()


def digest_json_stream(value, *, checkpoint=None):
    """Bridge-local encoding of the unchanged full canonical JSON identity."""
    digest = hashlib.sha256()
    for chunk in _canonical_chunks(value, checkpoint=checkpoint):
        digest.update(chunk)
    return digest.hexdigest()


_TYPED_BATCH_ROWS = 128
DEFAULT_ENCODING_PROFILE = 'TYPED_ROWS_128_V1'
CLOSURE_ENCODING_PROFILE = 'TYPED_ROWS_1024_V1'
_ENCODING_PROFILE_ROWS = {
    DEFAULT_ENCODING_PROFILE: _TYPED_BATCH_ROWS,
    CLOSURE_ENCODING_PROFILE: 1024,
}
_TYPED_BATCH_ADAPTERS = {
    DailyBar: TypeAdapter(tuple[DailyBar, ...]),
    SecurityRecord: TypeAdapter(tuple[SecurityRecord, ...]),
}


def validate_encoding_profile(profile):
    """Select an explicit fixed row-batch policy, never a resource-budget override."""
    _require(type(profile) is str and profile in _ENCODING_PROFILE_ROWS,
        'ENCODING_PROFILE_INVALID')
    return profile


def _typed_batch_eligible(bundle):
    return (type(bundle) is MarketInput and type(bundle.universe) is UniverseSnapshot
        and type(bundle.bars) is tuple and type(bundle.universe.securities) is tuple
        and all(type(row) is DailyBar for row in bundle.bars)
        and all(type(row) is SecurityRecord for row in bundle.universe.securities))


def _typed_canonical_chunks(bundle, *, checkpoint=None, encoding_profile=DEFAULT_ENCODING_PROFILE):
    """Keep the frozen row-wise identity with fixed-size model row batches.

    The row count is bounded, not each row's size or the remaining envelope.
    Only exact validated containers and row types use the Rust serializer;
    other inputs retain immediate per-row dump/encoding order.
    No full input dump or mutable-object hash cache is introduced.
    The explicit closure profile changes row-count/checkpoint granularity only;
    complete fields, byte identity, UTF-8 copies, and caller budgets remain.
    """
    batch_rows = _ENCODING_PROFILE_ROWS[validate_encoding_profile(encoding_profile)]
    check = checkpoint or (lambda: None)
    use_batches = _typed_batch_eligible(bundle)

    def encoded(value):
        yield from _canonical_chunks(value, checkpoint=checkpoint)

    def rows(values, model_type):
        yield b'['
        if not use_batches:
            for index, row in enumerate(values):
                check()
                if index:
                    yield b','
                dumped = row.model_dump(mode='json') if hasattr(row, 'model_dump') else row
                yield from encoded(dumped)
            yield b']'
            return
        source, emitted = iter(values), False
        while True:
            check()
            batch = tuple(islice(source, batch_rows))
            if not batch:
                break
            if all(type(row) is model_type for row in batch):
                dumped = _TYPED_BATCH_ADAPTERS[model_type].dump_python(batch, mode='json')
            else:
                dumped = [row.model_dump(mode='json') if hasattr(row, 'model_dump') else row
                          for row in batch]
            check()
            text = json.dumps(dumped, sort_keys=True, ensure_ascii=False, separators=(',', ':'),
                              default=str, allow_nan=False)
            check()
            if emitted:
                yield b','
            # Strip only the batch array's delimiters, without a whole-text
            # slice or UTF-8 copy; the surrounding array preserves row order.
            end = len(text) - 1
            for start in range(1, end, 256 * 1024):
                check()
                yield text[start:min(start + 256 * 1024, end)].encode('utf-8')
            check()
            del batch, dumped, text
            emitted = True
        yield b']'

    check()
    envelope = bundle.model_dump(mode='json', exclude={'bars', 'universe'})
    check()
    universe = bundle.universe.model_dump(mode='json', exclude={'securities'})
    check()
    yield b'{'
    for index, key in enumerate(sorted((*envelope, 'bars', 'universe'))):
        if index:
            yield b','
        yield from encoded(key)
        yield b':'
        if key == 'bars':
            yield from rows(bundle.bars, DailyBar)
        elif key == 'universe':
            yield b'{'
            for position, field in enumerate(sorted((*universe, 'securities'))):
                if position:
                    yield b','
                yield from encoded(field)
                yield b':'
                if field == 'securities':
                    yield from rows(bundle.universe.securities, SecurityRecord)
                else:
                    yield from encoded(universe[field])
            yield b'}'
        else:
            yield from encoded(envelope[key])
    yield b'}'
    check()


def digest_market_input(bundle, *, checkpoint=None, encoding_profile=DEFAULT_ENCODING_PROFILE):
    """Bridge-local equivalent of the unchanged normalizer identity helper."""
    validate_encoding_profile(encoding_profile)
    if checkpoint is not None:
        checkpoint()
    if not _typed_batch_eligible(bundle):
        # Iterators and custom row serializers may have Python side effects.
        # Preserve the reference's immediate per-row dump/encode order for
        # anything outside the exact immutable containers of validated input.
        result = _reference_digest_market_input(bundle)
    else:
        digest = hashlib.sha256()
        for chunk in _typed_canonical_chunks(bundle, checkpoint=checkpoint, encoding_profile=encoding_profile):
            digest.update(chunk)
        result = digest.hexdigest()
    if checkpoint is not None:
        checkpoint()
    return result


def _nonfinite_json(_):
    raise ValueError('R2_ARCHIVE_BRIDGE_NONFINITE_JSON')


def _json(raw):
    def pairs(items):
        # The JSON decoder supplies a complete list of string-key pairs.
        # Building the dict in C and comparing cardinalities still checks
        # every key (including escaped-equivalent names), without one Python
        # function call per field in the full market input and frozen run.
        result = dict(items)
        _require(len(result) == len(items), 'DUPLICATE_JSON_KEY')
        return result
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=_nonfinite_json)


def _sha(value):
    _require(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value), 'SHA256_REQUIRED')
    return value


def _fp(path):
    value = Path(path).stat()
    _require(stat.S_ISREG(value.st_mode), 'REGULAR_FILE_REQUIRED')
    return {key: getattr(value, key, 0) for key in _FP}


def _rss():
    if resource is not None:
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024)
    import psutil
    return psutil.Process().memory_info().rss


def _ref(value):
    _require(isinstance(value, dict) and set(value) == {'path', 'sha256', 'bytes', 'fp'}, 'REF4_REQUIRED')
    _require(isinstance(value['path'], str) and Path(value['path']).is_absolute(), 'ABSOLUTE_ORIGINAL_PATH_REQUIRED')
    _sha(value['sha256'])
    _require(type(value['bytes']) is int and value['bytes'] > 0 and isinstance(value['fp'], dict)
        and set(value['fp']) == set(_FP) and all(type(v) is int for v in value['fp'].values())
        and value['bytes'] == value['fp']['st_size'], 'PHYSICAL_IDENTITY_REQUIRED')
    return value


class ArchiveVerificationSession:
    """One invocation budget shared by all runs, inputs, and terminal rereads.

    Caches file identities and successful archive capabilities, never open file
    handles. ``terminal`` rereads every registered byte and validates locators.
    Cancellation callbacks run at every file/chunk and normalization boundary.
    """
    def __init__(self, *, checkpoint=None, max_total_read_bytes=8 * 1024**3,
                 max_unique_bytes=4 * 1024**3, max_files=4096,
                 max_seconds=300, max_rss_bytes=2 * 1024**3,
                 encoding_profile=DEFAULT_ENCODING_PROFILE):
        self.encoding_profile = validate_encoding_profile(encoding_profile)
        for value in (max_total_read_bytes, max_unique_bytes, max_files, max_rss_bytes):
            _require(type(value) is int and value > 0,
                'POSITIVE_SESSION_BUDGET_REQUIRED')
        _require(type(max_seconds) in (int, float) and math.isfinite(max_seconds) and max_seconds > 0,
            'FINITE_POSITIVE_TIME_BUDGET_REQUIRED')
        self.checkpoint = checkpoint or (lambda: None)
        self.max_total_read_bytes, self.max_unique_bytes = max_total_read_bytes, max_unique_bytes
        self.max_files, self.max_seconds, self.max_rss_bytes = max_files, max_seconds, max_rss_bytes
        self.started = time.monotonic()
        self.files, self.capabilities, self.locators = {}, {}, []
        self._verified_views, self._borrowed_values = {}, {}
        self._pending_run_projections, self._borrowed_identities = {}, {}
        self._projection_audit_enabled = False
        self.read_bytes = self.unique_bytes = self.terminal_count = 0
        self.check()

    def check(self):
        self.checkpoint()
        _require(time.monotonic() - self.started <= self.max_seconds, 'SESSION_TIME_BUDGET_EXCEEDED')
        _require(_rss() <= self.max_rss_bytes, 'SESSION_RSS_BUDGET_EXCEEDED')

    def read(self, path, expected=None, limit=256 * 1024**2, *, retain=True, force=False):
        self.check()
        original = Path(path)
        path = original.resolve(strict=True)
        before = _fp(path)
        _require(0 < before['st_size'] <= limit, 'FILE_SIZE_BUDGET_EXCEEDED')
        prior = self.files.get(str(path))
        if isinstance(expected, dict):
            _ref(expected)
            _require(str(path) == expected['path'] and before == expected['fp'], 'ORIGINAL_PHYSICAL_IDENTITY_CHANGED')
        if prior:
            _require(prior['fp'] == before, 'REGISTERED_FILE_CHANGED')
        if not retain and prior and not force:
            checksum = expected['sha256'] if isinstance(expected, dict) else expected
            _require(checksum is None or checksum == prior['sha256'], 'FILE_SHA_MISMATCH')
            return None
        if prior is None:
            _require(len(self.files) < self.max_files and self.unique_bytes + before['st_size'] <= self.max_unique_bytes,
                'SESSION_UNIQUE_FILE_BUDGET_EXCEEDED')
        digest, pieces = hashlib.sha256(), []
        count = 0
        # O_NONBLOCK prevents a stat/open FIFO replacement from hanging before
        # any checkpoint; the descriptor must still be the same regular file.
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(descriptor, 'rb') as handle:
            opened = os.fstat(handle.fileno())
            _require(stat.S_ISREG(opened.st_mode) and
                {key: getattr(opened, key, 0) for key in _FP} == before, 'OPENED_FILE_IDENTITY_CHANGED')
            while True:
                self.check()
                chunk = handle.read(1024**2)
                if not chunk:
                    break
                count += len(chunk)
                self.read_bytes += len(chunk)
                _require(count <= limit and self.read_bytes <= self.max_total_read_bytes,
                    'SESSION_TOTAL_READ_BUDGET_EXCEEDED')
                digest.update(chunk)
                if retain:
                    pieces.append(chunk)
        _require(_fp(path) == before and count == before['st_size'] and original.resolve(strict=True) == path,
            'FILE_CHANGED_DURING_READ')
        actual = dict(path=str(path), sha256=digest.hexdigest(), bytes=count, fp=before)
        if expected is not None:
            _require(actual == expected if isinstance(expected, dict) else actual['sha256'] == _sha(expected),
                'FILE_SHA_OR_IDENTITY_MISMATCH')
        _require(prior is None or prior == actual, 'REGISTERED_FILE_CHANGED')
        if prior is None:
            self.unique_bytes += count
        self.files[str(path)] = actual
        self.check()
        return b''.join(pieces) if retain else None

    def terminal(self):
        self.check()
        for expected in tuple(self.files.values()):
            self.read(expected['path'], expected, retain=False, force=True)
        for locator in self.locators:
            self.check()
            locator.validate()
        for value, expected, typed in self._borrowed_values.values():
            self.check()
            if typed:
                _inline_typed_input(value)
            actual = digest_market_input(value, checkpoint=self.check,
                encoding_profile=self.encoding_profile) if typed else digest_json_stream(value, checkpoint=self.check)
            _require(actual == expected, 'BORROWED_VERIFIED_CONTENT_CHANGED')
            self.check()
        self.terminal_count += 1
        return self.audit()

    def release_cached_archives(self):
        """Drop unborrowed heavy capabilities, never provenance or guards."""
        self.check()
        self.capabilities.clear()
        gc.collect()
        self.check()

    def audit(self):
        result = dict(files=len(self.files), unique_bytes=self.unique_bytes, actual_read_bytes=self.read_bytes,
            elapsed_seconds=round(time.monotonic() - self.started, 6), terminal_rereads=self.terminal_count,
            borrowed_values=len(self._borrowed_values), verified_views=len(self._verified_views),
            peak_rss_bytes=_rss(),
            limits=dict(total_read_bytes=self.max_total_read_bytes, unique_bytes=self.max_unique_bytes,
                files=self.max_files, seconds=self.max_seconds, rss_bytes=self.max_rss_bytes))
        # Preserve the legacy default audit shape. Explicit profiles are
        # observable without changing the strategy or input canonical identity.
        if self.encoding_profile != DEFAULT_ENCODING_PROFILE:
            result.update(encoding_profile=self.encoding_profile,
                batch_rows=_ENCODING_PROFILE_ROWS[validate_encoding_profile(self.encoding_profile)])
        if self._projection_audit_enabled:
            _require(set(self._borrowed_identities) == set(self._borrowed_values),
                'BORROWED_IDENTITY_AUDIT_INCOMPLETE')
            result['pending_run_projections'] = deepcopy(sorted(self._pending_run_projections.values(),
                key=lambda row: (row['run_id'], row['bridge_id'])))
            result['borrowed_value_identities'] = deepcopy(sorted(self._borrowed_identities.values(),
                key=lambda row: (row['kind'], row['document_id'], row['bridge_id'], row['content_hash'])))
        return result

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _decompressed_bytes(raw, session):
    pieces, total = [], 0
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as handle:
        while True:
            session.check()
            part = handle.read(1024**2)
            if not part:
                break
            total += len(part)
            _require(total <= 256 * 1024**2, 'DECOMPRESSED_DOCUMENT_BUDGET_EXCEEDED')
            pieces.append(part)
    raw = b''.join(pieces)
    del pieces
    return raw


def _decompress(raw, session):
    return _json(_decompressed_bytes(raw, session))


def _source_map(session, registry, overrides, run):
    _require(isinstance(registry, list) and registry and len(registry) <= 2048, 'CODE_REGISTRY_REQUIRED')
    originals = {}
    for value in registry:
        _ref(value)
        _require(value['path'] not in originals, 'DUPLICATE_CODE_REFERENCE')
        originals[value['path']] = value
    launchers = [Path(p).parent for p in originals if Path(p).name == 'premarket']
    _require(len(launchers) == 1, 'ORIGINAL_SOURCE_ROOT_AMBIGUOUS')
    root = launchers[0]
    by_name = {}
    for path, value in originals.items():
        _require(Path(path).is_relative_to(root), 'SOURCE_PATH_OUTSIDE_ORIGINAL_ROOT')
        name = Path(path).relative_to(root).as_posix()
        _require(name in {'premarket', 'premarket.ps1', 'scripts/stockfish.py'}
            or name.startswith('stockfish_core/') and name.endswith('.py'), 'CODE_REGISTRY_PATH_INVALID')
        by_name[name] = value
    required = {'premarket', 'premarket.ps1', 'scripts/stockfish.py',
        'stockfish_core/yaogu/premarket_workflow_v1.py', 'stockfish_core/yaogu/premarket_native_core_v1.py',
        'stockfish_core/yaogu/strategy_experiments.py'} | set(_NORMALIZER_FILES)
    _require(required <= set(by_name), 'NATIVE_IMPLEMENTATION_CLOSURE_MISSING')
    frozen = run.get('source_file_hashes')
    _require(isinstance(frozen, dict) and frozen, 'FROZEN_STRATEGY_SOURCE_HASHES_REQUIRED')
    for name, checksum in frozen.items():
        _require(isinstance(name, str) and not Path(name).is_absolute() and '..' not in Path(name).parts
            and '\\' not in name, 'FROZEN_SOURCE_PATH_INVALID')
        _sha(checksum)
        if name in by_name:
            _require(by_name[name]['sha256'] == checksum, 'STRATEGY_VS_WORKER_SOURCE_MISMATCH')
    _require(digest_json_stream(frozen) == run['source_hash'], 'STRATEGY_SOURCE_HASH_MISMATCH')
    _require(isinstance(overrides, dict) and set(overrides) <= (set(by_name) | set(frozen)), 'UNREGISTERED_SOURCE_OVERRIDE')
    proofs = {}
    for name in sorted(set(by_name) | set(frozen)):
        original = by_name.get(name)
        expected = original['sha256'] if original else frozen[name]
        if name in overrides:
            _require(isinstance(overrides[name], str) and Path(overrides[name]).is_absolute(), 'ABSOLUTE_SOURCE_OVERRIDE_REQUIRED')
            path = Path(overrides[name]).resolve(strict=True)
            session.read(path, expected, limit=16 * 1024**2, retain=False)
            mode = 'EXPLICIT_COPY_CONTENT_PROOF_NOT_ORIGINAL_PHYSICAL_OWNER'
        else:
            path = root / name
            session.read(path, original or expected, limit=16 * 1024**2, retain=False)
            mode = 'ORIGINAL_PHYSICAL_REFERENCE' if original else 'ORIGINAL_DECLARED_CONTENT_ONLY'
        proofs[name] = dict(original_reference=deepcopy(original), original_sha256=expected,
            current_reference=deepcopy(session.files[str(path.resolve())]), proof_mode=mode)
    current_root = Path(__file__).resolve().parents[2]
    for name in _NORMALIZER_FILES:
        session.read(current_root / name, by_name[name]['sha256'], limit=16 * 1024**2, retain=False)
    return root, by_name, proofs


def _trio(session, item, sealed_refs, receipts, source_proofs, coverage, cutoff):
    """Whole original-trio checks, with every read charged to this session.

    Same receipt preimage/request/RAW checks as historical_replay.verify_capture;
    no historical reconstruction object or owner is created.
    """
    from .premarket_native_core_v1 import _typed, _LocatorBinding
    documents = {}
    for role in ('result', 'raw', 'receipt'):
        reference = _ref(item[role])
        _require(sealed_refs.get(reference['path']) == reference, 'UNSEALED_CORE_' + role.upper())
        documents[role] = _json(session.read(reference['path'], reference))
    result, raw, physical = (documents[k] for k in ('result', 'raw', 'receipt'))
    rid = result['request_receipt_id']
    _require(rid in receipts, 'UNREGISTERED_CORE_RECEIPT')
    expected_id = hashlib.sha256(json.dumps({k: v for k, v in physical.items() if k != 'receipt_id'},
        sort_keys=True).encode()).hexdigest()
    _require(rid == expected_id == physical['receipt_id'] and
        [r for r in result['receipts'] if r.get('receipt_id') == rid] == [physical], 'ORIGINAL_RECEIPT_IDENTITY_MISMATCH')
    receipt_path, raw_path = Path(item['receipt']['path']), Path(item['raw']['path'])
    _require(raw_path == receipt_path.parent / (physical['content_hash'] + '.raw.json')
        and receipt_path.name == rid + '.receipt.json' and item['raw']['sha256'] == physical['content_hash'],
        'ORIGINAL_TRIO_LAYOUT_MISMATCH')
    root = Path(item['archive_root']).resolve(strict=True)
    direct = root / 'cache/raw' / receipt_path.name
    nested = root / 'raw'
    _require((direct.exists() and direct.resolve() == receipt_path) or
        (receipt_path.is_relative_to(nested) and receipt_path.parent.name == 'raw'), 'ORIGINAL_TRIO_ROOT_MISMATCH')
    for pkey, rkey in (('dataset', 'endpoint'), ('provider', 'provider'), ('parameters', 'parameters')):
        _require(physical[pkey] == result[rkey], 'ORIGINAL_REQUEST_SCOPE_MISMATCH')
    for key in ('rows', 'status', 'pagination_complete'):
        _require(raw.get(key) == result.get(key), 'ORIGINAL_RAW_' + key.upper() + '_MISMATCH')
    for key in ('coverage', 'gaps'):
        default = {} if key == 'coverage' else []
        _require(raw.get(key, default) == result.get(key, default), 'ORIGINAL_COVERAGE_MISMATCH')
    _require(physical['status'] == result['status'] and physical['pagination_complete'] == result['pagination_complete']
        and (physical['row_count'] is None or physical['row_count'] == len(result['rows'])), 'ORIGINAL_STATUS_OR_COUNT_MISMATCH')
    _require(raw.get('field_version') == result.get('field_version') == physical.get('field_version'), 'ORIGINAL_FIELD_VERSION_MISMATCH')
    seen, ingested = stamp(physical['first_seen_at']), stamp(physical['ingested_at'])
    _require(stamp(result['started_at']) <= seen <= ingested <= stamp(result['completed_at']) <= cutoff,
        'ORIGINAL_CAPTURE_CLOCK_INVALID')
    for document in (raw, result):
        if 'first_seen_at' in document:
            _require(stamp(document['first_seen_at']) == seen, 'ORIGINAL_FIRST_SEEN_CONFLICT')
    typed = _typed(result, physical)
    _require(typed == receipts[rid] and max(typed.source_published_at, typed.first_seen_at, typed.ingested_at) <= cutoff,
        'ORIGINAL_TYPED_RECEIPT_MISMATCH')
    producers = item.get('producer_files', ())
    if coverage != 'TEST_FIXTURE' or producers:
        expected = result.get('implementation_source_hashes')
        names = {'provider_requests.py', 'provider_requests_worker.py', 'provider_budget.py'}
        _require(isinstance(expected, dict) and set(expected) == names and len(producers) == 3
            and {Path(p['path']).name for p in producers} == names, 'ORIGINAL_THREE_PRODUCER_FILES_REQUIRED')
        for ref in producers:
            _ref(ref)
            key = 'stockfish_core/yaogu/' + Path(ref['path']).name
            proof = source_proofs.get(key)
            _require(sealed_refs.get(ref['path']) == ref and ref['sha256'] == expected[Path(ref['path']).name],
                'ORIGINAL_PRODUCER_BINDING_MISMATCH')
            # Cached captures can have an older producer in a distinct worker
            # checkout. Its original proof is independent of this job's core
            # implementation epoch; never substitute a same-named newer file.
            if proof is None or proof['original_reference'] != ref:
                session.read(ref['path'], ref, limit=16 * 1024**2, retain=False)
    if item.get('verification_locator') is not None:
        session.locators.append(_LocatorBinding(item['verification_locator']))
    return result, typed, raw_path


def _validate_run(run, bundle, manifest, artifacts, session, input_hash):
    from .candidate_service import CandidateService
    _require(digest_json_stream({k: v for k, v in run.items() if k != 'content_hash'},
        checkpoint=session.check) == run.get('content_hash'), 'RUN_CONTENT_HASH_MISMATCH')
    _require(set(run.get('results', {})) == {STRATEGY}, 'SINGLE_NATIVE_R2_REQUIRED')
    result = run['results'][STRATEGY]
    _require(result.get('strategy_id') == STRATEGY and result.get('full_eligible_pool_retained') is True,
        'FULL_POOL_REQUIRED')
    for key in _IDENTITY:
        _require(key in run and run[key] == result.get(key), 'TOP_INNER_IDENTITY_MISMATCH_' + key.upper())
    _require(run.get('research_context_role') == 'NATIVE_R1_PLUS_CONTEXT'
        and run.get('artifact_role') == 'NATIVE_READ_ONLY_RESEARCH_ARTIFACT_NOT_BUSINESS_DB_REGISTRATION'
        and run.get('source_database_written') is False, 'ORIGINAL_NATIVE_ROLE_REQUIRED')
    _require(run['input_hash'] == result['input_hash'] == manifest['input_hash'] == input_hash
        and run['input_id'] == 'market_' + run['input_hash']
        and run['coverage_type'] == result['coverage_type'] == bundle.coverage_type
        and run['observation_date'] == str(bundle.observation_date), 'INLINE_INPUT_IDENTITY_MISMATCH')
    _require(stamp(result['decision_cutoff']) == stamp(run['decision_cutoff']), 'INNER_CLOCK_MISMATCH')
    for key in ('run_id', 'observation_date', 'target_session', 'decision_cutoff'):
        _require(manifest.get(key) == run.get(key), 'MANIFEST_RUN_BINDING_MISMATCH_' + key.upper())
    fields = ('input_hash', 'observation_date', 'decision_cutoff', 'spec_hash', 'source_hash',
        'implementation_version', 'universe_hash', 'execution_profile', 'feature_version', 'research_context_hash')
    _require(run['run_id'] == 'strategy_experiment_' + digest_json_stream({k: run[k] for k in fields})[:24], 'NATIVE_RUN_ID_MISMATCH')
    rows = result.get('items')
    _require(isinstance(rows, list), 'FULL_ITEMS_REQUIRED')
    ids = [row.get('instrument_id') for row in rows]
    _require(all(isinstance(i, str) and re.fullmatch(r'[0-9]{6}\.(SH|SZ|BJ)', i) for i in ids)
        and len(ids) == len(set(ids)) and len(result.get('eligible_pool_ids', ())) == len(ids)
        and set(result['eligible_pool_ids']) == set(ids), 'FULL_ELIGIBLE_POOL_IDENTITY_MISMATCH')
    names = {row.instrument_id: row.name for row in bundle.universe.securities}
    _require(all(i in names and row.get('name') == names[i] and not row.get('detail_ref')
        for i, row in zip(ids, rows)), 'POOL_NAME_OR_INLINE_DETAIL_MISMATCH')
    counts = Counter(row.get('decision_status') for row in rows)
    _require(not set(counts) - {'FOCUS', 'WATCH', 'REJECTED', 'UNKNOWN'}, 'INVALID_DECISION_STATUS')
    expected_counts = dict(eligible=len(rows), evaluated=len(rows),
        **{key: counts[key.upper()] for key in ('focus', 'watch', 'rejected', 'unknown')})
    _require(result['counts'] == manifest['counts'] and all(result['counts'].get(k) == v for k, v in expected_counts.items()), 'POOL_COUNT_MISMATCH')
    _require(all(type(row.get('research_selected')) is bool for row in rows), 'RESEARCH_SELECTION_BOOL_REQUIRED')
    selected = [row['instrument_id'] for row in rows if row['research_selected']]
    _require(selected == result.get('research_selected_ids') and all(row['decision_status'] in {'FOCUS', 'WATCH'}
        for row in rows if row['research_selected']), 'RESEARCH_SELECTION_MISMATCH')
    policy = result['selection_policy_id']
    _require(set(run.get('selection_results', {})) == {policy}, 'SELECTION_POLICY_MISMATCH')
    selection = run['selection_results'][policy]
    _require(selection.get('selected_ids') == selected and selection.get('count') == len(selected)
        and selection.get('strategy_id') == STRATEGY and selection.get('selection_policy_id') == policy,
        'NATIVE_SELECTION_MISMATCH')
    class Encoder(CandidateService):
        def strategy_experiment(self, run_id):
            _require(run_id == run['run_id'], 'EXPORT_RUN_MISMATCH')
            return run
    encoder = object.__new__(Encoder)
    _require(set(manifest.get('official_exports', {})) == _EXPORTS, 'FIVE_OFFICIAL_EXPORTS_REQUIRED')
    for name in _EXPORTS:
        session.check()
        _require(manifest['official_exports'][name] == dict(source_content_hash=run['content_hash'], reference=artifacts[name]),
            'OFFICIAL_EXPORT_REFERENCE_MISMATCH')
        raw = session.read(artifacts[name]['path'], artifacts[name])
        if name.endswith('.csv'):
            expected = encoder._format_strategy_experiment_csv(run, strategy_id=STRATEGY, selection=name[:-4]).encode()
            _require(raw == expected, 'OFFICIAL_CSV_MISMATCH_' + name)
        else:
            expected = encoder.export_strategy_experiment_json(run['run_id'], strategy_id=STRATEGY, selection='observation').encode()
            _require(_json(raw) == _json(expected), 'OFFICIAL_JSON_MISMATCH')


def _bridge_identity(session):
    root = Path(__file__).resolve().parents[2]
    # Import and projection dependencies are intentionally conservative: a
    # package implementation change requires explicit new bridge verification.
    paths = list((root / 'stockfish_core').rglob('*.py'))
    paths += list((root / 'config').glob('*.yaml')) + list((root / 'config').glob('*.json'))
    files = {}
    for path in sorted(paths):
        session.read(path, limit=16 * 1024**2, retain=False)
        files[path.relative_to(root).as_posix()] = session.files[str(path.resolve())]['sha256']
    return dict(schema_version=VERSION, source_file_hashes=files, source_hash=digest_json_stream(files))


class ArchiveBridgeCapability:
    """Fresh local verification authority; JSON/pickle cannot reconstruct it."""
    __slots__ = ('_session', '__weakref__')
    def __new__(cls, token=None, session=None):
        _require(token is _MINT, 'FRESH_ARCHIVE_AUTHENTICATION_REQUIRED')
        result = super().__new__(cls)
        result._session = session
        return result

    def __reduce__(self):
        raise TypeError('ARCHIVE_BRIDGE_CAPABILITY_NOT_SERIALIZABLE')

    def audit(self):
        state = _state(self)
        return dict(deepcopy(state['audit']), verification_budget=state['session'].audit())


def _state(capability):
    _require(type(capability) is ArchiveBridgeCapability and capability in _CAPABILITIES,
        'GENUINE_FRESH_ARCHIVE_CAPABILITY_REQUIRED')
    state = _CAPABILITIES[capability]
    session = state['session_ref']()
    _require(session is not None and capability._session is session, 'CAPABILITY_SESSION_CHANGED')
    return dict(state, session=session)


def _verified_run(state):
    """Recover a released run only from this invocation's pinned original."""
    run = state.get('run')
    if run is None:
        ref = state['run_reference']
        run = _decompress(state['session'].read(ref['path'], ref), state['session'])
    _require(digest_json_stream(run, checkpoint=state['session'].check) == state['run_hash'], 'VERIFIED_CAPABILITY_CONTENT_CHANGED')
    state['session'].check()
    return run


def authenticate_archive(job_dir, *, expected_final_sha256, source_overrides=None,
                         allow_test_fixture=False, session=None, retain_input=False):
    """Authenticate originals read-only; never write a DB, run a selector or fetch.

    ``source_overrides`` maps registered relative source paths to explicit old
    byte copies. Copies prove original content only, never old physical owners.
    """
    session = session or ArchiveVerificationSession()
    _require(type(session) is ArchiveVerificationSession, 'VERIFICATION_SESSION_REQUIRED')
    _require(type(retain_input) is bool, 'RETAIN_INPUT_BOOL_REQUIRED')
    session.check()
    initial_identity = _bridge_identity(session)
    job = Path(job_dir).resolve(strict=True)
    _require(job.is_dir(), 'ORIGINAL_JOB_DIRECTORY_REQUIRED')
    checksum = _sha(expected_final_sha256)
    overrides = dict(source_overrides or {})
    cache_key = (str(job), checksum, digest_json_stream(overrides), bool(allow_test_fixture), retain_input)
    if cache_key in session.capabilities:
        cap = session.capabilities[cache_key]
        _require(_state(cap)['identity'] == initial_identity, 'VERIFIER_IMPLEMENTATION_CHANGED')
        if retain_input:
            _inline_typed_input(_state(cap)['retained_input'])
        return cap
    final_raw = session.read(job / 'final-control.json', checksum, limit=64 * 1024)
    final = _json(final_raw)
    _require(final.get('schema_version') == 'STANDALONE_PREMARKET_WORKFLOW_V1'
        and final.get('status') in {'COMPLETE', 'COMPLETED_WITH_UNKNOWNS'}
        and final.get('external_worker_exit_code') in {0, 2}
        and all(final.get(key) is True for key in ('external_worker_reaped', 'core_verified',
            'implementation_epoch_verified', 'source_terminal_binding_verified'))
        and final.get('source_capability_restored') is False and Path(final['job']) == job,
        'SEALED_SUCCESSFUL_NATIVE_JOB_REQUIRED')
    _require(len(final_raw) == 16384 and stamp(final['final_sealed_at']) <= datetime.now(timezone.utc), 'SEALED_FINAL_CONTROL_REQUIRED')
    controls = {key: _json(session.read(_ref(final[key])['path'], final[key], limit=16 * 1024**2))
        for key in ('configuration', 'code_registry', 'completion')}
    completion = controls['completion']
    _require(completion.get('schema_version') == 'STANDALONE_PREMARKET_WORKFLOW_V1'
        and completion.get('status') == final['status'] and completion.get('core_verified') is True
        and completion.get('strategy_id') == STRATEGY and completion.get('business_database_written') is False
        and completion.get('source_capability_serialized') is False
        and stamp(completion['completed_at']) <= stamp(final['final_sealed_at']), 'SEALED_COMPLETION_MISMATCH')
    artifacts = completion.get('core_artifacts')
    _require(isinstance(artifacts, dict) and set(artifacts) == _ARTIFACTS, 'COMPLETE_EIGHT_CORE_ARTIFACTS_REQUIRED')
    for name, ref in artifacts.items():
        _ref(ref)
        _require(Path(ref['path']).name == name and Path(ref['path']).is_relative_to(job), 'CORE_ARTIFACT_OUTSIDE_ORIGINAL_JOB')
    manifest = _json(session.read(artifacts['manifest.json']['path'], artifacts['manifest.json'], limit=16 * 1024**2))
    _require(manifest.get('schema_version') == 'PREMARKET_NATIVE_CORE_V1' and manifest.get('status') == 'PASS'
        and manifest.get('artifacts') == {k: v for k, v in artifacts.items() if k != 'manifest.json'}
        and manifest.get('source_capability_serialized') is False and manifest.get('source_database_written') is False,
        'NATIVE_MANIFEST_MISMATCH')
    input_document = _decompress(session.read(artifacts['input.json.gz']['path'], artifacts['input.json.gz']), session)
    _require(isinstance(input_document, dict) and isinstance(input_document.get('receipts'), list)
        and all(isinstance(row, dict) for row in input_document['receipts']), 'INLINE_RECEIPT_LIST_REQUIRED')
    _require(input_document.get('history_artifact_ref') is None and input_document.get('history_artifact_hash') is None
        and not any(row.get('historical_time_proof') is not None for row in input_document['receipts']),
        'INLINE_INPUT_WITHOUT_HISTORICAL_OWNER_REQUIRED')
    coverage_type = input_document['coverage_type']
    _require(isinstance(coverage_type, str) and (coverage_type in {'PROSPECTIVE_CAPTURED', 'RECONSTRUCTED_HISTORICAL'} or
        (allow_test_fixture is True and coverage_type == 'TEST_FIXTURE')), 'PROSPECTIVE_CAPTURED_OR_EXPLICIT_FIXTURE_REQUIRED')
    input_hash = digest_json_stream(input_document, checkpoint=session.check)
    observation_date = date.fromisoformat(input_document['observation_date'])
    original_sessions = tuple(date.fromisoformat(day) for day in input_document['sessions'])
    saved_receipts = tuple(SourceReceipt.model_validate(row) for row in input_document['receipts'])
    # Hash every original JSON field before releasing the full document. The
    # fresh, unchanged normalizer below supplies the sole typed MarketInput;
    # its complete canonical hash must equal this original hash. Coercions,
    # omitted defaults, extra fields and altered rows therefore cannot pass by
    # sharing just the small envelope retained here. Original bytes stay pinned.
    del input_document
    gc.collect()
    run = _decompress(session.read(artifacts['canonical-run.json.gz']['path'], artifacts['canonical-run.json.gz']), session)
    _require(input_hash == run.get('input_hash') == manifest.get('input_hash'), 'CANONICAL_TYPED_INLINE_INPUT_REQUIRED')
    _require(run.get('content_hash') == manifest.get('canonical_content_hash')
        and run.get('run_id') == completion.get('run_id') and run.get('observation_date') == completion.get('observation_date')
        and run.get('target_session') == completion.get('target_session'), 'COMPLETION_CORE_IDENTITY_MISMATCH')
    root, source_registry, source_proofs = _source_map(session, controls['code_registry'], overrides, run)
    cutoff = stamp(run['decision_cutoff'])
    _require(cutoff <= stamp(run['generated_at']) <= stamp(completion['completed_at']), 'ORIGINAL_RUN_TIME_ORDER_INVALID')
    sealed_refs = {}
    for ref in completion.get('validation_references', ()):
        _ref(ref)
        _require(ref['path'] not in sealed_refs or sealed_refs[ref['path']] == ref, 'SEALED_SOURCE_REFERENCE_CONFLICT')
        sealed_refs[ref['path']] = ref
    _require(final.get('source_reference_count') == len(completion['validation_references']), 'SEALED_SOURCE_COUNT_MISMATCH')
    # This summary is only a locator. Its entries acquire authority exclusively
    # from the sealed refs and exact receipt set in the sealed native manifest.
    capture = _json(session.read(job / 'capture-summary.json', limit=16 * 1024**2))
    items = capture.get('core_records')
    _require(isinstance(items, list) and 0 < len(items) <= 1024, 'CORE_CAPTURE_COUNT_INVALID')
    typed = [SourceReceipt.model_validate(value) for value in manifest['source_receipts']]
    receipts = {value.receipt_id: value for value in typed}
    _require(len(receipts) == len(typed) == len(items) and receipts == {r.receipt_id: r for r in saved_receipts}, 'EXACT_CORE_RECEIPT_SET_REQUIRED')
    del saved_receipts
    results, raw_paths, used, evidence = [], {}, set(), []
    for item in items:
        session.check()
        result, receipt, raw_path = _trio(session, item, sealed_refs, receipts, source_proofs, coverage_type, cutoff)
        _require(receipt.receipt_id not in used, 'DUPLICATE_CORE_CAPTURE')
        used.add(receipt.receipt_id)
        results.append(result)
        raw_paths[receipt.receipt_id] = raw_path
        evidence.append(dict(receipt_id=receipt.receipt_id, typed_receipt=receipt.model_dump(mode='json'),
            originals={k: deepcopy(item[k]) for k in ('raw', 'receipt', 'result')},
            producer_files=deepcopy(item.get('producer_files', [])),
            original_integrity_scope='LOCAL_SHA256_NOT_VENDOR_AUTHENTICATION'))
    _require(used == set(receipts), 'CORE_CAPTURE_RECEIPT_OMISSION')
    from .premarket_native_core_v1 import _calendar_sessions
    from .provider_normalization import normalize_capture_with_report
    from .research_context_artifacts import SourceArtifactReader
    sessions = _calendar_sessions(results, observation_date)
    _require(sessions == original_sessions, 'ORIGINAL_SESSION_CALENDAR_MISMATCH')
    session.check()
    normalized, normalization_report = normalize_capture_with_report(results, observation_date, cutoff,
        sessions, coverage_type=coverage_type)
    session.check()
    _require(digest_market_input(normalized, checkpoint=session.check,
        encoding_profile=session.encoding_profile) == input_hash == run['input_hash']
        and normalization_report == manifest['normalization_report'], 'FRESH_NORMALIZATION_DIFFERS_FROM_ORIGINAL_INPUT')
    if retain_input:
        _inline_typed_input(normalized)
    del results
    _validate_run(run, normalized, manifest, artifacts, session, input_hash)
    class Reader(SourceArtifactReader):
        def __call__(self, receipt_id):
            return session.read(raw_paths[receipt_id], receipts[receipt_id].content_hash)
    reader = Reader(tuple(receipts.values()), raw_paths)
    reader.validate_target_calendar_context(run['target_calendar_evidence'], bundle=normalized, cutoff=cutoff)
    retained_input = normalized if retain_input else None
    del normalized
    gc.collect()
    identity = _bridge_identity(session)
    _require(identity == initial_identity, 'VERIFIER_IMPLEMENTATION_CHANGED')
    bridge_id = 'r2_archive_bridge_' + digest_json_stream(dict(version=VERSION, archive_final_sha256=checksum,
        signal_content_hash=run['content_hash'], input_hash=run['input_hash'], verifier_source_hash=identity['source_hash']))
    run_hash = digest_json_stream(run, checkpoint=session.check)
    session.check()
    audit = dict(schema_version=VERSION, status='VERIFIED', bridge_id=bridge_id, run_id=run['run_id'],
        signal_content_hash=run['content_hash'], run_document_hash=run_hash,
        input_id=run['input_id'], input_hash=run['input_hash'],
        archive_final_sha256=checksum, archive_job=str(job), source_overrides=overrides,
        observation_date=run['observation_date'], target_session=run['target_session'],
        original_decision_cutoff=run['decision_cutoff'], original_generated_at=run['generated_at'],
        original_final_sealed_at=final['final_sealed_at'], verified_at=datetime.now(timezone.utc).isoformat(),
        coverage_type=coverage_type, counts=deepcopy(run['results'][STRATEGY]['counts']),
        original_strategy_identity={key: deepcopy(run[key]) for key in _IDENTITY},
        verifier_identity=identity, authenticated_source_count=len(evidence),
        source_proofs=source_proofs, core_artifacts=deepcopy(artifacts),
        original_source_root=str(root), reexecuted_selector=False, historical_owner_restored=False,
        native_core_owner_restored=False, current_import_time_is_original_capture_time=False,
        integrity_scope='PINNED_LOCAL_SEAL_AND_REAUTHENTICATED_ORIGINAL_BYTES_NOT_VENDOR_AUTHENTICATION',
        scope='ORIGINAL_NATIVE_R2_CORE_INPUT_RUN_AND_FIVE_EXPORTS_ONLY',
        excluded_scope=['THEME_PRESENTATION', 'NEWS_PRESENTATION', 'ACCOUNT_COST_EXECUTION_PORTFOLIO'],
        market_edge='NOT_ESTABLISHED', source_database_written=False)
    session.terminal()
    if retained_input is not None:
        _inline_typed_input(retained_input)
    capability = ArchiveBridgeCapability(_MINT, session)
    # The weak-key registry must not retain session -> cached cap as a global
    # strong path. The only strong cycle is session <-> cap and is collectable.
    _CAPABILITIES[capability] = dict(session_ref=weakref.ref(session), run=run, retained_input=retained_input,
        # Mint only after the original run's full hashes, every archive check,
        # and the source terminal. This private copy describes those originals;
        # it is never reconstructed from a serialized audit or caller metadata.
        run_metadata={name: deepcopy(run.get(name)) for name in _RUN_METADATA_FIELDS},
        pool_nonempty=bool(run['results'][STRATEGY]['items']),
        run_reference=deepcopy(artifacts['canonical-run.json.gz']),
        input_reference=deepcopy(artifacts['input.json.gz']),
        input_index=dict(input_id=run['input_id'], observation_date=run['observation_date'],
            coverage_type=coverage_type, max_session=str(max(original_sessions)) if original_sessions else None),
        input_sessions=tuple(str(day) for day in original_sessions),
        evidence=evidence, audit=audit, identity=identity, run_hash=run_hash,
        input_hash=input_hash, allow_test_fixture=allow_test_fixture)
    session.capabilities[cache_key] = capability
    return capability


def _stored_matches(connection, namespace, document_id, expected_hash, session=None):
    """Verify bytes against an already authenticated canonical source hash.

    This is not metadata-only authority. BLOB reads hash the entire saved JSON
    and avoid expanding a second huge Python object for immutable reuse.
    """
    row = connection.execute('SELECT rowid,content_hash FROM documents WHERE namespace=? AND document_id=?',
        (namespace, document_id)).fetchone()
    if row is None:
        return False
    _require(row['content_hash'] == expected_hash, 'BRIDGED_DATABASE_PARENT_CHANGED')
    _require(hasattr(connection, 'blobopen'), 'STREAMING_SQLITE_BLOB_READER_REQUIRED')
    digest = hashlib.sha256()
    with connection.blobopen('documents', 'payload', row['rowid'], readonly=True) as handle:
        while True:
            if session is not None:
                session.check()
            chunk = handle.read(1024**2)
            if not chunk:
                break
            digest.update(chunk)
    _require(digest.hexdigest() == expected_hash, 'BRIDGED_DATABASE_PARENT_CHANGED')
    return True


def _put_archived_document(connection, namespace, document_id, expected_hash, session,
                           *, reference=None, document=None, temporary_dir=None):
    """Write authenticated canonical TEXT without a whole UTF-8 encoding copy.

    The original gzip is not canonical JSON. Encode the validated object to a
    private temporary file, verify every canonical byte, then release a newly
    decoded input before binding. A normal INSERT still enforces all JSON and
    index constraints; no incremental writes to indexed payload columns occur.
    The temporary file is closed and removed on success, conflict or cancellation.
    """
    if _stored_matches(connection, namespace, document_id, expected_hash, session):
        return
    _require((reference is None) != (document is None), 'ARCHIVED_DOCUMENT_SOURCE_REQUIRED')
    if reference is not None:
        document = _decompress(session.read(reference['path'], reference), session)
    _require(isinstance(document, dict), 'ARCHIVED_DOCUMENT_OBJECT_REQUIRED')
    with tempfile.TemporaryFile(prefix='stockfish-verified-bridge-', dir=temporary_dir) as spool:
        digest, total = hashlib.sha256(), 0
        for block in _canonical_chunks(document, checkpoint=session.check):
            total += len(block)
            _require(total <= 256 * 1024**2, 'CANONICAL_DOCUMENT_BUDGET_EXCEEDED')
            digest.update(block)
            spool.write(block)
        _require(digest.hexdigest() == expected_hash, 'VERIFIED_CAPABILITY_CONTENT_CHANGED')
        del document, block
        gc.collect()
        session.check()
        spool.flush()
        # SQLite copies a Python UDF's returned bytes before returning from
        # the callback. The Python bytes are released before JSON indexes are
        # evaluated, unlike a bound buffer pinned throughout sqlite3_step.
        # Keep this connection-local function private and non-deterministic.
        function_name = '_stockfish_verified_bridge_payload_v1'
        called, callback_errors = False, []
        def payload():
            nonlocal called
            raw = None
            try:
                _require(not called, 'SPOOL_CALLBACK_REPEATED')
                called = True
                session.check()
                spool.seek(0)
                raw = spool.read(total + 1)
                _require(len(raw) == total and hashlib.sha256(raw).hexdigest() == expected_hash,
                    'VERIFIED_CAPABILITY_CONTENT_CHANGED')
                session.check()
                return raw
            except BaseException as exc:
                raw = None
                callback_errors.append(exc)
                raise
        connection.create_function(function_name, 0, payload)
        cursor, failure = None, None
        try:
            cursor = connection.execute(
                'INSERT INTO documents VALUES (?, ?, CAST(_stockfish_verified_bridge_payload_v1() AS TEXT), ?, ?)',
                (namespace, document_id, expected_hash, datetime.now(timezone.utc).isoformat()))
        except BaseException as exc:
            failure = callback_errors[0] if callback_errors else exc
        finally:
            try:
                if cursor is not None:
                    cursor.close()
            except BaseException as exc:
                if failure is None:
                    failure = exc
            try:
                connection.create_function(function_name, 0, None)
            except BaseException as exc:
                if failure is None:
                    failure = exc
        if failure is not None:
            raise failure
        _require(called, 'SPOOL_CALLBACK_NOT_CALLED')
        session.check()
        _require(_stored_matches(connection, namespace, document_id, expected_hash, session),
            'BRIDGED_DATABASE_PARENT_CHANGED')


def commit_bridge(service, capability):
    """Atomically append frozen input/run and separate provenance; no activation.

    Caller must own the existing service job lock before opening a writable DB.
    There is no timestamp argument: imported_at is the actual current UTC time.
    """
    state = _state(capability)
    session, run = state['session'], _verified_run(state)
    _require(_bridge_identity(session) == state['identity'], 'VERIFIED_CAPABILITY_CONTENT_CHANGED')
    _require(state['audit']['coverage_type'] != 'TEST_FIXTURE' or service.allow_test_fixture is True, 'ISOLATED_FIXTURE_SERVICE_REQUIRED')
    from ..jobs import JobService
    _require(isinstance(service.jobs, JobService) and service.jobs.owner_id and not service.jobs._closed
        and not service.jobs._service_lock.file.closed and service.jobs.store.path == service.store.path,
        'EXISTING_JOB_LOCK_OWNER_REQUIRED')
    session.terminal()
    certificate = dict(capability.audit(), imported_at=datetime.now(timezone.utc).isoformat(),
        source_database_written=True, original_run_mutated=False, original_input_mutated=False,
        active_input_changed=False)
    bridge_id, run_id, input_id = (certificate[k] for k in ('bridge_id', 'run_id', 'input_id'))
    # SQLite must parse the complete input to enforce its JSON indexes. Do not
    # retain an unrelated expanded signal run during that high-water operation.
    # Its original ref/hash remains authenticated; reload it before projection.
    _CAPABILITIES[capability]['run'] = None
    state.pop('run', None)
    del run
    gc.collect()
    session.check()
    with service.store.transaction() as connection:
        # checked_payload on all existing rows is handled by the same immutable
        # writer. Never compact the inline input or remint the old run identity.
        existing = connection.execute('SELECT * FROM documents WHERE namespace=? AND document_id=?',
            (CERTIFICATES, bridge_id)).fetchone()
        if existing:
            from ..storage import checked_payload
            certificate = checked_payload(existing)
            _require(all(certificate.get(k) == state['audit'][k] for k in
                ('bridge_id', 'run_id', 'input_id', 'input_hash', 'signal_content_hash', 'run_document_hash', 'archive_final_sha256')),
                'EXISTING_CERTIFICATE_IDENTITY_CONFLICT')
        put = service.store._put_document
        _put_archived_document(connection, 'candidate_inputs', input_id, state['input_hash'], session,
            reference=state['input_reference'], temporary_dir=service.store.path.parent)
        put(connection, 'candidate_input_indexes', input_id, state['input_index'])
        run = _verified_run(state)
        _put_archived_document(connection, 'strategy_experiment_runs', run_id, state['run_hash'], session,
            document=run, temporary_dir=service.store.path.parent)
        # Reassert all immutable projection payloads; this repairs missing pages
        # and refuses corrupt/conflicting headers instead of silently skipping.
        service._write_strategy_projection(connection, run)
        for item in state['evidence']:
            put(connection, SOURCES, bridge_id + ':' + item['receipt_id'], dict(schema_version=VERSION,
                bridge_id=bridge_id, run_id=run_id, input_id=input_id, **item))
        put(connection, CERTIFICATES, bridge_id, certificate)
        for selection in CSV_SELECTIONS:
            ref = state['audit']['core_artifacts'][selection + '.csv']
            raw = session.read(ref['path'], ref)
            export = dict(schema_version=VERSION, artifact_role='AUTHENTICATED_ORIGINAL_CSV',
                bridge_id=bridge_id, run_id=run_id, strategy_id=STRATEGY, selection=selection,
                run_document_hash=state['run_hash'], signal_content_hash=run['content_hash'],
                input_id=input_id, input_hash=state['input_hash'], archive_final_sha256=certificate['archive_final_sha256'],
                original_reference=deepcopy(ref), csv_sha256=ref['sha256'], csv_bytes=ref['bytes'],
                csv_text=raw.decode('utf-8'))
            put(connection, CSV_EXPORTS, run_id + ':' + selection, export)
        binding = {k: certificate[k] for k in ('bridge_id', 'run_id', 'signal_content_hash', 'input_id',
            'input_hash', 'archive_final_sha256', 'imported_at')}
        put(connection, RUN_BINDINGS, run_id, dict(schema_version=VERSION, **binding))
        input_binding = connection.execute('SELECT * FROM documents WHERE namespace=? AND document_id=?',
            (INPUT_BINDINGS, input_id)).fetchone()
        if input_binding:
            from ..storage import checked_payload
            saved = checked_payload(input_binding)
            _require(saved.get('input_id') == input_id and saved.get('input_hash') == certificate['input_hash'],
                'EXISTING_INPUT_BRIDGE_IDENTITY_CONFLICT')
        else:
            put(connection, INPUT_BINDINGS, input_id, dict(schema_version=VERSION, **binding))
        session.terminal()
    return deepcopy(certificate)


def saved_csv_export(service, run, strategy_id, selection):
    """Read the exact authenticated original CSV, never regenerate its bytes.

    This validates saved immutable provenance, not a fresh archive invocation.
    Any partial bridge/export set fails closed; only entirely unbridged runs
    may use the ordinary formatter. The caller's complete run and its actual
    stored bytes must both match the certificate's original canonical hash.
    """
    from ..storage import checked_payload
    _require(isinstance(run, dict) and isinstance(run.get('run_id'), str), 'CSV_RUN_REQUIRED')
    run_id = run['run_id']
    with service.store.transaction(write=False) as connection:
        def read(namespace, key):
            row = connection.execute('SELECT * FROM documents WHERE namespace=? AND document_id=?',
                (namespace, key)).fetchone()
            return checked_payload(row) if row else None
        def scoped(namespace, limit):
            indexed = connection.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='strategy_item_observation_lookup'").fetchone()
            sql = 'SELECT * FROM documents' + (' INDEXED BY strategy_item_observation_lookup' if indexed else '')
            rows = connection.execute(sql + " WHERE namespace=? AND json_extract(payload,'$.run_id')=? LIMIT ?",
                (namespace, run_id, limit)).fetchall()
            return [(row['document_id'], checked_payload(row)) for row in rows]
        binding = read(RUN_BINDINGS, run_id)
        export_rows = dict(scoped(CSV_EXPORTS, 5))
        for selected in CSV_SELECTIONS:
            key = run_id + ':' + selected
            direct = read(CSV_EXPORTS, key)
            if direct is not None:
                export_rows[key] = direct
        exports = list(export_rows.items())
        certificates = scoped(CERTIFICATES, 2)
        if binding is None and not exports and not certificates:
            _require(not scoped(SOURCES, 1) and not scoped(INPUT_BINDINGS, 1),
                'CSV_ORPHAN_BRIDGE_STATE')
            return None
        _require(binding is not None and binding.get('schema_version') == VERSION
            and binding.get('run_id') == run_id, 'CSV_RUN_BINDING_REQUIRED')
        certificate = read(CERTIFICATES, binding.get('bridge_id'))
        _require(certificate is not None and certificate.get('schema_version') == VERSION
            and len(certificates) == 1 and certificates[0] == (binding['bridge_id'], certificate),
            'CSV_CERTIFICATE_REQUIRED')
        _require(all(certificate.get(key) == value for key, value in binding.items()),
            'CSV_CERTIFICATE_BINDING_MISMATCH')
        run_hash = _sha(certificate.get('run_document_hash'))
        _require(run.get('strategy_id') == STRATEGY and run.get('execution_profile') == 'SHORT_HORIZON_RESEARCH_SINGLE'
            and certificate.get('run_id') == run_id and run.get('content_hash') == certificate.get('signal_content_hash')
            and run.get('input_id') == certificate.get('input_id') and run.get('input_hash') == certificate.get('input_hash')
            and digest_json_stream(run) == run_hash
            and _stored_matches(connection, 'strategy_experiment_runs', run_id, run_hash),
            'CSV_FROZEN_RUN_IDENTITY_MISMATCH')
        _require(len(exports) == len(CSV_SELECTIONS)
            and {key for key, _ in exports} == {run_id + ':' + item for item in CSV_SELECTIONS},
            'CSV_COMPLETE_ORIGINAL_SET_REQUIRED')
        by_selection = {}
        for key, export in exports:
            selected = export.get('selection')
            _require(selected in CSV_SELECTIONS and key == run_id + ':' + selected, 'CSV_SELECTION_BINDING_MISMATCH')
            expected = dict(schema_version=VERSION, artifact_role='AUTHENTICATED_ORIGINAL_CSV',
                bridge_id=binding['bridge_id'], run_id=run_id, strategy_id=STRATEGY, selection=selected,
                run_document_hash=run_hash, signal_content_hash=run['content_hash'],
                input_id=run['input_id'], input_hash=run['input_hash'], archive_final_sha256=certificate['archive_final_sha256'])
            _require(all(export.get(name) == value for name, value in expected.items()), 'CSV_PARENT_IDENTITY_MISMATCH')
            ref = _ref((certificate.get('core_artifacts') or {}).get(selected + '.csv'))
            _require(export.get('original_reference') == ref and export.get('csv_sha256') == ref['sha256']
                and export.get('csv_bytes') == ref['bytes'] and isinstance(export.get('csv_text'), str),
                'CSV_ORIGINAL_REFERENCE_MISMATCH')
            raw = export['csv_text'].encode('utf-8')
            _require(len(raw) == ref['bytes'] and hashlib.sha256(raw).hexdigest() == ref['sha256'],
                'CSV_ORIGINAL_BYTES_CHANGED')
            by_selection[selected] = export['csv_text']
        if (strategy_id or 'PV_OBSERVATION_V1') != STRATEGY:
            return None
        selected = selection or 'all'
        _require(selected in CSV_SELECTIONS, 'CSV_SELECTION_REQUIRED')
        return by_selection[selected]


def _verified(service, namespace, document_id, session, *, return_state=False, retain_input=False):
    owned_session = session is None
    session = session or ArchiveVerificationSession()
    binding = service.store.get_document(namespace, document_id)
    _require(binding is not None and binding.get('schema_version') == VERSION, 'VERIFIED_BRIDGE_BINDING_REQUIRED')
    _require(document_id == binding.get('run_id' if namespace == RUN_BINDINGS else 'input_id'),
        'REQUESTED_BRIDGE_IDENTITY_MISMATCH')
    certificate = service.store.get_document(CERTIFICATES, binding['bridge_id'])
    _require(certificate is not None and certificate.get('schema_version') == VERSION,
        'VERIFIED_BRIDGE_CERTIFICATE_REQUIRED')
    for key, value in binding.items():
        _require(certificate.get(key) == value, 'BRIDGE_CERTIFICATE_BINDING_MISMATCH')
    capability = authenticate_archive(certificate['archive_job'],
        expected_final_sha256=certificate['archive_final_sha256'],
        source_overrides=certificate['source_overrides'],
        allow_test_fixture=service.allow_test_fixture, session=session, retain_input=retain_input)
    state = _state(capability)
    for key in ('bridge_id', 'run_id', 'input_id', 'input_hash', 'signal_content_hash', 'run_document_hash', 'archive_final_sha256'):
        _require(certificate.get(key) == state['audit'][key], 'BRIDGE_REAUTHENTICATION_MISMATCH')
    with service.store.transaction(write=False) as connection:
        _require(_stored_matches(connection, 'strategy_experiment_runs', binding['run_id'], state['run_hash'], session)
            and _stored_matches(connection, 'candidate_inputs', binding['input_id'], state['input_hash'], session),
            'BRIDGED_DATABASE_PARENT_CHANGED')
    for item in state['evidence']:
        expected = dict(schema_version=VERSION, bridge_id=binding['bridge_id'], run_id=binding['run_id'],
            input_id=binding['input_id'], **item)
        _require(service.store.get_document(SOURCES, binding['bridge_id'] + ':' + item['receipt_id']) == expected,
            'BRIDGED_SOURCE_REGISTRATION_CHANGED')
    if owned_session:
        session.terminal()
    credential = {k: certificate[k] for k in ('bridge_id', 'run_id', 'signal_content_hash', 'input_id',
        'input_hash', 'archive_final_sha256', 'imported_at')}
    return (credential, state) if return_state else credential


def require_verified_bridge(service, run_id, *, session=None):
    return _verified(service, RUN_BINDINGS, run_id, session)


def require_verified_bridge_input(service, input_id, *, session=None):
    return _verified(service, INPUT_BINDINGS, input_id, session)


def _view_store_identity(service):
    path = Path(service.store.path).resolve(strict=True)
    identity = _fp(path)
    return (str(path), identity['st_dev'], identity['st_ino'])


def _check_verified_view(service, proof, session):
    """A small invocation proof is bound to this actual store and originals."""
    session.check()
    _require(proof.get('session_ref', lambda: None)() is session
        and any(value is proof for value in session._verified_views.values()),
        'GENUINE_INVOCATION_VIEW_REQUIRED')
    _require(proof['store'] is service.store and proof['store_identity'] == _view_store_identity(service),
        'VERIFIED_VIEW_STORE_MISMATCH')
    _require(service.store.get_document(proof['namespace'], proof['document_id']) == proof['binding']
        and service.store.get_document(CERTIFICATES, proof['credential']['bridge_id']) == proof['certificate'],
        'VERIFIED_VIEW_BINDING_CHANGED')
    with service.store.transaction(write=False) as connection:
        _require(_stored_matches(connection, 'candidate_inputs', proof['credential']['input_id'], proof['input_hash'], session)
            and _stored_matches(connection, 'strategy_experiment_runs', proof['credential']['run_id'], proof['run_hash'], session),
            'BRIDGED_DATABASE_PARENT_CHANGED')
    for document_id, expected in proof['source_rows']:
        _require(service.store.get_document(SOURCES, document_id) == expected, 'BRIDGED_SOURCE_REGISTRATION_CHANGED')


def _verified_second_run_decode(proof, session):
    """Decode the exact run bytes already strictly parsed in this invocation.

    The registered reference hashes the compressed original, including its
    seven physical identity fields. The bounded gzip decoder is unchanged.
    Only duplicate-key detection is omitted on this second stdlib parse: the
    identical original already passed it before the private proof was minted.
    This helper neither creates authority nor replaces the caller's store and
    full canonical checks or the terminal guard on the borrowed mutable run.
    """
    _require(type(session) is ArchiveVerificationSession
        and any(value is proof for value in session._verified_views.values())
        and proof.get('session_ref', lambda: None)() is session,
        'GENUINE_INVOCATION_VIEW_REQUIRED')
    ref = _ref(proof['run_reference'])
    _require(session.files.get(ref['path']) == ref, 'STRICTLY_AUTHENTICATED_RUN_REFERENCE_REQUIRED')
    session.check()
    raw = _decompressed_bytes(session.read(ref['path'], ref), session)
    session.check()
    run = json.loads(raw, parse_constant=_nonfinite_json)
    del raw
    session.check()
    return run


def _record_borrowed_identity(session, proof, value, *, typed):
    credential = proof['credential']
    identity = dict(kind='input' if typed else 'run',
        document_id=credential['input_id' if typed else 'run_id'],
        content_hash=proof['input_hash' if typed else 'run_hash'],
        bridge_id=credential['bridge_id'], run_id=credential['run_id'], input_id=credential['input_id'])
    previous = session._borrowed_identities.get(id(value))
    _require(previous is None or previous == identity, 'BORROWED_IDENTITY_CHANGED')
    session._borrowed_identities[id(value)] = identity


def authenticated_bridge_view(service, *, run_id=None, input_id=None, session, borrow_run=False,
                              retain_input=False, include_archive_seal=False, borrow_if_empty=False):
    """Return small authenticated metadata, optionally lending one frozen run.

    Returned metadata is descriptive, never a serializable import capability.
    The session retains private store-bound provenance and terminal hash guards
    for any borrowed mutable object. A plain view cannot authorize the loader.
    Only an explicit input view may retain the fresh normalizer's typed input;
    it remains private and the loader still checks its complete canonical hash.
    The opt-in nonempty projection is only a fact about the fully authenticated
    original pool. The caller must independently establish all-PENDING horizons.
    Empty pools still borrow the complete original for the existing empty-pool
    contract; a projection never turns missing evidence into a legal empty run.
    """
    _require(isinstance(session, ArchiveVerificationSession), 'GENUINE_SESSION_REQUIRED')
    _require((run_id is None) != (input_id is None) and type(borrow_run) is bool,
        'ONE_VERIFIED_VIEW_KEY_REQUIRED')
    _require(type(retain_input) is bool and (not retain_input or input_id is not None),
        'RETAIN_INPUT_REQUIRES_INPUT_VIEW')
    _require(type(include_archive_seal) is bool, 'INCLUDE_ARCHIVE_SEAL_BOOL_REQUIRED')
    _require(type(borrow_if_empty) is bool and (not borrow_if_empty or
        run_id is not None and not borrow_run), 'BORROW_IF_EMPTY_REQUIRES_EXCLUSIVE_RUN_VIEW')
    namespace, document_id = (RUN_BINDINGS, run_id) if run_id is not None else (INPUT_BINDINGS, input_id)
    _require(isinstance(document_id, str) and document_id, 'VERIFIED_VIEW_KEY_REQUIRED')
    key = (id(service.store), namespace, document_id)
    proof = session._verified_views.get(key)
    run = None
    if proof is not None:
        _check_verified_view(service, proof, session)
    if proof is None or (retain_input and proof.get('retained_input') is None
                         and proof.get('borrowed_input') is None):
        # A prior metadata-only proof cannot synthesize a fresh typed object.
        # Reauthenticate with the explicit retention mode in the same budget.
        credential, state = _verified(service, namespace, document_id, session, return_state=True,
                                      retain_input=retain_input)
        run = _verified_run(state) if borrow_run else None
        proof = dict(session_ref=weakref.ref(session), store=service.store, store_identity=_view_store_identity(service),
            namespace=namespace, document_id=document_id, credential=deepcopy(credential),
            certificate=service.store.get_document(CERTIFICATES, credential['bridge_id']),
            binding=service.store.get_document(namespace, document_id),
            run_hash=state['run_hash'], input_hash=state['input_hash'],
            run_reference=deepcopy(state['run_reference']), input_reference=deepcopy(state['input_reference']),
            run_metadata=deepcopy(state['run_metadata']),
            pool_nonempty=state['pool_nonempty'],
            archive_sealed_at=state['audit']['original_final_sealed_at'],
            input_metadata=dict(input_id=credential['input_id'], input_hash=state['input_hash'],
                observation_date=state['input_index']['observation_date'],
                coverage_type=state['input_index']['coverage_type'], sessions=list(state['input_sessions'])),
            source_rows=[(credential['bridge_id'] + ':' + item['receipt_id'],
                dict(schema_version=VERSION, bridge_id=credential['bridge_id'], run_id=credential['run_id'],
                    input_id=credential['input_id'], **deepcopy(item))) for item in state['evidence']])
        if retain_input:
            _inline_typed_input(state['retained_input'])
            proof['retained_input'] = state['retained_input']
        _require(proof['binding'] == dict(schema_version=VERSION, **credential)
            and isinstance(proof['certificate'], dict)
            and all(proof['certificate'].get(name) == value for name, value in credential.items()),
            'VERIFIED_VIEW_BINDING_CHANGED')
        session._verified_views[key] = proof
        # Reuse the same authenticated archive when its other public binding
        # points to precisely this certificate. A shared input's first proof
        # may legitimately differ, in which case it is authenticated separately.
        other_namespace = INPUT_BINDINGS if namespace == RUN_BINDINGS else RUN_BINDINGS
        other_id = credential['input_id' if other_namespace == INPUT_BINDINGS else 'run_id']
        other_binding = service.store.get_document(other_namespace, other_id)
        if other_binding == dict(schema_version=VERSION, **credential):
            session._verified_views[(id(service.store), other_namespace, other_id)] = dict(proof,
                namespace=other_namespace, document_id=other_id, binding=other_binding)
    if retain_input:
        retained = proof.get('borrowed_input')
        _inline_typed_input(proof.get('retained_input') if retained is None else retained)
    result = {name: deepcopy(proof[name]) for name in ('credential', 'run_metadata', 'input_metadata')}
    if include_archive_seal:
        result['archive_sealed_at'] = proof['archive_sealed_at']
    if borrow_if_empty:
        _require(type(proof.get('pool_nonempty')) is bool, 'AUTHENTICATED_POOL_PROJECTION_REQUIRED')
        result.update(pool_nonempty=proof['pool_nonempty'], run=None)
        session._projection_audit_enabled = True
        if proof['pool_nonempty']:
            credential = proof['credential']
            projection = {name: credential[name] for name in
                ('run_id', 'bridge_id', 'input_id', 'input_hash', 'signal_content_hash')}
            projection.update(run_document_hash=proof['run_hash'], pool_nonempty=True)
            session._pending_run_projections[(credential['run_id'], credential['bridge_id'])] = projection
    if borrow_run or borrow_if_empty and not proof['pool_nonempty']:
        run = proof.get('borrowed_run') or run
        if run is None:
            run = _verified_second_run_decode(proof, session)
        _require(digest_json_stream(run, checkpoint=session.check) == proof['run_hash'], 'BORROWED_VERIFIED_CONTENT_CHANGED')
        proof['borrowed_run'] = run
        session._borrowed_values[id(run)] = (run, proof['run_hash'], False)
        _record_borrowed_identity(session, proof, run, typed=False)
        result['run'] = run
    session.check()
    return result


def load_verified_bridge_input(service, input_id, *, session):
    """Materialize the original typed input from an invocation-authenticated ref."""
    _require(isinstance(session, ArchiveVerificationSession), 'GENUINE_SESSION_REQUIRED')
    proofs = [proof for proof in session._verified_views.values()
        if proof['store'] is service.store and proof['credential']['input_id'] == input_id]
    _require(bool(proofs), 'VERIFIED_INPUT_VIEW_REQUIRED')
    proof = next((value for value in proofs if value.get('borrowed_input') is not None
                  or value.get('retained_input') is not None), proofs[0])
    _check_verified_view(service, proof, session)
    bundle = proof.get('borrowed_input')
    if bundle is None:
        bundle = proof.get('retained_input')
    if bundle is None:
        ref = proof['input_reference']
        # Duplicate keys and the complete original object were already checked
        # during this invocation's authentication. Read exactly those pinned
        # bytes directly into the typed parser, avoiding a second full dict.
        raw = _decompressed_bytes(session.read(ref['path'], ref), session)
        bundle = MarketInput.model_validate_json(raw)
        del raw
        gc.collect()
    _inline_typed_input(bundle)
    _require(digest_market_input(bundle, checkpoint=session.check,
        encoding_profile=session.encoding_profile) == proof['input_hash'], 'BORROWED_VERIFIED_CONTENT_CHANGED')
    proof['borrowed_input'] = bundle
    session._borrowed_values[id(bundle)] = (bundle, proof['input_hash'], True)
    _record_borrowed_identity(session, proof, bundle, typed=True)
    session.check()
    return bundle
