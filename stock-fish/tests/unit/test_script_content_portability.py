"""TEST_FIXTURE portability checks; no provider, service, or production state."""
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import codex_premarket as adapter


def replace(path, raw):
    other = path.with_name(path.name + '.TEST_FIXTURE-new-inode')
    other.write_bytes(raw)
    other.replace(path)


def test_archived_artifact_accepts_same_bytes_and_rejects_same_size_change(tmp_path):
    job = tmp_path / 'TEST_FIXTURE-job'
    job.mkdir()
    path = job / 'artifact.json'
    raw = b'{"TEST_FIXTURE":1}'
    path.write_bytes(raw)
    reference = adapter._reference(path)
    replace(path, raw)
    adapter._inside_job(reference, job)
    replace(path, raw.replace(b':1', b':2'))
    with pytest.raises(adapter.PremarketError, match='ARCHIVE_CONTENT_CHANGED'):
        adapter._inside_job(reference, job)
