import pytest

from analysis.agents.base import EmployeeReport
from analysis.agents.cio import CIOAgent
from analysis.agents.cio_prompts import get_master_info
from analysis.agents.risk_manager import RiskManager
from prediction_report.report_generator import PredictionReportGenerator


def test_legacy_risk_veto_and_risk_score_survive_schema():
    risk = RiskManager()
    llm = risk._build_report(dict(outlook="看空", confidence="高", score=-8, risk_score=9,
                                  veto=True, veto_reason="liquidity", key_points=[], risks=[]))
    assert llm.veto and llm.risk_score == 9 and llm.veto_reason == "liquidity"
    rule = risk._rule_analyze({"technical_indicators": {"amplitude": 10, "volume_ratio": .1}})
    assert rule.veto and rule.risk_score >= 7
    assert risk._build_report({}).status == "INVALID"


def test_legacy_empty_json_is_invalid_not_valid_neutral():
    report = EmployeeReport(raw_output="{}")
    assert report.status == "INVALID" and report.error


def test_negative_var_stop_take_and_percent_units():
    cio = CIOAgent()
    report = EmployeeReport(employee_id="technical", outlook="看多", confidence="高", score=5)
    result = cio._fallback_decision(get_master_info("buffett"), [report], {"quote": {"price": 1000}, "var_95": -3})
    assert result.order["stop_loss"]["level"] == 955
    assert result.order["take_profit"]["level_1"] == 1060
    assert result.short_term["change_pct"] == 3
    assert result.to_dict()["authority"] == "RESEARCH_ONLY"


def test_legacy_veto_blocks_bullish_order():
    report = EmployeeReport(employee_id="risk", outlook="看多", confidence="高", score=5,
                            veto=True, veto_reason="test", risk_score=9)
    result = CIOAgent()._fallback_decision(get_master_info("buffett"), [report], {"quote": {"price": 1000}, "var_95": -3})
    assert result.order["position_size_pct"] == 0 and result.order["action"] == "观望"


@pytest.mark.parametrize("magnitude", [100, float("inf"), float("nan"), "invalid"])
def test_legacy_extreme_or_invalid_var_cannot_issue_bullish_order(magnitude):
    report = EmployeeReport(employee_id="technical", outlook="看多", confidence="高", score=5)
    result = CIOAgent()._fallback_decision(get_master_info("buffett"), [report],
                                         {"quote": {"price": 1000}, "var_95": magnitude})
    assert result.order["position_size_pct"] == 0 and result.order["action"] == "观望"
    assert result.order["stop_loss"]["level"] == 0


def test_cio_invalid_probabilities_and_extreme_allocation_are_invalid():
    cio = CIOAgent()
    cases = {k: {"probability": .9} for k in ("base_case", "bull_case", "bear_case")}
    assert cio._parse_cio_result(cases, get_master_info("buffett")).error == "INVALID_SCENARIO_PROBABILITY_SUM"
    assert cio._parse_cio_result({"order": {"position_size_pct": 500}}, get_master_info("buffett")).error == "INVALID_POSITION_SIZE"


def test_report_does_not_invent_confidence_and_escapes_html(tmp_path):
    generator = PredictionReportGenerator(str(tmp_path))
    assert generator._calc_confidence({}, {"status": "simulated"}) is None
    result = generator.generate({"symbol": "600000.SH", "stock_name": '<img src=x onerror="alert(1)">'})
    html = generator.to_html(result)
    assert '<img src=x' not in html
    assert '&lt;img src=x' in html
    assert '概率未校准' in html
