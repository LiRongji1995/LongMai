"""Bounded R16-R1 evidence helpers. Production sources are always URI read-only."""
from __future__ import annotations
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from stockfish_core.storage import ReadOnlySQLiteStore, SQLiteStore, IntegrityError
from stockfish_core.yaogu.contracts import MarketInput, digest
from stockfish_core.yaogu.config import StrategyConfig
from stockfish_core.yaogu.history_artifacts import HistoryArtifactStore, bind_history, digest_market_input
from stockfish_core.yaogu.e2e_tracing import traced_stage


def load_bundle(store_or_path, input_id):
    store = store_or_path if isinstance(store_or_path,SQLiteStore) else ReadOnlySQLiteStore(store_or_path)
    raw = store.get_document_bytes('candidate_inputs',input_id,expected_hash=input_id.removeprefix('market_'))
    if raw is None:
        raise ValueError('SOURCE_INPUT_NOT_FOUND')
    return bind_history(MarketInput.model_validate_json(raw),store)


def active_config_metadata(store_or_path):
    store = store_or_path if isinstance(store_or_path,SQLiteStore) else ReadOnlySQLiteStore(store_or_path)
    configs = store.list_documents('strategy_configs',limit=1000)
    activations = store.list_documents('strategy_config_activations',limit=1000)
    if not configs:
        raise ValueError('REGISTERED_STRATEGY_CONFIG_REQUIRED')
    activation = activations[-1] if activations else None
    wanted = activation['payload']['config_hash'] if activation else configs[-1]['payload']['config_hash']
    selected = next(row for row in configs if row['payload']['config_hash'] == wanted)
    config = StrategyConfig.model_validate(selected['payload']['config'])
    if config.config_hash != wanted:
        raise ValueError('CONFIG_HASH_MISMATCH')
    return config,{'config_hash':wanted,'config_document_id':selected['document_id'],
                   'config_content_hash':selected['content_hash'],
                   'activation_document_id':activation['document_id'] if activation else None,
                   'activation_content_hash':activation['content_hash'] if activation else None}


def _copy_document(source, target, namespace, document_id):
    with source.transaction(write=False) as con:
        row = con.execute('SELECT * FROM documents WHERE namespace=? AND document_id=?',(namespace,document_id)).fetchone()
    if row is None:
        return False
    if hashlib.sha256(row['payload'].encode()).hexdigest() != row['content_hash']:
        raise IntegrityError('SOURCE_DOCUMENT_HASH_MISMATCH')
    with target.transaction() as con:
        existing = con.execute('SELECT * FROM documents WHERE namespace=? AND document_id=?',(namespace,document_id)).fetchone()
        if existing and tuple(existing) != tuple(row):
            raise IntegrityError('TARGET_DOCUMENT_CONFLICT')
        con.execute('INSERT OR IGNORE INTO documents VALUES(?,?,?,?,?)',tuple(row))
    return True


def copy_input_closure(source_db, target_db, input_ids, *, progress=lambda event:None, include_config=True):
    source = ReadOnlySQLiteStore(source_db)
    if source.path == Path(target_db).resolve():
        raise ValueError('ISOLATED_TARGET_MUST_DIFFER_FROM_SOURCE_DB')
    target = SQLiteStore(target_db)
    origin = HistoryArtifactStore(source)
    owner = HistoryArtifactStore(target)
    manifests = {}
    for iid in input_ids:
        raw = source.get_document_bytes('candidate_inputs',iid,expected_hash=iid.removeprefix('market_'))
        if raw is None:
            raise ValueError('SOURCE_INPUT_MISSING')
        bundle = MarketInput.model_validate_json(raw)
        if bundle.history_artifact_ref:
            manifests[bundle.history_artifact_ref] = origin.manifest(bundle.history_artifact_ref,bundle.history_artifact_hash)
    parts = {p['partition_hash']:p for manifest in manifests.values() for p in manifest['partitions']}
    row_count = 0
    for index,(part_hash,part) in enumerate(sorted(parts.items(),key=lambda item:(item[1]['session'],item[0])),1):
        progress({'stage':'COPY_HISTORY_PARTITION_START','processed':index-1,'total':len(parts),'session':part['session'],'copied_rows':row_count})
        with source.transaction(write=False) as read_con,target.transaction() as write_con:
            count = 0
            partition_hash = hashlib.sha256()
            for row in read_con.execute('SELECT m.row_hash,m.kind,m.instrument_id,m.session,r.payload,r.kind AS row_kind,r.instrument_id AS row_iid,r.session AS row_session FROM history_partition_members m '
                    'JOIN history_evidence_rows r ON r.row_hash=m.row_hash WHERE m.partition_hash=? ORDER BY m.kind,m.instrument_id,m.row_hash',(part_hash,)):
                if hashlib.sha256(row['payload'].encode()).hexdigest() != row['row_hash']:
                    raise IntegrityError('CORRUPT_SOURCE_HISTORY_ROW')
                value = json.loads(row['payload'])
                if (row['kind'],row['instrument_id'],row['session']) != (row['row_kind'],row['row_iid'],row['row_session']) or (
                        value.get('instrument_id') != row['instrument_id'] or
                        value.get('session',value.get('effective_from')) != row['session']):
                    raise IntegrityError('SOURCE_HISTORY_MEMBERSHIP_METADATA_CONFLICT')
                descriptor = [row['row_hash'],row['kind'],row['instrument_id'],row['session']]
                partition_hash.update(json.dumps(descriptor,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()+b'\n')
                stored_row = (*descriptor,row['payload'])
                inserted = write_con.execute('INSERT OR IGNORE INTO history_evidence_rows VALUES(?,?,?,?,?)',stored_row).rowcount
                if not inserted and tuple(write_con.execute('SELECT * FROM history_evidence_rows WHERE row_hash=?',(row['row_hash'],)).fetchone()) != stored_row:
                    raise IntegrityError('TARGET_HISTORY_ROW_CONFLICT')
                member = (part_hash,*descriptor)
                inserted = write_con.execute('INSERT OR IGNORE INTO history_partition_members VALUES(?,?,?,?,?)',member).rowcount
                if not inserted and tuple(write_con.execute('SELECT * FROM history_partition_members WHERE partition_hash=? AND row_hash=?',(part_hash,row['row_hash'])).fetchone()) != member:
                    raise IntegrityError('TARGET_HISTORY_MEMBERSHIP_CONFLICT')
                count += 1
            if count != part['row_count'] or partition_hash.hexdigest() != part_hash:
                raise IntegrityError('SOURCE_PARTITION_INTEGRITY_MISMATCH')
            if write_con.execute('SELECT count(*) FROM history_partition_members WHERE partition_hash=?',
                                 (part_hash,)).fetchone()[0] != count:
                raise IntegrityError('TARGET_PARTITION_EXTRA_MEMBERS')
        row_count += count
        progress({'stage':'COPY_HISTORY_PARTITION_DONE','processed':index,'total':len(parts),'session':part['session'],'copied_rows':row_count})
    for ref in manifests:
        _copy_document(source,target,'history_artifact_manifests',ref)
    for iid in input_ids:
        _copy_document(source,target,'candidate_inputs',iid)
        _copy_document(source,target,'candidate_input_indexes',iid)
    config_meta = None
    if include_config:
        _,config_meta = active_config_metadata(source)
        _copy_document(source,target,'strategy_configs',config_meta['config_document_id'])
        if config_meta['activation_document_id']:
            _copy_document(source,target,'strategy_config_activations',config_meta['activation_document_id'])
    return {'target_db':str(target.path),'input_ids':list(input_ids),'history_refs':list(manifests),
            'partition_count':len(parts),'row_members_copied':row_count,'config':config_meta,
            'source_access':'SQLITE_URI_MODE_RO_QUERY_ONLY','official_records_copied':False,
            'resume_policy':'CONTENT_ADDRESSED_INSERT_OR_IGNORE_REVERIFIES_SOURCE_PARTITIONS'}


@traced_stage('RECORDED_SOURCE_VALIDATION', actual_network_requests=0)
def load_inventory_results(inventory, receipt_ids, *, progress=lambda event:None):
    values, assets = [], []
    for index,rid in enumerate(dict.fromkeys(receipt_ids),1):
        choices = inventory['asset_bindings'].get(rid) or []
        if not choices:
            raise ValueError('REQUIRED_RAW_ASSET_MISSING:'+rid)
        asset = choices[0]
        path = Path(asset['path'])
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != asset['file_sha256']:
            raise ValueError('RAW_ASSET_BYTES_CHANGED:'+rid)
        value = json.loads(raw)
        if digest(value) != asset['result_canonical_sha256'] or value.get('request_receipt_id') != rid:
            raise ValueError('RAW_ASSET_CANONICAL_IDENTITY_MISMATCH:'+rid)
        authority = [r for r in value.get('receipts',()) if r.get('receipt_id') == rid]
        if len(authority) != 1 or authority[0]['content_hash'] != asset['source_content_hash']:
            raise ValueError('RAW_ASSET_RECEIPT_MISMATCH:'+rid)
        values.append(value)
        assets.append(asset)
        progress({'stage':'RAW_ASSET_VERIFIED','processed':index,'total':len(set(receipt_ids)),
                  'receipt_id':rid,'status':value['status'],'rows':len(value['rows'])})
    return values, assets


@traced_stage('MARKET_INPUT_CREATION_OR_LOAD', phase='IMMUTABLE_INPUT_REGISTRATION')
def register_isolated_input(store, bundle, *, metadata=None):
    input_hash = digest_market_input(bundle)
    input_id = 'market_'+input_hash
    store.put_document('candidate_inputs',input_id,bundle.model_dump(mode='json'))
    store.put_document('candidate_input_indexes',input_id,{
        'input_id':input_id,'observation_date':str(bundle.observation_date),'max_session':str(max(bundle.sessions)),
        'coverage_type':bundle.coverage_type,**(metadata or {})})
    return input_id
