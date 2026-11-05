"""TEST_FIXTURE archive delivery: no network, DB, SDK or stock selector."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from stockfish_core.yaogu import economic_direction_display as display
from stockfish_core.yaogu import economic_direction_explanation_v1 as producer
from stockfish_core.yaogu.history_artifacts import digest_json_stream
from tests.yaogu.test_economic_direction_explanation_v1 import parent, business, file, CUTOFF


class EconomicDirectionDisplayTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='TEST_FIXTURE_economic_display_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        def forbidden(*args, **kwargs):
            raise AssertionError('TEST_FIXTURE_NO_EXTERNAL_IO_OR_API')
        for target in ('socket.socket.connect', 'sqlite3.connect'):
            guard = patch(target, forbidden)
            guard.start(); self.addCleanup(guard.stop)
        saved, original = parent(self.root)
        original['attachment_content_hash'] = digest_json_stream(original)
        self.parent = file(saved.path, original)
        self.original = original
        attachment = producer.build_economic_direction_explanation(frozen_grouped=self.parent,
            business_sources=(business(self.root),), counted_nodes=(), cutoff=CUTOFF)
        self.attachment = self.root / 'explanation.json'
        self.write_attachment(attachment)
        self.old_bytes = {p: p.read_bytes() for p in (self.parent.path, self.attachment)}
        self.as_of = '2029-01-01T00:00:00Z'

    def write_attachment(self, value):
        value = deepcopy(value)
        value.pop('attachment_content_hash', None)
        value['attachment_content_hash'] = digest_json_stream(value)
        self.attachment.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def read(self, **overrides):
        return display.archived_economic_direction_display(self.attachment,
            **({'grouped_parent': self.parent.path, 'as_of': self.as_of} | overrides))

    def test_complete_archived_delivery_preserves_three_items_and_divergence(self):
        result = self.read()
        self.assertEqual(result['observation_count'], 3)
        self.assertIn('出现分歧', result['text'])
        for row in self.original['groups'][0]['items']:
            self.assertIn(row['instrument_id'], result['text'])
        self.assertEqual({p: p.read_bytes() for p in self.old_bytes}, self.old_bytes)
        self.assertFalse(result['source_registry_reauthenticated'])
        self.assertFalse(result['selector_reauthenticated'])
        self.assertFalse(result['source_capability_issued'])

    def test_old_asof_hides_late_text_and_candidate_count(self):
        result = self.read(as_of='2020-01-01T00:00:00Z')
        self.assertEqual(result['status'], 'NOT_YET_VISIBLE')
        self.assertNotIn('observation_count', result)
        self.assertNotIn('TEST_FIXTURE甲', result['text'])
        self.assertNotIn('10001', result['text'])

    def test_naive_display_time_is_rejected(self):
        with self.assertRaises(ValueError):
            self.read(as_of=datetime(2029, 1, 1))

    def test_changed_parent_file_rejected_even_if_valid_json(self):
        self.parent.path.write_text(json.dumps(self.original), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'GROUPED_PARENT_HASH_MISMATCH'):
            self.read()

    def test_rehashed_changed_selection_and_original_values_are_rejected(self):
        original = json.loads(self.attachment.read_text())
        changed = deepcopy(original)
        changed['frozen_selected_ids'].pop()
        self.write_attachment(changed)
        with self.assertRaisesRegex(ValueError, 'FROZEN_BINDING_MISMATCH'):
            self.read()
        changed = deepcopy(original)
        changed['primary_display_groups'][0]['original_items'][0]['exact_features']['amount']['numerator'] += 1
        self.write_attachment(changed)
        with self.assertRaisesRegex(ValueError, 'FULL_UNION_OR_ITEMS_MISMATCH'):
            self.read()

    def test_rehashed_duplicate_or_removed_display_row_rejected(self):
        original = json.loads(self.attachment.read_text())
        for duplicate in (False, True):
            changed = deepcopy(original)
            changed['primary_display_groups'][0]['original_items'].pop()
            if duplicate:
                changed['primary_display_groups'][-1]['original_items'].append(
                    deepcopy(changed['primary_display_groups'][-1]['original_items'][0]))
            self.write_attachment(changed)
            with self.assertRaisesRegex(ValueError, 'FULL_UNION_OR_ITEMS_MISMATCH'):
                self.read()

    def test_rehashed_hidden_native_divergence_rejected(self):
        changed = json.loads(self.attachment.read_text())
        changed['preserved_native_subtheme_layers']['active_divergence_mainlines'] = []
        self.write_attachment(changed)
        with self.assertRaisesRegex(ValueError, 'FROZEN_BINDING_MISMATCH'):
            self.read()

    def test_rehashed_removed_business_renderer_index_cannot_hide_stock(self):
        changed = json.loads(self.attachment.read_text())
        changed['economic_directions'][0]['business_relations'] = []
        self.write_attachment(changed)
        with self.assertRaisesRegex(ValueError, 'RENDERED_BUSINESS_INDEX_MISMATCH'):
            self.read()

    def test_rehashed_fallback_business_role_cannot_hide_stock(self):
        changed = json.loads(self.attachment.read_text())
        changed['primary_display_groups'][-1]['group_type'] = 'BUSINESS_RELATION_ONLY_NOT_MAINLINE'
        self.write_attachment(changed)
        with self.assertRaisesRegex(ValueError, 'RENDERED_GROUP_ROLE_MISMATCH'):
            self.read()

    def test_rehashed_display_times_cannot_precede_parent_or_capture(self):
        original = json.loads(self.attachment.read_text())
        changed = deepcopy(original)
        for key in ('created_at', 'new_evidence_cutoff', 'attachment_visible_at'):
            changed[key] = '2020-01-01T00:00:00Z'
        self.write_attachment(changed)
        with self.assertRaisesRegex(ValueError, 'KNOWN_CLOCK_FLOOR_VIOLATION'):
            self.read(as_of='2025-01-01T00:00:00Z')
        changed = deepcopy(original)
        changed['economic_directions'][0]['business_relations'][0]['actual_capture_clocks']['request_completed_at'] = '2027-01-01T00:00:00Z'
        self.write_attachment(changed)
        with self.assertRaisesRegex(ValueError, 'KNOWN_CLOCK_FLOOR_VIOLATION'):
            self.read()

    def test_schema_and_capability_claim_rejected(self):
        original = json.loads(self.attachment.read_text())
        changed = deepcopy(original); changed['schema_version'] = 'TEST_FIXTURE_UNREGISTERED'
        self.write_attachment(changed)
        with self.assertRaisesRegex(ValueError, 'SCHEMA_MISMATCH'):
            self.read()
        changed = deepcopy(original); changed['source_capability_issued'] = True
        self.write_attachment(changed)
        with self.assertRaisesRegex(ValueError, 'FROZEN_BINDING_MISMATCH'):
            self.read()

    def test_size_duplicate_json_and_nonfinite_guards(self):
        with patch.object(display, 'MAX_ARCHIVE_BYTES', 1):
            with self.assertRaisesRegex(ValueError, 'ARCHIVE_SIZE_INVALID'):
                self.read()
        for raw, expected in ((b'{"a":1,"a":2}', 'DUPLICATE_JSON_KEY'),
                              (b'{"a":NaN}', 'NONFINITE_JSON_VALUE')):
            self.attachment.write_bytes(raw)
            with self.assertRaisesRegex(ValueError, expected):
                self.read()

    def test_source_terminal_mutation_rejected(self):
        original = producer.render_economic_direction_explanation
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            self.attachment.write_bytes(self.attachment.read_bytes() + b' ')
            return result
        with patch.object(producer, 'render_economic_direction_explanation', changed):
            with self.assertRaisesRegex(ValueError, 'ARCHIVE_CHANGED'):
                self.read()




if __name__ == '__main__':
    unittest.main()
