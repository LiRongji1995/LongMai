"""Whole saved strategy results must not allocate a second complete JSON string."""
import pytest

from stockfish_core.service import ApplicationService
from stockfish_core.storage import SQLiteStore
from stockfish_core.yaogu import candidate_service, history_artifacts
from stockfish_core.yaogu.contracts import digest
from tests.yaogu.fixtures import make_bundle


@pytest.fixture
def isolated_service(tmp_path):
    core = ApplicationService(root=tmp_path / "TEST_FIXTURE_stream_state",
                              store=SQLiteStore(tmp_path / "TEST_FIXTURE_stream.sqlite3"),
                              allow_test_fixture=True)
    bundle, cutoff = make_bundle(3)
    yield core.candidates, bundle, cutoff
    core.close()


def guard_full_result_digest(monkeypatch):
    original = candidate_service.digest
    stream = history_artifacts.digest_json_stream
    checked = []

    def small_digest(value):
        if isinstance(value, dict) and "results" in value:
            pytest.fail("complete strategy result used the allocating digest")
        return original(value)

    def observed_stream(value):
        actual = stream(value)
        assert actual == digest(value)
        checked.append(actual)
        return actual

    monkeypatch.setattr(candidate_service, "digest", small_digest)
    monkeypatch.setattr(history_artifacts, "digest_json_stream", observed_stream)
    return checked


def test_build_result_stream_hash_matches_existing_canonical_bytes(isolated_service, monkeypatch):
    service, bundle, cutoff = isolated_service
    checked = guard_full_result_digest(monkeypatch)
    result = service._build_strategy_experiment_result("TEST_FIXTURE_input", bundle, cutoff, service.current_config())
    assert result["content_hash"] in checked
    assert result["content_hash"] == digest({key: value for key, value in result.items() if key != "content_hash"})
    assert result["prospective_validation"]["signal_run_persisted"] is False


def test_write_result_stream_hash_matches_persisted_flags_and_unicode(isolated_service, monkeypatch):
    service, bundle, cutoff = isolated_service
    result = service._build_strategy_experiment_result("TEST_FIXTURE_input", bundle, cutoff, service.current_config())
    result["test_fixture_note"] = {"中文": "合成测试", "nested": [None, True, -1, 1.25]}
    checked = guard_full_result_digest(monkeypatch)
    with service.store.transaction() as connection:
        service._write_strategy_result(connection, result, created_at=cutoff.isoformat())
    saved = service.store.get_document("strategy_experiment_runs", result["run_id"])
    assert saved == result
    assert saved["prospective_validation"]["signal_run_persisted"] is True
    assert saved["content_hash"] in checked
    assert saved["content_hash"] == digest({key: value for key, value in saved.items() if key != "content_hash"})
