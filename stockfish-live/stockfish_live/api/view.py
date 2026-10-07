"""Integrity-checked, time-bounded views over one explicitly selected run.

The trusted archive reader can load later snapshots to verify a completed run.
Responses expose only snapshots and actual observations at or before ``as_of``.
No client-supplied filename, database path or directory is ever opened.
"""
from __future__ import annotations

from datetime import datetime
from copy import deepcopy
import json
from pathlib import Path
import re
import stat
from threading import RLock

from .. import BROKER_CONNECTION, LIVE_TRADING, MARKET_EDGE
from ..common import LiveError, aware, canonical_instrument, canonical_json, digest
from ..contracts.forecast import ForecastResult
from ..contracts.market import MinuteBar


def _read_json(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise LiveError("RUN_INTEGRITY_ERROR")
    try:
        def reject_constant(_value):
            raise ValueError("nonfinite JSON value")
        value = json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant)
    except (OSError, ValueError, UnicodeError) as exc:
        raise LiveError("RUN_INTEGRITY_ERROR") from exc
    if not isinstance(value, dict):
        raise LiveError("RUN_INTEGRITY_ERROR")
    return value


def parse_as_of(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        return aware(parsed)
    except (TypeError, AttributeError, ValueError) as exc:
        raise LiveError("INVALID_AS_OF") from exc


def _unavailable(reason="UNAVAILABLE") -> dict:
    return {"status": "UNAVAILABLE", "reason": reason}


def _stat_key(path):
    try:
        value = path.lstat()
    except OSError as exc:
        raise LiveError("RUN_INTEGRITY_ERROR") from exc
    if not stat.S_ISREG(value.st_mode):
        raise LiveError("RUN_INTEGRITY_ERROR")
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _compact_forecast(raw):
    """Transport summaries; the full immutable forecast remains in its artifact."""
    result = {key: deepcopy(value) for key, value in raw.items() if key != "horizons"}
    result["response_detail"] = "DASHBOARD_SUMMARY_FULL_EVIDENCE_IN_SAVED_ARTIFACT"
    result["horizons"] = []
    for horizon in raw.get("horizons", []):
        row = {key: deepcopy(value) for key, value in horizon.items()
               if key not in {"neighbors", "training_state_ids", "normalization_mean", "normalization_scale"}}
        row["training_state_count"] = len(horizon.get("training_state_ids", []))
        row["neighbors"] = []
        for neighbor in horizon.get("neighbors", [])[:10]:
            context = {key: deepcopy(value) for key, value in neighbor["context"].items() if key != "sectors"}
            sector_context = neighbor["context"].get("sectors") or {}
            context["sector_count"] = len(sector_context.get("sectors", [])) if isinstance(sector_context, dict) else 0
            outcome = neighbor["outcome"]
            compact_outcome = {key: deepcopy(value) for key, value in outcome.items()
                               if key not in {"future_path", "source_ids"}}
            compact_outcome["path_point_count"] = len(outcome.get("future_path", []))
            compact_outcome["source_count"] = len(outcome.get("source_ids", []))
            row["neighbors"].append({**{key: deepcopy(value) for key, value in neighbor.items()
                                       if key not in {"context", "outcome"}},
                                     "context": context, "outcome": compact_outcome})
        result["horizons"].append(row)
    return result


class RunView:
    """Read one run without changing any byte or creating a missing directory."""

    def __init__(self, run_dir: str | Path, *, allow_in_progress: bool = True):
        self.root = Path(run_dir).expanduser().absolute()
        self.allow_in_progress = allow_in_progress
        self._cache = {}
        self._validated_forecasts = {}
        self._lock = RLock()

    def _load(self) -> tuple[list[dict], dict | None, str]:
        with self._lock:
            return self._load_locked()

    def _cached_json(self, path, *, snapshot=False):
        signature = _stat_key(path)
        previous = self._cache.get(path)
        if previous is not None and previous[0] == signature:
            return previous[1]
        record = _read_json(path)
        staged = {}
        if snapshot:
            record, staged = self._validate_snapshot(path, record)
        if _stat_key(path) != signature:
            raise LiveError("RUN_INTEGRITY_ERROR")
        self._cache[path] = (signature, record)
        self._validated_forecasts.update(staged)
        return record

    def _validate_snapshot(self, path, record):
        if (not isinstance(record.get("snapshot_id"), str)
            or record["snapshot_id"] != digest({k: v for k, v in record.items() if k != "snapshot_id"})):
            raise LiveError("RUN_INTEGRITY_ERROR")
        try:
            if (record.get("schema_version") not in {"LIVE_REPLAY_STATE_V1", "LIVE_REPLAY_STATE_V2"}
                or not isinstance(record.get("state"), dict)
                or record.get("pit_status") not in {"VERIFIED_PIT", "RECONSTRUCTED", "RESEARCH_ONLY", "TEST_FIXTURE"}):
                raise ValueError("invalid snapshot contract")
            when = parse_as_of(record["as_of"])
            if path.name != when.strftime("%H%M.json"):
                raise ValueError("snapshot name/time mismatch")
            cutoff = parse_as_of(record.get("data_cutoff"))
            if cutoff is not None and cutoff > when:
                raise ValueError("future cutoff")
            for raw in record.get("received_bars", []):
                bar = MinuteBar.model_validate(raw)
                if bar.timestamp.date() != when.date() or bar.timestamp > when or bar.available_at > when or (
                    bar.source_timestamp is not None and bar.source_timestamp > when
                ):
                    raise ValueError("future or wrong-day actual observation")
            market = record.get("market_state")
            if market is not None and not isinstance(market, dict):
                raise ValueError("invalid market state")
            if market and market.get("as_of") and parse_as_of(market["as_of"]) > when:
                raise ValueError("future market state")
            if market and market.get("data_cutoff") and parse_as_of(market["data_cutoff"]) > when:
                raise ValueError("future market cutoff")
            forecasts, staged = {}, {}
            for identity, raw in (record.get("forecasts") or {}).items():
                fingerprint = digest(raw)
                key = raw.get("forecast_id")
                previous = staged.get(key) or self._validated_forecasts.get(key)
                if previous is not None and previous[0] != fingerprint:
                    raise ValueError("immutable forecast ID conflict")
                if previous is None:
                    forecast = ForecastResult.model_validate(raw)
                    compact = _compact_forecast(raw)
                else:
                    compact = previous[1]
                if compact["instrument_id"] != identity or parse_as_of(compact["as_of"]) > when:
                    raise ValueError("future or mismatched forecast")
                if previous is None:
                    staged[key] = (fingerprint, compact)
                forecasts[identity] = compact
            record["forecasts"] = forecasts
        except (KeyError, TypeError, ValueError, AttributeError, LiveError) as exc:
            raise LiveError("RUN_INTEGRITY_ERROR") from exc
        return record, staged

    def _load_locked(self):
        if self.root.is_symlink() or not self.root.is_dir():
            raise LiveError("RUN_UNAVAILABLE")
        folder = self.root / "snapshots"
        if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
            raise LiveError("RUN_INTEGRITY_ERROR")
        records = []
        bar_identities = set()
        for path in sorted(folder.glob("*.json")):
            if not re.fullmatch(r"\d{4}\.json", path.name):
                raise LiveError("RUN_INTEGRITY_ERROR")
            record = self._cached_json(path, snapshot=True)
            for row in record.get("received_bars", []):
                identity = (row["instrument_id"], row["timestamp"])
                if identity in bar_identities:
                    raise LiveError("RUN_INTEGRITY_ERROR")
                bar_identities.add(identity)
            records.append(record)
        records.sort(key=lambda record: parse_as_of(record["as_of"]))
        times = [parse_as_of(record["as_of"]) for record in records]
        if len(times) != len(set(times)) or len({t.date() for t in times}) > 1:
            raise LiveError("RUN_INTEGRITY_ERROR")
        marker = self.root / "FAILED"
        if marker.is_symlink() or (marker.exists() and not marker.is_file()):
            raise LiveError("RUN_INTEGRITY_ERROR")
        summary_path = self.root / "summary.json"
        summary = self._cached_json(summary_path) if summary_path.exists() or summary_path.is_symlink() else None
        if summary is not None:
            if (not records or summary.get("snapshot_sequence_hash") != digest([r["snapshot_id"] for r in records])
                or summary.get("ticks") != len(records)
                or summary.get("date") != times[0].date().isoformat()):
                raise LiveError("RUN_INTEGRITY_ERROR")
            for name, expected in (("final_state", records[-1]["state"]),
                                   ("pit_status", records[-1]["pit_status"]),
                                   ("scope", records[-1].get("scope")),
                                   ("failed_cycles", sum(r.get("cycle_error") is not None for r in records))):
                if name in summary and summary[name] != expected:
                    raise LiveError("RUN_INTEGRITY_ERROR")
        status = "FAILED" if marker.exists() else "COMPLETE" if summary is not None else "IN_PROGRESS"
        if status == "IN_PROGRESS" and not self.allow_in_progress:
            raise LiveError("RUN_INCOMPLETE")
        active = {folder / (parse_as_of(record["as_of"]).strftime("%H%M.json")) for record in records}
        if summary is not None:
            active.add(summary_path)
        self._cache = {path: value for path, value in self._cache.items() if path in active}
        return records, summary, status

    def _select(self, as_of=None) -> tuple[list[dict], dict | None, str, datetime | None]:
        requested = parse_as_of(as_of)
        records, summary, status = self._load()
        latest = parse_as_of(records[-1]["as_of"]) if records else None
        if requested is not None and (latest is None or requested > latest):
            raise LiveError("FUTURE_ACCESS_DENIED")
        if requested is not None:
            records = [r for r in records if parse_as_of(r["as_of"]) <= requested]
            if latest is not None and requested < latest:
                summary = None  # A final-state summary would leak later observations.
        return records, summary, status, requested

    @staticmethod
    def _metadata(records, status, requested):
        record = records[-1] if records else {}
        return {"run_status": status, "as_of": record.get("as_of"),
            "requested_as_of": requested.isoformat() if requested else None,
            "data_cutoff": record.get("data_cutoff"), "snapshot_id": record.get("snapshot_id"),
            "input_hash": record.get("input_hash"), "pit_status": record.get("pit_status", "UNKNOWN"),
            "scope": record.get("scope", "DECLARED_SCOPE_ONLY"),
            "market_edge": MARKET_EDGE, "live_trading": LIVE_TRADING,
            "broker_connection": BROKER_CONNECTION, "integrity_status": "VERIFIED",
            "fixture": record.get("pit_status") == "TEST_FIXTURE"}

    def _model_status(self, records):
        latest = {}
        for forecast in self._forecasts(records):
            latest[forecast["instrument_id"]] = forecast["status"]
        values = set(latest.values())
        if values == {"AVAILABLE"}:
            return "AVAILABLE", None
        if values & {"AVAILABLE", "PARTIAL"}:
            return "PARTIAL", "SOME_HORIZONS_OR_INDICES_UNAVAILABLE"
        if "INSUFFICIENT" in values:
            return "INSUFFICIENT", "INSUFFICIENT_MATURE_HISTORY"
        if values or (records and records[-1].get("schema_version") == "LIVE_REPLAY_STATE_V2"):
            return "IMPLEMENTED", "WAITING_FOR_SCHEDULED_FORECAST"
        return "UNAVAILABLE", "NO_SAVED_FORECAST"

    def status(self, as_of=None) -> dict:
        records, summary, status, requested = self._select(as_of)
        indices, _ = self._indices(records)
        model_status, reason = self._model_status(records)
        return {**self._metadata(records, status, requested), "snapshot_count": len(records),
            "timeline": [r["as_of"] for r in records], "indices": sorted(indices),
            "model_status": model_status, "model_reason": reason,
            "forecast_status": records[-1].get("forecast_status") if records else "UNAVAILABLE",
            "data_quality": records[-1]["state"].get("quality", "UNAVAILABLE") if records else "UNAVAILABLE",
            "summary": deepcopy(summary)}

    @staticmethod
    def _indices(records):
        indices, paths = {}, {}
        seen = set()
        for record in records:
            for row in record.get("received_bars", []):
                if row["asset_type"] != "INDEX":
                    continue
                identity = row["instrument_id"]
                key = (identity, row["timestamp"])
                if key in seen:
                    raise LiveError("RUN_INTEGRITY_ERROR")
                seen.add(key)
                point = {key: row.get(key) for key in ("timestamp", "available_at", "close", "quality")}
                paths.setdefault(identity, []).append(point)
                previous = indices.get(identity)
                previous_time = parse_as_of(previous.get("timestamp")) if previous else None
                if previous_time is None or parse_as_of(row["timestamp"]) >= previous_time:
                    indices[identity] = {"instrument_id": identity, "timestamp": row["timestamp"],
                                         "current_price": row["close"], "quality": row["quality"]}
            market = record.get("market_state") or {}
            details = market.get("indices") or {}
            if isinstance(details, dict):
                for identity, value in details.items():
                    if isinstance(value, dict):
                        indices[identity] = {**indices.get(identity, {}), **value, "instrument_id": identity}
        for rows in paths.values():
            rows.sort(key=lambda row: parse_as_of(row["timestamp"]))
        return indices, paths

    @staticmethod
    def _forecasts(records, identity=None):
        history = []
        seen = {}
        for record in records:
            for key, forecast in (record.get("forecasts") or {}).items():
                if identity is not None and key != identity:
                    continue
                forecast_id = forecast["forecast_id"]
                if forecast_id in seen and seen[forecast_id] != forecast:
                    raise LiveError("RUN_INTEGRITY_ERROR")
                if forecast_id not in seen:
                    history.append(forecast)
                    seen[forecast_id] = forecast
        history.sort(key=lambda row: parse_as_of(row["as_of"]))
        return history

    @staticmethod
    def _warnings(records):
        if not records:
            return ["NO_SAVED_SNAPSHOT"]
        record = records[-1]
        values = [*(record.get("state") or {}).get("warnings", []),
                  *((record.get("market_state") or {}).get("warnings") or []),
                  *(record.get("warnings") or [])]
        for forecast in (record.get("forecasts") or {}).values():
            values.extend(forecast.get("warnings", []))
        evidenced_codes = {value.get("code") for value in values if isinstance(value, dict)}
        values = [value for value in values if not isinstance(value, str) or value not in evidenced_codes]
        unique = {canonical_json(value): value for value in values}
        return [unique[key] for key in sorted(unique)]

    def market(self, as_of=None):
        records, _, status, requested = self._select(as_of)
        current = records[-1] if records else {}
        indices, paths = self._indices(records)
        forecasts = {}
        for forecast in self._forecasts(records):
            forecasts[forecast["instrument_id"]] = forecast
        return deepcopy({**self._metadata(records, status, requested),
            "market_state": current.get("market_state") or _unavailable("NO_SAVED_MARKET_STATE"),
            "indices": indices, "actual_paths": paths, "forecasts": forecasts,
            "forecast_revisions": current.get("forecast_revisions") or {},
            "data_quality": current.get("state") or _unavailable("NO_SAVED_SNAPSHOT"),
            "warnings": self._warnings(records)})

    def component(self, name: str, as_of=None):
        if name not in ("breadth", "sentiment", "sectors"):
            raise LiveError("INVALID_COMPONENT")
        result = self.market(as_of)
        value = result["market_state"].get(name)
        return {key: result[key] for key in self._metadata([], "", None)} | {
            name: value if value is not None else _unavailable("NO_SAVED_MARKET_STATE")}

    @staticmethod
    def _identity(identity: str):
        try:
            if identity != canonical_instrument(identity):
                raise ValueError("noncanonical instrument")
        except (ValueError, AttributeError) as exc:
            raise LiveError("INVALID_INDEX") from exc
        return identity

    def forecast(self, identity="000001.SH", as_of=None):
        identity = self._identity(identity)
        records, _, status, requested = self._select(as_of)
        history = self._forecasts(records, identity)
        return deepcopy({**self._metadata(records, status, requested), "index": identity,
            "forecast": history[-1] if history else _unavailable("NO_SAVED_FORECAST")})

    def history(self, identity="000001.SH", as_of=None):
        identity = self._identity(identity)
        records, _, status, requested = self._select(as_of)
        revisions = []
        seen = set()
        for record in records:
            revision = (record.get("forecast_revisions") or {}).get(identity)
            if revision is not None:
                key = digest(revision)
                if key not in seen:
                    revisions.append({"as_of": record["as_of"], "revision": revision})
                    seen.add(key)
        history = deepcopy(self._forecasts(records, identity))
        for forecast in history:
            for horizon in forecast.get("horizons", []):
                horizon.pop("neighbors", None)
        return deepcopy({**self._metadata(records, status, requested), "index": identity,
                         "history": history, "revisions": revisions})

    def similar_states(self, identity="000001.SH", as_of=None, limit=10):
        result = self.forecast(identity, as_of)
        grouped = {}
        for horizon in result["forecast"].get("horizons", []):
            for neighbor in horizon.get("neighbors", []):
                row = grouped.setdefault(neighbor["state_id"], {"state_id": neighbor["state_id"],
                    "decision_at": neighbor["decision_at"], "distance": neighbor["distance"],
                    "context": neighbor["context"], "future_results": {}, "outcomes": {}})
                outcome = neighbor["outcome"]
                row["future_results"][horizon["horizon"]] = outcome["target_price"] / outcome["anchor_price"] - 1.
                row["outcomes"][horizon["horizon"]] = outcome
        result.pop("forecast")
        result["similar_states"] = sorted(grouped.values(), key=lambda row: (row["distance"], row["state_id"]))[:limit]
        return result
