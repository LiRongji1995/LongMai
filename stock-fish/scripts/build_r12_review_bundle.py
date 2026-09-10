"""Build review source bytes from FINAL COMMIT only, after verified push."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[2]


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--evidence-dir', type=Path)
    args = parser.parse_args(argv)
    commit = git('rev-parse', 'HEAD').decode().strip()
    origin_main = git('rev-parse', 'origin/main').decode().strip()
    remote_ref = git('ls-remote', 'origin', 'refs/heads/main').decode().split()
    if len(remote_ref) != 2 or remote_ref[1] != 'refs/heads/main':
        raise SystemExit('VERIFIED_ORIGIN_MAIN_UNAVAILABLE')
    remote_head = remote_ref[0]
    if not commit == origin_main == remote_head:
        raise SystemExit('FINAL_COMMIT_NOT_EQUAL_TO_VERIFIED_ORIGIN_MAIN')
    status = git('status', '--porcelain').decode()
    if status.strip():
        raise SystemExit('WORKTREE_NOT_CLEAN_AFTER_FINAL_COMMIT')
    provenance = {'final_commit': commit, 'origin_main': origin_main, 'remote_main': remote_head,
        'parent_commit': git('rev-parse', commit + '^').decode().strip(),
        'tree_hash': git('rev-parse', commit + '^{tree}').decode().strip(),
        'origin': git('remote', 'get-url', 'origin').decode().strip(),
        'git_status': git('status', '--short', '--branch').decode(),
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'source_policy': 'git show <final_commit>:<path>; never working-tree bytes',
        'evidence_policy': 'Private runtime summaries separately identified; not commit source bytes'}
    source_prefixes = ('stock-fish/stockfish_core/', 'stock-fish/tests/', 'stock-fish/config/',
        'stock-fish/scripts/', 'stock-fish/docs/', 'stock-fish/static/')
    root_files = {'AGENTS.md', 'README.md', 'DEPLOYMENT.md', 'ENGINEERING_BASELINE.md',
        'Start-StockFish.ps1','Stop-StockFish.ps1','Logs-StockFish.ps1','requirements-windows.lock.txt',
        'stock-fish/app.py','stock-fish/wsgi.py','stock-fish/AGENTS.md','stock-fish/requirements-dev.txt',
        'LICENSE', 'THIRD_PARTY_NOTICES.md', 'stock-fish/LICENSE', 'stock-fish/ENGINEERING_BASELINE.md'}
    paths = [p.decode() for p in git('ls-tree','-r','--name-only','-z',commit).split(b'\0') if p]
    manifest = {}
    evidence_manifest = {}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, 'x', compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in paths:
            if path in root_files or path.startswith(source_prefixes):
                content = git('show', commit + ':' + path)
                manifest[path] = {'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content),
                                  'git_object': git('rev-parse', commit + ':' + path).decode().strip()}
                bundle.writestr('source/' + path, content)
        bundle.writestr('provenance.json', json.dumps(provenance, ensure_ascii=False, indent=2))
        bundle.writestr('source-manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        if args.evidence_dir:
            for path in sorted(args.evidence_dir.rglob('*')):
                if (path.is_file() and path.suffix.lower() in {'.json','.md','.xml','.svg','.png','.txt','.csv'}
                        and path.name not in {'market-input.json','signal-run.json'} and path.stat().st_size < 5_000_000):
                    relative = path.relative_to(args.evidence_dir).as_posix()
                    content = path.read_bytes()
                    bundle.writestr('runtime-evidence/' + relative, content)
                    evidence_manifest[relative] = {'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content),
                                                   'source_kind': 'PRIVATE_RUNTIME_EVIDENCE_NOT_COMMIT_SOURCE'}
        bundle.writestr('runtime-evidence-manifest.json', json.dumps(evidence_manifest, ensure_ascii=False, indent=2))
    result = {'final_commit': commit, 'bundle': str(args.output.resolve()),
        'sha256': _file_sha256(args.output),
        'bytes': args.output.stat().st_size, 'commit_source_files': len(manifest)}
    args.output.with_suffix(args.output.suffix + '.sha256.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))


def _file_sha256(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


if __name__ == '__main__':
    main()
