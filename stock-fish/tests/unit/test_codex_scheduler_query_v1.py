"""TEST_FIXTURE: opt-in scheduler queries, original semantics and ownership."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import codex_scheduler_query_v1 as extension
from stockfish_core.common import canonical_json
from stockfish_core.jobs import JobService
from stockfish_core.storage import IntegrityError, SQLiteStore, StorageError
from stockfish_core.yaogu.scheduler import CandidateScheduler, SH


class SchedulerQueryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="TEST_FIXTURE_scheduler_query_")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.database = self.root / "TEST_FIXTURE.sqlite3"
        self.state = self.root / "TEST_FIXTURE_state"
        self.state.mkdir()
        self.store = SQLiteStore(self.database)
        self.day = date(2026, 9, 21)
        self.clock = lambda: datetime(2026, 9, 21, 20, 40, tzinfo=SH)

    def insert(self, job_id, *, root=None, kind=extension.KIND, state="SUCCEEDED",
               payload=None, created="2026-09-21T20:31:00+08:00", result=None, error=None):
        value = {"source": "TEST_FIXTURE", "root_job_id": root} if payload is None else payload
        with self.store.transaction() as connection:
            connection.execute(
                "INSERT INTO jobs(job_id,kind,state,payload,data,result,error,created_at,updated_at,finished_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (job_id, kind, state, canonical_json(value), "{}", canonical_json(result),
                 canonical_json(error), created, created, created))

    def jobs(self):
        extension.migrate(self.database)
        jobs = extension.FamilyQueryJobService(self.store, max_workers=1, max_pending=20, recover=False)
        self.addCleanup(jobs.shutdown, cancel_pending=False)
        return jobs

    def scheduler(self, jobs, cls=extension.FamilyQueryScheduler):
        value = object.__new__(cls)
        config = SimpleNamespace(schedule=SimpleNamespace(evening="20:30"))
        value.service = SimpleNamespace(jobs=jobs, store=self.store,
                                        current_config=lambda: config)
        value.clock = self.clock
        value.sessions = (date(2026, 9, 18), self.day, date(2026, 9, 22))
        value.started_at = datetime(2026, 9, 21, 19, tzinfo=SH)
        return value

    def content_snapshot(self):
        with self.store.transaction(write=False) as connection:
            return {name: [tuple(row) for row in connection.execute(f"SELECT * FROM {name}")]
                    for name in ("jobs", "job_events", "documents", "schema_migrations")}

    def test_migrate_preserves_jobs_documents_core_migrations_and_is_idempotent(self):
        self.insert("TEST_FIXTURE_queued", state="QUEUED")
        self.store.put_document("TEST_FIXTURE", "preserved", {"source": "TEST_FIXTURE"})
        before = self.content_snapshot()
        self.assertEqual(extension.status(self.database)["status"], "NOT_INSTALLED")
        self.assertEqual(extension.migrate(self.database)["status"], "INSTALLED")
        self.assertEqual(extension.migrate(self.database)["status"], "ALREADY_INSTALLED")
        self.assertEqual(self.content_snapshot(), before)
        self.assertEqual(extension.status(self.database)["status"], "INSTALLED")
        # The unchanged default store still recognizes its original V2 schema.
        self.assertEqual(SQLiteStore(self.database).readiness()["status"], "healthy")

    def test_missing_database_and_alias_are_rejected_without_creation(self):
        missing = self.root / "TEST_FIXTURE_missing.sqlite3"
        for function in (extension.status, extension.migrate):
            with self.assertRaisesRegex(ValueError, "EXISTING_UNALIASED"):
                function(missing)
        self.assertFalse(missing.exists())
        alias = self.root / "TEST_FIXTURE_alias.sqlite3"
        alias.symlink_to(self.database)
        with self.assertRaisesRegex(ValueError, "EXISTING_UNALIASED"):
            extension.migrate(alias)

    def test_migration_refuses_existing_worker_owner(self):
        jobs = JobService(self.store, recover=False)
        try:
            with self.assertRaisesRegex(StorageError, "another job worker"):
                extension.migrate(self.database)
            self.assertEqual(extension.status(self.database)["status"], "NOT_INSTALLED")
        finally:
            jobs.shutdown(cancel_pending=False)

    def test_partial_or_wrong_extension_objects_are_never_adopted(self):
        with self.store.transaction() as connection:
            connection.execute(f"CREATE INDEX {extension.INDEX} ON jobs(kind)")
        with self.assertRaisesRegex(IntegrityError, "MISSING_OR_PARTIAL"):
            extension.migrate(self.database)
        with self.assertRaisesRegex(IntegrityError, "MISSING_OR_PARTIAL"):
            extension.status(self.database)

    def test_ambiguous_existing_payload_refuses_atomic_migration(self):
        self.insert("TEST_FIXTURE_bad")
        for raw in ('[]', 'null', '{"root_job_id":"a","root_job_id":"b"}'):
            with self.subTest(payload=raw):
                with self.store.transaction() as connection:
                    connection.execute("UPDATE jobs SET payload=? WHERE job_id=?", (raw, "TEST_FIXTURE_bad"))
                with self.assertRaisesRegex(IntegrityError, "AMBIGUOUS_EXISTING"):
                    extension.migrate(self.database)
                self.assertEqual(extension.status(self.database)["status"], "NOT_INSTALLED")

    def test_insert_and_update_guards_preserve_json_root_equivalence(self):
        extension.migrate(self.database)
        self.insert("TEST_FIXTURE_safe", root="safe")
        for raw in ('[]', '{"root_job_id":"a","root_job_id":"b"}'):
            with self.assertRaises(StorageError):
                with self.store.transaction() as connection:
                    connection.execute("UPDATE jobs SET payload=? WHERE job_id=?", (raw, "TEST_FIXTURE_safe"))
            with self.assertRaises(StorageError):
                with self.store.transaction() as connection:
                    connection.execute("INSERT INTO jobs(job_id,kind,state,payload,data,created_at,updated_at) "
                                       "VALUES(?,?,'QUEUED',?,'{}','TEST_FIXTURE','TEST_FIXTURE')",
                                       ("TEST_FIXTURE_bad", extension.KIND, raw))
        # Other job kinds retain the original store's behavior.
        self.insert("TEST_FIXTURE_other", kind="TEST_FIXTURE_OTHER", payload=[])
        with self.assertRaises(StorageError):
            with self.store.transaction() as connection:
                connection.execute("UPDATE jobs SET kind=? WHERE job_id='TEST_FIXTURE_other'", (extension.KIND,))

    def test_all_fixed_ids_and_over_thousand_random_retries_equal_original(self):
        jobs = self.jobs()
        optimized = self.scheduler(jobs)
        original = self.scheduler(jobs, CandidateScheduler)
        root = original._primary_schedule_for(self.day)["job_id"]
        self.insert(root, payload={"source": "TEST_FIXTURE"}, state="FAILED")
        self.insert(root + "_retry_1", kind="TEST_FIXTURE_LEGACY_KIND", state="CANCELLED")
        self.insert(root + "_retry_2", root=root, state="RUNNING")
        with self.store.transaction() as connection:
            for index in range(2105):
                wanted = index < 1005
                connection.execute(
                    "INSERT INTO jobs(job_id,kind,state,payload,data,created_at,updated_at) "
                    "VALUES(?,?,'SUCCEEDED',?,'{}',?,?)",
                    (f"TEST_FIXTURE_random_{index:05}", extension.KIND,
                     canonical_json({"source": "TEST_FIXTURE", "root_job_id": root if wanted else "TEST_FIXTURE_OTHER"}),
                     f"2026-09-21T20:32:{index % 60:02}+08:00", "TEST_FIXTURE"))
        with patch.object(jobs, "_decode", wraps=JobService._decode) as decode:
            expected = original._primary_jobs(self.day)
            old_decodes = decode.call_count
            decode.reset_mock()
            actual = optimized._primary_jobs(self.day)
            new_decodes = decode.call_count
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual[1]), 1008)
        self.assertEqual(old_decodes - new_decodes, 1101)
        self.assertIn(root + "_retry_1", {j["job_id"] for j in actual[1]})

    def test_family_scope_preserves_generic_lists_and_state_filters(self):
        self.insert("TEST_FIXTURE_a", root="a", state="FAILED")
        self.insert("TEST_FIXTURE_b", root="b")
        self.insert("TEST_FIXTURE_other", root="b", kind="TEST_FIXTURE_OTHER")
        jobs = self.jobs()
        all_rows = jobs.list(kind=extension.KIND)
        with jobs.family_scope("a"):
            self.assertEqual([r["job_id"] for r in jobs.list(kind=extension.KIND, state="FAILED")], ["TEST_FIXTURE_a"])
            self.assertEqual(jobs.list(kind=extension.KIND, state="SUCCEEDED"), [])
            self.assertEqual(len(jobs.list(kind="TEST_FIXTURE_OTHER")), 1)
            self.assertEqual(len(jobs.list()), 3)
            with jobs.family_scope("b"):
                self.assertEqual([r["job_id"] for r in jobs.list(kind=extension.KIND)], ["TEST_FIXTURE_b"])
            self.assertEqual([r["job_id"] for r in jobs.list(kind=extension.KIND)], ["TEST_FIXTURE_a"])
        self.assertEqual(jobs.list(kind=extension.KIND), all_rows)

    def test_scope_is_thread_local_and_resets_after_exception(self):
        import threading
        for root in ("a", "b"):
            self.insert("TEST_FIXTURE_" + root, root=root)
        jobs = self.jobs()
        barrier = threading.Barrier(2)
        def read(root):
            with jobs.family_scope(root):
                barrier.wait(timeout=10)
                return [row["job_id"] for row in jobs.list(kind=extension.KIND)]
        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(list(executor.map(read, ("a", "b"))), [["TEST_FIXTURE_a"], ["TEST_FIXTURE_b"]])
        with self.assertRaisesRegex(RuntimeError, "TEST_FIXTURE"):
            with jobs.family_scope("a"):
                raise RuntimeError("TEST_FIXTURE")
        self.assertEqual(len(jobs.list(kind=extension.KIND)), 2)

    def test_nontext_json_roots_cannot_match_their_text_representation(self):
        for index, value in enumerate(({}, [], None, True, 1)):
            self.insert("TEST_FIXTURE_nontext_" + str(index), root=value)
        self.insert("TEST_FIXTURE_text", root="{}")
        jobs = self.jobs()
        with jobs.family_scope("{}"):
            self.assertEqual([r["job_id"] for r in jobs.list(kind=extension.KIND)], ["TEST_FIXTURE_text"])
        for root in ("[]", "null", "true", "1"):
            with jobs.family_scope(root):
                self.assertEqual(jobs.list(kind=extension.KIND), [])

    def test_original_plan_called_once_and_reused_by_original_family_algorithm(self):
        jobs = self.jobs()
        scheduler = self.scheduler(jobs)
        original = CandidateScheduler._primary_schedule_for
        with patch.object(CandidateScheduler, "_primary_schedule_for", autospec=True, side_effect=original) as plan:
            self.assertEqual(scheduler._primary_jobs(self.day)[1], [])
            self.assertEqual(plan.call_count, 1)
        self.assertIsNone(extension._FROZEN_PLAN.get())

    def test_existing_retry_stop_and_delay_decisions_match_original(self):
        jobs = self.jobs()
        original = self.scheduler(jobs, CandidateScheduler)
        optimized = self.scheduler(jobs)
        root = original._primary_schedule_for(self.day)["job_id"]
        cases = [
            ("QUEUED", None, None), ("RUNNING", None, None), ("CANCELLED", None, None),
            ("SUCCEEDED", {"daily_primary_final_status": "READY"}, None),
            ("FAILED", None, {"type": "ValueError"}),
            ("FAILED", None, {"type": "TEST_FIXTURE_TRANSIENT"}),
            ("SUCCEEDED", {"daily_primary_final_status": "NOT_READY"}, None),
            ("INTERRUPTED", None, {"type": "TEST_FIXTURE_INTERRUPTED"}),
        ]
        self.insert(root, root=root, payload={"source": "TEST_FIXTURE", "root_job_id": root,
                    "scheduled_at": "2026-09-21T20:30:00+08:00", "market_session": "EVENING"})
        terminal = {"observed_at": "2026-09-21T20:31:00+08:00"}
        self.store.put_document("candidate_schedule_events", root + "_terminal", terminal)
        for state, result, error in cases:
            with self.subTest(state=state, result=result, error=error):
                with self.store.transaction() as connection:
                    connection.execute("UPDATE jobs SET state=?,result=?,error=? WHERE job_id=?",
                                       (state, canonical_json(result), canonical_json(error), root))
                outputs = []
                for scheduler in (original, optimized):
                    calls = []
                    def submit(kind, payload, *, job_id):
                        calls.append((kind, payload, job_id))
                        return {"job_id": job_id}
                    with patch.object(jobs, "submit", side_effect=submit), patch.object(scheduler, "_event"):
                        outputs.append((scheduler.schedule_primary_retries(self.clock()), calls))
                self.assertEqual(outputs[0], outputs[1])
                # The target deadline still prevents any later retry.
                self.assertEqual(optimized.schedule_primary_retries(self.clock() + timedelta(days=1)), [])

    def test_random_successful_retry_prevents_duplicate_automatic_attempt(self):
        jobs = self.jobs()
        scheduler = self.scheduler(jobs)
        root = scheduler._primary_schedule_for(self.day)["job_id"]
        self.insert(root, root=root, state="FAILED", error={"type": "TEST_FIXTURE_TRANSIENT"})
        self.insert("TEST_FIXTURE_random_success", root=root, result={"recommendation_published": True})
        with patch.object(jobs, "submit") as submit:
            self.assertEqual(scheduler.schedule_primary_retries(self.clock()), [])
            submit.assert_not_called()

    def test_explain_uses_family_search_without_temporary_sort(self):
        with self.store.transaction(write=False) as connection:
            original = [row["detail"] for row in connection.execute(
                "EXPLAIN QUERY PLAN SELECT * FROM jobs WHERE kind=? "
                "ORDER BY created_at DESC,job_id LIMIT ? OFFSET ?", (extension.KIND, 1000, 0))]
        self.assertTrue(any("SCAN jobs" in item for item in original))
        self.assertTrue(any("TEMP B-TREE" in item for item in original))
        extension.migrate(self.database)
        plan = [row["detail"] for row in extension.explain(self.database, "TEST_FIXTURE") ["query_plan"]]
        self.assertTrue(any("SEARCH jobs USING INDEX " + extension.INDEX in item for item in plan))
        self.assertFalse(any("TEMP B-TREE" in item for item in plan))

    def test_query_rechecks_index_guard_and_receipt_without_silent_fallback(self):
        jobs = self.jobs()
        with self.store.transaction() as connection:
            connection.execute(f"UPDATE {extension.TABLE} SET ddl_hash='TEST_FIXTURE_TAMPERED'")
        with jobs.family_scope("TEST_FIXTURE"):
            with self.assertRaisesRegex(IntegrityError, "RECEIPT_MISMATCH"):
                jobs.list(kind=extension.KIND)
        jobs.shutdown(cancel_pending=False)
        with self.assertRaisesRegex(IntegrityError, "RECEIPT_MISMATCH"):
            extension.migrate(self.database)

    def test_query_rechecks_index_and_guard_definitions(self):
        jobs = self.jobs()
        for name in (extension.INDEX, "codex_scheduler_root_update_v1"):
            kind, ddl = extension.OBJECTS[name]
            with self.subTest(object=name):
                with self.store.transaction() as connection:
                    connection.execute(f"DROP {kind.upper()} {name}")
                    replacement = (f"CREATE INDEX {name} ON jobs(kind)" if kind == "index" else
                                   f"CREATE TRIGGER {name} BEFORE UPDATE ON jobs BEGIN SELECT 1; END")
                    connection.execute(replacement)
                with jobs.family_scope("TEST_FIXTURE"):
                    with self.assertRaisesRegex(IntegrityError, "DDL_MISMATCH"):
                        jobs.list(kind=extension.KIND)
                with self.store.transaction() as connection:
                    connection.execute(f"DROP {kind.upper()} {name}")
                    connection.execute(ddl)

    def test_explicit_application_uses_adapter_without_starting_scheduler(self):
        extension.migrate(self.database)
        app, core = extension.build_application(self.database, self.state)
        try:
            self.assertIsInstance(core.jobs, extension.FamilyQueryJobService)
            self.assertIs(core.jobs, core.candidates.jobs)
            self.assertIsInstance(core.candidate_scheduler, extension.FamilyQueryScheduler)
            self.assertIsNone(core.candidate_scheduler.thread)
            self.assertEqual(app.test_client().get("/health/live").status_code, 200)
        finally:
            core.close()

    def test_application_refuses_missing_extension_and_running_owner(self):
        with self.assertRaisesRegex(IntegrityError, "MISSING_OR_PARTIAL"):
            extension.build_application(self.database, self.state)
        jobs = self.jobs()
        with self.assertRaisesRegex(StorageError, "another job worker"):
            extension.build_application(self.database, self.state)
        self.assertFalse(jobs._closed)

    def test_explicit_serve_dispatch_starts_once_and_always_closes(self):
        from unittest.mock import Mock
        core = Mock()
        with patch.object(extension, "build_application", return_value=("TEST_FIXTURE_APP", core)), \
                patch("waitress.serve", side_effect=RuntimeError("TEST_FIXTURE_STOP")) as serve:
            with self.assertRaisesRegex(RuntimeError, "TEST_FIXTURE_STOP"):
                extension.main(["serve", "--database", str(self.database), "--state", str(self.state), "--port", "12345"])
        core.candidate_scheduler.start.assert_called_once_with()
        core.close.assert_called_once_with()
        serve.assert_called_once_with("TEST_FIXTURE_APP", host="127.0.0.1", port=12345, threads=4)

    def test_cli_subprocess_migration_status_and_explain_are_runnable(self):
        script = Path(extension.__file__).resolve()
        for name, expected, extra in (("status", "NOT_INSTALLED", []), ("migrate", "INSTALLED", []),
                                      ("status", "INSTALLED", []), ("explain", None, ["--root-job-id", "TEST_FIXTURE"])):
            cache = sys._xoptions.get("pycache_prefix")
            command = [sys.executable, "-I", "-B", *(["-X", "pycache_prefix=" + cache] if cache else []),
                       str(script), name, "--database", str(self.database), *extra]
            process = subprocess.run(command, text=True, capture_output=True, timeout=60)
            self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
            value = json.loads(process.stdout)
            self.assertEqual(value["version"], extension.VERSION)
            if expected:
                self.assertEqual(value["status"], expected)
            else:
                self.assertTrue(value["query_plan"])


if __name__ == "__main__":
    unittest.main()
