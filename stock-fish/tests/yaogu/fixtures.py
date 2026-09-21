"""Synthetic, explicitly TEST_FIXTURE. Never registered in normal runtime state."""
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo
from stockfish_core.yaogu.contracts import MarketInput,digest

TZ=ZoneInfo('Asia/Shanghai')
def make_bundle(count=6, *, session_count=85, start_date=date(2026,2,2)):
    days=[];d=start_date
    while len(days)<session_count:
        if d.weekday()<5:days.append(d)
        d+=timedelta(days=1)
    start=datetime.combine(days[0],time(0),TZ)
    def clocks(stamp):return {'event_time':stamp,'source_published_at':stamp,'first_seen_at':stamp,'ingested_at':stamp}
    securities=[];bars=[]; ids=[f'TEST{i:03d}' for i in range(count)]
    last=len(days)-1
    patterns={0:{last},1:{last-1,last},2:{last-2,last-1,last},3:{last-4,last-2},4:{last-2,last-1}}
    for number,iid in enumerate(ids):
        securities.append(dict(instrument_id=iid,exchange='SSE',board='MAIN',security_type='A_COMMON',listed_on=days[0],effective_from=days[0],st=False,suspended=False,name=iid,source_refs=['fixture-master'],**clocks(start)))
        prev=Decimal('10.00')
        hits=patterns.get(number,{84} if number>=6 else set())
        for index,day in enumerate(days):
            limit=(prev*Decimal('1.10')).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
            lower=(prev*Decimal('.90')).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
            close=limit if index in hits else (prev*Decimal('1.01')).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
            opened=prev
            high=close
            low=prev
            stamp=datetime.combine(day,time(15),TZ)
            bars.append(dict(instrument_id=iid,session=day,open=opened,high=high,low=low,close=close,reference_prev_close=prev,
                             volume_shares=1000000,amount_cny=20000000,limit_up=limit,limit_down=lower,limit_applicable=True,limit_verified=True,rule_id='TEST_FIXTURE_10_PERCENT',
                             comparable_open=opened,comparable_high=high,comparable_low=low,comparable_close=close,comparable_basis='TEST_FIXTURE_NO_ACTION',
                             turnover_fraction=.08,turnover_basis='TEST_FIXTURE_verified_float',source_refs=['fixture-bars'],**clocks(stamp)))
            prev=close
    theme=dict(theme_id='TEST_THEME',name='Synthetic registered event theme',theme_type='EVENT',member_ids=ids,effective_from=days[0],definition_version='TEST_FIXTURE_V1',source_refs=['fixture-theme'],**clocks(start))
    receipts=[dict(receipt_id=rid,provider='TEST_FIXTURE',dataset=rid,content_hash=digest(payload),field_version='TEST_FIXTURE_V1',row_count=len(payload),pagination_complete=True,status='COMPLETE',**clocks(start)) for rid,payload in [('fixture-master',securities),('fixture-bars',bars),('fixture-theme',[theme])]]
    bundle=MarketInput.model_validate(dict(observation_date=days[-1],sessions=days,universe=dict(snapshot_id='TEST_FIXTURE_UNIVERSE',securities=securities,complete=True,expected_count=count,source_refs=['fixture-master'],**clocks(start)),bars=bars,themes=[theme],receipts=receipts,coverage_type='TEST_FIXTURE'))
    return bundle,datetime.combine(days[-1],time(20,30),TZ)
