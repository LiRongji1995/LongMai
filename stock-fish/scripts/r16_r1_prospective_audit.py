"""Read-only R16-R1 prospective audit; no service construction or score generation.

The output directory is new, private evidence. Original large run payloads are
stream-hashed, then projected with SQLite JSON1; saved outcome payloads are
streamed losslessly to JSONL. Runtime process/listener observations do not claim
to observe the scheduler's Python thread or the epoch loaded by that process.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import time
import traceback
import zlib

VERSION = "R16_R1_READ_ONLY_PROSPECTIVE_AUDIT_V1"
STAGE = "STAGE_AWARE_MOMENTUM_V2"
HORIZONS = (1, 2, 3, 4, 5, 10, 20)
METRICS = ("close_reference_change", "next_open_gap", "mfe_bound", "mae_bound",
           "any_close_limit_up", "any_close_limit_down")
SMALL_NAMESPACES = (
    "stage_aware_prospective_primary_runs", "stage_aware_prospective_validation_epochs",
    "stage_aware_prospective_validation_schedules", "recommendation_score_revisions",
    "strategy_research_pending", "strategy_research_horizon_requests",
    "candidate_schedule_events", "candidate_observation_plans",
    "strategy_config_activations",
)
OUTCOMES_NS = "strategy_trading_session_observations"
RUNS_NS = "strategy_experiment_runs"
DETAIL_NS = "strategy_experiment_item_details"
SOURCE_FILES = (
    "stockfish_core/yaogu/validation_identity.py", "stockfish_core/yaogu/recommendation_scoring.py",
    "stockfish_core/yaogu/strategy_tracking.py", "stockfish_core/yaogu/candidate_service.py",
    "stockfish_core/yaogu/scheduler.py", "stockfish_core/yaogu/stage_aware_momentum.py",
    "stockfish_core/yaogu/strategy_experiments.py", "stockfish_core/yaogu/source_resolution.py",
    "stockfish_core/yaogu/derived_feature_store.py", "stockfish_core/yaogu/history_artifacts.py",
    "stockfish_core/yaogu/contracts.py", "stockfish_core/storage.py", "stockfish_core/yaogu/visibility.py",
    "stockfish_core/yaogu/events.py", "stockfish_core/yaogu/theme_capital_trend.py",
    "stockfish_core/yaogu/coverage_diagnostics.py", "config/r05_source_lock.json",
    "config/r05_strategy_experiments.yaml", "config/r05_completion_requirements.yaml",
    "config/stage_aware_momentum_v2.yaml", "config/theme_capital_trend_v1.yaml", "config/yaogu_v1.defaults.yaml",
)
RUN_FIELDS = (
    "run_id", "input_id", "observation_date", "coverage_type", "audit_mode", "cutoff", "decision_cutoff",
    "implementation_version", "source_hash", "config_hash", "spec_hash", "strategy_spec_hash",
    "spec_revision", "spec_conformance", "execution_profile", "content_hash",
    "qualification", "prospective_validation", "signal_data_quality", "selection_results",
    "validation_epoch_id", "validation_protocol_hash", "score_spec_identity",
    "signal_implementation_version", "signal_spec_revision", "signal_config_hash", "signal_source_hash",
)
STAGE_FIELDS = (
    "strategy_id", "canonical_strategy_id", "stage_aware_implementation_version", "implementation_version",
    "stage_aware_spec_revision", "spec_revision", "stage_aware_config_hash", "config_hash",
    "stage_aware_source_hash", "source_hash", "scope_label", "spec_conformance", "counts",
)
ITEM_FIELDS = (
    "instrument_id", "rank", "decision_status", "primary_stage", "comparable_close", "comparable_basis",
    "condition_statuses", "conditions", "detail_ref", "detail_hash", "detail_encoding",
)
SCORE_FIELDS = (
    "score_id", "signal_run_id", "signal_observation_date", "validation_epoch_id", "evidence_type",
    "official_recommendation_score", "signal_evaluable", "data_quality_status", "reason_codes",
    "selection_tier", "setup_id", "evidence_role", "horizon", "horizon_label", "score", "score_status",
    "M", "valid_M", "selected_outcome_coverage", "benchmark_N", "benchmark_valid_N",
    "benchmark_coverage", "benchmark_min_coverage", "equal_weight_return", "eligible_market_return",
    "excess_return", "median_return", "hit_rate", "outperform_market_rate", "best_return", "worst_return",
    "K", "sampling_policy", "outcome_revision_identity", "source_outcome_observation_ids",
)


def text_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def utc_now():
    return datetime.now(timezone.utc)


def aware(value):
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("AWARE_TIMESTAMP_REQUIRED:" + str(value))
    return parsed.astimezone(timezone.utc)


def finite(value):
    try:
        return value is not None and not isinstance(value, bool) and math.isfinite(float(value))
    except (ValueError, TypeError, OverflowError):
        return False


def digest_json(value):
    return hashlib.sha256(text_json(value).encode("utf-8")).hexdigest()


class BudgetExceeded(RuntimeError):
    pass


class Audit:
    def __init__(self, args):
        self.args = args
        self.root = args.repo_root.resolve()
        self.project = self.root / "stock-fish"
        if not (self.project / "stockfish_core").is_dir():
            raise ValueError("REPO_ROOT_MUST_CONTAIN_STOCK_FISH")
        self.db = args.db.resolve(strict=True)
        self.output = args.output.resolve()
        self.output.mkdir(parents=True, exist_ok=False)
        (self.output / "raw").mkdir()
        self.started = utc_now()
        self.clock_started = time.monotonic()
        self.last_progress = 0.0
        self.phase = "START"
        self.counts = {}
        self.commands = []
        self.warnings = []
        self.exports = {}
        self.projections = {}
        self.raw_sinks = {}
        self.detail_cache = {}
        self.con = sqlite3.connect(self.db.as_uri() + "?mode=ro", uri=True, timeout=5)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA query_only=ON")
        self.con.set_progress_handler(self._sql_budget, 100000)
        self.con.execute("BEGIN")
        self.write_json("invocation.json", {
            "audit_version": VERSION, "argv": sys.argv, "args": {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
            "started_at": self.started.isoformat(), "db_path": str(self.db), "output": str(self.output),
            "access": "SQLITE_URI_MODE_RO_QUERY_ONLY_EXPLICIT_READ_SNAPSHOT_NO_SERVICE_CONSTRUCTION",
            "budget_seconds": args.budget_seconds,
        })
        self.progress("START", force=True)

    def _sql_budget(self):
        return int(time.monotonic() - self.clock_started > self.args.budget_seconds)

    def check_budget(self):
        if self._sql_budget():
            raise BudgetExceeded("AUDIT_BUDGET_EXCEEDED")

    def write_json(self, name, value):
        path = self.output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")

    def progress(self, phase, *, force=False, **values):
        self.check_budget()
        self.phase = phase
        self.counts.update(values)
        elapsed = time.monotonic() - self.clock_started
        if not force and elapsed - self.last_progress < self.args.progress_seconds:
            return
        value = {"audit_version": VERSION, "phase": phase, "at": utc_now().isoformat(),
                 "elapsed_seconds": elapsed, "budget_seconds": self.args.budget_seconds, "counts": self.counts}
        print(text_json(value), flush=True)
        with (self.output / "progress.jsonl").open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(text_json(value) + "\n")
        temporary = self.output / "checkpoint.tmp"
        temporary.write_text(text_json(value) + "\n", encoding="utf-8")
        temporary.replace(self.output / "checkpoint.json")
        self.last_progress = elapsed

    def command(self, argv, *, cwd=None, timeout=20):
        self.check_budget()
        started = utc_now()
        remaining = max(1, self.args.budget_seconds - (time.monotonic() - self.clock_started))
        result = {"argv": argv, "cwd": str(cwd or self.root), "started_at": started.isoformat()}
        try:
            completed = subprocess.run(argv, cwd=cwd or self.root, capture_output=True,
                                       text=True, encoding="utf-8", errors="replace", timeout=min(timeout, remaining),
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            result.update(exit_code=completed.returncode, stdout=completed.stdout, stderr=completed.stderr)
        except (OSError, subprocess.TimeoutExpired) as exc:
            result.update(exit_code=None, error=type(exc).__name__, detail=str(exc))
        result["finished_at"] = utc_now().isoformat()
        self.commands.append(result)
        self.write_json("commands.json", self.commands)
        return result

    def file_hash(self, path):
        hasher = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                self.check_budget()
                hasher.update(block)
        return hasher.hexdigest()

    def source_identity(self):
        sys.path.insert(0, str(self.project))
        from stockfish_core.yaogu.validation_identity import current_validation_identity
        started = utc_now()
        identity = current_validation_identity()
        files = {str(path.relative_to(self.project)).replace("\\", "/"): self.file_hash(path)
                 for path in (self.project / name for name in SOURCE_FILES) if path.is_file()}
        files["AUDIT_SCRIPT"] = self.file_hash(Path(__file__).resolve())
        git = {}
        for name, tail in (("commit", ["rev-parse", "HEAD"]), ("tree", ["rev-parse", "HEAD^{tree}"]),
                           ("branch", ["branch", "--show-current"]),
                           ("tracked_status", ["--no-optional-locks", "status", "--short", "--untracked-files=no"])):
            command = self.command(["git", *tail])
            git[name] = command.get("stdout", "").strip() if command.get("exit_code") == 0 else None
        value = {"calculated_at": started.isoformat(), "completed_at": utc_now().isoformat(),
                 "current_calculated_identity": identity, "code_file_sha256": files, "git": git,
                 "toolchain": {"python_executable": sys.executable, "python_version": sys.version,
                               "sqlite_version": sqlite3.sqlite_version, "platform": platform.platform()},
                 "semantics": "CALCULATED_FROM_THIS_PROCESS_SOURCE_FILES_NOT_REGISTERED_OR_ACTIVE_LIVE_EPOCH"}
        self.write_json("source_and_toolchain.json", value)
        return value

    def metadata(self, namespace):
        return [dict(row) for row in self.con.execute(
            "SELECT namespace,document_id,created_at,content_hash,length(CAST(payload AS BLOB)) AS payload_bytes "
            "FROM documents WHERE namespace=? ORDER BY document_id", (namespace,))]

    def document(self, namespace, document_id):
        row = self.con.execute("SELECT namespace,document_id,created_at,content_hash,payload FROM documents "
                               "WHERE namespace=? AND document_id=?", (namespace, document_id)).fetchone()
        if row is None:
            raise ValueError("MISSING_REFERENCED_DOCUMENT:" + namespace + ":" + document_id)
        return self.export(row)

    def export(self, row):
        self.check_budget()
        namespace, document_id = row["namespace"], row["document_id"]
        raw = row["payload"].encode("utf-8")
        actual = hashlib.sha256(raw).hexdigest()
        if actual != row["content_hash"]:
            raise ValueError("PERSISTED_DOCUMENT_HASH_MISMATCH:" + namespace + ":" + document_id)
        # The original canonical payload is retained. Metadata byte offsets and
        # lengths remain lossless even for an unusual multi-line saved payload;
        # that fact is marked instead of pretending it is ordinary JSONL.
        key = (namespace, document_id)
        if key not in self.exports:
            if namespace not in self.raw_sinks:
                self.raw_sinks[namespace] = (
                    (self.output / "raw" / (namespace + ".jsonl")).open("wb"),
                    (self.output / "raw" / (namespace + ".metadata.jsonl")).open("w", encoding="utf-8", newline="\n"),
                )
            raw_sink, metadata_sink = self.raw_sinks[namespace]
            offset = raw_sink.tell()
            raw_sink.write(raw + b"\n")
            meta = {k: row[k] for k in ("namespace", "document_id", "created_at", "content_hash")}
            meta.update(raw_utf8_sha256=actual, raw_bytes=len(raw), byte_offset=offset,
                        payload_has_literal_newline=(b"\n" in raw or b"\r" in raw))
            metadata_sink.write(text_json(meta) + "\n")
            self.exports[key] = meta
        return json.loads(raw)

    def small_documents(self):
        values = {}
        for namespace in SMALL_NAMESPACES:
            values[namespace] = []
            for row in self.con.execute("SELECT * FROM documents WHERE namespace=? ORDER BY document_id", (namespace,)):
                values[namespace].append(self.export(row))
            self.progress("SMALL_DOCUMENTS", namespace=namespace, rows=len(values[namespace]), force=True)
        return values

    def verify_run_blob(self, document_id):
        row = self.con.execute("SELECT rowid,content_hash,length(CAST(payload AS BLOB)) AS bytes FROM documents "
                               "WHERE namespace=? AND document_id=?", (RUNS_NS, document_id)).fetchone()
        if row is None:
            raise ValueError("MISSING_SIGNAL_PARENT:" + document_id)
        hasher, processed = hashlib.sha256(), 0
        with self.con.blobopen("documents", "payload", row["rowid"], readonly=True) as blob:
            for block in iter(lambda: blob.read(1024 * 1024), b""):
                self.check_budget()
                hasher.update(block)
                processed += len(block)
                self.progress("PARENT_BLOB_HASH", run_id=document_id, processed_bytes=processed, total_bytes=row["bytes"])
        actual = hasher.hexdigest()
        if actual != row["content_hash"]:
            raise ValueError("PERSISTED_PARENT_HASH_MISMATCH:" + document_id)
        return {"namespace": RUNS_NS, "document_id": document_id, "payload_bytes": processed,
                "source_document_sha256": actual, "verification": "ALL_ORIGINAL_PAYLOAD_BYTES_STREAM_HASHED"}

    def projection_values(self, document_id, paths):
        placeholders = ",".join("?" for _ in paths)
        row = self.con.execute("SELECT json_extract(payload," + placeholders + ") FROM documents "
                               "WHERE namespace=? AND document_id=?", (*paths, RUNS_NS, document_id)).fetchone()
        return json.loads(row[0])

    def hydrate_detail(self, item):
        ref = item.get("detail_ref")
        if not ref:
            return item
        if ref not in self.detail_cache:
            document = self.document(DETAIL_NS, ref)
            raw = zlib.decompress(base64.b64decode(document["payload_b64"], validate=True))
            if len(raw) != document["raw_bytes"] or hashlib.sha256(raw).hexdigest() != document["raw_sha256"]:
                raise ValueError("DETAIL_HASH_OR_LENGTH_MISMATCH:" + ref)
            self.detail_cache[ref] = (document, json.loads(raw))
        document, detail = self.detail_cache[ref]
        expected = digest_json({"sha256": document["raw_sha256"], "bytes": document["raw_bytes"]})
        if item.get("detail_hash") != expected or detail.get("instrument_id") != item.get("instrument_id"):
            raise ValueError("DETAIL_REFERENCE_IDENTITY_MISMATCH:" + ref)
        return {key: detail.get(key) for key in ITEM_FIELDS}

    def project_run(self, document_id):
        proof = self.verify_run_blob(document_id)
        paths = ["$." + key for key in RUN_FIELDS] + ["$.results." + STAGE + "." + key for key in STAGE_FIELDS]
        values = self.projection_values(document_id, paths)
        run = dict(zip(RUN_FIELDS, values[:len(RUN_FIELDS)]))
        stage = dict(zip(STAGE_FIELDS, values[len(RUN_FIELDS):]))
        run["results"] = {STAGE: stage}
        args = []
        for key in ITEM_FIELDS:
            args.extend([key, "$." + key])
        # json_each streams only the stage's items. json_object projects the
        # exact fields used by the independent production countability checker.
        expression = "json_object(" + ",".join("?,json_extract(j.value,?)" for _ in ITEM_FIELDS) + ")"
        query = ("SELECT " + expression + " FROM documents d,json_each(d.payload,?) j "
                 "WHERE d.namespace=? AND d.document_id=? ORDER BY CAST(j.key AS INTEGER)")
        items, detail_errors = [], []
        for row in self.con.execute(query, (*args, "$.results." + STAGE + ".items", RUNS_NS, document_id)):
            self.check_budget()
            item = json.loads(row[0])
            try:
                item = self.hydrate_detail(item)
            except (KeyError, ValueError, zlib.error) as exc:
                detail_errors.append({"instrument_id": item.get("instrument_id"), "detail_ref": item.get("detail_ref"), "error": str(exc)})
            items.append(item)
        stage["items"] = items
        proof.update(projection_fields=paths, item_projection_fields=list(ITEM_FIELDS),
                     parent_projection_sql="SELECT json_extract(payload," + ",".join("?" for _ in paths) + ") FROM documents WHERE namespace=? AND document_id=?",
                     item_projection_sql=query, item_projection_parameters=[*args, "$.results." + STAGE + ".items", RUNS_NS, document_id],
                     projected_item_count=len(items), missing_or_invalid_details=detail_errors,
                     projection_sha256=digest_json(run), source_role="ORIGINAL_FROZEN_PARENT_PROJECTION_NOT_RECOMPUTED_SIGNAL")
        self.write_json("parents/" + document_id + ".projection.json", run)
        self.projections[document_id] = proof
        self.progress("PARENT_PROJECTED", run_id=document_id, stage_items=len(items), force=True)
        return run

    def inspect_runs(self, small):
        from stockfish_core.yaogu.validation_identity import (official_primary_capture_for_run,
            official_signal_countable, signal_data_quality, validation_identity_for_run)
        metadata = self.metadata(RUNS_NS)
        self.write_json("all_signal_parent_metadata.json", metadata)
        # Read all saved parent identities and their stage audit fields; no
        # latest-N limit. Memory retains these projections, not 200MB raw runs.
        runs, results = {}, []
        for row in metadata:
            run = self.project_run(row["document_id"])
            runs[row["document_id"]] = run
            pv = run.get("prospective_validation") or {}
            identity = validation_identity_for_run(run)
            quality = signal_data_quality(run)
            errors = self.projections[row["document_id"]]["missing_or_invalid_details"]
            results.append({
                "run_id": row["document_id"], "observation_date": run.get("observation_date"),
                "source_document_sha256": row["content_hash"], "coverage_type": run.get("coverage_type"),
                "validation_role": pv.get("validation_role"), "frozen_identity": identity,
                "saved_official_primary_capture": pv.get("official_primary_capture"),
                "saved_official_primary_signal_countable": pv.get("official_primary_signal_countable"),
                "saved_official_validation_signal_countable": pv.get("official_validation_signal_countable"),
                "rechecked_official_primary_capture": official_primary_capture_for_run(run),
                "rechecked_signal_data_quality": quality,
                "rechecked_official_signal_countable": official_signal_countable(run) if not errors else None,
                "verification": "NOT_VERIFIED_DETAIL_CLOSURE" if errors else "PRODUCTION_PURE_CHECKER_OVER_HASH_BOUND_AUDIT_FIELDS",
                "decision_cutoff": run.get("decision_cutoff") or run.get("cutoff"),
                "actual_decision_cutoff": pv.get("actual_decision_cutoff"),
                "stage_counts": (run["results"][STAGE] or {}).get("counts"),
                "primary_registry_rows": [p for p in small["stage_aware_prospective_primary_runs"] if p.get("run_id") == row["document_id"]],
            })
        self.write_json("parent_projections_manifest.json", self.projections)
        self.write_json("official_signal_rechecks.json", results)
        return runs, results

    @staticmethod
    def expected_label_identity(run):
        return {"run_id": run.get("run_id"), "strategy_id": STAGE, "observation_date": run.get("observation_date"),
                "implementation_version": run.get("implementation_version"), "implementation_source_hash": run.get("source_hash"),
                "config_hash": run.get("config_hash"), "spec_hash": run.get("spec_hash") or run.get("strategy_spec_hash")}

    def inspect_outcomes(self, runs, rechecks, small):
        expected = {rid: self.expected_label_identity(run) for rid, run in runs.items()}
        frozen = {row["run_id"]: row["frozen_identity"] for row in rechecks}
        eligible = {rid: set((run.get("qualification") or {}).get("eligible_ids") or []) for rid, run in runs.items()}
        counts, ranges, best, binding_rejects = defaultdict(Counter), {}, {}, Counter()
        raw_total = 0
        for row in self.con.execute("SELECT * FROM documents WHERE namespace=? ORDER BY document_id", (OUTCOMES_NS,)):
            label = self.export(row)
            raw_total += 1
            rid, horizon = label.get("run_id"), label.get("horizon")
            key = (rid, label.get("strategy_id"), label.get("label_kind", "SIGNAL"), horizon)
            bucket = counts[key]
            bucket["raw_rows"] += 1
            bucket["status:" + str(label.get("status"))] += 1
            for metric in METRICS:
                if (label.get("metrics") or {}).get(metric) is None:
                    bucket["null_metric:" + metric] += 1
            if label.get("status") != "NOT_MATURED" and finite((label.get("metrics") or {}).get("close_reference_change")):
                bucket["saved_finite_price_rows_not_unique_samples"] += 1
            for field in ("observation_date", "label_end", "evaluated_at"):
                value = label.get(field)
                if value is not None:
                    if field == "evaluated_at":
                        try:
                            value = aware(value).isoformat()
                        except ValueError:
                            bucket["invalid_evaluated_at"] += 1
                            continue
                    previous = ranges.setdefault(field, {"min": str(value), "max": str(value)})
                    previous["min"], previous["max"] = min(previous["min"], str(value)), max(previous["max"], str(value))
            reason = None
            if rid not in runs:
                reason = "PARENT_RUN_NOT_SAVED"
            elif label.get("label_kind", "SIGNAL") != "SIGNAL" or label.get("strategy_id") != STAGE:
                reason = "OTHER_STRATEGY_OR_LABEL_KIND_SEPARATE_SCOPE"
            elif label.get("instrument_id") not in eligible[rid]:
                reason = "NOT_IN_FROZEN_ELIGIBLE_UNIVERSE"
            elif any(value in (None, "") or label.get(field) != value for field, value in expected[rid].items()):
                reason = "FROZEN_PARENT_IDENTITY_MISMATCH"
            elif any(field in label and label[field] != frozen[rid].get(field) for field in
                     ("signal_implementation_version", "signal_spec_revision", "signal_config_hash", "signal_source_hash")):
                reason = "OPTIONAL_STAGE_IDENTITY_MISMATCH"
            elif not label.get("observation_id") or type(horizon) is not int or horizon <= 0:
                reason = "LABEL_ID_OR_HORIZON_INVALID"
            else:
                try:
                    evaluated = aware(label["evaluated_at"])
                    if evaluated > self.started:
                        reason = "LABEL_AFTER_AUDIT_CUTOFF"
                except (KeyError, ValueError):
                    reason = "LABEL_EVALUATION_CLOCK_INVALID"
            if reason:
                binding_rejects[reason] += 1
            else:
                logical = (rid, label["instrument_id"], horizon)
                valid = label.get("status") != "NOT_MATURED" and finite((label.get("metrics") or {}).get("close_reference_change"))
                rank = (int(valid), evaluated, label["observation_id"], row["content_hash"])
                if logical not in best or rank > best[logical][0]:
                    best[logical] = (rank, {key: label.get(key) for key in
                        ("observation_id", "logical_label_id", "run_id", "strategy_id", "instrument_id", "observation_date",
                         "horizon", "label_end", "evaluated_at", "status", "coverage_type", "metrics", "metric_states",
                         "price_label_status", "return_exact", "reason_codes", "input_id", "input_hash")}, row["content_hash"])
            if raw_total % 250 == 0:
                self.progress("OUTCOME_STREAM", outcomes_processed=raw_total)
        aggregate = [{"run_id": key[0], "strategy_id": key[1], "label_kind": key[2], "horizon": key[3],
                      "frozen_epoch_if_parent_saved": frozen.get(key[0], {}).get("validation_epoch_id"), "counts": dict(value)}
                     for key, value in sorted(counts.items(), key=lambda pair: str(pair[0]))]
        self.write_json("outcome_raw_aggregation.json", {"raw_rows": raw_total, "time_ranges_as_saved": ranges,
            "raw_groups": aggregate, "binding_excluded_rows": dict(binding_rejects),
            "scope": "ALL_SAVED_ROWS_ALL_HORIZONS_INCLUDING_REVISIONS_NOT_UNIQUE_SIGNAL_SAMPLES"})
        selected_path = self.output / "selected_bound_outcomes.jsonl"
        with selected_path.open("w", encoding="utf-8", newline="\n") as handle:
            for key, (_, label, raw_hash) in sorted(best.items()):
                handle.write(text_json({"key": list(key), "original_document_sha256": raw_hash, "projection": label}) + "\n")
        pending = []
        registered_pending = {p["run_id"]: p for p in small["strategy_research_pending"]}
        requests = defaultdict(set)
        for request in small["strategy_research_horizon_requests"]:
            requests[request["run_id"]].update(request.get("horizons") or [])
        for rid, run in runs.items():
            index = registered_pending.get(rid)
            hs = set(HORIZONS) | set((index or {}).get("horizons") or []) | requests[rid]
            hs.update(h for parent, _, h in best if parent == rid)
            for horizon in sorted(hs, key=lambda value: (type(value) is not int, str(value))):
                if type(horizon) is not int or horizon <= 0:
                    self.warnings.append("INVALID_SAVED_REQUEST_HORIZON:" + str(rid) + ":" + str(horizon))
                    continue
                expected_ids = eligible[rid]
                labels = {iid: best[(rid, iid, horizon)][1] for iid in expected_ids if (rid, iid, horizon) in best}
                valid_ids = sorted(iid for iid, label in labels.items() if label.get("status") != "NOT_MATURED" and
                                   finite((label.get("metrics") or {}).get("close_reference_change")))
                missing_ids = sorted(expected_ids - labels.keys())
                statuses = dict(Counter(str(label.get("status")) for label in labels.values()))
                pending.append({"run_id": rid, "validation_epoch_id": frozen[rid]["validation_epoch_id"],
                    "coverage_type": run.get("coverage_type"), "horizon": horizon,
                    "pending_index_status": "SAVED" if index else "MISSING_INDEX_AUDIT_ONLY_NO_BACKFILL",
                    "expected_instrument_count": len(expected_ids), "present_bound_label_count": len(labels),
                    "saved_mature_finite_price_count": len(valid_ids), "absent_label_count": len(missing_ids),
                    "saved_status_counts": statuses, "mature_price_coverage": len(valid_ids)/len(expected_ids) if expected_ids else None,
                    "missing_instrument_ids": missing_ids,
                    "metric_missing_instruments": {metric: sorted(iid for iid in expected_ids if
                        (labels.get(iid, {}).get("metrics") or {}).get(metric) is None) for metric in METRICS},
                    "maturity_policy": "SAVED_LABEL_STATUS_AND_VALUE_ONLY_NO_NEW_PRICE_OR_OUTCOME_COMPUTATION"})
        self.write_json("pending_outcomes_by_run_horizon.json", {"audit_as_of": self.started.isoformat(), "items": pending,
            "revision_selection": "BOUND_PARENT_ELIGIBLE_SIGNAL; valid finite price preferred, then aware evaluated_at/observation_id/original_digest; mirrors production _latest_labels",
            "scope": "ALL_FROZEN_PARENT_STAGE_ELIGIBLE_IDS; OTHER_STRATEGIES_AND_POST_EXIT_REMAIN_IN_RAW_AGGREGATES",
            "notes": ["Pending index documents are durable demand, not a count of uncompleted outcomes.",
                      "Zero denominators have null coverage. Absent labels are not zero returns.",
                      "This audit does not infer calendar maturity or fabricate labels after an old saved evaluation date."]})
        self.progress("OUTCOMES_DONE", outcomes_processed=raw_total, selected_bound_labels=len(best), force=True)
        return {"raw_outcome_rows": raw_total, "selected_bound_label_count": len(best),
                "groups": aggregate, "pending_by_horizon": pending, "binding_excluded_rows": dict(binding_rejects)}

    def inspect_scores(self, small, runs, rechecks):
        records, by_group, issues = [], {}, []
        parent_checks = {row["run_id"]: row for row in rechecks}
        for document in small["recommendation_score_revisions"]:
            group = document.get("score_revision_group_id") or document.get("score_run_id")
            try:
                rank = (int(document.get("revision") or 0), aware(document["generated_at"]), document["score_run_id"])
            except (KeyError, ValueError):
                issues.append({"score_run_id": document.get("score_run_id"), "reason": "INVALID_REVISION_CLOCK"})
                rank = (-1, datetime.min.replace(tzinfo=timezone.utc), str(document.get("score_run_id")))
            if group not in by_group or rank > by_group[group][0]:
                by_group[group] = (rank, document)
            for record in document.get("records") or []:
                parent = parent_checks.get(record.get("signal_run_id"))
                eligible = bool(parent and parent.get("rechecked_official_signal_countable") is True
                                and record.get("validation_epoch_id") == parent["frozen_identity"]["validation_epoch_id"]
                                and document.get("validation_epoch_id") == parent["frozen_identity"]["validation_epoch_id"])
                records.append({"score_run_id": document.get("score_run_id"), "revision": document.get("revision"),
                    "score_revision_group_id": group, "generated_at": document.get("generated_at"),
                    "saved_official_validation_signal_countable": document.get("official_validation_signal_countable"),
                    "parent_rechecked_countable_and_epoch_matches": eligible,
                    **{key: record.get(key) for key in SCORE_FIELDS}})
        self.write_json("all_saved_score_records.json", {"authority": "EXISTING_IMMUTABLE_SCORE_REVISIONS_ONLY_NO_SCORE_COMPUTATION",
            "records": records, "issues": issues, "count": len(records)})
        latest = [value[1] for _, value in sorted(by_group.items())]
        observed = defaultdict(set)
        for document in latest:
            observed[(document.get("signal_run_id"), document.get("validation_epoch_id"))].update(
                row.get("horizon") for row in document.get("records") or [] if type(row.get("horizon")) is int)
        coverage = []
        for rid, run in runs.items():
            epoch = (run.get("prospective_validation") or {}).get("validation_epoch_id") or run.get("validation_epoch_id")
            for horizon in sorted(set(HORIZONS) | observed.get((rid, epoch), set())):
                saved = [row for document in latest if document.get("signal_run_id") == rid
                         for row in document.get("records") or [] if row.get("horizon") == horizon]
                coverage.append({"run_id": rid, "validation_epoch_id": epoch, "horizon": horizon,
                    "saved_score_record_count": len(saved), "status": "SAVED_RECORDS" if saved else "NO_SAVED_SCORE",
                    "score_status_counts": dict(Counter(str(row.get("score_status")) for row in saved))})
        self.write_json("latest_score_revision_groups.json", {"selection_policy": "LATEST_PER_IMMUTABLE_REVISION_GROUP_NOT_ONE_D1_HEADLINE",
            "score_run_ids": [row.get("score_run_id") for row in latest], "horizon_inventory": coverage})
        return {"saved_revision_documents": len(small["recommendation_score_revisions"]), "all_record_count": len(records),
                "latest_group_score_run_ids": [row.get("score_run_id") for row in latest], "horizon_inventory": coverage,
                "official_complete_records": [row for row in records if row.get("official_recommendation_score") is True
                    and row.get("saved_official_validation_signal_countable") is True and row.get("score_status") == "COMPLETE"
                    and row.get("parent_rechecked_countable_and_epoch_matches") is True],
                "issues": issues}

    def runtime(self, small):
        state_files = {}
        for name in ("stockfish-service.json", "stockfish-process.json"):
            path = self.root / "work" / name
            if path.is_file():
                raw = path.read_bytes()
                state_files[name] = {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
                                     "payload": json.loads(raw.decode("utf-8-sig"))}
            else:
                state_files[name] = {"path": str(path), "status": "NOT_PRESENT"}
        result = {"observed_at": utc_now().isoformat(), "saved_runtime_metadata": state_files,
                  "requested_port": self.args.port, "api_calls": [],
                  "scheduler_thread_alive": None, "scheduler_enabled": None, "active_live_validation_epoch_id": None,
                  "next_actual_execution": None, "verification": "NOT_VERIFIED_LIVE_PYTHON_SCHEDULER_THREAD",
                  "reason": "Process/listener and saved schedules cannot observe Python thread liveness or loaded epoch; schedule API writes state and was not called."}
        if os.name == "nt":
            root_literal = str(self.root).replace("'", "''")
            ps = ("$ErrorActionPreference='Stop'; $taskRoot='" + root_literal + "'; $port=" + str(self.args.port) + "; "
                  "$match=[regex]::Escape($taskRoot); "
                  "$processes=@(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match $match -and $_.CommandLine -match '(app\\.py|wsgi(?::|\\.py))' } | "
                  "Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine,@{Name='CreationTimeUtc';Expression={$_.CreationDate.ToUniversalTime().ToString('o')}}); "
                  "$listeners=@(Get-NetTCPConnection -State Listen | Where-Object {$_.LocalPort -eq $port} | Select-Object LocalAddress,LocalPort,OwningProcess); "
                  "[pscustomobject]@{ObservedAt=(Get-Date).ToUniversalTime().ToString('o');PowerShellVersion=$PSVersionTable.PSVersion.ToString();Processes=$processes;Listeners=$listeners} | ConvertTo-Json -Depth 6 -Compress")
            command = self.command(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                                    base64.b64encode(ps.encode("utf-16le")).decode("ascii")], timeout=25)
            result["process_probe_source"] = ps
            if command.get("exit_code") == 0:
                try:
                    result["process_listener_observation"] = json.loads(command["stdout"])
                except json.JSONDecodeError:
                    result["process_probe_status"] = "NOT_VERIFIED_NON_JSON_OUTPUT"
            else:
                result["process_probe_status"] = "NOT_VERIFIED_COMMAND_FAILED"
        else:
            result["process_probe_status"] = "NOT_VERIFIED_WINDOWS_RUNTIME_PROBE_NOT_APPLICABLE"
        schedules = small["stage_aware_prospective_validation_schedules"]
        future = []
        for schedule in schedules:
            try:
                if aware(schedule["scheduled_at"]) >= self.started:
                    future.append(schedule)
            except (KeyError, ValueError):
                continue
        result["next_persisted_plan"] = min(future, key=lambda row: aware(row["scheduled_at"]), default=None)
        result["next_persisted_plan_semantics"] = "SAVED_PLAN_ONLY_NOT_PROMISE_OF_RUNNING_SCHEDULER"
        job_ids = {row.get("job_id") or row.get("event_id") for row in small["candidate_schedule_events"]}
        job_ids |= {row.get("job_id") for row in schedules}
        jobs = []
        for row in self.con.execute("SELECT * FROM jobs ORDER BY job_id"):
            if row["job_id"] not in job_ids and row["kind"] != "scheduled_candidate_capture":
                continue
            item = dict(row)
            for key in ("payload", "data", "result", "error"):
                item[key] = json.loads(item[key]) if item.get(key) is not None else None
            jobs.append(item)
        self.write_json("saved_scheduler_jobs.json", {"items": jobs, "source": "READ_ONLY_JOBS_TABLE_SNAPSHOT"})
        successful = [row for row in jobs if row["kind"] == "scheduled_candidate_capture" and row["state"] == "SUCCEEDED" and row.get("finished_at")]
        result["last_saved_successful_capture_job"] = max(successful, key=lambda row: aware(row["finished_at"]), default=None)
        result["last_success_semantics"] = "SAVED_OPERATIONAL_CAPTURE_SUCCESS_NOT_COUNTABLE_SIGNAL_OR_PROVIDER_FRESHNESS"
        self.write_json("runtime_and_scheduler.json", result)
        return result

    def signal_summary(self, small, rechecks, calculated):
        verified = [row for row in rechecks if row["rechecked_official_signal_countable"] is True]
        captures = [row for row in rechecks if row["rechecked_official_primary_capture"] is True]
        epochs = small["stage_aware_prospective_validation_epochs"]
        registered = {row.get("validation_epoch_id") or row.get("epoch_id") for row in epochs}
        epoch_id = calculated["validation_epoch_id"]
        groups = []
        all_ids = registered | {row["frozen_identity"]["validation_epoch_id"] for row in rechecks} | {epoch_id}
        for item in sorted(all_ids - {None}):
            scoped = [row for row in rechecks if row["frozen_identity"]["validation_epoch_id"] == item]
            groups.append({"validation_epoch_id": item, "is_current_calculated": item == epoch_id, "is_registered": item in registered,
                "saved_parent_run_count": len(scoped), "official_capture_run_count": sum(row["rechecked_official_primary_capture"] is True for row in scoped),
                "countable_official_run_count": sum(row["rechecked_official_signal_countable"] is True for row in scoped),
                "countable_signal_days": sorted({row["observation_date"] for row in scoped if row["rechecked_official_signal_countable"] is True}),
                "not_verified_runs": [row["run_id"] for row in scoped if row["rechecked_official_signal_countable"] is None]})
        def latest(rows):
            def sort_key(row):
                try:
                    clock = aware(row["decision_cutoff"])
                except (KeyError, ValueError):
                    clock = datetime.min.replace(tzinfo=timezone.utc)
                return str(row.get("observation_date") or ""), clock
            return max(rows, key=sort_key, default=None)
        return {"official_registry_rows": len(small["stage_aware_prospective_primary_runs"]),
                "independently_rechecked_official_capture_runs": len(captures), "countable_official_signal_runs": len(verified),
                "countable_official_signal_days": sorted({row["observation_date"] for row in verified}),
                "latest_official_capture": latest(captures), "latest_countable_official_signal": latest(verified),
                "current_calculated_identity": calculated, "current_calculated_epoch_is_registered": epoch_id in registered,
                "registered_epoch_count": len(epochs), "per_frozen_epoch": groups,
                "active_live_epoch": None, "active_live_epoch_status": "NOT_VERIFIED_FROM_READ_ONLY_DATABASE_AND_SOURCE_FILES"}

    def smoke(self):
        result = {"audit_version": VERSION, "status": "SMOKE_ONLY_NOT_FULL_AUDIT", "namespaces": {}}
        for namespace in (*SMALL_NAMESPACES, RUNS_NS, OUTCOMES_NS):
            # Deliberately no payload read, no source import, no runtime process
            # probe, no large namespace scan in the preparation smoke.
            row = self.con.execute("SELECT document_id,created_at,content_hash FROM documents WHERE namespace=? LIMIT 1", (namespace,)).fetchone()
            result["namespaces"][namespace] = dict(row) if row else None
        result["connection_total_changes"] = self.con.total_changes
        result["query_only"] = self.con.execute("PRAGMA query_only").fetchone()[0]
        self.write_json("smoke.json", result)
        self.progress("SMOKE_DONE", force=True)
        return result

    def run(self):
        if self.args.smoke:
            return self.smoke()
        source = self.source_identity()
        scope_names = (*SMALL_NAMESPACES, RUNS_NS, OUTCOMES_NS)
        before = {name: self.metadata(name) for name in scope_names}
        self.write_json("source_document_manifest_before.json", before)
        small = self.small_documents()
        runs, rechecks = self.inspect_runs(small)
        outcome_summary = self.inspect_outcomes(runs, rechecks, small)
        scores = self.inspect_scores(small, runs, rechecks)
        runtime = self.runtime(small)
        signals = self.signal_summary(small, rechecks, source["current_calculated_identity"])
        self.write_json("official_signal_and_epoch_summary.json", signals)
        # End the original read snapshot, then observe whether original
        # immutable document identities survived concurrent external activity.
        total_changes = self.con.total_changes
        self.con.rollback()
        after = {name: self.metadata(name) for name in scope_names}
        self.write_json("source_document_manifest_after.json", after)
        missing, changed, added = [], [], []
        for namespace in scope_names:
            old = {row["document_id"]: row["content_hash"] for row in before[namespace]}
            new = {row["document_id"]: row["content_hash"] for row in after[namespace]}
            missing.extend([namespace, key] for key in old.keys() - new.keys())
            changed.extend([namespace, key] for key in old.keys() & new.keys() if old[key] != new[key])
            added.extend([namespace, key] for key in new.keys() - old.keys())
        immutability = {"connection_total_changes": total_changes, "query_only": self.con.execute("PRAGMA query_only").fetchone()[0],
            "original_document_ids_and_saved_hashes_unchanged": not missing and not changed,
            "missing_original_documents": missing, "changed_original_hashes": changed, "concurrently_added_documents": added,
            "semantics": "AUDIT_USES_READ_ONLY_HANDLE; additions_by_another_process_not_attributed_to_audit"}
        final_files = {name: self.file_hash(self.project / name) for name in SOURCE_FILES if (self.project / name).is_file()}
        changed_source = [name for name, value in final_files.items() if source["code_file_sha256"].get(name) != value]
        result = {"audit_version": VERSION, "started_at": self.started.isoformat(), "finished_at": utc_now().isoformat(),
            "elapsed_seconds": time.monotonic() - self.clock_started, "budget_seconds": self.args.budget_seconds,
            "status": "READ_ONLY_AUDIT_COMPLETE_WITH_EXPLICIT_NOT_VERIFIED_ITEMS" if not missing and not changed and not changed_source else "AUDIT_SOURCE_OR_ORIGINAL_DOCUMENT_CHANGED",
            "db_path": str(self.db), "source_identity": source, "read_only_and_immutability": immutability,
            "code_changed_during_audit": changed_source, "official_signals": signals,
            "score_summary": scores, "outcome_summary": {key: value for key, value in outcome_summary.items() if key not in {"groups", "pending_by_horizon"}},
            "runtime_summary": {key: runtime.get(key) for key in ("verification", "scheduler_thread_alive", "scheduler_enabled", "active_live_validation_epoch_id", "next_actual_execution", "next_persisted_plan", "last_saved_successful_capture_job")},
            "not_verified": ["LIVE_SCHEDULER_PYTHON_THREAD_AND_ENABLED_STATE", "EPOCH_LOADED_BY_LIVE_SERVICE", "NEXT_ACTUAL_EXECUTION", "PROVIDER_LIVE_FRESHNESS_NOT_PROBED"],
            "warnings": self.warnings, "market_edge": "NOT_ESTABLISHED", "exported_raw_documents": len(self.exports),
            "raw_payload_access": "raw/*.jsonl + byte offsets, original UTF8 SHA256 and document metadata; parent projection has independently streamed whole-original-payload hash"}
        self.write_json("audit.json", result)
        self.progress("DONE", force=True)
        return result

    def close(self):
        for handles in self.raw_sinks.values():
            for handle in handles:
                handle.close()
        self.raw_sinks.clear()
        self.con.close()
        manifest = []
        for path in sorted(self.output.rglob("*")):
            if path.is_file() and path.name not in {"SHA256SUMS", "artifact_manifest.json"}:
                # Final small-output hashing also respects the command budget.
                manifest.append({"path": path.relative_to(self.output).as_posix(), "bytes": path.stat().st_size,
                                 "sha256": self.file_hash(path)})
        self.write_json("artifact_manifest.json", manifest)
        manifest.append({"path": "artifact_manifest.json", "bytes": (self.output / "artifact_manifest.json").stat().st_size,
                         "sha256": self.file_hash(self.output / "artifact_manifest.json")})
        with (self.output / "SHA256SUMS").open("w", encoding="utf-8", newline="\n") as handle:
            for item in manifest:
                handle.write(item["sha256"] + "  " + item["path"] + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New private output directory; existing paths are rejected")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--budget-seconds", type=float, default=300)
    parser.add_argument("--progress-seconds", type=float, default=5)
    parser.add_argument("--smoke", action="store_true", help="Only namespace LIMIT 1 metadata reads; not a final audit")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535 or args.budget_seconds <= 0 or args.progress_seconds <= 0:
        parser.error("positive budget/progress and a valid port are required")
    audit = None
    exit_code = 0
    try:
        audit = Audit(args)
        result = audit.run()
        exit_code = 1 if result.get("status") == "AUDIT_SOURCE_OR_ORIGINAL_DOCUMENT_CHANGED" else 0
    except Exception as exc:
        if audit is not None:
            audit.write_json("failure.json", {"status": "FAILED_OR_BUDGET_EXCEEDED", "error_type": type(exc).__name__,
                "error": str(exc), "phase": audit.phase, "at": utc_now().isoformat(), "traceback": traceback.format_exc()})
        print(text_json({"status": "FAILED_OR_BUDGET_EXCEEDED", "error_type": type(exc).__name__, "error": str(exc)}), flush=True)
        exit_code = 1
    finally:
        if audit is not None:
            try:
                audit.close()
            except Exception as exc:
                # The failure marker remains reviewable; do not hide a failed
                # bounded audit behind successful-looking packaging evidence.
                audit.write_json("packaging_incomplete.json", {"status": "NOT_VERIFIED", "error": str(exc)})
                exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
