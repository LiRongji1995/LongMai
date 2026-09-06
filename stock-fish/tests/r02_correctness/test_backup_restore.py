"""Isolated WAL and external artifacts; these are not user account backups."""
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
