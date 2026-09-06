"""Frozen peer-excluded registered themes and full-reference average-tie ranks."""
from statistics import median
from decimal import Decimal
import math
from .config import R
from .events import TRUE, FALSE

def value(features,key):
    obj=features.get(key)
    if obj is None:return None
    valid_qualities={'VALID','EXACT','OBSERVED','COMPLETE','COMPLETE_APPLICABLE','COMPLETE_WITH_KNOWN_SUSPENSION','VERIFIED_PROVIDER_BASIS','VERIFIED_FLOAT_SHARES','PRIOR_NO_EVENT','KNOWN_NON_LIMIT_BREAK','SUSPENSION_BREAK','RULE_NOT_APPLICABLE'}
    if obj.quality not in valid_qualities and not obj.quality.startswith('LEFT_CENSORED_AT_LEAST_'):return None
    if isinstance(obj.value,(int,float,Decimal)) and not math.isfinite(float(obj.value)):return None
    return obj.value

def percentile_rank(x, values):
    def finite(v):return type(v) is not bool and isinstance(v,(int,float,Decimal)) and math.isfinite(float(v))
    if not finite(x) or len(values)<2 or not all(finite(v) for v in values): return None
    # rank_average_ascending = number below + (number equal + 1)/2
    rank=sum(v<x for v in values)+(sum(v==x for v in values)+1)/2
    return 100*(rank-1)/(len(values)-1)

def compute_theme(instrument_id,themes,pool,features,config):
    evidence=[]
    usable=[]
    for theme in sorted(themes,key=lambda t:t.theme_id):
        if instrument_id not in theme.member_ids: continue
        members=tuple(sorted(set(theme.member_ids)&set(pool)))
        peers=tuple(i for i in members if i!=instrument_id)
        e={'theme_id':theme.theme_id,'name':theme.name,'theme_type':theme.theme_type,'definition_version':theme.definition_version,
           'member_ids':list(members),'peer_ids':list(peers),'source_refs':list(theme.source_refs),'as_of':theme.source_published_at.isoformat(),
           'T':None,'L':None,'selection_reason':'UNAVAILABLE'}
        if theme.theme_type not in ('INDUSTRY','EVENT'):
            e['selection_reason']='INDUSTRY_PROXY_REQUIRES_SEPARATE_PROFILE' if theme.theme_type=='INDUSTRY_PROXY' else 'NON_RESEARCH_QUALIFICATION_THEME';evidence.append(e);continue
        if len(members)<config.thresholds.theme_min_members:
            e['selection_reason']='TOO_FEW_MAIN_BOARD_MEMBERS';evidence.append(e);continue
        observed=[i for i in peers if i in features and value(features[i],'close_limit_up') in (TRUE,FALSE) and value(features[i],'close') is not None and value(features[i],'reference_prev_close') is not None and value(features[i],'ret3') is not None and value(features[i],'streak_exchange') is not None]
        coverage=len(observed)/len(peers) if peers else 0
        e.update({'peer_expected':len(peers),'peer_observed':len(observed),'coverage':coverage})
        if coverage<config.thresholds.theme_coverage_fraction:
            e['selection_reason']='INSUFFICIENT_PEER_QUOTE_COVERAGE';evidence.append(e);continue
        u=sum(value(features[i],'close_limit_up')==TRUE for i in observed)
        c2=sum(value(features[i],'streak_exchange')>=2 for i in observed)
        pos=sum(value(features[i],'close')>value(features[i],'reference_prev_close') for i in observed)/len(observed)
        ret=median(value(features[i],'ret3') for i in observed)
        t=.35*R(u,0,5)+.25*R(c2,0,3)+.25*R(pos,.40,.80)+.15*R(ret,-.03,.09)
        ranks={key:percentile_rank(value(features[instrument_id],key),[value(features.get(i,{}),key) for i in members]) for key in ('M10','ret5','median_amount20')}
        l=.50*ranks['M10']+.30*ranks['ret5']+.20*ranks['median_amount20'] if all(v is not None for v in ranks.values()) else None
        e.update({'T':t,'L':l,'peer_close_limit_up_count':u,'peer_streak_ge2_count':c2,'peer_positive_return_fraction':pos,'peer_median_ret3':ret,
                  'rank_denominator':len(members),'ranks':ranks,'tie_rule':'AVERAGE_ASCENDING','selection_reason':'AVAILABLE_NOT_SELECTED'})
        usable.append(e);evidence.append(e)
    if not usable: return None,None,None,tuple(evidence)
    chosen=sorted(usable,key=lambda e:(-e['T'],e['theme_id']))[0]
    chosen['selection_reason']='MAX_T_STABLE_THEME_ID'
    return chosen['T'],chosen['L'],chosen['theme_id'],tuple(evidence)

class ThemeEngine:
    compute=staticmethod(compute_theme)
