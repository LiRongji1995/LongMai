"""Deterministic, conservative business dependency manifest; no installation.

All runtime Go/SQL sources are covered except read/display/operational adapters.
Tests, documentation and formatting of tests cannot invalidate business reuse.
Runtime business source bytes, locked dependencies, policies, conversion and
calendar changes do invalidate it. This is a hash, not a developer version label.
"""
import hashlib
import json
import pathlib

BUSINESS_DIRS = ('application/research', 'domain', 'evidence', 'facts', 'ports', 'strategies',
                 'infrastructure/native', 'infrastructure/calendar',
                 'infrastructure/legacy', 'infrastructure/buildidentity', 'infrastructure/provider')
FACTS_DIRS = ('application/research', 'domain', 'evidence', 'facts', 'ports',
              'infrastructure/native', 'infrastructure/calendar', 'infrastructure/legacy', 'infrastructure/provider')

# History readers call projection/codec and verification helpers in this
# package. Hashing history*.go alone missed changes to those actual dependencies.
# The storage scope is deliberately conservative. Saved-result display and the
# separate review journal do not participate in stock-fact calculation.
STORAGE_NONBUSINESS = {'readonly.go', 'readonly_history.go', 'review.go',
                       'progress.go', 'stats.go', 'queryplans.go'}

# The fixed critical-capture path uses V2 history-window math and imports the
# R05 context/coverage helpers for readiness. Their module-level frozen-spec
# checks require these three R05 documents; no private config directory is used.
WORKER_CONFIG_FILES = ('stage_aware_momentum_v2.yaml',
                       'r05_strategy_experiments.yaml',
                       'r05_completion_requirements.yaml',
                       'r05_source_lock.json')
CORE_NONRUNTIME_DIRS = {'tests', 'testdata', 'fixtures', 'fixture', 'data',
                       'datasets', 'cache', '__pycache__'}

def worker_config_paths(root):
    root = pathlib.Path(root)
    return [root.parent/'stock-fish/config'/name for name in WORKER_CONFIG_FILES]

def runtime_python_paths(root):
    """The fixed worker consumes these production sources and exact pins.

    This conservative scope also covers imported normalization/readiness
    helpers and their static JSON/YAML resources. Their source bytes participate;
    local data, fixtures and site packages do not.
    """
    root=pathlib.Path(root)
    core=root.parent/'stock-fish/stockfish_core'
    paths={p for p in core.rglob('*') if p.is_file()
           and p.suffix in {'.py', '.json', '.yaml', '.yml'}
           and not p.name.startswith('test_') and not p.name.endswith('_test.py')
           and not CORE_NONRUNTIME_DIRS.intersection(p.relative_to(core).parts)}
    paths.update(p for p in (root/'infrastructure/provider').glob('*.py')
                 if not p.name.startswith('test_') and not p.name.endswith('_test.py'))
    paths.update(p for p in (root.parent/'stock-fish').glob('requirements-*.txt')
                 if p.name in {'requirements-rules.txt','requirements-daily.txt'})
    paths.update(p for p in worker_config_paths(root) if p.is_file())
    return sorted(paths)

def worker_manifest(root):
    root=pathlib.Path(root)
    files=[{'path':p.relative_to(root.parent).as_posix(),
            'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in runtime_python_paths(root)]
    canonical=json.dumps(files,sort_keys=True,separators=(',',':')).encode()
    return {'contract':'FIXED_WORKER_SOURCE_MANIFEST_V2','sha256':hashlib.sha256(canonical).hexdigest(),'files':files}

def manifest(root, facts=False, toolchain=None):
    root = pathlib.Path(root)
    dirs = FACTS_DIRS if facts else BUSINESS_DIRS
    files = set()
    for directory in dirs:
        for path in (root / directory).rglob('*'):
            if path.is_file() and path.suffix in ('.go', '.sql', '.json', '.py') and not path.name.endswith(('_test.go','_test.py')) and not path.name.startswith('test_'):
                files.add(path)
    files.update(path for path in (root/'infrastructure/sqlitev2').glob('*')
                 if path.is_file() and path.suffix in ('.go', '.sql')
                 and not path.name.endswith('_test.go')
                 and path.name not in STORAGE_NONBUSINESS)
    if not facts:
        files.update(path for path in (root/'application').glob('*.go') if not path.name.endswith('_test.go'))
    files.update((root/'go.mod', root/'go.sum', root/'tools/build_identity.py'))
    entries = [{'path': path.relative_to(root).as_posix(),
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
               for path in sorted(files)]
    entries += [{'path':'../'+p.relative_to(root.parent).as_posix(),
                 'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
                for p in runtime_python_paths(root) if not p.is_relative_to(root)]
    canonical = json.dumps({'files':entries,'toolchain':toolchain}, sort_keys=True, separators=(',', ':')).encode()
    return {'contract': 'BUSINESS_SOURCE_MANIFEST_V2',
            'scope': 'FACT_ARITHMETIC' if facts else 'BUSINESS_IMPLEMENTATION',
            'sha256': hashlib.sha256(canonical).hexdigest(), 'files': entries, 'toolchain':toolchain}
