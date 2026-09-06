"""Non-destructive SQLite WAL + explicitly selected immutable artifact backup.

Backups are private operational data, not review-bundle content. A local hash
detects corruption; it does not authenticate against a full disk writer.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import sqlite3

from .common import canonical_json, utc_now
from .model_registry import file_hash, write_once
from .storage import SQLiteStore, checked_payload


def verify_backup(root: str | Path) -> dict:
    root = Path(root).resolve()
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    verified = []
    for record in manifest['files']:
        target = (root / record['path']).resolve()
        if not target.is_relative_to(root) or target.is_symlink():
            raise ValueError('BACKUP_PATH_OUTSIDE_ROOT')
        if not target.is_file() or target.stat().st_size != record['size'] or file_hash(target) != record['sha256']:
            raise ValueError('BACKUP_FILE_MISMATCH')
        verified.append(record['path'])
    db = root / manifest['database']
    with sqlite3.connect(db.as_uri() + '?mode=ro', uri=True) as connection:
        connection.row_factory = sqlite3.Row
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('BACKUP_SQLITE_INTEGRITY_FAILED')
        if connection.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('BACKUP_FOREIGN_KEY_FAILED')
        rows = 0
        for table in ('documents', 'recommendation_records', 'evaluation_observations'):
            for row in connection.execute(f'SELECT payload,content_hash FROM {table}'):
                checked_payload(row)
                rows += 1
    return {'status': 'VERIFIED', 'verified_at': utc_now().isoformat(),
            'files': len(verified), 'immutable_payload_rows': rows,
            'scope': 'SQLITE_SNAPSHOT_AND_LISTED_EXTERNAL_BYTES',
            'runtime_path_semantics': 'ORIGINAL_ABSOLUTE_REFERENCES_PRESERVED_SEE_PATH_MAP',
            'integrity_guarantee': 'LOCAL_CORRUPTION_DETECTION_NOT_FULL_DISK_WRITER_AUTHENTICATION'}


def backup_state(database: str | Path, destination: str | Path, *, artifact_roots=()) -> dict:
    database, destination = Path(database).resolve(), Path(destination).resolve()
    roots = tuple(Path(p).resolve() for p in artifact_roots)
    if not database.is_file() or any(not p.is_dir() for p in roots):
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
        with source.open('rb') as inp, target.open('xb') as out:
            shutil.copyfileobj(inp, out)
        if before != file_hash(source) or before != file_hash(target):
            raise ValueError('ARTIFACT_CHANGED_DURING_BACKUP')
        records.append({'path': relative.as_posix(), 'size': target.stat().st_size, 'sha256': before})
        path_map.append({'original': str(source), 'backup': relative.as_posix()})
    # A reserved write lock serializes application DB writes. SQLite's backup
    # API includes committed WAL pages; copying only the .sqlite3 file does not.
    with sqlite3.connect(str(database), isolation_level=None, timeout=15) as guard:
        guard.execute('BEGIN IMMEDIATE')
        try:
            active = guard.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('RUNNING','CANCEL_REQUESTED')").fetchone()[0]
            if active:
                raise ValueError('BACKUP_REQUIRES_NO_ACTIVE_JOBS')
            for index, source_root in enumerate(roots):
                for source in sorted(source_root.rglob('*')):
                    if source.is_file():
                        if source.name.endswith(('.jobs.lock', '.sqlite3-wal', '.sqlite3-shm')):
                            continue
                        copy(source, Path('artifacts') / str(index) / source.relative_to(source_root))
            with sqlite3.connect(str(database), timeout=15) as source_conn:
                with sqlite3.connect(str(destination / 'database.sqlite3')) as target_conn:
                    source_conn.backup(target_conn)
            target = destination / 'database.sqlite3'
            records.append({'path': target.name, 'size': target.stat().st_size, 'sha256': file_hash(target)})
        finally:
            guard.rollback()
    manifest = {'version': 'wal-artifact-backup-v1', 'created_at': utc_now().isoformat(),
        'original_database': str(database), 'database': 'database.sqlite3',
        'artifact_roots': [str(p) for p in roots], 'path_map': path_map,
        'files': sorted(records, key=lambda r: r['path']),
        'scope': 'COMMITTED_WAL_DATABASE_AND_EXPLICIT_ARTIFACT_ROOTS',
        'absolute_paths': 'Preserved inside immutable records. Isolated restoration verifies bytes; '
                          'run recovery requires original deployment paths or explicit application relocation.'}
    write_once(destination / 'manifest.json', manifest)
    verification = verify_backup(destination)
    write_once(destination / 'verification.json', verification)
    return verification


def restore_state(backup: str | Path, destination: str | Path) -> dict:
    backup, destination = Path(backup).resolve(), Path(destination).resolve()
    verify_backup(backup)
    if destination.exists() or destination.is_relative_to(backup):
        raise ValueError('RESTORE_DESTINATION_MUST_BE_NEW_AND_OUTSIDE_BACKUP')
    manifest = json.loads((backup / 'manifest.json').read_text(encoding='utf-8'))
    destination.mkdir(parents=True, exist_ok=False)
    for record in manifest['files']:
        relative = Path(record['path'])
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with (backup / relative).open('rb') as inp, target.open('xb') as out:
            shutil.copyfileobj(inp, out)
    write_once(destination / 'manifest.json', manifest)
    verification = verify_backup(destination)
    write_once(destination / 'restoration-verification.json', verification)
    return verification
