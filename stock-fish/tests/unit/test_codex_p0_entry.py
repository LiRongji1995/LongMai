"""TEST_FIXTURE: bounded carriers, honest session denominators and no mutation."""
from contextlib import redirect_stdout
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import stat

import pytest

from scripts import codex_p0 as entry


def carrier(tmp_path, raw=b'{"coverage_type":"TEST_FIXTURE"}', name="TEST_FIXTURE.json"):
    path = tmp_path / name
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("compressed", [False, True])
def test_full_carrier_with_reference_and_terminal(tmp_path, compressed):
    raw = b'{"coverage_type":"TEST_FIXTURE","items":[1,2,3]}'
    path, sha = carrier(tmp_path, gzip.compress(raw) if compressed else raw,
                        "TEST_FIXTURE.json.gz" if compressed else "TEST_FIXTURE.json")
    original = path.read_bytes()
    value, reference = entry.read_snapshot(path, sha)
    assert value["items"] == [1, 2, 3]
    entry.verify_snapshots([reference])
    assert path.read_bytes() == original


def test_same_bytes_replaced_inode_rejected_at_terminal(tmp_path):
    path, sha = carrier(tmp_path)
    _, reference = entry.read_snapshot(path, sha)
    replacement = tmp_path / "replacement"
    replacement.write_bytes(path.read_bytes())
    replacement.replace(path)
    with pytest.raises(ValueError, match="IDENTITY_CHANGED"):
        entry.verify_snapshots([reference])


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"a":NaN}', b'[]', b'{'])
def test_invalid_or_nonfull_json_rejected(tmp_path, raw):
    path, sha = carrier(tmp_path, raw)
    with pytest.raises(ValueError):
        entry.read_snapshot(path, sha)


def test_gzip_decoding_is_bounded(tmp_path):
    path, sha = carrier(tmp_path, gzip.compress(b'{"large":"' + b'x'*4096 + b'"}'),
                        "TEST_FIXTURE.json.gz")
    with pytest.raises(ValueError, match="DECODED_JSON_SIZE_LIMIT"):
        entry.read_snapshot(path, sha, max_json=128)


def test_gzip_crc_and_header_rejected(tmp_path):
    raw = gzip.compress(b'{"coverage_type":"TEST_FIXTURE"}')
    path, sha = carrier(tmp_path, raw[:-4], "TEST_FIXTURE.json.gz")
    with pytest.raises(ValueError, match="INVALID_GZIP"):
        entry.read_snapshot(path, sha)
    path.write_bytes(b'{}')
    with pytest.raises(ValueError, match="GZIP_HEADER_REQUIRED"):
        entry.read_snapshot(path, hashlib.sha256(b'{}').hexdigest())


def test_wrong_expected_hash_and_stored_bound(tmp_path):
    path, sha = carrier(tmp_path)
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        entry.read_snapshot(path, "0"*64)
    with pytest.raises(ValueError, match="FILE_SIZE_LIMIT"):
        entry.read_snapshot(path, sha, max_stored=1)


def test_input_symlink_and_linked_ancestor_are_rejected(tmp_path):
    path, sha = carrier(tmp_path)
    link = tmp_path / "TEST_FIXTURE_link.json"
    link.symlink_to(path)
    with pytest.raises((ValueError, OSError)):
        entry.read_snapshot(link, sha)
    directory_link = tmp_path / "alias"
    directory_link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises((ValueError, OSError)):
        entry.read_snapshot(directory_link/path.name, sha)


def test_private_report_is_new_only_and_has_no_temp_residue(tmp_path):
    path = tmp_path / "TEST_FIXTURE_report.json"
    entry.publish_new(path, {"status": "UNKNOWN"})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    before = path.read_bytes()
    with pytest.raises(ValueError, match="EXISTING_REPORT_PRESERVED"):
        entry.publish_new(path, {"status": "COMPLETE"})
    assert path.read_bytes() == before
    assert not list(tmp_path.glob(".p0-*"))


@pytest.mark.parametrize("fault", ["directory_fsync", "temporary_unlink"])
def test_publication_failure_removes_only_its_new_report(tmp_path, monkeypatch, fault):
    path = tmp_path / "TEST_FIXTURE_failed_report.json"
    fsync, unlink = os.fsync, os.unlink
    failed = False
    def faulty_fsync(fd):
        nonlocal failed
        if fault == "directory_fsync" and not failed and stat.S_ISDIR(os.fstat(fd).st_mode):
            failed = True
            raise OSError("TEST_FIXTURE directory fsync failure")
        return fsync(fd)
    def faulty_unlink(name, **kwargs):
        nonlocal failed
        if fault == "temporary_unlink" and not failed and str(name).startswith(".p0-"):
            failed = True
            raise OSError("TEST_FIXTURE temporary unlink failure")
        return unlink(name, **kwargs)
    monkeypatch.setattr(os, "fsync", faulty_fsync)
    monkeypatch.setattr(os, "unlink", faulty_unlink)
    with pytest.raises(OSError):
        entry.publish_new(path, {"command_exit_code": 0})
    assert not path.exists()
    assert not list(tmp_path.glob(".p0-*"))


@pytest.mark.parametrize("mutate_on", [1, 2])
def test_publication_terminal_catches_input_replacement(tmp_path, mutate_on):
    source, sha = carrier(tmp_path)
    _, reference = entry.read_snapshot(source, sha)
    destination = tmp_path / "TEST_FIXTURE_publish.json"
    calls = 0
    def check():
        nonlocal calls
        calls += 1
        if calls == mutate_on:
            replacement = tmp_path / "TEST_FIXTURE_replacement"
            replacement.write_bytes(source.read_bytes())
            replacement.replace(source)
        entry.verify_snapshots([reference])
    with pytest.raises(ValueError, match="IDENTITY_CHANGED"):
        entry.publish_new(destination, {"command_exit_code": 0}, validate=check)
    assert not destination.exists()
    assert not list(tmp_path.glob(".p0-*"))


@pytest.mark.parametrize("replace_inode", [False, True])
def test_publication_terminal_rejects_changed_output_and_preserves_other_inode(tmp_path, replace_inode):
    destination = tmp_path / "TEST_FIXTURE_report.json"
    replacement = b'{"TEST_FIXTURE":"other_writer"}'
    calls = 0
    def check():
        nonlocal calls
        calls += 1
        if calls == 2:
            if replace_inode:
                destination.unlink()
            destination.write_bytes(replacement)
            destination.chmod(0o600)
    with pytest.raises(ValueError, match="IDENTITY_CHANGED|HASH_CHANGED"):
        entry.publish_new(destination, {"TEST_FIXTURE": "intended_report"}, validate=check)
    if replace_inode:
        assert destination.read_bytes() == replacement
    else:
        assert not destination.exists()
    assert not list(tmp_path.glob(".p0-*"))


def test_publication_terminal_rejects_moved_parent_and_rolls_back_owned_report(tmp_path):
    selected = tmp_path / "TEST_FIXTURE_parent"
    moved = tmp_path / "TEST_FIXTURE_moved_parent"
    selected.mkdir()
    destination = selected / "report.json"
    calls = 0
    def check():
        nonlocal calls
        calls += 1
        if calls == 2:
            selected.rename(moved)
            selected.mkdir()
            (selected / "TEST_FIXTURE_other_writer.json").write_bytes(b'{}')
    with pytest.raises(ValueError, match="PARENT_IDENTITY_CHANGED"):
        entry.publish_new(destination, {"TEST_FIXTURE": "intended_report"}, validate=check)
    assert not destination.exists()
    assert not (moved / destination.name).exists()
    assert (selected / "TEST_FIXTURE_other_writer.json").read_bytes() == b'{}'
    assert not list(moved.glob(".p0-*"))


@pytest.mark.parametrize("replace_after_link", [False, True])
def test_temporary_replacement_is_preserved_and_owned_publication_is_rolled_back(
        tmp_path, monkeypatch, replace_after_link):
    destination = tmp_path / "TEST_FIXTURE_report.json"
    replacement = b'{"TEST_FIXTURE":"other_temporary"}'
    temporary = None
    def replace_temporary():
        nonlocal temporary
        temporary, = tmp_path.glob(".p0-*")
        temporary.unlink()
        temporary.write_bytes(replacement)
    link = os.link
    if replace_after_link:
        def changed_link(*args, **kwargs):
            link(*args, **kwargs)
            replace_temporary()
        monkeypatch.setattr(os, "link", changed_link)
    with pytest.raises(ValueError, match="TEMPORARY_IDENTITY_CHANGED"):
        entry.publish_new(destination, {"TEST_FIXTURE": "intended_report"},
                          validate=None if replace_after_link else replace_temporary)
    assert not destination.exists()
    assert temporary.read_bytes() == replacement
    assert list(tmp_path.glob(".p0-*")) == [temporary]


def test_declared_sessions_preserve_absence_and_duplicate_requests():
    report = {"reports": [
        {"observation_date": "2026-10-26", "operation_id": "a", "workflow_status": "COMPLETE"},
        {"observation_date": "2026-10-26", "operation_id": "b", "workflow_status": "FAILED"},
        {"observation_date": "2026-10-28", "operation_id": "c", "workflow_status": "FAILED_OR_UNKNOWN"}]}
    result = entry.declared_session_coverage(report, ["2026-10-26", "2026-10-27", "2026-10-28"])
    assert [r["status"] for r in result["sessions"]] == [
        "MULTIPLE_OPERATIONS_RECONCILIATION_REQUIRED", "NO_SUPPORTED_DELIVERY_OPERATION_IN_SELECTED_SCOPE",
        "FAILED_OR_UNKNOWN"]
    assert result["complete_data_delivery_rate"] is None
    assert result["user_delivery_rate"] is None
    assert result["absence_does_not_prove_no_job"] is True
    assert result["continuous_delivery_accepted"] is False


def test_unsupported_operation_inventory_does_not_claim_absence():
    result = entry.declared_session_coverage(
        {"reports": [], "unsupported_or_other_operation_count": 1}, ["2026-10-26"])
    assert result["inventory_support_complete"] is False
    assert result["unsupported_or_other_operation_count"] == 1
    assert result["sessions"][0]["status"] == "NO_SUPPORTED_DELIVERY_OPERATION_IN_SELECTED_SCOPE"


def test_empty_expected_sessions_does_not_invent_rate():
    assert entry.declared_session_coverage({}, [])["declared_session_count"] == 0
    with pytest.raises(ValueError, match="UNIQUE_BOUNDED"):
        entry.declared_session_coverage({}, ["2026-10-26"]*2)


def test_execution_readiness_preserves_blocked_exit_and_carrier(tmp_path, monkeypatch):
    from scripts import codex_r2_execution_v1 as execution
    path, sha = carrier(tmp_path, b'{"coverage_type":"TEST_FIXTURE","content_hash":"test"}')
    monkeypatch.setattr(execution, "readiness", lambda run, **kwargs: {
        "status": "BLOCKED_DATA", "returns": None, "source_authority_restored": False})
    stream = io.StringIO()
    with redirect_stdout(stream):
        code = entry.main(["execution-readiness", "--run", str(path), "--run-file-sha256", sha])
    value = json.loads(stream.getvalue())
    assert code == value["command_exit_code"] == 4
    assert value["result"]["returns"] is None
    assert value["network_calls"] == 0
    assert value["business_database_opened"] is False
    assert len(list(tmp_path.iterdir())) == 1


def test_coverage_rechecks_input_after_computation(tmp_path, monkeypatch):
    from scripts import codex_research_coverage_v1 as coverage
    path, sha = carrier(tmp_path)
    output = tmp_path / "TEST_FIXTURE_output.json"
    def changed(*args, **kwargs):
        path.write_bytes(b'{"changed":true}')
        return {"status": "COMPLETE"}
    monkeypatch.setattr(coverage, "build_coverage_report", changed)
    with redirect_stdout(io.StringIO()):
        code = entry.main(["coverage", "--run", str(path), "--run-file-sha256", sha,
                           "--output", str(output)])
    assert code == 2
    assert not output.exists()


def test_bad_outcome_pair_rejected_before_any_read(tmp_path, monkeypatch):
    monkeypatch.setattr(entry, "read_snapshot", lambda *a, **k: pytest.fail("no input read expected"))
    with redirect_stdout(io.StringIO()):
        code = entry.main(["coverage", "--run", str(tmp_path/"absent"),
                           "--run-file-sha256", "0"*64, "--outcomes", "missing"])
    assert code == 2


def test_no_execute_or_retry_commands():
    for name in ("premarket", "retry", "run", "evaluate"):
        with pytest.raises(SystemExit):
            entry.parser().parse_args([name])
