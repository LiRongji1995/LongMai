# R12 Memory Profile

## 真实完整采集：2026-09-08 17:01—17:13

现有服务真实 capture `capture_4c6199ac6b464389bc1fd5ddd17f2ed5`：peak RSS **923,172,864 bytes**（约 923 MB / 880 MiB），OS peak working set 923,463,680 bytes，端到端耗时 767.454 s。作业 SUCCEEDED、consumer quality=UNAVAILABLE，角色为 ON_DEMAND_NOT_OFFICIAL_PRIMARY。

这是包括 provider 原始结果、normalization 索引、consumer 与持久化的实际全流程。单独实际旧输入迁移 peak RSS 为 130,613,248 bytes，下方合成 artifact benchmark 为 159,555,584 bytes；三者覆盖的工作不同，不能拿 131 MB / 160 MB 代替真实 capture 的 923 MB。remaining raw/index 驻留成本仍存在，未宣称所有 provider ingestion 都是 streaming。

采集预算仍为 max_requests=128、max_seconds=600；实际 98 adapter tasks / 74 HTTP admissions / 179 SDK operations。600 秒控制新采集 admission，后续计算与写入使端到端时间超过该值。最新 capture daily input 为 10,163,247 bytes，history manifest 为 210,254 bytes。该运行没有单独测出同一真实输入的 R11 完整 capture 峰值，不能据此计算同输入前后改善倍数。

真实证据在仓库根目录本机 `reports/codex/2026-09-08/r12_integrity_history/live-capture/capture-report.json`；真实迁移测量在同目录 `real-migration/migration-report.json`。下方保留隔离合成实验，不与真实数据混合。

## 隔离合成 before/after benchmark


TEST_FIXTURE only: 512 instruments × 315 sessions. Fresh subprocesses; RSS sampled every 10 ms and OS peak working set retained.

| Measurement | R11 old path | R12 artifact path |
|---|---:|---:|
| Daily input bytes | 120,061,527 | 2,526,444 |
| Peak RSS bytes | 2,588,327,936 | 159,555,584 |
| Load/consumer seconds | 8.632 | 10.232 |
| Daily incremental bytes | 120,061,527 | 2,906,348 |

New load measurement includes querying every historical row by instrument. Old measurement includes full JSON, Pydantic validation/copy, and full dump/hash. These intentionally describe different operational paths, not a parser microbenchmark. R12 peak includes initial migration (14.273 s). One-session append added 512 rows; old session partitions remain shared. SQLite row/index overhead and WAL are additional to logical payload bytes. Original immutable legacy inputs remain retained and are not reclaimed.

Source receipt references, comparable basis and four clocks remain in each immutable history row. No synthetic record is inserted into the real operational database. MARKET_EDGE=NOT_ESTABLISHED.

## Actual archived capture migration

The separately measured 2026-09-08 real capture contained 321,622,653 bytes, 150,626 bars and 155,226 security rows. Streaming migration produced a 10,018,721-byte daily input and 234,077,094 logical bytes of unique historical rows. Migration took 25.146 seconds with peak RSS 130,613,248 bytes. Original file SHA256: `595666a2e22f3cfa7e15a6137edf8a1ac4e53112890846f5e9e67f10931834bc`.

This migration preserved `PROSPECTIVE_CAPTURED` source coverage and all original clocks; `signal_evaluated=false`. It does not establish a countable signal or complete MA coverage. The old monolithic real-file path was not rerun to reproduce MemoryError; the old peak memory number above belongs exclusively to the synthetic comparison.
