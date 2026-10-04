"""Local task supervisor. No downloads, cleanup, deployment or budget resets.

Initialize once. All subprocesses serialize on the durable ledger lock. Explicit
reservations and attempts survive failures; an interrupted RUNNING reservation
blocks further runs until its actual consumption is reconciled, never refunded.
"""
import argparse
import datetime
import fcntl
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import time

GIB = 1024**3
LIMITS = {'engineering': (5400,1800,None), 'readonly': (1800,1800,None),
          'research': (3600,1800,2), 'online': (600,600,1), 'dependency':(600,600,1)}

def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def size(path):
    return int(subprocess.check_output(['du','-sk',str(path)],text=True).split()[0])*1024
def save(path,data):
    tmp=path.with_suffix('.pending')
    with tmp.open('w') as f:
        json.dump(data,f,indent=2,ensure_ascii=False);f.write('\n');f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)
def tree(pid):
    rows=[]
    for line in subprocess.check_output(['ps','-axo','pid,ppid,rss'],text=True).splitlines()[1:]:
        try: rows.append(tuple(map(int,line.split())))
        except ValueError: pass
    owned={pid}
    while True:
        grown=owned|{p for p,parent,rss in rows if parent in owned}
        if grown==owned:break
        owned=grown
    return sum(rss*1024 for p,parent,rss in rows if p in owned)

def main():
    invoked=time.monotonic()
    p=argparse.ArgumentParser();p.add_argument('--ledger',required=True,type=pathlib.Path)
    p.add_argument('--init',action='store_true');p.add_argument('--root',type=pathlib.Path)
    p.add_argument('--kind',choices=LIMITS);p.add_argument('--label');p.add_argument('--seconds',type=float)
    p.add_argument('--reserve-bytes',type=int,default=128*1024**2);p.add_argument('--cwd',type=pathlib.Path)
    p.add_argument('command',nargs=argparse.REMAINDER);a=p.parse_args()
    ledger=a.ledger.resolve();ledger.parent.mkdir(parents=True,exist_ok=True)
    with ledger.with_suffix('.lock').open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if a.init:
            if ledger.exists():raise SystemExit('LEDGER_EXISTS_NO_RESET')
            root=a.root.resolve();project=size(root)
            data={'task_id':'STOCKFISH_FOUR_STAGE_20261004','started_at':now(),
                  'root':str(root),'initial_project_bytes':project,'project_limit_bytes':50*GIB,
                  'new_bytes_limit':2*GIB,'required_free_bytes':10*GIB,'rss_limit_bytes':2*GIB,
                  'online_http_limit':100,'online_http_used':0,'limits':LIMITS,
                  'attempts':[], 'admission':'BLOCKED_PROJECT_CAPACITY' if project>50*GIB else 'ADMITTED',
                  'git_commit_push_authorized':False,'production_activation':'NOT_AUTHORIZED'}
            save(ledger,data);print(json.dumps(data));return
        data=json.loads(ledger.read_text());root=pathlib.Path(data['root'])
        def deny(reason):
            data.setdefault('admission_attempts',[]).append({'at':now(),'kind':a.kind,'label':a.label,'requested_seconds':a.seconds,'requested_bytes':a.reserve_bytes,'decision':'DENIED','reason':reason,'process_started':False,'elapsed_seconds':time.monotonic()-invoked})
            save(ledger,data);raise SystemExit(reason)
        if any(x['status']=='RUNNING' for x in data['attempts']):raise SystemExit('UNRECONCILED_RUNNING_ATTEMPT')
        if not a.kind or not a.label or not a.seconds or not a.command:deny('EXPLICIT_SCOPE_REQUIRED')
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',a.label) or any(x['label']==a.label for x in data['attempts']):deny('NEW_SAFE_LABEL_REQUIRED')
        if a.reserve_bytes<0 or a.reserve_bytes>data['new_bytes_limit']:deny('RESERVATION_BYTES_INVALID')
        command=a.command[1:] if a.command[0]=='--' else a.command
        total,per,count=LIMITS[a.kind]
        selected=[x for x in data['attempts'] if x['kind']==a.kind]
        used=sum(x.get('elapsed_seconds',x['reserved_seconds']) for x in selected)
        if a.kind=='engineering':used+=sum(x.get('elapsed_seconds',x['reserved_seconds']) for x in data['attempts'] if x['kind']=='dependency')
        if a.seconds>per or used+a.seconds>total or (count is not None and len(selected)>=count):deny('BUDGET_ADMISSION_DENIED')
        if a.kind=='dependency' and sum(x.get('elapsed_seconds',x['reserved_seconds']) for x in data['attempts'] if x['kind'] in ('engineering','dependency'))+a.seconds>5400:deny('ENGINEERING_SHARED_DEPENDENCY_BUDGET_DENIED')
        project=size(root);new=max(0,project-data['initial_project_bytes']);free=shutil.disk_usage(root).free
        if project+a.reserve_bytes>data['project_limit_bytes'] or new+a.reserve_bytes>data['new_bytes_limit'] or free-a.reserve_bytes<data['required_free_bytes']:
            deny('STORAGE_ADMISSION_DENIED')
        env=os.environ.copy();env.update(GOPROXY='off',GOSUMDB='off',GOTOOLCHAIN='local',PYTHONDONTWRITEBYTECODE='1')
        scratch=ledger.parent/'scratch';scratch.mkdir(exist_ok=True)
        env['TMPDIR']=str(scratch);env['GOCACHE']=str(ledger.parent/'go-cache')
        record={'label':a.label,'kind':a.kind,'command':command,'cwd':str(a.cwd or root),
                'started_at':now(),'reserved_seconds':a.seconds,'reserved_bytes':a.reserve_bytes,
                'project_admission_bytes':project,'free_admission_bytes':free,'status':'RUNNING'}
        data['attempts'].append(record);save(ledger,data)
        started=invoked;wall=time.time();peak=0;reason=None;process=None
        try:
            with (ledger.parent/(a.label+'.stdout')).open('wb') as out,(ledger.parent/(a.label+'.stderr')).open('wb') as err:
                process=subprocess.Popen(command,cwd=a.cwd or root,env=env,stdout=out,stderr=err,start_new_session=True)
                last_storage=0
                while process.poll() is None:
                    elapsed=time.monotonic()-started;peak=max(peak,tree(process.pid))
                    if elapsed>=a.seconds-2:reason='TIME_LIMIT_WITH_CLEANUP_RESERVE'
                    if peak>data['rss_limit_bytes']:reason='PROCESS_TREE_RSS_LIMIT'
                    if elapsed-last_storage>10:
                        current=size(root);record['project_peak_bytes']=max(current,record.get('project_peak_bytes',0))
                        if current>data['project_limit_bytes'] or current-data['initial_project_bytes']>data['new_bytes_limit'] or shutil.disk_usage(root).free<data['required_free_bytes']:reason='STORAGE_LIMIT'
                        last_storage=elapsed
                    if reason:
                        os.killpg(process.pid,signal.SIGTERM)
                        try:process.wait(timeout=1)
                        except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()
                        break
                    time.sleep(.25)
                code=process.wait();out.flush();err.flush();os.fsync(out.fileno());os.fsync(err.fileno())
        except BaseException as error:
            reason=type(error).__name__+': '+str(error);code=1
            if process and process.poll() is None:
                os.killpg(process.pid,signal.SIGKILL);process.wait()
        record.update(exit_code=code,stop_reason=reason,elapsed_seconds=time.monotonic()-started,
                      wall_seconds=time.time()-wall,peak_sampled_tree_rss=peak,finished_at=now(),
                      status='PASS' if code==0 and not reason else 'FAIL')
        save(ledger,data);print(json.dumps(record,ensure_ascii=False));raise SystemExit(code if code else (1 if reason else 0))

if __name__=='__main__':main()
