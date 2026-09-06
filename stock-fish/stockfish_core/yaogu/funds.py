"""Whitelisted public disclosure evidence and fixed FUNDS_V1 shadow scoring.

No participant name changes a score; a seat label is not a natural person.
Future outcomes are never part of this adapter's feature/LLM projection.
"""
from __future__ import annotations
from datetime import date, datetime
import math

FEATURE_FIELDS=frozenset({'instrument_id','provider','money_definition','session','net_large_cny','amount_cny',
    'window_start','window_end','buy_cny','sell_cny','seat_id','seat_name','disclosure_id','reason',
    'event_time','source_published_at','first_seen_at','ingested_at','source_refs',
    'definition_version','currency','amount_unit','window_sessions','amount_definition'})

def feature_whitelist(row):
    return {key:value for key,value in row.items() if key in FEATURE_FIELDS}

def llm_evidence_context(rows):
    return [feature_whitelist(row) for row in rows]

def _dt(value):
    value=value if isinstance(value,datetime) else datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if value.tzinfo is None: raise ValueError('TIMEZONE_REQUIRED')
    return value

def _day(value):
    return value if isinstance(value,date) and not isinstance(value,datetime) else date.fromisoformat(str(value))

def _visible(row,cutoff):
    # Validate the envelope before reading business fields.
    try:
        from .visibility import clock_visible
        return clock_visible(row,cutoff)
    except (ValueError,KeyError,TypeError): return False

def _num(value):
    if isinstance(value,bool):raise ValueError('BOOLEAN_IS_NOT_A_FUNDS_AMOUNT')
    x=float(value)
    if not math.isfinite(x): raise ValueError('NONFINITE_FUNDS')
    return x

def _r(x,a,b): return 100*min(1,max(0,(x-a)/(b-a)))

def deduplicate_disclosures(rows,cutoff,*,instrument_id,resolver=None):
    """Dedup repeated reasons and buy/sell list appearances; never add overlaps.

    A provider may repeat a seat in the buy-five and sell-five lists. Both sides
    must represent the same totals; conflicting copies fail instead of summing.
    Prefer the widest fully known disclosure window, then disjoint windows.
    """
    unique={}
    for source in rows:
        if source.get('instrument_id')!=instrument_id or not (resolver.is_visible(source,instrument_id=instrument_id) if resolver else _visible(source,cutoff)):
            continue
        row=feature_whitelist(source)
        start,end=_day(row['window_start']),_day(row['window_end'])
        if start>end: raise ValueError('INVALID_DISCLOSURE_WINDOW')
        key=(row['provider'],start,end,row['seat_id'])
        values=(_num(row['buy_cny']),_num(row['sell_cny']))
        if min(values)<0: raise ValueError('NEGATIVE_DISCLOSURE_AMOUNT')
        if key in unique:
            prior=unique[key]
            if values!=(prior['buy_cny'],prior['sell_cny']): raise ValueError('CONFLICTING_SEAT_COPY')
            prior['reasons']=sorted(set(prior['reasons']+[row.get('reason','')]))
        else: unique[key]=dict(row,buy_cny=values[0],sell_cny=values[1],reasons=[row.get('reason','')])
    windows=sorted({(key[1],key[2]) for key in unique},key=lambda w:(-(w[1]-w[0]).days,w[0],w[1]))
    selected=[]
    for window in windows:
        if not any(window[0]<=existing[1] and existing[0]<=window[1] for existing in selected): selected.append(window)
    return [v for k,v in sorted(unique.items()) if (k[1],k[2]) in selected]

def calculate_funds(evidence,cutoff,observation_date=None,*,sessions=None,receipts=None,
                    coverage_type='PROSPECTIVE_CAPTURED',definition_version='FUNDS_EVIDENCE_V1'):
    unavailable=lambda code:dict(status='UNAVAILABLE',reason_codes=[code],F_money=None,F_lhb=None,F=None,S_funds=None)
    from .visibility import EvidenceResolver, clock_visible
    if observation_date is None or not sessions or receipts is None:
        return unavailable('FUNDS_AUTHORITY_CONTEXT_REQUIRED')
    if not evidence or not clock_visible(evidence,cutoff,allow_late_capture=coverage_type=='RECONSTRUCTED_HISTORICAL'):
        return unavailable('FUNDS_ENVELOPE_NOT_VISIBLE')
    iid=evidence.get('instrument_id'); provider=evidence.get('provider'); definition=evidence.get('money_definition')
    if not iid or not provider or not definition or not evidence.get('source_refs'): return unavailable('FUNDS_PROVENANCE_MISSING')
    if evidence.get('money_status')!='COMPLETE': return unavailable('MONEY_'+str(evidence.get('money_status','UNAVAILABLE')))
    status=evidence.get('disclosure_status')
    if status not in ('DISCLOSED','NOT_DISCLOSED'): return unavailable('LHB_'+str(status or 'UNAVAILABLE'))
    if evidence.get('disclosure_complete') is not True: return unavailable('DISCLOSURE_UNIVERSE_INCOMPLETE')
    try:
        observation_date=_day(observation_date)
        calendar=tuple(_day(d) for d in sessions)
        if calendar!=tuple(sorted(set(calendar))) or observation_date not in calendar:
            return unavailable('AUTHORITATIVE_EXCHANGE_CALENDAR_REQUIRED')
        index=calendar.index(observation_date)
        if index<2:return unavailable('THREE_SESSION_CALENDAR_INCOMPLETE')
        resolver=EvidenceResolver(receipts,cutoff=cutoff,coverage_type=coverage_type,sessions=calendar,observation_date=observation_date)
        envelope=feature_whitelist(evidence)
        envelope.setdefault('session',observation_date.isoformat())
        gate=resolver.resolve(envelope,instrument_id=iid,session=observation_date,require_complete=True,require_session=True)
        if not gate.allowed:return unavailable(gate.reason_codes[0])
        if evidence.get('definition_version')!=definition_version:
            return unavailable('FUNDS_DEFINITION_VERSION_REQUIRED')
        expected=tuple(_day(v) for v in evidence['money_sessions'])
        if expected!=calendar[index-2:index+1]: return unavailable('CONSECUTIVE_THREE_EXCHANGE_SESSIONS_REQUIRED')
        def validate(row,role,measurement,source_provider,*,business_day=None):
            if row.get('provider')!=source_provider or row.get('definition_version')!=definition_version:
                raise ValueError('INCOMPARABLE_SOURCE_OR_DEFINITION')
            if row.get('currency')!='CNY' or row.get('amount_unit')!='CNY':
                raise ValueError('FUNDS_CNY_UNIT_REQUIRED')
            result=resolver.resolve(row,instrument_id=iid,session=business_day,require_complete=True,
                require_session=True,source_types={role},definition_version=definition_version)
            if not result.allowed:raise ValueError(result.reason_codes[0])
            from datetime import time
            from .visibility import SH
            close=datetime.combine(_day(row['session']),time(15),SH)
            if min(_dt(row[k]) for k in ('event_time','source_published_at'))<close:
                raise ValueError('CLOSED_SESSION_FUNDS_NOT_YET_AVAILABLE')
            for ref in result.source_ids:
                parent=resolver.receipts[ref]
                if parent.provider!=source_provider or parent.measurement_definition!=measurement:
                    raise ValueError('SOURCE_MEASUREMENT_DEFINITION_MISMATCH')
                if iid not in parent.instrument_ids or not parent.business_sessions:
                    raise ValueError('SOURCE_IDENTITY_AND_SESSIONS_SCOPE_REQUIRED')
                if parent.currency!='CNY' or parent.amount_unit!='CNY':
                    raise ValueError('SOURCE_AMOUNT_UNIT_MISMATCH')
            return feature_whitelist(row)
        rows=[]
        for row in evidence.get('money_rows',[]):
            if not clock_visible(row,cutoff,allow_late_capture=coverage_type=='RECONSTRUCTED_HISTORICAL'): continue
            if row.get('instrument_id')!=iid or row.get('provider')!=provider or row.get('money_definition')!=definition:
                return unavailable('INCOMPARABLE_MONEY_SOURCE')
            rows.append(validate(feature_whitelist(row),'MONEY_FLOW',definition,provider))
        rows.sort(key=lambda x:_day(x['session']))
        if tuple(_day(r['session']) for r in rows)!=expected: return unavailable('MONEY_ROWS_INCOMPLETE')
        amounts=[_num(r['amount_cny']) for r in rows]; nets=[_num(r['net_large_cny']) for r in rows]
        if min(amounts)<=0: return unavailable('INVALID_MONEY_DENOMINATOR')
        daily=nets[-1]/amounts[-1]; three=sum(nets)/sum(amounts)
        if max(abs(daily),abs(three))>1: return unavailable('MONEY_NET_EXCEEDS_TURNOVER')
        money=.60*_r(daily,-.10,.10)+.40*_r(three,-.10,.10)
        lhb_provider=evidence.get('lhb_provider',provider)
        lhb_definition=evidence.get('lhb_definition','LHB_DISCLOSED_SEATS_V1')
        turnover_provider=evidence.get('turnover_provider',lhb_provider)
        turnover_definition=evidence.get('turnover_definition','EXCHANGE_TURNOVER_RAW_CNY')
        def window_row(source,role,measurement,source_provider):
            row=feature_whitelist(source)
            start,end=_day(row['window_start']),_day(row['window_end'])
            if start>end or end>observation_date or start not in calendar or end not in calendar:
                raise ValueError('DISCLOSURE_WINDOW_OUTSIDE_OBSERVATION')
            window=calendar[calendar.index(start):calendar.index(end)+1]
            if len(window) not in (1,3) or tuple(_day(d) for d in row.get('window_sessions',()))!=window:
                raise ValueError('VERIFIED_ONE_OR_THREE_SESSION_WINDOW_REQUIRED')
            if row.get('session') is None or _day(row['session'])!=end:
                raise ValueError('DISCLOSURE_END_SESSION_MISMATCH')
            row=validate(row,role,measurement,source_provider,business_day=end)
            for ref in row['source_refs']:
                if not set(window)<=set(resolver.receipts[ref].business_sessions):
                    raise ValueError('SOURCE_WINDOW_SCOPE_MISMATCH')
            return row
        disclosed_rows=[]
        for row in evidence.get('disclosures',[]):
            if not clock_visible(row,cutoff,allow_late_capture=coverage_type=='RECONSTRUCTED_HISTORICAL'):continue
            if row.get('instrument_id')!=iid:continue
            disclosed_rows.append(window_row(row,'LHB_DISCLOSURE',lhb_definition,lhb_provider))
        disclosed=deduplicate_disclosures(disclosed_rows,cutoff,instrument_id=iid,resolver=resolver)
        net=None; windows=[]
        if status=='NOT_DISCLOSED':
            if disclosed: return unavailable('NOT_DISCLOSED_CONTRADICTION')
            universe=evidence.get('disclosure_universe')
            if not universe:return unavailable('COMPLETE_DISCLOSURE_UNIVERSE_PROOF_REQUIRED')
            if universe.get('instrument_id') is not None and universe['instrument_id']!=iid:
                return unavailable('DISCLOSURE_UNIVERSE_INSTRUMENT_MISMATCH')
            projection=feature_whitelist(universe)|{'instrument_id':iid}
            validate(projection,'LHB_UNIVERSE',lhb_definition,lhb_provider,business_day=observation_date)
            if iid not in universe.get('queried_instrument_ids',()) or iid in universe.get('disclosed_instrument_ids',()):
                return unavailable('NOT_DISCLOSED_UNIVERSE_CONTRADICTION')
            # Only verified membership/absence is a decision dependency, not
            # arbitrary future outcome columns on an upstream disclosure row.
            resolver.rows['disclosure_universe']={**projection,
                'queried_instrument_ids':sorted(universe['queried_instrument_ids']),
                'disclosed_instrument_ids':sorted(universe['disclosed_instrument_ids'])}
            lhb=50.
        else:
            if not disclosed: return unavailable('VISIBLE_DISCLOSURE_MISSING')
            windows=sorted({(r['window_start'],r['window_end']) for r in disclosed})
            denoms=[]
            for start,end in windows:
                matches=[r for r in evidence.get('window_amounts',[]) if clock_visible(r,cutoff,allow_late_capture=coverage_type=='RECONSTRUCTED_HISTORICAL')
                         and r.get('instrument_id')==iid and r.get('window_start')==start and r.get('window_end')==end]
                if len(matches)!=1 or _num(matches[0]['amount_cny'])<=0: return unavailable('LHB_WINDOW_DENOMINATOR_MISSING')
                denominator=window_row(matches[0],'WINDOW_TURNOVER',turnover_definition,turnover_provider)
                if denominator.get('amount_definition')!=turnover_definition: return unavailable('TURNOVER_DEFINITION_MISMATCH')
                denoms.append(_num(denominator['amount_cny']))
            net=sum(r['buy_cny']-r['sell_cny'] for r in disclosed)
            lhb=_r(net/sum(denoms),-.05,.05)
        total=.60*money+.40*lhb
        return dict(status='AVAILABLE',reason_codes=[],F_money=money,F_lhb=lhb,F=total,S_funds=None,
                    lhb_raw_net_cny=net,disclosure_status=status,money_ratio_day=daily,money_ratio_3=three,
                    money_definition=definition,provider=provider,money_sessions=[d.isoformat() for d in expected],
                    windows=windows,seats=disclosed,source_refs=sorted(resolver.used),identity_note='SEAT_LABEL_NOT_NATURAL_PERSON',
                    definition_version=definition_version,effective_dependencies=resolver.effective_dependencies())
    except ValueError as exc:return unavailable(str(exc) if str(exc).isupper() and len(str(exc))<120 else 'INVALID_FUNDS_FIELDS')
    except (TypeError,KeyError,ZeroDivisionError): return unavailable('INVALID_FUNDS_FIELDS')

def score_family_funds(evidence,daily_scores,cutoff,observation_date=None,*,sessions=None,receipts=None,
                       coverage_type='PROSPECTIVE_CAPTURED',definition_version='FUNDS_EVIDENCE_V1'):
    from .visibility import clock_visible,EvidenceResolver
    mapped={}; invalid={}; duplicate=False
    resolver=EvidenceResolver(receipts,cutoff=cutoff,coverage_type=coverage_type,sessions=sessions or (),
        observation_date=observation_date) if receipts is not None else None
    for row in evidence:
        if not clock_visible(row,cutoff,allow_late_capture=coverage_type=='RECONSTRUCTED_HISTORICAL'): continue
        iid=row.get('instrument_id')
        # The structural family is frozen before funds enrichment. Evidence for
        # another security cannot affect its completeness or duplicate checks.
        if iid not in daily_scores: continue
        if resolver is not None:
            outer=feature_whitelist(row)
            if observation_date is not None:outer.setdefault('session',_day(observation_date).isoformat())
            gate=resolver.resolve(outer,instrument_id=iid,session=observation_date,require_complete=True)
            if not gate.allowed:
                invalid[iid]=row
                continue
        if iid in mapped: duplicate=True
        mapped[iid]=row
    results={iid:calculate_funds(mapped.get(iid,invalid.get(iid)),cutoff,observation_date,sessions=sessions,receipts=receipts,
        coverage_type=coverage_type,definition_version=definition_version) for iid in daily_scores}
    reasons=[]
    if duplicate: reasons.append('DUPLICATE_FUNDS_ENVELOPE')
    if any(r['status']!='AVAILABLE' or daily_scores[iid] is None for iid,r in results.items()): reasons.append('FAMILY_FUNDS_INCOMPLETE')
    comparable={(r.get('provider'),r.get('money_definition'),tuple(r.get('money_sessions',()))) for r in results.values() if r['status']=='AVAILABLE'}
    if len(comparable)>1: reasons.append('FAMILY_FUNDS_INCOMPARABLE')
    dependencies={'policy_id':'R03_EVIDENCE_VISIBILITY_V1','items':{
        iid:value['effective_dependencies'] for iid,value in sorted(results.items()) if value.get('effective_dependencies')}}
    if reasons or not results: return dict(status='UNAVAILABLE',reason_codes=reasons or ['EMPTY_FAMILY'],items=results,
                                           effective_dependencies=dependencies)
    for iid,r in results.items(): r['S_funds']=.90*_num(daily_scores[iid])+.10*r['F']
    return dict(status='AVAILABLE',reason_codes=[],items=results,effective_dependencies=dependencies)

family_funds_profile=score_family_funds

def visible_seat_aliases(aliases,cutoff,session,*,resolver=None):
    return [feature_whitelist(x)|{k:x[k] for k in ('alias','valid_from','valid_to','sample_count') if k in x}
        for x in aliases if (resolver.is_visible(x) if resolver else _visible(x,cutoff)) and x.get('source_refs') and x.get('alias')
        and _day(x['valid_from'])<=_day(session) and (not x.get('valid_to') or _day(session)<=_day(x['valid_to']))]

def coappearance(rows,theme_members,cutoff,*,resolver=None):
    """Evidence query only, no causal same-controller assertion or score bonus."""
    indexed={}
    for row in rows:
        if not (resolver.is_visible(row) if resolver else _visible(row,cutoff)): continue
        iid=row.get('instrument_id')
        for theme,members in theme_members.items():
            if iid in members:
                key=(theme,row.get('seat_id'),row.get('window_start'),row.get('window_end'))
                indexed.setdefault(key,set()).add(iid)
    return [dict(theme_id=k[0],seat_id=k[1],window_start=k[2],window_end=k[3],instruments=sorted(v),
                 meaning='DISCLOSED_COAPPEARANCE_NOT_COMMON_CONTROL') for k,v in sorted(indexed.items())]
