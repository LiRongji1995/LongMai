"""R16 correctness boundary, contract and shadow foundation tests."""
from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal
import hashlib
import importlib.util
import json
from pathlib import Path

from tests.yaogu.fixtures import TZ, make_bundle
from stockfish_core.common import canonical_json
from stockfish_core.storage import SQLiteStore
from stockfish_core.yaogu.contracts import DailyBar, digest
from stockfish_core.yaogu.derived_feature_store import (
    MATERIALIZED_FAMILIES,
    DerivedFeatureStore,
    _dec,
    build_missing_features,
    dependency_plan_for_context,
    identity_for_context,
)
from stockfish_core.yaogu.feature_evidence_contract import (
    compare_facts,
    facts_from_run,
    validate_facts,
    validate_manifest,
    write_contract_snapshot,
)
from stockfish_core.yaogu.history_artifacts import HistoryArtifactStore, compact_input
from stockfish_core.yaogu.source_resolution import canonical_price_source_view, source_token
from stockfish_core.yaogu.strategy_experiments import _prepare_context, run_active_daily_strategy


ROOT = Path(__file__).resolve().parents[2]
GOLDEN_MANIFEST = ROOT / "tests" / "fixtures" / "r16_equivalence" / "golden_manifest.json"
AUDIT_SCRIPT = ROOT / "scripts" / "r16_history_stability_audit.py"


def _slice_bundle(bundle, index):
    day = bundle.sessions[index]
    return (
        bundle.model_copy(update={
            "observation_date": day,
            "sessions": tuple(session for session in bundle.sessions if session <= day),
            "bars": tuple(bar for bar in bundle.bars if bar.session <= day),
        }),
        datetime.combine(day, time(20, 30), TZ),
    )


def _compact(bundle, store):
    compact = compact_input(bundle, store, recent_sessions=6)
    return compact


def _store_candidate_input(store, bundle):
    payload = bundle.model_dump(mode="json")
    content_hash = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    input_id = "market_" + content_hash
    store.put_document("candidate_inputs", input_id, payload)
    store.put_document("candidate_input_indexes", input_id, {"observation_date": str(bundle.observation_date)})
    return input_id


def _audit_module():
    spec = importlib.util.spec_from_file_location("r16_history_stability_audit", AUDIT_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _bar_with_revision(bar, *, published_hour, close):
    stamp = datetime.combine(bar.session, time(published_hour, 0), TZ)
    value = bar.model_dump(mode="json")
    value.update({
        "close": close,
        "high": max(Decimal(str(value["high"])), Decimal(str(close))),
        "comparable_close": close,
        "comparable_high": max(Decimal(str(value["comparable_high"])), Decimal(str(close))),
        "source_published_at": stamp,
        "first_seen_at": stamp,
        "ingested_at": stamp,
        "source_refs": tuple(value.get("source_refs") or ("fixture-bars",)),
    })
    return DailyBar.model_validate(value)


def _fingerprint(ctx, iid, session):
    view = canonical_price_source_view(ctx, [session], [iid])
    plan = dependency_plan_for_context(ctx, [session], [iid], source_view=view)
    return plan[(iid, str(session))]["PRICE_BASE"]["dependency_fingerprint"], view


def test_r16_append_only_source_view_keeps_prior_session_tokens():
    bundle, _ = make_bundle(3)
    day_t, cutoff_t = _slice_bundle(bundle, -2)
    day_tp1, cutoff_tp1 = _slice_bundle(bundle, -1)
    ctx_t = _prepare_context(day_t, cutoff_t)
    ctx_tp1 = _prepare_context(day_tp1, cutoff_tp1)
    iid = ctx_t.eligible_ids[0]
    prior = str(day_t.observation_date)
    view_t = canonical_price_source_view(ctx_t, [prior], [iid])
    view_tp1 = canonical_price_source_view(ctx_tp1, [prior], [iid])

    assert source_token(view_t, iid, prior)["row_hash"] == source_token(view_tp1, iid, prior)["row_hash"]
    assert source_token(view_t, iid, prior)["value_hash"] == source_token(view_tp1, iid, prior)["value_hash"]


def test_r16_inline_higher_revision_changes_identity_and_payload(tmp_path):
    bundle, cutoff = make_bundle(1)
    compact = _compact(bundle, SQLiteStore(tmp_path / "history.sqlite3"))
    target = compact.bars[-1]
    revised = _bar_with_revision(target, published_hour=16, close=Decimal("99.00"))
    with_inline = compact.model_copy(update={"bars": tuple(compact.bars) + (revised,)})
    with_inline._history_store = compact._history_store
    ctx_base = _prepare_context(compact, cutoff)
    ctx_revised = _prepare_context(with_inline, cutoff)
    iid = ctx_revised.eligible_ids[0]
    session = str(target.session)

    base_fingerprint, _base_view = _fingerprint(ctx_base, iid, session)
    revised_fingerprint, revised_view = _fingerprint(ctx_revised, iid, session)
    identity = identity_for_context(ctx_revised, "TEST_INPUT_HASH")
    plan = dependency_plan_for_context(ctx_revised, [session], [iid], source_view=revised_view)
    built = build_missing_features(ctx_revised, identity, [(iid, session)], dependency_plan=plan, source_view=revised_view)

    assert revised_fingerprint != base_fingerprint
    assert source_token(revised_view, iid, session)["source_kind"] == "INLINE_MARKET_INPUT"
    assert _dec(built[0]["features"]["comparable_close"]) == 99


def test_r16_inline_lower_revision_does_not_change_identity(tmp_path):
    bundle, cutoff = make_bundle(1)
    compact = _compact(bundle, SQLiteStore(tmp_path / "history.sqlite3"))
    target = compact.bars[-1]
    lower = _bar_with_revision(target, published_hour=14, close=Decimal("88.00"))
    with_inline = compact.model_copy(update={"bars": tuple(compact.bars) + (lower,)})
    with_inline._history_store = compact._history_store
    ctx_base = _prepare_context(compact, cutoff)
    ctx_lower = _prepare_context(with_inline, cutoff)
    iid = ctx_lower.eligible_ids[0]
    session = str(target.session)

    base_fingerprint, _ = _fingerprint(ctx_base, iid, session)
    lower_fingerprint, lower_view = _fingerprint(ctx_lower, iid, session)

    assert lower_fingerprint == base_fingerprint
    assert source_token(lower_view, iid, session)["values"]["comparable_close"] != "88.00"


def test_r16_future_revision_is_not_visible_to_historical_replay(tmp_path):
    bundle, cutoff = make_bundle(1)
    compact = _compact(bundle, SQLiteStore(tmp_path / "history.sqlite3"))
    target = compact.bars[-1]
    future_stamp = datetime.combine(target.session, time(23, 0), TZ)
    value = target.model_dump(mode="json")
    value.update({
        "close": Decimal("77.00"),
        "comparable_close": Decimal("77.00"),
        "source_published_at": future_stamp,
        "first_seen_at": future_stamp,
        "ingested_at": future_stamp,
    })
    future = DailyBar.model_validate(value)
    with_future = compact.model_copy(update={"bars": tuple(compact.bars) + (future,)})
    with_future._history_store = compact._history_store
    ctx_base = _prepare_context(compact, cutoff)
    ctx_future = _prepare_context(with_future, cutoff)
    iid = ctx_future.eligible_ids[0]
    session = str(target.session)

    base_fingerprint, _ = _fingerprint(ctx_base, iid, session)
    future_fingerprint, future_view = _fingerprint(ctx_future, iid, session)

    assert future_fingerprint == base_fingerprint
    assert source_token(future_view, iid, session)["values"]["comparable_close"] != "77.00"


def test_r16_cache_lifecycle_separates_new_from_source_revision(tmp_path):
    store = DerivedFeatureStore(SQLiteStore(tmp_path / "cache.sqlite3"))
    bundle, cutoff = make_bundle(1)
    ctx = _prepare_context(bundle, cutoff)
    identity = identity_for_context(ctx, "TEST_INPUT_HASH")
    iid = "TEST000"
    session = "2026-01-01"
    row = {
        "instrument_id": iid,
        "session": session,
        "comparable_basis": "TEST",
        "dependency_identities": {
            family: {
                "required_source_session_range": {"start": session, "end": session, "sessions": [session]},
                "dependency_fingerprint": "OLD_" + family,
                "comparable_basis_identity": "TEST_BASIS",
                "pit_policy_identity": "TEST_PIT",
                "provider_revision_identity": "TEST_PROVIDER_OLD",
            }
            for family in MATERIALIZED_FAMILIES
        },
        "features": {"comparable_close": {"numerator": 10, "denominator": 1}, "returns": {}, "ma": {}, "ma_reasons": {}, "amount_change": {}},
    }
    store.put_many(identity, [row])
    revised_plan = {
        (iid, session): {
            family: {
                "required_source_session_range": {"start": session, "end": session, "sessions": [session]},
                "dependency_fingerprint": "NEW_" + family,
                "comparable_basis_identity": "TEST_BASIS",
                "pit_policy_identity": "TEST_PIT",
                "provider_revision_identity": "TEST_PROVIDER_NEW",
            }
            for family in MATERIALIZED_FAMILIES
        },
        ("BRANDNEW", session): {
            family: {
                "required_source_session_range": {"start": session, "end": session, "sessions": [session]},
                "dependency_fingerprint": "BRAND_NEW_" + family,
                "comparable_basis_identity": "TEST_BASIS",
                "pit_policy_identity": "TEST_PIT",
                "provider_revision_identity": "TEST_PROVIDER_NEW",
            }
            for family in MATERIALIZED_FAMILIES
        },
    }
    assert store.get_many(identity, [iid, "BRANDNEW"], [session], dependency_plan=revised_plan) == {}

    lifecycle = store.last_get_many_stats["family_lifecycle"]
    assert lifecycle["INVALIDATED_SOURCE_REVISION"] == len(MATERIALIZED_FAMILIES)
    assert lifecycle["NEW"] == len(MATERIALIZED_FAMILIES)
    assert store.last_get_many_stats["row_lifecycle"]["invalidated_rows"] == 1
    assert store.last_get_many_stats["row_lifecycle"]["new_rows"] == 1


def test_r16_normalizer_append_base_preserves_comparable_rows(tmp_path):
    from tests.r03_sources.test_provider_boundaries import DAY, NOW, SESSIONS, base, factor, raw, result
    from stockfish_core.yaogu.provider_normalization import normalize_capture_with_report

    owner = HistoryArtifactStore(SQLiteStore(tmp_path / "history.sqlite3"))
    factors = result(
        "adjustment_factors",
        [factor() | {"code": "sh.600000"}],
        {"codes": ["sh.600000"], "start_date": "1990-01-01", "end_date": str(DAY)},
    )
    base_bundle, _ = normalize_capture_with_report(base() + [factors], DAY, NOW, SESSIONS, history_store=owner)
    raw_only = base()
    raw_only[-1] = result(
        "market_daily",
        [raw()],
        {"date": str(DAY)},
        stamp=NOW - timedelta(seconds=10),
        identity="raw-only-delta",
    )
    delta_bundle, report = normalize_capture_with_report(
        raw_only,
        DAY,
        NOW,
        SESSIONS,
        history_store=owner,
        history_base_ref=base_bundle.history_artifact_ref,
    )
    ctx = _prepare_context(delta_bundle, NOW)
    view = canonical_price_source_view(ctx, [DAY], ["600000.SH"])
    token = source_token(view, "600000.SH", str(DAY))

    assert report["history_artifact"]["base_history_artifact_ref"] == base_bundle.history_artifact_ref
    assert token["values"]["comparable_close"] == "22"
    assert token["values"]["comparable_basis"].startswith("BAOSTOCK_HFQ_RAW_X_BACK_FACTOR_R03_V1")


def test_r16_history_audit_set_based_progress_and_canonical_checks(tmp_path):
    module = _audit_module()
    bundle, _ = make_bundle(4)
    day_t, cutoff_t = _slice_bundle(bundle, -2)
    day_tp1, cutoff_tp1 = _slice_bundle(bundle, -1)
    store = SQLiteStore(tmp_path / "audit.sqlite3")
    compact_t = compact_input(day_t, store, recent_sessions=1)
    compact_tp1 = compact_input(day_tp1, store, recent_sessions=1)
    input_t = _store_candidate_input(store, compact_t)
    input_tp1 = _store_candidate_input(store, compact_tp1)
    checkpoint = tmp_path / "audit.checkpoint.json"

    result = module.audit(
        str(store.path),
        input_t,
        input_tp1,
        cutoff_t=cutoff_t.isoformat(),
        cutoff_tp1=cutoff_tp1.isoformat(),
        instrument_limit=2,
        checkpoint_json=checkpoint,
        progress_interval_seconds=0,
        canonical_batch_size=1,
        canonical_session_batch_size=1,
    )

    assert result["audit_version"] == "R16_FAST_SET_BASED_HISTORY_STABILITY_AUDIT_V3"
    assert result["comparison_scope"]["comparison_method"].startswith("SET_BASED_SELECTED_SOURCE_RECORDS")
    assert result["comparison_scope"]["canonical_verification_method"] == "SESSION_BATCHED_FULL_CANONICAL_SOURCE_VIEW"
    assert result["comparison_scope"]["total_pairs"] > 0
    assert result["append_only_invariant"]["status"] == "PASS"
    assert result["targeted_canonical_resolver_checks"]["status"] == "PASS"
    assert result["targeted_canonical_resolver_checks"]["all_discovered_discrepancies_checked"] is True
    assert checkpoint.exists()
    assert json.loads(checkpoint.read_text(encoding="utf-8"))["stage"] == "DONE"


def test_r16_contract_export_validation_and_comparison(tmp_path):
    bundle, cutoff = make_bundle(2)
    run = run_active_daily_strategy(bundle, cutoff=cutoff, input_id="TEST_FIXTURE_R16")
    files = write_contract_snapshot(run, tmp_path)
    manifest = __import__("json").loads(files["manifest"].read_text(encoding="utf-8"))
    facts = facts_from_run(run)

    assert validate_manifest(manifest)["valid"] is True
    assert validate_facts(facts)["valid"] is True
    comparison = compare_facts(facts, facts)
    assert comparison["matched"] is True
    assert comparison["left_hash"] == comparison["right_hash"]
    assert files["manifest_hash"] == manifest["manifest_hash"]
    assert digest({"contract_version": manifest["contract_version"]})


def test_r16_golden_fixture_manifest_covers_required_cases():
    manifest = json.loads(GOLDEN_MANIFEST.read_text(encoding="utf-8"))
    required = {
        "NORMAL_DAY_T_FROZEN_SNAPSHOT",
        "APPEND_ONLY_T_TO_T_PLUS_1",
        "SOURCE_REVISION_CASE",
        "MISSING_DATA_CASE",
        "HISTORICAL_PIT_REPLAY_CASE",
        "PERCENTILE_RANK_TIE_CASE",
        "INLINE_SOURCE_HIGHER_REVISION_CASE",
    }
    actual = {case["case_id"] for case in manifest["cases"]}

    assert manifest["contract_version"] == "FEATURE_EVIDENCE_CONTRACT_V1"
    assert required <= actual
    assert manifest["storage_policy"].endswith("no_market_database")
