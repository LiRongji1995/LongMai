"""Validate immutable history bases without treating a hash as revision authority."""
from datetime import date, datetime

from ..storage import IntegrityError
from .contracts import MarketInput
from .history_artifacts import HistoryArtifactStore

PRICE_DATASETS = {'market_daily','history','history_adjusted','adjustment_factors','tushare_daily','tushare_adj_factor'}


def price_schema_identities(receipts):
    """Explicit usable source schema identities; failed attempts certify none."""
    schemas = set()
    for receipt in receipts:
        row = receipt.model_dump(mode='json') if hasattr(receipt, 'model_dump') else receipt
        dataset = row.get('dataset', row.get('endpoint'))
        if dataset not in PRICE_DATASETS or row.get('status') not in {'COMPLETE', 'PARTIAL'}:
            continue
        provider, version = row.get('provider'), row.get('field_version')
        if not isinstance(provider, str) or not provider or not isinstance(version, str) or not version:
            raise ValueError('HISTORY_PRICE_SCHEMA_IDENTITY_UNKNOWN')
        schemas.add((provider, dataset, version))
    return schemas


def validate_price_schema_compatibility(base_receipts, incoming_receipts):
    base = price_schema_identities(base_receipts)
    incoming = price_schema_identities(incoming_receipts)
    if not base:
        raise ValueError('HISTORY_BASE_PRICE_SCHEMA_MISSING')
    matrix = []
    supported = None
    for provider, dataset in sorted({identity[:2] for identity in base}):
        before = {version for p, d, version in base if (p, d) == (provider, dataset)}
        after = {version for p, d, version in incoming if (p, d) == (provider, dataset)}
        if len(before) != 1:
            raise ValueError('HISTORY_BASE_PRICE_SCHEMA_INCOMPATIBLE:' + provider + ':' + dataset)
        version = next(iter(before))
        if not after:
            if supported is None:
                from .provider_requests_worker import supported_price_schema_identities
                supported = supported_price_schema_identities()
            if (provider, dataset, version) not in supported:
                raise ValueError('HISTORY_BASE_PRICE_SCHEMA_NOT_PROVEN:' + provider + ':' + dataset)
            status = 'RETAINED_BASE_SCHEMA_SUPPORTED_NO_NEW_FAMILY_EVIDENCE'
        else:
            if len(after) != 1 or before != after:
                raise ValueError('HISTORY_BASE_PRICE_SCHEMA_INCOMPATIBLE:' + provider + ':' + dataset)
            status = 'VERIFIED_EXACT_USABLE_INCOMING_SCHEMA_IDENTITY'
        matrix.append({'provider': provider, 'dataset': dataset, 'field_version': version,
                       'status': status, 'new_comparable_values_authorized': False})
    return {'status': ('VERIFIED_SCHEMA_COMPATIBILITY_WITH_RETAINED_BASE'
                      if any(row['status']=='RETAINED_BASE_SCHEMA_SUPPORTED_NO_NEW_FAMILY_EVIDENCE' for row in matrix)
                      else 'VERIFIED_EXACT_USABLE_SCHEMA_IDENTITY'),
            'policy': 'EXACT_SINGLE_INCOMING_VERSION_OR_INSTALLED_ADAPTER_SUPPORTED_UNCHANGED_BASE_NO_IMPLICIT_MIGRATION',
            'checked_base_schemas': matrix, 'incoming_price_schemas': [list(value) for value in sorted(incoming)],
            'numeric_price_basis_validation': 'NOT_INFERRED_FROM_SCHEMA_IDENTITY'}


def validate_history_base(bundle, owner, *, observation_date, cutoff, allowed_price_providers=None,
                          verify_partitions=True, incoming_price_receipts=None):
    """Completeness here means intact referenced partitions, not DAILY readiness.

    Source/PIT validation remains mandatory for individual rows at consumption.
    No base can certify an absent future factor or authorize a retraction.
    """
    day = date.fromisoformat(str(observation_date)[:10])
    if cutoff.tzinfo is None or not bundle.observation_date < day:
        raise ValueError('HISTORY_BASE_MUST_PRECEDE_OBSERVATION_AND_HAVE_AWARE_CUTOFF')
    if not bundle.history_artifact_ref or not bundle.history_artifact_hash:
        raise ValueError('HISTORY_BASE_REFERENCE_REQUIRED')
    manifest = owner.manifest(bundle.history_artifact_ref, bundle.history_artifact_hash)
    parts = manifest.get('partitions') or []
    sessions = [p['session'] for p in parts]
    if not parts or len(sessions) != len(set(sessions)) or any(
            s > str(bundle.observation_date) or date.fromisoformat(s) not in bundle.sessions for s in sessions):
        raise ValueError('HISTORY_BASE_PARTITION_DATE_OR_CALENDAR_INCOMPATIBLE')
    if any(p.get('row_count',0) <= 0 or p.get('bar_count',-1) < 0 for p in parts):
        raise ValueError('HISTORY_BASE_PARTITION_COMPLETENESS_INVALID')
    receipts = manifest.get('receipts') or []
    clocks = [datetime.fromisoformat(str(r[field]).replace('Z','+00:00'))
              for r in receipts for field in ('source_published_at','first_seen_at','ingested_at')]
    if not clocks or any(stamp.tzinfo is None or stamp > cutoff for stamp in clocks):
        raise ValueError('HISTORY_BASE_SOURCE_NOT_AVAILABLE_AT_BUILD_CUTOFF')
    price_receipts = [r for r in receipts if r.get('dataset') in PRICE_DATASETS and r.get('status') in {'COMPLETE','PARTIAL'}]
    providers = {r.get('provider') for r in price_receipts}
    if not providers or None in providers or any(not r.get('field_version') for r in price_receipts):
        raise ValueError('HISTORY_BASE_PRICE_SOURCE_IDENTITY_UNKNOWN')
    if allowed_price_providers is not None and not providers <= set(allowed_price_providers):
        raise ValueError('HISTORY_BASE_PRICE_PROVIDER_INCOMPATIBLE')
    schema_proof = ({'status':'PENDING_ACTUAL_INCOMING_PRICE_SCHEMAS',
                     'numeric_price_basis_validation':'NOT_INFERRED_FROM_SCHEMA_IDENTITY'}
                    if incoming_price_receipts is None else
                    validate_price_schema_compatibility(price_receipts, incoming_price_receipts))
    if verify_partitions:
        owner.verify(bundle.history_artifact_ref, bundle.history_artifact_hash)
    return {'history_artifact_ref':bundle.history_artifact_ref,'history_artifact_hash':bundle.history_artifact_hash,
            'observation_date':str(bundle.observation_date),'source_watermark':max(clocks).isoformat(),
            'price_providers':sorted(providers),'field_versions':sorted({r['field_version'] for r in price_receipts}),
            'partition_count':len(parts),'bar_count':sum(p['bar_count'] for p in parts),
            'partition_integrity':'VERIFIED' if verify_partitions else 'NOT_VERIFIED',
            'price_schema_compatibility':schema_proof,
            'completeness_semantics':'REFERENCED_PARTITIONS_NOT_DAILY_OR_MA_READINESS',
            'price_basis_policy':'PRESERVE_EACH_ROW_BASIS_NO_CROSS_PROVIDER_OR_CROSS_BASIS_MERGE',
            'pit_policy':'BASE_AVAILABLE_AT_BUILD_CUTOFF_AND_CONSUMER_RESOLVER_STILL_REQUIRED'}


def select_prior_history_base(store, *, observation_date, cutoff, allowed_price_providers=None):
    owner = HistoryArtifactStore(store)
    candidates, rejected = [], []
    offset = 0
    while True:
        indexes = store.list_documents('candidate_input_indexes',limit=1000,offset=offset)
        for index in indexes:
            payload = index['payload']
            observed = payload.get('observation_date')
            if not observed or observed >= str(observation_date):
                continue
            iid = payload.get('input_id') or index['document_id']
            try:
                raw = store.get_document_bytes('candidate_inputs',iid,expected_hash=iid.removeprefix('market_'))
                if raw is None:
                    raise ValueError('HISTORY_BASE_INPUT_MISSING')
                bundle = MarketInput.model_validate_json(raw)
                proof = validate_history_base(bundle,owner,observation_date=observation_date,cutoff=cutoff,
                    allowed_price_providers=allowed_price_providers,verify_partitions=False)
                source_clock = datetime.fromisoformat(proof['source_watermark'].replace('Z','+00:00'))
                registered_clock = datetime.fromisoformat(index['created_at'].replace('Z','+00:00'))
                if registered_clock.tzinfo is None:
                    raise ValueError('HISTORY_BASE_REGISTRATION_CLOCK_MUST_BE_AWARE')
                candidates.append((proof['observation_date'],source_clock,registered_clock,iid,bundle,proof))
            except (ValueError,IntegrityError) as exc:
                rejected.append({'input_id':iid,'reason':str(exc)})
        if len(indexes) < 1000:
            break
        offset += len(indexes)
    if not candidates:
        return None
    # Actual availability and registration clocks are chronology; IDs never rank.
    candidates.sort(key=lambda item:item[:3],reverse=True)
    top = candidates[0]
    if any(row[:3] == top[:3] and row[4].history_artifact_ref != top[4].history_artifact_ref for row in candidates[1:]):
        raise ValueError('AMBIGUOUS_HISTORY_BASE_SAME_CLOCKS_EXPLICIT_SELECTION_REQUIRED')
    proof = validate_history_base(top[4],owner,observation_date=observation_date,cutoff=cutoff,
                                  allowed_price_providers=allowed_price_providers)
    return {**proof,'input_id':top[3],'selection_policy':'OBSERVATION_SOURCE_WATERMARK_REGISTRATION_CLOCK_V1',
            'rejected_candidates':rejected,'readiness':'NOT_INFERRED_FROM_ARTIFACT_INTEGRITY'}
