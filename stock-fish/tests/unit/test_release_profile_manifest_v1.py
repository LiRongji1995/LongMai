"""TEST_FIXTURE: input identity checks never stand in for platform acceptance."""
import json
from pathlib import Path
import shutil

import pytest

from scripts import release_profile_manifest_v1 as profiles


@pytest.fixture
def fixture_root(tmp_path):
    root = tmp_path / "TEST_FIXTURE_release"
    root.mkdir()
    manifest = json.loads((profiles.ROOT / profiles.MANIFEST).read_bytes())
    names = {profiles.MANIFEST}
    for spec in manifest["profiles"].values():
        names.update(spec["source_files"])
    names.add(manifest["profiles"]["daily-review"]["lock_manifest"])
    lock = json.loads((profiles.ROOT / manifest["profiles"]["daily-review"]["lock_manifest"]).read_bytes())
    names.update("stock-fish/runtime-locks/" + name for name in lock["locks"])
    for name in names:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(profiles.ROOT / name, target)
    return root


def test_complete_inventory_distinguishes_hashed_inputs_and_unverified_platforms(fixture_root):
    rows = {row["profile"]: row for row in profiles.inspect_profiles(fixture_root)["profiles"]}
    assert rows["daily-review"]["status"] == "HASHED_INPUTS_VERIFIED"
    assert rows["daily-review"]["runtime_acceptance"] == "NOT_CHECKED"
    assert rows["daily-review"]["artifact_count"] > 0
    for name in ("docker", "live", "go"):
        assert rows[name]["status"] == "UNVERIFIED" and rows[name]["missing_evidence"]
    assert all(row["market_edge"] == "NOT_ESTABLISHED" for row in rows.values())


def test_changed_requirement_or_lock_fails_without_relocking(fixture_root):
    path = fixture_root / "stock-fish/requirements-daily.txt"
    path.write_text(path.read_text() + "\n# TEST_FIXTURE tamper\n")
    with pytest.raises(ValueError, match="INPUT_HASH_MISMATCH"):
        profiles.inspect_profiles(fixture_root)


def test_platform_mismatch_is_never_promoted(fixture_root, capsys):
    row = profiles.inspect_profiles(fixture_root, "daily-review", "Linux/x86_64/3.12")["profiles"][0]
    assert row["status"] == "UNVERIFIED"
    assert row["missing_evidence"] == ["TARGET_PLATFORM_NOT_COVERED"]
    assert profiles.main(["--root", str(fixture_root), "--profile", "live", "--require-hashed-inputs"]) == 3


@pytest.mark.parametrize("change", ["duplicate", "origin", "missing_hash"])
def test_artifact_metadata_must_be_complete_and_official(fixture_root, change):
    path = fixture_root / "stock-fish/runtime-locks/daily-review-py312-macos-x86_64.json"
    lock = json.loads(path.read_bytes())
    if change == "duplicate":
        lock["packages"].append(dict(lock["packages"][0]))
    elif change == "origin":
        lock["packages"][0]["url"] = "https://example.invalid/package.whl"
    else:
        lock["packages"][0]["sha256"] = ""
    path.write_text(json.dumps(lock))
    with pytest.raises(ValueError, match="RELEASE_ARTIFACT"):
        profiles.inspect_profiles(fixture_root)


def test_manifest_cannot_promote_unverified_without_a_lock(fixture_root):
    path = fixture_root / profiles.MANIFEST
    manifest = json.loads(path.read_bytes())
    manifest["profiles"]["live"]["missing_evidence"] = []
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="EXPLICIT_GAPS"):
        profiles.inspect_profiles(fixture_root)
