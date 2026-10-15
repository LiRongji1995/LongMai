"""Flask product shell backed by durable jobs and one recommendation service."""
from __future__ import annotations

from collections import defaultdict, deque
import atexit
from datetime import datetime
import hmac
from html import escape
import ipaddress
import json
import os
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit

from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context
from pydantic import ValidationError
from werkzeug.exceptions import HTTPException

from .common import UnavailableError
from .jobs import JobConflict, TERMINAL_STATES
from .service import ApplicationService, RecommendationRequest
from .storage import ImmutableConflict, StorageError


def create_app(service: ApplicationService | None = None) -> Flask:
    static = Path(__file__).resolve().parents[1] / "static"
    app = Flask(__name__, static_folder=str(static), static_url_path="")
    app.config.update(MAX_CONTENT_LENGTH=2 * 1024 * 1024, JSON_SORT_KEYS=False)
    core = service or ApplicationService()
    app.extensions["stockfish_core"] = core
    if service is None:
        atexit.register(core.close)
    api_key = os.environ.get("STOCKFISH_API_KEY", "")
    allowed_hosts = {"localhost", "127.0.0.1", "::1"} | set(filter(None, os.environ.get("STOCKFISH_ALLOWED_HOSTS", "").split(",")))
    visits: dict[str, deque] = defaultdict(deque)
    visit_lock = threading.Lock()
    # Both supported WSGI entries have four request threads. Long-lived streams
    # may occupy only two, leaving capacity for status, cancellation and health.
    stream_slots = threading.BoundedSemaphore(2)

    def diagnostic_request():
        return request.path.startswith(("/api/", "/health/"))

    @app.before_request
    def security_gate():
        hostname = urlsplit(request.host_url).hostname
        if hostname not in allowed_hosts:
            return jsonify(error="HOST_NOT_ALLOWED"), 403
        # Liveness is deliberately public and process-only. Other health aliases
        # have exactly the same peer, bearer and Origin checks as the API.
        if not diagnostic_request() or request.path == "/health/live":
            return None
        try:
            local = ipaddress.ip_address(request.remote_addr or "0.0.0.0").is_loopback
        except ValueError:
            local = False
        if api_key:
            supplied = request.headers.get("Authorization", "")
            if not hmac.compare_digest(supplied, f"Bearer {api_key}"):
                return jsonify(error="AUTHENTICATION_REQUIRED"), 401
        elif not local:
            return jsonify(error="REMOTE_API_REQUIRES_CONFIGURED_KEY"), 403
        origin = request.headers.get("Origin")
        if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
            return jsonify(error="CROSS_ORIGIN_REQUEST_DENIED"), 403
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            now = time.monotonic()
            with visit_lock:
                bucket = visits[request.remote_addr]
                while bucket and bucket[0] <= now - 60:
                    bucket.popleft()
                if len(bucket) >= 30:
                    return jsonify(error="RATE_LIMITED"), 429
                bucket.append(now)
        return None

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        if diagnostic_request():
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(Exception)
    def controlled_error(exc):
        if isinstance(exc, HTTPException):
            return jsonify(error=exc.name.upper().replace(" ", "_")), exc.code
        if isinstance(exc, (JobConflict, ImmutableConflict)):
            return jsonify(error="STATE_CONFLICT"), 409
        if isinstance(exc, (ValidationError, ValueError, TypeError)):
            # Validation internals may echo complete input; return only safe field paths.
            fields = [".".join(map(str, e["loc"])) for e in exc.errors()] if isinstance(exc, ValidationError) else []
            return jsonify(error="INVALID_REQUEST", fields=fields), 400
        if isinstance(exc, KeyError):
            return jsonify(error="NOT_FOUND"), 404
        if isinstance(exc, UnavailableError):
            return jsonify(error=exc.reason, component=exc.component, quality="UNAVAILABLE"), 503
        app.logger.error("core request failed component=api error_type=%s", type(exc).__name__)
        return jsonify(error="INTERNAL_FAILURE", quality="UNAVAILABLE"), 500

    def body():
        value = request.get_json()
        if not isinstance(value, dict):
            raise ValueError("JSON object required")
        return value

    def legacy_request(value: dict) -> RecommendationRequest:
        canonical_keys = set(RecommendationRequest.model_fields)
        legacy_keys = {"symbol", "symbols", "model", "master", "cost_price", "cost_prices", "shares", "total_assets", "available_cash", "scenario"}
        if set(value) - canonical_keys - legacy_keys:
            raise ValueError("unknown request fields")
        result = {k: v for k, v in value.items() if k in canonical_keys}
        for key in ("symbol", "symbols", "model", "master", "scenario"):
            if key in value and not isinstance(value[key], str):
                raise ValueError("text field required")
        symbols = value.get("symbols") or value.get("symbol")
        if symbols:
            result["instruments"] = core.resolve_symbols(tuple(s.strip() for s in symbols.split("/") if s.strip()))
        if value.get("model"):
            result["model_id"] = value["model"]
        for key in ("available_cash", "total_assets", "cost_price"):
            if key in value and (isinstance(value[key], bool) or not isinstance(value[key], (int, float)) or value[key] < 0):
                raise ValueError("invalid amount")
        if "available_cash" in value:
            result["cash"] = value["available_cash"]
        # Cost basis and total-assets guesses cannot substitute for current marks,
        # industry and T+1 sellability. Require the explicit Holding contract.
        shares = value.get("shares", 0)
        if isinstance(shares, str):
            if shares.strip() and any(float(s or 0) != 0 for s in shares.split("/")):
                raise ValueError("explicit holdings required")
        elif not isinstance(shares, (int, float)) or isinstance(shares, bool) or shares != 0:
            raise ValueError("explicit holdings required")
        if value.get("scenario") not in (None, "base"):
            raise ValueError("simulation scenarios are experimental")
        return RecommendationRequest.model_validate(result)

    def submit(kind, value):
        parsed = legacy_request(value)
        core._model(parsed)  # cheap preflight; expensive execution always uses the bounded worker.
        if parsed.snapshot_id:
            core.snapshot(parsed.snapshot_id)
        job = core.jobs.submit(kind, parsed.model_dump(mode="json"))
        return jsonify(task_id=job["job_id"], job_id=job["job_id"], state=job["state"], status="pending"), 202

    def presented_job(job):
        if job is None:
            raise KeyError("job")
        state = job["state"]
        status = "completed" if state == "SUCCEEDED" else "failed" if state in TERMINAL_STATES else "running"
        result = job.get("result") or {}
        return {**job, **result, "job_id": job["job_id"], "task_id": job["job_id"], "state": state,
                "status": status, "message": job.get("message") or result.get("message", ""),
                "error": job.get("error"), "progress": job["progress"]}

    def checked_core_health(*, deep=False):
        try:
            return core.health(deep=deep)
        except TypeError as exc:
            if "deep" not in str(exc):
                raise
            return core.health()

    def checked_readiness():
        if hasattr(core, "readiness"):
            return core.readiness()
        checked = checked_core_health(deep=False)
        status = checked.get("status")
        ready = status in {"healthy", "ready"}
        return {"status": "ready" if ready else "not_ready", "ready": ready,
                "checks": {"application": {"status": "ok" if ready else "not_ready"}},
                "reason_codes": [] if ready else ["APPLICATION_NOT_READY"],
                "health_contract": "readiness", "deep_integrity": "NOT_CHECKED_USE_/health/deep"}

    @app.get("/")
    def index():
        return send_from_directory(static, "index.html")

    @app.get("/health/live")
    def health_live():
        return jsonify(status="alive", alive=True, health_contract="liveness",
                       checks={"process": "alive", "http": "responsive"})

    @app.get("/health/ready")
    def health_ready():
        value = checked_readiness()
        return jsonify(value), 200 if value.get("ready") is True else 503

    @app.get("/health/deep")
    @app.get("/api/health/deep")
    def health_deep():
        candidates = getattr(core, 'candidates', None)
        checked = checked_core_health(deep=True)
        return jsonify(**checked, candidate_research=candidates.health(storage_health=checked.get('components',{}).get('tracker')) if candidates is not None else
                       {'status': 'UNAVAILABLE', 'reason_codes': ['CANDIDATE_SERVICE_NOT_CONFIGURED']})

    @app.get("/api/health")
    @app.get("/api/config/health")
    def health():
        candidates = getattr(core, 'candidates', None)
        checked=checked_core_health(deep=False)
        return jsonify(**checked, candidate_research=candidates.health(storage_health=checked.get('components',{}).get('tracker')) if candidates is not None else
                       {'status': 'UNAVAILABLE', 'reason_codes': ['CANDIDATE_SERVICE_NOT_CONFIGURED']})

    @app.get("/api/config")
    def config():
        return jsonify(backend="trusted-versioned", llm_configured=bool(core.research.transport),
                       llm_model=core.research.configuration.model, mirofish_available=False,
                       mode="RESEARCH", canonical_policy=core.recommendations.policy.version)

    @app.post("/api/config")
    def config_update():
        body()
        return jsonify(error="CONFIGURATION_IS_SERVER_MANAGED", quality="UNAVAILABLE"), 409

    @app.get("/api/qlib/models")
    def models():
        return jsonify([{"name": m.model_id, "model_id": m.model_id, "model_version": m.model_version,
                         "market": "csi300" if "csi300" in m.model_id else "declared-universe", "date": m.created_at.date().isoformat(),
                         "status": m.status, "checkpoint_hash": m.checkpoint_hash, "oos_validated": m.oos_validated,
                         "has_scores": True, "is_finetune": False} for m in core.registry.list()])

    @app.get("/api/qlib/index-stocks")
    def index_stocks():
        if request.args.get("index", "csi300") not in ("csi300", "hs300"):
            raise ValueError("only versioned CSI300 universe is installed")
        day = datetime.fromisoformat(request.args["as_of"]).date() if request.args.get("as_of") else None
        if day is None:
            calendar = core.data_dir / "calendars/day.txt"
            if not calendar.is_file():
                raise UnavailableError("CALENDAR_UNAVAILABLE", "data")
            day = datetime.fromisoformat(calendar.read_text().splitlines()[-1]).date()
        stocks = core.universe(day)
        if request.args.get("exclude_star", "false").lower() == "true":
            stocks = tuple(s for s in stocks if not s.startswith("688"))
        return jsonify(stocks="/".join(stocks), count=len(stocks), as_of=day.isoformat(), source="versioned_qlib_universe")

    @app.post("/api/analyze")
    def analyze():
        value = legacy_request(body())
        if not value.instruments:
            raise ValueError("instrument required")
        core._model(value)
        if value.snapshot_id:
            core.snapshot(value.snapshot_id)
        job = core.jobs.submit("recommendation", value.model_dump(mode="json"))
        # Keep the fast legacy success shape, with a bounded wait. Native Qlib
        # returns 202 and a durable handle instead of spawning unbounded workers.
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            job = core.jobs.get(job["job_id"])
            if job["state"] == "SUCCEEDED":
                return jsonify(job["result"])
            if job["state"] in TERMINAL_STATES:
                return jsonify(presented_job(job)), 503
            time.sleep(.02)
        return jsonify(task_id=job["job_id"], job_id=job["job_id"], state=job["state"], status="pending"), 202

    @app.post("/api/recommendations")
    @app.post("/api/predict")
    @app.post("/api/batch/analyze")
    @app.post("/api/qlib/infer")
    def start_recommendation():
        return submit("recommendation", body())

    @app.post("/api/quant/runs")
    def start_quant():
        return submit("quant", body())

    @app.get("/api/jobs/<job_id>")
    @app.get("/api/predict/<job_id>")
    @app.get("/api/batch/analyze/<job_id>")
    def job_status(job_id):
        return jsonify(presented_job(core.jobs.get(job_id)))

    @app.get("/api/jobs")
    @app.get("/api/predictions")
    def jobs():
        return jsonify([presented_job(j) for j in core.jobs.list(limit=min(100, int(request.args.get("limit", 50))))])

    @app.post("/api/jobs/<job_id>/cancel")
    def cancel(job_id):
        return jsonify(presented_job(core.jobs.cancel(job_id)))

    @app.post("/api/jobs/<job_id>/retry")
    def retry(job_id):
        return jsonify(presented_job(core.jobs.retry(job_id))), 202

    @app.get("/api/jobs/<job_id>/stream")
    @app.get("/api/predict/<job_id>/stream")
    @app.get("/api/batch/analyze/<job_id>/stream")
    @app.get("/api/qlib/infer/<job_id>/stream")
    @app.get("/api/qlib/train/<job_id>/stream")
    @app.get("/api/qlib/finetune/<job_id>/stream")
    @app.get("/api/qlib/data/update/<job_id>/stream")
    def job_stream(job_id):
        if core.jobs.get(job_id) is None:
            raise KeyError(job_id)
        if not stream_slots.acquire(blocking=False):
            return jsonify(error="STREAM_CAPACITY_EXCEEDED", retry_after_seconds=1), 429, {"Retry-After": "1"}
        release_lock = threading.Lock()
        released = False

        def release_slot():
            # Generator completion and WSGI close can both run; an unconsumed
            # response (including HEAD) must release its admission as well.
            nonlocal released
            with release_lock:
                if not released:
                    released = True
                    stream_slots.release()

        @stream_with_context
        def generate():
            try:
                revision = None
                deadline = time.monotonic() + 55
                while time.monotonic() < deadline:
                    value = presented_job(core.jobs.get(job_id))
                    if value["revision"] != revision:
                        yield "data: " + json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n\n"
                        revision = value["revision"]
                    if value["state"] in TERMINAL_STATES:
                        return
                    yield ": heartbeat\n\n"
                    time.sleep(0.5)
            finally:
                release_slot()
        try:
            response = Response(generate(), mimetype="text/event-stream", headers={"X-Accel-Buffering": "no"})
            response.call_on_close(release_slot)
            return response
        except BaseException:
            release_slot()
            raise

    @app.get("/api/recommendations/history")
    def history():
        return jsonify(core.tracker.list_history(instrument_id=request.args.get("instrument_id"),
                                                limit=min(1000, int(request.args.get("limit", 100)))))

    @app.get("/api/recommendations/<recommendation_id>")
    def get_recommendation(recommendation_id):
        value = core.tracker.get(recommendation_id)
        if value is None:
            raise KeyError(recommendation_id)
        return jsonify(record=value, evaluations=core.tracker.evaluations(recommendation_id))

    @app.get("/api/recommendation-runs/<run_id>")
    def recommendation_run(run_id):
        value = core.store.get_document("recommendation_runs", run_id)
        if value is None:
            raise KeyError(run_id)
        return jsonify(value)

    @app.post("/api/recommendation-runs/<run_id>/replay")
    def recommendation_replay(run_id):
        return jsonify(core.replay_recommendation(run_id).presentation())

    @app.get("/api/quant/runs/<run_id>")
    def quant_run(run_id):
        value = core.store.get_document("quant_runs", run_id)
        if value is None:
            raise KeyError(run_id)
        return jsonify(value)

    @app.get("/api/memory/stats")
    def memory_stats():
        return jsonify(storage=core.store.health(), records=len(core.tracker.list_history(limit=10000)),
                       jobs=core.jobs.health())

    @app.get("/api/stock/search")
    def stock_search():
        query = request.args.get("q", "").strip().upper()
        return jsonify([{"code": iid, "name": iid, "source": "versioned-universe"}
                        for iid in core.universe() if query and query in iid][:10])

    @app.get("/api/masters")
    def masters():
        return jsonify(masters=[], status="RESEARCH_ONLY", reason="LLM_HAS_NO_FINAL_VOTE")

    def report_document(result):
        payload = escape(json.dumps(result, indent=2, ensure_ascii=False))
        return f'<!doctype html><html lang="zh"><meta charset="utf-8"><title>StockFish recommendation audit</title><body><h1>统一推荐审计记录</h1><p>RESEARCH_ONLY 结果不代表实盘验证。成交须再次检查交易日条件。</p><pre>{payload}</pre></body></html>'

    @app.get("/api/predict/<job_id>/report")
    def job_report(job_id):
        job = core.jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job["state"] != "SUCCEEDED":
            return jsonify(error="REPORT_UNAVAILABLE"), 409
        return Response(report_document(job["result"]), mimetype="text/html")

    @app.post("/api/report/download")
    def download_report():
        value = body()
        run_id = value.get("recommendation_run_id")
        if not isinstance(run_id, str):
            raise ValueError("saved recommendation run required")
        result = core.store.get_document("recommendation_runs", run_id)
        if result is None:
            raise KeyError(run_id)
        return Response(report_document(result), mimetype="text/html", headers={"Content-Disposition": "attachment; filename=stockfish-audit.html"})

    @app.get("/api/qlib/train-targets")
    def train_targets():
        return jsonify([{"value": "linear-purged-v2", "label": "CPU 可复现基线（时间与摘要校验）",
                         "description": "提交 TrainRequest；保留所有分割、验证和失败记录"}])

    def training_worker(ctx):
        from .experiments import TrainRequest
        ctx.update(0.1, "校验特征快照与标签时间边界")
        with core.registry.pending_job(ctx.job_id):
            result = core.quant.train(TrainRequest.model_validate(ctx.payload))
        ctx.check_cancelled()
        value = {"status": "complete", "run_id": result.run_id, "training": result.model_dump(mode="json")}
        return ctx.complete(value, lambda connection: core.registry.publish_training(
            result, connection=connection, job_id=ctx.job_id))

    core.jobs.register("training", training_worker)

    from .common import CoreModel
    from .experiments import TrainRequest
    from .model_registry import safe_id

    class FinetuneRequest(CoreModel):
        training: TrainRequest
        base_model_id: str
        base_model_version: str

    def finetune_worker(ctx):
        value = FinetuneRequest.model_validate(ctx.payload)
        ctx.update(.1, "核验基础模型与完整清洁窗口；执行版本化重新拟合")
        with core.registry.pending_job(ctx.job_id):
            result = core.quant.finetune(value.training, base_model_id=value.base_model_id,
                                        base_model_version=value.base_model_version)
        ctx.check_cancelled()
        terminal = {"status": "complete", "run_id": result.run_id, "training": result.model_dump(mode="json"),
                    "method": "FULL_CLEAN_WINDOW_REFIT", "warm_start": False,
                    "base_model_id": value.base_model_id, "base_model_version": value.base_model_version}
        return ctx.complete(terminal, lambda connection: core.registry.publish_training(
            result, connection=connection, job_id=ctx.job_id))

    core.jobs.register("finetune", finetune_worker)

    @app.post("/api/qlib/train")
    def train():
        from .experiments import TrainRequest
        value = TrainRequest.model_validate(body())
        job = core.jobs.submit("training", value.model_dump(mode="json"))
        return jsonify(task_id=job["job_id"], state=job["state"], status="pending"), 202

    @app.post("/api/qlib/finetune")
    def finetune():
        value = FinetuneRequest.model_validate(body())
        safe_id(value.base_model_id)
        safe_id(value.base_model_version)
        # Reject unavailable/wrong-base requests before creating a durable job.
        base = core.registry.get(value.base_model_id, value.base_model_version)
        if base.format != "linear_v1" or base.feature_schema != value.training.feature_schema:
            raise UnavailableError("CLEAN_REFIT_BASE_SCHEMA_UNSUPPORTED", "quant")
        job = core.jobs.submit("finetune", value.model_dump(mode="json"))
        return jsonify(task_id=job["job_id"], state=job["state"], status="pending"), 202

    def update_worker(ctx):
        import importlib.util
        path = Path(__file__).resolve().parents[1] / "qlib-zh" / "data_runner.py"
        spec = importlib.util.spec_from_file_location("stockfish_versioned_data_update", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        def progress(value):
            ctx.update(min(.95, float(value.get("progress", .1))), value.get("message", "验证数据快照"))
        result = module.run_data_update(progress_callback=progress, expected_sha256=ctx.payload.get("expected_sha256"),
                                        cancel_check=ctx.check_cancelled)
        ctx.check_cancelled()
        if not result.get("success"):
            raise UnavailableError(result.get("error", "DATA_UPDATE_FAILED"), "data")
        validation = result.get("validation", {})
        return {"status": "complete", **result,
                "message": "数据下载和声明消费者内容校验完成；仅适用于 " + validation.get("consumer", "UNSPECIFIED_CONSUMER")
                           + "。其他模型特征与全市场覆盖未由此获验证；当前活动数据未改变。"}

    core.jobs.register("data_update", update_worker)

    @app.post("/api/qlib/data/update")
    def update_data():
        import re
        value = body() if request.content_length else {}
        if set(value) - {"expected_sha256"} or (value.get("expected_sha256") and
                (not isinstance(value["expected_sha256"], str) or not re.fullmatch(r"[a-fA-F0-9]{64}", value["expected_sha256"]))):
            raise ValueError("invalid archive digest")
        job = core.jobs.submit("data_update", value)
        return jsonify(task_id=job["job_id"], job_id=job["job_id"], state=job["state"], status="pending"), 202

    from .yaogu.candidate_api import register_candidate_routes
    # Protocol-limited dependency injection is used by the original HTTP security
    # contract probes. The actual ApplicationService always configures candidates.
    if getattr(core, 'candidates', None) is not None:
        register_candidate_routes(app, core.candidates)
    return app
