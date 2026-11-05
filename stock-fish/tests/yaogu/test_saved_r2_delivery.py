"""TEST_FIXTURE complete archived delivery, without selector/SDK/DB/network."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from stockfish_core.yaogu import saved_r2_delivery as delivery
from stockfish_core.yaogu.contracts import MarketInput, SourceReceipt
from stockfish_core.yaogu.history_artifacts import digest_json_stream, digest_market_input
from stockfish_core.yaogu.provider_normalization import _clock
from tests.support.short_horizon_strength_fixture import strength_bundle


def write(path, value):
    raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}


def fixture(tmp_path, selected_count=13):
    original, cutoff = strength_bundle()
    value = original.model_dump(mode='json')
    securities = []
    for index in range(15):
        row = deepcopy(value['universe']['securities'][0])
        row.update(instrument_id=f'{600001+index:06}.SH', name='TEST_FIXTURE股' + str(index))
        securities.append(row)
    value['universe'].update(securities=securities, expected_count=len(securities))
    # No selector is called: the saved decision archive is explicit TEST_FIXTURE.
    value['bars'] = []
    value['themes'] = []
    bundle = MarketInput.model_validate(value)
    rows = []
    for index, security in enumerate(securities):
        status = 'WATCH' if index < selected_count else 'UNKNOWN' if index == 14 else 'REJECTED'
        rows.append(dict(instrument_id=security['instrument_id'], name=security['name'],
            decision_status=status, research_selected=index < selected_count,
            primary_stage=('STRONG_START', 'STRONG_CONTINUATION', 'FIRST_PULLBACK')[index % 3],
            matched_arms=['TEST_FIXTURE_ARM'], rank=index+1, display_order=index+1,
            exact_features={'amount': {'numerator': 500000000+index, 'denominator': 1}},
            participation_status='PASS', style_status='PASS', liquidity_status='UNKNOWN',
            reason_codes=['TEST_FIXTURE_UNKNOWN'] if status == 'UNKNOWN' else []))
    selected = [row for row in rows if row['research_selected']]
    counts = dict(eligible=len(rows), focus=0, watch=selected_count,
                  rejected=len(rows)-selected_count-1, unknown=1)
    if selected_count == 0:
        counts.update(rejected=14, unknown=1)
    input_hash = digest_market_input(bundle)
    identity = dict(implementation_version='TEST_FIXTURE_R2', spec_revision='TEST_FIXTURE_V1',
        feature_version='TEST_FIXTURE_R2_FEATURE', source_hash='a'*64, config_hash='b'*64,
        spec_hash='c'*64, selection_policy_hash='d'*64, source_file_hashes={'TEST_FIXTURE.py': 'e'*64})
    native_counts = dict(counts, evaluated=len(rows), research_observation=len(selected),
                         complete_25_window=0, current_source_unknown=1)
    policy = 'TEST_FIXTURE_SELECTION'
    run = dict(run_id='strategy_experiment_TEST_FIXTURE_DELIVERY', input_id='EXPLICIT_TEST_FIXTURE_INPUT_ID',
        input_hash=input_hash, observation_date=str(bundle.observation_date), decision_cutoff=cutoff.isoformat(),
        target_session=None, **identity, parameter_status='UNVALIDATED',
        generated_at=cutoff.isoformat(), target_calendar_evidence={'status': 'UNKNOWN'},
        results={delivery.STRATEGY: dict(strategy_id=delivery.STRATEGY,
            **identity, input_hash=input_hash, selection_policy_id=policy,
            eligible_pool_ids=[row['instrument_id'] for row in rows],
            full_eligible_pool_retained=True, items=rows, counts=native_counts,
            research_selected_ids=[row['instrument_id'] for row in selected])},
        selection_results={policy: {'selection_policy_id': policy, 'strategy_id': delivery.STRATEGY,
            'count': len(selected), 'selected_ids': [row['instrument_id'] for row in selected]}})
    run['content_hash'] = digest_json_stream(run)
    sources = dict(schema_version=delivery.SOURCES_SCHEMA, captures=[], business_sources=[],
                   counted_nodes=[], reported_scope=None, extra_registered_files=[])
    paths = dict(run=tmp_path/'native-run.json', input=tmp_path/'input.json',
        sources=tmp_path/'sources.json', manifest=tmp_path/'run-manifest.json')
    binding = dict(strategy_id=delivery.STRATEGY, run_id=run['run_id'], input_id=run['input_id'], input_hash=input_hash,
        observation_date=run['observation_date'], target_session=None, original_decision_cutoff=run['decision_cutoff'],
        source_hash=run['source_hash'], counts=counts, native_counts=deepcopy(native_counts),
        original_strategy_identity=deepcopy(identity), selection_policy_id=policy,
        selected_ids=[row['instrument_id'] for row in selected],
        selected_original_items_hash=digest_json_stream(selected))
    write(paths['run'], run); write(paths['input'], bundle.model_dump(mode='json')); write(paths['sources'], sources)
    manifest = dict(schema_version=delivery.RUN_SCHEMA, run_sha256=write(paths['run'], run)['sha256'],
        input_sha256=write(paths['input'], bundle.model_dump(mode='json'))['sha256'],
        sources_manifest_sha256=write(paths['sources'], sources)['sha256'], binding=binding)
    write(paths['manifest'], manifest)
    return bundle, cutoff, run, sources, manifest, paths


def capture(root, endpoint, parameters, rows, seen, *, provider='baostock', version='BAOSTOCK_00.9.30_R03_V1', coverage=None, status='COMPLETE', pagination_complete=None):
    pagination = status == 'COMPLETE' if pagination_complete is None else pagination_complete
    payload = dict(status=status, pagination_complete=pagination, field_version=version,
        first_seen_at=seen.isoformat(), rows=rows, coverage=coverage or {}, gaps=[])
    raw = json.dumps(payload, ensure_ascii=False).encode()
    original = dict(provider=provider, dataset=endpoint, parameters=parameters,
        content_hash=hashlib.sha256(raw).hexdigest(), first_seen_at=seen.isoformat(), ingested_at=seen.isoformat(),
        status=status, pagination_complete=payload['pagination_complete'], row_count=len(rows),
        field_version=version, source_url='https://TEST_FIXTURE.invalid/original')
    rid = hashlib.sha256(json.dumps(original, sort_keys=True).encode()).hexdigest()
    original = dict(receipt_id=rid, **original)
    trio = root / 'cache/raw'
    rfile = write(trio/(rid+'.receipt.json'), original)
    rawfile = write(trio/(original['content_hash']+'.raw.json'), raw)
    result = dict(request_receipt_id=rid, provider=provider, endpoint=endpoint, parameters=parameters,
        receipts=[original], **payload, started_at=seen.isoformat(), completed_at=seen.isoformat())
    resultfile = write(root/'results'/(rid+'.json'), result)
    end = parameters.get('date') or parameters.get('end_date')
    end = datetime.fromisoformat(end).date() if end else None
    typed = SourceReceipt(receipt_id=rid, provider=provider, dataset=endpoint,
        content_hash=original['content_hash'], field_version=version, row_count=len(rows), status=status,
        pagination_complete=pagination, parameters=parameters, source_url=original['source_url'],
        business_sessions=tuple((coverage or {}).get('business_sessions', ())), **_clock(original, end))
    return dict(archive_root=str(root), result=resultfile, raw=rawfile, receipt=rfile,
                typed_receipt=typed.model_dump(mode='json'))


def market_and_themes(root, bundle, sources, *, capture_after=None):
    captures = sources['captures']
    codes = ['sh.' + row.instrument_id[:6] for row in bundle.universe.securities]
    for day in bundle.sessions[-22:]:
        seen = capture_after or datetime.combine(day, datetime.min.time(), timezone.utc) + timedelta(hours=8)
        master = [dict(code=code, code_name='TEST_FIXTURE', tradeStatus='1') for code in codes]
        market = [dict(code=code, date=str(day), adjustflag='3', tradestatus='1',
            open='10', high='11', low='9', close='10.5', preclose='10', volume='100', amount='1000000000') for code in codes]
        for endpoint, rows in (('master_all', master), ('market_daily', market)):
            captures.append(capture(root, endpoint, {'date': str(day)}, rows, seen,
                                    coverage={'business_sessions': [str(day)]}))
    day = bundle.observation_date
    seen = capture_after or datetime.combine(day, datetime.min.time(), timezone.utc) + timedelta(hours=8)
    symbol = 'gn_TEST_FIXTURE_BATTERY'
    captures.append(capture(root, 'concept_list_sina', {},
        [{'label': symbol, '板块': 'TEST_FIXTURE电池', '公司家数': len(codes)}], seen,
        provider='sina', version='TEST_FIXTURE_SINA_V1'))
    captures.append(capture(root, 'concept_members_sina', {'symbol': symbol},
        [dict(code=code[3:], symbol='sh'+code[3:], name='TEST_FIXTURE') for code in codes], seen,
        provider='sina', version='TEST_FIXTURE_SINA_V1'))


def update_sources(paths, sources, manifest):
    manifest['sources_manifest_sha256'] = write(paths['sources'], sources)['sha256']
    write(paths['manifest'], manifest)


def invoke(paths, cutoff, output, **kwargs):
    return delivery.deliver_saved_r2(run_path=paths['run'], run_manifest_path=paths['manifest'],
        input_path=paths['input'], sources_manifest_path=paths['sources'], output=output,
        context_cutoff=cutoff, **kwargs)


@pytest.fixture(autouse=True)
def forbidden_external(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError('TEST_FIXTURE_NO_NETWORK_DB_SDK_SELECTOR')
    import socket
    import sqlite3
    from stockfish_core.yaogu import strategy_experiments, short_horizon_participation, short_horizon_strength
    monkeypatch.setattr(socket.socket, 'connect', reject)
    monkeypatch.setattr(sqlite3, 'connect', reject)
    monkeypatch.setattr(strategy_experiments, 'run_short_horizon_strategy', reject)
    monkeypatch.setattr(short_horizon_participation, 'run_short_horizon_strength', reject)
    monkeypatch.setattr(short_horizon_strength, 'run_short_horizon_strength', reject)


def test_complete_more_than_ten_fallback_preserves_all_rows_order_unknown_and_exports(tmp_path):
    bundle, cutoff, run, sources, manifest, paths = fixture(tmp_path)
    before = {path: path.read_bytes() for path in paths.values()}
    result = invoke(paths, cutoff, tmp_path/'delivery')
    assert result['observation_count'] == 13
    group = json.loads((tmp_path/'delivery/grouped.json').read_text())
    selected = [row for row in run['results'][delivery.STRATEGY]['items'] if row['research_selected']]
    lookup = {row['instrument_id']: row for part in group['groups'] for row in part['items']}
    assert [lookup[iid] for iid in group['frozen_selected_ids']] == selected
    for part in group['groups']:
        assert part['primary_selected_ids'] == [iid for iid in manifest['binding']['selected_ids'] if iid in part['primary_selected_ids']]
    text = (tmp_path/'delivery/complete-observation-list.txt').read_text()
    assert all(row['instrument_id'] in text for row in selected)
    assert run['results'][delivery.STRATEGY]['items'][-1]['instrument_id'] not in text
    saved = json.loads((tmp_path/'delivery/delivery-manifest.json').read_text())
    assert saved['counts'] == manifest['binding']['native_counts']
    assert saved['count_summary'] == manifest['binding']['counts']
    assert saved['counts']['unknown'] == 1
    assert saved['native_theme_producer_calls'] == 0
    assert saved['network_DB_SDK_selector_calls'] == 0
    assert saved['context_status'] == 'UNKNOWN_MODEL_FALLBACK'
    assert {path: path.read_bytes() for path in paths.values()} == before


def test_legitimate_empty_does_not_call_nonempty_producer(tmp_path, monkeypatch):
    _, cutoff, _, _, _, paths = fixture(tmp_path, selected_count=0)
    from stockfish_core.yaogu import economic_direction_explanation_v1 as producer
    def forbidden(**kwargs): raise AssertionError('EMPTY_NOT_SYNTHETIC_ECONOMIC_ATTACHMENT')
    monkeypatch.setattr(producer, 'build_economic_direction_explanation', forbidden)
    result = invoke(paths, cutoff, tmp_path/'empty')
    assert result['observation_count'] == 0
    assert '真实为空' in (tmp_path/'empty/complete-observation-list.txt').read_text()
    assert not (tmp_path/'empty/economic-direction-explanation.json').exists()


@pytest.mark.parametrize('kind', ['hash', 'counts', 'same_count_other_id', 'unknown_selected', 'input', 'schema', 'partial_run'])
def test_frozen_identity_counterexamples_are_rejected(tmp_path, kind):
    _, cutoff, run, _, manifest, paths = fixture(tmp_path)
    if kind == 'hash':
        paths['run'].write_bytes(paths['run'].read_bytes()+b' ')
    elif kind == 'counts':
        manifest['binding']['counts']['unknown'] = 0
    elif kind == 'same_count_other_id':
        manifest['binding']['selected_ids'][-1] = '600014.SH'
    elif kind == 'unknown_selected':
        run['results'][delivery.STRATEGY]['items'][0]['decision_status'] = 'UNKNOWN'
    elif kind == 'input':
        manifest['binding']['input_hash'] = 'f'*64
    elif kind == 'schema':
        manifest['capability_restored'] = True
    else:
        run['results'][delivery.STRATEGY]['full_eligible_pool_retained'] = False
    if kind in ('unknown_selected', 'partial_run'):
        run.pop('content_hash'); run['content_hash'] = digest_json_stream(run)
        manifest['run_sha256'] = write(paths['run'], run)['sha256']
    write(paths['manifest'], manifest)
    with pytest.raises(ValueError):
        invoke(paths, cutoff, tmp_path/'rejected')
    assert not (tmp_path/'rejected').exists()


def test_missing_run_is_not_empty_and_existing_output_not_overwritten(tmp_path):
    _, cutoff, _, _, _, paths = fixture(tmp_path)
    destination = tmp_path/'existing'; destination.mkdir(); (destination/'keep').write_bytes(b'keep')
    with pytest.raises(ValueError, match='OUTPUT_MUST_BE_NEW'):
        invoke(paths, cutoff, destination)
    paths['run'].unlink()
    with pytest.raises(FileNotFoundError):
        invoke(paths, cutoff, tmp_path/'missing')
    assert (destination/'keep').read_bytes() == b'keep'


def test_genuine_original_reader_and_native_parent_are_consumed_once(tmp_path):
    bundle, cutoff, _, sources, manifest, paths = fixture(tmp_path)
    market_and_themes(tmp_path/'capture', bundle, sources)
    update_sources(paths, sources, manifest)
    invoke(paths, cutoff, tmp_path/'native', membership_mode='CURRENT_COHORT_BACKVIEW')
    saved = json.loads((tmp_path/'native/delivery-manifest.json').read_text())
    assert saved['native_theme_producer_calls'] == 1
    assert saved['source_capability_restored_from_JSON'] is False
    native = json.loads((tmp_path/'native/native-theme-context.json').read_text())
    assert native['source_input_hash'] == digest_market_input(bundle)
    assert saved['native_context_dependency_scope']['unavailable_dates']
    assert saved['counts'] == manifest['binding']['native_counts']


@pytest.mark.parametrize('kind', ['raw', 'typed_clock', 'scope', 'completion', 'synthetic_capability'])
def test_authentication_cannot_be_replaced_by_manifest_flags(tmp_path, kind):
    bundle, cutoff, _, sources, manifest, paths = fixture(tmp_path)
    market_and_themes(tmp_path/'capture', bundle, sources)
    first = sources['captures'][0]
    if kind == 'raw':
        Path(first['raw']['path']).write_bytes(Path(first['raw']['path']).read_bytes()+b' ')
    elif kind == 'typed_clock':
        first['typed_receipt']['ingested_at'] = (cutoff-timedelta(days=1)).isoformat()
    elif kind == 'scope':
        raw = json.loads(Path(first['result']['path']).read_text())
        raw['parameters']['date'] = str(bundle.sessions[-1])
        first['result'] = write(Path(first['result']['path']), raw)
    elif kind == 'completion':
        raw = json.loads(Path(first['result']['path']).read_text())
        raw['completed_at'] = (cutoff-timedelta(days=100)).isoformat()
        first['result'] = write(Path(first['result']['path']), raw)
    else:
        sources['saved_source_capability'] = {'verified': True}
    update_sources(paths, sources, manifest)
    with pytest.raises(ValueError):
        invoke(paths, cutoff, tmp_path/'invalid', membership_mode='CURRENT_COHORT_BACKVIEW')


def test_late_actual_captures_are_unknown_not_backdated_or_dropped_stocks(tmp_path):
    bundle, cutoff, _, sources, manifest, paths = fixture(tmp_path)
    market_and_themes(tmp_path/'capture', bundle, sources, capture_after=cutoff+timedelta(seconds=1))
    update_sources(paths, sources, manifest)
    invoke(paths, cutoff, tmp_path/'late', membership_mode='CURRENT_COHORT_BACKVIEW')
    saved = json.loads((tmp_path/'late/delivery-manifest.json').read_text())
    assert saved['native_theme_producer_calls'] == 0
    assert saved['native_context_dependency_scope']['capture_not_visible_at_context_cutoff']
    assert saved['observation_count'] == 13


def test_parent_binder_rejects_serialized_equal_document_and_wrong_token(tmp_path):
    with pytest.raises(ValueError, match='PRIVATE_NATIVE_ISSUER'):
        delivery._FreshThemeBinding(None, {}, None, None, None, {}, None, {})
    bundle, cutoff, _, _, _, _ = fixture(tmp_path)
    parent = {'TEST_FIXTURE': 'LIVE_OBJECT_NOT_SERIALIZED_CAPABILITY'}
    binder = delivery._FreshThemeBinding(delivery._ISSUER, parent, bundle, cutoff,
                                        None, {}, None, delivery.source_identity())
    with pytest.raises(ValueError, match='LIVE_NATIVE_PARENT_OR_IDENTITY_CHANGED'):
        binder(json.loads(json.dumps(parent)), bundle=bundle, cutoff=cutoff)


@pytest.mark.parametrize('kind', ['unselected_outside', 'explicit_input', 'selection_input', 'selection_strategy', 'inner_identity', 'native_counts', 'unselected_name', 'truncated_unselected', 'duplicate_eligible', 'evaluated'])
def test_full_pool_and_inner_saved_bindings_cannot_be_hidden_by_selected_subset(tmp_path, kind):
    _, cutoff, run, _, manifest, paths = fixture(tmp_path)
    result = run['results'][delivery.STRATEGY]
    if kind == 'unselected_outside':
        result['items'][-1]['instrument_id'] = '002999.SZ'
        result['eligible_pool_ids'][-1] = '002999.SZ'
    elif kind == 'explicit_input':
        run['input_id'] = 'OTHER_EXPLICIT_INPUT'
    elif kind == 'selection_input':
        run['selection_results']['TEST_FIXTURE_SELECTION']['input_id'] = 'WRONG'
    elif kind == 'selection_strategy':
        run['selection_results']['TEST_FIXTURE_SELECTION']['strategy_id'] = 'STAGE_AWARE_MOMENTUM_V2'
    elif kind == 'inner_identity':
        result['config_hash'] = 'f'*64
    elif kind == 'unselected_name':
        result['items'][-1]['name'] = 'TEST_FIXTURE不属于原输入的名称'
    elif kind == 'truncated_unselected':
        result['items'].pop()
        for counts in (manifest['binding']['counts'], manifest['binding']['native_counts'], result['counts']):
            counts['eligible'] -= 1; counts['unknown'] -= 1
            if 'evaluated' in counts:
                counts['evaluated'] -= 1
    elif kind == 'duplicate_eligible':
        result['eligible_pool_ids'].append(result['eligible_pool_ids'][0])
    elif kind == 'evaluated':
        result['counts']['evaluated'] -= 1
        manifest['binding']['native_counts']['evaluated'] -= 1
    else:
        result['counts']['complete_25_window'] = 1
    run.pop('content_hash'); run['content_hash'] = digest_json_stream(run)
    manifest['run_sha256'] = write(paths['run'], run)['sha256']
    write(paths['manifest'], manifest)
    with pytest.raises(ValueError):
        invoke(paths, cutoff, tmp_path/'invalid')


@pytest.mark.parametrize('source_condition', ['MISSING', 'LATE', 'PARTIAL', 'INCOMPLETE_PAGINATION'])
def test_dated_unavailable_theme_refs_keep_complete_saved_observation_unknown(tmp_path, source_condition):
    from stockfish_core.yaogu.contracts import ThemeSnapshot
    bundle, cutoff, _, sources, manifest, paths = fixture(tmp_path)
    market_and_themes(tmp_path/'capture', bundle, sources,
                      capture_after=cutoff+timedelta(seconds=1) if source_condition == 'LATE' else None)
    if source_condition in ('PARTIAL', 'INCOMPLETE_PAGINATION'):
        original = sources['captures'][-1]
        wrapper = json.loads(Path(original['result']['path']).read_text())
        sources['captures'][-1] = capture(tmp_path/'incomplete', wrapper['endpoint'], wrapper['parameters'],
            wrapper['rows'], datetime.fromisoformat(wrapper['first_seen_at']), provider=wrapper['provider'],
            version=wrapper['field_version'], status='PARTIAL' if source_condition == 'PARTIAL' else 'COMPLETE',
            pagination_complete=False)
    value = bundle.model_dump(mode='json')
    refs = (['TEST_FIXTURE_MISSING_PARENT'] if source_condition == 'MISSING'
            else [item['typed_receipt']['receipt_id'] for item in sources['captures'][-2:]])
    value['themes'] = [ThemeSnapshot(theme_id='TEST_FIXTURE_DATED', name='TEST_FIXTURE日期题材',
        theme_type='EVENT', definition_version='TEST_FIXTURE_DATED_MEMBERS_V1',
        member_ids=tuple(manifest['binding']['selected_ids']), effective_from=bundle.observation_date,
        source_refs=tuple(refs), event_time=cutoff, source_published_at=cutoff,
        first_seen_at=cutoff, ingested_at=cutoff).model_dump(mode='json')]
    changed = MarketInput.model_validate(value)
    input_hash = digest_market_input(changed)
    manifest['binding']['input_hash'] = input_hash
    run = json.loads(paths['run'].read_text())
    run['input_hash'] = input_hash; run['results'][delivery.STRATEGY]['input_hash'] = input_hash
    run.pop('content_hash'); run['content_hash'] = digest_json_stream(run)
    manifest['run_sha256'] = write(paths['run'], run)['sha256']
    manifest['input_sha256'] = write(paths['input'], changed.model_dump(mode='json'))['sha256']
    update_sources(paths, sources, manifest)
    invoke(paths, cutoff, tmp_path/'dated', membership_mode='DATED_ONLY')
    saved = json.loads((tmp_path/'dated/delivery-manifest.json').read_text())
    assert saved['native_theme_producer_calls'] == 0
    assert saved['observation_count'] == 13
    unavailable = saved['native_context_dependency_scope']['dated_theme_missing_sources'][0]
    if source_condition in ('MISSING', 'LATE'):
        assert unavailable['unavailable_source_refs'] == refs
    else:
        assert unavailable['incomplete_source_refs'] == [dict(receipt_id=refs[-1],
            status='PARTIAL' if source_condition == 'PARTIAL' else 'COMPLETE', pagination_complete=False)]
        assert 'DATED_THEME_REQUIRED_ORIGINAL_PARENTS_INCOMPLETE' in unavailable['reason_codes']
        audit = json.loads((tmp_path/'dated/NON_DELIVERY_AUDIT/source-authentication-audit.json').read_text())
        assert audit['captures'][-1]['status'] == sources['captures'][-1]['typed_receipt']['status']


def test_primary_overlap_and_native_gradient_preserve_order_without_double_count(tmp_path):
    _, _, run, _, manifest, _ = fixture(tmp_path)
    selected = [row for row in run['results'][delivery.STRATEGY]['items'] if row['research_selected']]
    ids = manifest['binding']['selected_ids']
    diagnostic = {'confirmed_mainlines': [dict(theme_id='a', name='TEST_FIXTURE强', stage='STRENGTHENING', selected_ids=ids[:5]),
        dict(theme_id='b', name='TEST_FIXTURE次', stage='EMERGING', selected_ids=ids[3:7])],
        'fermentation_watch': [], 'active_divergence_mainlines': []}
    result = delivery._complete_groups(diagnostic, selected, manifest['binding'])
    assert [part['name'] for part in result['groups'][:2]] == ['TEST_FIXTURE强', 'TEST_FIXTURE次']
    assert result['groups'][1]['primary_selected_ids'] == ids[5:7]
    assert len(result['secondary_verified_relations'][ids[3]]) == 2
    assert sum(len(part['items']) for part in result['groups']) == 13
    assert result['cross_category_strength_ranking'] == 'UNKNOWN_NO_NEW_WEIGHTS'


def test_explicit_early_asof_keeps_only_original_visible_model_rows(tmp_path, monkeypatch):
    _, cutoff, _, _, _, paths = fixture(tmp_path)
    invoke(paths, cutoff, tmp_path/'old', as_of=cutoff.isoformat())
    saved = json.loads((tmp_path/'old/delivery-manifest.json').read_text())
    assert saved['status'] == 'COMPLETE_SAVED_OBSERVATION_WITH_UNKNOWN_CONTEXT'
    text = (tmp_path/'old/complete-observation-list.txt').read_text()
    assert 'NEW_EXPLANATION_NOT_YET_VISIBLE' in text
    def forbidden(*args, **kwargs):
        raise AssertionError('INVISIBLE_ORIGINAL_MUST_NOT_ISSUE_CONTEXT_OR_EXPLANATION')
    monkeypatch.setattr(delivery, '_source_inputs', forbidden)
    monkeypatch.setattr(delivery, '_theme_diagnostic', forbidden)
    hidden = invoke(paths, cutoff, tmp_path/'too_old', as_of=(cutoff-timedelta(seconds=1)).isoformat())
    assert (tmp_path/'too_old/complete-observation-list.txt').read_text().startswith('NOT_YET_VISIBLE')
    assert 'observation_count' not in hidden and 'counts' not in hidden
    saved = json.loads((tmp_path/'too_old/delivery-manifest.json').read_text())
    assert not {'counts', 'observation_count', 'frozen_selected_ids'}.intersection(saved)
    assert saved['native_theme_producer_calls'] == saved['economic_explanation_producer_calls'] == 0
    assert {path.name for path in (tmp_path/'too_old').iterdir()} == {'delivery-manifest.json', 'complete-observation-list.txt'}
    assert '600001.SH' not in ''.join(path.read_text() for path in (tmp_path/'too_old').iterdir())




def mutate_real_archive_read(monkeypatch, target, *, original_source=None):
    """Mutate real fixture bytes between the real reader's two FP5 checks."""
    from stockfish_core.yaogu import economic_direction_display as display
    original_read, original_open = display._read, Path.open
    state = {'mutated': False, 'real_reader_rejected': False}
    def controlled_read(path):
        path = Path(path)
        if state['mutated'] or path.name != target or 'NON_DELIVERY_AUDIT' not in path.parts:
            return original_read(path)
        class ChangedHandle:
            def __init__(self, handle): self.handle = handle
            def __enter__(self): return self
            def __exit__(self, *args): return self.handle.__exit__(*args)
            def read(self, *args):
                raw = self.handle.read(*args)
                with original_open(path, 'ab') as writer:
                    writer.write(b' TEST_FIXTURE_REAL_ARCHIVE_CHANGE')
                if original_source is not None:
                    with original_open(original_source, 'ab') as writer:
                        writer.write(b' TEST_FIXTURE_REAL_ORIGINAL_CHANGE')
                state['mutated'] = True
                return raw
        def controlled_open(self, *args, **kwargs):
            handle = original_open(self, *args, **kwargs)
            return ChangedHandle(handle) if self == path and args == ('rb',) else handle
        with monkeypatch.context() as scope:
            scope.setattr(Path, 'open', controlled_open)
            try:
                return original_read(path)
            except ValueError as exc:
                state['real_reader_rejected'] = str(exc) == 'ECONOMIC_DISPLAY_ARCHIVE_CHANGED'
                raise
    monkeypatch.setattr(display, '_read', controlled_read)
    return state


@pytest.mark.parametrize('target', ['economic-direction-explanation.json', 'grouped.json'])
def test_real_derived_archive_change_delivers_only_complete_original_model(tmp_path, monkeypatch, target):
    bundle, cutoff, run, sources, manifest, paths = fixture(tmp_path)
    market_and_themes(tmp_path/'capture', bundle, sources)
    update_sources(paths, sources, manifest)
    before = {path: path.read_bytes() for path in paths.values()}
    mutation = mutate_real_archive_read(monkeypatch, target)
    output = tmp_path/'fallback'
    result = invoke(paths, cutoff, output, membership_mode='CURRENT_COHORT_BACKVIEW')
    assert mutation == {'mutated': True, 'real_reader_rejected': True}
    assert (output/'NON_DELIVERY_AUDIT'/target).read_bytes().endswith(b'TEST_FIXTURE_REAL_ARCHIVE_CHANGE')
    saved = json.loads((output/'delivery-manifest.json').read_text())
    assert result['status'] == saved['status'] == 'COMPLETE_SAVED_OBSERVATION_MODEL_FALLBACK'
    assert saved['new_explanation_status'] == 'UNKNOWN_FAILED_ARCHIVE_STATE'
    assert saved['economic_explanation_delivery_succeeded'] is False
    assert saved['display_failure']['message'] == 'ECONOMIC_DISPLAY_ARCHIVE_CHANGED'
    assert saved['observation_count'] == 13 and saved['counts'] == manifest['binding']['native_counts']
    assert saved['count_summary'] == manifest['binding']['counts']
    assert saved['native_theme_producer_calls'] == saved['economic_explanation_producer_calls'] == 1
    assert saved['original_registry_terminal_equal'] is True
    assert not any(name.startswith('NON_DELIVERY_AUDIT/') or name.startswith('display/')
                   or name in ('native-theme-context.json', 'economic-direction-explanation.json') for name in result['files'])
    assert not (output/'display').exists()
    assert not (output/'economic-direction-explanation.json').exists()
    assert not (output/'native-theme-context.json').exists()
    model = json.loads((output/'grouped.json').read_text())
    assert model['projection_role'] == 'ORIGINAL_SAVED_MODEL_ONLY'
    assert model['new_explanation_values_delivered'] is False
    assert 'V4' not in model and 'native_context_dependency_scope' not in model
    selected = [row for row in run['results'][delivery.STRATEGY]['items'] if row['research_selected']]
    lookup = {row['instrument_id']: row for group in model['groups'] for row in group['items']}
    assert [lookup[iid] for iid in manifest['binding']['selected_ids']] == selected
    assert digest_json_stream([lookup[iid] for iid in model['frozen_selected_ids']]) == manifest['binding']['selected_original_items_hash']
    deliverable_contents = json.dumps(result, ensure_ascii=False) + ''.join(
        (output/name).read_text() for name in result['files'] if name.endswith(('.json', '.txt')))
    assert 'TEST_FIXTURE电池' not in deliverable_contents
    assert 'confirmed_mainlines' not in deliverable_contents
    assert {path: path.read_bytes() for path in paths.values()} == before
    audit = json.loads((output/'NON_DELIVERY_AUDIT/audit-manifest.json').read_text())
    assert audit['role'] == 'NON_DELIVERY_AUDIT' and audit['explanation_delivery_succeeded'] is False
    assert audit['write_identities_are_not_current_source_authority'] is True


def test_original_source_change_during_real_archive_failure_is_still_hard_failure(tmp_path, monkeypatch):
    _, cutoff, _, _, _, paths = fixture(tmp_path)
    state = mutate_real_archive_read(monkeypatch, 'economic-direction-explanation.json', original_source=paths['input'])
    with pytest.raises(ValueError, match='FILE_SHA_MISMATCH|SOURCE_CHANGED'):
        invoke(paths, cutoff, tmp_path/'failed')
    assert state['mutated'] and state['real_reader_rejected']
    assert not (tmp_path/'failed/delivery-manifest.json').exists()
    assert not (tmp_path/'failed/complete-observation-list.txt').exists()


@pytest.mark.parametrize('kind', ['content_hash', 'schema'])
def test_real_derived_archive_integrity_errors_are_not_caught_as_state_fallback(tmp_path, monkeypatch, kind):
    from stockfish_core.yaogu import economic_direction_display as display
    _, cutoff, _, _, _, paths = fixture(tmp_path)
    original_read = display._read
    state = {'changed': False}
    def corrupt_then_read(path):
        path = Path(path)
        if path.name == 'economic-direction-explanation.json' and not state['changed']:
            document = json.loads(path.read_text())
            if kind == 'schema': document['schema_version'] = 'TEST_FIXTURE_WRONG_SCHEMA'
            else: document['complete_economic_mainline_coverage'] = 'TEST_FIXTURE_FORGED'
            path.write_text(json.dumps(document, ensure_ascii=False))
            state['changed'] = True
        return original_read(path)
    monkeypatch.setattr(display, '_read', corrupt_then_read)
    with pytest.raises(ValueError, match='ECONOMIC_DISPLAY_SCHEMA_MISMATCH' if kind == 'schema' else 'ECONOMIC_DISPLAY_ATTACHMENT_HASH_MISMATCH'):
        invoke(paths, cutoff, tmp_path/'failed')
    assert state['changed']
    assert not (tmp_path/'failed/delivery-manifest.json').exists()
    assert not (tmp_path/'failed/complete-observation-list.txt').exists()


def test_implementation_change_after_real_archive_failure_is_still_hard_failure(tmp_path, monkeypatch):
    _, cutoff, _, _, _, paths = fixture(tmp_path)
    state = mutate_real_archive_read(monkeypatch, 'economic-direction-explanation.json')
    identity = delivery.source_identity
    def actual_identity_with_changed_declaration():
        value = identity()
        if state['mutated']: value['source_hash'] = 'f'*64
        return value
    monkeypatch.setattr(delivery, 'source_identity', actual_identity_with_changed_declaration)
    with pytest.raises(ValueError, match='IMPLEMENTATION_CHANGED'):
        invoke(paths, cutoff, tmp_path/'failed')
    assert state['real_reader_rejected']
    assert not (tmp_path/'failed/delivery-manifest.json').exists()


def test_normal_complete_explanation_publishes_only_after_archive_success(tmp_path):
    bundle, cutoff, _, sources, manifest, paths = fixture(tmp_path)
    market_and_themes(tmp_path/'capture', bundle, sources)
    update_sources(paths, sources, manifest)
    output = tmp_path/'normal'
    result = invoke(paths, cutoff, output, membership_mode='CURRENT_COHORT_BACKVIEW')
    saved = json.loads((output/'delivery-manifest.json').read_text())
    assert saved['economic_explanation_delivery_succeeded'] is True
    assert saved['new_explanation_status'] == 'DELIVERED_ARCHIVED_EXPLANATION'
    assert saved['display_failure'] is None
    assert saved['version'] == 'SAVED_R2_COMPLETE_DELIVERY_V2'
    assert saved['feature_version'] == 'SAVED_R2_COMPLETE_DELIVERY_PROJECTION_V2'
    assert saved['source_revision'] == saved['source_identity']['source_revision'] == 2
    for name in ('grouped.json', 'economic-direction-explanation.json', 'native-theme-context.json'):
        assert (output/name).read_bytes() == (output/'NON_DELIVERY_AUDIT'/name).read_bytes()
        assert name in result['files'] and 'NON_DELIVERY_AUDIT/'+name not in result['files']
    assert (output/'display/complete-observation-list.txt').exists()


@pytest.mark.parametrize('change', ['APPEND_TXT', 'REPLACE_TXT', 'EXTRA_FILE', 'MANIFEST_DECLARATION'])
def test_real_saved_display_output_change_is_hard_failure_before_delivery(tmp_path, monkeypatch, change):
    from stockfish_core.yaogu import economic_direction_display as display
    _, cutoff, _, _, _, paths = fixture(tmp_path)
    before = {path: path.read_bytes() for path in paths.values()}
    original_save = display.save_archived_economic_direction_display
    state = {'genuine_save_calls': 0, 'genuine_status': None, 'changed': False}
    def genuine_save_then_change(*args, **kwargs):
        result = original_save(*args, **kwargs)
        state['genuine_save_calls'] += 1
        state['genuine_status'] = result['status']
        directory = Path(result['output_directory'])
        text_path = directory/'complete-observation-list.txt'
        if change == 'APPEND_TXT':
            with text_path.open('ab') as handle:
                handle.write(b' TEST_FIXTURE_POST_SAVE_CHANGE')
        elif change == 'REPLACE_TXT':
            replacement = directory/'TEST_FIXTURE_REPLACEMENT'
            replacement.write_bytes(text_path.read_bytes()+b' TEST_FIXTURE_POST_SAVE_REPLACEMENT')
            replacement.replace(text_path)
        elif change == 'EXTRA_FILE':
            (directory/'TEST_FIXTURE_UNDECLARED.txt').write_text('not declared by the genuine producer')
        else:
            manifest_path = directory/'manifest.json'
            manifest = json.loads(manifest_path.read_text())
            manifest['files']['complete-observation-list.txt']['sha256'] = 'f'*64
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False))
        state['changed'] = True
        # Preserve the real producer's return value and expected byte hashes.
        return result
    monkeypatch.setattr(display, 'save_archived_economic_direction_display', genuine_save_then_change)
    reason = ('FILE_SHA_MISMATCH' if change in ('APPEND_TXT', 'REPLACE_TXT') else
              'DISPLAY_OUTPUT_SCOPE_INVALID' if change == 'EXTRA_FILE' else
              'DISPLAY_MANIFEST_OR_TEXT_BINDING_MISMATCH')
    output = tmp_path/'failed'
    with pytest.raises(ValueError, match=reason):
        invoke(paths, cutoff, output)
    assert state == {'genuine_save_calls': 1, 'genuine_status': 'VISIBLE_ARCHIVED_EXPLANATION', 'changed': True}
    assert not (output/'delivery-manifest.json').exists()
    assert not (output/'complete-observation-list.txt').exists()
    assert not (output/'grouped.json').exists()
    assert not (output/'display').exists()
    assert (output/'NON_DELIVERY_AUDIT/display/complete-observation-list.txt').exists()
    assert {path: path.read_bytes() for path in paths.values()} == before


@pytest.mark.parametrize('change', ['EXPLANATION_AMOUNT', 'GROUPED_PARENT_AND_EXPLANATION'])
def test_real_self_consistent_archive_rewrite_before_first_read_is_not_original_write(tmp_path, monkeypatch, change):
    from stockfish_core.yaogu import economic_direction_display as display
    from tests.yaogu.test_economic_direction_explanation_v1 import business
    _, cutoff, run, sources, manifest, paths = fixture(tmp_path)
    first = run['results'][delivery.STRATEGY]['items'][0]
    registration = business(tmp_path/'TEST_FIXTURE_business', iid=first['instrument_id'],
        name=first['name'], clock=cutoff.isoformat())
    declared = {key: getattr(registration, key) for key in registration.__dataclass_fields__}
    declared['literals'] = list(declared['literals'])
    for key in ('original', 'receipt', 'derived_text', 'transform'):
        declared[key] = dict(path=str(declared[key].path), sha256=declared[key].sha256)
    sources['business_sources'] = [declared]
    update_sources(paths, sources, manifest)
    before = {path: path.read_bytes() for path in paths.values()}
    original_read, original_save = display._read, display.save_archived_economic_direction_display
    state = {'changed': False, 'genuine_save_returned': False, 'genuine_status': None}
    def rewrite_then_genuine_read(path):
        path = Path(path)
        if not state['changed'] and path.name == 'economic-direction-explanation.json':
            attachment = json.loads(path.read_bytes())
            if change == 'EXPLANATION_AMOUNT':
                metric = attachment['economic_directions'][0]['selected_subset_metrics']
                assert metric['amount_status'] == 'AVAILABLE' and metric['N'] == 1
                assert metric['amount_cny'] == first['exact_features']['amount']
                metric['amount_cny'] = {'numerator': 2000000000, 'denominator': 1}
            else:
                parent_path = path.with_name('grouped.json')
                parent = json.loads(parent_path.read_bytes())
                parent['groups'][0]['name'] = 'TEST_FIXTURE_SELF_CONSISTENT_CHANGED_GROUP'
                parent.pop('attachment_content_hash')
                parent['attachment_content_hash'] = digest_json_stream(parent)
                parent_bytes = json.dumps(parent, ensure_ascii=False, indent=2).encode()
                parent_path.write_bytes(parent_bytes)
                attachment['original_grouped_file_sha256'] = hashlib.sha256(parent_bytes).hexdigest()
                attachment['frozen_original_groups'] = deepcopy(parent['groups'])
                for group in attachment['primary_display_groups']:
                    if group['group_id'] == parent['groups'][0]['group_id']:
                        group['name'] = parent['groups'][0]['name']
            attachment.pop('attachment_content_hash')
            attachment['attachment_content_hash'] = digest_json_stream(attachment)
            path.write_bytes(json.dumps(attachment, ensure_ascii=False, indent=2).encode())
            state['changed'] = True
        return original_read(path)
    def observe_genuine_save(*args, **kwargs):
        result = original_save(*args, **kwargs)
        state['genuine_save_returned'], state['genuine_status'] = True, result['status']
        return result
    monkeypatch.setattr(display, '_read', rewrite_then_genuine_read)
    monkeypatch.setattr(display, 'save_archived_economic_direction_display', observe_genuine_save)
    output = tmp_path/'failed'
    with pytest.raises(ValueError, match='DISPLAY_CONSUMED_ARCHIVE_NOT_ORIGINAL_WRITE'):
        invoke(paths, cutoff, output)
    assert state == {'changed': True, 'genuine_save_returned': True, 'genuine_status': 'VISIBLE_ARCHIVED_EXPLANATION'}
    archived_text = (output/'NON_DELIVERY_AUDIT/display/complete-observation-list.txt').read_text()
    if change == 'EXPLANATION_AMOUNT':
        assert '20.00亿元' in archived_text
    else:
        assert 'TEST_FIXTURE_SELF_CONSISTENT_CHANGED_GROUP' in archived_text
    assert not (output/'delivery-manifest.json').exists()
    assert not (output/'complete-observation-list.txt').exists()
    assert not (output/'grouped.json').exists()
    assert not (output/'economic-direction-explanation.json').exists()
    assert not (output/'display').exists()
    assert {path: path.read_bytes() for path in paths.values()} == before
