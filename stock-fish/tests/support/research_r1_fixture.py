"""Explicit synthetic exchange calendar and execution evidence; never market data."""
from datetime import datetime, timedelta
from tests.r05_product.test_stage_aware_momentum_v1 import _with_high_level_repair_target


def clocks(day, hour='08:00:00'):
    stamp = day+'T'+hour+'+08:00'
    return dict(source_published_at=stamp, first_seen_at=stamp, ingested_at=stamp, source_refs=['TEST_FIXTURE'])


def fixture_export():
    bundle, cutoff = _with_high_level_repair_target()
    day = str(bundle.observation_date)
    # Deliberately irregular synthetic sessions (not a real exchange calendar).
    offsets = [0,1,4,5,6,7,10,11,12]
    sessions = [(bundle.observation_date+timedelta(days=d)).isoformat() for d in offsets]
    ids = [s.instrument_id for s in bundle.universe.securities]
    rule = dict(verified=True, effective_from=sessions[0], effective_to=sessions[-1],
                lot_size=100, tick_size='.01', settlement='T_PLUS_1', **clocks(day))
    cost = dict(rule, commission_rate='.0003', minimum_commission='5', transfer_rate='.00001',
                sell_tax_rate='.0005', commission_includes_transfer_or_tax=False, scenario='TEST_FIXTURE_HYPOTHETICAL')
    tape = dict(coverage_type='TEST_FIXTURE', sessions=sessions, as_of=sessions[-1]+'T16:00:00+08:00',
                rules={}, orders={}, bars={}, valuations={}, cost_scenario=cost)
    for index, session in enumerate(sessions):
        tape['rules'][session] = {i: dict(rule) for i in ids}
        tape['orders'][session] = {i: dict(limit_price='11.00', **clocks(session)) for i in ids}
        tape['bars'][session] = {i: dict(open='10.00', close='10.05', limit_up='11.00', limit_down='9.00',
            suspended=False, limit_verified=True, basis='TEST_FIXTURE_NO_ACTION', corporate_actions_complete=True,
            no_corporate_action_since_signal=True, **clocks(session, '15:00:00')) for i in ids}
        if index:
            tape['valuations'][session] = {i: dict(price='10.05', session=sessions[index-1],
                basis='TEST_FIXTURE_NO_ACTION', **clocks(sessions[index-1], '15:00:00')) for i in ids}
    return dict(input=bundle.model_dump(mode='json'), cutoff=cutoff.isoformat(),
        publication=dict(published_at=day+'T20:30:00+08:00', evidence_available_at=day+'T20:00:00+08:00'), execution_tape=tape)


if __name__ == '__main__':
    import json, sys
    from pathlib import Path
    with Path(sys.argv[1]).open('x') as f:
        json.dump(fixture_export(), f)
