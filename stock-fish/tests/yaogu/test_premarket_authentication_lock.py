"""TEST_FIXTURE: shared snapshot readers restore correctly across waiters."""
from pathlib import Path
import threading
from unittest.mock import patch

import pytest

from stockfish_core.yaogu import historical_replay
from stockfish_core.yaogu import premarket_capture_cache_v1 as capture
from stockfish_core.yaogu import premarket_native_core_v1 as core
from tests.yaogu.test_premarket_native_core_v1 import FixtureBudget


@pytest.mark.parametrize('first_kind', ['core', 'cache'])
@pytest.mark.parametrize('failure_stage', [None, 'first', 'second'])
def test_waiting_authentication_restores_original_path_methods(tmp_path, first_kind, failure_stage):
    source = tmp_path / 'TEST_FIXTURE_source.json'
    source.write_bytes(b'{"TEST_FIXTURE":true}\n')
    raw, reference = FixtureBudget().read(source)
    first_entered, second_waiting = threading.Event(), threading.Event()
    original_bytes, original_text = Path.read_bytes, Path.read_text
    outcomes, failures, registries = {}, {}, []

    class ObservedLock:
        def __init__(self):
            self.lock = threading.RLock()

        def __enter__(self):
            if threading.current_thread().name == 'TEST_FIXTURE_second':
                # The waiter has reached the precise lock-acquisition boundary.
                second_waiting.set()
            self.lock.acquire()
            return self

        def __exit__(self, *_):
            self.lock.release()

    def read_inside_snapshot():
        assert source.read_bytes() == raw
        assert source.read_text(encoding='utf-8') == raw.decode('utf-8')

    def body(stage):
        read_inside_snapshot()
        if stage == 'first':
            first_entered.set()
            if not second_waiting.wait(5):
                raise AssertionError('TEST_FIXTURE_SECOND_DID_NOT_REACH_LOCK')
            read_inside_snapshot()
        if stage == failure_stage:
            raise RuntimeError('TEST_FIXTURE_EXPECTED_' + stage)
        return {'TEST_FIXTURE_verified': True}

    def verifier(*_):
        stage = 'first' if threading.current_thread().name == 'TEST_FIXTURE_first' else 'second'
        return body(stage)

    def execute(stage, kind):
        try:
            if kind == 'core':
                registry = core._BudgetRegistry(FixtureBudget())
                registries.append(registry)
                with registry.authentication():
                    registry.read(source, reference)
                    with registry.mature_verifier((reference,)):
                        outcomes[stage] = body(stage)
            else:
                cache = capture.CaptureCache(tmp_path / ('TEST_FIXTURE_cache_' + stage),
                    FixtureBudget(), config={'allow_test_fixture': True, 'offline': True})
                outcomes[stage] = cache._native_verify(str(tmp_path), {},
                    (reference,), {reference['path']: raw})
        except BaseException as error:
            failures[stage] = error

    first = threading.Thread(target=execute, args=('first', first_kind), name='TEST_FIXTURE_first')
    second = threading.Thread(target=execute, args=('second', 'cache'), name='TEST_FIXTURE_second')
    try:
        with patch.object(capture, '_VERIFY_LOCK', ObservedLock()), \
             patch.object(historical_replay, 'verify_capture', verifier):
            first.start()
            assert first_entered.wait(5), 'TEST_FIXTURE_FIRST_DID_NOT_ENTER_SNAPSHOT'
            second.start()
            first.join(6)
            second.join(6)
            assert not first.is_alive() and not second.is_alive(), 'TEST_FIXTURE_AUTHENTICATION_DEADLOCK'
        assert Path.read_bytes is original_bytes
        assert Path.read_text is original_text
        assert set(failures) == ({failure_stage} if failure_stage else set())
        if failure_stage:
            assert isinstance(failures[failure_stage], RuntimeError)
            assert str(failures[failure_stage]) == 'TEST_FIXTURE_EXPECTED_' + failure_stage
        assert set(outcomes) == {'first', 'second'} - set(failures)
        assert all(registry._capture_bytes is None and registry._capture_owner is None for registry in registries)
        assert source.read_bytes() == raw
    finally:
        # A regression must fail this test without poisoning subsequent tests.
        second_waiting.set()
        if first.ident is not None:
            first.join(6)
        if second.ident is not None:
            second.join(6)
        Path.read_bytes, Path.read_text = original_bytes, original_text
