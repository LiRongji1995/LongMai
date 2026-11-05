"""Load the preserved, finite SH/SZ exchange schedule without network access."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from .data import TradingCalendar


def exchange_calendar_2026() -> TradingCalendar:
    """SH/SZ only, finite official schedule verified 2026-09-05.

    Weekday rule and explicit exchange holidays are both sourced; no extension
    beyond 2026, and government weekend make-up workdays are never trading days.
    Source links and revision metadata are in exchange_calendar_2026.json.
    """
    config = json.loads(Path(__file__).with_name("exchange_calendar_2026.json").read_text(encoding="utf-8"))
    closed = set()
    for begin, end in config["closed_ranges"]:
        start, stop = date.fromisoformat(begin), date.fromisoformat(end)
        closed.update(start + timedelta(days=n) for n in range((stop-start).days + 1))
    start, stop = date(2026, 1, 1), date(2026, 12, 31)
    days = [start + timedelta(days=n) for n in range((stop-start).days + 1)]
    return TradingCalendar([d for d in days if d.weekday() < 5 and d not in closed], start, stop)
