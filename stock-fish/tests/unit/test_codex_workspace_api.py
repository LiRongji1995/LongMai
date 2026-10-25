"""TEST_FIXTURE localhost protocols, private temporary roots, no market/provider data."""
from contextlib import redirect_stdout
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import ModuleType
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import codex_chat as entry
from scripts import codex_workspace as workspace


class ApiFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codex-api-TEST_FIXTURE-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.data = self.root / "TEST_FIXTURE-data"
        self.data.mkdir()
        self.state = self.data / "state"
        self.state.mkdir()
        self.reports = self.data / "reports"
        self.reports.mkdir()
        self.db = self.data / "TEST_FIXTURE.db"
        self.db.write_bytes(b"TEST_FIXTURE not a business database")
        self.registry = self.root / "TEST_FIXTURE-registry"
        self.posts = 0
        self.exports = 0
        self.readiness_status = 503
        self.identity = {"db_path": str(self.db), "state_root": str(self.state)}
        self.jobs = {}
        self.fail_post = False
        self.post_delay = 0
        self.summary_hash = "c" * 64
        self.rows = [{"instrument_id": "600000.SH", "rank": 1}]
        self.saved = {"test_fixture": True, "run_id": "TEST_FIXTURE-run", "input_id": "TEST_FIXTURE-input",
                      "observation_date": "2026-10-23", "coverage_type": "RECONSTRUCTED_HISTORICAL",
                      "results": {entry.PV: {"items": self.rows}},
                      "export_view": {"strategy_id": entry.PV, "selection": "all",
                                      "source_content_hash": self.summary_hash, "filtered_total": 1, "ranked_total": 1}}
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, value, status=200):
                raw = value if isinstance(value, bytes) else json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                if self.path == "/health/ready":
                    self.reply({"ready": False, "service_identity": fixture.identity}, fixture.readiness_status)
                elif self.path == "/api/jobs?limit=100":
                    self.reply(list(fixture.jobs.values())[:100])
                elif self.path.startswith("/api/jobs/"):
                    job = fixture.jobs.get(self.path.rsplit("/", 1)[1])
                    self.reply(job or {}, 200 if job else 404)
                elif "/summary?" in self.path:
                    self.reply({"run_id": fixture.saved["run_id"], "source_run_hash": fixture.summary_hash,
                                "filtered_total": 1, "page": 1, "has_next": False, "items": fixture.rows,
                                "results": {entry.PV: {"item_count": 1}}})
                elif self.path.endswith("/review"):
                    self.reply({"status": "TEST_FIXTURE", "market_edge": "NOT_ESTABLISHED"})
                elif "/export?" in self.path:
                    fixture.exports += 1
                    if "format=csv" in self.path:
                        self.reply(("run_id,observation_date,strategy_id,instrument_id,rank\n"
                                    "TEST_FIXTURE-run,2026-10-23,PV_OBSERVATION_V1,600000.SH,1\n").encode())
                    else:
                        self.reply(fixture.saved)
                else:
                    self.reply({}, 404)

            def do_POST(self):
                fixture.posts += 1
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if fixture.post_delay:
                    time.sleep(fixture.post_delay)
                if fixture.fail_post:
                    self.reply({}, 500)
                    return
                kind, payload = entry.RegisteredClient.job_contract(self.path, body)
                job_id = "TEST_FIXTURE-job-" + str(fixture.posts)
                payload = {**payload, "server_clock": "TEST_FIXTURE-original-clock"}
                fixture.jobs[job_id] = {"job_id": job_id, "kind": kind, "payload": payload, "state": "SUCCEEDED"}
                self.reply({"job_id": job_id, "state": "QUEUED"}, 202)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base_url = "http://127.0.0.1:" + str(self.server.server_port)
        self.profile = {"schema_version": "CODEX_CHAT_RUNTIME_V1", "name": "TEST_FIXTURE-main", "backend": "api",
                        "base_url": self.base_url, "db_path": str(self.db), "state_root": str(self.state),
                        "report_root": str(self.reports), "storage": {
                            "roots": [{"name": "test", "path": str(self.data), "role": "runtime"}],
                            "target_root": "test", "expected_growth_bytes": 4096,
                            "min_free_bytes": 0, "max_managed_bytes": 64 * 1024 * 1024}}
        self.register(self.profile, default=True)

    def register(self, profile, default=False):
        config = self.root / (profile["name"] + ".json")
        config.write_text(json.dumps(profile))
        workspace.register_profile(config, self.registry, make_default=default)
        return workspace.load_runtime(profile["name"], self.registry)

    def client(self, key="TEST_FIXTURE-request", name=None):
        return entry.RegisteredClient(workspace.load_runtime(name, self.registry), request_key=key)

    def test_readiness_503_identity_is_accepted_without_claiming_data_ready(self):
        client = self.client()
        self.assertFalse(client.verify_instance()["ready"])
        self.assertEqual(self.posts, 0)

    def test_readiness_exception_is_only_exact_get_path(self):
        client = entry.Client(self.base_url)
        for path, body in [("/health/ready?x=1", None), ("/health/ready", {}), ("/health/deep", None)]:
            with self.assertRaisesRegex(entry.ResearchError, "INVALID_API_PATH"):
                client.raw(path, body)
        self.assertEqual(self.posts, 0)

    def test_wrong_instance_is_rejected_before_post(self):
        self.identity["db_path"] = str(self.root / "other.db")
        with self.assertRaisesRegex(entry.ResearchError, "IDENTITY_MISMATCH"):
            self.client()
        self.assertEqual(self.posts, 0)

    def test_distinct_chats_reuse_original_post_and_job_id(self):
        first = self.client().get("/api/candidate-data-pipeline/capture", {})
        second = self.client().get("/api/candidate-data-pipeline/capture", {})
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertFalse(first["reused_request"])
        self.assertTrue(second["reused_request"])
        self.assertEqual(self.posts, 1)

    def test_alias_profile_cannot_bypass_same_data_receipt(self):
        alias = {**self.profile, "name": "TEST_FIXTURE-alias"}
        self.register(alias)
        first = self.client().get("/api/candidate-data-pipeline/capture", {})
        second = self.client(name=alias["name"]).get("/api/candidate-data-pipeline/capture", {})
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertEqual(self.posts, 1)

    def test_missing_request_key_capture_is_rejected(self):
        with self.assertRaisesRegex(entry.ResearchError, "EXPLICIT_REQUEST_KEY_REQUIRED"):
            self.client(key=None).get("/api/candidate-data-pipeline/capture", {})
        self.assertEqual(self.posts, 0)

    def test_explicit_input_strategy_reuses_without_request_key(self):
        path = "/api/strategy-experiments/run"
        body = {"input_id": "TEST_FIXTURE-input", "strategy_id": entry.SHORT_HORIZON}
        self.client(key=None).get(path, body)
        self.client(key=None).get(path, body)
        self.assertEqual(self.posts, 1)

    def test_unknown_response_blocks_second_post(self):
        self.fail_post = True
        with self.assertRaisesRegex(entry.ResearchError, "HTTP_500"):
            self.client().get("/api/candidate-data-pipeline/capture", {})
        self.fail_post = False
        with self.assertRaisesRegex(workspace.WorkspaceError, "STATE_UNKNOWN"):
            self.client().get("/api/candidate-data-pipeline/capture", {})
        self.assertEqual(self.posts, 1)

    def test_changed_saved_job_payload_is_not_reused_or_resubmitted(self):
        result = self.client().get("/api/candidate-data-pipeline/capture", {})
        self.jobs[result["job_id"]]["payload"]["server_clock"] = "TEST_FIXTURE-altered"
        with self.assertRaisesRegex(entry.ResearchError, "PAYLOAD_CHANGED"):
            self.client().get("/api/candidate-data-pipeline/capture", {})
        self.assertEqual(self.posts, 1)

    def test_failed_job_is_preserved_without_new_post(self):
        result = self.client().get("/api/candidate-data-pipeline/capture", {})
        self.jobs[result["job_id"]]["state"] = "FAILED"
        with self.assertRaisesRegex(entry.ResearchError, "JOB_FAILED"):
            entry.submit_or_reuse_job(self.client(), "scheduled_candidate_capture",
                                     "/api/candidate-data-pipeline/capture", {}, 0)
        self.assertEqual(self.posts, 1)

    def test_new_request_key_reuses_matching_active_job(self):
        result = self.client().get("/api/candidate-data-pipeline/capture", {})
        self.jobs[result["job_id"]]["state"] = "RUNNING"
        second = self.client(key="TEST_FIXTURE-other-key").get("/api/candidate-data-pipeline/capture", {})
        self.assertEqual(result["job_id"], second["job_id"])
        self.assertTrue(second["reused_inflight_job"])
        self.assertEqual(self.posts, 1)

    def test_new_request_key_cannot_overlap_other_active_worker(self):
        self.jobs["TEST_FIXTURE-other"] = {"job_id": "TEST_FIXTURE-other", "state": "RUNNING",
                                           "kind": "strategy_experiments", "payload": {"input_id": "TEST_FIXTURE"}}
        with self.assertRaisesRegex(entry.ResearchError, "ACTIVE_JOB_REQUIRES_FOLLOW"):
            self.client().get("/api/candidate-data-pipeline/capture", {})
        self.assertEqual(self.posts, 0)

    def test_full_job_window_does_not_hide_an_active_worker(self):
        self.jobs = {str(i): {"job_id": str(i), "state": "SUCCEEDED"} for i in range(100)}
        with self.assertRaisesRegex(entry.ResearchError, "JOB_WINDOW_FULL"):
            self.client().get("/api/candidate-data-pipeline/capture", {})
        self.assertEqual(self.posts, 0)

    def test_concurrent_clients_make_one_post(self):
        self.post_delay = .15
        def submit(_):
            try:
                return self.client().get("/api/candidate-data-pipeline/capture", {})
            except workspace.WorkspaceError as exc:
                return str(exc)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, range(2)))
        self.assertEqual(self.posts, 1)
        self.assertTrue(any(isinstance(row, dict) for row in results))

    def test_shared_exports_avoid_second_large_get_and_duplicate_files(self):
        first_client = self.client()
        first = entry.save_bundle(first_client, entry.official_exports(first_client, self.saved["run_id"]), with_report=False)
        before = {str(path): path.stat().st_size for path in self.reports.rglob("*") if path.is_file()}
        second_client = self.client()
        second_client.runtime.profile["storage"]["max_managed_bytes"] = 1
        second = entry.save_bundle(second_client, entry.official_exports(second_client, self.saved["run_id"]), with_report=False)
        self.assertEqual(self.exports, 2)
        self.assertEqual(first["saved_directory"], second["saved_directory"])
        self.assertTrue(second["reused_local_bundle"])
        self.assertEqual(before, {str(path): path.stat().st_size for path in self.reports.rglob("*") if path.is_file()})

    def test_same_attachment_and_cutoff_reuse_whole_report_without_source_copy(self):
        # This unit tests output coordination only. The frozen archive verifier
        # is represented by a deterministic TEST_FIXTURE return, never bypassed
        # by production code or used to make a market claim.
        module = ModuleType("stockfish_core.yaogu.market_explanation_artifacts")
        module.archived_explanation = lambda path, **kwargs: {"markdown": "TEST_FIXTURE explanation", "as_of": kwargs["as_of"]}
        attachment = self.root / "TEST_FIXTURE-attachment.json"
        attachment.write_text('{"test_fixture":true}')
        cutoff = "2026-10-25T10:00:00+08:00"
        with patch.dict(sys.modules, {module.__name__: module}):
            client = self.client()
            first = entry.save_bundle(client, entry.official_exports(client, self.saved["run_id"]),
                                      market_explanation=attachment, attachment_as_of=cutoff)
            before = {str(p): p.stat().st_size for p in self.reports.rglob("*") if p.is_file()}
            other = self.client()
            second = entry.save_bundle(other, entry.official_exports(other, self.saved["run_id"]),
                                       market_explanation=attachment, attachment_as_of=cutoff)
        self.assertTrue(second["reused_local_bundle"])
        self.assertEqual(first["saved_directory"], second["saved_directory"])
        self.assertEqual(self.exports, 2)
        self.assertEqual(before, {str(p): p.stat().st_size for p in self.reports.rglob("*") if p.is_file()})

    def test_tampered_shared_export_is_rejected_without_download_or_overwrite(self):
        client = self.client()
        saved = entry.save_bundle(client, entry.official_exports(client, self.saved["run_id"]), with_report=False)
        source = Path(saved["saved_directory"]) / "source.json"
        source.write_bytes(b"TEST_FIXTURE altered")
        with self.assertRaisesRegex(entry.ResearchError, "SHARED_REPORT_BYTES_CHANGED"):
            entry.official_exports(self.client(), self.saved["run_id"])
        self.assertEqual(self.exports, 2)
        self.assertEqual(source.read_bytes(), b"TEST_FIXTURE altered")

    def test_capacity_blocks_new_report_before_directory_creation(self):
        client = self.client()
        bundle = entry.official_exports(client, self.saved["run_id"])
        client.runtime.profile["storage"]["max_managed_bytes"] = 1
        with self.assertRaisesRegex(entry.ResearchError, "CAPACITY_PREFLIGHT_BLOCKED"):
            entry.save_bundle(client, bundle, with_report=False)
        self.assertEqual(list(self.reports.iterdir()), [])

    def test_capacity_measures_all_exact_new_report_bytes(self):
        client = self.client()
        bundle = entry.official_exports(client, self.saved["run_id"])
        with patch.object(client.runtime, "preflight", wraps=client.runtime.preflight) as check:
            saved = entry.save_bundle(client, bundle, with_report=False)
        size = sum(path.stat().st_size for path in Path(saved["saved_directory"]).iterdir())
        check.assert_called_once_with(expected_growth_bytes=size, destination=Path(saved["saved_directory"]))

    def test_remote_source_hash_change_rejects_cached_exports(self):
        client = self.client()
        entry.save_bundle(client, entry.official_exports(client, self.saved["run_id"]), with_report=False)
        self.summary_hash = "d" * 64
        with self.assertRaisesRegex(entry.ResearchError, "SHARED_REPORT_SOURCE_IDENTITY_CHANGED"):
            entry.official_exports(self.client(), self.saved["run_id"])
        self.assertEqual(self.exports, 2)

    def test_cached_bytes_still_require_every_summary_order_check(self):
        client = self.client()
        entry.save_bundle(client, entry.official_exports(client, self.saved["run_id"]), with_report=False)
        self.rows = [{"instrument_id": "600001.SH", "rank": 1}]
        with self.assertRaisesRegex(entry.ResearchError, "SUMMARY_EXPORT_SAVED_ORDER_MISMATCH"):
            entry.official_exports(self.client(), self.saved["run_id"])
        self.assertEqual(self.exports, 2)

    def test_unregistered_api_command_never_falls_back_to_8000(self):
        empty = self.root / "TEST_FIXTURE-empty-registry"
        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), patch.object(entry.Client, "raw", side_effect=AssertionError("network forbidden")):
            with redirect_stdout(output):
                code = entry.main(["--registry-root", str(empty), "status"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["reason"], "RUNTIME_REGISTRATION_REQUIRED")
        self.assertFalse(empty.exists())

    def test_explicit_endpoint_override_cannot_silently_change_registered_intent(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = entry.main(["--registry-root", str(self.registry), "--base-url", "http://127.0.0.1:8000", "status"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["reason"], "REGISTERED_RUNTIME_LOCATOR_MUST_COME_FROM_PROFILE")

    def test_unconfigured_runtime_status_does_not_connect_or_create(self):
        empty = self.root / "TEST_FIXTURE-empty-registry"
        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), patch.object(entry.Client, "raw", side_effect=AssertionError("network forbidden")):
            with redirect_stdout(output):
                code = entry.main(["--registry-root", str(empty), "runtime-status"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "UNCONFIGURED")
        self.assertFalse(empty.exists())


if __name__ == "__main__":
    unittest.main()
