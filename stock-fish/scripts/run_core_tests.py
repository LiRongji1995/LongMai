"""Offline core suite, using the isolated review pytest installation if necessary."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
isolated = ROOT.parent / "review" / "test-tools"
if isolated.is_dir():
    sys.path.insert(0, str(isolated))
os.chdir(ROOT)
import pytest

raise SystemExit(pytest.main(sys.argv[1:] or ["tests", "-q"]))
