> 历史归档：保留当时的说明、数字、失败和限制，仅调整文档相对链接。当前使用请看[当前状态](../../../../../CURRENT_STATUS.md)；原路径可在[归档索引](../../README.md)查找。

# R12 Memory Profile

## 2026-09-09 全市场计算内存回归

今早以 4 个有界 adapter tasks 补取 9/8 与 9/4 的日期化目录和日线，最终登记输入为 **42,538,681 bytes、95 个历史会话分区**。09:00 开始的真实 V2 作业约 24 分钟后发生 `MemoryError`；记录 peak RSS **2,424,721,408 bytes**、peak private memory **3,426,811,904 bytes**。下方历史 capture、迁移和合成测量不代表这次策略运行成功。

本次修复复用同一 context 的市场聚合，并将输入与有效依赖改为哈希等价的流式编码，未改交易或覆盖阈值。真实最终输入的单次哈希 tracemalloc 临时分配峰值从 **285,035,334** 降为 **111,743 bytes**；独立进程生命周期 peak RSS 从 **458,297,344** 降为 **404,361,216 bytes**，包含输入解析。tracemalloc 与 RSS 的测量范围不同，不能用单次哈希改善比例推断整个作业的改善。

**修复后这份全市场真实输入已完成计算、落库、官方分页摘要和V2/PV CSV核验，耗时1,414.672秒；全核心984 passed、1 skipped，pytest 227.21秒。** 完成作业的 peak RSS 为 **2,646,999,040 bytes**，高于原失败作业；进程生命周期 peak private memory 为 **3,427,647,488 bytes**，与原峰值接近。单次哈希临时分配下降不等于端到端峰值下降；存储 `canonical_json` 仍有大峰值，完整JSON deepcopy导出路径未验证，未宣称长期稳定或全部内存问题消除。

输入08:58冻结且无9/9行情，资料cutoff09:42:54，结果10:05:09生成、10:06:28.578864完成持久化。V2 `FOCUS/WATCH=0/0`、`REJECTED/UNKNOWN=2851/190`，仍为 `SIGNAL_DATA_UNAVAILABLE`；PV仅284/3041可评估。独立只读均线探针对3,041只的MA20/60/120/250可用数均为0，MA20全市场缺8/12、8/24、8/27、8/28。详细时钟、导出边界及证据见 [R12_MEMORY_FOLLOWUP_20260909](R12_MEMORY_FOLLOWUP_20260909.md)。9/8 原始 primary 不可评价状态保留；新周期正式尝试/可评价信号均为0。

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
