"""Original bytes and full dated scope for the native session stream."""
from datetime import timedelta

import pytest

from stockfish_core.yaogu.history_artifacts import digest_json_stream
from stockfish_core.yaogu.research_context_artifacts import SourceArtifactReader, RETURN_BASIS
from stockfish_core.yaogu.theme_evolution import ThemeMarketSession
from stockfish_core.yaogu.theme_market_stream import SourceMarketStream
from tests.yaogu.test_research_context_artifacts import capability


def test_stream_matches_authenticated_full_pack_without_compressing_calendar(tmp_path):
    bundle, cutoff, reader, _ = capability(tmp_path)
    days = bundle.sessions[-5:]
    pack = reader.build_market_pack(bundle, cutoff=cutoff, sessions=days, return_basis=RETURN_BASIS)
    stream = SourceMarketStream.from_reader(reader, bundle, cutoff=cutoff, sessions=days)
    stream.validate_binding(bundle, cutoff)
    assert stream.calendar == tuple(str(day) for day in bundle.sessions)
    assert stream.available_sessions == tuple(str(day) for day in days)
    for panel in pack['sessions']:
        expected = ThemeMarketSession.model_validate(panel).model_dump(mode='json')
        assert stream.panel(panel['session']).model_dump(mode='json') == expected
        assert stream.descriptor['panels'][panel['session']]['panel_hash'] == digest_json_stream(expected)
    descriptor = stream.descriptor
    descriptor['available_sessions'].clear()
    assert len(stream.available_sessions) == len(stream.descriptor['available_sessions']) == 5
    assert stream.panel(bundle.sessions[0]) is None


def test_missing_parent_keeps_true_calendar_gap_unknown(tmp_path):
    bundle, cutoff, reader, paths = capability(tmp_path)
    day = str(bundle.sessions[-3])
    receipts = [row for row in reader.receipts if not (row.dataset == 'master_all' and str(row.parameters.get('date')) == day)]
    reader = SourceArtifactReader(receipts, paths)
    stream = SourceMarketStream(reader, bundle, cutoff=cutoff, sessions=bundle.sessions[-5:])
    assert stream.panel(day) is None
    assert day in stream.calendar and day in stream.requested_sessions and day not in stream.available_sessions
    assert stream.descriptor['missing_sessions'][day]['status'] == 'UNKNOWN'
    assert len(stream.calendar) == len(bundle.sessions)


def test_late_capture_is_unknown_and_cannot_be_used_at_old_cutoff(tmp_path):
    bundle, cutoff, reader, paths = capability(tmp_path)
    late = [row.model_copy(update={'first_seen_at': cutoff + timedelta(seconds=1),
                                   'ingested_at': cutoff + timedelta(seconds=1)})
            for row in reader.receipts]
    reader = SourceArtifactReader(late, paths)
    stream = SourceMarketStream(reader, bundle, cutoff=cutoff, sessions=bundle.sessions[-5:])
    assert stream.available_sessions == ()
    assert all('CAPTURE_AFTER_CUTOFF' in row['reason_codes'][0]
               for row in stream.descriptor['missing_sessions'].values())


def test_stream_original_byte_tampering_and_binding_change_fail(tmp_path):
    bundle, cutoff, reader, paths = capability(tmp_path)
    stream = SourceMarketStream(reader, bundle, cutoff=cutoff, sessions=bundle.sessions[-5:])
    day = stream.available_sessions[0]
    parent = stream.descriptor['panels'][day]['source_refs'][0]
    paths[parent].write_bytes(paths[parent].read_bytes() + b' ')
    with pytest.raises(ValueError, match='SHA_MISMATCH'):
        stream.panel(day)
    with pytest.raises(ValueError, match='INPUT_BINDING_MISMATCH'):
        stream.validate_binding(bundle, cutoff + timedelta(seconds=1))
    changed = bundle.model_copy(update={'coverage_type': 'PROSPECTIVE_CAPTURED'})
    with pytest.raises(ValueError, match='INPUT_BINDING_MISMATCH'):
        stream.validate_binding(changed, cutoff)


@pytest.mark.parametrize('kind', ['duplicate', 'reversed', 'future', 'unsupported_basis'])
def test_bad_requested_scope_never_becomes_empty_ready(tmp_path, kind):
    bundle, cutoff, reader, _ = capability(tmp_path)
    days, basis = list(bundle.sessions[-5:]), RETURN_BASIS
    if kind == 'duplicate':
        days.append(days[-1])
    elif kind == 'reversed':
        days.reverse()
    elif kind == 'future':
        days.append(days[-1] + timedelta(days=1))
    else:
        basis = 'UNVERIFIED_BASIS'
    with pytest.raises(ValueError):
        SourceMarketStream(reader, bundle, cutoff=cutoff, sessions=days, return_basis=basis)


def test_receipt_or_internal_descriptor_changes_are_rejected(tmp_path):
    bundle, cutoff, reader, _ = capability(tmp_path)
    stream = SourceMarketStream(reader, bundle, cutoff=cutoff, sessions=bundle.sessions[-5:])
    stream._descriptor['available_sessions'].clear()
    with pytest.raises(ValueError, match='DESCRIPTOR_CHANGED'):
        stream.panel(bundle.sessions[-1])
    stream = SourceMarketStream(reader, bundle, cutoff=cutoff, sessions=bundle.sessions[-5:])
    parent = reader.receipts[0]
    reader._receipts[parent.receipt_id] = parent.model_copy(update={'status': 'PARTIAL'})
    with pytest.raises(ValueError, match='SOURCE_RECEIPT_CHANGED'):
        stream.validate_binding(bundle, cutoff)
