"""TEST_FIXTURE-only doctor contracts and guarded, isolated import probes."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/runtime_doctor.py"
spec = importlib.util.spec_from_file_location("runtime_doctor_TEST_FIXTURE", SCRIPT)
doctor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(doctor)


class DoctorFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="runtime-doctor-TEST_FIXTURE-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "stock-fish"
        self.root.mkdir()
        self.live = self.root.parent / "stockfish-live"
        self.live.mkdir()
        self.write("requirements-rules.txt", "pydantic==2.13.5\n")
        self.write("requirements-daily.txt", "-r requirements-rules.txt\nwaitress==3.0.2\n")
        self.write("requirements-qlib.txt", "pyqlib==0.9.7\n")
        self.write("requirements-review.txt", "pytest==9.1.1\npsutil==7.2.2\n")
        for path in (*doctor.CORE_SOURCE, *doctor.CORE_CONFIG):
            self.write(path, "# TEST_FIXTURE: must only be read\n")
        self.write("stockfish_core/storage.py", "_SCHEMA = 'CREATE TABLE TEST_FIXTURE(id INTEGER);'\nraise RuntimeError('MUST_NOT_IMPORT')\n")
        for path in doctor.LIVE_SOURCE:
            self.write(path, "# TEST_FIXTURE\n", self.live)
        self.write("pyproject.toml", '[project]\nname="TEST_FIXTURE-live"\nrequires-python=">=3.12,<3.14"\ndependencies=["pydantic>=2.10,<3", "duckdb>=1.2,<2"]\n', self.live)
        self.calls = []

    def write(self, relative, content, root=None):
        path = (root or self.root) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def fake_probe(self, executable, request, timeout):
        self.calls.append(request)
        result = {"ok": True, "guard_violations": [], "runtime": {
            "executable": executable, "prefix": "TEST_FIXTURE", "base_prefix": "TEST_FIXTURE-base",
            "python_version": "3.12.1", "machine": "TEST_FIXTURE", "pointer_bits": 64}}
        if request["action"] != "identity":
            versions = {"pydantic": "2.13.5", "waitress": "3.0.2", "pyqlib": "0.9.7",
                        "pytest": "9.1.1", "psutil": "7.2.2", "duckdb": "1.5.6"}
            result.update(distribution={"version": versions[request["distribution"]], "editable": False,
                "wheel_metadata_present": True, "record_present": True},
                modules=[{"name": name, "origin": "TEST_FIXTURE/"+name} for name in request["modules"]],
                qlib_origin_matches_distribution=True)
        return result

    def inspect(self, profile="rules", runner=None, **kwargs):
        return doctor.inspect_runtime(self.root, profile, "TEST_FIXTURE-python", probe_runner=runner or self.fake_probe, **kwargs)

    def test_rules_uses_pins_real_import_contract_and_no_qlib(self):
        result = self.inspect()
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual([x["distribution"] for x in result["packages"]], ["pydantic"])
        self.assertIn("pydantic_core._pydantic_core", self.calls[1]["modules"])
        self.assertEqual(result["python_requirement"], ">=3.12,<3.13")
        self.assertFalse(result["database_opened"])
        self.assertFalse(result["application_imported"])
        self.assertIsNotNone(result["source_identity"]["schema"]["sha256"])

    def test_daily_recursive_requirements_and_missing_declared_psutil_warning(self):
        result = self.inspect("daily")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual([x["distribution"] for x in result["packages"]], ["psutil", "pydantic", "waitress"])
        self.assertEqual(len(result["requirement_files"]), 2)
        self.assertEqual(result["warnings"][0]["distribution"], "psutil")

    def test_review_checks_its_tools_without_qlib(self):
        result = self.inspect("review")
        self.assertEqual([x["distribution"] for x in result["packages"]], ["psutil", "pydantic", "pytest"])

    @unittest.skipUnless(sys.version_info >= (3, 11), "Live TOML reader requires standard-library tomllib")
    def test_live_uses_project_ranges_and_no_business_schema(self):
        result = self.inspect("live")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["python_requirement"], ">=3.12,<3.14")
        self.assertIsNone(result["source_identity"]["schema"])
        self.assertEqual(self.calls[1]["modules"], ("duckdb", "_duckdb"))

    def test_qlib_requires_matching_wheel_origin_version_and_noneditable(self):
        self.assertEqual(self.inspect("qlib")["status"], "PASS")
        for key, value in (("editable", True), ("wheel_metadata_present", False),
                           ("record_present", False), ("version", "0.9.8"), ("origin", False)):
            def fake(executable, request, timeout):
                result = self.fake_probe(executable, request, timeout)
                if request.get("distribution") == "pyqlib":
                    if key == "origin":
                        result["qlib_origin_matches_distribution"] = value
                    else:
                        result["distribution"][key] = value
                return result
            with self.subTest(key=key):
                report = self.inspect("qlib", fake)
                row = next(row for row in report["packages"] if row["distribution"] == "pyqlib")
                self.assertIn("PYQLIB_097_WHEEL_IDENTITY_REQUIRED", row["reason_codes"])
                self.assertEqual(report["exit_code"], 1)

    def test_metadata_success_cannot_hide_native_import_failure(self):
        def fake(executable, request, timeout):
            result = self.fake_probe(executable, request, timeout)
            if request["action"] == "import":
                result.update(ok=False, error_type="ModuleNotFoundError")
            return result
        report = self.inspect(runner=fake)
        self.assertEqual(report["packages"][0]["probe"]["distribution"]["version"], "2.13.5")
        self.assertEqual(report["exit_code"], 1)
        self.assertIn("ACTUAL_IMPORT_OR_METADATA_FAILED", report["packages"][0]["reason_codes"])

    def test_wrong_python_and_changed_probe_identity_fail(self):
        def wrong(executable, request, timeout):
            result = self.fake_probe(executable, request, timeout)
            result["runtime"]["python_version"] = "3.10.6"
            return result
        self.assertIn({"code": "PYTHON_VERSION_NOT_SATISFIED"}, self.inspect(runner=wrong)["issues"])
        def changed(executable, request, timeout):
            result = self.fake_probe(executable, request, timeout)
            if request["action"] != "identity":
                result["runtime"]["prefix"] = "OTHER_TEST_FIXTURE"
            return result
        self.assertIn("RUNTIME_IDENTITY_CHANGED_BETWEEN_PROBES", self.inspect(runner=changed)["packages"][0]["reason_codes"])

    def test_requirement_include_cycle_escape_and_conflicts_fail_closed(self):
        for text in ("-r requirements-rules.txt\n", "-r ../outside.txt\n", "pydantic==2.13.5\npydantic==2.10.0\n"):
            self.write("requirements-rules.txt", text)
            self.calls.clear()
            with self.subTest(text=text):
                report = self.inspect()
                self.assertEqual(report["exit_code"], 1)
                self.assertEqual(self.calls, [])

    def test_source_and_configuration_identity_changes_are_visible(self):
        before = self.inspect()["source_identity"]
        self.write("config/short_horizon_strength_r2.yaml", "# TEST_FIXTURE altered\n")
        after = self.inspect()["source_identity"]
        self.assertNotEqual(before["summary_hash"], after["summary_hash"])
        self.assertEqual(before["schema"], after["schema"])

    def test_missing_schema_or_dynamic_schema_is_not_executed(self):
        self.write("stockfish_core/storage.py", "_SCHEMA = __import__('os').environ['TEST_FIXTURE_SECRET']")
        self.assertEqual(self.inspect()["exit_code"], 1)
        self.assertEqual(self.calls, [])

    def test_locator_is_explicit_single_path_relative_to_its_directory(self):
        link = self.root / "TEST_FIXTURE-python"
        link.symlink_to(sys.executable)
        locator = self.write("TEST_FIXTURE.locator", "TEST_FIXTURE-python\n")
        self.assertEqual(doctor.resolve_python(locator=locator), str(link))
        self.write("TEST_FIXTURE.locator", "a\nb\n")
        with self.assertRaises(ValueError):
            doctor.resolve_python(locator=locator)

    def test_locator_and_source_cannot_read_env_or_database_through_alias(self):
        for name in (".env", ".env.private", "private.sqlite"):
            secret = self.write(name, "TEST_FIXTURE_SECRET")
            alias = self.root / "TEST_FIXTURE_alias"
            alias.symlink_to(secret)
            with self.subTest(name=name), self.assertRaises(ValueError):
                doctor.resolve_python(locator=alias)
            alias.unlink()
        path = self.root / "requirements-rules.txt"
        path.unlink()
        path.symlink_to(self.root / ".env")
        self.assertEqual(self.inspect()["exit_code"], 1)

    def test_exhausted_budget_does_not_spawn_more_probes(self):
        with patch.object(doctor.time, "monotonic", side_effect=[0., 2., 2., 2.]):
            report = self.inspect(budget_seconds=1.)
        self.assertEqual(self.calls, [])
        self.assertEqual(report["runtime_probe"]["error_type"], "TOTAL_PROBE_BUDGET_EXHAUSTED")
        self.assertEqual(report["status"], "FAIL")

    def test_cli_help_does_not_probe_or_inspect_sources(self):
        with patch.object(doctor, "inspect_runtime", side_effect=AssertionError("must not inspect")), \
             patch.object(doctor, "resolve_python", side_effect=AssertionError("must not resolve")), \
             contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as error:
            doctor.main(["--help"])
        self.assertEqual(error.exception.code, 0)

    def test_invalid_locator_reports_structured_failure_without_secret(self):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = doctor.main(["--python-locator", str(self.root / "does-not-exist"), "--json"])
        report = json.loads(stream.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(report["issues"][0]["code"], "INTERPRETER_SELECTION_FAILED")

    def test_numeric_version_ranges_and_unsupported_versions(self):
        for version, spec, expected in (("3.12.1", ">=3.12,<3.14", True), ("3.14.0", ">=3.12,<3.14", False),
                                        ("2.13.5", "==2.13.5", True), ("2.13.5rc1", ">=2.10,<3", False),
                                        (None, "", False), ("1.2", "==1.2.0", True)):
            with self.subTest(version=version, spec=spec):
                self.assertEqual(doctor.version_satisfies(version, spec), expected)


class RealGuardFixture(unittest.TestCase):
    """Import only tiny synthetic packages; never load the project/application."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="runtime-doctor-import-TEST_FIXTURE-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        dist = self.root / "test_fixture_package-1.0.dist-info"
        dist.mkdir()
        (dist / "METADATA").write_text("Name: test-fixture-package\nVersion: 1.0\n", encoding="utf-8")
        self.request = {"action": "import", "distribution": "test-fixture-package", "modules": ["test_fixture_package"]}

    def execute(self, module, *, timeout=5., site_prefix=False):
        (self.root / "test_fixture_package.py").write_text(module, encoding="utf-8")
        injection = "sys.path.insert(0, " + repr(str(self.root)) + ")"
        replacement = ("site.addsitedir(" + repr(str(self.root)) + ")\n    site.main()") if site_prefix else ("site.main()\n    " + injection)
        source = doctor.PROBE.replace("    site.main()", "    " + replacement, 1)
        with patch.object(doctor, "PROBE", source):
            return doctor.probe(sys.executable, self.request, timeout)

    def test_actual_import_returns_origin_and_leaves_no_bytecode(self):
        before = set(self.root.rglob("*"))
        result = self.execute("VALUE = 'TEST_FIXTURE'\n")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["distribution"]["version"], "1.0")
        self.assertEqual(result["modules"][0]["origin"], str(self.root / "test_fixture_package.py"))
        self.assertFalse(list(self.root.rglob("__pycache__")))
        self.assertEqual(set(self.root.rglob("*"))-before, {self.root / "test_fixture_package.py"})

    def test_native_import_failure_has_metadata_but_never_passes(self):
        result = self.execute("import TEST_FIXTURE_missing_native_extension\n")
        self.assertFalse(result["ok"])
        self.assertEqual(result["distribution"]["version"], "1.0")
        self.assertEqual(result["error_type"], "ModuleNotFoundError")

    def test_import_stdout_and_exception_messages_do_not_leak(self):
        result = self.execute("print('TEST_FIXTURE_SECRET')\nraise RuntimeError('TEST_FIXTURE_SECRET')\n")
        self.assertFalse(result["ok"])
        self.assertNotIn("TEST_FIXTURE_SECRET", json.dumps(result))

    def test_filesystem_write_is_blocked_even_if_package_catches_exception(self):
        target = self.root / "TEST_FIXTURE_forbidden"
        result = self.execute("try:\n    open("+repr(str(target))+", 'w').write('bad')\nexcept Exception:\n    pass\n")
        self.assertFalse(target.exists())
        self.assertFalse(result["ok"])
        self.assertIn("FILESYSTEM_WRITE_DENIED", result["guard_violations"])

    def test_env_database_and_network_are_denied(self):
        env = self.root / ".env"
        env.write_text("TEST_FIXTURE_SECRET", encoding="utf-8")
        cases = (("open("+repr(str(env))+").read()", "ENV_FILE_READ_DENIED"),
                 ("import sqlite3\nsqlite3.connect(':memory:')", "NETWORK_DATABASE_OR_PROCESS_DENIED"),
                 ("import socket\nsocket.getaddrinfo('TEST_FIXTURE.invalid', 80)", "NETWORK_DATABASE_OR_PROCESS_DENIED"))
        for source, code in cases:
            with self.subTest(code=code):
                result = self.execute(source)
                self.assertFalse(result["ok"])
                self.assertIn(code, result["guard_violations"])

    def test_caught_socket_capability_probe_is_reported_without_creating_socket(self):
        result = self.execute("import socket\ntry:\n    socket.socket()\nexcept OSError:\n    pass\n")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["blocked_capability_probes"], ["SOCKET_CREATION_BLOCKED"])
        result = self.execute("import socket\nsocket.socket()\n")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "PermissionError")
        self.assertEqual(result["blocked_capability_probes"], ["SOCKET_CREATION_BLOCKED"])

    def test_pth_side_effect_is_guarded_before_site_initialization(self):
        target = self.root / "TEST_FIXTURE_pth_write"
        (self.root / "TEST_FIXTURE.pth").write_text("import builtins; builtins.open("+repr(str(target))+", 'w')\n", encoding="utf-8")
        result = self.execute("VALUE=1\n", site_prefix=True)
        self.assertFalse(target.exists())
        self.assertFalse(result["ok"])
        self.assertIn("FILESYSTEM_WRITE_DENIED", result["guard_violations"])

    def test_import_timeout_is_reported(self):
        result = self.execute("import time\ntime.sleep(2)\n", timeout=.1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "PROBE_TIMEOUT")

    def test_missing_executable_and_invalid_fake_interpreter_output_fail(self):
        result = doctor.probe(str(self.root / "absent"), {"action": "identity"}, 1.)
        self.assertFalse(result["ok"])
        with patch.object(doctor.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, '{"ok":true}', '')):
            self.assertEqual(doctor.probe("TEST_FIXTURE", {"action":"identity"}, 1.)["error_type"], "PROBE_UNAVAILABLE_OR_INVALID_OUTPUT")


if __name__ == "__main__":
    unittest.main()
