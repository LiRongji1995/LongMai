"""Regression for the real full-master quadratic scan, without wall-time gates."""
from datetime import timedelta
from time import perf_counter
from stockfish_core.yaogu.contracts import digest
from stockfish_core.yaogu.engine import HistoricalSecurityIndex,security_at,run
from stockfish_core.yaogu.visibility import EvidenceResolver
from tests.yaogu.fixtures import make_bundle


def test_interval_index_matches_original_asof_and_source_scope():
    b,c=make_bundle();base=b.universe.securities[0]
    end=b.sessions[-2]
    rows=(base.model_copy(update={'effective_to':end}),
          base.model_copy(update={'effective_from':end,'st':True}),
          base.model_copy(update={'effective_from':b.observation_date+timedelta(days=1),'suspended':True}))
    resolver=EvidenceResolver(b.receipts,cutoff=c,coverage_type=b.coverage_type,sessions=b.sessions,observation_date=b.observation_date)
    index=HistoricalSecurityIndex(rows,resolver)
    for day in b.sessions[-22:]:
        assert index.at(base.instrument_id,day)==security_at(rows,base.instrument_id,day,c)
    assert index.at('MISSING',b.observation_date) is None
    # A source scope is a real authority boundary, not an index optimization hint.
    refs=tuple(r.model_copy(update={'business_sessions':(b.observation_date,)}) if r.receipt_id=='fixture-master' else r for r in b.receipts)
    limited=HistoricalSecurityIndex(rows,EvidenceResolver(refs,cutoff=c,coverage_type=b.coverage_type,sessions=b.sessions))
    assert limited.at(base.instrument_id,end) is None
    assert limited.at(base.instrument_id,b.observation_date).st is True


def test_3000_unknown_identities_22_daily_bars_do_not_construct_millions_of_unused_events(monkeypatch,record_testsuite_property):
    import stockfish_core.yaogu.engine as engine
    b,c=make_bundle();template=b.universe.securities[0]
    records=tuple(template.model_copy(update={'instrument_id':f'TEST_LARGE_{i:04d}','delisting_period':None}) for i in range(3000))
    templates=[x for x in b.bars if x.instrument_id=='TEST000' and x.session>=b.sessions[-22]]
    bars=tuple(row.model_copy(update={'instrument_id':s.instrument_id}) for s in records for row in templates)
    large=b.model_copy(update={'universe':b.universe.model_copy(update={'securities':records,'expected_count':3000}),
        'bars':bars,'themes':()})
    event_calls=[];real=engine.limit_event
    def counted(*a,**k):event_calls.append(1);return real(*a,**k)
    monkeypatch.setattr(engine,'limit_event',counted)
    start=perf_counter();result=run(large,cutoff=c);elapsed=perf_counter()-start
    record_testsuite_property('full_master_identities',3000);record_testsuite_property('full_master_daily_bar_rows',len(bars));record_testsuite_property('full_master_elapsed_seconds',elapsed)
    assert len(bars)==66000
    assert result.status=='UNAVAILABLE' and not result.items
    assert result.coverage['master_expected']==3000 and result.coverage['eligibility_unknown']==3000
    assert len(result.excluded)==3000
    # Unknown eligibility is still a denominator blocker. No scoring consumer
    # needs millions of UNKNOWN events for securities already outside its pool.
    assert not event_calls


def test_unrelated_excluded_prices_are_archived_but_not_effective_scoring_dependencies():
    b,c=make_bundle();outside=b.universe.securities[0].model_copy(update={'instrument_id':'TEST_OUTSIDE','board':'CHINEXT'})
    extra=tuple(x.model_copy(update={'instrument_id':outside.instrument_id}) for x in b.bars if x.instrument_id=='TEST000')
    b=b.model_copy(update={'universe':b.universe.model_copy(update={'securities':(*b.universe.securities,outside),'expected_count':7}),'bars':(*b.bars,*extra)})
    a=run(b,cutoff=c)
    changed=tuple(x.model_copy(update={'amount_cny':x.amount_cny*2}) if x.instrument_id==outside.instrument_id else x for x in b.bars)
    z=run(b.model_copy(update={'bars':changed}),cutoff=c)
    assert a.raw_bundle_hash!=z.raw_bundle_hash
    assert a.effective_input_hash==z.effective_input_hash and a.semantic_hash==z.semantic_hash
    assert a.coverage['known_excluded']==1 and a.coverage['eligible']==6


def test_outcome_only_records_do_not_enlarge_current_master_partition():
    b,c=make_bundle();a=run(b,cutoff=c)
    old=b.universe.securities[0].model_copy(update={'instrument_id':'TEST_OLD_OUTCOME','board':'UNKNOWN','risk_flags':('OUTCOME_ONLY',)})
    z=run(b.model_copy(update={'universe':b.universe.model_copy(update={'securities':(*b.universe.securities,old)})}),cutoff=c)
    assert z.coverage['master_expected']==z.coverage['identity_observed']==6
    assert z.emotion.score==a.emotion.score and [i.instrument_id for i in z.items]==[i.instrument_id for i in a.items]


def test_timely_archived_parent_allows_late_historical_row_but_cached_identity_not_future():
    b,c=make_bundle();late=b.universe.securities[0].model_copy(update={'first_seen_at':c+timedelta(hours=1),'ingested_at':c+timedelta(hours=1)})
    resolver=EvidenceResolver(b.receipts,cutoff=c,coverage_type='RECONSTRUCTED_HISTORICAL',sessions=b.sessions)
    assert resolver.resolve(late,instrument_id=late.instrument_id,session=b.observation_date).allowed
    index=HistoricalSecurityIndex((late,),resolver)
    assert index.at(late.instrument_id,b.observation_date)==late
    assert index.at(late.instrument_id,b.observation_date+timedelta(days=1)) is None
