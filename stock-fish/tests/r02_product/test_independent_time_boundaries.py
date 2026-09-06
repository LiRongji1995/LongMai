"""Independent small TEST_FIXTURE contracts; no generated fixture enters user DB."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import threading
import time as wall_time
from zoneinfo import ZoneInfo

import pytest

from stockfish_core.common import UnavailableError
from stockfish_core.storage import SQLiteStore, ImmutableConflict
from stockfish_core.jobs import JobService
from stockfish_core.yaogu.contracts import (CandidateItem, CandidateRun, DailyBar, FeatureValue,
    MarketEmotionSnapshot, MarketInput, SecurityRecord, SourceReceipt, UniverseSnapshot, digest)
from stockfish_core.yaogu.auction import AuctionSnapshot, evaluate_auction, freeze_theses
from stockfish_core.yaogu.outcomes import evaluate_outcomes
from stockfish_core.yaogu.candidate_service import CandidateService
from stockfish_core.yaogu.config import StrategyConfig

SH = ZoneInfo('Asia/Shanghai')
DAY = date(2026, 8, 24)
IID = '600000.SH'
SESSIONS = tuple(DAY + timedelta(days=i) for i in range(5))


def at(day, hour=15, minute=0):
    return datetime.combine(day, time(hour, minute), SH)


def clocks(value):
    return dict(event_time=value, source_published_at=value, first_seen_at=value, ingested_at=value)


def tiny_fixture(family='ONE_TO_TWO'):
    sec = SecurityRecord(instrument_id=IID, exchange='SSE', board='MAIN', security_type='A_COMMON',
        listed_on=date(2000, 1, 1), effective_from=date(2000, 1, 1), st=False, suspended=False,
        source_refs=('TEST_FIXTURE_MASTER',),
        **clocks(at(DAY, 9)))
    universe = UniverseSnapshot(snapshot_id='TEST_FIXTURE', securities=(sec,), complete=True, expected_count=1,
                                source_refs=('TEST_FIXTURE_MASTER',),
                                **clocks(at(DAY, 9)))
    def bar(day):
        return DailyBar(instrument_id=IID, session=day, open=10, high=10, low=10, close=10,
            reference_prev_close=10, volume_shares=10000, amount_cny=100000, limit_up=20, limit_down=1,
            limit_applicable=True, limit_verified=True, comparable_open=10, comparable_high=10,
            comparable_low=10, comparable_close=10, comparable_basis='TEST_FIXTURE_RAW_BASIS',
            source_refs=('TEST_FIXTURE_BAR_'+day.isoformat(),), **clocks(at(day)))
    bars=tuple(bar(day) for day in SESSIONS)
    master_receipt=SourceReceipt(receipt_id='TEST_FIXTURE_MASTER',provider='TEST_FIXTURE',dataset='master',
        content_hash=digest(sec),field_version='TEST_FIXTURE',row_count=1,pagination_complete=True,status='COMPLETE',
        **clocks(at(DAY,9)))
    bar_receipts=tuple(SourceReceipt(receipt_id='TEST_FIXTURE_BAR_'+b.session.isoformat(),provider='TEST_FIXTURE',
        dataset='daily_bar',content_hash=digest(b),field_version='TEST_FIXTURE',row_count=1,pagination_complete=True,
        status='COMPLETE',**clocks(at(b.session))) for b in bars)
    bundle = MarketInput(observation_date=DAY, sessions=SESSIONS, universe=universe,
                         bars=bars,receipts=(master_receipt,*bar_receipts),coverage_type='TEST_FIXTURE')
    def feature(value, unit):
        return FeatureValue(value=value, unit=unit, window='TEST_FIXTURE', as_of=at(DAY))
    item = CandidateItem(instrument_id=IID, name='TEST_FIXTURE', primary_family=family, secondary_tags=(),
        features={'amount_cny': feature(100000, 'CNY'), 'comparable_close': feature(10, 'CNY'),
                  'previous_run_end_close': feature(11, 'CNY')}, components={k:80 for k in 'BVTLGM'},
        weights={}, contributions={}, score=80, daily_score=80, family_rank=1, tier='FOCUS',
        primary_theme=None, reason_codes=(), risk_flags=(), missing=())
    run = CandidateRun(run_id='TEST_FIXTURE_RUN', revision=1, parent_run_id=None, generated_at=at(DAY, 20, 30),
        observation_date=DAY, decision_cutoff=at(DAY, 20, 30), market_session='EVENING', strategy_id='TEST_FIXTURE',
        config_hash='TEST_FIXTURE', feature_definition_version='YAOGU_FEATURES_V1', source_hash='TEST_FIXTURE',
        universe_hash=digest(universe), input_hash=digest(bundle), semantic_hash='TEST_FIXTURE', profile='DAILY_V1',
        coverage_type='TEST_FIXTURE', coverage={}, status='COMPLETE', items=(item,), display_ids=(IID,), focus_ids=(IID,),
        excluded=(), unavailable=(), emotion=MarketEmotionSnapshot(session=DAY, as_of=at(DAY),score=70,state='FERMENT'), source_refs=())
    return freeze_theses(run, bundle), bundle


def quote(day=SESSIONS[1], hour=9, minute=25, **changes):
    values = dict(instrument_id=IID, session=day, matched_price=Decimal('9.70'), matched_amount_cny=2000,
        reference_prev_close=10, limit_up=11, source_ref='TEST_FIXTURE_0925', capture_kind='AUCTION_0925', quality='VALID',
        **clocks(at(day, hour, minute)))
    return AuctionSnapshot(**(values | changes))


def settle(jobs, job_id):
    deadline = wall_time.monotonic() + 10
    while wall_time.monotonic() < deadline:
        value = jobs.get(job_id)
        if value['state'] in {'SUCCEEDED','FAILED','CANCELLED','INTERRUPTED'}:
            return value
        wall_time.sleep(.01)
    raise AssertionError('bounded TEST_FIXTURE job did not settle')


def publish_fixture(service, run, input_id='TEST_FIXTURE'):
    def worker(ctx):
        def write(connection):
            value = run.model_dump(mode='json')
            service.store._put_document(connection, 'candidate_runs', run.run_id, value)
            service.store._put_document(connection, 'candidate_publications', run.run_id,
                {'run_hash': digest(value), 'job_id': ctx.job_id, 'config_hash':run.config_hash, 'input_id':input_id})
        return ctx.complete({'candidate_run_id':run.run_id}, write)
    job = service.jobs.submit('TEST_FIXTURE_PUBLISH', {}, worker)
    assert settle(service.jobs, job['job_id'])['state'] == 'SUCCEEDED'


@pytest.mark.parametrize('contaminant', ['next_session', '0930_same_session'])
def test_auction_unrelated_later_quote_cannot_hide_frozen_0925(contaminant):
    run, _ = tiny_fixture()
    good = quote()
    bad = quote(SESSIONS[2]) if contaminant == 'next_session' else quote(hour=9,minute=30)
    cutoff = at(SESSIONS[2], 16)
    receipt=SourceReceipt(receipt_id=good.source_ref,provider='TEST_FIXTURE',dataset='AUCTION_0925',
        source_type='AUCTION_0925',instrument_ids=(IID,),business_sessions=(good.session,bad.session),
        content_hash=digest((good,bad)),field_version='TEST_FIXTURE',row_count=2,pagination_complete=True,
        status='COMPLETE',**clocks(good.event_time))
    context=dict(receipts=(receipt,),sessions=SESSIONS,coverage_type='TEST_FIXTURE')
    baseline = evaluate_auction(run, (good,), cutoff,**context)[0]
    changed = evaluate_auction(run, (good, bad), cutoff,**context)[0]
    assert baseline.conditions['GAP_WEAK']['state'] == 'TRUE'
    assert changed.conditions['GAP_WEAK'] == baseline.conditions['GAP_WEAK']
    assert changed.reviewed_tier == baseline.reviewed_tier == 'WATCH'


def test_auction_persisted_summary_does_not_label_invalid_quotes_evaluated(tmp_path):
    store = SQLiteStore(tmp_path/'db.sqlite3')
    with JobService(store) as jobs:
        service = CandidateService(store, jobs, tmp_path/'state', allow_test_fixture=True)
        run, _ = tiny_fixture()
        publish_fixture(service, run)
        snapshot = service.register_auction((quote(minute=30),), coverage_type='TEST_FIXTURE')
        job = service.submit_auction(run.run_id, snapshot)
        assert settle(jobs, job['job_id'])['state'] == 'SUCCEEDED'
        saved = service.auction_reviews(run.run_id)[0]
        assert saved['evaluations'][0]['status'] == 'UNAVAILABLE'
        assert saved['status'] == 'UNAVAILABLE'


def test_outcome_reference_revision_blocks_rebound_price_label():
    run, bundle = tiny_fixture('REBOUND_SETUP')
    bars = tuple(bar.model_copy(update={'comparable_close': Decimal('12'), 'comparable_high':Decimal('12'),
                    'comparable_low':Decimal('12'), 'comparable_open':Decimal('12')}) for bar in bundle.bars)
    revised = bundle.model_copy(update={'bars': bars})
    outcome = evaluate_outcomes(run, revised, at(SESSIONS[3], 16), horizons=(3,))[0]
    assert 'REFERENCE_REVISION_MISMATCH' in outcome.reason_codes
    assert outcome.metrics['close_reference_change'] is None
    assert outcome.structure_success is None


def test_strategy_id_is_unique_under_concurrent_different_config_registration(tmp_path, monkeypatch):
    store = SQLiteStore(tmp_path/'db.sqlite3')
    with JobService(store) as jobs:
        service = CandidateService(store, jobs, tmp_path/'state', allow_test_fixture=True)
        original, synchronized, seen = service._all, threading.Barrier(2), set()
        guard = threading.Lock()
        def frozen_read(namespace):
            result = original(namespace)
            if namespace == 'strategy_configs' and threading.current_thread().name.startswith('config-race'):
                with guard:
                    first = threading.get_ident() not in seen
                    seen.add(threading.get_ident())
                if first:
                    synchronized.wait(5)
            return result
        monkeypatch.setattr(service, '_all', frozen_read)
        configs = []
        for score in (70,71):
            value = StrategyConfig().model_dump(mode='json')
            value['strategy_id'] = 'CONCURRENT_TEST_FIXTURE_V1'
            value['thresholds']['focus_score'] = score
            configs.append(StrategyConfig.model_validate(value))
        def register(config):
            try:
                service.register_config(config)
                return 'REGISTERED'
            except ImmutableConflict:
                return 'CONFLICT'
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix='config-race') as pool:
            results = list(pool.map(register, configs))
        assert sorted(results) == ['CONFLICT','REGISTERED']
        saved = [row for row in original('strategy_configs') if row['config']['strategy_id']=='CONCURRENT_TEST_FIXTURE_V1']
        assert len(saved) == 1


def test_coverage_joins_publication_input_identity_not_different_hash_domains(tmp_path):
    from tests.yaogu.fixtures import make_bundle
    from stockfish_core.yaogu.engine import run as build_run
    store = SQLiteStore(tmp_path/'db.sqlite3')
    with JobService(store) as jobs:
        service = CandidateService(store, jobs, tmp_path/'state', allow_test_fixture=True)
        bundle,cutoff=make_bundle()
        input_id = service.register_input(bundle, activate=True)
        run=build_run(bundle,service.current_config(),cutoff=cutoff,generated_at=cutoff,run_id='TEST_FIXTURE_COVERAGE')
        assert run.input_hash != digest(bundle)
        publish_fixture(service, run, input_id)
        summary = service.coverage()
        assert summary['consumer_status'] == run.status
        assert summary['status'] == ('STALE' if summary['stale'] else run.status)
        assert summary['coverage'] == run.coverage


def test_short_mature_horizon_does_not_invent_three_session_structure_label():
    run, bundle = tiny_fixture('TREND_NM')
    result = evaluate_outcomes(run, bundle, at(SESSIONS[1],16), horizons=(1,3))
    assert result[0].status == 'MATURE' and result[0].structure_success is None
    assert result[1].status == 'NOT_MATURED' and result[1].structure_success is None
    assert result[1].metrics['close_reference_change'] is None


def test_future_bars_do_not_change_already_mature_one_session_observation():
    run, bundle = tiny_fixture()
    future_mutation = bundle.model_copy(update={'bars':tuple(bar.model_copy(update={
        'comparable_close':Decimal('999')}) if bar.session>SESSIONS[1] else bar for bar in bundle.bars)})
    old = evaluate_outcomes(run,bundle,at(SESSIONS[1],16),horizons=(1,))[0]
    new = evaluate_outcomes(run,future_mutation,at(SESSIONS[1],16),horizons=(1,))[0]
    assert old.model_dump(exclude={'input_hash','observation_id'}) == new.model_dump(exclude={'input_hash','observation_id'})
