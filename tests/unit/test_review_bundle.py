from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from shutil import which
from typing import Any

import pytest

from scripts.build_review_bundle import (
    canonical_projection_bytes,
    changed_rows,
    load_evidence_json,
    scan_paths_and_secrets,
    validate_bundle,
    validate_command_evidence,
    validate_document_source,
    validate_evidence,
    validate_round_identity,
    validate_run_c_config_evidence,
    validate_run_c_review_artifacts,
)

pytestmark = pytest.mark.skipif(which("git") is None, reason="Git is required")


def evidence(
    head: str,
    base: str,
    *,
    network: bool = True,
    python: str = "3.12.13",
    exit_code: int = 0,
) -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {
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
        "command": "git clone --no-hardlinks --no-local . clean-source",
        "exit_code": exit_code,
        "status": "PASS",
        "head_commit": head,
        "origin_main_commit": head,
        "working_tree_clean": True,
    }
    return {
        "schema_version": 1,
        "project_name": "us-equity-quant-system",
        "head_commit": head,
        "origin_main_commit": head,
        "base_commit": base,
        "branch": "main",
        "project_phase": "Phase 1",
        "round_id": "test-round",
        "round_status": "PASS WITH LIMITATIONS",
        "limitations": ["test limitation"],
        "working_tree_clean": True,
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
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", head],
        cwd=repo,
        check=True,
    )
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(evidence(head, base)))
    return repo, base, head, path


def build_bundle(
    repo: Path,
    base: str,
    evidence_path: Path,
    output: Path,
    *,
    round_id: str = "test-round",
) -> subprocess.CompletedProcess[str]:
    script = Path(__file__).resolve().parents[2] / "scripts" / "build_review_bundle.py"
    return subprocess.run(
        [
            sys.executable,
            str(script),
            "--round-id",
            round_id,
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


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("missing_origin", "origin/main"),
        ("origin_behind", "origin/main"),
        ("origin_other", "origin/main"),
        ("non_main_branch", "branch or origin/main"),
        ("dirty", "working tree is not clean"),
        ("base_not_ancestor", "recorded base is not an ancestor"),
        ("forged_evidence_origin", "branch or origin/main"),
    ],
)
def test_bundle_requires_real_git_origin_main_and_clean_main_branch(
    tmp_path: Path, case: str, expected: str
) -> None:
    repo, base, head, evidence_path = make_repo(tmp_path)
    if case == "missing_origin":
        subprocess.run(
            ["git", "update-ref", "-d", "refs/remotes/origin/main"], cwd=repo, check=True
        )
    elif case == "origin_behind":
        subprocess.run(
            ["git", "update-ref", "refs/remotes/origin/main", base], cwd=repo, check=True
        )
    elif case == "origin_other":
        tree = subprocess.check_output(
            ["git", "rev-parse", "HEAD^{tree}"], cwd=repo, text=True
        ).strip()
        other = subprocess.check_output(
            ["git", "commit-tree", tree, "-p", head], cwd=repo, input="other\n", text=True
        ).strip()
        subprocess.run(
            ["git", "update-ref", "refs/remotes/origin/main", other], cwd=repo, check=True
        )
    elif case == "non_main_branch":
        subprocess.run(["git", "branch", "-m", "review"], cwd=repo, check=True)
    elif case == "dirty":
        (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    elif case == "base_not_ancestor":
        tree = subprocess.check_output(
            ["git", "rev-parse", "HEAD^{tree}"], cwd=repo, text=True
        ).strip()
        unrelated = subprocess.check_output(
            ["git", "commit-tree", tree], cwd=repo, input="unrelated\n", text=True
        ).strip()
        payload = evidence(head, unrelated)
        evidence_path.write_text(json.dumps(payload), encoding="utf-8")
        base = unrelated
    elif case == "forged_evidence_origin":
        subprocess.run(
            ["git", "update-ref", "refs/remotes/origin/main", base], cwd=repo, check=True
        )
        # The forged JSON retains HEAD as origin_main_commit; Git is authoritative.
    result = build_bundle(repo, base, evidence_path, tmp_path / "out")
    assert result.returncode != 0
    assert expected in result.stderr


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


def test_native_evidence_requires_native_checks_not_docker(tmp_path: Path) -> None:
    _repo, base, head, _ = make_repo(tmp_path)
    payload = evidence(head, base)
    payload["verification_environment"] = {
        "type": "native",
        "python_version": "3.12.10",
        "network_disabled": True,
    }
    checks = payload["checks"]
    del checks["docker_compose_config"]
    checks["native_build"] = {"command": "dotnet build", "exit_code": 0, "status": "PASS"}
    checks["native_build"].update({"dotnet_sdk_version": "10.0.302", "dll_sha256": "a" * 64})
    checks["native_run_c"] = {
        "command": "dotnet launcher --config $env:LEAN_CONFIG",
        "exit_code": 0,
        "status": "PASS",
        "filled_orders": 2,
        "order_sequence": ["buy", "sell"],
        "final_holdings": 0,
        "golden_orders": "PASS",
        "golden_statistics": "PASS",
        "deterministic_build_identity": "PASS",
        **{
            name: "b" * 64
            for name in (
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
        },
    }
    payload["verification_environment"].update(
        {"dotnet_sdk_version": "10.0.302", "lean_commit": "c" * 40}
    )

    validate_evidence(payload, head, base, "main")


def document_evidence(head: str, base: str) -> dict[str, Any]:
    payload = evidence(head, base)
    payload["verification_environment"] = {
        "type": "document",
        "python_version": "3.12.13",
        "network_disabled": True,
    }
    del payload["checks"]["docker_compose_config"]
    for name in (
        "relative_link_validation",
        "json_example_validation",
        "absolute_path_scan",
        "secret_scan",
        "cross_document_consistency",
    ):
        payload["checks"][name] = {
            "command": f"python -c validate_{name}",
            "exit_code": 0,
            "status": "PASS",
        }
    payload.update(
        {
            "created_documents": ["docs/test.md"],
            "document_sha256": {"docs/test.md": "a" * 64},
            "relative_link_validation": {"status": "PASS", "checked": 0, "failed": 0},
            "json_example_validation": {"status": "PASS", "checked": 0, "failed": 0},
            "absolute_path_scan": {"status": "PASS", "checked": 1, "failed": 0},
            "secret_scan": {"status": "PASS", "checked": 1, "failed": 0},
            "cross_document_consistency": {"status": "PASS", "checked": 1, "failed": 0},
            "python_checks": {"status": "PASS"},
            "git_checks": {"status": "PASS"},
        }
    )
    return payload


def test_document_evidence_requires_document_checks_not_native_or_docker(
    tmp_path: Path,
) -> None:
    _repo, base, head, _ = make_repo(tmp_path)
    payload = document_evidence(head, base)

    validate_evidence(payload, head, base, "main")

    del payload["checks"]["relative_link_validation"]
    with pytest.raises(ValueError, match="required checks"):
        validate_evidence(payload, head, base, "main")


@pytest.mark.parametrize(
    "unsafe_path",
    ("../outside.md", "/outside.md", "C:\\outside.md", "docs\\outside.md", "docs//x.md"),
)
def test_document_evidence_rejects_non_repository_paths(tmp_path: Path, unsafe_path: str) -> None:
    source = tmp_path / "source"
    source.mkdir()
    payload = document_evidence("a" * 40, "b" * 40)
    payload["created_documents"] = [unsafe_path]
    payload["document_sha256"] = {unsafe_path: "a" * 64}

    with pytest.raises(ValueError, match="document evidence paths"):
        validate_document_source(source, payload)


def test_document_evidence_rejects_boolean_counts(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    payload = document_evidence("a" * 40, "b" * 40)
    payload["relative_link_validation"]["checked"] = True

    with pytest.raises(ValueError, match="validation summary"):
        validate_document_source(source, payload)


DOCUMENTS = (
    "docs/map.md",
    "docs/contracts/data.md",
    "docs/contracts/boundaries.md",
    "docs/research/backtest.md",
    "docs/research/lifecycle.md",
    "docs/operations/runbook.md",
)


def document_round_evidence(head: str, base: str, hashes: dict[str, str]) -> dict[str, Any]:
    payload = document_evidence(head, base)
    payload.update(
        {
            "round_id": "test-document-round",
            "round_status": "PASS WITH LIMITATIONS",
            "limitations": ["contract baseline only"],
            "created_documents": list(DOCUMENTS),
            "document_sha256": hashes,
            "relative_link_validation": {"status": "PASS", "checked": 1, "failed": 0},
            "json_example_validation": {"status": "PASS", "checked": 8, "failed": 0},
            "absolute_path_scan": {"status": "PASS", "checked": 6, "failed": 0},
            "secret_scan": {"status": "PASS", "checked": 6, "failed": 0},
            "cross_document_consistency": {"status": "PASS", "checked": 1, "failed": 0},
            "python_checks": {"status": "PASS"},
            "git_checks": {"status": "PASS"},
        }
    )
    return payload


def test_document_source_validation_fails_closed_on_tampering(tmp_path: Path) -> None:
    source = tmp_path / "source"
    for index, relative in enumerate(DOCUMENTS):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        link = "[data](contracts/data.md)\n" if index == 0 else ""
        examples = 3 if index == 1 else 1
        blocks = "\n".join(f'```json\n{{"example": {item}}}\n```' for item in range(examples))
        path.write_text(f"# Document {index}\n\n{link}{blocks}\n", encoding="utf-8")
    hashes = {
        relative: hashlib.sha256((source / relative).read_bytes()).hexdigest()
        for relative in DOCUMENTS
    }
    payload = document_round_evidence("a" * 40, "b" * 40, hashes)

    validate_document_source(source, payload)

    (source / DOCUMENTS[0]).write_text("tampered\n", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA"):
        validate_document_source(source, payload)


def test_document_bundle_uses_generic_evidence_without_run_c_artifacts(tmp_path: Path) -> None:
    repo, _base, previous_head, evidence_path = make_repo(tmp_path)
    document = repo / "docs" / "test.md"
    document.write_text("# Contract baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "docs/test.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "document"], cwd=repo, check=True, capture_output=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    subprocess.run(["git", "update-ref", "refs/remotes/origin/main", head], cwd=repo, check=True)
    payload = document_evidence(head, previous_head)
    committed = subprocess.check_output(["git", "show", f"{head}:docs/test.md"], cwd=repo)
    payload["document_sha256"]["docs/test.md"] = hashlib.sha256(committed).hexdigest()
    evidence_path.write_text(json.dumps(payload), encoding="utf-8")

    result = build_bundle(repo, previous_head, evidence_path, tmp_path / "out")

    assert result.returncode == 0, result.stderr
    bundle = next((tmp_path / "out").glob("*.zip"))
    with zipfile.ZipFile(bundle) as archive:
        assert not any("RUN_C_" in name for name in archive.namelist())
    assert validate_bundle(bundle, head, repo).status == "PASS"


def test_document_bundle_rejects_uncovered_code_change(tmp_path: Path) -> None:
    repo, _base, previous_head, evidence_path = make_repo(tmp_path)
    document = repo / "docs" / "test.md"
    document.write_text("# Contract baseline\n", encoding="utf-8")
    source = repo / "src" / "quant_system" / "cli.py"
    source.write_text(source.read_text(encoding="utf-8") + "# unrelated\n", encoding="utf-8")
    subprocess.run(["git", "add", "docs/test.md", "src/quant_system/cli.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "document"], cwd=repo, check=True, capture_output=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    subprocess.run(["git", "update-ref", "refs/remotes/origin/main", head], cwd=repo, check=True)
    payload = document_evidence(head, previous_head)
    committed = subprocess.check_output(["git", "show", f"{head}:docs/test.md"], cwd=repo)
    payload["document_sha256"]["docs/test.md"] = hashlib.sha256(committed).hexdigest()
    evidence_path.write_text(json.dumps(payload), encoding="utf-8")

    result = build_bundle(repo, previous_head, evidence_path, tmp_path / "out")

    assert result.returncode != 0
    assert "does not cover the Base..Head change set" in result.stderr


def native_config_evidence(*, projection_sha256: str | None = None) -> dict[str, Any]:
    projection = {
        "algorithm_language": "CSharp",
        "environment": "backtesting",
        "live_mode": False,
    }
    return {
        "actual_config_sha256": "a" * 64,
        "machine_independent_projection": projection,
        "projection_sha256": projection_sha256
        or hashlib.sha256(canonical_projection_bytes(projection)).hexdigest(),
        "algorithm_type_name": "Phase01DeterministicSmokeTest",
        "algorithm_language": "CSharp",
        "environment": "backtesting",
        "live_mode": False,
        "brokerage_safety_status": "backtesting-safe",
        "transaction_handler_safety_status": "backtesting-safe",
        "data_folder_environment_variable": "LEAN_DATA_ROOT",
        "results_folder_environment_variable": "LEAN_RESULTS_ROOT",
        "lean_commit": "0269115d3cfbf691c7a0b7cfcc9ed412cafb91f6",
        "removed_or_replaced_path_fields": ["data-folder", "results-destination-folder"],
        "generation_command": (
            "$env:PYTHON312 scripts/project_run_c_config.py --input $env:LEAN_CONFIG "
            "--output $env:RUN_C_CONFIG_EVIDENCE"
        ),
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda item: item.update({"projection_sha256": "0" * 64}),
        lambda item: item.update({"projection_sha256": "0" * 63}),
        lambda item: item["machine_independent_projection"].update({"live_mode": True}),
        lambda item: item.update({"generation_command": ""}),
        lambda item: item.update(
            {"generation_command": "config projection generated from LEAN_CONFIG"}
        ),
        lambda item: item.pop("brokerage_safety_status"),
        lambda item: item.pop("transaction_handler_safety_status"),
    ],
)
def test_round_1b_config_evidence_fails_closed_for_projection_and_safety(mutate: object) -> None:
    config = native_config_evidence()
    mutate(config)  # type: ignore[operator]
    with pytest.raises(ValueError):
        validate_run_c_config_evidence(config, "a" * 64)


@pytest.mark.parametrize(
    "field,value",
    [
        ("project_name", "wrong-project"),
        ("project_phase", "Phase 0"),
        ("round_id", "wrong-round"),
        ("round_status", "PASS"),
        ("base_commit", "0" * 40),
    ],
)
def test_round_1b_identity_rejects_every_required_mismatch(field: str, value: str) -> None:
    payload = {
        "project_name": "us-equity-quant-system",
        "project_phase": "Phase 1",
        "round_id": "phase-01-round-01b-native-lean-foundation",
        "round_status": "PASS WITH LIMITATIONS",
        "base_commit": "ec7bba61c6d190c7e6624a467c4a45a6911c46e1",
    }
    payload[field] = value
    with pytest.raises(ValueError):
        validate_round_identity(payload)


def test_round_1b_requires_real_post_run_commands(tmp_path: Path) -> None:
    _repo, base, head, _ = make_repo(tmp_path)
    payload = evidence(head, base)
    payload.update(
        {
            "round_id": "phase-01-round-01b-native-lean-foundation",
            "base_commit": "ec7bba61c6d190c7e6624a467c4a45a6911c46e1",
            "commands": [],
        }
    )
    with pytest.raises(ValueError, match="command evidence"):
        validate_evidence(payload, head, payload["base_commit"], "main")


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
            "test-round",
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


def test_scan_rejects_concrete_absolute_paths_but_allows_generic_drive_literals(
    tmp_path: Path,
) -> None:
    (tmp_path / "concrete.txt").write_text("E:\\" + "QuantInfra\\Lean-src\\config.json\n")
    (tmp_path / "posix.txt").write_text("/" + "home/reviewer/run/result.json\n")
    (tmp_path / "generic.txt").write_text('assert "E:\\\\" not in value\n[A-Za-z]:\\\\\n')

    findings = scan_paths_and_secrets(tmp_path)

    assert "absolute-path:concrete.txt" in findings
    assert "absolute-path:posix.txt" in findings
    assert "absolute-path:generic.txt" not in findings


def test_successful_offline_cli_validation_has_no_error(tmp_path: Path) -> None:
    repo, base, head, evidence_path = make_repo(tmp_path)
    output = tmp_path / "out"
    script = Path(__file__).resolve().parents[2] / "scripts" / "build_review_bundle.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--round-id",
            "test-round",
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


def test_cli_rejects_round_id_that_does_not_match_evidence(tmp_path: Path) -> None:
    repo, base, _head, evidence_path = make_repo(tmp_path)

    result = build_bundle(
        repo,
        base,
        evidence_path,
        tmp_path / "out",
        round_id="different-round",
    )

    assert result.returncode != 0
    assert "round argument does not match evidence identity" in result.stderr


@pytest.mark.parametrize("round_id", ("../escape", "Bad_Round", "round/name"))
def test_evidence_rejects_unsafe_round_id(tmp_path: Path, round_id: str) -> None:
    _repo, base, head, _evidence_path = make_repo(tmp_path)
    payload = evidence(head, base)
    payload["round_id"] = round_id

    with pytest.raises(ValueError, match="safe lowercase slug"):
        validate_evidence(payload, head, base, "main")


def _round_1b_review_artifacts(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    review = tmp_path / "review"
    review.mkdir()
    source = Path(__file__).resolve().parents[2]
    orders = json.loads(
        (source / "integrations/lean/expected/phase01-golden-orders.json").read_text()
    )
    statistics = json.loads(
        (source / "integrations/lean/expected/phase01-golden-statistics.json").read_text()
    )
    raw = [
        {"status": "filled", "direction": "buy", "fillQuantity": 10.0, "fillPrice": 100.01},
        {"status": "filled", "direction": "sell", "fillQuantity": -10.0, "fillPrice": 97.73},
    ]
    (review / "RUN_C_RAW_ORDER_EVENTS.json").write_text(json.dumps(raw), encoding="utf-8")
    (review / "RUN_C_NORMALIZED_ORDERS.json").write_text(json.dumps(orders), encoding="utf-8")
    (review / "RUN_C_NORMALIZED_STATISTICS.json").write_text(
        json.dumps(statistics), encoding="utf-8"
    )
    summary = {
        "algorithm_status": "Completed",
        "runtime_error": "",
        "launcher_exit_code": 0,
        "processed_data_points": 22,
        "filled_orders": 2,
        "order_sequence": ["buy", "sell"],
        "final_holdings": 0,
        "final_cash": 977.20,
        "final_portfolio_value": 977.20,
        "raw_order_events_sha256": hashlib.sha256(
            (review / "RUN_C_RAW_ORDER_EVENTS.json").read_bytes()
        ).hexdigest(),
        "normalized_orders_sha256": hashlib.sha256(
            (review / "RUN_C_NORMALIZED_ORDERS.json").read_bytes()
        ).hexdigest(),
        "normalized_statistics_sha256": hashlib.sha256(
            (review / "RUN_C_NORMALIZED_STATISTICS.json").read_bytes()
        ).hexdigest(),
        "config_sha256": "a" * 64,
        "algorithm_dll_sha256": "b" * 64,
        "lean_commit": "0269115d3cfbf691c7a0b7cfcc9ed412cafb91f6",
        "run_start_time_utc": "2026-07-26T00:00:00Z",
        "run_end_time_utc": "2026-07-26T00:00:01Z",
        "duration_seconds": 1.0,
    }
    (review / "RUN_C_EXECUTION_SUMMARY.json").write_text(json.dumps(summary), encoding="utf-8")
    support = {
        "support_files": [
            {
                "lean_repository_relative_path": (
                    "Data/symbol-properties/symbol-properties-database.csv"
                ),
                "destination_role": "symbol-properties",
                "sha256": "c" * 64,
                "source_lean_commit": "0269115d3cfbf691c7a0b7cfcc9ed412cafb91f6",
            }
        ]
    }
    (review / "RUN_C_SUPPORT_DATA_EVIDENCE.json").write_text(json.dumps(support), encoding="utf-8")
    evidence = {
        "checks": {
            "native_run_c": {"config_sha256": "a" * 64},
            "native_build": {"dll_sha256": "b" * 64},
        }
    }
    return review, evidence


@pytest.mark.parametrize(
    "case",
    [
        "missing-orders",
        "missing-statistics",
        "tampered-orders",
        "tampered-statistics",
        "third-fill",
        "wrong-sequence",
        "wrong-price",
        "wrong-quantity",
        "holdings",
        "portfolio-value",
        "status",
        "runtime-error",
        "hash",
        "missing-support",
        "support-commit",
    ],
)
def test_round_1b_normalized_artifacts_fail_closed(tmp_path: Path, case: str) -> None:
    review, evidence_payload = _round_1b_review_artifacts(tmp_path)
    if case == "missing-orders":
        (review / "RUN_C_NORMALIZED_ORDERS.json").unlink()
    elif case == "missing-statistics":
        (review / "RUN_C_NORMALIZED_STATISTICS.json").unlink()
    elif case == "tampered-orders":
        path = review / "RUN_C_NORMALIZED_ORDERS.json"
        payload = json.loads(path.read_text())
        payload["orders"][0]["fillPrice"] = 1
        path.write_text(json.dumps(payload))
    elif case == "tampered-statistics":
        path = review / "RUN_C_NORMALIZED_STATISTICS.json"
        payload = json.loads(path.read_text())
        payload["final_cash"] = 1
        path.write_text(json.dumps(payload))
    elif case == "third-fill":
        path = review / "RUN_C_RAW_ORDER_EVENTS.json"
        payload = json.loads(path.read_text())
        payload.append(dict(payload[0]))
        path.write_text(json.dumps(payload))
    elif case in {
        "wrong-sequence",
        "wrong-price",
        "wrong-quantity",
        "holdings",
        "portfolio-value",
        "status",
        "runtime-error",
        "hash",
    }:
        path = review / "RUN_C_EXECUTION_SUMMARY.json"
        payload = json.loads(path.read_text())
        key, value = {
            "wrong-sequence": ("order_sequence", ["sell", "buy"]),
            "wrong-price": ("final_cash", 1),
            "wrong-quantity": ("filled_orders", 3),
            "holdings": ("final_holdings", 1),
            "portfolio-value": ("final_portfolio_value", 1),
            "status": ("algorithm_status", "Failed"),
            "runtime-error": ("runtime_error", "boom"),
            "hash": ("normalized_orders_sha256", "0" * 64),
        }[case]
        payload[key] = value
        path.write_text(json.dumps(payload))
    elif case == "missing-support":
        (review / "RUN_C_SUPPORT_DATA_EVIDENCE.json").unlink()
    else:
        path = review / "RUN_C_SUPPORT_DATA_EVIDENCE.json"
        payload = json.loads(path.read_text())
        payload["support_files"][0]["source_lean_commit"] = "0" * 40
        path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        validate_run_c_review_artifacts(
            review, Path(__file__).resolve().parents[2], evidence_payload
        )


@pytest.mark.parametrize(
    "case", ["orders-import", "statistics-no-call", "identity-hash-only", "limitations-boundary"]
)
def test_round_1b_command_and_limitation_evidence_fail_closed(case: str) -> None:
    commands = [
        {"name": "native_restore", "command": "dotnet restore x", "exit_code": 0, "status": "PASS"},
        {"name": "native_build", "command": "dotnet build x", "exit_code": 0, "status": "PASS"},
        {"name": "native_run_c", "command": "dotnet x", "exit_code": 0, "status": "PASS"},
        {"name": "normalizer", "command": "python normalize.py", "exit_code": 0, "status": "PASS"},
        {
            "name": "golden_orders",
            "command": (
                "python -c 'compare_semantic(phase01-golden-orders.json, "
                "RUN_C_NORMALIZED_ORDERS.json)'"
            ),
            "exit_code": 0,
            "status": "PASS",
        },
        {
            "name": "golden_statistics",
            "command": (
                "python -c 'compare_semantic(phase01-golden-statistics.json, "
                "RUN_C_NORMALIZED_STATISTICS.json)'"
            ),
            "exit_code": 0,
            "status": "PASS",
        },
        {
            "name": "deterministic_identity",
            "command": (
                "python -c 'x=algorithm-build-identity.json; "
                "y=deterministic_build_a_b_dll_sha256; assert actual != expected'"
            ),
            "exit_code": 0,
            "status": "PASS",
        },
    ]
    if case == "orders-import":
        commands[4]["command"] = (
            "python -c 'from scripts.normalize_lean_result import compare_semantic'"
        )
    elif case == "statistics-no-call":
        commands[5]["command"] = (
            "python -c 'phase01-golden-statistics.json "
            "RUN_C_NORMALIZED_STATISTICS.json compare_semantic'"
        )
    elif case == "identity-hash-only":
        commands[6]["command"] = "Get-FileHash dll"
    else:
        commands.pop()
    payload = {"round_id": "phase-01-round-01b-native-lean-foundation", "commands": commands}
    with pytest.raises(ValueError):
        validate_command_evidence(payload)
