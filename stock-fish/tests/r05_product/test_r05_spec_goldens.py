"""Independent arithmetic examples, all TEST_FIXTURE, no market claims."""
from datetime import datetime,time,timedelta
from decimal import Decimal as D
from fractions import Fraction as F
import math
import pytest
from tests.yaogu.fixtures import make_bundle,TZ
from stockfish_core.yaogu.strategy_experiments import (
    run_pv_observation,run_pv_focus_preview,run_dna_rerank,run_theme_context,
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

def dna_bundle():
    b,_=make_bundle(3);days=[];d=b.sessions[0]
    while len(days)<252:
        if d.weekday()<5:days.append(d)
        d+=timedelta(days=1)
    bars=[];base=b.bars[0]
    attempts=[{5,15,25},{5,15,25,35},{5,15,25}]
    sealed=[{5,15,25},{5,15,25},{5}]
    for i in range(3):
        for k,day in enumerate(days):
            close=D(11) if k in sealed[i] else D(10)
            if k-1 in attempts[i]:close=D('10.8') if i==0 else D('10.2') if i==1 else D('10.9')
            opened=D('10.4') if k-1 in attempts[i] and i==0 else D('10.1') if k-1 in attempts[i] and i==1 else D(10)
            high=max(close,opened,D(11) if k in attempts[i] else D(10));low=min(opened,close,D(10))
            clock=datetime.combine(day,time(15),TZ)
            bars.append(base.model_copy(update=dict(instrument_id=f'TEST{i:03}',session=day,open=opened,high=high,low=low,close=close,
                reference_prev_close=D(10),comparable_open=opened,comparable_high=high,comparable_low=low,comparable_close=close,
                amount_cny=D(40 if k==251 else 20),limit_up=D(11),limit_down=D(9),
                event_time=clock,source_published_at=clock,first_seen_at=clock,ingested_at=clock)))
    # Current signal prices rise from T-5, but current T limit count is distinct from mature events.
    bars=[x.model_copy(update={'close':D(11),'high':D(11),'comparable_close':D(11),'comparable_high':D(11)}) if x.session==days[-1] else x for x in bars]
    return b.model_copy(update={'sessions':tuple(days),'observation_date':days[-1],'bars':tuple(bars)}),datetime.combine(days[-1],time(20,30),TZ)

def independent_wilson(s,n):
    z=1.959963984540054;p=s/n
    return (p+z*z/(2*n)-z*math.sqrt(p*(1-p)/n+z*z/(4*n*n)))/(1+z*z/n)

def test_dna_independent_counts_prior_factor_percentiles_and_6040_golden():
    b,t=dna_bundle();pv=run_pv_observation(b,cutoff=t);r=run_dna_rerank(b,pv,cutoff=t)
    rows={x['instrument_id']:x for x in r['items']};a=rows['TEST000'];z=rows['TEST001']
    assert a['status']==z['status']=='EVALUATED'
    assert rows['TEST002']['status']=='INSUFFICIENT_COVERAGE'
    assert a['sample_counts']=={'close_limit_n':3,'first_attempt_n':3,'first_sealed_n':3,'open_premium_n':3}
    assert z['sample_counts']['open_premium_n']==4 # includes the failed seal attempt
    assert z['sample_counts']['first_attempt_n']==4
    assert a['factors']['LIMIT_COUNT_250D']==4 # includes T, not just mature events
    assert a['factors']['LB_PREMIUM5']==pytest.approx(independent_wilson(3,3),abs=5e-7)
    assert z['factors']['LB_PREMIUM5']==0
    # Event weighted pool=(3*.04+4*.01)/7; ineligible TEST002 excluded.
    pool=F(16,700)
    assert a['m_pool']==pytest.approx(float(pool),abs=5e-7)
    assert a['factors']['M_ADJ']==pytest.approx(float((F(12,100)+5*pool)/8),abs=5e-7)
    assert z['factors']['M_ADJ']==pytest.approx(float((F(4,100)+5*pool)/9),abs=5e-7)
    # A wins premium/seal/mean; red and count tied: G_A=.25*75+.25*50+.25*75+.15*75+.10*50=66.25.
    assert a['G']==66.25 and z['G']==33.75
    assert a['S_DNA']==56.5 and z['S_DNA']==43.5 # tied PV cohort score=50
    assert a['original_S_PV']==pv['items'][0]['S_PV']

def test_dna_unknown_next_reference_excluded_from_mature_denominators():
    b,t=dna_bundle()
    b=b.model_copy(update={'bars':tuple(x.model_copy(update={'reference_prev_close':None})
        if x.instrument_id=='TEST000' and x.session==b.sessions[6] else x for x in b.bars)})
    rows=run_dna_rerank(b,cutoff=t)['items'];a=next(r for r in rows if r['instrument_id']=='TEST000')
    assert a['status']=='EVALUATED'
    assert a['sample_counts']=={'close_limit_n':2,'first_attempt_n':3,'first_sealed_n':2,'open_premium_n':2}
    assert a['success_counts']['close_limit_success']==2

def test_future_price_changes_cannot_change_prior_scores_or_denominators():
    b,t=dna_bundle();a=run_strategy_experiments(b,cutoff=t)
    future=b.sessions[-1]+timedelta(days=3);clock=datetime.combine(future,time(15),TZ)
    extra=b.bars[-1].model_copy(update={'session':future,'amount_cny':D('99999999'),'event_time':clock,
        'source_published_at':clock,'first_seen_at':clock,'ingested_at':clock})
    changed=b.model_copy(update={'sessions':(*b.sessions,future),'bars':(*b.bars,extra)})
    z=run_strategy_experiments(changed,cutoff=t)
    for strategy in ('PV_OBSERVATION_V1','DNA_RERANK_V1','THEME_CONTEXT_V1'):
        assert a['results'][strategy]['items']==z['results'][strategy]['items']
    assert a['effective_input_hash']==z['effective_input_hash']

@pytest.mark.parametrize('change',['future','expired','invalid_parent','partial_parent'])
def test_theme_temporal_or_incomplete_parent_evidence_never_authorized(change):
    b,t=make_bundle(8)
    b=b.model_copy(update={'bars':tuple(x.model_copy(update={'amount_cny':D('40000000')}) if x.session==b.observation_date else x for x in b.bars)})
    if change in ('future','expired'):
        updates={'effective_from':b.observation_date+timedelta(days=1)} if change=='future' else {'effective_to':b.observation_date}
        b=b.model_copy(update={'themes':tuple(x.model_copy(update=updates) for x in b.themes)})
    else:
        updates={'status':'INVALID'} if change=='invalid_parent' else {'status':'PARTIAL','pagination_complete':False}
        b=b.model_copy(update={'receipts':tuple(x.model_copy(update=updates) if x.receipt_id=='fixture-theme' else x for x in b.receipts)})
    assert not run_theme_context(b,cutoff=t)['top_ids']
    assert run_pv_observation(b,cutoff=t)['items']

def test_theme_independent_frozen_theme_population_and_daily_up_golden():
    b,t=make_bundle(10);current=b.observation_date;bars=[]
    for old in b.bars:
        i=int(old.instrument_id[-3:]);first=i<5
        # Theme A peers: 5d +10%, current up, current limit. B: 5d +20%, today down.
        c=D(11 if first else 12) if old.session==current else D(13) if old.session==b.sessions[-2] and not first else D(10)
        ref=D(10) if first else D(13) if old.session==current else D(12)
        bars.append(old.model_copy(update=dict(open=c,high=c,low=c,close=c,reference_prev_close=ref,
            comparable_open=c,comparable_high=c,comparable_low=c,comparable_close=c,
            limit_up=D(11) if first else D(15),limit_down=D(9),amount_cny=D(40 if old.session==current else 20))))
    base=b.themes[0];themes=(base.model_copy(update={'theme_id':'A','member_ids':tuple(f'TEST{i:03}' for i in range(5))}),
        base.model_copy(update={'theme_id':'B','member_ids':tuple(f'TEST{i:03}' for i in range(5,10))}))
    b=b.model_copy(update={'bars':tuple(bars),'themes':themes});pv=run_pv_observation(b,cutoff=t)
    # Only two targets enter this external S1 subset, peers must remain all ten qualified stocks.
    pv['items']=[x for x in pv['items'] if x['instrument_id'] in ('TEST000','TEST005')]
    result=run_theme_context(b,pv,cutoff=t);rows={x['instrument_id']:x for x in result['items']}
    a,z=rows['TEST000'],rows['TEST005']
    assert a['peer_expected']==z['peer_expected']==4
    assert a['peer_up_fraction']==1 and z['peer_up_fraction']==0
    assert a['percentile_population_theme_ids']==['A','B']
    # A: .40*75 + .30*25 + .30*75=60, B=40.
    assert a['T']==60 and z['T']==40
    # PV common A=41.25 and B=58.75 (A/H tied, R lower/higher).
    assert a['S_PV_THEME_COHORT']==41.25 and z['S_PV_THEME_COHORT']==58.75
    assert a['S_THEME']==45.9375 and z['S_THEME']==54.0625

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
    for strategy in ('PV_OBSERVATION_V1','DNA_RERANK_V1','THEME_CONTEXT_V1'):
        assert original['results'][strategy]['items']==replayed['results'][strategy]['items']
    assert original['selection_results']==replayed['selection_results']

def test_frozen_original_spec_edit_rejected_without_silent_same_name_change(tmp_path,monkeypatch):
    import stockfish_core.yaogu.strategy_experiments as module
    path=tmp_path/module.SPEC_PATH.name
    path.write_bytes(module.SPEC_PATH.read_bytes().replace(b'percentile_A: 0.45',b'percentile_A: 0.40'))
    monkeypatch.setattr(module,'SPEC_PATH',path)
    with pytest.raises(ValueError,match='R05_FROZEN_SPEC_CHANGED_REQUIRES_NEW_VERSION'):
        module.source_identity()

@pytest.mark.parametrize('kind',['security','bar','theme'])
def test_same_clock_conflicting_revisions_never_depend_on_input_order(kind):
    b,t=golden_pv()
    if kind=='security':
        a=b.universe.securities[0];b=b.model_copy(update={'universe':b.universe.model_copy(update={'securities':(*b.universe.securities,a.model_copy(update={'st':True}))})})
        assert 'TEST000' not in run_pv_observation(b,cutoff=t)['top_ids']
    elif kind=='bar':
        a=b.bars[-1];b=b.model_copy(update={'bars':(*b.bars,a.model_copy(update={'amount_cny':D(100)}))})
        assert a.instrument_id not in run_pv_observation(b,cutoff=t)['top_ids']
    else:
        a=b.themes[0];b=b.model_copy(update={'themes':(*b.themes,a.model_copy(update={'member_ids':()}))})
        assert not run_theme_context(b,cutoff=t)['top_ids']
