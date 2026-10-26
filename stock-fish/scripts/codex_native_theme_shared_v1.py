"""Fresh native theme delivery using one authenticated projection per session.

Only invocation-local function bindings change. The original source issuer,
selection, cohort qualification, V2 FSM, diagnostics and UNKNOWN gates remain
the native implementations. A completed spool is not a source capability.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
from pathlib import Path
import time
from types import SimpleNamespace

from scripts import codex_daily_session_v1 as pipeline
from stockfish_core.yaogu import industry_sina_context_v1 as industry
from stockfish_core.yaogu import premarket_theme_context_v1 as native
from stockfish_core.yaogu import saved_r2_sources_v2 as issuer
from stockfish_core.yaogu import theme_observation_diagnostics as diagnostics
from stockfish_core.yaogu import short_horizon_archive_bridge_v1 as canonical
from stockfish_core.yaogu.history_artifacts import digest_json_stream, digest_market_input
from stockfish_core.yaogu.premarket_runtime_v1 import Budget
from stockfish_core.yaogu.visibility import stamp

VERSION = 'PREMARKET_NATIVE_THEME_SHARED_V1'
ROOT = pipeline.consumer.base.ROOT
_clone = pipeline._clone


def _need(value, reason):
    if not value:
        raise ValueError(VERSION + '_' + reason)


def source_identity():
    """Bind the complete new execution closure and all unchanged producers."""
    prior = pipeline.source_identity()
    files = dict(prior['source_file_hashes'])
    for identity in (industry.source_identity(), issuer.source_identity(),
                     diagnostics.source_identity()):
        files.update(identity['source_file_hashes'])
    for path in (Path(__file__).resolve(), Path(native.__file__).resolve(),
                 Path(native.__file__).with_name('premarket_native_core_v1.py'),
                 Path(native.__file__).with_name('premarket_runtime_v1.py'),
                 Path(canonical.__file__).resolve()):
        files[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {**prior, 'implementation_version': VERSION,
            'feature_version': 'NATIVE_THEME_SHARED_EXACT_67_V1',
            'spec_revision': 'NATIVE_THEME_SHARED_V1_INLINE_CONTRACT',
            'source_file_hashes': files, 'source_hash': digest_json_stream(files),
            'shared_pipeline_source_identity': prior}


@contextmanager
def _meter(budget):
    # Production and fixtures use the same real, sticky whole-phase budget.
    _need(type(budget) is Budget, 'ORIGINAL_EXECUTION_BUDGET_REQUIRED')
    if budget._meter_active:
        _need(budget.phase_name == 'theme', 'ORIGINAL_THEME_PHASE_REQUIRED')
        yield
    else:
        with budget.phase('theme'), budget.meter_files():
            yield


def _limits(budget):
    remaining = (budget.limits['max_seconds'] - (time.monotonic() - budget.started)
                 if budget.limits['max_seconds'] is not None else 2400)
    return pipeline.PipelineLimits(
        max_seconds=min(remaining, 2400),
        max_read_bytes=min(budget.limits['theme_read_bytes'], 4 * 1024 ** 3),
        max_output_bytes=min(budget.limits['output_bytes'], 512 * pipeline.MIB),
        max_rss_bytes=min(budget.limits['self_rss_bytes'], 2560 * pipeline.MIB))


def _matching_readers(capability, reader):
    _need(type(capability) is issuer.AuthenticatedSourceArtifactsV2 and
          type(reader) is industry.IndustrySinaSourceArtifactReader,
          'GENUINE_ISSUER_AND_INDUSTRY_READER_REQUIRED')
    original = capability._reader
    _need(type(original) is pipeline.SourceArtifactReader and
          reader.receipts == original.receipts and reader._paths == original._paths and
          reader._receipts == original._receipts, 'INDUSTRY_READER_ISSUER_SCOPE_CHANGED')
    reader._terminal_industry_sources()


def _physical(reference):
    path = Path(reference['path'])
    _need(not path.is_symlink() and pipeline._fp(path.lstat()) == reference['fp'],
          'PHYSICAL_IDENTITY_CHANGED')


def _encoders(budget):
    """Original C canonical encoder; full values are hashed on every call."""
    check = lambda: budget.check('canonical_full_hash')
    return {'digest_json_stream': lambda value: canonical.digest_json_stream(value, checkpoint=check),
            'digest_market_input': lambda value: canonical.digest_market_input(value, checkpoint=check)}


def _run_shared(prepared, capability, *, cutoff, directory, evidence_directory,
                budget, terminal):
    """Adapt the exact industry subtype without weakening the original issuer."""
    reader, bundle = prepared.reader, prepared.bundle
    _matching_readers(capability, reader)
    encoders = _encoders(budget)
    def clone(function, replacements):
        return _clone(function, {**encoders, **replacements})
    projected = _clone(pipeline.fusion.project_session_v1,
        {'SourceArtifactReader': industry.IndustrySinaSourceArtifactReader})
    fusion = SimpleNamespace(**{**vars(pipeline.fusion), 'project_session_v1': projected})

    class IndustryScope(pipeline._ScopeStream):
        __init__ = clone(pipeline._ScopeStream.__init__,
            {'fusion': fusion, 'source_identity': source_identity})
        _check_reader = clone(pipeline._ScopeStream._check_reader,
            {'SourceArtifactReader': industry.IndustrySinaSourceArtifactReader})
        _check_borrows = clone(pipeline._ScopeStream._check_borrows, {})
        validate_binding = clone(pipeline._ScopeStream.validate_binding, {})
        panel = clone(pipeline._ScopeStream.panel, {})

    captured = {}
    class Files(pipeline._Files):
        def __init__(self, local_budget):
            super().__init__(local_budget)
            _need(not captured, 'SINGLE_PIPELINE_FILE_REGISTRY_REQUIRED')
            captured['files'] = self

    @contextmanager
    def owner_meter(validator, actual_reader, actual_bundle, local_budget, files):
        _need(actual_reader is reader and actual_bundle is bundle and
              getattr(validator, '__self__', None) is capability and
              getattr(validator, '__func__', None) is issuer.AuthenticatedSourceArtifactsV2.validate_binding,
              'GENUINE_TERMINAL_BINDING_REQUIRED')
        # The genuine capability keeps its exact base reader. The industry
        # reader borrows only its identical, fully authenticated registry.
        with pipeline._owner_meter(validator, capability._reader, bundle,
                                    local_budget, files) as original_terminal:
            def validate():
                _matching_readers(capability, reader)
                original_terminal()
                _matching_readers(capability, reader)
            yield validate

    consumer_factory = _clone(pipeline._consumer,
        {'_ScopeStream': IndustryScope, 'source_identity': source_identity, '_clone': clone})

    def consumer(scope, identity):
        run = consumer_factory(scope, identity)
        def execute(*args, **kwargs):
            kwargs['evidence_directory'] = evidence_directory
            return run(*args, **kwargs)
        return execute

    run = clone(pipeline.run_daily_session_pipeline,
        {'SourceArtifactReader': industry.IndustrySinaSourceArtifactReader,
         '_ScopeStream': IndustryScope, '_owner_meter': owner_meter,
         '_consumer': consumer, '_Files': Files, 'source_identity': source_identity})
    result = run(bundle, reader=reader, cutoff=cutoff, directory=directory,
        limits=_limits(budget), source_terminal_validator=capability.validate_binding,
        themes=prepared.themes, membership_mode='CURRENT_COHORT_BACKVIEW', output_history=True,
        checkpoint=lambda: budget.check('native_shared_theme'))
    terminal()  # Native core and context locators also finish before publication.
    # These bytes remain in this job's output envelope; they are not deleted
    # behind the final validator and cannot restore the expired scope.
    # Capture the live registry after its complete final scan, including the
    # manifest's original 7FP. Re-reading cannot improve these already checked
    # refs and would allow an unpinned replacement before its first registration.
    references = [{**deepcopy(reference), 'bytes': reference['fp']['st_size']}
                  for reference in captured['files'].refs.values()]
    prepared.result, prepared.references = result, references
    return result['theme']


def attach_theme_context_shared(core_owner, *, context_captures=(), raw_receipt_sources=(),
        context_cutoff, output_dir, budget, requested_industry_nodes=(),
        requested_concept_symbols=(), parent_acquisition=None):
    """Original native interface, with a fresh issuer and shared 67-day facts.

    ``None`` retains the legacy explicit infer-from-captured-nodes behavior;
    an empty tuple requests no nodes. No stored manifest grants execution.
    """
    native._owner_state(core_owner)  # Refuse fake owners before any output.
    with _meter(budget):
        destination = Path(output_dir).absolute()
        _need(budget.job_dir is not None and destination == destination.resolve() and
              destination.is_relative_to(Path(budget.job_dir).resolve()),
              'OUTPUT_MUST_BELONG_TO_WHOLE_BUDGET_JOB')
        identity = source_identity()
        code_refs = [budget.read(ROOT / name, expected=sha)[1]
                     for name, sha in identity['source_file_hashes'].items()]
        live = {'capability': None, 'prepared': None, 'building': True,
                'binding': None, 'validated_refs': {}}
        encoders = _encoders(budget)
        full_json = encoders['digest_json_stream']
        full_input = encoders['digest_market_input']

        def issue(**kwargs):
            _need(live['capability'] is None, 'SINGLE_FRESH_ISSUANCE_REQUIRED')
            value = issuer.issue_saved_r2_source_reader(**kwargs)
            live['capability'] = value
            return value

        class Prepared:
            @classmethod
            def from_reader(cls, reader, bundle, *, cutoff, sessions, themes):
                _need(live['prepared'] is None, 'SINGLE_NATIVE_THEME_SCOPE_REQUIRED')
                _matching_readers(live['capability'], reader)
                expected = tuple(str(day) for day in bundle.sessions if day <= bundle.observation_date)[-67:]
                _need(len(expected) == 67 and tuple(sessions) == expected,
                      'ORIGINAL_67_SESSION_AXIS_REQUIRED')
                value = cls()
                value.reader, value.bundle, value.cutoff = reader, bundle, stamp(cutoff)
                value.themes, value.sessions = deepcopy(themes), expected
                value.result, value.references = None, []
                live['prepared'] = value
                return value

        def run(bundle, *, cutoff, market_stream, evidence_directory, membership_mode,
                output_history, history_scope, _source_terminal_validator):
            _need(type(market_stream) is Prepared and market_stream is live['prepared'] and
                  market_stream.bundle is bundle and market_stream.cutoff == stamp(cutoff) and
                  membership_mode == 'CURRENT_COHORT_BACKVIEW' and output_history is True and
                  history_scope == 'LATEST_67', 'ORIGINAL_NATIVE_CONSUMER_SCOPE_REQUIRED')
            return _run_shared(market_stream, live['capability'], cutoff=cutoff,
                directory=Path(evidence_directory).parent / 'shared-session',
                evidence_directory=evidence_directory, budget=budget,
                terminal=_source_terminal_validator)

        class Binding:
            def __init__(self, token, owner, parent, stream, capability, journal,
                         cutoff, originals, locators):
                _need(token is native._MINT and owner is core_owner and
                      type(stream) is Prepared and stream is live['prepared'] and
                      capability is live['capability'] and stream.result is not None and
                      parent is stream.result['theme'], 'LIVE_NATIVE_PARENT_REQUIRED')
                self.parent, self.parent_hash = parent, full_json(parent)
                self.stream, self.capability, self.cutoff = stream, capability, stamp(cutoff)
                self.originals, self.locators = originals, locators
                self.journal = deepcopy(journal)
                self.input_hash = full_input(stream.bundle)
                live['binding'] = self

            def models(self, parent, bundle, cutoff):
                _need(parent is self.parent and bundle is self.stream.bundle and
                      bundle is native._owner_state(core_owner)['bundle'] and
                      stamp(cutoff) == self.cutoff and full_json(parent) == self.parent_hash and
                      full_input(bundle) == self.input_hash, 'LIVE_PARENT_CHANGED')

            def __call__(self, parent, *, bundle, cutoff):
                self.models(parent, bundle, cutoff)
                references = {row['path']: row for row in [*code_refs, *self.stream.references,
                    *self.journal, *core_owner.validation_references, *self.originals.references.values()]}
                if live['building']:
                    # The completed pipeline just authenticated these exact
                    # originals. Diagnostic construction borrows only parent
                    # values; it retains value/7FP checks and gets one complete
                    # fresh source terminal below before any result is returned.
                    for reference in references.values():
                        _physical(reference)
                    return True
                # Scope capabilities expire inside the pipeline. Re-authenticate
                # genuine originals and every owned derivative after it returns.
                _need(source_identity() == identity, 'IMPLEMENTATION_CHANGED')
                core_owner.validate()
                self.originals.terminal()
                for locator in self.locators:
                    locator.validate()
                self.capability.validate_binding()
                _matching_readers(self.capability, self.stream.reader)
                original_paths = (set(native._owner_state(core_owner)['registry'].references) |
                    set(self.originals.references) | set(self.capability.registry.files))
                for reference in references.values():
                    if reference['path'] not in original_paths:
                        budget.read(reference['path'], expected=reference)
                    _physical(reference)
                # This map lives only until this one validate() returns. It is
                # not retained proof for a later call, nor a cross-run cache.
                live['validated_refs'] = references
                return True

        attach = _clone(native.attach_theme_context,
            {'VERSION': VERSION, 'issue_saved_r2_source_reader': issue,
             'SourceMarketStream': Prepared, 'run_theme_evolution_stream': run,
             '_FreshThemeBinding': Binding})
        try:
            result = attach(core_owner, context_captures=context_captures,
                raw_receipt_sources=raw_receipt_sources, context_cutoff=context_cutoff,
                output_dir=output_dir, budget=budget,
                requested_industry_nodes=requested_industry_nodes,
                requested_concept_symbols=requested_concept_symbols,
                parent_acquisition=parent_acquisition)
        finally:
            live['building'] = False
        prepared = live['prepared']
        shared_refs = prepared.references if prepared is not None else []
        refs = {}
        for row in [*result.validation_references, *code_refs, *shared_refs, *result.artifacts.values()]:
            previous = refs.setdefault(row['path'], deepcopy(row))
            _need(previous == row, 'CONFLICTING_PHYSICAL_REFERENCE')
        artifacts = deepcopy(result.artifacts)
        if prepared is not None and prepared.result is not None:
            manifest_path = prepared.result['manifest_path']
            artifacts['shared-session/manifest.json'] = deepcopy(refs[manifest_path])
            metrics_path = Path(manifest_path).with_name('execution-metrics.json')
            reference = budget.write_json(metrics_path, {
                'schema_version': VERSION, 'source_identity': identity,
                'pipeline_metrics': prepared.result['metrics'],
                'whole_phase_metrics': budget.metrics,
                'status': result.to_dict()['status'],
                'restorable_source_capability': False})
            refs[reference['path']] = reference
            artifacts['shared-session/execution-metrics.json'] = deepcopy(reference)

        def validate():
            with _meter(budget):
                _need(source_identity() == identity, 'IMPLEMENTATION_CHANGED')
                live['validated_refs'] = {}
                try:
                    result.validate()
                    for reference in refs.values():
                        verified = live['validated_refs'].get(reference['path'])
                        if verified is None:
                            budget.read(reference['path'], expected=reference)
                        else:
                            _need(verified == reference, 'VALIDATION_REFERENCE_CHANGED')
                    for reference in refs.values():
                        _physical(reference)
                    if live['binding'] is not None:
                        binding = live['binding']
                        binding.models(binding.parent, binding.stream.bundle, binding.cutoff)
                    budget.check('shared_native_terminal', force=True)
                    return True
                finally:
                    live['validated_refs'] = {}

        validate()
        return native.ThemeContextResult(result.to_dict(), artifacts, validate, list(refs.values()))
