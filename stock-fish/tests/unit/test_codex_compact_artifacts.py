"""TEST_FIXTURE byte-storage tests; no network, real data, DB, or strategy."""
from copy import deepcopy
import gzip
import hashlib
import json
import os
from pathlib import Path
import pickle
import socket
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import codex_compact_artifacts as compact


class CompactTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="stockfish-compact-TEST_FIXTURE-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        for guard in (patch.object(socket.socket, "connect", side_effect=AssertionError("TEST_FIXTURE_NETWORK")),
                      patch.object(sqlite3, "connect", side_effect=AssertionError("TEST_FIXTURE_DB"))):
            guard.start()
            self.addCleanup(guard.stop)
        self.calls = 0

    def ref(self, path):
        return compact._ref(path, compact._Meter(compact.CompactLimits()))

    def fixture(self, size=10000):
        stage = compact.create_staging(self.root)
        directory = stage.core_dir
        self.assertFalse(directory.exists())
        directory.mkdir()
        payload = (b'TEST_FIXTURE,UNKNOWN,null,"row 3 then row 1"\r\n' * size)
        values = {role: payload + role.encode() for role in compact.CORE_FILES}
        values["observation.json"] = json.dumps({"coverage_type": "TEST_FIXTURE", "items": [
            {"code": "3", "status": "UNKNOWN", "value": None, "name": "未确认"},
            {"code": "1", "status": "WATCH", "value": 1.20}]}, ensure_ascii=False).encode()
        for role in ("canonical-run.json.gz", "input.json.gz"):
            values[role] = gzip.compress(values[role], mtime=0)
        for role, value in values.items():
            (directory / role).write_bytes(value)
        refs = {role: self.ref(directory / role) for role in values}
        return stage, directory, refs, values

    def terminal(self):
        self.calls += 1
        return True

    def pack(self, stage, refs, **kwargs):
        return compact.compact_owned(stage, refs, destination=self.root / "compact",
                                     terminal_validator=self.terminal, **kwargs)

    def rewrite_manifest(self, result, edit):
        path = Path(result["manifest"]["path"])
        value = json.loads(path.read_bytes())
        edit(value)
        path.write_text(json.dumps(value))
        return self.ref(path)

    def test_exact_eight_roundtrip_order_unknown_and_real_disk_reduction(self):
        stage, directory, refs, values = self.fixture()
        old = self.root / "old-run"
        old.mkdir()
        (old / "original").write_bytes(b"TEST_FIXTURE never change this old evidence")
        old_ref = self.ref(old / "original")
        result = self.pack(stage, refs)
        self.assertEqual(self.calls, 2)
        self.assertFalse(directory.parent.exists())
        self.assertTrue(result["staging_removed"])
        self.assertFalse(result["source_capability_restored"])
        self.assertEqual(self.ref(old / "original"), old_ref)
        files = list((self.root / "compact").iterdir())
        self.assertEqual(len(files), 9)
        self.assertEqual(sum(p.stat().st_size for p in files), result["stored_bytes"])
        self.assertEqual(result["saved_bytes"], sum(map(len, values.values())) - result["stored_bytes"])
        self.assertGreater(result["saved_bytes"], 1_000_000)
        archive = compact.CompactArchive(result["manifest"])
        self.assertEqual(archive.verify_all()["artifact_count"], 8)
        for role, value in values.items():
            self.assertEqual(archive.read_artifact(role, max_bytes=len(value)), value)
            item = archive.manifest["artifacts"][role]
            self.assertEqual(item["logical_sha256"], hashlib.sha256(value).hexdigest())
            self.assertEqual(item["source_reference"], refs[role])
            self.assertFalse(Path(item["source_reference"]["path"]).exists())
            self.assertEqual(item["codec"], "identity" if role.endswith(".gz") else "gzip")

    def test_staging_cannot_be_adopted_serialized_or_consumed_twice(self):
        with self.assertRaisesRegex(compact.CompactError, "LIVE_OWNED"):
            compact.OwnedStaging()
        stage, _, refs, _ = self.fixture(10)
        with self.assertRaisesRegex(TypeError, "NOT_SERIALIZABLE"):
            pickle.dumps(stage)
        self.pack(stage, refs)
        with self.assertRaisesRegex(compact.CompactError, "ALREADY_CONSUMED"):
            self.pack(stage, refs)

    def test_old_directory_path_is_not_a_cleanup_capability(self):
        stage, directory, refs, _ = self.fixture(10)
        with self.assertRaisesRegex(compact.CompactError, "LIVE_OWNED"):
            self.pack(directory.parent, refs)
        other = compact.create_staging(self.root)
        with self.assertRaisesRegex(compact.CompactError, "ONLY_OWNED"):
            self.pack(other, refs)
        self.assertTrue((directory / "manifest.json").exists())

    def test_missing_role_rejected_before_writing(self):
        stage, directory, refs, _ = self.fixture(10)
        del refs["focus.csv"]
        with self.assertRaisesRegex(compact.CompactError, "COMPLETE_EIGHT"):
            self.pack(stage, refs)
        self.assertEqual(len(list(directory.iterdir())), 8)
        self.assertFalse((self.root / "compact").exists())

    def test_unknown_staging_child_preserved(self):
        stage, directory, refs, _ = self.fixture(10)
        (directory / "someone-elses-file").write_bytes(b"preserve")
        with self.assertRaisesRegex(compact.CompactError, "UNKNOWN_CORE_ENTRY_PRESERVED"):
            self.pack(stage, refs)
        self.assertEqual((directory / "someone-elses-file").read_bytes(), b"preserve")

    def test_unknown_root_child_preserved(self):
        stage, directory, refs, _ = self.fixture(10)
        (directory.parent / "unexpected").write_bytes(b"preserve")
        with self.assertRaisesRegex(compact.CompactError, "UNKNOWN_STAGING_ENTRY_PRESERVED"):
            self.pack(stage, refs)
        self.assertTrue((directory.parent / "unexpected").exists())

    def test_existing_destination_preserved(self):
        stage, directory, refs, _ = self.fixture(10)
        target = self.root / "compact"
        target.mkdir()
        (target / "mine").write_bytes(b"keep")
        with self.assertRaises(FileExistsError):
            self.pack(stage, refs)
        self.assertEqual((target / "mine").read_bytes(), b"keep")
        self.assertEqual(len(list(directory.iterdir())), 8)

    def test_changed_marker_rejected(self):
        stage, directory, refs, _ = self.fixture(10)
        (directory.parent / "owned-token").write_bytes(b"changed")
        with self.assertRaisesRegex(compact.CompactError, "IDENTITY_CHANGED"):
            self.pack(stage, refs)
        self.assertTrue(directory.exists())

    def test_symlink_source_rejected_without_touching_target(self):
        stage, directory, refs, _ = self.fixture(10)
        victim = self.root / "victim"
        victim.write_bytes(b"TEST_FIXTURE target survives")
        (directory / "all.csv").unlink()
        (directory / "all.csv").symlink_to(victim)
        with self.assertRaisesRegex(compact.CompactError, "REGULAR_SINGLE_LINK"):
            self.pack(stage, refs)
        self.assertEqual(victim.read_bytes(), b"TEST_FIXTURE target survives")

    def test_hardlinked_source_rejected(self):
        stage, directory, refs, _ = self.fixture(10)
        os.link(directory / "all.csv", self.root / "outside-link")
        with self.assertRaisesRegex(compact.CompactError, "REGULAR_SINGLE_LINK"):
            self.pack(stage, refs)
        self.assertTrue((self.root / "outside-link").exists())

    def test_failed_initial_terminal_preserves_all_originals(self):
        stage, directory, refs, _ = self.fixture(10)
        with self.assertRaisesRegex(compact.CompactError, "TERMINAL_VALIDATION_FAILED"):
            compact.compact_owned(stage, refs, destination=self.root / "compact", terminal_validator=lambda: False)
        self.assertEqual(len(list(directory.iterdir())), 8)
        self.assertFalse((self.root / "compact").exists())

    def test_failed_final_terminal_preserves_all_originals(self):
        stage, directory, refs, _ = self.fixture(10)
        outcomes = iter((True, False))
        with self.assertRaisesRegex(compact.CompactError, "TERMINAL_VALIDATION_FAILED"):
            compact.compact_owned(stage, refs, destination=self.root / "compact", terminal_validator=lambda: next(outcomes))
        self.assertEqual(len(list(directory.iterdir())), 8)
        # Storage bytes may be valid; the caller received no completion and no
        # source ownership was removed or restored by this storage manifest.
        value = json.loads((self.root / "compact" / compact.MANIFEST_NAME).read_bytes())
        self.assertFalse(value["source_capability_restored"])

    def test_terminal_source_mutation_detected_and_not_deleted(self):
        stage, directory, refs, _ = self.fixture(10)
        def change():
            self.calls += 1
            if self.calls == 2:
                (directory / "observation.csv").write_bytes(b"different")
            return True
        with self.assertRaisesRegex(compact.CompactError, "IDENTITY_CHANGED"):
            compact.compact_owned(stage, refs, destination=self.root / "compact", terminal_validator=change)
        self.assertEqual(len(list(directory.iterdir())), 8)

    def test_terminal_compact_mutation_detected_before_original_cleanup(self):
        for name in ("all.csv.gz", compact.MANIFEST_NAME):
            with self.subTest(name=name):
                stage, directory, refs, _ = self.fixture(10)
                destination = self.root / ("compact-mutated-" + name)
                calls = []
                def change():
                    calls.append(True)
                    if len(calls) == 2:
                        path = destination / name
                        path.write_bytes(path.read_bytes() + b"changed")
                    return True
                with self.assertRaisesRegex(compact.CompactError, "IDENTITY_CHANGED"):
                    compact.compact_owned(stage, refs, destination=destination, terminal_validator=change)
                self.assertEqual(len(list(directory.iterdir())), 8)

    def test_cleanup_io_failure_retains_complete_verified_compact_archive(self):
        stage, directory, refs, values = self.fixture(10)
        original_unlink, removed = os.unlink, []
        def fail_second(name, *args, **kwargs):
            if name in compact.CORE_FILES:
                removed.append(name)
                if len(removed) == 2:
                    raise OSError("TEST_FIXTURE_CLEANUP_IO_FAILURE")
            return original_unlink(name, *args, **kwargs)
        with patch.object(compact.os, "unlink", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "CLEANUP_IO_FAILURE"):
                self.pack(stage, refs)
        self.assertEqual(len(list(directory.iterdir())), 7)
        reference = self.ref(self.root / "compact" / compact.MANIFEST_NAME)
        archive = compact.CompactArchive(reference)
        self.assertEqual(archive.verify_all()["artifact_count"], 8)
        for role, value in values.items():
            self.assertEqual(archive.read_artifact(role, max_bytes=len(value)), value)
        with self.assertRaisesRegex(compact.CompactError, "UNKNOWN_CORE_ENTRY_PRESERVED"):
            self.pack(stage, refs)

    def test_compact_mutation_during_last_original_read_prevents_cleanup(self):
        stage, directory, refs, _ = self.fixture(10)
        original_chunks, callbacks = compact._chunks, []
        changed = []
        def terminal():
            callbacks.append(True)
            return True
        def chunks(path, *args, **kwargs):
            if len(callbacks) == 2 and Path(path) == directory / "all.csv" and not changed:
                target = self.root / "compact" / "all.csv.gz"
                target.write_bytes(target.read_bytes() + b"TEST_FIXTURE_RACE")
                changed.append(True)
            yield from original_chunks(path, *args, **kwargs)
        with patch.object(compact, "_chunks", side_effect=chunks):
            with self.assertRaisesRegex(compact.CompactError, "DESTINATION_CHANGED_BEFORE_CLEANUP"):
                compact.compact_owned(stage, refs, destination=self.root / "compact", terminal_validator=terminal)
        self.assertEqual(len(list(directory.iterdir())), 8)

    def test_declared_peak_covers_original_and_maximum_new_output(self):
        stage, directory, refs, _ = self.fixture(10)
        with self.assertRaisesRegex(compact.CompactError, "PEAK_RESERVATION_REQUIRED"):
            self.pack(stage, refs, limits=compact.CompactLimits(peak_bytes=128 * compact.MIB))
        self.assertFalse((self.root / "compact").exists())
        self.assertTrue(directory.exists())

    def test_output_limit_failure_preserves_originals(self):
        stage, directory, refs, _ = self.fixture(10)
        with self.assertRaisesRegex(compact.CompactError, "STORED_LIMIT"):
            self.pack(stage, refs, limits=compact.CompactLimits(stored_bytes=10))
        self.assertEqual(len(list(directory.iterdir())), 8)
        self.assertFalse((self.root / "compact" / compact.MANIFEST_NAME).exists())

    def test_read_limit_failure_preserves_originals(self):
        stage, directory, refs, _ = self.fixture(10)
        with self.assertRaisesRegex(compact.CompactError, "READ_LIMIT"):
            self.pack(stage, refs, limits=compact.CompactLimits(returned_read_bytes=1))
        self.assertEqual(len(list(directory.iterdir())), 8)

    def test_mutated_compressed_storage_rejected(self):
        stage, _, refs, _ = self.fixture(10)
        result = self.pack(stage, refs)
        archive = compact.CompactArchive(result["manifest"])
        path = Path(archive.manifest["artifacts"]["all.csv"]["stored_reference"]["path"])
        path.write_bytes(path.read_bytes()[:-1] + b"x")
        with self.assertRaisesRegex(compact.CompactError, "IDENTITY_CHANGED"):
            archive.verify_all()

    def test_same_bytes_new_inode_rejected(self):
        stage, _, refs, _ = self.fixture(10)
        result = self.pack(stage, refs)
        archive = compact.CompactArchive(result["manifest"])
        path = Path(archive.manifest["artifacts"]["all.csv"]["stored_reference"]["path"])
        raw = path.read_bytes()
        temporary = path.with_suffix(".new")
        temporary.write_bytes(raw)
        os.replace(temporary, path)
        with self.assertRaisesRegex(compact.CompactError, "IDENTITY_CHANGED"):
            archive.verify_all()

    def test_logical_sha_mismatch_rejected(self):
        stage, _, refs, _ = self.fixture(10)
        result = self.pack(stage, refs)
        def edit(value):
            item = value["artifacts"]["all.csv"]
            item["logical_sha256"] = item["source_reference"]["sha256"] = "0" * 64
        manifest_ref = self.rewrite_manifest(result, edit)
        with self.assertRaisesRegex(compact.CompactError, "LOGICAL_BYTES_CHANGED"):
            compact.CompactArchive(manifest_ref).read_artifact("all.csv", max_bytes=compact.MIB)

    def test_expansion_beyond_declared_logical_length_rejected(self):
        stage, _, refs, _ = self.fixture(10)
        result = self.pack(stage, refs)
        def edit(value):
            item = value["artifacts"]["all.csv"]
            value["logical_bytes"] -= item["logical_bytes"] - 1
            item["logical_bytes"] = item["source_reference"]["bytes"] = item["source_reference"]["fp"]["st_size"] = 1
        manifest_ref = self.rewrite_manifest(result, edit)
        with self.assertRaisesRegex(compact.CompactError, "LOGICAL_SIZE_LIMIT"):
            compact.CompactArchive(manifest_ref).read_artifact("all.csv", max_bytes=1)

    def test_archive_missing_role_or_path_escape_rejected(self):
        for kind in ("missing", "escape"):
            with self.subTest(kind=kind):
                stage, _, refs, _ = self.fixture(10)
                target = self.root / ("compact-" + kind)
                result = compact.compact_owned(stage, refs, destination=target, terminal_validator=self.terminal)
                def edit(value):
                    if kind == "missing":
                        value["artifacts"].pop("focus.csv")
                    else:
                        value["artifacts"]["focus.csv"]["stored_reference"]["path"] = str(target / ".." / "evil")
                with self.assertRaises(compact.CompactError):
                    compact.CompactArchive(self.rewrite_manifest(result, edit))

    def test_bounded_read_and_cumulative_budget(self):
        stage, _, refs, values = self.fixture(10)
        result = self.pack(stage, refs)
        archive = compact.CompactArchive(result["manifest"])
        with self.assertRaisesRegex(compact.CompactError, "READ_RESULT_SIZE_LIMIT"):
            archive.read_artifact("all.csv", max_bytes=1)
        self.assertEqual(archive.read_artifact("focus.csv", max_bytes=len(values["focus.csv"])), values["focus.csv"])
        archive.meter.limits = compact.CompactLimits(returned_read_bytes=archive.meter.read_bytes + 1)
        with self.assertRaisesRegex(compact.CompactError, "READ_LIMIT"):
            archive.verify_all()

    def test_public_manifest_mutation_does_not_change_reader(self):
        stage, _, refs, values = self.fixture(10)
        result = self.pack(stage, refs)
        archive = compact.CompactArchive(result["manifest"])
        view = archive.manifest
        view["artifacts"]["focus.csv"]["logical_bytes"] = 0
        self.assertEqual(archive.read_artifact("focus.csv", max_bytes=len(values["focus.csv"])), values["focus.csv"])

    def test_missing_source_capability_cannot_be_asserted_by_manifest(self):
        stage, _, refs, _ = self.fixture(10)
        result = self.pack(stage, refs)
        reference = self.rewrite_manifest(result, lambda value: value.update(source_capability_restored=True))
        with self.assertRaisesRegex(compact.CompactError, "MANIFEST_SEMANTICS"):
            compact.CompactArchive(reference)

    def test_time_limit_fails_closed(self):
        stage, directory, refs, _ = self.fixture(10)
        with patch.object(compact.time, "monotonic", side_effect=range(1000)):
            with self.assertRaisesRegex(compact.CompactError, "TIME_LIMIT"):
                self.pack(stage, refs, limits=compact.CompactLimits(max_seconds=0.5))
        self.assertEqual(len(list(directory.iterdir())), 8)


if __name__ == "__main__":
    unittest.main()
