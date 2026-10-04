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
                 'infrastructure/legacy', 'infrastructure/buildidentity')
FACTS_DIRS = ('application/research', 'domain', 'evidence', 'facts', 'ports',
              'infrastructure/native', 'infrastructure/calendar', 'infrastructure/legacy')

def manifest(root, facts=False, toolchain=None):
    root = pathlib.Path(root)
    dirs = FACTS_DIRS if facts else BUSINESS_DIRS
    files = set()
    for directory in dirs:
        for path in (root / directory).rglob('*'):
            if path.is_file() and path.suffix in ('.go', '.sql', '.json') and not path.name.endswith('_test.go'):
                files.add(path)
    files.update(path for path in (root/'infrastructure/sqlitev2').glob('history*.go') if not path.name.endswith('_test.go'))
    if not facts:
        files.update(path for path in (root/'application').glob('*.go') if not path.name.endswith('_test.go'))
    files.update((root/'go.mod', root/'go.sum', root/'tools/build_identity.py'))
    entries = [{'path': path.relative_to(root).as_posix(),
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
               for path in sorted(files)]
    canonical = json.dumps({'files':entries,'toolchain':toolchain}, sort_keys=True, separators=(',', ':')).encode()
    return {'contract': 'BUSINESS_SOURCE_MANIFEST_V1',
            'scope': 'FACT_ARITHMETIC' if facts else 'BUSINESS_IMPLEMENTATION',
            'sha256': hashlib.sha256(canonical).hexdigest(), 'files': entries, 'toolchain':toolchain}
