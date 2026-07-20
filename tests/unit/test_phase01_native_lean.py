from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from scripts.generate_phase01_synthetic_data import LEAN_COMMIT, validate_fixture
from scripts.normalize_lean_result import load_orders, normalize


def test_checked_in_toolbox_fixture_is_valid() -> None:
    manifest = validate_fixture(Path("integrations/lean/data"))
    assert manifest["lean_commit"] == LEAN_COMMIT
    assert manifest["seed"] == 11399
    assert manifest["symbol"] == "SYNTH"
    assert manifest["bar_count"] > 0
    checked_in_manifest = json.loads(
        Path("integrations/lean/data/fixture-manifest.json").read_text(encoding="utf-8")
    )
    assert checked_in_manifest["files"] == manifest["files"]
    run_manifest = json.loads(
        Path("integrations/lean/expected/phase01-run-manifest.json").read_text(encoding="utf-8")
    )
    assert run_manifest["run_a"]["filled_orders"] == 2
    assert run_manifest["run_b"]["final_holdings"] == 0.0


def test_fixture_validation_rejects_invalid_ohlc(tmp_path: Path) -> None:
    root = tmp_path / "data"
    for relative in ("equity/usa/daily", "equity/usa/map_files", "equity/usa/factor_files"):
        (root / relative).mkdir(parents=True)
    with zipfile.ZipFile(root / "equity/usa/daily/synth.zip", "w") as archive:
        archive.writestr("synth.csv", "20200102 00:00,10,9,11,10,1\n")
    (root / "equity/usa/map_files/synth.csv").write_text("20200101,SYNTH\n", encoding="ascii")
    (root / "equity/usa/factor_files/synth.csv").write_text("20200101,1,1,1,1\n", encoding="ascii")
    with pytest.raises(ValueError, match="OHLC"):
        validate_fixture(root)


def test_normalizer_uses_valid_order_events_and_rejects_bad_summary(tmp_path: Path) -> None:
    orders_path = tmp_path / "orders.json"
    orders_path.write_text(
        json.dumps(
            [
                {
                    "time": 1,
                    "direction": "buy",
                    "quantity": 10,
                    "status": "filled",
                    "fillQuantity": 10,
                    "fillPrice": 100,
                },
                {
                    "time": 2,
                    "direction": "sell",
                    "quantity": -10,
                    "status": "filled",
                    "fillQuantity": -10,
                    "fillPrice": 101,
                },
            ]
        ),
        encoding="utf-8",
    )
    algorithm = tmp_path / "algorithm.dll"
    algorithm.write_bytes(b"test")
    normalized_orders, statistics = normalize(
        orders_path, lean_commit="a" * 40, fixture_sha="b" * 64, algorithm_sha="c" * 64
    )
    assert len(normalized_orders["orders"]) == 2
    assert statistics["final_holdings"] == 0
    malformed = tmp_path / "malformed-summary.json"
    malformed.write_text('{"Hostname":"bad}', encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        json.loads(malformed.read_text(encoding="utf-8"))


def test_toolchain_lock_is_machine_independent_and_safety_defaults_remain() -> None:
    lock = Path("config/toolchain.lock.json")
    payload = json.loads(lock.read_text(encoding="utf-8"))
    assert payload["lean"]["commit"] == "0269115d3cfbf691c7a0b7cfcc9ed412cafb91f6"
    assert "E:\\" not in lock.read_text(encoding="utf-8")
    env_example = Path(".env.example").read_text(encoding="utf-8")
    assert "LEAN_SOURCE_ROOT=" in env_example
    assert "LIVE_TRADING_ENABLED=false" in env_example
    assert "PAPER_TRADING_ENABLED=false" in env_example


def test_order_events_must_have_exactly_two_fills(tmp_path: Path) -> None:
    path = tmp_path / "orders.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="exactly two"):
        load_orders(path)
