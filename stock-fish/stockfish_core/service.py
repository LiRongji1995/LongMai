"""Application orchestration. Provider, quant, policy and storage boundaries meet here."""
from __future__ import annotations

import os
import re
from datetime import date, datetime, time
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .common import CoreModel, UnavailableError, digest, utc_now
from .data import CanonicalMarketBar, SecurityMaster, SecurityState, TradingCalendar, TradabilityService, SHANGHAI, canonical_instrument
from .model_registry import ModelRegistry
from .quant import FeatureRow, QlibQuantEngine, QuantRequest, QuantResult
from .recommendation import Candidate, FinalRecommendation, RecommendationContext, RecommendationEngine
from .research import Evidence, ResearchEngine, ResearchSnapshot
from .risk import Holding, PortfolioSnapshot, RiskInput, aware
from .storage import SQLiteStore, StorageError
from .tracker import RecommendationTracker
from .jobs import JobService


class InputSnapshot(CoreModel):
    """Only local administrator ingestion can create trusted input documents.

    HTTP recommendation callers select an existing ID; they cannot assert that
    arbitrary market inputs, models or commercial entitlements are verified.
    """
    as_of: datetime
    available_at: datetime
    source: str
    kind: Literal["OBSERVED", "TEST_FIXTURE", "HISTORICAL_PIT"]
    bars: tuple[CanonicalMarketBar, ...]
    securities: tuple[SecurityState, ...]
    sessions: tuple[date, ...]
    calendar_start: date
    calendar_end: date
    features: tuple[FeatureRow, ...] = ()
    data_license: Literal["APPROVED", "REVIEW_REQUIRED", "NOT_FOR_PRODUCTION"] = "REVIEW_REQUIRED"
    evidence: dict = Field(default_factory=dict)

    _times = field_validator("as_of", "available_at")(aware)

    @model_validator(mode="after")
    def validate_snapshot(self):
        if self.as_of > self.available_at:
            raise ValueError("snapshot cutoff follows availability")
        if len({b.instrument_id for b in self.bars}) != len(self.bars):
            raise ValueError("snapshot requires one decision bar per instrument")
        if any(b.timestamp > self.as_of or b.available_at > self.available_at for b in self.bars):
            raise ValueError("bar is outside snapshot cutoff/availability")
        if any(s.available_at > self.available_at for s in self.securities):
            raise ValueError("future security state")
        TradingCalendar(self.sessions, self.calendar_start, self.calendar_end)
        if self.kind == "TEST_FIXTURE" and self.data_license == "APPROVED":
            raise ValueError("test fixtures cannot be production approved")
        return self


class RecommendationRequest(CoreModel):
    model_id: str | None = None
    model_version: str | None = None
    snapshot_id: str | None = None
    instruments: tuple[str, ...] | None = None
    as_of: datetime | None = None
    decision_time: datetime | None = None
    mode: Literal["PRODUCTION", "RESEARCH", "REPLAY"] = "RESEARCH"
    cash: float = Field(default=0, ge=0, strict=True)
    holdings: tuple[Holding, ...] = ()
    include_research: bool = True
    include_llm: bool = False

    @field_validator("as_of", "decision_time")
    @classmethod
    def timestamps(cls, value):
        return aware(value) if value else None

    @field_validator("instruments")
    @classmethod
    def identifiers(cls, value):
        if value is None:
            return None
        if len(value) > 5000 or len(value) != len(set(value)):
            raise ValueError("invalid universe size or duplicate instruments")
        return tuple(canonical_instrument(v) for v in value)


class ApplicationService:
    def __init__(self, root: str | Path | None = None, *, store=None, quant=None, registry=None,
                 jobs=None, data_dir=None, allow_test_fixture=False):
        self.root = Path(root or os.environ.get("STOCKFISH_STATE_DIR") or Path(__file__).resolve().parents[1] / "data" / "core").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = store or SQLiteStore()
        self.registry = registry or ModelRegistry(self.root / "model_registry", store=self.store)
        if registry is not None:
            self.registry.bind_store(self.store)
        self.data_dir = Path(data_dir or os.environ.get("QLIB_DATA_DIR") or Path(__file__).resolve().parents[2] / "data" / "cn_data")
        self.quant = quant or QlibQuantEngine(self.registry, self.root / "quant", self.data_dir)
        self.recommendations = RecommendationEngine()
        self.research = ResearchEngine(self.store)
        self.tracker = RecommendationTracker(self.store)
        self.jobs = jobs or JobService(self.store, max_workers=1, max_pending=20)
        self.jobs.register("recommendation", self._recommendation_job)
        self.jobs.register("quant", self._quant_job)
        from .yaogu.candidate_service import CandidateService
        self.candidates = CandidateService(self.store, self.jobs, self.root / "yaogu", allow_test_fixture=allow_test_fixture)
        self.candidate_scheduler = None

    def close(self):
        if self.candidate_scheduler is not None:
            self.candidate_scheduler.stop()
        self.jobs.shutdown(wait=True)

    def register_snapshot(self, snapshot: InputSnapshot) -> str:
        value = snapshot.model_dump(mode="json")
        snapshot_id = "data_" + digest(value)
        self.store.put_document("data_snapshots", snapshot_id, value)
        return snapshot_id

    def snapshot(self, snapshot_id: str) -> InputSnapshot:
        value = self.store.get_document("data_snapshots", snapshot_id)
        if value is None:
            raise UnavailableError("DATA_SNAPSHOT_UNAVAILABLE", "data")
        if snapshot_id != "data_" + digest(value):
            raise UnavailableError("DATA_SNAPSHOT_HASH_MISMATCH", "data")
        return InputSnapshot.model_validate(value)

    def active_snapshot_id(self) -> str | None:
        pointer = self.root / "active_snapshot.txt"
        return pointer.read_text(encoding="utf-8").strip() if pointer.is_file() else None

    def resolve_symbols(self, values: tuple[str, ...]) -> tuple[str, ...]:
        """Resolve legacy bare symbols against explicit Qlib universe identities."""
        identities = self.universe()
        result = []
        for value in values:
            value = value.strip().upper()
            if re.fullmatch(r"\d{6}", value):
                matches = [iid for iid in identities if iid[:6] == value]
                if len(matches) != 1:
                    raise ValueError(f"ambiguous/unknown symbol {value}; specify exchange")
                result.append(matches[0])
            else:
                result.append(canonical_instrument(value))
        if len(result) != len(set(result)):
            raise ValueError("duplicate instruments")
        return tuple(result)

    def universe(self, as_of: date | None = None) -> tuple[str, ...]:
        path = self.data_dir / "instruments" / "csi300.txt"
        if not path.is_file():
            return ()
        result = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) != 3:
                raise UnavailableError("INVALID_UNIVERSE_FILE", "quant")
            if as_of is None or date.fromisoformat(parts[1]) <= as_of <= date.fromisoformat(parts[2]):
                result.add(canonical_instrument(parts[0]))
        return tuple(sorted(result))

    def _model(self, request: RecommendationRequest):
        model_id = request.model_id
        if model_id is None:
            models = self.registry.list()
            if len(models) != 1:
                raise UnavailableError("MODEL_SELECTION_REQUIRED", "quant")
            model_id = models[0].model_id
        return self.registry.get(model_id, request.model_version)

    def run_quant(self, request: RecommendationRequest, progress=None) -> tuple[QuantResult, InputSnapshot | None, object]:
        model = self._model(request)
        snapshot_id = request.snapshot_id or self.active_snapshot_id()
        snapshot = self.snapshot(snapshot_id) if snapshot_id else None
        cutoff = request.as_of or (snapshot.as_of if snapshot else None)
        if cutoff is None:
            calendar_file = self.data_dir / "calendars" / "day.txt"
            if not calendar_file.is_file():
                raise UnavailableError("DATA_UNAVAILABLE", "data")
            dates = [date.fromisoformat(s.strip()) for s in calendar_file.read_text().splitlines() if s.strip()]
            eligible = [d for d in dates if datetime.combine(d, time(15), SHANGHAI) <= utc_now()]
            if not eligible:
                raise UnavailableError("NO_COMPLETED_DATA_SESSION", "data")
            cutoff = datetime.combine(max(eligible), time(15), SHANGHAI)
        if snapshot and cutoff != snapshot.as_of:
            raise ValueError("as_of must match selected immutable snapshot")
        instruments = request.instruments if request.instruments is not None else (tuple(b.instrument_id for b in snapshot.bars) if snapshot else self.universe(cutoff.date()))
        if model.format == "linear_v1":
            if snapshot is None or not snapshot.features:
                raise UnavailableError("FEATURE_SNAPSHOT_REQUIRED", "quant")
            features = tuple(r for r in snapshot.features if r.instrument_id in instruments)
            data_version = digest([f.model_dump(mode="json") for f in features])
            universe_version = digest(tuple(sorted(instruments)))
        else:
            features = ()
            versions = self.quant.inspect_data()
            data_version, universe_version = versions["data_version"], versions["universe_version"]
        if progress:
            progress(0.15, "已校验模型与数据版本；正在量化推理")
        result = self.quant.rank(QuantRequest(model_id=model.model_id, model_version=model.model_version, as_of=cutoff,
            data_version=data_version, universe_version=universe_version, policy_version=self.recommendations.policy.version,
            instruments=instruments, features=features, data_snapshot_id=snapshot_id or data_version),
            allow_research=request.mode != "PRODUCTION")
        self.store.put_document("quant_runs", result.run_id, result.model_dump(mode="json"))
        # Metadata schemas can add explicit null fields without changing the
        # checkpoint identity. Preserve each exact metadata document by content.
        self.store.put_document("model_artifacts", "model_" + digest(model), model.model_dump(mode="json"))
        return result, snapshot, model

    def recommend(self, request: RecommendationRequest, progress=None, job_context=None) -> dict:
        quant, snapshot, model = self.run_quant(request, progress)
        now = utc_now()
        if request.mode != "REPLAY" and request.decision_time and abs((now - request.decision_time).total_seconds()) > 5:
            raise ValueError("historical decision_time requires explicit REPLAY mode")
        decision_time = (request.decision_time or now) if request.mode == "REPLAY" else now
        if decision_time > utc_now() or decision_time < quant.as_of:
            raise ValueError("invalid decision_time")
        if snapshot is None:
            # Quant output remains accessible, but lack of trusted raw market context
            # cannot be disguised as a successful recommendation.
            raise UnavailableError(f"TRUSTED_RISK_SNAPSHOT_REQUIRED:{quant.run_id}", "recommendation")
        if snapshot.available_at > decision_time:
            raise UnavailableError("SNAPSHOT_NOT_YET_AVAILABLE", "data")
        if request.mode == "PRODUCTION" and snapshot.kind == "TEST_FIXTURE":
            raise UnavailableError("TEST_FIXTURE_NOT_FOR_PRODUCTION", "data")
        calendar = TradingCalendar(snapshot.sessions, snapshot.calendar_start, snapshot.calendar_end)
        securities = SecurityMaster(snapshot.securities)
        tradability = TradabilityService(calendar, securities)
        earliest = calendar.earliest_trade_at(decision_time)
        bars = {b.instrument_id: b for b in snapshot.bars}
        verified_holdings = []
        for holding in request.holdings:
            held_bar = bars.get(holding.instrument_id)
            if held_bar is None or not held_bar.raw_price_available:
                raise UnavailableError("HOLDING_MARK_UNAVAILABLE", "risk")
            state = securities.resolve(holding.instrument_id, held_bar.timestamp.astimezone(SHANGHAI).date(), decision_time)
            if not state.industry or not held_bar.available_at <= decision_time:
                raise UnavailableError("HOLDING_CONTEXT_UNAVAILABLE", "risk")
            if abs(holding.mark_price - held_bar.close) > 1e-7 or holding.industry != state.industry or holding.marked_at != held_bar.timestamp:
                raise ValueError("holding mark/industry/time must match trusted snapshot")
            verified_holdings.append(holding)
        portfolio = PortfolioSnapshot(as_of=decision_time, cash=request.cash, holdings=tuple(verified_holdings))
        risk_inputs = {}
        for instrument_id in sorted({s.instrument_id for s in quant.scores} | {h.instrument_id for h in portfolio.holdings}):
            bar = bars.get(instrument_id)
            if bar is None:
                continue
            # Decision risk uses facts known for the observation session. Actual
            # entry requires a separate trade-date check; future states are not invented.
            tr = tradability.evaluate(instrument_id, decision_time, bar.timestamp.astimezone(SHANGHAI).date(), "BUY", bar)
            exit_tr = tradability.evaluate(instrument_id, decision_time, bar.timestamp.astimezone(SHANGHAI).date(), "SELL", bar)
            risk_inputs[instrument_id] = RiskInput(instrument_id=instrument_id,
                decision_time=decision_time, data_available_at=bar.available_at, price_timestamp=bar.timestamp,
                reference_price=bar.close, price_adjustment=bar.price_adjustment, currency=bar.currency,
                quality=bar.quality, provider=bar.provider, industry=tr.industry, tradability=tr.decision,
                tradability_reasons=tr.reason_codes, exit_tradability=exit_tr.decision, exit_reasons=exit_tr.reason_codes,
                lot_size=tr.lot_size, min_buy_shares=tr.min_buy_shares)
        context = RecommendationContext(run_id=quant.run_id, as_of=quant.as_of, decision_time=decision_time,
            earliest_trade_at=earliest, model_id=model.model_id, model_version=model.model_version,
            checkpoint_hash=model.checkpoint_hash, feature_schema_hash=model.feature_schema_hash,
            data_snapshot_id=quant.data_snapshot_id, data_version=quant.data_version, universe_version=quant.universe_version,
            quant_quality="UNAVAILABLE" if quant.rejected or quant.errors else quant.quality,
            model_validation="PASSED" if model.oos_validated else model.status,
            data_license=snapshot.data_license, mode=request.mode)
        research_results = {}
        if request.include_research:
            for score in quant.scores[:20]:
                bar = bars.get(score.instrument_id)
                evidence = (Evidence(evidence_id=f"{quant.data_snapshot_id}:{score.instrument_id}", category="technical",
                    source=bar.provider, available_at=bar.available_at,
                    content={"observation": bar.model_dump(mode="json"), "meaning": "observed market context, not a return forecast"},
                    quality=bar.quality),) if bar else ()
                research_snapshot = ResearchSnapshot(snapshot_id=quant.data_snapshot_id, instrument_id=score.instrument_id,
                                                     as_of=decision_time, evidence=evidence)
                opinion = self.research.summarize_context(research_snapshot, run_id=quant.run_id)
                llm = self.research.analyze(research_snapshot, run_id=quant.run_id) if request.include_llm else None
                research_results[score.instrument_id] = {"context": opinion.model_dump(mode="json"),
                                                       "llm": llm.model_dump(mode="json") if llm else None}
        candidates = tuple(Candidate(instrument_id=s.instrument_id, raw_score=s.raw_score, quant_rank=s.rank,
                                     evidence_ids=(quant.data_snapshot_id,) + ((research_results[s.instrument_id]["context"]["research_id"],)
                                         if s.instrument_id in research_results else ())) for s in quant.scores)
        final = self.recommendations.evaluate(context, candidates, risk_inputs, portfolio)
        input_document = {"context": context.model_dump(mode="json"), "candidates": [c.model_dump(mode="json") for c in candidates],
                          "risk_inputs": {k: v.model_dump(mode="json") for k, v in risk_inputs.items()},
                          "portfolio": portfolio.model_dump(mode="json"), "execution_recheck_required": True}
        research_bundle_id = "research_" + digest({"run_id": quant.run_id, "research": research_results})
        self.store.put_document("research_runs", research_bundle_id, {"run_id": quant.run_id, "research": research_results})
        policy = final.policy.model_dump(mode="json")
        risk_policy = self.recommendations.risk.policy.model_dump(mode="json")
        documents = [("recommendation_policies", final.policy_hash, policy),
                     ("risk_policies", final.risk_policy_hash, risk_policy),
                     ("recommendation_inputs", final.input_hash, input_document),
                     ("recommendation_runs", final.recommendation_run_id, final.presentation())]
        refs = [{"role": role, "namespace": n, "document_id": i} for role, n, i in (
            ("data", "data_snapshots", quant.data_snapshot_id), ("model", "model_artifacts", "model_" + digest(model)),
            ("policy", "recommendation_policies", final.policy_hash), ("risk", "risk_policies", final.risk_policy_hash),
            ("quant", "quant_runs", quant.run_id), ("input", "recommendation_inputs", final.input_hash))]
        records = self.recommendations.records(final)
        for record in records:
            record["mode"] = "production" if request.mode == "PRODUCTION" else "research"
            record["metadata"].update(artifact_refs=refs, execution_recheck_required=True,
                                       source=snapshot.source, input_kind=snapshot.kind,
                                       execution_mode=request.mode.lower(), production_approved=final.status == "READY")
        if progress:
            progress(0.9, "风控与组合约束完成；正在事务保存推荐及证据")
        result = self.present(final, quant)
        result["research"] = research_results
        result["research_bundle_id"] = research_bundle_id
        if job_context is not None:
            writer = lambda connection: self.tracker.publish_many(records, documents=documents, connection=connection)
            if final.status == "UNAVAILABLE":
                job_context.fail(result, {"code": "RECOMMENDATION_UNAVAILABLE", "component": "risk",
                    "reason_codes": list(final.reason_codes), "message": "No actionable recommendation was published."}, writer)
            else:
                job_context.complete(result, writer)
        else:
            self.tracker.publish_many(records, documents=documents)
        from loguru import logger
        logger.info("recommendation persisted run_id={} job_id={} recommendation_id={} provider={} model_version={} quality={}",
                    quant.run_id, job_context.job_id if job_context else "synchronous", final.recommendation_run_id,
                    snapshot.source, model.model_version, final.status)
        return result

    @staticmethod
    def present(final: FinalRecommendation, quant: QuantResult | None = None) -> dict:
        value = final.presentation()
        return {"status": "complete" if final.status != "UNAVAILABLE" else "unavailable",
                "run_id": final.context.run_id, "recommendation_run_id": final.recommendation_run_id,
                "source": final.context.data_snapshot_id, "quality": final.status,
                "versions": {"model": final.context.model_version, "data": final.context.data_version,
                             "universe": final.context.universe_version, "policy": final.policy.version},
                "final_recommendation": value, "top20": value["top20"], "top5": value["top5"],
                "best_stock": value["best_stock"], "stocks": "/".join(i.instrument_id for i in final.items),
                "count": len(final.items), "scores": value["top20"], "pred_date": final.context.as_of.date().isoformat(),
                "summary": {"ranking": value["top20"], "summary_text": final.status},
                "quality_pick": {"best_stock": value["best_stock"], "runner_up": value["top20"][1] if len(value["top20"]) > 1 else None},
                "quant": quant.model_dump(mode="json") if quant else None}

    def replay_recommendation(self, recommendation_run_id: str) -> FinalRecommendation:
        value = self.store.get_document("recommendation_runs", recommendation_run_id)
        if value is None:
            raise KeyError(recommendation_run_id)
        inputs = self.store.get_document("recommendation_inputs", value["input_hash"])
        policy = self.store.get_document("recommendation_policies", value["policy_hash"])
        risk_policy = self.store.get_document("risk_policies", value["risk_policy_hash"])
        from .recommendation import RecommendationPolicy
        from .risk import RiskEngine, RiskPolicy
        engine = RecommendationEngine(RecommendationPolicy.model_validate(policy), RiskEngine(RiskPolicy.model_validate(risk_policy)), replay_legacy=True)
        replay = engine.evaluate(RecommendationContext.model_validate(inputs["context"]),
            tuple(Candidate.model_validate(c) for c in inputs["candidates"]),
            {k: RiskInput.model_validate(v) for k, v in inputs["risk_inputs"].items()}, PortfolioSnapshot.model_validate(inputs["portfolio"]))
        if replay.presentation() != value:
            raise UnavailableError("RECOMMENDATION_REPLAY_MISMATCH", "recommendation")
        return replay

    def _recommendation_job(self, ctx):
        return self.recommend(RecommendationRequest.model_validate(ctx.payload), ctx.update, job_context=ctx)

    def _quant_job(self, ctx):
        result, _, _ = self.run_quant(RecommendationRequest.model_validate(ctx.payload), ctx.update)
        value = {"run_id": result.run_id, "quality": result.quality, "quant": result.model_dump(mode="json"),
                "status": "complete", "stocks": "", "count": 0,
                "message": "量化候选已保存；必须运行统一推荐与风控后才有最终名单"}
        if result.quality == "UNAVAILABLE":
            value["status"] = "unavailable"
            return ctx.fail(value, {"code": "QUANT_COVERAGE_UNAVAILABLE", "component": "quant",
                                   "message": "requested quant coverage unavailable; diagnostics retained"}, lambda connection: None)
        return value

    def health(self):
        research = self.research.health()
        components = {"application": {"status": "healthy"}, "market_data": {"status": "unavailable", "reason": "TRUSTED_SNAPSHOT_NOT_SELECTED"},
                      "quant": self.quant.health(), "model_registry": self.registry.health(),
                      "research": {"status": research["status"], "role": research["role"]}, "llm": research["llm"],
                      "tracker": self.store.health(), "jobs": self.jobs.health()}
        try:
            snapshot_id = self.active_snapshot_id()
            if snapshot_id:
                snapshot = self.snapshot(snapshot_id)
                components["market_data"] = {"status": "degraded" if snapshot.data_license != "APPROVED" else "healthy",
                    "snapshot_id": snapshot_id, "as_of": snapshot.as_of.isoformat(), "available_at": snapshot.available_at.isoformat(),
                    "source": snapshot.source, "kind": snapshot.kind, "licensing": snapshot.data_license}
                if (utc_now() - snapshot.as_of).total_seconds() > self.recommendations.risk.policy.max_price_age_seconds:
                    components["market_data"].update(status="unavailable", reason="STALE_SNAPSHOT")
                elif snapshot.available_at > utc_now():
                    components["market_data"].update(status="unavailable", reason="SNAPSHOT_NOT_YET_AVAILABLE")
        except (ValueError, UnavailableError, OSError, StorageError):
            components["market_data"] = {"status": "unavailable", "reason": "ACTIVE_SNAPSHOT_INVALID"}
        status = "healthy" if all(c["status"] == "healthy" for c in components.values()) else "degraded"
        if any(components[k]["status"] == "unavailable" for k in ("application", "tracker", "jobs")):
            status = "unavailable"
        production_ready = (components["market_data"].get("status") == "healthy"
                            and components["market_data"].get("licensing") == "APPROVED"
                            and components["model_registry"].get("production_validated", 0) > 0
                            and all(components[k]["status"] == "healthy" for k in ("application", "quant", "tracker", "jobs")))
        return {"status": status, "components": components, "production_ready": production_ready,
                "policy_version": self.recommendations.policy.version}
