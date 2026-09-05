"""Capture local source/runtime/test evidence without reading environment secrets."""
from collections import Counter
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT.parent / "review/astra-master"


def command(args, cwd=ROOT):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=60)
    return {"exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def git(*args, cwd=ROOT):
    result = command(["git", *args], cwd)
    if result["exit_code"]:
        raise RuntimeError(result["stderr"])
    return result["stdout"].strip()


def save(name, value):
    (OUTPUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


files = set(git("diff", "--name-only").splitlines()) | set(git("ls-files", "--others", "--exclude-standard").splitlines())
inventory = {}
for name in sorted(files):
    path = ROOT / name
    if path.is_file():
        inventory[name] = {"bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
for name in ("Start-StockFish.ps1", "Stop-StockFish.ps1", "Logs-StockFish.ps1", "ENGINEERING_BASELINE.md", "DEPLOYMENT.md"):
    path = ROOT.parent / name
    inventory["../" + name] = {"bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
save("source-final-inventory.json", inventory)
save("git-final.json", {"stockfish": {"branch": git("branch", "--show-current"), "commit": git("rev-parse", "HEAD"),
     "status": git("status", "--short"), "numstat": git("diff", "--numstat")},
     "qlib": {"branch": git("branch", "--show-current", cwd=ROOT.parent / "qlib"),
              "commit": git("rev-parse", "HEAD", cwd=ROOT.parent / "qlib"),
              "status": git("status", "--short", cwd=ROOT.parent / "qlib")},
     "created_commits": [], "push_performed": False})
packages = {}
for line in (ROOT / "requirements-core.txt").read_text().splitlines():
    if "==" in line and not line.startswith("#"):
        name = line.split("==")[0]
        packages[name] = version(name)
save("runtime-environment.json", {"python": sys.version, "executable": sys.executable,
     "platform": platform.platform(), "packages": packages,
     "pip_check": command([sys.executable, "-m", "pip", "check"]),
     "compile": command([sys.executable, "-m", "compileall", "-q", "stockfish_core", "app.py", "scripts"]),
     "diff_check": command(["git", "diff", "--check"])})
report = ET.parse(OUTPUT / "core-final.xml")
groups = Counter()
failures = []
for case in report.findall(".//testcase"):
    name = case.attrib["classname"]
    group = "API" if name.endswith(("test_core_api", "test_finetune_api")) else name.split(".")[1]
    groups[group] += 1
    if case.find("failure") is not None or case.find("error") is not None or case.find("skipped") is not None:
        failures.append(case.attrib)
save("tests-final-summary.json", {"counts": dict(sorted(groups.items())), "total": sum(groups.values()),
                                  "failures_errors_skips": failures, "xml": "core-final.xml"})
print(json.dumps({"tests": sum(groups.values()), "nonpassing": len(failures), "source_files": len(inventory)}))
