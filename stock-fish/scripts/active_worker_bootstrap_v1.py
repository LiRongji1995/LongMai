"""Isolated, fixed-root bootstrap for declared active Python children."""
from __future__ import annotations

import argparse
from pathlib import Path
import runpy
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import active_business_gateway_v1 as guard


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--module")
    group.add_argument("--script",type=Path)
    parser.add_argument("arguments",nargs=argparse.REMAINDER)
    args=parser.parse_args(argv)
    remainder=args.arguments[1:] if args.arguments[:1]==["--"] else args.arguments
    guard.install(__file__)
    from scripts.active_runtime_v1 import verify
    manifest=verify(ROOT)
    if args.module:
        relative=args.module.replace(".","/")+".py"
        guard.need(relative in manifest["output_sha256"] and relative.startswith(("scripts/","stockfish_core/")),
                   "ACTIVE_BOOTSTRAP_MODULE_UNDECLARED")
        guard.current("worker")
        sys.argv=[str(ROOT/relative),*remainder]
        runpy.run_module(args.module,run_name="__main__",alter_sys=True)
    else:
        path=args.script.resolve(strict=True)
        guard.need(path.is_relative_to(ROOT) and path.relative_to(ROOT).as_posix() in manifest["output_sha256"],
                   "ACTIVE_BOOTSTRAP_SCRIPT_UNDECLARED")
        sys.argv=[str(path),*remainder]
        runpy.run_path(str(path),run_name="__main__")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
