"""Bounded date selection over an operator-selected run or replay-range root.

Only direct ISO-date children are considered. Inventory reads one small summary
per completed day, or one snapshot for an in-progress day; selection still uses
RunView's full archive validation. Dates never become arbitrary filesystem paths.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
import re
from threading import RLock
from typing import Callable

from ..common import LiveError, SHANGHAI, aware
from .view import RunView, _read_json, _stat_key, parse_as_of


def parse_date(value: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise LiveError("INVALID_DATE")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise LiveError("INVALID_DATE") from exc


@dataclass(frozen=True)
class RunSelection:
    view: RunView
    as_of: datetime | str | None
    now: datetime
    selected_date: date | None


class RunCatalog:
    """Keep at most four fully parsed daily views, without changing archives."""

    def __init__(self, run_dir: str | Path, *, allow_in_progress=True,
                 now: Callable[[], datetime] | None = None):
        self.root = Path(run_dir).expanduser().absolute()
        self.allow_in_progress = allow_in_progress
        self._now = now or (lambda: datetime.now(SHANGHAI))
        self.single_view = RunView(self.root, allow_in_progress=allow_in_progress)
        self._views: OrderedDict[date, RunView] = OrderedDict()
        self._metadata_cache = {}
        self._first_snapshot_cache = None
        self._lock = RLock()
        # Existing single-run callers keep their exact no-date behavior,
        # including explicitly synthetic future replay fixtures.
        self.is_range = (not (self.root / "snapshots").exists()
                         and not (self.root / "summary.json").exists()
                         and self.root.is_dir() and not self.root.is_symlink()
                         and any(re.fullmatch(r"\d{4}-\d{2}-\d{2}", child.name)
                                 for child in self.root.iterdir()))

    def current_time(self) -> datetime:
        return aware(self._now())

    @staticmethod
    def bounds(now: datetime) -> tuple[date, date]:
        latest = aware(now).date()
        try:
            earliest = latest.replace(year=latest.year - 1)
        except ValueError:  # February 29 becomes February 28 of the prior year.
            earliest = latest.replace(year=latest.year - 1, day=28)
        return earliest, latest

    def _day_metadata(self, root: Path) -> date | None:
        if root.is_symlink():
            raise LiveError("RUN_INTEGRITY_ERROR")
        if not root.is_dir():
            return None
        folder = root / "snapshots"
        if folder.is_symlink():
            raise LiveError("RUN_INTEGRITY_ERROR")
        if not folder.is_dir():
            return None
        snapshots = sorted(folder.glob("*.json"))
        if not snapshots:
            return None
        if any(not re.fullmatch(r"\d{4}\.json", path.name) for path in snapshots):
            raise LiveError("RUN_INTEGRITY_ERROR")
        summary = root / "summary.json"
        path = summary if summary.exists() or summary.is_symlink() else snapshots[0]
        signature = _stat_key(path)
        cached = self._metadata_cache.get(path)
        if cached is not None and cached[0] == signature:
            return cached[1]
        value = _read_json(path)
        try:
            day = parse_date(value["date"]) if path == summary else parse_as_of(value["as_of"]).date()
        except (KeyError, AttributeError, LiveError) as exc:
            raise LiveError("RUN_INTEGRITY_ERROR") from exc
        if _stat_key(path) != signature:
            raise LiveError("RUN_INTEGRITY_ERROR")
        self._metadata_cache[path] = (signature, day)
        return day

    def _inventory(self, now: datetime) -> dict[date, Path]:
        if self.root.is_symlink():
            raise LiveError("RUN_INTEGRITY_ERROR")
        if not self.root.is_dir():
            return {}
        earliest, latest = self.bounds(now)
        result = {}
        if self.is_range:
            for child in self.root.iterdir():
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", child.name):
                    continue
                try:
                    declared_day = parse_date(child.name)
                except LiveError as exc:
                    raise LiveError("RUN_INTEGRITY_ERROR") from exc
                if child.is_symlink():
                    raise LiveError("RUN_INTEGRITY_ERROR")
                if not earliest <= declared_day <= latest:
                    continue
                actual_day = self._day_metadata(child)
                if actual_day is None:
                    continue
                if actual_day != declared_day:
                    raise LiveError("RUN_INTEGRITY_ERROR")
                if declared_day == latest and not self._visible_today(child, now):
                    continue
                result[declared_day] = child
        else:
            day = self._day_metadata(self.root)
            if (day is not None and earliest <= day <= latest
                and (day != latest or self._visible_today(self.root, now))):
                result[day] = self.root
        active = {root / "summary.json" for root in result.values()}
        active.update(path for path in self._metadata_cache if path.parent.name == "snapshots"
                      and path.parent.parent in result.values())
        self._metadata_cache = {path: entry for path, entry in self._metadata_cache.items() if path in active}
        return result

    def _visible_today(self, root: Path, now: datetime) -> bool:
        """Do not offer a synthetic/queued day before its first visible tick."""
        paths = sorted((root / "snapshots").glob("*.json"))
        if not paths:
            return False
        path = paths[0]
        signature = _stat_key(path)
        cached = self._first_snapshot_cache
        if cached is not None and cached[:2] == (path, signature):
            return cached[2] <= now
        value = _read_json(path)
        try:
            first_time = parse_as_of(value["as_of"])
            if first_time is None:
                raise ValueError("missing observation time")
        except (KeyError, ValueError, LiveError) as exc:
            raise LiveError("RUN_INTEGRITY_ERROR") from exc
        if _stat_key(path) != signature:
            raise LiveError("RUN_INTEGRITY_ERROR")
        self._first_snapshot_cache = (path, signature, first_time)
        return first_time <= now

    def dates(self) -> dict:
        with self._lock:
            now = self.current_time()
            earliest, latest = self.bounds(now)
            available = sorted(self._inventory(now))
            return {"schema_version": "LIVE_DASHBOARD_DATES_V1", "min_date": earliest.isoformat(),
                    "max_date": latest.isoformat(),
                    "latest_date": available[-1].isoformat() if available else None,
                    "available_dates": [day.isoformat() for day in available]}

    def select(self, selected_date: str | None = None, as_of=None) -> RunSelection:
        with self._lock:
            now = self.current_time()
            if selected_date is None and not self.is_range:
                return RunSelection(self.single_view, as_of, now, None)
            day = parse_date(selected_date) if selected_date is not None else None
            earliest, latest = self.bounds(now)
            if day is not None and not earliest <= day <= latest:
                raise LiveError("DATE_OUT_OF_RANGE")
            inventory = self._inventory(now)
            if day is None:
                day = max(inventory) if inventory else None
            if day is None or day not in inventory:
                raise LiveError("DATE_NOT_AVAILABLE")
            root = inventory[day]
            view = self._views.pop(day, None)
            if view is None or view.root != root:
                view = self.single_view if not self.is_range else RunView(root, allow_in_progress=self.allow_in_progress)
            self._views[day] = view
            while len(self._views) > 4:
                self._views.popitem(last=False)
            records, _, _ = view._load()
            if not records or parse_as_of(records[0]["as_of"]).date() != day:
                raise LiveError("RUN_INTEGRITY_ERROR")
            requested = parse_as_of(as_of)
            if requested is not None and requested.date() != day:
                raise LiveError("AS_OF_DATE_MISMATCH")
            if requested is not None and requested > now:
                raise LiveError("FUTURE_ACCESS_DENIED")
            if requested is None and parse_as_of(records[-1]["as_of"]) > now:
                visible = [parse_as_of(record["as_of"]) for record in records
                           if parse_as_of(record["as_of"]) <= now]
                if not visible:
                    raise LiveError("DATE_NOT_AVAILABLE")
                requested = max(visible)
            return RunSelection(view, requested, now, day)

    def validate_startup(self) -> dict:
        if not self.is_range:
            return self.single_view.status()
        selection = self.select()
        return selection.view.status(selection.as_of)
