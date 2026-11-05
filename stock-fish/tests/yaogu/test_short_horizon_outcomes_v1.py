"""Isolated TEST_FIXTURE tests for versioned short-horizon observations."""
from copy import deepcopy
from datetime import datetime, time, timedelta
from decimal import Decimal

import pytest

from stockfish_core.yaogu import candidate_service
from stockfish_core.yaogu.contracts import MarketInput, digest
from stockfish_core.yaogu.history_artifacts import digest_json_stream
from stockfish_core.yaogu.short_horizon_outcomes_v1 import HORIZONS, NAMESPACES, VERSION, evaluate
from stockfish_core.yaogu.strategy_experiments import run_short_horizon_strategy
from tests.support.short_horizon_strength_fixture import START, WEAK, clocks, payload, strength_bundle, TZ
from tests.yaogu.test_short_horizon_strength_r2_integration import r2_api, launch, wait_for_job


def future_input(original):
    value = payload(original)
    end = original.observation_date
    # The old signal fixture intentionally uses one synthetic historical
    # receipt. Re-observe the frozen reference in this NEW outcome input with
    # a final daily-price response; never mutate the original run/input.
    reference_rows = [bar for bar in value['bars'] if bar['session'] == end]
    for bar in reference_rows:
        bar['source_refs'] = ['TEST_FIXTURE_REFERENCE_CLOSE']
    reference_receipt = deepcopy(value['receipts'][1])
    reference_receipt.update(receipt_id='TEST_FIXTURE_REFERENCE_CLOSE', dataset='history',
        content_hash=digest(reference_rows), row_count=len(reference_rows), business_sessions=[end], **clocks(end))
    value['receipts'].append(reference_receipt)
    future = [end + timedelta(days=d) for d in (3, 4, 5, 6, 7, 10, 11, 12, 13, 14)]
    rows = []
    for iid in {bar.instrument_id for bar in original.bars}:
        reference = next(bar for bar in original.bars if bar.instrument_id == iid and bar.session == end)
        for index, day in enumerate(future, 1):
            close = reference.close + Decimal(index) / 10
            bar = reference.model_dump(mode='python')
            bar.update(session=day, open=close - Decimal('.05'), high=close + Decimal('.1'),
                low=close - Decimal('.1'), close=close, tick_size=Decimal('.00000001'),
                source_refs=['TEST_FIXTURE_FUTURE_' + str(day)], **clocks(day))
            for field in ('open', 'high', 'low', 'close'):
                bar['comparable_' + field] = bar[field] * 2
            rows.append(bar)
    for day in future:
        day_rows = [bar for bar in rows if bar['session'] == day]
        receipt = deepcopy(value['receipts'][1])
        receipt.update(receipt_id='TEST_FIXTURE_FUTURE_' + str(day), dataset='history', content_hash=digest(day_rows),
                       row_count=len(day_rows), business_sessions=[day], **clocks(day))
        value['receipts'].append(receipt)
    value['bars'].extend(rows)
    value['sessions'].extend(future)
    value['observation_date'] = future[-1]
    return MarketInput.model_validate(value), datetime.combine(future[-1], time(20), TZ)


@pytest.fixture
def evidence():
    original, cutoff = strength_bundle()
    run = run_short_horizon_strategy(original, cutoff=cutoff)
    run['content_hash'] = digest_json_stream(run)
    outcome, evaluated = future_input(original)
    return run, original, outcome, evaluated


def row(products, iid=START, horizon=3):
    return next(r for r in products['outcomes']['items'] if r['instrument_id'] == iid and r['horizon'] == horizon)


def revise(bundle, mutate):
    value = payload(bundle)
    mutate(value)
    return MarketInput.model_validate(value)


def test_exact_frozen_prices_session_units_and_full_pool(evidence):
    run, original, bundle, evaluated = evidence
    saved = deepcopy(run)
    products = evaluate(run, bundle, evaluated)
    assert run == saved
    assert len(products['outcomes']['items']) == 4 * len(HORIZONS)
    assert all(r['status'] == 'MATURE' for r in products['outcomes']['items'])
    assert row(products)['metrics']['close_reference_change'] == pytest.approx(11.3 / 11 - 1)
    assert row(products)['label_end'] == str(original.observation_date + timedelta(days=5))
    assert row(products, WEAK)['research_selected'] is False
    assert all(not r['formal_focus'] for r in products['outcomes']['items'])
    assert products['tracking']['tracking_semantics'] == 'PER_SESSION_OBSERVATION_NO_EPISODE_OR_TRADE'
    assert products['review']['counts']['distinct_signal_dates'] == 1
    assert products['review']['market_edge'] == 'NOT_ESTABLISHED'
    groups = products['review']['by_horizon_and_selection']
    assert all(g['inference_status'].startswith('INSUFFICIENT_') for g in groups)
    assert {g['cohort'] for g in groups} == {'ALL_EVALUATED', 'RESEARCH_SELECTED'}
    assert products['outcomes']['returns'] is None


@pytest.mark.parametrize('fault,reason', [
    ('missing', 'MISSING_OR_NOT_VISIBLE_PRICE'),
    ('missing_parent', 'MISSING_OR_NOT_VISIBLE_PRICE'),
    ('late_parent', 'MISSING_OR_NOT_VISIBLE_PRICE'),
    ('late_row', 'MISSING_OR_NOT_VISIBLE_PRICE'),
    ('partial_parent', 'PRICE_PARENT_NOT_COMPLETE_OR_VISIBLE'),
    ('partial_bar', 'INVALID_OR_PARTIAL_OHLC'),
    ('basis', 'COMPARABILITY_UNSUPPORTED'),
    ('suspended', 'SUSPENDED_NO_ASSUMED_FILL'),
    ('unknown_state', 'DATED_SECURITY_STATE_UNKNOWN'),
    ('delisted', 'NOT_LISTED_OR_DELISTED'),
    ('reference_revision', 'FROZEN_REFERENCE_MISSING_NOT_VISIBLE_OR_REVISED'),
])
def test_gaps_and_visibility_never_become_zero_or_endpoint_only_returns(evidence, fault, reason):
    run, original, bundle, evaluated = evidence
    def mutate(value):
        middle = original.observation_date + timedelta(days=4)
        bar = next(b for b in value['bars'] if b['instrument_id'] == START and b['session'] == middle)
        receipt = next(r for r in value['receipts'] if r['receipt_id'] == 'TEST_FIXTURE_FUTURE_' + str(middle))
        state = next(s for s in value['universe']['securities'] if s['instrument_id'] == START)
        if fault == 'missing': value['bars'].remove(bar)
        elif fault == 'missing_parent': bar['source_refs'] = ['TEST_FIXTURE_ABSENT_PARENT']
        elif fault == 'late_parent':
            receipt.update(first_seen_at=evaluated + timedelta(days=1), ingested_at=evaluated + timedelta(days=1))
        elif fault == 'late_row':
            bar.update(first_seen_at=evaluated + timedelta(days=1), ingested_at=evaluated + timedelta(days=1))
        elif fault == 'partial_parent': receipt['status'] = 'PARTIAL'
        elif fault == 'partial_bar': bar['quality'] = 'PARTIAL'
        elif fault == 'basis': bar['comparable_basis'] = 'TEST_FIXTURE_DIFFERENT_BASIS'
        elif fault == 'suspended': state['suspended'] = True
        elif fault == 'unknown_state': state['suspended'] = None
        elif fault == 'delisted': state['delisted_on'] = middle
        elif fault == 'reference_revision':
            reference = next(b for b in value['bars'] if b['instrument_id'] == START and b['session'] == original.observation_date)
            reference['comparable_close'] += 1
    products = evaluate(run, revise(bundle, mutate), evaluated)
    observed = row(products)
    assert observed['status'] == 'UNKNOWN'
    assert reason in observed['reason_codes']
    assert all(value is None for value in observed['metrics'].values())
    assert products['review']['counts']['unknown'] > 0


def test_late_capture_not_relaxed_for_historical_reconstruction(evidence):
    run, original, bundle, evaluated = evidence
    evaluated = datetime(2026, 10, 15, 20, tzinfo=TZ)
    def late(value):
        value['coverage_type'] = 'RECONSTRUCTED_HISTORICAL'
        bar = next(b for b in value['bars'] if b['instrument_id'] == START and b['session'] > original.observation_date)
        # Parent was captured earlier: legacy resolver permits reconstructed row
        # late capture in this case. This new evaluation policy must still deny it.
        bar.update(first_seen_at=evaluated + timedelta(days=1), ingested_at=evaluated + timedelta(days=1))
    result = row(evaluate(run, revise(bundle, late), evaluated))
    assert result['status'] == 'UNKNOWN'
    assert 'MISSING_OR_NOT_VISIBLE_PRICE' in result['reason_codes']


def test_session_close_maturity_and_missing_calendar_are_pending(evidence):
    run, original, bundle, evaluated = evidence
    first = original.observation_date + timedelta(days=3)
    before_close = datetime.combine(first, time(14, 59), TZ)
    assert row(evaluate(run, bundle, before_close), horizon=1)['status'] == 'PENDING'
    assert row(evaluate(run, bundle, datetime.combine(first, time(15), TZ)), horizon=1)['status'] == 'MATURE'
    pending = evaluate(run, original, evaluated)
    assert all(r['status'] == 'UNKNOWN' and r['label_end'] is None and r['calendar_mature'] is None
               for r in pending['outcomes']['items'])
    assert all(r['metrics']['close_reference_change'] is None for r in pending['outcomes']['items'])
    assert all(g['mean_close_reference_change'] is None for g in pending['review']['by_horizon_and_selection'])


def test_identity_idempotence_input_revision_and_frozen_hash(evidence):
    run, original, bundle, evaluated = evidence
    first = evaluate(run, bundle, evaluated)
    again = evaluate(run, bundle, evaluated + timedelta(minutes=3))
    assert first['outcomes']['revision_id'] == again['outcomes']['revision_id']
    changed = revise(bundle, lambda value: value.update(coverage_type='PROSPECTIVE_CAPTURED'))
    assert evaluate(run, changed, evaluated)['outcomes']['revision_id'] != first['outcomes']['revision_id']
    bad = deepcopy(run)
    bad['results'][bad['strategy_id']]['items'][0]['research_selected'] = False
    with pytest.raises(ValueError, match='CONTENT_HASH'):
        evaluate(bad, bundle, evaluated)
    with pytest.raises(ValueError, match='PRECEDES_SIGNAL'):
        evaluate(run, bundle, datetime.fromisoformat(run['decision_cutoff']) - timedelta(seconds=1))


def test_unverifiable_coverage_is_rejected_and_missing_calendar_session_not_compressed(evidence):
    run, original, bundle, evaluated = evidence
    unverified = revise(bundle, lambda value: value.update(coverage_type='UNVERIFIABLE_AS_OF'))
    with pytest.raises(ValueError, match='UNVERIFIABLE_OUTCOME_COVERAGE'):
        evaluate(run, unverified, evaluated)
    missing_first = revise(bundle, lambda value: value['sessions'].remove(original.observation_date + timedelta(days=3)))
    result = row(evaluate(run, missing_first, evaluated), horizon=1)
    assert result['status'] == 'UNKNOWN' and result['calendar_mature'] is None
    assert result['label_end'] is None and result['metrics']['close_reference_change'] is None
    assert 'REGISTERED_CALENDAR_HORIZON_UNAVAILABLE' in result['reason_codes']
    missing_signal = revise(bundle, lambda value: value['sessions'].remove(original.observation_date))
    assert row(evaluate(run, missing_signal, evaluated))['calendar_mature'] is None


def test_real_calendar_verification_clock_is_not_backdated(evidence):
    run, _, bundle, evaluated = evidence
    real_clock_shape = revise(bundle, lambda value: value.update(coverage_type='RECONSTRUCTED_HISTORICAL'))
    result = row(evaluate(run, real_clock_shape, evaluated))
    assert result['status'] == 'UNKNOWN' and result['calendar_mature'] is None
    assert 'LOCAL_CALENDAR_VERIFICATION_NOT_AVAILABLE_AT_EVALUATION' in result['reason_codes']
    later = evaluate(run, real_clock_shape, datetime(2026, 10, 15, 20, tzinfo=TZ))
    assert row(later)['status'] == 'MATURE'
    assert later['outcomes']['calendar_evidence']['test_fixture_clock_exception'] is False


def test_rounded_display_reference_never_replaces_exact_reference(evidence):
    run, _, bundle, evaluated = evidence
    changed = deepcopy(run)
    item = next(r for r in changed['results'][changed['strategy_id']]['items'] if r['instrument_id'] == START)
    item['comparable_close'] = 999
    changed['content_hash'] = digest_json_stream({k:v for k,v in changed.items() if k != 'content_hash'})
    assert row(evaluate(changed, bundle, evaluated))['metrics'] == row(evaluate(run, bundle, evaluated))['metrics']


@pytest.mark.parametrize('basis', ['', '   '])
def test_empty_comparable_basis_cannot_produce_numeric_observations(evidence, basis):
    run, _, bundle, evaluated = evidence
    changed = deepcopy(run)
    item = next(r for r in changed['results'][changed['strategy_id']]['items'] if r['instrument_id'] == START)
    item['comparable_basis'] = basis
    changed['content_hash'] = digest_json_stream({k:v for k,v in changed.items() if k != 'content_hash'})
    def blank(value):
        for bar in value['bars']:
            if bar['instrument_id'] == START:
                bar['comparable_basis'] = basis
    result = row(evaluate(changed, revise(bundle, blank), evaluated))
    assert result['status'] == 'UNKNOWN'
    assert all(value is None for value in result['metrics'].values())


@pytest.mark.parametrize('field', ['event_time', 'source_published_at'])
def test_early_session_bar_cannot_become_daily_close_when_evaluated_later(evidence, field):
    run, original, bundle, evaluated = evidence
    def early(value):
        bar = next(b for b in value['bars'] if b['instrument_id'] == START and b['session'] > original.observation_date)
        bar[field] = datetime.combine(bar['session'], time(10), TZ)
    result = row(evaluate(run, revise(bundle, early), evaluated))
    assert result['status'] == 'UNKNOWN'
    assert 'DAILY_CLOSE_NOT_FINAL_AS_OF_SESSION' in result['reason_codes']
    assert all(value is None for value in result['metrics'].values())


@pytest.mark.parametrize('reference', [False, True])
@pytest.mark.parametrize('early_ingest', [False, True])
def test_early_price_parent_cannot_support_later_final_daily_bar(evidence, reference, early_ingest):
    run, original, bundle, evaluated = evidence
    target = original.observation_date if reference else original.observation_date + timedelta(days=3)
    receipt_id = 'TEST_FIXTURE_REFERENCE_CLOSE' if reference else 'TEST_FIXTURE_FUTURE_' + str(target)
    def early_parent(value):
        receipt = next(r for r in value['receipts'] if r['receipt_id'] == receipt_id)
        receipt.update(first_seen_at=datetime.combine(target, time(10), TZ))
        if early_ingest:
            receipt.update(**clocks(target, time(10)))
        # The daily row remains final at 15:00. Delaying only row derivation
        # cannot create facts absent from its immutable early parent response.
    result = row(evaluate(run, revise(bundle, early_parent), evaluated), horizon=1)
    assert result['status'] == 'UNKNOWN'
    assert 'PRICE_PARENT_CAPTURE_PRECEDES_DAILY_CLOSE' in result['reason_codes']
    assert all(value is None for value in result['metrics'].values())


def test_unrecognized_parent_dataset_cannot_be_guessed_as_price_authority(evidence):
    run, original, bundle, evaluated = evidence
    def unknown_role(value):
        target = original.observation_date + timedelta(days=3)
        receipt = next(r for r in value['receipts'] if r['receipt_id'] == 'TEST_FIXTURE_FUTURE_' + str(target))
        receipt['dataset'] = 'TEST_FIXTURE_UNVERIFIED_PRICE_ROLE'
    result = row(evaluate(run, revise(bundle, unknown_role), evaluated), horizon=1)
    assert result['status'] == 'UNKNOWN'
    assert 'DAILY_PRICE_PARENT_ROLE_UNVERIFIED' in result['reason_codes']


def test_earlier_auxiliary_factor_does_not_replace_or_invalidate_final_price_parent(evidence):
    run, original, bundle, evaluated = evidence
    def add_factor(value):
        receipt = deepcopy(value['receipts'][0])
        receipt.update(receipt_id='TEST_FIXTURE_EARLY_FACTOR', dataset='adjustment_factors',
                       content_hash=digest({'TEST_FIXTURE_FACTOR': 2}), business_sessions=[])
        value['receipts'].append(receipt)
        for bar in value['bars']:
            if bar['session'] >= original.observation_date:
                bar['source_refs'] = list(bar['source_refs']) + ['TEST_FIXTURE_EARLY_FACTOR']
    result = row(evaluate(run, revise(bundle, add_factor), evaluated))
    assert result['status'] == 'MATURE'
    assert result['metrics']['close_reference_change'] == pytest.approx(11.3 / 11 - 1)


def test_genuine_later_historical_download_visible_at_evaluation_remains_usable(evidence):
    run, original, bundle, _ = evidence
    captured = datetime(2026, 10, 14, 16, tzinfo=TZ)
    evaluated = captured + timedelta(days=1)
    def later_download(value):
        value['coverage_type'] = 'RECONSTRUCTED_HISTORICAL'
        for receipt in value['receipts']:
            if receipt['dataset'] == 'history':
                receipt.update(first_seen_at=captured, ingested_at=captured)
        for bar in value['bars']:
            if bar['session'] >= original.observation_date:
                bar.update(first_seen_at=captured, ingested_at=captured)
    products = evaluate(run, revise(bundle, later_download), evaluated)
    assert all(result['status'] == 'MATURE' for result in products['outcomes']['items'])
    assert row(products)['metrics']['close_reference_change'] == pytest.approx(11.3 / 11 - 1)


@pytest.mark.parametrize('horizons', [[], [0], [4], [20], [True], ['1']])
def test_invalid_horizons_fail_closed(evidence, horizons):
    run, _, bundle, evaluated = evidence
    with pytest.raises(ValueError, match='H1_H2_H3_H5_H10'):
        evaluate(run, bundle, evaluated, horizons=horizons)


def test_service_api_jobs_persist_three_products_idempotently_without_legacy_writes(r2_api, monkeypatch):
    core, client, input_id, original, cutoff = r2_api
    run_id = launch(core, client, input_id)
    saved = deepcopy(core.candidates.strategy_experiment(run_id))
    pending = client.strategy_outcomes(run_id)
    assert pending['schema_version'] == VERSION and pending['status'] == 'PENDING'
    bundle, evaluated = future_input(original)
    outcome_id = core.candidates.register_input(bundle, activate=False)
    original_bytes = core.store.get_document_bytes('candidate_inputs', input_id)
    outcome_bytes = core.store.get_document_bytes('candidate_inputs', outcome_id)
    monkeypatch.setattr(candidate_service, 'utc_now', lambda: evaluated)
    monkeypatch.setattr(core.candidates, '_all', lambda *_: (_ for _ in ()).throw(AssertionError('full namespace scan')))
    for endpoint in ('outcomes', 'tracking'):
        response = getattr(client, 'submit_strategy_' + endpoint)(run_id, outcome_id)
        completed = wait_for_job(core, response['job_id'])
        assert completed['state'] == 'SUCCEEDED', completed
        monkeypatch.setattr(candidate_service, 'utc_now', lambda: evaluated + timedelta(seconds=10))
    for endpoint, namespace in NAMESPACES.items():
        response = getattr(client, 'strategy_' + endpoint)(run_id)
        assert response['status'] == 'AVAILABLE'
        assert response['revision_count'] == 1
        assert response['evaluated_at'] == evaluated.isoformat()
        assert len(core.store.documents_for_run(namespace, run_id)) == 1
    assert core.candidates.strategy_experiment(run_id) == saved
    assert core.store.get_document_bytes('candidate_inputs', input_id) == original_bytes
    assert core.store.get_document_bytes('candidate_inputs', outcome_id) == outcome_bytes
    detail = client.strategy_experiment_item_detail(run_id, START)
    assert detail['outcomes'] == [r for r in core.candidates.strategy_outcomes(run_id)['items'] if r['instrument_id'] == START]
    assert detail['short_horizon_observation']['outcomes']['revision_id'] == core.candidates.strategy_outcomes(run_id)['revision_id']
    assert detail['short_horizon_observation']['tracking']['schema_version'] == VERSION
    assert len(detail['tracking']) == 1
    for namespace in ('strategy_research_pending', 'strategy_research_horizon_requests',
                      'strategy_trading_session_observations', 'strategy_tracking_runs',
                      'research_episodes', 'research_episode_observations', 'recommendation_score_revisions',
                      'stage_aware_prospective_primary_runs'):
        assert core.store.list_document_metadata(namespace) == []
    with pytest.raises(ValueError):
        client.submit_strategy_outcomes(run_id)


def test_service_new_input_creates_revision_scoped_to_original_run(r2_api, monkeypatch):
    core, client, input_id, original, cutoff = r2_api
    run_id = launch(core, client, input_id)
    bundle, evaluated = future_input(original)
    outcome_id = core.candidates.register_input(bundle, activate=False)
    monkeypatch.setattr(candidate_service, 'utc_now', lambda: evaluated)
    for source in (input_id, outcome_id):
        job_id = core.candidates.submit_strategy_outcomes(run_id, source)['job_id']
        assert wait_for_job(core, job_id)['state'] == 'SUCCEEDED'
        monkeypatch.setattr(candidate_service, 'utc_now', lambda: evaluated + timedelta(minutes=1))
    product = core.candidates.strategy_outcomes(run_id)
    assert product['revision_count'] == 2
    assert product['outcome_input_id'] == outcome_id
    assert all(r['status'] == 'MATURE' for r in product['items'])
    assert core.store.documents_for_run(NAMESPACES['outcomes'], 'TEST_FIXTURE_OTHER_RUN') == []


def test_cancelled_job_publishes_no_short_horizon_products(r2_api, monkeypatch):
    from threading import Event
    from stockfish_core.yaogu import short_horizon_outcomes_v1 as module
    core, client, input_id, original, _ = r2_api
    run_id = launch(core, client, input_id)
    bundle, evaluated = future_input(original)
    outcome_id = core.candidates.register_input(bundle, activate=False)
    monkeypatch.setattr(candidate_service, 'utc_now', lambda: evaluated)
    reached, resume = Event(), Event()
    implementation = module.evaluate
    def bounded_pause(*args, checkpoint, **kwargs):
        calls = 0
        def check():
            nonlocal calls
            calls += 1
            if calls == 2:
                reached.set()
                assert resume.wait(5), 'TEST_FIXTURE cancellation release timeout'
            checkpoint()
        return implementation(*args, checkpoint=check, **kwargs)
    monkeypatch.setattr(module, 'evaluate', bounded_pause)
    job_id = core.candidates.submit_strategy_tracking(run_id, outcome_id)['job_id']
    try:
        assert reached.wait(5), 'TEST_FIXTURE worker did not reach per-instrument checkpoint'
        core.jobs.cancel(job_id)
    finally:
        resume.set()
    assert wait_for_job(core, job_id)['state'] == 'CANCELLED'
    for namespace in NAMESPACES.values():
        assert core.store.documents_for_run(namespace, run_id) == []
