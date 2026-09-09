"""R05 approved, evidence-resolved experiments. No accounts, orders or tuning."""
from __future__ import annotations
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction
from pathlib import Path
from statistics import median
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import yaml
from .contracts import MarketInput, digest
from .events import TRUE, FALSE, limit_event, annotate_streaks
from .visibility import EvidenceResolver, stamp

ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = ROOT / 'config/r05_strategy_experiments.yaml'
COMPLETION_PATH = ROOT / 'config/r05_completion_requirements.yaml'
SOURCE_LOCK_PATH = ROOT / 'config/r05_source_lock.json'
SOURCE_LOCK = json.loads(SOURCE_LOCK_PATH.read_text(encoding='utf-8'))
def _assert_frozen_specs():
    for path in (SPEC_PATH,COMPLETION_PATH):
        if hashlib.sha256(path.read_bytes()).hexdigest()!=SOURCE_LOCK['spec_files'][path.name]:
            raise ValueError('R05_FROZEN_SPEC_CHANGED_REQUIRES_NEW_VERSION')

_assert_frozen_specs()
SPEC = yaml.safe_load(SPEC_PATH.read_text(encoding='utf-8'))
COMPLETION = yaml.safe_load(COMPLETION_PATH.read_text(encoding='utf-8'))
PV_OBSERVATION_ID = 'PV_OBSERVATION_V1'
DNA_ID, THEME_ID = 'DNA_RERANK_V1', 'THEME_CONTEXT_V1'
DNA_REVERSAL_ID, THEME_LIMITUP_ID = 'DNA_REVERSAL_V1', 'THEME_LIMITUP_V1'
FOCUS_POLICY_ID = COMPLETION['selection_policy']['id']
IMPLEMENTATION_VERSION = 'R05_STRATEGY_EXPERIMENTS_V4_CANONICAL_IDENTITY'
PARAMETER_STATUS = SPEC['parameter_status']
PV_SPEC, DNA_SPEC, THEME_SPEC = (SPEC['strategies'][k] for k in (PV_OBSERVATION_ID,DNA_ID,THEME_ID))
PV_WEIGHTS = {k: Decimal(str(PV_SPEC['weights']['percentile_'+k])) for k in ('A','R','H')}
DNA_WEIGHTS = dict(zip(('LB_PREMIUM5','LB_FIRST_RED','LB_FIRST_SEAL','M_ADJ','LIMIT_COUNT_250D'),
    (Decimal(str(v)) for v in DNA_SPEC['gene_weights'].values())))
THEME_WEIGHTS = dict(zip(('PEER_LIMIT_COUNT','PEER_RET5_MEDIAN','PEER_UP_FRACTION'),
    (Decimal(str(v)) for v in THEME_SPEC['theme_weights'].values())))
DNA_HISTORY_SESSIONS = DNA_SPEC['history_sessions']
DNA_MATURE_EVENT_SESSIONS = DNA_SPEC['mature_event_window_sessions']
PV_QUOTE_COVERAGE_MIN = Decimal(str(SPEC['universe']['quote_coverage_min']))
THEME_PEER_COVERAGE_MIN = Decimal(str(THEME_SPEC['peer_required_quote_coverage_min']))
FOCUS_POLICY = {**COMPLETION['selection_policy'], 'policy_id': FOCUS_POLICY_ID}

def _stable_id(prefix, payload):return prefix+'_'+digest(payload)[:24]

def _as_decimal(value):
    try:
        if value is None:return None
        if isinstance(value,Fraction):
            with localcontext() as ctx:
                ctx.prec=50
                return Decimal(value.numerator)/Decimal(value.denominator)
        result=value if isinstance(value,Decimal) else Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation,ValueError,TypeError):return None

def _number(value, *, places=6):
    value=_as_decimal(value)
    if value is None:return None
    with localcontext() as ctx:
        ctx.prec=60
        return float(value.quantize(Decimal(1).scaleb(-places)))

def _fraction(value):
    if isinstance(value,Fraction):return value
    value=_as_decimal(value)
    return Fraction(value) if value is not None else None

def _percentile_map(values):
    ordered=sorted(values);result={};left=0
    for value in ordered:result[value]=result.get(value,0)+1
    for value,count in result.copy().items():
        result[value]=Fraction(100*(2*left+count),2*len(ordered));left+=count
    return result

def _percentile_rank_decimal(value,values):
    value=_fraction(value);population=[v for v in map(_fraction,values) if v is not None]
    if value is None or not population:return None
    return _as_decimal(Fraction(100*(2*sum(x<value for x in population)+sum(x==value for x in population)),2*len(population)))

def percentile_rank_midpoint(value,values):
    result=_percentile_rank_decimal(value,values)
    return float(result) if result is not None else None

def _score_from_decimals(weights,factors):
    if any(factors.get(k) is None for k in weights):raise ValueError('MISSING_FACTOR_NO_IMPUTATION')
    return sum((Fraction(w)*_fraction(factors[k]) for k,w in weights.items()),Fraction(0))

def _parse_bar(bar):return bar.model_dump() if hasattr(bar,'model_dump') else dict(bar) if bar is not None else None
def _bar_decimal(bar,field):return _as_decimal((_parse_bar(bar) or {}).get(field))
def _bar_limit_event(bar,iid,session):return limit_event(SimpleNamespace(**bar) if isinstance(bar,dict) else bar,iid,date.fromisoformat(str(session)[:10]),suspended=False)
def _known_limit_flag(value):return value in (TRUE,FALSE)
def _session_text(value):return str(value)[:10]

@dataclass(frozen=True)
class _ExperimentContext:
    bundle: MarketInput
    observation_date: str
    sessions: list
    eligible_ids: list
    bars_by_session: dict
    universe_complete: bool
    resolver: EvidenceResolver
    securities: dict
    qualification: dict
    rejected_evidence: list
    cutoff: datetime
    themes: list
    # Scalar feature reuse is scoped to this exact immutable input and cutoff.
    # It prevents cross-instrument loops from thrashing the bounded row cache.
    scalar_cache: dict = field(default_factory=dict,compare=False,repr=False)

def _revision_key(row):return (row.source_published_at,row.ingested_at,row.first_seen_at)

def _select_revision(rows):
    if not rows:return None
    latest=max(_revision_key(r) for r in rows);chosen=[r for r in rows if _revision_key(r)==latest]
    return chosen[0] if len({digest(r) for r in chosen})==1 else None

def _qualification(state,day,sessions):
    if state is None:return 'UNKNOWN_IDENTITY_OR_CONFLICT'
    u=SPEC['universe']
    if 'UNKNOWN' in (state.exchange,state.board,state.security_type):return 'UNKNOWN_IDENTITY'
    if state.exchange not in u['exchanges'] or state.board!=u['board'] or state.security_type!=u['security_type']:return 'OUTSIDE_MAIN_BOARD_A_COMMON'
    if state.st is None or state.suspended is None or state.delisting_period is None:return 'UNKNOWN_HISTORICAL_STATUS'
    if state.delisting_period or state.delisted_on is not None and state.delisted_on<=day:return 'DELISTING_OR_DELISTED'
    if state.st and not u['include_st']:return 'ST_POOL_DISABLED'
    if state.suspended:return 'SUSPENDED_CURRENT_SESSION'
    age=sum(state.listed_on<=d<=day for d in sessions)
    if age<u['min_listing_exchange_sessions']:
        return 'UNKNOWN_LISTING_HISTORY' if sessions and state.listed_on<sessions[0] else 'INSUFFICIENT_LISTING_SESSIONS'
    return None

def _prepare_context(bundle,cutoff=None):
    cutoff=stamp(cutoff) if cutoff is not None else datetime.combine(bundle.observation_date,time.max,ZoneInfo('Asia/Shanghai'))
    day=bundle.observation_date;sessions=[s for s in bundle.sessions if s<=day]
    from .history_artifacts import history_resolver, InstrumentBars
    resolver=history_resolver(bundle,cutoff=cutoff)
    rejected=[];groups=defaultdict(list)
    for s in bundle.universe.securities:groups[s.instrument_id].append(s)
    states={};eligible=[];excluded=[];unknown=[]
    for iid,rows in sorted(groups.items()):
        visible=[]
        for row in rows:
            if row.effective_from>day or row.effective_to is not None and day>=row.effective_to:continue
            proof=resolver.resolve(row,instrument_id=iid,session=day)
            if proof.allowed:visible.append(row)
            else:rejected.append({'kind':'SECURITY','instrument_id':iid,'reason_codes':list(proof.reason_codes)})
        state=_select_revision(visible);states[iid]=state;reason=_qualification(state,day,sessions)
        if reason is None:eligible.append(iid)
        else:(unknown if reason.startswith('UNKNOWN_') else excluded).append({'instrument_id':iid,'reason':reason})
    universe_proof=resolver.resolve(bundle.universe,session=day,require_complete=True)
    expected=max(bundle.universe.expected_count or 0,len(groups));absent=max(0,expected-len(groups))
    universe_complete=bool(bundle.universe.complete and universe_proof.allowed and not unknown and not absent and day<=cutoff.astimezone(ZoneInfo('Asia/Shanghai')).date())
    bar_groups=defaultdict(list)
    for b in (() if bundle.history_artifact_ref else bundle.bars):
        if b.session>day:continue
        if b.quality=='INVALID':
            rejected.append({'kind':'BAR','instrument_id':b.instrument_id,'session':str(b.session),'reason_codes':['INVALID_PRICE_QUALITY']});continue
        proof=resolver.resolve(b,instrument_id=b.instrument_id,session=b.session,require_session=True)
        if proof.allowed:bar_groups[(str(b.session),b.instrument_id)].append(b)
        else:rejected.append({'kind':'BAR','instrument_id':b.instrument_id,'session':str(b.session),'reason_codes':list(proof.reason_codes)})
    bars={}
    for (session,iid),rows in bar_groups.items():
        row=_select_revision(rows)
        if row is not None:bars.setdefault(session,{})[iid]=row.model_dump()
        else:rejected.append({'kind':'BAR','instrument_id':iid,'session':session,'reason_codes':['CONFLICTING_PRICE_REVISION']})
    if bundle.history_artifact_ref:bars=InstrumentBars(bundle,resolver,rejected)
    tg=defaultdict(list)
    for t in bundle.themes:
        proof=resolver.resolve(t,session=day,require_complete=True)
        if proof.allowed and t.theme_type=='EVENT':tg[t.theme_id].append(t)
        else:rejected.append({'kind':'THEME','theme_id':t.theme_id,'reason_codes':list(proof.reason_codes) or ['NOT_EVENT_THEME']})
    themes=[x.model_dump() for rows in tg.values() if (x:=_select_revision(rows)) is not None]
    partition={'partition_version':'R05_UNIQUE_DATED_QUALIFICATION_V1','master_expected':expected,'identity_observed':len(groups),'identity_absent':absent,
        'eligible':len(eligible),'eligible_ids':eligible,'known_excluded':len(excluded),'known_excluded_records':excluded,
        'eligibility_unknown':len(unknown)+absent,'eligibility_unknown_records':unknown,'selected_security_hash':digest({i:s.model_dump(mode='json') for i,s in states.items() if s is not None}),
        'selected_records':[{'instrument_id':i,'name':s.name,'effective_from':str(s.effective_from),'effective_to':str(s.effective_to) if s.effective_to else None,
            'listing_exchange_sessions':sum(s.listed_on<=d<=day for d in sessions),'source_refs':list(s.source_refs),'record_hash':digest(s)} for i,s in sorted(states.items()) if s is not None],
        'universe_evidence_reason_codes':list(universe_proof.reason_codes)}
    return _ExperimentContext(bundle,str(day),list(map(str,sessions)),eligible,bars,universe_complete,resolver,states,partition,rejected,cutoff,themes)

def _bar(ctx,session,iid):
    if hasattr(ctx.bars_by_session,'bar'):return ctx.bars_by_session.bar(session,iid)
    return ctx.bars_by_session.get(str(session),{}).get(iid)
def _prior_sessions(ctx,count):return ctx.sessions[-count:] if len(ctx.sessions)>=count else []

def _valid_ohlc(current):
    values=[_as_decimal(current.get(k)) for k in ('open','high','low','close')]
    if any(v is None or v<=0 for v in values):return False
    o,h,l,c=values
    return l<=min(o,c)<=max(o,c)<=h

def _amount_ratio(current,previous):
    amounts=[_fraction(x.get('amount_cny')) for x in [*previous,current]]
    if len(previous)!=PV_SPEC['amount_ratio_prior_sessions'] or any(x is None or x<0 for x in amounts):return None
    denominator=sum(amounts[:-1],Fraction(0))
    return amounts[-1]*len(previous)/denominator if denominator>0 else None

def _comparable_return(current,previous):
    if not current.get('comparable_basis') or current.get('comparable_basis')!=previous.get('comparable_basis'):return None
    a,b=_fraction(current.get('comparable_close')),_fraction(previous.get('comparable_close'))
    return a/b-1 if a is not None and b is not None and a>0 and b>0 else None

def _high_low_range(current):
    h,l,ref=(_fraction(current.get(k)) for k in ('high','low','reference_prev_close'))
    return (h-l)/ref if h is not None and l is not None and ref is not None and ref>0 and h>=l>0 else None

def _exact_factor(item,key):
    raw=item.get('exact_factors',{}).get(key)
    return Fraction(raw['numerator'],raw['denominator']) if raw is not None else _fraction(item.get(key))

def _exact_raw(item,key):return _fraction(item.get('exact_prices',{}).get(key,item.get(key)))

def _complete_pv_row(ctx,iid,window):
    if len(window)!=PV_SPEC['lookback_sessions']:return None,'PV_6_EXCHANGE_SESSIONS_REQUIRED'
    bars=[_bar(ctx,s,iid) for s in window]
    if any(b is None for b in bars):return None,'PV_6_SESSION_WINDOW_INCOMPLETE'
    if any(not _valid_ohlc(b) for b in bars):return None,'PV_INVALID_RAW_OHLC'
    basis=bars[0].get('comparable_basis')
    if not basis or any(b.get('comparable_basis')!=basis or _bar_decimal(b,'comparable_close') is None or _bar_decimal(b,'comparable_close')<=0 for b in bars):return None,'PV_COMPARABLE_BASIS_WINDOW_MISMATCH'
    current=bars[-1];a=_amount_ratio(current,bars[:-1]);r=_comparable_return(current,bars[0]);h=_high_low_range(current)
    if a is None:return None,'PV_AMOUNT_MEAN5_UNAVAILABLE'
    if r is None:return None,'PV_COMPARABLE_RETURN_T_MINUS_5_UNAVAILABLE'
    if h is None:return None,'PV_HIGH_LOW_RANGE_UNAVAILABLE'
    s=ctx.securities[iid];event=_bar_limit_event(current,iid,ctx.observation_date);factors=dict(A=a,R=r,H=h)
    row={'instrument_id':iid,'name':s.name,'observation_date':ctx.observation_date,
        **{k:_number(v) for k,v in factors.items()},**{k:_number(_bar_decimal(current,k),places=8) for k in ('open','high','low','close','reference_prev_close','volume_shares','amount_cny','comparable_close')},
        'exact_factors':{k:{'numerator':v.numerator,'denominator':v.denominator} for k,v in factors.items()},
        'exact_prices':{k:str(current[k]) if current.get(k) is not None else None for k in ('open','high','low','close','reference_prev_close','comparable_close')},
        'mean_prior5_amount_cny':_number(sum(_fraction(b['amount_cny']) for b in bars[:-1])/len(bars[:-1]),places=2),
        'comparable_close_T_minus_5':str(bars[0]['comparable_close']),'comparable_basis':basis,
        'source_refs':sorted(set(s.source_refs).union(*(b.get('source_refs',()) for b in bars))),
        'security_record_hash':digest(s),'price_record_hashes':[digest(b) for b in bars],
        'limit_event_status':event.quality,'close_limit_up':event.close_limit_up,'board_state':'VERIFIED' if event.quality=='VALID' else 'UNVERIFIED_NOT_ZERO',
        'risk_flags':sorted(set(s.risk_flags)|{'AMPLITUDE_IS_RISK_NOT_SAFETY','RESEARCH_SCORE_NOT_PROBABILITY'}|({'LIMIT_EVENTS_UNVERIFIED'} if event.quality!='VALID' else set())),
        'risk_review_status':'NOT_MANUALLY_REVIEWED','_A':a,'_R':r,'_H':h}
    return row,None

def _public(row):return {k:v for k,v in row.items() if not k.startswith('_')}

def _rank_pv(rows):
    maps={k:_percentile_map(row['_'+k] for row in rows) for k in PV_WEIGHTS}
    for row in rows:
        p={k:maps[k][row['_'+k]] for k in PV_WEIGHTS};score=_score_from_decimals(PV_WEIGHTS,p)
        row.update(rank_percentiles={k:_number(v) for k,v in p.items()},S_PV=_number(score),_S_PV=score)
    ranked=sorted(rows,key=lambda x:(-x['_S_PV'],x['instrument_id']))
    for rank,row in enumerate(ranked,1):row['rank']=rank
    return ranked

def run_pv_observation(bundle,*,top_n=100,cutoff=None,_context=None):
    ctx=_context or _prepare_context(bundle,cutoff);window=_prior_sessions(ctx,PV_SPEC['lookback_sessions'])
    complete=[];missing=[];rejected=[];rows=[]
    for iid in ctx.eligible_ids:
        row,reason=_complete_pv_row(ctx,iid,window)
        if row is None:missing.append({'instrument_id':iid,'reason':reason});continue
        complete.append(row);reasons=[]
        if row['_A']<Fraction(str(PV_SPEC['min_amount_ratio_inclusive'])):reasons.append('PV_AMOUNT_RATIO_BELOW_1_2')
        if row['_R']<=Fraction(str(PV_SPEC['min_ret5_exclusive'])):reasons.append('PV_RET5_NOT_POSITIVE')
        if reasons:rejected.append({'instrument_id':iid,'reason_codes':reasons})
        else:rows.append(row.copy())
    ranked=_rank_pv(rows);coverage=Fraction(len(complete),len(ctx.eligible_ids)) if ctx.eligible_ids else Fraction(0)
    full=ctx.universe_complete and (coverage>=PV_QUOTE_COVERAGE_MIN or not ctx.eligible_ids)
    status=('COMPLETE' if ranked else 'EMPTY') if full else ('LIMITED_SCOPE' if complete else 'UNAVAILABLE')
    return {'strategy_id':PV_OBSERVATION_ID,'canonical_strategy_id':PV_OBSERVATION_ID,'implementation_version':IMPLEMENTATION_VERSION,
        'parameter_status':PARAMETER_STATUS,'spec_conformance':'SPEC_CONFORMANT','status':status,'scope_label':'FULL_SCOPE' if full else 'LIMITED_SCOPE',
        'observation_date':ctx.observation_date,'decision_cutoff':ctx.cutoff.isoformat(),'formula':'0.45*PR_mid(A) + 0.35*PR_mid(R) + 0.20*PR_mid(H)',
        'tie_break':'exact S_PV desc, instrument_id asc','arithmetic':'EXACT_RATIONAL_UNTIL_DISPLAY',
        'counts':{'eligible':len(ctx.eligible_ids),'evaluated':len(complete),'filter_passed':len(ranked),'missing':len(missing),'known_rejected':len(rejected),
        'field_coverage':_number(coverage),'quote_coverage_min':float(PV_QUOTE_COVERAGE_MIN),'universe_complete':ctx.universe_complete},
        'qualification':ctx.qualification,'qualified_ids':list(ctx.eligible_ids),'complete_feature_ids':[r['instrument_id'] for r in complete],
        'eligible_feature_items':[_public(r) for r in complete],'items':[_public(r) for r in ranked],
        'top_ids':[r['instrument_id'] for r in ranked[:top_n]],'incomplete':missing,'missing':missing,'rejected':rejected,'rejected_evidence':ctx.rejected_evidence}

def _close_location(item):
    c,h,l=(_exact_raw(item,k) for k in ('close','high','low'))
    return (c-l)/(h-l) if all(x is not None for x in (c,h,l)) and h>l else None

def run_pv_focus_preview(pv_result,*,max_count=None):
    # Deprecated display argument is accepted, never used for qualification.
    thresholds=FOCUS_POLICY['thresholds'];items=[]
    for parent in pv_result.get('items',[]):
        a,r=_exact_factor(parent,'A'),_exact_factor(parent,'R');o,c,h,l=(_exact_raw(parent,k) for k in ('open','close','high','low'))
        clv=_close_location(parent);reasons=[]
        if a is None:reasons.append('A_UNAVAILABLE')
        elif a<Fraction(thresholds['amount_ratio_prior_5_excluding_today_min_inclusive']):reasons.append('A_BELOW_2_0')
        if r is None:reasons.append('R_UNAVAILABLE')
        elif r<Fraction(thresholds['comparable_return_5_sessions_min_inclusive']):reasons.append('R_BELOW_0_10')
        if any(x is None or x<=0 for x in (o,c,h,l)):reasons.append('RAW_OHLC_UNAVAILABLE')
        elif not l<=min(o,c)<=max(o,c)<=h:reasons.append('RAW_OHLC_INVALID')
        elif c<o:reasons.append('RAW_CLOSE_BELOW_RAW_OPEN')
        if clv is None:reasons.append('NOT_APPLICABLE_ZERO_RANGE' if h is not None and h==l else 'CLOSE_LOCATION_UNAVAILABLE')
        elif clv<Fraction(thresholds['raw_close_location_min_inclusive']):reasons.append('CLOSE_LOCATION_BELOW_0_70')
        items.append({**parent,'parent_rank':parent.get('rank'),'parent_score':parent.get('S_PV'),'close_location':_number(clv),
            'selected':not reasons,'reason_codes':reasons or ['SELECTED_BY_POLICY'],'risk_review_status':parent.get('risk_review_status','NOT_MANUALLY_REVIEWED')})
    selected=[r for r in items if r['selected']]
    return {'strategy_id':FOCUS_POLICY_ID,'implementation_version':IMPLEMENTATION_VERSION,'selection_policy_id':FOCUS_POLICY_ID,
        'status':('COMPLETE' if selected else 'EMPTY') if pv_result.get('status') in ('COMPLETE','EMPTY',None) else pv_result.get('status'),
        'selection_policy':FOCUS_POLICY,'selection_policy_hash':digest(FOCUS_POLICY),'parent_strategy_id':PV_OBSERVATION_ID,
        'observation_date':pv_result.get('observation_date'),'items':items,'selected_items':selected,'selected_ids':[r['instrument_id'] for r in selected],
        'counts':{'parent_count':len(items),'selected':len(selected),'rejected':len(items)-len(selected)},'count':len(selected),
        'quota_min':None,'quota_max':None,'rank_window':None,'parameter_status':PARAMETER_STATUS}

def _wilson_lower_bound(successes,n,*,z=None):
    if n<=0:return None
    if not 0<=successes<=n:raise ValueError('INVALID_BINOMIAL_COUNTS')
    with localcontext() as ctx:
        ctx.prec=50
        z=Decimal(str(z if z is not None else DNA_SPEC['wilson_z']));nn=Decimal(n);p=Decimal(successes)/nn
        result=(p+z*z/(2*nn)-z*(p*(1-p)/nn+z*z/(4*nn*nn)).sqrt())/(1+z*z/nn)
        return max(Decimal(0),result)

def _pv_population_scores(pv_items,ids,output_field=None):
    ids=sorted(set(ids)&pv_items.keys())
    maps={k:_percentile_map(_exact_factor(pv_items[i],k) for i in ids) for k in PV_WEIGHTS}
    return {i:_score_from_decimals(PV_WEIGHTS,{k:maps[k][_exact_factor(pv_items[i],k)] for k in PV_WEIGHTS}) for i in ids}

def _enhancement_base(strategy_id,ctx,pv_items,reason):
    return {'strategy_id':strategy_id,'canonical_strategy_id':strategy_id,'implementation_version':IMPLEMENTATION_VERSION,
        'base_strategy_id':PV_OBSERVATION_ID,'parameter_status':PARAMETER_STATUS,'spec_conformance':'SPEC_CONFORMANT',
        'observation_date':ctx.observation_date,'status':'INSUFFICIENT_COVERAGE','reason':reason,
        'counts':{'eligible':len(pv_items),'evaluated':0,'insufficient':len(pv_items)},'qualified_ids':[],
        'items':[{'instrument_id':i,'status':'INSUFFICIENT_COVERAGE','reason':reason} for i in sorted(pv_items)],'top_ids':[]}

def _next_premium(nxt,field):
    # The next session's reference price preserves ex-rights semantics.
    value,ref=_bar_decimal(nxt,field),_bar_decimal(nxt,'reference_prev_close')
    return Fraction(value)/Fraction(ref)-1 if value is not None and ref is not None and value>0 and ref>0 else None

def run_dna_rerank(bundle,pv_result=None,*,top_n=100,cutoff=None,_context=None):
    ctx=_context or _prepare_context(bundle,cutoff)
    pv_result=pv_result if pv_result is not None else run_pv_observation(bundle,cutoff=cutoff,_context=ctx)
    pv={r['instrument_id']:r for r in pv_result.get('items',[]) if r['instrument_id'] in ctx.eligible_ids}
    result=_enhancement_base(DNA_ID,ctx,pv,'DNA_252_SESSION_HISTORY_REQUIRED')
    result.update(history_sessions_required=DNA_HISTORY_SESSIONS,history_sessions_available=len(ctx.sessions))
    if len(ctx.sessions)<DNA_HISTORY_SESSIONS:return result
    history=ctx.sessions[-DNA_HISTORY_SESSIONS:]
    mature=history[-(DNA_MATURE_EVENT_SESSIONS+1):-1]
    count_window=history[-DNA_SPEC['limit_count_window_sessions_including_T']:]
    raw=[];missing=[]
    for iid in sorted(pv):
        bars={s:_bar(ctx,s,iid) for s in history};events={s:_bar_limit_event(b,iid,s) for s,b in bars.items()}
        if any(b is None for b in bars.values()) or any(not _known_limit_flag(e.close_limit_up) or not _known_limit_flag(e.touched_limit_up) for e in events.values()):
            missing.append({'instrument_id':iid,'status':'INSUFFICIENT_COVERAGE','reason':'DNA_HISTORY_BARS_OR_EVENTS_INCOMPLETE'});continue
        row={'instrument_id':iid,'close_limit_n':0,'close_limit_success':0,'first_attempt_n':0,'first_attempt_success':0,'first_sealed_n':0,'first_sealed_red':0,'open_premiums':[]}
        for index in range(1,len(history)-1):
            s=history[index]
            if s not in mature:continue
            ev=events[s];prev=events[history[index-1]];nxt=bars[history[index+1]]
            close_ret=_next_premium(nxt,'close');open_ret=_next_premium(nxt,'open')
            if ev.close_limit_up==TRUE and close_ret is not None:
                row['close_limit_n']+=1;row['close_limit_success']+=close_ret>Fraction('0.05')
            if ev.touched_limit_up==TRUE and prev.close_limit_up==FALSE:
                row['first_attempt_n']+=1;row['first_attempt_success']+=ev.close_limit_up==TRUE
                if open_ret is not None:row['open_premiums'].append(open_ret)
                if ev.close_limit_up==TRUE and close_ret is not None:
                    row['first_sealed_n']+=1;row['first_sealed_red']+=close_ret>0
        n=row['first_attempt_n'];rate=Fraction(row['first_attempt_success'],n) if n else None
        if n<DNA_SPEC['min_first_board_attempts'] or rate<=Fraction(str(DNA_SPEC['raw_first_board_seal_rate_gt'])) or not row['close_limit_n'] or not row['first_sealed_n'] or not row['open_premiums']:
            missing.append({'instrument_id':iid,'status':'INSUFFICIENT_COVERAGE','reason':'DNA_RELIABILITY_GATE_NOT_MET',
                'sample_counts':{k:v for k,v in row.items() if k.endswith('_n')},'raw_first_seal_rate':_number(rate)});continue
        row['limit_count_250d']=sum(events[s].close_limit_up==TRUE for s in count_window)
        row['raw_first_seal_rate']=rate;raw.append(row)
    if not raw:
        result.update(reason='DNA_RELIABILITY_GATE_NOT_MET',items=missing);return result
    # The event-weighted prior contains only the frozen reliability-qualified cohort.
    premiums=[p for r in raw for p in r['open_premiums']]
    pool_mean=sum(premiums,Fraction(0))/len(premiums)
    for row in raw:
        n=len(row['open_premiums']);prior=Fraction(str(DNA_SPEC['mean_prior_strength']))
        row['_factors']={'LB_PREMIUM5':_wilson_lower_bound(row['close_limit_success'],row['close_limit_n']),
            'LB_FIRST_RED':_wilson_lower_bound(row['first_sealed_red'],row['first_sealed_n']),
            'LB_FIRST_SEAL':_wilson_lower_bound(row['first_attempt_success'],row['first_attempt_n']),
            'M_ADJ':(sum(row['open_premiums'],Fraction(0))+prior*pool_mean)/(n+prior),
            'LIMIT_COUNT_250D':Fraction(row['limit_count_250d'])}
    maps={k:_percentile_map(r['_factors'][k] for r in raw) for k in DNA_WEIGHTS}
    rebased=_pv_population_scores(pv,[r['instrument_id'] for r in raw]);enriched=[]
    for row in raw:
        iid=row['instrument_id'];percentiles={k:maps[k][row['_factors'][k]] for k in DNA_WEIGHTS}
        g=_score_from_decimals(DNA_WEIGHTS,percentiles)
        score=Fraction(str(DNA_SPEC['combination']['pv_score_on_gene_eligible_cohort']))*rebased[iid]+Fraction(str(DNA_SPEC['combination']['gene_score']))*g
        enriched.append({**pv[iid],'status':'EVALUATED','original_S_PV':pv[iid]['S_PV'],'S_PV_GENE_COHORT':_number(rebased[iid]),'G':_number(g),'S_DNA':_number(score),
            'factors':{k:_number(v) for k,v in row['_factors'].items()},'factor_percentiles':{k:_number(v) for k,v in percentiles.items()},
            'sample_counts':{**{k:v for k,v in row.items() if k.endswith('_n')},'open_premium_n':len(row['open_premiums'])},
            'success_counts':{k:row[k] for k in ('close_limit_success','first_attempt_success','first_sealed_red')},
            'raw_first_seal_rate':_number(row['raw_first_seal_rate']),'m_pool':_number(pool_mean),'m_pool_event_n':len(premiums),'m_pool_instrument_ids':sorted(rebased),
            'window':{'history':history,'mature_event_sessions':mature,'limit_count_sessions':count_window},'_score':score})
    ranked=sorted(enriched,key=lambda r:(-r['_score'],r['instrument_id']))
    for rank,row in enumerate(ranked,1):row['rank']=rank
    result.update(status='COMPLETE' if not missing else 'LIMITED_SCOPE',reason=None,formula='0.60*S_PV_GENE_COHORT + 0.40*G',
        items=[_public(r) for r in ranked]+missing,top_ids=[r['instrument_id'] for r in ranked[:top_n]],qualified_ids=sorted(rebased),
        counts={'eligible':len(pv),'evaluated':len(ranked),'insufficient':len(missing)})
    return result

def run_dna_reversal(bundle,pv_result=None,*,top_n=100,cutoff=None,_context=None):
    return {**run_dna_rerank(bundle,pv_result,top_n=top_n,cutoff=cutoff,_context=_context),
        'strategy_id':DNA_REVERSAL_ID,'canonical_strategy_id':DNA_ID,'compatibility_alias':True}

def _theme_peer_stat(ctx,theme,members,target,window):
    peers=sorted(set(members)-{target});values=[];up=limits=0;refs=set(theme['source_refs'])
    for iid in peers:
        bars=[_bar(ctx,s,iid) for s in window]
        if any(b is None or not _valid_ohlc(b) for b in bars):continue
        basis=bars[0].get('comparable_basis')
        if not basis or any(b.get('comparable_basis')!=basis or _bar_decimal(b,'comparable_close') is None for b in bars):continue
        ret5=_comparable_return(bars[-1],bars[0]);ret1=_comparable_return(bars[-1],bars[-2]);event=_bar_limit_event(bars[-1],iid,window[-1])
        if ret5 is None or ret1 is None or not _known_limit_flag(event.close_limit_up):continue
        values.append(ret5);up+=ret1>0;limits+=event.close_limit_up==TRUE
        refs.update(r for b in bars for r in b.get('source_refs',()))
    coverage=Fraction(len(values),len(peers)) if peers else Fraction(0)
    if coverage<THEME_PEER_COVERAGE_MIN:return None
    return {'theme_id':theme['theme_id'],'theme_name':theme['name'],'member_count':len(members),'peer_expected':len(peers),'peer_observed':len(values),
        'peer_coverage':coverage,'peer_ret5_median':median(values),'peer_up_fraction':Fraction(up,len(values)),
        'peer_limit_count':Fraction(limits),'source_refs':sorted(refs)}

def run_theme_context(bundle,pv_result=None,*,top_n=100,cutoff=None,_context=None):
    ctx=_context or _prepare_context(bundle,cutoff)
    pv_result=pv_result if pv_result is not None else run_pv_observation(bundle,cutoff=cutoff,_context=ctx)
    pv={r['instrument_id']:r for r in pv_result.get('items',[]) if r['instrument_id'] in ctx.eligible_ids};window=_prior_sessions(ctx,6)
    result=_enhancement_base(THEME_ID,ctx,pv,'THEME_EVENT_SNAPSHOT_REQUIRED')
    result['emotion_context']=_emotion_context(ctx)
    if len(window)<6:result['reason']='THEME_6_SESSION_WINDOW_REQUIRED';return result
    frozen=[];eligible_set=set(ctx.eligible_ids)
    for theme in sorted(ctx.themes,key=lambda t:t['theme_id']):
        members=sorted(set(theme['member_ids'])&eligible_set)
        if len(members)<THEME_SPEC['min_theme_members']:continue
        if _theme_peer_stat(ctx,theme,members,None,window) is not None:frozen.append((theme,members))
    if not frozen:return result
    chosen={};all_choices={};missing=[]
    factor_fields=[('PEER_LIMIT_COUNT','peer_limit_count'),('PEER_RET5_MEDIAN','peer_ret5_median'),('PEER_UP_FRACTION','peer_up_fraction')]
    for iid in sorted(pv):
        # Every dated theme contributes once, after removing this target wherever present.
        population=[r for theme,members in frozen if (r:=_theme_peer_stat(ctx,theme,members,iid,window)) is not None]
        if len(population)!=len(frozen):
            missing.append({'instrument_id':iid,'status':'INSUFFICIENT_COVERAGE','reason':'FROZEN_THEME_POPULATION_UNAVAILABLE_AFTER_SELF_REMOVAL'})
            continue
        maps={key:_percentile_map(r[field] for r in population) for key,field in factor_fields}
        owned={t['theme_id'] for t,m in frozen if iid in m};choices=[]
        for row in population:
            if row['theme_id'] not in owned:continue
            p={k:maps[k][row[field]] for k,field in factor_fields}
            row.update(T=_score_from_decimals(THEME_WEIGHTS,p),factor_percentiles=p,
                percentile_population_theme_ids=[r['theme_id'] for r in population]);choices.append(row)
        if choices:
            choices.sort(key=lambda r:(-r['T'],r['theme_id']));chosen[iid]=choices[0];all_choices[iid]=choices
        else:missing.append({'instrument_id':iid,'status':'INSUFFICIENT_COVERAGE','reason':'THEME_MEMBER_OR_PEER_COVERAGE_GATE_NOT_MET'})
    rebased=_pv_population_scores(pv,chosen);ranked=[]
    def display_theme(row):
        return {k:({a:_number(b) for a,b in v.items()} if k=='factor_percentiles' else _number(v) if isinstance(v,(Fraction,Decimal)) else v) for k,v in row.items()}
    for iid,row in chosen.items():
        score=Fraction(str(THEME_SPEC['combination']['pv_score_on_theme_eligible_cohort']))*rebased[iid]+Fraction(str(THEME_SPEC['combination']['theme_score']))*row['T']
        ranked.append({**pv[iid],**display_theme(row),'status':'EVALUATED','original_S_PV':pv[iid]['S_PV'],
            'S_PV_THEME_COHORT':_number(rebased[iid]),'S_THEME':_number(score),'candidate_themes':[display_theme(r) for r in all_choices[iid]],
            'selection_reason':'MAX_THEME_SCORE_THEN_THEME_ID_ASC','_score':score})
    ranked.sort(key=lambda r:(-r['_score'],r['instrument_id']))
    for rank,row in enumerate(ranked,1):row['rank']=rank
    result.update(status=('COMPLETE' if not missing else 'LIMITED_SCOPE') if ranked else 'INSUFFICIENT_COVERAGE',
        reason=None if ranked else 'THEME_PEER_COVERAGE_GATE_NOT_MET',formula='0.75*S_PV_THEME_COHORT + 0.25*T',
        items=[_public(r) for r in ranked]+missing,top_ids=[r['instrument_id'] for r in ranked[:top_n]],qualified_ids=sorted(chosen),
        frozen_theme_ids=[t['theme_id'] for t,m in frozen],
        counts={'eligible':len(pv),'evaluated':len(ranked),'insufficient':len(missing),'theme_snapshot_count':len(frozen)})
    return result

def _emotion_context(ctx):
    """Descriptive facts on the declared current-qualified cohort; no score vote."""
    output={'role':'CONTEXT_AND_STRATIFIED_EVALUATION_NO_NEW_HARD_VETO',
        'cohort':'CURRENT_DATE_UNIQUE_QUALIFIED_SECURITIES','expected':len(ctx.eligible_ids),
        'status':'UNAVAILABLE','reason':'VERIFIED_EVENTS_OR_HISTORY_INSUFFICIENT'}
    if len(ctx.sessions)<2:return output
    today,yesterday=ctx.sessions[-1],ctx.sessions[-2];observed=[];queue=[];buckets={'1':[],'2':[],'3_PLUS':[]}
    width={'close_limit_count':0,'touched_not_closed_count':0};heights=[]
    for iid in ctx.eligible_ids:
        previous=_bar_limit_event(_bar(ctx,yesterday,iid),iid,yesterday)
        current=_bar_limit_event(_bar(ctx,today,iid),iid,today)
        if not _known_limit_flag(previous.close_limit_up) or not _known_limit_flag(current.close_limit_up):continue
        history=[_bar_limit_event(_bar(ctx,s,iid),iid,s) for s in ctx.sessions[-22:]]
        annotated=annotate_streaks(history);previous,current=annotated[-2:]
        observed.append(iid);width['close_limit_count']+=current.close_limit_up==TRUE
        width['touched_not_closed_count']+=current.touched_not_closed==TRUE
        if current.close_limit_up==TRUE and current.streak_exchange is not None:heights.append(current.streak_exchange)
        if previous.close_limit_up==TRUE:
            ret=_comparable_return(_bar(ctx,today,iid),_bar(ctx,yesterday,iid))
            queue.append({'instrument_id':iid,'return':_number(ret),'positive':ret>0 if ret is not None else None,'resealed':current.close_limit_up==TRUE})
            streak=previous.streak_exchange
            if streak is not None:buckets['1' if streak==1 else '2' if streak==2 else '3_PLUS'].append(current.close_limit_up==TRUE)
    coverage=Fraction(len(observed),len(ctx.eligible_ids)) if ctx.eligible_ids else Fraction(0)
    output.update(observed=len(observed),coverage=_number(coverage),scope_label='FULL_SCOPE' if ctx.universe_complete else 'LIMITED_SCOPE')
    if coverage<PV_QUOTE_COVERAGE_MIN:return output
    returns=[r['return'] for r in queue if r['return'] is not None]
    output.update(status='COMPLETE' if ctx.universe_complete else 'LIMITED_SCOPE',reason=None,
        promotions={k:{'n':len(v),'successes':sum(v),'rate':sum(v)/len(v) if v else None,'status':'AVAILABLE' if v else 'NO_COHORT'} for k,v in buckets.items()},
        previous_limit_queue={'n':len(queue),'valid_price_n':len(returns),'median_return':median(returns) if returns else None,
            'positive_fraction':sum(x>0 for x in returns)/len(returns) if returns else None,
            'reseal_fraction':sum(r['resealed'] for r in queue)/len(queue) if queue else None,'items':queue},
        width=width,verified_max_height=max(heights) if heights else None,
        ladder_gaps=[n for n in range(1,max(heights)) if n not in heights] if heights else [],
        source_refs=sorted(ctx.resolver.used))
    return output

def run_theme_limitup(bundle,pv_result=None,*,top_n=100,cutoff=None,_context=None):
    return {**run_theme_context(bundle,pv_result,top_n=top_n,cutoff=cutoff,_context=_context),
        'strategy_id':THEME_LIMITUP_ID,'canonical_strategy_id':THEME_ID,'compatibility_alias':True}

def strategy_comparison(daily_summary,pv,dna,theme,focus=None,extra_strategies=None):
    candidate_sets={k:set(v.get('top_ids',[])) for k,v in [('PV',pv),('DNA',dna),('THEME',theme)]}
    rows=[{'strategy_id':x.get('canonical_strategy_id',x.get('strategy_id')),'status':x.get('status'),'scope_label':x.get('scope_label'),
        'top_ids':x.get('top_ids',[]),'actual_top_ids':x.get('top_ids',[]),'top_ids_source':'ACTUAL_STRATEGY','count':len(x.get('top_ids',[])),
        'full_rank_count':sum(r.get('rank') is not None for r in x.get('items',[]))} for x in (pv,dna,theme)]
    for x in extra_strategies or ():
        rows.append({'strategy_id':x.get('canonical_strategy_id',x.get('strategy_id')),'status':x.get('status'),'scope_label':x.get('scope_label'),
            'top_ids':x.get('top_ids',[]),'actual_top_ids':x.get('top_ids',[]),'top_ids_source':'ACTUAL_STRATEGY','count':len(x.get('top_ids',[])),
            'full_rank_count':sum(r.get('rank') is not None for r in x.get('items',[])),
            'formal_list_policy':x.get('formal_list_policy'),'parameter_status_label_zh':x.get('parameter_status_label_zh')})
    return {'comparison_version':'R05_STRATEGY_COMPARISON_V1','parameter_status':PARAMETER_STATUS,'strategies':rows,
        'focus':{'strategy_id':FOCUS_POLICY_ID,'selected_ids':(focus or {}).get('selected_ids',[]),'count':len((focus or {}).get('selected_ids',[]))},
        'overlaps':{'pv_dna':sorted(candidate_sets['PV']&candidate_sets['DNA']),'pv_theme':sorted(candidate_sets['PV']&candidate_sets['THEME']),
            'dna_theme':sorted(candidate_sets['DNA']&candidate_sets['THEME']),'all_three':sorted(set.intersection(*candidate_sets.values()))},
        'overlap_role':'DESCRIPTIVE_ONLY_NOT_COMMON_ELIGIBILITY_PERFORMANCE','published_reference':daily_summary.get('run_id') if daily_summary else None,
        'market_edge':'NOT_ESTABLISHED'}

def implementation_conformance(run):
    """Append this assessment separately; never rewrite the historical run."""
    version=run.get('implementation_version')
    identity=source_identity()
    conformant=(version==IMPLEMENTATION_VERSION and run.get('spec_hash')==identity['spec_hash']
        and run.get('source_hash')==identity['source_hash'] and run.get('config_hash')==identity['config_hash']
        and bool(run.get('decision_cutoff')) and all(len(str(run.get(k,'')))==64 for k in ('input_hash','universe_hash')))
    return {'status':'SPEC_CONFORMANT' if conformant else 'SPEC_NONCONFORMANT' if version=='R05_STRATEGY_EXPERIMENTS_V2' else 'SPEC_NOT_VERIFIED',
        'run_id':run.get('run_id'),'implementation_version':version,'assessment_policy':'R05_SPEC_RECTIFICATION_AUDIT_V1',
        'exclude_from_compliant_performance':not conformant,'preserve_original_record':True}

def source_identity():
    _assert_frozen_specs()
    paths=[
        Path(__file__),
        ROOT/'stockfish_core/yaogu/visibility.py',
        ROOT/'stockfish_core/yaogu/contracts.py',
        ROOT/'stockfish_core/yaogu/history_artifacts.py',
        ROOT/'stockfish_core/yaogu/events.py',
        ROOT/'stockfish_core/yaogu/theme_capital_trend.py',
        ROOT/'stockfish_core/yaogu/stage_aware_momentum.py',
        ROOT/'config/theme_capital_trend_v1.yaml',
        ROOT/'config/stage_aware_momentum_v2.yaml',
        SOURCE_LOCK_PATH,
    ]
    hashes={str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    baseline=ROOT/SPEC['baseline']['config_path']
    return {'source_hash':digest(hashes),'source_file_hashes':hashes,'spec_hash':digest({'strategy':SPEC,'completion':COMPLETION}),
        'config_hash':digest(SPEC),'selection_policy_hash':digest(FOCUS_POLICY),
        'strategy_dependency_source_hashes':{k:v for k,v in hashes.items() if k.startswith('stockfish_core/yaogu/theme_capital_trend') or k.startswith('stockfish_core/yaogu/stage_aware_momentum') or k.startswith('config/theme_capital_trend') or k.startswith('config/stage_aware_momentum')},
        'spec_source_hashes':{SPEC_PATH.name:hashlib.sha256(SPEC_PATH.read_bytes()).hexdigest(),COMPLETION_PATH.name:hashlib.sha256(COMPLETION_PATH.read_bytes()).hexdigest()},
        'frozen_daily_config_hash':hashlib.sha256(baseline.read_bytes()).hexdigest(),'upstream_source_lock':SOURCE_LOCK['upstream_research']}

def run_strategy_experiments(bundle,daily_summary=None,*,top_n=100,cutoff=None,config=None):
    ctx=_prepare_context(bundle,cutoff);pv=run_pv_observation(bundle,top_n=top_n,cutoff=cutoff,_context=ctx);focus=run_pv_focus_preview(pv)
    pv.update(selection_policy_id=FOCUS_POLICY_ID,focus_count=focus['count'],focus_ids=focus['selected_ids'])
    dna=run_dna_rerank(bundle,pv,top_n=top_n,cutoff=cutoff,_context=ctx);theme=run_theme_context(bundle,pv,top_n=top_n,cutoff=cutoff,_context=ctx)
    from .stage_aware_momentum import run_stage_aware_momentum
    from .theme_capital_trend import run_simple_trend_template, run_theme_capital_trend, run_theme_capital_trend_technical_only
    theme_capital_technical=run_theme_capital_trend_technical_only(bundle,top_n=top_n,cutoff=cutoff,_context=ctx)
    theme_capital=run_theme_capital_trend(bundle,top_n=top_n,cutoff=cutoff,_context=ctx)
    simple_trend=run_simple_trend_template(bundle,top_n=top_n,cutoff=cutoff,_context=ctx)
    stage_aware=run_stage_aware_momentum(bundle,top_n=top_n,cutoff=cutoff,_context=ctx)
    from .history_artifacts import history_identity, digest_market_input, digest_json_stream
    identity=source_identity();input_hash=digest_market_input(bundle);deps=ctx.resolver.effective_dependencies()
    payload={'strategy_family':'R05','input_id':'market_input_'+input_hash[:16],'input_hash':input_hash,'observation_date':ctx.observation_date,
        'implementation_version':IMPLEMENTATION_VERSION,'parameter_status':PARAMETER_STATUS,'decision_cutoff':ctx.cutoff.isoformat(),'coverage_type':bundle.coverage_type,
        'audit_mode':'HISTORICAL_RECONSTRUCTION' if bundle.coverage_type=='RECONSTRUCTED_HISTORICAL' else 'ARCHIVED_POINT_IN_TIME' if bundle.coverage_type=='PROSPECTIVE_CAPTURED' else bundle.coverage_type,
        'spec_conformance':'SPEC_CONFORMANT','qualification':ctx.qualification,'universe_hash':digest(ctx.qualification),'effective_input_hash':digest_json_stream(deps),
        'source_refs':sorted(ctx.resolver.used),'data_watermark':max((r.ingested_at.isoformat() for r in bundle.receipts),default=None),**identity,
        **history_identity(bundle),
        'results':{PV_OBSERVATION_ID:pv,DNA_ID:dna,THEME_ID:theme,
            theme_capital_technical['strategy_id']:theme_capital_technical,
            theme_capital['strategy_id']:theme_capital,
            simple_trend['strategy_id']:simple_trend,
            stage_aware['strategy_id']:stage_aware},
        'selection_results':{FOCUS_POLICY_ID:focus}}
    for result in payload['results'].values():
        for key,value in identity.items():result.setdefault(key,value)
        result.update(input_hash=input_hash,decision_cutoff=ctx.cutoff.isoformat(),coverage_type=bundle.coverage_type,**history_identity(bundle))
    payload['results'][DNA_REVERSAL_ID]={**dna,'strategy_id':DNA_REVERSAL_ID,'canonical_strategy_id':DNA_ID,'compatibility_alias':True}
    payload['results'][THEME_LIMITUP_ID]={**theme,'strategy_id':THEME_LIMITUP_ID,'canonical_strategy_id':THEME_ID,'compatibility_alias':True}
    payload['comparison']=strategy_comparison(daily_summary,pv,dna,theme,focus,(theme_capital_technical,theme_capital,simple_trend,stage_aware))
    payload['run_id']=_stable_id('strategy_experiment',{k:payload[k] for k in ('input_hash','observation_date','decision_cutoff','spec_hash','source_hash','implementation_version','universe_hash')})
    return payload

def strategy_specs():
    from .stage_aware_momentum import stage_aware_strategy_specs
    from .theme_capital_trend import theme_capital_strategy_specs
    return {'task_id':'R05','implementation_version':IMPLEMENTATION_VERSION,**source_identity(),
        'strategies':[{'strategy_id':'DAILY_V1','description':'原五族冻结对照，缺数据不由PV替代。','config_hash':source_identity()['frozen_daily_config_hash']},
            *[{'strategy_id':k,'parameter_status':PARAMETER_STATUS,'spec':v} for k,v in SPEC['strategies'].items()],
            {'strategy_id':FOCUS_POLICY_ID,'selection_policy':FOCUS_POLICY,'parameter_status':PARAMETER_STATUS},
            *theme_capital_strategy_specs(),
            *stage_aware_strategy_specs()]}

STRATEGY_SPECS=strategy_specs()['strategies']
