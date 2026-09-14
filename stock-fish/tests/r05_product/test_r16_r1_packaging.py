"""TEST_FIXTURE: lossless inclusion and verification before final real packaging."""
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'scripts'))
from r16_r1_package_review import Packaging, policy, relative_name


def test_lossless_jsonl_and_checksum_detects_extracted_changes(tmp_path):
    source = tmp_path/'facts.jsonl'
    source.write_bytes('{"来源":"原文","value":null}\n'.encode())
    args = SimpleNamespace(repo_root=tmp_path, inclusion_spec=tmp_path/'spec.json',
        output_dir=tmp_path/'package', budget_seconds=30, max_source_file_bytes=100000,
        max_evidence_file_bytes=100000, max_bundle_bytes=100000)
    package = Packaging(args)
    package.final_commit, package.final_tree = 'TEST_FIXTURE_COMMIT', 'TEST_FIXTURE_TREE'
    package.copy(source, 'facts.jsonl', role='LOSSLESS_FACTS')
    package.controls('TEST_FIXTURE')
    zipped = tmp_path/'test.zip'
    package.zip_staging(zipped)
    extracted = tmp_path/'extracted'
    package.extract(zipped, extracted)
    assert package.verify(extracted)['status'] == 'PASS'
    assert (extracted/'facts.jsonl').read_bytes() == source.read_bytes()
    (extracted/'facts.jsonl').write_bytes(b'changed')
    with pytest.raises(ValueError, match='CHECKSUM_MISMATCH'):
        package.verify(extracted)


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'C:/absolute', 'a/../escape', 'NUL', 'a\\b'])
def test_portable_extraction_path_rejects_escape(name):
    with pytest.raises(ValueError):
        relative_name(name)


def test_runtime_and_private_files_rejected_but_jsonl_and_source_cache_retained():
    assert policy(Path('proof/facts.jsonl')) is None
    assert policy(Path('proof/profile.cprofile')) is None
    assert policy(Path('memory/cache/ttl_cache.py'), tracked_source=True) is None
    for name in ('.env', 'actual.sqlite3', 'actual.sqlite3-wal', 'provider_cache/result.json', '.venv/x.py', 'credentials.json'):
        assert policy(Path(name)) is not None
