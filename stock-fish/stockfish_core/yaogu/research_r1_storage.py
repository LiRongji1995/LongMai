"""Research-local bounded input and content-addressed shared result partitions."""
import hashlib
import json
import time
from pathlib import Path
from .contracts import digest
from .history_artifacts import HistoryArtifactStore


def sample_memory():
    """Process RSS in bytes; unavailable sampling is never reported as zero."""
    import sys
    base=dict(unit='bytes',scope='PROCESS',status='AVAILABLE')
    try:
        if sys.platform=='win32':
            import ctypes
            from ctypes import wintypes
            class Counters(ctypes.Structure):
                _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)]+[(key,ctypes.c_size_t) for key in ('PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage')]
            counters=Counters();counters.cb=ctypes.sizeof(counters)
            kernel=ctypes.WinDLL('kernel32',use_last_error=True);psapi=ctypes.WinDLL('psapi',use_last_error=True)
            kernel.GetCurrentProcess.restype=wintypes.HANDLE
            psapi.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
            if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(),ctypes.byref(counters),counters.cb):raise OSError('GetProcessMemoryInfo failed')
            return dict(base,method='WINDOWS_GET_PROCESS_MEMORY_INFO',current=counters.WorkingSetSize,peak=counters.PeakWorkingSetSize)
        import resource
        peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024)
        return dict(base,method='UNIX_GETRUSAGE',current=None,peak=peak)
    except (ImportError,OSError,AttributeError):
        return dict(base,status='MEMORY_MEASUREMENT_UNAVAILABLE',method='UNAVAILABLE',current=None,peak=None)


def validate_history_seconds(seconds):
    """Explicit offline runtime allowance; never disable byte/RSS/integrity gates."""
    import math
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not 0 < seconds <= 7200:
        raise ValueError('INVALID_RESEARCH_HISTORY_TIME_BUDGET')


class BudgetHistory(HistoryArtifactStore):
    """Bound requested window, not total immutable archive size.

    V2 still retains its full pool/window context; budget measures source bytes,
    not RSS. Direct preload reads are audited by the read-only connection proxy.
    """
    def __init__(self, store, *, seconds=300, byte_budget=2*1024**3):
        validate_history_seconds(seconds)
        self.metrics=dict(time_budget_seconds=seconds,payload_rows_read=0,payload_bytes_read=0,partition_members_verified=0,verification='PARTITION_INDEX_AND_EACH_CONSUMED_PAYLOAD')
        self.metrics.update(memory_measurement=sample_memory(),memory_budget_bytes=1024**3,memory_policy='ENFORCE_WHEN_AVAILABLE_ELSE_BYTE_TIME_BUDGETS')
        self.started=time.monotonic();self.seconds=seconds;self.byte_budget=byte_budget
        self._scratch_cleanups=[]
        self._read_context=store.transaction(write=False)
        self._read_connection=self._read_context.__enter__()
        self._closed=False
        # Connection-local cache only; does not alter the database or its schema.
        self._read_connection.execute('PRAGMA cache_size=-65536')
        self.metrics['sqlite_page_cache_budget_bytes']=64*1024**2
        owner=self
        class Cursor:
            def __init__(self,cursor):self.cursor=cursor
            def __iter__(self):
                try:
                    for row in self.cursor:
                        if time.monotonic()-owner.started>owner.seconds:raise ValueError('RECOVERABLE_HISTORY_TIME_BUDGET_EXCEEDED')
                        if 'payload' in row.keys() and row['payload'] is not None:
                            owner.metrics['payload_rows_read']+=1
                            if owner.metrics['payload_rows_read'] % 1024 == 0:
                                measurement=sample_memory();owner.metrics['memory_measurement']=measurement
                                rss=measurement['peak']
                                if rss is not None:
                                    owner.metrics['process_peak_rss_bytes']=rss
                                    if rss>owner.metrics['memory_budget_bytes']:raise ValueError('RECOVERABLE_RESEARCH_RSS_BUDGET_EXCEEDED')
                            owner.metrics['payload_bytes_read']+=len(row['payload'].encode())
                            if owner.metrics['payload_bytes_read']>owner.byte_budget:raise ValueError('RECOVERABLE_HISTORY_BYTE_BUDGET_EXCEEDED')
                        yield row
                except Exception:
                    owner.close()
                    raise
            def __getattr__(self,key):return getattr(self.cursor,key)
        class Connection:
            def __init__(self,con):self.con=con
            def execute(self,*args,**kwargs):return Cursor(self.con.execute(*args,**kwargs))
        class Store:
            read_only=True
            def get_document(self,namespace,document_id):
                from ..storage import checked_payload
                row=owner._read_connection.execute('SELECT * FROM documents WHERE namespace=? AND document_id=?',(namespace,document_id)).fetchone()
                return checked_payload(row) if row else None
            def transaction(self,**kwargs):
                from contextlib import contextmanager
                @contextmanager
                def transaction():
                    if kwargs.get('write',True):raise ValueError('READ_ONLY_HISTORY_WRITE_REJECTED')
                    if owner._closed:raise ValueError('HISTORY_SNAPSHOT_CLOSED')
                    yield Connection(owner._read_connection)
                return transaction()
        try:super().__init__(Store())
        except BaseException:
            self.close()
            raise

    def close(self):
        if not self._closed:
            self._read_context.__exit__(None,None,None);self._closed=True
            for cleanup in self._scratch_cleanups:cleanup()
            self._scratch_cleanups.clear()

    def __del__(self):
        if hasattr(self,'_closed'):self.close()

    def verify(self, ref, expected_hash=None):
        result=super().verify(ref,expected_hash)
        self.metrics['partition_members_verified']=sum(p['row_count'] for p in result['partitions'])
        return result


class SharedWriter:
    """Hash-addressed DAG, packed in bounded atomic JSON shards (4 MiB target)."""
    def __init__(self,root,writer,*,max_bytes=2*1024**3):
        self.root=Path(root);self.write=writer;self.max_bytes=max_bytes;self.bytes=0;self.count=0
        self.index={};self.pending={};self.pending_bytes=0
        (self.root/'objects').mkdir()

    def flush(self):
        if not self.pending:return
        key=digest(self.pending)
        self.write(self.root/'objects'/f'{key}.json',self.pending)
        for member in self.pending:self.index[member]=key
        self.pending={};self.pending_bytes=0

    def pack(self,value):
        if isinstance(value,list):
            if len(value)>256:return {'$r1_list':[self.pack({'items':value[i:i+256]}) for i in range(0,len(value),256)]}
            return [self.pack(v) for v in value]
        if not isinstance(value,dict):return value
        packed={k:self.pack(v) for k,v in value.items()}
        encoded=json.dumps(packed,ensure_ascii=False,sort_keys=True,default=str)
        if len(encoded)<384:return packed
        key=digest(packed)
        if key not in self.index and key not in self.pending:
            size=len(encoded.encode())
            if self.bytes+size>self.max_bytes:raise ValueError('RECOVERABLE_OUTPUT_BYTE_BUDGET_EXCEEDED')
            self.pending[key]=packed;self.pending_bytes+=size;self.bytes+=size;self.count+=1
            if self.pending_bytes>=4*1024**2:self.flush()
        return {'$r1_ref':key}

    def finish(self):
        self.flush();self.write(self.root/'objects/index.json',self.index)
        return dict(index_hash=digest(self.index),objects=self.count,bytes=self.bytes)


def expand(root,value):
    """Expand one date. Verify shard and object hashes, with a bounded shard LRU."""
    from collections import OrderedDict
    root=Path(root);index_path=root/'objects/index.json'
    index=json.loads(index_path.read_text()) if index_path.exists() else None
    cache=OrderedDict()
    def read(key):
        if len(key)!=64 or any(c not in '0123456789abcdef' for c in key):raise ValueError('INVALID_RESULT_REFERENCE')
        shard=index[key] if index is not None else key
        if shard not in cache:
            payload=json.loads((root/'objects'/f'{shard}.json').read_text())
            if digest(payload)!=shard:raise ValueError('RESULT_OBJECT_HASH_MISMATCH')
            cache[shard]=payload
            if len(cache)>4:cache.popitem(last=False)
        cache.move_to_end(shard)
        payload=cache[shard][key] if index is not None else cache[shard]
        if digest(payload)!=key:raise ValueError('RESULT_OBJECT_HASH_MISMATCH')
        return payload
    def restore(value):
        if isinstance(value,list):return [restore(x) for x in value]
        if not isinstance(value,dict):return value
        if set(value)=={'$r1_ref'}:return restore(read(value['$r1_ref']))
        if set(value)=={'$r1_list'}:return [item for part in value['$r1_list'] for item in restore(part)['items']]
        return {k:restore(v) for k,v in value.items()}
    return restore(value)


class VerifiedWindowCache:
    """Private, bounded L3 for rows already resolved by InstrumentBars.

    A full-pool sweep larger than the 64 MiB L2 must not re-read and re-resolve
    every immutable window on each benchmark horizon. This disposable cache
    belongs to one input/cutoff/read transaction, never to a later research run.
    """
    def __init__(self, source, owner, *, max_bytes=256*1024**2):
        import tempfile
        self.source=source;self.owner=owner;self.max_bytes=max_bytes
        self.temporary=tempfile.TemporaryDirectory(prefix='stockfish-r1-verified-windows-')
        self.root=Path(self.temporary.name);self.entries={};self.bytes=0
        owner._scratch_cleanups.append(self.temporary.cleanup)
        owner.metrics.update(verified_window_disk_hits=0,verified_window_disk_bytes=0,
                             verified_window_disk_budget_bytes=max_bytes)

    def __getattr__(self, name):
        return getattr(self.source,name)

    def bar(self, session, iid):
        import pickle
        import zlib
        if self.owner._closed:raise ValueError('HISTORY_SNAPSHOT_CLOSED')
        source=self.source
        if str(session)[:10]==str(source.bundle.observation_date) and iid in source.current_rows:
            return source.bar(session,iid)
        if iid not in source.cache and iid not in source.compressed and iid in self.entries:
            key,size=self.entries[iid];path=self.root/key
            if path.stat().st_size!=size:raise ValueError('VERIFIED_WINDOW_CACHE_CORRUPT')
            encoded=path.read_bytes()
            if hashlib.sha256(encoded).hexdigest()!=key:raise ValueError('VERIFIED_WINDOW_CACHE_CORRUPT')
            # Bytes are accepted only if they match what this object just wrote.
            # Never deserialize an external artifact or provider payload.
            source.cache[iid]=pickle.loads(zlib.decompress(encoded))
            if len(source.cache)>source.cache_size:source.cache.popitem(last=False)
            self.owner.metrics['verified_window_disk_hits']+=1
        value=source.bar(session,iid)
        if iid not in self.entries and iid in source.cache:
            encoded=zlib.compress(pickle.dumps(source.cache[iid],protocol=5),level=1)
            if self.bytes+len(encoded)>self.max_bytes:raise ValueError('RECOVERABLE_VERIFIED_WINDOW_DISK_BUDGET_EXCEEDED')
            key=hashlib.sha256(encoded).hexdigest();path=self.root/key
            if not path.exists():
                with path.open('xb') as stream:stream.write(encoded)
            self.entries[iid]=(key,len(encoded));self.bytes+=len(encoded)
            self.owner.metrics['verified_window_disk_bytes']=self.bytes
            self.owner.metrics['verified_window_count']=len(self.entries)
        return value


def bounded_context(bundle, cutoff):
    """Use existing V2 InstrumentBars LRU/L2 instead of duplicating its window.

    Only the optional preload marker is short-circuited. Every _bar lookup still
    reaches the original resolver, revision selection, and full-pool primitives.
    Scalar facts and the current cross-section remain proportional to universe.
    """
    from dataclasses import replace
    from . import stage_aware_momentum as v2
    ctx=v2._prepare_context(bundle,cutoff)
    if not hasattr(ctx.bars_by_session,'bar'):return ctx
    ctx.bars_by_session.compressed_budget=64*1024**2
    class NoWindowPreload(dict):
        def __contains__(self,key):
            return (isinstance(key,tuple) and key and key[0]=='BAR_PRELOAD') or super().__contains__(key)
    bars=VerifiedWindowCache(ctx.bars_by_session,bundle._history_store) if isinstance(bundle._history_store,BudgetHistory) else ctx.bars_by_session
    return replace(ctx,bars_by_session=bars,
                   scalar_cache=NoWindowPreload(ctx.scalar_cache))


def verify_shared_report(root, report):
    """Verify shared evidence without expanding it into twelve duplicated views."""
    root=Path(root);index_path=root/'objects/index.json'
    roots={}
    for result in report['results']:
        descriptor=result['detail']
        if set(descriptor)!={'$r1_ref'}:raise ValueError('INVALID_RESULT_ROOT_REFERENCE')
        roots[descriptor['$r1_ref']]=(report['manifest_hash'],result['input_hash'],result['observation_date'],result['source_hash'])
    if len(roots)!=len(report['results']):raise ValueError('DUPLICATE_RESULT_ROOT_REFERENCE')
    def check_root(key,value):
        if key in roots and tuple(value.get(k) for k in ('manifest_hash','input_hash','observation_date','source_hash'))!=roots[key]:raise ValueError('RESULT_DETAIL_IDENTITY_CONFLICT')
    if not index_path.exists():
        # Early V2 layout stored one object per file. Walk one object at a time.
        pending=[];seen=set()
        def refs(value):
            if isinstance(value,dict):
                if set(value)=={'$r1_ref'}:yield value['$r1_ref']
                else:
                    for child in value.values():yield from refs(child)
            elif isinstance(value,list):
                for child in value:yield from refs(child)
        pending.extend(refs(report))
        while pending:
            key=pending.pop()
            if key in seen:continue
            if len(key)!=64 or any(c not in '0123456789abcdef' for c in key):raise ValueError('INVALID_RESULT_REFERENCE')
            value=json.loads((root/'objects'/f'{key}.json').read_text())
            if digest(value)!=key:raise ValueError('RESULT_OBJECT_HASH_MISMATCH')
            check_root(key,value)
            seen.add(key);pending.extend(refs(value))
        return
    index=json.loads(index_path.read_text())
    if digest(index)!=report['storage']['index_hash']:raise ValueError('RESULT_INDEX_HASH_MISMATCH')
    seen=set()
    def check_refs(value):
        if isinstance(value,dict):
            if set(value)=={'$r1_ref'} and value['$r1_ref'] not in index:raise ValueError('MISSING_RESULT_REFERENCE')
            for child in value.values():check_refs(child)
        elif isinstance(value,list):
            for child in value:check_refs(child)
    check_refs(report)
    for shard in sorted(set(index.values())):
        if len(shard)!=64 or any(c not in '0123456789abcdef' for c in shard):raise ValueError('INVALID_RESULT_REFERENCE')
        path=root/'objects'/f'{shard}.json'
        if path.stat().st_size>64*1024**2:raise ValueError('RECOVERABLE_RESULT_SHARD_BYTE_BUDGET')
        values=json.loads(path.read_text())
        if digest(values)!=shard:raise ValueError('RESULT_SHARD_HASH_MISMATCH')
        for key,value in values.items():
            if index.get(key)!=shard or digest(value)!=key:raise ValueError('RESULT_OBJECT_HASH_MISMATCH')
            check_root(key,value)
            seen.add(key);check_refs(value)
    if seen!=set(index):raise ValueError('INCOMPLETE_RESULT_OBJECT_INDEX')
