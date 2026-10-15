from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from stockfish_core.storage import SQLiteStore, ImmutableConflict, StorageError, IntegrityError


def test_wal_migration_reopen_and_append_only(tmp_path):
    path = tmp_path / "state.sqlite3"
    store = SQLiteStore(path)
    store.put_document("research", "one", {"完整": [1, 2], "v": 1})
    assert store.put_document("research", "one", {"v": 1, "完整": [1, 2]}) == "one"
    with pytest.raises(ImmutableConflict):
        store.put_document("research", "one", {"v": 2})
    for sql in ("UPDATE documents SET payload='{}'", "DELETE FROM documents"):
        with pytest.raises(StorageError):
            with store.transaction() as connection:
                connection.execute(sql)
    reopened = SQLiteStore(path)
    assert reopened.get_document("research", "one")["v"] == 1
    assert reopened.health()["journal_mode"] == "wal"
    assert reopened.health()["immutable_triggers"]
    with reopened.transaction(write=False) as connection:
        assert [row[0] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")] == [1, 2]


def test_transaction_rolls_back_all_documents(tmp_path):
    store = SQLiteStore(tmp_path / "state.sqlite3")
    with pytest.raises(ValueError):
        with store.transaction() as connection:
            store._put_document(connection, "n", "a", {"ok": True})
            raise ValueError("failure before commit")
    assert store.get_document("n", "a") is None


def test_concurrent_process_style_connections_are_lossless(tmp_path):
    path = tmp_path / "state.sqlite3"
    store = SQLiteStore(path)
    def write(number):
        return SQLiteStore(path).put_document("n", str(number), {"number": number})
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert len(list(pool.map(write, range(60)))) == 60
    assert len(store.list_documents("n")) == 60
    assert store.health()["status"] == "healthy"


def test_integrity_and_migration_tampering_fail_closed(tmp_path):
    path = tmp_path / "state.sqlite3"
    store = SQLiteStore(path)
    store.put_document("n", "a", {"v": 1})
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER documents_no_update")
        connection.execute("UPDATE documents SET payload='{}'")
    with pytest.raises(IntegrityError):
        store.get_document("n", "a")
    assert store.health()["status"] == "unavailable"
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE schema_migrations SET checksum='tampered'")
    with pytest.raises(IntegrityError):
        SQLiteStore(path)


def test_corrupt_database_and_invalid_json_never_claim_success(tmp_path):
    path = tmp_path / "corrupt.sqlite3"
    path.write_bytes(b"this is not sqlite")
    with pytest.raises(StorageError):
        SQLiteStore(path)
    store = SQLiteStore(tmp_path / "valid.sqlite3")
    with pytest.raises(ValueError):
        store.put_document("n", "nan", {"value": float("nan")})
    assert store.get_document("n", "nan") is None
