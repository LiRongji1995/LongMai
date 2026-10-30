"""Independent TEST_FIXTURE factor mathematics and fresh-capture boundaries.

No downloaded prices, provider calls, database, saved run or source mutation.
Expected OHLC values are literal hand calculations, cross-checked with Fraction;
the original comparator is the subject, never the expected-value generator.
"""
from copy import deepcopy
from datetime import date, datetime, timezone
from fractions import Fraction
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import codex_factor_capture_v3 as patch
from stockfish_core.yaogu import provider_comparability as original_math
from stockfish_core.yaogu.premarket_capture_cache_v1 import factor_contract
from stockfish_core.yaogu.provider_normalization import normalize_capture_with_report


ROOT = Path(__file__).resolve().parents[2]
CODE, NEIGHBOR = "sh.600001", "sz.000001"
FIELDS = ("code", "dividOperateDate", "backAdjustFactor", "foreAdjustFactor", "adjustFactor")
PARAMS = {"codes": [CODE, NEIGHBOR], "start_date": "1990-01-01", "end_date": "2026-09-10"}
PRICE_FIELDS = ("open", "high", "low", "close")
RAW_GOLDEN = [
    ("2026-09-02", ("10", "12", "8", "10")),
    ("2026-09-03", ("5", "6", "4", "5")),
    ("2026-09-07", ("4", "4.8", "3.2", "4")),
    ("2026-09-08", ("4.4", "5.2", "3.6", "4.8")),
]
ADJUSTED_GOLDEN = [
    ("10", "12", "8", "10"), ("10", "12", "8", "10"),
    ("10", "12", "8", "10"), ("11", "13", "9", "12"),
]


def factors(code=CODE):
    # Synthetic original listing anchor, 2:1 split, then a 1-unit cash dividend
    # from a 5-unit raw close. These encode the frozen price-reference convention,
    # not an account total-return or an assertion about any real security.
    return [dict(zip(FIELDS, (code, day, back, "1", adjustment)))
            for day, back, adjustment in (("2000-01-04", "1", "1"),
                ("2026-09-03", "2", "2"), ("2026-09-07", "2.5", "1.25"))]


def raw_rows():
    return [(date.fromisoformat(day), (dict(zip(PRICE_FIELDS, values)), "TEST_FIXTURE_RAW"))
            for day, values in RAW_GOLDEN]


def independent_values(rows, events):
    """Small Fraction oracle; no production parser, lookup, digest or selector."""
    result = {}
    for day, (prices, _) in rows:
        previous = [(event["dividOperateDate"], Fraction(event["backAdjustFactor"]))
                    for event in events if event["dividOperateDate"] <= day.isoformat()]
        if previous:
            multiplier = max(previous, key=lambda item: item[0])[1]
            result[day] = tuple(Fraction(prices[name]) * multiplier for name in PRICE_FIELDS)
    return result


class StopSDK(BaseException):
    """Process loss is not an SDK Exception that a per-code loop can swallow."""


class Cursor:
    fields = FIELDS

    def __init__(self, rows, fail_after=None):
        self.rows, self.fail_after = deepcopy(rows), fail_after
        self.position, self.error_code = -1, "0"

    def next(self):
        self.position += 1
        if self.fail_after is not None and self.position == self.fail_after:
            self.error_code = "10002007"
            return False
        return self.position < len(self.rows)

    def get_row_data(self):
        return [self.rows[self.position][key] for key in FIELDS]


class SDK:
    __version__ = "TEST_FIXTURE_INDEPENDENT_REFERENCE"

    def __init__(self, rows=None, *, stop=None, cursor_failures=None):
        self.rows = rows if rows is not None else {code: factors(code) for code in PARAMS["codes"]}
        self.stop, self.cursor_failures, self.calls = stop, cursor_failures or {}, []

    def login(self):
        self.calls.append("login")
        return SimpleNamespace(error_code="0")

    def query_adjust_factor(self, *, code, start_date, end_date):
        self.calls.append(code)
        assert start_date == "1990-01-01" and end_date == PARAMS["end_date"]
        if code == self.stop:
            raise StopSDK("TEST_FIXTURE_PROCESS_TERMINATED")
        return Cursor(self.rows.get(code, []), self.cursor_failures.get(code))

    def logout(self):
        self.calls.append("logout")
        return SimpleNamespace(error_code="0")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def isolated_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("TEST_FIXTURE_NETWORK_AND_DATABASE_FORBIDDEN")
    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, forbidden)
    for name in ("create_connection", "getaddrinfo"):
        monkeypatch.setattr(socket, name, forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)


@pytest.fixture
def capture(tmp_path, monkeypatch):
    names = ("provider_requests.py", "provider_requests_worker.py", "provider_budget.py")
    source = ROOT / "stockfish_core/yaogu"
    originals = {name: (source / name).read_bytes() for name in names}
    comparator_before = Path(original_math.__file__).read_bytes()
    runtime = tmp_path / "TEST_FIXTURE_runtime/stockfish_core/yaogu"
    runtime.mkdir(parents=True)
    (runtime / names[0]).write_bytes(patch.patch_parent(originals[names[0]])[0])
    (runtime / names[1]).write_bytes(patch.patch_worker(originals[names[1]])[0])
    (runtime / names[2]).write_bytes(originals[names[2]])
    worker = load("stockfish_core.yaogu.TEST_FIXTURE_reference_worker", runtime / names[1])
    parent = load("stockfish_core.yaogu.TEST_FIXTURE_reference_parent", runtime / names[0])
    admissions = []
    def reserve(kind):
        admissions.append(kind)
        worker._attempt_counts[kind] += 1
    monkeypatch.setattr(worker, "reserve_attempt", reserve)
    count = 0

    def execute(sdk, params=None):
        nonlocal count
        count += 1
        selected = deepcopy(PARAMS if params is None else params)
        output = tmp_path / ("TEST_FIXTURE_capture_" + str(count))
        monkeypatch.setitem(sys.modules, "baostock", sdk)
        admissions.clear()
        worker._attempt_counts.update(HTTP=0, SDK=0)
        def run(argv, *, input, **kwargs):
            request = json.loads(input)
            assert argv[-1] == "stockfish_core.yaogu.provider_requests_worker"
            assert Path(kwargs["cwd"]) == runtime.parents[1]
            worker._transport.clear()
            worker._transport.update(request)
            try:
                result = worker._baostock_session_request(request["endpoint"],
                    request["parameters"], request["partial_path"])
            except StopSDK:
                raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
            result.setdefault("first_seen_at", datetime.now(timezone.utc).isoformat())
            result.setdefault("coverage", {}).update(http_attempts=0, sdk_operations=len(admissions))
            result["http_responses"] = []
            return SimpleNamespace(returncode=0, stdout=json.dumps(result))
        monkeypatch.setattr(parent.subprocess, "run", run)
        result = parent.execute_request("adjustment_factors", selected, output, timeout=3)
        receipt = next(r for r in result["receipts"] if r["receipt_id"] == result["request_receipt_id"])
        payload = json.loads((output / "raw" / (receipt["content_hash"] + ".raw.json")).read_bytes())
        assert hashlib.sha256((output / "raw" / (receipt["content_hash"] + ".raw.json")).read_bytes()).hexdigest() == receipt["content_hash"]
        return result, payload, receipt

    execute.validate = parent._factor_v3_validate
    yield execute
    assert Path(original_math.__file__).read_bytes() == comparator_before
    assert all((source / name).read_bytes() == raw for name, raw in originals.items())
    assert not list(tmp_path.rglob("*.sqlite*"))


def test_full_capture_matches_independent_split_dividend_and_listing_anchor_golden(capture):
    result, _, _ = capture(SDK())
    assert result["status"] == "COMPLETE" and result["pagination_complete"] is True
    events = [row for row in result["rows"] if row["code"] == CODE]
    expected = {date.fromisoformat(day): tuple(Fraction(v) for v in values)
                for (day, _), values in zip(RAW_GOLDEN, ADJUSTED_GOLDEN)}
    assert independent_values(raw_rows(), events) == expected
    values, status = original_math.comparable_rows("600001.SH", raw_rows(), {},
        [(events, "TEST_FIXTURE_FACTOR", PARAMS["end_date"], PARAMS["start_date"])])
    assert status == "VERIFIED_PROVIDER_FACTOR_DEFINITION"
    assert {day: tuple(Fraction(str(row[0]["comparable_" + name])) for name in PRICE_FIELDS)
            for day, row in values.items()} == expected
    assert len({row[0]["comparable_basis"] for row in values.values()}) == 1


def test_future_action_changes_no_earlier_price_or_anchor_basis(capture):
    rows = {code: factors(code) for code in PARAMS["codes"]}
    rows[CODE].append(dict(zip(FIELDS, (CODE, "2026-09-09", "100", "1", "40"))))
    result, _, _ = capture(SDK(rows))
    events = [row for row in result["rows"] if row["code"] == CODE]
    old, _ = original_math.comparable_rows("600001.SH", raw_rows(), {},
        [(factors(), "TEST_FIXTURE_OLD", "2026-09-08", "1990-01-01")])
    new, _ = original_math.comparable_rows("600001.SH", raw_rows(), {},
        [(events, "TEST_FIXTURE_NEW", PARAMS["end_date"], "1990-01-01")])
    assert {day: row[0] for day, row in old.items()} == {day: row[0] for day, row in new.items()}
    assert independent_values(raw_rows(), events) == independent_values(raw_rows(), factors())


@pytest.mark.parametrize("revision", ["value_changed", "old_deleted", "historical_inserted"])
def test_fresh_complete_capture_does_not_authorize_mixing_historical_revisions(capture, revision):
    old_result, _, _ = capture(SDK())
    rows = {code: factors(code) for code in PARAMS["codes"]}
    if revision == "value_changed":
        rows[CODE][0]["backAdjustFactor"] = "1.1"
    elif revision == "old_deleted":
        del rows[CODE][1]
    else:
        rows[CODE].append(dict(zip(FIELDS, (CODE, "2025-03-04", "1.25", "1", "1.25"))))
    new_result, _, _ = capture(SDK(rows))
    assert old_result["status"] == new_result["status"] == "COMPLETE"
    captures = [([row for row in result["rows"] if row["code"] == CODE],
                 result["request_receipt_id"], PARAMS["end_date"], "1990-01-01")
                for result in (old_result, new_result)]
    converted, status = original_math.comparable_rows("600001.SH", raw_rows(), {}, captures)
    assert converted == {} and status == "FACTOR_REVISION_CONFLICT_REQUIRES_REVIEW"


def test_tail_request_is_rejected_before_sdk_not_promoted_to_full_capture(capture):
    sdk = SDK()
    with pytest.raises(ValueError, match="FACTOR"):
        capture(sdk, {**PARAMS, "start_date": "2026-09-01"})
    assert sdk.calls == []


def test_complete_empty_query_does_not_invent_factor_one(capture):
    result, _, _ = capture(SDK(rows={}))
    assert result["status"] == "COMPLETE" and result["rows"] == []
    assert factor_contract(result)["independent_complete_codes"] == PARAMS["codes"]
    values, status = original_math.comparable_rows("600001.SH", raw_rows(), {},
        [([], "TEST_FIXTURE_COMPLETE_EMPTY", PARAMS["end_date"], "1990-01-01")])
    assert values == {} and status == "NO_COMPARABLE_SOURCE"


def test_empty_completed_neighbor_survives_timeout_without_upgrading_parent(capture):
    result, payload, _ = capture(SDK(rows={}, stop=NEIGHBOR))
    assert result["status"] == "PARTIAL" and result["pagination_complete"] is False
    assert result["rows"] == [] and payload["rows"] == []
    statuses = result["coverage"]["security_statuses"]
    assert [row["status"] for row in statuses] == ["COMPLETE", "UNKNOWN"]
    assert statuses[0]["row_count"] == 0 and "captured_at" in statuses[0]
    assert "captured_at" not in statuses[1]
    assert factor_contract(result)["independent_complete_codes"] == [CODE]


@pytest.mark.parametrize("fail_after", [1, 3])
def test_cursor_error_discards_whole_failed_security_and_keeps_complete_neighbor(capture, fail_after):
    result, _, _ = capture(SDK(cursor_failures={NEIGHBOR: fail_after}))
    assert result["status"] == "PARTIAL" and result["pagination_complete"] is False
    assert result["rows"] == factors(CODE)
    statuses = {row["code"]: row for row in result["coverage"]["security_statuses"]}
    assert statuses[CODE]["status"] == "COMPLETE"
    assert statuses[NEIGHBOR]["status"] == "FETCH_FAILED"
    assert statuses[NEIGHBOR]["cursor_exhausted"] is False
    assert factor_contract(result)["independent_complete_codes"] == [CODE]


def test_duplicate_effective_date_cannot_hide_behind_equal_factor_values(capture):
    rows = {CODE: factors(CODE), NEIGHBOR: factors(NEIGHBOR)}
    rows[NEIGHBOR].append(deepcopy(rows[NEIGHBOR][0]))
    result, _, _ = capture(SDK(rows))
    assert result["status"] == "PARTIAL" and result["rows"] == factors(CODE)
    assert factor_contract(result)["independent_complete_codes"] == [CODE]


@pytest.mark.parametrize("scope, field", [
    ("security", "error_code"), ("security", "failure_category"),
    ("parent", "gaps"), ("parent", "error_code"), ("parent", "failure_category"),
])
def test_complete_label_with_failure_evidence_is_rejected(capture, scope, field):
    _, payload, _ = capture(SDK())
    snapshot = {**payload, "http_responses": []}
    assert capture.validate(snapshot, snapshot["checkpoint_binding"]) is snapshot
    invalid = deepcopy(snapshot)
    target = invalid if scope == "parent" else invalid["coverage"]["security_statuses"][0]
    target[field] = ["TEST_FIXTURE_GAP"] if field == "gaps" else "TEST_FIXTURE_FAILURE"
    with pytest.raises(ValueError, match="FACTOR"):
        capture.validate(invalid, invalid["checkpoint_binding"])


def test_missing_left_factor_keeps_pre_anchor_prices_unknown(capture):
    rows = {code: factors(code)[1:] for code in PARAMS["codes"]}
    result, _, _ = capture(SDK(rows))
    events = [row for row in result["rows"] if row["code"] == CODE]
    values, status = original_math.comparable_rows("600001.SH", raw_rows(), {},
        [(events, "TEST_FIXTURE_MISSING_ANCHOR", PARAMS["end_date"], "1990-01-01")])
    assert date(2026, 9, 2) not in values
    assert len(values) == 3 and status == "FACTOR_LEFT_BOUNDARY_OR_RAW_GAP"


@pytest.mark.parametrize("coverage_type", ["TEST_FIXTURE", "RECONSTRUCTED_HISTORICAL"])
def test_actual_late_capture_cannot_be_relabelled_historical_point_in_time(capture, coverage_type):
    before = datetime.now(timezone.utc)
    result, payload, receipt = capture(SDK())
    completed = datetime.fromisoformat(result["completed_at"])
    seen, ingested = (datetime.fromisoformat(receipt[key]) for key in ("first_seen_at", "ingested_at"))
    assert before <= datetime.fromisoformat(result["started_at"]) <= seen <= ingested <= completed
    assert payload["first_seen_at"] == receipt["first_seen_at"]
    for status in result["coverage"]["security_statuses"]:
        assert datetime.fromisoformat(result["started_at"]) <= datetime.fromisoformat(status["captured_at"]) <= seen
    assert all("historical_time_proof" not in value for value in (result, payload, receipt))
    prior_cutoff = datetime.fromisoformat("2026-09-10T20:30:00+08:00")
    assert seen > prior_cutoff
    with pytest.raises(ValueError, match="CUTOFF_PRECEDES_ACTUAL_CAPTURE_COMPLETION"):
        normalize_capture_with_report([result], PARAMS["end_date"], prior_cutoff,
            [date.fromisoformat(PARAMS["end_date"])], coverage_type=coverage_type)


def test_owned_real_worker_timeout_keeps_checkpoint_and_is_reaped(tmp_path):
    """Real child/timeout/kill/reap; only the provider transport is a fixture."""
    runtime = tmp_path / "TEST_FIXTURE_real_runtime"
    source = ROOT / "stockfish_core"
    originals = {name: (source / "yaogu" / name).read_bytes() for name in
                 ("provider_requests.py", "provider_requests_worker.py", "provider_budget.py")}
    shutil.copytree(source, runtime / "stockfish_core", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    producers = runtime / "stockfish_core/yaogu"
    (producers / "provider_requests.py").write_bytes(patch.patch_parent(originals["provider_requests.py"])[0])
    (producers / "provider_requests_worker.py").write_bytes(patch.patch_worker(originals["provider_requests_worker.py"])[0])
    fake = tmp_path / "TEST_FIXTURE_fake_transport"
    fake.mkdir()
    private_tmp = tmp_path / "TEST_FIXTURE_process_tmp"
    private_tmp.mkdir()
    marker = tmp_path / "TEST_FIXTURE_blocked_worker.json"
    violations = tmp_path / "TEST_FIXTURE_forbidden_io.jsonl"
    guards = tmp_path / "TEST_FIXTURE_guarded_pids.jsonl"
    guard_source = r'''
import builtins, json, os, socket, sqlite3
from pathlib import Path
if os.environ.get('STOCKFISH_TEST_FIXTURE_FACTOR_V3') != 'OWNED_TIMEOUT_SMOKE':
    raise RuntimeError('TEST_FIXTURE_ENVIRONMENT_REQUIRED')
def denied(*args, **kwargs):
    with Path(os.environ['STOCKFISH_TEST_FIXTURE_VIOLATIONS']).open('a') as stream:
        stream.write(json.dumps({'pid':os.getpid(),'forbidden':'NETWORK_OR_DATABASE'})+'\n')
    raise AssertionError('TEST_FIXTURE_NETWORK_AND_DATABASE_FORBIDDEN')
socket.socket.connect = socket.socket.connect_ex = denied
socket.create_connection = socket.getaddrinfo = denied
sqlite3.connect = denied
import requests
requests.sessions.Session.request = denied
builtins.TEST_FIXTURE_NETWORK_AND_DB_DENIED = True
with Path(os.environ['STOCKFISH_TEST_FIXTURE_GUARDS']).open('a') as stream:
    stream.write(json.dumps({'pid':os.getpid(),'guard':'NETWORK_AND_DB_DENIED'})+'\n')
'''
    (fake / "sitecustomize.py").write_text(guard_source)
    (fake / "baostock.py").write_text(r'''
import builtins, json, os, time
from pathlib import Path
from types import SimpleNamespace
if os.environ.get('STOCKFISH_TEST_FIXTURE_FACTOR_V3') != 'OWNED_TIMEOUT_SMOKE' or not getattr(
        builtins, 'TEST_FIXTURE_NETWORK_AND_DB_DENIED', False):
    raise RuntimeError('TEST_FIXTURE_GUARDED_FAKE_SDK_REQUIRED')
__version__ = 'TEST_FIXTURE_OWNED_PROCESS'
class Cursor:
    error_code = '0'
    fields = ['code','dividOperateDate','foreAdjustFactor','backAdjustFactor','adjustFactor']
    def __init__(self): self.remaining = 1
    def next(self):
        if self.remaining:
            self.remaining -= 1
            return True
        return False
    def get_row_data(self): return ['sh.600001','2000-01-04','1','1','1']
def login(): return SimpleNamespace(error_code='0')
def logout(): return SimpleNamespace(error_code='0')
def query_adjust_factor(*, code, start_date, end_date):
    assert start_date == '1990-01-01' and end_date == '2026-09-10'
    if code == 'sh.600001': return Cursor()
    assert code == 'sz.000001'
    Path(os.environ['STOCKFISH_TEST_FIXTURE_WORKER_MARKER']).write_text(json.dumps(
        {'pid':os.getpid(),'parent_pid':os.getppid(),'phase':'SECOND_CODE_BLOCKED',
         'fake_sdk':str(Path(__file__).resolve()),'network_forbidden':True}))
    time.sleep(30)
    raise AssertionError('TEST_FIXTURE_WORKER_TIMEOUT_DID_NOT_KILL_CHILD')
''')
    output = tmp_path / "TEST_FIXTURE_real_archive"
    code = r'''
from pathlib import Path
import json, os, sys
sys.path.insert(0, sys.argv[1])
exec(compile((Path(sys.argv[2])/'sitecustomize.py').read_bytes(), 'TEST_FIXTURE_parent_io_guard', 'exec'))
from stockfish_core.yaogu.provider_requests import execute_request
result = execute_request('adjustment_factors', json.loads(sys.argv[4]), sys.argv[3], timeout=3)
print(json.dumps({'parent_pid':os.getpid(),'result':result}, allow_nan=False))
'''
    env = dict(os.environ, PYTHONPATH=str(fake), PYTHONDONTWRITEBYTECODE="1", TMPDIR=str(private_tmp),
        STOCKFISH_TEST_FIXTURE_FACTOR_V3="OWNED_TIMEOUT_SMOKE",
        STOCKFISH_TEST_FIXTURE_WORKER_MARKER=str(marker),
        STOCKFISH_TEST_FIXTURE_VIOLATIONS=str(violations), STOCKFISH_TEST_FIXTURE_GUARDS=str(guards))
    process = subprocess.Popen([sys.executable, "-I", "-B", "-c", code, str(runtime), str(fake),
        str(output), json.dumps(PARAMS)], cwd=runtime, env=env, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=15)
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate(timeout=5)
        raise
    assert process.returncode == 0, stderr
    message = json.loads(stdout)
    worker = json.loads(marker.read_bytes())
    assert worker["parent_pid"] == message["parent_pid"] == process.pid
    assert worker["fake_sdk"] == str(fake / "baostock.py")
    try:
        os.kill(worker["pid"], 0)
    except ProcessLookupError:
        pass
    else:
        # Only this invocation's dedicated group can be cleaned on a failure.
        if os.getpgid(worker["pid"]) == process.pid:
            os.killpg(process.pid, signal.SIGKILL)
        pytest.fail("TEST_FIXTURE_OWNED_WORKER_NOT_REAPED")
    result = message["result"]
    assert result["status"] == "PARTIAL" and result["pagination_complete"] is False
    assert result["error_code"] == "REQUEST_TIMEOUT_AFTER_PARTIAL"
    assert result["coverage"]["requested_codes"] == PARAMS["codes"]
    statuses = result["coverage"]["security_statuses"]
    assert [item["status"] for item in statuses] == ["COMPLETE", "UNKNOWN"]
    assert "captured_at" not in statuses[1]
    assert result["rows"] == [dict(zip(("code", "dividOperateDate", "foreAdjustFactor",
        "backAdjustFactor", "adjustFactor"), (CODE, "2000-01-04", "1", "1", "1")))]
    receipt = next(item for item in result["receipts"] if item["receipt_id"] == result["request_receipt_id"])
    payload = json.loads((output / "raw" / (receipt["content_hash"] + ".raw.json")).read_bytes())
    checkpoint = Path(result["diagnostics"]["checkpoint_reference"]["path"])
    assert checkpoint.is_relative_to(tmp_path)
    saved = json.loads(checkpoint.read_bytes())
    declared_hash = saved.pop("checkpoint_hash")
    assert hashlib.sha256(json.dumps(saved, sort_keys=True, ensure_ascii=True,
        allow_nan=False).encode()).hexdigest() == declared_hash
    assert saved["first_seen_at"] == payload["first_seen_at"] == receipt["first_seen_at"]
    assert saved["coverage"]["security_statuses"] == statuses
    assert datetime.fromisoformat(result["started_at"]) <= datetime.fromisoformat(statuses[0]["captured_at"]) <= datetime.fromisoformat(receipt["first_seen_at"]) <= datetime.fromisoformat(receipt["ingested_at"]) <= datetime.fromisoformat(result["completed_at"])
    assert not list(checkpoint.parent.glob("*.tmp"))
    assert not violations.exists()
    guarded = {json.loads(line)["pid"] for line in guards.read_text().splitlines()}
    assert {process.pid, worker["pid"]} <= guarded
    assert all((source / "yaogu" / name).read_bytes() == raw for name, raw in originals.items())
    assert not list(tmp_path.rglob("*.sqlite*")) and not list(runtime.rglob("__pycache__"))
