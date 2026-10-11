"""Explicit complete saved-R2 delivery from freshly authenticated source bytes.

The frozen selection is never rerun. A genuine original-byte stream issues this
invocation's new context; saved JSON and external validators grant no authority.
The previous trio-based coordinator remains unchanged.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .history_artifacts import digest_json_stream, digest_market_input
from .visibility import stamp
from . import saved_r2_delivery as base
from .saved_r2_sources_v2 import (SOURCES_SCHEMA, AuthenticatedSourceArtifactsV2,
    issue_saved_r2_source_reader)
from . import saved_r2_sources_v2, theme_evolution_streaming, theme_market_stream

VERSION = 'SAVED_R2_COMPLETE_STREAM_DELIVERY_V3'
FEATURE_VERSION = 'SAVED_R2_COMPLETE_STREAM_DELIVERY_PROJECTION_V3'
SOURCE_REVISION = 1
MAX_FILE_BYTES = base.MAX_FILE_BYTES
MAX_OUTPUT_BYTES = base.MAX_OUTPUT_BYTES
CONTROL_RESERVE_BYTES = 1024 * 1024
CATEGORIES = base.CATEGORIES
_ISSUER = object()
_require, _strict, _json = base._require, base._strict, base._json
_Registry, _freeze_run = base._Registry, base._freeze_run
_complete_groups, _model_text = base._complete_groups, base._model_text


def source_identity():
    from .economic_direction_explanation_v1 import source_identity as economic_identity
    from .theme_observation_diagnostics import source_identity as diagnostic_identity
    root = Path(__file__).resolve().parents[2]
    hashes = dict(base.source_identity()['source_file_hashes'])
    for identity in (saved_r2_sources_v2.source_identity(), theme_evolution_streaming.source_identity(),
                     theme_market_stream.source_identity(), economic_identity(), diagnostic_identity()):
        for name, sha in identity['source_file_hashes'].items():
            _require(name not in hashes or hashes[name] == sha, 'IMPLEMENTATION_DEPENDENCY_CONFLICT')
            hashes[name] = sha
    for path in (Path(__file__), root / 'docs/SAVED_R2_COMPLETE_STREAM_DELIVERY_V3.md'):
        hashes[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return dict(version=VERSION, feature_version=FEATURE_VERSION, source_revision=SOURCE_REVISION,
        source_file_hashes=hashes, source_hash=digest_json_stream(hashes),
        sources_schema=SOURCES_SCHEMA, parameter_status='UNVALIDATED',
        market_edge='NOT_ESTABLISHED', default_strategy_changed=False)


def _tree_bytes(directory):
    total = 0
    if not directory.exists():
        return total
    for path in directory.rglob('*'):
        _require(not path.is_symlink(), 'OUTPUT_SYMLINK_NOT_ALLOWED')
        if path.is_file():
            size = path.stat().st_size
            _require(size <= MAX_FILE_BYTES, 'OUTPUT_FILE_BYTE_BUDGET_EXCEEDED')
            total += size
        else:
            _require(path.is_dir(), 'OUTPUT_SCOPE_INVALID')
    return total


def _check_output(directory, *, reserve=0):
    _require(_tree_bytes(directory) + reserve <= MAX_OUTPUT_BYTES, 'OUTPUT_BYTE_BUDGET_EXCEEDED')


class _OriginalBindings:
    def __init__(self, registry, capability, identity):
        self.registry, self.capability, self.identity = registry, capability, identity
        self.registry_hash = digest_json_stream(registry.files)

    def validate(self):
        _require(type(self.capability) is AuthenticatedSourceArtifactsV2, 'GENUINE_SOURCE_ISSUER_REQUIRED')
        _require(source_identity() == self.identity
            and digest_json_stream(self.registry.files) == self.registry_hash,
            'ORIGINAL_BINDING_OR_IMPLEMENTATION_CHANGED')
        unique = dict(self.registry.files)
        for path, record in self.capability.registry.files.items():
            _require(path not in unique or unique[path] == record, 'ORIGINAL_REGISTRY_CONFLICT')
            unique[path] = record
        _require(sum(record['st_size'] for record in unique.values()) <= base.MAX_TOTAL_SOURCE_BYTES,
            'TOTAL_SOURCE_BYTE_BUDGET_EXCEEDED')
        self.registry.terminal()
        self.capability.validate_binding()


class _JournalBinding:
    """Seal the actual producer journal, including its own whole manifest."""
    def __init__(self, directory, parent):
        self.directory, self.registry = directory, _Registry()
        self.audit = deepcopy(parent['execution_provenance']['audit_manifest'])
        expected = self.audit['files']
        _require(isinstance(expected, dict) and all(Path(name).name == name
            and name not in ('', '.', '..', 'manifest.json') for name in expected), 'NATIVE_JOURNAL_SCOPE_INVALID')
        self.names = set(expected) | {'manifest.json'}
        _require({path.name for path in directory.iterdir()} == self.names,
            'NATIVE_JOURNAL_SCOPE_INVALID')
        for name, value in expected.items():
            _strict(value, ('bytes', 'sha256'), 'NATIVE_JOURNAL_FILE_DECLARATION_INVALID')
            _require(type(value['bytes']) is int and 0 <= value['bytes'] <= MAX_FILE_BYTES,
                'NATIVE_JOURNAL_FILE_BYTES_INVALID')
            # Empty index/segment files are genuine for a bounded empty audit.
            path = directory / name
            if value['bytes'] == 0:
                _require(path.is_file() and not path.is_symlink() and path.read_bytes() == b''
                    and value['sha256'] == hashlib.sha256(b'').hexdigest(), 'NATIVE_JOURNAL_EMPTY_FILE_CHANGED')
                s = path.stat()
                self.registry.files[str(path.resolve())] = dict(path=str(path.resolve()), sha256=value['sha256'],
                    **{key: getattr(s, key) for key in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')})
            else:
                raw = self.registry.read(path, value['sha256'], MAX_FILE_BYTES)
                _require(len(raw) == value['bytes'], 'NATIVE_JOURNAL_FILE_BYTES_MISMATCH')
        manifest = _json(self.registry.read(directory / 'manifest.json', limit=MAX_FILE_BYTES))
        _require(manifest == self.audit and self.audit['complete_allowed_row_audit'] is True,
            'NATIVE_JOURNAL_MANIFEST_MISMATCH')
        self.registry_hash = digest_json_stream(self.registry.files)

    def validate(self):
        _require({path.name for path in self.directory.iterdir()} == self.names
            and digest_json_stream(self.registry.files) == self.registry_hash, 'NATIVE_JOURNAL_SCOPE_CHANGED')
        for path, expected in self.registry.files.items():
            if expected['st_size'] == 0:
                s = Path(path).stat()
                _require({key: getattr(s, key) for key in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')} ==
                    {key: expected[key] for key in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')}
                    and Path(path).read_bytes() == b'', 'NATIVE_JOURNAL_EMPTY_FILE_CHANGED')
            else:
                self.registry.read(path, expected['sha256'], MAX_FILE_BYTES)


class _FreshStreamThemeBinding:
    def __init__(self, token, parent, bundle, cutoff, stream, originals, journal, identity):
        _require(token is _ISSUER and type(stream) is theme_market_stream.SourceMarketStream,
            'GENUINE_STREAM_NATIVE_BINDER_REQUIRED')
        self.parent, self.bundle, self.cutoff = parent, bundle, cutoff
        self.stream, self.originals, self.journal, self.identity = stream, originals, journal, identity
        self.parent_hash, self.input_hash = digest_json_stream(parent), digest_market_input(bundle)
        self.descriptor_hash = stream.descriptor_hash

    def __call__(self, parent, *, bundle, cutoff):
        _require(parent is self.parent and bundle is self.bundle and stamp(cutoff) == self.cutoff
            and digest_json_stream(parent) == self.parent_hash
            and digest_market_input(bundle) == self.input_hash
            and self.stream.descriptor_hash == self.descriptor_hash
            and parent.get('market_stream_descriptor_hash') == self.descriptor_hash
            and parent.get('market_stream_descriptor') == self.stream.descriptor
            and source_identity() == self.identity, 'LIVE_NATIVE_PARENT_OR_IDENTITY_CHANGED')
        self.stream.validate_binding(bundle, cutoff)
        self.originals.validate()
        self.journal.validate()
        return True


def _theme_diagnostic_stream(bundle, binding, selected, capability, originals, identity, cutoff,
        membership_mode, evidence_directory):
    from . import theme_observation_diagnostics
    from .research_context_artifacts import RETURN_BASIS
    default = {key: [] for key in CATEGORIES}
    default.update(model_only_selected_ids=list(binding['selected_ids']), context_status='UNKNOWN',
        reason_codes=['THEME_ORIGINAL_SOURCE_CONTEXT_UNAVAILABLE'])
    dependencies = [str(day) for day in bundle.sessions if day <= bundle.observation_date][-67:]
    scope = dict(calendar_dependencies=dependencies, requested_history_scope='LATEST_67',
        required_exchange_session_count=67, actual_calendar_dependency_count=len(dependencies),
        missing_sessions_not_compressed=True, prior60_missing_is_UNKNOWN=True,
        native_market_stream_calls=0)
    if len(dependencies) < 67:
        scope['reason_codes'] = ['ORIGINAL_CALENDAR_WARMUP_SHORTER_THAN_67']
    reader = capability.reader
    if not selected:
        return default, None, scope, 0, None
    if membership_mode == 'CURRENT_COHORT_BACKVIEW':
        current = reader.build_current_theme_context(cutoff=cutoff)
        themes = current['themes']
        scope['current_membership_context'] = current
    else:
        authorized, themes, missing = {row.receipt_id: row for row in reader.receipts}, [], []
        for row in bundle.themes:
            absent = sorted(set(row.source_refs) - set(authorized))
            incomplete = [dict(receipt_id=ref, status=authorized[ref].status,
                pagination_complete=authorized[ref].pagination_complete) for ref in row.source_refs
                if ref in authorized and (authorized[ref].status != 'COMPLETE' or not authorized[ref].pagination_complete)]
            late = any(getattr(row, key) > cutoff for key in
                ('event_time','source_published_at','first_seen_at','ingested_at'))
            if absent or not row.source_refs or incomplete or late:
                missing.append(dict(theme_id=row.theme_id, status='UNKNOWN', unavailable_source_refs=absent,
                    incomplete_source_refs=incomplete, reason_codes=[
                        *(['DATED_THEME_REQUIRED_ORIGINAL_REFS_UNAVAILABLE'] if absent or not row.source_refs else []),
                        *(['DATED_THEME_REQUIRED_ORIGINAL_PARENTS_INCOMPLETE'] if incomplete else []),
                        *(['DATED_THEME_NOT_VISIBLE_AT_CONTEXT_CUTOFF'] if late else [])]))
            else:
                themes.append(row.model_dump(mode='json'))
        scope['dated_theme_missing_sources'] = missing
    if not themes:
        default['reason_codes'] = ['DATED_OR_CURRENT_THEME_MEMBERSHIP_UNAVAILABLE']
        return default, None, scope, 0, None
    stream = theme_market_stream.SourceMarketStream.from_reader(reader, bundle, cutoff=cutoff,
        sessions=dependencies, themes=themes, return_basis=RETURN_BASIS)
    _require(type(stream) is theme_market_stream.SourceMarketStream, 'GENUINE_NATIVE_STREAM_REQUIRED')
    scope.update(native_market_stream_calls=1, original_available_dates=list(stream.available_sessions),
        unavailable_dates=[day for day in dependencies if day not in stream.available_sessions],
        stream_descriptor_hash=stream.descriptor_hash,
        source_scope_validation='GENUINE_WHOLE_RAW_DATED_MASTER_NATIVE_CONSUMER')
    # No producer is needed when not one whole dated market panel is usable.
    if not stream.available_sessions:
        return default, None, scope, 0, None
    parent = theme_evolution_streaming.run_theme_evolution_stream(bundle, cutoff=cutoff, market_stream=stream,
        evidence_directory=evidence_directory, membership_mode=membership_mode,
        output_history=True, history_scope='LATEST_67')
    _require(parent.get('computed_exchange_sessions') == dependencies
        and parent.get('requested_history_scope') == 'LATEST_67'
        and parent.get('requested_output_scope') == 'ALL_BOUNDED_HISTORY',
        'NATIVE_REQUESTED_WARMUP_SCOPE_CHANGED')
    _check_output(evidence_directory.parents[1], reserve=CONTROL_RESERVE_BYTES)
    journal = _JournalBinding(evidence_directory, parent)
    binder = _FreshStreamThemeBinding(_ISSUER, parent, bundle, cutoff, stream, originals, journal, identity)
    selection = base._FrozenSelectionBinding(base._ISSUER, binding, selected, originals.registry)
    diagnostic = theme_observation_diagnostics.build_theme_observation_diagnostics(bundle, parent,
        cutoff=cutoff, selected_ids=binding['selected_ids'], _theme_context_validator=binder,
        _selection_validator=selection)
    return diagnostic, parent, scope, 1, journal


def deliver_saved_r2_stream(*, run_path, run_manifest_path, input_path, sources_manifest_path,
                     output, context_cutoff, membership_mode='DATED_ONLY', as_of=None):
    """Generate complete same-run groups and explanations, then archived display.

    A manifest pins files and declarations; only this invocation's reader and
    producer return are live context authority. Saved selector authority is not
    reissued. Missing context retains all original observation rows unchanged.
    """
    from .research_context_artifacts import SourceArtifactReader
    from .economic_direction_explanation_v1 import RegisteredFile, build_economic_direction_explanation
    from .economic_direction_display import save_archived_economic_direction_display
    cutoff = stamp(context_cutoff)
    _require(cutoff <= datetime.now(timezone.utc), 'FUTURE_CONTEXT_CUTOFF')
    explicit_as_of = stamp(as_of) if as_of is not None else None
    _require(explicit_as_of is None or explicit_as_of <= datetime.now(timezone.utc), 'FUTURE_DISPLAY_AS_OF')
    _require(membership_mode in ('DATED_ONLY', 'CURRENT_COHORT_BACKVIEW'), 'MEMBERSHIP_MODE_INVALID')
    directory = Path(output)
    _require(not directory.exists(), 'OUTPUT_MUST_BE_NEW')
    identity, registry = source_identity(), _Registry()
    bundle, binding, selected, exports, original_visible_at, original_calendar = _freeze_run(
        registry, run_path, run_manifest_path, input_path, sources_manifest_path, cutoff)
    if explicit_as_of is not None and explicit_as_of < original_visible_at:
        # Archive inspection is private verification, not visibility permission.
        # Do not persist candidate exports, IDs/counts, or newly issued context.
        registry.terminal()
        _require(source_identity() == identity, 'IMPLEMENTATION_CHANGED')
        directory.mkdir(parents=True, exist_ok=False)
        hidden = dict(version=VERSION, feature_version=FEATURE_VERSION, status='NOT_YET_VISIBLE',
            display_as_of=explicit_as_of.isoformat(), original_visible_at=original_visible_at.isoformat(),
            created_at=datetime.now(timezone.utc).isoformat(), original_archive_binding_verified=True,
            candidate_fields_withheld=True, native_theme_producer_calls=0,
            economic_explanation_producer_calls=0, network_DB_SDK_selector_calls=0,
            source_capability_restored_from_JSON=False, selector_reauthenticated=False,
            complete_delivery_claimed=False, market_edge='NOT_ESTABLISHED')
        payloads = {'delivery-manifest.json': json.dumps(hidden, ensure_ascii=False, indent=2).encode('utf-8'),
            'complete-observation-list.txt': 'NOT_YET_VISIBLE：原保存运行在显示截止后才可见；未交付候选内容。\n'.encode('utf-8')}
        files = {}
        for name, raw in payloads.items():
            with (directory / name).open('xb') as handle:
                handle.write(raw)
            files[name] = dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        return dict(status='NOT_YET_VISIBLE', output_directory=str(directory.resolve()),
                    candidate_fields_withheld=True, files=files)
    capability = issue_saved_r2_source_reader(sources_manifest_path=sources_manifest_path, cutoff=cutoff)
    _require(type(capability) is AuthenticatedSourceArtifactsV2, 'GENUINE_SOURCE_ISSUER_REQUIRED')
    originals = _OriginalBindings(registry, capability, identity)
    originals.validate()
    source_audit = capability.audits
    businesses, nodes, extra, reported = capability.businesses, capability.nodes, capability.extra, capability.reported
    directory.mkdir(parents=True, exist_ok=False)
    diagnostic, native_parent, dependencies, native_calls, journal = _theme_diagnostic_stream(
        bundle, binding, selected, capability, originals, identity, cutoff, membership_mode,
        directory / 'NON_DELIVERY_AUDIT/native-evidence')
    dependencies['capture_not_visible_at_context_cutoff'] = [row['receipt_id'] for row in source_audit
        if not row['visible_at_context_cutoff']]
    originals.validate()
    _check_output(directory, reserve=CONTROL_RESERVE_BYTES)
    groups = _complete_groups(diagnostic, selected, binding)
    grouped = dict(schema_version=VERSION, feature_version=FEATURE_VERSION,
        created_at=datetime.now(timezone.utc).isoformat(), new_context_cutoff=cutoff.isoformat(),
        observation_date=binding['observation_date'], target_session=binding['target_session'],
        frozen_run_binding=binding, **groups, V4=diagnostic,
        stock_selection_changed=False, score_or_FOCUS_changed=False,
        optional_limits_LHB_funds={'status': 'UNKNOWN_NOT_REISSUED_BY_DELIVERY'},
        native_context_dependency_scope=dependencies, membership_mode=membership_mode,
        context_status='GENUINE_CURRENT_PROCESS_CONTEXT' if native_calls else 'UNKNOWN_MODEL_FALLBACK',
        source_identity=identity, saved_JSON_grants_source_capability=False,
        saved_selector_reauthenticated=False, parameter_status='UNVALIDATED', market_edge='NOT_ESTABLISHED')
    grouped['attachment_content_hash'] = digest_json_stream(grouped)
    registry.terminal()
    if 'originals' in locals():
        originals.validate()
        if journal is not None:
            journal.validate()
    _require(source_identity() == identity, 'IMPLEMENTATION_CHANGED')
    files, audit_files, output_bytes = {}, {}, 0
    audit_prefix = 'NON_DELIVERY_AUDIT/'
    def save(name, value, *, deliverable=True):
        nonlocal output_bytes
        raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
        output_bytes += len(raw)
        reserve = 0 if name == 'delivery-manifest.json' else CONTROL_RESERVE_BYTES
        _require(len(raw) <= MAX_FILE_BYTES and _tree_bytes(directory) + len(raw) + reserve <= MAX_OUTPUT_BYTES,
            'OUTPUT_BYTE_BUDGET_EXCEEDED')
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as handle:
            handle.write(raw)
        entry = dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        (files if deliverable else audit_files)[name] = entry
        return RegisteredFile(path.resolve(), entry['sha256'])
    for name, raw in exports.items():
        save(name, raw)
    grouped_file = save(audit_prefix + 'grouped.json', grouped, deliverable=False)
    if native_parent is not None:
        save(audit_prefix + 'native-theme-context.json', native_parent, deliverable=False)
    save(audit_prefix + 'source-authentication-audit.json', dict(captures=source_audit, original_registry=registry.files,
        original_source_registry=capability.registry.files,
        source_issuance_schema=SOURCES_SCHEMA, requested_warmup_sessions=dependencies['calendar_dependencies'],
        original_statuses_and_clocks_unchanged=True, source_capability_serialized=False), deliverable=False)
    economic_calls, display_result = 0, None
    display_failure, explanation_delivered = None, False
    display_registry, display_payloads = None, {}
    economic_cutoff = datetime.now(timezone.utc)
    if selected:
        attachment = build_economic_direction_explanation(frozen_grouped=grouped_file,
            business_sources=businesses, counted_nodes=nodes, cutoff=economic_cutoff,
            extra_registered_files=extra, reported_scope=reported)
        economic_calls = 1
        attachment_file = save(audit_prefix + 'economic-direction-explanation.json', attachment, deliverable=False)
        display_as_of = stamp(as_of) if as_of is not None else datetime.now(timezone.utc)
        _require(display_as_of <= datetime.now(timezone.utc), 'FUTURE_DISPLAY_AS_OF')
        try:
            display_result = save_archived_economic_direction_display(attachment_file.path,
                grouped_parent=grouped_file.path, output=directory / audit_prefix / 'display', as_of=display_as_of)
        except ValueError as exc:
            if str(exc) != 'ECONOMIC_DISPLAY_ARCHIVE_CHANGED':
                raise
            # Only a new derived display archive state failure can take this
            # branch. Original whole sources and implementation remain hard
            # guards; no new explanation value is delivered on this path.
            originals.validate()
            if journal is not None:
                journal.validate()
            _require(source_identity() == identity, 'IMPLEMENTATION_CHANGED')
            display_failure = dict(type=type(exc).__name__, message=str(exc))
            old_groups = _complete_groups({}, selected, binding)['groups']
            text, _ = _model_text(binding, old_groups, original_visible_at, display_as_of,
                'NEW_EXPLANATION_FAILED_ARCHIVE_STATE')
            status = 'COMPLETE_SAVED_OBSERVATION_MODEL_FALLBACK'
        if display_result:
            # The genuine producer's returned byte identities close the
            # saved-display -> delivery boundary. A later TXT replacement is
            # an integrity failure, outside the exact archive-state catch.
            from .economic_direction_display import VERSION as DISPLAY_VERSION
            display_directory = directory / audit_prefix / 'display'
            _require(Path(display_result['output_directory']).resolve() == display_directory.resolve()
                and set(display_result['files']) == {'display.json', 'complete-observation-list.txt'}
                and display_result['stock_selection_changed'] is False
                and display_result['source_capability_issued'] is False, 'DISPLAY_RETURN_BINDING_INVALID')
            _require({path.name for path in display_directory.iterdir()} ==
                {'display.json', 'complete-observation-list.txt', 'manifest.json'}
                and all(path.is_file() for path in display_directory.iterdir()), 'DISPLAY_OUTPUT_SCOPE_INVALID')
            display_registry = _Registry()
            for name, expected in display_result['files'].items():
                _strict(expected, ('bytes', 'sha256'), 'DISPLAY_FILE_DECLARATION_INVALID')
                raw = display_registry.read(display_directory / name, expected['sha256'], MAX_FILE_BYTES)
                _require(type(expected['bytes']) is int and len(raw) == expected['bytes'], 'DISPLAY_FILE_BYTES_MISMATCH')
                display_payloads[name] = raw
            display_payloads['manifest.json'] = display_registry.read(display_directory / 'manifest.json', limit=MAX_FILE_BYTES)
            display_document = _json(display_payloads['display.json'])
            display_manifest = _json(display_payloads['manifest.json'])
            consumed = display_document['consumed_archives']
            _strict(consumed, ('explanation', 'grouped_parent'), 'DISPLAY_CONSUMED_ARCHIVE_SCOPE_INVALID')
            for name, registered, byte_count in (
                ('explanation', attachment_file, audit_files[audit_prefix + 'economic-direction-explanation.json']['bytes']),
                ('grouped_parent', grouped_file, audit_files[audit_prefix + 'grouped.json']['bytes'])):
                value = consumed[name]
                _require(isinstance(value, dict) and isinstance(value.get('path'), str)
                    and Path(value['path']).resolve() == registered.path.resolve()
                    and value.get('sha256') == registered.sha256
                    and type(value.get('st_size')) is int and value['st_size'] == byte_count,
                    'DISPLAY_CONSUMED_ARCHIVE_NOT_ORIGINAL_WRITE')
            _require(display_manifest['version'] == DISPLAY_VERSION
                and display_manifest['status'] == display_result['status'] == display_document['status']
                and display_manifest['files'] == display_result['files']
                and display_manifest['consumed_archives'] == display_document['consumed_archives']
                and display_manifest['selection_changed'] is False
                and display_manifest['live_source_capability_restored'] is False
                and display_manifest['network_DB_SDK_selector_calls'] == 0
                and display_document['text'].encode('utf-8') == display_payloads['complete-observation-list.txt'],
                'DISPLAY_MANIFEST_OR_TEXT_BINDING_MISMATCH')
            display_registry.terminal()
        if display_result and display_result['status'] == 'NOT_YET_VISIBLE':
            # Old display times may use only the saved run, not new group facts.
            old_groups = _complete_groups({}, selected, binding)['groups']
            text, status = _model_text(binding, old_groups, original_visible_at, display_as_of, 'NEW_EXPLANATION_NOT_YET_VISIBLE')
        elif display_result:
            text = display_payloads['complete-observation-list.txt'].decode('utf-8')
            status = 'COMPLETE_SAVED_OBSERVATION_DELIVERED'
            explanation_delivered = True
    else:
        _require(not businesses and not nodes and reported is None, 'EMPTY_SELECTION_CANNOT_HAVE_SELECTED_EXPLANATIONS')
        display_as_of = stamp(as_of) if as_of is not None else datetime.now(timezone.utc)
        _require(display_as_of <= datetime.now(timezone.utc), 'FUTURE_DISPLAY_AS_OF')
        text, status = _model_text(binding, [], original_visible_at, display_as_of, 'GENUINE_EMPTY_SAVED_OBSERVATION')
    if explanation_delivered:
        # The held objects produced the exact staged bytes consumed by the
        # verified archive reader. Keep pinned audit paths in place; publish
        # separate projections only after complete archived display success.
        save('grouped.json', grouped)
        save('economic-direction-explanation.json', attachment)
        if native_parent is not None:
            save('native-theme-context.json', native_parent)
    else:
        model = dict(schema_version=VERSION, feature_version=FEATURE_VERSION,
            created_at=datetime.now(timezone.utc).isoformat(), observation_date=binding['observation_date'],
            target_session=binding['target_session'], frozen_run_binding=binding,
            **_complete_groups({}, selected, binding),
            context_status='UNKNOWN_FAILED_ARCHIVE_STATE' if display_failure else 'UNKNOWN_MODEL_FALLBACK',
            projection_role='ORIGINAL_SAVED_MODEL_ONLY', new_explanation_values_delivered=False,
            stock_selection_changed=False, score_or_FOCUS_changed=False,
            selected_source_role='FROZEN_SAVED_RUN_NOT_REISSUED_SELECTOR_CAPABILITY')
        model['category_order'] = ['MODEL_ONLY_NOT_THEME_MAINLINE']
        model['native_strength_order'] = 'ORIGINAL_SAVED_MODEL_STAGE_AND_ROW_ORDER'
        model['attachment_content_hash'] = digest_json_stream(model)
        save('grouped.json', model)
    save('complete-observation-list.txt', text.encode('utf-8'))
    registry.terminal()
    if 'originals' in locals():
        originals.validate()
        if journal is not None:
            journal.validate()
    _require(source_identity() == identity, 'IMPLEMENTATION_CHANGED')
    # All derived bytes, including private audit artifacts, share the original
    # byte budget. Failed/hidden explanations never enter delivery files.
    if display_result:
        for name, raw in sorted(display_payloads.items()):
            output_bytes += len(raw)
            _check_output(directory, reserve=CONTROL_RESERVE_BYTES)
            audit_files[audit_prefix + 'display/' + name] = dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
            if explanation_delivered:
                save('display/' + name, raw)
        display_registry.terminal()
    save(audit_prefix + 'audit-manifest.json', dict(role='NON_DELIVERY_AUDIT',
        write_file_hashes=audit_files, write_identities_are_not_current_source_authority=True,
        display_failure=display_failure, explanation_delivery_succeeded=explanation_delivered,
        source_capability_restored_from_JSON=False), deliverable=False)
    context_status = (grouped['context_status'] if explanation_delivered else
        'UNKNOWN_FAILED_ARCHIVE_STATE' if display_failure else 'UNKNOWN_MODEL_FALLBACK')
    delivered_dependencies = dependencies if explanation_delivered else dict(
        status='UNKNOWN', reason_codes=['NEW_EXPLANATION_FAILED_ARCHIVE_STATE' if display_failure else
                                       'NEW_EXPLANATION_NOT_DELIVERED'])
    registry.terminal()
    if 'originals' in locals():
        originals.validate()
        if journal is not None:
            journal.validate()
    _require(source_identity() == identity, 'IMPLEMENTATION_CHANGED')
    result = dict(version=VERSION, feature_version=FEATURE_VERSION, source_revision=SOURCE_REVISION, status=status,
        run_id=binding['run_id'], input_hash=binding['input_hash'], observation_date=binding['observation_date'],
        target_session=binding['target_session'], original_target_calendar_evidence=original_calendar,
        original_decision_cutoff=binding['original_decision_cutoff'], new_context_cutoff=cutoff.isoformat(),
        new_economic_evidence_cutoff=economic_cutoff.isoformat(), display_as_of=display_as_of.isoformat(),
        created_at=datetime.now(timezone.utc).isoformat(), counts=binding['native_counts'], count_summary=binding['counts'],
        observation_count=len(selected), frozen_selected_ids=binding['selected_ids'],
        selected_original_items_hash=binding['selected_original_items_hash'],
        source_identity=identity, original_run_source_hash=binding['source_hash'],
        native_theme_producer_calls=native_calls, economic_explanation_producer_calls=economic_calls,
        authenticated_source_issuer_calls=1, native_market_stream_calls=dependencies['native_market_stream_calls'],
        requested_warmup_sessions=dependencies['calendar_dependencies'],
        native_history_scope='LATEST_67', original_input_themes_unchanged=True,
        original_raw_physical_receipts_unchanged=True, missing_sessions_not_compressed=True,
        output_budget_includes_native_journal=True,
        network_DB_SDK_selector_calls=0, original_registry_terminal_equal=True,
        saved_selector_reauthenticated=False, source_capability_restored_from_JSON=False,
        stock_selection_changed=False, score_or_FOCUS_changed=False,
        historical_PIT_revalidated=False, complete_cycles=0, market_edge='NOT_ESTABLISHED',
        parameter_status='UNVALIDATED', cross_direction_strength='UNKNOWN',
        context_status=context_status, native_context_dependency_scope=delivered_dependencies,
        new_explanation_status='DELIVERED_ARCHIVED_EXPLANATION' if explanation_delivered else
            'UNKNOWN_FAILED_ARCHIVE_STATE' if display_failure else 'UNKNOWN_NOT_DELIVERED',
        economic_explanation_delivery_succeeded=explanation_delivered, display_failure=display_failure,
        non_delivery_audit_role='NON_DELIVERY_AUDIT_NOT_CANDIDATE_DELIVERY',
        original_counts_not_unknown_candidates=True, files=files)
    save('delivery-manifest.json', result)
    _check_output(directory)
    originals.validate()
    if journal is not None:
        journal.validate()
    _require(source_identity() == identity, 'IMPLEMENTATION_CHANGED')
    return dict(status=status, output_directory=str(directory.resolve()), run_id=binding['run_id'],
                counts=binding['native_counts'], count_summary=binding['counts'], observation_count=len(selected), files=files)
