"""Build a new portable native candidate directory; never installs or activates."""
import argparse
import hashlib
import json
import pathlib
import platform
import shutil
import subprocess

p = argparse.ArgumentParser()
p.add_argument('--go', required=True)
p.add_argument('--output', required=True, type=pathlib.Path)
a = p.parse_args()
root = pathlib.Path(__file__).resolve().parents[1]
repo = root.parent
out = a.output.resolve()
if out.exists():
    raise SystemExit('NEW_PACKAGE_DIRECTORY_REQUIRED')
out.mkdir(parents=True)
identity = subprocess.check_output(['python3', str(root/'tools/build.py'), '--go', a.go,
    '--command', 'stockfish', '--output', str(out/'stockfish')], text=True).strip()
shutil.copytree(root/'licenses', out/'licenses')
for source, name in [(repo/'THIRD_PARTY_NOTICES.md','THIRD_PARTY_NOTICES.md'),
                     (repo/'stock-fish/LICENSE','licenses/stock-fish-LICENSE.txt'),
                     (root/'LICENSES.md','LICENSES.md')]:
    shutil.copyfile(source, out/name)
(out/'resources').mkdir()
shutil.copyfile(root/'infrastructure/calendar/exchange_calendar_2026.json', out/'resources/exchange_calendar_2026.json')
shutil.copyfile(root/'interfaces/daily/config.example.json', out/'resources/config.example.json')
worker = out/'worker'
(worker/'python').mkdir(parents=True)
shutil.copyfile(root/'infrastructure/provider/daily_refresh_worker.py', worker/'daily_refresh_worker.py')
# Deterministic capture modules, without caches, data, credentials or weights.
# Kept as source for the optional worker; normal Go usage imports none of these.
shutil.copytree(repo/'stock-fish/stockfish_core', worker/'python/stockfish_core',
                ignore=shutil.ignore_patterns('__pycache__','*.pyc','.env','*.db'))
shutil.copyfile(repo/'stock-fish/requirements-rules.txt', worker/'requirements-rules.txt')
shutil.copyfile(repo/'stock-fish/requirements-daily.txt', worker/'requirements-daily.txt')
shutil.copyfile(root/'tools/install.sh', out/'install.sh')
shutil.copyfile(root/'tools/setup-worker.sh', worker/'setup-worker.sh')
for name in ('USER_GUIDE.md','MANUAL_ACCEPTANCE.md','SELFTEST_REPORT.md'):
    shutil.copyfile(root/'delivery'/name, out/name)
meta = {'protocol':'STOCKFISH_DAILY_V1','go_source_hash':identity,
        'host_os':platform.system(),'process_architecture':platform.machine(),
        'go_build':subprocess.check_output([a.go,'version'],text=True).strip(),
        'production_activation':'NOT_AUTHORIZED','manual_acceptance':'NOT_RUN_BY_USER'}
(out/'BUILD.json').write_text(json.dumps(meta,indent=2)+'\n')
for script in (out/'install.sh', worker/'setup-worker.sh'):
    script.chmod(0o755)
paths = sorted(path for path in out.rglob('*') if path.is_file())
(out/'checksums.sha256').write_text(''.join(hashlib.sha256(path.read_bytes()).hexdigest()+'  '+path.relative_to(out).as_posix()+'\n' for path in paths))
print(json.dumps(meta))
