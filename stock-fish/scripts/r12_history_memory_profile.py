"""Reproducible TEST_FIXTURE old/new history memory benchmark, no network.

Run from stock-fish: python scripts/r12_history_memory_profile.py --output <dir>
Each path runs in a fresh process. The fixture is synthetic and never a signal.
"""
from __future__ import annotations
import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from stockfish_core.storage import SQLiteStore
from stockfish_core.yaogu.contracts import DailyBar, MarketInput, digest
from stockfish_core.yaogu.history_artifacts import HistoryArtifactStore, migrate_legacy_input, bind_history, iter_bars


def json_text(value):return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'))


def fixture(root,instruments,sessions):
    days=[];day=date(2025,1,1)
    while len(days)<sessions:
        if day.weekday()<5:days.append(day)
        day+=timedelta(days=1)
    stamp=datetime.combine(days[-1],datetime.min.time(),timezone.utc).isoformat()
    clocks={k:stamp for k in ('event_time','source_published_at','first_seen_at','ingested_at')}
    securities=[dict(instrument_id=f'TEST_FIXTURE_{i:06d}',exchange='SSE',board='MAIN',security_type='A_COMMON',listed_on='2000-01-01',effective_from=str(days[0]),effective_to=None,delisted_on=None,st=False,suspended=False,delisting_period=False,name='TEST_FIXTURE',source_refs=['fixture'],risk_flags=[],**clocks) for i in range(instruments)]
    envelope=dict(observation_date=str(days[-1]),sessions=list(map(str,days)),universe=dict(snapshot_id='TEST_FIXTURE_SYNTHETIC',securities=securities,source_refs=['fixture'],complete=True,expected_count=instruments,**clocks),themes=[],receipts=[dict(receipt_id='fixture',provider='TEST_FIXTURE',dataset='SYNTHETIC_MEMORY_PROFILE',content_hash='0'*64,field_version='TEST_FIXTURE',row_count=instruments*sessions,pagination_complete=True,status='COMPLETE',parameters={},source_url=None,source_type=None,instrument_ids=[],business_sessions=[],definition_version=None,measurement_definition=None,currency=None,amount_unit=None,historical_time_proof=None,**clocks)],coverage_type='TEST_FIXTURE',previous_emotions=[],funds=[])
    path=root/'TEST_FIXTURE_legacy.json'
    with path.open('w',encoding='utf-8',newline='') as stream:
        stream.write('{"bars":[')
        first=True
        for i in range(instruments):
            for index,day in enumerate(days):
                bar=DailyBar(instrument_id=securities[i]['instrument_id'],session=day,open='10',high='11',low='9',close=str(10+index/1000),reference_prev_close='10',volume_shares='1000000',amount_cny='10000000',comparable_open='10',comparable_high='11',comparable_low='9',comparable_close=str(10+index/1000),comparable_basis='TEST_FIXTURE_STABLE_BASIS',source_refs=('fixture',),**clocks)
                if not first:stream.write(',')
                stream.write(json_text(bar.model_dump(mode='json')));first=False
        stream.write('],'+json_text(envelope)[1:])
    raw=path.read_text(encoding='utf-8');identity=hashlib.sha256(raw.encode()).hexdigest();input_id='market_'+identity
    store=SQLiteStore(root/'TEST_FIXTURE_memory.sqlite3')
    # Exact canonical fixture bytes inserted once. This setup is outside both measurements.
    with store.transaction() as con:
        con.execute('INSERT INTO documents(namespace,document_id,payload,content_hash,created_at) VALUES(?,?,?,?,?)',('candidate_inputs',input_id,raw,identity,stamp))
    store.put_document('candidate_input_indexes',input_id,{'observation_date':str(days[-1])})
    (root/'fixture.json').write_text(json.dumps({'input_id':input_id,'instruments':instruments,'sessions':sessions,'observation_date':str(days[-1]),'legacy_input_bytes':path.stat().st_size}),encoding='utf-8')


def worker(root,mode):
    import psutil
    process=psutil.Process();peak=[process.memory_info().rss];stop=threading.Event()
    def sample():
        while not stop.wait(.01):peak[0]=max(peak[0],process.memory_info().rss)
    thread=threading.Thread(target=sample,daemon=True);thread.start()
    started=time.perf_counter();metadata=json.loads((root/'fixture.json').read_text());store=SQLiteStore(root/'TEST_FIXTURE_memory.sqlite3')
    result={'mode':mode,'evidence_type':'TEST_FIXTURE','instruments':metadata['instruments'],'sessions':metadata['sessions'],'legacy_input_bytes':metadata['legacy_input_bytes']}
    if mode=='old':
        with store.transaction(write=False) as con:raw=con.execute("SELECT payload FROM documents WHERE namespace='candidate_inputs' AND document_id=?",(metadata['input_id'],)).fetchone()[0]
        bundle=MarketInput.model_validate_json(raw)
        dumped=bundle.model_dump(mode='json');copy=MarketInput.model_validate(dumped);digest(copy)
        result['input_bytes']=len(raw.encode());result['daily_incremental_bytes']=len(raw.encode())
        result['load_seconds']=time.perf_counter()-started
    else:
        bundle,migration=migrate_legacy_input(store,metadata['input_id'])
        compact=bundle.model_dump(mode='json');input_bytes=len(json_text(compact).encode())
        input_id='market_'+digest(bundle);store.put_document('candidate_inputs',input_id,compact)
        build_seconds=time.perf_counter()-started;load_start=time.perf_counter()
        loaded=MarketInput.model_validate_json(json_text(compact));bind_history(loaded,store)
        rows=0
        # Actual bounded consumer iteration across the full synthetic universe.
        for security in loaded.universe.securities:
            for bar in iter_bars(loaded,instrument_id=security.instrument_id):rows+=1
        load_seconds=time.perf_counter()-load_start
        writer=loaded._history_store.writer(loaded.receipts,base_ref=loaded.history_artifact_ref)
        new_day=loaded.observation_date+timedelta(days=1)
        for bar in loaded.bars:
            if bar.session==loaded.observation_date:writer.append(bar.model_copy(update={'session':new_day}))
        delta=writer.finish()
        with store.transaction(write=False) as con:
            artifact_bytes=con.execute('SELECT sum(length(CAST(payload AS BLOB))) FROM history_evidence_rows').fetchone()[0]
        result.update(input_bytes=input_bytes,load_seconds=load_seconds,initial_migration_seconds=build_seconds,queried_rows=rows,
            daily_incremental_history_row_bytes=writer.added_bytes,daily_incremental_bytes=input_bytes+writer.added_bytes,
            daily_new_rows=writer.added_rows,history_unique_row_bytes=artifact_bytes,manifest_bytes=len(json_text(loaded._history_store.manifest(loaded.history_artifact_ref)).encode()),history_artifact_hash=loaded.history_artifact_hash,
            incremental_manifest_hash=delta['history_artifact_hash'],migration=migration)
    peak[0]=max(peak[0],process.memory_info().rss);stop.set();thread.join()
    info=process.memory_info();result['peak_rss_bytes']=max(peak[0],getattr(info,'peak_wset',0));result['total_seconds']=time.perf_counter()-started
    (root/(mode+'.json')).write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--instruments',type=int,default=512);parser.add_argument('--sessions',type=int,default=315);parser.add_argument('--mode',choices=('setup','old','new'))
    args=parser.parse_args();root=args.output.resolve();root.mkdir(parents=True,exist_ok=True)
    if args.mode=='setup':fixture(root,args.instruments,args.sessions);return
    if args.mode:worker(root,args.mode);return
    for mode in ('setup','old','new'):
        completed=subprocess.run([sys.executable,__file__,'--output',str(root),'--instruments',str(args.instruments),'--sessions',str(args.sessions),'--mode',mode],capture_output=True,text=True)
        if completed.returncode:raise RuntimeError(f'{mode} failed: {completed.stderr}')
        print(completed.stdout,flush=True)
    result={mode:json.loads((root/(mode+'.json')).read_text()) for mode in ('old','new')}
    result['comparison_scope']='Synthetic TEST_FIXTURE; old full deserialize+Pydantic copy+dump/hash; new initial streaming migration peak and bounded all-instrument query. No market validity claim.'
    (root/'MEMORY_PROFILE.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    before,after=result['old'],result['new']
    report=f'''# R12 Memory Profile\n\nTEST_FIXTURE only: {args.instruments} instruments × {args.sessions} sessions. Fresh subprocesses; RSS sampled every 10 ms and OS peak working set retained.\n\n| Measurement | R11 old path | R12 artifact path |\n|---|---:|---:|\n| Daily input bytes | {before['input_bytes']:,} | {after['input_bytes']:,} |\n| Peak RSS bytes | {before['peak_rss_bytes']:,} | {after['peak_rss_bytes']:,} |\n| Load/consumer seconds | {before['load_seconds']:.3f} | {after['load_seconds']:.3f} |\n| Daily incremental bytes | {before['daily_incremental_bytes']:,} | {after['daily_incremental_bytes']:,} |\n\nNew load measurement includes querying every historical row by instrument. Old measurement includes full JSON, Pydantic validation/copy, and full dump/hash. These intentionally describe different operational paths, not a parser microbenchmark. R12 peak includes initial migration ({after['initial_migration_seconds']:.3f} s). One-session append added {after['daily_new_rows']} rows; old session partitions remain shared. SQLite row/index overhead and WAL are additional to logical payload bytes. Original immutable legacy inputs remain retained and are not reclaimed.\n\nSource receipt references, comparable basis and four clocks remain in each immutable history row. No synthetic record is inserted into the real operational database. MARKET_EDGE=NOT_ESTABLISHED.\n'''
    (root/'MEMORY_PROFILE.md').write_text(report,encoding='utf-8')


if __name__=='__main__':main()
