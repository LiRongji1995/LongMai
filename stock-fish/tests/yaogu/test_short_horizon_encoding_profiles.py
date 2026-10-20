"""Explicit TEST_FIXTURE encoding policies; no real inputs, services, or network."""
from decimal import Decimal
import hashlib
import inspect
import json
import math

from pydantic import TypeAdapter
import pytest

from stockfish_core.storage import canonical_json
from stockfish_core.yaogu import short_horizon_archive_bridge_v1 as bridge
from stockfish_core.yaogu.history_artifacts import digest_market_input as reference
from tests.yaogu import test_short_horizon_archive_bridge_v1 as original
from tests.yaogu.test_short_horizon_archive_bridge_v1 import archive, service


FAST = bridge.CLOSURE_ENCODING_PROFILE


@pytest.mark.parametrize('count', [0, 1, 1023, 1024, 1025, 2049])
def test_explicit_profile_encodes_all_rows_and_fields_identically(count, monkeypatch):
    bundle = original._typed_adapter_fixture(count)
    canonical = canonical_json(bundle.model_dump(mode='json')).encode('utf-8')
    expected = reference(bundle)
    assert hashlib.sha256(canonical).hexdigest() == expected
    chunks = list(bridge._typed_canonical_chunks(bundle, encoding_profile=FAST))
    assert b''.join(chunks) == canonical and all(len(chunk) <= 1024**2 for chunk in chunks)
    calls, dump = [], TypeAdapter.dump_python
    def observed(adapter, values, *args, **kwargs):
        calls.append(len(values))
        assert 1 <= len(values) <= 1024 and kwargs.get('mode') == 'json'
        return dump(adapter, values, *args, **kwargs)
    monkeypatch.setattr(TypeAdapter, 'dump_python', observed)
    assert bridge.digest_market_input(bundle, encoding_profile=FAST) == expected
    assert calls == ([1024] * (count // 1024) + ([count % 1024] if count % 1024 else [])) * 2


class StringSubclass(str):
    pass


@pytest.mark.parametrize('profile', [None, True, False, 128, 1024, 1.0, '',
    'TYPED_ROWS_512_V1', 'TYPED_ROWS_2048_V1', [], {}, StringSubclass(FAST)])
def test_invalid_profiles_fail_before_callbacks_or_input_access(profile):
    def forbidden():
        pytest.fail('TEST_FIXTURE_INVALID_PROFILE_REACHED_CALLBACK')
    for call in (
        lambda: bridge.validate_encoding_profile(profile),
        lambda: bridge.ArchiveVerificationSession(encoding_profile=profile, checkpoint=forbidden),
        lambda: bridge.digest_market_input(None, encoding_profile=profile, checkpoint=forbidden),
        lambda: list(bridge._typed_canonical_chunks(None, encoding_profile=profile, checkpoint=forbidden)),
    ):
        with pytest.raises(ValueError, match='R2_ARCHIVE_BRIDGE_ENCODING_PROFILE_INVALID'):
            call()


def test_default_api_and_audit_remain_128_and_fast_profile_does_not_expand_budgets():
    for function in (bridge.digest_market_input, bridge._typed_canonical_chunks,
                     bridge.ArchiveVerificationSession):
        assert inspect.signature(function).parameters['encoding_profile'].default == bridge.DEFAULT_ENCODING_PROFILE
    default = bridge.ArchiveVerificationSession()
    explicit = bridge.ArchiveVerificationSession(encoding_profile=FAST)
    old, new = default.audit(), explicit.audit()
    assert 'encoding_profile' not in old and 'batch_rows' not in old
    assert new['encoding_profile'] == FAST and new['batch_rows'] == 1024
    assert set(new) == set(old) | {'encoding_profile', 'batch_rows'}
    assert old['limits'] == new['limits'] == dict(total_read_bytes=8 * 1024**3,
        unique_bytes=4 * 1024**3, files=4096, seconds=300, rss_bytes=2 * 1024**3)
    assert bridge.validate_encoding_profile(bridge.DEFAULT_ENCODING_PROFILE) == bridge.DEFAULT_ENCODING_PROFILE


@pytest.mark.parametrize('budget', ['rss', 'time'])
def test_explicit_profile_keeps_resource_checks(budget, monkeypatch):
    session = bridge.ArchiveVerificationSession(encoding_profile=FAST)
    if budget == 'rss':
        monkeypatch.setattr(bridge, '_rss', lambda: 2 * 1024**3 + 1)
        reason = 'RSS_BUDGET_EXCEEDED'
    else:
        session.started -= 301
        reason = 'TIME_BUDGET_EXCEEDED'
    with pytest.raises(ValueError, match=reason):
        session.check()


def test_explicit_profile_bounds_utf8_copy_even_when_one_row_is_large():
    bundle = original._typed_adapter_fixture(1)
    security = bundle.universe.securities[0].model_copy(update={'name': 'TEST_FIXTURE_' + '😀' * 300000})
    bundle = bundle.model_copy(update={'universe': bundle.universe.model_copy(update={'securities': (security,)})})
    chunks = list(bridge._typed_canonical_chunks(bundle, encoding_profile=FAST))
    assert max(map(len, chunks)) <= 1024**2
    assert b''.join(chunks) == canonical_json(bundle.model_dump(mode='json')).encode('utf-8')
    assert bridge.digest_market_input(bundle, encoding_profile=FAST) == reference(bundle)


@pytest.mark.parametrize('value', [-0.0, 5e-324, math.nextafter(1.0, 2.0), 1e308,
                                 float('nan'), float('inf'), -float('inf')])
def test_explicit_profile_preserves_float_values_and_reference_rejections(value):
    bundle = original._typed_adapter_fixture(1)
    bundle = bundle.model_copy(update={'bars': (bundle.bars[0].model_copy(update={
        'turnover_fraction': value, 'close': Decimal('1234567890123.000000000000001')}),)})
    if math.isfinite(value):
        assert bridge.digest_market_input(bundle, encoding_profile=FAST) == reference(bundle)
    else:
        with pytest.raises(ValueError):
            reference(bundle)
        with pytest.raises(ValueError):
            bridge.digest_market_input(bundle, encoding_profile=FAST)


@pytest.mark.parametrize('field', ['bars', 'securities'])
@pytest.mark.parametrize('kind', ['generator', 'repeatable_iterator', 'row_subclass'])
def test_explicit_profile_keeps_whole_reference_side_effect_semantics(field, kind):
    expected, original_order = original._typed_guard_side_effect_fixture(field, kind)
    changed_hash = reference(original_order)
    assert changed_hash != expected
    _, changed = original._typed_guard_side_effect_fixture(field, kind)
    assert bridge.digest_market_input(changed, encoding_profile=FAST) == changed_hash
    _, borrowed = original._typed_guard_side_effect_fixture(field, kind)
    session = bridge.ArchiveVerificationSession(encoding_profile=FAST)
    # Explicit malformed TEST_FIXTURE seam; this does not mint provenance.
    session._borrowed_values[id(borrowed)] = (borrowed, expected, True)
    with pytest.raises(ValueError, match='BORROWED_VERIFIED_CONTENT_CHANGED'):
        session.terminal()
    assert session.terminal_count == 0


@pytest.mark.parametrize('stage', ['before_batch', 'after_dump', 'after_json'])
def test_explicit_profile_cancellation_keeps_batch_boundary_checkpoints(stage, monkeypatch):
    bundle = original._typed_adapter_fixture(1025)
    batches, state = [], {'json': False}
    dump, dumps = TypeAdapter.dump_python, json.dumps
    failure = KeyboardInterrupt('TEST_FIXTURE_EXPLICIT_PROFILE_CANCELLED')
    def observed_dump(adapter, values, *args, **kwargs):
        result = dump(adapter, values, *args, **kwargs)
        batches.append(len(values))
        return result
    def observed_json(value, *args, **kwargs):
        result = dumps(value, *args, **kwargs)
        state['json'] = True
        return result
    def checkpoint():
        if stage == 'before_batch' or stage == 'after_dump' and batches or stage == 'after_json' and state['json']:
            raise failure
    monkeypatch.setattr(TypeAdapter, 'dump_python', observed_dump)
    monkeypatch.setattr(bridge.json, 'dumps', observed_json)
    with pytest.raises(KeyboardInterrupt) as caught:
        bridge.digest_market_input(bundle, encoding_profile=FAST, checkpoint=checkpoint)
    assert caught.value is failure
    assert batches == ([] if stage == 'before_batch' else [1024])
    assert state['json'] is (stage == 'after_json')


@pytest.mark.parametrize('change', ['nested', 'late_last_bar', 'private_owner', 'history_ref'])
def test_explicit_profile_late_borrowed_change_keeps_atomic_rollback(service, change):
    bundle = original._typed_adapter_fixture(1025)
    expected = reference(bundle)
    session = bridge.ArchiveVerificationSession(encoding_profile=FAST)
    session._borrowed_values[id(bundle)] = (bundle, expected, True)
    assert session.terminal()['borrowed_values'] == 1
    session.release_cached_archives()
    if change == 'nested':
        bundle.funds[0]['TEST_FIXTURE']['price'] = 'changed'
    elif change == 'late_last_bar':
        object.__setattr__(bundle.bars[-1], 'close', Decimal('11.0010'))
    elif change == 'private_owner':
        bundle._history_store = object()
    else:
        object.__setattr__(bundle, 'history_artifact_ref', 'TEST_FIXTURE_LATE_REF')
    reason = ('BORROWED_VERIFIED_CONTENT_CHANGED' if change in ('nested', 'late_last_bar')
              else 'INLINE_TYPED_INPUT_WITHOUT_HISTORY_OWNER_REQUIRED')
    with pytest.raises(ValueError, match=reason):
        with service.store.transaction() as con:
            service.store._put_document(con, 'TEST_FIXTURE_products', 'fast-late', {'TEST_FIXTURE': True})
            session.terminal()
    assert service.store.get_document('TEST_FIXTURE_products', 'fast-late') is None


def test_explicit_profile_terminal_cancellation_keeps_atomic_rollback(service, monkeypatch):
    bundle = original._typed_adapter_fixture(1025)
    expected = reference(bundle)
    batches, dump = [], TypeAdapter.dump_python
    failure = KeyboardInterrupt('TEST_FIXTURE_EXPLICIT_TERMINAL_CANCELLED')
    def observed(adapter, values, *args, **kwargs):
        result = dump(adapter, values, *args, **kwargs)
        batches.append(len(values))
        return result
    def checkpoint():
        if batches:
            raise failure
    session = bridge.ArchiveVerificationSession(encoding_profile=FAST, checkpoint=checkpoint)
    session._borrowed_values[id(bundle)] = (bundle, expected, True)
    monkeypatch.setattr(TypeAdapter, 'dump_python', observed)
    with pytest.raises(KeyboardInterrupt) as caught:
        with service.store.transaction() as con:
            service.store._put_document(con, 'TEST_FIXTURE_products', 'fast-cancel', {'TEST_FIXTURE': True})
            session.terminal()
    assert caught.value is failure and batches == [1024]
    assert service.store.get_document('TEST_FIXTURE_products', 'fast-cancel') is None


def test_explicit_profile_authentication_load_and_terminal_share_policy(archive, service, monkeypatch):
    calls, digest = [], bridge.digest_market_input
    def observed(bundle, *, checkpoint=None, encoding_profile=bridge.DEFAULT_ENCODING_PROFILE):
        calls.append(encoding_profile)
        return digest(bundle, checkpoint=checkpoint, encoding_profile=encoding_profile)
    monkeypatch.setattr(bridge, 'digest_market_input', observed)
    session = bridge.ArchiveVerificationSession(encoding_profile=FAST)
    cap = original.authenticate(archive, session=session)
    assert calls == [FAST]
    credential = bridge.commit_bridge(service, cap)
    view = bridge.authenticated_bridge_view(service, input_id=credential['input_id'], session=session)
    assert set(view) == {'credential', 'run_metadata', 'input_metadata'}
    before_load = len(calls)
    bundle = bridge.load_verified_bridge_input(service, credential['input_id'], session=session)
    assert len(calls) == before_load + 1 and calls[-1] == FAST
    assert bundle._history_store is bundle.history_artifact_ref is bundle.history_artifact_hash is None
    before_terminal = len(calls)
    audit = session.terminal()
    assert len(calls) == before_terminal + 1 and set(calls) == {FAST}
    assert audit['encoding_profile'] == FAST and audit['batch_rows'] == 1024
    assert audit['limits']['seconds'] == 300 and audit['limits']['rss_bytes'] == 2 * 1024**3
