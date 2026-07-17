$ErrorActionPreference = 'Stop'
$python = '.\.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw 'Run scripts/bootstrap.ps1 first.' }
& $python -m ruff check .
& $python -m ruff format --check .
& $python -m mypy src
& $python -m pytest
& $python -m build
& $python -m quant_system.cli status
git diff --check
