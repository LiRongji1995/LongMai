"""R03 exact-three-session emotion history selection and auditable context.

The pure selector accepts already authorized documents. CandidateService obtains
them only through successful immutable publication authority, never HTTP input.
"""
from .contracts import digest

EMOTION_VERSION = 'MARKET_EMOTION_D3_V1'
COVERAGE_POLICY = 'MAIN_NON_ST_PARTITION_V1'


def context(config, coverage_type,authorization_mode='PUBLISHED_CANDIDATE'):
    return {'emotion_definition_version': EMOTION_VERSION,
            'strategy_id': config.strategy_id, 'config_hash': config.config_hash,
            'feature_definition_version': config.feature_definition_version,
            'pool_profile': 'MAIN_NON_ST', 'coverage_policy': COVERAGE_POLICY,
            'coverage_type': coverage_type,'authorization_mode':authorization_mode}


def select_history(snapshots, *, day, sessions, cutoff, config, coverage_type,authorization_mode='PUBLISHED_CANDIDATE'):
    if authorization_mode not in ('PUBLISHED_CANDIDATE','RECONSTRUCTED_EVALUATION_ARTIFACT'):
        raise ValueError('unknown emotion authorization mode')
    if authorization_mode=='RECONSTRUCTED_EVALUATION_ARTIFACT' and coverage_type not in ('TEST_FIXTURE','RECONSTRUCTED_HISTORICAL'):
        raise ValueError('research emotion requires explicit reconstructed/fixture coverage')
    expected = tuple(d for d in sessions if d < day)[-3:]
    wanted = context(config, coverage_type,authorization_mode)
    def available(e):return e.research_available_as_of if authorization_mode=='RECONSTRUCTED_EVALUATION_ARTIFACT' else e.published_at
    selected, rejected, missing = [], [], []
    for session in expected:
        eligible = []
        for e in snapshots:
            if e.session != session:
                continue
            reason = None
            if e.as_of > cutoff or available(e) is None or available(e) > cutoff:
                reason = 'NOT_PUBLISHED_AT_CUTOFF'
            elif any(getattr(e, k) != v for k, v in wanted.items()):
                reason = 'HISTORY_CONTEXT_MISMATCH'
            elif e.score is None or not e.source_complete or not e.document_id or not e.effective_input_hash:
                reason = 'HISTORY_NOT_COMPLETE_OR_AUTHORIZED'
            elif authorization_mode=='RECONSTRUCTED_EVALUATION_ARTIFACT' and (
                    e.publication_authority.get('type')!='RECONSTRUCTED_EVALUATION_ARTIFACT'
                    or not e.publication_authority.get('scope_id') or not e.publication_authority.get('source_version')):
                reason = 'RESEARCH_ARTIFACT_AUTHORITY_REQUIRED'
            if reason:
                rejected.append({'session': session.isoformat(), 'document_id': e.document_id,
                                 'reason': reason})
            else:
                eligible.append(e)
        if not eligible:
            missing.append(session.isoformat())
            continue
        chosen = max(eligible, key=lambda e: (available(e), e.as_of, e.document_id))
        selected.append(chosen)
    refs = [{'document_id': e.document_id, 'document_hash': e.publication_authority.get('emotion_hash',digest(e)),
             'session': e.session.isoformat(), 'score': e.score,
             'published_at': e.published_at.isoformat() if e.published_at else None, 'decision_cutoff': e.as_of.isoformat(),
             'available_as_of':available(e).isoformat(),'authorization_mode':e.authorization_mode,
             'effective_input_hash': e.effective_input_hash,
             'publication_authority':e.publication_authority,
             'selection_reason': 'LATEST_AUTHORIZED_VISIBLE_VERSION_FOR_EXACT_SESSION' if authorization_mode=='PUBLISHED_CANDIDATE' else 'RECONSTRUCTED_SAME_SCOPE_ARTIFACT_NOT_PROSPECTIVE_PUBLICATION'} for e in selected]
    complete = len(expected) == 3 and len(selected) == 3
    return tuple(selected), {'policy': EMOTION_VERSION,'authorization_mode':authorization_mode, 'formula': 'M_t - mean(M_t-1, M_t-2, M_t-3)',
                            'expected_sessions': [d.isoformat() for d in expected],
                            'missing_sessions': missing, 'calendar_history_missing_count': 3-len(expected),
                            'selected': refs, 'rejected': rejected,
                            'status': 'COMPLETE' if complete else 'UNKNOWN_DELTA'}


def effective_history(selection):
    """Unused/future rejected revisions are audit material, not dependencies."""
    return {k: v for k, v in selection.items() if k != 'rejected'}


def reconstructed_artifact(result,*,scope_id,generated_at):
    """Separate research artifact, never an assertion of past live publication."""
    if result.coverage_type not in ('TEST_FIXTURE','RECONSTRUCTED_HISTORICAL'):
        raise ValueError('research artifact requires explicit reconstruction coverage')
    return result.emotion.model_copy(update={
        'authorization_mode':'RECONSTRUCTED_EVALUATION_ARTIFACT',
        'document_id':'research_emotion_'+scope_id+'_'+result.observation_date.isoformat(),
        'effective_input_hash':result.effective_input_hash,'published_at':generated_at,
        'research_available_as_of':result.decision_cutoff,
        'publication_authority':{'type':'RECONSTRUCTED_EVALUATION_ARTIFACT','scope_id':scope_id,
            'source_version':result.source_hash,'not_a_prospective_publication':True,
            'generated_at':generated_at.isoformat(),
            'availability_basis':'DETERMINISTIC_RECONSTRUCTION_FROM_VALIDATED_AS_OF_SOURCE_RECORDS'}})
