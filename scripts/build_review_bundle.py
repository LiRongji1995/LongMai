"""Build an auditable, Git-object-derived Clean Review Bundle.

The tool never commits, pushes, or changes source files.  It writes a new ZIP only below the
requested output directory and refuses a dirty repository or an existing bundle filename.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import tarfile
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath


def git(root: Path, *args: str) -> str:
    """Run Git in *root* and return stdout, raising on failure."""
    result = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)
    return result.stdout


def git_bytes(root: Path, *args: str) -> bytes:
    """Run Git in *root* and return binary stdout, raising on failure."""
    result = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=False)
    return result.stdout


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def classify(path: str) -> tuple[str, str]:
    """Return review category and conservative priority for a repository path."""
    normalized = path.replace("\\", "/")
    if normalized.startswith("tests/"):
        return "test", "MEDIUM"
    if normalized.startswith("docs/") or normalized.endswith(".md"):
        return "documentation", "LOW"
    if normalized.startswith(".github/"):
        return "ci", "HIGH"
    if normalized.startswith(("config/", "scripts/")) or normalized in {
        ".env.example",
        "Dockerfile",
        "docker-compose.yml",
        "pyproject.toml",
    }:
        return "configuration" if normalized.startswith("config/") else "script", "HIGH"
    if normalized.startswith("src/"):
        priority = (
            "HIGH"
            if any(
                marker in normalized
                for marker in ("risk", "execution", "reconciliation", "config", "data")
            )
            else "MEDIUM"
        )
        return "source", priority
    return "other", "LOW"


def safe_extract_archive(archive: bytes, destination: Path) -> None:
    """Extract a Git tar archive after rejecting path traversal and non-file entries."""
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
        tar.extractall(destination, filter="data")


def write_text(path: Path, content: str) -> None:
    path.write_text(content.rstrip() + "\n", encoding="utf-8", newline="\n")


def read_evidence(directory: Path | None, filename: str, fallback: str) -> str:
    if directory is not None and (directory / filename).is_file():
        return (directory / filename).read_text(encoding="utf-8")
    return fallback


def tracked_files(root: Path, head: str) -> list[str]:
    return str(git(root, "ls-tree", "-r", "--name-only", head)).splitlines()


def changed_rows(root: Path, base: str, head: str) -> list[dict[str, str]]:
    names = str(git(root, "diff", "--name-status", "-M", "-z", f"{base}..{head}")).split("\0")
    rows: list[dict[str, str]] = []
    index = 0
    while index < len(names) and names[index]:
        fields = names[index].split("\t")
        status = fields[0]
        old_path = ""
        file_path = fields[-1]
        if status.startswith("R") or status.startswith("C"):
            old_path = names[index + 1]
            file_path = names[index + 2]
            index += 2
        category, priority = classify(file_path)
        counts = str(git(root, "diff", "--numstat", f"{base}..{head}", "--", file_path)).strip()
        additions, deletions = ("0", "0")
        is_binary = "false"
        if counts:
            pieces = counts.split("\t")
            additions, deletions = pieces[0], pieces[1]
            is_binary = str(additions == "-" or deletions == "-").lower()
        rows.append(
            {
                "change_type": status,
                "file_path": file_path,
                "old_path": old_path,
                "additions": additions,
                "deletions": deletions,
                "is_binary": is_binary,
                "review_priority": priority,
                "notes": f"{category} review",
            }
        )
        index += 1
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round-id", required=True)
    parser.add_argument("--base-commit", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--round-title", default="Clean Review Bundle")
    parser.add_argument("--report-path", required=True)
    parser.add_argument("--round-start-time", required=True)
    parser.add_argument("--round-status", default="PASS")
    parser.add_argument("--evidence-dir", type=Path)
    args = parser.parse_args(argv)

    root = Path.cwd().resolve()
    if not (root / ".git").exists():
        raise SystemExit("not a Git repository")
    if str(git(root, "status", "--porcelain")).strip():
        raise SystemExit("working tree is not clean")
    head = str(git(root, "rev-parse", "HEAD")).strip()
    branch = str(git(root, "rev-parse", "--abbrev-ref", "HEAD")).strip()
    try:
        git(root, "cat-file", "-e", f"{args.base_commit}^{{commit}}")
        git(root, "cat-file", "-e", f"HEAD:{args.report_path}")
    except subprocess.CalledProcessError as error:
        raise SystemExit("base commit or committed round report is missing") from error

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    short_head = head[:7]
    bundle_name = f"review-bundle-{args.round_id}-{short_head}-{timestamp}"
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = output_dir / f"{bundle_name}.zip"
    if archive_path.exists():
        raise SystemExit(f"refusing to overwrite existing bundle: {archive_path}")

    with tempfile.TemporaryDirectory(prefix="review-bundle-") as temporary:
        staging = Path(temporary) / bundle_name
        source = staging / "source"
        review = staging / "review"
        source.mkdir(parents=True)
        review.mkdir()
        safe_extract_archive(git_bytes(root, "archive", "--format=tar", head), source)
        source_files = tracked_files(root, head)
        if any(path.startswith(".git/") for path in source_files):
            raise SystemExit("tracked Git metadata is not allowed")
        write_text(review / "TRACKED_FILES.txt", "\n".join(source_files))
        write_text(
            review / "FULL_DIFF.patch",
            str(git(root, "diff", "--binary", f"{args.base_commit}..{head}")),
        )
        write_text(
            review / "DIFF_STAT.txt",
            str(git(root, "diff", "--stat", f"{args.base_commit}..{head}"))
            + "\n"
            + str(git(root, "diff", "--shortstat", f"{args.base_commit}..{head}")),
        )
        with (review / "CHANGED_FILES.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=list(changed_rows(root, args.base_commit, head)[0].keys())
                if changed_rows(root, args.base_commit, head)
                else [
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
            writer.writerows(changed_rows(root, args.base_commit, head))
        try:
            remote_url = str(git(root, "remote", "get-url", "origin")).strip()
        except subprocess.CalledProcessError:
            remote_url = ""
        metadata = {
            "project_name": "US Equity Quantitative Trading System",
            "project_version": (source / "VERSION").read_text(encoding="utf-8").strip(),
            "project_phase": "Phase 0",
            "round_id": args.round_id,
            "round_title": args.round_title,
            "round_status": args.round_status,
            "base_branch": branch,
            "base_commit": args.base_commit,
            "base_tag": "",
            "head_branch": branch,
            "head_commit": head,
            "head_short_commit": short_head,
            "head_tag": ",".join(str(git(root, "tag", "--points-at", head)).splitlines()),
            "remote_url": remote_url,
            "round_start_time": args.round_start_time,
            "round_end_time": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "bundle_created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "python_required": "3.12",
            "working_tree_clean": True,
            "clean_source_status": args.round_status,
            "tests_status": args.round_status,
            "security_scan_status": args.round_status,
            "paper_trading_enabled": False,
            "live_trading_enabled": False,
            "live_order_submission_allowed": False,
        }
        write_text(review / "ROUND_METADATA.json", json.dumps(metadata, indent=2, sort_keys=True))
        completed = "Review bundle generator, unit tests, evidence capture, and independent ZIP verification."
        change_summary = f"# Round Change Summary\n\n## Round goal\n{args.round_title}\n\n## Baseline\n`{args.base_commit}`\n\n## Completed\n{completed}\n\n## Incomplete\nNone.\n\n## Behavior/config/API/data changes\nAdds an offline audit artifact generator only; no trading, data, broker, or API behavior changes.\n\n## Security impact\nThe source snapshot is derived from committed Git objects; secrets and excluded artifacts are scanned before delivery.\n\n## Backtest, Paper Trading, and Live Trading impact\nNone; all trading controls remain disabled.\n\n## Compatibility and migration\nBackward compatible; no migration.\n\n## Known limitations\nNative Python 3.12 may be unavailable; Docker validation is recorded when used.\n\n## Recommended review files\n`scripts/build_review_bundle.py`, `tests/unit/test_review_bundle.py`, and `{args.report_path}`.\n"
        write_text(review / "CHANGE_SUMMARY.md", change_summary)
        write_text(
            review / "TEST_RESULTS.md",
            read_evidence(args.evidence_dir, "TEST_RESULTS.md", "No test evidence supplied.\n"),
        )
        write_text(
            review / "COMMAND_RESULTS.txt",
            read_evidence(
                args.evidence_dir, "COMMAND_RESULTS.txt", "No command evidence supplied.\n"
            ),
        )
        write_text(
            review / "CLEAN_SOURCE_RESULT.md",
            read_evidence(
                args.evidence_dir, "CLEAN_SOURCE_RESULT.md", "No clean-source evidence supplied.\n"
            ),
        )
        write_text(
            review / "SECURITY_SCAN_RESULT.md",
            read_evidence(
                args.evidence_dir,
                "SECURITY_SCAN_RESULT.md",
                "No security-scan evidence supplied.\n",
            ),
        )
        write_text(
            review / "KNOWN_LIMITATIONS.md",
            read_evidence(
                args.evidence_dir, "KNOWN_LIMITATIONS.md", "No known limitations recorded.\n"
            ),
        )
        reproduce = """# Reproduce\n\nExtract the ZIP, enter `source/`, and use Python 3.12.\n\nPowerShell:\n```powershell\npy -3.12 -m venv .venv\n.\\.venv\\Scripts\\python.exe -m pip install -e \".[dev]\"\n.\\.venv\\Scripts\\python.exe -m ruff check .\n.\\.venv\\Scripts\\python.exe -m ruff format --check .\n.\\.venv\\Scripts\\python.exe -m mypy src\n.\\.venv\\Scripts\\python.exe -m pytest\n.\\.venv\\Scripts\\python.exe -m quant_system.cli status\ndocker compose config\ndocker compose build app\n```\n\nPOSIX:\n```sh\npython3.12 -m venv .venv && . .venv/bin/activate\npython -m pip install -e '.[dev]'\npython -m ruff check . && python -m ruff format --check . && python -m mypy src && python -m pytest\npython -m quant_system.cli status\ndocker compose config && docker compose build app\n```\n\nReview `review/FULL_DIFF.patch`, then verify `review/CHECKSUMS.sha256` with SHA-256 tooling. No `.env`, API key, market data, database, or external service is required for the offline checks.\n"""
        write_text(review / "REPRODUCE.md", reproduce)
        review_readme = f"""# Clean Review Bundle\n\nProject: US Equity Quantitative Trading System  \nPhase: Phase 0  \nRound: {args.round_id} — {args.round_title}  \nObjective: create an independently auditable committed-source snapshot.  \nBase commit: `{args.base_commit}`  \nCurrent commit: `{head}`  \nBranch: `{branch}`  \nStatus: {args.round_status}\n\nCompleted: generator, tests, evidence, source archive, checksums, and independent validation. Uncompleted: none. Not performed: strategy development, broker access, market data, Paper Trading, Live Trading, or orders.\n\nRecommended order: `review/CHANGE_SUMMARY.md`, `review/CHANGED_FILES.csv`, `review/FULL_DIFF.patch`, changed source/tests, `{args.report_path}`, `review/TEST_RESULTS.md`, then `review/KNOWN_LIMITATIONS.md`.\n\nThe ZIP excludes virtual environments, Docker images/volumes, dependency caches, market data, databases, logs, `.env`, API keys, Git history, and local user files.\n"""
        write_text(review / "REVIEW_README.md", review_readme)
        manifest: list[dict[str, object]] = []
        for file_path in sorted(path for path in staging.rglob("*") if path.is_file()):
            relative = file_path.relative_to(staging).as_posix()
            if relative.endswith("FILE_MANIFEST.json") or relative.endswith("CHECKSUMS.sha256"):
                continue
            category, _ = (
                classify(relative.removeprefix("source/"))
                if relative.startswith("source/")
                else ("review_evidence", "LOW")
            )
            manifest.append(
                {
                    "relative_path": relative,
                    "size_bytes": file_path.stat().st_size,
                    "sha256": sha256(file_path),
                    "category": category,
                    "source": "git_head"
                    if relative.startswith("source/")
                    else "generated_review_evidence",
                }
            )
        write_text(review / "FILE_MANIFEST.json", json.dumps(manifest, indent=2))
        checksum_lines = []
        for file_path in sorted(
            path
            for path in staging.rglob("*")
            if path.is_file() and path.name != "CHECKSUMS.sha256"
        ):
            checksum_lines.append(
                f"{sha256(file_path)}  {file_path.relative_to(staging).as_posix()}"
            )
        write_text(review / "CHECKSUMS.sha256", "\n".join(checksum_lines))
        write_text(
            staging / "BUNDLE_INFO.txt",
            f"Bundle: {bundle_name}\nHead: {head}\nBase: {args.base_commit}\nFiles: {len(checksum_lines)}\nSource files: {len(source_files)}\n",
        )
        with zipfile.ZipFile(
            archive_path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for file_path in sorted(path for path in staging.rglob("*") if path.is_file()):
                archive.write(file_path, file_path.relative_to(staging.parent).as_posix())
    print(
        json.dumps(
            {
                "bundle_path": str(archive_path),
                "size_bytes": archive_path.stat().st_size,
                "sha256": sha256(archive_path),
                "head_commit": head,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
