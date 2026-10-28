"""TEST_FIXTURE configuration boundary for explicit real collection recovery."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import codex_recovered_premarket_v1 as entry


@pytest.fixture
def configuration(tmp_path):
    root = tmp_path / "TEST_FIXTURE_recovered_entry"
    root.mkdir()
    path = root / "configuration.json"
    value = dict(state_root=str(root), limits=dict(entry.CAPS), capture=dict(
        offline=False, retry_failed=False), seed_sources=[])
    path.write_text(json.dumps(value))
    runtime = SimpleNamespace(profile=dict(config=str(path), state_root=str(root)))
    return path, value, runtime


def test_explicit_finite_configuration_keeps_actual_values(configuration):
    _, value, runtime = configuration
    result = entry.configuration(runtime, entry.PROFILE)
    assert result["limits"] == value["limits"]
    assert result["capture"] == value["capture"]
    with pytest.raises(ValueError, match="EXPLICIT_COLLECTION_PROFILE"):
        entry.configuration(runtime, "default")


@pytest.mark.parametrize("change,reason", [
    ({"capture": {"allow_test_fixture": True}}, "REAL_SOURCE_REQUIRED"),
    ({"capture": {"retry_failed": True}}, "AUTOMATIC_RETRY_FORBIDDEN"),
    ({"seed_sources": ["TEST_FIXTURE_unverified_seed.json"]}, "SEEDS_REQUIRE_SEPARATE"),
    ({"state_root": "/TEST_FIXTURE_foreign_state"}, "STATE_BINDING"),
])
def test_new_epoch_cannot_grant_unrelated_authority(configuration, change, reason):
    path, value, runtime = configuration
    value.update(change)
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match=reason):
        entry.configuration(runtime, entry.PROFILE)


@pytest.mark.parametrize("key", sorted(entry.CAPS))
def test_each_explicit_limit_is_bounded(configuration, key):
    path, value, runtime = configuration
    value["limits"][key] = entry.CAPS[key] + 1
    path.write_text(json.dumps(value))
    reason = "PROVIDER_TIMEOUT_MUST_BE_0_TO_90_SECONDS" if key == "request_timeout_seconds" else "PROFILE_LIMIT_EXCEEDED"
    with pytest.raises(ValueError, match=reason):
        entry.configuration(runtime, entry.PROFILE)


def test_unlimited_time_not_accepted(configuration):
    path, value, runtime = configuration
    value["limits"]["max_seconds"] = None
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="PROFILE_LIMIT_EXCEEDED"):
        entry.configuration(runtime, entry.PROFILE)


def test_unattested_runtime_cannot_claim_or_start_worker(configuration):
    _, _, runtime = configuration
    with pytest.raises(ValueError, match="ATTESTED_RUNTIME_REQUIRED"):
        entry.run(runtime, SimpleNamespace())


def test_wrong_calendar_dates_refused_without_network(tmp_path, monkeypatch):
    from datetime import datetime, timedelta
    from stockfish_core.yaogu import premarket_capture_cache_v1 as native
    root = tmp_path / "TEST_FIXTURE_calendar_gate"
    for name in ("index", "raw", "raw-index", "locators"):
        (root / "cache" / name).mkdir(parents=True)
    now = datetime.now(entry.frozen.SH)
    observation = now.date() if now.hour >= 18 else now.date()-timedelta(days=1)
    rows = [dict(calendar_date=str(observation+timedelta(days=i)), is_trading_day="1")
            for i in range(-3, 6)]
    calls = []
    class FixtureCache:
        def __init__(self, path, budget, config, executor):
            assert config["offline"] is True and config["retry_failed"] is False
            with pytest.raises(ValueError, match="NETWORK_FORBIDDEN"):
                executor()
        def calendar_reference(self, now): return "TEST_FIXTURE"
        def verify(self, record): return dict(status="COMPLETE", pagination_complete=True, rows=rows)
        def terminal(self): calls.append("terminal")
    monkeypatch.setattr(native, "CaptureCache", FixtureCache)
    with pytest.raises(ValueError, match="LATEST_COMPLETED_SESSION_CHANGED"):
        entry.calendar_gate(dict(state=root), dict(capture={}), str(observation-timedelta(days=1)),
                            str(observation+timedelta(days=1)), entry.Budget(entry.CAPS))
    assert calls == []
