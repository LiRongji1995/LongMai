"""Run offline tests using only the selected venv's third-party packages."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
import site
if sys.prefix == sys.base_prefix or site.ENABLE_USER_SITE:
    raise RuntimeError('ISOLATED_VIRTUAL_ENVIRONMENT_REQUIRED')
for path in sys.path:
    if 'site-packages' in Path(path).parts and not Path(path).resolve().is_relative_to(Path(sys.prefix).resolve()):
        raise RuntimeError('FOREIGN_SITE_PACKAGES_REJECTED')
os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
import pytest

if not Path(pytest.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()):
    raise RuntimeError('FOREIGN_PYTEST_REJECTED')
raise SystemExit(pytest.main(['-p', 'no:cacheprovider', *sys.argv[1:]]))
