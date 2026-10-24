"""TEST_FIXTURE byte recovery: original owner stays lost; default V1 refuses."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

from stockfish_core.yaogu import short_horizon_archive_bridge_v1 as bridge
from stockfish_core.yaogu import short_horizon_daily_closure_v1 as daily
from stockfish_core.yaogu import r2_closure_cli as cli
from stockfish_core.yaogu import premarket_workflow_v1 as workflow
from stockfish_core.yaogu.sealed_result_restoration_v1 import PROFILE, protocol_identity
from tests.support.short_horizon_archive_fixture import fixture_archive
from tests.yaogu.test_short_horizon_archive_bridge_v1 import service, _offline_fixture_document


def ref(path):
    return workflow._ref(path)


def write_manifest(path, value):
    path.write_text(json.dumps(value, sort_keys=True), encoding='utf-8')
    return ref(path)


def rematerialize(original):
    path = Path(original['path']); raw = path.read_bytes()
    replacement = path.with_name(path.name + '.TEST_FIXTURE-recovered')
    replacement.write_bytes(raw); os.replace(replacement, path)
    current = ref(path)
    assert current['sha256'] == original['sha256'] and current['bytes'] == original['bytes']
    assert current['fp'] != original['fp']
    return current


@pytest.fixture
def recovered(tmp_path):
    archive = fixture_archive(tmp_path / 'TEST_FIXTURE-original')
    item = next(i for i in archive['items'] if json.loads(Path(i['result']['path']).read_bytes())['endpoint'] == 'market_daily')
    original = deepcopy(item['result']); current = rematerialize(original)
    value = dict(schema_version=PROFILE, archives=[dict(archive_final_sha256=archive['final_sha256'],
        results=[dict(original_reference=original, current_reference=current)])])
    manifest = write_manifest(tmp_path / 'TEST_FIXTURE-restoration.json', value)
    return archive, original, current, value, manifest


def authenticate(archive, manifest=None, **kwargs):
    options = {'restoration_manifest_ref': manifest} if manifest is not None else {}
    session = bridge.ArchiveVerificationSession(**options)
    return bridge.authenticate_archive(archive['job'], expected_final_sha256=archive['final_sha256'],
        session=session, allow_test_fixture=True, **kwargs)


def test_explicit_result_restoration_preserves_loss_and_complete_originals(recovered, service):
    archive, original, current, value, manifest = recovered
    with pytest.raises((ValueError, FileNotFoundError)):
        authenticate(archive)
    cap = authenticate(archive, manifest)
    audit = cap.audit(); protocol = protocol_identity(manifest)
    assert audit['source_protocol'] == protocol
    assert audit['original_result_physical_ownership'] == 'LOST_NOT_RESTORED'
    assert audit['run_document_hash'] == bridge.digest_json_stream(archive['run'])
    assert audit['input_hash'] == archive['run']['input_hash']
    assert audit['verification_budget']['source_protocol'] == protocol
    assert audit['verification_budget']['result_restorations'] == [dict(original_reference=original,
        current_reference=current, original_physical_identity_status='LOST_NOT_RESTORED',
        proof_mode='SEALED_BYTES_AND_CURRENT_PHYSICAL_PIN')]
    with pytest.raises(ValueError, match='EXPLICIT_RESULT_RESTORATION_COMMIT_REQUIRED'):
        bridge.commit_bridge(service, cap)
    cert = bridge.commit_bridge(service, cap, restoration_manifest_ref=manifest)
    assert cert['source_protocol'] == protocol
    assert service.store.get_document('strategy_experiment_runs', cert['run_id']) == archive['run']
    # An ordinary service is not implicitly authorized by the stored JSON.
    ordinary = SimpleNamespace(store=service.store)
    with pytest.raises(ValueError, match='EXPLICIT_MATCHING_RESULT_RESTORATION_PROTOCOL_REQUIRED'):
        bridge.saved_csv_export(ordinary, archive['run'], archive['run']['strategy_id'], 'all')
    assert bridge.saved_csv_export(service, archive['run'], archive['run']['strategy_id'], 'all') == Path(archive['artifacts']['all.csv']['path']).read_bytes().decode('utf-8')
    with pytest.raises(ValueError, match='EXPLICIT_MATCHING_RESULT_RESTORATION_PROTOCOL_REQUIRED'):
        bridge.require_verified_bridge(service, cert['run_id'])
    session = bridge.ArchiveVerificationSession(restoration_manifest_ref=manifest)
    verified = bridge.require_verified_bridge(service, cert['run_id'], session=session)
    assert verified['source_protocol'] == protocol
    with service.store.transaction(write=False) as connection:
        sources = [json.loads(r['payload']) for r in connection.execute('SELECT payload FROM documents WHERE namespace=?', (bridge.SOURCES,))]
    proof = next(x for x in sources if x.get('result_restoration'))
    assert proof['originals']['result'] == original and proof['result_restoration']['current_reference'] == current
    assert original['fp'] != current['fp']
    session.terminal()


@pytest.mark.parametrize('change', ['wrong_version','extra_field','empty_archives','duplicate_archive','empty_results',
    'duplicate_result','different_path','different_sha','different_size','same_fp','extra_entry_field'])
def test_malformed_manifest_rejected_before_authority(recovered, tmp_path, change):
    archive, original, current, value, manifest = recovered
    value = deepcopy(value); scope = value['archives'][0]; entry = scope['results'][0]
    if change == 'wrong_version': value['schema_version'] = 'V1'
    elif change == 'extra_field': value['skip_physical'] = True
    elif change == 'empty_archives': value['archives'] = []
    elif change == 'duplicate_archive': value['archives'].append(deepcopy(scope))
    elif change == 'empty_results': scope['results'] = []
    elif change == 'duplicate_result': scope['results'].append(deepcopy(entry))
    elif change == 'different_path': entry['current_reference']['path'] += '-other'
    elif change == 'different_sha': entry['current_reference']['sha256'] = '0' * 64
    elif change == 'different_size': entry['current_reference']['bytes'] += 1; entry['current_reference']['fp']['st_size'] += 1
    elif change == 'same_fp': entry['current_reference'] = deepcopy(original)
    else: entry['role'] = 'raw'
    changed = write_manifest(tmp_path / 'TEST_FIXTURE-bad-map.json', value)
    with pytest.raises(ValueError): authenticate(archive, changed)


@pytest.mark.parametrize('kind', ['raw','receipt','producer','core_artifact','configuration'])
def test_map_cannot_authorize_non_result_roles(recovered, tmp_path, kind):
    archive, original, current, value, manifest = recovered
    if kind in {'raw','receipt'}: target = archive['items'][0][kind]
    elif kind == 'producer': target = archive['registry'][0]
    elif kind == 'core_artifact': target = archive['artifacts']['all.csv']
    else: target = archive['final']['configuration']
    fake = deepcopy(target); fake['fp']['st_ino'] += 1
    value['archives'][0]['results'].append(dict(original_reference=target, current_reference=fake))
    changed = write_manifest(tmp_path / 'TEST_FIXTURE-wrong-role.json', value)
    with pytest.raises(ValueError, match='ONLY_EXACT_SEALED_CORE_RESULT_ALLOWED'):
        authenticate(archive, changed)


def test_changed_old_ref_or_unlisted_required_recovery_cannot_pass(recovered, tmp_path):
    archive, original, current, value, manifest = recovered
    value['archives'][0]['results'][0]['original_reference']['fp']['st_ino'] += 1
    changed = write_manifest(tmp_path / 'TEST_FIXTURE-old-ref.json', value)
    with pytest.raises(ValueError, match='ONLY_EXACT_SEALED_CORE_RESULT_ALLOWED'):
        authenticate(archive, changed)
    other = archive['items'][0]['result']; rematerialize(other)
    with pytest.raises(ValueError): authenticate(archive, manifest)


def test_scoped_manifest_other_archive_is_not_authority(recovered, tmp_path):
    archive, original, current, value, manifest = recovered
    value['archives'][0]['archive_final_sha256'] = '0' * 64
    changed = write_manifest(tmp_path / 'TEST_FIXTURE-wrong-seal.json', value)
    with pytest.raises(ValueError): authenticate(archive, changed)


@pytest.mark.parametrize('kind', ['raw','receipt','configuration','core_artifact'])
def test_valid_recovery_never_weakens_original_other_role_physical_pin(recovered, kind):
    archive, original, current, value, manifest = recovered
    target = (archive['items'][0][kind] if kind in {'raw','receipt'} else
              archive['final']['configuration'] if kind == 'configuration' else archive['artifacts']['all.csv'])
    rematerialize(target)
    with pytest.raises(ValueError): authenticate(archive, manifest)


@pytest.mark.parametrize('mutation', ['changed_bytes','same_bytes_new_inode','manifest_changed'])
def test_all_recovered_bytes_and_map_remain_required_terminal_sources(recovered, mutation):
    archive, original, current, value, manifest = recovered
    cap = authenticate(archive, manifest); session = bridge._state(cap)['session']
    if mutation == 'changed_bytes':
        p = Path(current['path']); raw = p.read_bytes(); p.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    elif mutation == 'same_bytes_new_inode': rematerialize(current)
    else:
        p = Path(manifest['path']); p.write_bytes(p.read_bytes() + b' ')
    with pytest.raises(ValueError): session.terminal()


def test_live_manifest_mutation_or_symlink_refused(recovered, tmp_path):
    archive, original, current, value, manifest = recovered
    session = bridge.ArchiveVerificationSession(restoration_manifest_ref=manifest)
    session._restoration.archives[archive['final_sha256']][original['path']]['current_reference']['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='IN_MEMORY_MANIFEST_CHANGED'): session.terminal()
    p = Path(current['path']); other = tmp_path / 'TEST_FIXTURE-copy'; other.write_bytes(p.read_bytes())
    p.unlink(); p.symlink_to(other)
    with pytest.raises(ValueError): authenticate(archive, manifest)


def test_wrong_protocol_default_and_cache_proofs_cannot_be_mixed(recovered, service):
    archive, original, current, value, manifest = recovered
    cap = authenticate(archive, manifest); cert = bridge.commit_bridge(service, cap, restoration_manifest_ref=manifest)
    session = bridge.ArchiveVerificationSession(restoration_manifest_ref=manifest)
    view = bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=session, borrow_run=True)
    key = bridge._verified_view_key(service, bridge.RUN_BINDINGS, cert['run_id'], session)
    proof = session._verified_views[key]
    assert key[-1] == bridge.digest_json_stream(protocol_identity(manifest))
    ordinary = bridge.ArchiveVerificationSession()
    ordinary._verified_views[(id(service.store), bridge.RUN_BINDINGS, cert['run_id'])] = proof
    with pytest.raises(ValueError):
        bridge.authenticated_bridge_view(service, run_id=cert['run_id'], session=ordinary, borrow_run=True)
    with pytest.raises(ValueError): bridge._verified_second_run_decode(proof, ordinary)
    proof['source_protocol_context'] = None
    with pytest.raises(ValueError, match='SOURCE_PROTOCOL_CHANGED'):
        bridge.load_verified_bridge_input(service, cert['input_id'], session=session)


def test_restored_closure_policy_is_explicit_distinct_and_retry_bound(recovered, service):
    archive, original, current, value, manifest = recovered
    restored = daily.RestoredResultDailyClosure(service, restoration_manifest_ref=manifest,
                                               verification_profile='FULL_ARCHIVE_900S_V1')
    normal = daily.ShortHorizonDailyClosure(service, verification_profile='FULL_ARCHIVE_900S_V1')
    policy = restored._policy_identity()
    assert policy['version'] == restored.version != normal._policy_identity()['version']
    assert policy['source_protocol'] == protocol_identity(manifest)
    assert restored.job_kind == 'short_horizon_restored_result_daily_v1'
    assert restored.ledgers == 'short_horizon_restored_result_daily_ledgers_v1'
    assert restored.job_prefix == 'short_restored_daily_'
    assert restored._session_options()['restoration_manifest_ref'] == manifest
    request = {'policy': deepcopy(policy)}
    restored._check_retry_profile(request)
    request['policy']['source_protocol']['manifest_sha256'] = '0' * 64
    with pytest.raises(Exception, match='SOURCE_PROTOCOL_MISMATCH'): restored._check_retry_profile(request)


def test_cli_requires_explicit_protocol_and_manifest_pin(tmp_path):
    args = ['r2-restored-daily-run','--db',str(tmp_path/'db'),'--state',str(tmp_path),
            '--run-id','TEST_FIXTURE','--input-id','market_TEST_FIXTURE']
    with pytest.raises(SystemExit): cli.parser().parse_args(args)
    parsed = cli.parser().parse_args(args + ['--source-protocol',PROFILE,'--restoration-manifest',str(tmp_path/'map'),
        '--restoration-manifest-sha256','0'*64])
    assert parsed.command == 'r2-restored-daily-run'
    ordinary = cli.parser().parse_args(['r2-archive-verify',str(tmp_path), '--expected-final-sha256','0'*64])
    assert ordinary.source_protocol is ordinary.restoration_manifest is ordinary.restoration_manifest_sha256 is None


def test_import_transaction_last_terminal_rolls_back_same_byte_replacement(recovered, service, monkeypatch):
    archive, original, current, value, manifest = recovered
    cap = authenticate(archive, manifest)
    service.store.put_document('TEST_FIXTURE_PROTECTED', 'prior', {'preserve': True})
    put = service.store._put_document; changed = False
    def observed_put(connection, namespace, document_id, value):
        nonlocal changed
        result = put(connection, namespace, document_id, value)
        if namespace == bridge.CERTIFICATES and not changed:
            changed = True; rematerialize(current)
        return result
    monkeypatch.setattr(service.store, '_put_document', observed_put)
    with pytest.raises(ValueError):
        bridge.commit_bridge(service, cap, restoration_manifest_ref=manifest)
    with service.store.transaction(write=False) as connection:
        assert [(r['namespace'], r['document_id']) for r in connection.execute('SELECT namespace,document_id FROM documents')] == [('TEST_FIXTURE_PROTECTED', 'prior')]
    assert changed


def complete(service, identifier):
    end = time.monotonic() + 120
    while time.monotonic() < end:
        job = service.jobs.get(identifier)
        if job['state'] in {'SUCCEEDED','FAILED','CANCELLED','INTERRUPTED'}:
            return job
        time.sleep(.005)
    raise AssertionError('TEST_FIXTURE_WORKER_TIMEOUT')


def three_archives(tmp_path, service, monkeypatch):
    # Fully synthetic future-session fixture clocks keep these trust-contract
    # tests independent of the actual weekday. Physical file timestamps remain
    # untouched and precede the declared fixture cutoff. Never used for real acceptance.
    from stockfish_core import observed_data
    from stockfish_core.data import TradingCalendar
    from zoneinfo import ZoneInfo
    from tests.support import short_horizon_archive_fixture as fixture_support
    # This is an explicitly synthetic calendar, never an extension of the
    # production official2026 authority. Its finite axis also works across years.
    anchor=datetime.now(ZoneInfo('Asia/Shanghai')).date()
    start,stop=anchor-timedelta(days=250),anchor+timedelta(days=60)
    calendar=tuple(start+timedelta(days=n) for n in range((stop-start).days+1)
        if (start+timedelta(days=n)).weekday()<5)
    schedule=TradingCalendar(calendar,start,stop)
    monkeypatch.setattr(observed_data,'exchange_calendar_2026',lambda:schedule)
    monkeypatch.setattr(daily,'exchange_calendar_2026',lambda:schedule)
    target=next(day for day in calendar if day>=anchor+timedelta(days=30))
    index=calendar.index(target)
    evaluated=datetime(target.year,target.month,target.day,15,tzinfo=timezone.utc)
    class FixtureArchiveClock(datetime):
        @classmethod
        def now(cls,tz=None):
            return (evaluated-timedelta(minutes=1)).astimezone(tz or timezone.utc)
    class FixtureVerifierClock(datetime):
        @classmethod
        def now(cls,tz=None):
            return evaluated.astimezone(tz or timezone.utc)
    monkeypatch.setattr(fixture_support,'datetime',FixtureArchiveClock)
    monkeypatch.setattr(bridge,'datetime',FixtureVerifierClock)
    originals = [fixture_archive(tmp_path / ('TEST_FIXTURE-' + role), observation_date=day)
        for role,day in [('old',calendar[index-6]),('second',calendar[index-5]),('current',target)]]
    scopes = []; currents = []
    for archive in originals[:2]:
        old = deepcopy(next(i['result'] for i in archive['items']
            if json.loads(Path(i['result']['path']).read_bytes())['endpoint'] == 'market_daily'))
        current = rematerialize(old);currents.append(current)
        scopes.append(dict(archive_final_sha256=archive['final_sha256'],
            results=[dict(original_reference=old,current_reference=current)]))
    manifest = write_manifest(tmp_path/'TEST_FIXTURE-multi.json',dict(schema_version=PROFILE,archives=scopes))
    certificates = []
    for index,archive in enumerate(originals):
        cap = authenticate(archive, manifest if index < 2 else None)
        certificates.append(bridge.commit_bridge(service,cap,
            **({'restoration_manifest_ref':manifest} if index<2 else {})))
    return originals,certificates,currents,manifest,evaluated


def test_real_fixture_single_double_idempotence_mixed_strict_current_and_exact_reuse(tmp_path, service, monkeypatch):
    archives,certs,currents,manifest,evaluated = three_archives(tmp_path,service,monkeypatch)
    assert all('source_protocol' in c for c in certs[:2]) and 'source_protocol' not in certs[2]
    closure = daily.RestoredResultDailyClosure(service,restoration_manifest_ref=manifest,
        clock=lambda:evaluated,verification_profile='FULL_ARCHIVE_900S_V1')
    run_ids = sorted(c['run_id'] for c in certs[:2]); current_id = certs[2]['input_id']
    # Strict default cannot consume restored certificates despite explicit service CSV authorization.
    normal = daily.ShortHorizonDailyClosure(service,clock=lambda:evaluated)
    with pytest.raises(ValueError,match='EXPLICIT_MATCHING_RESULT_RESTORATION_PROTOCOL_REQUIRED'):
        normal.plan([certs[0]['run_id']],current_id,evaluated_at=evaluated)
    first = closure.submit([certs[0]['run_id']],current_id,evaluated_at=evaluated)
    first = complete(service,first['job_id'])
    assert first['state'] == 'SUCCEEDED'
    status = closure.status(first['job_id']);ledger=status['ledger']
    assert ledger['schema_version']==closure.version and ledger['source_protocol']==protocol_identity(manifest)
    assert len(ledger['bindings']['archive_seals'])==2
    assert ledger['archive_verification']['terminal_rereads']==3
    assert ledger['archive_verification']['source_protocol']==protocol_identity(manifest)
    assert ledger['original_result_physical_ownership']=='LOST_NOT_RESTORED'
    before = {k: service.store.get_document(ns,first['result']['revision_ids'][0])
        for k,ns in daily.NAMESPACES.items()}
    again = closure.submit([certs[0]['run_id']],current_id,evaluated_at=evaluated)
    assert again['job_id']==first['job_id'] and again['state']=='SUCCEEDED'
    second = closure.submit(run_ids,current_id,evaluated_at=evaluated)
    done=complete(service,second['job_id']);assert done['state']=='SUCCEEDED',done.get('error')
    assert len(closure.status(second['job_id'])['ledger']['bindings']['archive_seals'])==3
    assert closure.status(second['job_id'])['ledger']['archive_verification']['terminal_rereads']==4
    for kind,ns in daily.NAMESPACES.items():
        assert service.store.get_document(ns,first['result']['revision_ids'][0])==before[kind]
    assert service.strategy_outcomes(certs[0]['run_id'])['revision_count']==1
    for cert,archive in zip(certs,archives):
        assert bridge.saved_csv_export(service,archive['run'],archive['run']['strategy_id'],'all') == Path(archive['artifacts']['all.csv']['path']).read_bytes().decode()


def test_worker_transaction_last_terminal_rejects_same_byte_new_inode_atomically(tmp_path,service,monkeypatch):
    import threading
    archives,certs,currents,manifest,evaluated = three_archives(tmp_path,service,monkeypatch)
    closure=daily.RestoredResultDailyClosure(service,restoration_manifest_ref=manifest,
        clock=lambda:evaluated,verification_profile='FULL_ARCHIVE_900S_V1')
    with service.store.transaction(write=False) as c:
        before={(r['namespace'],r['document_id']):r['payload'] for r in c.execute('SELECT * FROM documents')}
    original_terminal=bridge.ArchiveVerificationSession.terminal; worker_calls=[]; main=threading.get_ident()
    def terminal(session):
        if threading.get_ident()!=main:
            worker_calls.append(session.terminal_count)
            if len(worker_calls)==4: rematerialize(currents[0])
        return original_terminal(session)
    monkeypatch.setattr(bridge.ArchiveVerificationSession,'terminal',terminal)
    job=closure.submit([certs[0]['run_id']],certs[2]['input_id'],evaluated_at=evaluated)
    done=complete(service,job['job_id']);assert done['state']=='FAILED'
    assert worker_calls==[0,1,2,3]
    with service.store.transaction(write=False) as c:
        after={(r['namespace'],r['document_id']):r['payload'] for r in c.execute('SELECT * FROM documents')}
    assert after==before and closure.status(job['job_id'])['ledger'] is None


def test_current_physical_pin_time_cannot_be_after_declared_cutoff(recovered,service):
    archive, original, current, value, manifest = recovered
    closure=daily.RestoredResultDailyClosure(service,restoration_manifest_ref=manifest)
    evaluated=datetime.now(timezone.utc)+timedelta(seconds=1)
    session=SimpleNamespace(audit=lambda:{'result_restorations':[{'current_reference':current}]})
    closure._check_restoration_cutoff(session,evaluated)
    delta=evaluated-datetime(1970,1,1,tzinfo=timezone.utc)
    exact_ns=((delta.days*86400+delta.seconds)*1000000+delta.microseconds)*1000
    altered=deepcopy(current);altered['fp']['st_ctime_ns']=exact_ns+1
    with pytest.raises(ValueError,match='PHYSICAL_PIN_AFTER_CUTOFF'):
        closure._check_restoration_cutoff(SimpleNamespace(audit=lambda:{'result_restorations':[{'current_reference':altered}]}),evaluated)


def test_default_capability_cannot_be_rebound_to_another_source_protocol(recovered):
    archive,original,current,value,manifest=recovered
    cap=authenticate(archive,manifest);session=bridge._state(cap)['session']
    session._restoration_reference=None
    with pytest.raises(ValueError,match='SOURCE_PROTOCOL_CHANGED'):
        cap.audit()


@pytest.mark.parametrize('strip_every_marker',[False,True])
def test_csv_cannot_silently_downgrade_when_protocol_metadata_is_removed(recovered,service,strip_every_marker):
    archive,original,current,value,manifest=recovered
    cap=authenticate(archive,manifest)
    cert=bridge.commit_bridge(service,cap,restoration_manifest_ref=manifest)
    cert.pop('source_protocol')
    if strip_every_marker:
        cert.pop('original_result_physical_ownership')
        cert['verification_budget'].pop('source_protocol')
        cert['verification_budget'].pop('result_restorations')
    _offline_fixture_document(service,bridge.CERTIFICATES,cert['bridge_id'],cert)
    for ns,key in [(bridge.RUN_BINDINGS,cert['run_id']),(bridge.INPUT_BINDINGS,cert['input_id'])]+[
            (bridge.CSV_EXPORTS,cert['run_id']+':'+selection) for selection in bridge.CSV_SELECTIONS]:
        payload=service.store.get_document(ns,key);payload.pop('source_protocol',None)
        _offline_fixture_document(service,ns,key,payload)
    ordinary=SimpleNamespace(store=service.store)
    with pytest.raises(ValueError,match='CSV_RESTORED_SOURCE_PROTOCOL_CANNOT_BE_REMOVED|CSV_SOURCE_PROTOCOL_BRIDGE_IDENTITY_MISMATCH'):
        bridge.saved_csv_export(ordinary,archive['run'],archive['run']['strategy_id'],'all')


@pytest.mark.parametrize('fixture_flag',[False,None,1,'True'])
def test_nonfixture_clock_override_rejected_before_any_manifest_or_database(fixture_flag):
    service=SimpleNamespace(allow_test_fixture=fixture_flag)
    with pytest.raises(ValueError,match='RESTORED_TEST_CLOCK_ONLY'):
        daily.RestoredResultDailyClosure(service,restoration_manifest_ref={'must_not_be_read':True},
            clock=lambda:datetime(2040,1,1,tzinfo=timezone.utc))


def test_fixture_clock_explicitly_allowed_but_runtime_nonfixture_mutation_refused(recovered,service):
    archive,original,current,value,manifest=recovered
    fixed=datetime(2040,1,1,tzinfo=timezone.utc)
    fixture=daily.RestoredResultDailyClosure(service,restoration_manifest_ref=manifest,clock=lambda:fixed)
    assert fixture.clock()==fixed
    actual=SimpleNamespace(allow_test_fixture=False)
    closure=daily.RestoredResultDailyClosure(actual,restoration_manifest_ref=manifest)
    assert closure.clock is daily.utc_now
    closure.clock=lambda:fixed
    with pytest.raises(ValueError,match='RESTORED_TEST_CLOCK_ONLY'): closure.plan([], 'TEST_FIXTURE')
    with pytest.raises(ValueError,match='RESTORED_TEST_CLOCK_ONLY'): closure._validated_evaluation({})
    with pytest.raises(ValueError,match='RESTORED_TEST_CLOCK_ONLY'): closure._check_retry_profile({'policy':{}})
