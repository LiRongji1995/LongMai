"""Explicit recovery of sealed result bytes, never original physical ownership.

Only the bridge's sealed core result role may use this protocol. Original raw,
receipt, producer, locator, source and artifact references retain strict checks.
"""
from __future__ import annotations

from stockfish_core.file_integrity import content_equal, content_reference_equal, content_fingerprint
from copy import deepcopy
import hashlib
import json
from pathlib import Path

PROFILE = 'SEALED_RESULT_BYTES_RESTORED_V1'
MAX_MANIFEST_BYTES = 4 * 1024**2


def require(value, reason):
    if not value:
        raise ValueError('R2_RESULT_RESTORATION_' + reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def protocol_identity(reference):
    from .short_horizon_archive_bridge_v1 import _ref
    _ref(reference)
    return dict(profile=PROFILE, manifest_reference=deepcopy(reference), manifest_sha256=reference['sha256'])


def validate_protocol(value):
    require(type(value) is dict and set(value) == {'profile', 'manifest_reference', 'manifest_sha256'}
            and value['profile'] == PROFILE, 'EXPLICIT_VERSIONED_PROTOCOL_REQUIRED')
    require(value == protocol_identity(value['manifest_reference']), 'MANIFEST_IDENTITY_MISMATCH')
    return value


def manifest_reference(path, expected_sha256):
    """Pin a small explicitly selected manifest; no downloaded data are read."""
    from .short_horizon_archive_bridge_v1 import ArchiveVerificationSession, _fp, _sha
    path = Path(path).absolute()
    require(not path.is_symlink() and path.resolve(strict=True) == path, 'MANIFEST_EXACT_PATH_REQUIRED')
    before = _fp(path)
    session = ArchiveVerificationSession()
    raw = session.read(path, _sha(expected_sha256), limit=MAX_MANIFEST_BYTES)
    # session.read already verifies whole bytes against the declared SHA.
    return dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw), fp=before)


class RestorationManifest:
    def __init__(self, session, reference):
        from .short_horizon_archive_bridge_v1 import _ref, _json, _sha
        self.identity = protocol_identity(reference)
        self._identity_json = canonical(self.identity)
        raw = session.read(reference['path'], reference, limit=MAX_MANIFEST_BYTES)
        value = _json(raw)
        require(type(value) is dict and set(value) == {'schema_version', 'archives'}
                and value['schema_version'] == PROFILE and type(value['archives']) is list
                and 0 < len(value['archives']) <= 100, 'MANIFEST_SCHEMA_REQUIRED')
        self.archives = {}
        for archive in value['archives']:
            require(type(archive) is dict and set(archive) == {'archive_final_sha256', 'results'}, 'ARCHIVE_SCOPE_REQUIRED')
            seal = _sha(archive['archive_final_sha256'])
            require(seal not in self.archives and type(archive['results']) is list
                    and 0 < len(archive['results']) <= 1024, 'UNIQUE_NONEMPTY_ARCHIVE_SCOPE_REQUIRED')
            entries, physical_files = {}, set()
            for entry in archive['results']:
                require(type(entry) is dict and set(entry) == {'original_reference', 'current_reference'}, 'PAIRED_REF4_REQUIRED')
                original, current = (_ref(entry[key]) for key in ('original_reference', 'current_reference'))
                require(original['path'] == current['path'] and original['sha256'] == current['sha256']
                        and original['bytes'] == current['bytes'], 'EXACT_SEALED_BYTES_AND_LOST_OWNER_REQUIRED')
                path = original['path']
                require(path not in entries and str(Path(path).absolute()) == path, 'UNIQUE_EXACT_RESULT_PATH_REQUIRED')
                physical = current['path']
                require(physical not in physical_files, 'DUPLICATE_RECOVERED_PHYSICAL_FILE')
                physical_files.add(physical)
                entries[path] = deepcopy(entry)
            self.archives[seal] = entries
        self._archives_json = canonical(self.archives)

    def check(self):
        require(canonical(self.identity) == self._identity_json and canonical(self.archives) == self._archives_json,
                'IN_MEMORY_MANIFEST_CHANGED')

    def scope(self, seal):
        self.check()
        return self.archives.get(seal, {})

    def validate_scope(self, seal, core_records, sealed_refs):
        """Establish the exact role before any recovered read is authorized."""
        entries = self.scope(seal)
        selected = {item['result']['path']: item['result'] for item in core_records}
        for path, entry in entries.items():
            original = entry['original_reference']
            require(content_reference_equal(selected.get(path), original) and content_reference_equal(sealed_refs.get(path), original),
                    'ONLY_EXACT_SEALED_CORE_RESULT_ALLOWED')
        return set(entries)

    def read_result(self, session, seal, original, allowed_paths, used):
        self.check()
        entry = self.scope(seal).get(original['path'])
        if entry is None:
            return session.read(original['path'], original), None
        require(original['path'] in allowed_paths and content_reference_equal(entry['original_reference'], original),
                'SEALED_CORE_RESULT_CONTEXT_REQUIRED')
        current = entry['current_reference']; path = Path(current['path'])
        require(not path.is_symlink() and path.resolve(strict=True) == path, 'RECOVERED_EXACT_PATH_REQUIRED')
        raw = session.read(path, current)
        require(len(raw) == original['bytes'] and hashlib.sha256(raw).hexdigest() == original['sha256'],
                'SEALED_RESULT_BYTES_CHANGED')
        require(original['path'] not in used, 'DUPLICATE_RECOVERED_RESULT')
        used.add(original['path'])
        return raw, dict(original_reference=deepcopy(original), current_reference=deepcopy(current),
                        original_physical_identity_status='LOST_NOT_RESTORED',
                        proof_mode='SEALED_BYTES_AND_CURRENT_CONTENT_REFERENCE')
