"""TEST_FIXTURE independent attacks and real nested local HTTP admission.

No audit hook is installed in the pytest process. The SQLite attack runs in a
fresh child. HTTP tests use the original admission AST plus the actual overlay
and real urllib3/stdlib/socket IO, exclusively against a local fixture server.
"""
import ast
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import runpy
import socket
import subprocess
import sys
import threading

import pytest
import requests

from scripts import active_business_gateway_v1 as gateway
from scripts import active_runtime_v1 as active
from scripts import business_authority_v1 as authority

ROOT=Path(__file__).resolve().parents[2]


@pytest.fixture
def local_http():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass

        def do_GET(self):
            if self.path=="/redirect":
                self.send_response(302)
                self.send_header("Location","/final")
                self.end_headers()
            else:
                body=b"TEST_FIXTURE_LOCAL_ONLY"
                self.send_response(200)
                self.send_header("Content-Length",str(len(body)))
                self.end_headers()
                self.wfile.write(body)
    server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        yield server.server_address
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)


def admission_with_actual_overlay():
    relative="stockfish_core/yaogu/provider_requests_worker.py"
    tree=ast.parse((ROOT/relative).read_bytes(),filename=relative)
    overlay=active.Overlay(relative)
    tree=overlay.visit(tree)
    ast.fix_missing_locations(tree)
    assert overlay.matches==dict.fromkeys(active.HOOKS[relative],1)
    namespace={"__name__":"stockfish_core.yaogu.TEST_FIXTURE_overlay_worker",
               "__package__":"stockfish_core.yaogu","_active":gateway}
    # Overlay.visit contains the production function changes. Deliberately do
    # not use transform(), whose module-level install would persist in pytest.
    exec(compile(tree,relative,"exec"),namespace)
    reservations=[]
    def reserved(kind="HTTP"):
        # Isolated transport-token issuer: the counter is the independent
        # oracle, while nesting/one-shot tokens remain the original algorithm.
        reservations.append(kind)
        gateway._NETWORK.set(True)
    namespace["reserve_attempt"]=reserved
    return namespace["http_attempt_admission"],reservations


@pytest.mark.parametrize("path,expected",[("/final",1),("/redirect",2)])
def test_real_nested_http_keeps_single_admission_and_restores_scope(local_http,monkeypatch,path,expected):
    admission,reservations=admission_with_actual_overlay()
    real_connect=socket.socket.connect
    observed=[]
    def connect(sock,address):
        assert address[0]=="127.0.0.1","TEST_FIXTURE external network forbidden"
        observed.append(gateway._NETWORK.get())
        assert gateway._NETWORK.get() is True,"NESTED_HTTP_LOST_ADMITTED_TRANSPORT"
        return real_connect(sock,address)
    monkeypatch.setattr(socket.socket,"connect",connect)
    original=gateway._NETWORK.set(False)
    try:
        with requests.Session() as session:
            session.trust_env=False
            with admission():
                response=session.get("http://127.0.0.1:"+str(local_http[1])+path,timeout=3)
                assert response.status_code==200 and response.content==b"TEST_FIXTURE_LOCAL_ONLY"
            assert gateway._NETWORK.get() is False
        assert reservations==["HTTP"]*expected
        assert observed and all(observed)
    finally:
        gateway._NETWORK.reset(original)


def test_failed_actual_transport_restores_scope(local_http,monkeypatch):
    admission,reservations=admission_with_actual_overlay()
    def connect(sock,address):
        assert address[0]=="127.0.0.1"
        assert gateway._NETWORK.get() is True,"NESTED_HTTP_LOST_ADMITTED_TRANSPORT"
        raise OSError("TEST_FIXTURE planned connection failure")
    monkeypatch.setattr(socket.socket,"connect",connect)
    original=gateway._NETWORK.set(False)
    try:
        with requests.Session() as session:
            session.trust_env=False
            with admission(),pytest.raises(requests.ConnectionError):
                session.get("http://127.0.0.1:"+str(local_http[1])+"/final",timeout=3)
        assert gateway._NETWORK.get() is False
        assert reservations==["HTTP"]
    finally:
        gateway._NETWORK.reset(original)


def test_sqlite_filename_cannot_fake_read_only_uri(tmp_path):
    code="""import sys,sqlite3,json,os
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from scripts import active_business_gateway_v1 as g
path=Path(sys.argv[2])/'TEST_FIXTURE_unauthorized?mode=ro.sqlite3'
sys.addaudithook(g._audit)
try:
 sqlite3.connect(path)
except ValueError as error:
 assert str(error)=='AUTHORITY_EXPLICIT_BOUND_ENVIRONMENT_REQUIRED'
 print(json.dumps({'blocked':True,'created':path.exists()}),flush=True)
 os._exit(0)
os._exit(19)
"""
    env={k:v for k,v in __import__('os').environ.items() if not k.startswith("STOCKFISH_")}
    result=subprocess.run([sys.executable,"-I","-B","-c",code,str(ROOT),str(tmp_path)],
                          env=env,text=True,capture_output=True,timeout=10)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)==dict(blocked=True,created=False)


@pytest.mark.parametrize("params",[{},dict(date="2026-11-04"),dict(start_date="2026-11-04",end_date="2026-11-04")])
def test_actual_source_window_same_across_premarket_and_capture(params):
    premarket=authority.canonical_business(dict(backend="premarket",observation_date="2026-11-04",target_session="2026-11-05"))
    capture=authority.canonical_business(dict(operation="CAPTURE",provider="TEST_FIXTURE",dataset="RAW",
        window_start="2026-11-04",window_end="2026-11-04",codes=[]))
    assert gateway.source_window(premarket,params)==gateway.source_window(capture,params)


def test_claimed_active_process_cannot_delegate_to_unbound_python_worker(tmp_path_factory):
    # Reuse only exact-source/authority fixture construction, then independently
    # exercise the production subprocess boundary with an actual child attempt.
    helpers=runpy.run_path(str(Path(__file__).with_name("test_active_runtime_v1.py")))
    active_tree=helpers["active"].__wrapped__(tmp_path_factory)
    claim=helpers["new_claim"](active_tree,20)
    unbound=active_tree["root"]/"TEST_FIXTURE_unbound_worker.py"
    unbound.write_text("import sqlite3,sys\nc=sqlite3.connect(sys.argv[1])\nc.execute('CREATE TABLE TEST_FIXTURE_unbound(value INTEGER)')\nc.commit()\n")
    result=helpers["child"](active_tree,"""
from scripts import codex_workspace
from scripts import active_business_gateway_v1 as g
import subprocess
target=Path(sys.argv[2])/'TEST_FIXTURE_unbound.sqlite3'
try:
 g.process_call(subprocess.run,[sys.executable,'-I','-B',str(Path(sys.argv[2])/'TEST_FIXTURE_unbound_worker.py'),str(target)],check=False)
except ValueError as error:
 assert str(error).startswith('ACTIVE_'),str(error)
else:
 raise AssertionError('CLAIMED_PROCESS_DELEGATED_TO_UNGUARDED_EXTERNAL_WORKER')
assert not target.exists()
print('TEST_FIXTURE_UNBOUND_WORKER_BLOCKED')
""",claim=claim)
    assert result.returncode==0,result.stderr
    assert not (active_tree["root"]/"TEST_FIXTURE_unbound.sqlite3").exists()


def test_health_memory_oracle_cannot_attach_or_vacuum_persistent_database(tmp_path):
    code=r'''import sys,sqlite3,json,os
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from scripts import active_business_gateway_v1 as g
from stockfish_core.storage import _MIGRATIONS
sys.addaudithook(g._audit)
try:sqlite3.connect(':memory:')
except ValueError:pass
else:raise AssertionError('ordinary memory connections were globally exempted')
connection=g.memory_schema_oracle()
assert not g._CONTROL.get()
for _,sql in _MIGRATIONS:connection.executescript(sql)
assert connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]>=5
target=Path(sys.argv[2])/'TEST_FIXTURE_must_not_exist.sqlite3'
attempts=[('ATTACH DATABASE ? AS escaped',(str(target),)),
          ('ATTACH DATABASE ? AS escaped',(target.as_uri()+'?mode=rwc',)),
          ('VACUUM INTO ?',(str(target),)),
          ("SELECT load_extension('TEST_FIXTURE_FORBIDDEN')",()),
          ('PRAGMA temp_store=FILE',()),
          ("PRAGMA temp_store_directory='/tmp'",())]
for sql,parameters in attempts:
 try:connection.execute(sql,parameters)
 except sqlite3.DatabaseError:pass
 else:raise AssertionError('oracle escaped through '+sql)
assert not target.exists()
assert connection.execute('PRAGMA temp_store').fetchone()[0]==2
connection.close()
print(json.dumps({'attacks_blocked':len(attempts),'persistent_file_created':False}),flush=True)
os._exit(0)
'''
    env={k:v for k,v in __import__('os').environ.items() if not k.startswith("STOCKFISH_")}
    result=subprocess.run([sys.executable,"-I","-B","-c",code,str(ROOT),str(tmp_path)],
                          env=env,text=True,capture_output=True,timeout=10)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)==dict(attacks_blocked=6,persistent_file_created=False)


def test_same_capability_two_jobs_starts_only_one_real_supervisor_and_worker(tmp_path_factory):
    from concurrent.futures import ThreadPoolExecutor
    helpers=runpy.run_path(str(Path(__file__).with_name("test_active_runtime_v1.py")))
    active_tree=helpers["active"].__wrapped__(tmp_path_factory)
    claim=helpers["new_claim"](active_tree,22)
    jobs=[]
    for index in range(2):
        job=active_tree["state"]/f"TEST_FIXTURE_job_{index}";job.mkdir()
        # Deliberately omit seed_sources: the actual original worker raises
        # before capture_collect, retaining a real journal without network IO.
        config=dict(state_root=str(job),capture=dict(allow_test_fixture=True),limits=dict(
            max_seconds=15,tree_rss_bytes=2*1024**3,self_rss_bytes=2*1024**3,
            output_bytes=16*1024**2,supervisor_read_bytes=128*1024**2))
        (job/"configuration.json").write_text(json.dumps(config))
        jobs.append(job)
    barrier=threading.Barrier(2)
    def launch(job):
        barrier.wait(timeout=5)
        code="JOB="+repr(str(job))+r'''
from scripts import active_business_gateway_v1 as g
import subprocess
try:
 process=g.process_call(subprocess.Popen,[sys.executable,'-B','-m',
     'stockfish_core.yaogu.premarket_workflow_v1','_worker','--job',JOB],
     stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
except ValueError as error:
 assert 'ALREADY_RESERVED' in str(error),str(error)
 print(json.dumps({'supervisor_started':False}))
else:
 out,err=process.communicate(timeout=40)
 print(json.dumps({'supervisor_started':True,'exit_code':process.returncode,'stderr':err}))
'''
        return helpers["child"](active_tree,code,claim=claim)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(launch,jobs))
    assert all(result.returncode==0 for result in results),[result.stderr for result in results]
    observed=[json.loads(result.stdout) for result in results]
    assert sum(item["supervisor_started"] for item in observed)==1,observed
    controls=[job/"authority-supervisor"/"claim.json" for job in jobs]
    assert sum(path.is_file() for path in controls)==1,observed
    journals=[path for job in jobs for path in job.glob("authority-metrics-worker-*")]
    assert len(journals)==1,observed
    from scripts.durable_stage_metrics_v1 import audit_journal
    audit=audit_journal(journals[0])
    assert audit["status"]=="FAILED",audit
    assert audit["identity"]["business_id"]==claim["business_id"]
    with active_tree["owner"]._connect(readonly=True) as db:
        assert db.execute("SELECT COUNT(*) FROM effects WHERE business_id=? AND effect_kind='WORKER_START'",
                          (claim["business_id"],)).fetchone()[0]==1


def test_active_tree_preserves_frozen_control_verifier_environment_reference(tmp_path_factory):
    from scripts import codex_runtime_recovery_v1 as frozen
    helpers=runpy.run_path(str(Path(__file__).with_name("test_active_runtime_v1.py")))
    active_tree=helpers["active"].__wrapped__(tmp_path_factory)
    report=active_tree["root"]/"TEST_FIXTURE_control_environment_acceptance"
    environment=frozen.verify_environment(Path(sys.executable),Path(sys.executable).parent.parent,report,
        active_tree["source"],_fixture_verifier=lambda result,directory:{"TEST_FIXTURE":True},allow_test_fixture=True)
    before=json.loads(Path(environment["path"]).read_text())
    assert Path(before["source_refs"][3]["path"])==Path(frozen.__file__).resolve()
    result=helpers["child"](active_tree,"ENVIRONMENT="+repr(environment)+r'''
from scripts import active_business_gateway_v1 as gateway
from scripts import codex_runtime_recovery_v2 as recovery
with gateway._control():
 recovery._environment_binding(ENVIRONMENT,{'source_root':sys.argv[1],'python':sys.executable},allow_test_fixture=True)
print('TEST_FIXTURE_CONTROL_VERIFIER_REFERENCE_PRESERVED')
''')
    assert result.returncode==0,result.stderr
    assert "TEST_FIXTURE_CONTROL_VERIFIER_REFERENCE_PRESERVED" in result.stdout
    assert json.loads(Path(environment["path"]).read_text())==before
