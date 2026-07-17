#!/usr/bin/env sh
set -eu
python -m ruff check .
python -m ruff format --check .
python -m mypy src
python -m pytest
python -m build
python -m quant_system.cli status
git diff --check
