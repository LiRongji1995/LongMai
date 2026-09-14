"""Append explicit comparable-price review revisions to an isolated history.

This uses the existing provider's series-level refusal policy. It never accepts
the replacement factor's numeric value, changes RAW fields, or rewrites history.
"""
from __future__ import annotations

from collections import defaultdict
from itertools import chain
from time import monotonic

from .contracts import DailyBar, SourceReceipt, digest
from .provider_comparability import DEFINITION, factor_series
from .strategy_experiments import _revision_key, _select_revision
from .visibility import EvidenceResolver, clock_visible, stamp

REASON = 'FACTOR_REVISION_CONFLICT_REQUIRES_REVIEW'
POLICY = 'EXISTING_PROVIDER_SERIES_LEVEL_FAIL_CLOSED'
COMPARABLE = ('comparable_open', 'comparable_high', 'comparable_low',
              'comparable_close', 'comparable_basis')
REVISION_CLOCKS = ('source_published_at', 'first_seen_at', 'ingested_at')


def apply_factor_revision_reviews(bundle, factor_captures, *, cutoff,
                                  base_bundle=None, max_rows=50000,
                                  max_instruments=64, budget_seconds=60,
                                  progress=None):
    """Return a new wrapper and lossless report; all prior artifacts survive.

    ``factor_captures`` is the normalizer's already validated complete/per-code
    complete input, in actual capture order. Only conflicting instruments are
    read, through the indexed HistoryArtifactStore.iter_rows API. One canonical
    visible RAW revision per key supplies the review row. Ambiguity aborts the
    build; an arbitrary old revision must never become new fact authority.
    """
    start = monotonic()
    cutoff = stamp(cutoff)
    report = {'policy': POLICY, 'reason': REASON, 'conflicts': [], 'reviews': [],
              'already_unknown': [], 'outside_factor_scope': [], 'skipped_invisible': [],
              'retained_inline_originals': [], 'rows_read': 0,
              'source_history_ref': bundle.history_artifact_ref,
              'source_history_hash': bundle.history_artifact_hash,
              'max_rows': max_rows, 'max_instruments': max_instruments,
              'budget_seconds': budget_seconds, 'cutoff': cutoff.isoformat()}
    conflicts = {}
    for iid, captures in sorted(factor_captures.items()):
        series, refs, conflict = factor_series(captures)
        if conflict:
            conflicts[iid] = (captures, refs)
    if not conflicts:
        report.update(status='NO_FACTOR_CONFLICT', new_review_rows=0,
                      history_ref=bundle.history_artifact_ref)
        return bundle, report
    if len(conflicts) > max_instruments:
        raise ValueError('FACTOR_REVIEW_INSTRUMENT_BUDGET_EXCEEDED')
    owner = bundle._history_store
    if owner is None or not bundle.history_artifact_ref:
        raise ValueError('FACTOR_REVIEW_BOUND_HISTORY_REQUIRED')
    manifest = owner.manifest(bundle.history_artifact_ref, bundle.history_artifact_hash)
    if base_bundle is not None:
        owner.manifest(base_bundle.history_artifact_ref, base_bundle.history_artifact_hash)
        if base_bundle.observation_date > bundle.observation_date:
            raise ValueError('FACTOR_REVIEW_BASE_FROM_FUTURE')
        report['base_input'] = {'history_ref': base_bundle.history_artifact_ref,
            'history_hash': base_bundle.history_artifact_hash,
            'observation_date': str(base_bundle.observation_date),
            'inline_bars_hash': digest([row.model_dump(mode='json') for row in base_bundle.bars])}
    receipt_map = {r['receipt_id']: SourceReceipt.model_validate(r) for r in manifest['receipts']}
    for receipt in chain(bundle.receipts, base_bundle.receipts if base_bundle else ()):
        if receipt.receipt_id in receipt_map and receipt_map[receipt.receipt_id] != receipt:
            raise ValueError('FACTOR_REVIEW_RECEIPT_IDENTITY_CONFLICT')
        receipt_map[receipt.receipt_id] = receipt
    resolver = EvidenceResolver(receipt_map, cutoff=cutoff, coverage_type=bundle.coverage_type,
        sessions=bundle.sessions, observation_date=bundle.observation_date, record_rows=False)

    def budget():
        if monotonic() - start > budget_seconds:
            raise TimeoutError('FACTOR_REVIEW_TIME_BUDGET_EXCEEDED')
        if report['rows_read'] > max_rows:
            raise ValueError('FACTOR_REVIEW_ROW_BUDGET_EXCEEDED')

    additions, selected_unknown, retained_inline = {}, {}, {}
    inline = defaultdict(list)
    for row in chain(bundle.bars, base_bundle.bars if base_bundle else ()):
        if row.instrument_id in conflicts and row.session <= bundle.observation_date:
            inline[row.instrument_id].append(row)
    for iid, (captures, factor_refs) in conflicts.items():
        budget()
        for ref in factor_refs:
            parent = receipt_map.get(ref)
            if parent is None or parent.dataset != 'adjustment_factors':
                raise ValueError('FACTOR_REVIEW_PARENT_REQUIRED:' + str(ref))
            # A historical reconstruction label cannot move the review before
            # an actually captured conflict; all four receipt clocks must fit.
            if not clock_visible(parent, cutoff) or not resolver.receipt_resolution(ref).allowed:
                raise ValueError('FACTOR_REVIEW_PARENT_NOT_VISIBLE:' + ref)
        report['conflicts'].append({'instrument_id': iid, 'policy': POLICY,
            'source_refs': list(factor_refs), 'reason': REASON,
            'captures': [{'rows': rows, 'receipt_id': ref,
                'end_date': end, 'start_date': begin} for rows, ref, end, begin in captures],
            'parent_receipts': [receipt_map[ref].model_dump(mode='json') for ref in factor_refs]})
        factor_providers = {receipt_map[ref].provider for ref in factor_refs}
        if len(factor_providers) != 1:
            raise ValueError('FACTOR_REVIEW_CONFLICT_PROVIDER_IDENTITY')
        groups = defaultdict(dict)
        history_hashes = set()
        for row, from_history in chain(((r, True) for r in owner.iter_rows(bundle.history_artifact_ref, 'bar',
                    instrument_id=iid, end=bundle.observation_date,
                    expected_hash=bundle.history_artifact_hash)), ((r, False) for r in inline[iid])):
            report['rows_read'] += 1
            budget()
            if from_history:
                history_hashes.add(digest(row))
            if row.quality == 'INVALID':
                report['skipped_invisible'].append({'row_hash': digest(row), 'reason': 'INVALID_PRICE_QUALITY'})
                continue
            proof = resolver.resolve(row, instrument_id=iid, session=row.session, require_session=True)
            if not proof.allowed:
                report['skipped_invisible'].append({'row_hash': digest(row), 'reason_codes': list(proof.reason_codes)})
                continue
            groups[row.session][digest(row)] = row
        for session, candidates in sorted(groups.items()):
            budget()
            old = _select_revision(list(candidates.values()))
            if old is None:
                raise ValueError('FACTOR_REVIEW_CANONICAL_RAW_CONFLICT:' + iid + ':' + str(session))
            key = (iid, session)
            if all(getattr(old, field) is None for field in COMPARABLE):
                selected_unknown[key] = old
                for original in inline[iid]:
                    original_hash = digest(original)
                    if original.session == session and original_hash not in history_hashes:
                        retained_inline[original_hash] = original
                report['already_unknown'].append({'instrument_id': iid, 'session': str(session),
                    'row_hash': digest(old), 'same_conflict_refs': set(factor_refs) <= set(old.source_refs)})
                continue
            # BaoStock's refusal cannot revoke a separate Tushare (or direct
            # capture) basis merely because its instrument id is the same.
            dependent = str(old.comparable_basis or '').startswith(DEFINITION + ':' + iid + ':') or any(
                receipt_map[ref].dataset == 'adjustment_factors' and
                receipt_map[ref].provider in factor_providers for ref in old.source_refs)
            if not dependent:
                report['outside_factor_scope'].append({'instrument_id': iid, 'session': str(session),
                    'row_hash': digest(old), 'comparable_basis': old.comparable_basis,
                    'source_refs': list(old.source_refs), 'status': 'OUTSIDE_CONFLICT_FACTOR_SERIES'})
                for original in inline[iid]:
                    original_hash = digest(original)
                    if original.session == session and original_hash not in history_hashes:
                        retained_inline[original_hash] = original
                continue
            refs = tuple(dict.fromkeys((*old.source_refs, *factor_refs)))
            if any(ref not in receipt_map for ref in refs):
                raise ValueError('FACTOR_REVIEW_RAW_PARENT_REQUIRED')
            clocks = {field: max(getattr(old, field), *(getattr(receipt_map[ref], field) for ref in refs))
                      for field in REVISION_CLOCKS}
            # Validate anew: no model_copy shortcut can bypass typed clocks.
            review = DailyBar.model_validate(old.model_dump() | {field: None for field in COMPARABLE}
                                             | clocks | {'source_refs': refs})
            if _revision_key(review) <= _revision_key(old):
                raise ValueError('FACTOR_REVIEW_NO_STRICT_ACTUAL_CLOCK_ADVANCE:' + iid + ':' + str(session))
            proof = resolver.resolve(review, instrument_id=iid, session=session, require_session=True)
            if not proof.allowed:
                raise ValueError('FACTOR_REVIEW_PIT_REJECTED:' + ','.join(proof.reason_codes))
            before, after = old.model_dump(mode='json'), review.model_dump(mode='json')
            changed = {field for field in before if before[field] != after[field]}
            if not changed <= set(COMPARABLE) | set(REVISION_CLOCKS) | {'source_refs'}:
                raise AssertionError('FACTOR_REVIEW_RAW_OR_EVENT_MUTATION')
            additions[key] = review
            # Inline-only old revisions must remain available at earlier PIT
            # cutoffs after the new wrapper substitutes its current bar.
            for original in inline[iid]:
                original_hash = digest(original)
                if original.session == session and original_hash not in history_hashes:
                    retained_inline[original_hash] = original
            report['reviews'].append({'instrument_id': iid, 'session': str(session), 'reason': REASON,
                'policy': POLICY, 'old_row_hash': digest(old), 'review_row_hash': digest(review),
                'old_row': before, 'review_row': after, 'changed_fields': sorted(changed),
                'raw_and_qualification_fields_unchanged': True,
                'source_refs': list(refs), 'actual_revision_clocks': {k: v.isoformat() for k, v in clocks.items()}})
        if progress:
            progress({'stage': 'FACTOR_REVIEW_PLAN', 'instrument_id': iid,
                      'rows_read': report['rows_read'], 'review_rows': len(additions)})
    # The whole bounded plan is validated before the first append. No build-time
    # timestamp enters a row or its identity, including repeated normalizations.
    identity = {'history_artifact_ref': bundle.history_artifact_ref,
                'history_artifact_hash': bundle.history_artifact_hash}
    if additions or retained_inline:
        if progress:
            progress({'stage': 'FACTOR_REVIEW_WRITE_START', 'review_rows': len(additions),
                      'retained_inline_rows': len(retained_inline), 'rows_read': report['rows_read']})
        writer = owner.writer(tuple(receipt_map.values()), base_ref=bundle.history_artifact_ref)
        try:
            for original_hash, original in retained_inline.items():
                budget(); writer.append(original)
                report['retained_inline_originals'].append({'row_hash': original_hash,
                    'instrument_id': original.instrument_id, 'session': str(original.session),
                    'policy': 'EXACT_OLD_INLINE_ROW_PRESERVED_WITH_ORIGINAL_CLOCKS',
                    'row': original.model_dump(mode='json')})
            for review in additions.values():
                budget(); writer.append(review)
            budget(); identity = writer.finish(); budget()
        finally:
            writer.close()
    # Include old-cutoff variants; current consumers select the later review.
    # The builder's Day-T envelope uses the same substitution map below.
    replacements = selected_unknown | additions
    bars = tuple(replacements.get((row.instrument_id, row.session), row) for row in bundle.bars)
    present = {(row.instrument_id, row.session) for row in bars}
    original_inline_keys = {(row.instrument_id, row.session) for row in (base_bundle.bars if base_bundle else ())}
    recent_start = bundle.sessions[-min(6, len(bundle.sessions))]
    bars += tuple(row for key, row in replacements.items() if key not in present and
                  (row.session >= recent_start or key in original_inline_keys))
    result = bundle.model_copy(update=identity | {'bars': bars})
    result._history_store = owner
    report.update(status='FACTOR_REVIEW_APPLIED' if additions else 'FACTOR_REVIEW_ALREADY_UNKNOWN',
                  new_review_rows=len(additions), history_ref=result.history_artifact_ref,
                  history_hash=result.history_artifact_hash, elapsed_seconds=monotonic()-start,
                  review_rows_hash=digest(report['reviews']))
    return result, report


def reviewed_t_inline_bars(base_bars, normalized_history, review_report):
    """Substitute only explicitly reviewed keys in the new Day-T wrapper."""
    if not review_report or not review_report.get('conflicts'):
        return base_bars
    selected = {(row['instrument_id'], row['session']): row['review_row_hash'] for row in review_report['reviews']}
    selected.update({(row['instrument_id'], row['session']): row['row_hash'] for row in review_report['already_unknown']})
    replacements = {}
    for row in normalized_history.bars:
        key = (row.instrument_id, str(row.session))
        if key in selected and digest(row) == selected[key]:
            if any(getattr(row, field) is not None for field in COMPARABLE):
                raise ValueError('FACTOR_REVIEW_INLINE_NOT_UNKNOWN')
            replacements[(row.instrument_id, row.session)] = row
    for row in base_bars:
        if (row.instrument_id, str(row.session)) in selected and (row.instrument_id, row.session) not in replacements:
            raise ValueError('FACTOR_REVIEW_SELECTED_INLINE_ROW_MISSING')
    return tuple(replacements.get((row.instrument_id, row.session), row) for row in base_bars)
