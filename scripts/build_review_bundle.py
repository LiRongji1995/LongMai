"""Create and independently verify Git-object-derived review bundles."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

COMMON_EVIDENCE_CHECKS = (
    "ruff_check",
    "ruff_format",
    "mypy",
    "pytest",
    "build",
    "cli",
    "git_diff_check",
    "clean_source",
)
DOCKER_EVIDENCE_CHECKS = ("docker_compose_config",)
NATIVE_EVIDENCE_CHECKS = ("native_build", "native_run_c")
ROUND_ID = "phase-01-round-01b-native-lean-foundation"
ROUND_BASE = "ec7bba61c6d190c7e6624a467c4a45a6911c46e1"
REQUIRED_REVIEW_FILES = (
    "REVIEW_README.md",
    "ROUND_METADATA.json",
    "CHANGE_SUMMARY.md",
    "CHANGED_FILES.csv",
    "FULL_DIFF.patch",
    "DIFF_STAT.txt",
    "TEST_RESULTS.md",
    "COMMAND_RESULTS.txt",
    "CLEAN_SOURCE_RESULT.md",
    "SECURITY_SCAN_RESULT.md",
    "BUNDLE_VALIDATION_RESULT.md",
    "KNOWN_LIMITATIONS.md",
    "REPRODUCE.md",
    "TRACKED_FILES.txt",
    "FILE_MANIFEST.json",
    "CHECKSUMS.sha256",
    "EVIDENCE.json",
    "BUNDLE_INFO.txt",
)
FORBIDDEN_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}
SECRET_PATTERNS = (
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    (
        "github-token",
        re.compile(r"\b(?:ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{20,})\b"),
    ),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("bearer-token", re.compile(r"\bBearer\s+[A-Za-z0-9._-]{20,}\b", re.I)),
)
FORBIDDEN_FILE_SUFFIXES = (
    ".db",
    ".sqlite",
    ".sqlite3",
    ".parquet",
    ".feather",
    ".arrow",
    ".log",
)
MAX_NON_FIXTURE_BINARY_BYTES = 2 * 1024 * 1024
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
CONCRETE_ABSOLUTE_PATH_PATTERNS = (
    re.compile(r"[A-Za-z]:\\(?:[^\\/\r\n]+\\)+[^\\/\r\n]+"),
    re.compile(r"/(?:home|Users)/[^/\s]+(?:/[^/\s]+)*"),
)


@dataclass(frozen=True)
class BundleValidationResult:
    status: str
    checks: dict[str, dict[str, Any]]
    errors: list[str]


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


def git_bytes(root: Path, *args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True).stdout


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_text(path: Path, content: str) -> None:
    path.write_text(content.rstrip() + "\n", encoding="utf-8", newline="\n")


def load_evidence_json(path: Path) -> dict[str, Any]:
    """Load UTF-8 evidence, accepting only an optional UTF-8 BOM."""
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff", b"\xff\xfe\x00", b"\x00\x00\xfe\xff")):
        raise ValueError("evidence must be UTF-8, not UTF-16 or UTF-32")
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("evidence must be valid UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise ValueError("evidence JSON root must be an object")
    return payload


def classify(path: str) -> tuple[str, str]:
    if path.startswith("tests/"):
        return "test", "MEDIUM"
    if path.startswith("docs/") or path.endswith(".md"):
        return "documentation", "LOW"
    if path.startswith(".github/"):
        return "ci", "HIGH"
    if path.startswith(("config/", "scripts/")):
        return "configuration", "HIGH"
    return ("source", "HIGH") if path.startswith("src/") else ("other", "LOW")


def changed_rows(root: Path, base: str, head: str) -> list[dict[str, str]]:
    tokens = git_bytes(root, "diff", "--name-status", "-M", "-z", f"{base}..{head}").split(b"\0")
    rows: list[dict[str, str]] = []
    index = 0
    while index < len(tokens) and tokens[index]:
        status = tokens[index].decode("utf-8", "surrogateescape")
        index += 1
        old_path = ""
        if status.startswith(("R", "C")):
            old_path = tokens[index].decode("utf-8", "surrogateescape")
            index += 1
        file_path = tokens[index].decode("utf-8", "surrogateescape")
        index += 1
        counts = (
            git(root, "diff", "--numstat", f"{base}..{head}", "--", file_path).strip().split("\t")
        )
        additions, deletions = counts[:2] if len(counts) >= 2 else ["0", "0"]
        category, priority = classify(file_path)
        rows.append(
            {
                "change_type": status,
                "file_path": file_path,
                "old_path": old_path,
                "additions": additions,
                "deletions": deletions,
                "is_binary": str("-" in (additions, deletions)).lower(),
                "review_priority": priority,
                "notes": f"{category} review",
            }
        )
    return rows


def scan_paths_and_secrets(root: Path, *, allow_git_root: bool = False) -> list[str]:
    findings: list[str] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if allow_git_root and relative.parts and relative.parts[0] == ".git":
            continue
        name = path.name.lower()
        is_fixture = relative.parts[:3] == ("integrations", "lean", "data") or relative.parts[
            :2
        ] == (
            "tests",
            "fixtures",
        )
        forbidden_env = name == ".env" or (name.startswith(".env.") and name != ".env.example")
        forbidden_suffix = name.endswith(FORBIDDEN_FILE_SUFFIXES)
        forbidden_log_directory = "logs" in relative.parts
        if (
            any(part in FORBIDDEN_PARTS for part in relative.parts)
            or forbidden_env
            or forbidden_suffix
            or forbidden_log_directory
        ):
            findings.append(f"forbidden-path:{relative.as_posix()}")
        if path.is_file() and not is_fixture and path.stat().st_size > MAX_NON_FIXTURE_BINARY_BYTES:
            findings.append(f"oversized-file:{relative.as_posix()}")
        if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
            continue
        content = path.read_text(encoding="utf-8", errors="ignore")
        if any(pattern.search(content) for pattern in CONCRETE_ABSOLUTE_PATH_PATTERNS):
            findings.append(f"absolute-path:{relative.as_posix()}")
        for kind, pattern in SECRET_PATTERNS:
            if pattern.search(content):
                findings.append(f"{kind}:{relative.as_posix()}")
    return findings


def scan_git_head(root: Path, head: str) -> list[str]:
    """Scan only committed source, never ignored workspace caches or build output."""
    with tempfile.TemporaryDirectory(prefix="bundle-source-scan-") as temporary:
        source = Path(temporary) / "source"
        source.mkdir()
        safe_extract_archive(git_bytes(root, "archive", "--format=tar", head), source)
        return scan_paths_and_secrets(source)


def validate_evidence(payload: object, head: str, base: str, branch: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("evidence must be a JSON object")
    required = {
        "schema_version",
        "project_name",
        "head_commit",
        "base_commit",
        "branch",
        "project_phase",
        "round_id",
        "round_status",
        "limitations",
        "verification_environment",
        "checks",
    }
    if required - payload.keys() or payload.get("schema_version") != 1:
        raise ValueError("evidence schema is incomplete or unsupported")
    if (
        payload["head_commit"] != head
        or payload.get("origin_main_commit") != head
        or payload.get("working_tree_clean") is not True
        or payload["base_commit"] != base
        or payload["branch"] != branch
    ):
        raise ValueError("evidence commit or branch does not match bundle")
    environment = payload["verification_environment"]
    if not isinstance(environment, dict) or environment.get("type") not in {"docker", "native"}:
        raise ValueError("evidence must record a docker or native verification environment")
    if not re.fullmatch(r"3\.12\.\d+", str(environment.get("python_version", ""))):
        raise ValueError("evidence Python version must be 3.12.x")
    if environment.get("network_disabled") is not True:
        raise ValueError("evidence must record network_disabled=true")
    if environment.get("type") == "docker" and not environment.get("image_id"):
        raise ValueError("docker evidence must record image_id")
    if environment.get("type") == "native" and ({"image", "image_id"} & environment.keys()):
        raise ValueError("native evidence must not record image or image_id")
    if payload["round_status"] == "PASS WITH LIMITATIONS" and not payload["limitations"]:
        raise ValueError("PASS WITH LIMITATIONS evidence requires limitations")
    checks = payload["checks"]
    required_checks = COMMON_EVIDENCE_CHECKS + (
        DOCKER_EVIDENCE_CHECKS if environment.get("type") == "docker" else NATIVE_EVIDENCE_CHECKS
    )
    if not isinstance(checks, dict) or set(required_checks) - checks.keys():
        raise ValueError("evidence is missing required checks")
    for name in required_checks:
        check = checks[name]
        if not isinstance(check, dict) or check.get("status") != "PASS":
            raise ValueError(f"evidence check is not PASS: {name}")
        if (
            not isinstance(check.get("command"), str)
            or not check["command"].strip()
            or check.get("exit_code") != 0
        ):
            raise ValueError(f"evidence command is incomplete or failed: {name}")
    pytest = checks["pytest"]
    if any(
        not isinstance(pytest.get(key), int) or pytest[key] < 0
        for key in ("passed", "failed", "skipped")
    ):
        raise ValueError("evidence pytest counts are invalid")
    if pytest["failed"] != 0 or not isinstance(pytest.get("coverage_percent"), (int, float)):
        raise ValueError("evidence pytest result is invalid")
    clean = checks["clean_source"]
    if clean.get("head_commit") != head or clean.get("working_tree_clean") is not True:
        raise ValueError("evidence clean-source result does not match HEAD")
    if environment.get("type") == "native":
        if not re.fullmatch(r"[0-9a-f]{40}", str(environment.get("lean_commit", ""))):
            raise ValueError("native evidence must record a 40-character LEAN commit")
        if not environment.get("dotnet_sdk_version"):
            raise ValueError("native evidence must record dotnet_sdk_version")
        native_build = checks["native_build"]
        if native_build.get("dotnet_sdk_version") != environment[
            "dotnet_sdk_version"
        ] or not SHA256_PATTERN.fullmatch(str(native_build.get("dll_sha256", ""))):
            raise ValueError("native build identity is incomplete")
        native_run = checks["native_run_c"]
        if (
            native_run.get("filled_orders") != 2
            or native_run.get("order_sequence") != ["buy", "sell"]
            or native_run.get("final_holdings") != 0
            or native_run.get("golden_orders") != "PASS"
            or native_run.get("golden_statistics") != "PASS"
            or native_run.get("deterministic_build_identity") != "PASS"
        ):
            raise ValueError("native Run C result is incomplete")
        required_hashes = (
            "algorithm_source_sha256",
            "csproj_sha256",
            "fixture_zip_sha256",
            "fixture_extracted_content_sha256",
            "fixture_manifest_sha256",
            "config_sha256",
            "map_sha256",
            "factor_sha256",
            "golden_orders_sha256",
            "golden_statistics_sha256",
        )
        if any(
            not SHA256_PATTERN.fullmatch(str(native_run.get(name, ""))) for name in required_hashes
        ):
            raise ValueError("native Run C hashes are incomplete")
    return payload


def safe_extract_archive(archive: bytes, destination: Path) -> None:
    with tarfile.open(fileobj=__import__("io").BytesIO(archive), mode="r:") as tar:
        for member in tar.getmembers():
            candidate = PurePosixPath(member.name)
            if (
                candidate.is_absolute()
                or ".." in candidate.parts
                or member.issym()
                or member.islnk()
            ):
                raise ValueError(f"unsafe git archive member: {member.name}")
        for member in tar.getmembers():
            tar.extract(member, destination)


def zip_staging(staging: Path, archive_path: Path) -> None:
    with zipfile.ZipFile(
        archive_path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for item in sorted(path for path in staging.rglob("*") if path.is_file()):
            archive.write(item, item.relative_to(staging.parent).as_posix())


def _check(
    checks: dict[str, dict[str, Any]],
    name: str,
    ok: bool,
    count: int = 0,
    error: str = "",
) -> None:
    checks[name] = {"status": "PASS" if ok else "FAIL", "count": count, "error": error}


def validate_bundle(
    zip_path: Path,
    expected_head: str,
    repository_root: Path,
    require_validation_result: bool = True,
) -> BundleValidationResult:
    checks: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    try:
        with zipfile.ZipFile(zip_path) as archive:
            members = archive.namelist()
            safe = all(
                not PurePosixPath(name).is_absolute()
                and ".." not in PurePosixPath(name).parts
                and not re.match(r"^[A-Za-z]:", name)
                for name in members
            )
            roots = {PurePosixPath(name).parts[0] for name in members if PurePosixPath(name).parts}
            _check(checks, "zip_readable", True, len(members))
            _check(
                checks,
                "path_traversal",
                safe,
                len(members),
                "unsafe ZIP member" if not safe else "",
            )
            _check(
                checks,
                "single_root",
                len(roots) == 1,
                len(roots),
                "multiple roots" if len(roots) != 1 else "",
            )
            if not safe or len(roots) != 1:
                raise ValueError("unsafe or multi-root ZIP")
            root_name = next(iter(roots))
            with tempfile.TemporaryDirectory(prefix="bundle-validate-") as temporary:
                extracted = Path(temporary) / root_name
                for info in archive.infolist():
                    target = Path(temporary, *PurePosixPath(info.filename).parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if not info.is_dir():
                        target.write_bytes(archive.read(info))
                review = extracted / "review"
                required = set(REQUIRED_REVIEW_FILES)
                if not require_validation_result:
                    required.remove("BUNDLE_VALIDATION_RESULT.md")
                missing = [name for name in required if not (review / name).is_file()]
                _check(
                    checks,
                    "required_review_files",
                    not missing,
                    len(required) - len(missing),
                    ", ".join(missing),
                )
                if missing:
                    raise ValueError("missing review files")
                checksum_lines = (
                    (review / "CHECKSUMS.sha256").read_text(encoding="utf-8").splitlines()
                )
                checksum_ok = all(
                    (extracted / line.split("  ", 1)[1]).is_file()
                    and sha256(extracted / line.split("  ", 1)[1]) == line.split("  ", 1)[0]
                    for line in checksum_lines
                    if "  " in line
                )
                _check(
                    checks,
                    "checksums",
                    checksum_ok,
                    len(checksum_lines),
                    "checksum mismatch" if not checksum_ok else "",
                )
                manifest = json.loads((review / "FILE_MANIFEST.json").read_text(encoding="utf-8"))
                manifest_ok = isinstance(manifest, list) and all(
                    (extracted / row["relative_path"]).is_file()
                    and sha256(extracted / row["relative_path"]) == row["sha256"]
                    and (extracted / row["relative_path"]).stat().st_size == row["size_bytes"]
                    for row in manifest
                )
                _check(
                    checks,
                    "manifest",
                    manifest_ok,
                    len(manifest) if isinstance(manifest, list) else 0,
                    "manifest mismatch" if not manifest_ok else "",
                )
                source_files = sorted(
                    path.relative_to(extracted / "source").as_posix()
                    for path in (extracted / "source").rglob("*")
                    if path.is_file()
                )
                tracked = (review / "TRACKED_FILES.txt").read_text(encoding="utf-8").splitlines()
                _check(
                    checks,
                    "tracked_files",
                    source_files == tracked,
                    len(source_files),
                    ("source and tracked files differ" if source_files != tracked else ""),
                )
                mismatches = [
                    path
                    for path in tracked
                    if archive.read(f"{root_name}/source/{path}")
                    != git_bytes(repository_root, "show", f"{expected_head}:{path}")
                ]
                byte_ok = not mismatches
                _check(
                    checks,
                    "git_head_bytes",
                    byte_ok,
                    len(tracked),
                    (f"Git byte mismatch: {', '.join(mismatches)}" if not byte_ok else ""),
                )
                forbidden = scan_paths_and_secrets(extracted)
                _check(
                    checks,
                    "excluded_and_secret_scan",
                    not forbidden,
                    len(forbidden),
                    "; ".join(forbidden[:5]),
                )
                evidence = validate_evidence(
                    json.loads((review / "EVIDENCE.json").read_text(encoding="utf-8")),
                    expected_head,
                    json.loads((review / "ROUND_METADATA.json").read_text(encoding="utf-8"))[
                        "base_commit"
                    ],
                    json.loads((review / "ROUND_METADATA.json").read_text(encoding="utf-8"))[
                        "head_branch"
                    ],
                )
                _check(checks, "evidence_schema", evidence["head_commit"] == expected_head)
                command = [
                    "python",
                    "-c",
                    "import quant_system; from quant_system import cli; raise SystemExit(cli.main(['status']))",
                ]
                cli = subprocess.run(
                    command,
                    cwd=extracted / "source",
                    env={"PYTHONPATH": str(extracted / "source" / "src")},
                    capture_output=True,
                    text=True,
                    check=False,
                )
                cli_ok = cli.returncode == 0 and all(
                    token in cli.stdout
                    for token in ("paper trading enabled: false", "live trading enabled: false")
                )
                _check(
                    checks,
                    "offline_cli",
                    cli_ok,
                    error=(cli.stderr.strip() or "offline CLI output mismatch")
                    if not cli_ok
                    else "",
                )
    except (
        OSError,
        ValueError,
        KeyError,
        json.JSONDecodeError,
        zipfile.BadZipFile,
        subprocess.SubprocessError,
    ) as error:
        errors.append(str(error))
    status = (
        "PASS"
        if not errors and all(item["status"] == "PASS" for item in checks.values())
        else "FAIL"
    )
    return BundleValidationResult(status, checks, errors)


def render_validation(
    result: BundleValidationResult, filename: str, head: str, timestamp: str, scope: str
) -> str:
    lines = [
        "# Bundle Validation Result",
        "",
        f"STATUS: {result.status}",
        "",
        "## Validation Scope",
        "",
        f"- Scope: {scope}",
        f"- Candidate filename: `{filename}`",
        f"- Head commit: `{head}`",
        f"- Validation timestamp: `{timestamp}`",
        "",
        "## Checks",
        "",
    ]
    for name, item in result.checks.items():
        lines.append(
            f"- {name}: {item['status']} (count={item['count']})"
            + (f" — {item['error']}" if item["error"] else "")
        )
    if result.errors:
        lines.extend(["", "## Errors", *[f"- {error}" for error in result.errors]])
    return "\n".join(lines)


def populate_integrity_files(staging: Path, source_files: list[str]) -> None:
    review = staging / "review"
    write_text(
        review / "BUNDLE_INFO.txt",
        "\n".join(
            (
                f"Source file count: {len(source_files)}",
                f"Review file count: {len([path for path in review.iterdir() if path.is_file()])}",
                f"Total file count: {len([path for path in staging.rglob('*') if path.is_file()])}",
            )
        ),
    )
    manifest = []
    for file_path in sorted(path for path in staging.rglob("*") if path.is_file()):
        relative = file_path.relative_to(staging).as_posix()
        if relative.endswith(("FILE_MANIFEST.json", "CHECKSUMS.sha256")):
            continue
        manifest.append(
            {
                "relative_path": relative,
                "size_bytes": file_path.stat().st_size,
                "sha256": sha256(file_path),
            }
        )
    write_text(review / "FILE_MANIFEST.json", json.dumps(manifest, indent=2, sort_keys=True))
    lines = [
        f"{sha256(path)}  {path.relative_to(staging).as_posix()}"
        for path in sorted(staging.rglob("*"))
        if path.is_file() and path.name != "CHECKSUMS.sha256"
    ]
    write_text(review / "CHECKSUMS.sha256", "\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round-id", required=True)
    parser.add_argument("--base-commit", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--report-path", required=True)
    parser.add_argument("--round-start-time", required=True)
    parser.add_argument("--evidence-json", required=True, type=Path)
    args = parser.parse_args(argv)
    root = Path.cwd().resolve()
    if git(root, "status", "--porcelain").strip():
        raise SystemExit("working tree is not clean")
    head, branch = (
        git(root, "rev-parse", "HEAD").strip(),
        git(root, "rev-parse", "--abbrev-ref", "HEAD").strip(),
    )
    origin_main = git(root, "rev-parse", "origin/main").strip()
    if branch != "main" or head != origin_main:
        raise SystemExit("branch or origin/main does not match HEAD")
    if subprocess.run(
        ["git", "merge-base", "--is-ancestor", args.base_commit, head], cwd=root, check=False
    ).returncode:
        raise SystemExit("recorded base is not an ancestor of HEAD")
    report = Path(args.report_path)
    if not report.is_file():
        raise SystemExit("round arguments or report path are invalid")
    if args.report_path not in git(root, "ls-tree", "-r", "--name-only", head).splitlines():
        raise SystemExit("formal report is not tracked by HEAD")
    evidence = validate_evidence(
        load_evidence_json(args.evidence_json),
        head,
        args.base_commit,
        branch,
    )
    if args.round_id == ROUND_ID and (
        args.base_commit != ROUND_BASE
        or evidence["project_name"] != "us-equity-quant-system"
        or evidence["project_phase"] != "Phase 1"
        or evidence["round_id"] != args.round_id
        or evidence["round_status"] != "PASS WITH LIMITATIONS"
        or evidence["base_commit"] != args.base_commit
    ):
        raise SystemExit("round arguments or evidence identity are invalid")
    if scan_git_head(root, head):
        raise SystemExit("repository secret or forbidden-file scan failed")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"review-bundle-{args.round_id}-{head[:7]}-{timestamp}"
    args.output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = args.output_dir / f"{name}.zip"
    if archive_path.exists():
        raise SystemExit(f"refusing to overwrite existing bundle: {archive_path}")
    with tempfile.TemporaryDirectory(prefix="review-bundle-") as temporary:
        staging = Path(temporary) / name
        source = staging / "source"
        review = staging / "review"
        source.mkdir(parents=True)
        review.mkdir()
        source_files = git(root, "ls-tree", "-r", "--name-only", head).splitlines()
        for relative_path in source_files:
            destination = source / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(git_bytes(root, "show", f"{head}:{relative_path}"))
        write_text(review / "TRACKED_FILES.txt", "\n".join(source_files))
        write_text(review / "EVIDENCE.json", json.dumps(evidence, indent=2, sort_keys=True))
        metadata = {
            "project_name": "us-equity-quant-system",
            "project_phase": evidence["project_phase"],
            "round_id": evidence["round_id"],
            "round_status": evidence["round_status"],
            "base_commit": args.base_commit,
            "head_branch": branch,
            "head_commit": head,
            "clean_source_status": "PASS",
            "tests_status": "PASS",
            "security_scan_status": "PASS",
        }
        write_text(
            review / "ROUND_METADATA.json",
            json.dumps(metadata, indent=2, sort_keys=True),
        )
        write_text(
            review / "TEST_RESULTS.md",
            "STATUS: PASS\n\nSee EVIDENCE.json for real command results.",
        )
        write_text(
            review / "COMMAND_RESULTS.txt",
            "STATUS: PASS\n\nNative Python/.NET/LEAN clean-source commands are recorded in EVIDENCE.json.",
        )
        write_text(
            review / "CLEAN_SOURCE_RESULT.md",
            f"STATUS: PASS\n\nHEAD: `{head}`\nNetwork: disabled.",
        )
        write_text(
            review / "SECURITY_SCAN_RESULT.md",
            "STATUS: PASS\n\nCommitted source and pre-packaging staging contents found no disallowed secrets.",
        )
        write_text(
            review / "KNOWN_LIMITATIONS.md",
            "STATUS: "
            + str(evidence["round_status"])
            + "\n\n"
            + "\n".join(evidence["limitations"]),
        )
        write_text(
            review / "REVIEW_README.md",
            f"# Clean Review Bundle\n\nSTATUS: {evidence['round_status']}\n\nHead: `{head}`\nBase: `{args.base_commit}`",
        )
        write_text(
            review / "CHANGE_SUMMARY.md",
            f"# Round Change Summary\n\nSTATUS: {evidence['round_status']}\n\nBase: `{args.base_commit}`\nHead: `{head}`",
        )
        write_text(
            review / "FULL_DIFF.patch",
            git(root, "diff", "--binary", f"{args.base_commit}..{head}"),
        )
        write_text(
            review / "DIFF_STAT.txt",
            git(root, "diff", "--stat", f"{args.base_commit}..{head}"),
        )
        rows = changed_rows(root, args.base_commit, head)
        with (review / "CHANGED_FILES.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "change_type",
                    "file_path",
                    "old_path",
                    "additions",
                    "deletions",
                    "is_binary",
                    "review_priority",
                    "notes",
                ],
            )
            writer.writeheader()
            writer.writerows(rows)
        write_text(
            review / "REPRODUCE.md",
            "# Reproduce\n\nUse Python 3.12 and run the checks recorded in `EVIDENCE.json`.",
        )
        candidate = args.output_dir / f"{name}-candidate.zip"
        populate_integrity_files(staging, source_files)
        zip_staging(staging, candidate)
        candidate_result = validate_bundle(candidate, head, root, require_validation_result=False)
        if candidate_result.status != "PASS":
            candidate.unlink(missing_ok=True)
            raise SystemExit(
                f"candidate ZIP validation failed: {candidate_result.errors} {candidate_result.checks}"
            )
        write_text(
            review / "BUNDLE_VALIDATION_RESULT.md",
            render_validation(
                candidate_result,
                candidate.name,
                head,
                timestamp,
                "Pre-packaging candidate-content validation; this is not final-ZIP validation.",
            ),
        )
        populate_integrity_files(staging, source_files)
        zip_staging(staging, archive_path)
        candidate.unlink(missing_ok=True)
    final_result = validate_bundle(archive_path, head, root)
    if final_result.status != "PASS":
        archive_path.unlink(missing_ok=True)
        raise SystemExit(
            f"final ZIP validation failed: {final_result.errors} {final_result.checks}"
        )
    print(
        json.dumps(
            {
                "bundle_path": str(archive_path),
                "size_bytes": archive_path.stat().st_size,
                "sha256": sha256(archive_path),
                "head_commit": head,
                "status": "PASS",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
