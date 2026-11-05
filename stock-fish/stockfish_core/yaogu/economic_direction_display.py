"""Read-only delivery of an archived explanation and its exact grouped parent.

This reader verifies archive bindings, not source, selector or historical PIT
authority. It never recreates the explanation, changes stock selection, or
opens the upstream source registry. Fresh production belongs to the separately
registered original-byte workflow.
"""
from __future__ import annotations

from stockfish_core.file_integrity import content_equal, content_reference_equal, content_fingerprint

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .history_artifacts import digest_json_stream

VERSION = 'ECONOMIC_DIRECTION_ARCHIVED_DISPLAY_V1'
# Derived explanation archives include preserved whole selected rows. This
# limit is separate from the producer's smaller per-business-original limit.
MAX_ARCHIVE_BYTES = 32 * 1024**2


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('ECONOMIC_DISPLAY_DUPLICATE_JSON_KEY')
        result[key] = value
    return result


def _constant(value):
    raise ValueError('ECONOMIC_DISPLAY_NONFINITE_JSON_VALUE')


def _read(path):
    path = Path(path).resolve()
    before = path.stat()
    if not 0 < before.st_size <= MAX_ARCHIVE_BYTES:
        raise ValueError('ECONOMIC_DISPLAY_ARCHIVE_SIZE_INVALID')
    with path.open('rb') as handle:
        raw = handle.read(MAX_ARCHIVE_BYTES + 1)
    after = path.stat()
    stat_fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
    state = {key: getattr(before, key) for key in stat_fields}
    if state != {key: getattr(after, key) for key in stat_fields} or len(raw) != before.st_size:
        raise ValueError('ECONOMIC_DISPLAY_ARCHIVE_CHANGED')
    document = json.loads(raw.decode('utf-8'), object_pairs_hook=_object, parse_constant=_constant)
    if not isinstance(document, dict):
        raise ValueError('ECONOMIC_DISPLAY_ARCHIVE_DOCUMENT_REQUIRED')
    return document, {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(), **state}


def _hash(document):
    return digest_json_stream({key: value for key, value in document.items()
                               if key != 'attachment_content_hash'})


def _instant(value):
    value = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('ECONOMIC_DISPLAY_TIMEZONE_REQUIRED')
    return value


def _rendered_indices(attachment, parent, original):
    from .economic_direction_explanation_v1 import DIRECTIONS
    directions = attachment['economic_directions']
    if [row['direction'] for row in directions] != list(DIRECTIONS):
        raise ValueError('ECONOMIC_DISPLAY_DIRECTION_INDEX_MISMATCH')
    assigned, expected_groups = set(), []
    business_by_group = {}
    for direction in directions:
        ids = direction['selected_ids']
        relations = direction['business_relations']
        if (not isinstance(ids, list) or [row['instrument_id'] for row in relations] != ids
                or len(set(ids)) != len(ids) or set(ids) & assigned
                or any(iid not in original for iid in ids)
                or any(row['name'] != original[row['instrument_id']]['name']
                       or row['direction'] != direction['direction'] for row in relations)):
            raise ValueError('ECONOMIC_DISPLAY_RENDERED_BUSINESS_INDEX_MISMATCH')
        assigned.update(ids)
        if ids:
            gid = 'ECONOMIC_BUSINESS:' + direction['direction']
            expected_groups.append(gid); business_by_group[gid] = direction
    original_groups = {group['group_id']: group for group in parent['groups']}
    if len(original_groups) != len(parent['groups']):
        raise ValueError('ECONOMIC_DISPLAY_ORIGINAL_GROUP_SCOPE_INVALID')
    for group in parent['groups']:
        if any(iid not in assigned for iid in group['primary_selected_ids']):
            expected_groups.append(group['group_id'])
    if [group['group_id'] for group in attachment['primary_display_groups']] != expected_groups:
        raise ValueError('ECONOMIC_DISPLAY_RENDERED_GROUP_INDEX_MISMATCH')
    for group in attachment['primary_display_groups']:
        ids = [row['instrument_id'] for row in group['original_items']]
        if ids != group['primary_selected_ids']:
            raise ValueError('ECONOMIC_DISPLAY_RENDERED_GROUP_INDEX_MISMATCH')
        if group['group_id'] in business_by_group:
            direction = business_by_group[group['group_id']]
            if (group['group_type'] != 'BUSINESS_RELATION_ONLY_NOT_MAINLINE'
                    or ids != direction['selected_ids'] or group['name'] != direction['name_zh']):
                raise ValueError('ECONOMIC_DISPLAY_RENDERED_GROUP_ROLE_MISMATCH')
        else:
            original_group = original_groups[group['group_id']]
            expected = [iid for iid in original_group['primary_selected_ids'] if iid not in assigned]
            if (ids != expected or group['group_type'] != original_group['group_type']
                    or group['name'] != original_group['name'] or group.get('stage') != original_group.get('stage')
                    or group.get('original_V4_observation') != original_group.get('original_V4_observation')):
                raise ValueError('ECONOMIC_DISPLAY_RENDERED_GROUP_ROLE_MISMATCH')


def _clock_floor(attachment, parent):
    if (attachment['original_selection_cutoff'] != parent['frozen_run_binding']['original_decision_cutoff']
            or attachment['original_native_context_cutoff'] != parent['new_context_cutoff']):
        raise ValueError('ECONOMIC_DISPLAY_ORIGINAL_CLOCK_BINDING_MISMATCH')
    known = [parent['created_at'], parent['new_context_cutoff'],
             parent['frozen_run_binding']['original_decision_cutoff']]
    for direction in attachment['economic_directions']:
        for relation in direction['business_relations']:
            clocks = relation['actual_capture_clocks']
            known.extend(clocks.values())
    for node in attachment['counted_provider_nodes']:
        known.extend(value for key, value in node['actual_capture_clocks'].items()
                     if key.endswith('_at') and value is not None)
    scope = attachment.get('reported_observation_day_scope')
    if scope:
        known.extend(row['actual_received_at'] for row in scope['reports'])
    floor = max(map(_instant, known))
    cutoff, created, visible = (_instant(attachment[key]) for key in
                                ('new_evidence_cutoff', 'created_at', 'attachment_visible_at'))
    if cutoff < floor or created < floor or visible < max(created, cutoff):
        raise ValueError('ECONOMIC_DISPLAY_KNOWN_CLOCK_FLOOR_VIOLATION')


def archived_economic_direction_display(attachment_path, *, grouped_parent, as_of):
    """Read one exact saved pair; old display cutoffs hide all late contents."""
    from .economic_direction_explanation_v1 import CATEGORIES, VERSION as SCHEMA
    from .economic_direction_explanation_v1 import render_economic_direction_explanation

    attachment, attachment_file = _read(attachment_path)
    parent, parent_file = _read(grouped_parent)
    if attachment.get('schema_version') != SCHEMA:
        raise ValueError('ECONOMIC_DISPLAY_SCHEMA_MISMATCH')
    if _hash(attachment) != attachment.get('attachment_content_hash'):
        raise ValueError('ECONOMIC_DISPLAY_ATTACHMENT_HASH_MISMATCH')
    if parent_file['sha256'] != attachment.get('original_grouped_file_sha256'):
        raise ValueError('ECONOMIC_DISPLAY_GROUPED_PARENT_HASH_MISMATCH')
    if _hash(parent) != parent.get('attachment_content_hash'):
        raise ValueError('ECONOMIC_DISPLAY_GROUPED_CONTENT_HASH_MISMATCH')
    checks = (
        attachment.get('frozen_run_binding') == parent.get('frozen_run_binding'),
        attachment.get('frozen_selected_ids') == parent.get('frozen_selected_ids'),
        attachment.get('selected_original_items_hash') == parent.get('selected_original_items_hash'),
        attachment.get('frozen_original_groups') == parent.get('groups'),
        attachment.get('observation_date') == parent.get('observation_date'),
        attachment.get('target_session') == parent.get('target_session'),
        attachment.get('preserved_native_subtheme_layers') == {
            key: (parent.get('V4') or {}).get(key, []) for key in CATEGORIES},
        attachment.get('stock_selection_changed') is False,
        attachment.get('score_or_FOCUS_changed') is False,
        attachment.get('source_capability_issued') is False,
        attachment.get('primary_group_union_exact') is True,
        parent.get('primary_group_union_exact') is True,
        parent.get('stock_selection_changed') is False,
        parent.get('score_or_FOCUS_changed') is False,
    )
    if not all(checks):
        raise ValueError('ECONOMIC_DISPLAY_FROZEN_BINDING_MISMATCH')
    ids = attachment.get('frozen_selected_ids')
    if not isinstance(ids, list) or not ids or len(ids) != len(set(ids)):
        raise ValueError('ECONOMIC_DISPLAY_SELECTED_SCOPE_INVALID')
    original_rows = [row for group in parent['groups'] for row in group['items']]
    original_ids = [row['instrument_id'] for row in original_rows]
    if (len(original_ids) != len(ids) or set(original_ids) != set(ids)
            or any(group['primary_selected_ids'] != [row['instrument_id'] for row in group['items']]
                   for group in parent['groups'])):
        raise ValueError('ECONOMIC_DISPLAY_ORIGINAL_GROUP_SCOPE_INVALID')
    original = {row['instrument_id']: row for row in original_rows}
    displayed = [row for group in attachment['primary_display_groups'] for row in group['original_items']]
    displayed_ids = [row['instrument_id'] for row in displayed]
    if (len(displayed_ids) != len(ids) or set(displayed_ids) != set(ids)
            or any(original.get(row['instrument_id']) != row for row in displayed)
            or digest_json_stream([original[iid] for iid in ids]) != parent['selected_original_items_hash']):
        raise ValueError('ECONOMIC_DISPLAY_FULL_UNION_OR_ITEMS_MISMATCH')
    _rendered_indices(attachment, parent, original)
    _clock_floor(attachment, parent)
    text = render_economic_direction_explanation(attachment, as_of=as_of)
    # Read the two complete consumed archives again, including file metadata.
    for path, expected in ((attachment_path, attachment_file), (grouped_parent, parent_file)):
        if not content_reference_equal(_read(path)[1], expected):
            raise ValueError('ECONOMIC_DISPLAY_ARCHIVE_CHANGED')
    visible = not text.startswith('NOT_YET_VISIBLE')
    result = dict(version=VERSION, status='VISIBLE_ARCHIVED_EXPLANATION' if visible else 'NOT_YET_VISIBLE',
        display_as_of=as_of.isoformat() if isinstance(as_of, datetime) else as_of,
        consumed_archives={'explanation': attachment_file, 'grouped_parent': parent_file},
        archive_integrity_and_binding_verified=True, source_registry_reauthenticated=False,
        selector_reauthenticated=False, source_capability_issued=False,
        stock_selection_changed=False, score_or_FOCUS_changed=False,
        network_DB_SDK_selector_calls=0, market_edge='NOT_ESTABLISHED', text=text)
    if visible:
        result.update(run_id=attachment['frozen_run_binding']['run_id'],
            observation_date=attachment['observation_date'], target_session=attachment.get('target_session'),
            observation_count=len(ids), original_selection_cutoff=attachment['original_selection_cutoff'],
            explanation_visible_at=attachment['attachment_visible_at'],
            complete_economic_mainline_coverage='UNKNOWN', historical_PIT_revalidated=False)
    return result


def save_archived_economic_direction_display(attachment_path, *, grouped_parent, output, as_of=None):
    result = archived_economic_direction_display(attachment_path, grouped_parent=grouped_parent,
        as_of=as_of or datetime.now(timezone.utc))
    # Finish validation before creating output. Existing delivery paths cannot
    # be reused or overwritten, including on an intentionally hidden display.
    directory = Path(output)
    directory.mkdir(parents=True, exist_ok=False)
    payloads = {'display.json': json.dumps(result, ensure_ascii=False, indent=2).encode('utf-8'),
                'complete-observation-list.txt': result['text'].encode('utf-8')}
    for name, raw in payloads.items():
        (directory / name).write_bytes(raw)
    manifest = dict(version=VERSION, created_at=datetime.now(timezone.utc).isoformat(),
        status=result['status'], consumed_archives=result['consumed_archives'],
        files={name: {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
               for name, raw in payloads.items()}, selection_changed=False,
        live_source_capability_restored=False, network_DB_SDK_selector_calls=0)
    (directory / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return {key: result[key] for key in ('status', 'stock_selection_changed', 'source_capability_issued')} | {
        'output_directory': str(directory.resolve()), 'observation_count': result.get('observation_count'),
        'files': manifest['files']}
