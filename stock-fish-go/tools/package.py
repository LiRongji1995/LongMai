"""Build a new portable native candidate directory; never installs or activates."""
import argparse
import hashlib
import json
import pathlib
import platform
import shutil
import subprocess
from build_identity import CORE_NONRUNTIME_DIRS, worker_config_paths

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
for source in (root/'infrastructure/provider').glob('*.py'):
    if not source.name.startswith('test_') and not source.name.endswith('_test.py'):
        shutil.copyfile(source,worker/source.name)
# Deterministic capture modules, without caches, data, credentials or weights.
# Kept as source for the optional worker; normal Go usage imports none of these.
shutil.copytree(repo/'stock-fish/stockfish_core', worker/'python/stockfish_core',
                ignore=shutil.ignore_patterns(*sorted(CORE_NONRUNTIME_DIRS), '*.pyc','.env','*.db'))
# Preserve each public frozen file byte-for-byte at the existing modules'
# ROOT/config relative lookup. Missing required configuration fails packaging;
# the portable worker must not depend on the history-window silent fallback.
(worker/'python/config').mkdir()
for source in worker_config_paths(root):
    shutil.copyfile(source, worker/'python/config'/source.name)
shutil.copyfile(repo/'stock-fish/requirements-rules.txt', worker/'requirements-rules.txt')
shutil.copyfile(repo/'stock-fish/requirements-daily.txt', worker/'requirements-daily.txt')
shutil.copyfile(root/'tools/install.sh', out/'install.sh')
shutil.copyfile(root/'tools/setup-worker.sh', worker/'setup-worker.sh')
# Each candidate carries the guide and verification boundaries of its own
# source generation. The previous daily package's PASS report is historical.
for source, name in (('FOUR_STAGE_USER_GUIDE.md','USER_GUIDE.md'),
                     ('FOUR_STAGE_MANUAL_ACCEPTANCE.md','MANUAL_ACCEPTANCE.md'),
                     ('FOUR_STAGE_REPORT.md','SELFTEST_REPORT.md')):
    shutil.copyfile(root/'delivery'/source, out/name)
meta = {'protocol':'STOCKFISH_DAILY_V1','go_source_hash':identity,
        'host_os':platform.system(),'process_architecture':platform.machine(),
        'go_build':subprocess.check_output([a.go,'version'],text=True).strip(),
        'production_activation':'NOT_AUTHORIZED','manual_acceptance':'NOT_RUN_BY_USER',
        'verification_report':'SELFTEST_REPORT.md',
        'build_status':'BUILT; RUNTIME_ACCEPTANCE_REQUIRES_SEPARATE_EVIDENCE'}
source_manifest=json.loads((out/'stockfish.identity.json').read_text())
meta['business_implementation_id']=source_manifest['business']['sha256']
meta['facts_implementation_id']=source_manifest['facts']['sha256']
meta['fixed_worker_source_manifest']=source_manifest['fixed_worker']
meta['binary_sha256']=hashlib.sha256((out/'stockfish').read_bytes()).hexdigest()
meta['dependencies']=subprocess.check_output([a.go,'version','-m',str(out/'stockfish')],text=True)
(out/'BUILD.json').write_text(json.dumps(meta,indent=2)+'\n')
for script in (out/'install.sh', worker/'setup-worker.sh'):
    script.chmod(0o755)
paths = sorted(path for path in out.rglob('*') if path.is_file())
(out/'checksums.sha256').write_text(''.join(hashlib.sha256(path.read_bytes()).hexdigest()+'  '+path.relative_to(out).as_posix()+'\n' for path in paths))
print(json.dumps(meta))
