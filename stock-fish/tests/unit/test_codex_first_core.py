"""TEST_FIXTURE first-computation equivalence and independent execution identity."""
from copy import deepcopy
from datetime import date
from decimal import Decimal
import errno
import gzip
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts import codex_first_core as first
from scripts import codex_compact_core as compact_core
from scripts import codex_compact_artifacts as compact
from scripts import codex_core as original
from scripts import codex_workspace as workspace
from tests.unit.test_codex_core import Base
from tests.yaogu.test_premarket_native_core_v1 import captures


class EncodingTests(unittest.TestCase):
    def test_complete_canonical_content_equivalent_without_hash_memoization(self):
        from stockfish_core.yaogu import history_artifacts as h
        reference = h.digest_json_stream
        value = {"unicode": "题材\U0001f422", "decimal": Decimal("12.30"), "date": date(2026, 9, 30),
                 "nested": [{"minus_zero": -0.0, "empty": []}, None, True, 2**70]}
        expected = reference(value)
        with first.encoding_scope(first.OPTIMIZED) as metrics:
            self.assertEqual(expected, h.digest_json_stream(value))
            value["nested"].append("new evidence")
            self.assertNotEqual(expected, h.digest_json_stream(value))
            self.assertEqual(reference(value), h.digest_json_stream(value))
        self.assertIs(h.digest_json_stream, reference)
        self.assertEqual(metrics["digest_json_stream"]["calls"], 3)

    def test_nonfinite_and_surrogate_fail_with_reference_semantics(self):
        from stockfish_core.yaogu import history_artifacts as h
        for profile in first.PROFILES:
            with self.subTest(profile=profile), first.encoding_scope(profile):
                with self.assertRaises(ValueError):
                    h.digest_json_stream({"x": float("nan")})
                with self.assertRaises(UnicodeEncodeError):
                    h.digest_json_stream({"x": "\ud800"})

    def test_reference_and_optimized_restore_on_exception_and_nested_refusal(self):
        from stockfish_core.yaogu import history_artifacts as h
        refs = (h.digest_json_stream, h.digest_market_input)
        for profile in first.PROFILES:
            with self.subTest(profile=profile):
                with self.assertRaisesRegex(RuntimeError, "TEST_FIXTURE"):
                    with first.encoding_scope(profile):
                        with self.assertRaisesRegex(original.CoreError, "NESTED"):
                            with first.encoding_scope(profile):
                                pass
                        raise RuntimeError("TEST_FIXTURE interruption")
                self.assertEqual(refs, (h.digest_json_stream, h.digest_market_input))

    def test_changed_native_function_binding_rejected(self):
        from stockfish_core.yaogu import premarket_native_core_v1 as native
        with patch.object(native, "digest_json_stream", return_value="TEST_FIXTURE"):
            with self.assertRaisesRegex(original.CoreError, "NATIVE_ENCODER_BINDING_CHANGED"):
                with first.encoding_scope(first.OPTIMIZED):
                    pass

    def test_binding_tamper_inside_scope_rejected_and_restored(self):
        from stockfish_core.yaogu import history_artifacts as h
        reference = h.digest_json_stream
        with self.assertRaisesRegex(original.CoreError, "BINDING_CHANGED_DURING"):
            with first.encoding_scope(first.OPTIMIZED):
                h.digest_json_stream = lambda value: "TEST_FIXTURE"
        self.assertIs(h.digest_json_stream, reference)


class FirstCoreTests(Base):
    def setUp(self):
        super().setUp()
        items, observed, target, cutoff = captures(self.root / "TEST_FIXTURE-originals", sessions=60, history=False)
        self.request(items, observed, target, cutoff.isoformat())

    def execute(self, profile=first.OPTIMIZED, first_id=None):
        return first.execute_child(self.runtime, self.manifest, profile=profile, first_run_id=first_id,
                                   allow_test_fixture=True, _fixture_in_process=True)

    def run_value(self, result):
        return json.loads(gzip.decompress(self.logical_values(result)["canonical-run.json.gz"]))

    def logical_values(self, result):
        evidence = workspace._read(result["receipt"]["evidence"]["path"])
        archive = compact.CompactArchive(evidence["compact_manifest"])
        archive.verify_all()
        return {role: archive.read_artifact(role, max_bytes=8*first.MIB) for role in compact.CORE_FILES}

    def test_both_first_paths_keep_all_strategy_semantics_and_execute_once_each(self):
        from stockfish_core.yaogu import premarket_native_core_v1 as native, strategy_experiments as strategy
        with patch.object(native, "normalize_capture_with_report", wraps=native.normalize_capture_with_report) as normalize, \
             patch.object(strategy, "run_short_horizon_strategy", wraps=strategy.run_short_horizon_strategy) as select:
            baseline = self.execute(first.REFERENCE, "TEST_FIXTURE-ab")
            optimized = self.execute(first.OPTIMIZED, "TEST_FIXTURE-ab")
            repeated = self.execute(first.OPTIMIZED, "TEST_FIXTURE-ab")
        self.assertEqual((normalize.call_count, select.call_count), (2, 2))
        self.assertNotEqual(baseline["operation_id"], optimized["operation_id"])
        self.assertEqual(optimized["operation_id"], repeated["operation_id"])
        self.assertEqual((baseline["normalizer_calls"], optimized["normalizer_calls"], repeated["normalizer_calls"]), (1, 1, 0))
        self.assertEqual(repeated["provider_calls"], 0)
        left, right = self.run_value(baseline), self.run_value(optimized)
        for value in (left, right):
            value.pop("generated_at")
            value.pop("content_hash")
        self.assertEqual(left, right)
        left_files, right_files = self.logical_values(baseline), self.logical_values(optimized)
        self.assertEqual(left_files["input.json.gz"], right_files["input.json.gz"])
        for role in ("observation.csv", "all.csv", "focus.csv", "unselected.csv"):
            self.assertEqual(left_files[role], right_files[role], role)
        json_views = [json.loads(files["observation.json"]) for files in (left_files, right_files)]
        for view, result in zip(json_views, (baseline, optimized)):
            self.assertEqual(view["export_view"]["source_content_hash"], result["summary"]["canonical_content_hash"])
            # Only actual generation time and hashes transitively binding that
            # clock differ. Every selected row, ordering and evidence remains.
            view.pop("generated_at")
            view.pop("view_content_hash")
            view["export_view"].pop("source_content_hash")
        self.assertEqual(*json_views)
        for result in (baseline, optimized):
            self.assertTrue(result["encoding_bindings_restored"])
            self.assertGreater(result["encoding_metrics"]["digest_market_input"]["calls"], 1)
            self.assertGreater(result["encoding_metrics"]["digest_json_stream"]["calls"], 1)
        self.assertEqual(repeated["new_large_artifact_bytes"], 0)

    def test_force_first_id_is_explicit_distinct_and_sticky(self):
        with patch.object(compact, "compact_owned", side_effect=RuntimeError("TEST_FIXTURE failure")):
            with self.assertRaisesRegex(RuntimeError, "failure"):
                self.execute(first_id="TEST_FIXTURE-failed")
        with self.assertRaisesRegex(workspace.WorkspaceError, "STATE_UNKNOWN_RECONCILE_NO_RETRY"):
            self.execute(first_id="TEST_FIXTURE-failed")
        result = self.execute(first_id="TEST_FIXTURE-new-explicit")
        self.assertEqual(result["status"], "RECORDED")

    def test_old_compact_operation_cannot_be_hit(self):
        old = compact_core.execute_compact_core(self.runtime, self.manifest,
            profile=compact_core.PROFILE, allow_test_fixture=True)
        new = self.execute()
        self.assertNotEqual(old["operation_id"], new["operation_id"])
        self.assertEqual(new["normalizer_calls"], 1)

    def test_source_tampering_still_fails_and_encoding_bindings_restore(self):
        from stockfish_core.yaogu import history_artifacts as h
        reference = h.digest_json_stream
        self.execute()
        Path(self.value["captures"][0]["raw"]["path"]).write_bytes(b"TEST_FIXTURE changed")
        with self.assertRaisesRegex(original.CoreError, "ORIGINAL_BYTES_OR_IDENTITY_CHANGED"):
            self.execute()
        self.assertIs(h.digest_json_stream, reference)

    def test_execution_provenance_cannot_be_removed_from_receipt(self):
        result = self.execute()
        record_path = self.runtime.directory / "operations" / (result["operation_id"] + ".json")
        record = workspace._read(record_path)
        record["receipt"].pop("first_core_execution")
        workspace._write(record_path, record, replace=True)
        with self.assertRaisesRegex(original.CoreError, "EXECUTION_RECEIPT_CHANGED"):
            self.execute()

    def test_actual_profiles_and_child_isolation_are_required(self):
        for profile in ("implicit", "TYPED_ROWS_1024_V1"):
            with self.subTest(profile=profile), self.assertRaisesRegex(original.CoreError, "EXPLICIT_PROFILE"):
                self.execute(profile)
        with self.assertRaisesRegex(original.CoreError, "FIRST_ID_INVALID"):
            self.execute(first_id="../TEST_FIXTURE")
        with self.assertRaisesRegex(original.CoreError, "ISOLATED_SUPERVISED_CHILD"):
            first.execute_child(self.runtime, self.manifest, profile=first.OPTIMIZED, allow_test_fixture=True)

    def test_capacity_block_is_retained(self):
        with patch.object(self.runtime, "preflight", return_value={"allowed": False}):
            with self.assertRaisesRegex(workspace.WorkspaceError, "CAPACITY_PREFLIGHT_BLOCKED"):
                self.execute()
        self.assertFalse(list(self.report.glob("compact-core-*")))

    def monitor_fixture(self):
        control = self.report / "compact-supervisor-first-TEST_FIXTURE"
        job = self.report / "compact-core-TEST_FIXTURE"
        control.mkdir()
        job.mkdir()
        value = dict(schema_version=compact_core.VERSION, job=str(job),
                     directory_identity=compact_core._owned_job_state(job))
        workspace._write(control / "owned-job.json", value)
        return control, job, value

    def test_monitor_retries_whole_inventory_on_owned_staging_unlink(self):
        from stockfish_core.yaogu import premarket_runtime_v1 as native_runtime
        control, job, value = self.monitor_fixture()
        disappeared = job / (".codex-compact-stage-" + "a"*32)
        error = FileNotFoundError(errno.ENOENT, "TEST_FIXTURE stage retired", str(disappeared))
        with patch.object(native_runtime, "directory_bytes", side_effect=[error, 123]) as inventory:
            actual, size = first._monitor_owned_job(control, self.report)
        self.assertEqual(actual, value)
        self.assertEqual(size, 123)
        self.assertEqual(inventory.call_count, 2)

    def test_monitor_never_swallows_missing_archive_or_job_or_unrelated_member(self):
        from stockfish_core.yaogu import premarket_runtime_v1 as native_runtime
        control, job, value = self.monitor_fixture()
        for missing in (job / "artifacts" / "all.csv.gz", job, job / "other-stage" / "core",
                        job / (".codex-compact-stage-" + "a"*32) / "unknown.json"):
            with self.subTest(missing=str(missing)):
                error = FileNotFoundError(errno.ENOENT, "TEST_FIXTURE missing", str(missing))
                with patch.object(native_runtime, "directory_bytes", side_effect=error) as inventory:
                    with self.assertRaises(FileNotFoundError):
                        first._monitor_owned_job(control, self.report, value)
                self.assertEqual(inventory.call_count, 1)

    def test_monitor_retry_count_is_bounded_and_does_not_hide_size_violation(self):
        from stockfish_core.yaogu import premarket_runtime_v1 as native_runtime
        control, job, value = self.monitor_fixture()
        missing = job / (".codex-compact-stage-" + "a"*32) / "core" / "all.csv"
        error = FileNotFoundError(errno.ENOENT, "TEST_FIXTURE disappeared", str(missing))
        with patch.object(native_runtime, "directory_bytes", side_effect=error) as inventory:
            with self.assertRaises(FileNotFoundError):
                first._monitor_owned_job(control, self.report, value)
        self.assertEqual(inventory.call_count, 3)
        with patch.object(native_runtime, "directory_bytes", side_effect=[error, compact_core.LIMITS["output_bytes"]+1]):
            with self.assertRaisesRegex(original.CoreError, "EXTERNAL_JOB_OUTPUT_LIMIT"):
                first._monitor_owned_job(control, self.report, value)

    @unittest.skipUnless(os.name == "posix", "Existing supervisor is explicitly POSIX")
    def test_real_child_supervision_and_reuse(self):
        result = first.supervised_command(self.runtime, self.manifest, profile=first.OPTIMIZED,
                                          first_run_id="TEST_FIXTURE-child", allow_test_fixture=True)
        repeated = first.supervised_command(self.runtime, self.manifest, profile=first.OPTIMIZED,
                                            first_run_id="TEST_FIXTURE-child", allow_test_fixture=True)
        self.assertEqual(result["status"], "RECORDED")
        self.assertEqual(repeated["status"], "REUSED")
        self.assertEqual(result["external_supervision"]["exit_code"], 0)
        self.assertTrue(result["external_supervision"]["reaped"])
        self.assertIsNone(repeated["external_supervision"]["owned_job"])

    @unittest.skipUnless(os.name == "posix", "Existing supervisor is explicitly POSIX")
    def test_external_time_budget_terminates_owned_process(self):
        with patch.dict(compact_core.LIMITS, max_seconds=.0001):
            with self.assertRaisesRegex(original.CoreError, "TREE_TIME_LIMIT"):
                first.supervised_command(self.runtime, self.manifest, profile=first.OPTIMIZED,
                    first_run_id="TEST_FIXTURE-time", allow_test_fixture=True)
        failure = list(self.report.glob("compact-supervisor-first-*/failure.json"))
        self.assertEqual(len(failure), 1)
        self.assertEqual(workspace._read(failure[0])["status"], "FAILED_OR_UNKNOWN")


if __name__ == "__main__":
    unittest.main()
