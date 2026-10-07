"""Preopen loading of independent, immutable research inputs."""
from datetime import datetime, time
import json
from pathlib import Path

from .common import LiveError, SHANGHAI, aware
from .contracts.context import ContextBundle
from .contracts.forecast import ForecastConfig
from .forecast.pipeline import MarketResearchPipeline
from .storage.context_store import ContextStore
from .storage.research_store import ResearchStore
from .storage.lineage import validate_history_provenance


def forecast_config(root):
    path = Path(root) / "research-config.json"
    if not path.exists():
        return ForecastConfig()
    if path.is_symlink():
        raise LiveError("CONFIG_INTEGRITY_ERROR")
    value = json.loads(path.read_text(encoding="utf-8"))
    if set(value) != {"schema_version", "forecast"} or value["schema_version"] != "LIVE_RESEARCH_CONFIG_V1":
        raise LiveError("INVALID_RESEARCH_CONFIG")
    return ForecastConfig.model_validate(value["forecast"])


def load_pipeline(root, calendar, scope, day, *, known_at=None, early_forecasts=False):
    before_open = datetime.combine(day, time(9, 30), SHANGHAI)
    known_at = aware(known_at or before_open)
    calendar.require_session(day, known_at, tuple(item.instrument_id[-2:] for item in scope.instruments))
    if scope.available_at > known_at:
        raise LiveError("SCOPE_NOT_YET_AVAILABLE")
    store = ResearchStore(root)
    history = store.visible("features", cutoff=known_at, now=known_at)
    history = tuple(item for item in history if item.decision_at.date() < day)
    ids = {item.state_id for item in history}
    if any((item.pit_status == "TEST_FIXTURE") != (scope.pit_status == "TEST_FIXTURE") for item in history):
        raise LiveError("DATA_PROVENANCE_MIXED")
    def visible_outcomes():
        for item in store.iter_visible("outcomes", cutoff=known_at, now=known_at):
            if item.state_id not in ids:
                continue
            if (item.pit_status == "TEST_FIXTURE") != (scope.pit_status == "TEST_FIXTURE"):
                raise LiveError("DATA_PROVENANCE_MIXED")
            yield item
    outcomes = validate_history_provenance(root, history, visible_outcomes(), calendar, scope,
        as_of=known_at, yield_outcomes=True)
    bundle = ContextStore(root).load()
    # Keep intraday revisions, including those that are not visible yet. Each
    # feature call still applies the actual visibility clock and validity range.
    end = datetime.combine(day, time(15), SHANGHAI)
    values = {"receipts": bundle.receipts}
    for name in ContextBundle.model_fields:
        if name in ("schema_version", "receipts"):
            continue
        values[name] = tuple(row for row in getattr(bundle, name)
            if row.effective_from <= end and (row.effective_to is None or row.effective_to > before_open))
    used = {row.source_id for name, rows in values.items() if name != "receipts" for row in rows}
    values["receipts"] = tuple(parent for parent in bundle.receipts if parent.source_id in used)
    return MarketResearchPipeline(calendar, scope, context=ContextBundle(**values),
        history=history, outcomes=outcomes, config=forecast_config(root), early_forecasts=early_forecasts)
