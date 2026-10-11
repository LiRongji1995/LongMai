"""Explicit, single-use consumer for genuine new factor facts and audit owner."""
from __future__ import annotations

from dataclasses import replace

from .contracts import DailyBar, digest
from .history_artifacts import digest_market_input, history_receipts
from .visibility import EvidenceResolver, mapping, stamp
from . import historical_replay_factor_facts as facts
from . import short_horizon_strength as r1
from . import strategy_experiments as se

VERSION = 'FACTOR_FACT_AUDIT_GUARDED_R1_CONSUMER_V1'


class FactorFactAuditResolver(EvidenceResolver):
    """Keep original complete-value checks; add mandatory genuine audit witness."""
    def __init__(self, context):
        record = facts._record(context, consuming=True)
        self._fact_context = context
        self._bar_refs = record['bar_refs']
        self._fact_links = record['fact_links']
        self._private_ids = set(record['private_receipt_ids'])
        super().__init__(history_receipts(context.bundle), cutoff=context.actual_cutoff,
            coverage_type=context.bundle.coverage_type, sessions=context.bundle.sessions,
            observation_date=context.bundle.observation_date, record_rows=False)

    def resolve(self, obj, **kwargs):
        facts._record(self._fact_context, consuming=True)
        row = mapping(obj)
        refs = tuple(kwargs.get('source_refs') if kwargs.get('source_refs') is not None else row.get('source_refs', ()))
        if 'comparable_close' in row or self._private_ids.intersection(refs):
            # Original strategy caches use Python date/datetime/Decimal rows;
            # canonicalize with the same DailyBar schema before matching the
            # factory's immutable JSON row identity (including UTC spelling).
            expected = self._bar_refs.get(digest(DailyBar.model_validate(row)))
            if expected is None or tuple(expected) != refs:
                raise ValueError('FACTOR_FACT_VALUE_ROW_OR_SOURCE_REFS_CHANGED')
            for ref in self._private_ids.intersection(refs):
                if ref not in self._fact_links or not self._fact_links[ref]:
                    raise ValueError('FACTOR_FACT_MANDATORY_AUDIT_PARENT_MISSING')
                if any(parent not in self.receipts for parent in self._fact_links[ref]):
                    raise ValueError('FACTOR_FACT_AUDIT_PROXY_REFERENCE_UNRESOLVED')
        return super().resolve(obj, **kwargs)

    def effective_dependencies(self):
        result = super().effective_dependencies()
        record = facts._record(self._fact_context, consuming=True)
        result['mandatory_factor_fact_audit'] = {
            'version': VERSION, 'owner_version': facts.OWNER_VERSION,
            'audit_graph_hash': record['graph_hash'], 'manifest_hash': record['manifest_hash'],
            'source_identity': record['source_identity'],
            'source_bindings': list(record['assembler']()._files.values()),
            'coverage': 'CURRENT_CUTOFF_INLINE_ROWS_NOT_COMPLETE_HISTORY_OR_PIT'}
        return result


def validate_factor_fact_context(context, *, cutoff):
    if type(context) is not facts.FactorFactContext:
        raise ValueError('FACTOR_FACT_GENUINE_CONTEXT_REQUIRED')
    if stamp(cutoff) != context.actual_cutoff:
        raise ValueError('FACTOR_FACT_EXACT_FACTORY_CUTOFF_REQUIRED')
    facts._validate_context(context, fresh_sources=True)


def run_short_horizon_with_factor_fact_context(context, *, cutoff):
    """Fresh whole-source auth before/after unchanged R1; no restored JSON cap.

    The returned wrapper distinguishes original R1 identity from this new source
    producer/consumer identity. It is read-only evidence, never a new authority.
    """
    if type(context) is not facts.FactorFactContext:
        raise ValueError('FACTOR_FACT_GENUINE_CONTEXT_REQUIRED')
    record = facts._record(context)
    if record['state'] != 'ISSUED':
        raise ValueError('FACTOR_FACT_AUTHORITY_CLOSED_OR_REUSED')
    record['state'] = 'CONSUMING'
    try:
        if stamp(cutoff) != context.actual_cutoff:
            raise ValueError('FACTOR_FACT_EXACT_FACTORY_CUTOFF_REQUIRED')
        facts._validate_context(context, consuming=True, fresh_sources=True)
        original_identity = r1.short_horizon_source_identity()
        ordinary = se._prepare_context(context.bundle, context.actual_cutoff)
        guarded = replace(ordinary, resolver=FactorFactAuditResolver(context), scalar_cache={})
        result = r1.run_short_horizon_strength(context.bundle, cutoff=context.actual_cutoff, _context=guarded)
        if r1.short_horizon_source_identity() != original_identity:
            raise ValueError('FACTOR_FACT_FROZEN_R1_IDENTITY_CHANGED_DURING_CONSUMPTION')
        terminal = facts._validate_context(context, consuming=True, fresh_sources=True)
        dependencies = guarded.resolver.effective_dependencies()
        wrapped = {'schema': 'FACTOR_FACT_GUARDED_R1_RESEARCH_RESULT_V1',
            'consumer_version': VERSION, 'producer_version': facts.VERSION,
            'owner_version': facts.OWNER_VERSION, 'source_identity': terminal['source_identity'],
            'original_R1_source_identity': original_identity,
            'actual_cutoff': context.actual_cutoff.isoformat(), 'issued_at': context.issued_at.isoformat(),
            'input_hash': digest_market_input(context.bundle),
            'audit_graph_hash': terminal['graph_hash'], 'owner_manifest_hash': terminal['manifest_hash'],
            'effective_value_and_audit_dependencies_hash': digest(dependencies),
            'mandatory_audit_source_bindings': list(terminal['assembler']()._files.values()),
            'all_original_source_SHA_stat_terminal_equal': True,
            'original_PARTIAL_statuses_modified': False, 'old_capability_restored': False,
            'archived_point_in_time': False, 'independent_cycles': 0,
            'market_edge': 'NOT_ESTABLISHED', 'result': result}
        record['state'] = 'SEALED'
        return wrapped
    except BaseException:
        record['state'] = 'INVALIDATED'
        raise
