"""TEST_FIXTURE-only different-path RESULT copies and late-session trust gates."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone, date
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from stockfish_core.yaogu import short_horizon_archive_bridge_v1 as bridge
from stockfish_core.yaogu import short_horizon_daily_closure_v1 as daily
from stockfish_core.yaogu import r2_closure_cli as cli
from stockfish_core.yaogu import sealed_result_local_copy_v1 as local
from stockfish_core.yaogu import premarket_workflow_v1 as workflow
from tests.support.short_horizon_archive_fixture import fixture_archive
from tests.yaogu.test_short_horizon_archive_bridge_v1 import service, _offline_fixture_document
from tests.yaogu.test_sealed_result_restoration_v1 import complete


def write_manifest(path, value):
    path.write_text(json.dumps(value, sort_keys=True), encoding='utf-8')
    return workflow._ref(path)


def locked_copy(original, path):
    path.write_bytes(Path(original['path']).read_bytes())
    os.chflags(path, path.stat().st_flags | local.IMMUTABLE)
    current = workflow._ref(path)
    assert current['path'] != original['path'] and current['sha256'] == original['sha256']
    assert current['bytes'] == original['bytes'] and current['fp']['st_flags'] & local.IMMUTABLE
    return current


def replace_copy(current, *, change=False):
    path = Path(current['path'])
    os.chflags(path, path.stat().st_flags & ~local.IMMUTABLE)
    raw = path.read_bytes()
    other = path.with_suffix('.TEST_FIXTURE-replaced')
    other.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]) if change else raw)
    os.replace(other, path)
    os.chflags(path, path.stat().st_flags | local.IMMUTABLE)


def clear_fixture_flags(root):
    for path in root.rglob('*'):
        if path.is_file() and not path.is_symlink() and path.stat().st_flags & local.IMMUTABLE:
            os.chflags(path, path.stat().st_flags & ~local.IMMUTABLE)


@pytest.fixture
def copied(tmp_path):
    archive = fixture_archive(tmp_path / 'TEST_FIXTURE-original')
    original = deepcopy(next(item['result'] for item in archive['items']
        if json.loads(Path(item['result']['path']).read_bytes())['endpoint'] == 'market_daily'))
    root = tmp_path / 'TEST_FIXTURE-objects'
    root.mkdir()
    current = locked_copy(original, root / 'result.json')
    Path(original['path']).unlink()
    value = dict(schema_version=local.PROFILE, copy_root_reference=local.root_reference(root),
        archives=[dict(archive_final_sha256=archive['final_sha256'],
            results=[dict(original_reference=original, current_reference=current)])])
    manifest = write_manifest(tmp_path / 'TEST_FIXTURE-local-copy.json', value)
    try:
        yield archive, original, current, value, manifest
    finally:
        clear_fixture_flags(tmp_path)


def authenticate(archive, manifest=None, **kwargs):
    session = bridge.ArchiveVerificationSession(**({'local_copy_manifest_ref': manifest} if manifest else {}))
    return bridge.authenticate_archive(archive['job'], expected_final_sha256=archive['final_sha256'],
        session=session, allow_test_fixture=True, **kwargs)


def test_different_path_explicit_authentication_exact_bytes_and_default_rejection(copied, service):
    archive, original, current, value, manifest = copied
    with pytest.raises((ValueError, FileNotFoundError)):
        authenticate(archive)
    with pytest.raises(ValueError):
        bridge.ArchiveVerificationSession(restoration_manifest_ref=manifest)
    cap = authenticate(archive, manifest)
    protocol = local.protocol_identity(manifest, value['copy_root_reference'])
    audit = cap.audit()
    assert audit['source_protocol'] == protocol
    assert audit['input_hash'] == archive['run']['input_hash']
    assert audit['run_document_hash'] == bridge.digest_json_stream(archive['run'])
    assert audit['verification_budget']['limits']['seconds'] == 300
    proof = dict(original_reference=original, current_reference=current,
        original_physical_identity_status='LOST_NOT_RESTORED', proof_mode='SEALED_BYTES_AND_IMMUTABLE_LOCAL_COPY_PIN')
    assert audit['verification_budget']['result_local_copies'] == [proof]
    assert 'result_restorations' not in audit['verification_budget']
    with pytest.raises(ValueError, match='EXPLICIT_RESULT_LOCAL_COPY_COMMIT_REQUIRED'):
        bridge.commit_bridge(service, cap)
    with pytest.raises(ValueError):
        bridge.commit_bridge(service, cap, restoration_manifest_ref=manifest)
    cert = bridge.commit_bridge(service, cap, local_copy_manifest_ref=manifest)
    assert cert['source_protocol'] == protocol and cert['original_result_physical_ownership'] == 'LOST_NOT_RESTORED'
    assert service.store.get_document('strategy_experiment_runs', cert['run_id']) == archive['run']
    ordinary = SimpleNamespace(store=service.store)
    with pytest.raises(ValueError, match='EXPLICIT_MATCHING_RESULT_LOCAL_COPY_PROTOCOL_REQUIRED'):
        bridge.saved_csv_export(ordinary, archive['run'], archive['run']['strategy_id'], 'all')
    with pytest.raises(ValueError, match='EXPLICIT_MATCHING_RESULT_LOCAL_COPY_PROTOCOL_REQUIRED'):
        bridge.require_verified_bridge(service, cert['run_id'])
    for selected in bridge.CSV_SELECTIONS:
        assert bridge.saved_csv_export(service, archive['run'], archive['run']['strategy_id'], selected) == Path(
            archive['artifacts'][selected + '.csv']['path']).read_bytes().decode('utf-8')
    session = bridge.ArchiveVerificationSession(local_copy_manifest_ref=manifest)
    assert bridge.require_verified_bridge(service, cert['run_id'], session=session)['source_protocol'] == protocol
    with service.store.transaction(write=False) as connection:
        sources = [json.loads(row['payload']) for row in connection.execute('SELECT payload FROM documents WHERE namespace=?', (bridge.SOURCES,))]
    saved = next(row for row in sources if row.get('result_local_copy'))
    assert saved['originals']['result'] == original and saved['result_local_copy'] == proof
    session.terminal()


@pytest.mark.parametrize('change', ['same_path', 'wrong_sha', 'outside_root', 'not_immutable', 'extra_field',
    'duplicate_archive', 'duplicate_result', 'root_inode', 'manifest_inside_root'])
def test_local_manifest_scope_and_shape_refusals(copied, tmp_path, change):
    archive, original, current, value, manifest = copied
    value = deepcopy(value)
    entry = value['archives'][0]['results'][0]
    if change == 'same_path': entry['current_reference']['path'] = original['path']
    elif change == 'wrong_sha': entry['current_reference']['sha256'] = '0' * 64
    elif change == 'outside_root': entry['current_reference']['path'] = str(tmp_path / 'outside.json')
    elif change == 'not_immutable': entry['current_reference']['fp']['st_flags'] &= ~local.IMMUTABLE
    elif change == 'extra_field': value['skip_raw'] = True
    elif change == 'duplicate_archive': value['archives'].append(deepcopy(value['archives'][0]))
    elif change == 'duplicate_result': value['archives'][0]['results'].append(deepcopy(entry))
    elif change == 'root_inode': value['copy_root_reference']['fp']['st_ino'] += 1
    path = tmp_path / 'TEST_FIXTURE-invalid-map.json'
    if change == 'manifest_inside_root': path = Path(value['copy_root_reference']['path']) / 'manifest.json'
    changed = write_manifest(path, value)
    with pytest.raises(ValueError): authenticate(archive, changed)


@pytest.mark.parametrize('role', ['raw', 'receipt', 'producer', 'control', 'artifact'])
def test_only_original_sealed_core_result_may_be_mapped(copied, tmp_path, role):
    archive, original, current, value, manifest = copied
    chosen = (archive['items'][0][role] if role in {'raw', 'receipt'} else
        workflow._ref(Path(bridge.__file__).with_name('provider_requests.py')) if role == 'producer' else
        archive['final']['configuration'] if role == 'control' else archive['artifacts']['all.csv'])
    copied_ref = locked_copy(chosen, Path(value['copy_root_reference']['path']) / 'wrong-role.json')
    value['archives'][0]['results'].append(dict(original_reference=chosen, current_reference=copied_ref))
    changed = write_manifest(tmp_path / 'TEST_FIXTURE-wrong-role-map.json', value)
    with pytest.raises(ValueError, match='ONLY_EXACT_SEALED_CORE_RESULT_ALLOWED'): authenticate(archive, changed)


@pytest.mark.parametrize('change', ['same_bytes_inode', 'changed_bytes', 'manifest_bytes', 'copy_root'])
def test_whole_source_and_root_terminal_guards(copied, change, tmp_path):
    archive, original, current, value, manifest = copied
    cap = authenticate(archive, manifest)
    if change in {'same_bytes_inode', 'changed_bytes'}: replace_copy(current, change=change == 'changed_bytes')
    elif change == 'manifest_bytes': Path(manifest['path']).write_bytes(Path(manifest['path']).read_bytes() + b' ')
    else: Path(value['copy_root_reference']['path']).rename(tmp_path / 'TEST_FIXTURE-renamed-root')
    with pytest.raises((ValueError, FileNotFoundError)): bridge._state(cap)['session'].terminal()


def test_manifest_first_duplicate_keys_and_mutually_exclusive_authority(copied, tmp_path):
    archive, original, current, value, manifest = copied
    path = tmp_path / 'TEST_FIXTURE-duplicate.json'
    path.write_text('{"schema_version":"bad","schema_version":"SEALED_RESULT_LOCAL_COPY_V1"}')
    with pytest.raises(ValueError, match='DUPLICATE_JSON_KEY'):
        bridge.ArchiveVerificationSession(local_copy_manifest_ref=workflow._ref(path))
    with pytest.raises(ValueError, match='MUTUALLY_EXCLUSIVE'):
        bridge.ArchiveVerificationSession(local_copy_manifest_ref=manifest, restoration_manifest_ref=manifest)


def test_global_copy_reverse_uniqueness_and_same_original_reuse(copied, tmp_path):
    archive, original, current, value, manifest = copied
    second = deepcopy(value['archives'][0])
    second['archive_final_sha256'] = '0' * 64
    value['archives'].append(second)
    reused = write_manifest(tmp_path / 'TEST_FIXTURE-same-original-reused.json', value)
    bridge.ArchiveVerificationSession(local_copy_manifest_ref=reused).terminal()
    second['results'][0]['original_reference']['path'] = str(tmp_path / 'TEST_FIXTURE-other-original.json')
    alias = write_manifest(tmp_path / 'TEST_FIXTURE-cross-scope-alias.json', value)
    with pytest.raises(ValueError, match='CROSS_SCOPE_COPY_PATH_ALIAS'):
        bridge.ArchiveVerificationSession(local_copy_manifest_ref=alias)


def test_private_view_and_borrow_proofs_cannot_cross_protocol(copied, service):
    archive, original, current, value, manifest = copied
    cert = bridge.commit_bridge(service, authenticate(archive, manifest), local_copy_manifest_ref=manifest)
    session = bridge.ArchiveVerificationSession(local_copy_manifest_ref=manifest)
    bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=session, borrow_run=True)
    proof = session._verified_views[bridge._verified_view_key(service, bridge.RUN_BINDINGS, cert['run_id'], session)]
    ordinary = bridge.ArchiveVerificationSession()
    ordinary._verified_views[(id(service.store), bridge.RUN_BINDINGS, cert['run_id'])] = proof
    with pytest.raises(ValueError):
        bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=ordinary, borrow_run=True)
    with pytest.raises(ValueError): bridge._verified_second_run_decode(proof, ordinary)
    proof['source_protocol_context'] = None
    with pytest.raises(ValueError, match='SOURCE_PROTOCOL_CHANGED'):
        bridge.load_verified_bridge_input(service, cert['input_id'], session=session)


@pytest.mark.parametrize('strip_all', [False, True])
def test_csv_certificate_marker_removal_cannot_downgrade(copied, service, strip_all):
    archive, original, current, value, manifest = copied
    cert = bridge.commit_bridge(service, authenticate(archive, manifest), local_copy_manifest_ref=manifest)
    cert.pop('source_protocol')
    if strip_all:
        cert.pop('original_result_physical_ownership')
        cert['verification_budget'].pop('source_protocol')
        cert['verification_budget'].pop('result_local_copies')
    _offline_fixture_document(service, bridge.CERTIFICATES, cert['bridge_id'], cert)
    for ns, key in [(bridge.RUN_BINDINGS, cert['run_id']), (bridge.INPUT_BINDINGS, cert['input_id'])] + [
        (bridge.CSV_EXPORTS, cert['run_id'] + ':' + selection) for selection in bridge.CSV_SELECTIONS]:
        payload = service.store.get_document(ns, key); payload.pop('source_protocol', None)
        _offline_fixture_document(service, ns, key, payload)
    with pytest.raises(ValueError, match='PROTOCOL_CANNOT_BE_REMOVED|BRIDGE_IDENTITY_MISMATCH'):
        bridge.saved_csv_export(SimpleNamespace(store=service.store), archive['run'], archive['run']['strategy_id'], 'all')


def test_import_last_terminal_rolls_back_same_bytes_copy_replacement(copied, service, monkeypatch):
    archive, original, current, value, manifest = copied
    cap = authenticate(archive, manifest)
    service.store.put_document('TEST_FIXTURE_PROTECTED', 'prior', {'preserve': True})
    put = service.store._put_document
    changed = False
    def observed(connection, namespace, document_id, value):
        nonlocal changed
        result = put(connection, namespace, document_id, value)
        if namespace == bridge.CERTIFICATES and not changed:
            changed = True; replace_copy(current)
        return result
    monkeypatch.setattr(service.store, '_put_document', observed)
    with pytest.raises(ValueError): bridge.commit_bridge(service, cap, local_copy_manifest_ref=manifest)
    with service.store.transaction(write=False) as connection:
        assert [(row['namespace'], row['document_id']) for row in connection.execute('SELECT namespace,document_id FROM documents')] == [('TEST_FIXTURE_PROTECTED', 'prior')]


@pytest.mark.parametrize('fixture_flag', [False, None, 1, 'True'])
def test_nonfixture_injected_clock_refused_before_any_io(fixture_flag):
    with pytest.raises(ValueError, match='LOCAL_COPY_REAL_CLOCK_REQUIRED'):
        daily.LocalCopyLateCompletedSessionClosure(SimpleNamespace(allow_test_fixture=fixture_flag),
            local_copy_manifest_ref={'must_not_read': True}, clock=lambda: datetime(2040, 1, 1, tzinfo=timezone.utc))


def test_official_20261024_late_latest_is23_and_never20():
    closure = object.__new__(daily.LocalCopyLateCompletedSessionClosure)
    now = datetime(2026, 10, 23, 18, 32, tzinfo=timezone.utc)
    assert closure._latest_completed(now) == date(2026, 10, 23)
    assert closure._late_reasons(date(2026, 10, 23), now, now, admission=True) == []
    assert 'LATE_BUSINESS_NOT_LATEST_COMPLETED_AT_CUTOFF' in closure._late_reasons(date(2026, 10, 20), now, now, admission=True)


def test_late_policy_retry_real_clock_and_late_context_are_all_bound(copied, service):
    archive, original, current, value, manifest = copied
    fixed = datetime.now(timezone.utc) + timedelta(days=2)
    closure = daily.LocalCopyLateCompletedSessionClosure(service, local_copy_manifest_ref=manifest,
        clock=lambda: fixed, verification_profile='FULL_ARCHIVE_900S_V1')
    policy = closure._policy_identity()
    assert policy['version'] == 'SHORT_HORIZON_LOCAL_COPY_LATE_COMPLETED_V1'
    assert policy['source_protocol'] == local.protocol_identity(manifest, value['copy_root_reference'])
    assert policy['execution_mode'] == daily.LATE_MODE
    assert closure._session_options()['local_copy_manifest_ref'] == manifest
    closure._check_retry_profile({'policy': deepcopy(policy)})
    bad = deepcopy(policy); bad['source_protocol']['manifest_sha256'] = '0' * 64
    with pytest.raises(Exception, match='SOURCE_PROTOCOL_MISMATCH'):
        closure._check_retry_profile({'policy': bad})
    context = closure._execution_context(dict(policy=policy, business_session='2026-10-23', evaluated_at=fixed.isoformat()), fixed)
    assert context['late_processing'] is True and context['current_session_acceptance'] is False
    assert context['knowledge_cutoff'] == fixed.isoformat() and context['business_session'] == '2026-10-23'
    assert context['source_protocol'] == policy['source_protocol'] and context['original_result_physical_ownership'] == 'LOST_NOT_RESTORED'
    service.allow_test_fixture = False
    with pytest.raises(ValueError, match='LOCAL_COPY_REAL_CLOCK_REQUIRED'): closure.plan_late([], 'bad', business_session='2026-10-23', evaluated_at=fixed)
    with pytest.raises(ValueError, match='LOCAL_COPY_REAL_CLOCK_REQUIRED'): closure._validated_evaluation({})
    with pytest.raises(ValueError, match='LOCAL_COPY_REAL_CLOCK_REQUIRED'): closure._submit_plan({})


def test_cli_local_late_and_archive_opt_in_are_separate(tmp_path):
    args = ['r2-local-copy-late-plan', '--db', str(tmp_path / 'db'), '--state', str(tmp_path),
        '--run-id', 'TEST_FIXTURE', '--input-id', 'market_TEST_FIXTURE', '--business-session', '2026-10-23',
        '--knowledge-cutoff', '2026-10-23T18:32:00+00:00']
    with pytest.raises(SystemExit): cli.parser().parse_args(args)
    parsed = cli.parser().parse_args(args + ['--source-protocol', local.PROFILE,
        '--local-copy-manifest', str(tmp_path / 'map'), '--local-copy-manifest-sha256', '0' * 64])
    assert parsed.command == 'r2-local-copy-late-plan'
    default = cli.parser().parse_args(['r2-archive-verify', str(tmp_path), '--expected-final-sha256', '0' * 64])
    assert default.source_protocol is default.local_copy_manifest is default.restoration_manifest is None


def test_cli_cycle_missing_late_context_rejected_before_import(tmp_path, monkeypatch):
    args = cli.parser().parse_args(['r2-daily-cycle', str(tmp_path), '--expected-final-sha256', '0' * 64,
        '--db', str(tmp_path / 'db'), '--state', str(tmp_path), '--run-id', 'TEST_FIXTURE',
        '--source-protocol', local.PROFILE, '--local-copy-manifest', str(tmp_path / 'map'),
        '--local-copy-manifest-sha256', '0' * 64])
    monkeypatch.setattr(local, 'manifest_reference', lambda *args: {'TEST_FIXTURE': True})
    monkeypatch.setattr(bridge, 'authenticate_archive', lambda *args, **kwargs: pytest.fail('SOURCE_AUTH_MUST_NOT_START'))
    with pytest.raises(ValueError, match='EXPLICIT_LATE_SESSION_AND_CUTOFF_REQUIRED'):
        cli.execute(args)


def local_late_archives(tmp_path, service, monkeypatch):
    from zoneinfo import ZoneInfo
    from stockfish_core import observed_data
    from stockfish_core.data import TradingCalendar
    from tests.support import short_horizon_archive_fixture as fixture_support
    anchor = datetime.now(ZoneInfo('Asia/Shanghai')).date()
    start, stop = anchor - timedelta(days=250), anchor + timedelta(days=90)
    axis = tuple(start + timedelta(days=n) for n in range((stop - start).days + 1)
        if (start + timedelta(days=n)).weekday() < 5)
    schedule = TradingCalendar(axis, start, stop)
    monkeypatch.setattr(observed_data, 'exchange_calendar_2026', lambda: schedule)
    monkeypatch.setattr(daily, 'exchange_calendar_2026', lambda: schedule)
    business = next(day for day in axis if day >= anchor + timedelta(days=30) and day.weekday() == 4)
    index = axis.index(business)
    evaluated = datetime.combine(business + timedelta(days=1), datetime.min.time(), timezone.utc) + timedelta(hours=10)
    class ArchiveClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return (evaluated - timedelta(minutes=1)).astimezone(tz or timezone.utc)
    class VerifierClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return evaluated.astimezone(tz or timezone.utc)
    monkeypatch.setattr(fixture_support, 'datetime', ArchiveClock)
    monkeypatch.setattr(bridge, 'datetime', VerifierClock)
    archives = [fixture_archive(tmp_path / ('TEST_FIXTURE-' + role), observation_date=day)
        for role, day in [('old', axis[index - 6]), ('second', axis[index - 5]), ('current', business)]]
    root = tmp_path / 'TEST_FIXTURE-mixed-objects'; root.mkdir()
    scopes, currents = [], []
    for number, archive in enumerate(archives[:2]):
        original = deepcopy(next(item['result'] for item in archive['items']
            if json.loads(Path(item['result']['path']).read_bytes())['endpoint'] == 'market_daily'))
        current = locked_copy(original, root / (str(number) + '.json'))
        currents.append(current); Path(original['path']).unlink()
        scopes.append(dict(archive_final_sha256=archive['final_sha256'],
            results=[dict(original_reference=original, current_reference=current)]))
    value = dict(schema_version=local.PROFILE, copy_root_reference=local.root_reference(root), archives=scopes)
    manifest = write_manifest(tmp_path / 'TEST_FIXTURE-mixed-map.json', value)
    certs = []
    for number, archive in enumerate(archives):
        cap = authenticate(archive, manifest if number < 2 else None)
        certs.append(bridge.commit_bridge(service, cap, **({'local_copy_manifest_ref': manifest} if number < 2 else {})))
    return archives, certs, currents, value, manifest, evaluated, business


def test_full_fixture_late_single_double_idempotence_mixed_current_and_products(tmp_path, service, monkeypatch):
    try:
        archives, certs, currents, value, manifest, evaluated, business = local_late_archives(tmp_path, service, monkeypatch)
        assert all('source_protocol' in cert for cert in certs[:2]) and 'source_protocol' not in certs[2]
        closure = daily.LocalCopyLateCompletedSessionClosure(service, local_copy_manifest_ref=manifest,
            clock=lambda: evaluated, verification_profile='FULL_ARCHIVE_900S_V1')
        options = dict(business_session=business, evaluated_at=evaluated)
        first = complete(service, closure.submit_late([certs[0]['run_id']], certs[2]['input_id'], **options)['job_id'])
        assert first['state'] == 'SUCCEEDED', first.get('error')
        ledger = closure.status(first['job_id'])['ledger']
        protocol = local.protocol_identity(manifest, value['copy_root_reference'])
        assert ledger['source_protocol'] == protocol and ledger['late_processing'] is True
        assert ledger['business_session'] == str(business) and ledger['knowledge_cutoff'] == evaluated.isoformat()
        assert len(ledger['bindings']['archive_seals']) == 2
        assert ledger['archive_verification']['terminal_rereads'] == 3
        assert ledger['archive_verification']['result_local_copies']
        before = {kind: service.store.get_document(ns, first['result']['revision_ids'][0]) for kind, ns in daily.NAMESPACES.items()}
        same = closure.submit_late([certs[0]['run_id']], certs[2]['input_id'], **options)
        assert same['job_id'] == first['job_id'] and same['state'] == 'SUCCEEDED'
        double = complete(service, closure.submit_late([cert['run_id'] for cert in certs[:2]], certs[2]['input_id'], **options)['job_id'])
        assert double['state'] == 'SUCCEEDED', double.get('error')
        assert len(closure.status(double['job_id'])['ledger']['bindings']['archive_seals']) == 3
        assert closure.status(double['job_id'])['ledger']['archive_verification']['terminal_rereads'] == 4
        for kind, ns in daily.NAMESPACES.items():
            assert service.store.get_document(ns, first['result']['revision_ids'][0]) == before[kind]
        assert service.strategy_outcomes(certs[0]['run_id'])['revision_count'] == 1
    finally:
        clear_fixture_flags(tmp_path)


def test_full_fixture_worker_last_terminal_copy_replacement_rolls_back(tmp_path, service, monkeypatch):
    import threading
    try:
        archives, certs, currents, value, manifest, evaluated, business = local_late_archives(tmp_path, service, monkeypatch)
        closure = daily.LocalCopyLateCompletedSessionClosure(service, local_copy_manifest_ref=manifest,
            clock=lambda: evaluated, verification_profile='FULL_ARCHIVE_900S_V1')
        with service.store.transaction(write=False) as connection:
            before = {(row['namespace'], row['document_id']): row['payload'] for row in connection.execute('SELECT * FROM documents')}
        original_terminal, main, calls = bridge.ArchiveVerificationSession.terminal, threading.get_ident(), []
        def terminal(session):
            if threading.get_ident() != main:
                calls.append(session.terminal_count)
                if len(calls) == 4: replace_copy(currents[0])
            return original_terminal(session)
        monkeypatch.setattr(bridge.ArchiveVerificationSession, 'terminal', terminal)
        done = complete(service, closure.submit_late([certs[0]['run_id']], certs[2]['input_id'],
            business_session=business, evaluated_at=evaluated)['job_id'])
        assert done['state'] == 'FAILED' and calls == [0, 1, 2, 3]
        with service.store.transaction(write=False) as connection:
            after = {(row['namespace'], row['document_id']): row['payload'] for row in connection.execute('SELECT * FROM documents')}
        assert after == before and closure.status(done['job_id'])['ledger'] is None
    finally:
        clear_fixture_flags(tmp_path)


def test_local_copy_pin_nanosecond_cutoff_preserved(copied, service, monkeypatch):
    archive, original, current, value, manifest = copied
    closure = daily.LocalCopyLateCompletedSessionClosure(service, local_copy_manifest_ref=manifest)
    cutoff = datetime.now(timezone.utc) + timedelta(seconds=2)
    monkeypatch.setattr(daily.ShortHorizonDailyClosure, '_authenticated', lambda *args, **kwargs: 'TEST_FIXTURE_PARENT_RESULT')
    session = SimpleNamespace(audit=lambda: {'result_local_copies': [{'current_reference': current}]})
    assert closure._authenticated([], 'fixture', session, sealed_cutoff=cutoff) == 'TEST_FIXTURE_PARENT_RESULT'
    delta = cutoff - datetime(1970, 1, 1, tzinfo=timezone.utc)
    ns = ((delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds) * 1000
    altered = deepcopy(current); altered['fp']['st_ctime_ns'] = ns + 1
    with pytest.raises(ValueError, match='PHYSICAL_PIN_AFTER_CUTOFF'):
        closure._authenticated([], 'fixture', SimpleNamespace(audit=lambda: {'result_local_copies': [{'current_reference': altered}]}), sealed_cutoff=cutoff)


def test_late_rechecks_after_authentication_and_refuses_stale_input(copied, service, monkeypatch):
    from stockfish_core.data import TradingCalendar
    archive, original, current, value, manifest = copied
    now = datetime.now(timezone.utc) + timedelta(days=30)
    friday = now.date() + timedelta(days=(4 - now.weekday()) % 7)
    evaluated = datetime.combine(friday + timedelta(days=1), datetime.min.time(), timezone.utc) + timedelta(hours=10)
    axis = tuple(friday + timedelta(days=n) for n in (-10, -7, -6, -5, -4, -3, 0, 3))
    monkeypatch.setattr(daily, 'exchange_calendar_2026', lambda: TradingCalendar(axis, axis[0], friday + timedelta(days=5)))
    clock = [evaluated]
    closure = daily.LocalCopyLateCompletedSessionClosure(service, local_copy_manifest_ref=manifest, clock=lambda: clock[0])
    calendar = SimpleNamespace(sessions=(friday,), observation_date=friday)
    monkeypatch.setattr(closure, '_authenticated', lambda *args, **kwargs: ([], calendar, {}))
    monkeypatch.setattr(closure, '_schedule', lambda *args: [])
    def terminal(session):
        clock[0] = datetime.combine(friday + timedelta(days=3), datetime.min.time(), timezone.utc) + timedelta(hours=10)
        return {'TEST_FIXTURE': True}
    monkeypatch.setattr(closure, '_terminal', terminal)
    plan = closure.plan_late(['TEST_FIXTURE-run'], 'TEST_FIXTURE-input', business_session=friday, evaluated_at=evaluated)
    assert 'LATE_BUSINESS_NOT_LATEST_COMPLETED_AT_ADMISSION' in plan['reason_codes'] and not plan['can_submit']
    clock[0] = evaluated
    calendar.observation_date = friday - timedelta(days=3); calendar.sessions = (calendar.observation_date,)
    monkeypatch.setattr(closure, '_terminal', lambda session: {'TEST_FIXTURE': True})
    plan = closure.plan_late(['TEST_FIXTURE-run'], 'TEST_FIXTURE-input', business_session=friday, evaluated_at=evaluated)
    assert 'LATE_INPUT_BUSINESS_SESSION_MISMATCH' in plan['reason_codes'] and not plan['can_submit']
