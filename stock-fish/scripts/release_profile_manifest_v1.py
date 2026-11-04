"""Read-only release input inventory. A source hash never certifies a runtime."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = "stock-fish/scripts/contracts/release_profiles_v1.json"


def _file(root, relative):
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or "\\" in relative:
        raise ValueError("RELEASE_PATH_OUTSIDE_ROOT")
    target = root.joinpath(*path.parts)
    if not target.resolve().is_relative_to(root.resolve()) or not target.is_file():
        raise ValueError("RELEASE_INPUT_MISSING_OR_OUTSIDE_ROOT")
    if any(item.is_symlink() for item in [target, *target.parents] if item != root.parent):
        raise ValueError("RELEASE_SYMLINK_REJECTED")
    return target


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect_profiles(root=ROOT, profile=None, target=None):
    root = Path(root).resolve()
    manifest_path = _file(root, MANIFEST)
    manifest = json.loads(manifest_path.read_bytes())
    if manifest.get("schema") != "RELEASE_PROFILE_INVENTORY_V1":
        raise ValueError("UNSUPPORTED_RELEASE_MANIFEST")
    profiles = manifest["profiles"]
    if set(profiles) != {"daily-review", "docker", "live", "go"}:
        raise ValueError("RELEASE_PROFILE_SET_CHANGED")
    selected = [profile] if profile else sorted(profiles)
    rows = []
    for name in selected:
        spec = profiles[name]
        files = [{"path": value, "sha256": _sha(_file(root, value))}
                 for value in spec["source_files"]]
        row = {"profile": name, "input_files": files,
               "runtime_acceptance": "NOT_CHECKED", "market_edge": "NOT_ESTABLISHED",
               "platform": spec.get("platform"), "status": "UNVERIFIED",
               "missing_evidence": spec.get("missing_evidence", [])}
        if spec["kind"] == "hashed-python-inputs":
            lock_path = _file(root, spec["lock_manifest"])
            lock = json.loads(lock_path.read_bytes())
            if (lock["contract"] != "DAILY_REVIEW_NATIVE_V1" or
                    lock["index_url"] != "https://pypi.org/simple" or
                    any(lock[key] != spec["platform"][key] for key in ("system", "machine", "python_minor"))):
                raise ValueError("RELEASE_LOCK_CONTRACT_MISMATCH")
            records = []
            for section, base in (("source_requirements", "stock-fish"), ("locks", "stock-fish/runtime-locks")):
                for filename, expected in lock[section].items():
                    if PurePosixPath(filename).name != filename:
                        raise ValueError("RELEASE_LOCK_PATH_REJECTED")
                    relative = base + "/" + filename
                    actual = _sha(_file(root, relative))
                    if actual != expected:
                        raise ValueError("RELEASE_LOCK_INPUT_HASH_MISMATCH:" + relative)
                    records.append({"path": relative, "sha256": actual})
            names = set()
            for package in lock["packages"]:
                url = urlsplit(package["url"])
                name_key = re.sub(r"[-_.]+", "-", package["name"]).lower()
                if name_key in names or not re.fullmatch("[0-9a-f]{64}", package["sha256"]):
                    raise ValueError("RELEASE_ARTIFACT_HASH_OR_UNIQUENESS_INVALID")
                if (url.scheme != "https" or url.hostname != "files.pythonhosted.org"
                        or url.username or url.password or url.port not in (None, 443)):
                    raise ValueError("RELEASE_ARTIFACT_ORIGIN_REJECTED")
                names.add(name_key)
            if not names:
                raise ValueError("RELEASE_ARTIFACT_SET_EMPTY")
            row.update(status="HASHED_INPUTS_VERIFIED", lock_manifest_sha256=_sha(lock_path),
                       verified_lock_inputs=records, artifact_count=len(names),
                       missing_evidence=["CURRENT_BUILD_AND_RUNTIME_ACCEPTANCE"])
            if target and target != "/".join(spec["platform"][key] for key in ("system", "machine", "python_minor")):
                row.update(status="UNVERIFIED", missing_evidence=["TARGET_PLATFORM_NOT_COVERED"])
        elif spec["kind"] != "unverified-platform-build" or not row["missing_evidence"]:
            raise ValueError("UNVERIFIED_PROFILE_REQUIRES_EXPLICIT_GAPS")
        rows.append(row)
    return {"schema": "RELEASE_PROFILE_CHECK_V1", "manifest_sha256": _sha(manifest_path),
            "read_only": True, "installed_or_changed": False, "profiles": rows}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--profile", choices=("daily-review", "docker", "live", "go"))
    parser.add_argument("--target", help="Exact system/machine/python_minor; never infer cross-platform compatibility")
    parser.add_argument("--require-hashed-inputs", action="store_true")
    args = parser.parse_args(argv)
    value = inspect_profiles(args.root, args.profile, args.target)
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))
    return 3 if args.require_hashed_inputs and any(row["status"] != "HASHED_INPUTS_VERIFIED" for row in value["profiles"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
