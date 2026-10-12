"""Independent TEST_FIXTURE rows only: dictionary/84B index/ordinary LRU."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import struct
import zlib

import pytest

from stockfish_core.yaogu import evidence_audit_spool_v2 as v2
from stockfish_core.yaogu import evidence_audit_spool_v3 as v3
from stockfish_core.yaogu import evidence_audit_spool_v4 as module
from stockfish_core.yaogu.contracts import digest
from stockfish_core.yaogu.history_artifacts import digest_json_stream
from stockfish_core.yaogu.visibility import EvidenceResolver
from tests.yaogu.test_theme_evolution_r1 import fixture_market


def make(tmp_path, *, callback=None, size=2000, cache_bytes=8000, entries=8):
    bundle, cutoff, _ = fixture_market(7)
    kwargs=dict(cutoff=cutoff,coverage_type='TEST_FIXTURE',sessions=bundle.sessions,
                observation_date=bundle.observation_date)
    resolver=module.SpoolingEvidenceResolverV4(bundle.receipts,evidence_directory=tmp_path/'v4',
        segment_bytes=size,logical_read_callback=callback,cache_max_bytes=cache_bytes,
        cache_max_entries=entries,**kwargs)
    return bundle,resolver


def raw_rows(tmp_path, *, cache_bytes=8000, entries=8, limit=2000, count=3):
    _, resolver=make(tmp_path,cache_bytes=cache_bytes,entries=entries,size=limit)
    keys=[]
    for i in range(count):
        row={'TEST_FIXTURE':True,'ordinal':i,'nested':{'values':[i,None,'UNKNOWN']}}
        key=hashlib.sha256(v2._json(row)).hexdigest()
        resolver.rows[key]=row
        keys.append(key)
    resolver.close()
    return resolver,keys


def mutate(path):
    prior=path.stat();raw=bytearray(path.read_bytes());assert raw
    raw[len(raw)//2]^=1;path.write_bytes(raw)
    os.utime(path,ns=(prior.st_atime_ns,prior.st_mtime_ns))


@pytest.mark.parametrize('cache_bytes,entries',[(0,0),(0,4),(50000,0),(50000,400)])
def test_same67_all_canonical_values_sources_unknown_and_digest_match_v2_v3(tmp_path,cache_bytes,entries):
    bundle,cutoff,pack=fixture_market(67)
    kwargs=dict(cutoff=cutoff,coverage_type='TEST_FIXTURE',sessions=bundle.sessions,
                observation_date=bundle.observation_date)
    plain=EvidenceResolver(bundle.receipts,**kwargs)
    old=v2.SpoolingEvidenceResolverV2(bundle.receipts,evidence_directory=tmp_path/'v2',**kwargs)
    compressed=v3.SpoolingEvidenceResolverV3(bundle.receipts,evidence_directory=tmp_path/'v3',**kwargs)
    new=module.SpoolingEvidenceResolverV4(bundle.receipts,evidence_directory=tmp_path/'v4',
        cache_max_bytes=cache_bytes,cache_max_entries=entries,**kwargs)
    for panel in reversed(pack['sessions']):
        for row in panel['rows']:
            for _ in range(3):
                results=[r.resolve(row,instrument_id=row['instrument_id'],session=row['session'],
                    require_complete=True,require_session=True) for r in (plain,old,compressed,new)]
                assert results[0]==results[1]==results[2]==results[3]
    first=pack['sessions'][0]['rows'][0]
    for bad in (dict(instrument_id='600999.SH'),dict(source_refs=('MISSING',)),
                dict(session='1999-01-01',require_session=True)):
        results=[r.resolve(first,**bad) for r in (plain,old,compressed,new)]
        assert results[0]==results[1]==results[2]==results[3]
    expected=plain.effective_dependencies()
    proofs=[r.joint_dependency_digest_and_audit_manifest() for r in (old,compressed,new)]
    assert len(expected['records'])==268
    assert all(x['dependency_hash']==digest(expected) for x in proofs)
    assert list(new.effective_dependencies()['records'])==expected['records']
    assert json.loads(json.dumps(new.effective_dependencies()))==expected
    assert new.audit_manifest()['canonical_decoded_row_count']==268
    assert new.read_metrics['cache_bytes_peak']<=cache_bytes
    assert new.read_metrics['cache_entries_peak']<=entries
    for r in (old,compressed,new):r.close()


def test_resolve_is_original_completion_path_and_hits_read_full_frames(tmp_path):
    bundle,new=make(tmp_path)
    row=bundle.bars[0]
    assert new.resolve(row,require_complete=True).allowed
    assert new.resolve(row,require_complete=True).allowed
    before=deepcopy(new.read_metrics)
    assert new.resolve(row,require_complete=True).allowed
    after=new.read_metrics
    key=digest(row.model_dump(mode='json'));position=new.rows.index[key]
    assert after['hits']==before['hits']+1
    assert after['ordinary_frame_reads']==before['ordinary_frame_reads']+1
    assert after['ordinary_frame_returned_bytes']-before['ordinary_frame_returned_bytes']==position[2]
    assert after['actual_logical_decompressed_returned_bytes']==before['actual_logical_decompressed_returned_bytes']
    assert after['ordinary_index_returned_bytes']>before['ordinary_index_returned_bytes']
    assert after['ordinary_dict_returned_bytes']>before['ordinary_dict_returned_bytes']
    new.close()


def test_cache_fresh_nested_return_and_immutable_value(tmp_path):
    new,keys=raw_rows(tmp_path,count=1)
    first=new.rows[keys[0]]
    first['nested']['values'].append('CALLER_MUTATION')
    second=new.rows[keys[0]]
    assert second['nested']['values']==[0,None,'UNKNOWN']
    assert all(type(value) is bytes for value in new.rows._cache.values())
    before=new.read_metrics['hits'];new.rows[keys[0]]
    assert new.read_metrics['hits']==before+1
    new.close()


@pytest.mark.parametrize('bad_value',[bytearray(b'{}\n'),{},None,b'{}\n'])
def test_mutable_or_wrong_cached_content_is_fatal(tmp_path,bad_value):
    new,keys=raw_rows(tmp_path,count=1)
    new.rows[keys[0]]
    key=next(iter(new.rows._cache));new.rows._cache[key]=bad_value
    with pytest.raises(module.EvidenceAuditIntegrityError):new.rows[keys[0]]
    with pytest.raises(module.EvidenceAuditIntegrityError):new.audit_manifest()


@pytest.mark.parametrize('cache_bytes,entries',[(0,2),(10000,0),(1,2)])
def test_zero_and_oversize_disable_cache_and_actual_miss_inflates(tmp_path,cache_bytes,entries):
    new,keys=raw_rows(tmp_path,count=1,cache_bytes=cache_bytes,entries=entries)
    new.rows[keys[0]];before=new.rows.logical_read_bytes;new.rows[keys[0]]
    assert new.rows.logical_read_bytes-before==new.rows.index[keys[0]][3]
    assert new.read_metrics['hits']==0 and new.read_metrics['misses']==2
    assert new.read_metrics['cache_entries_current']==0
    new.close()


def test_lru_eviction_reaccess_inflates_and_bounds_both_limits(tmp_path):
    new,keys=raw_rows(tmp_path,entries=2,cache_bytes=1000)
    for key in keys:new.rows[key]
    before=new.rows.logical_read_bytes;new.rows[keys[0]]
    assert new.rows.logical_read_bytes-before==new.rows.index[keys[0]][3]
    assert new.read_metrics['misses']==4 and new.read_metrics['evictions']==2
    assert new.read_metrics['cache_bytes_peak']<=1000
    assert new.read_metrics['cache_entries_peak']==2
    new.close()


@pytest.mark.parametrize('name,bad',[('cache_max_bytes',True),('cache_max_bytes',-1),
    ('cache_max_bytes',2**63),('cache_max_bytes',1.0),('cache_max_entries',False),
    ('cache_max_entries',-1),('cache_max_entries',2**32)])
def test_limit_parameters_reject_boolean_negative_nonint_overflow(tmp_path,name,bad):
    _,cutoff,_=fixture_market(7)
    args=dict(cache_max_bytes=1,cache_max_entries=1);args[name]=bad
    with pytest.raises(ValueError):module.SpoolingEvidenceResolverV4([],evidence_directory=tmp_path/'bad',
        cutoff=cutoff,coverage_type='TEST_FIXTURE',**args)
    assert not (tmp_path/'bad').exists()


def test_explicit_limits_live_accounting_and_no_restore_or_disabled_rows(tmp_path):
    bundle,cutoff,_=fixture_market(7)
    args=dict(cutoff=cutoff,coverage_type='TEST_FIXTURE',cache_max_bytes=0,cache_max_entries=0)
    with pytest.raises(TypeError):module.SpoolingEvidenceResolverV4([],evidence_directory=tmp_path/'missing',cutoff=cutoff)
    with pytest.raises(ValueError):module.SpoolingEvidenceResolverV4([],evidence_directory=tmp_path/'off',record_rows=False,**args)
    live={**args,'coverage_type':'RECONSTRUCTED_HISTORICAL'}
    with pytest.raises(ValueError):module.SpoolingEvidenceResolverV4([],evidence_directory=tmp_path/'live',**live)
    with pytest.raises(TypeError):module.SpoolingEvidenceResolverV4([],evidence_directory=tmp_path/'badcb',logical_read_callback=True,**args)
    new=module.SpoolingEvidenceResolverV4([],evidence_directory=tmp_path/'owner',**args)
    new.close()
    with pytest.raises(FileExistsError):module.SpoolingEvidenceResolverV4([],evidence_directory=tmp_path/'owner',**args)
    assert new.resolve(bundle.bars[0]).allowed is False


def test_cache_configuration_mutation_is_not_unbounded(tmp_path):
    new,keys=raw_rows(tmp_path,count=1)
    new.rows.cache_max_bytes=2**90
    with pytest.raises(module.EvidenceAuditIntegrityError,match='CACHE_CONFIG_CHANGED'):new.rows[keys[0]]


@pytest.mark.parametrize('name,bad',[('cache_max_bytes',1.0),('cache_max_bytes',True),
    ('cache_max_entries',1.0),('cache_max_entries',True)])
def test_cache_configuration_equal_numeric_value_cannot_change_type(tmp_path,name,bad):
    new,keys=raw_rows(tmp_path,count=1,cache_bytes=1,entries=1)
    setattr(new.rows,name,bad)
    with pytest.raises(module.EvidenceAuditIntegrityError,match='CACHE_CONFIG_CHANGED'):new.rows[keys[0]]


def test_warm_cache_joint_ordinary_iterator_and_verify_preserve_full_guards(tmp_path):
    new,keys=raw_rows(tmp_path,count=3)
    for _ in range(2):
        for key in keys:new.rows[key]
    before=new.rows.logical_read_bytes
    physical=new.rows.verify()
    assert new.rows.logical_read_bytes==before and physical['complete_allowed_row_audit'] is False
    joint=new.joint_dependency_digest_and_audit_manifest()
    assert new.rows.logical_read_bytes-before==physical['logical_canonical_bytes']
    assert joint['audit_manifest']['complete_allowed_row_audit'] is True
    assert joint['audit_manifest']['logical_decode_validation']=='ALL_CANONICAL_ROWS_ZLIB_DICTIONARY_ADLER_EOF_SHA_NEWLINE'
    assert json.loads((new.rows.directory/'manifest.json').read_bytes())['complete_allowed_row_audit'] is False
    before=new.rows.logical_read_bytes
    assert len(list(new.effective_dependencies()['records']))==3
    assert new.rows.logical_read_bytes-before==physical['logical_canonical_bytes']
    new.close()


def test_partial_slice_preserves_selected_uncached_decode_and_full_physical_pre_post(tmp_path):
    new,keys=raw_rows(tmp_path,count=3)
    for _ in range(2):
        for key in keys:new.rows[key]
    records=new.effective_dependencies()['records']
    before=deepcopy(new.read_metrics)
    selected=records[:1]
    after=new.read_metrics
    first=sorted(keys)[0]
    assert len(selected)==1
    assert hashlib.sha256(v2._json(selected[0])).hexdigest()==first
    assert after['actual_logical_decompressed_returned_bytes']-before['actual_logical_decompressed_returned_bytes']==new.rows.index[first][3]
    assert after['hits']==before['hits'] and after['misses']==before['misses']
    all_frame_bytes=sum(p.stat().st_size for p in new.rows.segments)
    assert after['physical_read_buckets']['verify_frames']-before['physical_read_buckets']['verify_frames']==2*all_frame_bytes
    assert after['physical_read_buckets']['verify_append_index']-before['physical_read_buckets']['verify_append_index']==2*84*3
    assert after['physical_read_buckets']['verify_sorted_index']-before['physical_read_buckets']['verify_sorted_index']==2*(4096+84*3)
    before=new.rows.logical_read_bytes
    proof=new.joint_dependency_digest_and_audit_manifest()
    assert proof['complete_allowed_row_audit'] is True
    assert new.rows.logical_read_bytes-before==sum(x[3] for x in new.rows.index.values())
    new.close()


def test_current_whole_epoch_cannot_be_json_restored_or_reused_after_next_verify(tmp_path):
    new,keys=raw_rows(tmp_path,count=1)
    pre=new.rows.verify();copied=deepcopy(pre)
    with pytest.raises(module.EvidenceAuditIntegrityError,match='PRE_NOT_CURRENT_OWNED_WHOLE_EPOCH'):
        new.rows._read_row(keys[0],copied)
    # Independent fixture: the first fatal cannot be reset to retry another pre.
    other,otherkeys=raw_rows(tmp_path/'other',count=1)
    expired=other.rows.verify();other.rows.verify()
    with pytest.raises(module.EvidenceAuditIntegrityError):other.rows._read_row(otherkeys[0],expired)


@pytest.mark.parametrize('target',['frame','append','sorted','dictionary','segment_manifest','manifest'])
def test_tamper_even_warm_hit_or_final_joint_never_hides_whole_and_7fp(tmp_path,target):
    new,keys=raw_rows(tmp_path,count=1)
    new.rows[keys[0]];new.rows[keys[0]]
    paths={'frame':new.rows.segments[0],'append':new.rows.append_index_path,
        'sorted':new.rows.sorted_index_path,'dictionary':new.rows._dictionary_paths[0],
        'segment_manifest':new.rows.segment_manifest_path,'manifest':new.rows.directory/'manifest.json'}
    mutate(paths[target])
    with pytest.raises(module.EvidenceAuditIntegrityError):new.joint_dependency_digest_and_audit_manifest()


@pytest.mark.parametrize('kind',['symlink','fifo','directory','hardlink','replaced_inode'])
@pytest.mark.parametrize('target',['frame','index','dictionary'])
def test_nonregular_or_replaced_owned_sources_are_refused_before_blocking_open(tmp_path,kind,target):
    new,keys=raw_rows(tmp_path,count=1)
    path={'frame':new.rows.segments[0],'index':new.rows.sorted_index_path,'dictionary':new.rows._dictionary_paths[0]}[target]
    raw=path.read_bytes();path.unlink()
    if kind=='symlink':
        extra=tmp_path/'external';extra.write_bytes(raw);path.symlink_to(extra)
    elif kind=='fifo':os.mkfifo(path)
    elif kind=='directory':path.mkdir()
    elif kind=='hardlink':
        extra=tmp_path/'external';extra.write_bytes(raw);os.link(extra,path)
    else:path.write_bytes(raw)
    with pytest.raises(module.EvidenceAuditIntegrityError):new.rows[keys[0]]


@pytest.mark.parametrize('part',['key','frame_hash','offset','length','segment','duplicate','missing','trailing','header'])
def test_binary_index_all_fields_and_complete_coverage_are_authenticated(tmp_path,part):
    new,keys=raw_rows(tmp_path,count=3)
    path=new.rows.sorted_index_path;raw=bytearray(path.read_bytes());start=module.INDEX_HEADER_BYTES
    if part=='duplicate':raw.extend(raw[start:start+84])
    elif part=='missing':del raw[-84:]
    elif part=='trailing':raw.extend(b'x')
    elif part=='header':raw[0]^=1
    else:
        at={'key':0,'frame_hash':32,'offset':64,'length':72,'segment':80}[part]
        raw[start+at]^=1
    path.write_bytes(raw)
    with pytest.raises(module.EvidenceAuditIntegrityError):new.audit_manifest()


@pytest.mark.parametrize('position',[(0,-1,1,1,'0'*64),(0,2**64,1,1,'0'*64),
    (2**32,0,1,1,'0'*64),(0,False,1,1,'0'*64),(0,0,2**32,1,'0'*64),
    (0,0,1,2**32,'0'*64)])
def test_fixed84_no_integer_truncation(position):
    with pytest.raises(module.EvidenceAuditIntegrityError):module.DictionaryRowJournalV4._pack('0'*64,position)


def test_sorted_full_keys_order_width_and_append_bytes_both_counted(tmp_path):
    new,keys=raw_rows(tmp_path,count=3)
    row=new.rows
    raw=row.sorted_index_path.read_bytes()
    assert len(raw)==4096+84*3 and row.append_index_path.stat().st_size==84*3
    assert [module.DictionaryRowJournalV4._unpack(raw[o:o+84])[0] for o in range(4096,len(raw),84)]==sorted(keys)
    before=new.read_metrics;row[keys[1]];after=new.read_metrics
    assert after['ordinary_header_returned_bytes']-before['ordinary_header_returned_bytes']==4096
    assert after['ordinary_segment_manifest_returned_bytes']>before['ordinary_segment_manifest_returned_bytes']
    proof=row.verify()
    assert proof['append_index_bytes']==84*3 and proof['sorted_index_bytes']==4096+84*3
    assert sum(after['physical_read_buckets'].values())==after['actual_physical_returned_read_bytes']
    new.close()


@pytest.mark.parametrize('kind',['trailing','multiple','truncated','adler','dict_id','no_dict','bomb','newline','json','wrong_row'])
def test_owned_bad_codec_bytes_never_claim_logical_authentication(tmp_path,monkeypatch,kind):
    bundle,new=make(tmp_path)
    original=new.rows._compress
    def bad(logical,dictionary):
        frame=original(logical,dictionary)
        if kind=='trailing':return frame+b'junk'
        if kind=='multiple':return frame+frame
        if kind=='truncated':return frame[:-1]
        if kind=='adler':return frame[:-1]+bytes([frame[-1]^1])
        if kind=='dict_id':return frame[:2]+bytes([frame[2]^1])+frame[3:]
        if kind=='no_dict':return zlib.compress(logical,6)
        if kind=='bomb':return original(b'x'*(len(logical)+module.CHUNK_BYTES),dictionary)
        if kind=='newline':return original(logical[:-1]+b' ',dictionary)
        if kind=='json':return original(b'!'+logical[1:],dictionary)
        return original(logical.replace(b'2026',b'2025',1),dictionary)
    monkeypatch.setattr(new.rows,'_compress',bad)
    assert new.resolve(bundle.bars[0]).allowed
    physical=new.close();assert physical['complete_allowed_row_audit'] is False
    before=new.rows.logical_read_bytes
    with pytest.raises(module.EvidenceAuditIntegrityError):new.audit_manifest()
    assert new.rows.logical_read_bytes>=before
    assert not new.rows._cache
    if kind=='bomb':assert new.rows.logical_read_bytes-before>physical['logical_canonical_bytes']


@pytest.mark.parametrize('kind',[ValueError,RuntimeError])
@pytest.mark.parametrize('operation',['duplicate','joint','ordinary'])
def test_budget_original_exception_is_sticky_and_bytes_returned_are_charged(tmp_path,kind,operation):
    sentinel=kind('TEST_FIXTURE_LOGICAL_RETURNED_LIMIT');charged=[]
    def callback(raw):charged.append(len(raw));raise sentinel
    bundle,new=make(tmp_path,callback=callback)
    new.resolve(bundle.bars[0])
    with pytest.raises(kind) as first:
        if operation=='duplicate':new.resolve(bundle.bars[0])
        elif operation=='joint':new.audit_manifest()
        else:digest_json_stream(new.effective_dependencies())
    assert first.value is sentinel and sum(charged)==new.rows.logical_read_bytes>0
    assert not new.rows._cache
    for op in (new.close,new.audit_manifest,new.effective_dependencies,lambda:new.resolve(bundle.bars[0])):
        with pytest.raises(kind) as second:op()
        assert second.value is sentinel
    assert new.rows.writer.closed and new.rows.index_writer.closed


@pytest.mark.parametrize('target',['frame','dictionary','index'])
def test_logical_primary_and_metadata_cleanup_are_separate(tmp_path,target):
    sentinel=ValueError('TEST_FIXTURE_PRIMARY');holder={}
    def callback(raw):
        new=holder['new']
        path={'frame':new.rows.segments[0],'dictionary':new.rows._dictionary_paths[0],
              'index':new.rows.append_index_path}[target]
        mutate(path);raise sentinel
    bundle,new=make(tmp_path,callback=callback);holder['new']=new
    new.resolve(bundle.bars[0])
    with pytest.raises(ValueError) as found:new.resolve(bundle.bars[0])
    assert found.value is sentinel and not new.rows._cache
    assert any(x['phase'].endswith('CLEANUP') for x in new.failure_provenance)
    assert any('PRIMARY' in x['phase'] for x in new.failure_provenance)


def test_physical_hit_failure_is_sticky_and_fresh_read_not_skipped(tmp_path,monkeypatch):
    new,keys=raw_rows(tmp_path,count=1);new.rows[keys[0]];new.rows[keys[0]]
    sentinel=RuntimeError('TEST_FIXTURE_FRAME_READ_BUDGET');old=new.rows._read
    def failed(source,size,*,line=False):
        raw=old(source,size,line=line)
        if new.rows._read_phase=='ordinary_frame':raise sentinel
        return raw
    monkeypatch.setattr(new.rows,'_read',failed)
    before=new.rows.read_bytes
    with pytest.raises(RuntimeError) as found:new.rows[keys[0]]
    assert found.value is sentinel and new.rows.read_bytes>before
    with pytest.raises(RuntimeError) as second:new.close()
    assert second.value is sentinel


def test_parent_alias_during_reader_is_detected_without_masking_primary(tmp_path,monkeypatch):
    new,keys=raw_rows(tmp_path,count=1)
    original=new.rows._read;alias=tmp_path/'moved';done=False
    def moved(source,size,*,line=False):
        nonlocal done
        raw=original(source,size,line=line)
        if not done:
            done=True;new.rows.directory.rename(alias);new.rows.directory.symlink_to(alias,target_is_directory=True)
        return raw
    monkeypatch.setattr(new.rows,'_read',moved)
    with pytest.raises(module.EvidenceAuditIntegrityError):new.rows[keys[0]]
    assert new.failure_provenance


def test_source_used_and_receipt_identity_not_authorized_by_valid_cache(tmp_path):
    bundle,new=make(tmp_path);new.resolve(bundle.bars[0]);new.resolve(bundle.bars[0]);new.resolve(bundle.bars[0])
    rid=next(iter(new.used));new.used[rid]['provider']='NOT_THE_ORIGINAL'
    with pytest.raises(module.EvidenceAuditIntegrityError):new.audit_manifest()


def test_required_records_cannot_be_lost_or_false_empty(tmp_path):
    new,keys=raw_rows(tmp_path,count=3)
    new.rows._index.pop(keys[0])
    with pytest.raises(module.EvidenceAuditIntegrityError):new.audit_manifest()


def test_dictionary_first_canonical_tail_and_segment_limits(tmp_path):
    _,new=make(tmp_path,size=50000,cache_bytes=0,entries=0)
    row={'TEST_FIXTURE':True,'payload':'a'*40000};raw=v2._json(row);key=hashlib.sha256(raw).hexdigest();new.rows[key]=row
    assert new.rows._dictionaries[0]==raw[-32768:]
    assert new.rows._dictionary_paths[0].read_bytes()==raw[-32768:]
    new.audit_manifest();new.close()
    _,small=make(tmp_path/'small',size=10)
    with pytest.raises(module.EvidenceAuditIntegrityError):small.rows[key]=row
    assert not small.rows.segments


def test_empty_joint_and_full_source_identity_new_version_not_capability(tmp_path):
    _,new=make(tmp_path)
    proof=new.joint_dependency_digest_and_audit_manifest()
    assert proof['row_count']==0 and proof['complete_allowed_row_audit'] is True
    assert new.rows.index_path.stat().st_size==4096
    identity=module.source_identity()
    assert identity['implementation_version']==module.VERSION
    assert identity['role']=='IMPLEMENTATION_IDENTITY_NOT_SOURCE_CAPABILITY'
    assert identity['union_scope']=='DIRECT_BACKEND_DEPENDENCIES_NOT_COMPLETE_CALLER_RUNTIME_UNION'
    assert identity['source_hash']==digest_json_stream(identity['source_file_hashes'])
    assert set(identity['source_file_hashes'])=={
        'stockfish_core/yaogu/evidence_audit_spool_v4.py','stockfish_core/yaogu/evidence_audit_spool_v3.py',
        'stockfish_core/yaogu/evidence_audit_spool_v2.py','stockfish_core/yaogu/visibility.py',
        'stockfish_core/yaogu/history_artifacts.py','docs/EVIDENCE_AUDIT_SPOOL_V2.md',
        'docs/EVIDENCE_AUDIT_SPOOL_V3.md','docs/EVIDENCE_AUDIT_SPOOL_V4.md'}
    new.close()
