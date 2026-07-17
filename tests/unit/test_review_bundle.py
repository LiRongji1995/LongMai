from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from shutil import which

import pytest

from scripts.build_review_bundle import changed_rows, derive_status

pytestmark = pytest.mark.skipif(which("git") is None, reason="Git is required by the review tool")


def test_changed_rows_handles_add_modify_delete_rename_and_unicode(tmp_path: Path) -> None:
    repo = tmp_path / "diff-repo"
    repo.mkdir()
    for command in (
        ["git", "init", "-b", "main"],
        ["git", "config", "user.email", "a@b.invalid"],
        ["git", "config", "user.name", "test"],
    ):
        subprocess.run(command, cwd=repo, check=True, capture_output=True)
    (repo / "old name.txt").write_text("old\n", encoding="utf-8")
    (repo / "remove.txt").write_text("remove\n", encoding="utf-8")
    (repo / "modify.txt").write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    (repo / "old name.txt").rename(repo / "新 名称.txt")
    (repo / "remove.txt").unlink()
    (repo / "modify.txt").write_text("after\n", encoding="utf-8")
    (repo / "added file.txt").write_text("added\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "change"], cwd=repo, check=True, capture_output=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    rows = changed_rows(repo, base, head)
    by_path = {row["file_path"]: row for row in rows}
    assert by_path["modify.txt"]["change_type"] == "M"
    assert by_path["added file.txt"]["change_type"] == "A"
    assert by_path["remove.txt"]["change_type"] == "D"
    renamed = next(row for row in rows if row["file_path"] == "新 名称.txt")
    assert renamed["change_type"].startswith("R") and renamed["old_path"] == "old name.txt"


def test_derive_status_rejects_missing_evidence_and_blocked_clean_source(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        derive_status(tmp_path)
    for name, content in {
        "TEST_RESULTS.md": "PASS\n",
        "COMMAND_RESULTS.txt": "python -m pytest\nPASS\n",
        "CLEAN_SOURCE_RESULT.md": "BLOCKED\n",
        "SECURITY_SCAN_RESULT.md": "PASS\n",
        "KNOWN_LIMITATIONS.md": "network blocked\n",
    }.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    assert derive_status(tmp_path)[0] == "BLOCKED"


def test_review_bundle_script_builds_git_object_snapshot(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Review Test"], cwd=repo, check=True)
    (repo / "VERSION").write_text("0.1.0\n", encoding="utf-8")
    report = repo / "docs" / "reports" / "round.md"
    report.parent.mkdir(parents=True)
    report.write_text("report\n", encoding="utf-8")
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    (repo / "tracked.txt").write_text("head\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "head"], cwd=repo, check=True, capture_output=True)
    output = tmp_path / "out"
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    for name, content in {
        "TEST_RESULTS.md": "PASS\n",
        "COMMAND_RESULTS.txt": "python -m pytest\nPASS\n",
        "CLEAN_SOURCE_RESULT.md": "PASS\n",
        "SECURITY_SCAN_RESULT.md": "PASS\n",
        "KNOWN_LIMITATIONS.md": "None\n",
    }.items():
        (evidence / name).write_text(content, encoding="utf-8")
    script = Path(__file__).resolve().parents[2] / "scripts" / "build_review_bundle.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--round-id",
            "test",
            "--base-commit",
            base,
            "--output-dir",
            str(output),
            "--report-path",
            "docs/reports/round.md",
            "--round-start-time",
            "2026-07-17T00:00:00Z",
            "--evidence-dir",
            str(evidence),
        ],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    bundle = next(output.glob("*.zip"))
    assert bundle.stat().st_size > 0
