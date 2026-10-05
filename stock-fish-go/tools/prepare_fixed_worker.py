"""One bounded, locked preparation from official artifacts in a new private venv.

Planning reads existing distribution metadata, without importing SDKs or using
the network. Preparation never resolves latest versions or modifies that env.
"""
import argparse
import hashlib
import importlib.metadata as metadata
import json
import math
import os
import pathlib
import subprocess
import sys
import time
import urllib.parse
import urllib.request

from pip._vendor.packaging.requirements import Requirement
from pip._vendor.packaging.tags import sys_tags
from pip._vendor.packaging.utils import canonicalize_name, parse_wheel_filename
from pip._vendor.packaging.version import Version

MAX_BYTES=512*1024**2
HOSTS={'pypi.org','files.pythonhosted.org'}
PURE_SOURCE_PACKAGES={'jsonpath','baostock','html5lib','webencodings'}

def existing_distribution(name):
    try:return metadata.distribution(name)
    except metadata.PackageNotFoundError:
        # Read the configured interpreter's existing base metadata when the
        # project env has no distribution. No package is imported or upgraded.
        base=pathlib.Path(sys.base_prefix)/'lib'/('python'+str(sys.version_info.major)+'.'+str(sys.version_info.minor))/'site-packages'
        for distribution in metadata.distributions(path=[str(base)]):
            if canonicalize_name(distribution.metadata.get('Name',''))==canonicalize_name(name):return distribution
        raise

def locked_plan(requirement_file):
    selected={};requirements=[];seen=set()
    def read(path):
        path=path.resolve()
        if path in seen:return
        seen.add(path)
        for line in path.read_text().splitlines():
            line=line.split('#',1)[0].strip()
            if not line:continue
            if line.startswith('-r '):read(path.parent/line[3:].strip());continue
            requirement=Requirement(line)
            if requirement.url or len(list(requirement.specifier))!=1 or next(iter(requirement.specifier)).operator!='==':
                raise ValueError('EXACT_EXISTING_TOP_LEVEL_PINS_REQUIRED')
            requirements.append(requirement)
    read(requirement_file)
    # These existing exact build-tool identities permit offline installation of
    # registered small pure-Python projects whose official release has no wheel.
    requirements += [Requirement(name+'=='+existing_distribution(name).version) for name in ('setuptools','wheel')]
    queue=[(r,{''}) for r in requirements]
    processed=set()
    while queue:
        requirement,extras=queue.pop(0)
        if requirement.marker and not any(requirement.marker.evaluate({'extra':extra}) for extra in extras):continue
        name=canonicalize_name(requirement.name)
        distribution=existing_distribution(name)
        version=distribution.version
        if requirement.url or Version(version) not in requirement.specifier:
            raise ValueError('EXISTING_DEPENDENCY_DOES_NOT_SATISFY_LOCK: '+str(requirement))
        selected[name]=version
        requested={''}|set(requirement.extras)
        pending={(name,extra) for extra in requested}-processed
        if not pending:continue
        processed.update(pending)
        active={extra for _,extra in pending}
        queue.extend((Requirement(value),active) for value in (distribution.requires or []))
    return {'contract':'EXISTING_FIXED_WORKER_LOCK_V1','python':sys.version.split()[0],
            'requirements':{name:selected[name] for name in sorted(selected)},
            'inputs':[{'path':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()} for path in sorted(seen)]}

def official_url(url):
    parsed=urllib.parse.urlsplit(url)
    if parsed.scheme!='https' or parsed.hostname not in HOSTS or parsed.username or parsed.password:
        raise ValueError('OFFICIAL_HTTPS_SOURCE_REQUIRED')
    return url

class OfficialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,request,fp,code,msg,headers,newurl):
        official_url(newurl)
        return super().redirect_request(request,fp,code,msg,headers,newurl)

def save(path,value):
    pending=path.with_suffix('.pending')
    with pending.open('w') as handle:
        json.dump(value,handle,indent=2,allow_nan=False);handle.write('\n');handle.flush();os.fsync(handle.fileno())
    os.replace(pending,path)

def choose_wheel(value,name,version):
    priority={tag:index for index,tag in enumerate(sys_tags())}
    candidates=[]
    for file in value['urls']:
        if file['packagetype']!='bdist_wheel' or file.get('yanked'):continue
        wheel_name,wheel_version,_,tags=parse_wheel_filename(file['filename'])
        matches=[priority[tag] for tag in tags if tag in priority]
        if canonicalize_name(wheel_name)==name and wheel_version==Version(version) and matches:
            candidates.append((min(matches),file['filename'],file))
    if candidates:
        file=min(candidates,key=lambda item:item[:2])[2]
    else:
        source=[file for file in value['urls'] if file['packagetype']=='sdist' and not file.get('yanked')]
        if name not in PURE_SOURCE_PACKAGES or len(source)!=1 or source[0]['size']>4*1024**2:
            raise ValueError('LOCKED_PLATFORM_WHEEL_UNAVAILABLE: '+name+'=='+version)
        file=source[0]
    official_url(file['url'])
    if pathlib.Path(file['filename']).name!=file['filename'] or '\\' in file['filename']:
        raise ValueError('ARTIFACT_FILENAME_INVALID')
    if len(file.get('digests',{}).get('sha256',''))!=64 or type(file['size']) is not int or file['size']<=0:
        raise ValueError('WHEEL_SOURCE_IDENTITY_UNVERIFIED')
    return file

def prepare(plan,target,seconds):
    if not math.isfinite(seconds) or seconds<=5 or seconds>570:
        raise ValueError('DEPENDENCY_TIME_LIMIT_INVALID')
    if target.exists():raise ValueError('NEW_PRIVATE_DEPENDENCY_ROOT_REQUIRED')
    target.mkdir(parents=True)
    record={'contract':'OFFICIAL_LOCKED_WORKER_PREPARATION_V1','status':'RUNNING',
            'byte_limit':MAX_BYTES,'downloaded_body_bytes':0,'requests':0,'lock':plan,'files':[]}
    evidence=target/'preparation.json';save(evidence,record)
    deadline=time.monotonic()+seconds
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),OfficialRedirect())
    def fetch(url,max_bytes,output=None):
        official_url(url)
        if max_bytes>MAX_BYTES-record['downloaded_body_bytes']:raise ValueError('DEPENDENCY_DOWNLOAD_ADMISSION_DENIED')
        remaining=deadline-time.monotonic()
        if remaining<=5:raise TimeoutError('DEPENDENCY_TIME_WITH_CLEANUP_RESERVE_EXHAUSTED')
        record['requests']+=1;save(evidence,record)
        digest=hashlib.sha256();data=bytearray();count=0
        with opener.open(urllib.request.Request(url,headers={'Accept-Encoding':'identity'}),timeout=min(30,remaining-3)) as response:
            official_url(response.url)
            length=response.headers.get('Content-Length')
            if length and int(length)>max_bytes:raise ValueError('DEPENDENCY_RESPONSE_SIZE_LIMIT')
            handle=output.open('xb') if output else None
            try:
                while count<max_bytes:
                    chunk=response.read(min(65536,max_bytes-count))
                    if not chunk:break
                    count+=len(chunk);record['downloaded_body_bytes']+=len(chunk);digest.update(chunk)
                    if handle:handle.write(chunk)
                    else:data.extend(chunk)
                    if time.monotonic()>=deadline-3:raise TimeoutError('DEPENDENCY_TIME_WITH_CLEANUP_RESERVE_EXHAUSTED')
                if handle:handle.flush();os.fsync(handle.fileno())
            finally:
                if handle:handle.close()
                save(evidence,record)
        return bytes(data),count,digest.hexdigest()
    try:
        wheels=target/'wheels';wheels.mkdir()
        identities=[]
        for name,version in plan['requirements'].items():
            body,_,_=fetch('https://pypi.org/pypi/'+name+'/'+version+'/json',2*1024**2)
            source=json.loads(body)
            if canonicalize_name(source['info']['name'])!=name or Version(source['info']['version'])!=Version(version):
                raise ValueError('OFFICIAL_RELEASE_IDENTITY_MISMATCH')
            file=choose_wheel(source,name,version)
            identities.append((name,version,file))
        total=sum(file['size'] for _,_,file in identities)
        if total>MAX_BYTES-record['downloaded_body_bytes']:raise ValueError('DEPENDENCY_LOCKED_WHEELS_EXCEED_DOWNLOAD_LIMIT')
        for name,version,file in identities:
            _,count,sha=fetch(file['url'],file['size'],wheels/file['filename'])
            if count!=file['size'] or sha!=file['digests']['sha256']:raise ValueError('DEPENDENCY_WHEEL_HASH_OR_LENGTH_MISMATCH')
            record['files'].append({'name':name,'version':version,'filename':file['filename'],'source':file['url'],'bytes':count,'sha256':sha});save(evidence,record)
        requirements=target/'locked-requirements.txt'
        def requirement_text(values):
            return ''.join(name+'=='+version+' --hash=sha256:'+file['digests']['sha256']+'\n' for name,version,file in values)
        requirements.write_text(requirement_text(identities))
        def run(command):
            remaining=deadline-time.monotonic()-3
            if remaining<=0:raise TimeoutError('DEPENDENCY_INSTALL_TIME_EXHAUSTED')
            subprocess.run(command,check=True,timeout=remaining)
        run([sys.executable,'-m','venv',str(target/'venv')])
        bootstrap=target/'locked-build-tools.txt'
        bootstrap.write_text(requirement_text([item for item in identities if item[0] in {'setuptools','wheel','packaging'}]))
        command=[str(target/'venv/bin/python'),'-m','pip','install','--no-index','--no-deps','--require-hashes','--find-links',str(wheels)]
        run([*command,'-r',str(bootstrap)])
        run([*command,'--no-build-isolation','-r',str(requirements)])
        record['status']='INSTALLED_NOT_ONLINE_VERIFIED'
    except BaseException as error:
        record['status']='FAILED';record['reason']=type(error).__name__+': '+str(error)
        raise
    finally:save(evidence,record)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--requirements',type=pathlib.Path)
    parser.add_argument('--plan',required=True,type=pathlib.Path);parser.add_argument('--plan-only',action='store_true')
    parser.add_argument('--target',type=pathlib.Path);parser.add_argument('--seconds',type=float,default=570)
    args=parser.parse_args()
    if args.plan_only:
        plan=locked_plan(args.requirements);save(args.plan,plan);print(json.dumps(plan))
    else:prepare(json.loads(args.plan.read_text()),args.target,args.seconds)
