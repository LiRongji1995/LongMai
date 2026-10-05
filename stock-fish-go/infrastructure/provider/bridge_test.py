"""TEST_FIXTURE transport/basis regressions, with no SDK or network import."""
import copy
import gzip
import hashlib
import importlib.util
import json
import pathlib
import sys
import tempfile
import types
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
from unittest.mock import patch

HERE = pathlib.Path(__file__).resolve().parent
CORE = HERE.parents[2] / 'stock-fish/stockfish_core/yaogu'


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), default=str, allow_nan=False).encode()).hexdigest()


class Bundle:
    def __init__(self, receipts):
        self.receipts, self.bars = receipts, ()
    def model_copy(self, update):
        result = copy.copy(self)
        for key, value in update.items():
            setattr(result, key, value)
        return result


class GoHistoryBridgeTest(unittest.TestCase):
    def setUp(self):
        self.module = load(HERE / 'go_history_bridge.py', 'TEST_FIXTURE_go_bridge')
        self.temp = tempfile.TemporaryDirectory(prefix='TEST_FIXTURE_go_history_bridge_')
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.sessions = []
        day = date(2025, 7, 1)
        while len(self.sessions) < 315:
            if day.weekday() < 5:
                self.sessions.append(day.isoformat())
            day += timedelta(days=1)
        while day.weekday() >= 5:
            day += timedelta(days=1)
        self.day = day
        self.old_day = self.sessions[-1]
        clock = self.old_day + 'T18:00:00+08:00'
        self.row = {'instrument_id': '600000.SH', 'session': self.old_day,
                    'open': '10', 'high': '11', 'low': '9', 'close': '10',
                    'comparable_open': '20', 'comparable_high': '22', 'comparable_low': '18', 'comparable_close': '20',
                    'comparable_basis': 'BAOSTOCK_HFQ_RAW_X_BACK_FACTOR_R03_V1:600000.SH:' + digest(['2000-01-01', '1'])[:20],
                    'quality': 'VALID', 'source_refs': ['TEST_FIXTURE_HISTORY']}
        self.row.update({field: clock for field in ('event_time', 'source_published_at', 'first_seen_at', 'ingested_at')})
        self.receipt = {'receipt_id': 'TEST_FIXTURE_HISTORY', 'provider': 'baostock', 'dataset': 'history',
                        'field_version': 'TEST_FIXTURE_SOURCE_V1', 'status': 'COMPLETE', 'pagination_complete': True}
        value = {'row': self.row, 'projection_sha256': digest(self.row),
                 'identity': {'origin': 'HISTORY_ROW', 'parent_hash': 'a' * 64, 'raw_sha256': 'b' * 64}}
        self.write(value)

    def write(self, value, transport_json=None):
        payload = gzip.compress(((transport_json or json.dumps(value)) + '\n').encode(), mtime=0)
        (self.root / 'TEST_FIXTURE.jsonl.gz').write_bytes(payload)
        self.proof = {'contract': self.module.CONTRACT, 'input_sha256': 'c' * 64,
                      'history_artifact_hash': 'a' * 64, 'history_artifact_ref': 'history_' + 'a' * 64,
                      'verified_at': datetime.now(timezone.utc).isoformat(), 'observation_date': self.old_day,
                      'sessions': self.sessions, 'receipts': [self.receipt],
                      'files': [{'instrument_id': '600000.SH', 'file': 'TEST_FIXTURE.jsonl.gz',
                                 'sha256': hashlib.sha256(payload).hexdigest(), 'bytes': len(payload), 'rows': 1,
                                 'verified_raw_sessions': [self.old_day]}]}
        raw = json.dumps(self.proof).encode()
        (self.root / 'manifest.json').write_bytes(raw)
        self.request = {'manifest_file': 'manifest.json', 'manifest_sha256': hashlib.sha256(raw).hexdigest()}

    def owner(self):
        bridge = self.module.VerifiedGoHistory(self.root, self.request)
        bridge.prepare(['600000.SH'], [date.fromisoformat(s) for s in self.sessions] + [self.day], self.day, self.initial())
        return bridge

    def initial(self):
        clock = datetime.now(timezone.utc).isoformat()
        results = []
        for endpoint in ('mainboard_sse', 'master_szse', 'calendar'):
            rows = ([{'calendar_date': session, 'is_trading_day': '1'} for session in self.sessions + [str(self.day)]]
                    if endpoint == 'calendar' else [{'code': 'TEST_FIXTURE_DIRECTORY'}])
            results.append(dict(endpoint=endpoint, provider='TEST_FIXTURE_PROVIDER', field_version='TEST_FIXTURE_SOURCE_V1',
                                status='COMPLETE', pagination_complete=True, rows=rows,
                                request_receipt_id='TEST_FIXTURE_' + endpoint,
                                receipts=[dict(receipt_id='TEST_FIXTURE_' + endpoint, content_hash=digest(rows),
                                               first_seen_at=clock, ingested_at=clock)]))
        return results

    def dependencies(self):
        # Use the real existing factor algorithm and schema compatibility
        # checker; replace only their heavyweight contract/import boundary.
        names = {'stockfish_core': types.ModuleType('stockfish_core'),
                 'stockfish_core.yaogu': types.ModuleType('stockfish_core.yaogu'),
                 'stockfish_core.yaogu.contracts': types.SimpleNamespace(digest=digest, MarketInput=object),
                 'stockfish_core.storage': types.SimpleNamespace(IntegrityError=ValueError),
                 'stockfish_core.yaogu.history_artifacts': types.SimpleNamespace(HistoryArtifactStore=object)}
        with patch.dict(sys.modules, names):
            math = load(CORE / 'provider_comparability.py', 'stockfish_core.yaogu.provider_comparability')
            schemas = load(CORE / 'history_base_selection.py', 'stockfish_core.yaogu.history_base_selection')
        def normalized(results, *args, **kwargs):
            receipts = [self.receipt]
            for result in results:
                rid = result.get('request_receipt_id')
                source = next((r for r in result.get('receipts', ()) if r.get('receipt_id') == rid), None)
                if source is None:
                    continue
                parameters = {k: v if isinstance(v, (str, int, float, bool)) or v is None else str(v)
                              for k, v in result.get('parameters', {}).items()}
                receipts.append(dict(receipt_id=rid, provider=result['provider'], dataset=result['endpoint'],
                                     field_version=result['field_version'], content_hash=source['content_hash'],
                                     row_count=len(result['rows']), status=result['status'],
                                     pagination_complete=result['pagination_complete'], parameters=parameters,
                                     source_published_at=source['first_seen_at'], first_seen_at=source['first_seen_at'],
                                     ingested_at=source['ingested_at']))
            return Bundle(receipts), {}
        names.update({'stockfish_core.yaogu.provider_comparability': math,
                      'stockfish_core.yaogu.history_base_selection': schemas,
                      'stockfish_core.yaogu.provider_normalization': types.SimpleNamespace(
                          normalize_capture_with_report=normalized,
                          _iid=lambda code: '600000.SH' if code in ('600000.SH', 'sh.600000') else None)})
        return names

    def factors(self, value='2', complete=True, omit_old=False):
        rows = [{'code': 'sh.600000', 'dividOperateDate': '2000-01-01', 'backAdjustFactor': '1'}]
        if not omit_old:
            rows.append({'code': 'sh.600000', 'dividOperateDate': '2026-01-01', 'backAdjustFactor': value})
        rows.append({'code': 'sh.600000', 'dividOperateDate': self.day.isoformat(), 'backAdjustFactor': '3'})
        clock = datetime.now(timezone.utc).isoformat()
        return [{'endpoint': 'adjustment_factors', 'status': 'COMPLETE' if complete else 'PARTIAL',
                 'provider': 'baostock', 'field_version': 'TEST_FIXTURE_FACTOR_SOURCE_V1',
                 'pagination_complete': complete, 'request_receipt_id': 'TEST_FIXTURE_FRESH_FACTORS',
                 'receipts': [{'receipt_id': 'TEST_FIXTURE_FRESH_FACTORS', 'content_hash': digest(rows),
                               'first_seen_at': clock, 'ingested_at': clock}],
                 'parameters': {'codes': ['600000.SH'], 'start_date': '1990-01-01', 'end_date': str(self.day)},
                 'rows': rows, 'coverage': {'security_statuses': []}}]

    def test_full_scope_missing_dates_remain_requests_and_current_always_fresh(self):
        bridge = self.owner()
        self.assertFalse(bridge.raw_request_needed(self.old_day, ['600000.SH']))
        self.assertTrue(bridge.raw_request_needed(self.sessions[-2], ['600000.SH']))
        self.assertTrue(bridge.raw_request_needed(self.day, ['600000.SH']))
        with self.assertRaisesRegex(self.module.HistoryBridgeBlocked, 'CURRENT_SCOPE_NOT_EXPORTED'):
            bridge.prepare(['600000.SH', '000001.SZ'], [date.fromisoformat(s) for s in self.sessions] + [self.day], self.day,
                           self.initial())

    def test_fallback_calendar_or_unparented_directory_cannot_skip_raw_history(self):
        for kind in ('calendar', 'directory'):
            with self.subTest(kind=kind):
                bridge = self.module.VerifiedGoHistory(self.root, self.request)
                results = self.initial()
                results[2 if kind == 'calendar' else 0]['receipts'] = []
                with self.assertRaisesRegex(self.module.HistoryBridgeBlocked, 'FRESH_CALENDAR|FRESH_DIRECTORY'):
                    bridge.prepare(['600000.SH'], [date.fromisoformat(s) for s in self.sessions] + [self.day],
                                   self.day, results)
                self.assertFalse(bridge.skipped_raw_sessions)

    def test_complete_factors_extend_without_rewriting_old_prices(self):
        bridge = self.owner()
        before = json.dumps(self.row, sort_keys=True)
        with patch.dict(sys.modules, self.dependencies()):
            bundle, report = bridge.normalize(self.factors(), self.day, datetime.now(timezone.utc), [])
        self.assertEqual(bridge.numeric_proof['checked_comparable_rows'], 1)
        self.assertEqual(bundle.history_artifact_hash, self.proof['history_artifact_hash'])
        self.assertIs(bundle._history_store, bridge)
        self.assertEqual(json.dumps(self.row, sort_keys=True), before)
        self.assertFalse(report['go_history_reuse']['original_database_modified'])

    def test_partial_batch_requires_complete_matching_security_fragment(self):
        for row_count in (3, 2):
            with self.subTest(row_count=row_count), patch.dict(sys.modules, self.dependencies()):
                bridge = self.owner()
                results = self.factors(complete=False)
                results[0]['coverage']['security_statuses'] = [dict(code='600000.SH', status='COMPLETE',
                                                                  cursor_exhausted=True, row_count=row_count)]
                if row_count == 3:
                    bridge.normalize(results, self.day, datetime.now(timezone.utc), [])
                    self.assertEqual(bridge.numeric_proof['checked_comparable_rows'], 1)
                else:
                    with self.assertRaisesRegex(self.module.HistoryBridgeBlocked, 'FACTOR_PROOF_INCOMPLETE'):
                        bridge.normalize(results, self.day, datetime.now(timezone.utc), [])

    def test_same_anchor_basis_cannot_mask_changed_omitted_or_unproven_factor(self):
        for args in ({'value': '3'}, {'value': '2.00000000000000000000000000001'},
                     {'omit_old': True}, {'complete': False}):
            with self.subTest(args=args), patch.dict(sys.modules, self.dependencies()):
                bridge = self.owner()
                with self.assertRaisesRegex(self.module.HistoryBridgeBlocked, 'FACTOR'):
                    bridge.normalize(self.factors(**args), self.day, datetime.now(timezone.utc), [])
                self.assertIsNone(bridge.numeric_proof)

    def test_complete_result_without_valid_normalized_parent_cannot_prove_factors(self):
        for change in ('missing', 'wrong_id', 'old_clock'):
            with self.subTest(change=change), patch.dict(sys.modules, self.dependencies()):
                bridge = self.owner()
                results = self.factors()
                if change == 'missing':
                    results[0]['receipts'] = []
                elif change == 'wrong_id':
                    results[0]['receipts'][0]['receipt_id'] = 'TEST_FIXTURE_UNRELATED_PARENT'
                else:
                    for field in ('first_seen_at', 'ingested_at'):
                        results[0]['receipts'][0][field] = self.row[field]
                with self.assertRaisesRegex(self.module.HistoryBridgeBlocked, 'FACTOR'):
                    bridge.normalize(results, self.day, datetime.now(timezone.utc), [])
                self.assertIsNone(bridge.numeric_proof)

    def test_unknown_comparable_requires_fresh_factors_and_stays_unknown(self):
        row = dict(self.row)
        for name in ('open', 'high', 'low', 'close'):
            row['comparable_' + name] = None
        row['comparable_basis'] = None
        value = {'row': row, 'projection_sha256': digest(row),
                 'identity': {'origin': 'HISTORY_ROW', 'parent_hash': 'a' * 64, 'raw_sha256': 'b' * 64}}
        self.write(value)
        bridge = self.owner()
        with patch.dict(sys.modules, self.dependencies()):
            with self.assertRaisesRegex(self.module.HistoryBridgeBlocked, 'FACTOR_PROOF_INCOMPLETE'):
                bridge.normalize(self.factors(complete=False), self.day, datetime.now(timezone.utc), [])
            bridge.normalize(self.factors(), self.day, datetime.now(timezone.utc), [])
        self.assertEqual(bridge.numeric_proof['retained_unknown_rows'], 1)
        self.assertIn('UNKNOWN_COMPARABLE_PRESERVED', bridge.audit()['status'])
        self.assertIsNone(next(bridge._rows('600000.SH'))['comparable_close'])

    def test_transport_retains_decimal_literal_below_turnover_threshold(self):
        row = dict(self.row, turnover_fraction='TEST_FIXTURE_DECIMAL_LITERAL')
        value = {'row': row, 'projection_sha256': digest(row),
                 'identity': {'origin': 'HISTORY_ROW', 'parent_hash': 'a' * 64, 'raw_sha256': 'b' * 64}}
        encoded = json.dumps(value).replace('"TEST_FIXTURE_DECIMAL_LITERAL"', '0.06999999999999999999')
        self.write(value, encoded)
        bridge = self.owner()
        selected = next(bridge.iter_rows(self.proof['history_artifact_ref'], 'bar',
                                        instrument_id='600000.SH', expected_hash=self.proof['history_artifact_hash']))
        self.assertIsInstance(selected['turnover_fraction'], Decimal)
        self.assertLess(Fraction(selected['turnover_fraction']), Fraction(7, 100))
        self.assertEqual(str(selected['turnover_fraction']), '0.06999999999999999999')

    def test_transport_binding_and_future_row_clock_fail_closed(self):
        value = {'row': self.row, 'projection_sha256': digest(self.row),
                 'identity': {'origin': 'HISTORY_ROW', 'parent_hash': 'a' * 64, 'raw_sha256': 'b' * 64}}
        value['identity']['parent_hash'] = 'd' * 64
        self.write(value)
        with self.assertRaisesRegex(self.module.HistoryBridgeBlocked, 'ROW_BINDING_INVALID'):
            self.owner()
        value['identity']['parent_hash'] = 'a' * 64
        value['row'] = dict(self.row, ingested_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat())
        self.write(value)
        with self.assertRaisesRegex(self.module.HistoryBridgeBlocked, 'ROW_CLOCK_INVALID'):
            self.owner()

    def test_mixed_offset_new_revision_wins_by_actual_instant(self):
        bridge = self.owner()
        new = dict(self.row, close='11', source_published_at=self.old_day + 'T11:00:00Z',
                   ingested_at=self.old_day + 'T11:00:00Z', first_seen_at=self.old_day + 'T11:00:00Z')
        bridge.new_rows = (new,)
        selected = list(bridge.selected_rows([self.old_day], {'600000.SH'}))
        self.assertEqual(selected, [new])


if __name__ == '__main__':
    unittest.main()
