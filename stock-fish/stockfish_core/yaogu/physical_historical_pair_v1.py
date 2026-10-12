"""One physical input, one cutoff, and two unchanged native research arms.

Returned JSON is evidence for review, never a source or consumption capability.
The frozen physical V1 builder and both original selectors remain unchanged.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
import hashlib
import os
from pathlib import Path
import weakref

from . import physical_historical_research as physical
from . import short_horizon_strength as r1
from . import short_horizon_participation as r2
from . import strategy_experiments as se
from .contracts import digest
from .history_artifacts import digest_json_stream, digest_market_input
from .research_context_artifacts import SourceArtifactReader
from .saved_r2_delivery import _Registry
from .saved_r2_sources_v2 import AuthenticatedSourceArtifactsV2, issue_saved_r2_source_reader
from .visibility import stamp

VERSION = 'PHYSICAL_ORIGINAL_HISTORICAL_R1_R2_PAIR_V1'
FEATURE_VERSION = 'SAME_PHYSICAL_INPUT_CUTOFF_NATIVE_PAIR_V1'
SCHEMA = 'PHYSICAL_HISTORICAL_R1_R2_PAIR_ARCHIVE_V1'
R1, R2 = 'SHORT_HORIZON_STRENGTH_R1', 'SHORT_HORIZON_STRENGTH_R2'
_TOKEN = object()
_USED_JOBS, _FACTORY_JOBS, _BINDINGS = set(), set(), {}
_ROW_OVERRIDES = frozenset(('strategy_id', 'implementation_version', 'spec_revision',
    'feature_version', 'decision_status', 'status', 'research_selected', 'research_status',
    'formal_status', 'formal_focus', 'core_pass', 'executability', 'market_edge',
    'reason_codes', 'watch_reasons', 'rank', 'display_rank', 'display_order'))
_R2_ROW_ADDITIONS = frozenset(('r1_decision_status', 'r1_research_selected', 'participation',
    'participation_status', 'participation_peak_turnover_fraction', 'liquidity',
    'liquidity_status', 'liquidity_position', 'liquidity_reference_cny',
    'liquidity_ratio_to_reference', 'liquidity_soft_score', 'liquidity_selection_gate', 'style_status'))
_R1_WRAPPER_FIELDS = frozenset(('data_scope_status', 'coverage_scope', 'input_hash',
    'coverage_type', 'execution_profile', 'history_artifact_ref', 'history_artifact_hash'))


def _require(value, reason):
    if not value:
        raise ValueError('PHYSICAL_HISTORICAL_PAIR_' + reason)


def source_identity():
    """Bind the new entry and spec plus the frozen builders, rules and configs."""
    root = Path(__file__).resolve().parents[2]
    dependencies = (physical.source_identity(), r1.short_horizon_source_identity(),
                    r2.short_horizon_source_identity())
    files = {}
    for identity in dependencies:
        for name, sha in identity['source_file_hashes'].items():
            _require(name not in files or files[name] == sha, 'DEPENDENCY_IDENTITY_CONFLICT')
            files[name] = sha
    for path in (Path(__file__), root / 'docs/PHYSICAL_HISTORICAL_PAIR_V1.md'):
        files[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    body = dict(implementation_version=VERSION, feature_version=FEATURE_VERSION,
        frozen_builder_source_hash=dependencies[0]['source_hash'],
        native_strategy_source_hashes={R1:dependencies[1]['source_hash'], R2:dependencies[2]['source_hash']},
        native_strategy_identities={R1:dependencies[1], R2:dependencies[2]},
        native_parameter_states={R1:r1.PARAMETER_STATUS, R2:r2.PARAMETER_STATUS},
        source_file_hashes=files, parameter_status='UNVALIDATED', market_edge='NOT_ESTABLISHED')
    return dict(body, source_hash=digest_json_stream(body))


def _native_identity(strategy):
    return (r1 if strategy == R1 else r2).short_horizon_source_identity()


def _selected_state(row):
    return None if row['decision_status'] == 'UNKNOWN' else row['research_selected']


def _validate_native(run, strategy, bundle, input_hash, cutoff, expected_identity=None):
    """Validate a complete native arm, including its stable run identifier."""
    identity = _native_identity(strategy)
    _require(expected_identity is None or identity == expected_identity, 'SEALED_NATIVE_RULE_IDENTITY_CHANGED')
    identity = expected_identity if expected_identity is not None else identity
    _require(isinstance(run, dict) and run.get('strategy_id') == strategy
        and set(run.get('results', {})) == {strategy}, 'NATIVE_RESULT_STRATEGY_CHANGED')
    _require(run.get('input_hash') == input_hash
        and run.get('input_id') == 'physical_historical_pair_' + input_hash
        and run.get('observation_date') == str(bundle.observation_date)
        and run.get('decision_cutoff') == cutoff.isoformat(), 'NATIVE_RESULT_BINDING_CHANGED')
    _require(bundle.coverage_type == 'RECONSTRUCTED_HISTORICAL'
        and run.get('coverage_type') == bundle.coverage_type
        and run.get('audit_mode') == 'HISTORICAL_RECONSTRUCTION'
        and run.get('strategy_family') == 'SHORT_HORIZON_STRENGTH_RESEARCH'
        and run.get('execution_profile') == 'SHORT_HORIZON_RESEARCH_SINGLE'
        and run.get('rule_application_role') == 'NEW_RESEARCH_VERSION_MATURITY_UNVALIDATED'
        and run.get('spec_conformance') == 'SPEC_CONFORMANT', 'NATIVE_ROLE_CHANGED')
    _require(run.get('parameter_status') == (r1 if strategy == R1 else r2).PARAMETER_STATUS,
        'NATIVE_PARAMETER_STATUS_CHANGED')
    for key, value in identity.items():
        _require(run.get(key) == value, 'NATIVE_RULE_IDENTITY_CHANGED:' + key)
    for key in ('history_artifact_ref', 'history_artifact_hash'):
        _require(run.get(key) == getattr(bundle, key), 'NATIVE_HISTORY_BINDING_CHANGED')
    _require(run.get('universe_hash') == digest(run.get('qualification')), 'NATIVE_QUALIFICATION_HASH_CHANGED')
    fields = ('input_hash', 'observation_date', 'decision_cutoff', 'spec_hash', 'source_hash',
              'implementation_version', 'universe_hash', 'execution_profile', 'feature_version')
    if strategy == R2:
        fields += ('research_context_hash',)
    _require(run.get('run_id') == se._stable_id('strategy_experiment', {key:run[key] for key in fields}),
        'NATIVE_RUN_ID_CHANGED')
    result = run['results'][strategy]
    for key, value in identity.items():
        _require(result.get(key) == value, 'INNER_RULE_IDENTITY_CHANGED:' + key)
    _require(result.get('strategy_id') == strategy and result.get('coverage_type') == bundle.coverage_type
        and result.get('execution_profile') == run['execution_profile']
        and result.get('selection_policy_id') == strategy + '_OBSERVATION', 'INNER_ROLE_CHANGED')
    for key in ('history_artifact_ref', 'history_artifact_hash'):
        _require(result.get(key) == getattr(bundle, key), 'INNER_HISTORY_BINDING_CHANGED')
    base_watermark = max((receipt.ingested_at.isoformat() for receipt in bundle.receipts), default=None)
    expected_watermark = base_watermark
    if strategy == R2:
        market = result['market_turnover_context']
        _require(market['context_content_hash'] == digest_json_stream(
            {key:value for key,value in market.items() if key != 'context_content_hash'}),
            'MARKET_CONTEXT_HASH_CHANGED')
        context = dict(dynamic_liquidity=market, theme_evolution=result['theme_evolution'],
            theme_market_pack_hash=None, theme_membership_mode='DATED_ONLY',
            application_mode='NATIVE_R1_PLUS_CONTEXT', base_run_content_hash=None)
        _require(run['research_context_hash'] == digest_json_stream(context), 'R2_CONTEXT_HASH_CHANGED')
        _require(run.get('research_context_role') == 'NATIVE_R1_PLUS_CONTEXT'
            and all(key not in run for key in ('base_run_provenance', 'target_calendar_evidence', 'target_session')),
            'R2_NATIVE_CONTEXT_ROLE_CHANGED')
        used = set(run['source_refs'])
        visible_used = [receipt for receipt in bundle.receipts if receipt.receipt_id in used
            and max(receipt.event_time, receipt.source_published_at, receipt.first_seen_at,
                    receipt.ingested_at) <= cutoff]
        context_watermark = max((receipt.ingested_at.isoformat() for receipt in visible_used), default=None)
        _require(run.get('base_data_watermark') == base_watermark
            and run.get('context_data_watermark') == context_watermark, 'NATIVE_CONTEXT_WATERMARK_CHANGED')
        expected_watermark = max(filter(None, (base_watermark, context_watermark)), default=None)
    _require(run.get('data_watermark') == expected_watermark, 'NATIVE_DATA_WATERMARK_CHANGED')
    _require(result.get('input_hash') == input_hash
        and result.get('decision_cutoff') == cutoff.isoformat(), 'INNER_RESULT_BINDING_CHANGED')
    rows = result.get('items')
    _require(isinstance(rows, list), 'COMPLETE_NATIVE_ITEMS_REQUIRED')
    ids = [row['instrument_id'] for row in rows]
    _require(len(ids) == len(set(ids)) and len(ids) == run['qualification']['eligible']
        and set(ids) == set(run['qualification']['eligible_ids'])
        and result.get('qualification') == run['qualification']
        and set(result.get('eligible_pool_ids', ())) == set(ids),
        'NATIVE_ELIGIBLE_ITEMS_INCOMPLETE')
    _require(ids == [row['instrument_id'] for row in sorted(rows,
        key=lambda value:(not value['research_selected'], value['instrument_id']))],
        'NATIVE_DISPLAY_ORDER_CHANGED')
    for rank, row in enumerate(rows, 1):
        _require(row['decision_status'] in ('WATCH', 'REJECTED', 'UNKNOWN')
            and type(row['research_selected']) is bool
            and row['research_selected'] is (row['decision_status'] == 'WATCH')
            and row.get('formal_focus') is False and row.get('core_pass') is False,
            'NATIVE_DECISION_OR_FORMAL_STATUS_CHANGED')
        _require(row.get('strategy_id') == strategy
            and all(row.get(key) == identity[key] for key in
                    ('implementation_version', 'spec_revision', 'feature_version'))
            and row.get('status') == row['decision_status']
            and row.get('rank') == row.get('display_rank') == row.get('display_order') == rank,
            'NATIVE_ROW_METADATA_CHANGED')
    selected = [row['instrument_id'] for row in rows if row['research_selected']]
    policy = strategy + '_OBSERVATION'
    _require(result.get('research_selected_ids') == selected
        and run['selection_results'][policy]['selected_ids'] == selected
        and run['selection_results'][policy]['count'] == len(selected), 'NATIVE_SELECTED_IDS_CHANGED')
    decisions = Counter(row['decision_status'] for row in rows)
    counts = result['counts']
    _require(counts['eligible'] == counts['evaluated'] == len(rows)
        and counts['focus'] == counts['passed'] == 0
        and counts['watch'] == decisions['WATCH'] and counts['rejected'] == decisions['REJECTED']
        and counts['unknown'] == decisions['UNKNOWN'], 'NATIVE_COUNTS_CHANGED')
    return result


def _native_r1_parent(left, bundle, input_hash):
    """Remove only this frozen wrapper's additions; never rerun the selector."""
    body = {key:value for key,value in left.items() if key not in _R1_WRAPPER_FIELDS}
    body['items'] = [{key:value for key,value in row.items()
                      if key not in ('display_order', 'rank_semantics')} for row in left['items']]
    body.update(input_hash=input_hash, history_artifact_ref=bundle.history_artifact_ref,
                history_artifact_hash=bundle.history_artifact_hash)
    return body


def _compare(r1_run, r2_run, bundle, input_hash, cutoff, native_identities=None):
    identities = native_identities if native_identities is not None else {
        R1:_native_identity(R1), R2:_native_identity(R2)}
    left = _validate_native(r1_run, R1, bundle, input_hash, cutoff, identities[R1])
    right = _validate_native(r2_run, R2, bundle, input_hash, cutoff, identities[R2])
    _require(r1_run['qualification'] == r2_run['qualification'], 'NATIVE_QUALIFICATION_CHANGED')
    by_left = {row['instrument_id']:row for row in left['items']}
    by_right = {row['instrument_id']:row for row in right['items']}
    _require(set(by_left) == set(by_right), 'NATIVE_PAIR_IID_SET_CHANGED')
    parent_hash = digest_json_stream(_native_r1_parent(left, bundle, input_hash))
    provenance = dict(mode='NATIVE_R1_EVALUATION',
        base_run_id='native_r1_' + digest(dict(input_hash=input_hash,
            cutoff=cutoff.isoformat(), base_result_hash=parent_hash)),
        base_run_id_role='IN_MEMORY_NATIVE_R1_RESULT_NOT_SAVED_RUN', base_result_hash=parent_hash,
        base_source_hash=identities[R1]['source_hash'], input_id='market_' + input_hash,
        input_hash=input_hash, observation_date=str(bundle.observation_date), decision_cutoff=cutoff.isoformat(),
        base_feature_version=identities[R1]['feature_version'], base_strategy_id=R1,
        base_qualification_hash=digest(r1_run['qualification']),
        history_artifact_ref=bundle.history_artifact_ref, history_artifact_hash=bundle.history_artifact_hash,
        original_r1_result_preserved=True, original_r1_history_database_reverified_in_this_application=True,
        style_source_scope='ORIGINAL_NATIVE_HISTORY_AND_INLINE_RESOLVER',
        base_decision_cutoff=cutoff.isoformat(), new_context_cutoff=cutoff.isoformat(),
        new_context_does_not_revise_base_price_or_qualification=True)
    _require(right['base_r1_result_hash'] == parent_hash and right['base_run_provenance'] == provenance
        and right['base_decision_cutoff'] == right['new_context_cutoff'] == cutoff.isoformat(),
        'R2_IN_MEMORY_NATIVE_PARENT_CHANGED')
    rows, transitions, participation = [], Counter(), Counter()
    for iid in sorted(by_left):
        a, b = by_left[iid], by_right[iid]
        _require(set(b) == set(a) | _R2_ROW_ADDITIONS, 'R2_ROW_FIELD_SET_CHANGED:' + iid)
        original, projected = {}, {}
        for key, value in a.items():
            if key in _ROW_OVERRIDES:
                continue
            _require(key in b, 'BASE_EVIDENCE_FIELD_MISSING:' + iid + ':' + key)
            original[key] = value
            if key in ('exact_features', 'conditions'):
                _require(isinstance(value, dict) and isinstance(b[key], dict)
                    and set(b[key]) == set(value) | {'recent_participation', 'current_liquidity'},
                    'BASE_EVIDENCE_MAPPING_CHANGED:' + iid + ':' + key)
                projected[key] = {name:b[key][name] for name in value}
            else:
                projected[key] = b[key]
        _require(original == projected, 'BASE_EVIDENCE_CHANGED:' + iid)
        _require(b['r1_decision_status'] == a['decision_status']
            and b['r1_research_selected'] is a['research_selected'], 'R2_BASE_DECISION_CHANGED:' + iid)
        state = b['participation_status']
        _require(state in ('PASS', 'FAIL', 'UNKNOWN')
            and state == b['participation']['status'], 'PARTICIPATION_STATUS_CHANGED:' + iid)
        _require(r2.evaluate_participation(b['exact_features']['recent_participation']) == b['participation'],
            'PARTICIPATION_EVIDENCE_CHANGED:' + iid)
        _require(r2.evaluate_liquidity(b['exact_features']['current_liquidity'], right['market_turnover_context'])
            == b['liquidity'], 'LIQUIDITY_EVIDENCE_CHANGED:' + iid)
        style = r1.combine({'recent_participation': b['participation']})
        _require(b['style_status'] == style['status']
            and b['conditions']['recent_participation'] == b['participation']
            and b['conditions']['current_liquidity'] == b['liquidity'], 'R2_STYLE_EVIDENCE_CHANGED:' + iid)
        expected_selected = a['research_selected'] is True and state == 'PASS'
        expected_decision = ('REJECTED' if a['decision_status'] == 'REJECTED' or state == 'FAIL'
                             else 'WATCH' if expected_selected else 'UNKNOWN')
        _require(b['research_selected'] is expected_selected and b['decision_status'] == expected_decision,
            'R2_PARTICIPATION_DECISION_CHANGED:' + iid)
        _require(b['reason_codes'] == sorted(set(a['reason_codes'] + b['participation']['reason_codes']))
            and b['status'] == expected_decision
            and b['formal_status'] == ('FAIL' if expected_decision == 'REJECTED' else 'UNKNOWN')
            and b['executability'] == 'UNCONFIRMED' and b['market_edge'] == 'NOT_ESTABLISHED',
            'R2_REASON_OR_SAFETY_CHANGED:' + iid)
        expected_research_status = ('RESEARCH_OBSERVATION' if expected_selected else
            'RECENT_PARTICIPATION_REJECTED' if state == 'FAIL' else
            'UNKNOWN_PARTICIPATION' if state == 'UNKNOWN' and a['decision_status'] != 'REJECTED' else
            a.get('research_status'))
        _require(b['research_status'] == expected_research_status
            and b['watch_reasons'] == (['SHR2_RESEARCH_ONLY_EXECUTION_UNCONFIRMED'] if expected_selected else []),
            'R2_RESEARCH_PRESENTATION_CHANGED:' + iid)
        _require(b['liquidity_selection_gate'] is False and b['liquidity']['selection_gate'] is False,
            'LIQUIDITY_MUST_NOT_FILTER:' + iid)
        exact_amount = a['exact_features']['amount']
        liquidity = b['liquidity']
        scalar_values = dict(participation_peak_turnover_fraction=se._number(
            r1.exact(b['participation']['maximum_verified_turnover_fraction'])),
            liquidity_status=liquidity['status'], liquidity_position=liquidity['position'],
            liquidity_reference_cny=se._number(r1.exact(liquidity['reference_cny'])),
            liquidity_ratio_to_reference=se._number(r1.exact(liquidity['ratio_to_reference'])),
            liquidity_soft_score=se._number(r1.exact(liquidity['soft_score_exact'])))
        _require(all(b[key] == value for key,value in scalar_values.items()), 'R2_SCALAR_EVIDENCE_CHANGED:' + iid)
        if liquidity['value'] is not None:
            _require(r1.exact(liquidity['value']) == r1.exact(exact_amount), 'CURRENT_AMOUNT_CHANGED:' + iid)
        transition = a['decision_status'] + '->' + b['decision_status']
        transitions[transition] += 1
        participation[state] += 1
        rows.append(dict(instrument_id=iid, primary_stage=a.get('primary_stage'),
            matched_arms=a.get('matched_arms'), r1_decision_status=a['decision_status'],
            r2_decision_status=b['decision_status'], r1_research_selected=a['research_selected'],
            r2_research_selected=b['research_selected'], r1_selected_state=_selected_state(a),
            r2_selected_state=_selected_state(b), participation_status=state,
            participation=b['participation'], amount_cny_exact=exact_amount,
            liquidity=liquidity, base_evidence_hash=digest_json_stream(original),
            r2_base_evidence_hash=digest_json_stream(projected)))
    return dict(schema_version='PHYSICAL_NATIVE_PAIR_EXACT_COMPARISON_V1',
        observation_date=str(bundle.observation_date), decision_cutoff=cutoff.isoformat(),
        input_hash=input_hash, qualification_hash=r1_run['universe_hash'], eligible=len(rows),
        rows=rows, decision_transitions=dict(sorted(transitions.items())),
        participation_counts={state:participation[state] for state in ('PASS','FAIL','UNKNOWN')},
        r1_selected_ids=left['research_selected_ids'], r2_selected_ids=right['research_selected_ids'],
        market_turnover_context=right['market_turnover_context'],
        unknown_decision_state_is_null=True, liquidity_is_soft_context_only=True,
        unchanged_base_evidence_verified=True, parameter_status='UNVALIDATED',
        historical_PIT_count=0, complete_independent_cycles=0, market_edge='NOT_ESTABLISHED')


class _PairBinding:
    def __new__(cls, *_args, **_kwargs):
        raise ValueError('PHYSICAL_HISTORICAL_PAIR_FACTORY_ONLY')

    def __copy__(self):
        raise ValueError('PHYSICAL_HISTORICAL_PAIR_LIVE_BINDING_NOT_COPYABLE')

    def __deepcopy__(self, _memo):
        raise ValueError('PHYSICAL_HISTORICAL_PAIR_LIVE_BINDING_NOT_COPYABLE')

    def validate(self):
        held = _BINDINGS.get(id(self))
        _require(type(self) is _PairBinding and self._token is _TOKEN and held is not None
            and held['owner']() is self and held['pid'] == os.getpid()
            and held['job'] == self.job and self.job in _USED_JOBS and self.job in _FACTORY_JOBS
            and held['issued_id'] == id(self.issued) and held['bundle_id'] == id(self.bundle)
            and held['reader_id'] == id(self.reader) and held['policy_registry_id'] == id(self.policy_registry)
            and held['deadline_id'] == id(self.deadline) and held['deadline_end'] == self.deadline.end
            and held['input_hash'] == self.input_hash and held['cutoff'] == self.cutoff
            and held['identity_hash'] == digest_json_stream(self.identity)
            and held['policy_registry_hash'] == self._policy_registry_hash
            and held['receipts_hash'] == digest_json_stream([row.model_dump(mode='json') for row in self.receipts])
            and held['state'] == self.state and held['attempts'] == self.attempts,
            'GENUINE_PAIR_FACTORY_BINDING_CHANGED')
        _require(source_identity() == self.identity, 'IMPLEMENTATION_CHANGED')
        _require(digest_market_input(self.bundle) == self.input_hash, 'DERIVED_INPUT_CHANGED')
        _require(type(self.issued) is AuthenticatedSourceArtifactsV2
            and type(self.reader) is SourceArtifactReader
            and self.issued._reader is self.reader and self.issued.receipts == self.receipts,
            'LIVE_ISSUER_READER_CHANGED')
        self.issued.validate_binding()
        _require(digest_json_stream(self.policy_registry.files) == self._policy_registry_hash,
            'POLICY_REGISTRY_CHANGED')
        self.policy_registry.terminal()
        _require(set(self.results) == set(held['results']), 'SEALED_NATIVE_RUN_SET_CHANGED')
        for strategy, record in held['results'].items():
            _require(id(self.results[strategy]) == record['object_id']
                and digest_json_stream(self.results[strategy]) == record['content_hash'],
                'SEALED_NATIVE_RUN_CHANGED:' + strategy)
        self.deadline.check()

    def _state(self, value):
        held = _BINDINGS[id(self)]
        permitted = {'READY': {'CONSUMING'}, 'CONSUMING': {'COMPLETE', 'FAILED'},
                     'COMPLETE': set(), 'FAILED': set()}
        _require(value in permitted[held['state']], 'ILLEGAL_PAIR_STATE_TRANSITION')
        _require(value == 'FAILED' or self.state == held['state'], 'PAIR_STATE_CHANGED')
        held['state'] = self.state = value

    def _arm(self, strategy):
        self.validate()
        _require(self.state == 'CONSUMING' and self.attempts[strategy] == 0, 'NATIVE_ARM_ALREADY_ATTEMPTED')
        self.attempts[strategy] = _BINDINGS[id(self)]['attempts'][strategy] = 1
        arguments = dict(cutoff=self.cutoff, input_id='physical_historical_pair_' + self.input_hash,
                         strategy_id=strategy)
        if strategy == R2:
            # Genuine SourceArtifactReader, never a supplied lambda or JSON decoder.
            arguments.update(_source_payload_reader=self.reader, _source_receipts=self.receipts)
        run = se.run_short_horizon_strategy(self.bundle, **arguments)
        _validate_native(run, strategy, self.bundle, self.input_hash, self.cutoff,
            self.identity['native_strategy_identities'][strategy])
        self.results[strategy] = run
        _BINDINGS[id(self)]['results'][strategy] = dict(object_id=id(run), content_hash=digest_json_stream(run))
        self.validate()
        return run

    def consume(self, cutoff):
        self.validate()
        _require(stamp(cutoff) == self.cutoff, 'NATIVE_EXACT_CUTOFF_CHANGED')
        _require(self.state == 'READY', 'NATIVE_PAIR_ALREADY_CONSUMED')
        self._state('CONSUMING')
        try:
            left, right = self._arm(R1), self._arm(R2)
            comparison = _compare(left, right, self.bundle, self.input_hash, self.cutoff,
                self.identity['native_strategy_identities'])
            self.validate()
        except BaseException:
            self._state('FAILED')
            raise
        self._state('COMPLETE')
        return self.results, comparison


def _new_pair_binding(issued, bundle, identity, policy_registry, job, cutoff, deadline):
    _require(job in _USED_JOBS and job not in _FACTORY_JOBS, 'REGISTERED_FACTORY_ALREADY_ISSUED')
    _require(type(issued) is AuthenticatedSourceArtifactsV2, 'GENUINE_PHYSICAL_SOURCE_ISSUER_REQUIRED')
    issued.validate_binding()
    _FACTORY_JOBS.add(job)
    owner = object.__new__(_PairBinding)
    owner._token, owner.issued, owner.bundle, owner.identity = _TOKEN, issued, bundle, identity
    owner.reader, owner.receipts = issued.reader, issued.receipts
    owner.policy_registry, owner.deadline = policy_registry, deadline
    owner._policy_registry_hash = digest_json_stream(policy_registry.files)
    owner.input_hash, owner.cutoff, owner.job = digest_market_input(bundle), stamp(cutoff), job
    owner.state, owner.attempts, owner.results = 'READY', {R1:0,R2:0}, {}
    key = id(owner)
    _BINDINGS[key] = dict(owner=weakref.ref(owner, lambda _: _BINDINGS.pop(key, None)),
        pid=os.getpid(), job=job, issued_id=id(issued), bundle_id=id(bundle), reader_id=id(owner.reader),
        policy_registry_id=id(policy_registry), policy_registry_hash=owner._policy_registry_hash,
        deadline_id=id(deadline), deadline_end=deadline.end,
        input_hash=owner.input_hash, cutoff=owner.cutoff, identity_hash=digest_json_stream(identity),
        receipts_hash=digest_json_stream([row.model_dump(mode='json') for row in owner.receipts]),
        state='READY', attempts={R1:0,R2:0}, results={})
    return owner


def run_physical_historical_pair(sources_manifest_path, source_policy, observation_date,
        registered_worker_deadline, history_sessions=26):
    """Fresh original issuance and one same-input/cutoff native R1/R2 comparison.

    No external input, callback, saved base run, selector override, or history DB.
    Only the new module's explicitly invoked research entry is affected.
    """
    _require(isinstance(sources_manifest_path, (str, Path)) and isinstance(source_policy, (str, Path)),
        'REAL_SOURCE_AND_POLICY_PATHS_REQUIRED')
    _require(type(history_sessions) is int and history_sessions == 26, 'REGISTERED_26_SESSION_WINDOW_REQUIRED')
    day = date.fromisoformat(str(observation_date))
    deadline = physical._Deadline(registered_worker_deadline)
    path = Path(sources_manifest_path).resolve(strict=True)
    job = (str(path), str(day), deadline.end.isoformat())
    _require(job not in _USED_JOBS, 'REGISTERED_JOB_ALREADY_ATTEMPTED')
    _USED_JOBS.add(job)
    with deadline:
        identity = source_identity()
        policy = physical.frozen.source_policy(source_policy)
        _require(policy['board_authority']['SZSE']['range'] == ['000001','004999']
            and policy['board_authority']['SZSE']['effective_from'] == '2021-04-06', 'SZSE_POLICY_SCOPE_CHANGED')
        policy_registry = _Registry()
        for registration in policy['source_documents'].values():
            policy_registry.read(registration['path'], registration['sha256'])
        issued_at = datetime.now(timezone.utc)
        issued = issue_saved_r2_source_reader(sources_manifest_path=path, cutoff=issued_at)
        _require(type(issued) is AuthenticatedSourceArtifactsV2, 'GENUINE_PHYSICAL_SOURCE_ISSUER_REQUIRED')
        started_at = datetime.now(timezone.utc)
        bundle, audit = physical._build(issued, policy, day, history_sessions, started_at, deadline)
        derived_at = stamp(audit['actual_derived_at'])
        cutoff = datetime.now(timezone.utc)
        _require(cutoff >= derived_at >= started_at >= issued_at, 'ACTUAL_CLOCK_ORDER_INVALID')
        binding = _new_pair_binding(issued, bundle, identity, policy_registry, job, cutoff, deadline)
        native_runs, comparison = binding.consume(cutoff)
        binding.validate()
        terminal_at = datetime.now(timezone.utc)
        deadline.check()
        return dict(schema_version=SCHEMA, source_identity=identity,
            native_strategy_identities=identity['native_strategy_identities'],
            issued_at=issued_at.isoformat(), derived_at=derived_at.isoformat(), decision_cutoff=cutoff.isoformat(),
            terminal_verified_at=terminal_at.isoformat(), registered_worker_deadline=deadline.end.isoformat(),
            native_attempts=dict(binding.attempts), native_r1_evaluations_expected=2,
            native_R2_recomputes_R1_by_frozen_design=True, selector_rule_changed=False,
            normalization_view_role='ONE_NEW_PHYSICAL_DERIVED_INPUT_SAME_CUTOFF',
            input_hash=binding.input_hash, input=bundle.model_dump(mode='json'),
            native_runs=native_runs, native_run_hashes={key:digest_json_stream(value) for key,value in native_runs.items()},
            comparison=comparison, source_registry=issued.registry.files, policy_registry=policy_registry.files,
            audit=audit, historical_qualification_scope=audit['scope'], historical_theme_membership='UNKNOWN',
            historical_PIT_count=0, complete_independent_cycles=0, market_edge='NOT_ESTABLISHED',
            default_service_connected=False, archive_role='ARCHIVE_ONLY_NOT_RESTORABLE_SOURCE_CAPABILITY')
