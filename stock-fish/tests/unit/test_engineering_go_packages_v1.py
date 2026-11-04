"""TEST_FIXTURE: whole-package CI contracts; fake Go only, no download or private DB."""
import json
from pathlib import Path
import sys

import pytest

from scripts import engineering_go_packages_v1 as gate


def fixture_inventory():
    return [{"ImportPath": gate.HEAVY["research"], "TestGoFiles": ["research_test.go"]},
            {"ImportPath": gate.HEAVY["core"], "TestGoFiles": ["core_test.go"]},
            {"ImportPath": gate.MODULE + "/infrastructure/sqlitev2", "TestGoFiles": ["cache_test.go"]},
            {"ImportPath": gate.MODULE + "/application"}]


def decode(values):
    return gate.decode_package_inventory("\n".join(json.dumps(value) for value in values))


def fake_go(tmp_path, monkeypatch, mode="success"):
    module = tmp_path / "TEST_FIXTURE_module"
    module.mkdir()
    (module / "go.mod").write_text("module TEST_FIXTURE\n")
    (module / "go.sum").write_text("TEST_FIXTURE\n")
    oracle = tmp_path / "TEST_FIXTURE_oracle"
    oracle.mkdir()
    config = tmp_path / "TEST_FIXTURE_go_config.json"
    config.write_text(json.dumps({"mode": mode, "inventory": fixture_inventory()}))
    calls = tmp_path / "TEST_FIXTURE_calls.jsonl"
    executable = tmp_path / "TEST_FIXTURE_go"
    executable.write_text("#!" + sys.executable + "\n" + '''
import json, os, signal, sys
from pathlib import Path
cfg = json.loads(Path(os.environ["TEST_FIXTURE_GO_CONFIG"]).read_text())
mode = cfg["mode"]
args = sys.argv[1:]
assert os.environ["GOTOOLCHAIN"] == "local"
assert os.environ["GOFLAGS"] == "-mod=readonly"
with open(os.environ["TEST_FIXTURE_GO_CALLS"], "a") as f:
    f.write(json.dumps(args) + "\\n")
if args[0] == "env":
    print(json.dumps({"GOVERSION": "go1.27.1", "GOOS": "darwin", "GOARCH": "amd64", "GOTOOLCHAIN": "local"}))
elif args[0] == "list":
    for value in cfg["inventory"]:
        print(json.dumps(value))
    if mode == "list_fail":
        sys.exit(23)
elif args[0] == "test":
    packages = [arg for arg in args if arg.startswith("github.com/")]
    known = {value["ImportPath"]: bool(value.get("TestGoFiles")) for value in cfg["inventory"]}
    if mode in {"race_fail", "both_fail"}:
        print(json.dumps({"Action": "fail", "Package": packages[0], "Elapsed": 1}))
        sys.exit(17)
    if mode == "race_signal":
        os.kill(os.getpid(), signal.SIGTERM)
    if mode == "unexpected_package":
        print(json.dumps({"Action": "pass", "Package": packages[0] + "/not_selected", "Elapsed": 1}))
    for package in packages:
        print(json.dumps({"Action": "start", "Package": package}))
        if mode == "test_skip":
            print(json.dumps({"Action": "skip", "Package": package, "Test": "TestFixture"}))
        if mode == "missing_terminal":
            continue
        value = {"Action": "pass" if known[package] else "skip", "Package": package, "Elapsed": 1}
        print(json.dumps(value))
        if mode == "duplicate_terminal":
            print(json.dumps(value))
elif args[0] == "vet":
    print("{}", file=sys.stderr)
    if mode in {"vet_fail", "both_fail"}:
        sys.exit(19)
else:
    sys.exit(99)
''')
    executable.chmod(0o700)
    monkeypatch.setenv("TEST_FIXTURE_GO_CONFIG", str(config))
    monkeypatch.setenv("TEST_FIXTURE_GO_CALLS", str(calls))
    monkeypatch.setenv("STOCKFISH_FORMAL_ACCEPTANCE", "1")
    monkeypatch.setenv("STOCKFISH_R1_ORACLE", str(oracle))
    return module, executable, calls, config


def test_partition_exact_union_includes_no_test_packages_and_preserves_test_files():
    inventory = decode(fixture_inventory())
    plans = [gate.make_plan(inventory, group) for group in gate.GROUPS]
    selected = [name for plan in plans for name in plan["selected_packages"]]
    assert len(selected) == len(set(selected)) == len(inventory)
    assert set(selected) == {value["package"] for value in inventory}
    assert gate.MODULE + "/application" in plans[2]["selected_packages"]
    assert plans[0]["all_three_jobs_allocated_limit_seconds"] == 3 * 45 * 60
    assert plans[0]["package_timeout"] == "30m"
    assert plans[2]["package_timeout"] == "10m_GO_DEFAULT"


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "extra", "wrong_heavy"])
def test_partition_refuses_omission_duplicate_unknown_and_displaced_heavy_package(mutation):
    inventory = decode(fixture_inventory())
    partitions = gate.make_plan(inventory, "remaining")["partitions"]
    if mutation == "missing":
        partitions["remaining"].pop()
    elif mutation == "duplicate":
        partitions["remaining"].append(gate.HEAVY["core"])
    elif mutation == "extra":
        partitions["remaining"].append(gate.MODULE + "/not_discovered")
    else:
        partitions["core"] = [gate.HEAVY["research"]]
    with pytest.raises(ValueError):
        gate.validate_partition(inventory, partitions)


@pytest.mark.parametrize("kind", ["missing_heavy", "duplicate", "unsafe", "empty", "error"])
def test_discovery_rejects_invalid_complete_package_inventory(kind):
    values = fixture_inventory()
    if kind == "missing_heavy":
        values.pop(0)
    elif kind == "duplicate":
        values.append(values[0])
    elif kind == "unsafe":
        values[0]["ImportPath"] += ";touch TEST_FIXTURE"
    elif kind == "empty":
        values = []
    elif kind == "error":
        values[0]["Error"] = {"Err": "TEST_FIXTURE_LIST_ERROR"}
    with pytest.raises(ValueError):
        gate.make_plan(decode(values), "remaining")


@pytest.mark.parametrize("group", gate.GROUPS)
def test_actual_fake_process_executes_every_selected_package_once_and_saves_evidence(tmp_path, monkeypatch, group):
    module, executable, calls, _ = fake_go(tmp_path, monkeypatch)
    output = tmp_path / "TEST_FIXTURE_result"
    assert gate.run_group(module, output, group, str(executable)) == 0
    plan = json.loads((output / "plan.json").read_text())
    result = json.loads((output / "execution.json").read_text())
    commands = [json.loads(line) for line in calls.read_text().splitlines()]
    test = next(command for command in commands if command[0] == "test")
    vet = next(command for command in commands if command[0] == "vet")
    assert [name for name in test if name.startswith(gate.MODULE)] == plan["selected_packages"]
    assert [name for name in vet if name.startswith(gate.MODULE)] == plan["selected_packages"]
    assert commands[1] == ["list", "-json", "./..."]
    assert test[:6] == ["test", "-race", "-p", "1", "-json", "-count=1"]
    assert ("-timeout=30m" in test) == (group in gate.HEAVY)
    assert not any(arg.startswith(("-run", "-skip", "-short")) for arg in test)
    assert vet[:4] == ["vet", "-p", "1", "-json"]
    assert result["status"] == "COMPLETE_PACKAGE_PARTITION_RACE_AND_VET_PASSED"
    assert all(record["returncode"] == 0 for record in result["commands"])
    assert (output / "race.stdout.log").read_text()
    assert (output / "vet.stderr.log").read_text() == "{}\n"
    assert len((output / "execution-events.jsonl").read_text().splitlines()) == 8


@pytest.mark.parametrize("mode,code", [("race_fail", 17), ("both_fail", 17), ("race_signal", 143),
                                       ("vet_fail", 19), ("unexpected_package", 2),
                                       ("test_skip", 2), ("missing_terminal", 2),
                                       ("duplicate_terminal", 2)])
def test_failure_skip_and_incomplete_events_cannot_be_upgraded_by_successful_vet(tmp_path, monkeypatch, mode, code):
    module, executable, calls, _ = fake_go(tmp_path, monkeypatch, mode)
    output = tmp_path / "TEST_FIXTURE_result"
    assert gate.run_group(module, output, "remaining", str(executable)) == code
    result = json.loads((output / "execution.json").read_text())
    commands = [json.loads(line) for line in calls.read_text().splitlines()]
    assert [command[0] for command in commands] == ["env", "list", "test", "vet"]
    assert result["status"] == "FAILED" and result["exit_code"] == code
    if mode in {"race_fail", "both_fail"}:
        assert result["commands"][2]["returncode"] == 17
    if mode == "race_signal":
        assert result["commands"][2]["returncode"] == -15


def test_nonzero_go_list_cannot_use_partial_inventory_or_start_tests(tmp_path, monkeypatch):
    module, executable, calls, _ = fake_go(tmp_path, monkeypatch, "list_fail")
    output = tmp_path / "TEST_FIXTURE_result"
    assert gate.run_group(module, output, "research", str(executable)) == 2
    assert [json.loads(line)[0] for line in calls.read_text().splitlines()] == ["env", "list"]
    assert not (output / "plan.json").exists()
    assert json.loads((output / "execution.json").read_text())["commands"][-1]["returncode"] == 23


def test_existing_result_and_unknown_group_never_launch_or_overwrite(tmp_path, monkeypatch):
    module, executable, calls, _ = fake_go(tmp_path, monkeypatch)
    output = tmp_path / "TEST_FIXTURE_result"
    output.mkdir()
    marker = output / "original.txt"
    marker.write_bytes(b"TEST_FIXTURE_ORIGINAL")
    with pytest.raises(FileExistsError):
        gate.run_group(module, output, "core", str(executable))
    with pytest.raises(ValueError, match="GROUP_REJECTED"):
        gate.run_group(module, tmp_path / "TEST_FIXTURE_unused", "unknown", str(executable))
    assert marker.read_bytes() == b"TEST_FIXTURE_ORIGINAL" and not calls.exists()


def test_missing_formal_oracle_refuses_before_creating_output(tmp_path, monkeypatch):
    module, executable, calls, _ = fake_go(tmp_path, monkeypatch)
    monkeypatch.delenv("STOCKFISH_R1_ORACLE")
    output = tmp_path / "TEST_FIXTURE_result"
    with pytest.raises(ValueError, match="ORACLE_REQUIRED"):
        gate.run_group(module, output, "core", str(executable))
    assert not output.exists() and not calls.exists()
