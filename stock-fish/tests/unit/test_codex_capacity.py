"""TEST_FIXTURE-only contracts: no business database, network, or source data."""
from collections import namedtuple
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/codex_capacity.py"
spec = importlib.util.spec_from_file_location("codex_capacity_TEST_FIXTURE", SCRIPT)
capacity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capacity)
Usage = namedtuple("Usage", "total used free")


class CapacityFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="codex-capacity-TEST_FIXTURE-")
        self.addCleanup(self.temporary.cleanup)
        # macOS /var is a symlink. The contract requires an explicit real root.
        self.base = Path(self.temporary.name).resolve()
        self.data = self.base / "TEST_FIXTURE-output"
        self.data.mkdir()
        self.roots = [{"name": "fixture", "path": str(self.data), "role": "output"}]
        self.usage = lambda path: Usage(1_000_000, 200_000, 800_000)

    def report(self, roots=None, **kwargs):
        return capacity.storage_report(roots or self.roots, disk_usage_fn=self.usage, **kwargs)

    def preflight(self, **kwargs):
        defaults = dict(expected_growth_bytes=100, min_free_bytes=1_000,
                        max_managed_bytes=10_000, disk_usage_fn=self.usage)
        defaults.update(kwargs)
        return capacity.preflight(self.roots, **defaults)

    def test_inventory_counts_hardlinks_once_without_opening_file_content(self):
        original = self.data / "TEST_FIXTURE.raw"
        original.write_bytes(b"TEST_FIXTURE" * 700)
        alias = self.data / "TEST_FIXTURE-hardlink.raw"
        os.link(original, alias)
        (self.data / "TEST_FIXTURE-link").symlink_to(self.base)
        before = original.stat()
        with patch("builtins.open", side_effect=AssertionError("content read prohibited")), \
                patch.object(Path, "open", side_effect=AssertionError("content read prohibited")):
            report = self.report()
        totals = report["totals"]
        self.assertTrue(report["scan_complete"])
        self.assertEqual(totals["regular_files"], 2)
        self.assertEqual(totals["logical_bytes"], before.st_size * 2)
        self.assertEqual(totals["unique_logical_bytes"], before.st_size)
        self.assertEqual(totals["allocated_bytes"], before.st_blocks * 512)
        self.assertEqual(totals["duplicate_inode_entries"], 1)
        self.assertEqual(totals["symlinks"], 1)
        self.assertFalse(report["file_contents_read"])
        self.assertFalse(report["snapshot_consistent"])
        after = original.stat()
        self.assertEqual(capacity._identity(before), capacity._identity(after))

    def test_cross_root_hardlinks_deduplicate_globally(self):
        other = self.base / "TEST_FIXTURE-protected"
        other.mkdir()
        original = other / "TEST_FIXTURE.raw"
        original.write_bytes(b"TEST_FIXTURE")
        os.link(original, self.data / "TEST_FIXTURE.raw")
        roots = [{"name": "protected", "path": str(other), "role": "protected"}, *self.roots]
        report = self.report(roots)
        self.assertTrue(report["scan_complete"])
        self.assertEqual(report["totals"]["unique_logical_bytes"], 12)
        self.assertEqual(report["totals"]["logical_bytes"], 24)
        self.assertEqual(sum(row["totals"]["unique_logical_bytes"] for row in report["roots"]), 12)

    def test_sparse_file_keeps_logical_and_allocated_separate(self):
        path = self.data / "TEST_FIXTURE.sparse"
        with path.open("wb") as handle:
            handle.truncate(4 * 1024 * 1024)
        report = self.report()
        self.assertEqual(report["totals"]["unique_logical_bytes"], 4 * 1024 * 1024)
        self.assertEqual(report["totals"]["allocated_bytes"], path.stat().st_blocks * 512)
        self.assertFalse(self.preflight(max_managed_bytes=1024)["allowed"])

    def test_preflight_exact_boundary_and_distinct_failure_reasons(self):
        (self.data / "TEST_FIXTURE.raw").write_bytes(b"TEST_FIXTURE")
        report = self.preflight(expected_growth_bytes=100, max_managed_bytes=112,
                                min_free_bytes=799_900)
        self.assertTrue(report["allowed"])
        self.assertFalse(report["reservation_created"])
        self.assertEqual(report["projected_unique_logical_bytes"], 112)
        self.assertEqual(report["projected_free_bytes"], 799_900)
        denied = self.preflight(expected_growth_bytes=101, max_managed_bytes=112,
                                min_free_bytes=799_900)
        self.assertEqual(denied["reason_codes"], ["MANAGED_LOGICAL_BUDGET_EXCEEDED",
                                                 "MINIMUM_FREE_SPACE_WOULD_BE_BREACHED"])

    def test_protected_root_never_write_destination(self):
        self.roots[0]["role"] = "protected"
        self.assertIn("TARGET_ROOT_PROTECTED", self.preflight()["reason_codes"])

    def test_shared_runtime_root_allows_bounded_growth_without_deleting_evidence(self):
        evidence = self.data / "TEST_FIXTURE-original.raw"
        evidence.write_bytes(b"TEST_FIXTURE historical evidence")
        before = capacity._identity(evidence.stat())
        self.roots[0]["role"] = "runtime"
        report = self.preflight()
        self.assertTrue(report["allowed"])
        self.assertEqual(report["report"]["roots"][0]["role"], "runtime")
        self.assertEqual(capacity._identity(evidence.stat()), before)
        self.assertEqual(evidence.read_bytes(), b"TEST_FIXTURE historical evidence")

    def test_missing_root_is_unknown_and_denies_admission(self):
        self.roots[0]["path"] = str(self.base / "TEST_FIXTURE-missing")
        report = self.preflight()
        self.assertFalse(report["allowed"])
        self.assertEqual(report["report"]["status"], "PARTIAL")
        self.assertIn("STORAGE_INVENTORY_INCOMPLETE", report["reason_codes"])
        self.assertIn("TARGET_FREE_SPACE_UNKNOWN", report["reason_codes"])
        self.assertEqual(report["report"]["issues"][0]["code"], "PATH_MISSING_DURING_SCAN")
        self.assertFalse((self.base / "TEST_FIXTURE-missing").exists())

    def test_cloud_placeholder_is_reported_without_hydration(self):
        placeholder = self.data / ".TEST_FIXTURE.raw.icloud"
        placeholder.write_bytes(b"TEST_FIXTURE cloud placeholder")
        report = self.preflight()
        self.assertFalse(report["allowed"])
        self.assertEqual(report["report"]["totals"]["cloud_placeholders"], 1)
        self.assertIn("CLOUD_PLACEHOLDER_NOT_HYDRATED", [issue["code"] for issue in report["report"]["issues"]])
        self.assertEqual(placeholder.read_bytes(), b"TEST_FIXTURE cloud placeholder")

    def test_dataless_directory_is_never_opened_or_enumerated(self):
        child = self.data / "TEST_FIXTURE-dataless"
        child.mkdir()
        (child / "TEST_FIXTURE-hidden").write_bytes(b"TEST_FIXTURE")
        metadata = capacity._metadata
        def dataless(name, fd):
            value = metadata(name, fd)
            if name == child.name:
                fields = {key: getattr(value, key) for key in dir(value) if key.startswith("st_")}
                fields["st_flags"] = 0x40000000
                return SimpleNamespace(**fields)
            return value
        with patch.object(capacity, "_metadata", side_effect=dataless):
            report = self.preflight()
        self.assertFalse(report["allowed"])
        self.assertEqual(report["report"]["totals"]["regular_files"], 0)
        self.assertEqual(report["report"]["totals"]["cloud_placeholders"], 1)

    def test_missing_allocation_metadata_is_not_reported_as_zero(self):
        (self.data / "TEST_FIXTURE.raw").write_bytes(b"TEST_FIXTURE")
        metadata = capacity._metadata
        def no_blocks(name, fd):
            value = metadata(name, fd)
            if name.endswith(".raw"):
                return SimpleNamespace(**{key: getattr(value, key) for key in dir(value)
                                          if key.startswith("st_") and key != "st_blocks"})
            return value
        with patch.object(capacity, "_metadata", side_effect=no_blocks):
            report = self.report()
        self.assertTrue(report["scan_complete"])
        self.assertFalse(report["totals"]["allocation_metadata_complete"])
        self.assertIsNone(report["totals"]["allocated_bytes"])

    def test_symlink_root_and_symlink_parent_are_rejected(self):
        alias = self.base / "TEST_FIXTURE-alias"
        alias.symlink_to(self.data, target_is_directory=True)
        (self.data / "TEST_FIXTURE-child").mkdir()
        for path in (alias, alias / "TEST_FIXTURE-child"):
            with self.subTest(path=path):
                self.roots[0]["path"] = str(path)
                report = self.preflight()
                self.assertFalse(report["allowed"])
                self.assertEqual(report["report"]["totals"]["regular_files"], 0)

    def test_directory_replaced_by_symlink_cannot_escape_scan(self):
        child = self.data / "TEST_FIXTURE-child"
        child.mkdir()
        external = self.base / "TEST_FIXTURE-outside"
        external.mkdir()
        (external / "TEST_FIXTURE-secret").write_bytes(b"TEST_FIXTURE outside inventory")
        metadata = capacity._metadata
        def replace_after_stat(name, fd):
            value = metadata(name, fd)
            if name == child.name and child.is_dir() and not child.is_symlink():
                child.rmdir()
                child.symlink_to(external, target_is_directory=True)
            return value
        with patch.object(capacity, "_metadata", side_effect=replace_after_stat):
            report = self.preflight()
        self.assertFalse(report["allowed"])
        self.assertEqual(report["report"]["totals"]["regular_files"], 0)
        self.assertIn("SYMLINK_OR_DIRECTORY_CHANGED", [row["code"] for row in report["report"]["issues"]])

    def test_file_change_is_partial_even_when_size_looks_sufficient(self):
        path = self.data / "TEST_FIXTURE.raw"
        path.write_bytes(b"TEST_FIXTURE")
        metadata = capacity._metadata
        changed = False
        def mutate(name, fd):
            nonlocal changed
            value = metadata(name, fd)
            if name == path.name and not changed:
                changed = True
                path.write_bytes(b"TEST_FIXTURE changed")
            return value
        with patch.object(capacity, "_metadata", side_effect=mutate):
            report = self.preflight()
        self.assertFalse(report["allowed"])
        self.assertIn("FILE_CHANGED_DURING_SCAN", [row["code"] for row in report["report"]["issues"]])

    def test_permission_error_never_counts_as_empty(self):
        path = self.data / "TEST_FIXTURE.raw"
        path.write_bytes(b"TEST_FIXTURE")
        metadata = capacity._metadata
        def denied(name, fd):
            if name == path.name:
                raise PermissionError("TEST_FIXTURE denied")
            return metadata(name, fd)
        with patch.object(capacity, "_metadata", side_effect=denied):
            report = self.preflight()
        self.assertFalse(report["allowed"])
        self.assertEqual(report["report"]["issues"][0]["code"], "METADATA_PERMISSION_DENIED")

    def test_entry_and_depth_limits_are_partial(self):
        child = self.data / "TEST_FIXTURE-child"
        child.mkdir()
        (child / "TEST_FIXTURE.raw").write_bytes(b"TEST_FIXTURE")
        for kwargs, code in (({"max_entries": 1}, "SCAN_ENTRY_LIMIT_REACHED"),
                             ({"max_depth": 1}, "SCAN_DEPTH_LIMIT_REACHED")):
            with self.subTest(kwargs=kwargs):
                report = self.preflight(**kwargs)
                self.assertFalse(report["allowed"])
                self.assertIn(code, [issue["code"] for issue in report["report"]["issues"]])

    def test_filesystem_failure_and_invalid_measurement_deny_admission(self):
        for callback in (lambda path: Usage(True, 0, 0), lambda path: Usage(100, 0, 200),
                         lambda path: None):
            with self.subTest(callback=callback):
                report = self.preflight(disk_usage_fn=callback)
                self.assertFalse(report["allowed"])
                self.assertIn("TARGET_FREE_SPACE_UNKNOWN", report["reason_codes"])
        with patch.object(capacity.shutil, "disk_usage", side_effect=PermissionError("TEST_FIXTURE")):
            report = self.preflight(disk_usage_fn=None)
        self.assertFalse(report["allowed"])

    def test_root_changed_while_filesystem_capacity_is_measured_is_rejected(self):
        moved = self.base / "TEST_FIXTURE-moved-output"
        def mutate(path):
            self.data.rename(moved)
            self.data.symlink_to(moved, target_is_directory=True)
            return Usage(1_000_000, 0, 1_000_000)
        report = self.preflight(disk_usage_fn=mutate)
        self.assertFalse(report["allowed"])
        self.assertIn("ROOT_CHANGED_DURING_SCAN", [row["code"] for row in report["report"]["issues"]])

    def test_configuration_rejects_bool_numeric_coercion_and_unknown_fields(self):
        for field in ("expected_growth_bytes", "min_free_bytes", "max_managed_bytes", "max_entries", "max_depth"):
            for value in (True, False, -1, 1.0, "1", None):
                with self.subTest(field=field, value=value), self.assertRaises(capacity.CapacityError):
                    self.preflight(**{field: value})
        with self.assertRaises(capacity.CapacityError):
            self.preflight(max_depth=257)
        with self.assertRaises(capacity.CapacityError):
            self.preflight(disk_usage_fn=False)
        invalid = [[], {}, [dict(self.roots[0], extra=True)], [dict(self.roots[0], role=True)],
                   [dict(self.roots[0], name="")], [dict(self.roots[0], path="relative")],
                   [dict(self.roots[0], path=str(self.data / ".." / "alias"))],
                   [dict(self.roots[0], path=str(self.data) + "\x00")]]
        for roots in invalid:
            with self.subTest(roots=roots), self.assertRaises(capacity.CapacityError):
                capacity.storage_report(roots)

    def test_overlap_duplicate_name_and_ambiguous_growth_target_rejected(self):
        for path, name in ((self.data, "another"), (self.data / "nested", "another"),
                           (self.base / "separate", "fixture")):
            roots = [*self.roots, {"name": name, "path": str(path), "role": "cache"}]
            with self.subTest(path=path), self.assertRaises(capacity.CapacityError):
                capacity.storage_report(roots)
        other = self.base / "TEST_FIXTURE-other"
        other.mkdir()
        self.roots.append({"name": "other", "path": str(other), "role": "cache"})
        with self.assertRaisesRegex(capacity.CapacityError, "EXPLICIT_TARGET_ROOT_REQUIRED"):
            self.preflight()
        self.assertTrue(self.preflight(target_root="other")["allowed"])

    def test_filesystem_free_space_uses_selected_target_not_sum_of_volumes(self):
        other = self.base / "TEST_FIXTURE-other"
        other.mkdir()
        self.roots.append({"name": "other", "path": str(other), "role": "cache"})
        def usage(path):
            return Usage(1_000_000, 999_950, 50) if path == str(other) else Usage(1_000_000, 0, 1_000_000)
        report = self.preflight(target_root="other", disk_usage_fn=usage)
        self.assertFalse(report["allowed"])
        self.assertEqual(report["projected_free_bytes"], -50)

    def test_unsupported_no_follow_scanning_fails_closed(self):
        with patch.object(capacity.os, "supports_fd", set()):
            report = self.preflight()
        self.assertFalse(report["allowed"])
        self.assertEqual(report["report"]["issues"][0]["code"], "SAFE_DIRECTORY_METADATA_SCAN_UNAVAILABLE")

    def test_root_count_is_bounded_before_any_scan(self):
        roots = [{"name": "fixture-" + str(index), "path": str(self.base / str(index)), "role": "cache"}
                 for index in range(129)]
        with self.assertRaisesRegex(capacity.CapacityError, "STORAGE_ROOT_COUNT_LIMIT_EXCEEDED"):
            self.report(roots)
        report = self.report(roots[:128])
        self.assertFalse(report["scan_complete"])
        self.assertEqual(len(report["roots"]), 128)

    def test_issue_bytes_and_paths_are_bounded_with_explicit_omission(self):
        # This fake metadata enumerator generates no filesystem data. It exposes
        # many long, denied TEST_FIXTURE names to exercise diagnostic limits.
        metadata = capacity._metadata
        root_stat = self.data.stat()
        class Children:
            def __enter__(self):
                return (SimpleNamespace(name="TEST_FIXTURE_" + "长" * 1100 + str(index))
                        for index in range(500))
            def __exit__(self, *args):
                return False
        original_scandir = capacity.os.scandir
        def enumerate_fixture(fd):
            if capacity.os.fstat(fd).st_ino == root_stat.st_ino:
                return Children()
            return original_scandir(fd)
        def denied(name, fd):
            if name.startswith("TEST_FIXTURE_长"):
                raise PermissionError("TEST_FIXTURE")
            return metadata(name, fd)
        with patch.object(capacity.os, "scandir", enumerate_fixture), \
                patch.object(capacity.os, "supports_fd", {*capacity.os.supports_fd, enumerate_fixture}), \
                patch.object(capacity, "_metadata", side_effect=denied):
            report = self.preflight()
        inventory = report["report"]
        self.assertFalse(report["allowed"])
        encoded = json.dumps(inventory["issues"], ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        self.assertEqual(inventory["issue_serialized_bytes"], len(encoded))
        self.assertLessEqual(len(encoded), 256 * 1024)
        self.assertEqual(len(inventory["issues"]) + inventory["omitted_issue_count"], 500)
        self.assertGreater(inventory["omitted_issue_count"], 0)
        self.assertTrue(all(issue.get("path_truncated") for issue in inventory["issues"]))
        self.assertTrue(all(len(issue["path"]) == 1024 for issue in inventory["issues"]))
        self.assertTrue(all(issue["original_path_chars"] > 1024 for issue in inventory["issues"]))


if __name__ == "__main__":
    unittest.main()
