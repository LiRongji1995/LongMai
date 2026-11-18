"""Offline tests for the private page site (no network: wrangler is replaced by a fake runner)."""
from __future__ import annotations

import stat
import subprocess

import pytest

from sf import publish, schedule


def _pages(home):
    pages = home / "pages"
    (pages / "2026-11-19").mkdir(parents=True)
    (pages / "go").mkdir()
    (pages / "index.html").write_text('<a href="2026-11-19/a.html">x</a>')
    (pages / "2026-11-19" / "a.html").write_text(f"<li>日志：{home}/schedule/logs/x.log</li>")
    (pages / "go" / "next-2026-11-19.html").write_text("<p>hop</p>")
    (home / "lists").mkdir()
    (home / "lists" / "secret.csv").write_text("data")


def test_export_copies_pages_and_the_gate_only_and_removes_local_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        publish.export()
    _pages(tmp_path)
    r = publish.export()
    out = r["out"]
    assert r["files"] == 3 and r["leaks"] == []
    assert str(tmp_path) not in (out / "2026-11-19" / "a.html").read_text()
    assert publish.LOCAL in (out / "2026-11-19" / "a.html").read_text()
    assert not list(out.rglob("*.csv")) and (out / "_headers").exists() and "Disallow: /" in (out / "robots.txt").read_text()
    gate = (out.parent / "functions" / "_middleware.js").read_text()
    assert "SITE_PASSWORD" in gate and "HttpOnly" in gate
    (out / "stale.html").write_text("old")
    assert not (publish.export()["out"] / "stale.html").exists()                       # rewritten each time


def test_password_is_private_and_sent_on_stdin_only(tmp_path, monkeypatch):
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    path = publish.new_password()
    value = path.read_text().strip()
    assert len(value) >= 20 and stat.S_IMODE(path.stat().st_mode) == 0o600
    calls = []

    def fake(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, "", "")
    assert publish.push_password(runner=fake) == 0
    cmd, kw = calls[0]
    assert value not in " ".join(cmd) and kw["input"] == value                        # never on the command line
    assert publish.deploy(runner=fake, log=lambda *_: None) == 0
    assert calls[1][0][-5:-3] == ["--project-name", publish.PROJECT] and calls[1][1]["cwd"] == publish.root()


def test_scheduled_publish_is_skipped_when_not_set_up(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    from sf.__main__ import main
    assert main(["publish", "--deploy", "--if-configured"]) == 0 and "跳过上传" in capsys.readouterr().out
    assert ["publish", "--deploy", "--if-configured"] in schedule.JOBS["premarket"]["then"]
    assert schedule.JOBS["auction"]["then"][-1] == ["publish", "--deploy", "--if-configured"]
