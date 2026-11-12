"""Scheduled capture on the user's Mac (launchd user agents). Data that can only be fetched on the day (the 09:25
auction, minute bars within the source's ~5-session window) is lost when nobody runs the command, so these jobs run
it on weekdays and record each run:

Times below are Shanghai time; `install` converts them to the Mac's local time zone (launchd fires in local time).

  auction    09:26        sf auction (only on a trading day, 09:20-11:30; launchd runs a missed job on wake), then
                          sf prune --yes (keep only the valid list of each past target day)
  premarket  18:40        sf premarket (update, pools, seats, minute bars, list), then sf page
  intraday   21:30, 08:40 sf intraday --backfill (retries a failed minute-bar capture at another time of day)
  baserates  Sat 10:00    sf baserates (weekly refresh of the 历史同类 table)
  themes     Sat 11:00    sf themes (weekly board-membership snapshot; every refresh is archived)

Each run writes a log under SF_HOME/schedule/logs/ and the latest status per job under SF_HOME/schedule/status/;
a failure posts a local macOS notification. Nothing is uploaded or sent anywhere. The computer must be on and awake.
"""
from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from . import store

LABEL = "com.longmai.sf"
WEEKDAYS = (1, 2, 3, 4, 5)
JOBS = {
    "auction": dict(args=["auction"], times=[(9, 26)], days=WEEKDAYS, window=((9, 20), (11, 30)), trading=True,
                    then=[["prune", "--yes"]]),
    "premarket": dict(args=["premarket"], times=[(18, 40)], days=WEEKDAYS, window=((18, 0), (23, 59)), trading=True,
                      then=[["page"]]),
    "intraday": dict(args=["intraday", "--backfill"], times=[(21, 30), (8, 40)], days=WEEKDAYS, window=None,
                     trading=False),
    "baserates": dict(args=["baserates"], times=[(10, 0)], days=(6,), window=None, trading=False),
    "themes": dict(args=["themes"], times=[(11, 0)], days=(6,), window=None, trading=False),
}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _dir(name: str) -> Path:
    path = store.home() / "schedule" / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def should_run(job: str, now: datetime, sessions: list[str]) -> str | None:
    """None if the job should run now, else the reason to skip it."""
    spec = JOBS[job]
    today = str(now.date())
    if spec["trading"] and today not in sessions:
        return "非交易日"
    if spec["window"]:
        lo, hi = spec["window"]
        if not lo <= (now.hour, now.minute) <= hi:
            return f"不在 {lo[0]:02d}:{lo[1]:02d}-{hi[0]:02d}:{hi[1]:02d} 之间（可能是睡眠后补跑）"
    return None


def notify(title: str, message: str) -> None:
    """A local macOS notification (no network); silently skipped elsewhere."""
    if sys.platform != "darwin":
        return
    script = f'display notification {json.dumps(message)} with title {json.dumps(title)}'
    subprocess.run(["osascript", "-e", script], check=False, capture_output=True)


def run(job: str, now: datetime | None = None, runner=None) -> int:
    """Run one job through bin/sf, logging output and recording its status."""
    now = now or store.now_sh()
    stamp = now.strftime("%Y%m%dT%H%M%S")
    log = _dir("logs") / f"{job}-{stamp}.log"
    status = dict(job=job, started_at=now.isoformat(timespec="seconds"), log=str(log))
    try:
        reason = should_run(job, now, store.calendar())
    except Exception as error:  # calendar unavailable: run anyway rather than lose the day; the log says so
        reason = None
        status["calendar_error"] = str(error)[:200]
    if reason:
        status.update(skipped=reason, exit=0, finished_at=store.now_sh().isoformat(timespec="seconds"))
        log.write_text(f"skipped: {reason}\n", encoding="utf-8")
        _status(job, status)
        return 0
    warn = offset_changed()
    if warn:
        status["warning"] = warn
        notify("龙脉定时任务", warn)
    runner = runner or _run_sf
    code = 0
    with open(log, "w", encoding="utf-8") as f:
        for args in [JOBS[job]["args"]] + JOBS[job].get("then", []):
            f.write(f"$ sf {' '.join(args)}\n")
            f.flush()
            code = runner(args, f)
            f.write(f"exit {code}\n")
            if code != 0:
                break
    status.update(exit=code, finished_at=store.now_sh().isoformat(timespec="seconds"))
    _status(job, status)
    if code != 0:
        notify("龙脉定时任务失败", f"{job} 退出码 {code}，日志 {log.name}")
    return code


def _run_sf(args: list[str], out) -> int:
    proc = subprocess.run([str(repo_root() / "bin" / "sf"), *args], stdout=out, stderr=subprocess.STDOUT,
                          cwd=repo_root(), env=os.environ.copy())
    return proc.returncode


def _status(job: str, status: dict) -> None:
    path = _dir("status") / f"{job}.json"  # latest run per job: an operational marker, not a saved result
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def local_slot(weekday: int, hour: int, minute: int, tz=None) -> tuple[int, int, int]:
    """A Shanghai (weekday 1=Mon..7=Sun, hour, minute) as launchd local time (weekday 0=Sun..6=Sat). launchd uses the
    Mac's own time zone (the user's Mac is on Europe/London), so the job times are converted at install time."""
    from datetime import timedelta
    today = store.now_sh().date()
    day = today + timedelta(days=(weekday - today.isoweekday()) % 7)
    sh = datetime(day.year, day.month, day.day, hour, minute, tzinfo=store.SH)
    local = sh.astimezone(tz)
    return local.isoweekday() % 7, local.hour, local.minute


def local_offset(tz=None) -> float:
    """Hours between Shanghai and the Mac's local time right now (8 in London winter, 7 in summer)."""
    now = store.now_sh()
    return (now.utcoffset() - now.astimezone(tz).utcoffset()).total_seconds() / 3600


def plist(job: str, tz=None) -> dict:
    spec = JOBS[job]
    slots = [local_slot(d, h, m, tz) for d in spec["days"] for h, m in spec["times"]]
    intervals = [dict(Weekday=d, Hour=h, Minute=m) for d, h, m in slots]
    env = {k: os.environ[k] for k in ("SF_HOME", "SF_PYTHON") if k in os.environ}
    env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    return dict(Label=f"{LABEL}.{job}", ProgramArguments=[str(repo_root() / "bin" / "sf"), "schedule", "run", job],
                StartCalendarInterval=intervals, WorkingDirectory=str(repo_root()), EnvironmentVariables=env,
                StandardOutPath=str(_dir("logs") / f"launchd-{job}.out"),
                StandardErrorPath=str(_dir("logs") / f"launchd-{job}.err"))


def agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def install(jobs=None, load: bool = True) -> list[Path]:
    paths = []
    agents_dir().mkdir(parents=True, exist_ok=True)
    for job in jobs or JOBS:
        path = agents_dir() / f"{LABEL}.{job}.plist"
        if load and path.exists():
            subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(path)], check=False, capture_output=True)
        with open(path, "wb") as f:
            plistlib.dump(plist(job), f)
        if load:
            subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)], check=True, capture_output=True)
        paths.append(path)
    (_dir("status") / "install.json").write_text(json.dumps(dict(offset_hours=local_offset(), jobs=list(jobs or JOBS),
                                                                 installed_at=store.now_sh().isoformat(timespec="seconds"))))
    return paths


def offset_changed() -> str | None:
    """A warning when the Shanghai-local offset changed since install (daylight saving): the jobs would fire an hour
    off. The run windows are checked in Shanghai time, so a shifted auction run is skipped rather than mistimed."""
    path = _dir("status") / "install.json"
    if not path.exists():
        return None
    was = json.loads(path.read_text()).get("offset_hours")
    now = local_offset()
    return None if was == now else f"本机与北京时间的时差从 {was:g} 小时变成 {now:g} 小时（夏令时切换），请重新运行 sf schedule install"


def uninstall(jobs=None) -> list[Path]:
    removed = []
    for job in jobs or JOBS:
        path = agents_dir() / f"{LABEL}.{job}.plist"
        if path.exists():
            subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(path)], check=False, capture_output=True)
            path.unlink()
            removed.append(path)
    return removed


def status() -> list[dict]:
    out = []
    for job in JOBS:
        path = agents_dir() / f"{LABEL}.{job}.plist"
        s = _dir("status") / f"{job}.json"
        local = [local_slot(JOBS[job]["days"][0], h, m) for h, m in JOBS[job]["times"]]
        out.append(dict(job=job, installed=path.exists(), last=json.loads(s.read_text()) if s.exists() else None,
                        times="、".join(f"{h:02d}:{m:02d}" for h, m in JOBS[job]["times"]),
                        local="、".join(f"{h:02d}:{m:02d}" for _, h, m in local),
                        days="工作日" if JOBS[job]["days"] == WEEKDAYS else "周六"))
    return out
