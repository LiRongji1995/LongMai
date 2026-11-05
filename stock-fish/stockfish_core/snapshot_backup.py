"""Non-destructive SQLite WAL + explicitly selected immutable artifact backup.

Backups are private operational data, not review-bundle content. A local hash
detects corruption; it does not authenticate against a full disk writer.
"""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
from contextlib import ExitStack

from .common import canonical_json, utc_now
from .storage import checked_payload


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def write_once(path, value):
    """Persist a backup manifest without replacing an existing backup."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(canonical_json(value).encode('utf-8'))
        stream.flush()
        os.fsync(stream.fileno())


def _backup_file(root: Path, relative: str) -> Path:
    """Manifest paths are canonical descendants before any filesystem lookup."""
    if (not isinstance(relative, str) or not relative or '\\' in relative
            or Path(relative).is_absolute() or '..' in Path(relative).parts
            or relative != Path(relative).as_posix() or relative == '.'):
        raise ValueError('BACKUP_PATH_OUTSIDE_ROOT')
    target = root
    for component in Path(relative).parts:
        target = target / component
        if target.is_symlink():
            raise ValueError('BACKUP_SYMLINK_UNSUPPORTED')
    if not target.resolve().is_relative_to(root):
        raise ValueError('BACKUP_PATH_OUTSIDE_ROOT')
    return target


def verify_backup(root: str | Path) -> dict:
    if Path(root).is_symlink():
        raise ValueError('BACKUP_SYMLINK_UNSUPPORTED')
    root = Path(root).resolve()
    manifest = json.loads(_backup_file(root, 'manifest.json').read_text(encoding='utf-8'))
    verified = []
    for record in manifest['files']:
        target = _backup_file(root, record['path'])
        if not target.is_file() or target.stat().st_size != record['size'] or file_hash(target) != record['sha256']:
            raise ValueError('BACKUP_FILE_MISMATCH')
        verified.append(record['path'])
    rows = 0
    if manifest['database'] is not None:
        db = _backup_file(root, manifest['database'])
        if manifest['database'] not in verified:
            raise ValueError('BACKUP_DATABASE_NOT_VERIFIED')
        with sqlite3.connect(db.as_uri() + '?mode=ro', uri=True) as connection:
            connection.row_factory = sqlite3.Row
            if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('BACKUP_SQLITE_INTEGRITY_FAILED')
            if connection.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('BACKUP_FOREIGN_KEY_FAILED')
            for table in ('documents', 'recommendation_records', 'evaluation_observations'):
                for row in connection.execute(f'SELECT payload,content_hash FROM {table}'):
                    checked_payload(row)
                    rows += 1
    return {'status': 'VERIFIED', 'verified_at': utc_now().isoformat(),
            'files': len(verified), 'immutable_payload_rows': rows,
            'scope': ('SQLITE_SNAPSHOT_AND_LISTED_EXTERNAL_BYTES' if manifest['database'] is not None
                      else 'LISTED_EXTERNAL_BYTES'),
            'runtime_path_semantics': 'ORIGINAL_ABSOLUTE_REFERENCES_PRESERVED_SEE_PATH_MAP',
            'integrity_guarantee': 'LOCAL_CORRUPTION_DETECTION_NOT_FULL_DISK_WRITER_AUTHENTICATION'}


def backup_state(database: str | Path | None, destination: str | Path, *, artifact_roots=()) -> dict:
    database = Path(database).resolve() if database is not None else None
    destination = Path(destination).resolve()
    roots = tuple(Path(p).resolve() for p in artifact_roots)
    if ((database is not None and not database.is_file()) or any(not p.is_dir() for p in roots)
            or (database is None and not roots)):
        raise ValueError('BACKUP_SOURCE_MISSING')
    if destination.exists() or destination == database or any(destination.is_relative_to(p) for p in roots):
        raise ValueError('BACKUP_DESTINATION_MUST_BE_NEW_AND_OUTSIDE_SOURCES')
    destination.mkdir(parents=True, exist_ok=False)
    records, path_map = [], []
    def copy(source, relative):
        if source.is_symlink():
            raise ValueError('BACKUP_SYMLINK_UNSUPPORTED')
        before = file_hash(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.open('rb') as check:
            sqlite_file=check.read(16)==b'SQLite format 3\x00'
        if sqlite_file:
            # Queue/transport SQLite databases also carry committed WAL. The
            # app DB write guard alone does not snapshot their main files.
            with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as src:
                with sqlite3.connect(target) as dst:
                    src.backup(dst)
                    if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('BACKUP_SQLITE_INTEGRITY_FAILED')
            records.append({'path':relative.as_posix(),'size':target.stat().st_size,'sha256':file_hash(target),'method':'SQLITE_ONLINE_BACKUP'})
            path_map.append({'original':str(source),'backup':relative.as_posix()})
            return
        with source.open('rb') as inp, target.open('xb') as out:
            shutil.copyfileobj(inp, out)
            out.flush()
            os.fsync(out.fileno())
        if before != file_hash(source) or before != file_hash(target):
            raise ValueError('ARTIFACT_CHANGED_DURING_BACKUP')
        records.append({'path': relative.as_posix(), 'size': target.stat().st_size, 'sha256': before})
        path_map.append({'original': str(source), 'backup': relative.as_posix()})
    # A reserved write lock serializes application DB writes. SQLite's backup
    # API includes committed WAL pages; copying only the .sqlite3 file does not.
    with ExitStack() as stack:
        from .jobs import _ServiceLock
        # CLI capture can run without an app job. Refuse an active queue and
        # hold every discovered OS lock through artifact + database snapshot.
        for root in roots:
            for lock_path in sorted(root.rglob('request-queue.lock')):
                lock=_ServiceLock(lock_path);stack.callback(lock.close)
        guard = None
        if database is not None:
            guard=stack.enter_context(sqlite3.connect(str(database), isolation_level=None, timeout=15))
            guard.execute('BEGIN IMMEDIATE')
        try:
            if guard is not None:
                active = guard.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('RUNNING','CANCEL_REQUESTED')").fetchone()[0]
                if active:
                    raise ValueError('BACKUP_REQUIRES_NO_ACTIVE_JOBS')
            for index, source_root in enumerate(roots):
                for source in sorted(source_root.rglob('*')):
                    if source.is_file():
                        if source.name.endswith(('.lock', '-wal', '-shm')):
                            continue
                        copy(source, Path('artifacts') / str(index) / source.relative_to(source_root))
            if database is not None:
                with sqlite3.connect(str(database), timeout=15) as source_conn:
                    with sqlite3.connect(str(destination / 'database.sqlite3')) as target_conn:
                        source_conn.backup(target_conn)
                target = destination / 'database.sqlite3'
                records.append({'path': target.name, 'size': target.stat().st_size, 'sha256': file_hash(target)})
        finally:
            if guard is not None:
                guard.rollback()
    manifest = {'version': 'wal-artifact-backup-v1', 'created_at': utc_now().isoformat(),
        'original_database': str(database) if database is not None else None,
        'database': 'database.sqlite3' if database is not None else None,
        'artifact_roots': [str(p) for p in roots], 'path_map': path_map,
        'files': sorted(records, key=lambda r: r['path']),
        'scope': ('COMMITTED_WAL_DATABASE_AND_EXPLICIT_ARTIFACT_ROOTS' if database is not None
                  else 'EXPLICIT_ARTIFACT_ROOTS_NO_APPLICATION_DATABASE'),
        'absolute_paths': 'Preserved inside immutable records. Isolated restoration verifies bytes; '
                          'run recovery requires original deployment paths or explicit application relocation.'}
    write_once(destination / 'manifest.json', manifest)
    verification = verify_backup(destination)
    write_once(destination / 'verification.json', verification)
    return verification


def restore_state(backup: str | Path, destination: str | Path) -> dict:
    if Path(backup).is_symlink():
        raise ValueError('BACKUP_SYMLINK_UNSUPPORTED')
    backup, destination = Path(backup).resolve(), Path(destination).resolve()
    verify_backup(backup)
    if destination.exists() or destination.is_relative_to(backup):
        raise ValueError('RESTORE_DESTINATION_MUST_BE_NEW_AND_OUTSIDE_BACKUP')
    manifest = json.loads(_backup_file(backup, 'manifest.json').read_text(encoding='utf-8'))
    # Validate the exact manifest used below before creating any destination.
    sources = [(record, _backup_file(backup, record['path'])) for record in manifest['files']]
    destination.mkdir(parents=True, exist_ok=False)
    for record, source in sources:
        target = _backup_file(destination, record['path'])
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.open('rb') as inp, target.open('xb') as out:
            shutil.copyfileobj(inp, out)
    write_once(destination / 'manifest.json', manifest)
    verification = verify_backup(destination)
    write_once(destination / 'restoration-verification.json', verification)
    return verification
