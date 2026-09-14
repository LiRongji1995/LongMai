"""Original production oracle, exact injection, offset selection and frozen goldens."""
from copy import deepcopy
from datetime import timedelta
from fractions import Fraction
import json

import pytest

from scripts import r16_production_oracle as oracle
from stockfish_core.yaogu.contracts import DailyBar, MarketInput
from stockfish_core.yaogu.source_resolution import canonical_price_source_view, source_token


def _load(case, tmp_path):
    return oracle.load_case(oracle.DEFAULT_FIXTURES / (case + ".input.json"), state_dir=tmp_path)


def test_oracle_calls_original_production_functions_and_uses_exact_percent(tmp_path, monkeypatch):
    _, bundle, cutoff = _load("UNIT_AND_ROUNDING", tmp_path)
    ctx, rows, _ = oracle.freeze_source(bundle, cutoff)
    original_return, original_ma = oracle.tct._window_return_uncached, oracle.tct._ma_value_uncached
    calls = {"return": 0, "ma": 0}
    def returns(*args):
        calls["return"] += 1
        return original_return(*args)
    def ma(*args):
        calls["ma"] += 1
        return original_ma(*args)
    monkeypatch.setattr(oracle.tct, "_window_return_uncached", returns)
    monkeypatch.setattr(oracle.tct, "_ma_value_uncached", ma)
    facts = oracle.production_facts(ctx, rows)
    by_key = {(r["instrument"], r["session"], r["field"]): r for r in facts}
    two_percent = by_key[("TEST000", str(bundle.sessions[-2]), "ret1_pct")]
    tie = by_key[("TEST000", str(bundle.sessions[-1]), "ret1_pct")]
    assert two_percent["value"] == 2.0 and two_percent["unit"] == "percent"
    assert oracle.exact_fraction(two_percent) == 2
    assert oracle.exact_fraction(tie) == Fraction(24689, 20000)
    assert tie["value"] == 1.2344  # 1.23445 -> HALF_EVEN, not binary rounding.
    assert calls == {"return": len(rows), "ma": 2 * len(rows)}


@pytest.mark.parametrize("case", ["NORMAL", "TRADING_SESSION_GAP", "MISSING_UNKNOWN", "SOURCE_REVISION",
                                  "HISTORICAL_PIT_FUTURE_SOURCE", "INLINE_HIGHER_REVISION", "CROSS_OFFSET_REVISION"])
def test_frozen_production_goldens_are_read_only(case, tmp_path):
    _, bundle, cutoff = _load(case, tmp_path)
    ctx, rows, _ = oracle.freeze_source(bundle, cutoff)
    expected = json.loads((oracle.DEFAULT_FIXTURES / (case + ".expected.json")).read_text(encoding="utf-8"))
    assert oracle.golden_summary(oracle.production_facts(ctx, rows)) == expected["facts"]


def test_cross_offset_selection_matches_original_production_and_preserves_text(tmp_path):
    _, bundle, cutoff = _load("CROSS_OFFSET_REVISION", tmp_path)
    ctx = oracle.se._prepare_context(bundle, cutoff)
    token = source_token(canonical_price_source_view(ctx, [ctx.observation_date], ["TEST000"]), "TEST000", ctx.observation_date)
    assert str(oracle.se._bar(ctx, ctx.observation_date, "TEST000")["close"]) == "30"
    assert token["values"]["close"] == "30"
    assert token["source_revision"]["source_published_at"].endswith("08:00:00+00:00")


def test_same_instant_different_offsets_remain_conflicting_not_arbitrary_winner(tmp_path):
    _, bundle, cutoff = _load("NORMAL", tmp_path)
    payload = bundle.model_dump(mode="json")
    old = next(row for row in payload["bars"] if row["instrument_id"] == "TEST000" and row["session"] == str(bundle.observation_date))
    revised = deepcopy(old)
    revised.update(close="30", high="31", comparable_close="30")
    for field in ("source_published_at", "first_seen_at", "ingested_at"):
        revised[field] = old["session"] + "T07:00:00+00:00"
    payload["bars"].append(revised)
    # The public model already rejects duplicate revision instants. Exercise the
    # lower resolver defense independently, as history+inline can supply rows.
    with pytest.raises(ValueError, match="duplicate bar revision"):
        MarketInput.model_validate(payload)
    combined = bundle.model_copy(update={"bars": bundle.bars + (DailyBar.model_validate(revised),)})
    ctx = oracle.se._prepare_context(combined, cutoff)
    view = canonical_price_source_view(ctx, [ctx.observation_date], ["TEST000"])
    assert oracle.se._bar(ctx, ctx.observation_date, "TEST000") is None
    assert source_token(view, "TEST000", ctx.observation_date)["status"] == "CONFLICT"


def test_missing_basis_production_behavior_is_preserved_and_injection_rejects_domain(tmp_path):
    _, bundle, cutoff = _load("BASIS_UNSPECIFIED_NEGATIVE", tmp_path)
    ctx, rows, _ = oracle.freeze_source(bundle, cutoff)
    facts = oracle.production_facts(ctx, rows)
    current = next(r for r in facts if r["instrument"] == "TEST000" and r["session"] == str(bundle.observation_date) and r["field"] == "ret1_pct")
    assert current["value"] is not None and current["status"] == "KNOWN"
    keys = {(r["instrument"], r["session"], r["field"]) for r in facts}
    with pytest.raises(ValueError, match="COMPARABLE_BASIS_UNSPECIFIED"):
        oracle.validate_injection(facts, keys)


def test_window_provenance_retains_previous_parent_refs_and_actual_max_visibility(tmp_path):
    _, bundle, cutoff = _load("NORMAL", tmp_path)
    payload = bundle.model_dump(mode="json")
    prior = next(r for r in payload["bars"] if r["instrument_id"] == "TEST000" and r["session"] == str(bundle.sessions[-2]))
    receipt = deepcopy(next(r for r in payload["receipts"] if r["receipt_id"] == prior["source_refs"][0]))
    receipt["receipt_id"] = "TEST_FIXTURE_LATE_PRIOR_PARENT"
    late = cutoff - timedelta(minutes=10)
    for key in ("source_published_at", "first_seen_at", "ingested_at"):
        receipt[key] = late.isoformat()
        prior[key] = late.isoformat()
    payload["receipts"].append(receipt)
    prior["source_refs"] = [receipt["receipt_id"]]
    _, rows, manifest = oracle.freeze_source(MarketInput.model_validate(payload), cutoff)
    current = next(r for r in rows if r["instrument"] == "TEST000" and r["session"] == str(bundle.observation_date))
    for field in ("ret1_pct", "MA20", "MA60"):
        proof = current["field_provenance"][field]
        assert receipt["receipt_id"] in proof["source_refs"]
        assert oracle._clock(proof["visible_at"]) == late
        tokens = {oracle.digest(t) for t in manifest["tokens"]}
        assert all(d["token_hash"] in tokens for d in proof["dependencies"])


def test_business_slice_exact_injection_and_real_counterfactual(tmp_path):
    _, bundle, cutoff = _load("BUSINESS_SLICE_RANK_TIE", tmp_path)
    ctx, rows, _ = oracle.freeze_source(bundle, cutoff)
    facts = oracle.production_facts(ctx, rows)
    result = oracle.assert_business_slice(bundle, cutoff, facts, deepcopy(facts))
    assert len(set(result["hashes"].values())) == 1
    assert result["native"]["counts"]["WATCH"] > 0
    ordered = {r["instrument_id"]: r for r in result["native"]["items"]}
    assert ordered["TEST001"]["setup_rank_features"] == ordered["TEST005"]["setup_rank_features"]
    assert ordered["TEST001"]["rank"] < ordered["TEST005"]["rank"]
    after = next(r for r in result["counterfactual"]["after"]["items"] if r["instrument_id"] == "TEST000")
    assert after["decision_status"] == "REJECTED"
    assert "SAM_DECISION_TREND_HARD_FAIL" in after["reason_codes"]
    assert len(result["invalid_injections"]) == 3
