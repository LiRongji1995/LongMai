from tests.integration.final_gate_helpers import paired_cash_nav_gate, CLASSIFICATION


def test_three_research_arms_use_real_cash_nav_and_same_live_policy(tmp_path):
    evidence = paired_cash_nav_gate(tmp_path)
    assert evidence["classification"] == CLASSIFICATION
    observations = evidence["paired_accounting"]["observations"]
    assert len(observations) == 9
    assert sum(row["return"] < 0 for row in observations) == 6
    assert all(row["paired_return_difference"] == 0 for row in observations)
