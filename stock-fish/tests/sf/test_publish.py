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


def test_each_day_gets_a_fixed_address_and_old_page_names_redirect(tmp_path, monkeypatch):
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    pages = tmp_path / "pages"
    (pages / "2026-11-19").mkdir(parents=True)
    (pages / "index.html").write_text('<a href="2026-11-19/20261118T164705Z.html">x</a>')
    (pages / "2026-11-19" / "20261118T164315Z.html").write_text("old")
    (pages / "2026-11-19" / "20261118T164705Z.html").write_text(
        '<a href="../go/prev-2026-11-19.html">p</a><a href="../2026-11-18/20261118T012642Z.html">q</a>new')
    out = publish.export()["out"]
    assert (out / "index.html").read_text() == '<a href="2026-11-19/">x</a>'
    day = (out / "2026-11-19" / "index.html").read_text()
    assert day.endswith("new") and 'href="../2026-11-18/"' in day                       # the newest page, stable links
    assert (out / "_redirects").read_text() == "/2026-11-19/2* /2026-11-19/ 302\n"


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


def test_rotation_waits_three_days_then_sets_deploys_and_mails(tmp_path, monkeypatch):
    import os
    import time
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    _pages(tmp_path)
    path = publish.new_password("old-password-value-1234")
    calls, sent = [], []

    def fake(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, "", "")
    assert publish.rotate(runner=fake, send=lambda s, b: sent.append(b), log=lambda *_: None) == 0
    assert not calls and not sent                                                       # too young: nothing done
    old = time.time() - 3 * 86400
    os.utime(path, (old, old))
    assert publish.rotate(runner=fake, send=lambda s, b: sent.append(b), log=lambda *_: None) == 0
    new = path.read_text().strip()
    assert new != "old-password-value-1234" and calls[0][1]["input"] == new and "deploy" in calls[1][0]
    assert new in sent[0] and "longmai-pages.pages.dev" in sent[0]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_rotation_keeps_the_old_password_when_cloudflare_refuses(tmp_path, monkeypatch):
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    _pages(tmp_path)
    publish.new_password("old-password-value-1234")
    refuse = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "")              # noqa: E731
    sent = []
    assert publish.rotate(runner=refuse, send=lambda s, b: sent.append(b), log=lambda *_: None, force=True) == 1
    assert publish.password_file().read_text().strip() == "old-password-value-1234" and not sent


def test_mail_failure_marks_the_run_failed_and_keeps_the_new_password(tmp_path, monkeypatch):
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    _pages(tmp_path)
    ok = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", "")                  # noqa: E731

    def broken(subject, body):
        raise OSError("smtp down")
    assert publish.rotate(runner=ok, send=broken, log=lambda *_: None, force=True) == 1
    assert len(publish.password_file().read_text().strip()) >= 20


def test_mail_reads_the_app_password_from_the_keychain(tmp_path, monkeypatch):
    import json
    from sf import mail
    monkeypatch.setenv("SF_HOME", str(tmp_path))
    assert mail.address() is None
    mail.config_path().parent.mkdir(parents=True)
    mail.config_path().write_text(json.dumps({"address": "me@example.com"}))
    keychain = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "abcd efgh ijkl mnop\n", "")  # noqa: E731
    assert mail.app_password("me@example.com", runner=keychain) == "abcdefghijklmnop"
    seen = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            seen["host"] = host

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def login(self, user, secret):
            seen["login"] = (user, secret)

        def send_message(self, msg):
            seen["to"] = msg["To"]
    monkeypatch.setattr(mail, "app_password", lambda addr: "abcdefghijklmnop")
    mail.send("主题", "正文", smtp=FakeSMTP)
    assert seen == dict(host="smtp.gmail.com", login=("me@example.com", "abcdefghijklmnop"), to="me@example.com")
