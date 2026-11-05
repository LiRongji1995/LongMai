"""Independent arithmetic examples, all TEST_FIXTURE, no market claims."""
from datetime import datetime,time,timedelta
from decimal import Decimal as D
from fractions import Fraction as F
import math
import pytest
from tests.yaogu.fixtures import make_bundle,TZ
from stockfish_core.yaogu.strategy_experiments import (
    run_pv_observation,run_pv_focus_preview,
    run_strategy_experiments,implementation_conformance,
)

def golden_pv():
    b,t=make_bundle(3);bars=[]
    for old in b.bars:
        i=int(old.instrument_id[-3:]);current=old.session==b.observation_date
        c=D([13,11,12][i]) if current else D(10)
        low=c-1 if current else c;high=low+D(['2','3','1'][i]) if current else c
        bars.append(old.model_copy(update=dict(open=low,close=c,low=low,high=high,reference_prev_close=D(10),
            comparable_open=low,comparable_close=c,comparable_low=low,comparable_high=high,
            amount_cny=D([40,60,80][i] if current else 20),limit_verified=False)))
    return b.model_copy(update={'bars':tuple(bars)}),t

def test_pv_independent_three_row_golden_and_coverage_before_screen():
    b,t=golden_pv();result=run_pv_observation(b,cutoff=t)
    rows={r['instrument_id']:r for r in result['items']}
    # PRs: A=(1/6,1/2,5/6); R=(5/6,1/6,1/2); H=(1/2,5/6,1/6).
    expected={'TEST000':F(140,3),'TEST001':F(45),'TEST002':F(175,3)}
    assert result['top_ids']==['TEST002','TEST000','TEST001']
    for iid,score in expected.items():assert rows[iid]['S_PV']==pytest.approx(float(score),abs=5e-7)
    assert result['counts']['field_coverage']==1
    # Zero eligible screen matches is a real EMPTY; required fields still complete.
    plain=b.model_copy(update={'bars':tuple(x.model_copy(update={'amount_cny':D(20)}) for x in b.bars)})
    empty=run_pv_observation(plain,cutoff=t)
    assert empty['status']=='EMPTY' and empty['counts']['evaluated']==3 and empty['counts']['filter_passed']==0

def test_focus_never_uses_rounded_factor_or_quota():
    row=dict(instrument_id='TEST',rank=999,A=2,R=.1,open=10,close=11,low=10,high=11,S_PV=50)
    pv={'items':[{**row,'instrument_id':f'TEST{i:03}'} for i in range(100)]}
    assert len(run_pv_focus_preview(pv,max_count=1)['selected_ids'])==100
    row['exact_factors']={'A':{'numerator':199999999999999999,'denominator':100000000000000000}}
    assert not run_pv_focus_preview({'items':[row]})['selected_ids']
    row.pop('exact_factors');row.update(high=11,low=11,open=11)
    result=run_pv_focus_preview({'items':[row]})
    assert 'NOT_APPLICABLE_ZERO_RANGE' in result['items'][0]['reason_codes']








def test_run_identity_cutoff_and_legacy_assessment_do_not_mutate():
    b,t=golden_pv();a=run_strategy_experiments(b,cutoff=t);z=run_strategy_experiments(b,cutoff=t+timedelta(minutes=1))
    assert a['run_id']!=z['run_id']
    assert a['results']['PV_OBSERVATION_V1']['items']==z['results']['PV_OBSERVATION_V1']['items']
    for key in ('source_hash','spec_hash','input_hash','universe_hash','frozen_daily_config_hash'):assert len(a[key])==64
    old={'run_id':'OLD','implementation_version':'R05_STRATEGY_EXPERIMENTS_V2'};copy=old.copy()
    assert implementation_conformance(old)['status']=='SPEC_NONCONFORMANT' and old==copy
    assert implementation_conformance(a)['status']=='SPEC_CONFORMANT'
    assert implementation_conformance({**a,'source_hash':None})['status']=='SPEC_NOT_VERIFIED'

def test_canonical_json_roundtrip_preserves_qualification_and_run_identity():
    from stockfish_core.yaogu.contracts import MarketInput, digest
    b,t=golden_pv()
    # JSON changes timezone implementation types without changing their instants.
    restored=MarketInput.model_validate_json(b.model_dump_json())
    assert type(b.universe.securities[0].event_time.tzinfo) is not type(restored.universe.securities[0].event_time.tzinfo)
    assert digest(b)==digest(restored)
    original=run_strategy_experiments(b,cutoff=t)
    replayed=run_strategy_experiments(restored,cutoff=t)
    for key in ('input_hash','source_hash','spec_hash','universe_hash','run_id'):
        assert original[key]==replayed[key]
    assert original['qualification']==replayed['qualification']
    assert original['qualification']['selected_security_hash']==digest({s.instrument_id:s.model_dump(mode='json') for s in b.universe.securities})
    for strategy in ('PV_OBSERVATION_V1','STAGE_AWARE_MOMENTUM_V2'):
        assert original['results'][strategy]['items']==replayed['results'][strategy]['items']
    assert original['selection_results']==replayed['selection_results']

def test_frozen_original_spec_edit_rejected_without_silent_same_name_change(tmp_path,monkeypatch):
    import stockfish_core.yaogu.strategy_experiments as module
    path=tmp_path/module.SPEC_PATH.name
    path.write_bytes(module.SPEC_PATH.read_bytes().replace(b'percentile_A: 0.45',b'percentile_A: 0.40'))
    monkeypatch.setattr(module,'SPEC_PATH',path)
    with pytest.raises(ValueError,match='R05_FROZEN_SPEC_CHANGED_REQUIRES_NEW_VERSION'):
        module.source_identity()

@pytest.mark.parametrize('kind',['security','bar'])
def test_same_clock_conflicting_revisions_never_depend_on_input_order(kind):
    b,t=golden_pv()
    if kind=='security':
        a=b.universe.securities[0];b=b.model_copy(update={'universe':b.universe.model_copy(update={'securities':(*b.universe.securities,a.model_copy(update={'st':True}))})})
        assert 'TEST000' not in run_pv_observation(b,cutoff=t)['top_ids']
    elif kind=='bar':
        a=b.bars[-1];b=b.model_copy(update={'bars':(*b.bars,a.model_copy(update={'amount_cny':D(100)}))})
        assert a.instrument_id not in run_pv_observation(b,cutoff=t)['top_ids']
