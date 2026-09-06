"""Export explicit TEST_FIXTURE study arithmetic, never to the normal state DB."""
import argparse
from datetime import timedelta
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
test_tools=ROOT.parent/'review/test-tools'
if test_tools.is_dir():
    sys.path.insert(0,str(test_tools))

from stockfish_core.common import canonical_json
from stockfish_core.yaogu.evaluation import StudyConfig, evaluate_study, export_study
from tests.r02_product.test_historical_evaluation import study_fixture, observation, DAY, at

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--destination',required=True)
args=parser.parse_args()
runs,outcomes=study_fixture(days=3)
trends,trend_outcomes=study_fixture(days=3,family='TREND_NM')
calendar=[]
day=DAY
while len(calendar)<12:
    if day.weekday()<5:
        calendar.append(day)
    day+=timedelta(days=1)
for run in trends:
    for index,item in enumerate(run.items):
        trend_outcomes.append(observation(run.run_id,item.instrument_id,item.primary_family,
            run.observation_date,horizon=3,success=index!=1,
            end=calendar[calendar.index(run.observation_date)+3]))
result=evaluate_study([*runs,*trends],[*outcomes,*trend_outcomes],StudyConfig(top_k=1),at(calendar[9],16))
result['report']['fixture']='TEST_FIXTURE'
result['report']['fixture_purpose']='Study denominators/comparisons/cluster bootstrap only; manually assigned component scores are not a market strategy validation.'
manifest=export_study(result,args.destination)
print(canonical_json({'fixture':'TEST_FIXTURE','status':result['report']['status'],
    'sample_count':len(result['samples']),'random_selections':sum(r['method']=='FIXED_RANDOM' for r in result['comparison_selections']),
    'bootstrap_repetitions':result['report']['config']['bootstrap_repetitions'],
    'market_edge':result['report']['market_edge'],'files':manifest['files']}))
