"""TEST_FIXTURE-only new writer, stored reader and unchanged native consumer."""
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from scripts import codex_capture_compact_v1 as compact
from scripts import codex_core as original
from scripts import codex_workspace as workspace
from tests.unit.test_codex_core import Base
from tests.yaogu.test_premarket_capture_cache_v1 import FixtureExecutor


class NativeFixtureExecutor(FixtureExecutor):
    """The calendar fixture declares the native reader's explicit fixture format."""
    def __call__(self, endpoint, params, output, **kwargs):
        answer = super().__call__(endpoint, params, output, **kwargs)
        from stockfish_core.yaogu.providers import archive_receipt
        from stockfish_core.yaogu.provider_requests import ENDPOINTS
        seen = answer["receipts"][0]["first_seen_at"]
        version = "TEST_FIXTURE_CALENDAR_V1" if endpoint == "calendar" else answer["field_version"]
        provider = ENDPOINTS[endpoint]["provider"]
        payload = dict(status=answer["status"], pagination_complete=answer["pagination_complete"],
            rows=answer["rows"], coverage=answer["coverage"], gaps=answer["gaps"],
            field_version=version, first_seen_at=seen)
        receipt = archive_receipt(Path(output) / "raw", provider, endpoint, params,
            payload, status=answer["status"], pagination_complete=answer["pagination_complete"],
            field_version=version, first_seen_at=seen)
        answer.update(provider=provider, field_version=version, request_receipt_id=receipt["receipt_id"],
            receipts=[receipt], completed_at=datetime.now(timezone.utc).isoformat())
        (Path(output) / "request-TEST_FIXTURE.json").write_text(json.dumps(answer))
        return answer


class CaptureCompactTests(Base):
    def setUp(self):
        super().setUp()
        self.executor = NativeFixtureExecutor()
        self.number = 0

    def request_file(self, endpoint="calendar", parameters=None, *, current=False, session=None):
        if parameters is None:
            parameters = dict(start_date="2026-07-01", end_date="2026-10-30")
        self.number += 1
        path = self.root / ("TEST_FIXTURE-request-" + str(self.number) + ".json")
        path.write_text(json.dumps(dict(endpoint=endpoint, parameters=parameters,
            business_session=session, role="TEST_FIXTURE", current=current)))
        return path

    def capture(self, request=None, executor=None):
        return compact.execute_capture(self.runtime, request or self.request_file(),
            allow_test_fixture=True, _fixture_executor=executor or self.executor)

    def load(self, result):
        path = Path(result["archive"]["path"])
        return path, json.loads(path.read_bytes())

    def test_new_writer_roundtrip_original_anchors_and_repeat_without_network(self):
        request = self.request_file()
        first = self.capture(request)
        path, value = self.load(first)
        self.assertGreater(first["avoided_result_bytes"], 0)
        self.assertFalse(Path(value["retired_unpublished_result"]["path"]).exists())
        originals = [value["capture"][key] for key in ("raw", "receipt")] + value["capture"]["producer_files"]
        before = {ref["path"]: original._reference(ref["path"]) for ref in originals}
        decoded = gzip.decompress(Path(value["result_storage"]["stored"]["path"]).read_bytes())
        self.assertEqual(len(decoded), value["result_storage"]["logical"]["bytes"])
        verified = compact.verify_capture_archive(self.runtime, path, allow_test_fixture=True)
        self.assertEqual(verified["status"], "VERIFIED")
        second = compact.execute_capture(self.runtime, request, allow_test_fixture=True)
        self.assertEqual(second["status"], "REUSED")
        self.assertEqual(second["archive"], first["archive"])
        self.assertEqual(len(self.executor.calls), 1)
        self.assertEqual(before, {ref["path"]: original._reference(ref["path"]) for ref in originals})

    def test_missing_network_permission_does_not_call_provider(self):
        with self.assertRaisesRegex(original.CoreError, "NETWORK_FLAG_REQUIRED"):
            compact.execute_capture(self.runtime, self.request_file())
        self.assertFalse(list((self.state / "cache" / "raw").glob("*")))

    def test_fixture_executor_requires_explicit_fixture(self):
        with self.assertRaisesRegex(original.CoreError, "TEST_EXECUTOR_FORBIDDEN"):
            compact.execute_capture(self.runtime, self.request_file(), _fixture_executor=self.executor)

    def test_existing_legacy_result_is_reused_without_compacting_or_new_calls(self):
        from stockfish_core.yaogu.premarket_capture_cache_v1 import CaptureCache, RequestSpec
        from stockfish_core.yaogu.premarket_runtime_v1 import Budget
        path = self.request_file()
        request = json.loads(path.read_bytes())
        cache = CaptureCache(self.state / "cache", Budget(compact._limits(self.runtime)[0]),
            config={"allow_test_fixture": True}, executor=self.executor)
        prior = cache.capture_one(RequestSpec(**request))
        references = {ref["path"]: original._reference(ref["path"])
                      for ref in [prior.metadata[k] for k in ("raw", "receipt", "result")]}
        result = compact.execute_capture(self.runtime, path, allow_test_fixture=True)
        self.assertEqual(result["status"], "REUSED_LEGACY_ORIGINAL")
        self.assertEqual(len(self.executor.calls), 1)
        self.assertEqual(references, {p: original._reference(p) for p in references})

    def test_changed_raw_rejected_without_provider_retry(self):
        path = self.request_file()
        first = self.capture(path)
        _, value = self.load(first)
        raw = Path(value["capture"]["raw"]["path"])
        raw.write_bytes(raw.read_bytes() + b" ")
        with self.assertRaisesRegex(original.CoreError, "PHYSICAL_REFERENCE_CHANGED"):
            self.capture(path)
        self.assertEqual(len(self.executor.calls), 1)

    def test_corrupt_gzip_rejected(self):
        first = self.capture()
        path, value = self.load(first)
        Path(value["result_storage"]["stored"]["path"]).write_bytes(b"TEST_FIXTURE broken")
        with self.assertRaisesRegex(original.CoreError, "PHYSICAL_REFERENCE_CHANGED"):
            compact.verify_capture_archive(self.runtime, path, allow_test_fixture=True)

    def test_reader_enforces_full_logical_length_and_no_trailing_member(self):
        from stockfish_core.yaogu.premarket_runtime_v1 import Budget
        raw = b"TEST_FIXTURE" * 100
        packed = gzip.compress(raw, mtime=0)
        import hashlib
        logical = dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        for data, declaration in ((packed, {**logical, "bytes": 1}), (packed + packed, logical),
                                  (packed[:-1], logical), (packed, {**logical, "sha256": "0" * 64})):
            with self.subTest(size=len(data), declaration=declaration):
                with self.assertRaises(original.CoreError):
                    compact._decode(Budget(), data, declaration)

    def test_deleted_raw_cannot_be_restored_from_compressed_result(self):
        first = self.capture()
        path, value = self.load(first)
        Path(value["capture"]["raw"]["path"]).unlink()
        with self.assertRaises(FileNotFoundError):
            compact.verify_capture_archive(self.runtime, path, allow_test_fixture=True)

    def test_changed_producer_is_rejected(self):
        first = self.capture()
        path, value = self.load(first)
        # Never edit frozen production sources: point to a separate TEST_FIXTURE file.
        fake = self.root / "provider_requests.py"
        fake.write_bytes(b"TEST_FIXTURE wrong producer")
        value["capture"]["producer_files"][0] = original._reference(fake)
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "ORIGINAL_PRODUCER_CHANGED"):
            compact.verify_capture_archive(self.runtime, path, allow_test_fixture=True)

    def test_failure_before_delete_keeps_result_and_refuses_implicit_retry(self):
        request = self.request_file()
        with patch.object(compact, "_decode", side_effect=ValueError("TEST_FIXTURE interrupted")):
            with self.assertRaisesRegex(ValueError, "interrupted"):
                self.capture(request)
        results = list((self.state / "cache" / "raw").glob("**/request-TEST_FIXTURE.json"))
        self.assertEqual(len(results), 1)
        with self.assertRaisesRegex(ValueError, "UNFINISHED_CAPTURE_NO_IMPLICIT_RETRY"):
            self.capture(request)
        self.assertEqual(len(self.executor.calls), 1)

    def test_original_lower_read_budget_is_not_raised(self):
        config = Path(self.runtime.profile["config"])
        config.write_text(json.dumps(dict(state_root=str(self.state), limits={"capture_read_bytes": 1})))
        with self.assertRaisesRegex(RuntimeError, "READ_LIMIT_EXCEEDED"):
            self.capture()
        self.assertEqual(len(self.executor.calls), 0)

    def test_cli_verify_is_available_in_a_new_process(self):
        first = self.capture()
        empty_lookup = self.root / "TEST_FIXTURE-cli-empty-pycache-lookup"
        empty_lookup.mkdir()
        command = [sys.executable, "-I", "-B", "-X", "pycache_prefix=" + str(empty_lookup),
            str(ROOT / "scripts/codex_capture_compact_v1.py"),
            "--runtime", self.runtime.profile["name"], "--registry-root", str(self.registry),
            "--allow-test-fixture", "verify", "--archive", first["archive"]["path"]]
        completed = subprocess.run(command, capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["status"], "VERIFIED")

    def test_native_core_consumes_compact_results_and_reuses_all_eight_artifacts(self):
        from stockfish_core.yaogu import strategy_experiments
        observed, target = "2026-10-23", "2026-10-26"
        specs = [("calendar", dict(start_date="2026-07-01", end_date="2026-10-30")),
                 ("mainboard_sse", {}), ("master_szse", {}), ("stock_basic_all", {}),
                 ("master_all", {"date": observed}), ("market_daily", {"date": observed}),
                 ("adjustment_factors", dict(codes=["sh.600001", "sz.000001"],
                     start_date="1990-01-01", end_date=observed))]
        archives = [self.capture(self.request_file(endpoint, params, session=observed))["archive"]
                    for endpoint, params in specs]
        request = dict(schema_version=compact.CORE_VERSION, compact_archives=archives,
            observation_date=observed, target_session=target,
            cutoff=datetime.now(timezone.utc).isoformat(), coverage_type="TEST_FIXTURE")
        path = self.root / "TEST_FIXTURE-compact-core-request.json"
        path.write_text(json.dumps(request))
        # Independent old-native reference over explicitly new TEST_FIXTURE RESULT
        # copies; real RAW/receipt anchors are the very same fixture originals.
        from stockfish_core.yaogu.premarket_native_core_v1 import run_core
        from stockfish_core.yaogu.premarket_runtime_v1 import Budget
        reference_captures = []
        for ordinal, ref in enumerate(archives):
            value = json.loads(Path(ref["path"]).read_bytes())
            capture = deepcopy(value["capture"])
            result_copy = self.root / ("TEST_FIXTURE-reference-result-" + str(ordinal) + ".json")
            result_copy.write_bytes(gzip.decompress(Path(capture["result"]["path"]).read_bytes()))
            capture["result"] = original._reference(result_copy)
            reference_captures.append(capture)
        reference_budget = Budget(compact._limits(self.runtime)[0])
        with reference_budget.phase("core"), reference_budget.meter_files():
            reference_owner = run_core(captures=reference_captures, observation_date=observed,
                target_session=target, cutoff=request["cutoff"], output_dir=self.root / "TEST_FIXTURE-reference-core",
                budget=reference_budget, coverage_type="TEST_FIXTURE")
            reference_owner.validate()
        with patch.object(strategy_experiments, "run_short_horizon_strategy",
                          wraps=strategy_experiments.run_short_horizon_strategy) as selector:
            first = compact.execute_core(self.runtime, path, allow_test_fixture=True)
            second = compact.execute_core(self.runtime, path, allow_test_fixture=True)
        self.assertEqual(selector.call_count, 1)
        self.assertTrue(second["reused"])
        self.assertEqual(first["summary"], second["summary"])
        cli_reuse = compact.supervised_command(self.runtime, "core", path, allow_test_fixture=True)
        self.assertTrue(cli_reuse["reused"])
        self.assertEqual(cli_reuse["summary"], first["summary"])
        self.assertEqual(cli_reuse["native_r2_calls"], 0)
        self.assertTrue(cli_reuse["external_supervision"]["reaped"])
        self.assertEqual(first["summary"]["daily_v1_readiness"], "UNKNOWN_NOT_EXECUTED")
        evidence = json.loads(Path(first["receipt"]["evidence"]["path"]).read_bytes())
        self.assertEqual(set(evidence["original_logical_artifacts"]), original.FILES)
        self.assertTrue(all(not Path(ref["path"]).exists() for ref in evidence["original_logical_artifacts"].values()))
        from scripts import codex_compact_artifacts as packed
        archive = packed.CompactArchive(evidence["compact_manifest"])
        archive.verify_all()
        saved_run = json.loads(gzip.decompress(archive.read_artifact("canonical-run.json.gz", max_bytes=128 * original.MIB)))
        self.assertEqual(saved_run["input_hash"], reference_owner.run["input_hash"])
        self.assertEqual(saved_run["run_id"], reference_owner.run["run_id"])
        self.assertEqual(saved_run["results"], reference_owner.run["results"])
        # Resealing both small control files cannot replace the real manifest.
        record_path = self.runtime.directory / "operations" / (first["operation_id"] + ".json")
        record = workspace._read(record_path)
        evidence["summary"]["counts"]["focus"] += 1
        record["receipt"]["summary"] = deepcopy(evidence["summary"])
        evidence_path = Path(first["receipt"]["evidence"]["path"])
        workspace._write(evidence_path, evidence, replace=True)
        record["receipt"]["evidence"] = original._reference(evidence_path)
        workspace._write(record_path, record, replace=True)
        with self.assertRaisesRegex(original.CoreError, "SUMMARY_CHANGED"):
            compact.execute_core(self.runtime, path, allow_test_fixture=True)
        self.assertFalse(list(self.root.rglob("*.sqlite*")))

    def test_same_bytes_changed_source_mtime_is_rejected(self):
        first = self.capture()
        path, value = self.load(first)
        raw = Path(value["capture"]["raw"]["path"])
        info = raw.stat()
        os.utime(raw, ns=(info.st_atime_ns, info.st_mtime_ns + 1000000))
        with self.assertRaisesRegex(original.CoreError, "PHYSICAL_REFERENCE_CHANGED"):
            compact.verify_capture_archive(self.runtime, path, allow_test_fixture=True)

    def test_mutation_after_native_authentication_is_detected_at_terminal(self):
        from stockfish_core.yaogu import premarket_native_core_v1 as native
        first = self.capture()
        path, value = self.load(first)
        raw = Path(value["capture"]["raw"]["path"])
        original_auth = native._authenticate_capture
        def changed(*args, **kwargs):
            result = original_auth(*args, **kwargs)
            raw.write_bytes(raw.read_bytes() + b" ")
            return result
        with patch.object(native, "_authenticate_capture", side_effect=changed):
            with self.assertRaisesRegex(original.CoreError, "PHYSICAL_REFERENCE_CHANGED"):
                compact.verify_capture_archive(self.runtime, path, allow_test_fixture=True)

    def test_cli_worker_option_cannot_be_abbreviated(self):
        with self.assertRaises(SystemExit) as error:
            compact.main(["--worker-resul", "TEST_FIXTURE", "verify", "--archive", "TEST_FIXTURE"])
        self.assertEqual(error.exception.code, 2)

    def test_retired_result_locator_is_bound_to_the_stored_logical_result(self):
        first = self.capture()
        path, value = self.load(first)
        for changes in ({"sha256": "0" * 64}, {"path": str(path.parent / "request-claim.json")},
                        {"path": str(self.root / "request-TEST_FIXTURE.json")}, {"fp": {}}):
            with self.subTest(changes=changes):
                changed = deepcopy(value)
                changed["retired_unpublished_result"].update(changes)
                path.write_text(json.dumps(changed))
                with self.assertRaises(ValueError):
                    compact.verify_capture_archive(self.runtime, path, allow_test_fixture=True)

    def test_supervisor_stops_owned_hung_worker_for_time_and_rss_limits(self):
        from stockfish_core.yaogu import premarket_workflow_v1 as workflow
        limits, configuration = compact._limits(self.runtime)
        real_popen = subprocess.Popen
        for trigger in ("TIME", "RSS"):
            with self.subTest(trigger=trigger):
                processes, invocations = [], []
                def sleeper(arguments, **kwargs):
                    invocations.append(arguments)
                    # A real isolated owned child deliberately never reaches a
                    # Python Budget checkpoint. No provider or network exists.
                    process = real_popen(arguments[:5] + ["-c", "import time; time.sleep(30)"], **kwargs)
                    processes.append(process)
                    return process
                lower = {**limits, "max_seconds": 1.5 if trigger == "TIME" else 10}
                sampled_rss = 0 if trigger == "TIME" else lower["tree_rss_bytes"] + 1
                before = set(Path(self.runtime.profile["report_root"]).glob("capture-compact-supervisor-*"))
                with patch.object(compact, "_limits", return_value=(lower, configuration)), \
                     patch.object(compact.subprocess, "Popen", side_effect=sleeper), \
                     patch.object(workflow, "_tree_sample", return_value=sampled_rss):
                    with self.assertRaisesRegex(original.CoreError, "TREE_" + trigger + "_LIMIT"):
                        compact.supervised_command(self.runtime, "verify", self.root / "TEST_FIXTURE-unused.json",
                                                   allow_test_fixture=True)
                self.assertEqual(len(processes), 1)
                self.assertIsNotNone(processes[0].poll())
                self.assertEqual(invocations[0][1:4], ["-I", "-B", "-X"])
                created = set(Path(self.runtime.profile["report_root"]).glob("capture-compact-supervisor-*")) - before
                self.assertEqual(len(created), 1)
                control = created.pop()
                self.assertEqual(invocations[0][4], "pycache_prefix=" + str(control / "empty-pycache-lookup"))
                self.assertEqual(list((control / "empty-pycache-lookup").iterdir()), [])
                failure = workspace._read(control / "failure.json")
                self.assertEqual(failure["status"], "FAILED_OR_UNKNOWN")
                self.assertIsNone(failure["cleanup_error_type"])
                self.assertNotEqual(failure["exit_code"], 0)


ROOT = Path(__file__).resolve().parents[2]
