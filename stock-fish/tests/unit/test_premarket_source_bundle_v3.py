"""TEST_FIXTURE source-only bundle checks; no real capture, database or network."""
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys

import pytest

from scripts import build_premarket_source_bundle_v3 as bundle

SOURCE = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def denied(*_args, **_kwargs):
        raise AssertionError("TEST_FIXTURE_NETWORK_AND_DB_FORBIDDEN")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(sqlite3, "connect", denied)


@pytest.fixture
def fixture_root(tmp_path):
    root = (tmp_path / "TEST_FIXTURE_source_bundle_v3").resolve()
    root.mkdir()
    return root


@pytest.fixture
def created(fixture_root):
    destination = fixture_root / "bundle"
    result = bundle.create(SOURCE, destination)
    return destination, result


def source_copy(root):
    source = root / "source"
    source.mkdir()
    for name, path in bundle.inventory(SOURCE).items():
        target = source.parent / name if name == "THIRD_PARTY_NOTICES.md" else source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    return source


def rewrite(path, raw):
    path.chmod(0o600)
    path.write_bytes(raw)
    path.chmod(0o444)


def mutate_manifest(destination, change):
    path = destination / bundle.MANIFEST
    value = json.loads(path.read_bytes())
    change(value)
    rewrite(path, bundle.previous.canonical(value))
    return bundle.read(path)[1]["sha256"]


def verify_created(created):
    destination, result = created
    return bundle.verify(destination, result["manifest"]["sha256"])


def test_exact_producer_patches_and_complete_original_workflow_closure(created):
    destination, result = created
    verification = verify_created(created)
    assert result["status"] == "CREATED_AND_VERIFIED_NOT_EXECUTED"
    assert verification["status"] == "VERIFIED_NOT_EXECUTED"
    manifest = json.loads((destination / bundle.MANIFEST).read_bytes())
    for name, (original_sha, transform) in bundle.PATCHED.items():
        before = bundle.read(SOURCE / name)[0]
        assert bundle.sha(before) == original_sha
        assert bundle.read(destination / name)[0] == transform(before)[0]
        assert bundle.read(destination / name)[0] != before
    assert manifest["producer_hashes"]["provider_budget.py"] == bundle.FROZEN[
        "stockfish_core/yaogu/provider_budget.py"]
    assert set(manifest["tools"]) == {"build_premarket_source_bundle_v3.py",
        "build_premarket_source_bundle_v2.py", "codex_factor_capture_v3.py"}
    assert set(manifest["original_workflow_scripts"]) == bundle.SCRIPT_NAMES
    assert {"scripts/" + name + ".py" for name in bundle.GATEWAY_SCRIPTS} <= set(manifest["files"])
    assert verification["source_capability_issued"] is False
    assert verification["retry_authorized"] is False
    assert verification["capture_executed"] is False


def test_build_preserves_full_source_bytes_and_physical_identities(fixture_root):
    selected = bundle.inventory(SOURCE)
    before = {name: bundle.read(path)[1] for name, path in selected.items()}
    bundle.create(SOURCE, fixture_root / "bundle")
    assert bundle.inventory(SOURCE) == selected
    assert {name: bundle.read(path)[1] for name, path in selected.items()} == before


def test_verify_does_not_reopen_mutable_original_checkout(fixture_root):
    source = source_copy(fixture_root)
    destination = fixture_root / "bundle"
    result = bundle.create(source, destination)
    source.rename(fixture_root / "original_no_longer_at_recorded_path")
    assert bundle.verify(destination, result["manifest"]["sha256"])["current_original_source_revalidated"] is False


def test_private_sibling_files_are_not_copied(fixture_root):
    source = source_copy(fixture_root)
    (source / ".env").write_text("TEST_FIXTURE_PRIVATE")
    (source / "scripts/private_do_not_copy.py").write_text("TEST_FIXTURE_PRIVATE = True")
    result = bundle.create(source, fixture_root / "bundle")
    assert result["verification"]["capture_executed"] is False
    assert not (fixture_root / "bundle/.env").exists()
    assert not (fixture_root / "bundle/scripts/private_do_not_copy.py").exists()


def test_existing_destination_is_never_overwritten(created):
    destination, result = created
    before = bundle.read(destination / bundle.MANIFEST)[1]
    with pytest.raises(FileExistsError):
        bundle.create(SOURCE, destination)
    assert bundle.read(destination / bundle.MANIFEST)[1] == before == result["manifest"]


def test_bundle_cannot_live_inside_original_source(fixture_root):
    source = source_copy(fixture_root)
    with pytest.raises(ValueError, match="SEPARATE_FROM_SOURCE"):
        bundle.create(source, source / "nested")
    assert not (source / "nested").exists()


def test_manifest_digest_is_required(created):
    with pytest.raises(ValueError, match="EXPLICIT_MANIFEST"):
        bundle.verify(created[0], "0" * 64)


@pytest.mark.parametrize("name", ["extra.txt", "empty_directory"])
def test_extra_file_or_empty_directory_is_rejected(created, name):
    path = created[0] / name
    path.mkdir() if name == "empty_directory" else path.write_text("TEST_FIXTURE")
    with pytest.raises(ValueError, match="UNREGISTERED_OR_MISSING"):
        verify_created(created)


def test_missing_runtime_document_is_rejected(created):
    (created[0] / "docs/THEME_EVOLUTION_STREAMING_V2.md").unlink()
    with pytest.raises(ValueError, match="UNREGISTERED_OR_MISSING"):
        verify_created(created)


def test_equal_bytes_new_physical_file_is_rejected(created):
    target = created[0] / "scripts/codex_capacity.py"
    raw = target.read_bytes()
    target.unlink()
    target.write_bytes(raw)
    target.chmod(0o444)
    with pytest.raises(ValueError, match="BUNDLE_REFERENCE_CHANGED"):
        verify_created(created)


def test_forged_producer_and_updated_local_reference_still_need_exact_patch(created):
    destination, _ = created
    target = destination / bundle.WORKER
    rewrite(target, target.read_bytes() + b"\n# TEST_FIXTURE_unauthorized\n")
    digest = mutate_manifest(destination, lambda value: value["files"].__setitem__(bundle.WORKER, bundle.read(target)[1]))
    with pytest.raises(ValueError, match="PRODUCER_PATCH_BYTES_DIFFER"):
        bundle.verify(destination, digest)


@pytest.mark.parametrize("field", ["retry_authorized", "source_capability_issued", "original_physical_owner_restored"])
def test_manifest_cannot_grant_execution_or_source_authority(created, field):
    digest = mutate_manifest(created[0], lambda value: value.__setitem__(field, True))
    with pytest.raises(ValueError, match="AUTHORITY_SCOPE"):
        bundle.verify(created[0], digest)


def test_changed_build_tool_identity_is_rejected(created):
    digest = mutate_manifest(created[0], lambda value: value["tools"]["codex_factor_capture_v3.py"].__setitem__("sha256", "0" * 64))
    with pytest.raises(ValueError, match="BUILD_TOOL_IDENTITY"):
        bundle.verify(created[0], digest)


def test_new_unreviewed_script_dependency_cannot_expand_public_allowlist(fixture_root):
    source = source_copy(fixture_root)
    path = source / "scripts/codex_daily_v2.py"
    path.write_bytes(path.read_bytes() + b"\nfrom scripts import TEST_FIXTURE_private_unreviewed\n")
    with pytest.raises(ValueError, match="UNREGISTERED_SCRIPT_DEPENDENCY"):
        bundle.create(source, fixture_root / "bundle")
    assert not (fixture_root / "bundle").exists()


def test_ancestor_symlink_is_rejected_for_read_and_create(fixture_root):
    real = fixture_root / "real"
    real.mkdir()
    (real / "input.json").write_text("{}")
    alias = fixture_root / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(OSError):
        bundle.read(alias / "input.json")
    with pytest.raises(OSError):
        bundle.create(SOURCE, alias / "bundle")


def test_ancestor_swap_after_open_is_rejected(fixture_root, monkeypatch):
    parent = fixture_root / "parent"
    parent.mkdir()
    path = parent / "input.json"
    path.write_text("{}")
    opened = os.open
    replaced = False
    def race(name, flags, *args, **kwargs):
        nonlocal replaced
        descriptor = opened(name, flags, *args, **kwargs)
        if name == "input.json" and kwargs.get("dir_fd") is not None and not replaced:
            replaced = True
            parent.rename(fixture_root / "moved_parent")
            parent.symlink_to(fixture_root / "moved_parent", target_is_directory=True)
        return descriptor
    monkeypatch.setattr(os, "open", race)
    with pytest.raises(OSError):
        bundle.read(path)
    assert replaced


def test_late_bundle_change_is_caught_by_full_terminal_reread(created, monkeypatch):
    destination, _ = created
    target = destination / "scripts/codex_capacity.py"
    original = bundle.read
    changed = False
    def race(path, *args, **kwargs):
        nonlocal changed
        result = original(path, *args, **kwargs)
        if Path(path) == destination / bundle.WORKER and not changed:
            changed = True
            rewrite(target, target.read_bytes() + b"\n# TEST_FIXTURE_late\n")
        return result
    monkeypatch.setattr(bundle, "read", race)
    with pytest.raises(ValueError, match="BUNDLE_TERMINAL_REFERENCE_CHANGED"):
        verify_created(created)
    assert changed


def test_late_original_change_after_bundle_verification_rejects_creation(fixture_root, monkeypatch):
    source = source_copy(fixture_root)
    target = source / "scripts/codex_capacity.py"
    original = bundle.verify
    def race(*args, **kwargs):
        result = original(*args, **kwargs)
        target.write_bytes(target.read_bytes() + b"\n# TEST_FIXTURE_late_source\n")
        return result
    monkeypatch.setattr(bundle, "verify", race)
    with pytest.raises(ValueError, match="SOURCE_CHANGED_DURING_BUILD"):
        bundle.create(source, fixture_root / "bundle")
    assert not (fixture_root / "bundle" / bundle.MANIFEST).exists()


def test_cli_failure_does_not_disclose_private_paths(fixture_root, capsys):
    status = bundle.main(["verify", "--destination", str(fixture_root / "TEST_FIXTURE_private_missing"),
        "--manifest-sha256", "0" * 64])
    output = capsys.readouterr().out
    assert status == 4
    assert "TEST_FIXTURE_private_missing" not in output
    assert json.loads(output)["capture_executed"] is False


def test_bundle_runs_self_contained_verifier_and_original_code_registry_offline(created, fixture_root):
    destination, result = created
    script = r'''
import json,pathlib,socket,sqlite3,sys
def denied(*args,**kwargs): raise AssertionError("TEST_FIXTURE_NETWORK_AND_DB_FORBIDDEN")
socket.socket.connect=denied; socket.socket.connect_ex=denied
socket.create_connection=denied; sqlite3.connect=denied
root=pathlib.Path(sys.argv[1]); sys.path.insert(0,str(root))
from scripts import build_premarket_source_bundle_v3 as builder
from scripts import codex_premarket_shared_v1 as workflow
assert pathlib.Path(builder.__file__).is_relative_to(root)
assert pathlib.Path(builder.patches.__file__).is_relative_to(root)
verified=builder.verify(root,sys.argv[2])
manifest=json.loads((root/builder.MANIFEST).read_bytes())
refs=workflow.code_refs()
for ref in refs:
    name=pathlib.Path(ref["path"]).relative_to(root).as_posix()
    assert ref["sha256"]==manifest["files"][name]["sha256"]
print(json.dumps({"status":verified["status"],"code_references":len(refs),"capture_executed":False}))
'''
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    absent = fixture_root / "absent_bytecode"
    command = [sys.executable, "-I", "-B", "-X", "pycache_prefix=" + str(absent), "-c", script,
        str(destination), result["manifest"]["sha256"]]
    process = subprocess.run(command, capture_output=True, text=True, timeout=60, env=environment,
        cwd=fixture_root)
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout)["code_references"] > 100
    assert not absent.exists()
    assert verify_created(created)["capture_executed"] is False
