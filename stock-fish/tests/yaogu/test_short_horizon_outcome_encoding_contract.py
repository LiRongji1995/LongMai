"""TEST_FIXTURE equivalence of outcome hash encoders, without market or DB reuse.

The reference evaluation retains the calculation body and replaces only its
three hash calls with the original history_artifacts encoders. Compare every
product field and row under one implementation identity, not just a summary.
"""
from copy import deepcopy
from datetime import datetime, time, timedelta
import hashlib
from pathlib import Path
from types import FunctionType

import pytest

from stockfish_core.storage import IntegrityError, SQLiteStore
from stockfish_core.yaogu import history_artifacts as history
from stockfish_core.yaogu import short_horizon_outcomes_v1 as outcome
from stockfish_core.yaogu.contracts import MarketInput
from stockfish_core.yaogu.strategy_experiments import run_short_horizon_strategy
from tests.support.short_horizon_strength_fixture import START, TZ, payload, strength_bundle
from tests.yaogu.test_short_horizon_outcomes_v1 import future_input


def _old_json(value, *, checkpoint=None):
    return history.digest_json_stream(value)


def _old_input(value, *, checkpoint=None):
    return history.digest_market_input(value)


def _reference_evaluate(*args, **kwargs):
    namespace = dict(outcome.evaluate.__globals__, digest_json_stream=_old_json,
                     _outcome_input_hash=_old_input)
    reference = FunctionType(outcome.evaluate.__code__, namespace,
                             outcome.evaluate.__name__, outcome.evaluate.__defaults__)
    reference.__kwdefaults__ = outcome.evaluate.__kwdefaults__
    return reference(*args, **kwargs)


@pytest.fixture
def evidence():
    original, cutoff = strength_bundle()
    run = run_short_horizon_strategy(original, cutoff=cutoff)
    run['content_hash'] = history.digest_json_stream(run)
    bundle, evaluated = future_input(original)
    return run, original, bundle, evaluated


@pytest.mark.parametrize('strategy_id', ['SHORT_HORIZON_STRENGTH_R1', 'SHORT_HORIZON_STRENGTH_R2'])
@pytest.mark.parametrize('case', ['mature', 'pending', 'missing_price', 'late_parent',
                                  'unknown_security', 'basis', 'missing_session', 'reconstructed'])
def test_complete_products_match_old_encoders_for_every_row(evidence, strategy_id, case):
    run, original, bundle, evaluated = evidence
    if strategy_id != run['strategy_id']:
        run = run_short_horizon_strategy(original,
            cutoff=datetime.fromisoformat(run['decision_cutoff']), strategy_id=strategy_id)
    # Exercise Unicode, numeric spelling and nested arbitrary frozen metadata.
    run['TEST_FIXTURE_encoding'] = {'中文': ['é', '𠮷', '\u2028', -0.0, 1e-30, None, True]}
    run['content_hash'] = history.digest_json_stream({k: v for k, v in run.items() if k != 'content_hash'})
    value = payload(bundle)
    day = original.observation_date + timedelta(days=4)
    bar = next(b for b in value['bars'] if b['instrument_id'] == START and b['session'] == day)
    if case == 'pending':
        evaluated = datetime.combine(original.observation_date + timedelta(days=3), time(14, 59), TZ)
    elif case == 'missing_price':
        value['bars'].remove(bar)
    elif case == 'late_parent':
        receipt = next(r for r in value['receipts'] if r['receipt_id'] == 'TEST_FIXTURE_FUTURE_' + str(day))
        receipt.update(first_seen_at=evaluated + timedelta(days=1), ingested_at=evaluated + timedelta(days=1))
    elif case == 'unknown_security':
        value['universe']['securities'][0]['suspended'] = None
    elif case == 'basis':
        bar['comparable_basis'] = 'TEST_FIXTURE_OTHER_BASIS'
    elif case == 'missing_session':
        value['sessions'].remove(day)
    elif case == 'reconstructed':
        value['coverage_type'] = 'RECONSTRUCTED_HISTORICAL'
        evaluated = datetime(2026, 10, 15, 20, tzinfo=TZ)
        bar.update(first_seen_at=evaluated + timedelta(days=1), ingested_at=evaluated + timedelta(days=1))
    bundle = MarketInput.model_validate(value)
    frozen_run = deepcopy(run)
    frozen_input_hash = history.digest_market_input(bundle)
    expected = _reference_evaluate(run, bundle, evaluated)
    actual = outcome.evaluate(run, bundle, evaluated)
    assert actual == expected
    assert len(actual['outcomes']['items']) == 4 * len(outcome.HORIZONS)
    assert run == frozen_run
    assert history.digest_market_input(bundle) == frozen_input_hash
    assert actual['outcomes']['outcome_input_id'] == 'market_' + frozen_input_hash
    if case == 'pending':
        assert set(actual['outcomes']['counts']) == {'PENDING'}
    elif case not in {'mature'}:
        assert actual['outcomes']['counts'].get('UNKNOWN', 0) > 0


@pytest.mark.parametrize('fault', ['frozen_hash', 'duplicate_instrument', 'outcome_coverage',
                                   'signal_coverage', 'before_signal', 'horizons'])
def test_existing_invalid_evaluations_remain_rejected(evidence, fault):
    run, _, bundle, evaluated = evidence
    run = deepcopy(run)
    kwargs = {}
    if fault == 'frozen_hash':
        run['results'][run['strategy_id']]['items'][0]['research_selected'] = 'TEST_FIXTURE_CHANGED'
    elif fault == 'duplicate_instrument':
        items = run['results'][run['strategy_id']]['items']
        items.append(deepcopy(items[0]))
    elif fault == 'outcome_coverage':
        bundle = bundle.model_copy(update={'coverage_type': 'TEST_FIXTURE_UNVERIFIED'})
    elif fault == 'signal_coverage':
        run['coverage_type'] = 'TEST_FIXTURE_UNVERIFIED'
    elif fault == 'before_signal':
        evaluated = datetime.fromisoformat(run['decision_cutoff']) - timedelta(seconds=1)
    elif fault == 'horizons':
        kwargs['horizons'] = [1, 4]
    if fault != 'frozen_hash':
        run['content_hash'] = history.digest_json_stream({k: v for k, v in run.items() if k != 'content_hash'})
    with pytest.raises(ValueError) as previous:
        _reference_evaluate(run, bundle, evaluated, **kwargs)
    with pytest.raises(type(previous.value)) as current:
        outcome.evaluate(run, bundle, evaluated, **kwargs)
    assert str(current.value) == str(previous.value)


@pytest.mark.parametrize('number', [float('nan'), float('inf'), float('-inf')])
def test_nonfinite_plain_json_is_still_rejected(evidence, number):
    run, _, bundle, evaluated = evidence
    run = deepcopy(run)
    run['TEST_FIXTURE_invalid_number'] = number
    for evaluate in (_reference_evaluate, outcome.evaluate):
        with pytest.raises(ValueError):
            evaluate(run, bundle, evaluated)


@pytest.mark.parametrize('number', [float('nan'), float('inf'), float('-inf')])
def test_nonfinite_typed_input_hash_is_still_rejected(evidence, number):
    _, _, bundle, _ = evidence
    bundle = bundle.model_copy(deep=True)
    object.__setattr__(bundle.bars[0], 'turnover_fraction', number)
    with pytest.raises(ValueError):
        history.digest_market_input(bundle)
    with pytest.raises(ValueError):
        outcome._outcome_input_hash(bundle, checkpoint=lambda: None)


@pytest.mark.parametrize('phase', ['frozen', 'input', 'revision'])
def test_cancellation_reaches_each_complete_hash(evidence, monkeypatch, phase):
    run, _, bundle, evaluated = evidence
    active = {'phase': None}
    original_json, original_input = outcome.digest_json_stream, outcome.digest_market_input

    class Cancelled(Exception):
        pass

    def checkpoint():
        if active['phase'] == phase:
            raise Cancelled(phase)

    def json_hash(value, *, checkpoint):
        active['phase'] = 'revision' if value.get('schema_version') == outcome.VERSION else 'frozen'
        try:
            return original_json(value, checkpoint=checkpoint)
        finally:
            active['phase'] = None

    def input_hash(value, *, checkpoint):
        active['phase'] = 'input'
        try:
            return original_input(value, checkpoint=checkpoint)
        finally:
            active['phase'] = None

    monkeypatch.setattr(outcome, 'digest_json_stream', json_hash)
    monkeypatch.setattr(outcome, 'digest_market_input', input_hash)
    with pytest.raises(Cancelled, match=phase):
        outcome.evaluate(run, bundle, evaluated, checkpoint=checkpoint)


def test_bridge_helper_source_is_part_of_revision_identity(evidence, monkeypatch):
    run, _, bundle, evaluated = evidence
    before = outcome.evaluate(run, bundle, evaluated)
    helper = Path(outcome.__file__).with_name('short_horizon_archive_bridge_v1.py')
    original_read = Path.read_bytes
    raw = original_read(helper)
    assert before['outcomes']['dependency_source_hashes'][helper.name] == hashlib.sha256(raw).hexdigest()

    def changed_read(path):
        data = original_read(path)
        return data + b'\n# TEST_FIXTURE implementation identity only\n' if path == helper else data

    monkeypatch.setattr(Path, 'read_bytes', changed_read)
    after = outcome.evaluate(run, bundle, evaluated)
    for kind in before:
        assert before[kind]['revision_id'] != after[kind]['revision_id']
        left, right = deepcopy(before[kind]), deepcopy(after[kind])
        for product in (left, right):
            product.pop('revision_id')
            product['dependency_source_hashes'].pop(helper.name)
        assert left == right


def test_history_artifact_input_keeps_reference_path_and_all_products(evidence, tmp_path, monkeypatch):
    run, _, bundle, evaluated = evidence
    store = SQLiteStore(tmp_path / 'TEST_FIXTURE_outcome_encoding.sqlite3')
    compact = history.compact_input(bundle, store, recent_sessions=6)
    assert compact.history_artifact_ref and compact._history_store is not None

    def unexpected_inline_hash(*args, **kwargs):
        raise AssertionError('TEST_FIXTURE history input must retain reference hashing')

    monkeypatch.setattr(outcome, 'digest_market_input', unexpected_inline_hash)
    expected = _reference_evaluate(run, compact, evaluated)
    actual = outcome.evaluate(run, compact, evaluated)
    assert actual == expected
    assert set(actual['outcomes']['counts']) == {'MATURE'}
    unbound = MarketInput.model_validate(compact.model_dump(mode='python'))
    assert unbound._history_store is None
    for evaluate in (_reference_evaluate, outcome.evaluate):
        with pytest.raises(IntegrityError, match='HISTORY_ARTIFACT_STORE_NOT_BOUND'):
            evaluate(run, unbound, evaluated)
