# Health Contract Separation Report

## A. Verdict

`HEALTH_CONTRACT_SEPARATION_V1 = PASS`

本轮完成 health contract 拆分：新增轻量 liveness、轻量 readiness、显式 deep integrity diagnostics；启动脚本改为等待 readiness contract，不再通过 `/api/health` 触发 SQLite deep integrity 检查。

## B. Architecture Change

Before:

```text
/api/health
  liveness
  + readiness
  + deep integrity
    - PRAGMA quick_check
    - foreign_key_check
    - payload hash scan
```

After:

```text
/health/live
  process alive + HTTP responsive only

/health/ready
  database connection/schema readiness
  + job service accepting work
  + scheduler thread alive
  no deep DB traversal

/health/deep and /api/health/deep
  explicit deep integrity diagnostics
  preserves existing quick_check / FK / payload hash validation

/api/health and /api/config/health
  compatibility health using lightweight storage readiness
  advertises health_contract=readiness_compatible
```

The existing `SQLiteStore.health()` deep check is preserved. A new lightweight `SQLiteStore.readiness()` is used by service and job readiness.

## C. Correctness

Confirmed unchanged:

- Daily Primary readiness semantics
- Critical Capture
- Strategy
- PIT
- provenance
- provider behavior
- database schema

No integrity checks were deleted. Deep checks moved out of startup/readiness and remain explicitly callable.

## D. Startup Experience

Startup harness after change:

```text
Start-StockFish.ps1 -Port 8001 -DatabasePath ./stock-fish/data/stockfish.sqlite3 -StateRoot ./stock-fish/data/core
elapsed_seconds: 4.143
result: ready
```

Latency evidence:

| Endpoint | Status | Latency |
|---|---:|---:|
| before `/api/health` | timeout | startup 90s timeout; standalone `PRAGMA quick_check` >100s |
| after `/health/live` | 200 | 54.61 ms |
| after `/health/ready` | 200 | 3.88 ms |
| after `/api/health` compatibility | 200 | 112.12 ms |

The real service was stopped after the startup acceptance probe; no listener remained on port 8001.

## E. Tests

Executed from `stock-fish/`:

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest .\tests\integration\test_health_contract_separation.py -q
# 5 passed

.\.venv\Scripts\python.exe -X utf8 -m pytest `
  .\tests\integration\test_core_api.py::test_health_is_component_specific_and_disk_persistence_visible `
  .\tests\contracts\test_data_security_boundary.py::test_remote_host_and_cross_origin_gates_with_valid_key `
  .\tests\unit\test_durable_storage.py `
  .\tests\unit\test_durable_jobs.py::test_owner_lock_and_write_failure_fail_closed -q
# 8 passed

.\.venv\Scripts\python.exe -X utf8 -m pytest `
  .\tests\r02_product\test_scheduled_outcomes.py::test_started_scheduler_reports_ready_and_running_before_evening_cutoff -q
# 1 passed
```

## F. Remaining Issues

- `/health/deep` is still expected to be slow on the 19.5GB SQLite DB.
- DB growth and why a 19.5GB file contains about 1.09GB document payload remains a separate investigation.
- Live Daily Primary was not rerun in this task; this task stops at service health contract separation.
- `/api/candidate-schedule` remains a richer scheduler state endpoint and is not used as startup readiness gate anymore.
