"""Independent counterexamples for exact, non-authoritative TEST_FIXTURE math.

No business DB, market originals, source issuer, selector or network is used.
"""
from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction as F
import json

import pytest

from stockfish_core.yaogu import cohort_participation_trends as native

CALENDAR = tuple(date(2026, 5, day) for day in (11, 12, 13, 14, 15, 18))
BASIS = 'TEST_FIXTURE_FROZEN_SELECTOR_AND_ELIGIBLE_SCOPE_V1_CNY'


def row(iid, selected=True, amount=100, status=None):
    return native.CohortRecord(iid, status or ('WATCH' if selected else 'REJECTED'),
                               selected, F(amount) if amount is not None else None)


def snapshot(index, rows, *, basis=BASIS):
    return native.DailySnapshot(CALENDAR[index], tuple(rows), basis,
                                eligible_pool_complete=True, coverage_type='TEST_FIXTURE')


def pair(a, b, **kwargs):
    return native.compare_snapshots(a, b, calendar=CALENDAR, **kwargs)


def track(result, iid):
    return next(item for item in result['tracks'] if item['instrument_id'] == iid)


def test_daily_pool_rotation_cannot_manufacture_same_stock_amount_growth():
    # Each stock's turnover is unchanged. Swapping a large stock into selection
    # creates a tenfold headline total but no same-stock amount growth at all.
    a = snapshot(0, [row('A', True, 100), row('B', False, 1000)])
    b = snapshot(1, [row('A', False, 100), row('B', True, 1000)])
    result = pair(a, b)
    totals = result['moving_selected_totals']
    assert [item['amount_cny'] for item in totals] == [F(100), F(1000)]
    assert result['moving_selected_total_growth_computed'] is False
    fixed = result['fixed_cohort_amounts']['common_eligible']
    assert fixed['previous_amount_cny'] == fixed['current_amount_cny'] == F(1100)
    assert fixed['growth']['value'] == 0
    assert result['selected']['known_transition_ids']['newly_selected'] == ['B']
    assert result['selected']['known_transition_ids']['exited'] == ['A']
    assert result['selected']['jaccard']['value'] == 0
    assert 'NOT_NET' in fixed['interpretation']


def test_qualification_replacement_is_neither_setup_exit_nor_setup_entry():
    a = snapshot(0, [row('KEEP'), row('LEAVES_SCOPE')])
    b = snapshot(1, [row('KEEP'), row('ENTERS_SCOPE')])
    result = pair(a, b)
    assert result['eligibility_scope']['leaving_ids'] == ['LEAVES_SCOPE']
    assert result['eligibility_scope']['entering_ids'] == ['ENTERS_SCOPE']
    assert result['selected']['known_transition_ids']['exited'] == []
    assert result['selected']['known_transition_ids']['newly_selected'] == []
    assert result['selected']['jaccard']['value'] == 1
    assert result['selected']['instrument_ids'] == ['KEEP']
    assert len(result['instrument_observations']) == 3


def test_exact_nontrivial_retention_entry_exit_and_watch_are_distinct():
    a = snapshot(0, [row('A'), row('B'), row('C'), row('D', False), row('E', False)])
    b = snapshot(1, [row('A'), row('B', status='FOCUS'), row('C', False), row('D'), row('E', False)])
    result = pair(a, b)
    selected, watch = result['selected'], result['watch']
    assert selected['retention_fraction']['value'] == F(2, 3)
    assert selected['newly_selected_fraction_of_current']['value'] == F(1, 3)
    assert selected['exit_fraction_of_previous']['value'] == F(1, 3)
    assert selected['jaccard']['value'] == F(1, 2)
    # B's WATCH -> FOCUS retains selection while leaving the WATCH subset.
    assert watch['known_transition_ids']['exited'] == ['B', 'C']
    assert watch['jaccard']['value'] == F(1, 4)


def test_unknown_saved_false_is_not_observed_exit_or_zero_rate():
    a = snapshot(0, [row('A'), row('B')])
    b = snapshot(1, [row('A', False, status='UNKNOWN'), row('B')])
    result = pair(a, b)
    assert result['selected']['unknown_ids'] == ['A']
    assert result['selected']['known_transition_ids']['exited'] == []
    assert result['selected']['retention_fraction']['value'] is None
    assert result['selected']['jaccard']['status'] == 'UNKNOWN'
    assert result['selected']['known_transition_ids']['retained'] == ['B']
    assert result['moving_selected_totals'][1]['amount_cny'] is None
    assert result['instrument_observations'][0]['current_selection_state'] is None
    assert result['instrument_observations'][0]['current_research_selected'] is False
    assert result['instrument_observations'][0]['current_decision_status'] == 'UNKNOWN'


def test_empty_or_disjoint_pools_do_not_supply_zero_rates():
    empty = pair(snapshot(0, []), snapshot(1, []))
    disjoint = pair(snapshot(0, [row('A')]), snapshot(1, [row('B')]))
    for result in (empty, disjoint):
        assert result['selected']['status'] == 'UNKNOWN'
        assert result['selected']['jaccard']['value'] is None
        assert result['fixed_cohort_amounts']['common_eligible']['previous_amount_cny'] is None
    negatives = pair(snapshot(0, [row('A', False)]), snapshot(1, [row('A', False)]))
    assert negatives['selected']['status'] == 'AVAILABLE'
    assert negatives['selected']['jaccard']['status'] == 'UNKNOWN'
    assert negatives['selected']['retention_fraction']['value'] is None
    assert negatives['moving_selected_totals'][0]['amount_cny'] is None


def test_unknown_amount_is_not_zero_or_a_complete_cohort_growth():
    a = snapshot(0, [row('A', amount=10), row('B', amount=None)])
    b = snapshot(1, [row('A', amount=20), row('B', amount=30)])
    result = pair(a, b)
    fixed = result['fixed_cohort_amounts']['common_eligible']
    assert fixed['previous_amount_cny'] is None
    assert fixed['current_amount_cny'] is None
    assert fixed['growth']['value'] is None
    assert fixed['known_both_ids'] == ['A']
    assert fixed['known_both_previous_amount_cny'] == 10
    assert fixed['known_both_current_amount_cny'] == 20
    assert fixed['known_both_subtotal_is_full_cohort'] is False
    assert result['moving_selected_totals'][0]['amount_cny'] is None


def test_genuine_zero_amount_has_undefined_base_growth_and_is_not_missing():
    result = pair(snapshot(0, [row('A', amount=0)]), snapshot(1, [row('A', amount=10)]))
    fixed = result['fixed_cohort_amounts']['common_eligible']
    assert fixed['status'] == 'AVAILABLE'
    assert fixed['previous_amount_cny'] == 0
    assert fixed['growth']['value'] is None
    assert fixed['growth']['reason_codes'] == ['ZERO_BASE_AMOUNT_GROWTH_UNDEFINED']


def test_frozen_cohort_requires_same_iids_eligible_both_days():
    result = pair(snapshot(0, [row('A'), row('B')]), snapshot(1, [row('A')]),
                  fixed_cohorts={'frozen': ['A', 'B']})
    fixed = result['fixed_cohort_amounts']['frozen']
    assert fixed['status'] == 'UNKNOWN'
    assert fixed['growth']['value'] is None
    assert fixed['observations'][1]['reason_codes'] == ['FIXED_COHORT_ELIGIBILITY_BOUNDARY']


def test_duplicate_ids_and_overlapping_membership_do_not_double_count():
    with pytest.raises(ValueError, match='DUPLICATE_INSTRUMENT'):
        snapshot(0, [row('A'), row('A')])
    a, b = snapshot(0, [row('A', amount=F(1, 3)), row('B', amount=F(2, 3))]), snapshot(1, [row('A'), row('B')])
    with pytest.raises(ValueError, match='DUPLICATE_INSTRUMENT'):
        pair(a, b, fixed_cohorts={'two_overlapping_themes': ['A', 'A', 'B']})
    result = pair(a, b, fixed_cohorts={'unique_union': {'A', 'B'}})
    assert result['fixed_cohort_amounts']['unique_union']['previous_amount_cny'] == 1
    assert len(result['instrument_observations']) == 2


def test_missing_exchange_day_censors_two_observed_runs():
    a, b = snapshot(0, [row('A')]), snapshot(2, [row('A')])
    comparison = pair(a, b)
    assert comparison['comparison_kind'] == 'ENDPOINT_COMPARISON_ACROSS_MISSING_SESSIONS'
    assert comparison['intermediate_exchange_sessions'] == [CALENDAR[1]]
    for group in ('selected', 'watch'):
        assert comparison[group]['transition_observation_role'] == 'ENDPOINT_MEMBERSHIP_CHANGE_NOT_CONTINUOUS_RETENTION'
    result = track(native.observed_selected_runs([a, b], calendar=CALENDAR), 'A')
    assert [item['observed_consecutive_selected_sessions'] for item in result['observed_runs']] == [1, 1]
    assert result['missing_snapshot_sessions'] == [CALENDAR[1]]
    assert result['observed_runs'][0]['right_reason_codes'] == ['SNAPSHOT_MISSING']
    assert result['observed_runs'][1]['left_reason_codes'] == ['SNAPSHOT_MISSING']
    assert result['complete_continuous_eligible_observation_coverage'] is False


@pytest.mark.parametrize('middle,reason', [([], 'ELIGIBILITY_BOUNDARY'),
    ([row('A', False, status='UNKNOWN')], 'DECISION_UNKNOWN')])
def test_unknown_action_and_eligibility_boundaries_censor_separately(middle, reason):
    result = track(native.observed_selected_runs(
        [snapshot(0, [row('A')]), snapshot(1, middle), snapshot(2, [row('A')])], calendar=CALENDAR), 'A')
    assert [item['observed_consecutive_selected_sessions'] for item in result['observed_runs']] == [1, 1]
    assert result['observed_runs'][0]['right_reason_codes'] == [reason]
    assert result['observed_runs'][1]['left_reason_codes'] == [reason]
    assert result['observed_longest_consecutive_selected_sessions'] == 1


def test_calendar_adjacency_not_calendar_day_count_and_known_termination():
    # Friday -> Monday is one exchange-session transition, not a missing day.
    result = track(native.observed_selected_runs(
        [snapshot(3, [row('A', False)]), snapshot(4, [row('A')]), snapshot(5, [row('A')])],
        calendar=CALENDAR), 'A')
    episode = result['observed_runs'][0]
    assert episode['observed_consecutive_selected_sessions'] == 2
    assert episode['left_censored'] is False
    assert episode['right_reason_codes'] == ['OBSERVATION_BOUNDARY']
    assert episode['complete_cycle_certified'] is False
    assert pair(snapshot(4, [row('A')]), snapshot(5, [row('A')]))['intermediate_exchange_sessions'] == []
    assert pair(snapshot(4, [row('A')]), snapshot(5, [row('A')]))['selected']['transition_observation_role'] == 'ADJACENT_EXCHANGE_SESSION_MEMBERSHIP_CHANGE'
    ended = track(native.observed_selected_runs(
        [snapshot(0, [row('A', False)]), snapshot(1, [row('A')]), snapshot(2, [row('A', False)])],
        calendar=CALENDAR), 'A')['observed_runs'][0]
    assert ended['left_censored'] is ended['right_censored'] is False


def test_basis_declaration_change_blocks_trend_and_censors_continuity():
    a = snapshot(0, [row('A', amount=100)])
    b = snapshot(1, [row('A', amount=1000)], basis='TEST_FIXTURE_DIFFERENT_RULE')
    comparison = pair(a, b)
    assert comparison['selected']['jaccard']['value'] is None
    assert comparison['selected']['known_transition_ids']['retained'] == []
    assert comparison['fixed_cohort_amounts']['common_eligible']['growth']['value'] is None
    runs = track(native.observed_selected_runs([a, b], calendar=CALENDAR), 'A')['observed_runs']
    assert len(runs) == 2
    assert runs[0]['right_reason_codes'] == ['COMPARISON_BASIS_BOUNDARY']
    assert runs[1]['left_reason_codes'] == ['COMPARISON_BASIS_BOUNDARY']


def test_no_positive_observation_with_unknown_cannot_be_zero_duration():
    a, b = snapshot(0, [row('A', False, status='UNKNOWN')]), snapshot(1, [row('A', False)])
    result = track(native.observed_selected_runs([a, b], calendar=CALENDAR), 'A')
    assert result['observed_runs'] == []
    assert result['observed_longest_consecutive_selected_sessions'] is None


@pytest.mark.parametrize('amount', [1.0, float('nan'), float('inf'), Decimal('1.01'), 1, True])
def test_amount_precision_and_unit_are_not_guessed(amount):
    with pytest.raises(ValueError, match='EXACT_FRACTION'):
        native.CohortRecord('A', 'WATCH', True, amount)


@pytest.mark.parametrize('status', ['UNKNOWN', 'REJECTED'])
def test_selected_unknown_or_rejected_is_rejected(status):
    with pytest.raises(ValueError, match='SELECTED_WITH_UNKNOWN_OR_REJECTED'):
        native.CohortRecord('A', status, True, F(1))


def test_invalid_input_id_amount_bool_and_completeness_are_rejected():
    with pytest.raises(ValueError, match='NEGATIVE'):
        row('A', amount=-1)
    with pytest.raises(ValueError, match='BOOL'):
        native.CohortRecord('A', 'WATCH', 1, F(1))
    with pytest.raises(ValueError, match='INSTRUMENT_ID'):
        row(' A')
    with pytest.raises(ValueError, match='COMPLETE_ELIGIBLE'):
        native.DailySnapshot(CALENDAR[0], (row('A'),), BASIS, False)
    with pytest.raises(ValueError, match='BUSINESS_DATE'):
        native.DailySnapshot(datetime(2026, 5, 11), (row('A'),), BASIS, True)


def test_date_order_calendar_membership_and_empty_axis_are_strict():
    a, b = snapshot(0, [row('A')]), snapshot(1, [row('A')])
    for sequence in ([b, a], [a, a]):
        with pytest.raises(ValueError, match='SNAPSHOTS_NOT_STRICTLY'):
            native.observed_selected_runs(sequence, calendar=CALENDAR)
    off_calendar = native.DailySnapshot(date(2026, 5, 16), (row('A'),), BASIS, True)
    with pytest.raises(ValueError, match='NOT_IN_DECLARED'):
        native.observed_selected_runs([off_calendar], calendar=CALENDAR)
    for calendar in ([], list(reversed(CALENDAR)), [CALENDAR[0], CALENDAR[0]]):
        with pytest.raises(ValueError, match='CALENDAR'):
            native.observed_selected_runs([a, b], calendar=calendar)
    with pytest.raises(ValueError, match='NONEMPTY_TYPED_SNAPSHOTS'):
        native.observed_selected_runs([], calendar=CALENDAR)


def test_declaration_hashes_do_not_authenticate_or_mutate_saved_facts():
    records = [row('B', False, F(1, 7)), row('A', True, F(1, 3))]
    a = native.DailySnapshot(CALENDAR[0], records, BASIS, True, 'TEST_FIXTURE')
    b = snapshot(1, records)
    before = native.snapshot_content_hash(a)
    records.clear()  # constructor froze the caller-owned sequence
    result = native.run_cohort_participation_trends([a, b], calendar=CALENDAR,
                                                   fixed_cohorts={'frozen': iter(['A', 'B'])})
    assert before == native.snapshot_content_hash(a)
    assert len(result['snapshot_declarations'][0]['records']) == 2
    assert result['source_capability_restored'] is False
    assert result['source_authentication_performed'] is False
    assert result['historical_qualification_authenticated'] is False
    assert result['selector_run'] is False
    assert result['prediction_weight_or_selection_threshold_added'] is False
    assert result['default_service_integration'] is False
    assert result['certified_emotion_cycle_count'] == result['official_pit_validation_count'] == 0
    assert result['market_edge'] == 'NOT_ESTABLISHED'
    assert result['pair_comparisons'][0]['comparison_basis_is_authenticated'] is False
    encoded = native.encode_exact(result)
    assert json.loads(json.dumps(encoded)) == encoded
    assert encoded['snapshot_declarations'][0]['records'][0]['amount_cny'] == {'numerator': 1, 'denominator': 3}
    assert native.source_identity()['source_file_hashes']['docs/COHORT_PARTICIPATION_TRENDS_V1.md']
    result['snapshot_declarations'][0]['records'][0]['research_selected'] = False
    assert a.records[0].research_selected is True


def test_fixed_cohort_generator_is_identical_for_every_pair_and_permutation_is_canonical():
    rows = [row('A'), row('B')]
    a, b, c = snapshot(0, rows), snapshot(1, rows), snapshot(2, rows)
    permuted = snapshot(0, list(reversed(rows)))
    assert native.snapshot_content_hash(a) == native.snapshot_content_hash(permuted)
    result = native.run_cohort_participation_trends([a, b, c], calendar=CALENDAR,
                                                   fixed_cohorts={'frozen': iter(['B', 'A'])})
    assert [item['fixed_cohort_amounts']['frozen']['instrument_ids'] for item in result['pair_comparisons']] == [['A', 'B'], ['A', 'B']]
    with pytest.raises(ValueError, match='RESERVED'):
        pair(a, b, fixed_cohorts={'common_eligible': ['A']})
    with pytest.raises(ValueError, match='RESERVED'):
        native.run_cohort_participation_trends([a], calendar=CALENDAR,
                                               fixed_cohorts={'common_eligible': ['A']})


def test_exact_money_beyond_float_precision_and_negative_growth_are_preserved():
    # A one-unit reduction beyond IEEE754 integer precision must not disappear.
    large = 2 ** 60 + 1
    a = snapshot(0, [row('A', amount=large), row('B', amount=F(1, 3))])
    b = snapshot(1, [row('A', amount=large - 1), row('B', amount=F(1, 3))])
    amounts = pair(a, b)['fixed_cohort_amounts']['common_eligible']
    assert amounts['current_amount_cny'] - amounts['previous_amount_cny'] == -1
    assert amounts['growth']['value'] == F(-3, 3 * large + 1)
    assert type(amounts['growth']['value']) is F


@pytest.mark.parametrize('label,error', [('common_eligible', 'RESERVED'), ('', 'NONEMPTY_STRING')])
def test_single_snapshot_still_validates_fixed_cohort_labels(label, error):
    with pytest.raises(ValueError, match=error):
        native.run_cohort_participation_trends([snapshot(0, [row('A')])], calendar=CALENDAR,
                                               fixed_cohorts={label: ['A']})
