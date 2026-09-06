"""One deterministic recommendation policy for API, UI, and historical replay."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .common import CoreModel, digest
from .risk import PortfolioSnapshot, RiskAction, RiskDecision, RiskEngine, RiskInput, aware


class Candidate(CoreModel):
    instrument_id: str
    raw_score: float
    quant_rank: int = Field(ge=1, strict=True)
    evidence_ids: tuple[str, ...] = ()


class RecommendationContext(CoreModel):
    run_id: str
    as_of: datetime
    decision_time: datetime
    earliest_trade_at: datetime
    model_id: str
    model_version: str
    checkpoint_hash: str
    feature_schema_hash: str
    data_snapshot_id: str
    data_version: str
    universe_version: str
    quant_quality: str
    model_validation: str
    data_license: str = "REVIEW_REQUIRED"
    market_regime: str = "UNCLASSIFIED"
    mode: Literal["PRODUCTION", "RESEARCH", "REPLAY"] = "RESEARCH"

    _time = field_validator("as_of", "decision_time", "earliest_trade_at")(aware)

    @model_validator(mode="after")
    def validate_order(self):
        if not self.as_of <= self.decision_time < self.earliest_trade_at:
            raise ValueError("require as_of <= decision_time < earliest_trade_at")
        return self


class RecommendationPolicy(CoreModel):
    version: str = "quant-hard-risk-v2"
    target_horizon: int = Field(default=30, ge=1, le=365, strict=True)
    benchmark: str = "000300.SH"
    entry_rule: str = "NEXT_TRADABLE_OPEN"
    # A raw model score has no calibrated return threshold. Only order is used.
    score_semantics: str = "MODEL_RAW_SCORE_ORDINAL_ONLY"
    research_has_voting_power: Literal[False] = False
    llm_has_voting_power: Literal[False] = False


class RecommendationItem(CoreModel):
    recommendation_id: str
    instrument_id: str
    quant_score: float
    quant_rank: int
    final_score: float | None = None
    final_rank: int | None = None
    confidence: None = None
    expected_return: None = None
    probability: None = None
    fundamental_score: float | None = None
    technical_score: float | None = None
    event_score: float | None = None
    llm_score: float | None = None
    risk_decision: RiskDecision
    reason_codes: tuple[str, ...]
    reason: str
    evidence_ids: tuple[str, ...]
    reference_price: float | None
    target_shares: int
    target_weight: float


class FinalRecommendation(CoreModel):
    recommendation_run_id: str
    context: RecommendationContext
    policy: RecommendationPolicy
    policy_hash: str
    risk_policy_hash: str
    input_hash: str
    status: Literal["READY", "RESEARCH_ONLY", "EMPTY", "UNAVAILABLE"]
    reason_codes: tuple[str, ...]
    items: tuple[RecommendationItem, ...]
    rejected: tuple[RecommendationItem, ...]

    @property
    def top20(self) -> tuple[RecommendationItem, ...]:
        return self.items[:20]

    @property
    def top5(self) -> tuple[RecommendationItem, ...]:
        return self.items[:5]

    @property
    def best_stock(self) -> RecommendationItem | None:
        return self.items[0] if self.items else None

    @property
    def target_weights(self) -> dict[str, float] | None:
        # None is an explicit no-rebalance signal; {} means a valid empty target.
        if self.status == "UNAVAILABLE":
            return None
        return {i.instrument_id: i.target_weight for i in self.items if i.target_weight > 0}

    def presentation(self) -> dict:
        value = self.model_dump(mode="json")
        value.update(top20=[i.model_dump(mode="json") for i in self.top20],
                     top5=[i.model_dump(mode="json") for i in self.top5],
                     best_stock=self.best_stock.model_dump(mode="json") if self.best_stock else None,
                     target_weights=self.target_weights)
        return value


class RecommendationEngine:
    def __init__(self, policy: RecommendationPolicy | None = None, risk: RiskEngine | None = None, *, replay_legacy: bool = False):
        self.policy = policy or RecommendationPolicy()
        self.risk = risk or RiskEngine()
        self._legacy_replay = self.policy.version == "quant-hard-risk-v1" and replay_legacy
        if self.policy.version != "quant-hard-risk-v2" and not self._legacy_replay:
            raise ValueError("unsupported recommendation policy; retired v1 is historical replay only")

    def evaluate(self, context: RecommendationContext, candidates: tuple[Candidate, ...],
                 risk_inputs: dict[str, RiskInput], portfolio: PortfolioSnapshot) -> FinalRecommendation:
        ids = [c.instrument_id for c in candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate candidate identifiers")
        ordered = tuple(sorted(candidates, key=lambda c: (-c.raw_score, c.instrument_id)))
        # Do not silently repair inconsistent quant runs; ranks are part of their audit.
        if any(c.quant_rank != n for n, c in enumerate(ordered, 1)):
            raise ValueError("quant ranks do not match deterministic score/identifier ordering")
        if portfolio.as_of > context.decision_time:
            raise ValueError("portfolio snapshot comes from the future")
        for iid, data in risk_inputs.items():
            if data.instrument_id != iid or data.decision_time != context.decision_time:
                raise ValueError("risk input identity or decision time mismatch")
        # Outer context is authoritative. Never read business values from a
        # future record, including its hard veto, reference price or reasons.
        future_ids = {iid for iid, data in risk_inputs.items()
                      if data.data_available_at > context.decision_time or data.price_timestamp > context.decision_time}
        risk_inputs = {iid: data for iid, data in risk_inputs.items() if iid not in future_ids}
        hashed_risk_inputs = dict(risk_inputs)
        for iid in sorted(future_ids):
            hashed_risk_inputs[iid] = {'instrument_id': iid, 'quality': 'UNAVAILABLE', 'reason': 'FUTURE_DATA'}
        if self._legacy_replay:
            # The first research-only runtime record predates explicit exit terms.
            # Its serialized input schema remains replayable; no API can select v1
            # for a new recommendation or turn a retired decision into a live order.
            hashed_risk_inputs = {k: v.model_dump(mode="json", exclude={"exit_tradability", "exit_reasons"}) for k, v in risk_inputs.items()}
            if portfolio.holdings:
                raise ValueError("retired v1 replay with holdings is unsupported; inspect immutable original instead")
        input_hash = digest({"context": context, "candidates": ordered, "risk_inputs": hashed_risk_inputs,
                             "portfolio": portfolio, "policy": self.policy, "risk_policy": self.risk.policy})
        rec_run = "rec_" + input_hash[:32]
        unavailable = []
        if context.quant_quality not in ("VALID", "VERIFIED", "RESEARCH_ONLY", "HEALTHY", "EMPTY"):
            unavailable.append("QUANT_UNAVAILABLE")
        if context.mode == "PRODUCTION":
            if context.model_validation not in ("VALIDATED", "PASSED"):
                unavailable.append("MODEL_NOT_VALIDATED")
            if context.data_license != "APPROVED":
                unavailable.append("DATA_LICENSE_REVIEW_REQUIRED")

        accepted, rejected = [], []
        reserved = 0.0
        planned: list[str] = []
        industries: dict[str, float] = {}
        for c in ordered:
            raw = risk_inputs.get(c.instrument_id)
            if unavailable or raw is None:
                risk = RiskDecision(decision=RiskAction.UNAVAILABLE,
                                    reason_codes=tuple(unavailable) or (("FUTURE_DATA",) if c.instrument_id in future_ids else ("RISK_INPUT_UNAVAILABLE",)),
                                    policy_version=self.risk.policy.version, policy_hash=self.risk.policy.policy_hash)
            else:
                risk = self.risk.evaluate(raw, portfolio, reserved_cash=reserved,
                                          planned_positions=tuple(planned), industry_additions=industries)
            chosen = risk.decision in (RiskAction.ACCEPT, RiskAction.REDUCE) and risk.approved_target_shares > 0
            rank = len(accepted) + 1 if chosen else None
            reasons = risk.reason_codes
            row = RecommendationItem(recommendation_id=f"{rec_run}_{c.instrument_id}", instrument_id=c.instrument_id,
                                     quant_score=c.raw_score, quant_rank=c.quant_rank,
                                     final_score=c.raw_score if chosen else None, final_rank=rank,
                                     risk_decision=risk, reason_codes=reasons, reason="; ".join(reasons),
                                     evidence_ids=c.evidence_ids, reference_price=raw.reference_price if raw else None,
                                     target_shares=risk.approved_target_shares, target_weight=risk.target_weight)
            if chosen:
                accepted.append(row)
                planned.append(c.instrument_id)
                reserved += risk.incremental_cash
                existing = next((h.shares for h in portfolio.holdings if h.instrument_id == c.instrument_id), 0)
                added_value = max(0, risk.approved_target_shares - existing) * raw.reference_price
                industries[raw.industry] = industries.get(raw.industry, 0.0) + added_value
            else:
                rejected.append(row)
        # Incomplete risk coverage can never authorize trading the rest of a portfolio:
        # omitted unknown holdings would otherwise be interpreted as liquidation.
        if any(r.risk_decision.decision == RiskAction.UNAVAILABLE for r in rejected):
            unavailable.append("RISK_COVERAGE_UNAVAILABLE")
        selected_ids = {i.instrument_id for i in accepted}
        for holding in portfolio.holdings:
            if holding.shares and holding.instrument_id not in selected_ids:
                exit_input = risk_inputs.get(holding.instrument_id)
                if exit_input is None:
                    unavailable.append("EXIT_RISK_UNAVAILABLE")
                else:
                    exit_risk = self.risk.evaluate_exit(exit_input, portfolio)
                    if exit_risk.decision != RiskAction.ACCEPT:
                        unavailable.extend(("EXIT_RISK_UNAVAILABLE", *exit_risk.reason_codes))
        if unavailable:
            status = "UNAVAILABLE"
            # Preserve decisions for diagnostics, but publish no candidates as actionable.
            rejected = [i.model_copy(update={"final_rank": None, "final_score": None}) for i in accepted] + rejected
            accepted = []
        elif not accepted:
            status = "EMPTY"
        elif context.mode != "PRODUCTION":
            status = "RESEARCH_ONLY"
        else:
            status = "READY"
        return FinalRecommendation(recommendation_run_id=rec_run, context=context, policy=self.policy,
                                   policy_hash=digest(self.policy), risk_policy_hash=self.risk.policy.policy_hash,
                                   input_hash=input_hash, status=status,
                                   reason_codes=tuple(dict.fromkeys(unavailable)) or (("NO_CANDIDATES",) if not accepted else ()),
                                   items=tuple(accepted), rejected=tuple(rejected))

    def records(self, final: FinalRecommendation) -> list[dict]:
        ctx = final.context
        result = []
        for item in (*final.items, *final.rejected):
            status = ("PUBLISHED" if final.status == "READY" else "RESEARCH_ONLY") if item.final_rank else "REJECTED"
            result.append(dict(recommendation_id=item.recommendation_id, run_id=ctx.run_id,
                               instrument_id=item.instrument_id, recommendation_time=ctx.decision_time.isoformat(),
                               as_of=ctx.as_of.isoformat(), earliest_trade_at=ctx.earliest_trade_at.isoformat(),
                               model_id=ctx.model_id, model_version=ctx.model_version, data_snapshot_id=ctx.data_snapshot_id,
                               universe_version=ctx.universe_version, policy_version=final.policy.version,
                               quant_score=item.quant_score, quant_rank=item.quant_rank, final_score=item.final_score,
                               final_rank=item.final_rank, risk_decision=item.risk_decision.model_dump(mode="json"),
                               reason_codes=list(item.reason_codes), reason=item.reason, evidence_ids=list(item.evidence_ids),
                               target_horizon=final.policy.target_horizon, reference_price=item.reference_price,
                               entry_rule=final.policy.entry_rule, benchmark=final.policy.benchmark, status=status,
                               market_regime=ctx.market_regime, metadata={"mode": ctx.mode, "data_version": ctx.data_version,
                                   "policy_hash": final.policy_hash, "risk_policy_hash": final.risk_policy_hash,
                                   "recommendation_run_id": final.recommendation_run_id, "input_hash": final.input_hash,
                                   "checkpoint_hash": ctx.checkpoint_hash, "feature_schema_hash": ctx.feature_schema_hash,
                                   "target_shares": item.target_shares, "target_weight": item.target_weight}))
        return result
