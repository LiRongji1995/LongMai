from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from shutil import which

import quant_system
from quant_system import cli

ROOT = Path(__file__).resolve().parents[2]


def test_package_metadata_and_safe_defaults() -> None:
    assert quant_system.__version__ == "0.1.0"
    assert quant_system.CURRENT_PHASE == "Phase 0"
    assert quant_system.LIVE_TRADING_ENABLED is False
    assert quant_system.PAPER_TRADING_ENABLED is False


def test_status_cli_is_offline_and_explicit(capsys: object) -> None:
    assert cli.main(["status"]) == 0
    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "project name: US Equity Quantitative Trading System" in output
    assert "current phase: Phase 0" in output
    assert "live trading enabled: false" in output
    assert "paper trading enabled: false" in output


def test_module_cli_runs() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "quant_system.cli", "status"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "paper trading enabled: false" in result.stdout


def test_dotenv_is_not_tracked_and_documents_exist() -> None:
    if which("git"):
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", ".env"],
            cwd=ROOT,
            capture_output=True,
            check=False,
        )
        assert tracked.returncode != 0
    else:
        assert ".env" in (ROOT / ".gitignore").read_text(encoding="utf-8")
        assert not (ROOT / ".env").exists()
    for path in (
        "README.md",
        "AGENTS.md",
        "docs/PROJECT_CHARTER.md",
        "docs/ROADMAP.md",
        "docs/ARCHITECTURE.md",
        "docs/RISK_POLICY.md",
        "docs/DATA_GOVERNANCE.md",
        "docs/TESTING_STRATEGY.md",
        "docs/SECURITY.md",
        "docs/GLOSSARY.md",
    ):
        assert (ROOT / path).is_file(), path
