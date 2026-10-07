"""TEST_FIXTURE causal equivalence and prepared-model performance boundaries."""
from datetime import date, datetime, timedelta
from time import perf_counter
from zoneinfo import ZoneInfo

from pydantic import ValidationError
import pytest

from stockfish_live.calendar import TradingCalendar
from stockfish_live.common import LiveError
from stockfish_live.contracts.forecast import ForecastConfig, HistoricalState, MatureOutcome, PathPoint
from stockfish_live.forecast.engine import ForecastEngine, horizon_targets, state_from_market_state
from stockfish_live.models.similarity import PreparedHistory, SimilarityModel, eligible_history

TZ = ZoneInfo("Asia/Shanghai")
START = date(2026, 6, 1)


def at(day, hour=9, minute=45, second=0):
    return datetime.combine(START+timedelta(days=day), datetime.min.time(), TZ).replace(
        hour=hour, minute=minute, second=second)


def calendar():
    return TradingCalendar(schema_version="LIVE_CALENDAR_V1", sessions=tuple(at(i).date() for i in range(21)),
        coverage_start=START, coverage_end=at(20).date(), exchanges=("SH", "SZ"),
        source="TEST_FIXTURE_PREPARED_HISTORY_EXPLICIT_SESSIONS", available_at=at(-1), pit_status="TEST_FIXTURE")


def state(day, *, identity="000001.SH", hour=9, minute=45, value=None, pit="TEST_FIXTURE", **updates):
    payload = dict(state_id=f"TEST_FIXTURE_PREPARED_{day}_{identity}_{hour}_{minute}", instrument_id=identity,
        decision_at=at(day,hour,minute), available_at=at(day,hour,minute), current_price=100.,
        features={"x":float(day if value is None else value), "return_5m_pct":.01},
        context={"fixture":{"tag":"TEST_FIXTURE_ORIGINAL_CONTEXT"}}, pit_status=pit)
    return HistoricalState(**(payload|updates))


def labels(item, *, result=.01, late=None, pit=None):
    return tuple(MatureOutcome(state_id=item.state_id, instrument_id=item.instrument_id,
        decision_at=item.decision_at, horizon=key, target_at=target, available_at=late or target,
        anchor_price=item.current_price, target_price=item.current_price*(1+result), regime="TREND_UP",
        pit_status=pit or item.pit_status) for key,target in horizon_targets(calendar(),item.decision_at).items())


def engine():
    return ForecastEngine(calendar(), ForecastConfig(k=3,min_samples=3,feature_names=("x",)))


def test_prepared_and_raw_forecasts_match_including_normalization_hash_and_neighbors():
    states = tuple(state(day) for day in range(5))
    outcomes = tuple(label for day,item in enumerate(states) for label in labels(item,result=(day-2)*.01))
    query = state(8)
    model = engine()
    prepared = model.prepare_history(states,outcomes)
    assert prepared.state_count == 5 and prepared.outcome_count == 25
    raw = model.predict(query,states,outcomes,as_of=query.decision_at)
    assert model.predict(query,prepared,as_of=query.decision_at) == raw
    assert model.predict(query,prepared,as_of=query.decision_at) == raw


def test_prepared_never_fits_future_same_day_other_index_other_time_or_feature_version():
    history = [state(day) for day in range(3)]
    query = state(8)
    ignored = [state(9,value=999999),state(8,value=999999),state(1,identity="399006.SZ",value=999999),
               state(1,hour=10,value=999999),state(1,value=999999,state_id="TEST_FIXTURE_OTHER_VERSION",feature_version="FUTURE_VERSION")]
    outcomes = tuple(y for s in history+ignored for y in labels(s))
    prepared = engine().prepare_history(history+ignored,outcomes)
    forecast = engine().predict(query,prepared,as_of=query.decision_at)
    for horizon in forecast.horizons:
        assert horizon.normalization_mean["x"] == 1.
        assert set(horizon.training_state_ids) == {s.state_id for s in history}


def test_prepared_late_label_values_do_not_affect_visible_forecast_or_hash():
    history = [state(day) for day in range(4)]
    query = state(8)
    ready = [y for s in history[:3] for y in labels(s)]
    late = labels(history[3],result=.01,late=at(12,16))
    changed = [y.model_copy(update={"target_price":999999.}) for y in late]
    model = engine()
    first = model.prepare_history(history,ready+list(late))
    second = model.prepare_history(history,ready+changed)
    assert first.history_hash != second.history_hash
    assert model.predict(query,first,as_of=query.decision_at) == model.predict(query,second,as_of=query.decision_at)


def test_prepared_label_maturity_changes_candidate_set_and_cache_cannot_reuse_future_fit():
    history = [state(day) for day in range(4)]
    query = state(8,available_at=at(8,9,45,3))
    ready = [y for s in history[:3] for y in labels(s)]
    delayed = labels(history[3],result=.4,late=at(8,9,45,4))
    prepared = engine().prepare_history(history,ready+list(delayed))
    first = engine().predict(query,prepared,as_of=at(8,9,45,3))
    second = engine().predict(query,prepared,as_of=at(8,9,45,4))
    assert {h.available_sample_size for h in first.horizons} == {3}
    assert {h.available_sample_size for h in second.horizons} == {4}
    assert first.horizons[0].model_hash != second.horizons[0].model_hash
    with pytest.raises(LiveError,match="FUTURE_FEATURE_ACCESS_DENIED"):
        engine().predict(query,prepared,as_of=query.decision_at)


def test_prepared_rows_are_detached_from_mutable_input_and_exposed_context():
    history = [state(day) for day in range(3)]
    outcomes = [y for s in history for y in labels(s)]
    query = state(8)
    model = engine()
    prepared = model.prepare_history(history,outcomes)
    expected = model.predict(query,prepared,as_of=query.decision_at)
    history[0].features["x"] = 999999.
    history[0].context["fixture"]["tag"] = "TEST_FIXTURE_CHANGED_CALLER_CONTEXT"
    candidates = eligible_history(query,prepared,(),as_of=query.decision_at,horizon="30m",feature_names=("x",))
    with pytest.raises(TypeError):
        candidates[0][0].features["x"] = 999999.
    candidates[0][0].context["fixture"]["tag"] = "TEST_FIXTURE_CHANGED_RETURN_CONTEXT"
    assert model.predict(query,prepared,as_of=query.decision_at) == expected


def test_prepared_revalidates_model_copy_and_conflicts_once_at_load():
    item = state(0)
    with pytest.raises(ValidationError):
        PreparedHistory((item.model_copy(update={"current_price":-1.}),),())
    with pytest.raises(LiveError,match="STATE_ID_CONFLICT"):
        PreparedHistory((item,item.model_copy(update={"current_price":99.})),())
    outcome = labels(item)[0]
    with pytest.raises(LiveError,match="OUTCOME_ID_CONFLICT"):
        PreparedHistory((item,),(outcome,outcome.model_copy(update={"target_price":102.})))
    with pytest.raises(LiveError,match="OUTCOME_HORIZON_MISMATCH"):
        wrong = outcome.model_copy(update={"target_at":outcome.target_at+timedelta(minutes=1),
                                          "available_at":outcome.available_at+timedelta(minutes=1)})
        engine().prepare_history((item,),(wrong,))


def test_every_fit_row_provenance_counts_even_outside_selected_k():
    history = [state(0,value=0,pit="VERIFIED_PIT"),state(1,value=1,pit="VERIFIED_PIT"),
               state(2,value=99999,pit="RESEARCH_ONLY")]
    query = state(8,value=0,pit="VERIFIED_PIT")
    outcomes = [y for s in history for y in labels(s)]
    model = SimilarityModel(ForecastConfig(k=1,min_samples=1,feature_names=("x",)))
    forecast = model.predict(query,PreparedHistory(history,outcomes),as_of=query.decision_at,
        horizon="30m",target_at=query.decision_at+timedelta(minutes=30))
    assert forecast.neighbors[0].state_id == history[0].state_id
    assert forecast.pit_status == "RESEARCH_ONLY"
    fixture = history[-1].model_copy(update={"pit_status":"TEST_FIXTURE"})
    contaminated = [*history[:-1],fixture]
    with pytest.raises(LiveError,match="DATA_PROVENANCE_MIXED"):
        model.predict(query,PreparedHistory(contaminated,[y for s in contaminated for y in labels(s)]),
            as_of=query.decision_at,horizon="30m",target_at=query.decision_at+timedelta(minutes=30))


def test_warm_prepared_prediction_does_not_revalidate_archive_or_refit_normalization(monkeypatch):
    import stockfish_live.models.similarity as module
    states = [state(day) for day in range(5)]
    outcomes = [y for s in states for y in labels(s)]
    query = state(8)
    model = engine()
    prepared = model.prepare_history(states,outcomes)
    expected = model.predict(query,prepared,as_of=query.decision_at)
    calls = {"historical":0,"outcome":0,"fit":0}
    validated_ids = set()
    historical_validate = HistoricalState.model_validate.__func__
    outcome_validate = MatureOutcome.model_validate.__func__
    normalization = module._normalization
    def historical(cls,*args,**kwargs):
        calls["historical"] += 1
        validated_ids.add(args[0]["state_id"])
        return historical_validate(cls,*args,**kwargs)
    def outcome(cls,*args,**kwargs):
        calls["outcome"] += 1
        return outcome_validate(cls,*args,**kwargs)
    def fit(*args,**kwargs):
        calls["fit"] += 1
        return normalization(*args,**kwargs)
    monkeypatch.setattr(HistoricalState,"model_validate",classmethod(historical))
    monkeypatch.setattr(MatureOutcome,"model_validate",classmethod(outcome))
    monkeypatch.setattr(module,"_normalization",fit)
    start = perf_counter()
    assert model.predict(query,prepared,as_of=query.decision_at) == expected
    elapsed = perf_counter()-start
    assert validated_ids == {query.state_id}
    assert calls["outcome"] == calls["fit"] == 0
    assert elapsed >= 0.  # Timing is reported separately, not asserted against host-specific speed.


def test_iso_market_state_clock_round_trips_without_rebasing_actual_capture():
    value = {"as_of":at(8,9,45,3).isoformat(),"pit_status":"TEST_FIXTURE",
        "indices":{"000001.SH":{"current_price":100.,"features":{"x":1.}}}}
    bridged = state_from_market_state(value,"000001.SH",at(8))
    assert bridged.available_at == at(8,9,45,3)
    assert bridged.decision_at == at(8)


def test_market_state_bridge_detaches_nested_context_before_transition_annotations():
    from stockfish_live.common import canonical_json
    value = {"as_of":at(8).isoformat(),"pit_status":"TEST_FIXTURE","config_hash":"TEST_FIXTURE_CONFIG",
        "sentiment":{"state":"UNKNOWN","inputs":{"advance_ratio":None}},
        "sectors":{"sectors":[{"sector_id":"TEST_FIXTURE_SECTOR","continuation_probability":None}]},
        "indices":{"000001.SH":{"current_price":100.,"features":{"x":1.}}}}
    bridged = state_from_market_state(value,"000001.SH",at(8))
    before = canonical_json(bridged)
    value["sentiment"]["inputs"]["advance_ratio"] = .8
    value["sentiment"]["next_state_probabilities"] = {"STRONG":1.}
    value["sectors"]["sectors"][0]["continuation_probability"] = .99
    assert canonical_json(bridged) == before


@pytest.mark.parametrize("prepared",[False,True])
def test_feature_configuration_hashes_never_mix_even_with_same_feature_version(prepared):
    history = [state(day,context={"configuration_hash":"TEST_FIXTURE_CONFIG_A"}) for day in range(3)]
    ignored = [state(3,value=999999,context={"configuration_hash":"TEST_FIXTURE_CONFIG_B"}),state(4,value=999999)]
    outcomes = [y for s in history+ignored for y in labels(s)]
    query = state(8,context={"configuration_hash":"TEST_FIXTURE_CONFIG_A"})
    model = engine()
    source = model.prepare_history(history+ignored,outcomes) if prepared else history+ignored
    forecast = model.predict(query,source,() if prepared else outcomes,as_of=query.decision_at)
    assert all(set(h.training_state_ids)=={s.state_id for s in history} for h in forecast.horizons)
    assert all(h.normalization_mean["x"]==1. for h in forecast.horizons)


def test_prepared_duplicate_business_decision_cannot_count_as_two_training_states():
    item = state(0)
    duplicate = item.model_copy(update={"state_id":"TEST_FIXTURE_OTHER_ID_SAME_DECISION"})
    with pytest.raises(LiveError,match="STATE_DECISION_CONFLICT"):
        PreparedHistory((item,duplicate),())


def test_walk_forward_uses_actual_same_minute_clock_and_excludes_next_minute_capture():
    from stockfish_live.evaluation.walk_forward import expanding_walk_forward
    states = [state(day,available_at=at(day,9,45,3)) for day in range(6)]
    stale = state(6,available_at=at(6,9,46))
    cross_day = state(7,available_at=at(8))
    outcomes = []
    for item in states+[stale,cross_day]:
        late = at(5,9,45,2) if item.state_id == states[3].state_id else (
            at(5,9,45,4) if item.state_id == states[4].state_id else None)
        outcomes.extend(labels(item,late=late))
    report = expanding_walk_forward(states+[stale,cross_day],outcomes,calendar(),
        ForecastConfig(k=2,min_samples=2,feature_names=("x",)),as_of=at(10,16),
        min_train_sessions=2,tune_k=False)
    target = [row for row in report["rows"] if row["state_id"] == states[-1].state_id]
    assert target
    for row in target:
        assert row["actual_as_of"] == at(5,9,45,3).isoformat()
        assert states[3].state_id in row["training_state_ids"]
        assert states[4].state_id not in row["training_state_ids"]
    skipped = {row["state_id"]:row["reason"] for row in report["skipped"]}
    assert skipped[stale.state_id] == skipped[cross_day.state_id] == "STALE_DECISION_CAPTURE"


def complete_labels(item):
    """Every trading minute and parent identity, including the lunch boundary."""
    from stockfish_live.common import digest
    result = []
    for label in labels(item):
        times = [tick for tick in calendar().minutes(label.target_at.date())
                 if item.decision_at < tick <= label.target_at]
        path = tuple(PathPoint(timestamp=tick, price=(label.target_price if n == len(times) else
            item.current_price+(label.target_price-item.current_price)*n/len(times)))
            for n, tick in enumerate(times, 1))
        result.append(label.model_copy(update={"future_path":path,
            "source_ids":tuple(digest({"TEST_FIXTURE_COMPLETE_PARENT":tick}) for tick in times)}))
    return tuple(result)


def test_lossless_prepared_full_paths_keep_original_canonical_forecast_and_model_hash():
    from stockfish_live.common import canonical_json, digest
    from stockfish_live.models.similarity import _normalization
    states = [state(day) for day in range(3)]
    outcomes = [label for item in states for label in complete_labels(item)]
    query = state(8)
    model = engine()
    prepared = model.prepare_history(states,outcomes)
    raw = model.predict(query,states,outcomes,as_of=query.decision_at)
    compact = model.predict(query,prepared,as_of=query.decision_at)
    assert canonical_json(compact) == canonical_json(raw)
    for horizon in compact.horizons:
        candidates = eligible_history(query,states,outcomes,as_of=query.decision_at,
            horizon=horizon.horizon,feature_names=("x",))
        means, scales = _normalization(candidates,("x",))
        original_hash = digest({"config":model.config.model_dump(mode="json"),"horizon":horizon.horizon,
            "training_states":[s.model_dump(mode="json") for s,_ in candidates],
            "labels":[label.model_dump(mode="json") for _,label in candidates],
            "features":("x",),"mean":means,"scale":scales})
        assert horizon.model_hash == original_hash
    for label in outcomes:
        cached = prepared.visible_label(label.state_id,label.horizon,as_of=query.decision_at)
        assert cached.path_point_count == len(label.future_path)
        assert cached.source_ids == label.source_ids
        assert cached.as_model() == label
        assert cached.canonical_bytes().decode() == canonical_json(label)
        assert isinstance(cached._payload,bytes)


def test_prepared_complete_path_is_detached_and_only_top_ten_are_reconstructed(monkeypatch):
    from stockfish_live.common import canonical_json
    from stockfish_live.models.similarity import _PreparedOutcome
    states = [state(day) for day in range(16)]
    outcomes = [complete_labels(item)[0] for item in states]
    query = state(20)
    prepared = engine().prepare_history(states,outcomes)
    model = SimilarityModel(ForecastConfig(k=15,min_samples=3,feature_names=("x",)))
    calls = []
    original = _PreparedOutcome.as_model
    def reconstruct(item):
        calls.append(item.state_id)
        return original(item)
    monkeypatch.setattr(_PreparedOutcome,"as_model",reconstruct)
    expected = model.predict(query,prepared,as_of=query.decision_at,
        horizon="30m",target_at=query.decision_at+timedelta(minutes=30))
    assert len(calls) == len(expected.neighbors) == 10
    assert expected.sample_size == 15 and expected.available_sample_size == 16
    assert set(calls) == {neighbor.state_id for neighbor in expected.neighbors}
    source = outcomes[15]
    source.future_path[0].__dict__["price"] = 999999.
    calls.clear()
    actual = model.predict(query,prepared,as_of=query.decision_at,
        horizon="30m",target_at=query.decision_at+timedelta(minutes=30))
    assert canonical_json(actual) == canonical_json(expected)
    assert len(calls) == 10
    cached = prepared.visible_label(source.state_id,"30m",as_of=query.decision_at)
    with pytest.raises(AttributeError):
        cached.target_price = 999999.


def test_prepared_rejects_corrupt_inner_pathpoint_from_bypassed_model_copy():
    source = state(0)
    label = complete_labels(source)[0]
    bad = label.future_path[0].model_copy(update={"price":-1.})
    with pytest.raises(ValidationError):
        engine().prepare_history((source,),(label.model_copy(update={"future_path":(bad,*label.future_path[1:])}),))
