"""TEST_FIXTURE runtime controls; no installs, providers or existing environments."""
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('runtime_bootstrap_under_test', ROOT / 'scripts/bootstrap_daily_runtime.py')
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


def fixture_lock(tmp_path):
    root = tmp_path / 'TEST_FIXTURE_source'
    locks = root / 'runtime-locks'
    locks.mkdir(parents=True)
    metadata = json.loads(bootstrap.MANIFEST.read_text())
    for name in metadata['source_requirements']:
        (root / name).write_bytes((ROOT / name).read_bytes())
    for name in metadata['locks']:
        (locks / name).write_bytes((bootstrap.LOCK_ROOT / name).read_bytes())
    path = locks / bootstrap.MANIFEST.name
    path.write_text(json.dumps(metadata))
    return root, path, metadata


def test_lock_covers_exact_existing_profiles_and_only_official_artifacts():
    manifest = bootstrap.validate_lock()
    assert set(manifest['source_requirements']) == {'requirements-rules.txt', 'requirements-daily.txt', 'requirements-review.txt'}
    packages = {item['name']: item for item in manifest['packages']}
    assert packages['pydantic']['version'] == '2.13.5'
    assert packages['numpy']['version'] == '2.5.2'
    assert packages['jsonpath']['filename'].endswith('.tar.gz')
    assert not {'pyqlib', 'torch'} & packages.keys()


@pytest.mark.parametrize('which,reason', [('source', 'SOURCE_REQUIREMENTS_CHANGED'), ('lock', 'LOCK_HASH_MISMATCH')])
def test_changed_requirement_or_locked_artifact_does_not_silently_resolve(tmp_path, which, reason):
    root, path, manifest = fixture_lock(tmp_path)
    target = root / 'requirements-rules.txt' if which == 'source' else path.parent / next(iter(manifest['locks']))
    target.write_text(target.read_text() + '\n# TEST_FIXTURE_CHANGED\n')
    with pytest.raises(ValueError, match=reason):
        bootstrap.validate_lock(root, path)


@pytest.mark.parametrize('url', ['http://files.pythonhosted.org/a.whl', 'https://evil.example/a.whl',
                               'https://user:secret@files.pythonhosted.org/a.whl'])
def test_artifact_host_and_https_cannot_be_overridden(tmp_path, url):
    root, path, manifest = fixture_lock(tmp_path)
    manifest['packages'][0]['url'] = url
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='NON_PYPI_ARTIFACT_REJECTED'):
        bootstrap.validate_lock(root, path)


def test_create_refuses_existing_environment_without_touching_it(tmp_path, monkeypatch):
    target = tmp_path / 'TEST_FIXTURE_existing'
    target.mkdir()
    sentinel = target / 'pyvenv.cfg'
    sentinel.write_bytes(b'TEST_FIXTURE_DO_NOT_MODIFY')
    monkeypatch.setattr(bootstrap, 'native_identity', lambda _: {})
    monkeypatch.setattr(bootstrap.venv, 'EnvBuilder', lambda **_: pytest.fail('existing venv must not reach creation'))
    with pytest.raises(ValueError, match='DESTINATION_ALREADY_EXISTS'):
        bootstrap.main(['create', '--destination', str(target)])
    assert sentinel.read_bytes() == b'TEST_FIXTURE_DO_NOT_MODIFY'
    assert list(target.iterdir()) == [sentinel]


def test_failed_new_creation_preserves_failed_evidence_and_does_not_retry_or_clean(tmp_path, monkeypatch):
    target = tmp_path / 'TEST_FIXTURE_new'
    monkeypatch.setattr(bootstrap, 'native_identity', lambda _: {})
    def failed(_):
        raise OSError('TEST_FIXTURE_CREATION_FAILED')
    monkeypatch.setattr(bootstrap.venv, 'EnvBuilder', lambda **_: SimpleNamespace(create=failed))
    with pytest.raises(OSError):
        bootstrap.main(['create', '--destination', str(target)])
    summaries = list(target.glob('acceptance/*/summary.json'))
    assert len(summaries) == 1
    assert json.loads(summaries[0].read_text())['status'] == 'FAILED'
    assert target.exists()


def test_foreign_package_directory_is_rejected(monkeypatch, tmp_path):
    class Foreign:
        def locate_file(self, _):
            return tmp_path / 'TEST_FIXTURE_foreign_site'
    monkeypatch.setattr(bootstrap.importlib.metadata, 'distributions', lambda: [Foreign()])
    with pytest.raises(ValueError, match='FOREIGN_DISTRIBUTION_PATH'):
        bootstrap.installed_identity()


def test_environment_does_not_inherit_python_pip_or_business_configuration(monkeypatch):
    for key in ('PYTHONPATH', 'PYTHONHOME', 'PIP_INDEX_URL', 'VIRTUAL_ENV', 'STOCKFISH_DB_PATH', 'QLIB_DATA_DIR', 'TUSHARE_TOKEN'):
        monkeypatch.setenv(key, 'TEST_FIXTURE_UNTRUSTED')
    result = bootstrap.clean_environment()
    assert not {'PYTHONPATH', 'PYTHONHOME', 'PIP_INDEX_URL', 'VIRTUAL_ENV', 'STOCKFISH_DB_PATH', 'QLIB_DATA_DIR', 'TUSHARE_TOKEN'} & result.keys()
    assert result['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] == '1'


def test_command_timeout_preserves_partial_output_and_nonzero_exit(tmp_path):
    command = [sys.executable, '-I', '-B', '-c',
               'import time; print("TEST_FIXTURE_PARTIAL", flush=True); time.sleep(30)']
    with pytest.raises(RuntimeError, match='RUNTIME_STEP_FAILED_TEST_FIXTURE_timeout'):
        bootstrap.execute(command, tmp_path, tmp_path, 'TEST_FIXTURE_timeout', timeout=1)
    record = json.loads((tmp_path / 'TEST_FIXTURE_timeout.json').read_text())
    assert record['argv'] == command
    assert record['cwd'] == str(tmp_path)
    assert record['status'] == 'TIMED_OUT'
    assert record['exit_code'] != 0
    assert record['timeout_seconds'] == 1
    assert 'TEST_FIXTURE_PARTIAL' in record['stdout']
    assert 'TEST_FIXTURE_PARTIAL' in (tmp_path / 'TEST_FIXTURE_timeout.log').read_text()
