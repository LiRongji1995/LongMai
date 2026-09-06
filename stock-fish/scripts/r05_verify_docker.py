"""Isolated R05 Docker runtime evidence; no existing service is changed.

Each command is really executed and logged. Volumes/artifacts remain private.
"""
import argparse
from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time

from r05_evidence_runner import run_command


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--private-root',required=True,type=Path)
    p.add_argument('--port',type=int,default=18005)
    p.add_argument('--base-image',default='python:3.12-slim')
    args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=False);args.private_root.mkdir(parents=True,exist_ok=False)
    root=Path(__file__).resolve().parents[1]
    name='r05verify'+datetime.now().strftime('%Y%m%d%H%M%S')
    envfile=args.private_root/'docker.env'
    key=secrets.token_hex(32)
    envfile.write_text(f'STOCKFISH_API_KEY={key}\nSTOCKFISH_PORT={args.port}\nTUSHARE_TOKEN=\nTUSHARE_PRO_TOKEN=\n',encoding='utf-8')
    os.environ['STOCKFISH_API_KEY']=key
    os.environ['TUSHARE_TOKEN']='';os.environ['TUSHARE_PRO_TOKEN']=''
    override=args.private_root/'override.yml'
    override.write_text(f'services:\n  stockfish:\n    image: {name}:local\n    build:\n      args:\n        BASE_IMAGE: {args.base_image}\n',encoding='utf-8')
    base=['docker','compose','-p',name,'--env-file',str(envfile),'-f',str(root/'docker-compose.yml'),'-f',str(override)]
    records=[]
    def run(label,command,timeout=900,*,input_path=None):
        print(label,flush=True)
        result=run_command(command,root,args.output/label,timeout,input_path=input_path)
        records.append({'label':label,**result})
        (args.output/'runtime-summary.json').write_text(json.dumps({'project':name,'port':args.port,'runtime_verified':False,'steps':records},indent=2),encoding='utf-8')
        if result['exit_code']!=0:raise RuntimeError(label+' failed; see recorded output')
        return (args.output/label/'stdout.log').read_text(encoding='utf-8').strip()
    run('01-daemon',['docker','info','--format','{{.ServerVersion}}'],30)
    run('02-compose-config',base+['config','--quiet'],30)
    run('03-build',base+['build','stockfish'],1800)
    run('04-up',base+['up','-d','--wait','--no-build','stockfish'],240)
    cid=run('05-container',base+['ps','-q','stockfish'],30)
    run('06-image-health',['docker','inspect',cid,'--format','{{.Image}} {{.State.Health.Status}} {{json .HostConfig.PortBindings}}'],30)
    hashes="from pathlib import Path; import hashlib,json; files=sorted(Path('stockfish_core').rglob('*.py'))+[p for folder in ('config','static') for p in sorted(Path(folder).rglob('*')) if p.is_file()]+[Path(p) for p in ('app.py','config.py','wsgi.py','requirements-core.txt','requirements-docker.txt','qlib-zh/data_runner.py','scripts/backup_restore_state.py')]; print(json.dumps({str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files},sort_keys=True))"
    image_hashes=json.loads(run('06a-image-source-hashes',base+['exec','-T','stockfish','python','-c',hashes],30))
    comparison=[]
    for path,image_hash in image_hashes.items():
        host_hash=hashlib.sha256((root/path).read_bytes()).hexdigest()
        comparison.append({'path':path,'image_sha256':image_hash,'host_sha256':host_hash,'matches':image_hash==host_hash})
    (args.output/'image-source-comparison.json').write_text(json.dumps({'compared_at':datetime.now(timezone.utc).isoformat(),
        'files':comparison,'all_matched':all(item['matches'] for item in comparison)},indent=2),encoding='utf-8')
    if not all(item['matches'] for item in comparison):raise RuntimeError('Built image differs from current source; see source comparison')
    check="import json,os,urllib.request; r=urllib.request.Request('http://127.0.0.1:8000/api/health',headers={'Authorization':'Bearer '+os.environ['STOCKFISH_API_KEY']}); print(urllib.request.urlopen(r).read().decode()); assert not os.environ.get('TUSHARE_TOKEN'); print('NO_TOKEN_CONTAINER_VERIFIED')"
    run('07-health-no-token',base+['exec','-T','stockfish','python','-c',check],30)
    sys.path.insert(0,str(root))
    from tests.yaogu.fixtures import make_bundle
    from decimal import Decimal
    fixture,cutoff=make_bundle()
    fixture=fixture.model_copy(update={'bars':tuple(bar.model_copy(update={'amount_cny':Decimal('40000000')}) if bar.session==fixture.observation_date else bar for bar in fixture.bars)})
    fixture_path=args.private_root/'pv-engineering-fixture.json'
    fixture_path.write_text(fixture.model_dump_json(),encoding='utf-8')
    transfer="from pathlib import Path; import sys; p=Path('/tmp/pv-engineering-fixture.json'); p.write_bytes(sys.stdin.buffer.read()); print('TEST_FIXTURE_STDIN_BYTES_RECEIVED')"
    run('07a-pv-fixture-copy',base+['exec','-T','stockfish','python','-c',transfer],30,input_path=fixture_path)
    pv="import os; from pathlib import Path; from datetime import datetime,timezone; from stockfish_core.yaogu.contracts import MarketInput; from stockfish_core.yaogu.strategy_experiments import run_pv_observation; assert not os.environ.get('TUSHARE_TOKEN'); b=MarketInput.model_validate_json(Path('/tmp/pv-engineering-fixture.json').read_text()); r=run_pv_observation(b,cutoff=datetime.now(timezone.utc)); assert r['items']; print({'scope':'TEST_FIXTURE','PV_without_token':True,'count':len(r['items'])})"
    run('07b-pv-no-token',base+['exec','-T','stockfish','python','-c',pv],30)
    seed="from stockfish_core.storage import SQLiteStore; s=SQLiteStore('/app/data/stockfish.sqlite3'); s.put_document('R05_DOCKER_ENGINEERING_FIXTURE','persistence',{'kind':'TEST_FIXTURE','value':73}); print(s.get_document('R05_DOCKER_ENGINEERING_FIXTURE','persistence'))"
    verify="from stockfish_core.storage import SQLiteStore; s=SQLiteStore('/app/data/stockfish.sqlite3'); assert s.get_document('R05_DOCKER_ENGINEERING_FIXTURE','persistence')['value']==73; print('PERSISTENCE_VERIFIED')"
    run('08-seed-isolated-state',base+['exec','-T','stockfish','python','-c',seed],30)
    run('09-restart',base+['restart','stockfish'],90)
    run('10-restart-wait',base+['up','-d','--wait','--no-build','stockfish'],240)
    run('11-restart-persistence',base+['exec','-T','stockfish','python','-c',verify],30)
    run('12-recreate',base+['up','-d','--wait','--force-recreate','--no-build','stockfish'],240)
    run('13-recreate-persistence',base+['exec','-T','stockfish','python','-c',verify],30)
    run('14-backup',base+['exec','-T','stockfish','python','scripts/backup_restore_state.py','backup','--database','/app/data/stockfish.sqlite3','--artifact-root','/app/data/core','--destination','/app/data/.backups/r05-consistent-backup'],120)
    run('15-backup-copy',base+['cp','stockfish:/app/data/.backups/r05-consistent-backup',str(args.private_root/'backup')],120)
    # Restore into new container tmp path; never replace the running database.
    run('16-isolated-restore',base+['exec','-T','stockfish','python','scripts/backup_restore_state.py','restore','--backup','/app/data/.backups/r05-consistent-backup','--destination','/app/data/.restored/r05-isolated-restored'],120)
    restore="from stockfish_core.storage import SQLiteStore; s=SQLiteStore('/app/data/.restored/r05-isolated-restored/database.sqlite3'); assert s.get_document('R05_DOCKER_ENGINEERING_FIXTURE','persistence')['value']==73; print('ISOLATED_RESTORE_PERSISTENCE_VERIFIED')"
    run('17-restore-persistence',base+['exec','-T','stockfish','python','-c',restore],30)
    # Explicit engineering sentinel verifies Compose mapping, not vendor rights.
    os.environ['TUSHARE_TOKEN']='TEST_FIXTURE_CONNECTOR_MAPPING_NOT_REAL'
    mapping="import os; from stockfish_core.yaogu.provider_requests import validate_request; assert os.environ['TUSHARE_TOKEN']=='TEST_FIXTURE_CONNECTOR_MAPPING_NOT_REAL'; assert validate_request('tushare_daily',{'trade_date':'2026-09-04'}); print('TOKEN_ENV_MAPPING_VERIFIED_NO_PROVIDER_REQUEST')"
    run('18-token-mapping',base+['run','--rm','--no-deps','stockfish','python','-c',mapping],90)
    os.environ['TUSHARE_TOKEN']=''
    run('19-final-logs',base+['logs','--no-color','--tail','100','stockfish'],30)
    run('20-stop-isolated',base+['stop','stockfish'],90)
    (args.output/'runtime-summary.json').write_text(json.dumps({'project':name,'port':args.port,'runtime_verified':True,
        'scope':'ISOLATED_ENGINEERING_FIXTURE_NO_MARKET_VALIDATION','private_artifacts':str(args.private_root),'steps':records},indent=2),encoding='utf-8')
    print('DOCKER_RUNTIME_VERIFIED=true',flush=True)


if __name__=='__main__':main()
