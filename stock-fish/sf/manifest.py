"""Run manifest (invariant 3): what a saved list, review or evaluation was computed from.

- code: git commit of the checkout and whether `stock-fish/sf` had uncommitted changes
- components: the version of every rule module, and the thresholds that live outside `longmai.PARAMS`
- bars: the stored session window (session files are immutable)
- inputs: content ids of the mutable inputs (board membership, stock basics, index returns, hype notes,
  calendar, regulation reports); each is archived under meta/archive/ so the run can be re-derived after
  the file is overwritten. The seat-skill cache is derived from the stored seats, so only its id is kept.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import store

ARCHIVED = ("meta/theme_boards.parquet", "meta/theme_members.parquet", "meta/stock_basic.parquet",
            "meta/indexes.parquet", "meta/hype_notes.csv", "meta/calendar.json",
            "research/szse_weekly_supervision.csv")


def code_version() -> dict:
    repo = Path(__file__).resolve().parents[2]
    try:
        run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True,  # noqa: E731
                                        timeout=10, check=True).stdout.strip()
        return dict(commit=run("rev-parse", "HEAD"), dirty=bool(run("status", "--porcelain", "--", "stock-fish/sf")))
    except Exception:  # not a git checkout or git missing: stated, not guessed
        return dict(commit="UNKNOWN", dirty=None)


def components() -> dict:
    from . import (abnormal, baserates, context, crowding, intraday, longmai, mood, namethemes, notes, strategy,
                   temperature, threads)
    return dict(
        versions=dict(longmai=longmai.VERSION, strategy=strategy.STRATEGY_VERSION, limits=strategy.LIMITS_VERSION,
                      mood=mood.VERSION, temperature=temperature.VERSION, abnormal=abnormal.VERSION,
                      names=namethemes.VERSION, intraday_features=intraday.FEATURES,
                      intraday_notes=intraday.NOTES_VERSION, baserates=baserates.VERSION,
                      context=context.VERSION, threads=threads.VERSION),
        params=dict(mood_signs=mood.SIGNS, heat_mid=temperature.HEAT_MID, pain_mid=temperature.PAIN_MID,
                    crowding_hot_pct=crowding.HOT_PCT, note_max_age=notes.MAX_AGE,
                    st_ten_pct_from=strategy.ST_TEN_PCT_FROM, severe=abnormal.SEVERE))


def build(sessions: list[str]) -> dict:
    home = store.home()
    inputs = {rel: store.archive(home / rel) for rel in ARCHIVED}
    skill = sorted((home / "research").glob("seat_skill-*.parquet"), key=lambda f: f.stat().st_mtime)
    inputs["seat_skill"] = None if not skill else f"{skill[-1].name}:{store.file_id(skill[-1])}"  # latest written
    return dict(code=code_version(), **components(), inputs=inputs,
                bars=dict(first=sessions[0] if sessions else None, last=sessions[-1] if sessions else None,
                          sessions=len(sessions)))
