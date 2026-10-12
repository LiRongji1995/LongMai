"""Exact registered bytes from isolated TEST_FIXTURE SQLite only."""
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from stockfish_core.storage import SQLiteStore, ReadOnlySQLiteStore, StorageError
from stockfish_core.yaogu import original_market_input_export_v1 as export
from stockfish_core.yaogu.contracts import MarketInput, digest
from stockfish_core.yaogu.history_artifacts import compact_input, bind_history, iter_bars
from tests.support.short_horizon_strength_fixture import strength_bundle


class OriginalInputExportFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='original-input-TEST_FIXTURE-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.database = self.root / 'TEST_FIXTURE.sqlite3'
        self.store = SQLiteStore(self.database)
        bundle, _ = strength_bundle()
        self.bundle = bundle.model_copy(update={'bars': ()})
        self.input_id = self.register(self.bundle)

    def register(self, bundle):
        input_id = 'market_' + digest(bundle)
        self.store.put_document('candidate_inputs', input_id, bundle.model_dump(mode='json'))
        return input_id

    def attempt(self, *, input_id=None, name='export', limits=export.Limits()):
        return export.export_original_input(database=self.database, input_id=input_id or self.input_id,
                                            output=self.root / name, limits=limits)

    def receipt(self, name='export'):
        return json.loads((self.root / name / 'export-receipt.json').read_bytes())

    def test_exact_original_utf8_and_content_address_without_dump(self):
        original = self.store.get_document_bytes('candidate_inputs', self.input_id, expected_hash=self.input_id[7:])
        with patch.object(MarketInput, 'model_dump', side_effect=AssertionError('no dump or reconstruction')):
            result = self.attempt()
        self.assertEqual((self.root / 'export/input.json').read_bytes(), original)
        self.assertEqual(result['input_sha256'], self.input_id[7:])
        self.assertEqual(result['cumulative_read_bytes'], 2 * len(original))
        self.assertTrue(result['input_original_bytes_preserved'])
        self.assertFalse(result['typed_schema_verified'])
        self.assertFalse(result['history_owner_authenticated'])
        self.assertFalse(result['source_capability_restored_from_JSON'])
        self.assertEqual(self.receipt()['status'], 'PASS')
        self.assertEqual(sorted(p.name for p in (self.root/'export').iterdir()), ['export-receipt.json', 'input.json'])

    def test_exact_identifier_required_and_no_latest_fallback(self):
        for invalid in ('', 'market_short', '../market_' + 'a'*64, 'market_' + 'A'*64, None, 'latest'):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'EXACT_REGISTERED_MARKET_INPUT_ID_REQUIRED'):
                export.export_original_input(database=self.database, input_id=invalid, output=self.root/'bad-id')
        self.assertFalse((self.root/'bad-id').exists())
        with self.assertRaisesRegex(ValueError, 'EXACT_REGISTERED_INPUT_MISSING'):
            self.attempt(input_id='market_'+'0'*64)
        self.assertEqual(self.receipt()['status'], 'FAILED')
        self.assertFalse((self.root/'export/input.json').exists())

    def test_registered_content_address_conflict_rejected(self):
        with self.store.transaction() as con:
            con.execute('DROP TRIGGER documents_no_update')
            con.execute('UPDATE documents SET content_hash=? WHERE document_id=?', ('0'*64, self.input_id))
        with self.assertRaisesRegex(ValueError, 'REGISTERED_INPUT_CONTENT_ADDRESS_MISMATCH'):
            self.attempt()
        self.assertEqual(self.receipt()['status'], 'FAILED')
        self.assertFalse((self.root/'export/input.partial').exists())

    def test_hash_corrupt_original_preserves_failed_partial(self):
        with self.store.transaction() as con:
            con.execute('DROP TRIGGER documents_no_update')
            con.execute('UPDATE documents SET payload=? WHERE document_id=?', ('{"TEST_FIXTURE":"corrupt"}', self.input_id))
        with self.assertRaisesRegex(ValueError, 'REGISTERED_INPUT_ORIGINAL_BYTES_HASH_MISMATCH'):
            self.attempt()
        self.assertEqual(self.receipt()['status'], 'FAILED')
        self.assertEqual((self.root/'export/input.partial').read_bytes(), b'{"TEST_FIXTURE":"corrupt"}')
        with self.assertRaisesRegex(ValueError, 'EXPORT_OUTPUT_MUST_BE_NEW'):
            self.attempt()

    def test_oversize_preflight_uses_blob_length_and_zero_payload_reads(self):
        statements, reads = [], []
        original_connect = ReadOnlySQLiteStore._connect
        class Blob:
            def __init__(self, blob): self.blob = blob
            def __enter__(self): return self
            def __exit__(self, *args): self.blob.close()
            def __len__(self): return len(self.blob)
            def read(self, size): reads.append(size); return self.blob.read(size)
        class Connection:
            def __init__(self, con): self.con = con
            def execute(self, sql, *args): statements.append(sql); return self.con.execute(sql, *args)
            def blobopen(self, *args, **kwargs): return Blob(self.con.blobopen(*args, **kwargs))
            def commit(self): return self.con.commit()
            def rollback(self): return self.con.rollback()
            def close(self): return self.con.close()
        with patch.object(ReadOnlySQLiteStore, '_connect', lambda store: Connection(original_connect(store))):
            with self.assertRaisesRegex(ValueError, 'REGISTERED_INPUT_EMPTY_OR_OVER_BYTE_BUDGET'):
                self.attempt(limits=replace(export.Limits(), input_bytes=64))
        self.assertEqual(reads, [])
        self.assertFalse(any('CAST' in sql or 'json_extract' in sql or 'SELECT payload' in sql for sql in statements))
        self.assertEqual(self.receipt()['cumulative_read_bytes'], 0)
        self.assertFalse((self.root/'export/input.partial').exists())

    def test_actual_uri_read_only_query_only_and_no_migration(self):
        queried = []
        original_connect = ReadOnlySQLiteStore._connect
        def connect(store):
            con = original_connect(store)
            queried.append(con.execute('PRAGMA query_only').fetchone()[0])
            with self.assertRaises(sqlite3.OperationalError):
                con.execute('CREATE TABLE illegal_write (id INTEGER)')
            return con
        with patch.object(ReadOnlySQLiteStore, '_connect', connect), patch.object(SQLiteStore, '_migrate', side_effect=AssertionError('no migration')):
            self.attempt()
        self.assertEqual(queried, [1])
        readonly = ReadOnlySQLiteStore(self.database)
        with self.assertRaises(StorageError):
            with readonly.transaction(write=True): pass

    def test_committed_wal_is_visible_without_immutable_mode(self):
        keeper = self.store._connect()
        self.addCleanup(keeper.close)
        extra = self.bundle.model_copy(update={'data_notes': {'TEST_FIXTURE_WAL': 'new committed input'}})
        input_id = self.register(extra)
        self.assertTrue(Path(str(self.database)+'-wal').exists())
        result = self.attempt(input_id=input_id)
        self.assertEqual(result['input_sha256'], input_id[7:])
        self.assertEqual(MarketInput.model_validate_json((self.root/'export/input.json').read_bytes()), extra)

    def test_existing_output_and_database_symlink_rejected(self):
        (self.root/'export').mkdir()
        sentinel = self.root/'export/sentinel'
        sentinel.write_bytes(b'TEST_FIXTURE_KEEP')
        with self.assertRaisesRegex(ValueError, 'EXPORT_OUTPUT_MUST_BE_NEW'):
            self.attempt()
        self.assertEqual(sentinel.read_bytes(), b'TEST_FIXTURE_KEEP')
        link = self.root/'database-link'
        link.symlink_to(self.database)
        with self.assertRaisesRegex(ValueError, 'DATABASE_SYMLINK_REJECTED'):
            export.export_original_input(database=link, input_id=self.input_id, output=self.root/'new')
        self.assertFalse((self.root/'new').exists())

    def test_atomic_final_file_does_not_replace_intervening_target(self):
        link = export.os.link
        original = self.store.get_document_bytes('candidate_inputs', self.input_id)
        sentinel = self.root/'same-byte-external'
        sentinel.write_bytes(original)
        for kind in ('foreign_target', 'symlink_partial', 'fifo_partial', 'hardlink_partial'):
            def race(source, target, **kwargs):
                if kind == 'foreign_target':
                    Path(target).write_bytes(b'TEST_FIXTURE_KEEP_FOREIGN')
                else:
                    Path(source).unlink()
                    if kind == 'symlink_partial': Path(source).symlink_to(sentinel)
                    elif kind == 'fifo_partial': os.mkfifo(source)
                    else: link(sentinel, source)
                return link(source, target, **kwargs)
            expected = FileExistsError if kind == 'foreign_target' else ValueError
            with self.subTest(kind=kind), patch.object(export.os, 'link', race), self.assertRaises(expected):
                self.attempt(name=kind)
            if kind == 'foreign_target':
                self.assertEqual((self.root/kind/'input.json').read_bytes(), b'TEST_FIXTURE_KEEP_FOREIGN')
            self.assertTrue((self.root/kind/'input.partial').exists())
            self.assertEqual(self.receipt(kind)['status'], 'FAILED')
            self.assertEqual(sentinel.read_bytes(), original)

    def test_same_length_receipt_and_output_mutations_are_failed(self):
        seal = export._seal
        for name in ('receipt', 'input'):
            def mutate(path, value, identity):
                result = seal(path, value, identity)
                if value['status'] == 'PASS':
                    target = path if name == 'receipt' else Path(path).parent/'input.json'
                    raw = target.read_bytes()
                    target.write_bytes(b'X'+raw[1:])
                return result
            expected = 'EXPORT_OWNED_FILE_IDENTITY_CHANGED' if name == 'receipt' else 'EXPORT_TERMINAL_FILE_CHANGED'
            with self.subTest(name=name), patch.object(export, '_seal', mutate), self.assertRaisesRegex(ValueError, expected):
                self.attempt(name=name)
            self.assertEqual(self.receipt(name)['status'], 'FAILED')

    def test_receipt_replacement_cannot_overwrite_external_sentinel(self):
        seal, sentinel = export._seal, self.root/'external-sentinel'
        sentinel.write_bytes(b'TEST_FIXTURE_EXTERNAL_KEEP')
        def replace_receipt(path, value, identity):
            if value['status'] == 'PASS':
                Path(path).unlink()
                Path(path).symlink_to(sentinel)
            return seal(path, value, identity)
        with patch.object(export, '_seal', replace_receipt), self.assertRaisesRegex(ValueError, 'EXPORT_RECEIPT_IDENTITY_CHANGED'):
            self.attempt()
        self.assertEqual(sentinel.read_bytes(), b'TEST_FIXTURE_EXTERNAL_KEEP')
        self.assertEqual(json.loads((self.root/'export/export-failure.json').read_bytes())['status'], 'FAILED')

    def test_final_seal_wall_overrun_is_failed_without_retry(self):
        clock = [100.0]
        seal = export._seal
        def delayed(path, value, identity):
            result = seal(path, value, identity)
            if value['status'] == 'PASS': clock[0] += 121
            return result
        with patch.object(export.time, 'monotonic', lambda: clock[0]), patch.object(export, '_seal', delayed):
            with self.assertRaisesRegex(TimeoutError, 'REGISTERED_EXPORT_WALL_BUDGET_EXCEEDED'):
                self.attempt()
        self.assertEqual(self.receipt()['status'], 'FAILED')
        self.assertTrue((self.root/'export/input.json').exists())

    def test_invalid_or_increased_budgets_and_initial_rss_rejected(self):
        for limits in (replace(export.Limits(), input_bytes=257*1024**2),
                       replace(export.Limits(), wall_seconds=True),
                       replace(export.Limits(), input_bytes=1.5)):
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                self.attempt(limits=limits)
        with patch.object(export, '_rss', return_value=257*1024**2), self.assertRaises(MemoryError):
            self.attempt()
        self.assertFalse((self.root/'export').exists())

    def test_compact_input_remains_exact_and_no_history_permission_serialized(self):
        original, _ = strength_bundle()
        compact = compact_input(original, self.store)
        input_id = self.register(compact)
        result = self.attempt(input_id=input_id)
        restored = MarketInput.model_validate_json((self.root/'export/input.json').read_bytes())
        self.assertEqual(digest(restored), input_id[7:])
        self.assertEqual(restored.history_artifact_ref, compact.history_artifact_ref)
        self.assertIsNone(restored._history_store)
        self.assertFalse(result['history_owner_authenticated'])
        with self.assertRaisesRegex(StorageError, 'STORE_NOT_BOUND'):
            list(iter_bars(restored))
        # Explicit native same-owner binding is possible, but is an independent
        # consumer operation, never authority restored from export-receipt.
        bind_history(restored, ReadOnlySQLiteStore(self.database))
        self.assertEqual(len(list(iter_bars(restored))), len(original.bars))
        self.assertIsNotNone(restored._history_store)

    def test_exported_original_is_consumed_by_daily_frozen_run_verifier(self):
        # Uses original fixture saved decisions; no selector computes a list.
        from stockfish_core.yaogu import strategy_experiments  # established import order
        from stockfish_core.yaogu import saved_r2_daily_coordination_v1 as daily
        from stockfish_core.yaogu import saved_r2_delivery as base
        from stockfish_core.yaogu.history_artifacts import digest_json_stream
        from tests.support.saved_r2_v3_fixture import fixture, write
        bundle, _, run, sources, _, paths = fixture(self.root/'saved',
            pool_count=17, selected_count=13, sessions_count=0, include_themes=False)
        # Declare the synthetic target before registering/exporting any saved
        # fixture input. This is not a posthoc change to a real saved run.
        run['target_session'] = str(bundle.observation_date + timedelta(days=1))
        input_id = self.register(bundle)
        run['input_id'] = input_id
        run['content_hash'] = digest_json_stream({key: value for key, value in run.items() if key != 'content_hash'})
        write(paths['run'], run)
        result = self.attempt(input_id=input_id)
        binding = daily._binding_from_run(run, base)
        manifest = {'schema_version': base.RUN_SCHEMA,
                    'run_sha256': hashlib.sha256(paths['run'].read_bytes()).hexdigest(),
                    'input_sha256': result['input_sha256'],
                    'sources_manifest_sha256': hashlib.sha256(paths['sources'].read_bytes()).hexdigest(),
                    'binding': binding}
        manifest_path = self.root/'saved/export-binding.json'
        write(manifest_path, manifest)
        parsed, frozen, selected, _, _, _ = base._freeze_run(base._Registry(), paths['run'],
            manifest_path, result['input_path'], paths['sources'], daily._now())
        self.assertEqual(parsed, bundle)
        self.assertEqual(frozen['input_id'], input_id)
        self.assertEqual(frozen['counts']['eligible'], 17)
        self.assertEqual([row['instrument_id'] for row in selected], binding['selected_ids'])
        self.assertEqual(len(selected), 13)
        self.assertEqual(paths['run'].read_bytes(), json.dumps(run, ensure_ascii=False, allow_nan=False).encode('utf-8'))

    def test_cli_returns_failure_and_preserves_original_attempt(self):
        with patch('builtins.print') as printed:
            code = export.main(['--database', str(self.database), '--input-id', 'market_'+'0'*64,
                                '--output', str(self.root/'export')])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(printed.call_args.args[0])['status'], 'FAILED')
        self.assertEqual(self.receipt()['status'], 'FAILED')


if __name__ == '__main__':
    unittest.main()
