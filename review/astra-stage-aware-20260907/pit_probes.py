"""Read-only production-code audit. All inputs are TEST_FIXTURE; no live DB/network."""
from pathlib import Path
import sys, json
from copy import deepcopy
from datetime import datetime, timedelta, time
from decimal import Decimal

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'stock-fish'))
from tests.r05_product.test_stage_aware_momentum_v1 import _with_high_level_divergence_target, _payload
from tests.r05_product.test_theme_capital_trend_v1 import trend_bundle, _fund
from stockfish_core.yaogu import stage_aware_momentum as sam
from stockfish_core.yaogu.strategy_experiments import run_strategy_experiments
from stockfish_core.yaogu.strategy_tracking import evaluate_strategy_experiment_outcomes, build_strategy_feedback_report
from stockfish_core.yaogu.contracts import MarketInput, digest
from stockfish_core.yaogu.visibility import EvidenceResolver
from stockfish_core.storage import SQLiteStore, ImmutableConflict

results = {'evidence_class': 'SYNTHETIC_TEST_FIXTURE_ENGINEERING_ONLY', 'findings': {}}
bundle, cutoff = _with_high_level_divergence_target()

def row(b):
    r = sam.run_stage_aware_momentum(b, cutoff=cutoff)
    return next(x for x in r['items'] if x['instrument_id'] == 'TEST000')

def compact(r):
    return {k: r[k] for k in ('status', 'condition_statuses', 'funds_grade')}

base = row(bundle)
results['findings']['baseline'] = compact(base)

# Fixture deliberately has no fund receipts, unlike the evidence gate used by prices/themes.
missing = deepcopy(bundle.funds[0])
proof = EvidenceResolver(bundle.receipts, cutoff=cutoff, coverage_type='TEST_FIXTURE', sessions=bundle.sessions)
results['findings']['funds_no_parent_authority'] = {
    **compact(base), 'fund_source_refs': [x.get('source_refs') for x in bundle.funds],
    'resolver_would_reject': proof.resolve(missing, instrument_id=missing['instrument_id']).reason_codes,
}

# A future, invalid, wrong-security parent is ignored by SAM's primitive clock test.
p = _payload(bundle)
future = p['receipts'][0].copy()
future.update(receipt_id='TEST_FUTURE_FUND', status='INVALID', instrument_ids=('OTHER',),
              source_published_at=cutoff + timedelta(days=1))
p['receipts'] = [*p['receipts'], future]
for f in p['funds']:
    f['source_refs'] = ['TEST_FUTURE_FUND']
results['findings']['funds_future_invalid_wrong_security_parent'] = compact(row(MarketInput.model_validate(p)))

# Reconstruction requires a historical time proof for late receipt/row capture elsewhere.
p = _payload(bundle)
p['coverage_type'] = 'RECONSTRUCTED_HISTORICAL'
for f in p['funds']:
    f.update(first_seen_at=cutoff + timedelta(days=2), ingested_at=cutoff + timedelta(days=2))
results['findings']['funds_late_capture_without_proof'] = compact(row(MarketInput.model_validate(p)))

# Future end of a declared multiday disclosure window isn't constrained to the observation day.
p = _payload(bundle)
for f in p['funds']:
    f['window_end'] = bundle.observation_date + timedelta(days=3)
results['findings']['funds_future_window'] = {
    **compact(row(MarketInput.model_validate(p))), 'window_end': str(p['funds'][0]['window_end']),
    'observation_date': str(bundle.observation_date),
}

# Classification may override an explicitly proxy layer by a single unverified boolean.
p = _payload(bundle)
for f in p['funds']:
    f.update(source_layer='MAIN_NET_FLOW_PROXY', classified_active=True)
results['findings']['proxy_promoted_by_boolean'] = compact(row(MarketInput.model_validate(p)))

# Dedupe silently chooses the first conflicting same-key record.
strong = deepcopy(bundle.funds[0])
weak = deepcopy(strong)
weak.update(buy_amount_cny=Decimal('1000000'), net_amount_cny=Decimal('-1000000'))
order_results = []
for funds in ([strong, weak], [weak, strong]):
    p = _payload(bundle); p['funds'] = funds
    order_results.append(compact(row(MarketInput.model_validate(p))))
results['findings']['dedupe_conflict_depends_on_input_order'] = order_results

# Rejected (e.g. unpublished) input themes turn absence of visible linkage into FAIL.
p = _payload(bundle)
for theme in p['themes']:
    theme['source_published_at'] = cutoff + timedelta(days=1)
unpublished_row = row(MarketInput.model_validate(p))
results['findings']['unpublished_theme_is_fail_not_unknown'] = {
    **compact(unpublished_row), 'sector_condition': unpublished_row['conditions']['sector_linkage']}

# Theme A has sufficient peers but no funds. Theme B has only one peer (sector FAIL)
# and one sufficiently large classified fund row (MODERATE). No theme passes both.
p = _payload(bundle)
theme_b = deepcopy(p['themes'][0])
theme_b.update(theme_id='THEME_B', name='TEST_FIXTURE_ONE_PEER', member_ids=('TEST000', 'TEST001'))
p['themes'].append(theme_b)
p['funds'] = [_fund('TEST001', bundle.observation_date, Decimal('60000000'), Decimal('40000000'), theme_id='THEME_B')]
cross = row(MarketInput.model_validate(p))
results['findings']['cross_theme_conjunction'] = {
    **compact(cross), 'sector_condition': cross['conditions']['sector_linkage'],
    'funds_condition': cross['conditions']['funds_linkage'],
    'sector_candidates': [{'theme_id': x['theme_id'], 'status': x['status']} for x in cross['sector_candidates']],
}

# Aggregate run identity doesn't include stage config identity; mutation is in-memory only.
run1 = run_strategy_experiments(bundle, cutoff=cutoff)
saved_config = deepcopy(sam.CONFIG)
try:
    sam.CONFIG['research_seed_parameters']['funds']['grade_mapping']['STRONG']['min_dedup_buy_amount_cny_inclusive'] = 999999999999
    sam.CONFIG['research_seed_parameters']['funds']['grade_mapping']['MODERATE']['min_dedup_buy_amount_cny_inclusive'] = 999999999999
    run2 = run_strategy_experiments(bundle, cutoff=cutoff)
finally:
    sam.CONFIG.clear(); sam.CONFIG.update(saved_config)
stage1, stage2 = run1['results'][sam.STAGE_AWARE_ID], run2['results'][sam.STAGE_AWARE_ID]
results['findings']['stage_config_run_id_collision'] = {
    'same_run_id': run1['run_id'] == run2['run_id'], 'run_id': run1['run_id'],
    'same_top_source_hash': run1['source_hash'] == run2['source_hash'],
    'same_stage_config_hash': stage1['stage_aware_config_hash'] == stage2['stage_aware_config_hash'],
    'stage_selected_before': stage1['counts'], 'stage_selected_after': stage2['counts'],
    'same_content_hash': digest(run1) == digest(run2),
}

store = SQLiteStore(HERE / 'pit_identity_fixture.sqlite3')
store.put_document('TEST_FIXTURE_RUNS', run1['run_id'], run1)
store.put_document('TEST_FIXTURE_RUNS', run1['run_id'], run1)
try:
    store.put_document('TEST_FIXTURE_RUNS', run2['run_id'], run2)
except ImmutableConflict as exc:
    results['findings']['stage_config_run_id_collision']['storage_conflict'] = type(exc).__name__
results['findings']['immutable_original_unchanged'] = digest(store.get_document('TEST_FIXTURE_RUNS', run1['run_id'])) == digest(run1)

# Visible funds change selected state but bypass the effective-dependency resolver.
p = _payload(bundle); p['funds'] = []
without_funds = run_strategy_experiments(MarketInput.model_validate(p), cutoff=cutoff)
results['findings']['funds_missing_from_effective_dependencies'] = {
    'same_effective_input_hash': run1['effective_input_hash'] == without_funds['effective_input_hash'],
    'same_input_hash': run1['input_hash'] == without_funds['input_hash'],
    'fund_refs_in_source_refs': [x for x in run1['source_refs'] if 'fund' in x.lower()],
}

# Different run IDs remain different label identities; coverage on the label is
# the outcome input coverage, not the original signal's coverage.
run3 = deepcopy(run1)
run3.update(run_id=run1['run_id'] + '_LATE_RECONSTRUCTION', coverage_type='RECONSTRUCTED_HISTORICAL',
            audit_mode='HISTORICAL_RECONSTRUCTION', decision_cutoff=(cutoff + timedelta(days=10)).isoformat())
labels1 = evaluate_strategy_experiment_outcomes(run1, bundle, cutoff, horizons=(1,))
labels3 = evaluate_strategy_experiment_outcomes(run3, bundle, cutoff, horizons=(1,))
results['findings']['late_reconstruction_outcome_identity'] = {
    'same_logical_label_ids': {x['logical_label_id'] for x in labels1} == {x['logical_label_id'] for x in labels3},
    'label_count': len(labels1), 'original_run_coverage': run3['coverage_type'],
    'outcome_coverage': sorted({x['coverage_type'] for x in labels3}),
    'signal_cutoff_after_evaluated_at_is_rejected': False,
}

signal_day = bundle.sessions[-3]
signal_bar = next(x for x in bundle.bars if x.instrument_id == 'TEST000' and x.session == signal_day)
signal = {
    'run_id': 'TEST_FIXTURE_EARLY_SIGNAL', 'observation_date': str(signal_day),
    'coverage_type': 'PROSPECTIVE_CAPTURED', 'audit_mode': 'ARCHIVED_POINT_IN_TIME',
    'decision_cutoff': datetime.combine(signal_day, time(20), cutoff.tzinfo).isoformat(),
    'implementation_version': 'TEST_FIXTURE_CONFORMANT', 'spec_conformance': 'SPEC_CONFORMANT',
    'config_hash': 'TEST_FIXTURE_CONFIG',
    'results': {sam.STAGE_AWARE_ID: {'strategy_id': sam.STAGE_AWARE_ID, 'status': 'COMPLETE',
        'items': [{'instrument_id': 'TEST000', 'rank': 1, 'comparable_close': str(signal_bar.comparable_close),
                   'comparable_basis': signal_bar.comparable_basis}]}},
}
late_signal = deepcopy(signal)
late_signal.update(run_id='TEST_FIXTURE_LATE_RECONSTRUCTION', coverage_type='RECONSTRUCTED_HISTORICAL',
                   audit_mode='HISTORICAL_RECONSTRUCTION', decision_cutoff=(cutoff + timedelta(days=10)).isoformat())
early_labels = evaluate_strategy_experiment_outcomes(signal, bundle, cutoff, horizons=(1,))
late_labels = evaluate_strategy_experiment_outcomes(late_signal, bundle, cutoff, horizons=(1,))
mixed = build_strategy_feedback_report(signal, early_labels + late_labels, [], cutoff,
                                      runs=[signal, late_signal], sessions=bundle.sessions)
results['findings']['mixed_prospective_reconstruction_review'] = {
    'late_signal_cutoff': late_signal['decision_cutoff'], 'evaluated_at': cutoff.isoformat(),
    'late_label_status': late_labels[0]['status'],
    'late_label_return': late_labels[0]['metrics']['close_reference_change'],
    'mixed_counts': mixed['counts'],
    'same_stock_day_two_runs_pooled': mixed['rolling_windows']['5']['statistics'][0]['pooled_signals'],
}

path = HERE / 'pit_probe_results.json'
path.write_text(json.dumps(results, indent=2, ensure_ascii=False, default=str), encoding='utf-8')
print(json.dumps(results, indent=2, ensure_ascii=False, default=str))
