from datetime import timedelta

from stockfish_core.service import ApplicationService, RecommendationRequest
from stockfish_core.storage import SQLiteStore
from tests.integration.core_fixture import fixture_service, ASOF, DECISION, IIDS
import pytest
from stockfish_core.risk import Holding


def test_actual_core_train_rank_risk_persist_retrieve_replay_restart(tmp_path):
    core, snapshot_id, model = fixture_service(tmp_path)
    try:
        result = core.recommend(RecommendationRequest(snapshot_id=snapshot_id, model_id=model.model_id,
            instruments=IIDS, mode="REPLAY", decision_time=DECISION, cash=100000))
        assert result["quality"] == "RESEARCH_ONLY"
        final = result["final_recommendation"]
        assert len(final["items"]) == 3
        records = core.tracker.list_history(run_id=result["run_id"])
        assert len(records) == 3
        assert all(r["status"] == "RESEARCH_ONLY" and r["metadata"]["input_kind"] == "TEST_FIXTURE" for r in records)
        replay = core.replay_recommendation(result["recommendation_run_id"])
        assert replay.presentation() == final
        record_id = records[0]["recommendation_id"]
        unchanged = core.tracker.get(record_id)
    finally:
        core.close()
    restarted = ApplicationService(tmp_path / "core", store=SQLiteStore(tmp_path / "state.sqlite3"), data_dir=tmp_path / "qlib-data")
    try:
        assert restarted.tracker.get(record_id) == unchanged
        assert restarted.replay_recommendation(result["recommendation_run_id"]).presentation() == final
    finally:
        restarted.close()


def test_live_cannot_backdate_decision_to_bypass_staleness(tmp_path):
    core, sid, model = fixture_service(tmp_path)
    try:
        with pytest.raises(ValueError, match="historical decision_time"):
            core.recommend(RecommendationRequest(snapshot_id=sid, model_id=model.model_id, instruments=IIDS,
                                                  mode="RESEARCH", decision_time=DECISION, cash=100000))
    finally:
        core.close()


def test_other_holding_cannot_inflate_nav_with_untrusted_price(tmp_path):
    core, sid, model = fixture_service(tmp_path)
    forged = Holding(instrument_id=IIDS[1], shares=100000, sellable_shares=100000,
                      mark_price=1000, industry="made-up", marked_at=ASOF)
    try:
        with pytest.raises(ValueError, match="holding mark/industry/time"):
            core.recommend(RecommendationRequest(snapshot_id=sid, model_id=model.model_id, instruments=(IIDS[0],),
                                                  mode="REPLAY", decision_time=DECISION, cash=100000, holdings=(forged,)))
    finally:
        core.close()


def test_missing_quant_features_do_not_turn_into_empty_liquidation(tmp_path):
    core, sid, model = fixture_service(tmp_path)
    try:
        original = core.snapshot(sid)
        partial = original.model_copy(update={"features": original.features[:1]})
        partial_id = core.register_snapshot(partial)
        holding = Holding(instrument_id=IIDS[1], shares=1000, sellable_shares=0,
                          mark_price=10, industry=IIDS[1], marked_at=ASOF)
        result = core.recommend(RecommendationRequest(snapshot_id=partial_id, model_id=model.model_id,
            instruments=IIDS[1:], mode="REPLAY", decision_time=DECISION, cash=100000, holdings=(holding,)))
        assert len(result["quant"]["rejected"]) == 2
        assert result["quality"] == "UNAVAILABLE"
        assert result["final_recommendation"]["target_weights"] is None
        assert result["count"] == 0
    finally:
        core.close()


def test_explicit_empty_universe_is_not_replaced_with_default_candidates(tmp_path):
    core, sid, model = fixture_service(tmp_path)
    try:
        result = core.recommend(RecommendationRequest(snapshot_id=sid, model_id=model.model_id,
            instruments=(), mode="REPLAY", decision_time=DECISION, cash=100000))
        assert result["quality"] == "EMPTY"
        assert result["quant"]["scores"] == []
        assert result["final_recommendation"]["target_weights"] == {}
    finally:
        core.close()


def test_empty_target_cannot_liquidate_using_stale_snapshot(tmp_path):
    core, sid, model = fixture_service(tmp_path)
    try:
        holding = Holding(instrument_id=IIDS[0], shares=100, sellable_shares=100,
                          mark_price=10, industry=IIDS[0], marked_at=ASOF)
        result = core.recommend(RecommendationRequest(snapshot_id=sid, model_id=model.model_id,
            instruments=(), mode="REPLAY", decision_time=DECISION + timedelta(days=14),
            cash=99000, holdings=(holding,)))
        assert result["quality"] == "UNAVAILABLE"
        assert result["final_recommendation"]["target_weights"] is None
        assert "STALE_PORTFOLIO_MARK" in result["final_recommendation"]["reason_codes"]
    finally:
        core.close()


def test_model_metadata_schema_evolution_preserves_immutable_history(tmp_path):
    from stockfish_core.common import digest
    core, sid, model = fixture_service(tmp_path)
    old = model.model_dump(mode="json", exclude={"training_information_cutoff", "source_code_hash"})
    legacy_id = f"{model.model_id}:{model.model_version}"
    core.store.put_document("model_artifacts", legacy_id, old)
    try:
        result = core.recommend(RecommendationRequest(snapshot_id=sid, model_id=model.model_id,
            instruments=IIDS, mode="REPLAY", decision_time=DECISION, cash=100000))
        assert result["quality"] == "RESEARCH_ONLY"
        assert core.store.get_document("model_artifacts", legacy_id) == old
        assert core.store.get_document("model_artifacts", "model_" + digest(model)) == model.model_dump(mode="json")
        assert core.replay_recommendation(result["recommendation_run_id"]).presentation() == result["final_recommendation"]
    finally:
        core.close()
