"""Frozen-input coverage inventory. Never changes strategy gates or fetches data."""
from collections import Counter,defaultdict
from . import stage_aware_momentum as v2


def diagnose(ctx):
    rows=[];aggregates=defaultdict(Counter);rejections=defaultdict(list)
    for rejected in ctx.rejected_evidence:
        if rejected.get('instrument_id'):rejections[rejected['instrument_id']].append(rejected)
    end=v2._session_index(ctx)
    windows={'amount_T':(1,0,'amount'),'turnover_T':(1,0,'turnover'),'turnover_previous_5':(5,1,'turnover'),
        'comparable_T':(1,0,'price'),'MA20_T':(20,0,'price'),'MA20_T_minus_5':(20,5,'price'),
        'START_prior_5':(5,1,'price'),'ACC_equal_3_plus_3':(7,0,'price'),'ACC_turnover_5':(5,0,'turnover'),
        'pullback_MA250_slope':(255,0,'price'),'close_limit_down_T':(1,0,'limit')}
    for iid in ctx.eligible_ids:
        facts={}
        for field,(count,offset,kind) in windows.items():
            stop=end-offset+1;start=stop-count
            dates=ctx.sessions[max(0,start):max(0,stop)];bars=[v2._bar(ctx,day,iid) for day in dates]
            valid=[];refs=set();bases=set();missing=[]
            for day,bar in zip(dates,bars):
                if bar:
                    refs.update(bar.get('source_refs') or [])
                    if bar.get('comparable_basis'):bases.add(bar['comparable_basis'])
                if kind=='price':ok=bool(bar and bar.get('comparable_close') is not None and bar.get('comparable_basis'))
                elif kind=='amount':ok=v2._amount_fraction(bar) is not None
                elif kind=='turnover':ok=v2._turnover_fraction(bar) is not None
                else:ok=v2._daily_path(ctx,iid).get('close_limit_down') in (v2.TRUE,v2.FALSE)
                (valid if ok else missing).append(day)
            reasons=[]
            if len(dates)<count:reasons.append('CALENDAR_HISTORY_LENGTH_INSUFFICIENT')
            if missing:
                reasons.append('SOURCE_REJECTED_OR_NOT_VISIBLE' if rejections[iid] else 'NOT_PRESENT_IN_FROZEN_INPUT_OR_FIELD_UNAVAILABLE')
            if kind=='price' and len(bases)>1:reasons.append('COMPARABLE_BASIS_MISMATCH')
            state='COMPLETE' if len(valid)==count and not reasons else 'INCOMPLETE'
            aggregates[field][state]+=1;aggregates[field]['valid_observations']+=len(valid);aggregates[field]['expected_observations']+=count
            facts[field]=dict(status=state,valid_count=len(valid),expected_count=count,window_start=dates[0] if dates else None,window_end=dates[-1] if dates else None,
                missing_dates=missing,calendar_missing_count=max(0,count-len(dates)),reason_codes=reasons,source_refs=sorted(refs),basis=sorted(bases))
        rows.append(dict(instrument_id=iid,facts=facts,rejected_evidence=rejections[iid]))
    return dict(contract='R1_COVERAGE_INVENTORY_V2',qualification=ctx.qualification,aggregates={k:dict(v) for k,v in aggregates.items()},items=rows,
        history_artifact_ref=ctx.bundle.history_artifact_ref,history_artifact_hash=ctx.bundle.history_artifact_hash,
        causal_limit='ABSENCE_IN_THIS_INPUT_DOES_NOT_PROVE_NEVER_COLLECTED; RAW_SOURCE_PARSE_FAILURE_REQUIRES_CAPTURE_RECEIPTS',
        minimum_remediation=[dict(gap='NOT_PRESENT_IN_FROZEN_INPUT_OR_FIELD_UNAVAILABLE',impact='REQUIRED_SELECTION_PATH_MAY_REMAIN_UNKNOWN',requirement='Original dated records for explicitly listed missing sessions; no guarantee historical PIT evidence exists'),
            dict(gap='SOURCE_REJECTED_OR_NOT_VISIBLE',impact='Unavailable at specified cutoff',requirement='Resolve recorded source/time conflicts; later download cannot backdate first_seen'),
            dict(gap='COMPARABLE_BASIS_MISMATCH',impact='Window cannot be used',requirement='Consistent same-chain adjusted price evidence with original source clocks')])
