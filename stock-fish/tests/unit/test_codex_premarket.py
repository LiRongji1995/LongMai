"""TEST_FIXTURE retained locator and archive-boundary helpers.

Request/worker/receipt safety is exercised through the sole daily_v2 entry.
"""
from pathlib import Path
import json
import sys
import pytest
from scripts import codex_premarket as adapter


@pytest.fixture
def case(tmp_path):
    root = (tmp_path / 'TEST_FIXTURE_premarket').resolve()
    source, state = root / 'source', root / 'state'
    (source / 'stockfish_core').mkdir(parents=True)
    state.mkdir()
    (source / 'stockfish_core/__init__.py').write_bytes(b'# TEST_FIXTURE\n')
    for name in adapter.STRATEGY_SUPPORT:
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'TEST_FIXTURE_STRATEGY_SUPPORT\n')
    config = root / 'config.json'
    config.write_text(json.dumps(dict(state_root=str(state), capture=dict(allow_test_fixture=True))))
    profile = dict(source_root=str(source), state_root=str(state), python=sys.executable, config=str(config))
    return root, profile, config


def test_explicit_matching_absolute_state_required(case):
    root, profile, config = case
    for value, reason in (({}, 'EXPLICIT_STATE_ROOT'), ({'state_root':'relative'}, 'ABSOLUTE_CONFIG_STATE'),
                          ({'state_root':str(root)}, 'CONFIG_STATE_ROOT_MISMATCH')):
        config.write_text(json.dumps(value))
        with pytest.raises(ValueError, match=reason):
            adapter._prepared(profile)


def test_missing_interpreter_never_installs_or_creates_runtime(case):
    root, profile, _ = case
    missing = root / 'missing-python'
    with pytest.raises(ValueError, match='PROFILE_PATH_UNAVAILABLE'):
        adapter._prepared(dict(profile, python=str(missing)))
    assert not missing.exists()


def test_snapshot_does_not_scan_private_config_and_binds_strategy(case):
    root, profile, _ = case
    prepared = adapter._prepared(profile)
    before = adapter._snapshot(prepared)
    private = prepared['source'] / 'config/private.env'
    private.write_text('TEST_FIXTURE_SECRET=not-read')
    assert adapter._snapshot(prepared) == before
    strategy = prepared['source'] / 'config/short_horizon_strength_r2.yaml'
    strategy.write_bytes(b'TEST_FIXTURE_CHANGED')
    assert adapter._snapshot(prepared) != before


def test_saved_artifact_requires_content_and_inside_job(case):
    root, profile, _ = case
    prepared = adapter._prepared(profile)
    job = prepared['state'] / 'jobs/TEST_FIXTURE'
    job.mkdir(parents=True)
    artifact = job / 'all.csv'
    artifact.write_bytes(b'TEST_FIXTURE,UNKNOWN\n')
    reference = adapter._reference(artifact)
    adapter._inside_job(reference, job)
    replacement = job / 'replacement'
    replacement.write_bytes(artifact.read_bytes())
    replacement.replace(artifact)
    adapter._inside_job(reference, job)
    artifact.write_bytes(b'X' + artifact.read_bytes()[1:])
    with pytest.raises(ValueError, match='CONTENT_CHANGED'):
        adapter._inside_job(reference, job)
    with pytest.raises(ValueError, match='REFERENCE_OUTSIDE_JOB'):
        adapter._inside_job(adapter._reference(root / 'config.json'), job)
