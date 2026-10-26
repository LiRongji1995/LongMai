"""Isolated TEST_FIXTURE capacity observations, never a real runtime or DB."""
from collections import namedtuple
from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import codex_capacity as capacity
from scripts import codex_storage_growth_v1 as growth
from scripts import codex_workspace as workspace

Usage = namedtuple("Usage", "total used free")


class GrowthFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="storage-growth-TEST_FIXTURE-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.data = self.root / "TEST_FIXTURE-data"
        self.env = self.root / "TEST_FIXTURE-environment"
        self.report = self.data / "reports"
        self.state = self.data / "state"
        for path in (self.env, self.report, self.state):
            path.mkdir(parents=True)
        self.original = self.state / "TEST_FIXTURE-original.json"
        self.original.write_bytes(b"TEST_FIXTURE" * 100)
        self.free = 900 * growth.MIB
        self.checks = 0
        self.profile = dict(name="TEST_FIXTURE", backend="premarket", state_root=str(self.state),
                            report_root=str(self.report), storage=dict(
            roots=[dict(name="data", path=str(self.data), role="runtime"),
                   dict(name="environment", path=str(self.env), role="protected")],
            target_root="data", expected_growth_bytes=32 * growth.MIB,
            min_free_bytes=100 * growth.MIB, max_managed_bytes=1024 * growth.MIB))
        self.scope = {"backend": "premarket", "device": self.data.stat().st_dev, "inode": self.data.stat().st_ino}
        self.binding = {"TEST_FIXTURE": self.scope}

    def check_binding(self):
        self.checks += 1

    def exclusive(self):
        return nullcontext()

    def usage(self, path):
        return Usage(2000 * growth.MIB, 2000 * growth.MIB - self.free, self.free)

    def storage_report(self):
        return capacity.storage_report(self.profile["storage"]["roots"], disk_usage_fn=self.usage)

    def preflight(self, expected_growth_bytes=None, destination=None):
        policy = self.profile["storage"]
        return capacity.preflight(policy["roots"], target_root="data",
            expected_growth_bytes=expected_growth_bytes,
            min_free_bytes=policy["min_free_bytes"], max_managed_bytes=policy["max_managed_bytes"],
            disk_usage_fn=self.usage)

    def test_peak_reserve_and_disk_floor_both_limit_the_next_operation(self):
        value = growth.plan(self, growth_per_run_bytes=34 * growth.MIB)
        self.assertTrue(value["next_operation_admissible"])
        self.assertEqual(value["peak_reserve_bytes"], 260 * growth.MIB)
        self.assertEqual(value["admission_reserve_bytes"], 262 * growth.MIB)
        self.assertEqual(value["net_growth_before_next_operation_blocks_bytes"], 538 * growth.MIB)
        self.assertEqual(value["scenario"]["further_full_growth_steps_before_next_operation_blocks"], 15)
        self.assertFalse(value["scenario"]["prediction"])
        self.free = 361 * growth.MIB
        value = growth.plan(self)
        self.assertFalse(value["next_operation_admissible"])
        self.assertEqual(value["net_growth_before_next_operation_blocks_bytes"], -growth.MIB)
        self.assertIn("MINIMUM_FREE_SPACE_WOULD_BE_BREACHED", value["reason_codes"])

    def test_exact_managed_boundary_and_no_policy_change(self):
        old = json.loads(json.dumps(self.profile))
        size = len(self.original.read_bytes())
        self.profile["storage"]["max_managed_bytes"] = size + 262 * growth.MIB
        self.assertTrue(growth.plan(self)["next_operation_admissible"])
        self.profile["storage"]["max_managed_bytes"] -= 1
        value = growth.plan(self)
        self.assertFalse(value["next_operation_admissible"])
        self.assertIn("MANAGED_LOGICAL_BUDGET_EXCEEDED", value["reason_codes"])
        self.assertFalse(value["capacity_policy_changed"])
        self.assertEqual(self.profile["storage"]["min_free_bytes"], old["storage"]["min_free_bytes"])

    def test_larger_generic_preflight_remains_a_separate_first_run_gate(self):
        self.profile["storage"]["expected_growth_bytes"] = 850 * growth.MIB
        value = growth.plan(self)
        self.assertEqual(value["peak_reserve_bytes"], 260 * growth.MIB)
        self.assertEqual(value["admission_reserve_bytes"], 852 * growth.MIB)
        self.assertFalse(value["next_operation_admissible"])
        self.assertEqual(value["net_growth_before_next_operation_blocks_bytes"], -52 * growth.MIB)

    def test_plan_reads_no_payload_or_creates_ledger(self):
        before = capacity._identity(self.original.stat())
        with patch.object(Path, "open", side_effect=AssertionError("payload read forbidden")):
            value = growth.plan(self)
        self.assertFalse(value["inventory"]["file_contents_read"])
        self.assertEqual(capacity._identity(self.original.stat()), before)
        self.assertFalse(growth._directory(self).exists())
        self.assertTrue(all("DELETE" not in row["action"] or row["action"].endswith("NO_DELETE")
                            for row in value["retention_policy"]))

    def test_incomplete_inventory_never_becomes_zero_or_positive_admission(self):
        self.env.rmdir()
        value = growth.plan(self, growth_per_run_bytes=1)
        self.assertEqual(value["status"], "PARTIAL")
        self.assertIsNone(value["managed_logical_bytes"])
        self.assertIsNone(value["net_growth_before_next_operation_blocks_bytes"])
        self.assertIsNone(value["scenario"]["further_full_growth_steps_before_next_operation_blocks"])
        self.assertFalse(value["next_operation_admissible"])
        with self.assertRaises(workspace.WorkspaceError):
            growth.record(self, "TEST_FIXTURE-missing")
        self.assertFalse(growth._directory(self).exists())

    def test_cloud_placeholder_no_hydration_or_snapshot_write(self):
        (self.state / ".TEST_FIXTURE.icloud").touch()
        value = growth.plan(self)
        self.assertEqual(value["status"], "PARTIAL")
        self.assertFalse(value["next_operation_admissible"])

    def test_record_is_immutable_and_duplicate_key_reuses_old_observation(self):
        first = growth.record(self, "TEST_FIXTURE-before")
        file = Path(first["sample_path"])
        before = capacity._identity(file.stat())
        self.original.write_bytes(b"TEST_FIXTURE changed")
        second = growth.record(self, "TEST_FIXTURE-before")
        self.assertEqual(second["status"], "REUSED_ORIGINAL_OBSERVATION")
        self.assertEqual(second["sample"], first["sample"])
        self.assertEqual(capacity._identity(file.stat()), before)
        self.assertEqual(second["new_files"], 0)
        self.assertTrue(first["sample"]["observation_precedes_own_metadata_write"])

    def test_history_reports_all_growth_including_its_own_metadata(self):
        first = growth.record(self, "TEST_FIXTURE-a")
        extra = self.state / "TEST_FIXTURE-new.raw"
        extra.write_bytes(b"TEST_FIXTURE" * 300)
        second = growth.record(self, "TEST_FIXTURE-b")
        history = growth.growth_history(self)
        self.assertEqual(history["samples"], 2)
        delta = history["deltas"][0]
        self.assertEqual(delta["net_logical_bytes"],
                         extra.stat().st_size + Path(first["sample_path"]).stat().st_size)
        self.assertEqual(delta["per_root_net_logical_bytes"]["environment"], 0)
        self.assertIn("NOT_EXCLUSIVE", delta["attribution"])
        self.assertFalse(history["deletion_authorized"])
        self.assertTrue(Path(second["sample_path"]).exists())

    def test_negative_delta_is_not_clamped_or_treated_as_safe_reclamation(self):
        self.original.write_bytes(b"TEST_FIXTURE" * 10000)
        growth.record(self, "TEST_FIXTURE-before")
        self.original.unlink()  # This file belongs only to this temporary fixture.
        growth.record(self, "TEST_FIXTURE-after")
        self.assertLess(growth.growth_history(self)["deltas"][0]["net_logical_bytes"], 0)

    def test_tampered_sample_and_cross_policy_samples_rejected(self):
        first = growth.record(self, "TEST_FIXTURE-first")
        path = Path(first["sample_path"])
        original = path.read_bytes()
        value = json.loads(original)
        value["payload"]["managed_logical_bytes"] = 0
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(workspace.WorkspaceError, "BYTES_CHANGED"):
            growth.growth_history(self)
        path.write_bytes(original)
        self.profile["storage"]["max_managed_bytes"] += 1
        with self.assertRaisesRegex(workspace.WorkspaceError, "DIFFERENT_RUNTIME_OR_POLICY"):
            growth.growth_history(self)

    def test_symlink_ledger_rejected_without_following_it(self):
        outside = self.root / "TEST_FIXTURE-outside"
        outside.mkdir()
        growth._directory(self).symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OSError):
            growth.record(self, "TEST_FIXTURE-no-follow")
        self.assertEqual(list(outside.iterdir()), [])

    def test_invalid_scenarios_and_sample_ids(self):
        for value in (0, -1, True, 1.5):
            with self.subTest(value=value), self.assertRaises(workspace.WorkspaceError):
                growth.plan(self, growth_per_run_bytes=value)
        for value in ("../outside", "", "x/other", "a" * 81):
            with self.subTest(value=value), self.assertRaises(workspace.WorkspaceError):
                growth.record(self, value)

    def test_sample_cap_preserves_existing_observations(self):
        growth.record(self, "TEST_FIXTURE-first")
        with patch.object(growth, "MAX_SAMPLES", 1):
            with self.assertRaisesRegex(workspace.WorkspaceError, "COUNT_LIMIT"):
                growth.record(self, "TEST_FIXTURE-second")
        self.assertEqual(len(list(growth._directory(self).glob("*.json"))), 1)


if __name__ == "__main__":
    unittest.main()
