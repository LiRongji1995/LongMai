"""R02 regression contracts. Every value and database here is TEST_FIXTURE."""
import sqlite3
import threading
import time
from datetime import timedelta

import pytest

from stockfish_core.api import create_app
from stockfish_core.common import UnavailableError
from stockfish_core.service import ApplicationService, RecommendationRequest
from stockfish_core.storage import SQLiteStore, _SCHEMA
from stockfish_core.recommendation import Candidate, RecommendationEngine
from stockfish_core.risk import Holding, PortfolioSnapshot, RiskEngine
from stockfish_core.backtest import CorporateAction
from tests.quant.test_quant_boundary import training_request
from tests.recommendation.test_canonical_policy import context, risk, NOW
from tests.backtest.test_cash_nav import setup_case, ID, DAYS, ts


def wait_terminal(core, job_id):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        job = core.jobs.get(job_id)
        if job['state'] in {'SUCCEEDED', 'FAILED', 'CANCELLED', 'INTERRUPTED'}:
            return job
        time.sleep(.01)
    raise AssertionError('TEST_FIXTURE worker did not terminate within bounded wait')


def test_e01_cancelled_training_hidden_default_explicit_api_restart(tmp_path, monkeypatch):
    core = ApplicationService(tmp_path / 'core', store=SQLiteStore(tmp_path / 'db.sqlite3'))
    client = create_app(core).test_client()
    prepared, resume = threading.Event(), threading.Event()
    original_train, result_box = core.quant.train, []
    def paused_train(value):
        result = original_train(value)
        result_box.append(result)
        prepared.set()
        assert resume.wait(10)
        return result
    monkeypatch.setattr(core.quant, 'train', paused_train)
    try:
        response = client.post('/api/qlib/train', json=training_request().model_dump(mode='json'))
        assert response.status_code == 202
        job_id = response.get_json()['task_id']
        assert prepared.wait(10)
        core.jobs.cancel(job_id)
        resume.set()
        assert wait_terminal(core, job_id)['state'] == 'CANCELLED'
        assert core.store.list_documents('training_runs') == []
        assert client.get('/api/qlib/models').get_json() == []
        with pytest.raises(UnavailableError):
            core._model(RecommendationRequest())
        with pytest.raises(UnavailableError):
            core.registry.get('cpu', result_box[0].model.model_version)
    finally:
        resume.set()
        core.close()
    reopened = ApplicationService(tmp_path / 'core', store=SQLiteStore(tmp_path / 'db.sqlite3'))
    try:
        assert reopened.registry.list() == ()
        with pytest.raises(UnavailableError):
            reopened.registry.get('cpu', result_box[0].model.model_version)
    finally:
        reopened.close()


def test_e03_future_hard_veto_pair_cannot_change_other_candidate():
    candidates = tuple(Candidate(instrument_id=f'60000{i}.SH', raw_score=2-i, quant_rank=i+1) for i in range(2))
    engine, portfolio = RecommendationEngine(), PortfolioSnapshot(as_of=NOW, cash=100000)
    outputs = []
    for veto in (False, True):
        inputs = {c.instrument_id: risk(c.instrument_id) for c in candidates}
        inputs[candidates[0].instrument_id] = inputs[candidates[0].instrument_id].model_copy(update={
            'data_available_at': NOW + timedelta(days=1), 'hard_veto': veto,
            'veto_reasons': ('FUTURE_NOTICE',) if veto else ()})
        outputs.append(engine.evaluate(context(), candidates, inputs, portfolio))
    assert [o.status for o in outputs] == ['UNAVAILABLE', 'UNAVAILABLE']
    assert [o.target_weights for o in outputs] == [None, None]
    assert [[(r.instrument_id, r.reason_codes) for r in o.rejected] for o in outputs][0] == [
        [(r.instrument_id, r.reason_codes) for r in o.rejected] for o in outputs][1]
    assert outputs[0].input_hash == outputs[1].input_hash


def test_e04_split_fractional_lots_conserve_every_share():
    holding = Holding(instrument_id=ID, shares=110, sellable_shares=55, mark_price=10,
                      industry='bank', marked_at=ts(DAYS[0], 8))
    action = CorporateAction(instrument_id=ID, ex_date=DAYS[0], pay_date=DAYS[0],
        available_at=ts(DAYS[0], 8), split_ratio=1.1, source_id='TEST_FIXTURE')
    initial = PortfolioSnapshot(as_of=ts(DAYS[0], 8), cash=0, holdings=(holding,))
    engine, request, bars = setup_case(initial=initial, actions=(action,))
    result = engine.run(request, bars, lambda *_: {})
    assert result.daily[0].positions[ID] == 121
    assert result.orders[0].filled_quantity == 121
    assert result.final_portfolio.holdings == ()


@pytest.mark.parametrize('tamper', ['payload_with_restored_trigger', 'same_named_weak_trigger'])
def test_e05_health_checks_content_and_trigger_definition(tmp_path, tamper):
    store = SQLiteStore(tmp_path / 'db.sqlite3')
    store.put_document('TEST_FIXTURE', 'one', {'value': 1})
    with sqlite3.connect(store.path) as connection:
        connection.execute('DROP TRIGGER documents_no_update')
        if tamper == 'payload_with_restored_trigger':
            connection.execute("UPDATE documents SET payload='{}'")
            statement = next(s for s in _SCHEMA.splitlines() if s.startswith('CREATE TRIGGER documents_no_update'))
        else:
            statement = 'CREATE TRIGGER documents_no_update BEFORE UPDATE ON documents BEGIN SELECT 1; END;'
        connection.execute(statement)
    health = store.health()
    assert health['status'] == 'unavailable'
    assert health.get('checked_at')


def test_e03_untrusted_source_is_checked_before_veto():
    original = risk('600000.SH').model_copy(update={'provider': 'unknown'})
    portfolio = PortfolioSnapshot(as_of=NOW, cash=100000)
    a = RiskEngine().evaluate(original, portfolio)
    b = RiskEngine().evaluate(original.model_copy(update={'hard_veto': True}), portfolio)
    assert a == b
