"""Owned TEST_FIXTURE journals only; no real data or network operations."""
from copy import deepcopy
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import sqlite3
import tarfile

import pytest

from scripts import codex_failed_theme_coldstore_v1 as cold
from stockfish_core.yaogu.evidence_audit_spool_v2 import _RowJournal


@pytest.fixture(autouse=True)
def no_network_or_database(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("TEST_FIXTURE_EXTERNAL_IO_FORBIDDEN")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)


def save(path, value):
    path.write_bytes(cold.raw_json(value))


def ref(path):
    return cold.reference(path, cold.Budget())


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "TEST_FIXTURE_state"
    job = root / "jobs" / "TEST_FIXTURE_failed_original"
    (job / "theme").mkdir(parents=True)
    (job / "core").mkdir()
    (job / "core" / "TEST_FIXTURE_keep.txt").write_text("TEST_FIXTURE_CORE_KEEP")
    (root / "premarket.lock").write_bytes(b'{"TEST_FIXTURE":"original_lock_preserved"}')
    save(job / "configuration.json", {"state_root": str(root), "fixture": "TEST_FIXTURE"})
    save(job / "code-registry.json", {"fixture": "TEST_FIXTURE"})
    save(job / "final-control.json", dict(schema_version="CODEX_PREMARKET_SHARED_WORKFLOW_V1",
        status="BUDGET_EXCEEDED", job=str(job), external_worker_reaped=True,
        external_worker_exit_code=-15, cleanup_error=None, display=None,
        error={"reason": "TEST_FIXTURE_TIMEOUT"}))
    journal = _RowJournal(job / "theme" / "price-journal", segment_bytes=220)
    for index in range(5):
        row = {"fixture": "TEST_FIXTURE", "number": index, "value": "z"*80}
        raw = cold.raw_json(row)
        journal[hashlib.sha256(raw).hexdigest()] = row
    journal.close()
    protected = {p: ref(p) for p in (root / "premarket.lock", job / "final-control.json",
        job / "configuration.json", job / "code-registry.json", job / "core" / "TEST_FIXTURE_keep.txt",
        job / "theme" / "price-journal" / "manifest.json")}
    return dict(root=root, job=job, journal=job / "theme" / "price-journal", protected=protected, parent=tmp_path)


def prepared(source):
    result = cold.prepare(source["root"], source["job"], source["parent"] / "TEST_FIXTURE_plan")
    return result["plan"]


def packed(source):
    plan = prepared(source)
    directory = source["parent"] / "TEST_FIXTURE_archive"
    cold.pack(plan["path"], plan["sha256"], directory)
    return directory, plan


def verified(source):
    directory, plan = packed(source)
    destination = source["parent"] / "TEST_FIXTURE_verification_copy"
    cold.verify(directory, plan["sha256"], destination)
    return directory, plan, destination, ref(directory / "verify.receipt.json")


def dependency_review(source, plan):
    wrapper = source["parent"] / "TEST_FIXTURE_wrapper.py"
    audit = source["parent"] / "TEST_FIXTURE_dependency_audit.json"
    wrapper.write_text("# TEST_FIXTURE explicit operator review wrapper\n")
    save(audit, {"role": "TEST_FIXTURE_managed_scope_audit"})
    target = source["parent"] / "TEST_FIXTURE_dependency_review.json"
    save(target, dict(schema_version="FAILED_THEME_SCOPED_DEPENDENCY_REVIEW_V1", plan_sha256=plan["sha256"],
        job=str(source["job"]), journal=str(source["journal"]), no_successful_consumer_in_reviewed_scope=True,
        no_active_managed_owner=True, global_absence_claimed=False, reviewed_scope="TEST_FIXTURE managed roots only",
        managed_roots=[str(source["root"])], root_wrapper_resource_lock_held=True,
        wrapper_script=ref(wrapper), audit_receipt=ref(audit)))
    return ref(target)


def retire(source, directory, plan, verification):
    review = dependency_review(source, plan)
    return cold.retire(directory, plan["sha256"], verification["path"], verification["sha256"],
        review["path"], review["sha256"], allow_retire_payloads=True)


def assert_protected(source):
    for original in source["protected"].values():
        cold.verify_ref(original, cold.Budget())


def test_full_pack_restore_retire_and_explicit_copy_release(source):
    original_payloads = {p.name: p.read_bytes() for p in source["journal"].iterdir() if p.name != "manifest.json"}
    directory, plan, destination, verification = verified(source)
    assert {p.name: p.read_bytes() for p in (destination / "journal").iterdir() if p.name != "manifest.json"} == original_payloads
    result = retire(source, directory, plan, verification)
    assert result["status"] == "RETIRED_ARCHIVE_ONLY"
    assert result["payload_bytes_removed"] == sum(map(len, original_payloads.values()))
    assert {p.name for p in source["journal"].iterdir()} == {"manifest.json"}
    assert_protected(source)
    cold.release_verification_copy(directory, plan["sha256"], verification["path"], verification["sha256"],
                                  allow_release_verification_copy=True)
    assert {p.name for p in (destination / "journal").iterdir()} == {"manifest.json"}
    assert (destination / "restore-map.json").is_file()
    assert (directory / "archive.tar.gz").is_file()
    manual = source["parent"] / "TEST_FIXTURE_manual_restore"
    result = cold.restore(directory, plan["sha256"], manual)
    assert result["status"] == "RESTORED_BYTES_ONLY" and result["source_capability_restored"] is False
    assert {p.name: p.read_bytes() for p in (manual / "journal").iterdir() if p.name != "manifest.json"} == original_payloads
    assert fp_values(manual / "journal" / "record-index.ndjson") != fp_values(destination / "journal" / "manifest.json")
    assert_protected(source)
    with pytest.raises(ValueError, match="NEW_DIRECTORY_REQUIRED"):
        cold.restore(directory, plan["sha256"], manual)


def fp_values(path):
    return cold.fp(path)


@pytest.mark.parametrize("fault", ("extra", "symlink", "hardlink", "changed", "manifest_sha", "manifest_fp", "gap", "extra_manifest_name"))
def test_prepare_refuses_nonexact_or_unpinned_journal(source, fault):
    journal = source["journal"]
    target = journal / "rows-000000.ndjson"
    if fault == "extra":
        (journal / "TEST_FIXTURE_extra").write_text("x")
    elif fault == "symlink":
        outside = source["parent"] / "TEST_FIXTURE_link_target"
        target.rename(outside)
        target.symlink_to(outside)
    elif fault == "hardlink":
        os.link(target, source["parent"] / "TEST_FIXTURE_hardlink")
    elif fault == "changed":
        target.write_text("TEST_FIXTURE_changed")
    else:
        manifest = json.loads((journal / "manifest.json").read_text())
        if fault == "manifest_sha":
            manifest["files"][target.name]["sha256"] = "0"*64
        elif fault == "manifest_fp":
            manifest["files"][target.name]["fp"]["st_ino"] += 1
        elif fault == "gap":
            manifest["files"]["rows-000100.ndjson"] = manifest["files"].pop(target.name)
        else:
            manifest["files"]["../TEST_FIXTURE_outside"] = manifest["files"][target.name]
        save(journal / "manifest.json", manifest)
    with pytest.raises(ValueError):
        prepared(source)
    assert not (source["parent"] / "TEST_FIXTURE_plan" / "plan.json").exists()


@pytest.mark.parametrize("fault", ("success", "not_reaped", "exit_zero", "cleanup_error", "other_job",
                                    "completion", "final_completion", "parent", "context", "display", "source_state"))
def test_prepare_requires_failed_reaped_owned_job_without_successful_outputs(source, fault):
    job = source["job"]
    if fault in ("parent", "context", "display"):
        target = job / {"parent": "theme/native-theme-parent.json.gz", "context": "theme/theme-context.json", "display": "display"}[fault]
        target.write_text("TEST_FIXTURE_success_output")
    elif fault == "final_completion":
        final = json.loads((job / "final-control.json").read_text())
        final["completion"] = {"path": "TEST_FIXTURE_missing_completion"}
        save(job / "final-control.json", final)
    elif fault == "completion":
        save(job / "worker-completion.json", {"status": "COMPLETED_WITH_UNKNOWNS"})
    elif fault == "source_state":
        save(job / "configuration.json", {"state_root": str(source["parent"])})
    else:
        final = json.loads((job / "final-control.json").read_text())
        key, value = {"success": ("status", "COMPLETE"), "not_reaped": ("external_worker_reaped", False),
            "exit_zero": ("external_worker_exit_code", 0), "cleanup_error": ("cleanup_error", {"role": "TEST_FIXTURE"}),
            "other_job": ("job", "TEST_FIXTURE_other_job")}[fault]
        final[key] = value
        save(job / "final-control.json", final)
    with pytest.raises(ValueError):
        prepared(source)


def test_state_lock_and_directory_alias_refuse(source):
    with cold.lock(source["root"] / "premarket.lock", cold.Budget()):
        with pytest.raises(BlockingIOError):
            prepared(source)
    alias = source["parent"] / "TEST_FIXTURE_alias"
    alias.symlink_to(source["root"], target_is_directory=True)
    with pytest.raises(ValueError, match="ALIASED_PATH"):
        cold.prepare(alias, source["job"], source["parent"] / "TEST_FIXTURE_rejected")
    assert_protected(source)


def test_pack_rechecks_current_terminal_and_all_payload_bytes(source):
    plan = prepared(source)
    (source["journal"] / "rows-000000.ndjson").write_text("TEST_FIXTURE_changed_after_plan")
    archive = source["parent"] / "TEST_FIXTURE_archive"
    with pytest.raises(ValueError):
        cold.pack(plan["path"], plan["sha256"], archive)
    receipt = json.loads((archive / "pack.receipt.json").read_text())
    assert receipt["status"] == "FAILED_PARTIAL_RETAINED"
    assert (source["journal"] / "record-index.ndjson").exists()
    with pytest.raises(ValueError):
        cold.pack(plan["path"], plan["sha256"], archive)


def replace_archive(directory, transform):
    target = directory / "archive.tar.gz"
    old = target.read_bytes()
    target.write_bytes(transform(old))
    packed = json.loads((directory / "pack.receipt.json").read_text())
    packed["archive"] = ref(target)
    # Explicit TEST_FIXTURE forged parent metadata tests the independent stream
    # verifier, not just the ordinary physical hash mismatch guard.
    save(directory / "pack.receipt.json", packed)


@pytest.mark.parametrize("fault", ("crc", "truncated", "tail", "link", "traversal", "extra", "duplicate", "pax", "member_byte"))
def test_full_tar_stream_crc_members_and_links_are_strict(source, fault):
    directory, plan = packed(source)
    def transform(raw):
        if fault == "crc":
            data = bytearray(raw); data[-8] ^= 1; return bytes(data)
        if fault == "truncated":
            return raw[:-4]
        decoded = gzip.decompress(raw)
        if fault == "tail":
            return gzip.compress(decoded+b"TEST_FIXTURE_EXTRA")
        if fault == "member_byte":
            changed = bytearray(decoded); changed[512] ^= 1; return gzip.compress(changed)
        original = []
        with tarfile.open(fileobj=io.BytesIO(decoded), mode="r:") as archive:
            for item in archive:
                original.append((item, archive.extractfile(item).read()))
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            for info, value in original:
                archive.addfile(info, io.BytesIO(value))
            info = tarfile.TarInfo({"traversal": "../TEST_FIXTURE_escape", "extra": "journal/rows-999999.ndjson",
                                   "duplicate": "plan.json"}.get(fault, "journal/TEST_FIXTURE_bad"))
            if fault == "link":
                info.type, info.linkname = tarfile.SYMTYPE, "/TEST_FIXTURE_outside"
            elif fault == "pax":
                info.type = tarfile.XHDTYPE
            archive.addfile(info, io.BytesIO(b""))
        return gzip.compress(output.getvalue())
    replace_archive(directory, transform)
    destination = source["parent"] / "TEST_FIXTURE_bad_restore"
    with pytest.raises((ValueError, EOFError, gzip.BadGzipFile, tarfile.TarError)):
        cold.verify(directory, plan["sha256"], destination)
    assert json.loads((directory / "verify.receipt.json").read_text())["status"] == "FAILED_PARTIAL_RETAINED"
    assert_protected(source)


@pytest.mark.parametrize("fault", ("receipt_sha", "receipt_status", "restored_copy", "source", "scope", "review", "success_appears"))
def test_retire_requires_fresh_source_restore_and_explicit_bound_reviews(source, fault):
    directory, plan, destination, verification = verified(source)
    review = dependency_review(source, plan)
    if fault == "receipt_sha":
        verification["sha256"] = "0"*64
    elif fault == "receipt_status":
        value = json.loads(Path(verification["path"]).read_text())
        value["status"] = "PACK_ONLY"
        save(Path(verification["path"]), value)
        verification = ref(Path(verification["path"]))
    elif fault == "restored_copy":
        (destination / "journal" / "record-index.ndjson").write_text("TEST_FIXTURE_changed")
    elif fault == "source":
        (source["journal"] / "rows-000000.ndjson").write_text("TEST_FIXTURE_changed")
    elif fault == "scope":
        (source["journal"] / "TEST_FIXTURE_extra").write_text("x")
    elif fault == "success_appears":
        save(source["job"] / "worker-completion.json", {"status": "COMPLETE"})
    else:
        value = json.loads(Path(review["path"]).read_text())
        value["global_absence_claimed"] = True
        save(Path(review["path"]), value)
        review = ref(Path(review["path"]))
    with pytest.raises(ValueError):
        cold.retire(directory, plan["sha256"], verification["path"], verification["sha256"],
            review["path"], review["sha256"], allow_retire_payloads=True)
    assert (source["journal"] / "record-index.ndjson").exists()
    assert json.loads((directory / "retire.receipt.json").read_text())["status"] == "FAILED_PARTIAL_RETAINED"


def test_interrupt_during_retire_retains_failure_and_prevents_retry(source, monkeypatch):
    directory, plan, _, verification = verified(source)
    review = dependency_review(source, plan)
    unlink = os.unlink
    calls = []
    def interrupted(name, **kwargs):
        if calls:
            raise KeyboardInterrupt("TEST_FIXTURE_retire_interruption")
        unlink(name, **kwargs)
        calls.append(name)
    monkeypatch.setattr(cold.os, "unlink", interrupted)
    with pytest.raises(KeyboardInterrupt):
        cold.retire(directory, plan["sha256"], verification["path"], verification["sha256"],
            review["path"], review["sha256"], allow_retire_payloads=True)
    outcome = json.loads((directory / "retire.receipt.json").read_text())
    assert outcome["status"] == "FAILED_PARTIAL_RETAINED" and len(outcome["removed_payloads"]) == 1
    assert (directory / "archive.tar.gz").exists()
    assert_protected(source)
    with pytest.raises(FileExistsError):
        cold.retire(directory, plan["sha256"], verification["path"], verification["sha256"],
            review["path"], review["sha256"], allow_retire_payloads=True)


def test_last_seal_time_failure_cannot_leave_success_receipt(source, monkeypatch):
    directory, plan, _, verification = verified(source)
    review = dependency_review(source, plan)
    clock = [0.0]
    monkeypatch.setattr(cold.time, "monotonic", lambda: clock[0])
    native_sync = cold.sync_dir
    def delayed(directory_path):
        native_sync(directory_path)
        receipt = directory / "retire.receipt.json"
        if Path(directory_path) == directory and receipt.exists():
            value = json.loads(receipt.read_text())
            if value.get("status") == "RETIRED_ARCHIVE_ONLY":
                clock[0] = 901
    monkeypatch.setattr(cold, "sync_dir", delayed)
    with pytest.raises(ValueError, match="TIME_BUDGET"):
        cold.retire(directory, plan["sha256"], verification["path"], verification["sha256"],
            review["path"], review["sha256"], allow_retire_payloads=True)
    assert json.loads((directory / "retire.receipt.json").read_text())["status"] == "FAILED_PARTIAL_RETAINED"
    assert_protected(source)


def test_release_is_explicit_only_own_verified_copy_after_retirement(source):
    directory, plan, destination, verification = verified(source)
    with pytest.raises(ValueError, match="EXPLICIT_VERIFICATION"):
        cold.release_verification_copy(directory, plan["sha256"], verification["path"], verification["sha256"])
    assert (destination / "journal" / "record-index.ndjson").exists()
    with pytest.raises(FileNotFoundError):
        cold.release_verification_copy(directory, plan["sha256"], verification["path"], verification["sha256"],
                                      allow_release_verification_copy=True)
    assert (destination / "journal" / "record-index.ndjson").exists()


@pytest.mark.parametrize("key", cold.DEFAULTS)
def test_budget_limits_fail_before_source_change(source, key):
    limits = dict(cold.DEFAULTS)
    limits[key] = 1
    if key == "seconds":
        with pytest.raises(ValueError):
            cold.Budget({"seconds": cold.DEFAULTS["seconds"]+1})
    else:
        with pytest.raises(ValueError):
            cold.prepare(source["root"], source["job"], source["parent"] / "TEST_FIXTURE_limited", limits=limits)
    assert_protected(source)


def test_unlink_intent_fsync_is_followed_by_physical_recheck(source, monkeypatch):
    directory, plan, _, verification = verified(source)
    review = dependency_review(source, plan)
    native_fsync = os.fsync
    changed = []
    def inject(fd):
        native_fsync(fd)
        events = directory / "retire-events.ndjson"
        if events.exists() and events.stat().st_size and not changed:
            row = json.loads(events.read_text().splitlines()[-1])
            if row["action"] == "PRE_UNLINK":
                (source["journal"] / row["name"]).write_text("TEST_FIXTURE_replaced_after_intent")
                changed.append(row["name"])
    monkeypatch.setattr(cold.os, "fsync", inject)
    with pytest.raises(ValueError, match="PAYLOAD_CHANGED_AFTER_UNLINK_INTENT"):
        cold.retire(directory, plan["sha256"], verification["path"], verification["sha256"],
            review["path"], review["sha256"], allow_retire_payloads=True)
    assert changed and (source["journal"] / changed[0]).exists()
    receipt = json.loads((directory / "retire.receipt.json").read_text())
    assert receipt["status"] == "FAILED_PARTIAL_RETAINED" and receipt["removed_payloads"] == []
    assert_protected(source)


def test_release_does_not_touch_manual_restoration_and_rejects_copy_extra_file(source):
    directory, plan, destination, verification = verified(source)
    retire(source, directory, plan, verification)
    manual = source["parent"] / "TEST_FIXTURE_unrelated_manual_copy"
    cold.restore(directory, plan["sha256"], manual)
    manual_refs = [ref(p) for p in (manual / "journal").iterdir()]
    (destination / "journal" / "TEST_FIXTURE_extra").write_text("TEST_FIXTURE_keep")
    with pytest.raises(ValueError, match="VERIFICATION_JOURNAL_SCOPE_CHANGED"):
        cold.release_verification_copy(directory, plan["sha256"], verification["path"], verification["sha256"],
                                      allow_release_verification_copy=True)
    assert (destination / "journal" / "record-index.ndjson").exists()
    for item in manual_refs:
        cold.verify_ref(item, cold.Budget())


def test_failed_prepare_plan_cannot_be_used_for_pack(source, monkeypatch):
    native_check = cold.check_source
    def fail_after_plan(*args, **kwargs):
        native_check(*args, **kwargs)
        raise ValueError("TEST_FIXTURE_prepare_terminal_failure")
    monkeypatch.setattr(cold, "check_source", fail_after_plan)
    with pytest.raises(ValueError, match="prepare_terminal_failure"):
        prepared(source)
    plan = ref(source["parent"] / "TEST_FIXTURE_plan" / "plan.json")
    with pytest.raises(ValueError, match="SUCCESSFUL_PREPARATION_RECEIPT_REQUIRED"):
        cold.pack(plan["path"], plan["sha256"], source["parent"] / "TEST_FIXTURE_rejected_pack")
    assert_protected(source)


@pytest.mark.parametrize("key", ("read_bytes", "output_bytes"))
def test_verify_bounds_all_stream_read_and_restoration_output(source, key):
    directory, plan = packed(source)
    limits = dict(cold.DEFAULTS)
    limits[key] = Path(cold.__file__).stat().st_size+cold.RESERVE+1 if key == "read_bytes" else 40000
    with pytest.raises(ValueError, match="READ_BUDGET|OUTPUT_BUDGET"):
        cold.verify(directory, plan["sha256"], source["parent"] / "TEST_FIXTURE_limited_restore", limits=limits)
    assert json.loads((directory / "verify.receipt.json").read_text())["status"] == "FAILED_PARTIAL_RETAINED"
    assert_protected(source)


@pytest.mark.parametrize("fault", ("bytes_after_seal", "inode_after_seal", "inode_after_final_seal"))
def test_implementation_terminal_check_includes_success_receipt_publication(tmp_path, monkeypatch, fault):
    directory = tmp_path / "TEST_FIXTURE_owned_operation"
    directory.mkdir()
    implementation = tmp_path / "TEST_FIXTURE_implementation.py"
    implementation.write_text("# TEST_FIXTURE original bytes\n")
    implementation_ref = ref(implementation)
    native_sync = cold.sync_dir
    seals = []
    def change_after_seal(target):
        native_sync(target)
        receipt = directory / "TEST_FIXTURE.receipt.json"
        if receipt.exists() and json.loads(receipt.read_text()).get("status") == "TEST_FIXTURE_COMPLETE":
            seals.append(True)
            if len(seals) == (2 if fault == "inode_after_final_seal" else 1):
                if fault == "bytes_after_seal":
                    implementation.write_text("# TEST_FIXTURE changed bytes\n")
                else:
                    replacement = tmp_path / "TEST_FIXTURE_replacement.py"
                    replacement.write_bytes(implementation.read_bytes())
                    replacement.replace(implementation)
    monkeypatch.setattr(cold, "sync_dir", change_after_seal)
    with pytest.raises(ValueError):
        with cold.operation(directory, "TEST_FIXTURE", cold.Budget(), implementation=implementation_ref) as result:
            result["status"] = "TEST_FIXTURE_COMPLETE"
    assert json.loads((directory / "TEST_FIXTURE.receipt.json").read_text())["status"] == "FAILED_PARTIAL_RETAINED"


@pytest.mark.parametrize("stage", ("pack", "verify", "retire", "release"))
def test_relevant_artifact_changed_after_success_seal_cannot_remain_success(source, monkeypatch, stage):
    if stage == "pack":
        plan = prepared(source)
        directory = source["parent"] / "TEST_FIXTURE_archive"
        verification = None
    else:
        directory, plan, _, verification = verified(source)
        if stage == "release":
            retire(source, directory, plan, verification)
    if stage == "verify":
        # Use a fresh fully packed archive; the existing operation must not be retried.
        source2 = source["parent"] / "TEST_FIXTURE_verify_archive"
        cold.pack(plan["path"], plan["sha256"], source2)
        directory = source2
    review = dependency_review(source, plan) if stage == "retire" else None
    statuses = dict(pack="PACKED_ORIGINALS_RETAINED", verify="FULL_ARCHIVE_AND_ENTITY_RESTORE_VERIFIED",
                    retire="RETIRED_ARCHIVE_ONLY", release="OWN_VERIFICATION_PAYLOAD_COPY_RELEASED")
    changed = []
    sync = cold.sync_dir
    def change(target):
        sync(target)
        receipt = directory / (stage+".receipt.json")
        if not changed and Path(target) == directory and receipt.exists():
            document = json.loads(receipt.read_text())
            if document.get("status") == statuses[stage]:
                changed.append(True)
                artifact = Path(document["mapping"]["path"]) if stage == "verify" else directory / "archive.tar.gz"
                with artifact.open("ab") as handle:
                    handle.write(b"TEST_FIXTURE_CHANGED_AFTER_SUCCESS_SEAL")
    monkeypatch.setattr(cold, "sync_dir", change)
    with pytest.raises(ValueError):
        if stage == "pack":
            cold.pack(plan["path"], plan["sha256"], directory)
        elif stage == "verify":
            cold.verify(directory, plan["sha256"], source["parent"] / "TEST_FIXTURE_second_verify_copy")
        elif stage == "retire":
            cold.retire(directory, plan["sha256"], verification["path"], verification["sha256"],
                review["path"], review["sha256"], allow_retire_payloads=True)
        else:
            cold.release_verification_copy(directory, plan["sha256"], verification["path"], verification["sha256"],
                                          allow_release_verification_copy=True)
    assert changed
    assert json.loads((directory / (stage+".receipt.json")).read_text())["status"] == "FAILED_PARTIAL_RETAINED"


@pytest.mark.parametrize("fault", ("append_space", "replacement_inode"))
def test_changed_success_receipt_is_rejected_and_cannot_authorize_next_stage(tmp_path, monkeypatch, fault):
    directory = tmp_path / "TEST_FIXTURE_receipt_tail"
    directory.mkdir()
    implementation = tmp_path / "TEST_FIXTURE_code.py"
    implementation.write_text("# TEST_FIXTURE\n")
    pin = ref(implementation)
    seals = []
    sync = cold.sync_dir
    def change(target):
        sync(target)
        receipt = directory / "prepare.receipt.json"
        if receipt.exists() and json.loads(receipt.read_text()).get("status") == "PREPARED_NO_SOURCE_CHANGES":
            seals.append(True)
            if len(seals) == 2:
                if fault == "append_space":
                    with receipt.open("ab") as handle:
                        handle.write(b" ")
                else:
                    replacement = directory / "TEST_FIXTURE_foreign_receipt"
                    replacement.write_bytes(receipt.read_bytes())
                    replacement.replace(receipt)
    monkeypatch.setattr(cold, "sync_dir", change)
    with pytest.raises(ValueError):
        with cold.operation(directory, "prepare", cold.Budget(), implementation=pin) as result:
            result["status"] = "PREPARED_NO_SOURCE_CHANGES"
    if fault == "append_space":
        assert json.loads((directory / "prepare.receipt.json").read_text())["status"] == "FAILED_PARTIAL_RETAINED"
        assert (directory / "prepare.rejected.json").stat().st_size == cold.RESERVE+1
    else:
        # Do not overwrite the replacement inode. Its old positive JSON has no
        # authority because its exact pinned UNUSED failure slot was revoked.
        with pytest.raises(ValueError):
            cold.load(directory / "prepare.receipt.json", cold.Budget())
        assert json.loads((directory / "prepare.failure-reserve.json").read_text())["status"] == "FAILED_PARTIAL_RETAINED"
