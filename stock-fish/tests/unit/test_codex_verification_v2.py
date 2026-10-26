"""Independent TEST_FIXTURE checks; no real DB, source collection or service."""
from collections import Counter
import gzip
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import codex_core_archive as archive_v1
from scripts import codex_core_archive_v2 as archive
from scripts import codex_compact_core as core_v1
from scripts import codex_compact_core_v2 as core
from scripts import codex_compact_artifacts as compact
from scripts import codex_verification_v2 as verification
from scripts import codex_first_core_v2 as first_core
from scripts import codex_workspace as workspace
from stockfish_core.yaogu import short_horizon_archive_bridge_v1 as bridge
from tests.unit import test_codex_core_archive as archive_fixture
from tests.unit import test_codex_compact_core as core_fixture


class BufferAndDiagnosticTests(unittest.TestCase):
    class Session:
        def check(self):
            pass

    def test_exact_json_values_and_rejections_match_original(self):
        samples = [b'{"a":[1,1.5,-0.0,"TEST_FIXTURE",null]}',
                   '{"汉字":"😀","number":1e+100}'.encode(),
                   b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}',
                   b'{"a":-Infinity}', b'{"a":1} trailing', b'{"x":"\xff"}']
        for raw in samples:
            with self.subTest(raw=raw):
                payload = gzip.compress(raw)
                try:
                    expected = bridge._decompress(payload, self.Session())
                except Exception as error:
                    with self.assertRaises(type(error)):
                        verification.decompress_document(payload, self.Session())
                else:
                    self.assertEqual(expected, verification.decompress_document(payload, self.Session()))

    def test_gzip_expansion_cap_cancellation_and_charging_preserved(self):
        charges = []
        raw = gzip.compress(b'{"TEST_FIXTURE":"value"}')
        value = verification.decompress_document(raw, self.Session(), charge=charges.append)
        self.assertEqual(value, {"TEST_FIXTURE": "value"})
        self.assertEqual(sum(charges), len(gzip.decompress(raw)))
        with self.assertRaisesRegex(ValueError, "DECOMPRESSED_DOCUMENT_BUDGET"):
            verification.decompress_document(raw, self.Session(), maximum=4)
        failure = RuntimeError("TEST_FIXTURE_CANCELLED")
        with patch.object(self.Session, "check", side_effect=failure):
            with self.assertRaises(RuntimeError) as caught:
                verification.decompress_document(raw, self.Session())
        self.assertIs(caught.exception, failure)
        with self.assertRaises((EOFError, OSError)):
            verification.decompress_document(raw[:-4], self.Session())

    def test_diagnostics_preserve_result_exception_and_cpu_stage(self):
        def digest_market_input():
            return sum(range(50))
        result, report = verification.outcome_diagnostics(digest_market_input)
        self.assertEqual(result, 1225)
        self.assertFalse(report["execution_or_budget_changed"])
        self.assertTrue(any(row["function"] == "digest_market_input" for row in report["stages"]))
        failure = ValueError("TEST_FIXTURE_FAILURE")
        def terminal():
            raise failure
        with self.assertRaises(ValueError) as caught:
            verification.outcome_diagnostics(terminal)
        self.assertIs(caught.exception, failure)
        self.assertEqual(failure.verification_v2["status"], "FAILED")

    def test_private_function_binding_never_changes_old_globals(self):
        old = bridge._trio
        replacement = verification.bind_functions(bridge, ("_trio",), {"_json": object()})["_trio"]
        self.assertIs(bridge._trio, old)
        self.assertIs(bridge._trio.__globals__["_json"], bridge._json)
        self.assertIsNot(replacement.__globals__, old.__globals__)

    def test_supervisor_passes_explicit_empty_bytecode_lookup_and_pins_script(self):
        commands = []
        module = SimpleNamespace(Popen=lambda command, **kwargs: commands.append(command))
        wrapped = verification.isolated_subprocess(module, __file__)
        with tempfile.TemporaryDirectory(prefix="TEST_FIXTURE-v2-pycache-") as directory:
            output = Path(directory).resolve() / "result.json"
            command = [sys.executable, "-I", "-B", str(Path(__file__).resolve()),
                       "--worker-result", str(output)]
            wrapped.Popen(command)
            self.assertEqual(commands[0][3:5], ["-X", "pycache_prefix=" + str(output.parent / "empty-pycache-lookup")])
            self.assertFalse((output.parent / "empty-pycache-lookup").exists())
            bad = list(command)
            bad[3] = str(output.parent / "foreign.py")
            with self.assertRaisesRegex(ValueError, "PYTHON_SCRIPT_IDENTITY"):
                wrapped.Popen(bad)
            (output.parent / "empty-pycache-lookup").mkdir()
            with self.assertRaisesRegex(ValueError, "EMPTY_PYCACHE_LOOKUP"):
                wrapped.Popen(command)


class ArchiveV2Tests(unittest.TestCase):
    setUp = archive_fixture.ArchiveTests.setUp
    save = archive_fixture.ArchiveTests.save
    edit_evidence_and_record = archive_fixture.ArchiveTests.edit_evidence_and_record

    def register(self, **kwargs):
        return archive.register_archive(self.runtime, self.request_path, self.worker, self.supervisor,
            profile=archive.PROFILE, allow_test_fixture=True, **kwargs)

    def reuse(self, first, **kwargs):
        return archive.reuse_archive(self.runtime, first["archive_id"], profile=archive.PROFILE,
            allow_test_fixture=True, **kwargs)

    def test_original_and_new_profiles_coexist_and_equal_complete_summary(self):
        old = archive_v1.register_archive(self.runtime, self.request_path, self.worker, self.supervisor,
            profile=archive_v1.PROFILE, allow_test_fixture=True)
        first = self.register()
        self.assertEqual(old["summary"], first["summary"])
        self.assertNotEqual(old["archive_id"], first["archive_id"])
        before = {str(p): archive_fixture.workflow._ref(p) for p in self.root.rglob("*") if p.is_file()}
        reads = Counter()
        original_read = bridge.ArchiveVerificationSession.read
        def read(session, path, *args, **kwargs):
            reads[str(path)] += 1
            return original_read(session, path, *args, **kwargs)
        with patch.object(bridge.ArchiveVerificationSession, "read", read):
            second = self.reuse(first)
            optimized_reads = reads.copy()
            reads.clear()
            legacy = archive_v1.reuse_archive(self.runtime, old["archive_id"], profile=archive_v1.PROFILE,
                                             allow_test_fixture=True)
        self.assertEqual(second["summary"], legacy["summary"])
        self.assertEqual(second["metrics"]["terminal_rereads"], legacy["metrics"]["terminal_rereads"])
        multiplicities = Counter(item["raw"]["path"] for item in self.items)
        for path, count in multiplicities.items():
            self.assertEqual(optimized_reads[path], reads[path] - count)
        self.assertEqual(before, {str(p): archive_fixture.workflow._ref(p) for p in self.root.rglob("*") if p.is_file()})
        self.assertIn("original_trio_with_clock", second["verification_v2_metrics"]["stages"])
        with self.assertRaisesRegex(ValueError, "RECORDED_OPERATION_REQUIRED"):
            self.reuse(old)

    test_resealed_evidence_cannot_omit_raw_dependency = archive_fixture.ArchiveTests.test_resealed_evidence_cannot_omit_raw_dependency
    test_same_bytes_replacement_changes_physical_identity = archive_fixture.ArchiveTests.test_same_bytes_replacement_changes_physical_identity
    test_query_date_cutoff_and_manifest_must_match = archive_fixture.ArchiveTests.test_query_date_cutoff_and_manifest_must_match
    test_tight_read_budget_never_normalizes_or_creates_big_file = archive_fixture.ArchiveTests.test_tight_read_budget_never_normalizes_or_creates_big_file

    def test_raw_clock_projection_is_exact_and_original_trio_unmodified(self):
        saved = bridge._trio
        calls = []
        original_read = archive_v1._read
        def read(*args, **kwargs):
            calls.append(str(args[1]))
            return original_read(*args, **kwargs)
        # The V2 orchestration must not call its control reader for RAW clocks.
        with patch.object(archive, "_read", side_effect=read):
            self.register()
        self.assertFalse({item["raw"]["path"] for item in self.items} & set(calls))
        self.assertIs(bridge._trio, saved)


class CompactV2Tests(core_fixture.Base):
    def setUp(self):
        super().setUp()
        items, observed, target, cutoff = core_fixture.captures(
            self.root / "TEST_FIXTURE-originals", sessions=60, history=False)
        self.request(items, observed, target, cutoff.isoformat())
    archive = core_fixture.CompactCoreTests.archive

    def execute(self, manifest=None):
        return core.execute_compact_core(self.runtime, manifest or self.manifest,
            profile=core.PROFILE, allow_test_fixture=True)

    def test_each_logical_artifact_consumed_once_and_original_storage_guards_remain(self):
        logical_reads = Counter()
        original_read = compact.CompactArchive.read_artifact
        def read(reader, role, **kwargs):
            logical_reads[role] += 1
            return original_read(reader, role, **kwargs)
        first = self.execute()
        before = {str(p): core.original._reference(p) for p in self.root.rglob("*") if p.is_file()}
        with patch.object(compact.CompactArchive, "read_artifact", read):
            again = self.execute()
        self.assertEqual(logical_reads, Counter({role: 1 for role in compact.CORE_FILES}))
        self.assertEqual(first["summary"], again["summary"])
        self.assertEqual(before, {str(p): core.original._reference(p) for p in self.root.rglob("*") if p.is_file()})
        self.assertEqual(again["verification_v2_metrics"]["stages"]["compact_final_physical_sha"]["calls"], 1)
        self.assertIs(core_v1.compact.CompactArchive, compact.CompactArchive)

    test_corrupt_compressed_output_rejected = core_fixture.CompactCoreTests.test_corrupt_compressed_output_rejected
    test_changed_raw_does_not_recompute = core_fixture.CompactCoreTests.test_changed_raw_does_not_recompute
    def test_failed_pack_preserves_native_files_and_does_not_retry(self):
        with patch.object(core._compact_reader, "compact_owned", side_effect=RuntimeError("TEST_FIXTURE pack interrupted")):
            with self.assertRaisesRegex(RuntimeError, "pack interrupted"):
                self.execute()
        self.assertEqual(len(list(self.report.rglob("canonical-run.json.gz"))), 1)
        with self.assertRaisesRegex(workspace.WorkspaceError, "STATE_UNKNOWN_RECONCILE_NO_RETRY"):
            self.execute()

    def test_missing_semantic_role_cannot_finalize(self):
        result = self.execute()
        _, evidence = self.archive(result)
        candidate = verification.single_pass_archive_class(compact)(evidence["compact_manifest"])
        ticket = candidate.verify_all()
        self.assertEqual(ticket["status"], "PENDING_COMPLETE_SEMANTIC_CONSUMPTION")
        for role in compact.CORE_FILES - {"all.csv"}:
            candidate.read_artifact(role, max_bytes=64 * verification.MIB)
        with self.assertRaisesRegex(ValueError, "COMPLETE_EIGHT_SEMANTIC_ROLES"):
            candidate.finish()

    def test_late_output_change_is_rejected_by_complete_physical_terminal(self):
        result = self.execute()
        _, evidence = self.archive(result)
        candidate = verification.single_pass_archive_class(compact)(evidence["compact_manifest"])
        candidate.verify_all()
        for role in compact.CORE_FILES:
            candidate.read_artifact(role, max_bytes=64 * verification.MIB)
        path = Path(candidate.manifest["artifacts"]["all.csv"]["stored_reference"]["path"])
        path.write_bytes(path.read_bytes() + b"TEST_FIXTURE_LATE_CHANGE")
        with self.assertRaises(ValueError):
            candidate.finish()

    def test_explicit_source_adapter_requires_exact_physical_reference(self):
        result = self.execute()
        _, evidence = self.archive(result)
        calls = []
        def verify(bad_reference):
            candidate = verification.single_pass_archive_class(compact)(evidence["compact_manifest"])
            candidate.verify_all()
            manifest = json.loads(candidate.read_artifact("manifest.json", max_bytes=16 * verification.MIB))
            meter = core.original._InvocationBudget()
            def read(path, *, expected, maximum):
                reference, raw = core.original._read_file(path, maximum, retain=True)
                meter.charge(len(raw))  # explicit adapter owns its returned bytes
                calls.append((path, expected))
                if bad_reference:
                    reference = dict(reference, sha256="0" * 64)
                return raw, reference
            core._verify_logical_core(candidate, evidence["original_logical_artifacts"],
                manifest, evidence["summary"], self.value, meter, source_read=read)
        verify(False)
        self.assertTrue(calls)
        with self.assertRaisesRegex(ValueError, "ORIGINAL_TRIO_CHANGED"):
            verify(True)

    def test_supervised_v2_worker_uses_new_profile_and_reuses(self):
        first = core.supervised_command(self.runtime, self.manifest, profile=core.PROFILE, allow_test_fixture=True)
        second = core.supervised_command(self.runtime, self.manifest, profile=core.PROFILE, allow_test_fixture=True)
        self.assertEqual(first["status"], "RECORDED")
        self.assertEqual(second["status"], "REUSED")
        self.assertEqual(second["selector_calls"], 0)
        self.assertTrue(second["external_supervision"]["reaped"])
        self.assertIn("verification_v2_metrics", second)

    def test_first_core_reference_and_c128_profiles_bind_v2_and_restore_encoders(self):
        from stockfish_core.yaogu import history_artifacts, premarket_native_core_v1
        saved = (history_artifacts.digest_market_input, premarket_native_core_v1.digest_market_input)
        results = []
        for profile in first_core.PROFILES:
            first = first_core.execute_child(self.runtime, self.manifest, profile=profile,
                allow_test_fixture=True, _fixture_in_process=True)
            again = first_core.execute_child(self.runtime, self.manifest, profile=profile,
                allow_test_fixture=True, _fixture_in_process=True)
            self.assertEqual(first["status"], "RECORDED")
            self.assertEqual(again["status"], "REUSED")
            self.assertEqual(again["selector_calls"], 0)
            self.assertTrue(again["encoding_bindings_restored"])
            self.assertIn("compact_final_physical_sha", again["verification_v2_metrics"]["stages"])
            self.assertEqual(first["first_core_execution"]["execution_profile"], profile)
            results.append(first)
        self.assertEqual(results[0]["summary"]["input_hash"], results[1]["summary"]["input_hash"])
        self.assertNotEqual(results[0]["operation_id"], results[1]["operation_id"])
        self.assertEqual(saved, (history_artifacts.digest_market_input, premarket_native_core_v1.digest_market_input))

    def test_supervised_first_core_v2_pins_source_and_keeps_lookup_empty(self):
        result = first_core.supervised_command(self.runtime, self.manifest,
            profile=first_core.OPTIMIZED, allow_test_fixture=True)
        self.assertEqual(result["status"], "RECORDED")
        self.assertEqual(result["first_core_execution"]["execution_profile"], first_core.OPTIMIZED)
        self.assertTrue(result["encoding_bindings_restored"])
        self.assertTrue(result["external_supervision"]["reaped"])
        directory = Path(result["external_supervision"]["control_directory"])
        self.assertFalse((directory / "empty-pycache-lookup").exists())


if __name__ == "__main__":
    unittest.main()
