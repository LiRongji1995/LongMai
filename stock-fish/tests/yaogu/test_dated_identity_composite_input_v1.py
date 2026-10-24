from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from stockfish_core.yaogu.contracts import DailyBar, MarketInput, SecurityRecord, SourceReceipt, UniverseSnapshot, digest
from stockfish_core.yaogu.dated_identity_composite_input_v1 import VERSION, project_dated_identity
from stockfish_core.yaogu.visibility import EvidenceResolver


DAY = date(2026, 10, 16)
BASE_DAY = date(2026, 10, 23)
DERIVED = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
CAPTURE = datetime(2026, 10, 16, 13, tzinfo=timezone.utc)
MASTER_CAPTURE = datetime(2026, 10, 15, 16, 12, tzinfo=timezone.utc)


def clocks(at):
    return {name: at for name in ('event_time', 'source_published_at', 'first_seen_at', 'ingested_at')}


def parent(rid, dataset, at, *, provider='baostock', parameters=None, url='https://baostock.com/'):
    return SourceReceipt(receipt_id=rid, provider=provider, dataset=dataset, content_hash='a' * 64,
        field_version='AKSHARE_1.18.94_R03_V1' if provider == 'akshare' else 'BAOSTOCK_00.9.30_R03_V1',
        row_count=1, pagination_complete=True, status='COMPLETE', source_url=url,
        parameters=parameters or {}, **clocks(at))


def state(iid='000001.SZ', *, board='MAIN', at=CAPTURE, refs=('master', 'market', 'basic'), **updates):
    values = dict(instrument_id=iid, exchange='SZSE', board=board, security_type='A_COMMON',
        listed_on=date(1991, 4, 3), effective_from=DAY, effective_to=DAY + timedelta(days=1),
        st=False, suspended=False, delisting_period=False, source_refs=refs,
        risk_flags=('HISTORICAL_BOARD_AND_RULE_EXCEPTION_STATUS_UNVERIFIED',), **clocks(at))
    values.update(updates)
    return SecurityRecord.model_validate(values)


def fixture():
    master = parent('master', 'master_szse', MASTER_CAPTURE, provider='akshare',
                    url='https://www.szse.cn/api/report/ShowReport')
    market = parent('market', 'market_daily', CAPTURE, parameters={'date': str(DAY)})
    basic = parent('basic', 'stock_basic_all', MASTER_CAPTURE - timedelta(days=1))
    receipts = (master, market, basic)
    identity_state = state()
    identity = MarketInput(observation_date=DAY, sessions=(DAY, BASE_DAY), bars=(),
        universe=UniverseSnapshot(snapshot_id='TEST_FIXTURE_IDENTITY', securities=(identity_state,),
            source_refs=('master',), complete=True, expected_count=1, **clocks(CAPTURE)),
        receipts=receipts, coverage_type='TEST_FIXTURE')
    old = state(board='UNKNOWN', at=CAPTURE + timedelta(days=7), delisting_period=None)
    untouched = state('000002.SZ', board='UNKNOWN', at=CAPTURE + timedelta(days=7),
                      effective_from=BASE_DAY, effective_to=BASE_DAY + timedelta(days=1))
    price = DailyBar(instrument_id=old.instrument_id, session=DAY, open=Decimal('10'),
        high=Decimal('11'), low=Decimal('9'), close=Decimal('10.5'), comparable_basis='TEST_FIXTURE',
        source_refs=('market',), **clocks(CAPTURE))
    base = MarketInput(observation_date=BASE_DAY, sessions=(DAY, BASE_DAY), bars=(price,),
        universe=UniverseSnapshot(snapshot_id='TEST_FIXTURE_BASE', securities=(old, untouched),
            source_refs=('master',), complete=True, expected_count=2, **clocks(CAPTURE + timedelta(days=7))),
        receipts=receipts, coverage_type='TEST_FIXTURE')
    return base, identity


def replace_states(bundle, states):
    return bundle.model_copy(update={'universe': bundle.universe.model_copy(update={'securities': tuple(states)})})


def test_projection_preserves_originals_prices_risk_and_other_sessions():
    base, identity = fixture()
    before = digest(base), digest(identity)
    result, manifest = project_dated_identity(base, (identity,), derived_at=DERIVED)
    assert (digest(base), digest(identity)) == before
    assert result is not base and result.bars[0] is base.bars[0]
    assert result.universe.securities[1] is base.universe.securities[1]
    updated = result.universe.securities[0]
    assert updated.board == 'MAIN' and updated.security_type == 'A_COMMON'
    assert updated.effective_from == DAY and updated.effective_to == DAY + timedelta(days=1)
    assert updated.risk_flags == base.universe.securities[0].risk_flags
    assert updated.delisting_period is None and updated.st is False and updated.suspended is False
    assert all(getattr(updated, key) == DERIVED for key in clocks(DERIVED))
    assert manifest['schema_version'] == VERSION and manifest['replacement_count'] == 1
    assert manifest['source_capability_issued'] is False
    assert manifest['historical_pit_restored'] is False
    assert manifest['field_lineage'][0]['changed_fields'] == {'board': {'old': 'UNKNOWN', 'new': 'MAIN'}}
    assert manifest['new_state_hashes'][0]['state_hash'] == digest(updated)
    assert all(receipt in result.receipts for receipt in base.receipts)
    assert project_dated_identity(base, (identity,), derived_at=DERIVED) == (result, manifest)


def test_new_derivation_is_invisible_before_actual_clock():
    base, identity = fixture()
    result, _ = project_dated_identity(base, (identity,), derived_at=DERIVED)
    row = result.universe.securities[0]
    for delta, allowed in ((timedelta(microseconds=-1), False), (timedelta(), True)):
        resolver = EvidenceResolver(result.receipts, cutoff=DERIVED + delta,
            coverage_type='PROSPECTIVE_CAPTURED', sessions=result.sessions, observation_date=BASE_DAY)
        assert resolver.is_visible(row, instrument_id=row.instrument_id, session=DAY,
                                   require_complete=True) is allowed


@pytest.mark.parametrize('side,field,value,reason', [
    ('base', 'st', True, 'DATED_RISK_STATE_CONFLICT'),
    ('base', 'suspended', True, 'DATED_RISK_STATE_CONFLICT'),
    ('base', 'listed_on', date(1992, 1, 1), 'LIFECYCLE_OR_EXCHANGE_CONFLICT'),
    ('base', 'delisted_on', date(2026, 10, 25), 'LIFECYCLE_OR_EXCHANGE_CONFLICT'),
    ('base', 'exchange', 'SSE', 'LIFECYCLE_OR_EXCHANGE_CONFLICT'),
    ('base', 'board', 'STAR', 'KNOWN_IDENTITY_CONFLICT'),
    ('base', 'security_type', 'FUND', 'KNOWN_IDENTITY_CONFLICT'),
    ('base', 'effective_to', DAY + timedelta(days=3), 'BASE_INTERVAL_MUST_BE_SINGLE_DAY'),
    ('identity', 'effective_to', DAY + timedelta(days=3), 'IDENTITY_INTERVAL_MUST_BE_CAPTURE_DAY_ONLY'),
])
def test_conflicting_identity_fields_and_intervals_rejected(side, field, value, reason):
    base, identity = fixture()
    target = base if side == 'base' else identity
    rows = list(target.universe.securities)
    rows[0] = rows[0].model_copy(update={field: value})
    target = replace_states(target, rows)
    if side == 'base':
        base = target
    else:
        identity = target
    with pytest.raises(ValueError, match=reason):
        project_dated_identity(base, (identity,), derived_at=DERIVED)


@pytest.mark.parametrize('side', ['base', 'identity'])
@pytest.mark.parametrize('same_clock_conflict', [False, True])
def test_duplicate_or_conflicting_dated_state_rejected(side, same_clock_conflict):
    base, identity = fixture()
    target = base if side == 'base' else identity
    duplicate = target.universe.securities[0]
    if same_clock_conflict:
        duplicate = duplicate.model_copy(update={'suspended': True})
    target = replace_states(target, target.universe.securities + (duplicate,))
    if side == 'base':
        base = target
    else:
        identity = target
    with pytest.raises(ValueError, match='DATED_STATE_CONFLICT'):
        project_dated_identity(base, (identity,), derived_at=DERIVED)


@pytest.mark.parametrize('mutation', ['previous_capture_day', 'partial', 'wrong_url', 'wrong_field_version', 'wrong_market_date'])
def test_unsupported_source_cannot_grant_identity(mutation):
    base, identity = fixture()
    receipts = list(identity.receipts)
    if mutation == 'previous_capture_day':
        receipts[0] = receipts[0].model_copy(update=clocks(MASTER_CAPTURE - timedelta(days=1)))
    elif mutation == 'partial':
        receipts[0] = receipts[0].model_copy(update={'status': 'PARTIAL'})
    elif mutation == 'wrong_url':
        receipts[0] = receipts[0].model_copy(update={'source_url': 'https://example.invalid/master'})
    elif mutation == 'wrong_field_version':
        receipts[0] = receipts[0].model_copy(update={'field_version': 'UNREGISTERED'})
    else:
        receipts[1] = receipts[1].model_copy(update={'parameters': {'date': '2026-10-19'}})
    # Use distinct IDs so a source-source conflict does not mask admission checks.
    remap = {r.receipt_id: r.receipt_id + '_other' for r in receipts}
    receipts = tuple(r.model_copy(update={'receipt_id': remap[r.receipt_id]}) for r in receipts)
    row = identity.universe.securities[0].model_copy(update={'source_refs': tuple(remap[r] for r in identity.universe.securities[0].source_refs)})
    identity = identity.model_copy(update={'receipts': receipts,
        'universe': identity.universe.model_copy(update={'source_refs': (remap['master'],), 'securities': (row,)})})
    with pytest.raises(ValueError, match='NO_SUPPORTED|UNIVERSE_NOT_COMPLETE'):
        project_dated_identity(base, (identity,), derived_at=DERIVED)


def test_same_receipt_id_conflict_rejected():
    base, identity = fixture()
    receipts = list(identity.receipts)
    receipts[0] = receipts[0].model_copy(update={'content_hash': 'b' * 64})
    identity = identity.model_copy(update={'receipts': tuple(receipts)})
    with pytest.raises(ValueError, match='PARENT_RECEIPT_CONFLICT'):
        project_dated_identity(base, (identity,), derived_at=DERIVED)


@pytest.mark.parametrize('bad_time', [DERIVED.replace(tzinfo=None), datetime(2026, 10, 22, tzinfo=timezone.utc)])
def test_derivation_clock_not_backdated(bad_time):
    base, identity = fixture()
    with pytest.raises(ValueError, match='AWARE_DERIVATION_TIME_REQUIRED|DERIVATION_PRECEDES_BASE_DAY'):
        project_dated_identity(base, (identity,), derived_at=bad_time)


@pytest.mark.parametrize('side', ['base', 'identity'])
def test_incomplete_universe_not_admitted(side):
    base, identity = fixture()
    target = base if side == 'base' else identity
    target = target.model_copy(update={'universe': target.universe.model_copy(update={'complete': False})})
    if side == 'base':
        base = target
    else:
        identity = target
    with pytest.raises(ValueError, match='UNIVERSE_NOT_COMPLETE_OR_VISIBLE'):
        project_dated_identity(base, (identity,), derived_at=DERIVED)


def test_missing_and_unknown_risk_are_counted_without_fabrication():
    base, identity = fixture()
    second = state('000003.SZ')
    identity = replace_states(identity, identity.universe.securities + (second,))
    base = replace_states(base, base.universe.securities + (state('000004.SZ', board='UNKNOWN', st=None),))
    identity = replace_states(identity, identity.universe.securities + (state('000004.SZ'),))
    projected, manifest = project_dated_identity(base, (identity,), derived_at=DERIVED)
    assert manifest['replacement_count'] == 1
    assert manifest['unchanged_unavailable_counts'] == {'BASE_DATED_RISK_STATE_UNKNOWN': 1, 'BASE_DATED_STATE_MISSING': 1}
    assert projected.universe.securities[-1].st is None


def test_no_source_authority_from_serialized_mapping():
    base, identity = fixture()
    with pytest.raises(ValueError, match='EXACT_MARKET_INPUT_REQUIRED'):
        project_dated_identity(base.model_dump(mode='json'), (identity,), derived_at=DERIVED)
    with pytest.raises(ValueError, match='EXACT_MARKET_INPUT_REQUIRED'):
        project_dated_identity(base, (identity.model_dump(mode='json'),), derived_at=DERIVED)


def test_no_implicit_extension_to_19_or_other_identity_day():
    base, identity = fixture()
    result, _ = project_dated_identity(base, (identity,), derived_at=DERIVED)
    assert all(not (s.board == 'MAIN' and s.effective_from <= date(2026, 10, 19)
                    and (s.effective_to is None or date(2026, 10, 19) < s.effective_to))
               for s in result.universe.securities)
    with pytest.raises(ValueError, match='DUPLICATE_IDENTITY_DAY'):
        project_dated_identity(base, (identity, identity), derived_at=DERIVED)


def test_checkpoint_abort_propagates_without_partial_result():
    base, identity = fixture()
    def stop():
        raise TimeoutError('TEST_FIXTURE_BUDGET')
    with pytest.raises(TimeoutError, match='TEST_FIXTURE_BUDGET'):
        project_dated_identity(base, (identity,), derived_at=DERIVED, checkpoint=stop)
