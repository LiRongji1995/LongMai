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
from scripts import codex_verification_v2 as verification
from scripts import codex_workspace as workspace
from stockfish_core.yaogu import short_horizon_archive_bridge_v1 as bridge
from tests.unit import test_codex_core_archive as archive_fixture


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
    test_same_bytes_replacement_reuses_but_content_tamper_fails = archive_fixture.ArchiveTests.test_same_bytes_replacement_reuses_but_content_tamper_fails
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
