"""Send a mail to the user from their own Gmail (user decision, 2026-11-18): used only to deliver the rotated password
of the private page site. The address lives in SF_HOME/publish/mail.json ({"address": ...}, sender = recipient); the
Gmail app password lives in the macOS Keychain (service KEYCHAIN_SERVICE, account = the address), never in a file.
"""
from __future__ import annotations

import json
import smtplib
import subprocess
from email.message import EmailMessage
from pathlib import Path

from . import store

KEYCHAIN_SERVICE = "longmai-gmail"
SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 465


def config_path() -> Path:
    return store.home() / "publish" / "mail.json"


def address() -> str | None:
    try:
        return json.loads(config_path().read_text()).get("address") or None
    except (OSError, ValueError):
        return None


def app_password(addr: str, runner=subprocess.run) -> str | None:
    r = runner(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", addr, "-w"],
               capture_output=True, text=True)
    value = (r.stdout or "").strip().replace(" ", "")
    return value if r.returncode == 0 and value else None


def configured() -> bool:
    addr = address()
    return bool(addr) and app_password(addr) is not None


def send(subject: str, body: str, smtp=smtplib.SMTP_SSL) -> None:
    addr = address()
    if not addr:
        raise RuntimeError(f"没有设置收件地址（{config_path()}）")
    secret = app_password(addr)
    if not secret:
        raise RuntimeError(f"钥匙串里没有 {KEYCHAIN_SERVICE} 的 Gmail 应用专用密码")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = addr, addr, subject
    msg.set_content(body)
    with smtp(SMTP_HOST, SMTP_PORT, timeout=30) as server:
        server.login(addr, secret)
        server.send_message(msg)
