"""Reproducible isolated R12 suite matrix with commands, counts and timings."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SUITES = {
    'scoring': ['tests/r05_product/test_recommendation_scoring_v1.py','tests/r05_product/test_recommendation_score_artifacts.py','tests/r05_product/test_r12_score_integrity.py'],
    'stage-aware': ['tests/r05_product/test_stage_aware_momentum_v1.py','tests/r05_product/test_stage_aware_momentum_v2_semantic_completion.py'],
    'outcome-tracking': ['tests/r05_product/test_outcome_context.py','tests/r05_product/test_rectification_tracking_service.py','tests/r03_strategy'],
    'scheduler': ['tests/r02_product/test_scheduled_outcomes.py','tests/r02_product/test_candidate_product_path.py'],
    'pipeline-queue': ['tests/r03_pipeline'],
    'pit-source': ['tests/r03_evidence','tests/r05_product/test_historical_replay_adapter.py'],
    'api-ui': ['tests/integration/test_core_api.py','tests/r05_product/test_recommendation_scoring_v1.py','tests/r05_product/test_recommendation_score_artifacts.py','tests/r05_product/test_r12_delivery_scripts.py'],
    'full-core': ['tests'],
    'full-repository': [],
}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--suite',choices=list(SUITES),action='append')
    a=p.parse_args(); a.output=a.output.resolve(); a.output.mkdir(parents=True,exist_ok=True)
    results=[]
    for name in a.suite or list(SUITES):
        xml=a.output/(name+'.xml'); log=a.output/(name+'.txt')
        command=[sys.executable,'-m','pytest',*SUITES[name],'-q','--tb=short','--junitxml='+str(xml)]
        started=time.perf_counter()
        with log.open('w',encoding='utf-8') as handle:
            done=subprocess.run(command,cwd=ROOT,stdout=handle,stderr=subprocess.STDOUT)
        counts={'passed':None,'failed':None,'errors':None,'skipped':None}
        if xml.exists():
            tree=ET.parse(xml).getroot(); suites=[tree] if tree.tag=='testsuite' else tree.findall('testsuite')
            totals={key:sum(int(s.get(key,0)) for s in suites) for key in ('tests','failures','errors','skipped')}
            counts={'passed':totals['tests']-totals['failures']-totals['errors']-totals['skipped'],
                'failed':totals['failures'],'errors':totals['errors'],'skipped':totals['skipped']}
        result={'suite':name,'command':subprocess.list2cmdline(command),'cwd':str(ROOT),
            'exit_code':done.returncode,**counts,'duration_seconds':round(time.perf_counter()-started,3),
            'log':str(log),'junit':str(xml)}
        results.append(result)
        (a.output/'test-results.json').write_text(json.dumps({'generated_at':datetime.now(timezone.utc).isoformat(),'results':results},indent=2),encoding='utf-8')
        print(json.dumps(result),flush=True)
    return int(any(r['exit_code'] for r in results if r['suite']!='full-repository'))


if __name__=='__main__': raise SystemExit(main())
