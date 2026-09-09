"""Immutable session partitions in the existing authoritative SQLite store.

Daily documents contain only a manifest identity and recent PIT rows. Historical
rows are queried by instrument/window, never deserialized as a market-wide JSON.
The private store binding is a reader capability, never serialized as a path.
"""
from __future__ import annotations

from collections import OrderedDict, defaultdict
from collections.abc import Mapping
from datetime import date, timedelta
import codecs
import hashlib
import json
import pickle
import zlib
from uuid import uuid4

from ..storage import IntegrityError
from .contracts import DailyBar, MarketInput, SecurityRecord, SourceReceipt, digest

VERSION = 'CONTENT_ADDRESSED_HISTORY_ARTIFACT_V1'


def _json(value):
    if hasattr(value, 'model_dump'):
        value = value.model_dump(mode='json')
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str, allow_nan=False)


def digest_json_stream(value):
    """The existing canonical digest without constructing the whole JSON text.

    Dependency records are already ordinary JSON mappings. Their universe
    envelope can contain every recent security row, so a second complete JSON
    string and UTF-8 copy needlessly overlap the live strategy result.
    """
    hasher = hashlib.sha256()
    encoder = json.JSONEncoder(sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str, allow_nan=False)
    for chunk in encoder.iterencode(value):
        hasher.update(chunk.encode())
    return hasher.hexdigest()


def digest_market_input(bundle):
    """Preserve MarketInput's canonical identity using at most one row dump.

    The daily artifact remains compact relative to history, but a full-market
    six-session input still contains tens of thousands of rows. Do not expand
    all Pydantic rows and serialize a second full copy just to calculate its ID.
    """
    hasher = hashlib.sha256()

    def write(value):
        hasher.update(value.encode())

    def rows(values):
        write('[')
        for index, value in enumerate(values):
            if index:
                write(',')
            write(_json(value))
        write(']')

    envelope = bundle.model_dump(mode='json', exclude={'bars', 'universe'})
    universe = bundle.universe.model_dump(mode='json', exclude={'securities'})
    write('{')
    for index, key in enumerate(sorted((*envelope, 'bars', 'universe'))):
        if index:
            write(',')
        write(_json(key) + ':')
        if key == 'bars':
            rows(bundle.bars)
        elif key == 'universe':
            write('{')
            for position, field in enumerate(sorted((*universe, 'securities'))):
                if position:
                    write(',')
                write(_json(field) + ':')
                if field == 'securities':
                    rows(bundle.universe.securities)
                else:
                    write(_json(universe[field]))
            write('}')
        else:
            write(_json(envelope[key]))
    write('}')
    return hasher.hexdigest()


class HistoryArtifactStore:
    def __init__(self, store):
        self.store = store
        self._manifests = {}
        with store.transaction() as con:
            con.execute('CREATE TABLE IF NOT EXISTS history_evidence_rows (row_hash TEXT PRIMARY KEY, kind TEXT NOT NULL, instrument_id TEXT NOT NULL, session TEXT NOT NULL, payload TEXT NOT NULL)')
            con.execute('CREATE TABLE IF NOT EXISTS history_partition_members (partition_hash TEXT NOT NULL, row_hash TEXT NOT NULL, kind TEXT NOT NULL, instrument_id TEXT NOT NULL, session TEXT NOT NULL, PRIMARY KEY(partition_hash,row_hash))')
            con.execute('CREATE INDEX IF NOT EXISTS history_instrument_window ON history_partition_members(partition_hash,kind,instrument_id,session)')
            for table in ('history_evidence_rows', 'history_partition_members'):
                for action in ('UPDATE', 'DELETE'):
                    con.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'immutable history artifact'); END")

    def writer(self, receipts=(), *, base_ref=None):
        return HistoryWriter(self, receipts, base_ref=base_ref)

    def manifest(self, ref, expected_hash=None):
        if ref in self._manifests:
            if expected_hash is not None and ref != 'history_' + expected_hash:
                raise IntegrityError('HISTORY_ARTIFACT_HASH_MISMATCH')
            if ref != 'history_' + digest(self._manifests[ref]):
                raise IntegrityError('HISTORY_ARTIFACT_HASH_MISMATCH')
            return self._manifests[ref]
        document = self.store.get_document('history_artifact_manifests', ref)
        if not document:
            raise IntegrityError('HISTORY_ARTIFACT_MISSING')
        value = document
        actual = digest(value)
        if ref != 'history_' + actual or expected_hash is not None and actual != expected_hash:
            raise IntegrityError('HISTORY_ARTIFACT_HASH_MISMATCH')
        if value.get('artifact_version') != VERSION:
            raise IntegrityError('HISTORY_ARTIFACT_VERSION_UNSUPPORTED')
        self._manifests = {ref: value}
        return value

    def iter_rows(self, ref, kind, *, instrument_id=None, start=None, end=None, expected_hash=None):
        manifest = self.manifest(ref, expected_hash)
        start, end = str(start or '0001-01-01'), str(end or '9999-12-31')
        # Security intervals can start before a requested date and remain valid.
        parts = [p['partition_hash'] for p in manifest['partitions'] if p['session'] <= end and (kind != 'bar' or p['session'] >= start)]
        for offset in range(0, len(parts), 400):
            batch = parts[offset:offset + 400]
            args = [*batch, kind]
            extra = ''
            if instrument_id is not None:
                extra = ' AND m.instrument_id=?'
                args.append(instrument_id)
            with self.store.transaction(write=False) as con:
                placeholders = ','.join('?' for _ in batch)
                cursor = con.execute('SELECT r.*,m.kind AS member_kind,m.instrument_id AS member_iid,m.session AS member_session FROM history_partition_members m LEFT JOIN history_evidence_rows r ON r.row_hash=m.row_hash WHERE m.partition_hash IN (' + placeholders + ') AND m.kind=?' + extra + ' ORDER BY m.instrument_id,m.session,r.row_hash', args)
                for row in cursor:
                    if row['row_hash'] is None or row['payload'] is None:
                        raise IntegrityError('MISSING_HISTORY_ROW')
                    if hashlib.sha256(row['payload'].encode()).hexdigest() != row['row_hash']:
                        raise IntegrityError('CORRUPT_HISTORY_ROW')
                    value = json.loads(row['payload'])
                    session = value.get('session', value.get('effective_from'))
                    if row['member_kind'] != kind or value['instrument_id'] != row['member_iid'] or str(session) != row['member_session']:
                        raise IntegrityError('CORRUPT_HISTORY_PARTITION_INDEX')
                    parsed = (DailyBar if kind == 'bar' else SecurityRecord).model_validate(value)
                    if kind == 'security' and parsed.effective_to is not None and str(parsed.effective_to) <= start:
                        continue
                    yield parsed

    def verify(self, ref, expected_hash=None):
        """Stream partition hashes; no full JSON or all-row Python collection."""
        manifest = self.manifest(ref, expected_hash)
        with self.store.transaction(write=False) as con:
            for part in manifest['partitions']:
                hasher = hashlib.sha256(); count = 0
                for row in con.execute('SELECT m.row_hash,m.kind,m.instrument_id,m.session,r.row_hash AS stored_hash FROM history_partition_members m LEFT JOIN history_evidence_rows r ON r.row_hash=m.row_hash WHERE m.partition_hash=? ORDER BY m.kind,m.instrument_id,m.row_hash', (part['partition_hash'],)):
                    if row['stored_hash'] is None:raise IntegrityError('MISSING_HISTORY_ROW')
                    hasher.update(_json(list(row)[:4]).encode() + b'\n'); count += 1
                if hasher.hexdigest() != part['partition_hash'] or count != part['row_count']:
                    raise IntegrityError('CORRUPT_HISTORY_PARTITION')
        return manifest


class HistoryWriter:
    """Disk staging holds descriptors; Python memory is at most one input row."""
    def __init__(self, owner, receipts=(), *, base_ref=None):
        self.owner = owner
        self.receipts = {r.receipt_id: r.model_dump(mode='json') for r in receipts}
        self.base = owner.manifest(base_ref) if base_ref else None
        self.con = owner.store._connect()
        self.table = 'history_stage_' + uuid4().hex
        self.con.execute(f'CREATE TEMP TABLE {self.table} (row_hash TEXT PRIMARY KEY,kind TEXT,instrument_id TEXT,session TEXT)')
        self.con.execute(f'CREATE INDEX {self.table}_session ON {self.table}(session,kind,instrument_id,row_hash)')
        self.con.execute('BEGIN IMMEDIATE')
        self.added_rows = 0
        self.added_bytes = 0
        self.closed = False

    def append(self, value):
        kind = 'bar' if isinstance(value, DailyBar) else 'security'
        session = str(value.session if kind == 'bar' else value.effective_from)
        payload = _json(value); row_hash = hashlib.sha256(payload.encode()).hexdigest()
        inserted = self.con.execute('INSERT OR IGNORE INTO history_evidence_rows VALUES(?,?,?,?,?)', (row_hash, kind, value.instrument_id, session, payload)).rowcount
        self.added_rows += inserted; self.added_bytes += len(payload.encode()) * inserted
        self.con.execute(f'INSERT OR IGNORE INTO {self.table} VALUES(?,?,?,?)', (row_hash, kind, value.instrument_id, session))

    def finish(self):
        try:
            partitions = {p['session']: p for p in self.base['partitions']} if self.base else {}
            for session_row in self.con.execute(f'SELECT DISTINCT session FROM {self.table} ORDER BY session').fetchall():
                session = session_row[0]
                # Explicit delta append retains unchanged evidence on that session.
                if self.base and session in partitions:
                    self.con.execute(f'INSERT OR IGNORE INTO {self.table} SELECT row_hash,kind,instrument_id,session FROM history_partition_members WHERE partition_hash=?', (partitions[session]['partition_hash'],))
                hasher = hashlib.sha256(); count = 0; bars = 0; instruments = set()
                for row in self.con.execute(f'SELECT row_hash,kind,instrument_id,session FROM {self.table} WHERE session=? ORDER BY kind,instrument_id,row_hash', (session,)):
                    hasher.update(_json(list(row)).encode() + b'\n'); count += 1
                    bars += row['kind'] == 'bar'; instruments.add(row['instrument_id'])
                part_hash = hasher.hexdigest()
                self.con.execute(f'INSERT OR IGNORE INTO history_partition_members SELECT ?,row_hash,kind,instrument_id,session FROM {self.table} WHERE session=?', (part_hash, session))
                partitions[session] = {'session': session, 'partition_hash': part_hash, 'row_count': count, 'bar_count': bars, 'instrument_count': len(instruments)}
            self.con.commit()
            receipts = {r['receipt_id']: r for r in self.base['receipts']} if self.base else {}
            if any(ref in receipts and SourceReceipt.model_validate(receipts[ref])!=SourceReceipt.model_validate(value) for ref,value in self.receipts.items()):
                raise IntegrityError('HISTORY_RECEIPT_IDENTITY_CONFLICT')
            receipts.update(self.receipts)
            value = {'artifact_version': VERSION, 'partitions': [partitions[s] for s in sorted(partitions)], 'receipts': [receipts[k] for k in sorted(receipts)],
                     'provenance_policy': 'ORIGINAL_SOURCE_REFS_COMPARABLE_BASIS_AND_FOUR_CLOCKS_PRESERVED_IN_EACH_IMMUTABLE_ROW'}
            artifact_hash = digest(value); ref = 'history_' + artifact_hash
            self.owner.store.put_document('history_artifact_manifests', ref, value)
            return {'history_artifact_ref': ref, 'history_artifact_hash': artifact_hash}
        finally:
            self.close()

    def close(self):
        if not self.closed:
            self.con.close(); self.closed = True

    def __del__(self):
        if hasattr(self,'closed'):self.close()


def bind_history(bundle, store):
    if bundle.history_artifact_ref:
        owner = store if isinstance(store, HistoryArtifactStore) else HistoryArtifactStore(store)
        owner.verify(bundle.history_artifact_ref, bundle.history_artifact_hash)
        bundle._history_store = owner
    return bundle


def compact_input(bundle, store, *, recent_sessions=6):
    if bundle.history_artifact_ref:
        owner = store if isinstance(store, HistoryArtifactStore) else HistoryArtifactStore(store)
        writer = owner.writer(bundle.receipts,base_ref=bundle.history_artifact_ref)
        for row in bundle.bars:writer.append(row)
        for row in bundle.universe.securities:writer.append(row)
        identity=writer.finish()
        return bind_history(bundle.model_copy(update=identity),owner)
    if len({b.session for b in bundle.bars}) <= recent_sessions:
        return bundle
    owner = store if isinstance(store, HistoryArtifactStore) else HistoryArtifactStore(store)
    writer = owner.writer(bundle.receipts)
    for row in bundle.bars:
        writer.append(row)
    for row in bundle.universe.securities:
        writer.append(row)
    identity = writer.finish()
    recent_start = [s for s in bundle.sessions if s <= bundle.observation_date][-recent_sessions]
    securities = tuple(s for s in bundle.universe.securities if s.effective_to is None or s.effective_to > recent_start)
    compact = bundle.model_copy(update={**identity, 'bars': tuple(b for b in bundle.bars if b.session >= recent_start),
        'universe': bundle.universe.model_copy(update={'securities': securities})})
    compact._history_store = owner
    return compact


def _owner(bundle):
    if bundle.history_artifact_ref and bundle._history_store is None:
        raise IntegrityError('HISTORY_ARTIFACT_STORE_NOT_BOUND')
    return bundle._history_store


def iter_bars(bundle, *, instrument_id=None, start=None, end=None):
    start=date.fromisoformat(start) if isinstance(start,str) else start
    end=date.fromisoformat(end) if isinstance(end,str) else end
    if bundle.history_artifact_ref:
        recent = {digest(b): b for b in bundle.bars if (instrument_id is None or b.instrument_id == instrument_id) and (start is None or b.session >= start) and (end is None or b.session <= end)}
        for row in _owner(bundle).iter_rows(bundle.history_artifact_ref, 'bar', instrument_id=instrument_id, start=start, end=end, expected_hash=bundle.history_artifact_hash):
            recent.pop(digest(row),None)
            yield row
        yield from recent.values()
        return
    for bar in bundle.bars:
        if (instrument_id is None or bar.instrument_id == instrument_id) and (start is None or bar.session >= start) and (end is None or bar.session <= end):
            yield bar


def iter_history_securities(bundle, *, instrument_id=None, start=None, end=None):
    if bundle.history_artifact_ref:
        recent = {digest(s):s for s in bundle.universe.securities if instrument_id is None or s.instrument_id == instrument_id}
        for row in _owner(bundle).iter_rows(bundle.history_artifact_ref, 'security', instrument_id=instrument_id, start=start, end=end, expected_hash=bundle.history_artifact_hash):
            recent.pop(digest(row),None)
            yield row
        yield from recent.values()
        return
    yield from (s for s in bundle.universe.securities if instrument_id is None or s.instrument_id == instrument_id)


def history_identity(bundle):
    return {'history_artifact_ref': bundle.history_artifact_ref, 'history_artifact_hash': bundle.history_artifact_hash} if bundle.history_artifact_ref else {}


def history_receipts(bundle):
    """Resolve historical parents from verified manifest; current IDs cannot rewrite them."""
    receipts={}
    if bundle.history_artifact_ref:
        manifest=_owner(bundle).manifest(bundle.history_artifact_ref,bundle.history_artifact_hash)
        receipts={r['receipt_id']:SourceReceipt.model_validate(r) for r in manifest['receipts']}
    for receipt in bundle.receipts:
        if receipt.receipt_id in receipts and receipts[receipt.receipt_id]!=receipt:
            raise IntegrityError('HISTORY_RECEIPT_IDENTITY_CONFLICT')
        receipts[receipt.receipt_id]=receipt
    return tuple(receipts[k] for k in sorted(receipts))


def history_resolver(bundle, *, cutoff, allow_future_sessions=False):
    from .visibility import EvidenceResolver
    class BoundedResolver(EvidenceResolver):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            self.history_record_hashes=defaultdict(set)

        def resolve(self, obj, **kwargs):
            result = super().resolve(obj, **kwargs)
            if bundle.history_artifact_ref and result.allowed:
                row = obj.model_dump(mode='json') if hasattr(obj, 'model_dump') else dict(obj)
                if 'comparable_basis' in row or 'listed_on' in row:
                    row_hash=digest(row)
                    self.rows.pop(row_hash, None)
                    self.history_record_hashes[row['instrument_id']].add(bytes.fromhex(row_hash))
            return result

        def effective_dependencies(self):
            result = super().effective_dependencies()
            if bundle.history_artifact_ref:
                selected={iid:digest(sorted(value.hex() for value in hashes)) for iid,hashes in sorted(self.history_record_hashes.items())}
                result['immutable_history'] = {**history_identity(bundle), 'selection_policy': 'ORIGINAL_EVIDENCE_RESOLVER_INSTRUMENT_SESSION_AND_FOUR_CLOCKS',
                    'selected_record_count':sum(map(len,self.history_record_hashes.values())), 'selected_records_hash':digest(selected)}
            return result
    return BoundedResolver(history_receipts(bundle), cutoff=cutoff, coverage_type=bundle.coverage_type, sessions=bundle.sessions,
                           observation_date=None if allow_future_sessions else bundle.observation_date)


class InstrumentBars:
    def __init__(self, bundle, resolver, rejected, *, cache_size=8, compressed_cache_bytes=8*1024*1024):
        self.bundle, self.resolver, self.rejected = bundle, resolver, rejected
        self.cache_size, self.cache = cache_size, OrderedDict()
        # Peer/sector loops repeatedly need T across the universe. Retain only
        # that one resolved PIT row per instrument outside the historical LRU.
        self.current_rows={}
        # Disposable, byte-bounded, per-instrument L2. Only bytes produced here
        # are decoded; never load pickle from an artifact, file or provider.
        self.compressed=OrderedDict();self.compressed_bytes=0;self.compressed_budget=compressed_cache_bytes

    def bar(self, session, iid):
        if str(session)==str(self.bundle.observation_date) and iid in self.current_rows:
            return self.current_rows[iid]
        if iid not in self.cache and iid in self.compressed:
            self.cache[iid]=pickle.loads(zlib.decompress(self.compressed[iid]))
            self.compressed.move_to_end(iid)
            if len(self.cache)>self.cache_size:self.cache.popitem(last=False)
        if iid not in self.cache:
            from .strategy_experiments import _select_revision
            groups = defaultdict(list)
            for b in iter_bars(self.bundle, instrument_id=iid, end=self.bundle.observation_date):
                if b.quality == 'INVALID':
                    self.rejected.append({'kind': 'BAR', 'instrument_id': iid, 'session': str(b.session), 'reason_codes': ['INVALID_PRICE_QUALITY']}); continue
                proof = self.resolver.resolve(b, instrument_id=iid, session=b.session, require_session=True)
                if proof.allowed:
                    groups[str(b.session)].append(b)
                else:
                    self.rejected.append({'kind': 'BAR', 'instrument_id': iid, 'session': str(b.session), 'reason_codes': list(proof.reason_codes)})
            selected = {}
            for day, rows in groups.items():
                value = _select_revision(rows)
                if value is not None:
                    selected[day] = value.model_dump()
                else:
                    self.rejected.append({'kind': 'BAR', 'instrument_id': iid, 'session': day, 'reason_codes': ['CONFLICTING_PRICE_REVISION']})
            self.cache[iid] = selected
            self.current_rows[iid]=selected.get(str(self.bundle.observation_date))
            encoded=zlib.compress(pickle.dumps(selected,protocol=5),level=1)
            if len(encoded)<=self.compressed_budget:
                self.compressed[iid]=encoded;self.compressed_bytes+=len(encoded)
                while self.compressed_bytes>self.compressed_budget:
                    _,evicted=self.compressed.popitem(last=False);self.compressed_bytes-=len(evicted)
            if len(self.cache) > self.cache_size:
                self.cache.popitem(last=False)
        self.cache.move_to_end(iid)
        return self.cache[iid].get(str(session))


class OutcomeBarMapping(Mapping):
    """Read-only bounded instrument cache with each consumer's original revision rule."""
    def __init__(self,bundle,cutoff,*,resolver=None,strict_ingested=False,cache_size=8):
        self.bundle=bundle;self.cutoff=cutoff;self.resolver=resolver or history_resolver(bundle,cutoff=cutoff,allow_future_sessions=True)
        self.strict_ingested=strict_ingested;self.cache_size=cache_size;self.cache=OrderedDict()

    def _instrument(self,iid):
        if iid not in self.cache:
            groups=defaultdict(list)
            for row in iter_bars(self.bundle,instrument_id=iid):
                if not self.strict_ingested and row.quality=='INVALID':continue
                if self.resolver.is_visible(row,instrument_id=iid,session=row.session,require_session=True):groups[row.session].append(row)
            selected={}
            for day,rows in groups.items():
                if self.strict_ingested:
                    latest=max(r.ingested_at for r in rows);choices=[r for r in rows if r.ingested_at==latest]
                    if len({digest(r) for r in choices})!=1:raise ValueError('conflicting outcome price revision')
                    selected[day]=choices[0]
                else:
                    from .strategy_experiments import _select_revision
                    row=_select_revision(rows)
                    if row is not None:selected[day]=row
            self.cache[iid]=selected
            if len(self.cache)>self.cache_size:self.cache.popitem(last=False)
        self.cache.move_to_end(iid);return self.cache[iid]

    def __getitem__(self,key):
        iid,day=key;return self._instrument(iid)[day]

    def __iter__(self):
        for iid in sorted({s.instrument_id for s in self.bundle.universe.securities}):
            for day in self._instrument(iid):yield (iid,day)

    def __len__(self):return sum(1 for _ in self)


class _JSONStream:
    """Incremental JSON values from SQLite blob I/O; no optional parser dependency."""
    def __init__(self, blob):
        self.blob=blob;self.buffer='';self.decoder=codecs.getincrementaldecoder('utf-8')();self.eof=False
        self.hasher=hashlib.sha256();self.json_decoder=json.JSONDecoder()

    def fill(self):
        raw=self.blob.read(65536);self.hasher.update(raw)
        if not raw:self.eof=True
        self.buffer+=self.decoder.decode(raw,final=not raw)

    def peek(self):
        while True:
            self.buffer=self.buffer.lstrip()
            if self.buffer:return self.buffer[0]
            if self.eof:raise IntegrityError('TRUNCATED_LEGACY_INPUT')
            self.fill()

    def take(self,token):
        if self.peek()!=token:raise IntegrityError('MALFORMED_LEGACY_INPUT')
        self.buffer=self.buffer[1:]

    def value(self):
        self.peek()
        while True:
            try:
                value,end=self.json_decoder.raw_decode(self.buffer)
                self.buffer=self.buffer[end:];return value
            except json.JSONDecodeError:
                if self.eof:raise IntegrityError('MALFORMED_LEGACY_INPUT')
                self.fill()

    def array(self):
        self.take('[')
        if self.peek()==']':self.take(']');return
        while True:
            yield self.value()
            if self.peek()==']':self.take(']');return
            self.take(',')

    def fields(self):
        self.take('{')
        if self.peek()=='}':self.take('}');return
        while True:
            name=self.value();self.take(':');yield name
            if self.peek()=='}':self.take('}');return
            self.take(',')


def migrate_legacy_input(store, input_id, *, observation_date=None, recent_sessions=6):
    """Append a compact replacement; verify original bytes, leave old ID immutable.

    sqlite3.blobopen reads TEXT bytes in 64 KiB blocks. Unlike json_each on a
    giant TEXT value, SQLite also need not materialize its entire JSON tree.
    The caller registers the returned bundle through CandidateService.
    """
    index=store.get_document('candidate_input_indexes',input_id) or {}
    observed=date.fromisoformat(str(observation_date or index.get('observation_date')))
    owner=HistoryArtifactStore(store);writer=owner.writer()
    value={};recent_bars=[];recent_securities=[];bar_count=0;security_count=0
    with store.transaction(write=False) as con:
        row=con.execute("SELECT rowid,content_hash,length(CAST(payload AS BLOB)) AS input_bytes FROM documents WHERE namespace='candidate_inputs' AND document_id=?",(input_id,)).fetchone()
        if not row:writer.close();raise IntegrityError('LEGACY_INPUT_MISSING')
        try:
            with con.blobopen('documents','payload',row['rowid'],readonly=True) as blob:
                parser=_JSONStream(blob)
                for field in parser.fields():
                    if field=='bars':
                        for item in parser.array():
                            bar=DailyBar.model_validate(item);writer.append(bar);bar_count+=1
                            if bar.session>=observed-timedelta(days=recent_sessions*4):recent_bars.append(bar)
                    elif field=='universe':
                        universe={}
                        for name in parser.fields():
                            if name=='securities':
                                for item in parser.array():
                                    security=SecurityRecord.model_validate(item);writer.append(security);security_count+=1
                                    if security.effective_to is None or security.effective_to>observed-timedelta(days=recent_sessions*4):recent_securities.append(security)
                            else:universe[name]=parser.value()
                        value['universe']=universe
                    else:value[field]=parser.value()
                while not parser.eof:parser.fill()
                if parser.buffer.strip() or parser.hasher.hexdigest()!=row['content_hash'] or input_id!='market_'+row['content_hash']:
                    raise IntegrityError('LEGACY_INPUT_BYTES_HASH_MISMATCH')
            if value.get('history_artifact_ref'):
                raise IntegrityError('INPUT_ALREADY_USES_HISTORY_ARTIFACT')
            writer.receipts={r['receipt_id']:SourceReceipt.model_validate(r).model_dump(mode='json') for r in value.get('receipts',())}
            identity=writer.finish()
        except BaseException:
            writer.close();raise
    recent_start=[date.fromisoformat(s) for s in value['sessions'] if date.fromisoformat(s)<=observed][-recent_sessions]
    value['bars']=[b for b in recent_bars if b.session>=recent_start]
    value['universe']['securities']=[s for s in recent_securities if s.effective_to is None or s.effective_to>recent_start]
    value.update(identity)
    bundle=MarketInput.model_validate(value);bundle._history_store=owner
    report={'source_input_id':input_id,'source_input_bytes':row['input_bytes'],'source_input_hash_verified':True,
            'source_bar_rows':bar_count,'source_security_rows':security_count,'daily_recent_bar_rows':len(bundle.bars),
            'new_row_bytes':writer.added_bytes,'new_rows':writer.added_rows,**identity,'migration_mode':'SQLITE_BLOB_64K_STREAM_APPEND_ONLY'}
    return bundle,report


def migrate_legacy_file(store, path, *, observation_date, recent_sessions=6):
    """Stream an archived capture file into the same authoritative history store.

    The file is read-only; its exact byte hash is returned. No coverage label,
    clock or source authority is upgraded by migration. Caller registers the
    compact input explicitly after deciding whether this capture is usable.
    """
    from pathlib import Path
    observed=date.fromisoformat(str(observation_date));owner=HistoryArtifactStore(store);writer=owner.writer()
    value={};recent_bars=[];recent_securities=[];bar_count=0;security_count=0
    try:
        with Path(path).open('rb') as stream:
            parser=_JSONStream(stream)
            for field in parser.fields():
                if field=='bars':
                    for item in parser.array():
                        bar=DailyBar.model_validate(item);writer.append(bar);bar_count+=1
                        if bar.session>=observed-timedelta(days=recent_sessions*4):recent_bars.append(bar)
                elif field=='universe':
                    universe={}
                    for name in parser.fields():
                        if name=='securities':
                            for item in parser.array():
                                security=SecurityRecord.model_validate(item);writer.append(security);security_count+=1
                                if security.effective_to is None or security.effective_to>observed-timedelta(days=recent_sessions*4):recent_securities.append(security)
                        else:universe[name]=parser.value()
                    value['universe']=universe
                else:value[field]=parser.value()
            while not parser.eof:parser.fill()
            if parser.buffer.strip():raise IntegrityError('MALFORMED_LEGACY_INPUT')
        if value.get('history_artifact_ref'):raise IntegrityError('INPUT_ALREADY_USES_HISTORY_ARTIFACT')
        if value.get('observation_date')!=str(observed):raise IntegrityError('LEGACY_INPUT_OBSERVATION_DATE_MISMATCH')
        writer.receipts={r['receipt_id']:SourceReceipt.model_validate(r).model_dump(mode='json') for r in value.get('receipts',())}
        identity=writer.finish()
    except BaseException:
        writer.close();raise
    recent_start=[date.fromisoformat(s) for s in value['sessions'] if date.fromisoformat(s)<=observed][-min(recent_sessions,len(value['sessions']))]
    value['bars']=[b for b in recent_bars if b.session>=recent_start]
    value['universe']['securities']=[s for s in recent_securities if s.effective_to is None or s.effective_to>recent_start]
    value.update(identity)
    bundle=MarketInput.model_validate(value);bundle._history_store=owner
    return bundle,{'source_file_sha256':parser.hasher.hexdigest(),'source_input_bytes':Path(path).stat().st_size,
            'source_bar_rows':bar_count,'source_security_rows':security_count,'daily_recent_bar_rows':len(bundle.bars),
            'new_row_bytes':writer.added_bytes,'new_rows':writer.added_rows,**identity,'migration_mode':'FILE_64K_STREAM_APPEND_ONLY',
            'source_coverage_type_preserved':bundle.coverage_type}
