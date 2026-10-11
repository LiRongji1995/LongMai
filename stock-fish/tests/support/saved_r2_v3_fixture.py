"""Independent TEST_FIXTURE saved pool and original physical capture files.

These bytes are deliberately synthetic, isolated under pytest's tmp_path and
never represent observed stock history. No selector or database constructs the
frozen decisions. Context source scope and saved decision scope are separate.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json

from stockfish_core.yaogu.contracts import MarketInput, SourceReceipt
from stockfish_core.yaogu.history_artifacts import digest_json_stream, digest_market_input
from stockfish_core.yaogu.provider_normalization import _clock
from tests.support.short_horizon_strength_fixture import strength_bundle


SOURCES_SCHEMA = 'SAVED_R2_DELIVERY_SOURCES_V2'
RUN_SCHEMA = 'SAVED_R2_DELIVERY_RUN_MANIFEST_V1'
STRATEGY = 'SHORT_HORIZON_STRENGTH_R2'


def write(path, document):
    raw = document if isinstance(document, bytes) else json.dumps(
        document, ensure_ascii=False, allow_nan=False).encode('utf-8')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}


def physical_source(root, endpoint, rows, seen, *, day=None, parameters=None,
                    provider='baostock', version='BAOSTOCK_00.9.30_R03_V1',
                    physical_row_count=None, include_result=False,
                    status='COMPLETE', pagination_complete=True):
    """Use the original archive receipt ID algorithm, without normalized files."""
    parameters = parameters if parameters is not None else {'date': str(day)}
    payload = dict(status=status, pagination_complete=pagination_complete,
        field_version=version, first_seen_at=seen.isoformat(), rows=rows,
        coverage={}, gaps=[])
    raw = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
    receipt = dict(provider=provider, dataset=endpoint, parameters=parameters,
        content_hash=hashlib.sha256(raw).hexdigest(), first_seen_at=seen.isoformat(),
        ingested_at=seen.isoformat(), status=status,
        pagination_complete=pagination_complete, row_count=physical_row_count,
        field_version=version, source_url='https://TEST_FIXTURE.invalid/original',
        integrity_scope='LOCAL_SHA256_NOT_SECURITY_AUTHENTICATION')
    rid = hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest()
    receipt = dict(receipt_id=rid, **receipt)
    raw_registration = write(root/'cache/raw'/(receipt['content_hash']+'.raw.json'), raw)
    receipt_registration = write(root/'cache/raw'/(rid+'.receipt.json'), receipt)
    typed = SourceReceipt(receipt_id=rid, provider=provider, dataset=endpoint,
        content_hash=receipt['content_hash'], field_version=version,
        row_count=len(rows), status=status, pagination_complete=pagination_complete,
        parameters=parameters, source_url=receipt['source_url'], **_clock(receipt, day))
    item = dict(raw=raw_registration, receipt=receipt_registration,
        typed_receipt=typed.model_dump(mode='json'))
    if include_result:
        result = dict(request_receipt_id=rid, provider=provider, endpoint=endpoint,
            parameters=parameters, receipts=[receipt], **payload,
            started_at=seen.isoformat(), completed_at=seen.isoformat())
        item.update(archive_root=str(root),
            result=write(root/'results'/(rid+'.json'), result))
    return item


def fixture(tmp_path, *, pool_count=15, selected_count=13, sessions_count=67,
            include_themes=True, late_capture=False):
    original, cutoff = strength_bundle()
    value = original.model_dump(mode='json')
    securities, rows = [], []
    for index in range(pool_count):
        security = deepcopy(value['universe']['securities'][0])
        security.update(instrument_id=f'{600001+index:06}.SH',
            name='TEST_FIXTURE股'+str(index))
        securities.append(security)
        status = 'WATCH' if index < selected_count else 'UNKNOWN' if index == pool_count-1 else 'REJECTED'
        rows.append(dict(instrument_id=security['instrument_id'], name=security['name'],
            decision_status=status, research_selected=index < selected_count,
            primary_stage=('STRONG_START', 'STRONG_CONTINUATION', 'FIRST_PULLBACK')[index % 3],
            matched_arms=['TEST_FIXTURE_ARM'], rank=index+1, display_order=index+1,
            exact_features={'amount': {'numerator': 500000000+index, 'denominator': 1}},
            participation_status='PASS', style_status='PASS', liquidity_status='UNKNOWN',
            reason_codes=['TEST_FIXTURE_UNKNOWN'] if status == 'UNKNOWN' else []))
    value['universe'].update(securities=securities, expected_count=pool_count)
    value.update(bars=[], themes=[])
    bundle = MarketInput.model_validate(value)
    selected = [row for row in rows if row['research_selected']]
    counts = dict(eligible=pool_count, focus=0, watch=selected_count,
        rejected=pool_count-selected_count-1, unknown=1)
    if pool_count == selected_count:
        counts.update(rejected=0, unknown=0)
    native_counts = dict(counts, evaluated=pool_count, research_observation=selected_count,
        complete_25_window=0, current_source_unknown=counts['unknown'])
    identity = dict(implementation_version='TEST_FIXTURE_R2', spec_revision='TEST_FIXTURE_V1',
        feature_version='TEST_FIXTURE_R2_FEATURE', source_hash='a'*64, config_hash='b'*64,
        spec_hash='c'*64, selection_policy_hash='d'*64,
        source_file_hashes={'TEST_FIXTURE.py': 'e'*64})
    input_hash, policy = digest_market_input(bundle), 'TEST_FIXTURE_SELECTION'
    run = dict(run_id='strategy_experiment_TEST_FIXTURE_DELIVERY_V3',
        input_id='EXPLICIT_TEST_FIXTURE_INPUT_ID', input_hash=input_hash,
        observation_date=str(bundle.observation_date), decision_cutoff=cutoff.isoformat(),
        target_session=None, **identity, parameter_status='UNVALIDATED',
        generated_at=cutoff.isoformat(), target_calendar_evidence={'status': 'UNKNOWN'},
        results={STRATEGY: dict(strategy_id=STRATEGY, **identity, input_hash=input_hash,
            selection_policy_id=policy, eligible_pool_ids=[row['instrument_id'] for row in rows],
            full_eligible_pool_retained=True, items=rows, counts=native_counts,
            research_selected_ids=[row['instrument_id'] for row in selected])},
        selection_results={policy: dict(selection_policy_id=policy, strategy_id=STRATEGY,
            count=selected_count, selected_ids=[row['instrument_id'] for row in selected])})
    run['content_hash'] = digest_json_stream(run)
    sources = dict(schema_version=SOURCES_SCHEMA, captures=[], raw_receipt_sources=[],
        business_sources=[], counted_nodes=[], reported_scope=None, extra_registered_files=[])
    codes = ['sh.'+row['instrument_id'][:6] for row in selected]
    codes += ['sh.688001', 'sz.000001', 'sz.300001']
    if not selected:
        codes.append('sh.600001')
    for day in bundle.sessions[-sessions_count:] if sessions_count else ():
        seen = cutoff+timedelta(seconds=1) if late_capture else datetime.combine(
            day, datetime.min.time(), timezone.utc)+timedelta(hours=8)
        master = [dict(code=code, code_name='TEST_FIXTURE', tradeStatus='1') for code in codes]
        # The index, B share, fund and BSE master entries remain outside the
        # source-defined four-board common-A market denominator.
        master += [dict(code=code, code_name='TEST_FIXTURE', tradeStatus='1')
            for code in ('sh.000001', 'sh.900001', 'sz.159001', 'bj.830001')]
        market = [dict(code=code, date=str(day), adjustflag='3', tradestatus='1',
            open='10', high='11', low='9', close='10.5', preclose='10',
            amount='1000000000', volume='100') for code in codes]
        for endpoint, contents in (('master_all', master), ('market_daily', market)):
            sources['raw_receipt_sources'].append(physical_source(tmp_path/'capture',
                endpoint, contents, seen, day=day))
    if include_themes and selected:
        day = bundle.observation_date
        seen = cutoff+timedelta(seconds=1) if late_capture else datetime.combine(
            day, datetime.min.time(), timezone.utc)+timedelta(hours=8)
        # One selected identity deliberately has only its original model group.
        thematic = selected[:-1]
        symbol = 'gn_TEST_FIXTURE_BATTERY'
        sources['captures'].append(physical_source(tmp_path/'capture', 'concept_list_sina',
            [{'label': symbol, '板块': 'TEST_FIXTURE电池', '公司家数': len(thematic)}],
            seen, parameters={}, provider='sina', version='TEST_FIXTURE_SINA_V1', include_result=True))
        sources['captures'].append(physical_source(tmp_path/'capture', 'concept_members_sina',
            [dict(code=row['instrument_id'][:6], symbol='sh'+row['instrument_id'][:6],
                name='TEST_FIXTURE') for row in thematic], seen,
            parameters={'symbol': symbol}, provider='sina', version='TEST_FIXTURE_SINA_V1', include_result=True))
    paths = dict(run=tmp_path/'native-run.json', input=tmp_path/'input.json',
        sources=tmp_path/'sources.json', manifest=tmp_path/'run-manifest.json')
    binding = dict(strategy_id=STRATEGY, run_id=run['run_id'], input_id=run['input_id'],
        input_hash=input_hash, observation_date=run['observation_date'], target_session=None,
        original_decision_cutoff=run['decision_cutoff'], source_hash=run['source_hash'],
        counts=counts, native_counts=deepcopy(native_counts), original_strategy_identity=identity,
        selection_policy_id=policy, selected_ids=[row['instrument_id'] for row in selected],
        selected_original_items_hash=digest_json_stream(selected))
    manifest = dict(schema_version=RUN_SCHEMA, run_sha256=write(paths['run'], run)['sha256'],
        input_sha256=write(paths['input'], bundle.model_dump(mode='json'))['sha256'],
        sources_manifest_sha256=write(paths['sources'], sources)['sha256'], binding=binding)
    write(paths['manifest'], manifest)
    return bundle, cutoff, run, sources, manifest, paths


def update_sources(paths, sources, manifest):
    manifest['sources_manifest_sha256'] = write(paths['sources'], sources)['sha256']
    write(paths['manifest'], manifest)
