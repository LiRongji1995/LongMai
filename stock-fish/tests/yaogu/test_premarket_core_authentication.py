"""Fresh core authentication shares bytes without weakening terminal checks."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from stockfish_core.yaogu import historical_replay
from stockfish_core.yaogu import premarket_native_core_v1 as core
from tests.yaogu.test_premarket_native_core_v1 import FixtureBudget, captures


class CoreAuthenticationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='stockfish-TEST_FIXTURE-core-auth-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.items, self.observed, self.target, self.cutoff = captures(self.root)

    def run_core(self, budget=None):
        return core.run_core(captures=self.items, observation_date=self.observed,
            target_session=self.target, cutoff=self.cutoff, output_dir=self.root/'core',
            budget=budget or FixtureBudget(), coverage_type='TEST_FIXTURE')

    def test_original_trio_is_read_once_per_authentication_then_terminal_rereads(self):
        class CountedBudget(FixtureBudget):
            def __init__(self):
                super().__init__()
                self.reads, self.authentication_reads = {}, None

            def read(self, path, expected=None, limit=None):
                key = str(Path(path).resolve())
                self.reads[key] = self.reads.get(key, 0) + 1
                return super().read(path, expected, limit)

            def check(self, stage):
                if stage == 'core_normalize':
                    self.authentication_reads = dict(self.reads)

        budget = CountedBudget()
        native, verified = historical_replay.verify_capture, []
        def verifier(root, result):
            verified.append(result['request_receipt_id'])
            return native(root, result)
        with patch.object(historical_replay, 'verify_capture', verifier):
            owner = self.run_core(budget)
        self.assertEqual(len(verified), len(self.items))
        # Calendar also has a separate, fresh source-reader call before normalize.
        for item in self.items:
            for role in ('result', 'raw', 'receipt'):
                expected = 2 if item is self.items[0] and role == 'raw' else 1
                self.assertEqual(budget.authentication_reads[item[role]['path']], expected)
                self.assertGreaterEqual(budget.reads[item[role]['path']], expected + 3)
        before = dict(budget.reads)
        self.assertTrue(owner.validate())
        for item in self.items:
            for role in ('result', 'raw', 'receipt'):
                self.assertGreater(budget.reads[item[role]['path']], before[item[role]['path']])

    def test_same_bytes_replacement_during_authentication_is_rejected(self):
        path = Path(self.items[0]['raw']['path'])
        original = path.read_bytes()
        native = historical_replay.verify_capture
        def replace(root, result):
            replacement = path.with_suffix('.TEST_FIXTURE-replacement')
            replacement.write_bytes(original)
            replacement.replace(path)
            return native(root, result)
        bytes_method, text_method = Path.read_bytes, Path.read_text
        with patch.object(historical_replay, 'verify_capture', replace):
            with self.assertRaisesRegex(ValueError, 'ORIGINAL_CHANGED_DURING_AUTHENTICATION'):
                self.run_core()
        self.assertIs(Path.read_bytes, bytes_method)
        self.assertIs(Path.read_text, text_method)
        self.assertFalse((self.root/'core').exists())

    def test_verifier_failure_restores_readers_and_drops_transaction_bytes(self):
        budget = FixtureBudget()
        registry = core._BudgetRegistry(budget)
        item = self.items[0]['raw']
        bytes_method, text_method = Path.read_bytes, Path.read_text
        with self.assertRaisesRegex(RuntimeError, 'TEST_FIXTURE_PRIMARY'):
            with registry.authentication():
                registry.read(item['path'], item)
                with registry.mature_verifier((item,)):
                    self.assertEqual(hashlib.sha256(Path(item['path']).read_bytes()).hexdigest(), item['sha256'])
                    raise RuntimeError('TEST_FIXTURE_PRIMARY')
        self.assertIs(Path.read_bytes, bytes_method)
        self.assertIs(Path.read_text, text_method)
        self.assertIsNone(registry._capture_bytes)
        self.assertIsNone(registry._capture_owner)
        path = Path(item['path'])
        raw = path.read_bytes()
        path.write_bytes(b'X' + raw[1:])
        with self.assertRaisesRegex(ValueError, 'TEST_FIXTURE_SHA_CHANGED'):
            with registry.authentication():
                registry.read(path, item['sha256'])
        with self.assertRaisesRegex(ValueError, 'TEST_FIXTURE_IDENTITY_CHANGED'):
            registry.terminal()

    def test_cached_read_still_checks_expected_sha_and_limit(self):
        registry = core._BudgetRegistry(FixtureBudget())
        item = self.items[0]['raw']
        with registry.authentication():
            raw = registry.read(item['path'], item)
            with self.assertRaisesRegex(ValueError, 'FILE_SHA256_CHANGED'):
                registry.read(item['path'], '0' * 64)
            with self.assertRaisesRegex(ValueError, 'FILE_READ_LIMIT_EXCEEDED'):
                registry.read(item['path'], item['sha256'], len(raw) - 1)
            with self.assertRaisesRegex(ValueError, 'INVALID_FILE_REFERENCE_SCHEMA'):
                registry.read(item['path'], {'path': item['path'], 'sha256': item['sha256']})
            for invalid in (-1, True, '1', 1.5):
                with self.subTest(limit=invalid):
                    with self.assertRaisesRegex(ValueError, 'INVALID_FILE_READ_LIMIT'):
                        registry.read(item['path'], item['sha256'], invalid)
            with self.assertRaisesRegex(ValueError, 'TERMINAL_REQUIRES_FRESH_ORIGINAL_READS'):
                registry.terminal()

    def test_invalid_cached_request_preserves_sticky_real_budget_failure(self):
        from stockfish_core.yaogu.premarket_runtime_v1 import Budget, BudgetExceeded
        budget = Budget()
        registry = core._BudgetRegistry(budget)
        item = self.items[0]['raw']
        with self.assertRaisesRegex(BudgetExceeded, 'INVALID_FILE_READ_LIMIT'):
            with registry.authentication():
                registry.read(item['path'], item)
                registry.read(item['path'], item['sha256'], True)
        self.assertEqual(budget.metrics['failures'], ['INVALID_FILE_READ_LIMIT'])
        with self.assertRaisesRegex(BudgetExceeded, 'INVALID_FILE_READ_LIMIT'):
            registry.terminal()

    def test_core_reuses_guarded_equivalent_encoders_and_rehashes_live_objects(self):
        from stockfish_core.yaogu import history_artifacts as original
        from stockfish_core.yaogu import short_horizon_archive_bridge_v1 as encoders
        calls = {'run': 0, 'input': 0}
        def run_digest(value, *, checkpoint=None):
            self.assertTrue(callable(checkpoint))
            calls['run'] += 1
            result = encoders.digest_json_stream(value, checkpoint=checkpoint)
            self.assertEqual(result, original.digest_json_stream(value))
            return result
        def input_digest(value, *, checkpoint=None):
            self.assertTrue(callable(checkpoint))
            calls['input'] += 1
            result = encoders.digest_market_input(value, checkpoint=checkpoint)
            self.assertEqual(result, original.digest_market_input(value))
            return result
        with patch.object(core, 'digest_json_stream', run_digest), \
                patch.object(core, 'digest_market_input', input_digest):
            owner = self.run_core()
            self.assertEqual(calls, {'run': 2, 'input': 2})
            self.assertTrue(owner.validate())
            self.assertEqual(calls, {'run': 3, 'input': 3})
            core._owner_state(owner)['run']['market_edge'] = 'TEST_FIXTURE_TAMPERED'
            with self.assertRaisesRegex(ValueError, 'LIVE_OWNER_CHANGED'):
                owner.validate()
            self.assertEqual(calls, {'run': 4, 'input': 4})

    def test_encoder_checkpoint_budget_failure_prevents_exports(self):
        class StoppedBudget(FixtureBudget):
            def check(self, stage):
                if stage == 'core_identity':
                    raise RuntimeError('TEST_FIXTURE_HASH_BUDGET_EXCEEDED')
        with self.assertRaisesRegex(RuntimeError, 'TEST_FIXTURE_HASH_BUDGET_EXCEEDED'):
            self.run_core(StoppedBudget())
        self.assertFalse((self.root/'core').exists())
