"""Explicit TEST_FIXTURE context; never used by product or real adapters."""
from datetime import date
from stockfish_core.yaogu.contracts import SourceReceipt,digest

VERSION='FUNDS_EVIDENCE_V1'
PROVIDER='TEST_FIXTURE'
MONEY='TEST_FIXTURE_LARGE_DEFINITION'
LHB='LHB_DISCLOSED_SEATS_V1'
TURNOVER='EXCHANGE_TURNOVER_RAW_CNY'


def clocks(t):return {k:t.isoformat() for k in ('event_time','source_published_at','first_seen_at','ingested_at')}


def funding(iid,sessions,cutoff,net=10):
    days=tuple(d if isinstance(d,date) else date.fromisoformat(d) for d in sessions)
    window=days[-3:]
    common=dict(instrument_id=iid,provider=PROVIDER,definition_version=VERSION,currency='CNY',amount_unit='CNY',**clocks(cutoff))
    def receipt(role,definition):
        rid='TEST_FIXTURE_'+role+'_'+iid
        return SourceReceipt(receipt_id=rid,provider=PROVIDER,dataset=role,field_version=VERSION,
            definition_version=VERSION,measurement_definition=definition,source_type=role,
            instrument_ids=(iid,),business_sessions=window,currency='CNY',amount_unit='CNY',
            content_hash=digest((iid,role)),row_count=3,pagination_complete=True,status='COMPLETE',**clocks(cutoff))
    sources=(receipt('MONEY_FLOW',MONEY),receipt('LHB_UNIVERSE',LHB),
             receipt('LHB_DISCLOSURE',LHB),receipt('WINDOW_TURNOVER',TURNOVER))
    base=common|dict(source_refs=[sources[0].receipt_id],money_definition=MONEY)
    universe=common|dict(source_refs=[sources[1].receipt_id],session=window[-1].isoformat(),
        queried_instrument_ids=[iid],disclosed_instrument_ids=[])
    evidence=base|dict(money_status='COMPLETE',money_sessions=[d.isoformat() for d in window],
        money_rows=[base|dict(session=d.isoformat(),net_large_cny=net,amount_cny=100.) for d in window],
        disclosure_complete=True,disclosure_status='NOT_DISCLOSED',disclosures=[],disclosure_universe=universe)
    return evidence,sources


def with_disclosure(evidence,sources,*,window=None):
    import copy
    e=copy.deepcopy(evidence)
    window=tuple(window or e['money_sessions'][-1:])
    common={k:e[k] for k in ('instrument_id','provider','definition_version','currency','amount_unit',
                            'event_time','source_published_at','first_seen_at','ingested_at')}
    base=common|dict(session=window[-1],window_start=window[0],window_end=window[-1],window_sessions=list(window))
    e.update(disclosure_status='DISCLOSED',lhb_provider=PROVIDER,disclosures=[base|dict(
        seat_id='TEST_FIXTURE_SEAT',seat_name='TEST_FIXTURE_PUBLIC_SEAT',reason='TEST_FIXTURE_REASON',
        buy_cny=20,sell_cny=10,source_refs=[sources[2].receipt_id])],window_amounts=[base|dict(
        amount_cny=100,amount_definition=TURNOVER,source_refs=[sources[3].receipt_id])])
    return e


def funds_context(sessions,cutoff,iid='000001.SZ'):
    e,r=funding(iid,sessions,cutoff)
    return e,dict(observation_date=sessions[-1],sessions=sessions,receipts=r,coverage_type='TEST_FIXTURE')
