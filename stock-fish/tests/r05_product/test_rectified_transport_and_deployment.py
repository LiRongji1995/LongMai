"""TEST_FIXTURE transport/process/WAL cases; no live market or credential claim."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time

import pytest

from stockfish_core.yaogu import provider_requests_worker as worker
from stockfish_core.yaogu.provider_budget import charge, counts, initialize, HttpBudgetExhausted
from stockfish_core.yaogu.provider_requests import execute_request, validate_request
from stockfish_core.yaogu.request_queue import RequestQueue, RequestBudget
from stockfish_core.snapshot_backup import backup_state, restore_state
from stockfish_core.storage import SQLiteStore


def test_http_admission_is_durable_and_refuses_before_extra_io(tmp_path):
    path=tmp_path/'budget.sqlite3';key=initialize(path,2)
    calls=[]
    for attempt in range(3):
        try:charge(path,key,'TEST_FIXTURE','tushare','daily')
        except HttpBudgetExhausted:break
        calls.append(attempt)
    assert calls==[0,1]
    assert counts(path,key)=={'HTTP':2}


def test_task_attempts_cannot_be_reported_as_http(tmp_path):
    from tests.r03_pipeline.test_request_queue import result
    with RequestQueue(tmp_path,budget=RequestBudget(max_requests=1,minimum_interval_seconds=0)) as queue:
        queue.enqueue('fixture',{},stage='TEST_FIXTURE',definition_version='TEST_FIXTURE')
        summary=queue.drain(lambda *a,**kw:result())
        assert summary['task_attempts_this_run']==1
        assert summary['http_attempts_this_run']==summary['network_requests_this_run']==0


def test_missing_token_queue_unblocks_when_credential_becomes_available(tmp_path,monkeypatch):
    from tests.r03_pipeline.test_request_queue import result
    def add(queue):return queue.enqueue('tushare_daily',{'trade_date':'2026-09-04'},stage='TEST_FIXTURE',definition_version='TEST_FIXTURE')
    monkeypatch.delenv('TUSHARE_TOKEN',raising=False);monkeypatch.delenv('TUSHARE_PRO_TOKEN',raising=False)
    with RequestQueue(tmp_path,budget=RequestBudget(max_attempts=1,minimum_interval_seconds=0)) as queue:
        rid=add(queue)
        queue.drain(lambda *a,**kw:{'status':'FETCH_FAILED','failure_category':'AUTH','error_code':'AUTH_REQUIRED','rows':[],'receipts':[]})
        queue.drain(lambda *a,**kw:result())
        assert queue.rows([rid])[0]['state']=='BLOCKED'
    monkeypatch.setenv('TUSHARE_TOKEN','TEST_FIXTURE_TOKEN_NOT_REAL')
    with RequestQueue(tmp_path,budget=RequestBudget(max_attempts=1,minimum_interval_seconds=0)) as queue:
        add(queue);queue.drain(lambda *a,**kw:result())
        assert queue.rows([rid])[0]['state']=='COMPLETE'
        assert queue.rows([rid])[0]['attempts']==2


def test_budget_error_retains_page_and_is_not_network(monkeypatch,tmp_path):
    monkeypatch.setenv('TUSHARE_TOKEN','TEST_FIXTURE_TOKEN_NOT_REAL')
    monkeypatch.setattr(worker,'TUSHARE_VERIFIED_REST_ENDPOINT','https://api.tushare.pro/')
    monkeypatch.setattr(worker,'_transport',{})
    from stockfish_core.yaogu.provider_requests import TUSHARE_ENDPOINTS
    monkeypatch.setitem(TUSHARE_ENDPOINTS['tushare_daily'],'page_limit',2)
    calls=[]
    class R:
        status_code=200
        url='https://api.tushare.pro/'
        history=()
        def json(self):return {'code':0,'data':{'fields':['ts_code','trade_date'],'items':[['600000.SH','20260904'],['600001.SH','20260904']]}}
        def raise_for_status(self):pass
    monkeypatch.setattr('requests.post',lambda *a,**kw:(calls.append(kw['json']['params']['offset']) or R()))
    value=worker.tushare_request('tushare_daily',{'trade_date':'2026-09-04'},[],1,tmp_path/'page.json')
    assert value['failure_category']=='BUDGET' and len(value['rows'])==2 and calls==[0]
    assert worker.read_partial(tmp_path/'page.json')['next_offset']==2


def wait_file(path, seconds=15):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if path.exists():return
        time.sleep(.05)
    raise AssertionError('TEST_PROCESS_DID_NOT_REACH_CHECKPOINT')


def test_real_parent_kill_then_worker_kill_resumes_successful_page(tmp_path):
    child=tmp_path/'worker.py';parent=tmp_path/'parent.py'
    pidfile=tmp_path/'worker.pid';marker=tmp_path/'second-page';partial=tmp_path/'checkpoint.json';trace=tmp_path/'offsets.jsonl'
    child.write_text('''import json,os,sys,time
from pathlib import Path
import requests
from stockfish_core.yaogu import provider_requests_worker as w
from stockfish_core.yaogu.provider_requests import TUSHARE_ENDPOINTS
TUSHARE_ENDPOINTS['tushare_daily']['page_limit']=2
w.TUSHARE_VERIFIED_REST_ENDPOINT='https://api.tushare.pro/' # TEST_FIXTURE mocked transport only
class R:
 status_code=200
 url='https://api.tushare.pro/'
 history=()
 def __init__(self,rows):self.rows=rows
 def json(self):return {'code':0,'data':{'fields':['ts_code','trade_date'],'items':self.rows}}
 def raise_for_status(self):pass
def post(*a,**kw):
 offset=kw['json']['params']['offset']
 with Path(os.environ['TEST_TRACE']).open('a') as f:f.write(str(offset)+'\\n')
 if offset==0:return R([['600000.SH','20260904'],['600001.SH','20260904']])
 if os.environ.get('TEST_RESUME')!='1':
  Path(os.environ['TEST_MARKER']).write_text('waiting after successful page')
  time.sleep(45)
 return R([['600002.SH','20260904']])
requests.post=post
w.main()
''',encoding='utf-8')
    parent.write_text('''import subprocess,sys
from pathlib import Path
p=subprocess.Popen([sys.executable,sys.argv[1]],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
Path(sys.argv[2]).write_text(str(p.pid))
out=p.communicate(sys.stdin.read())[0]
print(out)
''',encoding='utf-8')
    request={'provider':'tushare','endpoint':'tushare_daily','parameters':{'trade_date':'2026-09-04'},'max_http_requests':5,'partial_path':str(partial)}
    env={**os.environ,'TUSHARE_TOKEN':'TEST_FIXTURE_TOKEN_NOT_REAL','TEST_TRACE':str(trace),'TEST_MARKER':str(marker),
         'PYTHONPATH':str(Path(__file__).resolve().parents[2])}
    process=subprocess.Popen([sys.executable,str(parent),str(child),str(pidfile)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
    child_pid=None
    try:
        process.stdin.write(json.dumps(request));process.stdin.close()
        wait_file(marker);child_pid=int(pidfile.read_text())
        assert len(worker.read_partial(partial)['rows'])==2
        process.kill();process.wait(timeout=5)
        os.kill(child_pid,signal.SIGTERM);child_pid=None
        result=subprocess.run([sys.executable,str(child)],input=json.dumps(request),capture_output=True,text=True,env={**env,'TEST_RESUME':'1'},timeout=10,check=True)
        value=json.loads(result.stdout)
        assert value['status']=='COMPLETE' and len(value['rows'])==3
        assert value['coverage']['recovered_pages']==1
        assert trace.read_text().splitlines()==['0','2','2']
    finally:
        if process.poll() is None:process.kill();process.wait(timeout=5)
        if child_pid:
            try:os.kill(child_pid,signal.SIGTERM)
            except OSError:pass


def test_online_backup_includes_nested_queue_wal_and_isolated_restore(tmp_path):
    store=SQLiteStore(tmp_path/'app.sqlite3');root=tmp_path/'core';root.mkdir()
    with sqlite3.connect(root/'request-queue.sqlite3') as queue:
        queue.execute('PRAGMA journal_mode=WAL');queue.execute('PRAGMA wal_autocheckpoint=0')
        queue.execute('CREATE TABLE evidence(value TEXT)');queue.execute("INSERT INTO evidence VALUES('committed-in-wal')");queue.commit()
        backup_state(store.path,tmp_path/'backup',artifact_roots=[root])
        restore_state(tmp_path/'backup',tmp_path/'restore')
    with sqlite3.connect(tmp_path/'restore/artifacts/0/request-queue.sqlite3') as restored:
        assert restored.execute('SELECT value FROM evidence').fetchone()==('committed-in-wal',)


def test_bs_cursor_failure_after_last_page_is_not_complete():
    class Cursor:
        error_code='0';fields=['value']
        def next(self):self.error_code='10001001';return False
    with pytest.raises(ValueError,match='BAOSTOCK_QUERY_10001001'):worker.bs_rows(Cursor())


def test_registered_tushare_plan_uses_current_official_contract():
    from stockfish_core.yaogu.data_pipeline import tushare_request_plan
    from tests.r03_sources.test_provider_boundaries import DAY,SESSIONS
    for ep,params in tushare_request_plan(DAY,SESSIONS):validate_request(ep,params)
    assert validate_request('tushare_dc_member',{'ts_code':'BK1184.DC','trade_date':str(DAY)})


def test_tushare_price_factor_limit_receipts_are_consumed_by_pv():
    from tests.yaogu.fixtures import make_bundle
    from tests.r03_sources.test_provider_boundaries import result
    from stockfish_core.yaogu.provider_normalization import normalize_capture_with_report
    from stockfish_core.yaogu.strategy_experiments import run_pv_observation
    b,_=make_bundle();day=b.observation_date;now=datetime.now(timezone.utc)
    sessions=b.sessions
    rows=[{'ts_code':'600000.SH','trade_date':str(d),'open':'10','high':'11','low':'10',
           'close':'11' if d==day else '10','pre_close':'10','vol':'10000','amount':'40000' if d==day else '10000'} for d in sessions]
    source=[result('mainboard_sse',[{'证券代码':'600000','证券简称':'测试股','上市日期':'2000-01-01'}],stamp=now),
            result('master_szse',[],stamp=now),result('delisting_sse',[],stamp=now),
            result('tushare_daily',rows,{'start_date':str(sessions[0]),'end_date':str(day)},stamp=now),
            result('tushare_adj_factor',[{'ts_code':'600000.SH','trade_date':str(d),'adj_factor':'2'} for d in sessions],stamp=now),
            result('tushare_stk_limit',[{'ts_code':'600000.SH','trade_date':str(day),'up_limit':'11','down_limit':'9'}],stamp=now),
            result('tushare_stock_st',[],{'trade_date':str(day)},stamp=now)]
    bundle,report=normalize_capture_with_report(source,day,now+timedelta(seconds=1),sessions)
    bar=next(x for x in bundle.bars if x.session==day)
    assert bar.volume_shares==1000000 and bar.amount_cny==40000000 and bar.comparable_close==22
    assert bar.limit_verified and bar.limit_up==11 and len(bar.source_refs)>=3
    pv=run_pv_observation(bundle,cutoff=now+timedelta(seconds=2))
    assert [x['instrument_id'] for x in pv['items']]==['600000.SH']


def test_compose_explicit_secret_mapping_and_shell_needs_no_host_python():
    root=Path(__file__).resolve().parents[2]
    compose=(root/'docker-compose.yml').read_text(encoding='utf-8')
    shell=(root/'start-stockrecommendation-docker.sh').read_text(encoding='utf-8')
    assert 'TUSHARE_TOKEN: "${TUSHARE_TOKEN:-}"' in compose
    assert 'TUSHARE_PRO_TOKEN: "${TUSHARE_PRO_TOKEN:-}"' in compose
    assert 'if python' not in shell and 'python -c' not in shell
    assert shell.index('backup --database')<shell.index('compose build')


@pytest.mark.skipif(os.name!='nt',reason='Windows native-command regression')
def test_powershell_build_failure_cannot_pass_using_old_healthy_container(tmp_path):
    root=Path(__file__).resolve().parents[2]
    script=tmp_path/'start.ps1';script.write_text((root/'Start-StockRecommendation-Docker.ps1').read_text(encoding='utf-8'),encoding='utf-8')
    (tmp_path/'.env.docker.local').write_text('STOCKFISH_API_KEY=TEST_FIXTURE_SECRET_NOT_REAL\n',encoding='utf-8')
    wrapper=tmp_path/'wrapper.ps1'
    wrapper.write_text('''$ErrorActionPreference='Stop'
function global:docker {
 param([Parameter(ValueFromRemainingArguments=$true)][string[]]$A)
 $global:LASTEXITCODE=0
 if ($A -contains 'build') {$global:LASTEXITCODE=7;return 'TEST_BUILD_FAILURE'}
 if ($A -contains 'running') {return 'stockfish'}
 if ($A -contains 'inspect') {return 'healthy'}
 return 'TEST_FIXTURE'
}
& (Join-Path $PSScriptRoot 'start.ps1')
''',encoding='utf-8')
    value=subprocess.run(['pwsh','-NoProfile','-File',str(wrapper)],capture_output=True,text=True,encoding='utf-8',timeout=20)
    assert value.returncode!=0 and 'exit 7' in value.stderr
    assert 'as healthy' not in value.stdout and 'TEST_FIXTURE_SECRET_NOT_REAL' not in value.stdout+value.stderr
