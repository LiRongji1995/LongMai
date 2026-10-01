"""Container allowlist contract; application behavior is tested separately."""
from pathlib import Path
import ast
import shlex

ROOT = Path(__file__).resolve().parents[2]


def test_docker_copies_every_literal_runtime_resource():
    copied = []
    for line in (ROOT / 'Dockerfile').read_text().splitlines():
        if line.startswith('COPY '):
            copied.extend(shlex.split(line)[1:-1])
    assert '.' not in copied
    required = set()
    for source in (ROOT / 'stockfish_core').rglob('*.py'):
        tree = ast.parse(source.read_text())
        for call in ast.walk(tree):
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == 'send_from_directory':
                if len(call.args) >= 2 and isinstance(call.args[1], ast.Constant):
                    name = call.args[1].value
                    if isinstance(name, str):
                        required.add(name)
    assert 'daily_research.html' in required
    for name in required:
        candidates = [p for folder in ('templates', 'static') for p in (ROOT / folder).rglob(name)]
        assert candidates, name
        assert any(any(p.relative_to(ROOT).as_posix().startswith(src.rstrip('/') + '/')
                       or p.relative_to(ROOT).as_posix() == src for src in copied) for p in candidates), name
    assert 'templates' not in (ROOT / '.dockerignore').read_text().splitlines()


def test_allowlisted_application_page_api_auth_and_optional_imports(tmp_path):
    """Emulate the source allowlist; this does not claim a Docker build passed."""
    import os
    import shutil
    import subprocess
    import sys
    app_root = tmp_path / 'app'
    app_root.mkdir()
    for line in (ROOT / 'Dockerfile').read_text().splitlines():
        if not line.startswith('COPY '):
            continue
        *sources, target = shlex.split(line)[1:]
        for source in sources:
            source_path = ROOT / source
            destination = app_root / target
            if target.endswith('/'):
                if source_path.is_file():
                    destination /= source_path.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source_path.is_dir():
                shutil.copytree(source_path, destination, ignore=shutil.ignore_patterns('__pycache__'))
            else:
                shutil.copyfile(source_path, destination)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('STOCKFISH_', 'QLIB_', 'TUSHARE_', 'OPENAI_')) and k != 'PYTHONPATH'}
    env.update(STOCKFISH_DB_PATH=str(tmp_path / 'TEST_FIXTURE.sqlite3'),
               STOCKFISH_STATE_DIR=str(tmp_path / 'TEST_FIXTURE_state'),
               QLIB_DATA_DIR=str(tmp_path / 'TEST_FIXTURE_no_market_data'),
               STOCKFISH_API_KEY='TEST_FIXTURE_KEY')
    code = '''
import sys
from app import app
core = app.extensions['stockfish_core']
try:
    client = app.test_client()
    page = client.get('/daily-research')
    assert page.status_code == 200 and '每日研究报告' in page.text
    assert '/api/daily-research/report?' in page.text
    assert client.get('/api/health').status_code == 401
    headers = {'Authorization': 'Bearer TEST_FIXTURE_KEY'}
    assert client.get('/api/health', headers=headers).status_code == 200
    report = client.get('/api/daily-research/observation?days=1', headers=headers)
    assert report.status_code == 200, report.text
    assert report.json['read_only'] is True
    assert report.json['current']['counts']['FOCUS'] is None
    assert not {'qlib', 'lightgbm', 'sklearn', 'openai'} & sys.modules.keys()
finally:
    core.close()
'''
    result = subprocess.run([sys.executable, '-c', code], cwd=app_root, env=env,
                            text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
