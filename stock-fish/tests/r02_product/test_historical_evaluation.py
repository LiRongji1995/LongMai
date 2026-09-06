"""Golden study-denominator and experiment-boundary tests; all TEST_FIXTURE."""
from datetime import date, datetime, time, timedelta
import json

import pytest

from stockfish_core.yaogu.contracts import FeatureValue
from stockfish_core.yaogu.config import StrategyConfig
from stockfish_core.yaogu.evaluation import (StudyConfig, evaluate_study, export_study,
    walk_forward_boundaries)
from stockfish_core.yaogu.outcomes import OutcomeObservation
from tests.r02_product.test_independent_time_boundaries import tiny_fixture, at, SH, DAY


def sessions(n=220):
    values=[]
    day=date(2025,1,2)
    while len(values)<n:
        if day.weekday()<5:
            values.append(day)
        day+=timedelta(days=1)
    return tuple(values)


def observation(run_id,iid,family,day,horizon=1,success=True,*,status='MATURE',price=.02,end=None):
    end=end or day+timedelta(days=horizon)
    return OutcomeObservation(observation_id=f'{run_id}/{iid}/{horizon}',run_id=run_id,instrument_id=iid,
        family=family,horizon=horizon,observation_date=day,label_end=end,evaluated_at=at(end,16),
        coverage_type='TEST_FIXTURE',status=status,structure_success=success,
        structure_label='TEST_FIXTURE_STRUCTURAL_TARGET',future_three_session_limit_up=None,
        metrics={'close_reference_change':price,'mae_bound':-.03,'mfe_bound':.05},
        session_states=({'session':end.isoformat(),'status':'OBSERVED','close_limit_up':success,
                         'one_price_up':False},),reason_codes=(),evidence_refs=('TEST_FIXTURE',),input_hash='TEST_FIXTURE')


def study_fixture(days=3,family='ONE_TO_TWO'):
    prototype,_=tiny_fixture(family)
    strategy=StrategyConfig()
    runs,outcomes=[],[]
    for index in range(days):
        day=DAY+timedelta(days=index)
        run_id=f'TEST_FIXTURE_RUN_{family}_{index}'
        items=[]
        for n,score in enumerate((80,70,60)):
            iid=f'60000{n}.SH'
            components={k:float(score) for k in 'BVTLGM'}
            def f(value):
                return FeatureValue(value=value,unit='TEST_FIXTURE',window='fixture',as_of=at(day))
            items.append(prototype.items[0].model_copy(update={'instrument_id':iid,'score':float(score),
                'daily_score':float(score),'family_rank':n+1,'components':components,
                'weights':strategy.weights[family].model_dump(),
                'features':{'streak_exchange':f(n+1),'ret5':f(.1*n),'amount_ratio_5':f(3-n)},
                'tier':'FOCUS' if n==0 else 'WATCH'}))
            outcomes.append(observation(run_id,iid,family,day,success=n!=1))
        runs.append(prototype.model_copy(update={'run_id':run_id,'observation_date':day,
            'generated_at':at(day,20,30),'decision_cutoff':at(day,20,30),'items':tuple(items),
            'strategy_id':strategy.strategy_id,'config_hash':strategy.config_hash,
            'display_ids':tuple(i.instrument_id for i in items),'focus_ids':(items[0].instrument_id,)}))
    return runs,outcomes


def base_summary(result,method='RULE_SCORE_TOP_K',family='ONE_TO_TWO'):
    return next(s for s in result['report']['group_summaries'] if s['method']==method
        and s['dimensions']['family']==family and len(s['dimensions'])==4)


def test_all_comparisons_and_exact_random_repetitions_are_retained():
    runs,outcomes=study_fixture()
    cfg=StudyConfig(top_k=1,bootstrap_repetitions=100)
    cutoff=at(DAY+timedelta(days=10),16)
    result=evaluate_study(runs,outcomes,cfg,cutoff)
    assert len(result['samples'])==9
    randoms=[s for s in result['comparison_selections'] if s['method']=='FIXED_RANDOM']
    assert len(randoms)==3*100
    assert {r['repetition'] for r in randoms}==set(range(100))
    assert all(len(row['selected_ids'])==1 for row in randoms)
    assert all(row['eligible_count']==3 for row in randoms)
    assert base_summary(result)['structure_hit_rate']==1
    assert base_summary(result,'EQUAL_WEIGHT_ALL')['structure_hit_rate']==pytest.approx(2/3)
    assert base_summary(result)['structure_lift_pp_vs_family_all']==pytest.approx(100/3)
    shuffled=evaluate_study(list(reversed(runs)),outcomes,cfg,cutoff)
    assert result['comparison_selections']==shuffled['comparison_selections']
    assert len(result['report']['random_repetition_summaries'])==100
    assert result['report']['market_edge']=='NOT_ESTABLISHED'
    assert 'total_win_rate' not in result['report']
    random_summary=base_summary(result,'FIXED_RANDOM')
    assert random_summary['selected_exposures']==3
    assert random_summary['date_clusters']==3
    interval=base_summary(result)['structure_interval']
    assert interval['date_clusters']==3 and interval['bootstrap_repetitions']==100
    assert interval['base_seed']==20260905 and interval['status']=='SMALL_DATE_SAMPLE'


def test_maturity_and_censoring_keep_independent_metric_denominators():
    runs,outcomes=study_fixture(days=1)
    outcomes[0]=outcomes[0].model_copy(update={'status':'CENSORED','metrics':{'close_reference_change':None}})
    outcomes[1]=outcomes[1].model_copy(update={'status':'NOT_MATURED','structure_success':None,
                                            'metrics':{'close_reference_change':None}})
    outcomes=outcomes[:2]  # third candidate remains in the sample table as unknown
    result=evaluate_study(runs,outcomes,StudyConfig(bootstrap_repetitions=100),at(DAY+timedelta(days=10),16))
    summary=base_summary(result,'EQUAL_WEIGHT_ALL')
    assert summary['selected_exposures']==3
    assert summary['structure_known_exposures']==1
    assert summary['structure_hit_rate']==1
    assert summary['price']['1']['known_price_exposures']==0
    assert summary['structure_status_exposures']=={'CENSORED':1,'NOT_MATURED':1,'UNAVAILABLE':1}
    assert summary['structure_interval']['lower'] is None
    assert summary['structure_interval']['status']=='INSUFFICIENT_DATE_CLUSTERS'


def test_three_session_family_h1_maturity_is_not_structure_denominator():
    runs,outcomes=study_fixture(days=1,family='TREND_NM')
    outcomes=[o.model_copy(update={'structure_success':None}) for o in outcomes]
    result=evaluate_study(runs,outcomes,StudyConfig(bootstrap_repetitions=100),at(DAY+timedelta(days=10),16))
    summary=base_summary(result,'EQUAL_WEIGHT_ALL','TREND_NM')
    assert summary['structure_known_exposures']==0
    assert summary['structure_hit_rate'] is None
    assert summary['price']['1']['known_price_exposures']==3
    assert result['report']['status']=='PARTIAL'


def test_future_revision_and_wrong_identity_cannot_enter_old_evaluation():
    runs,outcomes=study_fixture(days=1)
    cutoff=at(DAY+timedelta(days=2),16)
    future=outcomes[0].model_copy(update={'observation_id':'FUTURE','evaluated_at':at(DAY+timedelta(days=3),16),
                                       'structure_success':False})
    cfg=StudyConfig(bootstrap_repetitions=100)
    a=evaluate_study(runs,outcomes,cfg,cutoff)
    b=evaluate_study(runs,[*outcomes,future],cfg,cutoff)
    assert a['samples']==b['samples']
    assert a['report']['group_summaries']==b['report']['group_summaries']
    wrong=outcomes[0].model_copy(update={'family':'TREND_NM'})
    with pytest.raises(ValueError,match='identity mismatch'):
        evaluate_study(runs,[wrong,*outcomes[1:]],cfg,cutoff)


def test_missing_single_factor_does_not_select_observed_winners():
    runs,outcomes=study_fixture(days=1)
    item=runs[0].items[0]
    partial=item.model_copy(update={'features':{k:v for k,v in item.features.items() if k!='ret5'}})
    runs[0]=runs[0].model_copy(update={'items':(partial,*runs[0].items[1:])})
    result=evaluate_study(runs,outcomes,StudyConfig(bootstrap_repetitions=100),at(DAY+timedelta(days=10),16))
    assert not any(s['method']=='RET5_ONLY' for s in result['comparison_selections'])
    missing=result['report']['missing_factor_comparisons']
    assert missing[0]['method']=='RET5_ONLY' and missing[0]['missing_ids']==['600000.SH']


def test_sensitivity_is_preregistered_plus_minus_ten_percent_and_never_deployed(tmp_path):
    runs,outcomes=study_fixture(days=1)
    before=runs[0].model_dump(mode='json')
    result=evaluate_study(runs,outcomes,StudyConfig(bootstrap_repetitions=100),at(DAY+timedelta(days=10),16))
    variants=result['report']['sensitivity']
    assert len(variants)==6*2+4*2
    assert {row['relative_change'] for row in variants}=={-.10,.10}
    assert all(row['deployed'] is False for row in variants)
    assert all(sum(row['weights'].values())==pytest.approx(100) for row in variants if 'weights' in row)
    assert runs[0].model_dump(mode='json')==before
    exported=export_study(result,tmp_path/'study')
    assert {f['path'] for f in exported['files']}=={
        'report.json','samples.json','comparison_selections.json','outcome_revisions.json','samples.csv','comparison_selections.csv'}
    assert len(json.loads((tmp_path/'study/samples.json').read_text(encoding='utf-8')))==3
    with pytest.raises(FileExistsError):
        export_study(result,tmp_path/'study')


def test_actual_label_end_purge_on_train_validation_and_test_boundaries():
    calendar=sessions(200)
    rows=[observation('TEST_FIXTURE',f'60000{i}.SH','ONE_TO_TWO',calendar[index],success=True,end=calendar[end])
          for i,(index,end) in enumerate(((118,119),(119,120),(158,159),(159,160),(170,171),(179,180)))]
    result=walk_forward_boundaries(calendar,rows,evaluated_at=at(calendar[-1],16))
    assert len(result['folds'])==2
    first=result['folds'][0]['partitions']
    assert first['train']['kept_ids']==[rows[0].observation_id]
    assert first['train']['purged'][0]['observation_id']==rows[1].observation_id
    assert first['valid']['kept_ids']==[rows[2].observation_id]
    assert first['valid']['purged'][0]['observation_id']==rows[3].observation_id
    assert first['test']['kept_ids']==[rows[4].observation_id]
    assert first['test']['purged'][0]['reason']=='LABEL_AFTER_TEST_WINDOW'
    assert result['trials_fitted']==0 and result['deployed'] is False
    assert result['folds'][1]['partitions']['train']['start']==calendar[20].isoformat()


def test_empty_study_is_unavailable_and_exports_no_fake_rows(tmp_path):
    result=evaluate_study([],[],StudyConfig(bootstrap_repetitions=100),at(DAY,16))
    assert result['report']['status']=='UNAVAILABLE'
    assert result['samples']==[] and result['comparison_selections']==[]
    assert result['report']['full_candidate_sample_count']==0
    export_study(result,tmp_path/'empty')
    assert (tmp_path/'empty/samples.csv').read_text(encoding='utf-8-sig').strip()=='status'
