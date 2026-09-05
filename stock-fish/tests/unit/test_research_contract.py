import json
from datetime import datetime, timedelta, timezone

import pytest

from stockfish_core.research import Evidence, LLMConfiguration, ResearchEngine, ResearchSnapshot, paired_evaluation

NOW = datetime(2026, 9, 4, 8, tzinfo=timezone.utc)


class Store:
    def __init__(self):
        self.docs = {}

    def put_document(self, namespace, document_id, payload):
        self.docs[namespace, document_id] = payload


def snapshot():
    return ResearchSnapshot(snapshot_id="fixture", instrument_id="600000.SH", as_of=NOW,
        evidence=(Evidence(evidence_id="e1", category="technical", source="fixture", available_at=NOW,
                           content={"close": 10}, quality="VERIFIED"),))


def test_llm_mock_valid_and_audit_complete():
    store = Store()
    transport = lambda prompt, config: {"output": json.dumps(dict(summary="observed close", outlook="NEUTRAL",
        evidence_ids=["e1"], limitations=["not predictive"], research_score=0)), "usage": {"total_tokens": 30}}
    engine = ResearchEngine(store, LLMConfiguration(provider="mock", model="fixed-model", seed=42), transport)
    result = engine.analyze(snapshot(), run_id="r1")
    assert result.status == "VALID"
    assert result.contributes_to_final_score is False
    audit = store.docs["llm_audits", result.audit_id]
    assert audit["prompt_hash"] and audit["input_hash"] and audit["latency_ms"] >= 0
    assert audit["provider"] == "mock" and audit["seed"] == 42
    assert audit["usage"]["total_tokens"] == 30


@pytest.mark.parametrize("raw", ["nonsense", "{}", '{"summary":"fabricated", "outlook":"NEUTRAL", "evidence_ids":["unknown"], "limitations":[]}'])
def test_invalid_output_is_invalid_not_neutral(raw):
    store = Store()
    result = ResearchEngine(store, transport=lambda *_: {"output": raw}).analyze(snapshot(), run_id="r1")
    assert result.status == "INVALID"
    assert result.opinion is None
    assert store.docs["llm_audits", result.audit_id]["output"] == raw


def test_missing_llm_and_transport_failure_are_audited():
    store = Store()
    assert ResearchEngine(store).analyze(snapshot(), run_id="r1").error == "LLM_NOT_CONFIGURED"
    def fail(*args):
        raise TimeoutError("secret=do-not-log")
    result = ResearchEngine(store, transport=fail).analyze(snapshot(), run_id="r2")
    assert result.status == "UNAVAILABLE"
    assert "do-not-log" not in json.dumps(store.docs["llm_audits", result.audit_id])


def test_disk_failure_is_not_success():
    class Broken:
        def put_document(self, *args):
            raise OSError("disk full")
    with pytest.raises(OSError):
        ResearchEngine(Broken()).analyze(snapshot(), run_id="r1")


@pytest.mark.parametrize("quality", ["UNAVAILABLE", "UNVERIFIED", "PARTIAL", "DEGRADED", "MOCK", "RESEARCH_ONLY", "unknown", ""])
def test_unverified_evidence_cannot_generate_a_valid_llm_opinion(quality):
    store, calls = Store(), []
    def misleading_transport(*args):
        calls.append(1)
        return {"output": json.dumps(dict(summary="unsupported assertion", outlook="POSITIVE",
                      evidence_ids=["e1"], limitations=[], research_score=1))}
    original = snapshot()
    bad = original.model_copy(update={"evidence": (original.evidence[0].model_copy(update={"quality": quality}),)})
    result = ResearchEngine(store, transport=misleading_transport).analyze(bad, run_id="quality-boundary")
    assert result.status == "UNAVAILABLE" and result.error == "UNVERIFIED_EVIDENCE"
    assert result.opinion is None and result.contributes_to_final_score is False
    assert calls == []
    audit = store.docs["llm_audits", result.audit_id]
    assert audit["status"] == "UNAVAILABLE" and audit["output"] is None
    assert audit["input_snapshot"]["evidence"][0]["quality"] == quality


def test_mixed_and_empty_evidence_fail_before_any_llm_call():
    store = Store()
    def never_call(*args):
        raise AssertionError("unverified evidence must not reach an LLM")
    engine = ResearchEngine(store, transport=never_call)
    original = snapshot()
    bad = original.evidence[0].model_copy(update={"evidence_id": "e2", "quality": "UNAVAILABLE"})
    mixed = engine.analyze(original.model_copy(update={"evidence": (*original.evidence, bad)}), run_id="mixed")
    empty = engine.analyze(original.model_copy(update={"evidence": ()}), run_id="empty")
    assert mixed.status == "UNAVAILABLE" and mixed.error == "UNVERIFIED_EVIDENCE"
    assert empty.status == "UNAVAILABLE" and empty.error == "RESEARCH_EVIDENCE_UNAVAILABLE"


def test_valid_quality_remains_supported():
    original = snapshot()
    valid = original.model_copy(update={"evidence": (original.evidence[0].model_copy(update={"quality": "VALID"}),)})
    result = ResearchEngine(Store(), transport=lambda *_: {"output": json.dumps(dict(summary="verified fact",
        outlook="NEUTRAL", evidence_ids=["e1"], limitations=["fixture"], research_score=0))}).analyze(valid, run_id="valid-quality")
    assert result.status == "VALID"


def test_paired_experiments_keep_all_arms_and_reject_label_returns():
    value = dict(input_hash="same", policy_version="v1", execution_method="CASH_NAV", **{"return": .01}, cost=5, max_drawdown=-.02)
    arms = {name: value for name in ("quant_only", "quant_research", "quant_research_llm")}
    result = paired_evaluation({"loser_also_kept": arms})
    assert len(result["observations"]) == 3
    assert all(r["paired_return_difference"] == 0 for r in result["observations"])
    assert result["llm_voting_enabled"] is False
    with pytest.raises(ValueError):
        paired_evaluation({"bad": {n: value | {"execution_method": "PROCESSED_LABEL"} for n in arms}})
