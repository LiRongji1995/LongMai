"""R12 immutable, bounded history architecture; isolated TEST_FIXTURE only."""
from datetime import timedelta
import sqlite3
import pytest

from stockfish_core.storage import SQLiteStore, IntegrityError
from stockfish_core.yaogu.contracts import MarketInput, digest
from stockfish_core.yaogu.history_artifacts import (
    HistoryArtifactStore, compact_input, bind_history, iter_bars,
    iter_history_securities, history_resolver,
)
from stockfish_core.yaogu.strategy_experiments import _prepare_context, _bar
from tests.r05_product.test_theme_capital_trend_v1 import trend_bundle


def test_immutable_manifest_reuse_append_basis_source_and_four_clocks(tmp_path):
    bundle,cutoff=trend_bundle()
    store=SQLiteStore(tmp_path/'TEST_FIXTURE.sqlite3')
    owner=HistoryArtifactStore(store)
    compact=compact_input(bundle,owner)
    repeated=compact_input(bundle,owner)
    assert compact.history_artifact_ref==repeated.history_artifact_ref
    assert len(compact.bars)==6*len({b.instrument_id for b in bundle.bars})
    original=list(iter_bars(bundle,instrument_id='TEST000'))
    restored=list(iter_bars(compact,instrument_id='TEST000'))
    assert {digest(x) for x in original}=={digest(x) for x in restored}
    assert restored[0].comparable_basis==original[0].comparable_basis
    assert restored[0].source_refs==original[0].source_refs
    assert restored[0].first_seen_at==original[0].first_seen_at
    assert restored[0].source_published_at==original[0].source_published_at
    assert restored[0].event_time==original[0].event_time
    assert restored[0].ingested_at==original[0].ingested_at
    base=owner.manifest(compact.history_artifact_ref)
    new_day=bundle.observation_date+timedelta(days=1)
    delta=original[-1].model_copy(update={'session':new_day})
    writer=owner.writer(bundle.receipts,base_ref=compact.history_artifact_ref)
    writer.append(delta);ref=writer.finish()
    after=owner.manifest(ref['history_artifact_ref'])
    assert after['partitions'][:-1]==base['partitions']
    assert writer.added_rows==1
    assert owner.manifest(compact.history_artifact_ref)==base
    with store.transaction() as con:
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("UPDATE history_evidence_rows SET payload='{}'")


def test_corrupt_rows_and_partition_membership_rejected(tmp_path):
    bundle,_=trend_bundle()
    store=SQLiteStore(tmp_path/'TEST_FIXTURE.sqlite3');compact=compact_input(bundle,store)
    with store.transaction() as con:
        con.execute('DROP TRIGGER history_evidence_rows_no_update')
        con.execute("UPDATE history_evidence_rows SET payload='{}' WHERE row_hash=(SELECT row_hash FROM history_evidence_rows WHERE kind='bar' LIMIT 1)")
    with pytest.raises(IntegrityError,match='CORRUPT_HISTORY_ROW'):
        list(iter_bars(compact))
    with store.transaction() as con:
        con.execute('DROP TRIGGER history_partition_members_no_delete')
        con.execute('DELETE FROM history_partition_members WHERE rowid=(SELECT rowid FROM history_partition_members LIMIT 1)')
    with pytest.raises(IntegrityError,match='CORRUPT_HISTORY_PARTITION'):
        bind_history(compact,store)


def test_lazy_query_matches_old_values_without_monolithic_copy(tmp_path,monkeypatch):
    bundle,cutoff=trend_bundle()
    legacy=_prepare_context(bundle,cutoff)
    original_dump=MarketInput.model_dump
    def guarded(self,*args,**kwargs):
        if len(self.bars)>6*len({b.instrument_id for b in bundle.bars}):raise MemoryError('TEST_FIXTURE monolithic dump forbidden')
        return original_dump(self,*args,**kwargs)
    monkeypatch.setattr(MarketInput,'model_dump',guarded)
    compact=compact_input(bundle,SQLiteStore(tmp_path/'TEST_FIXTURE.sqlite3'))
    digest(compact)
    fresh=MarketInput.model_validate_json(compact.model_dump_json())
    with pytest.raises(IntegrityError,match='STORE_NOT_BOUND'):
        list(iter_bars(fresh,instrument_id='TEST000'))
    bind_history(fresh,compact._history_store)
    context=_prepare_context(fresh,cutoff)
    for iid in context.eligible_ids:
        for session in context.sessions:
            assert _bar(context,session,iid)==_bar(legacy,session,iid)
    assert len(context.bars_by_session.cache)<=8
    assert len(context.resolver.rows)<100
    assert context.resolver.effective_dependencies()['immutable_history']['history_artifact_hash']==fresh.history_artifact_hash
    assert digest(compact)!=digest(compact.model_copy(update={'history_artifact_ref':'history_'+'0'*64,'history_artifact_hash':'0'*64}))


def test_recent_delta_overlay_not_lost(tmp_path):
    bundle,_=trend_bundle()
    store=SQLiteStore(tmp_path/'TEST_FIXTURE.sqlite3');compact=compact_input(bundle,store)
    row=compact.bars[-1].model_copy(update={'session':bundle.observation_date+timedelta(days=1)})
    overlaid=compact.model_copy(update={'bars':(*compact.bars,row)})
    assert digest(row) in {digest(x) for x in iter_bars(overlaid,instrument_id=row.instrument_id)}
    registered=compact_input(overlaid,store)
    assert registered.history_artifact_hash!=compact.history_artifact_hash


def test_legacy_sqlite_blob_migration_preserves_original_without_full_parse(tmp_path,monkeypatch):
    from stockfish_core.yaogu.history_artifacts import migrate_legacy_input
    bundle,_=trend_bundle();store=SQLiteStore(tmp_path/'TEST_FIXTURE.sqlite3')
    original=bundle.model_dump(mode='json',exclude={'history_artifact_ref','history_artifact_hash'})
    input_id='market_'+digest(original)
    store.put_document('candidate_inputs',input_id,original)
    store.put_document('candidate_input_indexes',input_id,{'observation_date':str(bundle.observation_date)})
    def forbidden(*args,**kwargs):raise MemoryError('TEST_FIXTURE full JSON parse forbidden')
    monkeypatch.setattr(MarketInput,'model_validate_json',forbidden)
    compact,report=migrate_legacy_input(store,input_id)
    assert report['source_input_hash_verified'] is True
    assert len(list(iter_bars(compact)))==len(bundle.bars)
    assert store.get_document('candidate_inputs',input_id)==original


def test_archived_file_stream_keeps_original_hash_and_source_clocks(tmp_path):
    from stockfish_core.yaogu.history_artifacts import migrate_legacy_file
    import hashlib
    bundle,_=trend_bundle();path=tmp_path/'TEST_FIXTURE_capture.json'
    path.write_text(bundle.model_dump_json(),encoding='utf-8');before=hashlib.sha256(path.read_bytes()).hexdigest()
    compact,report=migrate_legacy_file(SQLiteStore(tmp_path/'TEST_FIXTURE.sqlite3'),path,observation_date=bundle.observation_date)
    assert report['source_file_sha256']==before==hashlib.sha256(path.read_bytes()).hexdigest()
    assert compact.coverage_type=='TEST_FIXTURE'
    assert {digest(b) for b in iter_bars(compact)}=={digest(b) for b in bundle.bars}


def test_old_signal_outcomes_read_beyond_recent_six_sessions(tmp_path):
    from stockfish_core.yaogu.strategy_tracking import evaluate_strategy_experiment_outcomes
    from stockfish_core.yaogu.outcomes import _bar_map
    bundle,cutoff=trend_bundle()
    observed=bundle.sessions[-25]
    source=next(b for b in bundle.bars if b.instrument_id=='TEST000' and b.session==observed)
    run={'run_id':'TEST_FIXTURE_R12_OLD_SIGNAL','observation_date':str(observed),'coverage_type':'TEST_FIXTURE',
         'results':{'PV_OBSERVATION_V1':{'strategy_id':'PV_OBSERVATION_V1','items':[{'instrument_id':'TEST000','rank':1,'comparable_basis':source.comparable_basis,'comparable_close':str(source.comparable_close)}]}}}
    old=evaluate_strategy_experiment_outcomes(run,bundle,cutoff,horizons=(1,20))
    compact=compact_input(bundle,SQLiteStore(tmp_path/'TEST_FIXTURE.sqlite3'))
    assert all(b.session>observed for b in compact.bars)
    new=evaluate_strategy_experiment_outcomes(run,compact,cutoff,horizons=(1,20))
    assert old and new
    for a,b in zip(old,new):
        assert a['metrics']==b['metrics']
        assert a['price_label_status']==b['price_label_status']
        assert a['metrics']['close_reference_change'] is not None
    assert _bar_map(compact,cutoff).get(('TEST000',observed))==source


def test_frozen_daily_and_v2_business_results_equal_with_artifact(tmp_path):
    from stockfish_core.yaogu.engine import run
    from stockfish_core.yaogu.stage_aware_momentum import run_stage_aware_momentum
    from tests.yaogu.fixtures import make_bundle
    bundle,cutoff=make_bundle();compact=compact_input(bundle,SQLiteStore(tmp_path/'TEST_FIXTURE.sqlite3'))
    old=run(bundle,cutoff=cutoff);new=run(compact,cutoff=cutoff)
    assert old.status==new.status
    assert old.items==new.items
    trend,cutoff=trend_bundle();compact_trend=compact_input(trend,compact._history_store)
    before=run_stage_aware_momentum(trend,cutoff=cutoff)
    after=run_stage_aware_momentum(compact_trend,cutoff=cutoff)
    assert before['items']==after['items']


def test_provider_normalization_streamed_history_preserves_rows(tmp_path):
    from tests.r03_sources.test_provider_boundaries import base,raw,result,SESSIONS,DAY,NOW
    from stockfish_core.yaogu.provider_normalization import normalize_capture_with_report
    source=base()+[result('history',[raw(session=s) for s in SESSIONS],{'start_date':str(SESSIONS[0]),'end_date':str(DAY),'codes':['sh.600000']})]
    old,_=normalize_capture_with_report(source,DAY,NOW,SESSIONS)
    owner=HistoryArtifactStore(SQLiteStore(tmp_path/'TEST_FIXTURE.sqlite3'))
    compact,report=normalize_capture_with_report(source,DAY,NOW,SESSIONS,history_store=owner)
    assert compact.history_artifact_hash
    assert len(compact.bars)<len(old.bars)
    assert {digest(b) for b in iter_bars(compact)}=={digest(b) for b in old.bars}
    assert report['history_artifact']['total_bar_rows']==len(old.bars)


def test_missing_row_is_corruption_in_verify_and_iteration(tmp_path):
    bundle,_=trend_bundle();store=SQLiteStore(tmp_path/'TEST_FIXTURE.sqlite3');compact=compact_input(bundle,store)
    with store.transaction() as con:
        con.execute('DROP TRIGGER history_evidence_rows_no_delete')
        con.execute("DELETE FROM history_evidence_rows WHERE row_hash=(SELECT row_hash FROM history_evidence_rows WHERE kind='bar' LIMIT 1)")
    with pytest.raises(IntegrityError,match='MISSING_HISTORY_ROW'):
        compact._history_store.verify(compact.history_artifact_ref)
    with pytest.raises(IntegrityError,match='MISSING_HISTORY_ROW'):
        list(iter_bars(compact))


def test_cached_manifest_cannot_be_mutated_to_drop_partition(tmp_path):
    bundle,_=trend_bundle();compact=compact_input(bundle,SQLiteStore(tmp_path/'TEST_FIXTURE.sqlite3'))
    manifest=compact._history_store.manifest(compact.history_artifact_ref)
    manifest['partitions'].pop()
    with pytest.raises(IntegrityError,match='HISTORY_ARTIFACT_HASH_MISMATCH'):
        list(iter_bars(compact,instrument_id='TEST000'))


def test_incremental_daily_receipts_resolve_unchanged_historical_parents(tmp_path):
    bundle,cutoff=trend_bundle();compact=compact_input(bundle,SQLiteStore(tmp_path/'TEST_FIXTURE.sqlite3'))
    today=cutoff+timedelta(days=1)
    current=bundle.receipts[0].model_copy(update={'receipt_id':'TEST_FIXTURE_NEW_SESSION','event_time':today,'source_published_at':today,'first_seen_at':today,'ingested_at':today})
    delta=compact.model_copy(update={'receipts':(current,)})
    resolver=history_resolver(delta,cutoff=today)
    oldbar=next(iter_bars(delta,instrument_id='TEST000'))
    assert resolver.is_visible(oldbar,instrument_id=oldbar.instrument_id,session=oldbar.session,require_session=True)
    assert set(oldbar.source_refs)<=set(resolver.receipts)
    changed=bundle.receipts[0].model_copy(update={'first_seen_at':today,'ingested_at':today})
    collision=compact.model_copy(update={'receipts':(changed,)})
    with pytest.raises(IntegrityError,match='HISTORY_RECEIPT_IDENTITY_CONFLICT'):
        history_resolver(collision,cutoff=today)


def test_selected_history_hash_changes_only_when_visible_records_change(tmp_path):
    bundle,cutoff=trend_bundle();compact=compact_input(bundle,SQLiteStore(tmp_path/'TEST_FIXTURE.sqlite3'))
    rows=sorted(iter_bars(compact,instrument_id='TEST000'),key=lambda b:b.session)[-2:]
    hashes=[]
    for at in (rows[0].ingested_at,cutoff,cutoff+timedelta(minutes=1)):
        resolver=history_resolver(compact,cutoff=at)
        for row in rows:resolver.resolve(row,instrument_id=row.instrument_id,session=row.session,require_session=True)
        hashes.append(resolver.effective_dependencies()['immutable_history']['selected_records_hash'])
    assert hashes[0]!=hashes[1]
    assert hashes[1]==hashes[2]


def test_cross_instrument_windows_reuse_bounded_validated_blocks(tmp_path,monkeypatch):
    from tests.yaogu.fixtures import make_bundle
    from stockfish_core.yaogu.theme_capital_trend import _window_return
    bundle,cutoff=make_bundle(31);compact=compact_input(bundle,SQLiteStore(tmp_path/'TEST_FIXTURE.sqlite3'))
    context=_prepare_context(compact,cutoff);reads=[]
    original=compact._history_store.iter_rows
    def counted(*args,**kwargs):
        reads.append(kwargs.get('instrument_id'))
        yield from original(*args,**kwargs)
    monkeypatch.setattr(compact._history_store,'iter_rows',counted)
    for _ in range(3):
        for iid in context.eligible_ids:
            _bar(context,context.sessions[-1],iid)
            _bar(context,context.sessions[-20],iid)
            _window_return(context,iid,len(context.sessions)-1,5)
    assert len(reads)==31
    assert len(context.bars_by_session.cache)<=8
    assert context.bars_by_session.compressed_bytes<=8*1024*1024
    assert len(context.scalar_cache)==31


def test_streaming_input_hash_preserves_identity_without_full_model_dump(tmp_path, monkeypatch):
    from stockfish_core.yaogu.contracts import UniverseSnapshot
    from stockfish_core.yaogu.history_artifacts import digest_market_input
    original, _ = trend_bundle()
    compact = compact_input(original, SQLiteStore(tmp_path/'TEST_FIXTURE.sqlite3'))
    expected = [digest(original), digest(compact)]
    input_dump, universe_dump = MarketInput.model_dump, UniverseSnapshot.model_dump

    def bounded_input(self, *args, **kwargs):
        assert {'bars', 'universe'} <= set(kwargs.get('exclude', ()))
        return input_dump(self, *args, **kwargs)

    def bounded_universe(self, *args, **kwargs):
        assert 'securities' in kwargs.get('exclude', ())
        return universe_dump(self, *args, **kwargs)

    monkeypatch.setattr(MarketInput, 'model_dump', bounded_input)
    monkeypatch.setattr(UniverseSnapshot, 'model_dump', bounded_universe)
    assert [digest_market_input(original), digest_market_input(compact)] == expected


def test_streamed_effective_dependency_hash_preserves_complete_evidence(tmp_path):
    from stockfish_core.yaogu.history_artifacts import digest_json_stream
    bundle, cutoff = trend_bundle()
    compact = compact_input(bundle, SQLiteStore(tmp_path/'TEST_FIXTURE.sqlite3'))
    context = _prepare_context(compact, cutoff)
    for iid in context.eligible_ids:
        _bar(context, context.observation_date, iid)
    dependencies = context.resolver.effective_dependencies()
    assert digest_json_stream(dependencies) == digest(dependencies)
    assert digest_json_stream({'unicode': '\u4e2d\u6587', 'values': [None, False, -0.0, 1e-100, {'b': 2, 'a': 1}]}) == digest(
        {'unicode': '\u4e2d\u6587', 'values': [None, False, -0.0, 1e-100, {'b': 2, 'a': 1}]})
    changed = {**dependencies, 'immutable_history': {**dependencies['immutable_history'], 'selected_records_hash': '0'*64}}
    assert digest_json_stream(changed) != digest_json_stream(dependencies)
