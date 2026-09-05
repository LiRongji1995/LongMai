from tests.integration.final_gate_helpers import tracker_api_gate, CLASSIFICATION


def test_recommendation_registered_7_30_90_outcomes_restart_api(tmp_path, monkeypatch):
    monkeypatch.delenv("STOCKFISH_API_KEY", raising=False)
    evidence = tracker_api_gate(tmp_path)
    assert evidence["classification"] == CLASSIFICATION
    assert evidence["storage_health"]["status"] == "healthy"
    assert len(evidence["record_and_observations_after"]["evaluations"]) == 3
