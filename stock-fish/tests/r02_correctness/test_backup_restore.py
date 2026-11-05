"""Isolated WAL and external artifacts; these are not user account backups."""
import json
import sqlite3

import pytest

from stockfish_core.snapshot_backup import backup_state, restore_state, verify_backup
from stockfish_core.storage import SQLiteStore


def test_backup_captures_committed_wal_and_external_bytes_then_restores(tmp_path):
    store = SQLiteStore(tmp_path / 'db.sqlite3')
    artifacts = tmp_path / 'source-artifacts'
    artifacts.mkdir()
    (artifacts / 'model.json').write_text('{"kind":"TEST_FIXTURE","coefficients":[1]}')
    (artifacts / 'nested').mkdir()
    (artifacts / 'nested' / 'receipt.json').write_text('{"source":"TEST_FIXTURE"}')
    # Keep one reader open so WAL remains present during the backup.
    with sqlite3.connect(store.path) as keeper:
        keeper.execute('PRAGMA wal_autocheckpoint=0')
        keeper.execute('BEGIN')
        keeper.execute('SELECT COUNT(*) FROM documents').fetchone()
        store.put_document('TEST_FIXTURE', 'wal-document', {'observed': True})
        assert store.path.with_name(store.path.name + '-wal').is_file()
        backup = tmp_path / 'backup'
        result = backup_state(store.path, backup, artifact_roots=(artifacts,))
    assert result['status'] == 'VERIFIED'
    assert result['files'] == 3 and result['immutable_payload_rows'] == 1
    restored = tmp_path / 'restored'
    assert restore_state(backup, restored)['status'] == 'VERIFIED'
    restored_store = SQLiteStore(restored / 'database.sqlite3')
    assert restored_store.get_document('TEST_FIXTURE', 'wal-document') == {'observed': True}
    assert (restored / 'artifacts/0/model.json').read_bytes() == (artifacts / 'model.json').read_bytes()
    assert restored_store.health()['status'] == 'healthy'
    with pytest.raises(ValueError, match='MUST_BE_NEW'):
        restore_state(backup, restored)
    with pytest.raises(ValueError, match='MUST_BE_NEW'):
        backup_state(store.path, backup, artifact_roots=(artifacts,))


def test_backup_tamper_is_not_restored_as_verified(tmp_path):
    store = SQLiteStore(tmp_path / 'db.sqlite3')
    store.put_document('TEST_FIXTURE', 'one', {'value': 1})
    backup_state(store.path, tmp_path / 'backup')
    (tmp_path / 'backup/database.sqlite3').write_bytes(b'corrupted fixture')
    with pytest.raises(ValueError, match='BACKUP_FILE_MISMATCH'):
        restore_state(tmp_path / 'backup', tmp_path / 'restored')
    assert not (tmp_path / 'restored').exists()


def test_artifact_only_backup_preserves_ledger_raw_and_embedded_wal(tmp_path):
    root = tmp_path / 'TEST_FIXTURE-state'
    root.mkdir()
    (root / 'operations.json').write_bytes(b'{"status":"FAILED","spent":17}')
    (root / 'raw.bin').write_bytes(b'TEST_FIXTURE\x00original')
    queue = root / 'queue.sqlite3'
    with sqlite3.connect(queue) as connection:
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('PRAGMA wal_autocheckpoint=0')
        connection.execute('CREATE TABLE budget (spent INTEGER NOT NULL)')
        connection.execute('INSERT INTO budget VALUES (17)')
        connection.commit()
        assert queue.with_name(queue.name + '-wal').is_file()
        backup = tmp_path / 'backup'
        result = backup_state(None, backup, artifact_roots=(root,))
    assert result['status'] == 'VERIFIED'
    assert result['scope'] == 'LISTED_EXTERNAL_BYTES'
    assert result['files'] == 3
    assert not (backup / 'database.sqlite3').exists()
    restored = tmp_path / 'restored'
    assert restore_state(backup, restored)['status'] == 'VERIFIED'
    assert not (restored / 'database.sqlite3').exists()
    assert (restored / 'artifacts/0/operations.json').read_bytes() == (root / 'operations.json').read_bytes()
    assert (restored / 'artifacts/0/raw.bin').read_bytes() == (root / 'raw.bin').read_bytes()
    with sqlite3.connect(restored / 'artifacts/0/queue.sqlite3') as connection:
        assert connection.execute('SELECT spent FROM budget').fetchone()[0] == 17
    with pytest.raises(ValueError, match='MUST_BE_NEW'):
        restore_state(backup, restored)
    (backup / 'artifacts/0/raw.bin').write_bytes(b'TEST_FIXTURE\x00changed!')
    with pytest.raises(ValueError, match='BACKUP_FILE_MISMATCH'):
        restore_state(backup, tmp_path / 'corrupt-restored')
    assert not (tmp_path / 'corrupt-restored').exists()


def test_artifact_only_backup_requires_explicit_sources(tmp_path):
    with pytest.raises(ValueError, match='BACKUP_SOURCE_MISSING'):
        backup_state(None, tmp_path / 'backup')
    assert not (tmp_path / 'backup').exists()


@pytest.mark.parametrize('path_kind', ['parent_back_into_backup', 'absolute_inside_backup'])
def test_manifest_path_cannot_escape_new_restore_destination(tmp_path, path_kind):
    source = tmp_path / 'TEST_FIXTURE-source'
    source.mkdir()
    (source / 'evidence.bin').write_bytes(b'TEST_FIXTURE original evidence')
    backup = tmp_path / 'backup'
    backup_state(None, backup, artifact_roots=(source,))
    manifest_path = backup / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    relative = manifest['files'][0]['path']
    manifest['files'][0]['path'] = ('../backup/' + relative if path_kind == 'parent_back_into_backup'
                                   else str(backup / relative))
    manifest_path.write_text(json.dumps(manifest))
    # The old verifier normalized both strings into a valid backup file, but
    # restoration used the unchecked string under a different directory.
    destination_parent = tmp_path / 'TEST_FIXTURE-other-parent'
    destination_parent.mkdir()
    destination = destination_parent / 'restored'
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    with pytest.raises(ValueError, match='BACKUP_PATH_OUTSIDE_ROOT'):
        verify_backup(backup)
    with pytest.raises(ValueError, match='BACKUP_PATH_OUTSIDE_ROOT'):
        restore_state(backup, destination)
    assert not destination.exists()
    assert list(destination_parent.iterdir()) == []
    assert {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()} == before


@pytest.mark.parametrize('alias', ['file', 'directory', 'manifest', 'backup'])
def test_backup_rejects_symlinks_before_path_resolution(tmp_path, alias):
    source = tmp_path / 'TEST_FIXTURE-source'
    source.mkdir()
    (source / 'evidence.bin').write_bytes(b'TEST_FIXTURE original evidence')
    backup = tmp_path / 'backup'
    backup_state(None, backup, artifact_roots=(source,))
    target = {'file': backup / 'artifacts/0/evidence.bin', 'directory': backup / 'artifacts/0',
              'manifest': backup / 'manifest.json', 'backup': backup}[alias]
    original = target.with_name(target.name + '-original')
    target.rename(original)
    target.symlink_to(original, target_is_directory=original.is_dir())
    destination = tmp_path / 'restored'
    with pytest.raises(ValueError, match='BACKUP_SYMLINK_UNSUPPORTED'):
        verify_backup(backup)
    with pytest.raises(ValueError, match='BACKUP_SYMLINK_UNSUPPORTED'):
        restore_state(backup, destination)
    assert not destination.exists()
