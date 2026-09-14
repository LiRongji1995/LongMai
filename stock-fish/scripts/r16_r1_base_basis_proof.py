"""Independent, read-only proof of every immutable bar member in one history base.

No strategy, provider, normalizer or database initialization is called.  Complete
factor security fragments retain their PARTIAL parent status.  RAW-only rows are
verified as RAW observations and never promoted to comparable observations.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def file_sha(path):
    hasher = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            hasher.update(block)
    return hasher.hexdigest()


def stamp(text):
    result = datetime.fromisoformat(str(text).replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('CLOCK_MUST_BE_AWARE')
    return result


def number(value, positive=True):
    try:
        parsed = Decimal(str(value))
        return parsed if parsed.is_finite() and (parsed > 0 if positive else parsed >= 0) else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def instrument(code):
    return str(code)[3:] + '.' + str(code)[:2].upper()


class Unverified(ValueError):
    pass


class Proof:
    def __init__(self, args):
        self.args = args
        self.started = time.monotonic()
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.last_checkpoint = 0.0
        self.output = Path(args.output).resolve()
        self.output.mkdir(parents=True, exist_ok=False)
        self.counts = Counter()
        self.completed = []
        self.current_session = None
        self.assets = {}
        self.factor_captures = {}
        self.factor_groups = {}
        self.series = {}
        self.raw_captures = {}
        self.raw_indexes = {}
        self.parent_checks = set()
        self.ledger = (self.output / 'row-proof.jsonl').open('wb')
        self.ledger_hash = hashlib.sha256()
        self.failures = (self.output / 'failures.jsonl').open('wb')
        self.cutoff = stamp(args.cutoff)
        self.inventory_path = Path(args.inventory).resolve()
        inventory_bytes = self.inventory_path.read_bytes()
        self.inventory_hash = sha(inventory_bytes)
        self.bindings = json.loads(inventory_bytes)['asset_bindings']
        db = Path(args.db).resolve()
        self.con = sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)
        self.con.row_factory = sqlite3.Row
        self.con.execute('PRAGMA query_only=ON')
        stored = self.con.execute('SELECT payload,content_hash FROM documents WHERE namespace=? AND document_id=?',
                                  ('history_artifact_manifests', args.history_ref)).fetchone()
        if stored is None or sha(stored['payload'].encode()) != stored['content_hash'] or stored['content_hash'] != args.history_ref.removeprefix('history_'):
            raise ValueError('MANIFEST_MISSING_OR_HASH_MISMATCH')
        self.manifest_hash = stored['content_hash']
        self.manifest = json.loads(stored['payload'])
        if self.manifest['artifact_version'] != 'CONTENT_ADDRESSED_HISTORY_ARTIFACT_V1':
            raise Unverified('UNSUPPORTED_MANIFEST_SCHEMA')
        refs = [r['receipt_id'] for r in self.manifest['receipts']]
        if len(refs) != len(set(refs)):
            raise ValueError('DUPLICATE_MANIFEST_RECEIPT')
        self.parents = {r['receipt_id']: r for r in self.manifest['receipts']}
        (self.output / 'manifest.json').write_bytes(stored['payload'].encode())
        self.parts = sorted(self.manifest['partitions'], key=lambda p: p['session'])
        if len({p['session'] for p in self.parts}) != len(self.parts):
            raise ValueError('DUPLICATE_MANIFEST_SESSION')
        if args.partition_limit:
            self.parts = self.parts[:args.partition_limit]
        self.checkpoint(force=True)

    def checkpoint(self, force=False):
        elapsed = time.monotonic() - self.started
        if force or elapsed - self.last_checkpoint >= 10:
            self.ledger.flush()
            self.failures.flush()
            state = {'status': 'RUNNING', 'pid': os.getpid(), 'command': sys.argv,
                     'started_at': self.started_at, 'checkpoint_at': datetime.now(timezone.utc).isoformat(),
                     'elapsed_seconds': elapsed, 'budget_seconds': self.args.budget_seconds,
                     'history_ref': self.args.history_ref, 'manifest_sha256': self.manifest_hash,
                     'inventory_sha256': self.inventory_hash, 'script_sha256': sha(Path(__file__).read_bytes()),
                     'current_session': self.current_session, 'completed_partitions': self.completed,
                     'counts': dict(self.counts), 'row_ledger_sha256_so_far': self.ledger_hash.hexdigest(),
                     'resume_policy': 'NO_IMPLICIT_RESUME_USE_NEW_OUTPUT_DIRECTORY'}
            tmp = self.output / 'checkpoint.tmp'
            tmp.write_bytes(canonical(state))
            tmp.replace(self.output / 'checkpoint.json')
            print(json.dumps({k: state[k] for k in ('status', 'current_session', 'elapsed_seconds', 'counts')}), flush=True)
            self.last_checkpoint = elapsed
        if elapsed > self.args.budget_seconds:
            raise TimeoutError('BASE_BASIS_PROOF_BUDGET_EXCEEDED')

    def capture(self, ref):
        parent = self.parents.get(ref)
        if parent is None:
            raise Unverified('PARENT_RECEIPT_MISSING:' + ref)
        cache = self.factor_captures if parent['dataset'] == 'adjustment_factors' else self.raw_captures
        if ref in cache:
            return cache[ref]
        bindings = self.bindings.get(ref)
        if not bindings:
            raise Unverified('LOCAL_ASSET_MISSING:' + ref)
        identities = {(b['file_sha256'], b['result_canonical_sha256']) for b in bindings}
        if len(identities) != 1:
            raise Unverified('MULTIPLE_ASSET_BYTE_IDENTITIES:' + ref)
        binding = bindings[0]
        raw = Path(binding['path']).read_bytes()
        if sha(raw) != binding['file_sha256']:
            raise ValueError('ASSET_FILE_HASH_MISMATCH:' + ref)
        value = json.loads(raw)
        if sha(canonical(value)) != binding['result_canonical_sha256'] or value['request_receipt_id'] != ref:
            raise ValueError('ASSET_RESULT_IDENTITY_MISMATCH:' + ref)
        receipts = [r for r in value['receipts'] if r['receipt_id'] == ref]
        if len(receipts) != 1:
            raise ValueError('ASSET_UNIQUE_RECEIPT_REQUIRED:' + ref)
        original = receipts[0]
        for field, other in (('provider', 'provider'), ('dataset', 'endpoint'), ('field_version', 'field_version'), ('status', 'status')):
            if parent[field] != value[other]:
                raise ValueError('ASSET_PARENT_' + field.upper() + '_MISMATCH:' + ref)
        if parent['content_hash'] != original['content_hash'] or parent['row_count'] != len(value['rows']) or parent['pagination_complete'] != value['pagination_complete']:
            raise ValueError('ASSET_PARENT_CONTENT_OR_COVERAGE_MISMATCH:' + ref)
        # Normalized source_published_at is the actual first_seen_at; no old
        # publication timestamp is invented by this proof or by the adapter.
        for field, original_field in (('source_published_at', 'first_seen_at'), ('first_seen_at', 'first_seen_at'), ('ingested_at', 'ingested_at')):
            if stamp(parent[field]) != stamp(original[original_field]) or stamp(parent[field]) > self.cutoff:
                raise ValueError('ASSET_PARENT_CLOCK_MISMATCH_OR_FUTURE:' + ref)
        self.assets[ref] = {'binding': binding, 'manifest_receipt': parent, 'original_receipt': original,
                            'capture_status': value['status'], 'capture_parameters': value['parameters'],
                            'capture_coverage': value.get('coverage'), 'result_canonical_sha256': binding['result_canonical_sha256']}
        cache[ref] = value
        if parent['dataset'] == 'adjustment_factors':
            groups = defaultdict(list)
            for row in value['rows']:
                groups[instrument(row['code'])].append(row)
            self.factor_groups[ref] = groups
        self.checkpoint()
        return value

    def factor_fragment(self, ref, iid):
        value = self.capture(ref)
        if value['provider'] != 'baostock' or value['endpoint'] != 'adjustment_factors':
            raise Unverified('UNSUPPORTED_FACTOR_PROVIDER_OR_DATASET')
        rows = self.factor_groups[ref].get(iid, [])
        if iid not in {instrument(code) for code in value['parameters']['codes']}:
            raise ValueError('FACTOR_CODE_NOT_REQUESTED')
        if value['status'] == 'COMPLETE' and value['pagination_complete'] is True:
            proof = {'receipt_status': 'COMPLETE', 'scope': 'COMPLETE_REQUEST_WINDOW'}
        elif value['status'] == 'PARTIAL':
            statuses = [r for r in value.get('coverage', {}).get('security_statuses', ()) if instrument(r['code']) == iid]
            if len(statuses) != 1:
                raise Unverified('UNIQUE_SECURITY_FRAGMENT_STATUS_REQUIRED')
            status = statuses[0]
            captured_at = stamp(status['captured_at'])
            if status['status'] != 'COMPLETE' or status['cursor_exhausted'] is not True or status['row_count'] != len(rows) or captured_at > self.cutoff:
                raise Unverified('FACTOR_SECURITY_FRAGMENT_INCOMPLETE_OR_FUTURE')
            # A complete fragment cannot become available before it was
            # captured. Row visibility is already bounded by parent ingestion.
            if captured_at > stamp(self.parents[ref]['ingested_at']):
                raise Unverified('FACTOR_FRAGMENT_CAPTURE_AFTER_PARENT_INGESTION')
            proof = {'receipt_status': 'PARTIAL', 'scope': 'ONLY_THIS_COMPLETE_SECURITY_FRAGMENT', 'security_status': status}
        else:
            raise Unverified('FACTOR_CAPTURE_NOT_COMPLETE')
        return value, rows, proof

    def factor_series(self, iid, refs):
        # Source IDs are content addresses, never temporal revision authority.
        # All receipts were validated against their bound local captures by
        # factor_fragment/capture before this chronology is used.
        key = (iid, tuple(sorted(refs)))
        if key in self.series:
            return self.series[key]
        snapshots = []
        for ref in refs:
            capture, rows, proof = self.factor_fragment(ref, iid)
            params = capture['parameters']
            start, end = params.get('start_date'), params.get('end_date')
            if not start or not end:
                raise Unverified('FACTOR_COMPLETE_WINDOW_BOUNDS_MISSING')
            ingested = stamp(self.parents[ref]['ingested_at']).astimezone(timezone.utc)
            snapshots.append((ingested, ref, start, end, rows, proof))
        snapshots.sort(key=lambda value: value[0])
        if any(before[0] == after[0] for before, after in zip(snapshots, snapshots[1:])):
            raise Unverified('FACTOR_CAPTURE_CHRONOLOGY_AMBIGUOUS_SAME_INSTANT')
        factors, windows, proofs = {}, [], {}
        covered_to = None
        for ingested, ref, start, end, rows, proof in snapshots:
            incoming_days = {row['dividOperateDate'] for row in rows}
            if any(start <= day <= end and day not in incoming_days for day in factors):
                raise Unverified('FACTOR_COMPLETE_WINDOW_DELETION_REQUIRES_REVIEW')
            for row in rows:
                day = row['dividOperateDate']
                factor = number(row.get('backAdjustFactor'))
                if factor is None:
                    raise ValueError('INVALID_FACTOR_VALUE')
                if day in factors and factors[day] != factor:
                    raise ValueError('CONFLICTING_FACTOR_SERIES')
                if day not in factors and covered_to is not None and day <= covered_to:
                    raise Unverified('FACTOR_HISTORICAL_INSERTION_REQUIRES_REVIEW')
                factors[day] = factor
            covered_to = max(covered_to or end, end)
            windows.append((start, end))
            # Preserve original receipt clocks; UTC normalization ranks only.
            proofs[ref] = {**proof, 'capture_ingested_at': self.parents[ref]['ingested_at']}
        if not factors:
            raise Unverified('FACTOR_ANCHOR_MISSING')
        anchor = min(factors)
        basis = 'BAOSTOCK_HFQ_RAW_X_BACK_FACTOR_R03_V1:' + iid + ':' + sha(canonical([anchor, str(factors[anchor])]))[:20]
        result = (factors, sorted(factors), windows, proofs, anchor, basis)
        self.series[key] = result
        return result

    def row(self, stored, day):
        row = json.loads(stored['payload'])
        digest = stored['row_hash']
        if sha(stored['payload'].encode()) != digest:
            raise ValueError('ROW_PAYLOAD_HASH_MISMATCH')
        if stored['member_iid'] != stored['instrument_id'] or stored['member_session'] != stored['session'] or stored['member_kind'] != stored['kind']:
            raise ValueError('ROW_MEMBER_METADATA_MISMATCH')
        if row['instrument_id'] != stored['instrument_id'] or row['session'] != day or stored['session'] != day:
            raise ValueError('ROW_PAYLOAD_METADATA_MISMATCH')
        refs = row['source_refs']
        if not refs or len(refs) != len(set(refs)):
            raise ValueError('ROW_SOURCE_REFS_MISSING_OR_DUPLICATE')
        parents = [self.parents.get(ref) for ref in refs]
        if None in parents:
            raise Unverified('ROW_PARENT_RECEIPT_MISSING')
        raw_refs = [ref for ref, parent in zip(refs, parents) if parent['dataset'] == 'market_daily']
        factor_refs = tuple(sorted(ref for ref, parent in zip(refs, parents) if parent['dataset'] == 'adjustment_factors'))
        if len(raw_refs) != 1 or len(refs) != len(raw_refs) + len(factor_refs):
            raise Unverified('UNSUPPORTED_RAW_OR_COMPOSITE_PARENT_SET')
        rid = raw_refs[0]
        capture = self.capture(rid)
        if capture['provider'] != 'baostock' or capture['status'] != 'COMPLETE' or capture['pagination_complete'] is not True:
            raise Unverified('RAW_CAPTURE_NOT_SUPPORTED_COMPLETE_BAOSTOCK')
        if rid not in self.raw_indexes:
            index = {}
            for raw in capture['rows']:
                key = (instrument(raw['code']), raw['date'])
                if key in index:
                    raise ValueError('DUPLICATE_RAW_CAPTURE_KEY')
                index[key] = raw
            self.raw_indexes[rid] = index
        raw = self.raw_indexes[rid].get((row['instrument_id'], day))
        if raw is None:
            raise Unverified('RAW_CAPTURE_MEMBER_MISSING')
        if str(raw.get('adjustflag')) != '3':
            raise Unverified('RAW_CAPTURE_ADJUSTFLAG_NOT_UNADJUSTED_3')
        for field, parentfield in (('open', 'open'), ('high', 'high'), ('low', 'low'), ('close', 'close'), ('reference_prev_close', 'preclose'), ('volume_shares', 'volume'), ('amount_cny', 'amount')):
            expected = number(raw.get(parentfield), field not in ('volume_shares', 'amount_cny'))
            actual = Decimal(str(row[field])) if row.get(field) is not None else None
            if expected != actual:
                raise ValueError('RAW_' + field.upper() + '_MISMATCH')
        turnover = number(raw.get('turn'), False)
        expected = float(turnover / 100) if turnover is not None and turnover <= 1000 else None
        if expected != row.get('turnover_fraction'):
            raise ValueError('RAW_TURNOVER_MISMATCH')
        for ref in factor_refs:
            self.capture(ref)
        for field in ('source_published_at', 'first_seen_at', 'ingested_at'):
            values = [stamp(parent[field]) for parent in parents]
            if any(v > self.cutoff for v in values) or stamp(row[field]) != max(values):
                raise ValueError('ROW_DERIVED_CLOCK_MISMATCH_OR_FUTURE')
        if stamp(row['event_time']).date().isoformat() != day:
            raise ValueError('ROW_EVENT_SESSION_MISMATCH')
        result = {'row_hash': digest, 'instrument_id': row['instrument_id'], 'session': day,
                  'source_refs': refs, 'clocks': {f: row[f] for f in ('event_time', 'source_published_at', 'first_seen_at', 'ingested_at')},
                  'raw_proof': 'EXACT_7_NUMERIC_FIELDS_AND_TURNOVER_FROM_BOUND_CAPTURE'}
        comparable = [row.get('comparable_' + field) for field in ('open', 'high', 'low', 'close')]
        if all(value is None for value in comparable) and row.get('comparable_basis') is None:
            result['status'] = 'VERIFIED_RAW_ONLY_COMPARABILITY_NOT_ASSERTED'
            self.counts['verified_raw_only_rows'] += 1
        else:
            if any(value is None for value in comparable) or not row.get('comparable_basis'):
                raise Unverified('PARTIAL_COMPARABLE_PAYLOAD')
            factors, ordered, windows, proofs, anchor, basis = self.factor_series(row['instrument_id'], factor_refs)
            if not any(start <= anchor <= day <= end for start, end in windows):
                raise Unverified('FACTOR_COMPLETE_ANCHORED_WINDOW_NOT_PROVEN')
            effective = max((value for value in ordered if value <= day), default=None)
            if effective is None:
                raise Unverified('APPLICABLE_FACTOR_MISSING')
            factor = factors[effective]
            for field in ('open', 'high', 'low', 'close'):
                if row.get(field) is None or Decimal(row['comparable_' + field]) != Decimal(row[field]) * factor:
                    raise ValueError('COMPARABLE_' + field.upper() + '_MULTIPLICATION_MISMATCH')
            if row['comparable_basis'] != basis:
                raise ValueError('EXACT_FACTOR_ANCHOR_BASIS_MISMATCH')
            result.update(status='VERIFIED_RAW_AND_COMPOSITE_DECIMAL_ARITHMETIC', factor=str(factor),
                          factor_effective_date=effective, basis=basis, factor_parent_completeness=proofs)
            self.counts['verified_composite_rows'] += 1
        return result

    def run(self):
        fatal = None
        try:
            for part in self.parts:
                self.current_session = part['session']
                self.raw_captures.clear()
                self.raw_indexes.clear()
                digest = hashlib.sha256()
                rows = bars = 0
                before = dict(self.counts)
                cursor = self.con.execute('SELECT m.row_hash,m.kind AS member_kind,m.instrument_id AS member_iid,m.session AS member_session,r.kind,r.instrument_id,r.session,r.payload FROM history_partition_members m LEFT JOIN history_evidence_rows r ON r.row_hash=m.row_hash WHERE m.partition_hash=? ORDER BY m.kind,m.instrument_id,m.row_hash', (part['partition_hash'],))
                for stored in cursor:
                    digest.update(canonical([stored['row_hash'], stored['member_kind'], stored['member_iid'], stored['member_session']]) + b'\n')
                    rows += 1
                    if stored['payload'] is None:
                        raise ValueError('MISSING_REFERENCED_ROW')
                    if stored['member_kind'] != 'bar':
                        self.counts['non_bar_members_descriptor_checked_only'] += 1
                        continue
                    bars += 1
                    self.counts['bar_members_examined'] += 1
                    try:
                        result = self.row(stored, part['session'])
                    except (ValueError, KeyError, TypeError, InvalidOperation) as exc:
                        status = 'NOT_VERIFIED' if isinstance(exc, Unverified) else 'FAIL'
                        self.counts[status.lower() + '_rows'] += 1
                        result = {'status': status, 'row_hash': stored['row_hash'], 'session': part['session'],
                                  'instrument_id': stored['member_iid'], 'error': str(exc)}
                        self.failures.write(canonical(result) + b'\n')
                    raw = canonical(result) + b'\n'
                    self.ledger.write(raw)
                    self.ledger_hash.update(raw)
                    if bars % 256 == 0:
                        self.checkpoint()
                if digest.hexdigest() != part['partition_hash'] or rows != part['row_count'] or bars != part['bar_count']:
                    raise ValueError('PARTITION_DESCRIPTOR_HASH_OR_COUNT_MISMATCH')
                self.completed.append({'session': part['session'], 'partition_hash': part['partition_hash'],
                                       'rows': rows, 'bars': bars,
                                       'counts': {k: v - before.get(k, 0) for k, v in self.counts.items()}})
                self.checkpoint(force=True)
        except Exception as exc:
            fatal = {'type': type(exc).__name__, 'error': str(exc)}
        self.ledger.close()
        self.failures.close()
        self.con.close()
        incomplete = len(self.completed) != len(self.manifest['partitions'])
        status = 'FAIL' if fatal or self.counts['fail_rows'] else 'NOT_VERIFIED' if self.counts['not_verified_rows'] else 'PARTIAL_SCOPE_PASS' if incomplete else 'PASS'
        report = {'proof_version': 'R16_R1_INDEPENDENT_ALL_HISTORY_BAR_MEMBERS_V1', 'status': status,
                  'scope': 'ALL_IMMUTABLE_BAR_MEMBERS_NOT_ONLY_CANONICAL_LATEST', 'history_ref': self.args.history_ref,
                  'db': str(Path(self.args.db).resolve()), 'source_access': 'SQLITE_URI_MODE_RO_QUERY_ONLY',
                  'inventory': str(self.inventory_path), 'inventory_sha256': self.inventory_hash,
                  'manifest_sha256': self.manifest_hash, 'script_sha256': sha(Path(__file__).read_bytes()),
                  'command': sys.argv, 'pid': os.getpid(), 'cutoff': self.args.cutoff,
                  'started_at': self.started_at, 'finished_at': datetime.now(timezone.utc).isoformat(),
                  'elapsed_seconds': time.monotonic() - self.started, 'budget_seconds': self.args.budget_seconds,
                  'expected_partition_count': len(self.manifest['partitions']), 'checked_partition_count': len(self.completed),
                  'expected_bar_count': sum(p['bar_count'] for p in self.manifest['partitions']),
                  'counts': dict(self.counts), 'partitions': self.completed, 'fatal_error': fatal,
                  'assets': self.assets, 'row_proof_sha256': self.ledger_hash.hexdigest(),
                  'failures_sha256': file_sha(self.output / 'failures.jsonl'),
                  'limits': ['NO_PROSPECTIVE_AS_OF_ORIGINAL_DATE_CLAIM', 'RAW_ONLY_COMPARABILITY_NOT_ASSERTED',
                             'NO_SECURITY_RECORD_NUMERIC_OR_READINESS_PROOF', 'NO_PROVIDER_AUTHENTICITY_BEYOND_ORIGINAL_LOCAL_CAPTURE_BINDING',
                             'PARTITION_LIMIT_IS_NOT_FULL_BASE_VERIFICATION' if incomplete else 'ALL_REFERENCED_BAR_MEMBERS_EXAMINED']}
        (self.output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        (self.output / 'checkpoint.json').write_bytes(canonical({'status': status, 'result_sha256': file_sha(self.output / 'result.json'),
                                                              'completed_partitions': len(self.completed), 'elapsed_seconds': report['elapsed_seconds']}))
        print(json.dumps({k: report[k] for k in ('status', 'counts', 'elapsed_seconds', 'fatal_error')}), flush=True)
        return 0 if status in {'PASS', 'PARTIAL_SCOPE_PASS'} else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', required=True)
    parser.add_argument('--history-ref', required=True)
    parser.add_argument('--inventory', required=True)
    parser.add_argument('--cutoff', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--budget-seconds', type=float, default=1200)
    parser.add_argument('--partition-limit', type=int)
    args = parser.parse_args()
    raise SystemExit(Proof(args).run())
