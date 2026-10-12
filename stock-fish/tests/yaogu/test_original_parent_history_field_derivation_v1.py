"""Isolated TEST_FIXTURE original trios, genuine history DB and native R2."""
from copy import copy, deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from stockfish_core.storage import SQLiteStore, IntegrityError
from stockfish_core.yaogu import original_parent_history_field_derivation_v1 as subject
from stockfish_core.yaogu import original_parent_late_derivation as original
from stockfish_core.yaogu.contracts import MarketInput, digest
from stockfish_core.yaogu.history_artifacts import HistoryArtifactStore
from stockfish_core.yaogu.history_artifacts import compact_input, digest_market_input, digest_json_stream
from stockfish_core.yaogu.saved_r2_delivery import _Registry
from tests.yaogu import test_original_parent_field_derivation_v2 as fixture

DAY=fixture.DAY
CUTOFF=fixture.CUTOFF
BASE_DAY=date(2026,9,29)


def archive(tmp_path,monkeypatch,*,partial_history=False,missing_history_day=False,
            corrupt_basis=False,revision=False,missing_ancestor=False,rounded_history=False):
    with monkeypatch.context() as patch:
        patch.setattr(fixture,'DAY',BASE_DAY)
        old=fixture.originals(tmp_path/'base',patch,partial=partial_history,
            raw_close='10.12345678901234567890123456789' if rounded_history else '10.5')
        base,_=original.normalize_original_parent_asof(old,BASE_DAY,CUTOFF)
    if missing_history_day:
        base=base.model_copy(update={'bars':tuple(b for b in base.bars if b.session!=base.sessions[-10])})
    if corrupt_basis:
        base=base.model_copy(update={'bars':tuple(b.model_copy(update={'comparable_basis':'TEST_FIXTURE_UNLINKED_BASIS'}) for b in base.bars)})
    db=tmp_path/'TEST_FIXTURE-history.sqlite3'
    store=SQLiteStore(db)
    base=compact_input(base,store)
    # Freeze this isolated archive in rollback mode, so readonly opening does
    # not create a previously absent zero-byte WAL sidecar.  Production keeps
    # its already registered WAL identity; the source guard is unchanged.
    con=sqlite3.connect(db);con.execute('PRAGMA journal_mode=DELETE');con.close()
    path=tmp_path/'TEST_FIXTURE-original-frozen.json'
    wrapper=dict(input=base.model_dump(mode='json'),cutoff=CUTOFF.isoformat(),parent_input=None,
                 purpose='TEST_FIXTURE',supplement=None,target_session=str(DAY))
    path.write_text(json.dumps(wrapper),encoding='utf-8')
    binding=subject.OriginalHistoryArtifact(path,hashlib.sha256(path.read_bytes()).hexdigest(),
        digest_market_input(base),CUTOFF.isoformat(),db,base.history_artifact_ref,base.history_artifact_hash)
    fresh=fixture.originals(tmp_path/'fresh',monkeypatch,partial=True,
        revision=revision,missing_ancestor=missing_ancestor)
    # History is available only from the genuine owner; current RAW + true
    # factor trios are sufficient to exercise field authority independently.
    items=[]
    for item in fresh:
        body=json.loads(item.result_path.read_bytes())
        if body['endpoint']!='market_daily' or body['parameters']['date']==str(DAY):items.append(item)
    root=items[0].archive_root
    (root/'request-queue.lock').write_bytes(b'')
    con=sqlite3.connect(root/'request-queue.sqlite3')
    con.execute('CREATE TABLE requests(request_id TEXT PRIMARY KEY,payload TEXT,state TEXT,attempts INTEGER,result_path TEXT,result_hash TEXT,failure_category TEXT,updated_at TEXT)')
    con.execute('CREATE TABLE attempts(request_id TEXT,attempt INTEGER,state TEXT,result_path TEXT,result_hash TEXT,recorded_at TEXT)')
    requests=[];attempts=[]
    for n,item in enumerate(items):
        body=json.loads(item.result_path.read_bytes());rid='TEST_FIXTURE_REQUEST_'+str(n)
        body['queue_request_id']=rid
        item.result_path.write_text(json.dumps(body),encoding='utf-8')
        items[n]=replace(item,result_sha256=hashlib.sha256(item.result_path.read_bytes()).hexdigest())
        locator='results/'+item.result_path.name;canonical=digest_json_stream(body)
        payload=json.dumps({k:body[k] for k in ('provider','endpoint','parameters')})
        request=dict(request_id=rid,payload=payload,state=body['status'],attempts=1,
            result_path=locator,result_hash=canonical,failure_category=None,updated_at=fixture.SEEN.isoformat())
        attempt=dict(request_id=rid,attempt=1,state=body['status'],result_path=locator,
            result_hash=canonical,recorded_at=fixture.SEEN.isoformat())
        requests.append(request);attempts.append(attempt)
        con.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?,?)',tuple(request.values()))
        con.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?)',tuple(attempt.values()))
    con.commit();con.close()
    requests.sort(key=lambda r:r['request_id']);attempts.sort(key=lambda r:(r['request_id'],r['attempt']))
    inventory=subject.OriginalAttemptInventory(root,len(requests),len(attempts),
        digest_json_stream(dict(requests=requests,attempts=attempts)))
    return binding,inventory,items


def run(data,**kwargs):
    return subject.run_original_parent_history_field_r2(*data,DAY,CUTOFF,
        datetime.now(timezone.utc)+timedelta(seconds=90),requested_target_date='2026-10-09',**kwargs)


def current(result,iid='000001.SZ'):
    return next(b for b in result['input']['bars'] if b['instrument_id']==iid and b['session']==str(DAY))


def test_whole_original_and_live_history_fill_native_window_without_old_capability(tmp_path,monkeypatch):
    data=archive(tmp_path,monkeypatch)
    before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.rglob('*') if p.is_file()}
    result=run(data)
    assert result['normalizer_calls']==result['native_R2_attempts']==1
    assert result['input']['history_artifact_ref'] is result['input']['history_artifact_hash'] is None
    assert result['original_source_audit']['history_window']['actual_payload_SHA_and_typed_checked_rows']>0
    assert result['original_source_audit']['history_window']['field_axis_counts']['HISTORY_COMPLETE_SAME_BASIS']>0
    assert result['history_read_metrics']['payload_rows_read']>0
    native=result['native_run']['results']['SHORT_HORIZON_STRENGTH_R2']
    assert native['counts']['current_source_unknown']==0
    assert native['counts']['complete_25_window']==2
    assert len(native['items'])==result['native_run']['qualification']['eligible']==2
    assert all(item['exact_features']['window_coverage']['selected_25_count']==25 for item in native['items'])
    assert Decimal(current(result)['amount_cny'])==10300
    assert Fraction(str(current(result)['turnover_fraction']))==Fraction(Decimal('5.1234'))/100
    assert Decimal(current(result)['comparable_close'])==Decimal('10.5')*Decimal('1.5')
    assert result['target_calendar_status']=='UNKNOWN'
    assert result['historical_PIT_count']==result['complete_independent_cycles']==0
    assert {p:hashlib.sha256(p.read_bytes()).hexdigest() for p in before}==before


def test_partial_history_is_not_relabelled_complete_by_payload_hash(tmp_path,monkeypatch):
    result=run(archive(tmp_path,monkeypatch,partial_history=True))
    rows=[b for b in result['input']['bars'] if b['session']!=str(DAY)]
    assert rows and all(b['amount_cny'] is None and b['comparable_close'] is None for b in rows)
    assert result['original_source_audit']['history_window']['field_axis_counts']['HISTORY_PARENT_UNKNOWN']>0
    assert any(r['status']=='PARTIAL' for r in result['input']['receipts'])
    assert current(result)['amount_cny']=='10300'
    assert result['native_run']['results']['SHORT_HORIZON_STRENGTH_R2']['counts']['complete_25_window']==0


def test_missing_calendar_slot_is_not_compressed_or_zero_filled(tmp_path,monkeypatch):
    result=run(archive(tmp_path,monkeypatch,missing_history_day=True))
    audit=result['original_source_audit']['history_window']
    assert len(audit['actual_last25_sessions'])==25
    assert audit['field_axis_counts']['NO_SELECTED_ROW']==2
    assert result['native_run']['results']['SHORT_HORIZON_STRENGTH_R2']['counts']['complete_25_window']==0


def test_unlinked_basis_remains_unknown_and_raw_archive_value_is_retained(tmp_path,monkeypatch):
    result=run(archive(tmp_path,monkeypatch,corrupt_basis=True))
    rows=[b for b in result['input']['bars'] if b['session']!=str(DAY)]
    assert all(b['comparable_basis'] is None and b['comparable_close'] is None for b in rows)
    assert all(Decimal(b['amount_cny'])==10300 for b in rows)


@pytest.mark.parametrize('change',['byte','missing_byte_sha','digest','cutoff','ref','hash'])
def test_whole_base_identity_is_not_substitutable(tmp_path,monkeypatch,change):
    binding,inventory,items=archive(tmp_path,monkeypatch)
    if change=='byte':binding=replace(binding,input_sha256='0'*64)
    elif change=='missing_byte_sha':binding=replace(binding,input_sha256=None)
    elif change=='digest':binding=replace(binding,input_digest='0'*64)
    elif change=='cutoff':binding=replace(binding,input_cutoff=(CUTOFF-timedelta(seconds=1)).isoformat())
    elif change=='ref':binding=replace(binding,history_ref='history_'+'0'*64)
    else:binding=replace(binding,history_hash='0'*64)
    with pytest.raises(ValueError):run((binding,inventory,items))


@pytest.mark.parametrize('case',['revision','ancestor'])
def test_factor_attempt_or_ancestor_conflict_is_hard_failure(tmp_path,monkeypatch,case):
    data=archive(tmp_path,monkeypatch,revision=case=='revision',missing_ancestor=case=='ancestor')
    with pytest.raises(ValueError,match='CONFLICT|REVISION|ANCESTOR'):run(data)


@pytest.mark.parametrize('case',['omit','inventory_hash','request'])
def test_actual_queue_inventory_cannot_omit_successful_attempt(tmp_path,monkeypatch,case):
    binding,inventory,items=archive(tmp_path,monkeypatch)
    if case=='omit':items=items[:-1]
    elif case=='inventory_hash':inventory=replace(inventory,inventory_hash='0'*64)
    else:
        con=sqlite3.connect(inventory.queue_root/'request-queue.sqlite3')
        con.execute("UPDATE requests SET payload='{}' WHERE request_id=(SELECT min(request_id) FROM requests)");con.commit();con.close()
    with pytest.raises(ValueError,match='INVENTORY|REGISTRY'):run((binding,inventory,items))


def test_payload_corruption_is_rejected_by_real_official_owner(tmp_path,monkeypatch):
    data=archive(tmp_path,monkeypatch)
    con=sqlite3.connect(data[0].database_path)
    con.execute('DROP TRIGGER history_evidence_rows_no_update')
    con.execute("UPDATE history_evidence_rows SET payload='{}' WHERE row_hash=(SELECT row_hash FROM history_evidence_rows WHERE kind='bar' ORDER BY session DESC LIMIT 1)")
    con.commit();con.close()
    with pytest.raises(IntegrityError,match='CORRUPT_HISTORY_ROW'):run(data)


def test_partition_member_corruption_is_rejected_before_normalizer(tmp_path,monkeypatch):
    data=archive(tmp_path,monkeypatch)
    con=sqlite3.connect(data[0].database_path)
    con.execute('DROP TRIGGER history_partition_members_no_delete')
    con.execute('DELETE FROM history_partition_members WHERE rowid=(SELECT rowid FROM history_partition_members LIMIT 1)')
    con.commit();con.close()
    with pytest.raises(IntegrityError,match='CORRUPT_HISTORY_PARTITION'):run(data)


def private_owner(data):
    binding,inventory,items=data;registry=_Registry()
    deadline=subject.fields.physical._Deadline(datetime.now(timezone.utc)+timedelta(seconds=90))
    job=('TEST_FIXTURE_OWNER',str(binding.input_path))
    subject._JOBS.add(job)
    with subject._inventory(inventory,registry,items,original.DerivationLimits()) as snapshot:
        return subject._factory(binding,items,DAY,CUTOFF,original.DerivationLimits(),deadline,
            registry,snapshot,job)[0]


@pytest.mark.parametrize('change',['copy','deepcopy','closed','input','reader','registry','clock',
    'identity_replace','identity_mutate'])
def test_factory_owner_and_live_source_binding_cannot_be_replaced(tmp_path,monkeypatch,change):
    owner=private_owner(archive(tmp_path,monkeypatch))
    try:
        if change=='copy':
            with pytest.raises(ValueError,match='NOT_COPYABLE'):copy(owner)
        elif change=='deepcopy':
            with pytest.raises(ValueError,match='NOT_COPYABLE'):deepcopy(owner)
        else:
            if change=='closed':owner.history.close()
            elif change=='input':owner.bundle=owner.bundle.model_copy(update={'bars':()})
            elif change=='reader':owner.reader._receipts={}
            elif change=='registry':owner.registry.files={}
            elif change=='clock':owner.cutoff=CUTOFF+timedelta(seconds=1)
            elif change=='identity_replace':owner.identity={}
            elif change=='identity_mutate':owner.identity['source_hash']='0'*64
            with pytest.raises(ValueError):owner.consume()
    finally:owner.history.close()


def test_once_complete_owner_cannot_consume_twice(tmp_path,monkeypatch):
    owner=private_owner(archive(tmp_path,monkeypatch))
    try:
        assert owner.consume()['results']['SHORT_HORIZON_STRENGTH_R2']['counts']['current_source_unknown']==0
        with pytest.raises(ValueError,match='ALREADY_CONSUMED'):owner.consume()
    finally:owner.history.close()


def test_plain_json_cannot_supply_owner_or_override_price(tmp_path,monkeypatch):
    data=archive(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match='LOCATOR'):run((json.loads(data[0].input_path.read_bytes()),data[1],data[2]))
    with pytest.raises(TypeError):run(data,bundle=json.loads(data[0].input_path.read_bytes()))
    with pytest.raises(ValueError,match='GENUINE_FACTORY_REQUIRED'):subject._HistoryFieldInput()


def test_absolute_deadline_rejects_before_any_native_consumption(tmp_path,monkeypatch):
    data=archive(tmp_path,monkeypatch)
    with pytest.raises(TimeoutError,match='DEADLINE|BUDGET'):
        subject.run_original_parent_history_field_r2(*data,DAY,CUTOFF,
            datetime.now(timezone.utc)-timedelta(seconds=1))


def test_source_mutation_and_implementation_change_remain_hard_failures(tmp_path,monkeypatch):
    owner=private_owner(archive(tmp_path,monkeypatch))
    try:
        raw=Path(next(iter(owner.reader._paths.values())))
        raw.write_bytes(raw.read_bytes()+b' ')
        with pytest.raises(ValueError,match='SAVED_R2_DELIVERY_FILE_SHA_MISMATCH'):owner.consume()
    finally:owner.history.close()


def test_consumer_function_replacement_is_rejected(tmp_path,monkeypatch):
    owner=private_owner(archive(tmp_path,monkeypatch))
    try:
        monkeypatch.setattr(subject.fields.se,'run_short_horizon_strategy',lambda *_a,**_k: {})
        with pytest.raises(ValueError,match='IMPLEMENTATION_CHANGED'):owner.consume()
    finally:owner.history.close()


def inventory_binding(binding):
    con=sqlite3.connect(binding.queue_root/'request-queue.sqlite3');con.row_factory=sqlite3.Row
    requests=[dict(r) for r in con.execute('SELECT request_id,payload,state,attempts,result_path,result_hash,failure_category,updated_at FROM requests ORDER BY request_id')]
    attempts=[dict(r) for r in con.execute('SELECT request_id,attempt,state,result_path,result_hash,recorded_at FROM attempts ORDER BY request_id,attempt')]
    con.close()
    return replace(binding,inventory_hash=digest_json_stream(dict(requests=requests,attempts=attempts)))


@pytest.mark.parametrize('field',['provider','endpoint','parameters','queue_request_id'])
def test_self_consistent_inventory_cannot_reassign_original_result_request(tmp_path,monkeypatch,field):
    binding,inventory,items=archive(tmp_path,monkeypatch)
    con=sqlite3.connect(inventory.queue_root/'request-queue.sqlite3')
    rid,payload=con.execute('SELECT request_id,payload FROM requests WHERE payload LIKE ? LIMIT 1',('%market_daily%',)).fetchone()
    if field=='queue_request_id':
        n=next(i for i,item in enumerate(items) if json.loads(item.result_path.read_bytes())['queue_request_id']==rid)
        item=items[n];body=json.loads(item.result_path.read_bytes());body['queue_request_id']='TEST_FIXTURE_OTHER_REQUEST'
        item.result_path.write_text(json.dumps(body),encoding='utf-8')
        items[n]=replace(item,result_sha256=hashlib.sha256(item.result_path.read_bytes()).hexdigest())
        changed=digest_json_stream(body)
        con.execute('UPDATE requests SET result_hash=? WHERE request_id=?',(changed,rid))
        con.execute('UPDATE attempts SET result_hash=? WHERE request_id=?',(changed,rid))
    else:
        value=json.loads(payload)
        if field=='provider':value[field]='TEST_FIXTURE_OTHER_PROVIDER'
        elif field=='endpoint':value[field]='master_all'
        else:value['parameters']['date']='2026-09-29'
        con.execute('UPDATE requests SET payload=? WHERE request_id=?',(json.dumps(value),rid))
    con.commit();con.close();inventory=inventory_binding(inventory)
    with pytest.raises(ValueError,match='RESULT_REQUEST_IDENTITY_CONFLICT'):run((binding,inventory,items))


def test_same_basis_repeated_decimal_rounding_cannot_authorize_history_price(tmp_path,monkeypatch):
    with pytest.raises(ValueError,match='HISTORY_COMPARABLE_DECIMAL_EXACT_ROUNDTRIP_UNPROVEN'):
        run(archive(tmp_path,monkeypatch,rounded_history=True))


def test_same_registered_factory_cannot_issue_two_live_owners(tmp_path,monkeypatch):
    data=archive(tmp_path,monkeypatch);owner=private_owner(data)
    try:
        binding,inventory,items=data;registry=_Registry()
        deadline=subject.fields.physical._Deadline(datetime.now(timezone.utc)+timedelta(seconds=90))
        with subject._inventory(inventory,registry,items,original.DerivationLimits()) as snapshot:
            with pytest.raises(ValueError,match='ALREADY_ISSUED'):
                subject._factory(binding,items,DAY,CUTOFF,original.DerivationLimits(),deadline,
                    registry,snapshot,owner.job)
    finally:owner.history.close()


def test_self_hashed_history_manifest_cannot_overwrite_partial_receipt_identity(tmp_path,monkeypatch):
    binding,inventory,items=archive(tmp_path,monkeypatch)
    store=SQLiteStore(binding.database_path);history=HistoryArtifactStore(store)
    manifest=deepcopy(history.manifest(binding.history_ref,binding.history_hash))
    complete=next(r for r in manifest['receipts'] if r['dataset']=='adjustment_factors')
    manifest['receipts'].insert(0,{**complete,'status':'PARTIAL','pagination_complete':False})
    new_hash=digest(manifest);new_ref='history_'+new_hash
    store.put_document('history_artifact_manifests',new_ref,manifest)
    con=sqlite3.connect(binding.database_path);con.execute('PRAGMA journal_mode=DELETE');con.close()
    body=json.loads(binding.input_path.read_bytes())
    body['input'].update(history_artifact_ref=new_ref,history_artifact_hash=new_hash)
    binding.input_path.write_text(json.dumps(body),encoding='utf-8')
    binding=replace(binding,history_ref=new_ref,history_hash=new_hash,
        input_sha256=hashlib.sha256(binding.input_path.read_bytes()).hexdigest(),
        input_digest=digest_market_input(MarketInput.model_validate(body['input'])))
    with pytest.raises(ValueError,match='HISTORY_MANIFEST_RECEIPT_IDENTITY_CONFLICT'):
        run((binding,inventory,items))
