"""Seven external expectations, adapted only to R03 authoritative input APIs.

Original evidence remains untouched at review/r03-realdata-closure/external-evidence/
probes/test_r02_external_regressions.py. Legitimate positive funds now supply the
required receipts/calendar; nonexistent-receipt negative remains nonexistent.
The D3 positive now has explicit TEST_FIXTURE publication/version context.
"""
import copy
from datetime import timedelta
import pytest
from tests.yaogu.fixtures import make_bundle
from tests.r02_independent_strategy.test_adversarial_semantics import market
from tests.r03_evidence.fixtures import funding,with_disclosure
from tests.r03_strategy.test_history_partition_hashes import history
from stockfish_core.yaogu.engine import run
from stockfish_core.yaogu.funds import calculate_funds


def test_known_st_exclusions_are_not_missing_eligible_market_data():
    b,c=make_bundle(50)
    assert run(b,cutoff=c).emotion.score is not None
    secs=tuple(s.model_copy(update={'st':True}) if s.instrument_id in ('TEST040','TEST041','TEST042') else s for s in b.universe.securities)
    r=run(b.model_copy(update={'universe':b.universe.model_copy(update={'securities':secs})}),cutoff=c)
    assert r.emotion.coverage['price_coverage']==1
    assert len(r.excluded)==3
    assert r.emotion.score is not None, 'known exclusion was miscounted as unknown identity coverage'


def test_reconstruction_must_not_relax_receipt_publication_time():
    b,c=make_bundle();future=c+timedelta(days=1)
    clocks={k:future for k in ('event_time','source_published_at','first_seen_at','ingested_at')}
    receipts=tuple(r.model_copy(update=clocks) if r.receipt_id=='fixture-theme' else r for r in b.receipts)
    r=run(b.model_copy(update={'coverage_type':'RECONSTRUCTED_HISTORICAL','receipts':receipts}),cutoff=c)
    assert all(i.components['T'] is None for i in r.items), 'future-published source receipt supported historical theme scores'


def test_delta_is_three_prior_exchange_session_mean_not_yesterday_only():
    b,c=market(count=50,patterns={0:{69},**{i:{68} for i in range(1,11)}})
    r=run(b.model_copy(update={'previous_emotions':history(b)}),cutoff=c)
    expected=r.emotion.score-70/3
    assert r.emotion.readings['d']==pytest.approx(expected), 'R02 master specification §10 requires three-day mean'
    assert r.emotion.state=='FERMENT'


def test_funds_cannot_use_unregistered_receipt_in_real_rule_engine():
    b,c=make_bundle();base=run(b,cutoff=c)
    rows=tuple(funding(i.instrument_id,b.sessions,c)[0] for i in base.items)
    for f in rows:
        f['source_refs']=['UNREGISTERED_RECEIPT']
    r=run(b.model_copy(update={'funds':rows}),cutoff=c,profile='FUNDS_V1')
    assert all(i.score is None for i in r.items), 'FUNDS_V1 accepted nonexistent source references'


def test_three_session_funds_window_rejects_decades_apart_dates():
    b,c=make_bundle();f,receipts=funding('TEST000',b.sessions,c)
    ctx={'sessions':b.sessions,'receipts':receipts,'coverage_type':'TEST_FIXTURE'}
    assert calculate_funds(f,c,b.observation_date,**ctx)['status']=='AVAILABLE'
    f['money_sessions']=['2000-01-03','2010-01-04',str(b.observation_date)]
    for row,d in zip(f['money_rows'],f['money_sessions']):row['session']=d
    assert calculate_funds(f,c,b.observation_date,**ctx)['status']=='UNAVAILABLE', 'three ascending dates are not last three exchange sessions'


def test_future_disclosure_window_is_not_current_funds_evidence():
    b,c=make_bundle();f,receipts=funding('TEST000',b.sessions,c);future=(c+timedelta(days=1)).date().isoformat()
    ctx={'sessions':b.sessions,'receipts':receipts,'coverage_type':'TEST_FIXTURE'}
    assert calculate_funds(f,c,b.observation_date,**ctx)['status']=='AVAILABLE'
    f=with_disclosure(f,receipts,window=(future,))
    assert calculate_funds(f,c,b.observation_date,**ctx)['status']=='UNAVAILABLE'


def test_declared_input_hash_covers_score_driving_funds():
    b,c=make_bundle();items=run(b,cutoff=c).items;pairs=[funding(i.instrument_id,b.sessions,c,net=5) for i in items]
    b=b.model_copy(update={'funds':tuple(p[0] for p in pairs),'receipts':(*b.receipts,*(r for p in pairs for r in p[1]))})
    a=run(b,cutoff=c,profile='FUNDS_V1')
    changed=copy.deepcopy(b.funds)
    for f in changed:
        for x in f['money_rows']:x['net_large_cny']=-5
    z=run(b.model_copy(update={'funds':changed}),cutoff=c,profile='FUNDS_V1')
    assert a.items[0].score != z.items[0].score
    assert a.input_hash != z.input_hash, 'input_hash omits funds (full service input_id remains separately recoverable)'
