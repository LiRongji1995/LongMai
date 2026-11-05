"""Explicit sealed RESULT byte copies; original path/physical owner remain lost.

The manifest authorizes only sealed core result roles. It does not relocate RAW,
receipts, producer code, locators or core artifacts, or establish historical PIT.
Filesystem/provider residency is separately established by the deployment audit.
"""
from stockfish_core.file_integrity import content_equal, content_reference_equal, content_fingerprint
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import stat

PROFILE = 'SEALED_RESULT_LOCAL_COPY_V1'
MAX_MANIFEST_BYTES = 4 * 1024**2
ROOT_FIELDS = {'st_dev', 'st_ino', 'st_mode'}
IMMUTABLE = getattr(stat, 'UF_IMMUTABLE', 2)


def require(value, reason):
    if not value:
        raise ValueError('R2_RESULT_LOCAL_COPY_' + reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def root_reference(path):
    path = Path(path).absolute()
    require(not path.is_symlink() and path.resolve(strict=True) == path, 'EXACT_COPY_ROOT_REQUIRED')
    value = path.lstat()
    require(stat.S_ISDIR(value.st_mode), 'COPY_ROOT_DIRECTORY_REQUIRED')
    return dict(path=str(path), fp={key: getattr(value, key) for key in ROOT_FIELDS})


def validate_root(reference, *, physical=False):
    require(type(reference) is dict and set(reference) == {'path', 'fp'}
        and isinstance(reference['path'], str) and Path(reference['path']).is_absolute()
        and str(Path(reference['path']).absolute()) == reference['path']
        and type(reference['fp']) is dict and set(reference['fp']) == ROOT_FIELDS
        and all(type(value) is int for value in reference['fp'].values())
        and stat.S_ISDIR(reference['fp']['st_mode']), 'COPY_ROOT_REFERENCE_REQUIRED')
    if physical:
        root_reference(reference['path'])  # Check current directory structure/type, not old inode.
    return reference


def protocol_identity(reference, copy_root_reference):
    from .short_horizon_archive_bridge_v1 import _ref
    _ref(reference)
    validate_root(copy_root_reference)
    require(not Path(reference['path']).is_relative_to(Path(copy_root_reference['path'])), 'MANIFEST_MUST_BE_OUTSIDE_COPY_ROOT')
    return dict(profile=PROFILE, manifest_reference=deepcopy(reference), manifest_sha256=reference['sha256'],
        copy_root_reference=deepcopy(copy_root_reference))


def validate_protocol(value):
    require(type(value) is dict and set(value) == {'profile', 'manifest_reference', 'manifest_sha256', 'copy_root_reference'}
        and value['profile'] == PROFILE, 'EXPLICIT_VERSIONED_PROTOCOL_REQUIRED')
    require(value == protocol_identity(value['manifest_reference'], value['copy_root_reference']), 'MANIFEST_IDENTITY_MISMATCH')
    return value


def manifest_reference(path, expected_sha256):
    from .short_horizon_archive_bridge_v1 import ArchiveVerificationSession, _fp, _sha
    path = Path(path).absolute()
    require(not path.is_symlink() and path.resolve(strict=True) == path, 'MANIFEST_EXACT_PATH_REQUIRED')
    before = _fp(path)
    session = ArchiveVerificationSession()
    raw = session.read(path, _sha(expected_sha256), limit=MAX_MANIFEST_BYTES)
    # session.read already verifies whole bytes against the declared SHA.
    return dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw), fp=before)


class LocalCopyManifest:
    def __init__(self, session, reference):
        from .short_horizon_archive_bridge_v1 import _ref, _json, _sha
        raw = session.read(reference['path'], reference, limit=MAX_MANIFEST_BYTES)
        value = _json(raw)
        require(type(value) is dict and set(value) == {'schema_version', 'copy_root_reference', 'archives'}
            and value['schema_version'] == PROFILE and type(value['archives']) is list
            and 0 < len(value['archives']) <= 100, 'MANIFEST_SCHEMA_REQUIRED')
        validate_root(value['copy_root_reference'], physical=True)
        self.identity = protocol_identity(reference, value['copy_root_reference'])
        self._identity_json = canonical(self.identity)
        self.archives, global_copies, copy_path_owners, copy_physical_owners = {}, {}, {}, {}
        root = Path(self.identity['copy_root_reference']['path'])
        for archive in value['archives']:
            require(type(archive) is dict and set(archive) == {'archive_final_sha256', 'results'}, 'ARCHIVE_SCOPE_REQUIRED')
            seal = _sha(archive['archive_final_sha256'])
            require(seal not in self.archives and type(archive['results']) is list
                and 0 < len(archive['results']) <= 1024, 'UNIQUE_NONEMPTY_ARCHIVE_SCOPE_REQUIRED')
            entries, physical_files, current_paths = {}, set(), set()
            for entry in archive['results']:
                require(type(entry) is dict and set(entry) == {'original_reference', 'current_reference'}, 'PAIRED_REF4_REQUIRED')
                original, current = (_ref(entry[key]) for key in ('original_reference', 'current_reference'))
                path, copied = original['path'], Path(current['path'])
                require(path != current['path'] and original['sha256'] == current['sha256']
                    and original['bytes'] == current['bytes'], 'EXACT_SEALED_BYTES_AND_DIFFERENT_COPY_PATH_REQUIRED')
                require(copied.is_relative_to(root) and copied != root and str(copied.absolute()) == current['path']
                    and '..' not in copied.parts
                    and copied.is_file(),
                    'COPY_INSIDE_EXPLICIT_ROOT_REQUIRED')
                require(path not in entries and str(Path(path).absolute()) == path, 'UNIQUE_EXACT_RESULT_PATH_REQUIRED')
                physical = current['path']
                require(physical not in physical_files and current['path'] not in current_paths, 'DUPLICATE_COPIED_PHYSICAL_FILE')
                require(path not in global_copies or content_reference_equal(global_copies[path], current), 'CROSS_SCOPE_COPY_MAPPING_CONFLICT')
                require(current['path'] not in copy_path_owners or copy_path_owners[current['path']] == path,
                    'CROSS_SCOPE_COPY_PATH_ALIAS')
                require(physical not in copy_physical_owners or copy_physical_owners[physical] == path,
                    'CROSS_SCOPE_COPY_PHYSICAL_ALIAS')
                global_copies[path] = deepcopy(current)
                copy_path_owners[current['path']] = path
                copy_physical_owners[physical] = path
                physical_files.add(physical)
                current_paths.add(current['path'])
                entries[path] = deepcopy(entry)
            self.archives[seal] = entries
        self._archives_json = canonical(self.archives)

    def check(self):
        require(canonical(self.identity) == self._identity_json and canonical(self.archives) == self._archives_json,
            'IN_MEMORY_MANIFEST_CHANGED')
        validate_root(self.identity['copy_root_reference'], physical=True)

    def scope(self, seal):
        self.check()
        return self.archives.get(seal, {})

    def validate_scope(self, seal, core_records, sealed_refs):
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
        current = entry['current_reference']
        path = Path(current['path'])
        require(not path.is_symlink() and path.resolve(strict=True) == path, 'EXACT_COPY_PATH_REQUIRED')
        raw = session.read(path, current)
        require(len(raw) == original['bytes'] and hashlib.sha256(raw).hexdigest() == original['sha256'], 'SEALED_RESULT_BYTES_CHANGED')
        require(original['path'] not in used, 'DUPLICATE_COPIED_RESULT')
        used.add(original['path'])
        self.check()
        return raw, dict(original_reference=deepcopy(original), current_reference=deepcopy(current),
            original_physical_identity_status='LOST_NOT_RESTORED',
            proof_mode='SEALED_BYTES_AND_LOCAL_COPY_CONTENT_REFERENCE')
