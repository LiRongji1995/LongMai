from datetime import timedelta
from decimal import Decimal

from stockfish_core.observed_data import exchange_calendar_2026
from stockfish_core.yaogu.auction import freeze_theses
from tests.r02_product.test_independent_time_boundaries import tiny_fixture


def test_known_exchange_calendar_freezes_next_session_when_price_input_ends_today():
    run, bundle = tiny_fixture()
    ending_today = bundle.model_copy(update={'sessions': (run.observation_date,)})
    known = tuple(sorted(exchange_calendar_2026().sessions))
    frozen = freeze_theses(run, ending_today, known_sessions=known, calendar_ref='TEST_OFFICIAL_2026_CALENDAR')
    next_day = next(d for d in known if d > run.observation_date)
    assert frozen.items[0].thesis_conditions[0]['expected_session'] == next_day.isoformat()
    assert frozen.items[0].thesis_conditions[0]['calendar_ref'] == 'TEST_OFFICIAL_2026_CALENDAR'
    unknown = freeze_theses(run, ending_today)
    assert unknown.items[0].thesis_conditions[0]['expected_session'] is None


def test_unavailable_receipt_revision_cannot_rewrite_frozen_price_denominator():
    run, bundle = tiny_fixture()
    first = next(b for b in bundle.bars if b.session == run.observation_date)
    injected = first.model_copy(update={'amount_cny': Decimal('999999999'),
        'source_refs': ('UNREGISTERED_FUTURE_RECEIPT',), 'ingested_at': first.ingested_at + timedelta(seconds=1)})
    original = freeze_theses(run, bundle)
    mutated = freeze_theses(run, bundle.model_copy(update={'bars': (*bundle.bars, injected)}))
    assert mutated.items[0].thesis_conditions == original.items[0].thesis_conditions
