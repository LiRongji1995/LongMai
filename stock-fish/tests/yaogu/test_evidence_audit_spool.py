"""Original resolver equivalence and journal tamper boundaries; TEST_FIXTURE."""
import json

import pytest

from stockfish_core.yaogu.contracts import digest
from stockfish_core.yaogu.evidence_audit_spool import SpoolingEvidenceResolver
from stockfish_core.yaogu.history_artifacts import digest_json_stream
from stockfish_core.yaogu.visibility import EvidenceResolver
from tests.yaogu.test_theme_evolution_r1 import fixture_market


def resolvers(tmp_path, *, limit=32 * 1024 * 1024):
    bundle, cutoff, _ = fixture_market(7)
    kwargs = dict(cutoff=cutoff, coverage_type='TEST_FIXTURE',
                  sessions=bundle.sessions, observation_date=bundle.observation_date)
    old = EvidenceResolver(bundle.receipts, **kwargs)
    new = SpoolingEvidenceResolver(bundle.receipts, evidence_directory=tmp_path / 'audit',
                                  segment_bytes=limit, **kwargs)
    return bundle, old, new


def test_full_canonical_dependencies_and_all_guard_decisions_match(tmp_path):
    bundle, old, new = resolvers(tmp_path)
    for row in reversed(bundle.bars):
        kwargs = dict(instrument_id=row.instrument_id, session=row.session,
                      require_complete=True, require_session=True)
        assert new.resolve(row, **kwargs) == old.resolve(row, **kwargs)
        assert new.resolve(row, **kwargs) == old.resolve(row, **kwargs)  # Deduplicate.
    row = bundle.bars[0]
    assert new.resolve(row, instrument_id='600999.SH') == old.resolve(row, instrument_id='600999.SH')
    assert new.resolve(row, source_refs=('MISSING',)) == old.resolve(row, source_refs=('MISSING',))
    new.close()
    actual, expected = new.effective_dependencies(), old.effective_dependencies()
    assert len(actual['records']) == len(bundle.bars) == 24
    assert list(actual['records']) == expected['records']
    assert digest_json_stream(actual) == digest_json_stream(expected) == digest(expected)
    # Exercise CPython's fast JSON path as well as iterencode.
    assert json.loads(json.dumps(actual)) == expected
    assert new.audit_manifest()['row_count'] == 24
    assert new.audit_manifest()['complete_allowed_row_audit'] is True


def test_segmented_audit_keeps_every_row_and_deterministic_identity(tmp_path):
    bundle, old, new = resolvers(tmp_path, limit=2000)
    for row in bundle.bars:
        assert new.resolve(row).allowed
        assert old.resolve(row).allowed
    new.close()
    manifest = new.audit_manifest()
    row_files = {name: meta for name, meta in manifest['files'].items() if name.startswith('rows-')}
    assert len(row_files) > 1
    assert all(meta['bytes'] <= 2000 for meta in row_files.values())
    assert digest_json_stream(new.effective_dependencies()) == digest_json_stream(old.effective_dependencies())


@pytest.mark.parametrize('file_name', ['rows-000000.ndjson', 'record-index.ndjson'])
def test_sealed_journal_tampering_fails_even_if_record_index_is_live(tmp_path, file_name):
    bundle, _, new = resolvers(tmp_path)
    assert new.resolve(bundle.bars[0]).allowed
    new.close()
    path = tmp_path / 'audit' / file_name
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(ValueError, match='SEALED_JOURNAL_CHANGED'):
        digest_json_stream(new.effective_dependencies())


def test_live_record_tampering_and_wrong_row_key_fail(tmp_path):
    bundle, _, new = resolvers(tmp_path)
    row = bundle.bars[0]
    assert new.resolve(row).allowed
    new.flush()
    path = tmp_path / 'audit' / 'rows-000000.ndjson'
    raw = path.read_bytes()
    path.write_bytes(b'X' + raw[1:])
    with pytest.raises(ValueError, match='ORIGINAL_ROW_SHA_MISMATCH'):
        list(new.effective_dependencies()['records'])
    with pytest.raises(ValueError, match='ROW_KEY_MISMATCH'):
        new.rows['0' * 64] = row.model_dump(mode='json')


def test_complete_audit_cannot_be_disabled_or_overwrite_existing(tmp_path):
    _, _, new = resolvers(tmp_path)
    with pytest.raises(ValueError, match='COMPLETE_ROW_RECORDING_REQUIRED'):
        SpoolingEvidenceResolver([], evidence_directory=tmp_path / 'other',
                                 cutoff=new.cutoff, coverage_type='TEST_FIXTURE', record_rows=False)
    with pytest.raises(FileExistsError):
        SpoolingEvidenceResolver([], evidence_directory=tmp_path / 'audit',
                                 cutoff=new.cutoff, coverage_type='TEST_FIXTURE')
    new.close()
    with pytest.raises(TypeError, match='APPEND_ONLY'):
        del new.rows['0' * 64]


def test_empty_dependency_list_is_canonical_and_close_is_idempotent(tmp_path):
    _, old, new = resolvers(tmp_path)
    new.close()
    new.close()
    assert digest_json_stream(new.effective_dependencies()) == digest(old.effective_dependencies())
    assert json.loads(json.dumps(new.effective_dependencies())) == old.effective_dependencies()
