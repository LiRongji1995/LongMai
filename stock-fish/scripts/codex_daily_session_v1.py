"""One invocation's authenticated daily facts and frozen V2 theme consumer.

The bounded JSON spool is a derivative, never a restorable source capability.
Every original and borrowed input is checked again before success. Only the
RAW projection is built once; consuming JSON still rehydrates typed models.
Local checkpoints are cooperative. The caller retains process supervision and
whole-process I/O accounting (imports and legacy identity helpers are outside
the local returned-byte meter).
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import time
from types import FunctionType, SimpleNamespace
import weakref

from stockfish_core.yaogu import session_payload_fusion_v1 as fusion
from stockfish_core.yaogu import theme_evolution_streaming as legacy
from stockfish_core.yaogu import theme_evolution_streaming_v2 as consumer
from stockfish_core.yaogu.contracts import MarketInput, ThemeSnapshot, digest
from stockfish_core.yaogu.history_artifacts import digest_json_stream, digest_market_input
from stockfish_core.yaogu.research_context_artifacts import SourceArtifactReader, RETURN_BASIS
from stockfish_core.yaogu.theme_evolution import ThemeMarketSession
from stockfish_core.yaogu.visibility import stamp

VERSION = 'DAILY_SESSION_PIPELINE_V1'
STREAM_VERSION = 'DAILY_SESSION_FACTS_STREAM_V1'
_ISSUER = object()
_FP = ('st_dev', 'st_ino', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_flags')
MIB = 1024 ** 2


def _need(value, reason):
    if not value:
        raise ValueError(VERSION + '_' + reason)


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def _fp(info):
    return {key: getattr(info, key, 0) for key in _FP}


def _clone(function, replacements):
    namespace = dict(function.__globals__)
    namespace.update(replacements)
    result = FunctionType(function.__code__, namespace, function.__name__,
                          function.__defaults__, function.__closure__)
    result.__kwdefaults__ = function.__kwdefaults__
    return result


def source_identity():
    """New execution closure; numeric/FSM sources and policies are unchanged."""
    prior = consumer.source_identity()
    files = dict(prior['source_file_hashes'])
    here, root = Path(fusion.__file__).resolve().parent, consumer.base.ROOT
    for name, expected in fusion.source_identity()['source_files'].items():
        path = root / name if '/' in name else here / name
        files[str(path.relative_to(root))] = expected
    for name in ('saved_r2_sources_v2.py', 'saved_r2_delivery.py'):
        path = here / name
        files[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    path = Path(__file__).resolve()
    files[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {**prior, 'implementation_version': VERSION,
            'feature_version': 'DAILY_SESSION_PIPELINE_EXACT_THEME_V1',
            'spec_revision': 'DAILY_SESSION_PIPELINE_V1_INLINE_CONTRACT',
            'source_file_hashes': files, 'source_hash': digest(files),
            'frozen_consumer_source_identity': prior,
            'source_projection_identity': fusion.source_identity()}


@dataclass(frozen=True)
class PipelineLimits:
    """Explicit limits may tighten, never exceed the existing theme envelope."""
    max_seconds: float
    max_read_bytes: int
    max_output_bytes: int
    max_rss_bytes: int
    max_panel_bytes: int = 32 * MIB
    max_source_bytes: int = 8 * MIB

    def __post_init__(self):
        _need(type(self.max_seconds) in (int, float) and 0 < self.max_seconds <= 2400,
              'INVALID_TIME_LIMIT')
        for name, maximum in (('max_read_bytes', 4 * 1024 ** 3),
                ('max_output_bytes', 512 * MIB), ('max_rss_bytes', 2560 * MIB),
                ('max_panel_bytes', 256 * MIB), ('max_source_bytes', 8 * MIB)):
            value = getattr(self, name)
            _need(type(value) is int and 0 < value <= maximum, 'INVALID_' + name.upper())


class _Budget:
    def __init__(self, limits, checkpoint):
        import psutil
        _need(type(limits) is PipelineLimits, 'EXPLICIT_LIMITS_REQUIRED')
        _need(checkpoint is None or callable(checkpoint), 'INVALID_CHECKPOINT')
        self.limits, self.checkpoint = limits, checkpoint
        self.process, self.started = psutil.Process(), time.monotonic()
        self.reads, self.writes, self.peak_rss = {}, {}, 0
        self.projections, self.payload_decodes, self.panels_consumed = 0, 0, 0
        self.owner_terminal_calls = 0

    def check(self):
        if self.checkpoint is not None:
            self.checkpoint()
        _need(time.monotonic() - self.started <= self.limits.max_seconds, 'TIME_BUDGET_EXCEEDED')
        rss = self.process.memory_info().rss
        self.peak_rss = max(self.peak_rss, rss)
        _need(rss <= self.limits.max_rss_bytes, 'RSS_BUDGET_EXCEEDED')

    def read(self, amount, role):
        self.reads[role] = self.reads.get(role, 0) + amount
        _need(sum(self.reads.values()) <= self.limits.max_read_bytes, 'READ_BUDGET_EXCEEDED')
        self.check()

    def before_write(self, amount):
        _need(sum(self.writes.values()) + amount <= self.limits.max_output_bytes,
              'OUTPUT_BUDGET_EXCEEDED')
        self.check()

    def wrote(self, amount, role):
        self.writes[role] = self.writes.get(role, 0) + amount

    def report(self):
        return dict(returned_read_bytes=dict(self.reads), written_bytes=dict(self.writes),
            total_metered_returned_read_bytes=sum(self.reads.values()),
            total_metered_written_bytes=sum(self.writes.values()),
            source_session_projections=self.projections, source_payload_decodes=self.payload_decodes,
            panels_consumed=self.panels_consumed, owner_terminal_calls=self.owner_terminal_calls,
            elapsed_seconds=time.monotonic() - self.started, sampled_rss_peak_bytes=self.peak_rss,
            meter_scope='PIPELINE_SOURCE_CODE_SPOOL_JOURNAL_AND_OWNER_REGISTRY_RETURNS',
            whole_process_read_accounting=False,
            unmetered_scope='IMPORTS_AND_LEGACY_IDENTITY_HELPER_READS_REQUIRE_OUTER_METER',
            resource_enforcement='COOPERATIVE_CHECKPOINTS_REQUIRES_OUTER_PROCESS_SUPERVISION')


class _Files:
    def __init__(self, budget):
        self.budget, self.refs, self.owner_paths = budget, {}, set()

    def read(self, path, *, maximum, role, expected=None, register=True):
        path = Path(path).absolute()
        _need(path.resolve(strict=True) == path and not path.is_symlink(), 'UNALIASED_FILE_REQUIRED')
        info = path.lstat()
        _need(stat.S_ISREG(info.st_mode) and not getattr(info, 'st_flags', 0) & 0x40000000,
              'REGULAR_LOCAL_FILE_REQUIRED')
        _need(0 < info.st_size <= maximum, 'FILE_SIZE_LIMIT')
        before = _fp(info)
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(path, flags)
        try:
            _need(_fp(os.fstat(fd)) == before, 'FILE_CHANGED_BEFORE_READ')
            parts, hasher, count = [], hashlib.sha256(), 0
            while True:
                block = os.read(fd, min(256 * 1024, maximum + 1 - count))
                if not block:
                    break
                count += len(block)
                self.budget.read(len(block), role)
                _need(count <= maximum, 'FILE_SIZE_LIMIT')
                hasher.update(block)
                parts.append(block)
            _need(_fp(os.fstat(fd)) == before == _fp(path.lstat()) and count == before['st_size'],
                  'FILE_CHANGED_DURING_READ')
        finally:
            os.close(fd)
        record = dict(path=str(path), sha256=hasher.hexdigest(), fp=before)
        _need(expected is None or record['sha256'] == expected, 'FILE_SHA_MISMATCH')
        prior = self.refs.get(str(path))
        _need(prior is None or prior == record, 'FILE_IDENTITY_CHANGED')
        if register:
            self.refs[str(path)] = record
        return b''.join(parts)

    def terminal(self):
        for record in tuple(self.refs.values()):
            self.read(record['path'], maximum=max(record['fp']['st_size'], 1),
                      role='physical_terminal', expected=record['sha256'])


class _ScopeStream:
    """An unexported same-invocation source scope, never reconstructed from JSON."""
    def __init__(self, token, bundle, reader, cutoff, directory, budget, files, themes):
        _need(token is _ISSUER, 'LIVE_ISSUER_REQUIRED')
        self._token, self._active = token, True
        self.bundle, self.reader, self.cutoff = bundle, reader, stamp(cutoff)
        self.directory, self.budget, self.files = directory, budget, files
        self.input_hash = digest_market_input(bundle)
        self.calendar = tuple(str(day) for day in bundle.sessions if day <= bundle.observation_date)
        self.requested_sessions = self.calendar[-67:]
        self.receipts = tuple(reader.receipts)
        self.themes = tuple(ThemeSnapshot.model_validate(row) for row in themes)
        self._receipt_hash = digest_json_stream([row.model_dump(mode='json') for row in self.receipts])
        self._paths = dict(reader._paths)
        self._borrowed, self._consumed, self._spools = {}, [], {}
        self._directory_identity = (directory.stat().st_dev, directory.stat().st_ino)
        for theme in self.themes:
            reader._validate_theme(theme, self.cutoff)
        resolver = reader._resolver(bundle, self.cutoff)

        def payload(receipt, live_reader):
            _need(live_reader is reader, 'READER_CHANGED')
            self._check_reader()
            raw = files.read(reader._paths[receipt.receipt_id], maximum=budget.limits.max_source_bytes,
                             role='source_projection', expected=receipt.content_hash)
            result = fusion.r2._source_payload(receipt, lambda key: raw if key == receipt.receipt_id else b'')
            budget.payload_decodes += 1
            return result

        projected = _clone(fusion.project_session_v1, {'r2': SimpleNamespace(
            _source_parent=fusion.r2._source_parent, _source_payload=payload)})
        panels, missing = {}, {}
        for day in self.requested_sessions:
            budget.check()
            try:
                fact = projected(reader, resolver, day, RETURN_BASIS)
            except ValueError as error:
                reason = str(error)
                prefix, _, details = reason.partition(':')
                unavailable = prefix in ('SHR2_MARKET_MARKET_DAILY_SOURCE_UNAVAILABLE',
                                         'SHR2_MARKET_MASTER_ALL_SOURCE_UNAVAILABLE')
                allowed = all(value == 'CAPTURE_AFTER_CUTOFF' or value == 'SOURCE_NOT_COMPLETE'
                              or value.startswith('SOURCE_STATUS_') for value in details.split(',') if value)
                if not unavailable or not allowed:
                    raise
                missing[day] = {'status': 'UNKNOWN', 'reason_codes': [reason]}
                continue
            panel = ThemeMarketSession.model_validate(fact['panel'])
            fact['panel'] = panel.model_dump(mode='json')
            raw = _canonical(fact)
            _need(len(raw) <= budget.limits.max_panel_bytes, 'PANEL_SIZE_LIMIT')
            path = directory / (day + '.json')
            self._write(path, raw)
            self._spools[day] = files.refs[str(path)]
            panels[day] = {'panel_hash': digest_json_stream(fact['panel']),
                'source_refs': list(panel.source_refs),
                'source_content_hashes': {ref: reader._receipts[ref].content_hash for ref in panel.source_refs},
                'expected_instrument_count': len(panel.expected_instrument_ids),
                'trading_row_count': len(panel.rows),
                'nontrading_count': len(panel.nontrading_instrument_ids)}
            budget.projections += 1
            del panel, fact, raw
        self.available_sessions = tuple(day for day in self.requested_sessions if day in panels)
        self._descriptor = {'schema_version': STREAM_VERSION, 'source_input_hash': self.input_hash,
            'observation_date': str(bundle.observation_date), 'decision_cutoff': self.cutoff.isoformat(),
            'coverage_type': bundle.coverage_type, 'full_observed_calendar': list(self.calendar),
            'requested_sessions': list(self.requested_sessions), 'available_sessions': list(self.available_sessions),
            'missing_sessions': missing, 'panels': panels, 'source_receipt_identity_hash': self._receipt_hash,
            'theme_snapshots': [row.model_dump(mode='json') for row in self.themes],
            'return_basis': RETURN_BASIS, 'source_identity': source_identity(),
            'data_role': 'CURRENT_SOURCE_CAPTURE_CONTEXT_NOT_HISTORICAL_FIRST_SEEN',
            'official_pit_validation_countable': False}
        self.descriptor_hash = digest_json_stream(self._descriptor)

    def _write(self, path, raw):
        self.budget.before_write(len(raw))
        with path.open('xb') as handle:
            written = handle.write(raw)
            self.budget.wrote(written, 'fact_spool')
            _need(written == len(raw), 'SHORT_SPOOL_WRITE')
            handle.flush()
            os.fsync(handle.fileno())
        self.files.read(path, maximum=self.budget.limits.max_panel_bytes, role='spool_write_verification',
                        expected=hashlib.sha256(raw).hexdigest())

    def _check_reader(self):
        _need(self._active and self._token is _ISSUER, 'EXPIRED_SCOPE')
        _need(type(self.reader) is SourceArtifactReader and self.reader._paths == self._paths
            and self.reader.receipts == self.receipts
            and self.reader._receipts == {row.receipt_id: row for row in self.receipts}
            and digest_json_stream([row.model_dump(mode='json') for row in self.receipts]) == self._receipt_hash,
            'LIVE_READER_CHANGED')
        _need(not self.directory.is_symlink() and
              (self.directory.stat().st_dev, self.directory.stat().st_ino) == self._directory_identity,
              'SPOOL_DIRECTORY_CHANGED')

    @property
    def descriptor(self):
        return deepcopy(self._descriptor)

    def _check_borrows(self):
        for key, (reference, expected) in tuple(self._borrowed.items()):
            value = reference()
            if value is None:
                del self._borrowed[key]
            else:
                _need(digest_json_stream(value.model_dump(mode='json')) == expected,
                      'BORROWED_PANEL_CHANGED')

    def validate_binding(self, bundle, cutoff):
        self.budget.check()
        self._check_reader()
        self._check_borrows()
        _need(bundle is self.bundle and stamp(cutoff) == self.cutoff
              and digest_market_input(bundle) == self.input_hash, 'INPUT_BINDING_CHANGED')
        _need(digest_json_stream(self._descriptor) == self.descriptor_hash, 'DESCRIPTOR_CHANGED')

    def panel(self, day):
        self.budget.check()
        self._check_reader()
        self._check_borrows()
        text = str(day)
        _need(text in self.requested_sessions and text not in self._consumed, 'SESSION_CONSUMPTION_ORDER')
        _need(text == self.requested_sessions[len(self._consumed)], 'SESSION_CONSUMPTION_ORDER')
        self._consumed.append(text)
        if text not in self._spools:
            return None
        reference = self._spools[text]
        raw = self.files.read(reference['path'], maximum=self.budget.limits.max_panel_bytes,
                              role='spool_consumer', expected=reference['sha256'])
        value = json.loads(raw)
        panel = ThemeMarketSession.model_validate(value['panel'])
        expected = self._descriptor['panels'][text]['panel_hash']
        _need(digest_json_stream(panel.model_dump(mode='json')) == expected, 'SPOOL_PANEL_CHANGED')
        self._borrowed[id(panel)] = (weakref.ref(panel), expected)
        # Selected bars outlive their panel for the previous-day return window.
        # Keep weak row guards so discarding the panel cannot hide a late edit.
        for row in panel.rows:
            self._borrowed[id(row)] = (weakref.ref(row), digest_json_stream(row.model_dump(mode='json')))
        self.budget.panels_consumed += 1
        return panel

    def terminal(self):
        self.validate_binding(self.bundle, self.cutoff)
        _need(tuple(self._consumed) == self.requested_sessions, 'INCOMPLETE_SESSION_CONSUMPTION')


@contextmanager
def _owner_meter(validator, reader, bundle, budget, files):
    if bundle.coverage_type == 'TEST_FIXTURE' and validator is None:
        yield lambda: None
        return
    from stockfish_core.yaogu.saved_r2_sources_v2 import AuthenticatedSourceArtifactsV2
    owner = getattr(validator, '__self__', None)
    _need(type(owner) is AuthenticatedSourceArtifactsV2 and
          getattr(validator, '__func__', None) is AuthenticatedSourceArtifactsV2.validate_binding
          and owner._reader is reader, 'GENUINE_SOURCE_OWNER_TERMINAL_REQUIRED')
    registry, original = owner.registry, owner.registry.read
    _need('read' not in registry.__dict__, 'OWNER_ALREADY_METERED')

    def measured(*args, **kwargs):
        path = Path(args[0] if args else kwargs['path']).absolute()
        _need(path.resolve(strict=True) == path and not path.is_symlink(), 'UNALIASED_OWNER_FILE_REQUIRED')
        before = _fp(path.lstat())
        raw = original(*args, **kwargs)
        budget.read(len(raw), 'owner_registry')
        _need(before == _fp(path.lstat()), 'OWNER_FILE_CHANGED_DURING_READ')
        record = dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(), fp=before)
        prior = files.refs.get(str(path))
        _need(prior is None or prior == record, 'OWNER_PHYSICAL_IDENTITY_CHANGED')
        files.refs[str(path)] = record
        files.owner_paths.add(str(path))
        return raw

    def terminal():
        budget.check()
        validator()
        budget.owner_terminal_calls += 1
        budget.check()

    registry.read = measured
    try:
        yield terminal
    finally:
        del registry.read


def _consumer(scope, identity):
    binding = _clone(legacy._binding, {'SourceMarketStream': _ScopeStream,
                                     'MARKET_STREAM_SCHEMA': STREAM_VERSION})
    replacements = {'_binding': binding, 'source_identity': lambda: source_identity(),
        'STRATEGY_ID': 'THEME_EVOLUTION_DAILY_SESSION_PIPELINE_V1',
        'SCHEMA_VERSION': 'DAILY_SESSION_PIPELINE_THEME_CONTEXT_V1'}
    terminal = _clone(consumer._terminal_binding, replacements)

    class BudgetResolver(consumer._RecordingResolver):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            journal = self.rows._journal
            read, append = journal._read, journal._append

            def measured_read(*a, **kw):
                raw = read(*a, **kw)
                scope.budget.read(len(raw), 'evidence_journal')
                return raw

            def measured_append(path, handle, raw):
                scope.budget.before_write(len(raw))
                before = handle.tell()
                try:
                    return append(path, handle, raw)
                finally:
                    scope.budget.wrote(handle.tell() - before, 'evidence_journal')

            journal._read, journal._append = measured_read, measured_append

    return _clone(consumer.run_theme_evolution_stream,
        {**replacements, '_terminal_binding': terminal, '_RecordingResolver': BudgetResolver})


def run_daily_session_pipeline(bundle, *, reader, cutoff, directory, limits,
        source_terminal_validator=None, themes=(), membership_mode='CURRENT_COHORT_BACKVIEW',
        output_history=True, checkpoint=None):
    """Build once from RAW, consume frozen V2 FSM, and return a sealed result.

    ``directory`` must be new. Partial owned files remain on failure; all file
    handles close and the capability expires. ``manifest`` is descriptive and
    cannot reopen this scope. Non-fixtures require the genuine source issuer's
    bound ``validate_binding`` method, not a caller-authored success callback.
    """
    _need(type(bundle) is MarketInput and type(reader) is SourceArtifactReader, 'EXACT_INPUT_READER_REQUIRED')
    _need(bundle.coverage_type in ('TEST_FIXTURE', 'PROSPECTIVE_CAPTURED', 'RECONSTRUCTED_HISTORICAL'),
          'UNVERIFIABLE_COVERAGE')
    decision = stamp(cutoff)
    _need(bundle.observation_date <= decision.astimezone(legacy.SH).date(), 'FUTURE_OBSERVATION')
    _need(membership_mode in ('DATED_ONLY', 'CURRENT_COHORT_BACKVIEW') and type(output_history) is bool,
          'INVALID_CONSUMER_OPTIONS')
    destination = Path(directory).absolute()
    _need(not destination.exists() and not destination.is_symlink() and destination.parent.is_dir()
          and destination.parent.resolve(strict=True) == destination.parent, 'NEW_UNALIASED_DIRECTORY_REQUIRED')
    budget, scope = _Budget(limits, checkpoint), None
    files = _Files(budget)
    with _owner_meter(source_terminal_validator, reader, bundle, budget, files) as owner_terminal:
        owner_terminal()
        identity = source_identity()
        for relative, sha in identity['source_file_hashes'].items():
            files.read(consumer.base.ROOT / relative, maximum=8 * MIB, role='source_code', expected=sha)
        # Raw files are pinned before projecting; SHA is checked when the
        # selected full payload is read. Every path (also unused/late parents)
        # is checked by the explicit final source scan below.
        raw_pins = {key: (str(path.absolute()), _fp(path.lstat())) for key, path in reader._paths.items()}
        destination.mkdir(mode=0o700)
        facts = destination / 'facts'
        facts.mkdir(mode=0o700)
        try:
            scope = _ScopeStream(_ISSUER, bundle, reader, decision, facts, budget, files, themes)

            def terminal():
                scope.terminal()
                owner_terminal()
                for key, (path, initial) in raw_pins.items():
                    _need(str(reader._paths[key].absolute()) == path and _fp(Path(path).lstat()) == initial,
                          'ORIGINAL_PHYSICAL_IDENTITY_CHANGED')
                    if path not in files.owner_paths:
                        files.read(path, maximum=limits.max_source_bytes, role='source_terminal',
                                   expected=reader._receipts[key].content_hash)
                raw_paths = {path for path, _ in raw_pins.values()}
                for record in tuple(files.refs.values()):
                    if record['path'] not in raw_paths and record['path'] not in files.owner_paths:
                        files.read(record['path'], maximum=max(record['fp']['st_size'], 1),
                                   role='physical_terminal', expected=record['sha256'])
                _need(source_identity() == identity, 'IMPLEMENTATION_CHANGED')
                scope.terminal()

            result = _consumer(scope, identity)(bundle, cutoff=decision, market_stream=scope,
                evidence_directory=destination / 'evidence', membership_mode=membership_mode,
                output_history=output_history, history_scope='LATEST_67',
                _source_terminal_validator=terminal)
            manifest = {'schema_version': VERSION, 'source_identity': identity,
                'source_input_hash': scope.input_hash, 'decision_cutoff': decision.isoformat(),
                'requested_sessions': list(scope.requested_sessions),
                'available_sessions': list(scope.available_sessions),
                'missing_sessions': scope.descriptor['missing_sessions'],
                'window_status': 'COMPLETE_67_AXIS' if len(scope.requested_sessions) == 67 else 'UNKNOWN_SHORT_CALENDAR',
                'fact_files': deepcopy(scope._spools), 'source_references': deepcopy(list(files.refs.values())),
                'theme_context_content_hash': result['context_content_hash'],
                'restorable_source_capability': False, 'historical_pit_upgraded': False,
                'source_projection_count': budget.projections,
                'status': 'DERIVATIVE_REQUIRES_SUCCESSFUL_CALLER_COMPLETION_RECEIPT'}
            raw = _canonical(manifest)
            budget.before_write(len(raw))
            with (destination / 'manifest.json').open('xb') as handle:
                written = handle.write(raw)
                budget.wrote(written, 'manifest')
                _need(written == len(raw), 'SHORT_MANIFEST_WRITE')
                handle.flush()
                os.fsync(handle.fileno())
            files.read(destination / 'manifest.json', maximum=limits.max_output_bytes,
                       role='manifest_verification', expected=hashlib.sha256(raw).hexdigest())
            terminal()
            budget.check()
            return {'schema_version': VERSION, 'theme': result, 'manifest': manifest,
                    'manifest_path': str(destination / 'manifest.json'), 'metrics': budget.report()}
        except BaseException as error:
            try:
                error.daily_session_pipeline_metrics = budget.report()
                error.daily_session_pipeline_partial_directory = str(destination)
            except BaseException:
                pass
            raise
        finally:
            if scope is not None:
                scope._active = False
