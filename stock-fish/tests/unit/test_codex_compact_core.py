"""Native TEST_FIXTURE integration of new compact output, never real data."""
from copy import deepcopy
from collections import Counter
import gzip
import json
import os
from pathlib import Path
import subprocess
import sys
import types
from types import SimpleNamespace
from unittest.mock import patch

from scripts import codex_compact_core as coordinator
from scripts import codex_compact_artifacts as compact
from scripts import codex_core as original
from scripts import codex_workspace as workspace
from tests.unit.test_codex_core import Base
from tests.yaogu.test_premarket_native_core_v1 import captures


class CompactCoreTests(Base):
    def setUp(self):
        super().setUp()
        items, observed, target, cutoff = captures(self.root / "TEST_FIXTURE-originals", sessions=60, history=False)
        self.request(items, observed, target, cutoff.isoformat())

    def execute(self, manifest=None):
        return coordinator.execute_compact_core(self.runtime, manifest or self.manifest,
            profile=coordinator.PROFILE, allow_test_fixture=True)

    def archive(self, result):
        evidence = json.loads(Path(result["receipt"]["evidence"]["path"]).read_bytes())
        return compact.CompactArchive(evidence["compact_manifest"]), evidence

    def test_native_once_exact_eight_logical_bytes_then_new_runtime_reuses_without_growth(self):
        from stockfish_core.yaogu import strategy_experiments
        originals = {}
        pack = compact.compact_owned
        def capture(stage, artifacts, **kwargs):
            originals.update({name: Path(ref["path"]).read_bytes() for name, ref in artifacts.items()})
            return pack(stage, artifacts, **kwargs)
        with patch.object(compact, "compact_owned", side_effect=capture), patch.object(
                strategy_experiments, "run_short_horizon_strategy", wraps=strategy_experiments.run_short_horizon_strategy) as selector:
            first = self.execute()
            before = {str(p): original._reference(p) for p in self.root.rglob("*") if p.is_file()}
            self.runtime = workspace.load_runtime("TEST_FIXTURE-core", self.registry)
            second = self.execute()
        self.assertEqual(selector.call_count, 1)
        self.assertEqual(second["status"], "REUSED")
        self.assertEqual(second["normalizer_calls"], 0)
        self.assertEqual(second["selector_calls"], 0)
        self.assertEqual(second["new_large_artifact_bytes"], 0)
        self.assertEqual(first["summary"], second["summary"])
        self.assertEqual(before, {str(p): original._reference(p) for p in self.root.rglob("*") if p.is_file()})
        archive, evidence = self.archive(first)
        self.assertEqual(set(originals), original.FILES)
        for role, data in originals.items():
            self.assertEqual(data, archive.read_artifact(role, max_bytes=64 * original.MIB))
            self.assertFalse(Path(evidence["original_logical_artifacts"][role]["path"]).exists())
        self.assertFalse(evidence["native_owner_retained"])
        self.assertFalse(evidence["source_authority_restored"])
        self.assertGreater(first["validation"]["saved_payload_bytes"], 0)

    def test_same_request_at_another_path_reuses(self):
        first = self.execute()
        another = self.root / "another-request.json"
        another.write_text(json.dumps(self.value, indent=2))
        second = self.execute(another)
        self.assertEqual(first["operation_id"], second["operation_id"])
        self.assertTrue(second["reused"])

    def test_changed_raw_does_not_recompute(self):
        self.execute()
        Path(self.value["captures"][0]["raw"]["path"]).write_bytes(b"TEST_FIXTURE changed")
        from stockfish_core.yaogu import premarket_native_core_v1 as native
        with patch.object(native, "run_core", side_effect=AssertionError("NO_RECOMPUTE")):
            with self.assertRaisesRegex(original.CoreError, "ORIGINAL_BYTES_OR_IDENTITY_CHANGED"):
                self.execute()

    def test_corrupt_compressed_output_rejected(self):
        first = self.execute()
        archive, _ = self.archive(first)
        path = Path(archive.manifest["artifacts"]["all.csv"]["stored_reference"]["path"])
        path.write_bytes(b"TEST_FIXTURE broken gzip")
        with self.assertRaises(compact.CompactError):
            self.execute()

    def test_summary_cannot_replace_original_manifest(self):
        first = self.execute()
        path = self.runtime.directory / "operations" / (first["operation_id"] + ".json")
        value = workspace._read(path)
        value["receipt"]["summary"]["counts"]["focus"] = 100
        workspace._write(path, value, replace=True)
        with self.assertRaisesRegex(original.CoreError, "EVIDENCE_CHANGED"):
            self.execute()

    def test_explicit_profile_and_fixture_are_required(self):
        with self.assertRaisesRegex(original.CoreError, "EXPLICIT_PROFILE"):
            coordinator.execute_compact_core(self.runtime, self.manifest, profile="implicit", allow_test_fixture=True)
        with self.assertRaisesRegex(original.CoreError, "FIXTURE_SEPARATION"):
            coordinator.execute_compact_core(self.runtime, self.manifest, profile=coordinator.PROFILE)

    def test_failed_pack_preserves_native_files_and_does_not_retry(self):
        with patch.object(compact, "compact_owned", side_effect=RuntimeError("TEST_FIXTURE pack interrupted")):
            with self.assertRaisesRegex(RuntimeError, "pack interrupted"):
                self.execute()
        self.assertEqual(len(list(self.report.rglob("canonical-run.json.gz"))), 1)
        with self.assertRaisesRegex(workspace.WorkspaceError, "STATE_UNKNOWN_RECONCILE_NO_RETRY"):
            self.execute()

    def test_source_limit_rejects_before_claim(self):
        self.value["captures"][0]["raw"]["bytes"] = 17 * original.MIB
        self.value["captures"][0]["raw"]["fp"]["st_size"] = 17 * original.MIB
        self.save_request()
        with self.assertRaisesRegex(original.CoreError, "SOURCE_FILE_LIMIT"):
            self.execute()
        self.assertFalse(list((self.runtime.directory / "operations").glob("*.json")))

    def logical_values(self):
        result = self.execute()
        archive, evidence = self.archive(result)
        values = {role: archive.read_artifact(role, max_bytes=128 * compact.MIB) for role in compact.CORE_FILES}
        return values, evidence

    def verify_values(self, values, evidence, manifest=None, summary=None):
        class Reader:
            def read_artifact(self, role, *, max_bytes):
                self_limit = len(values[role])
                assert self_limit <= max_bytes
                return values[role]
        meter = original._InvocationBudget()
        meter.maximum = coordinator.LIMITS["core_read_bytes"]
        return coordinator._verify_logical_core(Reader(), evidence["original_logical_artifacts"],
            json.loads(values["manifest.json"]) if manifest is None else manifest,
            evidence["summary"] if summary is None else summary, self.value, meter)

    def reseal_values(self, values, evidence, run, source=None):
        """Make all changed output content and exports agree, without new sources."""
        from stockfish_core.yaogu.history_artifacts import digest_json_stream
        from stockfish_core.yaogu.candidate_service import CandidateService
        manifest = json.loads(values["manifest.json"])
        if source is not None:
            values["input.json.gz"] = gzip.compress(json.dumps(source).encode(), mtime=0)
            run["input_hash"] = run["results"][original.STRATEGY]["input_hash"] = digest_json_stream(source)
            run["input_id"] = "market_" + run["input_hash"]
            manifest["input_hash"] = run["input_hash"]
            manifest["source_receipts"] = deepcopy(source["receipts"])
        fields = ("input_hash", "observation_date", "decision_cutoff", "spec_hash", "source_hash",
            "implementation_version", "universe_hash", "execution_profile", "feature_version", "research_context_hash")
        run["run_id"] = "strategy_experiment_" + digest_json_stream({key: run[key] for key in fields})[:24]
        run["content_hash"] = digest_json_stream({key: value for key, value in run.items() if key != "content_hash"})
        manifest["run_id"], manifest["canonical_content_hash"] = run["run_id"], run["content_hash"]
        manifest["counts"] = deepcopy(run["results"][original.STRATEGY]["counts"])
        values["canonical-run.json.gz"] = gzip.compress(json.dumps(run).encode(), mtime=0)
        class Encoder(CandidateService):
            def strategy_experiment(self, run_id):
                assert run_id == run["run_id"]
                return run
        encoder = object.__new__(Encoder)
        for role in manifest["official_exports"]:
            manifest["official_exports"][role]["source_content_hash"] = run["content_hash"]
            if role.endswith(".csv"):
                values[role] = encoder.export_strategy_experiment_csv(run["run_id"],
                    strategy_id=original.STRATEGY, selection=role[:-4]).encode()
            else:
                values[role] = encoder.export_strategy_experiment_json(run["run_id"],
                    strategy_id=original.STRATEGY, selection="observation").encode()
        values["manifest.json"] = json.dumps(manifest).encode()
        return manifest, original._summary(run, manifest, self.value)

    def test_input_hash_and_duplicate_receipts_cannot_be_substituted(self):
        values, evidence = self.logical_values()
        for kind, expected in (("name", "INPUT_HASH_MISMATCH"), ("receipt", "EXACT_INPUT_RECEIPTS")):
            with self.subTest(kind=kind):
                altered = dict(values)
                source = json.loads(gzip.decompress(values["input.json.gz"]))
                if kind == "name":
                    source["universe"]["securities"][0]["name"] = "TEST_FIXTURE altered name"
                else:
                    source["receipts"].append(source["receipts"][0])
                altered["input.json.gz"] = gzip.compress(json.dumps(source).encode(), mtime=0)
                with self.assertRaisesRegex(ValueError, expected):
                    self.verify_values(altered, evidence)

    def test_original_five_export_semantics_are_checked_without_selection(self):
        values, evidence = self.logical_values()
        from stockfish_core.yaogu import strategy_experiments
        with patch.object(strategy_experiments, "run_short_horizon_strategy", side_effect=AssertionError("NO_SELECTION")):
            for role in ("all.csv", "observation.csv", "focus.csv", "unselected.csv", "observation.json"):
                with self.subTest(role=role):
                    altered = dict(values)
                    altered[role] = b'{}' if role.endswith(".json") else b'TEST_FIXTURE truncated csv\n'
                    with self.assertRaisesRegex(ValueError, "OFFICIAL_(CSV|JSON)_MISMATCH"):
                        self.verify_values(altered, evidence)

    def test_consistent_content_hash_does_not_hide_incomplete_eligible_pool(self):
        from stockfish_core.yaogu.history_artifacts import digest_json_stream
        values, evidence = self.logical_values()
        run = json.loads(gzip.decompress(values["canonical-run.json.gz"]))
        run["results"][original.STRATEGY]["items"].pop()
        run["content_hash"] = digest_json_stream({k: v for k, v in run.items() if k != "content_hash"})
        values["canonical-run.json.gz"] = gzip.compress(json.dumps(run).encode(), mtime=0)
        manifest = json.loads(values["manifest.json"])
        summary = deepcopy(evidence["summary"])
        manifest["canonical_content_hash"] = summary["canonical_content_hash"] = run["content_hash"]
        with self.assertRaisesRegex(ValueError, "FULL_QUALIFIED_POOL_REQUIRED"):
            self.verify_values(values, evidence, manifest, summary)

    def test_same_count_receipts_resealed_in_both_outputs_must_match_original_trios(self):
        values, evidence = self.logical_values()
        source = json.loads(gzip.decompress(values["input.json.gz"]))
        run = json.loads(gzip.decompress(values["canonical-run.json.gz"]))
        source["receipts"][0]["provider"] = "TEST_FIXTURE-unrelated-provider"
        manifest, summary = self.reseal_values(values, evidence, run, source)
        self.assertEqual(len(source["receipts"]), len(manifest["source_receipts"]))
        from stockfish_core.yaogu import strategy_experiments, premarket_native_core_v1 as native
        with patch.object(strategy_experiments, "run_short_horizon_strategy", side_effect=AssertionError("NO_SELECTION")), \
             patch.object(native, "normalize_capture_with_report", side_effect=AssertionError("NO_NORMALIZATION")):
            with self.assertRaisesRegex(ValueError, "ORIGINAL_TYPED_RECEIPT_MISMATCH"):
                self.verify_values(values, evidence, manifest, summary)

    def test_fully_self_consistent_pool_clipping_must_match_original_security_qualification(self):
        from stockfish_core.yaogu import strategy_experiments
        from stockfish_core.yaogu.contracts import digest
        values, evidence = self.logical_values()
        run = json.loads(gzip.decompress(values["canonical-run.json.gz"]))
        result = run["results"][original.STRATEGY]
        removed = result["items"].pop()["instrument_id"]
        result["eligible_pool_ids"].remove(removed)
        qualification = deepcopy(run["qualification"])
        qualification["eligible_ids"].remove(removed)
        qualification["eligible"] -= 1
        qualification["known_excluded"] += 1
        qualification["known_excluded_records"].append(dict(instrument_id=removed, reason="SUSPENDED_CURRENT_SESSION"))
        run["qualification"] = result["qualification"] = qualification
        run["universe_hash"] = digest(qualification)
        if "universe_hash" in result:
            result["universe_hash"] = run["universe_hash"]
        counts = Counter(row["decision_status"] for row in result["items"])
        result["counts"].update(eligible=len(result["items"]), evaluated=len(result["items"]),
            **{name: counts[name.upper()] for name in ("focus", "watch", "rejected", "unknown")})
        result["research_selected_ids"] = [row["instrument_id"] for row in result["items"] if row["research_selected"]]
        for selection in run["selection_results"].values():
            selection["selected_ids"] = result["research_selected_ids"]
            selection["count"] = len(selection["selected_ids"])
            selection["items"] = [row for row in selection["items"] if row["instrument_id"] != removed]
        manifest, summary = self.reseal_values(values, evidence, run)
        with patch.object(strategy_experiments, "run_short_horizon_strategy", side_effect=AssertionError("NO_SELECTION")):
            # Every other content, pool, count and formal-export relationship is
            # consistent: only binding qualification back to the input rejects it.
            with patch.object(strategy_experiments, "_prepare_context", return_value=SimpleNamespace(qualification=qualification)):
                self.verify_values(values, evidence, manifest, summary)
            with self.assertRaisesRegex(ValueError, "ORIGINAL_QUALIFICATION_MISMATCH"):
                self.verify_values(values, evidence, manifest, summary)

    def attach_locator(self):
        archive = self.root / "TEST_FIXTURE-locator"
        target = self.root / "TEST_FIXTURE-locator-target"
        (archive / "cache").mkdir(parents=True)
        target.mkdir()
        path = archive / "cache" / "raw"
        path.symlink_to(target, target_is_directory=True)
        declaration = dict(path=str(path), target=str(target), archive_root=str(archive), owned=True,
            fp={key: int(getattr(path.lstat(), key, 0)) for key in compact.FP_KEYS},
            directories={str(p): compact._dir_identity(p.lstat()) for p in (path.parent, archive, target)})
        self.value["captures"][0]["verification_locator"] = declaration
        self.save_request()
        return path, target

    def test_missing_locator_in_rewritten_evidence_is_rejected(self):
        self.attach_locator()
        result = self.execute()
        _, evidence = self.archive(result)
        evidence["locators"] = []
        evidence_path = Path(result["receipt"]["evidence"]["path"])
        workspace._write(evidence_path, evidence, replace=True)
        record_path = self.runtime.directory / "operations" / (result["operation_id"] + ".json")
        record = workspace._read(record_path)
        record["receipt"]["evidence"] = original._reference(evidence_path)
        workspace._write(record_path, record, replace=True)
        with self.assertRaisesRegex(original.CoreError, "COMPLETE_LOCATOR_SET_REQUIRED"):
            self.execute()

    def test_locator_change_during_output_semantics_is_caught_at_terminal(self):
        path, target = self.attach_locator()
        implementation = coordinator._verify_logical_core
        def mutate(*args, **kwargs):
            implementation(*args, **kwargs)
            path.unlink()
            path.symlink_to(target, target_is_directory=True)
        with patch.object(coordinator, "_verify_logical_core", side_effect=mutate):
            with self.assertRaisesRegex(ValueError, "LOCATOR_CHANGED"):
                self.execute()

    def test_foreign_loaded_native_module_is_rejected(self):
        foreign = types.ModuleType("stockfish_core.TEST_FIXTURE_FOREIGN")
        foreign.__file__ = __file__
        with patch.dict(sys.modules, {foreign.__name__: foreign}):
            with self.assertRaisesRegex(original.CoreError, "FOREIGN_LOADED_SOURCE"):
                self.execute()

    def test_coordinator_snapshot_change_during_output_is_rejected(self):
        snapshot, calls = coordinator._snapshot, []
        def changed(profile):
            value = snapshot(profile)
            calls.append(True)
            if len(calls) == 3:
                value = deepcopy(value)
                value["compact_coordinator_sources"][0]["sha256"] = "0" * 64
            return value
        with patch.object(coordinator, "_snapshot", side_effect=changed):
            with self.assertRaisesRegex(original.CoreError, "IMPLEMENTATION_CHANGED_AT_TERMINAL"):
                self.execute()
        record = workspace._read(next((self.runtime.directory / "operations").glob("*.json")))
        self.assertEqual(record["phase"], "FAILED_OR_UNKNOWN")

    def test_posix_supervisor_tracks_owned_output_and_reuses_no_new_core(self):
        self.assertEqual(os.name, "posix")
        first = coordinator.supervised_command(self.runtime, self.manifest,
            profile=coordinator.PROFILE, allow_test_fixture=True)
        self.assertEqual(first["status"], "RECORDED")
        external = first["external_supervision"]
        self.assertTrue(external["reaped"])
        self.assertGreater(external["peak_tree_rss_bytes"], 0)
        self.assertGreater(external["peak_owned_job_bytes"], 0)
        self.assertEqual(Path(external["owned_job"]["job"]), Path(first["receipt"]["evidence"]["path"]).parent)
        second = coordinator.supervised_command(self.runtime, self.manifest,
            profile=coordinator.PROFILE, allow_test_fixture=True)
        self.assertEqual(second["status"], "REUSED")
        self.assertEqual(second["selector_calls"], 0)
        self.assertIsNone(second["external_supervision"]["owned_job"])
        self.assertEqual(len(list(self.report.glob("compact-core-*"))), 1)

    def test_external_owned_job_limit_and_directory_identity(self):
        control = self.report / "compact-supervisor-TEST_FIXTURE"
        job = self.report / "compact-core-TEST_FIXTURE"
        control.mkdir()
        job.mkdir()
        declaration = dict(schema_version=coordinator.VERSION, job=str(job), directory_identity=coordinator._owned_job_state(job))
        workspace._write(control / "owned-job.json", declaration)
        (job / "payload").write_bytes(b"x" * 256)
        with patch.dict(coordinator.LIMITS, output_bytes=128):
            with self.assertRaisesRegex(original.CoreError, "EXTERNAL_JOB_OUTPUT_LIMIT"):
                coordinator._monitor_owned_job(control, self.report)
        job.rename(self.report / "preserved-old-job")
        job.mkdir()
        with self.assertRaisesRegex(original.CoreError, "OWNED_JOB_IDENTITY"):
            coordinator._monitor_owned_job(control, self.report)
        self.assertTrue((self.report / "preserved-old-job" / "payload").exists())

    def test_supervisor_timeout_reaps_its_child_and_keeps_failure(self):
        popen, spawned = subprocess.Popen, []
        def sleeper(command, **kwargs):
            if command[0] == "ps":
                return popen(command, **kwargs)
            process = popen([sys.executable, "-I", "-B", "-c", "import time; time.sleep(30)"], **kwargs)
            spawned.append(process)
            return process
        with patch.dict(coordinator.LIMITS, max_seconds=0.5), patch.object(coordinator.subprocess, "Popen", side_effect=sleeper):
            with self.assertRaisesRegex(original.CoreError, "TREE_TIME_LIMIT"):
                coordinator.supervised_command(self.runtime, self.manifest,
                    profile=coordinator.PROFILE, allow_test_fixture=True)
        self.assertEqual(len(spawned), 1)
        self.assertIsNotNone(spawned[0].poll())
        failure = json.loads(next(self.report.glob("compact-supervisor-*/failure.json")).read_bytes())
        self.assertEqual(failure["reason"], "COMPACT_CORE_TREE_TIME_LIMIT")
        self.assertIsNone(failure["cleanup_error_type"])

    def test_budget_failure_codes_survive_without_exposing_unknown_exception(self):
        from stockfish_core.yaogu.premarket_runtime_v1 import BudgetExceeded
        self.assertEqual(coordinator._failure_reason(BudgetExceeded("READ_LIMIT_EXCEEDED:core")), "READ_LIMIT_EXCEEDED:core")
        self.assertEqual(coordinator._failure_reason(RuntimeError("TEST_FIXTURE private credential")), "COMPACT_CORE_EXECUTION_FAILED")
