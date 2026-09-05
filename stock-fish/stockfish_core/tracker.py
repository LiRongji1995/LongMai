"""Immutable recommendations and separate, reproducible outcome observations.

Evaluation consumes explicitly unadjusted executable prices and corporate actions,
never model labels or normalized Qlib prices. It measures a hypothetical single
security holding; it does not claim an actual user fill or portfolio return.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import math
from pathlib import Path
import sqlite3
from typing import Any, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import AliasChoices, Field, field_validator, model_validator

from .common import CoreModel, canonical_json, digest, utc_now
from .storage import SQLiteStore, ImmutableConflict, checked_payload, _hash_text, _validate_page


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must include timezone")
    return value.astimezone(timezone.utc)


class RecommendationRecord(CoreModel):
    recommendation_id: str = Field(default_factory=lambda: f"rec_{uuid4().hex}")
    run_id: str = Field(min_length=1)
    instrument_id: str = Field(pattern=r"^\d{6}\.(SH|SZ|BJ)$")
    recommendation_time: datetime
    as_of: datetime
    earliest_trade_at: datetime
    model_id: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    data_snapshot_id: str = Field(min_length=1)
    universe_version: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    quant_score: float | None
    quant_rank: int | None = Field(default=None, ge=1)
    fundamental_score: float | None = None
    technical_score: float | None = None
    event_score: float | None = None
    llm_score: float | None = None
    final_score: float | None
    final_rank: int | None = Field(default=None, ge=1)
    risk_decision: dict[str, Any]
    reason_codes: list[str]
    reason: str = Field(min_length=1)
    evidence_ids: list[str]
    target_horizon: int = Field(gt=0, le=3650)
    horizon_unit: Literal["calendar_days", "trading_days"] = "calendar_days"
    reference_price: float | None = Field(default=None, gt=0)
    entry_rule: Literal["NEXT_TRADABLE_OPEN"] = "NEXT_TRADABLE_OPEN"
    benchmark: str = Field(min_length=1)
    status: Literal["PROPOSED", "PUBLISHED", "RESEARCH_ONLY", "REJECTED", "INVALIDATED"] = "RESEARCH_ONLY"
    mode: Literal["research", "production"] = "research"
    market_regime: str | None = None
    revision: int = Field(default=1, ge=1)
    root_recommendation_id: str | None = None
    supersedes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("recommendation_time", "as_of", "earliest_trade_at")
    @classmethod
    def aware_times(cls, value):
        return _aware(value)

    @field_validator("recommendation_id")
    @classmethod
    def valid_id(cls, value):
        if not value or len(value) > 200:
            raise ValueError("invalid recommendation id")
        return value

    @model_validator(mode="after")
    def chronological_and_safe(self):
        if not self.as_of <= self.recommendation_time <= self.earliest_trade_at:
            raise ValueError("require as_of <= recommendation_time <= earliest_trade_at")
        if self.status in {"PUBLISHED", "RESEARCH_ONLY"}:
            if self.risk_decision.get("decision") not in {"ACCEPT", "REDUCE"}:
                raise ValueError("recommendation requires an accepted deterministic risk decision")
            if self.final_rank is None or self.quant_rank is None or self.quant_score is None or self.final_score is None:
                raise ValueError("recommended record requires quant and final scores/ranks")
        if self.status == "PUBLISHED" and (self.mode != "production" or not self.metadata.get("production_approved", False)):
            raise ValueError("PUBLISHED requires explicit production approval; use RESEARCH_ONLY otherwise")
        if self.status == "RESEARCH_ONLY" and self.mode != "research":
            raise ValueError("RESEARCH_ONLY mode must be research")
        return self


class EvaluationObservation(CoreModel):
    observation_id: str = Field(default_factory=lambda: f"eval_{uuid4().hex}")
    recommendation_id: str
    horizon: int = Field(gt=0)
    horizon_unit: Literal["calendar_days", "trading_days"] = "calendar_days"
    evaluation_date: date
    evaluated_at: datetime
    entry_at: datetime | None = None
    exit_at: datetime | None = None
    entry_price: float | None = Field(default=None, gt=0)
    exit_price: float | None = Field(default=None, gt=0)
    return_fraction: float | None = Field(default=None, validation_alias=AliasChoices("return", "return_fraction"), serialization_alias="return")
    benchmark_return: float | None = None
    excess_return: float | None = None
    max_drawdown: float | None = Field(default=None, ge=0, le=1)
    executable: bool
    state: Literal["MATURED", "NOT_MATURED", "NOT_EXECUTABLE", "DATA_UNAVAILABLE"]
    method_version: str = Field(min_length=1)
    data_snapshot_ids: list[str]
    reason_codes: list[str] = Field(default_factory=list)
    entry_rule: Literal["NEXT_TRADABLE_OPEN"] = "NEXT_TRADABLE_OPEN"
    quantity: float | None = Field(default=None, gt=0)
    costs: dict[str, Any] = Field(default_factory=dict)
    accounting: dict[str, Any] = Field(default_factory=dict)
    input_snapshot_id: str = Field(min_length=1)
    provenance: Literal["OBSERVED", "TEST_FIXTURE", "UNVERIFIED_RESEARCH_INPUT"]

    @field_validator("evaluated_at", "entry_at", "exit_at")
    @classmethod
    def aware_times(cls, value):
        return _aware(value) if value else value

    @model_validator(mode="after")
    def outcome_consistency(self):
        if self.evaluation_date > self.evaluated_at.date():
            raise ValueError("evaluation cannot be dated in the future")
        if self.state == "MATURED":
            if not self.executable or any(value is None for value in (self.entry_at, self.exit_at, self.entry_price, self.exit_price,
                                                                    self.return_fraction, self.benchmark_return, self.excess_return, self.max_drawdown)):
                raise ValueError("mature evaluation requires all executable return fields")
            if not self.entry_at <= self.exit_at <= self.evaluated_at:
                raise ValueError("invalid entry/exit/evaluation chronology")
            if not math.isclose(self.excess_return, self.return_fraction - self.benchmark_return, abs_tol=1e-10):
                raise ValueError("excess return is inconsistent")
        elif any(value is not None for value in (self.return_fraction, self.benchmark_return, self.excess_return, self.max_drawdown)):
            raise ValueError("unavailable/unmatured observations cannot contain performance metrics")
        return self


class EvaluationBar(CoreModel):
    """One real daily bar. Dividends are cash per post-split share at session end."""
    instrument_id: str
    session: date
    open_at: datetime
    close_at: datetime
    available_at: datetime
    open: float = Field(gt=0)
    close: float = Field(gt=0)
    price_adjustment: Literal["raw"]
    provider: str = Field(min_length=1)
    data_snapshot_id: str = Field(min_length=1)
    can_buy: bool
    can_sell: bool
    corporate_actions_known: bool
    split_ratio: float = Field(default=1.0, gt=0)
    cash_dividend_per_share: float = Field(default=0.0, ge=0)

    @field_validator("open_at", "close_at", "available_at")
    @classmethod
    def aware_times(cls, value):
        return _aware(value)

    @model_validator(mode="after")
    def valid_times(self):
        if self.open_at >= self.close_at or self.available_at < self.close_at:
            raise ValueError("daily bars require open < close <= available_at")
        if (self.open_at.astimezone(ZoneInfo("Asia/Shanghai")).date() != self.session or
                self.close_at.astimezone(ZoneInfo("Asia/Shanghai")).date() != self.session):
            raise ValueError("bar timestamps must match the exchange session date")
        if self.provider.lower() in {"mock", "synthetic", "normalized_qlib", "unknown", "unavailable"}:
            raise ValueError("synthetic/normalized prices cannot evaluate actual outcomes")
        return self


class EvaluationCosts(CoreModel):
    quantity: int = Field(default=100, gt=0)
    lot_size: int = Field(default=100, gt=0)
    buy_commission_rate: float = Field(default=0.0003, ge=0, le=0.1)
    sell_commission_rate: float = Field(default=0.0003, ge=0, le=0.1)
    minimum_commission: float = Field(default=5.0, ge=0)
    sell_tax_rate: float = Field(default=0.0005, ge=0, le=0.1)
    slippage_bps: float = Field(default=0.0, ge=0, le=1000)
    version: str = "explicit_cost_assumption_v1"

    @model_validator(mode="after")
    def valid_lot(self):
        if self.quantity % self.lot_size:
            raise ValueError("entry quantity must be a whole trading lot")
        return self


class OutcomeDataSnapshot(CoreModel):
    """Local administrator ingestion contract; never accept from public HTTP.

    OBSERVED asserts verified raw prices, action coverage and exchange calendar
    provenance. TEST_FIXTURE remains explicitly distinguishable in every result.
    """
    source: str = Field(min_length=1)
    kind: Literal["OBSERVED", "TEST_FIXTURE"]
    available_at: datetime
    bars: tuple[EvaluationBar, ...]
    benchmark_bars: tuple[EvaluationBar, ...]
    trading_sessions: tuple[date, ...]
    calendar_source: str = Field(min_length=1)
    calendar_coverage_start: date
    calendar_coverage_end: date
    data_license: Literal["APPROVED", "REVIEW_REQUIRED", "NOT_FOR_PRODUCTION"] = "REVIEW_REQUIRED"

    @field_validator("available_at")
    @classmethod
    def aware_available(cls, value):
        return _aware(value)

    @model_validator(mode="after")
    def source_consistency(self):
        if self.kind == "TEST_FIXTURE" and self.data_license == "APPROVED":
            raise ValueError("fixture outcomes cannot be approved production data")
        if not self.trading_sessions or len(set(self.trading_sessions)) != len(self.trading_sessions):
            raise ValueError("explicit unique trading sessions required")
        if self.calendar_coverage_start > min(self.trading_sessions) or self.calendar_coverage_end < max(self.trading_sessions):
            raise ValueError("calendar sessions outside stated coverage")
        if any(bar.available_at > self.available_at for bar in (*self.bars, *self.benchmark_bars)):
            raise ValueError("outcome bar exceeds source availability")
        if self.kind == "OBSERVED" and any(any(word in label.lower() for word in ("fixture", "synthetic", "mock"))
                                           for label in (self.source, *(bar.provider for bar in (*self.bars, *self.benchmark_bars)))):
            raise ValueError("fixture/synthetic provenance cannot be promoted to OBSERVED")
        return self


class RecommendationTracker:
    def __init__(self, store: SQLiteStore | None = None):
        self.store = store or SQLiteStore()

    @staticmethod
    def _prepare(record: RecommendationRecord | dict) -> RecommendationRecord:
        # frozen Pydantic objects can still contain mutated nested dicts.
        record = RecommendationRecord.model_validate(record.model_dump() if isinstance(record, RecommendationRecord) else record)
        if record.root_recommendation_id is None:
            record = record.model_copy(update={"root_recommendation_id": record.recommendation_id})
        return record

    @staticmethod
    def _insert(connection: sqlite3.Connection, record: RecommendationRecord) -> str:
        payload = record.model_dump(mode="json")
        raw, checksum = canonical_json(payload), digest(payload)
        existing = connection.execute("SELECT * FROM recommendation_records WHERE recommendation_id=?", (record.recommendation_id,)).fetchone()
        if existing:
            checked_payload(existing)
            if existing["content_hash"] != checksum:
                raise ImmutableConflict("recommendation id already identifies different immutable content")
            return record.recommendation_id
        if record.revision == 1:
            if record.root_recommendation_id != record.recommendation_id or record.supersedes:
                raise ValueError("initial record must be the revision root")
        else:
            previous = connection.execute("SELECT * FROM recommendation_records WHERE recommendation_id=?", (record.supersedes,)).fetchone()
            latest = connection.execute("SELECT MAX(revision) FROM recommendation_records WHERE root_recommendation_id=?", (record.root_recommendation_id,)).fetchone()[0]
            if not previous or previous["root_recommendation_id"] != record.root_recommendation_id or latest != record.revision - 1 or previous["revision"] != latest:
                raise ImmutableConflict("revision must append to the latest record")
            if previous["instrument_id"] != record.instrument_id:
                raise ValueError("revision cannot change instrument identity")
        expected_roles = {"data": "data_snapshots", "model": "model_artifacts", "policy": "recommendation_policies",
                          "risk": "risk_policies", "quant": "quant_runs", "input": "recommendation_inputs"}
        references = {}
        for ref in record.metadata.get("artifact_refs", []):
            if not isinstance(ref, dict) or not isinstance(ref.get("namespace"), str) or not isinstance(ref.get("document_id"), str):
                raise ValueError("invalid artifact reference")
            row = connection.execute("SELECT * FROM documents WHERE namespace=? AND document_id=?", (ref["namespace"], ref["document_id"])).fetchone()
            if row is None:
                raise ValueError("referenced artifact is not durably stored")
            artifact = checked_payload(row)
            role = ref.get("role")
            if role in expected_roles:
                if role in references or ref["namespace"] != expected_roles[role]:
                    raise ValueError("duplicate or incorrectly namespaced artifact role")
                references[role] = ref
            if role == "data" and ref["document_id"] != record.data_snapshot_id:
                raise ValueError("data lineage does not match recommendation snapshot")
            if role == "model" and artifact.get("model_version", record.model_version) != record.model_version:
                raise ValueError("model artifact version does not match recommendation")
        if record.status in {"PUBLISHED", "RESEARCH_ONLY"} and expected_roles.keys() - references.keys():
            raise ValueError("accepted record requires durable data/model/policy/risk/quant/input lineage")
        connection.execute("""INSERT INTO recommendation_records(recommendation_id,root_recommendation_id,revision,supersedes,run_id,instrument_id,recommendation_time,target_horizon,status,payload,content_hash)
                              VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                           (record.recommendation_id, record.root_recommendation_id, record.revision, record.supersedes,
                            record.run_id, record.instrument_id, record.recommendation_time.isoformat(), record.target_horizon,
                            record.status, raw, checksum))
        return record.recommendation_id

    def publish(self, record: RecommendationRecord | dict) -> str:
        return self.publish_many([record])[0]

    def publish_many(self, records: list[RecommendationRecord | dict], *,
                     documents: list[tuple[str, str, dict]] | None = None,
                     connection: sqlite3.Connection | None = None) -> list[str]:
        """Atomic: all artifacts and recommendation records commit before publication."""
        prepared = [self._prepare(record) for record in records]
        def write(connection):
            for namespace, document_id, payload in documents or []:
                self.store._put_document(connection, namespace, document_id, payload)
            return [self._insert(connection, record) for record in prepared]
        if connection is not None:
            if not connection.in_transaction:
                raise ValueError("external connection requires an active transaction")
            database = next((row[2] for row in connection.execute("PRAGMA database_list") if row[1] == "main"), None)
            if not database or Path(database).resolve() != self.store.path:
                raise ValueError("external transaction belongs to another store")
            return write(connection)
        with self.store.transaction() as owned_connection:
            return write(owned_connection)

    def get(self, recommendation_id: str) -> dict | None:
        with self.store.transaction(write=False) as connection:
            row = connection.execute("SELECT * FROM recommendation_records WHERE recommendation_id=?", (recommendation_id,)).fetchone()
            return checked_payload(row) if row else None

    def revise(self, recommendation_id: str, changes: dict) -> str:
        if not isinstance(changes, dict):
            raise ValueError("revision changes must be an object")
        protected = {"recommendation_id", "root_recommendation_id", "revision", "supersedes", "instrument_id"}
        if protected & changes.keys():
            raise ValueError("revision cannot modify identity fields")
        with self.store.transaction() as connection:
            previous = connection.execute("SELECT * FROM recommendation_records WHERE recommendation_id=?", (recommendation_id,)).fetchone()
            if previous is None:
                raise KeyError(recommendation_id)
            payload = checked_payload(previous)
            payload.update(changes)
            payload.update(recommendation_id=f"rec_{uuid4().hex}", revision=previous["revision"] + 1,
                           supersedes=recommendation_id, root_recommendation_id=previous["root_recommendation_id"])
            return self._insert(connection, self._prepare(payload))

    def list_history(self, *, instrument_id: str | None = None, run_id: str | None = None,
                     since: datetime | None = None, until: datetime | None = None,
                     status: str | None = None, latest_only: bool = False,
                     limit: int = 100, offset: int = 0) -> list[dict]:
        _validate_page(limit, offset)
        filters, params = [], []
        for column, value in (("instrument_id", instrument_id), ("run_id", run_id), ("status", status)):
            if value is not None:
                filters.append(f"r.{column}=?")
                params.append(value)
        for operator, instant in ((">=", since), ("<=", until)):
            if instant is not None:
                filters.append(f"r.recommendation_time{operator}?")
                params.append(_aware(instant).isoformat())
        if latest_only:
            filters.append("r.revision=(SELECT MAX(s.revision) FROM recommendation_records s WHERE s.root_recommendation_id=r.root_recommendation_id)")
        where = " WHERE " + " AND ".join(filters) if filters else ""
        with self.store.transaction(write=False) as connection:
            rows = connection.execute("SELECT r.* FROM recommendation_records r" + where + " ORDER BY recommendation_time DESC,recommendation_id LIMIT ? OFFSET ?",
                                      (*params, limit, offset)).fetchall()
            return [checked_payload(row) for row in rows]

    def append_evaluation(self, observation: EvaluationObservation | dict) -> str:
        observation = EvaluationObservation.model_validate(observation.model_dump() if isinstance(observation, EvaluationObservation) else observation)
        payload = observation.model_dump(mode="json", by_alias=True)
        source = self.store.get_document("evaluation_inputs", observation.input_snapshot_id)
        if source is None or observation.input_snapshot_id != "evalinput_" + digest(source):
            raise ValueError("evaluation requires a retrievable content-verified input snapshot")
        expected = self.evaluate(source["recommendation_id"], source["bars"], source["benchmark_bars"],
                                 horizon=source["horizon"], evaluated_at=datetime.fromisoformat(source["evaluated_at"]),
                                 costs=source["costs"], trading_sessions=[date.fromisoformat(d) for d in source["trading_sessions"]],
                                 method_version=source["method_version"], source_snapshot_id=source["source_snapshot_id"], _persist=False)
        if canonical_json(expected) != canonical_json(payload):
            raise ValueError("evaluation metrics differ from deterministic raw-price recomputation")
        with self.store.transaction() as connection:
            record_row = connection.execute("SELECT * FROM recommendation_records WHERE recommendation_id=?", (observation.recommendation_id,)).fetchone()
            if record_row is None:
                raise KeyError(observation.recommendation_id)
            record = RecommendationRecord.model_validate(checked_payload(record_row))
            if record.status not in {"PUBLISHED", "RESEARCH_ONLY"}:
                raise ValueError("only accepted published or research records can be evaluated")
            if observation.entry_at and observation.entry_at < record.earliest_trade_at:
                raise ValueError("entry precedes earliest possible trade")
            if observation.state == "MATURED" and observation.horizon_unit == "calendar_days":
                if observation.exit_at < observation.entry_at + timedelta(days=observation.horizon):
                    raise ValueError("holding horizon has not matured")
            if observation.horizon_unit != record.horizon_unit:
                raise ValueError("observation and recommendation horizon units differ")
            existing = connection.execute("SELECT * FROM evaluation_observations WHERE observation_id=?", (observation.observation_id,)).fetchone()
            checksum = digest(payload)
            if existing:
                checked_payload(existing)
                if existing["content_hash"] != checksum:
                    raise ImmutableConflict("evaluation observation is immutable")
                return observation.observation_id
            connection.execute("INSERT INTO evaluation_observations VALUES(?,?,?,?,?,?,?)",
                               (observation.observation_id, observation.recommendation_id, observation.horizon,
                                observation.evaluation_date.isoformat(), observation.method_version, canonical_json(payload), checksum))
            return observation.observation_id

    def evaluations(self, recommendation_id: str, *, horizon: int | None = None) -> list[dict]:
        condition = " AND horizon=?" if horizon is not None else ""
        params = (recommendation_id, horizon) if horizon is not None else (recommendation_id,)
        with self.store.transaction(write=False) as connection:
            rows = connection.execute("SELECT * FROM evaluation_observations WHERE recommendation_id=?" + condition + " ORDER BY evaluation_date, observation_id", params).fetchall()
            return [checked_payload(row) for row in rows]

    def health(self) -> dict:
        store_health = self.store.health()
        return {"component": "tracker", "status": store_health["status"], "immutable_records": True,
                "separate_observations": True, "schema_version": 1}

    def register_outcome_snapshot(self, snapshot: OutcomeDataSnapshot | dict) -> str:
        snapshot = OutcomeDataSnapshot.model_validate(snapshot.model_dump() if isinstance(snapshot, OutcomeDataSnapshot) else snapshot)
        payload = snapshot.model_dump(mode="json")
        identifier = "outcome_" + digest(payload)
        return self.store.put_document("outcome_snapshots", identifier, payload)

    def evaluate_registered(self, recommendation_id: str, snapshot_id: str, *, horizon: int = 7,
                            evaluated_at: datetime | None = None, costs: EvaluationCosts | dict | None = None) -> dict:
        snapshot = self._outcome_snapshot(snapshot_id)
        return self.evaluate(recommendation_id, list(snapshot.bars), list(snapshot.benchmark_bars),
                             horizon=horizon, evaluated_at=evaluated_at, costs=costs,
                             trading_sessions=list(snapshot.trading_sessions), source_snapshot_id=snapshot_id)

    def _outcome_snapshot(self, snapshot_id: str) -> OutcomeDataSnapshot:
        value = self.store.get_document("outcome_snapshots", snapshot_id)
        if value is None or snapshot_id != "outcome_" + digest(value):
            raise ValueError("registered outcome source missing or inconsistent")
        return OutcomeDataSnapshot.model_validate(value)

    def evaluate(self, recommendation_id: str, bars: list[EvaluationBar | dict],
                 benchmark_bars: list[EvaluationBar | dict], *, horizon: int = 7,
                 evaluated_at: datetime | None = None, costs: EvaluationCosts | dict | None = None,
                 trading_sessions: list[date] | None = None, method_version: str = "raw_total_return_v1",
                 source_snapshot_id: str | None = None, _persist: bool = True) -> dict:
        """Evaluate one calendar/trading-day horizon and append its immutable observation.

        Horizon starts at the actual entry. Calendar horizons mark at the first
        exchange session on/after the due date; missing sessions are not invented.
        Supply the central TradingCalendar's complete session list for coverage.
        """
        raw = self.get(recommendation_id)
        if raw is None:
            raise KeyError(recommendation_id)
        record = RecommendationRecord.model_validate(raw)
        if method_version != "raw_total_return_v1":
            raise ValueError("unknown evaluation method version")
        if record.status not in {"PUBLISHED", "RESEARCH_ONLY"}:
            raise ValueError("record is not an accepted recommendation")
        if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon <= 0:
            raise ValueError("horizon must be a positive integer")
        now = _aware(evaluated_at or utc_now())
        fees = EvaluationCosts.model_validate(costs.model_dump() if isinstance(costs, EvaluationCosts) else costs or {})
        prices = self._bars(bars, record.instrument_id)
        benchmark = self._bars(benchmark_bars, record.benchmark)
        source_snapshot = self._outcome_snapshot(source_snapshot_id) if source_snapshot_id else None
        if source_snapshot:
            if (source_snapshot.bars != tuple(prices) or source_snapshot.benchmark_bars != tuple(benchmark)
                    or source_snapshot.trading_sessions != tuple(trading_sessions or [])):
                raise ValueError("evaluation inputs differ from registered source")
        if record.status == "PUBLISHED" and (not source_snapshot or source_snapshot.kind != "OBSERVED"
                                               or source_snapshot.data_license != "APPROVED"):
            raise ValueError("published evaluation requires an approved observed local source")
        provenance = source_snapshot.kind if source_snapshot else "UNVERIFIED_RESEARCH_INPUT"
        visible = [bar for bar in prices if bar.available_at <= now]
        visible_benchmark = [bar for bar in benchmark if bar.available_at <= now]
        snapshots = sorted({bar.data_snapshot_id for bar in visible + visible_benchmark})
        observation = {"recommendation_id": recommendation_id, "horizon": horizon,
                       "horizon_unit": record.horizon_unit, "evaluation_date": now.date().isoformat(), "evaluated_at": now,
                       "executable": False, "state": "DATA_UNAVAILABLE", "method_version": method_version,
                       "data_snapshot_ids": snapshots, "costs": fees.model_dump(), "entry_rule": record.entry_rule,
                       "provenance": provenance}
        inputs = {"recommendation_id": recommendation_id, "bars": [bar.model_dump(mode="json") for bar in prices],
                  "benchmark_bars": [bar.model_dump(mode="json") for bar in benchmark],
                  "horizon": horizon, "evaluated_at": now.isoformat(), "costs": fees.model_dump(mode="json"),
                  "trading_sessions": [day.isoformat() for day in trading_sessions or []],
                  "method_version": method_version, "source_snapshot_id": source_snapshot_id}
        observation["input_snapshot_id"] = "evalinput_" + digest(inputs)

        def finish(state: str, reason: str | None = None) -> dict:
            observation["state"] = state
            observation["reason_codes"] = [reason] if reason else []
            # Content-derived identity: repeated identical observations are idempotent.
            observation["observation_id"] = "eval_" + digest(observation)[:32]
            model = EvaluationObservation.model_validate(observation)
            if _persist:
                # An orphan input snapshot after a crash is harmless. No outcome
                # exists until its immutable input has committed and replays.
                self.store.put_document("evaluation_inputs", observation["input_snapshot_id"], inputs)
                self.append_evaluation(model)
            return model.model_dump(mode="json", by_alias=True)

        if now < record.earliest_trade_at:
            return finish("NOT_MATURED", "ENTRY_NOT_REACHED")
        if source_snapshot and source_snapshot.available_at > now:
            return finish("DATA_UNAVAILABLE", "OUTCOME_SNAPSHOT_NOT_YET_AVAILABLE")
        if source_snapshot and not source_snapshot.calendar_coverage_start <= record.earliest_trade_at.astimezone(ZoneInfo("Asia/Shanghai")).date() <= source_snapshot.calendar_coverage_end:
            return finish("DATA_UNAVAILABLE", "ENTRY_OUTSIDE_CALENDAR_COVERAGE")
        if not trading_sessions:
            return finish("DATA_UNAVAILABLE", "TRADING_CALENDAR_REQUIRED")
        if any(not isinstance(day, date) or isinstance(day, datetime) for day in trading_sessions):
            raise ValueError("trading_sessions must contain dates")
        sessions = sorted(set(trading_sessions))
        if len(sessions) != len(trading_sessions):
            raise ValueError("duplicate trading calendar sessions")
        if any(bar.session not in sessions for bar in visible + visible_benchmark):
            return finish("DATA_UNAVAILABLE", "BAR_OUTSIDE_CALENDAR")
        entry_candidates = [bar for bar in visible if bar.open_at >= record.earliest_trade_at and bar.can_buy]
        if not entry_candidates:
            return finish("NOT_EXECUTABLE" if visible else "DATA_UNAVAILABLE", "NO_EXECUTABLE_ENTRY")
        entry = entry_candidates[0]
        target_prices = {bar.session: bar for bar in visible}
        first_eligible_day = record.earliest_trade_at.astimezone(ZoneInfo("Asia/Shanghai")).date()
        prior_sessions = [day for day in sessions if first_eligible_day <= day <= entry.session]
        if not prior_sessions or any(day not in target_prices for day in prior_sessions):
            return finish("DATA_UNAVAILABLE", "INCOMPLETE_ENTRY_PATH")
        observation.update(entry_at=entry.open_at, entry_price=entry.open, quantity=fees.quantity)
        if record.horizon_unit == "calendar_days":
            due = entry.session + timedelta(days=horizon)
            exit_session = next((day for day in sessions if day >= due), None)
        else:
            index = sessions.index(entry.session) + horizon
            exit_session = sessions[index] if index < len(sessions) else None
        if exit_session is None:
            return finish("DATA_UNAVAILABLE", "CALENDAR_HORIZON_UNAVAILABLE")
        target = target_prices.get(exit_session)
        if target is None:
            return finish("NOT_MATURED" if exit_session >= now.date() else "DATA_UNAVAILABLE", "HORIZON_PRICE_UNAVAILABLE")
        if not target.can_sell:
            return finish("NOT_EXECUTABLE", "EXIT_NOT_TRADABLE")
        required = [day for day in sessions if entry.session <= day <= exit_session]
        benchmark_lookup = {bar.session: bar for bar in visible_benchmark}
        if any(day not in target_prices or day not in benchmark_lookup for day in required):
            return finish("DATA_UNAVAILABLE", "INCOMPLETE_PRICE_PATH")
        path = [target_prices[day] for day in required]
        benchmark_path = [benchmark_lookup[day] for day in required]
        if not all(bar.corporate_actions_known for bar in path + benchmark_path):
            return finish("DATA_UNAVAILABLE", "CORPORATE_ACTIONS_UNAVAILABLE")
        if benchmark_path[0].open_at != entry.open_at or benchmark_path[-1].close_at != target.close_at:
            return finish("DATA_UNAVAILABLE", "BENCHMARK_TIME_MISMATCH")
        accounting = self._account(path, fees)
        # Benchmark is an explicit total-return reference, no assumed brokerage
        # account; corporate actions are included, trading costs are not.
        benchmark_costs = EvaluationCosts(quantity=1, lot_size=1, buy_commission_rate=0,
                                         sell_commission_rate=0, minimum_commission=0, sell_tax_rate=0)
        benchmark_accounting = self._account(benchmark_path, benchmark_costs)
        observation.update(executable=True, exit_at=target.close_at, exit_price=target.close,
                           entry_price=accounting["entry_execution_price"],
                           return_fraction=accounting["net_return"], benchmark_return=benchmark_accounting["net_return"],
                           excess_return=accounting["net_return"] - benchmark_accounting["net_return"],
                           max_drawdown=accounting["max_drawdown"],
                           accounting={**accounting, "benchmark": benchmark_accounting})
        return finish("MATURED")

    @staticmethod
    def _bars(bars: list[EvaluationBar | dict], instrument_id: str) -> list[EvaluationBar]:
        result = [EvaluationBar.model_validate(bar.model_dump() if isinstance(bar, EvaluationBar) else bar) for bar in bars]
        if any(bar.instrument_id != instrument_id for bar in result):
            raise ValueError("evaluation bars contain another instrument")
        if len({bar.session for bar in result}) != len(result):
            raise ValueError("duplicate daily evaluation bars")
        return sorted(result, key=lambda bar: bar.open_at)

    @staticmethod
    def _account(path: list[EvaluationBar], costs: EvaluationCosts) -> dict:
        entry_price = path[0].open * (1 + costs.slippage_bps / 10000)
        qty = float(costs.quantity)
        entry_value = entry_price * qty
        buy_fee = max(costs.minimum_commission, entry_value * costs.buy_commission_rate)
        invested = entry_value + buy_fee
        dividends = 0.0
        nav = [invested]
        # Entry buys at the post-action open. That day's split/dividend belongs
        # to pre-open holders, not this new position.
        for index, bar in enumerate(path):
            if index:
                qty *= bar.split_ratio
                dividends += qty * bar.cash_dividend_per_share
            nav.append(qty * bar.close + dividends)
        exit_price = path[-1].close * (1 - costs.slippage_bps / 10000)
        exit_value = qty * exit_price
        sell_fee = max(costs.minimum_commission, exit_value * costs.sell_commission_rate)
        tax = exit_value * costs.sell_tax_rate
        final_nav = exit_value + dividends - sell_fee - tax
        nav[-1] = final_nav
        peak, max_drawdown = nav[0], 0.0
        for value in nav:
            peak = max(peak, value)
            max_drawdown = max(max_drawdown, (peak - value) / peak)
        return {"basis": "hypothetical_single_security_total_return", "entry_execution_price": entry_price,
                "exit_execution_price": exit_price, "initial_shares": costs.quantity, "final_shares": qty,
                "invested": invested, "dividends": dividends, "buy_commission": buy_fee,
                "sell_commission": sell_fee, "sell_tax": tax, "final_nav": final_nav,
                "net_return": final_nav / invested - 1, "max_drawdown": max_drawdown,
                "nav": nav, "benchmark_cost_basis": "gross_total_return_reference"}
