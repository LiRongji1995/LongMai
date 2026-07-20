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


def canonicalize(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a schema-checked semantic representation with stable ordering."""
    required = {"algorithm", "lean_commit", "fixture_sha256"}
    missing = required - payload.keys()
    if missing:
        raise ValueError(f"result is missing required fields: {sorted(missing)}")
    result = dict(payload)
    if "orders" in result:
        orders = result["orders"]
        if not isinstance(orders, list):
            raise ValueError("orders must be a list")
        required_order = {"time", "direction", "quantity", "status", "fillQuantity", "fillPrice"}
        canonical_orders = []
        for index, order in enumerate(orders):
            if not isinstance(order, dict) or required_order - order.keys():
                raise ValueError(f"orders[{index}] is missing a required field")
            canonical_orders.append(
                {
                    "direction": str(order["direction"]).lower(),
                    "fillPrice": float(order["fillPrice"]),
                    "fillQuantity": float(order["fillQuantity"]),
                    "quantity": float(order["quantity"]),
                    "status": str(order["status"]).lower(),
                    "time": float(order["time"]),
                }
            )
        result["orders"] = sorted(
            canonical_orders,
            key=lambda item: (item["time"], item["direction"], item["quantity"]),
        )
    for field in ("initial_cash", "final_holdings", "final_cash", "final_portfolio_value"):
        if field in result:
            result[field] = float(result[field])
    return result


def compare_semantic(expected: dict[str, Any], actual: dict[str, Any]) -> None:
    """Fail with exact JSON paths; only explicitly normalized fields may differ in form."""
    left = canonicalize(expected)
    right = canonicalize(actual)
    if left == right:
        return
    paths = sorted(set(left) | set(right))
    differences = [
        f"$.{key}: expected={left.get(key)!r}, actual={right.get(key)!r}"
        for key in paths
        if left.get(key) != right.get(key)
    ]
    raise ValueError("semantic result mismatch: " + "; ".join(differences))


def normalize(
    orders_path: Path, *, lean_commit: str, fixture_sha: str, algorithm_source_sha: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    orders = load_orders(orders_path)
    normalized_orders = {
        "algorithm": "Phase01DeterministicSmokeTest",
        "lean_commit": lean_commit,
        "fixture_sha256": fixture_sha,
        "algorithm_source_sha256": algorithm_source_sha,
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
    parser.add_argument("--algorithm-source", type=Path, required=True)
    args = parser.parse_args(argv)
    normalized_orders, statistics = normalize(
        args.orders,
        lean_commit=args.lean_commit,
        fixture_sha=args.fixture_sha,
        algorithm_source_sha=sha256(args.algorithm_source),
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
