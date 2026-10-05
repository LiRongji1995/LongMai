"""Read-only transport of Go-selected evidence; no database or SDK execution.

Go authenticates original history bytes/projections and selects revisions with
its ordinary resolver. Transport hashes bind that proof across the process
boundary; they do not create publication, completeness or factor authority.
"""
import gzip
import hashlib
import io
import json
import re
from bisect import bisect_right
from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction

CONTRACT = 'GO_VERIFIED_REFRESH_HISTORY_V1'
HASH = re.compile(r'^[0-9a-f]{64}$')


class HistoryBridgeBlocked(ValueError):
    pass


def strict_json(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise HistoryBridgeBlocked('GO_HISTORY_DUPLICATE_TRANSPORT_KEY')
            result[key] = value
        return result
    def invalid(value):
        raise HistoryBridgeBlocked('GO_HISTORY_NONFINITE_TRANSPORT_VALUE')
    return json.loads(raw, object_pairs_hook=pairs, parse_float=Decimal, parse_constant=invalid)


class VerifiedGoHistory:
    def __init__(self, root, request, checkpoint=lambda: None):
        self.root, self.checkpoint = root.resolve(), checkpoint
        path = self._path(request['manifest_file'])
        raw = path.read_bytes()
        if len(raw) > 32 << 20 or hashlib.sha256(raw).hexdigest() != request['manifest_sha256']:
            raise HistoryBridgeBlocked('GO_HISTORY_MANIFEST_TRANSPORT_MISMATCH')
        self.proof = strict_json(raw)
        proof = self.proof
        if (proof.get('contract') != CONTRACT or not HASH.fullmatch(proof.get('input_sha256', ''))
                or not HASH.fullmatch(proof.get('history_artifact_hash', ''))
                or proof.get('history_artifact_ref') != 'history_' + proof['history_artifact_hash']):
            raise HistoryBridgeBlocked('GO_HISTORY_PROOF_IDENTITY_INVALID')
        self.verified_at = datetime.fromisoformat(proof['verified_at'].replace('Z', '+00:00'))
        if self.verified_at.tzinfo is None:
            raise HistoryBridgeBlocked('GO_HISTORY_VERIFICATION_CLOCK_INVALID')
        self.files = {}
        for descriptor in proof['files']:
            iid = descriptor['instrument_id']
            if iid in self.files:
                raise HistoryBridgeBlocked('GO_HISTORY_DUPLICATE_INSTRUMENT_PROOF')
            self.files[iid] = descriptor
        self.current_ids, self.required, self.new_rows = (), (), ()
        self.skipped_raw_sessions = set()
        self.numeric_proof = None
        self.request_identity = request

    def _path(self, relative):
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root) or not path.is_file():
            raise HistoryBridgeBlocked('GO_HISTORY_TRANSPORT_PATH_INVALID')
        return path

    def _rows(self, iid):
        descriptor = self.files.get(iid)
        if descriptor is None:
            return
        self.checkpoint()
        raw = self._path(descriptor['file']).read_bytes()
        if len(raw) > 16 << 20 or len(raw) != descriptor['bytes'] or hashlib.sha256(raw).hexdigest() != descriptor['sha256']:
            raise HistoryBridgeBlocked('GO_HISTORY_ROWS_TRANSPORT_MISMATCH')
        count, sessions = 0, set()
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as handle:
            for line in handle:
                if len(line) > 1 << 20:
                    raise HistoryBridgeBlocked('GO_HISTORY_ROW_TRANSPORT_LIMIT')
                value = strict_json(line)
                row, identity = value['row'], value['identity']
                if row.get('instrument_id') != iid or row.get('session') in sessions or row.get('session') not in self.proof['sessions']:
                    raise HistoryBridgeBlocked('GO_HISTORY_SELECTED_MEMBER_IDENTITY_MISMATCH')
                origin = identity.get('origin')
                parent = self.proof['history_artifact_hash'] if origin == 'HISTORY_ROW' else self.proof['input_sha256']
                if (origin not in {'HISTORY_ROW', 'FROZEN_INPUT'} or identity.get('parent_hash') != parent
                        or not HASH.fullmatch(value.get('projection_sha256', ''))
                        or origin == 'HISTORY_ROW' and not HASH.fullmatch(identity.get('raw_sha256', ''))
                        or origin == 'FROZEN_INPUT' and not re.fullmatch(r'/input/bars/[0-9]+', identity.get('locator', ''))):
                    raise HistoryBridgeBlocked('GO_HISTORY_SELECTED_ROW_BINDING_INVALID')
                for field in ('event_time', 'source_published_at', 'first_seen_at', 'ingested_at'):
                    clock = datetime.fromisoformat(row[field].replace('Z', '+00:00'))
                    if clock.tzinfo is None or clock > self.verified_at:
                        raise HistoryBridgeBlocked('GO_HISTORY_SELECTED_ROW_CLOCK_INVALID')
                sessions.add(row['session']); count += 1
                if count > 315:
                    raise HistoryBridgeBlocked('GO_HISTORY_SELECTED_WINDOW_LIMIT')
                yield row
        if count != descriptor['rows'] or not set(descriptor['verified_raw_sessions']) <= sessions:
            raise HistoryBridgeBlocked('GO_HISTORY_SELECTED_WINDOW_INCOMPLETE')

    def prepare(self, ids, sessions, day, results):
        # Both current exchanges and the full actual provider calendar still
        # come through the original fixed capture DAG, never from retained data.
        fresh = []
        for result in results:
            if result.get('endpoint') not in {'mainboard_sse', 'master_szse', 'calendar'}:
                continue
            rid = result.get('request_receipt_id')
            source = next((r for r in result.get('receipts', ()) if r.get('receipt_id') == rid), None)
            if result.get('status') != 'COMPLETE' or result.get('pagination_complete') is not True or source is None:
                continue
            if not HASH.fullmatch(source.get('content_hash', '')) or not result.get('provider') or not result.get('field_version'):
                raise HistoryBridgeBlocked('GO_HISTORY_FRESH_INITIAL_PARENT_IDENTITY_INVALID')
            inspected_at = datetime.now(self.verified_at.tzinfo)
            for field in ('first_seen_at', 'ingested_at'):
                stamp = datetime.fromisoformat(source[field].replace('Z', '+00:00'))
                if stamp.tzinfo is None or not self.verified_at < stamp <= inspected_at:
                    raise HistoryBridgeBlocked('GO_HISTORY_FRESH_INITIAL_PARENT_NOT_ACTUALLY_FRESH')
            fresh.append(result)
        masters = {r['endpoint'] for r in fresh if r['endpoint'] in {'mainboard_sse', 'master_szse'}}
        if masters != {'mainboard_sse', 'master_szse'}:
            raise HistoryBridgeBlocked('GO_HISTORY_FRESH_DIRECTORY_NOT_COMPLETE')
        if not ids:
            raise HistoryBridgeBlocked('GO_HISTORY_FRESH_DIRECTORY_EMPTY_OR_UNRESOLVED')
        if len(sessions) < 315:
            raise HistoryBridgeBlocked('GO_HISTORY_FRESH_CALENDAR_WINDOW_INCOMPLETE')
        wanted = tuple(str(s) for s in sessions[-315:])
        calendar = {str(row.get('calendar_date', row.get('calendarDate', row.get('session'))))
                    for result in fresh if result['endpoint'] == 'calendar' for row in result.get('rows', ())
                    if str(row.get('is_trading_day', row.get('isTradingDay', '1'))) == '1'}
        if wanted[-1] != str(day) or not set(wanted) <= calendar:
            raise HistoryBridgeBlocked('GO_HISTORY_FRESH_CALENDAR_PARENT_OR_WINDOW_NOT_PROVEN')
        before = self.proof['observation_date']
        old = {s for s in self.proof['sessions'] if wanted[0] <= s <= before}
        incoming = {s for s in wanted if s <= before}
        if old != incoming:
            raise HistoryBridgeBlocked('GO_HISTORY_CALENDAR_COMPATIBILITY_NOT_PROVEN')
        if date.fromisoformat(before) >= day or set(ids) - self.files.keys():
            raise HistoryBridgeBlocked('GO_HISTORY_CURRENT_SCOPE_NOT_EXPORTED')
        self.current_ids, self.required = tuple(ids), wanted
        # Hash and row counts are checked before any request can be skipped.
        for iid in ids:
            for _ in self._rows(iid):
                pass

    def covered_sessions(self, iid):
        return set(self.files.get(iid, {}).get('verified_raw_sessions', ())) & set(self.required)

    def raw_request_needed(self, session, ids):
        day = str(session)
        if not self.required or day == self.required[-1] or any(day not in self.covered_sessions(iid) for iid in ids):
            return True
        self.skipped_raw_sessions.add(day)
        return False

    def manifest(self, ref, expected_hash=None):
        if ref != self.proof['history_artifact_ref'] or expected_hash != self.proof['history_artifact_hash']:
            raise HistoryBridgeBlocked('GO_HISTORY_CONSUMER_PARENT_MISMATCH')
        return {'receipts': self.proof['receipts'], 'partitions': [],
                'proof_contract': CONTRACT, 'partition_integrity': 'VERIFIED_BY_GO_ORIGINAL_ADAPTER',
                'scope': 'SELECTED_315_SESSION_ROWS_ONLY_NOT_HISTORY_DATABASE'}

    def iter_rows(self, ref, kind, *, instrument_id=None, start=None, end=None, expected_hash=None):
        self.manifest(ref, expected_hash)
        if kind != 'bar':
            raise HistoryBridgeBlocked('GO_HISTORY_SECURITY_REUSE_NOT_IMPLEMENTED')
        for iid in ([instrument_id] if instrument_id is not None else sorted(self.files)):
            for row in self._rows(iid):
                if (start is None or row['session'] >= str(start)) and (end is None or row['session'] <= str(end)):
                    # Existing InstrumentBars accepts its dict payload port.
                    # A DailyBar round-trip would cast turnover to binary float,
                    # changing authenticated literals near a rule threshold.
                    yield row

    def selected_rows(self, sessions, eligible_ids):
        wanted = set(map(str, sessions))
        for iid in sorted(eligible_ids):
            groups = {}
            for row in self._rows(iid):
                if row['session'] in wanted:
                    groups[row['session']] = row
            for row in self.new_rows:
                if row['session'] not in wanted or row['instrument_id'] != iid:
                    continue
                def clocks(value):
                    return tuple(datetime.fromisoformat(value[field].replace('Z', '+00:00'))
                                 for field in ('source_published_at', 'ingested_at', 'first_seen_at'))
                previous = groups.get(row['session'])
                if previous is None or clocks(row) > clocks(previous):
                    groups[row['session']] = row
                elif clocks(row) == clocks(previous) and row != previous:
                    raise HistoryBridgeBlocked('GO_HISTORY_CONSUMER_REVISION_CONFLICT')
            yield from groups.values()

    def normalize(self, results, observation_date, now, sessions):
        from stockfish_core.yaogu.provider_normalization import normalize_capture_with_report
        from stockfish_core.yaogu.history_base_selection import validate_price_schema_compatibility
        from stockfish_core.yaogu.provider_comparability import comparable_rows, factor_series, positive
        from stockfish_core.yaogu.provider_normalization import _iid
        bundle, report = normalize_capture_with_report(results, observation_date, now, sessions)
        schemas = validate_price_schema_compatibility(self.proof['receipts'], bundle.receipts)
        receipts = {r['receipt_id']: r for r in
                    (r.model_dump(mode='json') if hasattr(r, 'model_dump') else r for r in bundle.receipts)}
        captures = {}
        for result in results:
            if result.get('endpoint') != 'adjustment_factors':
                continue
            rid = result.get('request_receipt_id')
            parent = receipts.get(rid)
            source = next((r for r in result.get('receipts', ()) if r.get('receipt_id') == rid), None)
            # The normalizer deliberately discards results without a genuine
            # source parent. Such a COMPLETE label cannot prove fresh factors.
            if parent is None or source is None:
                continue
            parameters = result.get('parameters', {})
            normalized_parameters = {k: v if isinstance(v, (str, int, float, bool)) or v is None else str(v)
                                     for k, v in parameters.items()}
            if (parent.get('provider') != result.get('provider') or parent.get('provider') != 'baostock'
                    or parent.get('dataset') != 'adjustment_factors'
                    or parent.get('field_version') != result.get('field_version')
                    or not parent.get('field_version') or not HASH.fullmatch(source.get('content_hash', ''))
                    or parent.get('content_hash') != source.get('content_hash')
                    or parent.get('parameters') != normalized_parameters
                    or parent.get('row_count') != len(result.get('rows', ()))
                    or parent.get('status') != result.get('status')
                    or parent.get('pagination_complete') != result.get('pagination_complete')):
                raise HistoryBridgeBlocked('GO_HISTORY_FRESH_FACTOR_PARENT_IDENTITY_MISMATCH')
            for field in ('first_seen_at', 'ingested_at'):
                stamp = datetime.fromisoformat(source[field].replace('Z', '+00:00'))
                actual = datetime.fromisoformat(str(parent[field]).replace('Z', '+00:00'))
                if stamp.tzinfo is None or stamp != actual or not self.verified_at < stamp <= now:
                    raise HistoryBridgeBlocked('GO_HISTORY_FRESH_FACTOR_PARENT_NOT_ACTUALLY_FRESH')
            published = datetime.fromisoformat(str(parent['source_published_at']).replace('Z', '+00:00'))
            if published != datetime.fromisoformat(source['first_seen_at'].replace('Z', '+00:00')):
                raise HistoryBridgeBlocked('GO_HISTORY_FRESH_FACTOR_PUBLICATION_CLOCK_MISMATCH')
            codes = parameters.get('codes')
            if not isinstance(codes, (tuple, list)) or not codes or any(_iid(code) is None for code in codes):
                raise HistoryBridgeBlocked('GO_HISTORY_FRESH_FACTOR_REQUEST_SCOPE_INVALID')
            requested = {_iid(code) for code in codes}
            if parameters.get('end_date') != str(observation_date):
                raise HistoryBridgeBlocked('GO_HISTORY_FRESH_FACTOR_REQUEST_WINDOW_INVALID')
            grouped = {}
            for row in result.get('rows', ()):
                if _iid(row.get('code')) not in requested:
                    raise HistoryBridgeBlocked('GO_HISTORY_FRESH_FACTOR_RETURNED_SCOPE_MISMATCH')
                factor_day = str(row.get('dividOperateDate', ''))
                if factor_day <= str(observation_date):
                    grouped.setdefault(_iid(row.get('code')), []).append(row)
            complete = set()
            if result.get('status') == 'COMPLETE' and result.get('pagination_complete'):
                complete = requested
            elif result.get('status') == 'PARTIAL':
                complete = {_iid(s.get('code')) for s in result.get('coverage', {}).get('security_statuses', ())
                            if _iid(s.get('code')) in requested
                            and s.get('status') == 'COMPLETE' and s.get('cursor_exhausted') is True
                            and s.get('row_count') == len(grouped.get(_iid(s.get('code')), ()))}
            for iid in complete:
                if iid:
                    p = result['parameters']
                    captures.setdefault(iid, []).append((grouped.get(iid, []), result['request_receipt_id'],
                                                       p.get('end_date'), p.get('start_date')))
        checked, retained_unknown = 0, 0
        for iid in self.current_ids:
            old = [r for r in self._rows(iid) if r['session'] in self.required]
            raw = [(date.fromisoformat(r['session']), (r, 'GO_RETAINED_RAW_REFERENCE')) for r in old]
            converted, status = comparable_rows(iid, raw, {}, captures.get(iid, ()))
            series, _, conflict = factor_series(captures.get(iid, ()))
            days = [day for day, _ in series]
            for row in old:
                names = ('open', 'high', 'low', 'close')
                previous = [row.get('comparable_' + name) for name in names]
                fresh = converted.get(date.fromisoformat(row['session']))
                if conflict or fresh is None:
                    raise HistoryBridgeBlocked('GO_HISTORY_FRESH_FACTOR_PROOF_INCOMPLETE:' + iid + ':' + status)
                factor = series[bisect_right(days, row['session']) - 1][1]
                exact = {name: Fraction(positive(row.get(name))) * Fraction(factor) for name in names}
                if all(value is None for value in previous):
                    retained_unknown += 1
                    continue
                if any(positive(value) is None for value in previous):
                    raise HistoryBridgeBlocked('GO_HISTORY_FRESH_FACTOR_PROOF_INCOMPLETE:' + iid + ':' + status)
                expected = fresh[0]
                if row.get('comparable_basis') != expected['comparable_basis'] or any(
                        Fraction(positive(row.get('comparable_' + name))) != exact[name] for name in names):
                    raise HistoryBridgeBlocked('GO_HISTORY_FACTOR_REVISION_OR_BASIS_CHANGE_REQUIRES_REVIEW:' + iid)
                checked += 1
        # The frozen normalizer is unchanged. Refuse a newly derived price if
        # its Decimal context rounded RAW * factor instead of silently joining
        # that value to an exact historical series.
        for bar in bundle.bars:
            row = bar.model_dump(mode='json')
            if all(row.get('comparable_' + name) is None for name in ('open', 'high', 'low', 'close')):
                continue
            iid = row['instrument_id']
            series, _, conflict = factor_series(captures.get(iid, ()))
            position = bisect_right([day for day, _ in series], str(row['session'])) - 1
            if conflict or position < 0:
                raise HistoryBridgeBlocked('GO_HISTORY_FRESH_FACTOR_PROOF_INCOMPLETE:' + iid)
            factor = series[position][1]
            for name in ('open', 'high', 'low', 'close'):
                raw_value, comparable = positive(row.get(name)), positive(row.get('comparable_' + name))
                if raw_value is None or comparable is None or Fraction(raw_value) * Fraction(factor) != Fraction(comparable):
                    raise HistoryBridgeBlocked('GO_HISTORY_FRESH_FACTOR_ARITHMETIC_NOT_EXACT:' + iid)
        self.numeric_proof = {'policy': 'FRESH_COMPLETE_ANCHORED_FACTOR_SNAPSHOT_EXACT_ALL_CONSUMED_OLD_OHLC',
                              'checked_comparable_rows': checked, 'retained_unknown_rows': retained_unknown,
                              'schema_compatibility': schemas,
                              'revision_policy': 'BLOCK_NEW_FROZEN_INPUT_ON_CONFLICT_OR_MISSING_PROOF'}
        self.new_rows = tuple(b.model_dump(mode='json') for b in bundle.bars)
        bundle = bundle.model_copy(update={'history_artifact_ref': self.proof['history_artifact_ref'],
                                          'history_artifact_hash': self.proof['history_artifact_hash']})
        bundle._history_store = self
        report['go_history_reuse'] = self.audit()
        return bundle, report

    def audit(self):
        return {'contract': CONTRACT, 'status': ('VERIFIED_RAW_WINDOW_AND_FRESH_FACTOR_PROOF_UNKNOWN_COMPARABLE_PRESERVED'
                                                if self.numeric_proof and self.numeric_proof['retained_unknown_rows'] else
                                                'VERIFIED_RAW_WINDOW_WITH_FACTOR_PROOF' if self.numeric_proof else
                                                'VERIFIED_RAW_WINDOW_FACTOR_PROOF_PENDING'),
                'base_input_sha256': self.proof['input_sha256'], 'history_artifact_hash': self.proof['history_artifact_hash'],
                'manifest_sha256': self.request_identity['manifest_sha256'],
                'verified_at': self.proof['verified_at'], 'raw_daily_sessions_skipped': sorted(self.skipped_raw_sessions),
                'factor_compatibility': self.numeric_proof, 'history_security_reuse': 'NOT_IMPLEMENTED_RETAIN_OR_FETCH_EXISTING_QUALIFICATION_EVIDENCE',
                'original_database_modified': False, 'cold_capture': False}
