"""Controlled wrapper for LEAN ToolBox RandomDataGenerator.

This script is intentionally fail-closed.  It does not download market data or
modify a LEAN source checkout.  Callers must provide an isolated ToolBox runtime
copy whose ``config.json`` may be changed without touching the source checkout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import zipfile
from pathlib import Path

LEAN_COMMIT = "0269115d3cfbf691c7a0b7cfcc9ed412cafb91f6"
SEED = 11399
SYMBOL = "SYNTH"
RELATIVE_FILES = (
    Path("equity/usa/daily/synth.zip"),
    Path("equity/usa/map_files/synth.csv"),
    Path("equity/usa/factor_files/synth.csv"),
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_pinned_source(source_root: Path) -> None:
    if not (source_root / ".git").exists():
        raise ValueError("LEAN_SOURCE_ROOT must be a LEAN git checkout")
    actual = subprocess.run(
        ["git", "-C", str(source_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if actual != LEAN_COMMIT:
        raise ValueError(f"LEAN commit mismatch: expected {LEAN_COMMIT}, got {actual}")


def configure_runtime(runtime: Path, data_root: Path) -> Path:
    """Set only data-folder in an isolated LEAN JSON-with-comments config."""
    toolbox = runtime / "QuantConnect.ToolBox.dll"
    config = runtime / "config.json"
    if not toolbox.is_file() or not config.is_file():
        raise FileNotFoundError("isolated runtime lacks ToolBox DLL or config.json")
    escaped = str(data_root.resolve()).replace("\\", "\\\\")
    text = config.read_text(encoding="utf-8-sig")
    updated, changes = re.subn(
        r'(?m)^\s*"data-folder"\s*:\s*"[^"]*"\s*,',
        f'  "data-folder": "{escaped}",',
        text,
        count=1,
    )
    if changes != 1:
        raise ValueError("isolated config has no unique data-folder field")
    config.write_text(updated, encoding="utf-8", newline="\n")
    return toolbox


def validate_fixture(data_root: Path) -> dict[str, object]:
    files = [data_root / item for item in RELATIVE_FILES]
    if not all(path.is_file() for path in files):
        raise FileNotFoundError("ToolBox did not produce the required daily/map/factor files")
    with zipfile.ZipFile(files[0]) as archive:
        names = archive.namelist()
        if names != ["synth.csv"]:
            raise ValueError(f"unexpected daily ZIP members: {names}")
        rows = archive.read("synth.csv").decode("ascii").strip().splitlines()
    if not rows:
        raise ValueError("daily ZIP contains no bars")
    dates = [row.split(",", 1)[0] for row in rows]
    if dates != sorted(dates) or len(dates) != len(set(dates)):
        raise ValueError("daily rows are not strictly unique and increasing")
    for row in rows:
        _, opening, high, low, close, volume = row.split(",")
        if not (int(low) <= int(opening) <= int(high) and int(low) <= int(close) <= int(high)):
            raise ValueError("invalid OHLC range")
        if int(volume) <= 0:
            raise ValueError("non-positive volume")
    return {
        "generator": "LEAN ToolBox RandomDataGenerator",
        "lean_commit": LEAN_COMMIT,
        "seed": SEED,
        "symbol": SYMBOL,
        "bar_count": len(rows),
        "files": {
            str(path.relative_to(data_root)).replace("\\", "/"): sha256(path) for path in files
        },
        "daily_content_sha256": hashlib.sha256("\n".join(rows).encode("ascii")).hexdigest(),
    }


def generate(source_root: Path, runtime: Path, output_root: Path, dotnet: str) -> dict[str, object]:
    require_pinned_source(source_root)
    data_root = output_root / "data"
    if output_root.exists():
        raise FileExistsError("output root must not already exist")
    for relative in (
        "market-hours",
        "symbol-properties",
        "equity/usa/daily",
        "equity/usa/map_files",
        "equity/usa/factor_files",
    ):
        (data_root / relative).mkdir(parents=True, exist_ok=True)
    for relative in (
        "market-hours/market-hours-database.json",
        "symbol-properties/symbol-properties-database.csv",
    ):
        source = source_root / "Data" / relative
        target = data_root / relative
        target.write_bytes(source.read_bytes())
    toolbox = configure_runtime(runtime, data_root)
    command = [
        dotnet,
        str(toolbox),
        "--app=RDG",
        "--tickers=SYNTH",
        "--symbol-count=1",
        "--start=20200102",
        "--end=20200131",
        "--market=usa",
        "--security-type=Equity",
        "--resolution=Daily",
        "--data-density=Dense",
        "--include-coarse=false",
        f"--random-seed={SEED}",
        "--ipo-percentage=0",
        "--rename-percentage=0",
        "--splits-percentage=0",
        "--dividends-percentage=0",
        "--dividend-every-quarter-percentage=0",
        f"--destination-dir={output_root}",
    ]
    subprocess.run(command, cwd=runtime, check=True)
    manifest = validate_fixture(data_root)
    (output_root / "fixture-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--lean-source-root", type=Path, default=os.getenv("LEAN_SOURCE_ROOT"))
    parser.add_argument(
        "--toolbox-runtime", type=Path, default=os.getenv("LEAN_TOOLBOX_RUNTIME_ROOT")
    )
    parser.add_argument("--dotnet", default=os.getenv("LEAN_DOTNET", "dotnet"))
    args = parser.parse_args(argv)
    if args.lean_source_root is None or args.toolbox_runtime is None:
        parser.error(
            "--lean-source-root and --toolbox-runtime (or their environment variables) are required"
        )
    print(
        json.dumps(
            generate(args.lean_source_root, args.toolbox_runtime, args.output_root, args.dotnet),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
