"""Complete offline delivery of a frozen R2 run and fresh original-byte context.

Saved documents are archive bindings, never restorable source capabilities.
This coordinator does not call a selector, SDK, HTTP client, or business store.
"""
from __future__ import annotations

from stockfish_core.file_integrity import content_equal, content_reference_equal, content_fingerprint, verify_content_reference

from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timezone
import gc
import hashlib
import json
from pathlib import Path
import re

from .contracts import MarketInput, SourceReceipt
from .history_artifacts import digest_json_stream, digest_market_input
from .visibility import stamp

VERSION = 'SAVED_R2_COMPLETE_DELIVERY_V2'
FEATURE_VERSION = 'SAVED_R2_COMPLETE_DELIVERY_PROJECTION_V2'
SOURCE_REVISION = 2
RUN_SCHEMA = 'SAVED_R2_DELIVERY_RUN_MANIFEST_V1'
SOURCES_SCHEMA = 'SAVED_R2_DELIVERY_SOURCES_V1'
STRATEGY = 'SHORT_HORIZON_STRENGTH_R2'
IDENTITY_FIELDS = ('implementation_version', 'spec_revision', 'feature_version', 'source_hash',
                   'config_hash', 'spec_hash', 'selection_policy_hash', 'source_file_hashes')
MAX_FILE_BYTES = 256 * 1024**2
MAX_SOURCE_BYTES = 8 * 1024**2
MAX_OUTPUT_BYTES = 512 * 1024**2
MAX_TOTAL_SOURCE_BYTES = 2 * 1024**3
MAX_CAPTURES = 1024
CATEGORIES = ('confirmed_mainlines', 'fermentation_watch', 'active_divergence_mainlines')
STAGES = {'STRONG_START': '强势启动模型', 'STRONG_CONTINUATION': '强势延续模型',
          'FIRST_PULLBACK': '首次回踩模型'}
_ISSUER = object()


def _require(condition, reason):
    if not condition:
        raise ValueError('SAVED_R2_DELIVERY_' + reason)


def _strict(value, keys, reason):
    _require(isinstance(value, dict) and set(value) == set(keys), reason)


def _pairs(items):
    result = {}
    for key, value in items:
        _require(key not in result, 'DUPLICATE_JSON_KEY')
        result[key] = value
    return result


def _constant(value):
    raise ValueError('SAVED_R2_DELIVERY_NONFINITE_JSON_VALUE')


def _json(raw):
    return json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_constant)


def _hash(value):
    _require(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value), 'SHA256_REQUIRED')
    return value


def _path(value, base):
    _require(isinstance(value, str) and bool(value), 'PATH_REQUIRED')
    path = Path(value)
    return (path if path.is_absolute() else base / path).resolve(strict=True)


class _Registry:
    """Whole-byte and stable-file checks, including terminal rereads."""
    def __init__(self):
        self.files = {}
        self.total_bytes = 0

    def read(self, path, expected=None, limit=MAX_SOURCE_BYTES):
        path = Path(path).resolve(strict=True)
        before = path.stat()
        _require(0 < before.st_size <= limit, 'FILE_SIZE_INVALID')
        fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        state = {key: getattr(before, key) for key in fields}
        with path.open('rb') as handle:
            raw = handle.read(limit + 1)
        after = {key: getattr(path.stat(), key) for key in fields}
        _require(state == after and len(raw) == before.st_size, 'SOURCE_CHANGED')
        sha = hashlib.sha256(raw).hexdigest()
        if expected is not None:
            _require(sha == _hash(expected), 'FILE_SHA_MISMATCH')
        record = dict(path=str(path), sha256=sha, **state)
        prior = self.files.get(str(path))
        _require(prior is None or content_reference_equal(prior, record), 'REGISTERED_SOURCE_CHANGED')
        if prior is None:
            self.total_bytes += len(raw)
            _require(self.total_bytes <= MAX_TOTAL_SOURCE_BYTES, 'TOTAL_SOURCE_BYTE_BUDGET_EXCEEDED')
        self.files.setdefault(str(path), record)
        return raw

    def registered(self, value, base):
        from .economic_direction_explanation_v1 import RegisteredFile
        _strict(value, ('path', 'sha256'), 'REGISTERED_FILE_SCHEMA_INVALID')
        path = _path(value['path'], base)
        self.read(path, value['sha256'])
        return RegisteredFile(path, value['sha256'])

    def terminal(self):
        for key, value in tuple(self.files.items()):
            self.read(key, value['sha256'], max(MAX_SOURCE_BYTES, value['st_size']))


def source_identity():
    root = Path(__file__).resolve().parents[2]
    names = ('saved_r2_delivery.py', 'research_context_artifacts.py', 'theme_evolution.py',
        'theme_observation_diagnostics.py', 'economic_direction_explanation_v1.py',
        'economic_direction_display.py', 'historical_replay.py', 'provider_normalization.py',
        'short_horizon_participation.py', 'short_horizon_strength.py',
        'contracts.py', 'history_artifacts.py', 'visibility.py', 'candidate_service.py')
    files = [Path(__file__).with_name(name) for name in names]
    files.append(root / 'stockfish_core/file_integrity.py')
    files.extend((root / 'scripts/codex_daily_v2.py', root / 'config/theme_evolution_r1.yaml'))
    hashes = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    return dict(version=VERSION, feature_version=FEATURE_VERSION, source_revision=SOURCE_REVISION, source_file_hashes=hashes,
                source_hash=digest_json_stream(hashes), parameter_status='UNVALIDATED',
                market_edge='NOT_ESTABLISHED', default_strategy_changed=False)


def _freeze_run(registry, run_path, manifest_path, input_path, sources_path, cutoff):
    from .candidate_service import CandidateService
    manifest = _json(registry.read(manifest_path))
    _strict(manifest, ('schema_version', 'run_sha256', 'input_sha256',
                      'sources_manifest_sha256', 'binding'), 'RUN_MANIFEST_SCHEMA_INVALID')
    _require(manifest['schema_version'] == RUN_SCHEMA, 'RUN_MANIFEST_VERSION_INVALID')
    binding = manifest['binding']
    _strict(binding, ('strategy_id', 'run_id', 'input_id', 'input_hash', 'observation_date', 'target_session',
        'original_decision_cutoff', 'source_hash', 'counts', 'selected_ids',
        'native_counts', 'selection_policy_id', 'original_strategy_identity',
        'selected_original_items_hash'), 'RUN_BINDING_SCHEMA_INVALID')
    _require(binding['strategy_id'] == STRATEGY, 'R2_STRATEGY_REQUIRED')
    _hash(binding['input_hash']); _hash(binding['source_hash']); _hash(binding['selected_original_items_hash'])
    _require(stamp(binding['original_decision_cutoff']) <= cutoff, 'CONTEXT_PRECEDES_ORIGINAL_RUN')
    _require(binding['target_session'] is None or isinstance(binding['target_session'], str), 'TARGET_SESSION_INVALID')
    if binding['target_session'] is not None:
        date.fromisoformat(binding['target_session'])
    raw = registry.read(run_path, manifest['run_sha256'], MAX_FILE_BYTES)
    run = _json(raw)
    _require(digest_json_stream({key: value for key, value in run.items() if key != 'content_hash'})
             == run.get('content_hash'), 'RUN_CONTENT_HASH_MISMATCH')
    _require(set(run.get('results', {})) == {STRATEGY}, 'SINGLE_SAVED_R2_REQUIRED')
    _require(isinstance(binding['input_id'], str) and binding['input_id'], 'EXPLICIT_INPUT_ID_REQUIRED')
    for key in ('run_id', 'input_id', 'input_hash', 'observation_date', 'source_hash'):
        _require(run.get(key) == binding[key], 'RUN_BINDING_MISMATCH_' + key.upper())
    _require(stamp(run['decision_cutoff']) == stamp(binding['original_decision_cutoff'])
        and run.get('target_session') == binding['target_session'], 'RUN_CLOCK_OR_TARGET_MISMATCH')
    result = run['results'][STRATEGY]
    _require(result.get('strategy_id') == STRATEGY and result.get('full_eligible_pool_retained') is True,
             'COMPLETE_R2_POOL_REQUIRED')
    identity = binding['original_strategy_identity']
    _strict(identity, IDENTITY_FIELDS, 'ORIGINAL_IDENTITY_SCHEMA_INVALID')
    for key in IDENTITY_FIELDS:
        _require(identity[key] == run.get(key) == result.get(key), 'SAVED_TOP_AND_INNER_IDENTITY_MISMATCH_' + key.upper())
    for key in ('source_hash', 'config_hash', 'spec_hash', 'selection_policy_hash'):
        _hash(identity[key])
    _require(isinstance(identity['source_file_hashes'], dict) and identity['source_file_hashes']
        and all(isinstance(key, str) and key and _hash(value) for key, value in identity['source_file_hashes'].items()),
        'SAVED_SOURCE_FILE_IDENTITY_INVALID')
    _require(result.get('input_hash') == binding['input_hash'], 'INNER_INPUT_HASH_MISMATCH')
    for key, expected in (('input_id', binding['input_id']), ('decision_cutoff', run['decision_cutoff'])):
        if key in result:
            _require(result[key] == expected, 'INNER_INPUT_BINDING_MISMATCH_' + key.upper())
    rows = result.get('items')
    _require(isinstance(rows, list), 'FULL_ITEMS_REQUIRED')
    ids = [row.get('instrument_id') for row in rows]
    _require(all(isinstance(iid, str) and re.fullmatch(r'[0-9]{6}\.(SH|SZ|BJ)', iid) for iid in ids)
        and len(ids) == len(set(ids)), 'DUPLICATE_OR_INVALID_POOL_ID')
    eligible_ids = result.get('eligible_pool_ids')
    _require(isinstance(eligible_ids, list) and all(isinstance(iid, str) and iid in ids for iid in eligible_ids)
        and len(eligible_ids) == len(set(eligible_ids)) and set(eligible_ids) == set(ids),
        'ORIGINAL_ELIGIBLE_POOL_IDS_MISMATCH')
    pool_names = {row['instrument_id']: row.get('name') for row in rows}
    _require(not any(row.get('detail_ref') for row in rows), 'SELF_CONTAINED_FULL_ROWS_REQUIRED_NO_DB_DETAIL_REFS')
    _strict(binding['counts'], ('eligible', 'focus', 'watch', 'rejected', 'unknown'), 'COUNTS_SCHEMA_INVALID')
    _require(all(type(value) is int and value >= 0 for value in binding['counts'].values()), 'COUNTS_INVALID')
    count = Counter(row.get('decision_status') for row in rows)
    _require(not set(count) - {'FOCUS', 'WATCH', 'REJECTED', 'UNKNOWN'}, 'DECISION_STATUS_INVALID')
    expected_counts = dict(eligible=len(rows), **{name: count[name.upper()] for name in ('focus', 'watch', 'rejected', 'unknown')})
    _require(binding['counts'] == expected_counts and isinstance(binding['native_counts'], dict)
        and result.get('counts') == binding['native_counts']
        and all(binding['native_counts'].get(key) == value for key, value in expected_counts.items()),
        'FULL_POOL_COUNTS_MISMATCH')
    _require(type(binding['native_counts'].get('evaluated')) is int
        and binding['native_counts']['evaluated'] == len(eligible_ids), 'ORIGINAL_EVALUATED_POOL_COUNT_MISMATCH')
    _require(all(type(row.get('research_selected')) is bool for row in rows), 'SELECTION_BOOL_REQUIRED')
    selected = [row for row in rows if row['research_selected']]
    _require(all(row['decision_status'] in {'WATCH', 'FOCUS'} for row in selected), 'UNKNOWN_OR_REJECTED_SELECTED')
    selected_ids = [row['instrument_id'] for row in selected]
    _require(selected_ids == result.get('research_selected_ids') == binding['selected_ids'], 'COMPLETE_SELECTION_ORDER_MISMATCH')
    _require(digest_json_stream(selected) == binding['selected_original_items_hash'], 'SELECTED_ORIGINAL_ITEMS_MISMATCH')
    selection = run.get('selection_results')
    policy = binding['selection_policy_id']
    _require(isinstance(policy, str) and policy and result.get('selection_policy_id') == policy
        and isinstance(selection, dict) and set(selection) == {policy}, 'OFFICIAL_SELECTION_POLICY_MISMATCH')
    selection_row = selection[policy]
    _require(selection_row.get('strategy_id') == STRATEGY and selection_row.get('selection_policy_id') == policy
        and selection_row.get('selected_ids') == selected_ids and selection_row.get('count') == len(selected_ids),
        'OFFICIAL_SELECTION_MISMATCH')
    if 'input_id' in selection_row:
        _require(selection_row['input_id'] == binding['input_id'], 'SELECTION_INPUT_ID_MISMATCH')
    if 'input_hash' in selection_row:
        _require(selection_row['input_hash'] == binding['input_hash'], 'SELECTION_INPUT_HASH_MISMATCH')
    if 'items' in selection_row:
        _require([row.get('instrument_id') for row in selection_row['items']] == selected_ids, 'SELECTION_ITEM_ORDER_MISMATCH')
    binding = deepcopy(binding)
    binding['selection_input_id_status'] = ('EXPLICIT_SAME_INPUT_ID' if 'input_id' in selection_row
        else 'ABSENT_NATIVE_SCHEMA_BOUND_BY_AUTHENTICATED_SAME_RUN_CONTAINER')

    class ArchivedReader(CandidateService):
        def strategy_experiment(self, run_id):
            _require(run_id == run['run_id'], 'EXPORT_RUN_MISMATCH')
            return run
    service = object.__new__(ArchivedReader)
    exported = {}
    expected = dict(all=ids, observation=selected_ids,
        focus=[row['instrument_id'] for row in rows if row['decision_status'] == 'FOCUS'],
        unselected=[row['instrument_id'] for row in rows if row['decision_status'] != 'FOCUS'])
    import csv
    import io
    for view in ('all', 'observation', 'focus', 'unselected'):
        text = service.export_strategy_experiment_csv(run['run_id'], strategy_id=STRATEGY, selection=view)
        actual = list(csv.DictReader(io.StringIO(text)))
        _require([row['instrument_id'] for row in actual] == expected[view]
            and all(row['run_id'] == run['run_id'] for row in actual), 'OFFICIAL_CSV_ORDER_MISMATCH')
        exported[view + '.csv'] = text.encode('utf-8')
    observation = service.export_strategy_experiment_json(run['run_id'], strategy_id=STRATEGY, selection='observation')
    view = _json(observation.encode('utf-8'))
    _require(view['results'][STRATEGY]['items'] == selected, 'OFFICIAL_JSON_ROWS_MISMATCH')
    exported['observation.json'] = observation.encode('utf-8')
    original_visible_at = max(stamp(run['decision_cutoff']), stamp(run.get('generated_at', run['decision_cutoff'])))
    kept = deepcopy(selected)
    original_calendar = deepcopy(run.get('target_calendar_evidence'))
    # Avoid retaining the full native run during the separate typed-input read.
    del view, run, service, result, rows, selected, raw
    gc.collect()
    bundle_document = _json(registry.read(input_path, manifest['input_sha256'], MAX_FILE_BYTES))
    if 'input' in bundle_document:
        bundle_document = bundle_document['input']
    bundle = MarketInput.model_validate(bundle_document)
    _require(digest_market_input(bundle) == binding['input_hash']
        and str(bundle.observation_date) == binding['observation_date'], 'TYPED_INPUT_BINDING_MISMATCH')
    input_names = {row.instrument_id: row.name for row in bundle.universe.securities}
    _require(set(ids).issubset(input_names), 'FULL_POOL_OUTSIDE_INPUT_UNIVERSE')
    _require(all(isinstance(pool_names[iid], str) and pool_names[iid] == input_names[iid] for iid in ids),
             'ORIGINAL_POOL_NAME_NOT_SAME_INPUT_IDENTITY')
    registry.read(sources_path, manifest['sources_manifest_sha256'])
    return bundle, deepcopy(binding), kept, exported, original_visible_at, original_calendar


def _authenticate_capture(registry, registration, base):
    from .historical_replay import verify_capture
    from .provider_normalization import _clock
    _strict(registration, ('archive_root', 'result', 'raw', 'receipt', 'typed_receipt'), 'CAPTURE_SCHEMA_INVALID')
    files = {name: registry.registered(registration[name], base) for name in ('result', 'raw', 'receipt')}
    result = _json(registry.read(files['result'].path, files['result'].sha256))
    # Parse whole registered originals before the native verifier's rereads.
    payload = _json(registry.read(files['raw'].path, files['raw'].sha256))
    original = _json(registry.read(files['receipt'].path, files['receipt'].sha256))
    authenticated = verify_capture(_path(registration['archive_root'], base), result)
    _require(Path(authenticated['raw_path']) == files['raw'].path
        and Path(authenticated['receipt_path']) == files['receipt'].path
        and authenticated['raw_file_sha256'] == files['raw'].sha256
        and authenticated['receipt_file_sha256'] == files['receipt'].sha256, 'ORIGINAL_TRIO_LOCATOR_MISMATCH')
    seen, ingested = stamp(original['first_seen_at']), stamp(original['ingested_at'])
    _require(seen <= ingested, 'CAPTURE_CLOCK_ORDER_INVALID')
    for name, document in (('RAW', payload), ('RESULT', result)):
        if 'first_seen_at' in document:
            _require(stamp(document['first_seen_at']) == seen, name + '_FIRST_SEEN_CONFLICT')
    if 'started_at' in result:
        _require(stamp(result['started_at']) <= seen, 'CAPTURE_START_AFTER_SEEN')
    if 'completed_at' in result:
        _require(ingested <= stamp(result['completed_at']), 'CAPTURE_COMPLETE_BEFORE_INGESTED')
    _require(payload.get('field_version') == result.get('field_version') == original.get('field_version'), 'ORIGINAL_FIELD_VERSION_CONFLICT')
    parameters = result['parameters']
    end = parameters.get('end_date') or parameters.get('date') or parameters.get('trade_date')
    end = date.fromisoformat(str(end)) if end is not None else None
    coverage = result.get('coverage', {})
    expected = SourceReceipt(receipt_id=result['request_receipt_id'], provider=result['provider'],
        dataset=result['endpoint'], content_hash=original['content_hash'], field_version=result['field_version'],
        row_count=len(result['rows']), pagination_complete=result['pagination_complete'], status=result['status'],
        parameters={key: value if isinstance(value, (str, int, float, bool)) or value is None else str(value)
                    for key, value in parameters.items()}, source_url=original.get('source_url'),
        instrument_ids=tuple(sorted(str(value) for value in coverage.get('returned_instrument_ids', ()) if value)),
        business_sessions=tuple(sorted({date.fromisoformat(str(value)) for value in coverage.get('business_sessions', ())})),
        **_clock(original, end))
    declared = SourceReceipt.model_validate(registration['typed_receipt'])
    _require(declared == expected, 'TYPED_RECEIPT_NOT_ORIGINAL_NORMALIZATION')
    return expected, files['raw'].path, dict(receipt_id=expected.receipt_id,
        status=expected.status, dataset=expected.dataset, raw_first_seen_present='first_seen_at' in payload,
        started_at=result.get('started_at'), completed_at=result.get('completed_at'),
        original_four_clocks={key: getattr(expected, key).isoformat() for key in
                              ('event_time', 'source_published_at', 'first_seen_at', 'ingested_at')})


def _source_inputs(registry, document, base):
    from .economic_direction_explanation_v1 import OfficialBusinessRegistration, CountedNodeRegistration
    _strict(document, ('schema_version', 'captures', 'business_sources', 'counted_nodes',
                       'reported_scope', 'extra_registered_files'), 'SOURCES_SCHEMA_INVALID')
    _require(document['schema_version'] == SOURCES_SCHEMA, 'SOURCES_VERSION_INVALID')
    for key in ('captures', 'business_sources', 'counted_nodes', 'extra_registered_files'):
        _require(isinstance(document[key], list), 'SOURCES_LIST_REQUIRED')
    _require(len(document['captures']) <= MAX_CAPTURES, 'CAPTURE_COUNT_BUDGET_EXCEEDED')
    receipts, paths, audits = {}, {}, []
    for registration in document['captures']:
        receipt, path, audit = _authenticate_capture(registry, registration, base)
        prior = receipts.get(receipt.receipt_id)
        _require(prior is None, 'DUPLICATE_CAPTURE_RECEIPT')
        receipts[receipt.receipt_id], paths[receipt.receipt_id] = receipt, path
        audits.append(audit)
    businesses = []
    for value in document['business_sources']:
        keys = tuple(OfficialBusinessRegistration.__dataclass_fields__)
        _strict(value, keys, 'BUSINESS_REGISTRATION_SCHEMA_INVALID')
        item = dict(value)
        _require(isinstance(item['literals'], list) and all(isinstance(x, str) and x for x in item['literals']), 'BUSINESS_LITERALS_REQUIRED')
        item['literals'] = tuple(item['literals'])
        for name in ('original', 'receipt', 'derived_text', 'transform'):
            item[name] = registry.registered(item[name], base)
        businesses.append(OfficialBusinessRegistration(**item))
    nodes = []
    for value in document['counted_nodes']:
        _strict(value, tuple(CountedNodeRegistration.__dataclass_fields__), 'NODE_REGISTRATION_SCHEMA_INVALID')
        item = dict(value)
        item['capture_root'] = _path(item['capture_root'], base)
        for name in ('result', 'directory_receipt', 'directory_raw'):
            item[name] = registry.registered(item[name], base)
        _require(isinstance(item['originals'], list) and all(isinstance(pair, list) and len(pair) == 2 for pair in item['originals']), 'NODE_ORIGINAL_PAIRS_REQUIRED')
        item['originals'] = tuple(tuple(registry.registered(ref, base) for ref in pair) for pair in item['originals'])
        nodes.append(CountedNodeRegistration(**item))
    extra = tuple(registry.registered(value, base) for value in document['extra_registered_files'])
    reported = registry.registered(document['reported_scope'], base) if document['reported_scope'] is not None else None
    return tuple(receipts.values()), paths, audits, tuple(businesses), tuple(nodes), extra, reported


class _FreshThemeBinding:
    """Only the exact live producer result plus revalidated original-byte pack."""
    def __init__(self, token, parent, bundle, cutoff, reader, pack, registry, identity):
        _require(token is _ISSUER, 'PRIVATE_NATIVE_ISSUER_REQUIRED')
        self.parent, self.bundle, self.cutoff = parent, bundle, cutoff
        self.reader, self.pack, self.registry, self.identity = reader, pack, registry, identity
        self.parent_hash, self.pack_hash = digest_json_stream(parent), digest_json_stream(pack)
        self.input_hash = digest_market_input(bundle)

    def __call__(self, parent, *, bundle, cutoff):
        _require(parent is self.parent and bundle is self.bundle and stamp(cutoff) == self.cutoff
            and digest_json_stream(parent) == self.parent_hash
            and digest_json_stream(self.pack) == self.pack_hash
            and digest_market_input(bundle) == self.input_hash
            and source_identity() == self.identity, 'LIVE_NATIVE_PARENT_OR_IDENTITY_CHANGED')
        self.reader.validate_theme_market_pack(self.pack, bundle=bundle, cutoff=cutoff)
        self.registry.terminal()
        return True


class _FrozenSelectionBinding:
    def __init__(self, token, binding, rows, registry):
        _require(token is _ISSUER, 'PRIVATE_SELECTION_BINDER_REQUIRED')
        self.binding, self.rows, self.registry = binding, rows, registry
        self.binding_hash, self.rows_hash = digest_json_stream(binding), digest_json_stream(rows)

    def __call__(self, selected, *, bundle, cutoff):
        _require(digest_json_stream(self.binding) == self.binding_hash
            and digest_json_stream(self.rows) == self.rows_hash
            and list(selected) == sorted(self.binding['selected_ids'])
            and digest_market_input(bundle) == self.binding['input_hash']
            and str(bundle.observation_date) == self.binding['observation_date']
            and stamp(cutoff) >= stamp(self.binding['original_decision_cutoff']), 'FROZEN_SELECTION_BINDING_CHANGED')
        self.registry.terminal()
        return True


def _complete_groups(diagnostic, selected, binding):
    by_id = {row['instrument_id']: row for row in selected}
    assigned, groups = set(), []
    relations = {iid: [] for iid in binding['selected_ids']}
    for category in CATEGORIES:
        for row in diagnostic.get(category, ()):
            _require(row.get('theme_id') and row.get('name'), 'SOURCELESS_THEME_NAME')
            _require(len(row['selected_ids']) == len(set(row['selected_ids']))
                and set(row['selected_ids']).issubset(by_id), 'THEME_ADDS_OR_DUPLICATES_STOCK')
            for iid in row['selected_ids']:
                relations[iid].append(dict(theme_id=row['theme_id'], category=category, stage=row['stage']))
            ids = [iid for iid in binding['selected_ids'] if iid in row['selected_ids'] and iid not in assigned]
            if ids:
                groups.append(dict(group_id=category + ':' + row['theme_id'], group_type=category,
                    name=row['name'], stage=row['stage'], original_V4_observation=deepcopy(row),
                    primary_selected_ids=ids, items=[deepcopy(by_id[iid]) for iid in ids]))
                assigned.update(ids)
    for stage in (*STAGES, None):
        ids = [iid for iid in binding['selected_ids'] if iid not in assigned and by_id[iid].get('primary_stage') == stage]
        if ids:
            groups.append(dict(group_id='MODEL:' + str(stage or 'UNKNOWN'), group_type='MODEL_ONLY_NOT_THEME_MAINLINE',
                name=STAGES.get(stage, '模型归属未知'), stage=stage,
                theme_status='UNKNOWN_NOT_EVIDENCE_OF_NO_THEME', primary_selected_ids=ids,
                items=[deepcopy(by_id[iid]) for iid in ids]))
            assigned.update(ids)
    residual = [iid for iid in binding['selected_ids'] if iid not in assigned]
    if residual:
        groups.append(dict(group_id='MODEL:UNKNOWN_UNRECOGNIZED_PRIMARY_STAGE', group_type='MODEL_ONLY_NOT_THEME_MAINLINE',
            name='模型归属未知', stage=None, primary_selected_ids=residual,
            reason_codes=['ORIGINAL_UNRECOGNIZED_STAGE_RETAINED'], items=[deepcopy(by_id[iid]) for iid in residual]))
    flattened = [row for group in groups for row in group['items']]
    projected = {row['instrument_id']: row for row in flattened}
    _require(len(flattened) == len(projected) == len(selected) and set(projected) == set(by_id)
        and all(projected[iid] == by_id[iid] for iid in by_id), 'FULL_PRIMARY_UNION_OR_ROWS_CHANGED')
    return dict(groups=groups, secondary_verified_relations=relations,
        frozen_selected_ids=list(binding['selected_ids']), primary_group_union_exact=True,
        selected_original_items_hash=binding['selected_original_items_hash'],
        category_order=list(CATEGORIES), native_strength_order='UNCHANGED_WITHIN_EACH_NATIVE_CATEGORY',
        cross_category_strength_ranking='UNKNOWN_NO_NEW_WEIGHTS', original_order_preserved_within_group=True)


def _theme_diagnostic(bundle, binding, selected, reader, registry, identity, cutoff, membership_mode):
    from . import theme_evolution, theme_observation_diagnostics
    from .research_context_artifacts import RETURN_BASIS
    default = {key: [] for key in CATEGORIES}
    default.update(model_only_selected_ids=list(binding['selected_ids']), context_status='UNKNOWN',
                   reason_codes=['THEME_ORIGINAL_SOURCE_CONTEXT_UNAVAILABLE'])
    receipts = tuple(reader.receipts)
    dates = {dataset: {str(row.parameters['date']) for row in receipts
        if row.dataset == dataset and row.status == 'COMPLETE' and row.pagination_complete
        and 'date' in row.parameters and all(getattr(row, key) <= cutoff for key in
            ('event_time', 'source_published_at', 'first_seen_at', 'ingested_at'))}
        for dataset in ('market_daily', 'master_all')}
    dependencies = [str(day) for day in bundle.sessions if day <= bundle.observation_date][-67:]
    available = [day for day in dependencies if day in dates['market_daily'] & dates['master_all']]
    dependency_scope = dict(calendar_dependencies=dependencies, original_available_dates=available,
        unavailable_dates=[day for day in dependencies if day not in available], prior60_missing_is_UNKNOWN=True)
    if not selected:
        return default, None, dependency_scope, 0
    if membership_mode == 'CURRENT_COHORT_BACKVIEW':
        current = reader.build_current_theme_context(cutoff=cutoff)
        themes = current['themes']
    else:
        # The original dated snapshots still require actual reader receipts.
        authorized = {row.receipt_id: row for row in reader.receipts}
        themes, missing = [], []
        for row in bundle.themes:
            absent = sorted(set(row.source_refs) - set(authorized))
            incomplete = [dict(receipt_id=ref, status=authorized[ref].status,
                pagination_complete=authorized[ref].pagination_complete)
                for ref in row.source_refs if ref in authorized
                and (authorized[ref].status != 'COMPLETE' or not authorized[ref].pagination_complete)]
            not_visible = any(getattr(row, key) > cutoff for key in
                ('event_time', 'source_published_at', 'first_seen_at', 'ingested_at'))
            if absent or not row.source_refs or incomplete or not_visible:
                reasons = []
                if absent or not row.source_refs:
                    reasons.append('DATED_THEME_REQUIRED_ORIGINAL_REFS_UNAVAILABLE')
                if incomplete:
                    reasons.append('DATED_THEME_REQUIRED_ORIGINAL_PARENTS_INCOMPLETE')
                if not_visible:
                    reasons.append('DATED_THEME_NOT_VISIBLE_AT_CONTEXT_CUTOFF')
                missing.append(dict(theme_id=row.theme_id, status='UNKNOWN',
                    reason_codes=reasons, unavailable_source_refs=absent,
                    incomplete_source_refs=incomplete))
            else:
                themes.append(row.model_dump(mode='json'))
        dependency_scope['dated_theme_missing_sources'] = missing
    if not available:
        return default, None, dependency_scope, 0
    if not themes:
        default['reason_codes'] = ['DATED_OR_CURRENT_THEME_MEMBERSHIP_UNAVAILABLE']
        return default, None, dependency_scope, 0
    pack = reader.build_market_pack(bundle, cutoff=cutoff, sessions=available, themes=themes, return_basis=RETURN_BASIS)
    # The actual original-byte validator is passed directly to the producer.
    parent = theme_evolution.run_theme_evolution(bundle, cutoff=cutoff, market_pack=pack,
        _market_pack_validator=reader.validate_theme_market_pack,
        membership_mode=membership_mode)
    binder = _FreshThemeBinding(_ISSUER, parent, bundle, cutoff, reader, pack, registry, identity)
    selection = _FrozenSelectionBinding(_ISSUER, binding, selected, registry)
    diagnostic = theme_observation_diagnostics.build_theme_observation_diagnostics(bundle, parent,
        cutoff=cutoff, selected_ids=binding['selected_ids'], _theme_context_validator=binder,
        _selection_validator=selection)
    return diagnostic, parent, dependency_scope, 1


def _model_text(binding, groups, original_visible_at, as_of, reason):
    if as_of < original_visible_at:
        return 'NOT_YET_VISIBLE：原保存运行在显示截止后才可见。\n', 'NOT_YET_VISIBLE'
    lines = [f"完整原R2观察名单：{len(binding['selected_ids'])}只；FOCUS {binding['counts']['focus']}只。",
        f"run_id={binding['run_id']}；观察日={binding['observation_date']}；目标={binding['target_session'] or 'UNKNOWN'}",
        f"原资料截止={binding['original_decision_cutoff']}；解释状态=UNKNOWN（{reason}）",
        '初始参数尚未验证；MARKET_EDGE=NOT_ESTABLISHED。原UNKNOWN数量不作为观察推荐。']
    for group in groups:
        lines.append(f"\n{group['name']}（{len(group['items'])}只）")
        for row in group['items']:
            lines.append(f"{row['instrument_id']} {row.get('name', '')} | {row['decision_status']} | {row.get('primary_stage') or 'UNKNOWN'}")
    if not binding['selected_ids']:
        lines.append('原运行观察集合真实为空；不是缺结果。')
    return '\n'.join(lines) + '\n', 'COMPLETE_SAVED_OBSERVATION_WITH_UNKNOWN_CONTEXT'


def deliver_saved_r2(*, run_path, run_manifest_path, input_path, sources_manifest_path,
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
    sources_path = Path(sources_manifest_path).resolve(strict=True)
    document = _json(registry.read(sources_path))
    receipts, paths, source_audit, businesses, nodes, extra, reported = _source_inputs(registry, document, sources_path.parent)
    # Whole late originals remain authenticated/audited, but do not enter this
    # context capability. No clock/status is rewritten to make them visible.
    completed = {row['receipt_id']: row['completed_at'] for row in source_audit}
    visible = tuple(row for row in receipts if all(getattr(row, key) <= cutoff for key in
        ('event_time', 'source_published_at', 'first_seen_at', 'ingested_at'))
        and (completed[row.receipt_id] is None or stamp(completed[row.receipt_id]) <= cutoff))
    unavailable = [row.receipt_id for row in receipts if row not in visible]
    reader = SourceArtifactReader(visible, {row.receipt_id: paths[row.receipt_id] for row in visible})
    diagnostic, native_parent, dependencies, native_calls = _theme_diagnostic(
        bundle, binding, selected, reader, registry, identity, cutoff, membership_mode)
    dependencies['capture_not_visible_at_context_cutoff'] = unavailable
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
    _require(source_identity() == identity, 'IMPLEMENTATION_CHANGED')
    directory.mkdir(parents=True, exist_ok=False)
    files, audit_files, output_bytes = {}, {}, 0
    audit_prefix = 'NON_DELIVERY_AUDIT/'
    def save(name, value, *, deliverable=True):
        nonlocal output_bytes
        raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
        output_bytes += len(raw)
        _require(len(raw) <= MAX_FILE_BYTES and output_bytes <= MAX_OUTPUT_BYTES, 'OUTPUT_BYTE_BUDGET_EXCEEDED')
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
            registry.terminal()
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
    _require(source_identity() == identity, 'IMPLEMENTATION_CHANGED')
    # All derived bytes, including private audit artifacts, share the original
    # byte budget. Failed/hidden explanations never enter delivery files.
    if display_result:
        for name, raw in sorted(display_payloads.items()):
            output_bytes += len(raw)
            _require(len(raw) <= MAX_FILE_BYTES and output_bytes <= MAX_OUTPUT_BYTES, 'OUTPUT_BYTE_BUDGET_EXCEEDED')
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
    return dict(status=status, output_directory=str(directory.resolve()), run_id=binding['run_id'],
                counts=binding['native_counts'], count_summary=binding['counts'], observation_count=len(selected), files=files)
