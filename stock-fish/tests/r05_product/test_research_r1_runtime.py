"""Research runtime regressions; all synthetic evidence is TEST_FIXTURE."""
import json
import pytest
from stockfish_core.storage import SQLiteStore
from stockfish_core.yaogu.contracts import MarketInput
from stockfish_core.yaogu.history_artifacts import compact_input
from stockfish_core.yaogu.research_r1_io import load_input, run_local
from stockfish_core.yaogu.research_r1_storage import BudgetHistory
from tests.support.research_r1_fixture import fixture_export


def archived(tmp_path):
    payload=fixture_export()
    db=tmp_path/'TEST_FIXTURE.sqlite3'
    bundle=compact_input(MarketInput.model_validate(payload['input']),SQLiteStore(db))
    path=tmp_path/'input.json'
    path.write_text(json.dumps(dict(payload,input=bundle.model_dump(mode='json'))))
    return path,db


def test_diagnose_verifies_archive_once(tmp_path,monkeypatch):
    path,db=archived(tmp_path)
    calls=[];original=BudgetHistory.verify
    def verify(self,*args,**kwargs):
        calls.append(self)
        return original(self,*args,**kwargs)
    monkeypatch.setattr(BudgetHistory,'verify',verify)
    run_local([path],tmp_path/'out',groups=['R1_FULL','V2_CONTROL'],history_db=db,mode='diagnose')
    assert len(calls)==1


def test_explicit_runtime_budget_is_recorded(tmp_path):
    path,db=archived(tmp_path)
    _,bundle=load_input(path,history_db=db,history_seconds=600)
    try:
        assert bundle._history_store.seconds==600
        assert bundle._history_store.metrics['time_budget_seconds']==600
        assert bundle._history_store.byte_budget==2*1024**3
    finally:bundle._history_store.close()


@pytest.mark.parametrize('seconds',[0,-1,7201,float('nan'),float('inf'),True])
def test_invalid_runtime_budget_rejected_before_io(tmp_path,seconds):
    with pytest.raises(ValueError,match='INVALID_RESEARCH_HISTORY_TIME_BUDGET'):
        load_input(tmp_path/'missing.json',history_seconds=seconds)


def spill_context(tmp_path):
    from stockfish_core.yaogu.research_r1_storage import bounded_context
    path,db=archived(tmp_path)
    payload,bundle=load_input(path,history_db=db)
    ctx=bounded_context(bundle,payload['cutoff'])
    bars=ctx.bars_by_session
    source=getattr(bars,'source',bars)
    source.cache_size=1;source.compressed_budget=0
    return ctx,bundle._history_store


def test_evicted_verified_windows_do_not_reread_archive(tmp_path):
    ctx,owner=spill_context(tmp_path)
    try:
        bars=ctx.bars_by_session;a,b=ctx.eligible_ids[:2];day=ctx.sessions[-2]
        expected=bars.bar(day,a);assert expected is not None
        bars.bar(day,b);reads=owner.metrics['payload_rows_read']
        assert bars.bar(day,a)==expected
        assert owner.metrics['payload_rows_read']==reads
        assert owner.metrics['verified_window_disk_hits']==1
    finally:owner.close()


@pytest.mark.parametrize('same_size',[False,True])
def test_spill_corruption_rejected_and_scratch_cleaned(tmp_path,same_size):
    ctx,owner=spill_context(tmp_path)
    bars=ctx.bars_by_session;a,b=ctx.eligible_ids[:2];day=ctx.sessions[-2]
    try:
        bars.bar(day,a);bars.bar(day,b)
        scratch=bars.root
        path=scratch/bars.entries[a][0];raw=path.read_bytes()
        path.write_bytes(bytes([raw[0]^1])+raw[1:] if same_size else b'corrupt TEST_FIXTURE scratch')
        with pytest.raises(ValueError,match='VERIFIED_WINDOW_CACHE_CORRUPT'):bars.bar(day,a)
    finally:owner.close()
    assert not scratch.exists()


def test_spill_budget_and_closed_snapshot(tmp_path):
    ctx,owner=spill_context(tmp_path)
    bars=ctx.bars_by_session;bars.max_bytes=1
    try:
        with pytest.raises(ValueError,match='VERIFIED_WINDOW_DISK_BUDGET'):bars.bar(ctx.sessions[-2],ctx.eligible_ids[0])
    finally:owner.close()
    with pytest.raises(ValueError,match='HISTORY_SNAPSHOT_CLOSED'):bars.bar(ctx.sessions[-2],ctx.eligible_ids[0])
