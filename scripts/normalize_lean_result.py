"""Fail-closed normalizer for LEAN result files produced by the smoke test."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_orders(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("order-events JSON must be an array")
    filled = [item for item in payload if item.get("status") == "filled"]
    if len(filled) != 2:
        raise ValueError(f"expected exactly two filled orders, got {len(filled)}")
    return filled


def normalize(
    orders_path: Path, *, lean_commit: str, fixture_sha: str, algorithm_sha: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    orders = load_orders(orders_path)
    normalized_orders = {
        "algorithm": "Phase01DeterministicSmokeTest",
        "lean_commit": lean_commit,
        "fixture_sha256": fixture_sha,
        "algorithm_sha256": algorithm_sha,
        "orders": [
            {
                key: item[key]
                for key in ("time", "direction", "quantity", "status", "fillQuantity", "fillPrice")
            }
            for item in orders
        ],
    }
    holdings = sum(float(item["fillQuantity"]) for item in orders)
    if abs(holdings) > 1e-9:
        raise ValueError("final holdings are not zero")
    cash = 1000.0 - sum(float(item["fillQuantity"]) * float(item["fillPrice"]) for item in orders)
    statistics = {
        "algorithm": "Phase01DeterministicSmokeTest",
        "lean_commit": lean_commit,
        "fixture_sha256": fixture_sha,
        "initial_cash": 1000.0,
        "final_holdings": holdings,
        "final_cash": round(cash, 8),
        "final_portfolio_value": round(cash, 8),
        "fee_model": "ConstantFeeModel(0)",
        "slippage_model": "NullSlippageModel",
        "fill_model": "ImmediateFillModel",
    }
    return normalized_orders, statistics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orders", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--lean-commit", required=True)
    parser.add_argument("--fixture-sha", required=True)
    parser.add_argument("--algorithm", type=Path, required=True)
    args = parser.parse_args(argv)
    normalized_orders, statistics = normalize(
        args.orders,
        lean_commit=args.lean_commit,
        fixture_sha=args.fixture_sha,
        algorithm_sha=sha256(args.algorithm),
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in (
        ("normalized-orders.json", normalized_orders),
        ("normalized-statistics.json", statistics),
    ):
        (args.output_dir / name).write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
