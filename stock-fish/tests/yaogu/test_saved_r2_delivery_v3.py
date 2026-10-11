"""V3 real source issuance and frozen delivery boundaries; TEST_FIXTURE only."""
from copy import deepcopy
from datetime import timedelta
import csv
import hashlib
import io
import json
from pathlib import Path

import pytest

from stockfish_core.yaogu import saved_r2_delivery as old_delivery
from stockfish_core.yaogu import saved_r2_sources_v2 as sources_v2
from stockfish_core.yaogu import saved_r2_stream_delivery_v3 as delivery
from stockfish_core.yaogu.contracts import SourceReceipt
from stockfish_core.yaogu.history_artifacts import digest_market_input
from stockfish_core.yaogu.research_context_artifacts import SourceArtifactReader
from stockfish_core.yaogu.theme_market_stream import SourceMarketStream
from stockfish_core.yaogu.visibility import stamp
from tests.support.saved_r2_v3_fixture import fixture, physical_source, update_sources, write


@pytest.fixture(autouse=True)
def forbid_external_calls(monkeypatch):
    """No fake call-count claim: forbidden work would fail the actual test."""
    import socket
    import sqlite3
    from scripts import codex_research as cli
    from stockfish_core.yaogu import short_horizon_participation, short_horizon_strength, strategy_experiments
    def forbidden(*args, **kwargs):
        raise AssertionError('TEST_FIXTURE_NO_NETWORK_DB_SDK_SELECTOR')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    monkeypatch.setattr(cli, 'Client', forbidden)
    monkeypatch.setattr(strategy_experiments, 'run_short_horizon_strategy', forbidden)
    monkeypatch.setattr(short_horizon_participation, 'run_short_horizon_strength', forbidden)
    monkeypatch.setattr(short_horizon_strength, 'run_short_horizon_strength', forbidden)


def issue(paths, cutoff):
    return sources_v2.issue_saved_r2_source_reader(
        sources_manifest_path=paths['sources'], cutoff=cutoff)


def invoke(paths, cutoff, output, **kwargs):
    return delivery.deliver_saved_r2_stream(run_path=paths['run'],
        run_manifest_path=paths['manifest'], input_path=paths['input'],
        sources_manifest_path=paths['sources'], output=output,
        context_cutoff=cutoff, **kwargs)


def replace_market_source(tmp_path, sources, mutate):
    index = next(index for index, item in enumerate(sources['raw_receipt_sources'])
        if item['typed_receipt']['dataset'] == 'market_daily')
    prior = sources['raw_receipt_sources'][index]
    payload = json.loads(Path(prior['raw']['path']).read_bytes())
    original = json.loads(Path(prior['receipt']['path']).read_bytes())
    mutate(payload['rows'])
    sources['raw_receipt_sources'][index] = physical_source(tmp_path/'mutated_capture',
        'market_daily', payload['rows'], stamp(original['first_seen_at']),
        day=stamp(prior['typed_receipt']['event_time']).date())
    return sources['raw_receipt_sources'][index]


def test_original_physical_null_count_issues_actual_rows_without_normalized_wrappers(tmp_path):
    bundle, cutoff, _, sources, _, paths = fixture(tmp_path, sessions_count=1)
    before = {path: path.read_bytes() for path in paths.values()}
    assert all(json.loads(Path(item['receipt']['path']).read_bytes())['row_count'] is None
        for item in sources['raw_receipt_sources'])
    assert not any('result' in item for item in sources['raw_receipt_sources'])
    issued = issue(paths, cutoff)
    assert isinstance(issued.reader, SourceArtifactReader)
    assert all(isinstance(row, SourceReceipt) for row in issued.receipts)
    stream = SourceMarketStream.from_reader(issued.reader, bundle, cutoff=cutoff,
        sessions=bundle.sessions[-1:])
    panel = stream.panel(bundle.observation_date)
    assert len(panel.rows) == 16
    assert len(panel.expected_instrument_ids) == 16
    assert all(row.row_count == len(json.loads(issued.reader(row.receipt_id))['rows'])
        for row in issued.receipts)
    issued.validate_binding()
    assert {path: path.read_bytes() for path in paths.values()} == before


@pytest.mark.parametrize('kind', ['raw_bytes', 'receipt_bytes', 'receipt_id',
    'typed_clock', 'typed_count', 'typed_source_hash', 'typed_status',
    'typed_scope', 'typed_business_date', 'typed_historical_proof',
    'fake_capability', 'duplicate_parent'])
def test_whole_source_issuance_rejects_original_and_declaration_conflicts(tmp_path, kind):
    _, cutoff, _, sources, manifest, paths = fixture(tmp_path, sessions_count=1)
    item = sources['raw_receipt_sources'][0]
    if kind in ('raw_bytes', 'receipt_bytes'):
        filename = Path(item['raw' if kind == 'raw_bytes' else 'receipt']['path'])
        filename.write_bytes(filename.read_bytes()+b' ')
    elif kind == 'receipt_id':
        filename = Path(item['receipt']['path'])
        receipt = json.loads(filename.read_bytes())
        receipt['parameters']['date'] = str(stamp(item['typed_receipt']['event_time']).date()+timedelta(days=1))
        item['receipt'] = write(filename, receipt)
    elif kind == 'typed_clock':
        item['typed_receipt']['ingested_at'] = (cutoff-timedelta(days=100)).isoformat()
    elif kind == 'typed_count':
        item['typed_receipt']['row_count'] -= 1
    elif kind == 'typed_source_hash':
        item['typed_receipt']['content_hash'] = 'f'*64
    elif kind == 'typed_status':
        item['typed_receipt']['status'] = 'PARTIAL'
    elif kind == 'typed_scope':
        item['typed_receipt']['instrument_ids'] = ['600001.SH']
    elif kind == 'typed_business_date':
        item['typed_receipt']['business_sessions'] = [str(stamp(item['typed_receipt']['event_time']).date())]
    elif kind == 'typed_historical_proof':
        item['typed_receipt']['historical_time_proof'] = dict(
            policy_id='TEST_FIXTURE_FORGED_HISTORY', event_time_basis='SOURCE_DATED_EVENT_RECORD',
            publication_time_basis='SOURCE_DOCUMENTED_RELEASE_RULE',
            source_url='https://TEST_FIXTURE.invalid/proof', evidence_hash='f'*64,
            applies_from='2020-01-01', applies_to='2030-01-01',
            evidence_refs=['TEST_FIXTURE'], verified_at=cutoff.isoformat())
        # This is a well-typed declaration; it still lacks original evidence.
        SourceReceipt.model_validate(item['typed_receipt'])
    elif kind == 'fake_capability':
        sources['source_capability_restored_from_JSON'] = True
    else:
        sources['raw_receipt_sources'].append(deepcopy(item))
    update_sources(paths, sources, manifest)
    with pytest.raises(ValueError):
        issue(paths, cutoff)


def test_original_physical_nonnull_wrong_count_is_not_repaired_by_typed_count(tmp_path):
    _, cutoff, _, sources, manifest, paths = fixture(tmp_path, sessions_count=1)
    item = sources['raw_receipt_sources'][0]
    payload = json.loads(Path(item['raw']['path']).read_bytes())
    receipt = json.loads(Path(item['receipt']['path']).read_bytes())
    sources['raw_receipt_sources'][0] = physical_source(tmp_path/'wrong_count',
        receipt['dataset'], payload['rows'], stamp(receipt['first_seen_at']),
        day=stamp(item['typed_receipt']['event_time']).date(), physical_row_count=len(payload['rows'])+1)
    update_sources(paths, sources, manifest)
    with pytest.raises(ValueError):
        issue(paths, cutoff)


@pytest.mark.parametrize('kind', ['raw_status', 'raw_pagination', 'field_version',
    'wrong_provider', 'unsupported_endpoint', 'extra_request_parameter',
    'bool_row_count', 'raw_capture_clock'])
def test_original_receipt_identity_is_not_sufficient_for_invalid_envelope_or_request(tmp_path, kind):
    _, cutoff, _, sources, manifest, paths = fixture(tmp_path, sessions_count=1)
    item = sources['raw_receipt_sources'][0]
    raw_path, receipt_path = Path(item['raw']['path']), Path(item['receipt']['path'])
    payload, receipt = json.loads(raw_path.read_bytes()), json.loads(receipt_path.read_bytes())
    if kind == 'raw_status':
        payload['status'] = 'PARTIAL'
    elif kind == 'raw_pagination':
        payload['pagination_complete'] = False
    elif kind == 'raw_capture_clock':
        payload['first_seen_at'] = (stamp(receipt['first_seen_at'])+timedelta(seconds=1)).isoformat()
    elif kind == 'field_version':
        payload['field_version'] = receipt['field_version'] = 'TEST_FIXTURE_UNSUPPORTED_VERSION'
        item['typed_receipt']['field_version'] = receipt['field_version']
    elif kind == 'wrong_provider':
        receipt['provider'] = item['typed_receipt']['provider'] = 'TEST_FIXTURE_OTHER_VENDOR'
    elif kind == 'unsupported_endpoint':
        receipt['dataset'] = item['typed_receipt']['dataset'] = 'stock_basic_all'
    elif kind == 'extra_request_parameter':
        receipt['parameters']['page'] = 1
        item['typed_receipt']['parameters']['page'] = 1
    else:
        receipt['row_count'] = True
    # Whole file SHA and local receipt ID are honestly rebound. Semantic
    # disagreement cannot be dismissed as merely a stale manifest hash.
    raw_registration = write(raw_path, payload)
    receipt['content_hash'] = raw_registration['sha256']
    rid = hashlib.sha256(json.dumps({key: value for key, value in receipt.items()
        if key != 'receipt_id'}, sort_keys=True).encode()).hexdigest()
    receipt['receipt_id'] = rid
    item['raw'], item['receipt'] = raw_registration, write(receipt_path, receipt)
    item['typed_receipt'].update(receipt_id=rid, content_hash=receipt['content_hash'])
    update_sources(paths, sources, manifest)
    with pytest.raises(ValueError):
        issue(paths, cutoff)


@pytest.mark.parametrize('kind', ['adjusted_price', 'wrong_date', 'missing_scope',
    'duplicate_identity', 'unsupported_identity', 'unknown_status',
    'nontrading_positive_amount', 'negative_amount', 'nonfinite_amount', 'bad_ohlc'])
def test_whole_valid_original_bytes_still_need_native_market_semantic_validation(tmp_path, kind):
    bundle, cutoff, _, sources, manifest, paths = fixture(tmp_path, sessions_count=1)
    def mutate(rows):
        if kind == 'adjusted_price':
            rows[0]['adjustflag'] = '2'
        elif kind == 'wrong_date':
            rows[0]['date'] = str(bundle.observation_date-timedelta(days=1))
        elif kind == 'missing_scope':
            rows.pop()
        elif kind == 'duplicate_identity':
            rows.append(deepcopy(rows[0]))
        elif kind == 'unsupported_identity':
            rows[0]['code'] = 'sh.900001'
        elif kind == 'unknown_status':
            rows[0]['tradestatus'] = ''
        elif kind == 'nontrading_positive_amount':
            rows[0]['tradestatus'] = '0'
        elif kind == 'negative_amount':
            rows[0]['amount'] = '-1'
        elif kind == 'nonfinite_amount':
            rows[0]['amount'] = 'NaN'
        else:
            rows[0]['high'] = '1'
    replace_market_source(tmp_path, sources, mutate)
    update_sources(paths, sources, manifest)
    with pytest.raises(ValueError):
        issued = issue(paths, cutoff)
        SourceMarketStream.from_reader(issued.reader, bundle, cutoff=cutoff,
            sessions=bundle.sessions[-1:])


def test_original_late_sources_remain_unavailable_at_old_cutoff(tmp_path):
    bundle, cutoff, _, _, _, paths = fixture(tmp_path, sessions_count=1, late_capture=True)
    issued = issue(paths, cutoff)
    stream = SourceMarketStream.from_reader(issued.reader, bundle,
        cutoff=cutoff, sessions=bundle.sessions[-1:])
    assert stream.available_sessions == ()
    assert stream.descriptor['missing_sessions'][str(bundle.observation_date)]['status'] == 'UNKNOWN'
    assert stream.panel(bundle.observation_date) is None
    issued.validate_binding()


@pytest.mark.parametrize('change', ['source_bytes', 'registry', 'reader_path', 'reader_receipt_mapping'])
def test_capability_requires_current_issued_objects_and_unchanged_registry(tmp_path, change):
    bundle, cutoff, _, sources, _, paths = fixture(tmp_path, sessions_count=1)
    issued = issue(paths, cutoff)
    with pytest.raises((TypeError, ValueError)):
        deepcopy(issued)
    with pytest.raises(ValueError, match='AUTHENTICATED_READER_REQUIRED'):
        SourceMarketStream.from_reader(lambda _: b'{}', bundle,
            cutoff=cutoff, sessions=bundle.sessions[-1:])
    raw_path = Path(sources['raw_receipt_sources'][0]['raw']['path'])
    if change == 'source_bytes':
        raw_path.write_bytes(raw_path.read_bytes()+b' ')
    elif change == 'registry':
        issued.registry.files.clear()
    elif change == 'reader_path':
        receipt_id = next(iter(issued.reader._paths))
        other = tmp_path/'same_raw_elsewhere.json'
        other.write_bytes(issued.reader(receipt_id))
        issued.reader._paths[receipt_id] = other
    else:
        reader = issued.reader
        receipt_id = next(iter(reader._receipts))
        reader._receipts[receipt_id] = reader._receipts[receipt_id].model_copy(update={'status': 'PARTIAL'})
    with pytest.raises(ValueError):
        issued.validate_binding()


@pytest.mark.parametrize('attribute', ['businesses', 'nodes', 'extra', 'reported'])
def test_issuer_does_not_authorize_replaced_business_or_archive_registrations(tmp_path, attribute):
    _, cutoff, _, _, _, paths = fixture(tmp_path, sessions_count=0, include_themes=False)
    issued = issue(paths, cutoff)
    replacement = ('TEST_FIXTURE_UNISSUED_REGISTRATION',) if attribute != 'reported' else 'TEST_FIXTURE_UNISSUED_REPORT'
    try:
        setattr(issued, attribute, replacement)
    except (AttributeError, ValueError):
        # A read-only accessor is an equally valid immutable boundary.
        return
    with pytest.raises(ValueError):
        issued.validate_binding()


def test_legacy_v1_trio_does_not_fall_back_when_normalized_file_is_missing(tmp_path):
    _, cutoff, _, sources, manifest, paths = fixture(tmp_path, sessions_count=0)
    sources.pop('raw_receipt_sources')
    sources['schema_version'] = old_delivery.SOURCES_SCHEMA
    Path(sources['captures'][0]['result']['path']).unlink()
    update_sources(paths, sources, manifest)
    with pytest.raises(FileNotFoundError):
        old_delivery.deliver_saved_r2(run_path=paths['run'], run_manifest_path=paths['manifest'],
            input_path=paths['input'], sources_manifest_path=paths['sources'],
            output=tmp_path/'old_output', context_cutoff=cutoff,
            membership_mode='CURRENT_COHORT_BACKVIEW')
    assert not (tmp_path/'old_output').exists()


def test_complete_3038_pool_and_61_observations_use_real_67_session_stream_once(tmp_path, monkeypatch):
    bundle, cutoff, run, sources, manifest, paths = fixture(tmp_path,
        pool_count=3038, selected_count=61)
    from stockfish_core.yaogu import theme_evolution_streaming, theme_observation_diagnostics
    original_producer = theme_evolution_streaming.run_theme_evolution_stream
    original_diagnostic = theme_observation_diagnostics.build_theme_observation_diagnostics
    captured = {}
    def real_producer(*args, **kwargs):
        captured['producer_calls'] = captured.get('producer_calls', 0)+1
        assert isinstance(kwargs['market_stream'], SourceMarketStream)
        assert kwargs['history_scope'] == 'LATEST_67'
        assert len(kwargs['market_stream'].requested_sessions) == 67
        assert len(kwargs['market_stream'].available_sessions) == 67
        return original_producer(*args, **kwargs)
    def real_diagnostic(bundle_arg, parent, **kwargs):
        validator = kwargs['_theme_context_validator']
        assert callable(validator)
        # Equivalent serialized parent bytes cannot restore the held object.
        for copy in (deepcopy(parent), json.loads(json.dumps(parent))):
            with pytest.raises(ValueError):
                validator(copy, bundle=bundle_arg, cutoff=kwargs['cutoff'])
        assert validator(parent, bundle=bundle_arg, cutoff=kwargs['cutoff']) is True
        captured['parent'] = deepcopy(parent)
        return original_diagnostic(bundle_arg, parent, **kwargs)
    monkeypatch.setattr(theme_evolution_streaming, 'run_theme_evolution_stream', real_producer)
    monkeypatch.setattr(theme_observation_diagnostics, 'build_theme_observation_diagnostics', real_diagnostic)
    input_before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths.values()}
    outcome = invoke(paths, cutoff, tmp_path/'delivered', membership_mode='CURRENT_COHORT_BACKVIEW')
    assert outcome['observation_count'] == 61
    assert captured['producer_calls'] == 1
    saved = json.loads((tmp_path/'delivered/delivery-manifest.json').read_bytes())
    assert saved['native_theme_producer_calls'] == 1
    assert saved['network_DB_SDK_selector_calls'] == 0
    assert saved['counts'] == manifest['binding']['native_counts']
    assert saved['count_summary']['eligible'] == 3038
    assert saved['frozen_selected_ids'] == manifest['binding']['selected_ids']
    assert saved['historical_PIT_revalidated'] is False
    assert saved['complete_cycles'] == 0
    assert saved['source_capability_restored_from_JSON'] is False
    assert saved['market_edge'] == 'NOT_ESTABLISHED'
    all_rows = list(csv.DictReader(io.StringIO((tmp_path/'delivered/all.csv').read_text())))
    assert len(all_rows) == 3038
    assert [row['instrument_id'] for row in all_rows] == run['results'][old_delivery.STRATEGY]['eligible_pool_ids']
    observed = json.loads((tmp_path/'delivered/observation.json').read_bytes())
    selected = [row for row in run['results'][old_delivery.STRATEGY]['items'] if row['research_selected']]
    assert observed['results'][old_delivery.STRATEGY]['items'] == selected
    grouped = json.loads((tmp_path/'delivered/grouped.json').read_bytes())
    lookup = {row['instrument_id']: row for group in grouped['groups'] for row in group['items']}
    assert len(lookup) == 61
    assert [lookup[iid] for iid in manifest['binding']['selected_ids']] == selected
    assert captured['parent']['source_input_hash'] == digest_market_input(bundle)
    assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths.values()} == input_before


def test_no_original_theme_sources_preserves_all_selected_rows_as_unknown_models(tmp_path):
    _, cutoff, run, _, manifest, paths = fixture(tmp_path, sessions_count=1, include_themes=False)
    outcome = invoke(paths, cutoff, tmp_path/'models', membership_mode='CURRENT_COHORT_BACKVIEW')
    assert outcome['observation_count'] == 13
    saved = json.loads((tmp_path/'models/delivery-manifest.json').read_bytes())
    assert saved['native_theme_producer_calls'] == 0
    assert saved['context_status'] == 'UNKNOWN_MODEL_FALLBACK'
    grouped = json.loads((tmp_path/'models/grouped.json').read_bytes())
    assert grouped['frozen_selected_ids'] == manifest['binding']['selected_ids']
    lookup = {row['instrument_id']: row for group in grouped['groups'] for row in group['items']}
    assert [lookup[iid] for iid in grouped['frozen_selected_ids']] == [
        row for row in run['results'][old_delivery.STRATEGY]['items'] if row['research_selected']]
    assert all(group['group_type'] == 'MODEL_ONLY_NOT_THEME_MAINLINE' for group in grouped['groups'])


@pytest.mark.parametrize('counterfeit', ['json', 'lambda'])
def test_coordinator_does_not_accept_counterfeit_issuer_return(tmp_path, monkeypatch, counterfeit):
    _, cutoff, _, _, _, paths = fixture(tmp_path, sessions_count=0, include_themes=False)
    fake = {'verified': True, 'source_capability_restored_from_JSON': True} if counterfeit == 'json' else lambda: True
    monkeypatch.setattr(delivery, 'issue_saved_r2_source_reader', lambda **kwargs: fake)
    with pytest.raises(ValueError, match='GENUINE_SOURCE_ISSUER_REQUIRED'):
        invoke(paths, cutoff, tmp_path/'counterfeit')
    assert not (tmp_path/'counterfeit').exists()


@pytest.mark.parametrize('kind', ['missing_master', 'partial_master', 'pagination_incomplete'])
def test_legal_missing_or_incomplete_parent_stays_unknown_without_calendar_compression(tmp_path, kind):
    bundle, cutoff, _, sources, manifest, paths = fixture(tmp_path, sessions_count=1)
    index = next(index for index, item in enumerate(sources['raw_receipt_sources'])
        if item['typed_receipt']['dataset'] == 'master_all')
    prior = sources['raw_receipt_sources'][index]
    if kind == 'missing_master':
        sources['raw_receipt_sources'].pop(index)
    else:
        payload = json.loads(Path(prior['raw']['path']).read_bytes())
        receipt = json.loads(Path(prior['receipt']['path']).read_bytes())
        sources['raw_receipt_sources'][index] = physical_source(tmp_path/'incomplete_capture',
            'master_all', payload['rows'], stamp(receipt['first_seen_at']),
            day=bundle.observation_date, status='PARTIAL' if kind == 'partial_master' else 'COMPLETE',
            pagination_complete=False)
    update_sources(paths, sources, manifest)
    issued = issue(paths, cutoff)
    stream = SourceMarketStream.from_reader(issued.reader, bundle,
        cutoff=cutoff, sessions=bundle.sessions[-67:])
    assert len(stream.requested_sessions) == 67
    assert len(stream.calendar) == len(bundle.sessions)
    assert stream.available_sessions == ()
    assert len(stream.descriptor['missing_sessions']) == 67
    assert all(row['status'] == 'UNKNOWN' for row in stream.descriptor['missing_sessions'].values())


def test_actual_native_journal_tampering_cannot_be_authorized_by_unchanged_parent(tmp_path, monkeypatch):
    _, cutoff, _, _, _, paths = fixture(tmp_path, sessions_count=1)
    from stockfish_core.yaogu import theme_observation_diagnostics
    observed = {}
    def corrupt_then_check(bundle, parent, **kwargs):
        validator = kwargs['_theme_context_validator']
        journal = validator.journal.directory
        target = next(path for path in journal.iterdir()
            if path.name != 'manifest.json' and path.is_file() and path.stat().st_size > 0)
        observed['original_sha'] = hashlib.sha256(target.read_bytes()).hexdigest()
        target.write_bytes(target.read_bytes()+b' ')
        with pytest.raises(ValueError):
            validator(parent, bundle=bundle, cutoff=kwargs['cutoff'])
        observed['rejected'] = True
        raise ValueError('TEST_FIXTURE_STOP_AFTER_CORRUPT_JOURNAL_REJECTION')
    monkeypatch.setattr(theme_observation_diagnostics,
        'build_theme_observation_diagnostics', corrupt_then_check)
    output = tmp_path/'bad_journal'
    with pytest.raises(ValueError, match='TEST_FIXTURE_STOP_AFTER_CORRUPT_JOURNAL_REJECTION'):
        invoke(paths, cutoff, output, membership_mode='CURRENT_COHORT_BACKVIEW')
    assert observed['rejected'] is True
    assert observed['original_sha']
    assert (output/'NON_DELIVERY_AUDIT/native-evidence/manifest.json').exists()
    assert not (output/'delivery-manifest.json').exists()


def test_real_native_journal_bytes_are_counted_before_candidate_export_delivery(tmp_path, monkeypatch):
    _, cutoff, _, _, _, paths = fixture(tmp_path, sessions_count=1)
    from stockfish_core.yaogu import theme_evolution_streaming
    producer = theme_evolution_streaming.run_theme_evolution_stream
    observed = {}
    def produce_then_constrain(*args, **kwargs):
        parent = producer(*args, **kwargs)
        journal = kwargs['evidence_directory']
        size = sum(path.stat().st_size for path in journal.rglob('*') if path.is_file())
        assert size > 0
        observed.update(journal_bytes=size, producer_calls=1)
        # Lower only this isolated fixture's cap after a genuine producer has
        # written its journal. The existing bytes alone already exceed it.
        monkeypatch.setattr(delivery, 'MAX_OUTPUT_BYTES', size-1)
        monkeypatch.setattr(delivery, 'CONTROL_RESERVE_BYTES', 0)
        return parent
    monkeypatch.setattr(theme_evolution_streaming, 'run_theme_evolution_stream', produce_then_constrain)
    output = tmp_path/'journal_over_cap'
    with pytest.raises(ValueError, match='OUTPUT_BYTE_BUDGET_EXCEEDED'):
        invoke(paths, cutoff, output, membership_mode='CURRENT_COHORT_BACKVIEW')
    assert observed['producer_calls'] == 1
    assert observed['journal_bytes'] > delivery.MAX_OUTPUT_BYTES
    assert not (output/'all.csv').exists()
    assert not (output/'delivery-manifest.json').exists()


def cli_args(paths, cutoff, output):
    arguments = ['deliver-saved-r2-stream']
    for flag, key in (('run', 'run'), ('run-manifest', 'manifest'),
            ('input', 'input'), ('sources-manifest', 'sources')):
        arguments.extend(['--'+flag, str(paths[key])])
    return [*arguments, '--output', str(output), '--context-cutoff', cutoff.isoformat(),
        '--membership-mode', 'CURRENT_COHORT_BACKVIEW']


def test_cli_runs_actual_new_coordinator_without_api_key_or_client(tmp_path, monkeypatch, capsys):
    from scripts import codex_research as cli
    _, cutoff, _, _, manifest, paths = fixture(tmp_path, sessions_count=1, include_themes=False)
    def forbidden_key(*args, **kwargs):
        raise AssertionError('TEST_FIXTURE_OFFLINE_CLI_MUST_NOT_READ_API_KEY')
    monkeypatch.setattr(cli, 'api_key', forbidden_key)
    assert cli.main(cli_args(paths, cutoff, tmp_path/'cli_output')) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['observation_count'] == 13
    saved = json.loads((tmp_path/'cli_output/delivery-manifest.json').read_bytes())
    assert saved['frozen_selected_ids'] == manifest['binding']['selected_ids']
    assert saved['counts'] == manifest['binding']['native_counts']
    assert saved['version'] == delivery.VERSION
    assert saved['network_DB_SDK_selector_calls'] == 0


def test_cli_help_names_explicit_original_source_parameters(capsys):
    from scripts import codex_research as cli
    with pytest.raises(SystemExit) as exited:
        cli.main(['deliver-saved-r2-stream', '--help'])
    assert exited.value.code == 0
    help_text = capsys.readouterr().out
    assert all(flag in help_text for flag in ('--run', '--run-manifest', '--input',
        '--sources-manifest', '--context-cutoff', '--membership-mode', '--as-of', '--output'))


@pytest.mark.parametrize('kind', ['raw', 'clock', 'schema'])
def test_cli_rejects_real_bad_source_without_printing_payload(tmp_path, monkeypatch, capsys, kind):
    from scripts import codex_research as cli
    _, cutoff, _, sources, manifest, paths = fixture(tmp_path, sessions_count=1, include_themes=False)
    secret = 'TEST_FIXTURE_PRIVATE_PAYLOAD_DO_NOT_PRINT_7d4f8b'
    if kind == 'raw':
        filename = Path(sources['raw_receipt_sources'][0]['raw']['path'])
        filename.write_bytes(filename.read_bytes()+secret.encode())
    elif kind == 'clock':
        sources['raw_receipt_sources'][0]['typed_receipt']['ingested_at'] = (
            cutoff-timedelta(days=100)).isoformat()
    else:
        sources['source_capability_restored_from_JSON'] = {'secret': secret}
    update_sources(paths, sources, manifest)
    def forbidden_key(*args, **kwargs):
        raise AssertionError('TEST_FIXTURE_OFFLINE_CLI_MUST_NOT_READ_API_KEY')
    monkeypatch.setattr(cli, 'api_key', forbidden_key)
    output = tmp_path/'cli_failed'
    assert cli.main(cli_args(paths, cutoff, output)) == 2
    printed = capsys.readouterr().out
    assert secret not in printed
    assert 'TEST_FIXTURE股' not in printed
    assert 'Traceback' not in printed
    result = json.loads(printed)
    assert result['status'] == 'BLOCKED'
    assert result['reason']
    assert not (output/'delivery-manifest.json').exists()
