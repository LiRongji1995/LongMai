"""Offline Phase 0 command-line status entry point."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from quant_system import (
    CURRENT_PHASE,
    LIVE_TRADING_ENABLED,
    PAPER_TRADING_ENABLED,
    PROJECT_NAME,
    __version__,
)


def status_lines() -> tuple[str, ...]:
    """Return deterministic metadata; this function never uses a network or broker."""
    return (
        f"project name: {PROJECT_NAME}",
        f"project version: {__version__}",
        f"current phase: {CURRENT_PHASE}",
        f"live trading enabled: {str(LIVE_TRADING_ENABLED).lower()}",
        f"paper trading enabled: {str(PAPER_TRADING_ENABLED).lower()}",
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the deliberately minimal, offline CLI."""
    parser = argparse.ArgumentParser(prog="quant-system")
    parser.add_argument("command", choices=("status",))
    parser.parse_args(argv)
    print("\n".join(status_lines()))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
