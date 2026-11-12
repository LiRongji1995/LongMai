"""One pre-market list per trading day (user decision 2026-11-12).

A target day can collect several saved lists (re-runs, development, a rebuild after the open). Once the target day's
09:25 cutoff (Shanghai) has passed, only its valid list counts (listing.valid_run: the last one saved before the
cutoff). `prune` removes the other runs of that day, the auction snapshots taken against a removed run and the day's
local pages, then rewrites LATEST and renders one page from the valid run. Target days before their cutoff are left
alone. A day with no run saved before the cutoff keeps everything (nothing valid to keep instead).

Removed files are moved to the macOS Trash (a folder longmai-prune-<stamp>), not deleted outright, and every removal
is logged in meta/prune.jsonl. Bars, pools, intraday data, reviews and evaluations are never touched.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from . import listing, store


def trash_root() -> Path:
    return Path.home() / ".Trash"


def plan(now: datetime | None = None) -> list[dict]:
    """Per target day: the run to keep and the paths to remove (dry run; nothing is changed)."""
    now = now or store.now_sh()
    root = store.home() / "lists"
    out = []
    for day in sorted(p.name for p in root.iterdir() if p.is_dir()) if root.exists() else []:
        try:
            pending = now < listing.cutoff(day)
        except ValueError:
            continue
        runs = listing.runs(day)
        if pending or not runs:
            continue
        keep = listing.valid_run(day, now)
        if keep is None:
            out.append(dict(target=day, keep=None, remove=[], note="没有开盘前保存的清单，全部保留"))
            continue
        gone = [r for r in runs if r != keep]
        names = {r.name for r in gone}
        snaps = []
        for snap in sorted((store.home() / "auction" / day).glob("*/summary.json")):
            try:
                if json.loads(snap.read_text()).get("list_run") in names:
                    snaps.append(snap.parent)
            except ValueError:
                continue
        pages = sorted((store.home() / "pages" / day).glob("*.html"))
        out.append(dict(target=day, keep=keep, remove=gone + snaps + pages, note=""))
    return out


def size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) if path.is_dir() else path.stat().st_size


def apply(items: list[dict], render=None) -> Path | None:
    """Move the planned paths to the Trash, fix LATEST, log, and render one page per pruned day."""
    todo = [i for i in items if i["keep"] is not None]
    if not todo:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bin_ = trash_root() / f"longmai-prune-{stamp}"
    home = store.home()
    log = home / "meta" / "prune.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    for item in todo:
        moved = []
        for path in item["remove"]:
            if not path.exists():
                continue
            rel = path.relative_to(home)
            dest = bin_ / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            nbytes = size(path)
            shutil.move(str(path), str(dest))
            moved.append(dict(path=str(rel), bytes=nbytes))
        (item["keep"].parent / "LATEST").write_text(item["keep"].name)
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(at=stamp, target=item["target"], kept=item["keep"].name, moved=moved,
                                    trash=str(bin_)), ensure_ascii=False) + "\n")
        if render is None:
            from . import page
            render = page.write
        render(item["keep"])
    return bin_
