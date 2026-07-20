from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path
from shutil import which

import pytest

from scripts.build_review_bundle import (
    changed_rows,
    load_evidence_json,
    scan_paths_and_secrets,
    validate_bundle,
    validate_evidence,
)

pytestmark = pytest.mark.skipif(which("git") is None, reason="Git is required")


def evidence(
    head: str,
    base: str,
    *,
    network: bool = True,
    python: str = "3.12.13",
    exit_code: int = 0,
) -> dict[str, object]:
    checks: dict[str, dict[str, object]] = {
        name: {"command": name, "exit_code": exit_code, "status": "PASS"}
        for name in (
            "ruff_check",
            "ruff_format",
            "mypy",
            "build",
            "cli",
            "docker_compose_config",
            "git_diff_check",
        )
    }
    checks["pytest"] = {
        "command": "python -m pytest",
        "exit_code": exit_code,
        "status": "PASS",
        "passed": 4,
        "failed": 0,
        "skipped": 3,
        "coverage_percent": 100,
    }
    checks["clean_source"] = {
        "status": "PASS",
        "head_commit": head,
        "working_tree_clean": True,
    }
    return {
        "schema_version": 1,
        "project_name": "us-equity-quant-system",
        "head_commit": head,
        "base_commit": base,
        "branch": "main",
        "project_phase": "Phase 1",
        "round_id": "test-round",
        "round_status": "PASS WITH LIMITATIONS",
        "limitations": ["test limitation"],
        "verification_environment": {
            "type": "docker",
            "image": "test",
            "image_id": "sha256:test",
            "python_version": python,
            "network_disabled": network,
        },
        "checks": checks,
    }


def make_repo(tmp_path: Path) -> tuple[Path, str, str, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    for command in (
        ["git", "init", "-b", "main"],
        ["git", "config", "user.email", "a@b.invalid"],
        ["git", "config", "user.name", "test"],
    ):
        subprocess.run(command, cwd=repo, check=True, capture_output=True)
    (repo / ".gitattributes").write_text("* text eol=lf\n", encoding="utf-8")
    (repo / "VERSION").write_text("0.1.0\n")
    (repo / "src" / "quant_system").mkdir(parents=True)
    (repo / "src" / "quant_system" / "__init__.py").write_text(
        "CURRENT_PHASE='Phase 0'\nLIVE_TRADING_ENABLED=False\nPAPER_TRADING_ENABLED=False\n"
    )
    (repo / "src" / "quant_system" / "cli.py").write_text(
        "def main(argv): print("
        "'current phase: Phase 0\\npaper trading enabled: false\\n'"
        "'live trading enabled: false'); return 0\n"
    )
    report = repo / "docs" / "reports" / "round.md"
    report.parent.mkdir(parents=True)
    report.write_text("report\n")
    (repo / "tracked.txt").write_text("base\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    (repo / "tracked.txt").write_text("head\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "head"], cwd=repo, check=True, capture_output=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(evidence(head, base)))
    return repo, base, head, path


def test_changed_rows_handles_changes(tmp_path: Path) -> None:
    repo, base, head, _ = make_repo(tmp_path)
    assert any(
        row["file_path"] == "tracked.txt" and row["change_type"] == "M"
        for row in changed_rows(repo, base, head)
    )


def test_evidence_loader_accepts_utf8_with_and_without_bom_and_rejects_utf16(
    tmp_path: Path,
) -> None:
    payload = {"schema_version": 1, "head_commit": "a" * 40}
    plain = tmp_path / "plain.json"
    bom = tmp_path / "bom.json"
    utf16 = tmp_path / "utf16.json"
    plain.write_text(json.dumps(payload), encoding="utf-8")
    bom.write_bytes(b"\xef\xbb\xbf" + json.dumps(payload).encode("utf-8"))
    utf16.write_text(json.dumps(payload), encoding="utf-16")

    assert load_evidence_json(plain) == payload
    assert load_evidence_json(bom) == payload
    with pytest.raises(ValueError, match="UTF-16"):
        load_evidence_json(utf16)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda item: item.update({"head_commit": "bad"}), "commit"),
        (lambda item: item["checks"]["ruff_check"].update({"exit_code": 1}), "failed"),
        (
            lambda item: item["verification_environment"].update({"python_version": "3.11.9"}),
            "3.12",
        ),
        (
            lambda item: item["verification_environment"].update({"network_disabled": False}),
            "network",
        ),
    ],
)
def test_evidence_rejects_invalid_inputs(tmp_path: Path, mutate: object, message: str) -> None:
    _repo, base, head, _ = make_repo(tmp_path)
    payload = evidence(head, base)
    mutate(payload)  # type: ignore[operator]
    with pytest.raises(ValueError, match=message):
        validate_evidence(payload, head, base, "main")


def test_bundle_includes_evidence_and_validation_and_detects_tampering(
    tmp_path: Path,
) -> None:
    repo, base, head, evidence_path = make_repo(tmp_path)
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
            "--evidence-json",
            str(evidence_path),
        ],
        cwd=repo,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    bundle = next(output.glob("*.zip"))
    with zipfile.ZipFile(bundle) as archive:
        names = archive.namelist()
        assert any(name.endswith("review/EVIDENCE.json") for name in names)
        assert any(name.endswith("review/BUNDLE_VALIDATION_RESULT.md") for name in names)
        archive.extractall(tmp_path / "extract")
    extracted = next((tmp_path / "extract").iterdir())
    target = extracted / "source" / "tracked.txt"
    target.write_text("tampered\n")
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(tampered, "w") as archive:
        for path in extracted.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(extracted.parent))
    assert validate_bundle(tampered, head, repo).status == "FAIL"


def test_scan_rejects_restricted_files_but_allows_explicit_fixture(tmp_path: Path) -> None:
    (tmp_path / ".env.production").write_text("not-a-secret\n")
    (tmp_path / ".env.example").write_text("API_KEY=\n")
    (tmp_path / "state.sqlite3").write_bytes(b"SQLite format 3")
    (tmp_path / "prices.parquet").write_bytes(b"PAR1")
    (tmp_path / "run.log").write_text("diagnostic\n")
    fixture = tmp_path / "integrations" / "lean" / "data"
    fixture.mkdir(parents=True)
    (fixture / "synthetic-test.zip").write_bytes(b"fixture")

    findings = scan_paths_and_secrets(tmp_path)

    assert "forbidden-path:.env.production" in findings
    assert "forbidden-path:state.sqlite3" in findings
    assert "forbidden-path:prices.parquet" in findings
    assert "forbidden-path:run.log" in findings
    assert not any(".env.example" in finding for finding in findings)
    assert not any("synthetic-test.zip" in finding for finding in findings)


def test_successful_offline_cli_validation_has_no_error(tmp_path: Path) -> None:
    repo, base, head, evidence_path = make_repo(tmp_path)
    output = tmp_path / "out"
    script = Path(__file__).resolve().parents[2] / "scripts" / "build_review_bundle.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--round-id",
            "offline-cli-error",
            "--base-commit",
            base,
            "--output-dir",
            str(output),
            "--report-path",
            "docs/reports/round.md",
            "--round-start-time",
            "2026-07-17T00:00:00Z",
            "--evidence-json",
            str(evidence_path),
        ],
        cwd=repo,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    bundle = next(output.glob("*.zip"))
    validation = validate_bundle(bundle, head, repo)
    assert validation.status == "PASS"
    assert validation.checks["offline_cli"]["error"] == ""
