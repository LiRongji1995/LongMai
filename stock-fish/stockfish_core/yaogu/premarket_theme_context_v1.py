"""Explicit current-cohort native theme phase attached to a live core owner.

Ordinary saved JSON, a completed capture, and a Boolean validator cannot mint
this invocation's source authority. Price math and the theme FSM are unchanged.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path

from .history_artifacts import digest_json_stream, digest_market_input
from .industry_sina_context_v1 import IndustrySinaSourceArtifactReader
from .premarket_native_core_v1 import (_owner_state, _typed, _json, _two,
    _BudgetRegistry, _pin_capture_aux)
from .saved_r2_sources_v2 import issue_saved_r2_source_reader, SOURCES_SCHEMA
from .theme_market_stream import SourceMarketStream
from .theme_evolution_streaming_v2 import run_theme_evolution_stream, source_identity as stream_identity
from .theme_observation_diagnostics import build_theme_observation_diagnostics
from .visibility import stamp

VERSION = 'PREMARKET_THEME_CONTEXT_V1'
_MINT = object()


class ThemeContextResult:
    """A display result, deliberately not a source/selector capability."""
    def __init__(self, document, artifacts, validator, validation_references):
        self._document, self.artifacts = deepcopy(document), deepcopy(artifacts)
        self._validator = validator
        self.validation_references = deepcopy(validation_references)

    def to_dict(self):
        return deepcopy(self._document)

    def validate(self):
        """Recheck invocation-local original/stream/journal bindings, not JSON."""
        return self._validator()


class _FreshThemeBinding:
    def __init__(self, token, owner, parent, stream, capability, journal, cutoff, originals, locators):
        if token is not _MINT or type(stream) is not SourceMarketStream:
            raise ValueError('PREMARKET_THEME_GENUINE_NATIVE_BINDER_REQUIRED')
        self.owner, self.parent, self.stream = owner, parent, stream
        self.capability, self.journal, self.cutoff = capability, journal, cutoff
        self.originals, self.locators = originals, locators
        self.parent_hash = digest_json_stream(parent)
        self.input_hash = digest_market_input(_owner_state(owner)['bundle'])
        self.identity = stream_identity()

    def __call__(self, parent, *, bundle, cutoff):
        state = _owner_state(self.owner)
        if (parent is not self.parent or bundle is not state['bundle']
                or stamp(cutoff) != self.cutoff
                or digest_json_stream(parent) != self.parent_hash
                or digest_market_input(bundle) != self.input_hash
                or parent.get('market_stream_descriptor_hash') != self.stream.descriptor_hash
                or parent.get('market_stream_descriptor') != self.stream.descriptor
                or stream_identity() != self.identity):
            raise ValueError('PREMARKET_THEME_LIVE_PARENT_CHANGED')
        self.owner.validate()
        self.originals.terminal()
        for locator in self.locators:
            locator.validate()
        self.capability.validate_binding()
        self.stream.validate_binding(bundle, cutoff)
        for reference in self.journal:
            _, actual = state['registry'].budget.read(reference['path'], expected=reference)
            if actual != reference:
                raise ValueError('PREMARKET_THEME_JOURNAL_CHANGED')
        return True


def attach_theme_context(core_owner, *, context_captures=(), raw_receipt_sources=(),
        context_cutoff, output_dir, budget, requested_industry_nodes=None,
        requested_concept_symbols=None, parent_acquisition=None):
    """Issue original sources once, then compute the full selected cohort scope.

    ``raw_receipt_sources`` supplies cache recipes with full physical ref4 and
    the original typed receipt. After strict reads, refs are projected into the
    genuine SOURCES_V2 schema; no result wrapper is invented. Memberships are a
    backview cohort, not membership that was visible on historical price days.
    A failed theme phase leaves the separately saved core intact and UNKNOWN.
    """
    state = _owner_state(core_owner)  # Refuse JSON/fake owners before creating output.
    core_owner.validate()
    decision = stamp(context_cutoff)
    if decision < stamp(state['run']['decision_cutoff']) or decision > datetime.now(timezone.utc):
        raise ValueError('PREMARKET_THEME_INVALID_CONTEXT_CUTOFF')
    directory = Path(output_dir).resolve()
    if directory.exists():
        raise ValueError('PREMARKET_THEME_OUTPUT_MUST_BE_NEW')
    directory.mkdir(parents=True, exist_ok=False)
    artifacts, journal = {}, []
    originals, locators = _BudgetRegistry(budget), []
    validator = core_owner.validate
    run, bundle = state['run'], state['bundle']
    selection = run['results']['SHORT_HORIZON_STRENGTH_R2']['research_selected_ids']
    document = dict(schema_version=VERSION, run_id=run['run_id'],
        observation_date=run['observation_date'], target_session=run.get('target_session'),
        core_decision_cutoff=run['decision_cutoff'], context_cutoff=decision.isoformat(),
        generated_at=datetime.now(timezone.utc).isoformat(), status='UNKNOWN',
        source_verified=None, source_context=dict(themes=[], unknown=[], counts={
            'requested': None, 'verified': None, 'unknown': None, 'modeled': None}),
        diagnostic=None, parent_acquisition=deepcopy(parent_acquisition),
        scope=dict(kind='INTERSECTION_WITH_FULL_ORIGINAL_SELECTION',
            selected_ids=list(selection), included_theme_ids=[]),
        membership_mode='CURRENT_COHORT_BACKVIEW', historical_PIT_claimed=False,
        event_confirmation='UNKNOWN_NOT_ISSUED', formal_focus=False,
        parameter_status='UNVALIDATED', market_edge='NOT_ESTABLISHED',
        source_capability_restored_from_JSON=False, stock_selection_changed=False)

    def save(name, payload, compressed=False):
        raw = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        if compressed:
            raw = gzip.compress(raw, mtime=0)
        with (directory / name).open('xb') as handle:
            handle.write(raw)
        _, artifacts[name] = budget.read(directory / name,
            expected=hashlib.sha256(raw).hexdigest())

    try:
        budget.check('theme_sources')
        captures = deepcopy(state['captures'])
        seen = {row['typed_receipt']['receipt_id'] for row in captures}
        for capture in context_captures:
            item = capture if isinstance(capture, dict) else capture.metadata
            result = _json(originals.read(item['result']['path'], item['result']))
            physical = _json(originals.read(item['receipt']['path'], item['receipt']))
            originals.read(item['raw']['path'], item['raw'])
            _pin_capture_aux(item, result, originals, bundle.coverage_type, locators)
            typed = _typed(result, physical)
            if typed.receipt_id in seen:
                raise ValueError('PREMARKET_THEME_DUPLICATE_CONTEXT_RECEIPT')
            seen.add(typed.receipt_id)
            captures.append(dict(archive_root=str(item['archive_root']),
                **{key: _two(item[key]) for key in ('result', 'raw', 'receipt')},
                typed_receipt=typed.model_dump(mode='json')))
        raw_sources = deepcopy(list(raw_receipt_sources))
        if any(item['typed_receipt']['receipt_id'] in seen for item in raw_sources):
            raise ValueError('PREMARKET_THEME_DUPLICATE_RAW_RECEIPT')
        for item in raw_sources:
            for role in ('raw', 'receipt'):
                originals.read(item[role]['path'], item[role])
                item[role] = _two(item[role])
        sources = dict(schema_version=SOURCES_SCHEMA, captures=captures,
            raw_receipt_sources=raw_sources, business_sources=[], counted_nodes=[],
            reported_scope=None, extra_registered_files=[])
        save('sources-manifest.json', sources)
        capability = issue_saved_r2_source_reader(
            sources_manifest_path=directory/'sources-manifest.json', cutoff=decision)
        def validate_sources():
            core_owner.validate()
            originals.terminal()
            for locator in locators:
                locator.validate()
            return capability.validate_binding()
        validator = validate_sources
        issued = {row.receipt_id: row for row in capability.receipts}
        # Future calendar coverage-end is schedule metadata, never a price
        # visibility exception. It was already authenticated by the live owner.
        for receipt in bundle.receipts:
            if receipt.dataset == 'calendar':
                continue
            if issued.get(receipt.receipt_id) != receipt:
                raise ValueError('PREMARKET_THEME_CORE_PARENT_NOT_FRESHLY_AUTHENTICATED')
        reader = IndustrySinaSourceArtifactReader(capability.receipts, capability.reader._paths)
        concepts = reader.build_current_theme_context(cutoff=decision)
        symbols = tuple(requested_concept_symbols) if requested_concept_symbols is not None else tuple(sorted({
            row.parameters['symbol'] for row in reader.receipts
            if row.dataset == 'concept_members_sina'}))
        nodes = tuple(requested_industry_nodes) if requested_industry_nodes is not None else tuple(sorted({
            row.parameters['symbol'] for row in reader.receipts
            if row.dataset == 'industry_members_sina'}))
        if len(set(symbols)) != len(symbols) or len(set(nodes)) != len(nodes):
            raise ValueError('PREMARKET_THEME_DUPLICATE_REQUESTED_NODE')
        concept_themes = [row for row in concepts['themes']
            if row['theme_id'].split(':', 1)[-1] in symbols]
        unknown = [row for row in concepts.get('unknown', ())
            if row.get('symbol') in symbols or (symbols and row.get('symbol') is None)]
        if len(nodes) > 32:
            industry = dict(themes=[], status='UNKNOWN', requested_nodes=list(nodes),
                unknown=[dict(node=node, reason='REQUESTED_INDUSTRY_SCOPE_EXCEEDS_NATIVE_32_NODE_BOUND_NOT_TRUNCATED')
                         for node in nodes])
        else:
            industry = reader.build_current_industry_context(nodes=nodes, cutoff=decision) if nodes else {
                'themes': [], 'unknown': [], 'status': 'UNKNOWN', 'requested_nodes': []}
        themes = concept_themes + industry['themes']
        unknown += industry.get('unknown', [])
        requested_ids = {'concept_list_sina:'+symbol for symbol in symbols} | {
            'industry_list_sina:'+node for node in nodes}
        qualification_ids = {'concept_list_sina:'+row['symbol'] for row in
            concepts.get('qualification_categories', ())} & requested_ids
        unknown_ids = sorted(requested_ids - {row['theme_id'] for row in themes} - qualification_ids)
        document['source_verified'] = True
        document['source_context'] = dict(themes=themes, unknown=unknown,
            concepts=concepts, industry=industry, unknown_theme_ids=unknown_ids,
            excluded_qualification_theme_ids=sorted(qualification_ids),
            counts=dict(requested=len(requested_ids), verified=len(themes), unknown=len(unknown_ids), modeled=0))
        selected_set = set(selection)
        scoped = [row for row in themes if selected_set.intersection(row['member_ids'])]
        document['scope'].update(included_theme_ids=[row['theme_id'] for row in scoped],
            excluded_theme_ids=[row['theme_id'] for row in themes if row not in scoped],
            model_ready_cohort_count=len(themes), modeled_cohort_count=0,
            economic_theme_coverage='UNKNOWN_SOURCE_COHORTS_ARE_NOT_COMPLETE_ECONOMIC_TAXONOMY')
        sessions = tuple(str(day) for day in bundle.sessions if day <= bundle.observation_date)[-67:]
        if len(sessions) != 67:
            document['reason_codes'] = ['VERIFIED_CALENDAR_SHORTER_THAN_67_SESSIONS']
        elif not scoped:
            document['reason_codes'] = ['NO_VERIFIED_COHORT_INTERSECTS_ORIGINAL_SELECTION']
        else:
            budget.check('theme_stream')
            stream = SourceMarketStream.from_reader(reader, bundle, cutoff=decision,
                sessions=sessions, themes=scoped)
            parent = run_theme_evolution_stream(bundle, cutoff=decision, market_stream=stream,
                evidence_directory=directory/'price-journal', membership_mode='CURRENT_COHORT_BACKVIEW',
                output_history=True, history_scope='LATEST_67',
                _source_terminal_validator=validate_sources)
            if parent.get('computed_exchange_sessions') != list(sessions):
                raise ValueError('PREMARKET_THEME_REQUESTED_67_SCOPE_CHANGED')
            audit = parent['execution_provenance']['audit_manifest']
            actual_names = {path.name for path in (directory/'price-journal').iterdir()}
            if actual_names != set(audit['files']) | {'manifest.json'}:
                raise ValueError('PREMARKET_THEME_JOURNAL_SCOPE_CHANGED')
            for name, declared in audit['files'].items():
                if Path(name).name != name:
                    raise ValueError('PREMARKET_THEME_UNSAFE_JOURNAL_NAME')
                raw, ref = budget.read(directory/'price-journal'/name, expected=declared['sha256'])
                if len(raw) != declared['bytes']:
                    raise ValueError('PREMARKET_THEME_JOURNAL_BYTES_CHANGED')
                journal.append(ref)
            raw, ref = budget.read(directory/'price-journal/manifest.json')
            if _json(raw) != audit or audit['complete_allowed_row_audit'] is not True:
                raise ValueError('PREMARKET_THEME_JOURNAL_NOT_COMPLETE')
            journal.append(ref)
            binding = _FreshThemeBinding(_MINT, core_owner, parent, stream, capability,
                journal, decision, originals, locators)

            def selection_validator(ids, *, bundle, cutoff):
                live = _owner_state(core_owner)
                if (bundle is not live['bundle'] or stamp(cutoff) != decision
                        or tuple(ids) != tuple(sorted(selection))):
                    raise ValueError('PREMARKET_THEME_SELECTION_CHANGED')
                return core_owner.validate()

            diagnostic = build_theme_observation_diagnostics(bundle, parent, cutoff=decision,
                selected_ids=selection, _theme_context_validator=binding,
                _selection_validator=selection_validator)
            binding(parent, bundle=bundle, cutoff=decision)
            validator = lambda: binding(parent, bundle=bundle, cutoff=decision)
            document.update(diagnostic=diagnostic, status='PARTIAL' if unknown_ids or unknown else 'AVAILABLE')
            document['scope']['modeled_cohort_count'] = len(scoped)
            document['source_context']['counts']['modeled'] = len(scoped)
            save('native-theme-parent.json.gz', parent, compressed=True)
        validator()
        budget.check('theme_complete')
    except Exception as error:
        # Preserve the actual failure and all partial evidence without turning
        # an unfinished native parent into a usable diagnostic or empty market.
        document.update(status='FAILED', diagnostic=None,
            failure=dict(type=type(error).__name__, message=str(error),
                failed_at=datetime.now(timezone.utc).isoformat()),
            native_confirmation='UNKNOWN', native_mainline_count=None)
        if document['source_verified'] is None:
            document['source_verified'] = False
    document['completed_at'] = datetime.now(timezone.utc).isoformat()
    save('theme-context.json', document)
    references = {row['path']: row for row in
        [*core_owner.validation_references, *originals.references.values(), *journal]}
    return ThemeContextResult(document, artifacts, validator, list(references.values()))
