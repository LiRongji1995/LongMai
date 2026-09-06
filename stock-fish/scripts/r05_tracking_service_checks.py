from datetime import datetime,timezone
from pathlib import Path
import hashlib,json,subprocess,sys
out=Path('../review/r05-rectification/tracking-service-evidence');out.mkdir(parents=True,exist_ok=True)
cmd=[sys.executable,'-m','pytest','-q','tests/r05_product/test_rectification_tracking_service.py','tests/r05_product/test_strategy_experiments_and_tushare.py','tests/r04_product/test_direct_limits_and_summary.py','../work/r05-rectification-attachment/evidence/expanded/probes/test_closure_independent.py','--junitxml='+str(out/'checks.xml')]
start=datetime.now(timezone.utc).isoformat();result=subprocess.run(cmd,capture_output=True,text=True,encoding='utf-8',errors='replace');end=datetime.now(timezone.utc).isoformat()
(out/'stdout.log').write_text(result.stdout,encoding='utf-8');(out/'stderr.log').write_text(result.stderr,encoding='utf-8')
meta={'command':cmd,'cwd':str(Path.cwd()),'start':start,'end':end,'exit_code':result.returncode,'python':sys.version,'evidence_class':'TEST_FIXTURE_ENGINEERING_ONLY','files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir() if p.is_file() and p.name!='run.json'}}
(out/'run.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8');print(result.stdout);print(result.stderr);raise SystemExit(result.returncode)
