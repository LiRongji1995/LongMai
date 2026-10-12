"""Single-use research input from real original captures and readonly history.

The archive owner remains live through consumption.  Its verified payloads do
not authorize removing a PARTIAL parent from a historical row.  Only a fresh,
authenticated RAW original can acquire the V2 field-separated authority.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from fractions import Fraction
import fcntl
import hashlib
from itertools import islice
import os
from pathlib import Path
import sqlite3
import time
import weakref

from ..storage import ReadOnlySQLiteStore
from . import original_parent_field_derivation_v2 as fields
from . import original_parent_late_derivation as original
from .contracts import DailyBar, MarketInput, SourceReceipt, digest
from .history_artifacts import (InstrumentBars, digest_json_stream, digest_market_input,
    history_receipts, history_resolver)
from .history_base_selection import validate_history_base
from .research_r1_storage import BudgetHistory
from .research_context_artifacts import SourceArtifactReader
from .provider_requests import validate_request
from .saved_r2_delivery import _Registry
from .visibility import clock_visible, stamp

VERSION = 'ORIGINAL_PARENT_HISTORY_FIELD_DERIVATION_V1'
FEATURE_VERSION = 'LIVE_HISTORY_AND_ORIGINAL_FIELD_BINDING_V1'
SCHEMA = 'ORIGINAL_PARENT_HISTORY_FIELD_R2_RESEARCH_ARCHIVE_V1'
_NORMALIZER = original.normalize_original_parent_asof
_PROJECT = fields._project
_NATIVE = fields.se.run_short_horizon_strategy
_HELD = {}
_JOBS = set()
_FACTORIES = set()
_TOKEN = object()


def _require(value, reason):
    if not value:
        raise ValueError('ORIGINAL_PARENT_HISTORY_' + reason)


@dataclass(frozen=True)
class OriginalHistoryArtifact:
    """Expected original identities are locators, never capabilities."""
    input_path: Path
    input_sha256: str
    input_digest: str
    input_cutoff: str
    database_path: Path
    history_ref: str
    history_hash: str


@dataclass(frozen=True)
class OriginalAttemptInventory:
    queue_root: Path
    request_count: int
    attempt_count: int
    inventory_hash: str


def source_identity():
    root = Path(__file__).resolve().parents[2]
    prior = fields.source_identity()
    paths = {root / name for name in prior['source_file_hashes']}
    paths.update((Path(__file__), root / 'docs' / (VERSION + '.md'),
        root / 'stockfish_core/storage.py',
        Path(__file__).with_name('research_r1_storage.py'),
        Path(__file__).with_name('history_base_selection.py')))
    hashes = {str(path.relative_to(root)):hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted(paths)}
    body = dict(source_version=VERSION, feature_version=FEATURE_VERSION,
        source_file_hashes=hashes, inline_field_producer_identity=prior,
        native_strategy_identity=prior['native_strategy_identity'],
        parameter_status='UNVALIDATED', MARKET_EDGE='NOT_ESTABLISHED', default_integration=False)
    return dict(body, source_hash=digest_json_stream(body))


def _metadata(path):
    path = Path(path)
    try:
        s = path.stat()
    except FileNotFoundError:
        return dict(path=str(path), status='ABSENT')
    return dict(path=str(path.resolve(strict=True)), status='PRESENT', size=s.st_size,
        device=s.st_dev, inode=s.st_ino, mtime_ns=s.st_mtime_ns, mode=s.st_mode)


def _database_state(path):
    path = Path(path).resolve(strict=True)
    state = {key:_metadata(str(path)+suffix) for key,suffix in
             (('main',''), ('wal','-wal'), ('journal','-journal'))}
    _require(state['main']['status']=='PRESENT', 'HISTORY_DATABASE_ABSENT')
    _require(all(row['status']=='ABSENT' or row['size']==0
                 for key,row in state.items() if key!='main'), 'HISTORY_ACTIVE_WAL_OR_JOURNAL')
    return state


@contextmanager
def _inventory(binding, registry, artifacts, limits):
    _require(type(binding) is OriginalAttemptInventory, 'EXACT_INVENTORY_LOCATOR_REQUIRED')
    _require(all(isinstance(n,int) and not isinstance(n,bool) and 0<n<=128
                 for n in (binding.request_count,binding.attempt_count)), 'INVENTORY_SCOPE_LIMIT')
    root = Path(binding.queue_root).resolve(strict=True)
    db = root/'request-queue.sqlite3'
    _require(db.is_file(), 'ORIGINAL_QUEUE_REQUIRED')
    # This is the real transport snapshot, including any committed WAL.  The
    # existing lock is opened readonly; no queue or replacement DB is created.
    with (root/'request-queue.lock').open('rb') as lock:
        fcntl.flock(lock,fcntl.LOCK_SH|fcntl.LOCK_NB)
        registry.read(db, None, limits.per_file_bytes)
        if Path(str(db)+'-wal').exists() and Path(str(db)+'-wal').stat().st_size:
            registry.read(str(db)+'-wal', None, limits.per_file_bytes)
        con = sqlite3.connect(db.as_uri()+'?mode=ro',uri=True,isolation_level=None,timeout=0)
        con.row_factory=sqlite3.Row
        con.execute('PRAGMA query_only=ON'); con.execute('BEGIN')
        started=time.monotonic()
        con.set_progress_handler(lambda:int(time.monotonic()-started>5),1000)
        try:
            requests=[dict(r) for r in con.execute('SELECT request_id,payload,state,attempts,result_path,result_hash,failure_category,updated_at FROM requests ORDER BY request_id LIMIT 129')]
            attempts=[dict(r) for r in con.execute('SELECT request_id,attempt,state,result_path,result_hash,recorded_at FROM attempts ORDER BY request_id,attempt LIMIT 129')]
            body=dict(requests=requests,attempts=attempts)
            _require(len(requests)==binding.request_count and len(attempts)==binding.attempt_count
                and digest_json_stream(body)==binding.inventory_hash, 'ACTUAL_INVENTORY_BINDING_CHANGED')
            ids={r['request_id'] for r in requests}
            _require(len(ids)==len(requests) and all(r['state']!='RUNNING' for r in requests)
                and all(a['request_id'] in ids for a in attempts), 'INVENTORY_RUNNING_OR_IDENTITY_CONFLICT')
            for request in requests:
                matching=[a for a in attempts if a['request_id']==request['request_id']]
                _require(len(matching)==request['attempts'] and
                    (not request['result_hash'] or any(a['result_hash']==request['result_hash']
                        and a['result_path']==request['result_path'] for a in matching)), 'INVENTORY_ATTEMPT_NOT_CLOSED')
            expected=Counter((a['result_path'],a['result_hash']) for a in attempts
                             if a['result_path'] and a['result_hash'])
            _require(all((a['result_path'] and a['result_hash']) or
                         a['state'] not in ('COMPLETE','PARTIAL') for a in attempts), 'SUCCESSFUL_ATTEMPT_RESULT_MISSING')
            actual=Counter()
            for item in artifacts:
                _require(Path(item.archive_root).resolve(strict=True)==root, 'ATTEMPT_ARCHIVE_ROOT_CHANGED')
                raw=registry.read(item.result_path,item.result_sha256,limits.per_file_bytes)
                result=original._json(raw)
                canonical=digest_json_stream(result)
                # An explicit locator outside root must have its own expected
                # byte SHA and the original SQL canonical result identity.
                # This does not prove byte identity with an unavailable old
                # wrapper. RAW/receipt auth still uses the original root.
                matches=[pair for pair in expected if pair[1]==canonical]
                _require(len(matches)==1, 'RESULT_NOT_IN_ACTUAL_ATTEMPT_INVENTORY')
                parents=[a for a in attempts if (a['result_path'],a['result_hash'])==matches[0]]
                for parent in parents:
                    request=next(r for r in requests if r['request_id']==parent['request_id'])
                    parameters=original._json(request['payload'])
                    _require(result.get('queue_request_id')==parent['request_id']
                        and parameters['provider']==result['provider']
                        and parameters['endpoint']==result['endpoint']
                        and result['parameters']==validate_request(parameters['endpoint'],
                            parameters['parameters']), 'RESULT_REQUEST_IDENTITY_CONFLICT')
                actual[matches[0]]+=1
            _require(actual==expected, 'COMPLETE_ATTEMPT_RESULT_REGISTRY_REQUIRED')
            yield body
            registry.terminal()
        finally:
            con.close()
            fcntl.flock(lock,fcntl.LOCK_UN)


def _base(binding, registry, cutoff):
    _require(type(binding) is OriginalHistoryArtifact, 'EXACT_HISTORY_LOCATOR_REQUIRED')
    _require(all(isinstance(value,str) and len(value)==64 and
        all(char in '0123456789abcdef' for char in value) for value in
        (binding.input_sha256,binding.input_digest,binding.history_hash)),
        'ORIGINAL_HISTORY_SHA_BINDING_REQUIRED')
    raw=registry.read(binding.input_path,binding.input_sha256,64*1024**2)
    body=original._json(raw)
    _require(set(body)=={'input','cutoff','parent_input','purpose','supplement','target_session'}, 'ORIGINAL_FROZEN_WRAPPER_SCHEMA')
    _require(stamp(body['cutoff'])==stamp(binding.input_cutoff)
        and stamp(body['cutoff'])<=cutoff, 'ORIGINAL_INPUT_CUTOFF_CHANGED')
    base=MarketInput.model_validate(body['input'])
    _require(digest_market_input(base)==binding.input_digest
        and base.history_artifact_ref==binding.history_ref
        and base.history_artifact_hash==binding.history_hash
        and binding.history_ref=='history_'+binding.history_hash, 'WHOLE_ORIGINAL_TYPED_IDENTITY_CHANGED')
    _require(all(clock_visible(r,cutoff) for r in base.receipts), 'ORIGINAL_PARENT_AFTER_CUTOFF')
    return base


def _union(left, right):
    result={r.receipt_id:r for r in left}
    for row in right:
        _require(row.receipt_id not in result or result[row.receipt_id]==row, 'ORIGINAL_RECEIPT_IDENTITY_CONFLICT')
        result[row.receipt_id]=row
    return tuple(result[key] for key in sorted(result))


def _fragments(reader, fresh, audit, deadline):
    checks={c['receipt_id']:c for c in audit['original_captures']}
    values=tuple(r for r in fresh.receipts if r.status!='FETCH_FAILED')
    clocks=[dict(checks[r.receipt_id]['capture_clock_audit'],receipt_id=r.receipt_id) for r in values]
    return fields.physical._factor_sources(values,reader,clocks,deadline)[0]


def _history_window(base, fresh, projected, field_records, history, cutoff, reader, audit, deadline):
    receipts=_union(history_receipts(base),fresh.receipts)
    _require(all(clock_visible(r,cutoff) for r in receipts), 'HISTORY_PARENT_AFTER_CUTOFF')
    _require(base.observation_date<fresh.observation_date
        and set(base.sessions)<=set(fresh.sessions), 'ORIGINAL_CALENDAR_OR_OBSERVATION_CONFLICT')
    bars=tuple({digest(b):b for b in (*base.bars,*fresh.bars)}.values())
    original_bundle=MarketInput.model_validate({**fresh.model_dump(exclude={'bars','receipts'}),
        'bars':bars,'receipts':receipts,'history_artifact_ref':base.history_artifact_ref,
        'history_artifact_hash':base.history_artifact_hash})
    original_bundle._history_store=history
    resolver=history_resolver(original_bundle,cutoff=cutoff)
    sessions=tuple(s for s in fresh.sessions if s<=fresh.observation_date)[-25:]
    _require(sessions and sessions[-1]==fresh.observation_date, 'OBSERVATION_CALENDAR_MISSING')
    typed_sequence=hashlib.sha256();statuses=Counter();payload_count=0
    # This calls the official payload SHA/member/typed checks on ALL referenced
    # rows of the requested window, even if a row is later unselected.
    for row in history.iter_rows(base.history_artifact_ref,'bar',start=sessions[0],
            end=sessions[-1],expected_hash=base.history_artifact_hash):
        deadline.check();payload_count+=1
        typed_sequence.update((digest(row)+'\n').encode())
        proof=resolver.resolve(row,instrument_id=row.instrument_id,session=row.session,
                               require_session=True,require_complete=True)
        statuses['AVAILABLE' if proof.allowed else 'UNKNOWN:'+','.join(proof.reason_codes)]+=1
    by_iid=defaultdict(list)
    for bar in bars:by_iid[bar.instrument_id].append(bar)
    class LocalView:
        def __init__(self,iid):self.bars=tuple(by_iid.get(iid,()))
        def __getattr__(self,key):return getattr(original_bundle,key)
    fresh_fields={f['original_normalized_row_hash']:f for f in field_records}
    projected_by_hash={f['original_normalized_row_hash']:bar
                       for f,bar in zip(field_records,projected.bars)}
    captures=_fragments(reader,fresh,audit,deadline)
    output=[];bindings=[];counts=Counter();selected_hash=hashlib.sha256()
    for iid in sorted({s.instrument_id for s in fresh.universe.securities}):
        deadline.check()
        originals=InstrumentBars(LocalView(iid),resolver,[],start=sessions[0])
        code=iid[-2:].lower()+'.'+iid[:6]
        pieces=[];authorities=[]
        for capture in captures:
            if code not in capture['fragments']:continue
            parent=capture['parent']
            rows=[r for r in capture['fragments'][code][0]
                  if date.fromisoformat(r['dividOperateDate'])<=fresh.observation_date]
            piece=(rows,parent.receipt_id,str(min(capture['end'],fresh.observation_date)),str(capture['start']))
            pieces.append(piece)
            if parent.status=='COMPLETE' and parent.pagination_complete and capture['raw_first_seen_present']:
                authorities.append(piece)
        order={r.receipt_id:(r.first_seen_at,r.ingested_at,r.receipt_id) for r in fresh.receipts}
        pieces.sort(key=lambda p:order[p[1]]);authorities.sort(key=lambda p:order[p[1]])
        _,_,conflict=fields.factor_series(pieces)
        _require(not conflict,'ALL_ATTEMPT_FACTOR_REVISION_CONFLICT')
        authority_series,_,_=fields.factor_series(authorities[-1:])
        for session in sessions:
            deadline.check();value=originals.bar(session,iid)
            if value is None:
                bindings.append(dict(instrument_id=iid,session=str(session),status='UNKNOWN',
                    reason_codes=['NO_OFFICIAL_SELECTED_ROW']));counts['NO_SELECTED_ROW']+=1;continue
            row=DailyBar.model_validate(value);key=digest(row);selected_hash.update((key+'\n').encode())
            # Fresh originals have a genuine RAW exact-link proof, so only
            # this branch may use the frozen V2 field-separated projection.
            fresh_key=digest_json_stream(row)
            if fresh_key in fresh_fields:
                chosen=projected_by_hash[fresh_key];record=dict(fresh_fields[fresh_key],history_role='FRESH_AUTHENTICATED_RAW_FIELD_PROJECTION')
                counts['FRESH_RAW_PROJECTED']+=1
            else:
                full=resolver.resolve(row,instrument_id=iid,session=session,require_session=True,require_complete=True)
                original_refs=tuple(row.source_refs)
                update={}
                reason=None
                if not full.allowed:
                    # A stored hash is not a new field authority.  Do not drop
                    # mixed PARTIAL/failed/late refs and relabel RAW as known.
                    update.update({name:None for name in fields.RAW_FIELDS})
                    update.update(turnover_fraction=None,turnover_basis=None,quality='PARTIAL')
                    reason='HISTORY_FULL_PARENT_VISIBILITY_UNKNOWN'
                    counts['HISTORY_PARENT_UNKNOWN']+=1
                converted,status=fields.comparable_rows(iid,[(session,(row.model_dump(),'STORED_PAYLOAD_NOT_NEW_RAW_AUTHORITY'))],{},authorities[-1:])
                oracle,oracle_status=fields.comparable_rows(iid,[(session,(row.model_dump(),'STORED_PAYLOAD_NOT_NEW_RAW_AUTHORITY'))],{},pieces)
                _require(oracle_status!='FACTOR_REVISION_CONFLICT_REQUIRES_REVIEW','ALL_ATTEMPT_FACTOR_REVISION_CONFLICT')
                comparable=converted.get(session)
                valid=full.allowed and comparable is not None and row.comparable_basis is not None
                valid=valid and comparable[0]['comparable_basis']==row.comparable_basis
                if valid:
                    _require(session in oracle and oracle[session][0]==comparable[0], 'HISTORY_COMPLETE_ORACLE_CONFLICT')
                    for name in ('open','high','low','close'):
                        _require(fields._exact(getattr(row,'comparable_'+name))==
                            Fraction(comparable[0]['comparable_'+name]),'HISTORY_SAME_BASIS_EXACT_PRICE_CONFLICT')
                    factor=next((value for effective,value in reversed(authority_series)
                        if effective<=str(session)),None)
                    _require(factor is not None and all(
                        fields._exact(getattr(row,'comparable_'+name))==
                        fields._exact(getattr(row,name))*Fraction(factor)
                        for name in ('open','high','low','close')),
                        'HISTORY_COMPARABLE_DECIMAL_EXACT_ROUNDTRIP_UNPROVEN')
                    counts['HISTORY_COMPLETE_SAME_BASIS']+=1
                else:
                    update.update({name:None for name in fields.COMPARABLE_FIELDS})
                    counts['HISTORY_COMPARABLE_UNKNOWN']+=1
                update.update(limit_up=None,limit_down=None,limit_applicable=None,limit_verified=False,rule_id=None)
                chosen=DailyBar.model_validate({**row.model_dump(mode='python'),**update})
                record=dict(instrument_id=iid,session=str(session),original_selected_row_hash=key,
                    history_role='UNCHANGED_FULL_PARENT_AUTHORITY_OR_UNKNOWN_NOT_NARROWED',
                    source_refs=list(original_refs),strict_parent_reason_codes=list(full.reason_codes),
                    raw_status='AVAILABLE' if full.allowed else 'UNKNOWN',
                    comparable_status='AVAILABLE' if valid else 'UNKNOWN',comparator_status=status,
                    basis_rewritten=False,stored_turn_role='ORIGINAL_TYPED_FLOAT_NOT_RECONSTRUCTED_RAW_PERCENT',
                    reason_codes=[reason] if reason else [],audit_dependency_refs=list(original_refs))
            output.append(chosen);bindings.append(record)
    completed=datetime.now(timezone.utc)
    # This is a new inline research projection, not a new version of the old
    # content-addressed history manifest.  Keep original receipts unchanged.
    actual_rows=tuple(row.model_copy(update={'first_seen_at':completed,'ingested_at':completed}) for row in output)
    bundle=MarketInput.model_validate({**projected.model_dump(exclude={'bars','receipts'}),
        'bars':actual_rows,'receipts':receipts,'history_artifact_ref':None,'history_artifact_hash':None})
    for record,row in zip((b for b in bindings if b.get('status')!='UNKNOWN'),actual_rows):
        record['projected_row_hash']=digest(row)
    proof=dict(history_ref=base.history_artifact_ref,history_hash=base.history_artifact_hash,
        actual_last25_sessions=list(map(str,sessions)),actual_payload_SHA_and_typed_checked_rows=payload_count,
        typed_payload_digest_sequence=typed_sequence.hexdigest(),strict_full_parent_status_counts=dict(statuses),
        original_selected_digest_sequence=selected_hash.hexdigest(),field_axis_counts=dict(counts),
        old_history_identity_role='SOURCE_ARCHIVE_ONLY_NOT_REBOUND_TO_NEW_INLINE_INPUT',
        complete_inventory_coverage='ORIGINAL_QUEUE_ATTEMPTS_NOT_ALL_OLD_HISTORY_PROVIDER_CAPTURES',
        historical_RAW_reauthentication='ONLY_WHERE_GENUINE_ORIGINAL_TRIO_IS_PRESENT',
        original_receipts_modified=False,history_store_written=False)
    return bundle,bindings,proof,completed


class _HistoryFieldInput:
    def __new__(cls,*_args,**_kwargs):raise ValueError('ORIGINAL_PARENT_HISTORY_GENUINE_FACTORY_REQUIRED')
    def __copy__(self):raise ValueError('ORIGINAL_PARENT_HISTORY_CAPABILITY_NOT_COPYABLE')
    def __deepcopy__(self,memo):raise ValueError('ORIGINAL_PARENT_HISTORY_CAPABILITY_NOT_COPYABLE')

    def validate(self):
        held=_HELD.get(id(self))
        _require(type(self) is _HistoryFieldInput and held is not None and held['owner']() is self
            and self.token is _TOKEN and held['pid']==os.getpid() and held['job']==self.job
            and held['state']==self.state and held['cutoff']==self.cutoff
            and held['bundle'] is self.bundle and held['history'] is self.history
            and held['reader'] is self.reader and held['registry'] is self.registry
            and held['identity'] is self.identity
            and digest_json_stream(self.identity)==held['identity_hash']
            and digest_market_input(self.bundle)==held['input_hash']
            and digest_json_stream(self.fields)==held['fields_hash']
            and digest_json_stream(self.audit)==held['audit_hash']
            and digest_json_stream(self.registry.files)==held['registry_hash'], 'FACTORY_SEAL_CHANGED')
        _require(type(self.history) is BudgetHistory and not self.history._closed
            and self.history.store is held['history_store']
            and self.history._read_connection is held['history_connection']
            and self.history.manifest(held['history_ref'],held['history_hash'])==held['manifest'], 'LIVE_HISTORY_OWNER_CHANGED')
        _require(_database_state(held['database_path'])==held['database_state'], 'HISTORY_DATABASE_TERMINAL_CHANGED')
        _require(source_identity()==held['identity'] and original.normalize_original_parent_asof is _NORMALIZER
            and fields._project is _PROJECT and fields.se.run_short_horizon_strategy is _NATIVE, 'IMPLEMENTATION_CHANGED')
        _require(type(self.reader) is SourceArtifactReader
            and self.reader.receipts==held['reader_receipts']
            and self.reader._receipts=={r.receipt_id:r for r in held['reader_receipts']}
            and {k:str(v.resolve(strict=True)) for k,v in self.reader._paths.items()}==held['paths'], 'READER_BINDING_CHANGED')
        self.registry.terminal()

    def consume(self):
        self.validate();_require(self.state=='READY','FACTORY_ALREADY_CONSUMED')
        self.state=_HELD[id(self)]['state']='CONSUMING'
        try:
            result=_NATIVE(self.bundle,cutoff=self.cutoff,
                input_id='original_parent_history_field_'+digest_market_input(self.bundle),
                strategy_id='SHORT_HORIZON_STRENGTH_R2',
                _source_payload_reader=self.reader,_source_receipts=self.reader.receipts)
            self.validate()
        except BaseException:
            self.state=_HELD[id(self)]['state']='FAILED';raise
        self.state=_HELD[id(self)]['state']='COMPLETE'
        return result


def _factory(binding, artifacts, day, cutoff, limits, deadline, registry, inventory, job):
    _require(job in _JOBS and job not in _FACTORIES,'FACTORY_NOT_REGISTERED_OR_ALREADY_ISSUED')
    _FACTORIES.add(job)
    identity=source_identity();base=_base(binding,registry,cutoff)
    database_state=_database_state(binding.database_path)
    remaining=(deadline.end-datetime.now(timezone.utc)).total_seconds()
    _require(0<remaining<=7200,'LIVE_HISTORY_DEADLINE_LIMIT')
    history=BudgetHistory(ReadOnlySQLiteStore(binding.database_path,timeout=1),
        seconds=remaining,byte_budget=2*1024**3)
    base._history_store=history
    try:
        manifest=history.manifest(binding.history_ref,binding.history_hash)
        manifest_receipts={}
        _require(isinstance(manifest.get('receipts'),list),'HISTORY_MANIFEST_RECEIPT_SCHEMA')
        for value in manifest['receipts']:
            _require(isinstance(value,dict),'HISTORY_MANIFEST_RECEIPT_SCHEMA')
            receipt=SourceReceipt.model_validate(value)
            _require(receipt.receipt_id not in manifest_receipts or
                manifest_receipts[receipt.receipt_id]==receipt,'HISTORY_MANIFEST_RECEIPT_IDENTITY_CONFLICT')
            manifest_receipts[receipt.receipt_id]=receipt
        schema=validate_history_base(base,history,observation_date=day,cutoff=cutoff,
            verify_partitions=True,allowed_price_providers={'baostock','tushare'})
        fresh,audit=_NORMALIZER(artifacts,day,cutoff,limits=limits)
        schema=validate_history_base(base,history,observation_date=day,cutoff=cutoff,
            verify_partitions=False,incoming_price_receipts=fresh.receipts,
            allowed_price_providers={'baostock','tushare'})
        for record in audit['source_file_bindings']:
            registry.read(record['path'],record['sha256'],limits.per_file_bytes)
        checks=audit['original_captures']
        paths={c['receipt_id']:Path(c['archive_integrity']['raw_path']) for c in checks}
        _require(set(paths)=={r.receipt_id for r in fresh.receipts}, 'COMPLETE_FRESH_SOURCE_REGISTRY_REQUIRED')
        reader=SourceArtifactReader(fresh.receipts,paths)
        clocks=[dict(c['capture_clock_audit'],receipt_id=c['receipt_id']) for c in checks]
        projected,records,inheritance,_=_PROJECT(fresh,reader,clocks,checks,deadline)
        bundle,records,window,derived_at=_history_window(base,fresh,projected,records,history,cutoff,reader,audit,deadline)
        audit=dict(audit,history_schema=schema,history_window=window,
            immutable_history_original_receipts=[r.model_dump(mode='json') for r in history_receipts(base)],
            original_history_manifest_receipt_records=manifest['receipts'],
            actual_attempt_inventory=inventory,field_factor_inheritance=inheritance,
            original_base_input_hash=digest_market_input(base),new_input_history_identity=None)
        owner=object.__new__(_HistoryFieldInput)
        owner.token,owner.bundle,owner.reader,owner.history,owner.registry=_TOKEN,bundle,reader,history,registry
        owner.fields,owner.audit,owner.identity,owner.job,owner.cutoff,owner.state=records,audit,identity,job,cutoff,'READY'
        key=id(owner)
        _HELD[key]=dict(owner=weakref.ref(owner,lambda _: _HELD.pop(key,None)),pid=os.getpid(),job=job,
            cutoff=cutoff,state='READY',bundle=bundle,reader=reader,history=history,registry=registry,
            input_hash=digest_market_input(bundle),fields_hash=digest_json_stream(records),audit_hash=digest_json_stream(audit),
            registry_hash=digest_json_stream(registry.files),identity=identity,
            identity_hash=digest_json_stream(identity),
            history_store=history.store,history_connection=history._read_connection,
            history_ref=binding.history_ref,history_hash=binding.history_hash,
            manifest=history.manifest(binding.history_ref,binding.history_hash),
            database_path=binding.database_path,database_state=database_state,
            reader_receipts=reader.receipts,paths={k:str(v.resolve(strict=True)) for k,v in paths.items()})
        owner.validate()
        return owner,derived_at
    except BaseException:
        history.close();raise


def run_original_parent_history_field_r2(history_artifact, attempt_inventory, artifacts,
        observation_date, original_parent_as_of, registered_worker_deadline, *,
        requested_target_date=None, limits=None):
    """Whole original input + real readonly owner -> once-only inline R2.

    No supplied bundle/reader/owner/proof/callback is accepted.  Result JSON is
    an archive and cannot resume this invocation or restore its authority.
    """
    limits=limits or original.DerivationLimits(budget_seconds=1200)
    _require(type(limits) is original.DerivationLimits,'EXACT_LIMITS_REQUIRED');limits.validate()
    artifacts=tuple(islice(artifacts,limits.max_captures+1))
    _require(artifacts and len(artifacts)<=limits.max_captures
        and all(type(a) is original.OriginalCaptureArtifact for a in artifacts),'ORIGINAL_LOCATORS_REQUIRED')
    day,cutoff=date.fromisoformat(str(observation_date)),stamp(original_parent_as_of)
    target=date.fromisoformat(str(requested_target_date)) if requested_target_date is not None else None
    _require(target is None or target>=day,'TARGET_PRECEDES_OBSERVATION')
    deadline=fields.physical._Deadline(registered_worker_deadline)
    _require(type(history_artifact) is OriginalHistoryArtifact,'EXACT_HISTORY_LOCATOR_REQUIRED')
    job=(str(Path(history_artifact.input_path).resolve(strict=True)),history_artifact.input_sha256,
        str(day),cutoff.isoformat(),deadline.end.isoformat())
    _require(job not in _JOBS,'REGISTERED_JOB_ALREADY_ATTEMPTED');_JOBS.add(job)
    registry=_Registry();issued_at=datetime.now(timezone.utc);owner=None
    with deadline:
        with _inventory(attempt_inventory,registry,artifacts,limits) as inventory:
            try:
                owner,derived_at=_factory(history_artifact,artifacts,day,cutoff,limits,deadline,registry,inventory,job)
                native=owner.consume();deadline.check()
                _require(native['input_hash']==digest_market_input(owner.bundle)
                    and native['observation_date']==str(day)
                    and set(native['results'])=={'SHORT_HORIZON_STRENGTH_R2'},'NATIVE_RESULT_BINDING_CHANGED')
                owner.validate();terminal=datetime.now(timezone.utc)
                result=dict(schema_version=SCHEMA,source_identity=owner.identity,
                    native_strategy_identity={k:native[k] for k in ('implementation_version','feature_version',
                        'source_hash','config_hash','spec_hash','spec_revision')},
                    source_asof_cutoff=cutoff.isoformat(),actual_issued_at=issued_at.isoformat(),
                    actual_derived_at=derived_at.isoformat(),terminal_verified_at=terminal.isoformat(),
                    requested_target_date=str(target) if target else None,
                    target_calendar_status='AVAILABLE' if target in owner.bundle.sessions else 'UNKNOWN',
                    target_calendar_role='ORIGINAL_PARENT_CALENDAR_ONLY_NOT_EXTENDED',
                    input_hash=digest_market_input(owner.bundle),input=owner.bundle.model_dump(mode='json'),
                    native_run=native,field_bindings=owner.fields,original_source_audit=owner.audit,
                    source_registry=registry.files,history_read_metrics=dict(owner.history.metrics),
                    normalizer_calls=1,native_R2_attempts=1,selector_rule_changed=False,
                    historical_PIT_count=0,complete_independent_cycles=0,MARKET_EDGE='NOT_ESTABLISHED',
                    archive_role='ARCHIVE_ONLY_NOT_RESTORABLE_CAPABILITY',
                    resource_semantics=dict(unique_source_size_limit_bytes=limits.total_source_bytes,
                        source_size_limit_role='UNIQUE_CAPTURE_SIZE_NOT_CUMULATIVE_IO_OR_DATABASE_SIZE',
                        history_payload_read_budget_bytes=owner.history.byte_budget,cumulative_physical_read_bytes=None,
                        history_internal_memory_budget_bytes=owner.history.metrics['memory_budget_bytes'],
                        history_internal_memory_policy=owner.history.metrics['memory_policy'],
                        cumulative_physical_read_guard=False,OS_RSS_guard=False,
                        registered_absolute_deadline=deadline.end.isoformat(),real_supervisor_required=True))
            finally:
                if owner is not None:owner.history.close()
    return result
