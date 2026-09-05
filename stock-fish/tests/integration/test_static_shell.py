"""Catch broken script boundaries in the retained legacy HTML download template."""
from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts, self.active, self.text_outside = [], None, []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            assert self.active is None
            self.active = {"attrs": dict(attrs), "code": ""}

    def handle_data(self, data):
        if self.active is not None:
            self.active["code"] += data
        else:
            self.text_outside.append(data)

    def handle_endtag(self, tag):
        if tag == "script":
            assert self.active is not None
            self.scripts.append(self.active)
            self.active = None


def test_html_script_boundaries_and_javascript_parse(tmp_path):
    parser = Scripts()
    parser.feed((ROOT / "static/index.html").read_text(encoding="utf-8"))
    assert parser.active is None
    assert sum(s["attrs"].get("src") == "/core.js" for s in parser.scripts) == 1
    assert "const blob" not in "".join(parser.text_outside)
    node = shutil.which("node")
    assert node, "Node is required for the static shell syntax gate"
    scripts = [ROOT / "static/core.js"]
    for i, script in enumerate(parser.scripts):
        if not script["attrs"].get("src"):
            path = tmp_path / f"inline-{i}.js"
            path.write_text(script["code"], encoding="utf-8")
            scripts.append(path)
    for path in scripts:
        completed = subprocess.run([node, "--check", str(path)], capture_output=True, text=True, timeout=10)
        assert completed.returncode == 0, completed.stderr
