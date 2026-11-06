"""TEST_FIXTURE gateway contracts; no real runtime, database, or provider calls."""
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import date
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts import codex_daily_v2 as daily
from scripts import codex_result_view_v1 as views
from scripts import codex_workspace as workspace


def saved_view():
    artifacts = {name: dict(path="/TEST_FIXTURE/" + name, sha256="d" * 64, bytes=100)
                 for name in views.EXPORT_ROLES}
    return dict(status="VERIFIED_SAVED_RESULT_VIEW", view_id="a" * 64, archive_id="b" * 64,
        archive_profile="FULL_CORE_ARCHIVE_READONLY_V1", local_seal_authenticated_now=True,
        all_five_exports_verified_now=True, source_reauthenticated_now=False,
        last_full_authenticated_at="2026-10-24T00:00:00+00:00", artifacts=artifacts,
        summary=dict(run_id="TEST_FIXTURE-run", input_hash="c" * 64, canonical_content_hash="e" * 64,
            observation_date="2026-10-23", target_session="2026-10-26", decision_cutoff="2026-10-23T21:00:00+08:00",
            strategy_id="SHORT_HORIZON_STRENGTH_R2", coverage_type="TEST_FIXTURE",
            counts=dict(FOCUS=0, WATCH=1, REJECTED=1, UNKNOWN=1)))


class SavedViewGatewayTests(unittest.TestCase):
    def setUp(self):
        self.runtime = SimpleNamespace(profile=dict(backend="premarket", storage=dict(
            max_managed_bytes=1024**3, min_free_bytes=1024, expected_growth_bytes=4096)))
        self.value = saved_view()
        self.reader = SimpleNamespace(read_view=Mock(return_value=self.value),
            list_views=Mock(return_value=dict(views=[dict(view_id=self.value["view_id"],
                observation_date="2026-10-23", target_session="2026-10-26",
                decision_cutoff="2026-10-23T21:00:00+08:00")])) )
        self.capacity = dict(inventory=dict(scan_complete=True, totals=dict(unique_logical_bytes=100),
            roots=[dict(name="data", filesystem=dict(free_bytes=1024**3))]), destinations=[dict(name="data")])

    def test_default_selection_uses_flat_signed_locator_fields(self):
        self.reader.list_views.return_value = dict(views=[
            dict(view_id="a" * 64, observation_date="2026-10-22", decision_cutoff="2026-10-22T23:00:00+08:00"),
            dict(view_id="b" * 64, observation_date="2026-10-23", decision_cutoff="2026-10-23T20:00:00+08:00"),
            dict(view_id="c" * 64, observation_date="2026-10-23", decision_cutoff="2026-10-23T21:00:00+08:00")])
        with patch.object(daily, "_views", return_value=self.reader):
            self.assertEqual(daily._select_view(self.runtime, None), "c" * 64)
            self.assertEqual(daily._select_view(self.runtime, "explicit-view"), "explicit-view")
        self.assertEqual(self.reader.list_views.call_count, 1)

    def test_no_saved_view_stays_missing_and_does_not_compute(self):
        self.reader.list_views.return_value = dict(views=[])
        with patch.object(daily, "_views", return_value=self.reader):
            with self.assertRaisesRegex(ValueError, "NO_SEALED_RESULT"):
                daily._select_view(self.runtime, None)
        self.reader.read_view.assert_not_called()

    def test_requested_session_selects_matching_older_view_before_read(self):
        self.reader.list_views.return_value["views"].append(dict(view_id="f" * 64,
            observation_date="2026-11-05", target_session="2026-11-06",
            decision_cutoff="2026-11-05T21:00:00+08:00"))
        args = daily.parser().parse_args(["show", "--observation-date", "2026-10-23",
            "--target-session", "2026-10-26"])
        with patch.object(daily, "_views", return_value=self.reader):
            code, result = daily.execute(self.runtime, args)
        self.assertEqual(code, 0)
        self.assertIs(result, self.value)
        self.assertEqual(self.reader.read_view.call_args.args, (self.runtime, self.value["view_id"]))
        self.assertEqual(self.reader.read_view.call_args.kwargs["observation_date"], "2026-10-23")
        self.assertEqual(self.reader.read_view.call_args.kwargs["target_session"], "2026-10-26")

    def test_requested_session_missing_never_reads_or_computes_old_result(self):
        for arguments in (
            ["--observation-date", "2026-11-05"],
            ["--target-session", "2026-11-06"],
            ["--observation-date", "2026-10-23", "--target-session", "2026-11-06"],
        ):
            with self.subTest(arguments=arguments), patch.object(daily, "_views", return_value=self.reader):
                args = daily.parser().parse_args(["show", *arguments])
                with self.assertRaisesRegex(ValueError, "DAILY_SEALED_RESULT_NOT_FOUND_FOR_REQUESTED_SESSION"):
                    daily.execute(self.runtime, args)
        self.reader.read_view.assert_not_called()

    def test_show_passes_explicit_dates_and_preserves_formal_role(self):
        args = daily.parser().parse_args(["show", "--view-id", self.value["view_id"],
            "--role", "all.csv", "--observation-date", "2026-10-23", "--target-session", "2026-10-26"])
        with patch.object(daily, "_views", return_value=self.reader):
            code, result = daily.execute(self.runtime, args)
        self.assertEqual(code, 0)
        self.assertIs(result, self.value)
        self.reader.read_view.assert_called_once_with(self.runtime, self.value["view_id"], role="all.csv", allow_test_fixture=False,
            observation_date="2026-10-23", target_session="2026-10-26")


class SavedViewCliFixtureTests(unittest.TestCase):
    def setUp(self):
        from tests.unit.test_codex_result_view_v1 import SavedViewFixture
        self.fixture = SavedViewFixture(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.published, _ = self.fixture.publish()

    def call_cli(self, observation, target):
        before = {str(p): p.read_bytes() for p in self.fixture.root.rglob("*") if p.is_file()}
        result = subprocess.run([sys.executable, "-I", "-B", str(daily.ROOT / "scripts/codex_daily_v2.py"),
            "--registry-root", str(self.fixture.runtime.registry_root), "--runtime", self.fixture.runtime.profile["name"],
            "--allow-test-fixture", "show", "--observation-date", observation, "--target-session", target],
            cwd=daily.ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.stderr, "")
        after = {str(p): p.read_bytes() for p in self.fixture.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        return result.returncode, json.loads(result.stdout)

    def test_actual_cli_requested_session_reads_existing_seal(self):
        code, result = self.call_cli("2026-10-23", "2026-10-26")
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "VERIFIED_SAVED_RESULT_VIEW")
        self.assertEqual(result["view_id"], self.published["view_id"])
        self.assertEqual(result["summary"], self.fixture.summary)
        self.assertFalse(result["source_reauthenticated_now"])

    def test_actual_cli_missing_session_is_explicit_and_read_only(self):
        code, result = self.call_cli("2026-11-05", "2026-11-06")
        self.assertEqual(code, 2)
        self.assertEqual(result["status"], "BLOCKED_OR_FAILED")
        self.assertEqual(result["reason"], "DAILY_SEALED_RESULT_NOT_FOUND_FOR_REQUESTED_SESSION")
        self.assertNotIn("summary", result)
