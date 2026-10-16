"""Run offline tests using only the selected venv's third-party packages."""
import os
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]
bootstrap = runpy.run_path(str(ROOT / 'scripts/bootstrap_daily_runtime.py'))
bootstrap['installed_identity']()
os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
import pytest

if not Path(pytest.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()):
    raise RuntimeError('FOREIGN_PYTEST_REJECTED')
raise SystemExit(pytest.main(['-p', 'no:cacheprovider', *sys.argv[1:]]))
