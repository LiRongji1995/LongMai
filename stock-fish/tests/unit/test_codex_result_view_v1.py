"""TEST_FIXTURE local-seal security and original full-archive integration checks."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import codex_result_view_v1 as view
from scripts import codex_core_archive as archive_v1
from scripts import codex_core_archive_v2 as archive_v2
from scripts import codex_workspace as workspace


class SavedViewFixture(unittest.TestCase):
    """Small storage fixture; its authenticator stub is only a test mock."""
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="TEST_FIXTURE-result-view-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.report, self.state = self.root / "report", self.root / "state"
        self.report.mkdir(); self.state.mkdir()
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps(dict(state_root=str(self.state))))
        profile = dict(schema_version=workspace.PROFILE_VERSION, name="TEST_FIXTURE-result-view", backend="premarket",
            state_root=str(self.state), report_root=str(self.report), source_root=str(view.ROOT),
            python=sys.executable, config=str(self.config), storage=dict(
                roots=[dict(name="fixture", path=str(self.root), role="runtime")], target_root="fixture",
                min_free_bytes=0, max_managed_bytes=64 * view.MAX_CONTROL, expected_growth_bytes=1024))
        path = self.root / "profile.json"
        path.write_text(json.dumps(profile))
        workspace.register_profile(path, self.root / "registry")
        self.runtime = workspace.load_runtime(profile["name"], self.root / "registry")
        self.archive_id = "a" * 64
        self.profile = archive_v1.PROFILE
        self.summary = dict(run_id="TEST_FIXTURE-run", input_hash="b" * 64, canonical_content_hash="c" * 64,
            counts=dict(FOCUS=1, WATCH=2, REJECTED=1, UNKNOWN=1), observation_date="2026-10-23",
            target_session="2026-10-26", decision_cutoff="2026-10-23T21:00:00+08:00",
            normalization_gap_count=1, strategy_id=archive_v1.STRATEGY, coverage_type="TEST_FIXTURE",
            parameter_status="UNVALIDATED", market_edge="NOT_ESTABLISHED", DAILY_V1="UNKNOWN_NOT_EXECUTED",
            full_premarket_ready=False, source_authority_restored=False,
            result_role="AUTHENTICATED_ARCHIVED_CORE_RESEARCH")
        core = self.report / "bootstrap" / "core-context-TEST_FIXTURE" / "core"
        core.mkdir(parents=True)
        self.artifacts = {}
        for name in sorted(view.CORE_ROLES):
            data = json.dumps(dict(TEST_FIXTURE=True, summary=self.summary)).encode() if name.endswith(".json") else b"TEST_FIXTURE,UNKNOWN\n600000,UNKNOWN\n"
            artifact = core / name
            artifact.write_bytes(data)
            self.artifacts[name] = view._read_file(artifact, maximum=view.MAX_EXPORT)[1]
        self.original = self.root / "TEST_FIXTURE-original.raw"
        self.original.write_bytes(b"TEST_FIXTURE_RAW_SOURCE")
        original_ref = view._read_file(self.original, maximum=100)[1]
        self.identity = dict(schema_version=archive_v1.VERSION, profile=archive_v1.PROFILE, allow_test_fixture=True)
        evidence = dict(schema_version=archive_v1.VERSION, identity=self.identity, summary=self.summary,
                        artifacts=self.artifacts, references=[original_ref, *self.artifacts.values()])
        self.evidence_path = self.report / "core-archives" / (self.archive_id + ".json")
        self.evidence_path.parent.mkdir()
        self.evidence_path.write_text(json.dumps(evidence))
        evidence_ref = view._read_file(self.evidence_path, maximum=view.MAX_CONTROL)[1]
        record = dict(schema_version=workspace.VERSION, phase="RECORDED", operation_id=self.archive_id,
            scope=self.runtime.scope, identity=self.identity, receipt=dict(evidence=evidence_ref))
        operation = self.runtime.directory / "operations" / (self.archive_id + ".json")
        operation.parent.mkdir(parents=True)
        operation.write_text(json.dumps(record))
        self.authenticated = dict(status="REUSED", archive_id=self.archive_id, summary=self.summary,
            artifacts=self.artifacts, source_authority_restored=False, validation=dict(verified=True),
            metrics=dict(TEST_FIXTURE=True))

    def publish(self, **kwargs):
        with patch.object(archive_v1, "reuse_archive", return_value=deepcopy(self.authenticated)) as authentic:
            result = view.publish_view(self.runtime, self.archive_id, profile=self.profile,
                                      allow_test_fixture=True, **kwargs)
        return result, authentic.call_count

    def snapshot(self, result):
        return self.report / "result-views-v1" / (result["view_id"] + ".json")

    def read(self, result, **kwargs):
        return view.read_view(self.runtime, result["view_id"], allow_test_fixture=True, **kwargs)

    def test_first_publication_and_repeated_publish_read_preserve_bytes(self):
        first, calls = self.publish()
        self.assertEqual(calls, 1)
        self.assertTrue(first["published_now"])
        self.assertTrue(first["full_authentication_performed"])
        self.assertFalse(first["source_reauthenticated_now"])
        self.assertEqual(first["summary"], self.summary)
        snapshot = self.snapshot(first)
        before = view._read_file(snapshot, maximum=view.MAX_CONTROL)[1]
        second, calls = self.publish()
        self.assertEqual(calls, 0)
        self.assertTrue(second["reused"])
        self.assertEqual(first["last_full_authenticated_at"], second["last_full_authenticated_at"])
        self.assertEqual(view._read_file(snapshot, maximum=view.MAX_CONTROL)[1], before)
        self.assertEqual(len(list(snapshot.parent.glob("*.json"))), 1)
        self.assertLess(snapshot.stat().st_size, view.MAX_CONTROL)
        self.assertEqual(set(first["artifacts"]), view.EXPORT_ROLES)
        self.assertEqual(first["new_large_artifact_bytes"], 0)

    def test_read_uses_only_seal_private_key_and_all_five_exports(self):
        first, _ = self.publish()
        # A deleted RAW and input do not become valid sources; the saved result
        # remains readable with an explicit non-reauthentication disclosure.
        self.original.unlink()
        Path(self.artifacts["input.json.gz"]["path"]).unlink()
        with patch.object(archive_v1, "reuse_archive", side_effect=AssertionError("NO_SOURCE_AUTH")), \
             patch.object(archive_v2, "reuse_archive", side_effect=AssertionError("NO_SOURCE_AUTH")), \
             patch.object(view, "_read_file", wraps=view._read_file) as reader:
            result = self.read(first, role="observation.json")
        names = [Path(call.args[0]).name for call in reader.call_args_list]
        self.assertEqual(set(names), {first["view_id"] + ".json", "hmac.key", *view.EXPORT_ROLES})
        self.assertEqual(result["content"], Path(self.artifacts["observation.json"]["path"]).read_text())
        self.assertFalse(result["source_reauthenticated_now"])
        self.assertFalse(result["source_authority_restored"])
        self.assertFalse(result["currentness_recomputed"])
        self.assertTrue(result["all_five_exports_verified_now"])

    def test_edit_and_public_rehash_cannot_change_saved_counts_or_export_set(self):
        first, _ = self.publish()
        path = self.snapshot(first)
        envelope = json.loads(path.read_text())
        envelope["payload"]["summary"]["counts"]["FOCUS"] = 999
        envelope["payload"]["source_proof"]["authenticated_summary_sha256"] = view._sha(envelope["payload"]["summary"])
        envelope["hmac_sha256"] = hashlib.sha256(view._canonical(envelope["payload"])).hexdigest()
        path.write_text(json.dumps(envelope))
        with self.assertRaisesRegex(view.ResultViewError, "LOCAL_SIGNATURE_INVALID"):
            self.read(first)

    def test_any_export_tamper_rejects_even_summary_read(self):
        first, _ = self.publish()
        Path(self.artifacts["focus.csv"]["path"]).write_bytes(b"TEST_FIXTURE_FORGED_FOCUS\n")
        with self.assertRaisesRegex(view.ResultViewError, "PHYSICAL_IDENTITY_CHANGED"):
            self.read(first)

    def test_same_bytes_replacement_is_not_original_export(self):
        first, _ = self.publish()
        path = Path(self.artifacts["all.csv"]["path"])
        raw = path.read_bytes()
        path.unlink(); path.write_bytes(raw)
        with self.assertRaisesRegex(view.ResultViewError, "PHYSICAL_IDENTITY_CHANGED"):
            self.read(first)

    def test_key_is_private_and_missing_key_never_regenerated_for_existing_view(self):
        first, _ = self.publish()
        directory = self.report / ".result-view-keys-v1"
        path = directory / "hmac.key"
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(path.stat().st_size, 32)
        path.unlink()
        with self.assertRaises((FileNotFoundError, view.ResultViewError)):
            self.read(first)
        with self.assertRaises((FileNotFoundError, view.ResultViewError)):
            self.publish()
        self.assertFalse(path.exists())

    def test_relaxed_key_permissions_and_replaced_key_fail_closed(self):
        first, _ = self.publish()
        path = self.report / ".result-view-keys-v1" / "hmac.key"
        path.chmod(0o644)
        with self.assertRaisesRegex(view.ResultViewError, "PRIVATE_FILE_PERMISSIONS"):
            self.read(first)
        path.chmod(0o600)
        path.write_bytes(b"X" * 32)
        with self.assertRaisesRegex(view.ResultViewError, "SIGNING_KEY_CHANGED"):
            self.read(first)

    def test_export_symlink_and_hardlink_refused(self):
        first, _ = self.publish()
        path = Path(self.artifacts["focus.csv"]["path"])
        other = self.root / "TEST_FIXTURE-alias.csv"
        other.write_bytes(path.read_bytes())
        path.unlink(); path.symlink_to(other)
        with self.assertRaisesRegex(view.ResultViewError, "REGULAR_SINGLE_LINK"):
            self.read(first)
        path.unlink(); os.link(other, path)
        with self.assertRaisesRegex(view.ResultViewError, "REGULAR_SINGLE_LINK"):
            self.read(first)

    def test_parent_alias_and_unsafe_store_permissions_refused(self):
        directory = self.report / "result-views-v1"
        other = self.root / "TEST_FIXTURE-alias-directory"
        other.mkdir(mode=0o700)
        directory.symlink_to(other, target_is_directory=True)
        with self.assertRaises((OSError, ValueError)):
            self.publish()
        directory.unlink(); directory.mkdir(mode=0o755)
        with self.assertRaisesRegex(view.ResultViewError, "PRIVATE_DIRECTORY_PERMISSIONS"):
            self.publish()

    def test_cloud_placeholder_is_rejected_before_opening_payload(self):
        first, _ = self.publish()
        original = view._regular
        def reject(value, **kwargs):
            attrs = {name: getattr(value, name) for name in ("st_mode", "st_nlink", "st_uid")}
            attrs["st_flags"] = 0x40000000
            from types import SimpleNamespace
            return original(SimpleNamespace(**attrs), **kwargs)
        with patch.object(view, "_regular", side_effect=reject), patch.object(os, "read", side_effect=AssertionError("NO_PAYLOAD_READ")):
            with self.assertRaisesRegex(view.ResultViewError, "CLOUD_PLACEHOLDER"):
                self.read(first)

    def test_query_context_fixture_and_role_are_explicit(self):
        first, _ = self.publish()
        for kwargs in (dict(observation_date="2026-10-24"), dict(target_session="2026-10-27"),
                       dict(cutoff="2026-10-24T21:00:00+08:00")):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(view.ResultViewError, "EXACT_QUERY_CONTEXT"):
                self.read(first, **kwargs)
        with self.assertRaisesRegex(view.ResultViewError, "RUNTIME_OR_IDENTITY"):
            view.read_view(self.runtime, first["view_id"])
        with self.assertRaisesRegex(view.ResultViewError, "UNKNOWN_EXPORT_ROLE"):
            self.read(first, role="input.json.gz")
        with self.assertRaises(TypeError):
            view.publish_view(self.runtime, self.archive_id, profile=self.profile,
                              allow_test_fixture=True, validator=lambda: self.authenticated)

    def test_incomplete_authentication_or_mutation_during_auth_never_seals(self):
        bad = deepcopy(self.authenticated)
        bad["validation"]["verified"] = False
        with patch.object(archive_v1, "reuse_archive", return_value=bad):
            with self.assertRaisesRegex(view.ResultViewError, "FULL_ARCHIVE_AUTHENTICATION"):
                view.publish_view(self.runtime, self.archive_id, profile=self.profile, allow_test_fixture=True)
        def mutate(*args, **kwargs):
            self.original.write_bytes(b"TEST_FIXTURE_MUTATED_DURING_AUTH")
            return deepcopy(self.authenticated)
        with patch.object(archive_v1, "reuse_archive", side_effect=mutate):
            with self.assertRaisesRegex(view.ResultViewError, "PHYSICAL_IDENTITY_CHANGED"):
                view.publish_view(self.runtime, self.archive_id, profile=self.profile, allow_test_fixture=True)
        self.assertEqual(list((self.report / "result-views-v1").glob("*.json")), [])

    def test_capacity_blocked_before_auth_and_control_size_is_bounded(self):
        with patch.object(self.runtime, "preflight", return_value=dict(allowed=False)), \
             patch.object(archive_v1, "reuse_archive", side_effect=AssertionError("NO_AUTH")):
            with self.assertRaisesRegex(view.ResultViewError, "METADATA_CAPACITY_BLOCKED"):
                view.publish_view(self.runtime, self.archive_id, profile=self.profile, allow_test_fixture=True)
        self.assertFalse((self.report / "result-views-v1").exists())
        with self.assertRaisesRegex(view.ResultViewError, "CONTROL_SIZE_LIMIT"):
            view._write_new(self.root / "oversize.json", b"x" * (view.MAX_CONTROL + 1))

    def test_repeated_publish_remains_readable_when_new_growth_would_be_blocked(self):
        first, _ = self.publish()
        with patch.object(self.runtime, "preflight", side_effect=AssertionError("NO_NEW_GROWTH")):
            repeated, calls = self.publish()
        self.assertEqual(calls, 0)
        self.assertEqual(first["last_full_authenticated_at"], repeated["last_full_authenticated_at"])
        self.assertFalse(repeated["published_now"])

    def test_publish_respects_existing_runtime_lock(self):
        with self.runtime.exclusive():
            with self.assertRaisesRegex(workspace.WorkspaceError, "RUNTIME_BUSY"):
                self.publish()

    def test_hash_validation_survives_matching_metadata_check_test_double(self):
        first, _ = self.publish()
        path = Path(self.artifacts["focus.csv"]["path"])
        path.write_bytes(b"X" * path.stat().st_size)
        original_fp = view._fp
        expected = self.artifacts["focus.csv"]["fp"]
        inode = path.stat().st_ino
        def fake_fp(value):
            return expected if value.st_ino == inode else original_fp(value)
        with patch.object(view, "_fp", side_effect=fake_fp):
            with self.assertRaisesRegex(view.ResultViewError, "ORIGINAL_HASH_CHANGED"):
                self.read(first)

    def test_list_is_signed_bounded_and_does_not_claim_exports_revalidated(self):
        self.assertEqual(view.list_views(self.runtime, allow_test_fixture=True)["views"], [])
        first, _ = self.publish()
        result = view.list_views(self.runtime, allow_test_fixture=True)
        self.assertEqual(result["views"][0]["view_id"], first["view_id"])
        self.assertFalse(result["exports_revalidated_now"])
        self.assertFalse(result["source_reauthenticated_now"])
        with self.assertRaisesRegex(view.ResultViewError, "LIST_LIMIT"):
            view.list_views(self.runtime, limit=0, allow_test_fixture=True)

    def test_duplicate_json_and_unknown_artifact_roles_are_rejected(self):
        with self.assertRaisesRegex(view.ResultViewError, "DUPLICATE_JSON_KEY"):
            view._json(b'{"TEST_FIXTURE":1,"TEST_FIXTURE":2}')
        first, _ = self.publish()
        path = self.snapshot(first)
        envelope = json.loads(path.read_text())
        envelope["payload"]["artifacts"].pop("focus.csv")
        path.write_text(json.dumps(envelope))
        with self.assertRaisesRegex(view.ResultViewError, "LOCAL_SIGNATURE_INVALID"):
            self.read(first)


class OriginalArchiveIntegration(unittest.TestCase):
    """Full frozen native fixture → original registration → view, with no mocks."""
    def test_v1_and_v2_full_authentication_publish_and_formal_exports_agree(self):
        from tests.unit.test_codex_core_archive import ArchiveTests
        original = ArchiveTests(methodName="test_full_register_then_reload_has_no_normalizer_selector_or_file_growth")
        original.setUp()
        self.addCleanup(original.doCleanups)
        for module in (archive_v1, archive_v2):
            with self.subTest(profile=module.PROFILE):
                registered = module.register_archive(original.runtime, original.request_path, original.worker,
                    original.supervisor, profile=module.PROFILE, allow_test_fixture=True)
                published = view.publish_view(original.runtime, registered["archive_id"],
                    profile=module.PROFILE, allow_test_fixture=True)
                self.assertEqual(published["summary"], registered["summary"])
                for role in sorted(view.EXPORT_ROLES):
                    result = view.read_view(original.runtime, published["view_id"], role=role, allow_test_fixture=True)
                    self.assertEqual(result["content"].encode(), Path(original.artifacts[role]["path"]).read_bytes())
                    self.assertFalse(result["source_reauthenticated_now"])
                self.assertTrue(published["full_authentication_performed"])
                self.assertEqual(published["normalizer_calls"], 0)
                self.assertEqual(published["selector_calls"], 0)
                self.assertEqual(published["provider_calls"], 0)


if __name__ == "__main__":
    unittest.main()
