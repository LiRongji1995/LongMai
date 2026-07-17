from __future__ import annotations

import subprocess
import sys
from pathlib import Path


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
        ],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    bundle = next(output.glob("*.zip"))
    assert bundle.stat().st_size > 0
