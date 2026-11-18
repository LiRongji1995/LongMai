"""Private page site on Cloudflare Pages (user decision, 2026-11-18).

Only the rendered pages leave the machine: pages/<day>/*.html, pages/go/*.html, index.html and latest.html. Local
paths inside them are replaced and no-index headers are added; nothing else under SF_HOME is copied. The export
(`publish/site/` plus the password gate in `publish/functions/`) is a rendering, rewritten on every run.

The site is closed by a password gate (`sf/site/_middleware.js`, a Pages Function). The password is generated
locally, kept in `publish/password.txt` (mode 600) and stored on Cloudflare as the encrypted SITE_PASSWORD secret;
without that secret the gate refuses every request. It is never printed.
"""
from __future__ import annotations

import os
import re
import secrets
import shutil
import subprocess
from pathlib import Path

from . import store

LOCAL = "（本机数据目录）"
PROJECT = "longmai-pages"
WRANGLER = ["npx", "--yes", "wrangler@3"]
NODE_DIRS = ("/usr/local/bin", "/opt/homebrew/bin")   # launchd jobs start with a bare PATH
HEADERS = "/*\n  X-Robots-Tag: noindex, nofollow, noarchive\n  Referrer-Policy: no-referrer\n"
ROBOTS = "User-agent: *\nDisallow: /\n"
GATE = Path(__file__).parent / "site" / "_middleware.js"


def root() -> Path:
    return store.home() / "publish"


def out_dir() -> Path:
    return root() / "site"


def password_file() -> Path:
    return root() / "password.txt"


def scrub(text: str, home: Path | None = None) -> str:
    """Replace the data folder and the user's home folder with neutral words."""
    home = home or store.home()
    for r in sorted({str(home.resolve()), str(home), str(Path.home())}, key=len, reverse=True):
        text = text.replace(r, LOCAL)
    return re.sub(r"file://\S*?(?=[\"'<\s])", LOCAL, text)


def export(out: Path | None = None) -> dict:
    src = store.home() / "pages"
    if not (src / "index.html").exists():
        raise FileNotFoundError("还没有本地页面（先运行 sf page）")
    out = out or out_dir()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    files = [p for p in src.rglob("*.html") if p.parent == src or p.parent.parent == src]
    for p in files:
        target = out / p.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(scrub(p.read_text(encoding="utf-8")), encoding="utf-8")
    (out / "_headers").write_text(HEADERS)
    (out / "robots.txt").write_text(ROBOTS)
    functions = out.parent / "functions"
    functions.mkdir(exist_ok=True)
    shutil.copyfile(GATE, functions / "_middleware.js")
    leaks = [str(p.relative_to(out)) for p in out.rglob("*.html") if "/Users/" in p.read_text(encoding="utf-8")]
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    return dict(out=out, files=len(files), size=size, leaks=leaks)


def new_password() -> Path:
    """Write a new random password (never printed); the caller pushes it to Cloudflare."""
    path = password_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(secrets.token_urlsafe(18) + "\n")
    os.chmod(path, 0o600)
    return path


def _env() -> dict:
    env = dict(os.environ)
    found = shutil.which("npx")
    dirs = [str(Path(found).parent)] if found else [d for d in NODE_DIRS if (Path(d) / "npx").exists()]
    env["PATH"] = os.pathsep.join(dirs + [env.get("PATH", "/usr/bin:/bin")])
    return env


def push_password(project: str = PROJECT, runner=subprocess.run) -> int:
    value = password_file().read_text().strip()
    result = runner([*WRANGLER, "pages", "secret", "put", "SITE_PASSWORD", "--project-name", project],
                    input=value, text=True, capture_output=True, cwd=root(), env=_env())
    return result.returncode


def deploy(project: str = PROJECT, runner=subprocess.run, log=print) -> int:
    """Upload publish/site with the gate in publish/functions (run from publish/ so wrangler finds functions/)."""
    result = runner([*WRANGLER, "pages", "deploy", "site", "--project-name", project, "--branch", "main",
                     "--commit-dirty=true"], text=True, capture_output=True, cwd=root(), env=_env())
    tail = (result.stdout or "").strip().splitlines()[-2:] + (result.stderr or "").strip().splitlines()[-3:]
    for line in tail:
        log(re.sub(r"\x1b\[[0-9;]*m", "", line))
    return result.returncode
