"""Reproducible local TEST_FIXTURE contract evidence; never writes user state."""
from pathlib import Path
from datetime import datetime,timezone,timedelta
import hashlib,json,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'review/test-tools'))
import yaml
from stockfish_core.yaogu.config import StrategyConfig,weighted_score
from stockfish_core.yaogu.contracts import digest
from stockfish_core.yaogu.engine import run
from tests.r02_independent_strategy.test_adversarial_semantics import market
from tests.r03_strategy.test_history_partition_hashes import history


def main():
    root=Path(__file__).resolve().parents[1]
    review=root.parent/'review/r03-realdata-closure'
    config=StrategyConfig()
    baseline=yaml.safe_load((review/'baseline-source/stock-fish/config/yaogu_v1.defaults.yaml').read_text(encoding='utf-8'))
    current=config.model_dump(mode='json')
    changes={k:{'before':baseline.get(k),'after':v} for k,v in current.items() if baseline.get(k)!=v}
    assert set(changes)=={'strategy_id','feature_definition_version'},changes
    bundle,cutoff=market(count=50,patterns={0:{69},**{i:{68} for i in range(1,11)}})
    bundle=bundle.model_copy(update={'previous_emotions':history(bundle)})
    result=run(bundle,cutoff=cutoff)
    noisy=run(bundle,cutoff=cutoff+timedelta(minutes=1),generated_at=cutoff+timedelta(minutes=2))
    files=['stockfish_core/yaogu/'+n for n in ('contracts.py','config.py','engine.py','emotion.py','emotion_history.py','candidate_service.py','visibility.py','funds.py')]
    files+=['config/yaogu_v1.defaults.yaml','STRATEGY_SPEC_V1.md','scripts/yaogu_history_study.py','scripts/r03_strategy_probe.py']
    report={'evidence_type':'TEST_FIXTURE','generated_at':datetime.now(timezone.utc).isoformat(),
        'config_hash':config.config_hash,'config_diff_from_frozen_r02':changes,
        'five_family_weights_thresholds_and_other_parameters_unchanged':True,
        'golden_76_75':weighted_score(dict(B=80,V=76,T=85,L=80,G=60,M=62),config.weights['TWO_TO_THREE']),
        'd3':{'M':result.emotion.score,'previous_scores':[0,0,70],'d':result.emotion.readings['d'],
              'state':result.emotion.state,'history_selection':result.emotion.history_selection},
        'hash_contract':{'raw_bundle_hash':result.raw_bundle_hash,'effective_input_hash':result.effective_input_hash,
            'semantic_hash':result.semantic_hash,'hash_contract_version':result.hash_contract_version,
            'semantic_hash_version':result.semantic_hash_version,'changed_clock_semantic_hash':noisy.semantic_hash,
            'changed_clock_effective_input_hash':noisy.effective_input_hash},
        'files':[{'path':p,'size':(root/p).stat().st_size,'sha256':hashlib.sha256((root/p).read_bytes()).hexdigest()} for p in files],
        'actual_market_data_or_edge_claim':False,'market_edge':'NOT_ESTABLISHED'}
    destination=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else review/'evidence/strategy-engine-probe.json'
    with destination.open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2,allow_nan=False)
    print(json.dumps({'output':str(destination),'config_hash':config.config_hash,'golden_76_75':report['golden_76_75'],
        'd3':report['d3']['d'],'state':report['d3']['state']},ensure_ascii=True))


if __name__=='__main__':main()
