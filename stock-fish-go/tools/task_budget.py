"""Local task supervisor. No downloads, cleanup, deployment or budget resets.

Initialize once. All subprocesses serialize on the durable ledger lock. Explicit
reservations and attempts survive failures; an interrupted RUNNING reservation
blocks further runs until its actual consumption is reconciled, never refunded.
"""
import argparse
import datetime
import fcntl
import json
import math
import os
import pathlib
import re
import shutil
import signal
import subprocess
import time

GIB = 1024**3
TERMINAL_RESERVE_SECONDS = 1.0
LIMITS = {'engineering': (5400,1800,None), 'readonly': (1800,1800,None),
          'research': (3600,1800,2), 'online': (600,600,1), 'dependency':(600,600,1)}

def initialize_accounting(data):
    """Import existing outside-runner charges once, without refunding any usage.

    The historical aggregate includes admission checks already in the ledger.
    Subsequent admissions and source checks are explicit external charges, so
    there is no double count and a resume cannot silently forget earlier work.
    """
    engineering = float(data.get('known_engineering_source_and_admission_seconds', 0))
    reserved = float(data.get('source_check_reservation', {}).get('seconds', 0))
    readonly = float(data.get('readonly_preflight_used_seconds', 0))
    values = (engineering, reserved, readonly)
    if any(not math.isfinite(v) or v < 0 for v in values):
        raise ValueError('INVALID_PREVIOUS_BUDGET_CONSUMPTION')
    if 'external_budget_charges' in data:
        expected={'preserved-source-and-admission-usage':('engineering',engineering),
                  'preserved-untimed-source-reservation':('engineering',reserved),
                  'preserved-readonly-preflight':('readonly',readonly)}
        charges=data['external_budget_charges']
        if data.get('budget_accounting_version')!=2 or not isinstance(charges,list):
            raise ValueError('INCOMPLETE_PREVIOUS_BUDGET_IMPORT')
        for label,(kind,seconds) in expected.items():
            matches=[x for x in charges if x.get('label')==label]
            if len(matches)!=1 or matches[0].get('kind')!=kind or matches[0].get('seconds')!=seconds:
                raise ValueError('INCOMPLETE_PREVIOUS_BUDGET_IMPORT')
        return
    data['external_budget_charges'] = [
        {'kind':'engineering', 'label':'preserved-source-and-admission-usage',
         'seconds':engineering, 'basis':'PREEXISTING_LEDGER_AGGREGATE'},
        {'kind':'engineering', 'label':'preserved-untimed-source-reservation',
         'seconds':reserved, 'basis':'RESERVED_NOT_CLAIMED_AS_MEASURED'},
        {'kind':'readonly', 'label':'preserved-readonly-preflight',
         'seconds':readonly, 'basis':'PREEXISTING_LEDGER_ACTUAL'},
    ]
    data['budget_accounting_version'] = 2

def add_external_charge(data, kind, seconds, label):
    initialize_accounting(data)
    if kind not in LIMITS or not math.isfinite(seconds) or seconds < 0:
        raise ValueError('INVALID_EXTERNAL_BUDGET_CHARGE')
    if any(x['label'] == label for x in data['external_budget_charges']):
        raise ValueError('EXTERNAL_BUDGET_CHARGE_ALREADY_RECORDED')
    data['external_budget_charges'].append(
        {'kind':kind, 'label':label, 'seconds':seconds, 'basis':'MEASURED', 'at':now()})

def used_seconds(data, kind):
    initialize_accounting(data)
    kinds = (kind, 'dependency') if kind == 'engineering' else (kind,)
    values=[]
    for attempt in data['attempts']:
        if attempt['kind'] not in kinds:continue
        actual=attempt.get('elapsed_seconds',attempt['reserved_seconds'])
        charged=attempt.get('budget_charge_seconds',actual)
        if type(actual) not in (int,float) or not math.isfinite(actual) or actual<0:
            raise ValueError('INVALID_BUDGET_CONSUMPTION')
        if type(charged) not in (int,float) or not math.isfinite(charged) or charged<actual:
            raise ValueError('BUDGET_CHARGE_CANNOT_REFUND_MEASURED_RUNTIME')
        values.append(charged)
    values += [x['seconds'] for x in data['external_budget_charges'] if x['kind'] in kinds]
    if any(type(v) not in (int,float) or not math.isfinite(v) or v < 0 for v in values):
        raise ValueError('INVALID_BUDGET_CONSUMPTION')
    return sum(values)

def effective_limits(data, kind):
    """Stricter stored limits apply; only a task-bound 5400→6000 grant raises engineering."""
    fixed_total, fixed_per, fixed_count = LIMITS[kind]
    if 'engineering_limit_authorization' in data:
        authorization=data['engineering_limit_authorization']
        task_id=data.get('task_id')
        if (not isinstance(authorization,dict)
            or not isinstance(task_id,str) or not task_id.strip()
            or authorization.get('task_id')!=task_id
            or type(authorization.get('previous_total_seconds')) is not int
            or authorization['previous_total_seconds']!=5400
            or type(authorization.get('total_seconds')) is not int
            or authorization['total_seconds']!=6000
            or not isinstance(authorization.get('user_instruction'),str)
            or not authorization['user_instruction'].strip()):
            raise ValueError('ENGINEERING_TIME_EXCEPTION_NOT_AUTHORIZED')
        if kind=='engineering':fixed_total=6000
    total, per, count = data.get('limits', LIMITS)[kind]
    if not math.isfinite(total) or not math.isfinite(per) or total <= 0 or per <= 0:
        raise ValueError('INVALID_LEDGER_BUDGET_LIMIT')
    if count is not None and (not isinstance(count, int) or count < 0):
        raise ValueError('INVALID_LEDGER_ATTEMPT_LIMIT')
    counts = [c for c in (fixed_count, count) if c is not None]
    return min(fixed_total, total), min(fixed_per, per), min(counts) if counts else None

def storage_limits(data):
    names=('project_limit_bytes','new_bytes_limit','required_free_bytes','rss_limit_bytes')
    values=[data[name] for name in names]
    if any(type(value) is not int or value <= 0 for value in values):
        raise ValueError('FINITE_POSITIVE_RESOURCE_LIMITS_REQUIRED')
    project,new,free,rss=values
    if new>2*GIB or rss>2*GIB or free<10*GIB:
        raise ValueError('FIXED_RESOURCE_CEILINGS_CANNOT_BE_RELAXED')
    if project>50*GIB:
        authorization=data.get('storage_limit_authorization',{})
        if authorization.get('project_limit_bytes')!=project or not authorization.get('user_instruction'):
            raise ValueError('PROJECT_STORAGE_EXCEPTION_NOT_AUTHORIZED')
    return project,new,free,rss

def artifact_roots(data, ledger):
    # All runtime output roots must be explicitly registered before spawning.
    # TMPDIR/GOCACHE/PIP_CACHE_DIR below are always task-private roots.
    paths={ledger.parent.resolve()}
    paths.update(pathlib.Path(p).resolve() for p in data.get('managed_artifact_roots',[]))
    result=[]
    for path in sorted(paths,key=lambda p:len(p.parts)):
        if not any(path.is_relative_to(parent) for parent in result):result.append(path)
    return result

def resource_usage(data,ledger,deadline=None):
    project=size(pathlib.Path(data['root']),deadline)
    new=new_storage_bytes(data,ledger,project,deadline)
    volumes={}
    for path in [pathlib.Path(data['root']),*artifact_roots(data,ledger),*existing_growth_paths(data)]:
        while not path.exists():path=path.parent
        volumes[path.stat().st_dev]=shutil.disk_usage(path).free
    return project,new,min(volumes.values())

def existing_growth_paths(data):
    root=pathlib.Path(data['root']).resolve()
    result={}
    for entry in data.get('existing_file_growth',[]):
        path=(root/entry['path']).resolve()
        baseline=entry['baseline_allocated_bytes']
        if not path.is_relative_to(root) or type(baseline) is not int or baseline<0 or path in result:
            raise ValueError('INVALID_EXISTING_FILE_GROWTH_REGISTRATION')
        result[path]=entry
    return result

def existing_growth_bytes(data,managed):
    reserved=data.get('existing_data_reserved_bytes',0)
    if type(reserved) is not int or reserved<0 or reserved>2*GIB:
        raise ValueError('INVALID_EXISTING_DATA_STORAGE_RESERVATION')
    total=0
    for path,entry in existing_growth_paths(data).items():
        if any(path.is_relative_to(parent) for parent in managed):continue
        try:current=path.stat()
        except FileNotFoundError:
            if entry.get('must_exist'):raise ValueError('REGISTERED_EXISTING_FILE_MISSING')
            continue
        identity=entry.get('original_identity')
        if identity and [current.st_dev,current.st_ino]!=identity:
            raise ValueError('REGISTERED_EXISTING_FILE_REPLACED')
        total+=max(0,current.st_blocks*512-entry['baseline_allocated_bytes'])
    # Existing free pages can hold newly created rows without file growth.
    # A reviewed conservative bound remains charged as a reservation; it is
    # never presented as an observed amount of new database data.
    return max(total,reserved)

def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def size(path, deadline=None):
    # A compiler may remove its temporary directory during traversal. Retry a
    # complete measurement, never accept the partial subtotal from a failed du.
    # Other I/O errors and repeated churn remain unverified and stop admission.
    command=['du','-sk',str(path)]
    for attempt in range(3):
        timeout=None if deadline is None else deadline-time.monotonic()
        if timeout is not None and timeout<=0:
            raise subprocess.TimeoutExpired(command,0)
        env=os.environ.copy();env['LC_ALL']='C'
        result=subprocess.run(command,text=True,capture_output=True,timeout=timeout,env=env)
        if result.returncode==0:
            return int(result.stdout.split()[0])*1024
        error=subprocess.CalledProcessError(result.returncode,command,
                                            output=result.stdout,stderr=result.stderr)
        messages=result.stderr.strip().splitlines()
        if not messages or any(not line.endswith(': No such file or directory') for line in messages) or attempt==2:
            raise error
def new_storage_bytes(data, ledger, project, deadline=None):
    # Project net growth alone can hide new artifacts when an unrelated old
    # file disappears. All task subprocess output is rooted below this private
    # directory; changed source files are conservatively charged in full.
    managed=artifact_roots(data,ledger)
    charged=sum(size(path,deadline) for path in managed if path.exists())
    charged+=existing_growth_bytes(data,managed)
    for relative in data.get('task_source_paths', []):
        path=pathlib.Path(data['root'])/relative
        if path.exists():
            charged += path.stat().st_blocks*512
    if not data.get('task_source_paths'):
        charged += data.get('task_new_storage_source_files_bound_bytes', 0)
    return max(charged, max(0,project-data['initial_project_bytes']))
def save(path,data):
    tmp=path.with_suffix('.pending')
    with tmp.open('w') as f:
        json.dump(data,f,indent=2,ensure_ascii=False,allow_nan=False);f.write('\n');f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)
    directory=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(directory)
    finally:os.close(directory)

class TaskTree:
    """Track the owned session and observed descendants by PID/start identity.

    SDK workers use independent process groups in our session. Session identity
    still proves ownership after the parent exits; a sampled descendant that
    creates its own session remains tracked by its original start identity.
    """
    def __init__(self,pid):
        self.session=pid  # Popen(start_new_session=True) guarantees this SID.
        self.seen={}

    def sample(self):
        output=subprocess.check_output(['ps','-axo','pid,ppid,pgid,rss,lstart,stat'],
                                       text=True,timeout=1)
        rows={}
        for line in output.splitlines()[1:]:
            fields=line.split()
            if len(fields)<10:continue
            try:
                pid,parent,group,rss=map(int,fields[:4])
                sid=os.getsid(pid)
            except (ValueError,ProcessLookupError,PermissionError):continue
            rows[pid]={'parent':parent,'group':group,'rss':rss*1024,
                       'start':' '.join(fields[4:9]),'state':fields[9],'sid':sid}
        owned={pid for pid,row in rows.items()
               if row['sid']==self.session or self.seen.get(pid)==row['start']}
        while True:
            grown=owned|{pid for pid,row in rows.items() if row['parent'] in owned}
            if grown==owned:break
            owned=grown
        current={pid:rows[pid] for pid in owned if not rows[pid]['state'].startswith('Z')}
        for pid,row in current.items():self.seen[pid]=row['start']
        return current

    def stop(self):
        current=self.sample()
        if not current:return False
        groups={row['group'] for row in current.values()}
        for group in groups:
            try:os.killpg(group,signal.SIGTERM)
            except ProcessLookupError:pass
        time.sleep(.05)
        current=self.sample()
        for group in {row['group'] for row in current.values()}:
            try:os.killpg(group,signal.SIGKILL)
            except ProcessLookupError:pass
        if any(not row['state'].startswith('Z') for row in self.sample().values()):
            time.sleep(.05)
            if self.sample():raise RuntimeError('OWNED_PROCESS_TERMINATION_UNCONFIRMED')
        return True

    def emergency_stop(self):
        # If ps itself failed, kernel SID still proves ownership of SDK groups.
        # Escaped sessions require the normal identity check; never kill a
        # reused unrelated PID merely because it was observed earlier.
        for pid in {self.session,*self.seen}:
            try:
                if os.getsid(pid)==self.session:
                    os.killpg(os.getpgid(pid),signal.SIGKILL)
            except (ProcessLookupError,PermissionError):pass

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
        initialize_accounting(data)
        def deny(reason):
            elapsed=time.monotonic()-invoked
            admissions=data.setdefault('admission_attempts',[])
            if a.kind in LIMITS:
                add_external_charge(data,a.kind,elapsed,'admission-'+str(len(admissions)))
                data['external_budget_charges'].append({'kind':a.kind,
                    'label':'admission-terminal-'+str(len(admissions)),
                    'seconds':TERMINAL_RESERVE_SECONDS,'basis':'RESERVED_FOR_FINAL_LEDGER_FSYNC_AND_OUTPUT; NOT_MEASURED'})
            seconds=a.seconds if a.seconds is None or math.isfinite(a.seconds) else None
            admissions.append({'at':now(),'kind':a.kind,'label':a.label,'requested_seconds':seconds,'rejected_nonfinite_seconds':str(a.seconds) if a.seconds is not None and not math.isfinite(a.seconds) else None,'requested_bytes':a.reserve_bytes,'decision':'DENIED','reason':reason,'process_started':False,'elapsed_seconds':elapsed,'terminal_seconds_reserved':TERMINAL_RESERVE_SECONDS})
            save(ledger,data);raise SystemExit(reason)
        if any(x['status']=='RUNNING' for x in data['attempts']):raise SystemExit('UNRECONCILED_RUNNING_ATTEMPT')
        if not a.kind or not a.label or not a.seconds or not a.command:deny('EXPLICIT_SCOPE_REQUIRED')
        if not math.isfinite(a.seconds) or a.seconds <= 2:deny('FINITE_TIME_WITH_CLEANUP_RESERVE_REQUIRED')
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',a.label) or any(x['label']==a.label for x in data['attempts']):deny('NEW_SAFE_LABEL_REQUIRED')
        if a.reserve_bytes<0 or a.reserve_bytes>data['new_bytes_limit']:deny('RESERVATION_BYTES_INVALID')
        command=a.command[1:] if a.command[0]=='--' else a.command
        if a.cwd and not a.cwd.resolve().is_relative_to(root.resolve()):
            deny('WORKING_DIRECTORY_OUTSIDE_PROJECT_SCOPE')
        total,per,count=effective_limits(data,a.kind)
        selected=[x for x in data['attempts'] if x['kind']==a.kind]
        used=used_seconds(data,a.kind)
        if a.seconds>per or used+a.seconds>total or (count is not None and len(selected)>=count):deny('BUDGET_ADMISSION_DENIED')
        if a.kind=='dependency' and used_seconds(data,'engineering')+a.seconds>effective_limits(data,'engineering')[0]:deny('ENGINEERING_SHARED_DEPENDENCY_BUDGET_DENIED')
        try:
            project_limit,new_limit,free_limit,rss_limit=storage_limits(data)
            project,new,free=resource_usage(data,ledger,invoked+a.seconds-2)
        except (ValueError,subprocess.TimeoutExpired,OSError) as error:
            deny('RESOURCE_ADMISSION_UNVERIFIED: '+str(error))
        if project+a.reserve_bytes>project_limit or new+a.reserve_bytes>new_limit or free-a.reserve_bytes<free_limit:
            deny('STORAGE_ADMISSION_DENIED')
        cleanup_reserve=max(3,time.monotonic()-invoked+2)
        if time.monotonic()-invoked >= a.seconds-cleanup_reserve:
            deny('ADMISSION_CONSUMED_PROCESS_TIME_RESERVE')
        env=os.environ.copy();env.update(GOPROXY='off',GOSUMDB='off',GOTOOLCHAIN='local',PYTHONDONTWRITEBYTECODE='1')
        env['GOFLAGS']=(env.get('GOFLAGS','')+' -mod=readonly').strip()
        scratch=ledger.parent/'scratch';scratch.mkdir(exist_ok=True)
        env['TMPDIR']=str(scratch);env['GOCACHE']=str(ledger.parent/'go-cache')
        env['PIP_CACHE_DIR']=str(ledger.parent/'pip-cache')
        record={'label':a.label,'kind':a.kind,'command':command,'cwd':str(a.cwd or root),
                'started_at':now(),'reserved_seconds':a.seconds,'reserved_bytes':a.reserve_bytes,
                'project_admission_bytes':project,'new_admission_bytes':new,
                'free_admission_bytes':free,'cleanup_reserve_seconds':cleanup_reserve,
                'managed_artifact_roots':[str(path) for path in artifact_roots(data,ledger)],'status':'RUNNING'}
        record['existing_data_reserved_bytes']=data.get('existing_data_reserved_bytes',0)
        data['attempts'].append(record);save(ledger,data)
        started=invoked;wall=time.time();peak=0;reason=None;process=None;owned=None
        try:
            with (ledger.parent/(a.label+'.stdout')).open('xb') as out,(ledger.parent/(a.label+'.stderr')).open('xb') as err:
                process=subprocess.Popen(command,cwd=a.cwd or root,env=env,stdout=out,stderr=err,start_new_session=True)
                owned=TaskTree(process.pid)
                last_storage=0
                while process.poll() is None:
                    elapsed=time.monotonic()-started
                    peak=max(peak,sum(row['rss'] for row in owned.sample().values()))
                    if elapsed>=a.seconds-cleanup_reserve:reason='TIME_LIMIT_WITH_CLEANUP_RESERVE'
                    if peak>rss_limit:reason='PROCESS_TREE_RSS_LIMIT'
                    if elapsed-last_storage>10:
                        probe_deadline=started+a.seconds-cleanup_reserve
                        current,current_new,current_free=resource_usage(data,ledger,probe_deadline)
                        record['project_peak_bytes']=max(current,record.get('project_peak_bytes',0))
                        if current>project_limit or current_new>new_limit or current_free<free_limit:reason='STORAGE_LIMIT'
                        last_storage=elapsed
                    if reason:
                        owned.stop()
                        try:process.wait(timeout=1)
                        except subprocess.TimeoutExpired:owned.stop();process.wait(timeout=1)
                        break
                    time.sleep(.25)
                code=process.wait()
                if owned.stop() and reason is None:
                    reason='UNEXPECTED_REMAINING_CHILD_PROCESSES'
                out.flush();err.flush();os.fsync(out.fileno());os.fsync(err.fileno())
                final_project,final_new,final_free=resource_usage(data,ledger,started+a.seconds-TERMINAL_RESERVE_SECONDS)
                record.update(final_project_bytes=final_project,final_new_bytes=final_new,final_free_bytes=final_free)
                if final_project>project_limit or final_new>new_limit or final_free<free_limit:
                    reason=reason or 'FINAL_STORAGE_LIMIT'
        except BaseException as error:
            reason=type(error).__name__+': '+str(error);code=1
            if process and process.poll() is None:
                try:
                    if owned:owned.stop()
                    else:os.killpg(process.pid,signal.SIGKILL)
                    process.wait(timeout=1)
                except (OSError,RuntimeError,subprocess.TimeoutExpired) as cleanup_error:
                    if owned:owned.emergency_stop()
                    reason+='; CLEANUP_UNCONFIRMED: '+str(cleanup_error)
            if owned:
                try:owned.stop()
                except (OSError,RuntimeError,subprocess.TimeoutExpired) as cleanup_error:
                    owned.emergency_stop()
                    reason+='; CLEANUP_UNCONFIRMED: '+str(cleanup_error)
        elapsed=time.monotonic()-started
        if elapsed+TERMINAL_RESERVE_SECONDS>a.seconds:
            reason=reason or 'MEASURED_RUNTIME_WITH_TERMINAL_RESERVE_EXCEEDS_PROCESS_LIMIT'
        record.update(exit_code=code,stop_reason=reason,elapsed_seconds=elapsed,
                      budget_charge_seconds=elapsed+TERMINAL_RESERVE_SECONDS,
                      terminal_seconds_reserved=TERMINAL_RESERVE_SECONDS,
                      terminal_accounting_basis='RESERVED_FOR_FINAL_LEDGER_FSYNC_AND_OUTPUT; NOT_MEASURED',
                      wall_seconds=time.time()-wall,peak_sampled_tree_rss=peak,finished_at=now(),
                      status='RUNNING' if reason and 'UNCONFIRMED' in reason else ('PASS' if code==0 and not reason else 'FAIL'))
        save(ledger,data);print(json.dumps(record,ensure_ascii=False));raise SystemExit(code if code else (1 if reason else 0))

if __name__=='__main__':main()
