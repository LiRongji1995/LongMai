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
FOCUS_POLICY_ID = COMPLETION['selection_policy']['id']
IMPLEMENTATION_VERSION = 'R05_STRATEGY_EXPERIMENTS_V4_CANONICAL_IDENTITY'
PARAMETER_STATUS = SPEC['parameter_status']
PV_SPEC = SPEC['strategies'][PV_OBSERVATION_ID]
PV_WEIGHTS = {k: Decimal(str(PV_SPEC['weights']['percentile_'+k])) for k in ('A','R','H')}
PV_QUOTE_COVERAGE_MIN = Decimal(str(SPEC['universe']['quote_coverage_min']))
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

def percentile_rank_midpoint_map(values):
    population=[value for value in map(_fraction,values) if value is not None]
    return _percentile_map(population) if population else {}

def percentile_rank_midpoint_lookup(value,percentile_map):
    value=_fraction(value)
    if value is None or not percentile_map:return None
    result=percentile_map.get(value)
    return float(_as_decimal(result)) if result is not None else None

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
    if bundle.history_artifact_ref:
        from .data_pipeline import stage_aware_history_sessions_required
        history_start=sessions[-min(len(sessions),stage_aware_history_sessions_required())]
        bars=InstrumentBars(bundle,resolver,rejected,start=history_start,compressed_cache_bytes=8*1024*1024)
    tg=defaultdict(list)
    for t in bundle.themes:
        proof=resolver.resolve(t,session=day,require_complete=True)
        if proof.allowed and t.theme_type=='EVENT':tg[t.theme_id].append(t)
        else:rejected.append({'kind':'THEME','theme_id':t.theme_id,'reason_codes':list(proof.reason_codes) or ['NOT_EVENT_THEME']})
    themes=[x.model_dump() for rows in tg.values() if (x:=_select_revision(rows)) is not None]
    partition={'partition_version':'R05_UNIQUE_DATED_QUALIFICATION_V1','master_expected':expected,'identity_observed':len(groups),'identity_absent':absent,
        'eligible':len(eligible),'eligible_ids':eligible,'known_excluded':len(excluded),'known_excluded_records':excluded,
        'eligibility_unknown':len(unknown)+absent,'eligibility_unknown_records':unknown,'selected_security_hash':digest({i:s.model_dump(mode='json') for i,s in states.items() if s is not None}),
        'selected_records':[{'instrument_id':i,'name':s.name,'exchange':s.exchange,'board':s.board,'security_type':s.security_type,
            'effective_from':str(s.effective_from),'effective_to':str(s.effective_to) if s.effective_to else None,
            'listing_exchange_sessions':sum(s.listed_on<=d<=day for d in sessions),'source_refs':list(s.source_refs),'record_hash':digest(s)} for i,s in sorted(states.items()) if s is not None],
        'universe_evidence_reason_codes':list(universe_proof.reason_codes)}
    return _ExperimentContext(bundle,str(day),list(map(str,sessions)),eligible,bars,universe_complete,resolver,states,partition,rejected,cutoff,themes)

def _bar(ctx,session,iid):
    cache=getattr(ctx,'scalar_cache',None)
    key=('BAR',str(session),iid)
    if cache is not None and key in cache:return cache[key]
    if hasattr(ctx.bars_by_session,'bar'):return ctx.bars_by_session.bar(session,iid)
    return ctx.bars_by_session.get(str(session),{}).get(iid)

def _preload_bars(ctx,sessions,eligible_ids=None):
    cache=getattr(ctx,'scalar_cache',None)
    if cache is None or not sessions:return None
    sessions=tuple(str(session)[:10] for session in sessions)
    eligible=tuple(eligible_ids or ctx.eligible_ids)
    marker=('BAR_PRELOAD',sessions,eligible)
    if marker in cache:return None
    if hasattr(ctx.bars_by_session,'bundle'):
        from .coverage_diagnostics import _selected_bar_map
        from .history_artifacts import daily_bar_payload
        selected=_selected_bar_map(ctx,sessions,eligible)
        for session in sessions:
            for iid in eligible:
                row=selected.get((session,iid))
                if row is not None:
                    row=daily_bar_payload(row)
                    proof=ctx.resolver.resolve(row,instrument_id=iid,session=session,require_session=True)
                    if not proof.allowed:
                        ctx.rejected_evidence.append({'kind':'BAR','instrument_id':iid,'session':session,'reason_codes':list(proof.reason_codes)})
                        row=None
                cache[('BAR',session,iid)]=row
    else:
        for session in sessions:
            rows=ctx.bars_by_session.get(session,{})
            for iid in eligible:
                cache[('BAR',session,iid)]=rows.get(iid)
    cache[marker]=True
    return None

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
    _preload_bars(ctx,window,ctx.eligible_ids)
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


def strategy_comparison(daily_summary, pv, stage_aware, focus=None):
    rows = [{'strategy_id': x.get('strategy_id'), 'status': x.get('status'),
        'scope_label': x.get('scope_label'), 'data_scope_status': x.get('data_scope_status'),
        'coverage_scope': x.get('coverage_scope'), 'top_ids': x.get('top_ids', []),
        'actual_top_ids': x.get('top_ids', []), 'top_ids_source': 'ACTUAL_STRATEGY',
        'count': len(x.get('top_ids', [])),
        'full_rank_count': sum(row.get('rank') is not None for row in x.get('items', []))}
        for x in (pv, stage_aware)]
    return {'comparison_version': 'PERSONAL_DAILY_COMPARISON_V1',
        'parameter_status': PARAMETER_STATUS, 'strategies': rows,
        'focus': {'strategy_id': FOCUS_POLICY_ID,
            'selected_ids': (focus or {}).get('selected_ids', []),
            'count': len((focus or {}).get('selected_ids', []))},
        'published_reference': daily_summary.get('run_id') if daily_summary else None,
        'DAILY_V1': daily_summary or {'status': 'UNKNOWN_NOT_EXECUTED'},
        'market_edge': 'NOT_ESTABLISHED'}

def implementation_conformance(run):
    """Append this assessment separately; never rewrite the historical run."""
    version=run.get('implementation_version')
    if run.get('execution_profile') == 'SHORT_HORIZON_RESEARCH_SINGLE':
        from .short_horizon_profiles import short_horizon_module
        identity = short_horizon_module(run.get('strategy_id', 'SHORT_HORIZON_STRENGTH_R1')).short_horizon_source_identity()
        conformant = (all(run.get(key) == identity.get(key) for key in
            ('implementation_version', 'spec_hash', 'source_hash', 'config_hash', 'feature_version'))
            and bool(run.get('decision_cutoff'))
            and all(len(str(run.get(key, ''))) == 64 for key in ('input_hash', 'universe_hash')))
        return {'status': 'SPEC_CONFORMANT' if conformant else 'SPEC_NOT_VERIFIED',
            'run_id': run.get('run_id'), 'implementation_version': version,
            'assessment_policy': 'SHORT_HORIZON_RESEARCH_IDENTITY_' + run.get('strategy_id', 'SHORT_HORIZON_STRENGTH_R1').rsplit('_', 1)[-1],
            'exclude_from_compliant_performance': not conformant, 'preserve_original_record': True}
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
        ROOT/'stockfish_core/yaogu/source_resolution.py',
        ROOT/'stockfish_core/yaogu/derived_feature_store.py',
        ROOT/'stockfish_core/yaogu/events.py',
        ROOT/'stockfish_core/yaogu/theme_capital_trend.py',
        ROOT/'stockfish_core/yaogu/stage_aware_momentum.py',
        ROOT/'stockfish_core/yaogu/coverage_diagnostics.py',
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
    """Run the confirmed PV and active V2 rules against one evidence context."""
    ctx=_prepare_context(bundle,cutoff);pv=run_pv_observation(bundle,top_n=top_n,cutoff=cutoff,_context=ctx);focus=run_pv_focus_preview(pv)
    pv.update(selection_policy_id=FOCUS_POLICY_ID,focus_count=focus['count'],focus_ids=focus['selected_ids'])
    from .stage_aware_momentum import run_stage_aware_momentum
    stage_aware=run_stage_aware_momentum(bundle,top_n=top_n,cutoff=cutoff,_context=ctx)
    from .coverage_diagnostics import build_coverage_diagnostics, json_safe
    data_coverage=json_safe(build_coverage_diagnostics(ctx,pv_result=pv,stage_aware_result=stage_aware))
    pv_scope=data_coverage['coverage_scope']['PV_READINESS']
    trend_scope=data_coverage['coverage_scope']['TREND_READINESS']
    context_scope=data_coverage['coverage_scope']['CONTEXT_READINESS']
    pv.update(data_scope_status=pv_scope['status'],coverage_scope=pv_scope,
        full_market_gate_policy=data_coverage['coverage_scope']['gate_policy'])
    for result in (stage_aware,):
        result.update(data_scope_status=trend_scope['status'],coverage_scope=trend_scope,
            context_scope_status=context_scope['status'],context_coverage_scope=context_scope,
            full_market_gate_policy=data_coverage['coverage_scope']['gate_policy'])
    from .history_artifacts import history_identity, digest_market_input, digest_json_stream
    identity=source_identity();input_hash=digest_market_input(bundle);deps=ctx.resolver.effective_dependencies()
    payload={'strategy_family':'R05','input_id':'market_input_'+input_hash[:16],'input_hash':input_hash,'observation_date':ctx.observation_date,
        'implementation_version':IMPLEMENTATION_VERSION,'parameter_status':PARAMETER_STATUS,'decision_cutoff':ctx.cutoff.isoformat(),'coverage_type':bundle.coverage_type,
        'audit_mode':'HISTORICAL_RECONSTRUCTION' if bundle.coverage_type=='RECONSTRUCTED_HISTORICAL' else 'ARCHIVED_POINT_IN_TIME' if bundle.coverage_type=='PROSPECTIVE_CAPTURED' else bundle.coverage_type,
        'spec_conformance':'SPEC_CONFORMANT','qualification':ctx.qualification,'universe_hash':digest(ctx.qualification),'effective_input_hash':digest_json_stream(deps),
        'data_coverage_diagnostics':data_coverage,'data_readiness':data_coverage['coverage_scope'],
        'source_refs':sorted(ctx.resolver.used),'data_watermark':max((r.ingested_at.isoformat() for r in bundle.receipts),default=None),**identity,
        **history_identity(bundle),
        'results':{PV_OBSERVATION_ID:pv,stage_aware['strategy_id']:stage_aware},
        'selection_results':{FOCUS_POLICY_ID:focus}}
    for result in payload['results'].values():
        for key,value in identity.items():result.setdefault(key,value)
        result.update(input_hash=input_hash,decision_cutoff=ctx.cutoff.isoformat(),coverage_type=bundle.coverage_type,**history_identity(bundle))
    payload['comparison']=strategy_comparison(daily_summary,pv,stage_aware,focus)
    payload['run_id']=_stable_id('strategy_experiment',{k:payload[k] for k in ('input_hash','observation_date','decision_cutoff','spec_hash','source_hash','implementation_version','universe_hash')})
    return payload

def _stage_aware_focus_selection(stage_aware):
    focus = [row for row in stage_aware.get('items', ()) if row.get('decision_status') == 'FOCUS']
    return {'selection_policy_id':'STAGE_AWARE_MOMENTUM_V2_FOCUS','strategy_id':'STAGE_AWARE_MOMENTUM_V2',
        'selected_ids':[row['instrument_id'] for row in focus],
        'count':len(focus),'selection_basis':'DECISION_STATUS_EQUALS_FOCUS',
        'selection_role':'DAILY_PRIMARY_ACTIVE_STRATEGY_FOCUS_ONLY',
        'items':[{'instrument_id':row['instrument_id'],'rank':row.get('rank'),'reason_codes':row.get('reason_codes',()),
            'primary_stage':row.get('primary_stage'),'decision_status':row.get('decision_status')} for row in focus]}

def _active_daily_comparison(stage_aware):
    return {'comparison_version':'R14_DAILY_PRIMARY_FAST_PATH_V1',
        'parameter_status':PARAMETER_STATUS,
        'strategies':[{'strategy_id':stage_aware.get('strategy_id'),'status':stage_aware.get('status'),
            'scope_label':stage_aware.get('scope_label'),'data_scope_status':stage_aware.get('data_scope_status'),
            'coverage_scope':stage_aware.get('coverage_scope'),'context_scope_status':stage_aware.get('context_scope_status'),
            'context_coverage_scope':stage_aware.get('context_coverage_scope'),'top_ids':stage_aware.get('top_ids',()),
            'actual_top_ids':stage_aware.get('top_ids',()),'top_ids_source':'ACTIVE_DAILY_STRATEGY',
            'count':len(stage_aware.get('top_ids',())),'full_rank_count':sum(r.get('rank') is not None for r in stage_aware.get('items',()))}],
        'research_full_suite_executed':False,
        'market_edge':'NOT_ESTABLISHED'}

def run_active_daily_strategy(bundle,daily_summary=None,*,top_n=100,cutoff=None,config=None,
                              derived_store=None,input_id=None,execution_profile='DAILY_PRIMARY_FAST'):
    from .coverage_diagnostics import build_coverage_diagnostics, json_safe
    from .history_artifacts import history_identity, digest_market_input, digest_json_stream
    from .stage_aware_momentum import run_stage_aware_momentum

    ctx=_prepare_context(bundle,cutoff)
    input_hash=digest_market_input(bundle)
    if derived_store is not None:
        ctx.scalar_cache['DERIVED_FEATURE_STORE']=derived_store
        ctx.scalar_cache['DERIVED_INPUT_HASH']=input_hash
        ctx.scalar_cache['DERIVED_FEATURE_MODE']='DAILY_INCREMENTAL'
    stage_aware=run_stage_aware_momentum(bundle,top_n=top_n,cutoff=cutoff,_context=ctx)
    data_coverage=json_safe(build_coverage_diagnostics(ctx,pv_result=None,stage_aware_result=stage_aware))
    trend_scope=data_coverage['coverage_scope']['TREND_READINESS']
    context_scope=data_coverage['coverage_scope']['CONTEXT_READINESS']
    stage_aware.update(data_scope_status=trend_scope['status'],coverage_scope=trend_scope,
        context_scope_status=context_scope['status'],context_coverage_scope=context_scope,
        full_market_gate_policy=data_coverage['coverage_scope']['gate_policy'])
    stage_aware['diagnostics'] = []
    stage_aware['diagnostics_storage_policy'] = 'DAILY_PRIMARY_ITEMS_RETAIN_FULL_ELIGIBLE_POOL_DIAGNOSTICS_NOT_DUPLICATED'
    identity=source_identity();deps=ctx.resolver.effective_dependencies()
    focus=_stage_aware_focus_selection(stage_aware)
    baseline_items=[]
    for iid in ctx.eligible_ids:
        bar=_bar(ctx,ctx.observation_date,iid)
        if bar and bar.get('comparable_close') is not None and bar.get('comparable_basis'):
            baseline_items.append({'instrument_id':iid,'rank':len(baseline_items)+1,
                'comparable_close':str(bar.get('comparable_close')),'comparable_basis':bar.get('comparable_basis'),
                'source_refs':list(bar.get('source_refs',()))})
    payload={'strategy_family':'R14_DAILY_PRIMARY','execution_profile':execution_profile,
        'input_id':input_id or 'market_input_'+input_hash[:16],'input_hash':input_hash,'observation_date':ctx.observation_date,
        'implementation_version':IMPLEMENTATION_VERSION,'parameter_status':PARAMETER_STATUS,'decision_cutoff':ctx.cutoff.isoformat(),
        'coverage_type':bundle.coverage_type,
        'audit_mode':'HISTORICAL_RECONSTRUCTION' if bundle.coverage_type=='RECONSTRUCTED_HISTORICAL' else 'ARCHIVED_POINT_IN_TIME' if bundle.coverage_type=='PROSPECTIVE_CAPTURED' else bundle.coverage_type,
        'spec_conformance':'SPEC_CONFORMANT','qualification':ctx.qualification,'universe_hash':digest(ctx.qualification),
        'effective_input_hash':digest_json_stream(deps),'data_coverage_diagnostics':data_coverage,
        'data_readiness':data_coverage['coverage_scope'],'source_refs':sorted(ctx.resolver.used),
        'data_watermark':max((r.ingested_at.isoformat() for r in bundle.receipts),default=None),**identity,
        **history_identity(bundle),
        'results':{'STAGE_AWARE_MOMENTUM_V2':stage_aware},
        'selection_results':{'STAGE_AWARE_MOMENTUM_V2_FOCUS':focus},
        'evaluation_baselines':{'ELIGIBLE_UNIVERSE_EQUAL_WEIGHT_PRICE_DIAGNOSTIC':{
            'strategy_id':'ELIGIBLE_UNIVERSE_EQUAL_WEIGHT_PRICE_DIAGNOSTIC',
            'status':'COMPLETE' if baseline_items else 'UNAVAILABLE',
            'rank_semantics':'INSTRUMENT_ID_ORDER_NOT_SELECTION_RANK','items':baseline_items,
            'cohort_rule':'FROZEN_DATED_ELIGIBLE_UNIVERSE_WITH_SIGNAL_COMPARABLE_CLOSE',
            'input_id':input_id or 'market_input_'+input_hash[:16],'decision_cutoff':ctx.cutoff.isoformat()}},
        'derived_feature_store':ctx.scalar_cache.get('R15_DERIVED_FEATURE_STATS') or ctx.scalar_cache.get('R14_DERIVED_FEATURE_STATS') or {
            'store_version':'DERIVED_FEATURE_STORE_V2','status':'NOT_USED'},
        'daily_fast_path':{'path_version':'DAILY_RUNTIME_BUDGET_V1','active_strategy_id':'STAGE_AWARE_MOMENTUM_V2',
            'research_arms_executed':[],'research_arms_not_executed':['PV_OBSERVATION_V1','DNA_RERANK_V1',
                'THEME_CONTEXT_V1','THEME_CAPITAL_TREND_TECHNICAL_ONLY_V1','THEME_CAPITAL_TREND_V1',
                'SIMPLE_TREND_TEMPLATE_REFERENCE_V1','STAGE_AWARE_MOMENTUM_V1'],
            'score_in_critical_path':False,'tracking_registration_required':True,
            'market_edge':'NOT_ESTABLISHED'}}
    for result in payload['results'].values():
        for key,value in identity.items():result.setdefault(key,value)
        result.update(input_hash=input_hash,decision_cutoff=ctx.cutoff.isoformat(),coverage_type=bundle.coverage_type,
            execution_profile=execution_profile,**history_identity(bundle))
    payload['comparison']=_active_daily_comparison(stage_aware)
    payload['run_id']=_stable_id('strategy_experiment',{k:payload[k] for k in ('input_hash','observation_date',
        'decision_cutoff','spec_hash','source_hash','implementation_version','universe_hash','execution_profile')})
    return payload

def run_short_horizon_strategy(bundle, *, cutoff=None, input_id=None, strategy_id='SHORT_HORIZON_STRENGTH_R1',
                               _frozen_base_run=None, _base_provenance=None,
                               _source_payload_reader=None, _source_receipts=None,
                               _theme_market_pack=None, _theme_market_pack_validator=None,
                               _theme_membership_mode='DATED_ONLY',
                               _target_calendar_context=None, _target_calendar_validator=None):
    """One explicitly selected research arm using the ordinary immutable input.

    This wrapper does not execute the full research suite or the active V2
    strategy. Observation selection and formal FOCUS remain distinct.
    """
    from .short_horizon_profiles import short_horizon_module
    module = short_horizon_module(strategy_id)
    SHORT_HORIZON_ID = module.SHORT_HORIZON_ID
    short_horizon_source_identity = module.short_horizon_source_identity
    from .history_artifacts import history_identity, digest_market_input, digest_json_stream

    identity = short_horizon_source_identity()
    enhanced = strategy_id == 'SHORT_HORIZON_STRENGTH_R2'
    if not enhanced and any(value is not None for value in
            (_source_payload_reader, _source_receipts, _theme_market_pack, _theme_market_pack_validator,
             _target_calendar_context, _target_calendar_validator)):
        raise ValueError('EXTRA_RESEARCH_CONTEXT_REQUIRES_R2')
    if (_target_calendar_context is None) != (_target_calendar_validator is None):
        raise ValueError('TARGET_CALENDAR_REQUIRES_NATIVE_SOURCE_VALIDATOR_AND_CONTEXT')
    if _target_calendar_context is not None:
        if not callable(_target_calendar_validator):
            raise ValueError('TARGET_CALENDAR_NATIVE_SOURCE_VALIDATOR_UNAVAILABLE')
    context_arguments = ({'_source_payload_reader': _source_payload_reader,
                          '_source_receipts': _source_receipts} if enhanced else {})
    ctx = (module.prepare_inline_style_context(bundle, cutoff) if _frozen_base_run is not None
           and strategy_id == 'SHORT_HORIZON_STRENGTH_R2' else _prepare_context(bundle, cutoff))
    if _target_calendar_context is not None:
        if _target_calendar_validator(_target_calendar_context, bundle=bundle, cutoff=ctx.cutoff) is False:
            raise ValueError('TARGET_CALENDAR_NATIVE_AUTHENTICATION_FAILED')
    if _frozen_base_run is None:
        result = module.run_short_horizon_strength(bundle, cutoff=ctx.cutoff, _context=ctx, **context_arguments)
    else:
        if strategy_id != 'SHORT_HORIZON_STRENGTH_R2':
            raise ValueError('FROZEN_BASE_REPLAY_REQUIRES_R2')
        result = module.apply_style_to_r1_result(bundle,
            _frozen_base_run['results']['SHORT_HORIZON_STRENGTH_R1'],
            cutoff=ctx.cutoff, _context=ctx, base_provenance=_base_provenance, base_run=_frozen_base_run,
            **context_arguments)
    if enhanced:
        from .theme_evolution import run_theme_evolution
        result['theme_evolution'] = run_theme_evolution(bundle, cutoff=ctx.cutoff,
            market_pack=_theme_market_pack, _market_pack_validator=_theme_market_pack_validator,
            membership_mode=_theme_membership_mode)
    if short_horizon_source_identity() != identity:
        raise ValueError('SHORT_HORIZON_RESEARCH_IDENTITY_CHANGED_DURING_EVALUATION')
    for row in result.get('items') or []:
        row.setdefault('display_order', row.get('rank'))
        row.setdefault('rank_semantics', result.get('rank_semantics'))
    input_hash = digest_market_input(bundle)
    selected_ids = list(result.get('research_selected_ids') or [])
    selected_set = set(selected_ids)
    selected_rows = [row for row in result.get('items') or []
                     if row.get('instrument_id') in selected_set]
    policy_id = SHORT_HORIZON_ID + '_OBSERVATION'
    scope = {'status': 'COMPLETE' if ctx.universe_complete else 'LIMITED_SCOPE',
             'scope_role': 'DATED_QUALIFICATION_ONLY_NOT_PRICE_WINDOW_OR_DAILY_V1_READINESS',
             'eligible': len(ctx.eligible_ids), 'evaluated': result.get('counts', {}).get('evaluated'),
             'universe_complete': ctx.universe_complete,
             'formal_recommendation_ready': False,
             'price_window_readiness': 'SEE_PER_INSTRUMENT_EXACT_FEATURE_EVIDENCE',
             'feature_unknown_instrument_count': (result.get('counts') or {}).get('unknown'),
             'fact_coverage': result.get('fact_coverage')}
    result.update(data_scope_status=scope['status'], coverage_scope=scope)
    payload = {'strategy_family': 'SHORT_HORIZON_STRENGTH_RESEARCH',
        'strategy_id': SHORT_HORIZON_ID, 'execution_profile': 'SHORT_HORIZON_RESEARCH_SINGLE',
        'input_id': input_id or 'market_' + input_hash, 'input_hash': input_hash,
        'observation_date': ctx.observation_date, 'decision_cutoff': ctx.cutoff.isoformat(),
        'coverage_type': bundle.coverage_type,
        'audit_mode': 'HISTORICAL_RECONSTRUCTION' if bundle.coverage_type == 'RECONSTRUCTED_HISTORICAL'
            else 'ARCHIVED_POINT_IN_TIME' if bundle.coverage_type == 'PROSPECTIVE_CAPTURED' else bundle.coverage_type,
        'rule_application_role': 'NEW_RESEARCH_VERSION_MATURITY_UNVALIDATED',
        'parameter_status': result.get('parameter_status'), 'spec_conformance': 'SPEC_CONFORMANT',
        'qualification': ctx.qualification, 'universe_hash': digest(ctx.qualification),
        'effective_input_hash': digest_json_stream(ctx.resolver.effective_dependencies()),
        'source_refs': sorted(ctx.resolver.used),
        'data_watermark': max((row.ingested_at.isoformat() for row in bundle.receipts), default=None),
        'data_readiness': {'SHORT_HORIZON_QUALIFICATION': scope,
            'SHORT_HORIZON_RESEARCH': {'status': 'NOT_FORMAL_READY',
                'scope_role': 'EXPLICIT_RESEARCH_OBSERVATION_NOT_PRODUCTION_RECOMMENDATION',
                'formal_recommendation_ready': False,
                'unknown_decision_count': (result.get('counts') or {}).get('unknown'),
                'evidence_policy': 'NO_MISSING_PRICE_OR_CONTEXT_IMPUTATION_SEE_PER_INSTRUMENT_DETAILS'}},
        'results': {SHORT_HORIZON_ID: result},
        'selection_results': {policy_id: {'selection_policy_id': policy_id,
            'strategy_id': SHORT_HORIZON_ID, 'selected_ids': selected_ids, 'count': len(selected_ids),
            'selection_basis': 'RESEARCH_SELECTED_BY_FROZEN_SHORT_HORIZON_RULES',
            'selection_role': 'RESEARCH_OBSERVATION_NOT_FORMAL_FOCUS',
            'items': [{'instrument_id': row['instrument_id'], 'rank': row.get('rank'),
                'primary_stage': row.get('primary_stage'), 'reason_codes': row.get('reason_codes', [])}
                for row in selected_rows]}},
        'prospective_validation': {'validation_role': 'EXPLICIT_SHORT_HORIZON_RESEARCH',
            'input_integrity_valid': True, 'signal_run_persisted': False,
            'official_primary_capture': False, 'official_validation_signal_countable': False,
            'official_primary_signal_countable': False,
            'signal_observation_date': ctx.observation_date,
            'coverage_type': bundle.coverage_type, 'market_edge': 'NOT_ESTABLISHED'},
        'signal_data_quality': {'signal_evaluable': False,
            'data_quality_status': 'RESEARCH_NOT_COUNTABLE_AS_V2_PRIMARY',
            'reason_codes': ['EXPLICIT_RESEARCH_STRATEGY_NOT_V2_PRIMARY']},
        'comparison': {'comparison_version': 'SHORT_HORIZON_RESEARCH_SINGLE_' + strategy_id.rsplit('_', 1)[-1],
            'strategies': [{'strategy_id': SHORT_HORIZON_ID, 'status': result.get('status'),
                'scope_label': result.get('scope_label'), 'counts': result.get('counts'),
                'research_selected_ids': selected_ids, 'top_ids': []}],
            'active_daily_strategy_executed': False, 'market_edge': 'NOT_ESTABLISHED'},
        'market_edge': 'NOT_ESTABLISHED', **identity, **history_identity(bundle)}
    result.update(input_hash=input_hash, decision_cutoff=ctx.cutoff.isoformat(),
        coverage_type=bundle.coverage_type, execution_profile=payload['execution_profile'],
        **history_identity(bundle))
    if _frozen_base_run is not None:
        payload.update(base_run_provenance=result['base_run_provenance'],
            rule_application_role='POSTHOC_VERIFIED_FROZEN_R1_PLUS_INLINE_STYLE')
        payload['source_refs'] = sorted(set(payload['source_refs']) | set(_frozen_base_run['source_refs']))
        payload['effective_input_hash'] = digest_json_stream({
            'new_inline_style_dependencies': ctx.resolver.effective_dependencies(),
            'frozen_r1_effective_input_hash': _frozen_base_run['effective_input_hash'],
            'frozen_r1_content_hash': _base_provenance['base_run_content_hash']})
    id_fields = ('input_hash', 'observation_date', 'decision_cutoff', 'spec_hash', 'source_hash',
                 'implementation_version', 'universe_hash', 'execution_profile', 'feature_version')
    if enhanced:
        context_layer = {'dynamic_liquidity': result.get('market_turnover_context'),
                         'theme_evolution': result.get('theme_evolution'),
                         'theme_market_pack_hash': digest_json_stream(_theme_market_pack) if _theme_market_pack is not None else None,
                         'theme_membership_mode': _theme_membership_mode,
                         'application_mode': 'VERIFIED_FROZEN_R1_CONTEXT_UPDATE' if _frozen_base_run is not None else 'NATIVE_R1_PLUS_CONTEXT',
                         'base_run_content_hash': (_base_provenance or {}).get('base_run_content_hash')}
        if _target_calendar_context is not None:
            context_layer['target_calendar_context'] = _target_calendar_context
            payload['target_calendar_evidence'] = _target_calendar_context
            payload['target_session'] = _target_calendar_context['requested_target_session']
        payload['research_context_hash'] = digest_json_stream(context_layer)
        payload['research_context_role'] = context_layer['application_mode']
        payload['effective_input_hash'] = digest_json_stream({
            'price_and_participation_dependencies': payload['effective_input_hash'],
            'context_hash': payload['research_context_hash']})
        payload['source_refs'] = sorted(set(payload['source_refs']) |
            set((result.get('market_turnover_context') or {}).get('source_refs') or ()) |
            set((result.get('theme_evolution') or {}).get('source_refs') or ()) |
            set((_target_calendar_context or {}).get('source_refs') or ()))
        extra = (_source_receipts if _source_receipts is not None else
                 getattr(_source_payload_reader, 'receipts', ())) or ()
        from .contracts import SourceReceipt
        context_receipts = [SourceReceipt.model_validate(row) for row in extra]
        visible_used = [row for row in context_receipts
            if row.receipt_id in payload['source_refs'] and
            (max(row.event_time, row.source_published_at, row.first_seen_at, row.ingested_at) <= ctx.cutoff
             or (row.dataset == 'calendar'
                 and row.receipt_id in set((_target_calendar_context or {}).get('source_refs') or ())
                 and max(row.source_published_at, row.first_seen_at, row.ingested_at) <= ctx.cutoff))]
        payload['base_data_watermark'] = payload['data_watermark']
        payload['context_data_watermark'] = max((row.ingested_at.isoformat() for row in visible_used), default=None)
        payload['data_watermark'] = max(filter(None, (payload['base_data_watermark'],
            payload['context_data_watermark'])), default=None)
        id_fields += ('research_context_hash',)
    payload['run_id'] = _stable_id('strategy_experiment', {key: payload[key] for key in id_fields})
    return payload

def strategy_specs():
    from .stage_aware_momentum import stage_aware_strategy_specs
    from .short_horizon_strength import short_horizon_strategy_specs
    from .short_horizon_participation import short_horizon_strategy_specs as participation_specs
    return {'task_id':'R05','implementation_version':IMPLEMENTATION_VERSION,**source_identity(),
        'strategies':[{'strategy_id':'DAILY_V1','description':'原五族冻结对照，缺数据不由PV替代。','config_hash':source_identity()['frozen_daily_config_hash']},
            {'strategy_id':PV_OBSERVATION_ID,'parameter_status':PARAMETER_STATUS,'spec':PV_SPEC},
            {'strategy_id':FOCUS_POLICY_ID,'selection_policy':FOCUS_POLICY,'parameter_status':PARAMETER_STATUS},
                *stage_aware_strategy_specs(),
            *short_horizon_strategy_specs(),
            *participation_specs()]}

STRATEGY_SPECS=strategy_specs()['strategies']
