"""TEST_FIXTURE: real file replacement through source and archive readers."""
from copy import deepcopy
from pathlib import Path

import pytest

from stockfish_core.file_integrity import content_equal, content_reference_equal
from stockfish_core.yaogu.premarket_runtime_v1 import Budget, BudgetExceeded
from stockfish_core.yaogu.premarket_capture_cache_v1 import CaptureCache
from stockfish_core.yaogu.premarket_native_core_v1 import _BudgetRegistry
from stockfish_core.yaogu.saved_r2_delivery import _Registry
from stockfish_core.yaogu.short_horizon_archive_bridge_v1 import ArchiveVerificationSession
from stockfish_core.yaogu import premarket_workflow_v1 as workflow


def replace_bytes(path, raw):
    replacement = path.with_suffix('.TEST_FIXTURE_replacement')
    replacement.write_bytes(raw)
    replacement.replace(path)


@pytest.mark.parametrize('reader_kind', ['budget', 'cache', 'native', 'saved', 'archive', 'workflow'])
def test_replacement_accepts_same_bytes_and_rejects_same_size_tamper(tmp_path, reader_kind):
    path = tmp_path / 'TEST_FIXTURE.json'
    raw = b'{"TEST_FIXTURE":100}'
    path.write_bytes(raw)
    budget = Budget()
    _, original = budget.read(path)
    if reader_kind == 'budget':
        read = lambda: budget.read(path, original)
    elif reader_kind == 'cache':
        cache = CaptureCache(tmp_path / 'cache', budget,
                             config={'offline': True, 'allow_test_fixture': True})
        read = lambda: cache._read(original)
    elif reader_kind == 'native':
        registry = _BudgetRegistry(budget)
        read = lambda: registry.read(path, original)
    elif reader_kind == 'saved':
        registry = _Registry()
        read = lambda: registry.read(path, original['sha256'])
    elif reader_kind == 'archive':
        registry = ArchiveVerificationSession()
        # retain=False used to rely on inode/mtime and skip the current bytes.
        read = lambda: registry.read(path, original, retain=False)
    else:
        read = lambda: workflow._verify(original)
    read()
    replace_bytes(path, raw)
    read()
    if reader_kind in ('native', 'saved', 'archive'):
        registry.terminal()
    replace_bytes(path, raw.replace(b'100', b'200'))
    with pytest.raises((ValueError, BudgetExceeded), match='SHA|CONTENT|REFERENCE|CHANGED|MISMATCH'):
        read()


def test_legacy_reference_shapes_keep_size_and_business_fields(tmp_path):
    path = tmp_path / 'TEST_FIXTURE.bin'
    path.write_bytes(b'TEST_FIXTURE')
    _, original = Budget().read(path)
    changed = deepcopy(original)
    changed['fp']['st_dev'] += 1
    changed['fp']['st_ino'] += 1
    changed['fp']['st_mtime_ns'] += 1
    assert content_reference_equal(original, changed)
    legacy = {key: value for key, value in changed.items() if key != 'bytes'}
    assert content_equal(original, legacy)
    assert content_reference_equal(original, legacy)
    assert not content_equal({'observation_date': '2026-11-05', 'ref': original},
                             {'observation_date': '2026-11-04', 'ref': changed})
    assert not content_equal(original['fp'], changed['fp'])
    bad = deepcopy(changed)
    bad['bytes'] += 1
    assert not content_reference_equal(original, bad)
