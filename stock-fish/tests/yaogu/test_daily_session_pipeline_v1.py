"""Real original-byte readers, synthetic TEST_FIXTURE only; no business DB."""
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
from pathlib import Path

import pytest

from scripts import codex_daily_session_v1 as pipeline
from stockfish_core.yaogu import theme_evolution_streaming_v2 as reference
from stockfish_core.yaogu.contracts import SourceReceipt
from stockfish_core.yaogu.history_artifacts import digest_json_stream, digest_market_input
from stockfish_core.yaogu.research_context_artifacts import SourceArtifactReader
from stockfish_core.yaogu.theme_market_stream import SourceMarketStream
from tests.yaogu.test_theme_evolution_r1 import fixture_market


LIMITS = pipeline.PipelineLimits(max_seconds=120, max_read_bytes=64 * pipeline.MIB,
    max_output_bytes=32 * pipeline.MIB, max_rss_bytes=512 * pipeline.MIB)


@pytest.fixture(autouse=True)
def forbid_unrequested_io(monkeypatch):
    import socket
    import sqlite3
    from stockfish_core.yaogu import strategy_experiments

    def forbidden(*args, **kwargs):
        raise AssertionError('TEST_FIXTURE_NETWORK_DB_SELECTOR_FORBIDDEN')

    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    monkeypatch.setattr(strategy_experiments, 'run_short_horizon_strategy', forbidden)


def source_fixture(root, days=6, missing=None):
    root.mkdir()
    bundle, cutoff, pack = fixture_market(days)
    receipts, paths = [], {}
    for panel in pack['sessions']:
        day = panel['session']
        rows, masters = [], []
        for bar in panel['rows']:
            symbol, exchange = bar['instrument_id'].split('.')
            code = exchange.lower() + '.' + symbol
            masters.append({'code': code, 'code_name': 'TEST_FIXTURE', 'tradeStatus': '1'})
            rows.append(dict(code=code, date=day, adjustflag='3', tradestatus='1',
                amount=bar['amount_cny'], volume=bar['volume_shares'], open=bar['open'],
                high=bar['high'], low=bar['low'], close=bar['close'], preclose=bar['reference_prev_close']))
        for dataset, values in (('market_daily', rows), ('master_all', masters)):
            if missing == (day, dataset):
                continue
            rid = 'TEST_FIXTURE_' + dataset + '_' + day
            raw = json.dumps(dict(status='COMPLETE', pagination_complete=True,
                field_version='BAOSTOCK_00.9.30_R03_V1', first_seen_at=panel['clocks']['first_seen_at'],
                rows=values), sort_keys=True).encode()
            path = root / (rid + '.json')
            path.write_bytes(raw)
            paths[rid] = path
            receipts.append(SourceReceipt(receipt_id=rid, provider='baostock', dataset=dataset,
                content_hash=hashlib.sha256(raw).hexdigest(), field_version='BAOSTOCK_00.9.30_R03_V1',
                row_count=len(values), status='COMPLETE', pagination_complete=True,
                parameters={'date': day}, business_sessions=[day], **panel['clocks']))
    return bundle, cutoff, SourceArtifactReader(receipts, paths), paths


def run(root, bundle, cutoff, reader, **kwargs):
    return pipeline.run_daily_session_pipeline(bundle, reader=reader, cutoff=cutoff,
        directory=root, limits=kwargs.pop('limits', LIMITS), **kwargs)


@pytest.mark.parametrize('mode', ['DATED_ONLY', 'CURRENT_COHORT_BACKVIEW'])
def test_full_67_session_original_reader_complete_v2_differential(tmp_path, mode):
    bundle, cutoff, reader, paths = source_fixture(tmp_path / 'raw', 67)
    frozen = digest_market_input(bundle)
    before = {str(path): (path.read_bytes(), pipeline._fp(path.stat())) for path in paths.values()}
    old_globals = dict(reference.run_theme_evolution_stream.__globals__)
    original = SourceMarketStream(reader, bundle, cutoff=cutoff, sessions=bundle.sessions)
    expected = reference.run_theme_evolution_stream(bundle, cutoff=cutoff, market_stream=original,
        evidence_directory=tmp_path / 'reference-evidence', membership_mode=mode,
        output_history=True, history_scope='LATEST_67')
    actual = run(tmp_path / 'new', bundle, cutoff, reader, membership_mode=mode)
    result, metrics = actual['theme'], actual['metrics']
    for key in ('items', 'effective_input_hash', 'source_refs', 'computed_exchange_sessions',
                'source_input_hash', 'membership_mode', 'status', 'market_edge', 'parameter_status',
                'historical_membership_verified', 'official_pit_validation_countable'):
        assert result[key] == expected[key], key
    assert result['context_content_hash'] != expected['context_content_hash']
    assert result['implementation_version'] == pipeline.VERSION
    assert actual['manifest']['window_status'] == 'COMPLETE_67_AXIS'
    assert metrics['source_session_projections'] == metrics['panels_consumed'] == 67
    assert metrics['source_payload_decodes'] == 134
    assert metrics['returned_read_bytes']['source_projection'] == sum(path.stat().st_size for path in paths.values())
    assert metrics['returned_read_bytes']['evidence_journal'] > 0
    assert actual['manifest']['restorable_source_capability'] is False
    assert actual['manifest']['historical_pit_upgraded'] is False
    assert digest_market_input(bundle) == frozen
    assert reference.run_theme_evolution_stream.__globals__ == old_globals
    assert before == {str(path): (path.read_bytes(), pipeline._fp(path.stat())) for path in paths.values()}


def test_short_axis_remains_unknown_without_padding_and_gap_is_preserved(tmp_path):
    day = str(fixture_market(6)[0].sessions[-2])
    bundle, cutoff, reader, _ = source_fixture(tmp_path / 'raw', missing=(day, 'master_all'))
    actual = run(tmp_path / 'new', bundle, cutoff, reader)
    assert actual['manifest']['window_status'] == 'UNKNOWN_SHORT_CALENDAR'
    assert len(actual['theme']['computed_exchange_sessions']) == 6
    assert day in actual['manifest']['missing_sessions']
    assert actual['manifest']['missing_sessions'][day]['status'] == 'UNKNOWN'
    assert actual['metrics']['source_payload_decodes'] == 10
    assert actual['metrics']['source_session_projections'] == 5
    original = SourceMarketStream(reader, bundle, cutoff=cutoff, sessions=bundle.sessions)
    expected = reference.run_theme_evolution_stream(bundle, cutoff=cutoff, market_stream=original,
        evidence_directory=tmp_path / 'reference', history_scope='LATEST_67')
    assert actual['theme']['items'] == expected['items']
    assert actual['theme']['effective_input_hash'] == expected['effective_input_hash']


def test_late_sources_remain_unknown_and_never_receive_past_capability(tmp_path):
    bundle, cutoff, reader, paths = source_fixture(tmp_path / 'raw')
    receipts = [row.model_copy(update={'first_seen_at': cutoff + timedelta(seconds=1),
        'ingested_at': cutoff + timedelta(seconds=1)}) for row in reader.receipts]
    reader = SourceArtifactReader(receipts, paths)
    actual = run(tmp_path / 'new', bundle, cutoff, reader)
    assert actual['manifest']['available_sessions'] == []
    assert len(actual['manifest']['missing_sessions']) == 6
    assert actual['metrics']['source_payload_decodes'] == 0
    assert all(row['stage'] == 'UNKNOWN' for row in actual['theme']['items'])


@pytest.mark.parametrize('kind', ['raw_bytes', 'same_bytes_replaced', 'spool', 'bundle', 'borrowed_panel', 'borrowed_row'])
def test_inflight_change_fails_without_publishing_success(tmp_path, monkeypatch, kind):
    bundle, cutoff, reader, paths = source_fixture(tmp_path / 'raw')
    original = pipeline._ScopeStream.panel
    changed = False

    def mutate(scope, day):
        nonlocal changed
        panel = original(scope, day)
        if not changed:
            changed = True
            if kind == 'raw_bytes':
                path = next(iter(paths.values()))
                path.write_bytes(path.read_bytes() + b' ')
            elif kind == 'same_bytes_replaced':
                path = next(iter(paths.values()))
                replacement = path.with_suffix('.replacement')
                replacement.write_bytes(path.read_bytes())
                replacement.replace(path)
            elif kind == 'spool':
                path = Path(scope._spools[str(bundle.sessions[-1])]['path'])
                path.write_bytes(path.read_bytes() + b' ')
            elif kind == 'bundle':
                object.__setattr__(bundle, 'observation_date', bundle.sessions[-2])
            elif kind == 'borrowed_panel':
                # Keep an ordinary borrowed object alive, then change a nested
                # field after the consumer's own immediate panel hash check.
                scope._test_borrow = panel
            else:
                scope._test_row = panel.rows[0]
        elif kind == 'borrowed_panel' and hasattr(scope, '_test_borrow'):
            object.__setattr__(scope._test_borrow, 'status', 'PARTIAL')
        elif kind == 'borrowed_row' and hasattr(scope, '_test_row'):
            object.__setattr__(scope._test_row, 'quality', 'INVALID')
        return panel

    monkeypatch.setattr(pipeline._ScopeStream, 'panel', mutate)
    with pytest.raises(ValueError):
        run(tmp_path / 'new', bundle, cutoff, reader)
    assert not (tmp_path / 'new' / 'manifest.json').exists()
    assert (tmp_path / 'new' / 'facts').exists()  # Failed owned evidence is preserved.


@pytest.mark.parametrize('field,value', [('max_output_bytes', 1), ('max_read_bytes', 1),
                                        ('max_rss_bytes', 1), ('max_seconds', 0.000001)])
def test_explicit_resource_limit_refuses_and_keeps_actual_failure(tmp_path, field, value):
    bundle, cutoff, reader, _ = source_fixture(tmp_path / 'raw')
    with pytest.raises(ValueError, match='BUDGET_EXCEEDED'):
        run(tmp_path / 'new', bundle, cutoff, reader, limits=replace(LIMITS, **{field: value}))
    assert not (tmp_path / 'new' / 'manifest.json').exists()


def test_cancellation_is_not_swallowed_and_all_new_handles_close(tmp_path):
    bundle, cutoff, reader, _ = source_fixture(tmp_path / 'raw')
    calls = 0

    class Cancelled(RuntimeError):
        pass

    def cancel():
        nonlocal calls
        calls += 1
        if (tmp_path / 'new' / 'facts').exists():
            raise Cancelled('TEST_FIXTURE_CANCEL')

    with pytest.raises(Cancelled) as caught:
        run(tmp_path / 'new', bundle, cutoff, reader, checkpoint=cancel)
    assert caught.value.daily_session_pipeline_metrics['source_session_projections'] == 0
    assert not (tmp_path / 'new' / 'manifest.json').exists()
    assert calls > 1


@pytest.mark.parametrize('field,value', [('max_seconds', 2401), ('max_read_bytes', 4 * 1024 ** 3 + 1),
    ('max_output_bytes', 512 * pipeline.MIB + 1), ('max_rss_bytes', 2560 * pipeline.MIB + 1),
    ('max_source_bytes', 8 * pipeline.MIB + 1), ('max_read_bytes', True)])
def test_limits_cannot_expand_original_theme_envelope(field, value):
    with pytest.raises(ValueError, match='INVALID_'):
        replace(LIMITS, **{field: value})


def test_source_authority_requires_genuine_bound_owner_outside_fixture(tmp_path):
    bundle, cutoff, reader, _ = source_fixture(tmp_path / 'raw')
    bundle = bundle.model_copy(update={'coverage_type': 'RECONSTRUCTED_HISTORICAL'})
    with pytest.raises(ValueError, match='GENUINE_SOURCE_OWNER'):
        run(tmp_path / 'new', bundle, cutoff, reader, source_terminal_validator=lambda: True)
    assert not (tmp_path / 'new').exists()


def test_existing_output_and_symlink_parent_are_not_modified(tmp_path):
    bundle, cutoff, reader, _ = source_fixture(tmp_path / 'raw')
    existing = tmp_path / 'existing'
    existing.mkdir()
    marker = existing / 'keep'
    marker.write_text('TEST_FIXTURE')
    with pytest.raises(ValueError, match='NEW_UNALIASED_DIRECTORY'):
        run(existing, bundle, cutoff, reader)
    link = tmp_path / 'alias'
    link.symlink_to(existing, target_is_directory=True)
    with pytest.raises(ValueError, match='NEW_UNALIASED_DIRECTORY'):
        run(link / 'new', bundle, cutoff, reader)
    assert marker.read_text() == 'TEST_FIXTURE'


@pytest.mark.parametrize('cancelled', [False, True])
def test_genuine_owner_callback_is_metered_and_restored(tmp_path, cancelled):
    from tests.support.saved_r2_v3_fixture import fixture
    from stockfish_core.yaogu.saved_r2_sources_v2 import issue_saved_r2_source_reader
    bundle, cutoff, _, _, _, paths = fixture(tmp_path / 'original', sessions_count=2, include_themes=False)
    owner = issue_saved_r2_source_reader(sources_manifest_path=paths['sources'], cutoff=cutoff)
    reader = owner.reader
    old_reader = owner.registry.read
    bundle = bundle.model_copy(update={'coverage_type': 'RECONSTRUCTED_HISTORICAL'})

    def check():
        if cancelled and (tmp_path / 'new' / 'facts').exists():
            raise RuntimeError('TEST_FIXTURE_CANCEL_WITH_OWNER')

    if cancelled:
        with pytest.raises(RuntimeError, match='TEST_FIXTURE_CANCEL_WITH_OWNER'):
            run(tmp_path / 'new', bundle, cutoff, reader,
                source_terminal_validator=owner.validate_binding, checkpoint=check)
    else:
        result = run(tmp_path / 'new', bundle, cutoff, reader,
            source_terminal_validator=owner.validate_binding, checkpoint=check)
        assert result['metrics']['owner_terminal_calls'] == 3
        expected_bytes = sum(Path(path).stat().st_size for path in owner.registry.files)
        assert result['metrics']['returned_read_bytes']['owner_registry'] == 3 * expected_bytes
        refs = {row['path']: row for row in result['manifest']['source_references']}
        assert set(owner.registry.files).issubset(refs)
        assert all(set(refs[path]['fp']) == set(pipeline._FP) for path in owner.registry.files)
    assert 'read' not in owner.registry.__dict__
    assert owner.registry.read == old_reader
