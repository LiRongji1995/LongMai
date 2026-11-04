"""TEST_FIXTURE WAL backups and saved-document restoration in new directories."""
import json
import hashlib
import gc
from pathlib import Path
import sqlite3

import pytest

from scripts import recovery_drill_v1 as drill
from stockfish_core.snapshot_backup import backup_state
from stockfish_core.storage import SQLiteStore
from stockfish_core.common import canonical_json


@pytest.fixture
def saved_backup(tmp_path):
    root = tmp_path / "TEST_FIXTURE_recovery"
    root.mkdir()
    database = root / "original.sqlite3"
    store = SQLiteStore(database)
    payload = {"state": "UNKNOWN", "return": None}
    store.put_document("TEST_FIXTURE", "saved-run", payload)
    digest = hashlib.sha256(canonical_json(payload).encode()).hexdigest()
    backup = root / "backup"
    backup_state(database, backup)
    # Legacy sqlite connection context managers commit but do not close. End
    # fixture construction before pinning a closed backup; no sidecar deletion,
    # checkpoint, fingerprint relaxation or source rewrite is performed.
    gc.collect()
    probe = root / "probe.json"
    probe.write_text(json.dumps({"schema": "RECOVERY_PROBE_V1", "data_class": "TEST_FIXTURE",
                                "documents": [{"namespace": "TEST_FIXTURE", "document_id": "saved-run",
                                               "expected_content_hash": digest}]}))
    return root, database, backup, probe


def test_restore_measures_time_preserves_unknown_and_does_not_open_source_db(saved_backup, monkeypatch):
    root, source, backup, probe = saved_backup
    opened = []
    connect = sqlite3.connect
    def tracked(value, *args, **kwargs):
        opened.append(str(value))
        assert str(source) not in str(value)
        assert str(backup / "database.sqlite3") not in str(value)
        return connect(value, *args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", tracked)
    result = drill.run_drill(backup, root / "drill", probe)
    assert result["status"] == "COMPLETE_FOR_DECLARED_SCOPE"
    assert result["source_database_opened"] is False and result["original_backup_unchanged"] is True
    assert result["historical_pit_restored"] is False
    assert result["source_capability"] == "REAUTHENTICATION_REQUIRED"
    assert result["rpo"]["status"] == "NOT_ESTABLISHED" and result["rpo"]["seconds"] is None
    assert result["rto"]["elapsed_seconds"] >= 0 and result["rto"]["operational_service_recovery"] == "NOT_ESTABLISHED"
    with connect((root / "drill/restored/database.sqlite3").as_uri() + "?mode=ro&immutable=1", uri=True) as connection:
        value = json.loads(connection.execute("SELECT payload FROM documents").fetchone()[0])
    assert value == {"return": None, "state": "UNKNOWN"}
    assert opened


def test_missing_required_result_is_a_failed_drill_not_zero_or_success(saved_backup):
    root, _, backup, probe = saved_backup
    value = json.loads(probe.read_bytes())
    value["documents"][0]["document_id"] = "missing-result"
    probe.write_text(json.dumps(value))
    result = drill.run_drill(backup, root / "drill", probe)
    assert result["status"] == "FAILED"
    assert result["document_probes"][0]["status"] == "MISSING"
    assert result["saved_result_readability"] == "INCOMPLETE_OR_MISMATCH"


def test_tampered_bytes_cannot_pass_and_failure_report_is_retained(saved_backup):
    root, _, backup, probe = saved_backup
    path = backup / "database.sqlite3"
    raw = bytearray(path.read_bytes())
    raw[-1] ^= 1
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        drill.run_drill(backup, root / "drill", probe)
    report = json.loads((root / "drill/drill-report.json").read_bytes())
    assert report["status"] == "FAILED" and report["rto"]["elapsed_seconds"] >= 0


@pytest.mark.parametrize("path", ["../outside", "/tmp/outside", "artifacts/../database.sqlite3"])
def test_manifest_path_escape_rejected_before_destination_creation(saved_backup, path):
    root, _, backup, probe = saved_backup
    manifest_path = backup / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["files"][0]["path"] = path
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="PATH_REJECTED"):
        drill.run_drill(backup, root / "drill", probe)
    assert not (root / "drill").exists()


def test_existing_target_and_byte_limit_refuse_before_restore(saved_backup):
    root, _, backup, probe = saved_backup
    target = root / "existing"
    target.mkdir()
    with pytest.raises(ValueError, match="MUST_BE_NEW"):
        drill.run_drill(backup, target, probe)
    with pytest.raises(ValueError, match="BYTE_LIMIT"):
        drill.run_drill(backup, root / "drill", probe, max_bytes=1)
    assert not (root / "drill").exists()


def test_symlink_source_is_rejected(saved_backup):
    root, _, backup, probe = saved_backup
    source = backup / "database.sqlite3"
    source.rename(backup / "renamed.sqlite3")
    source.symlink_to(backup / "renamed.sqlite3")
    with pytest.raises(ValueError, match="SYMLINK"):
        drill.run_drill(backup, root / "drill", probe)


def test_nonempty_wal_is_rejected_without_checkpoint_or_removal(saved_backup):
    root, _, backup, probe = saved_backup
    sidecar = backup / "database.sqlite3-wal"
    sidecar.write_bytes(b"TEST_FIXTURE_UNCOMMITTED_OR_UNMANIFESTED_WAL")
    with pytest.raises(ValueError, match="NONEMPTY_BACKUP_SIDECAR"):
        drill.run_drill(backup, root / "drill", probe)
    assert sidecar.read_bytes() == b"TEST_FIXTURE_UNCOMMITTED_OR_UNMANIFESTED_WAL"
    assert not (root / "drill").exists()


def test_source_replacement_during_publication_revokes_success(saved_backup, monkeypatch):
    from scripts import codex_p0
    root, _, backup, probe = saved_backup
    publish = codex_p0.publish_new
    def changed_before_publish(path, value, **kwargs):
        if path.name == "drill-report.json":
            source = backup / "database.sqlite3"
            replacement = backup / "replacement"
            replacement.write_bytes(source.read_bytes())
            replacement.replace(source)
        return publish(path, value, **kwargs)
    monkeypatch.setattr(codex_p0, "publish_new", changed_before_publish)
    with pytest.raises(ValueError, match="SOURCE_CHANGED"):
        drill.run_drill(backup, root / "drill", probe)
    assert not (root / "drill/drill-report.json").exists()
    failure = json.loads((root / "drill/drill-failure.json").read_bytes())
    assert failure["status"] == "FAILED" and failure["original_backup_unchanged"] is False
