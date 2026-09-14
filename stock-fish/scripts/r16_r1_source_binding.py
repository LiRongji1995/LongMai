"""Bind an extracted git archive to its actual commit/tree and every file byte."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile


def create_binding(repository, commit, archive, source_root, output):
    repository, source_root = Path(repository).resolve(), Path(source_root).resolve()
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repository), *args]).decode().strip()
    resolved, tree = git('rev-parse', commit), git('rev-parse', commit+'^{tree}')
    # Independently recreate the same archive in memory. This connects bytes to
    # Git objects, rather than trusting a caller's commit label.
    expected = subprocess.check_output(['git', '-C', str(repository), 'archive', '--format=zip', resolved])
    if hashlib.sha256(expected).hexdigest() != hashlib.sha256(Path(archive).read_bytes()).hexdigest():
        raise ValueError('ARCHIVE_BYTES_DO_NOT_MATCH_GIT_COMMIT')
    files = {}
    with zipfile.ZipFile(archive) as zipped:
        for item in zipped.infolist():
            if item.is_dir():
                continue
            target = (source_root/item.filename).resolve()
            if not target.is_relative_to(source_root):
                raise ValueError('UNSAFE_ARCHIVE_PATH')
            value = hashlib.sha256(zipped.read(item)).hexdigest()
            if hashlib.sha256(target.read_bytes()).hexdigest() != value:
                raise ValueError('EXTRACTED_SOURCE_MISMATCH:'+item.filename)
            files[item.filename] = value
    binding = {'version': 'R16_R1_GIT_ARCHIVE_BINDING_V1', 'source_root': str(source_root),
        'commit': resolved, 'tree': tree, 'archive': str(Path(archive).resolve()),
        'archive_sha256': hashlib.sha256(expected).hexdigest(), 'files': files,
        'verification': 'RECREATED_GIT_ARCHIVE_EXACT_BYTES_AND_EVERY_EXTRACTED_FILE'}
    Path(output).write_text(json.dumps(binding, ensure_ascii=False, indent=2), encoding='utf-8')
    return binding


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for name in ('repository','commit','archive','source-root','output'):
        parser.add_argument('--'+name, required=True)
    args = parser.parse_args()
    create_binding(args.repository, args.commit, args.archive, args.source_root, args.output)
