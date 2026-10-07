# StockFish Live 架构

当前是 Phase 1 基础层，未来预测和 Dashboard 不属于已实现范围。审计和阶段门槛见[实施计划](IMPLEMENTATION_PLAN.md)与[集成审计](REPO_INTEGRATION_AUDIT.md)。

```text
私有来源原文 + 实际回执
        -> 显式规范化 MinuteBar
        -> ParquetStore（单写者、不可覆盖、hash 校验）
        -> ReplayProvider（私有完整归档；时钟门控的消费者接口）
LiveMarketProvider 协议 ─────────────┘
        -> Collector（超时/异常恢复、重新验证外部模型）
        -> QualityMonitor（同一交易日的单进程内存状态）
        -> immutable snapshots + 独立 latency

DuckDBStore：独立进程内连接 -> 已校验日分片 -> 固定 PIT SQL -> 可见记录
```

ReplayEngine 只负责按显式交易日历推进时钟、调用同一个 Collector 和保存输出。真实 provider 将在 Phase 6 实现；当前 replay / mock 不含独立的业务预测逻辑。全量归档仅由可信 loader 持有，未来 feature 层只能消费不晚于 cutoff 的受控输出；这是应用合同，不是阻止恶意 Python 代码读取磁盘的安全沙箱。

## 独立边界

独立 Python 3.12 虚拟环境、`STOCKFISH_LIVE_DATA_ROOT`、数据根、写锁、进程和未来服务端口。新包依赖只有 Pydantic、PyArrow、DuckDB；不添加到旧 requirements，不自动启动/停止/切换 Research。没有 Research 数据库连接、券商、订单或仓位模块。

证券 ID、shares/CNY、RAW/可比价格、实际来源时钟、未知与不可覆盖历史的语义来自既有合同。实现保持少量独立纯函数，并用测试约束；没有提前抽 shared package 或搬迁旧业务。

## 存储与恢复

`minute/date=YYYY-MM-DD/part-<hash>.parquet` 保存分钟；`sources/` 保存原始字节、receipt 和校验 manifest。写入采用单写者 OS lock、临时文件 fsync 和禁止覆盖原子发布。Parquet 最后发布作为可见提交点；中断留下的同内容辅助文件可复用，冲突拒绝。读操作不创建目录。

DuckDB 使用 `:memory:` 连接，不打开 recommendation SQLite，不提供任意 SQL 接口。Phase 1 先检查完整日分片，再注册 Arrow 执行参数化 PIT 查询；大数据量下应测量校验、读取、内存和查询开销后再优化。

快照目录必须为新目录，单个快照以独占创建追加。相同输入的快照序列 hash 确定；不同回放输出并存。延迟独立保存，不能改变原始事实 hash。此阶段没有预测表；Phase 3 必须沿用独立 append-only 预测/评价记录，不能复用快照覆盖来模拟预测修订。

## 故障与可观测性

provider timeout、网络/SDK 异常、非法返回、未来数据、父来源缺失、乱序与重复均记录明确码。失败批次不推进 provider cursor、不污染已接受状态；下一次调用可补收此前尚未接受的记录。重复或已接收较晚事件后的迟到旧 bar 会拒绝，历史快照不重写。

每次记录 event cutoff、as_of、input/config/snapshot hash、质量与警告。provider 调用耗时、event->available 延迟、内部处理与持久化耗时分别记录。`provider_event_to_available_ms` 依赖来源时钟，只是数据可用延迟观测，不等于 SDK 网络往返或实时 SLA。

Phase 1 不依赖 GPU、Redis、Kafka、Flink、Spark、Kubernetes 或微服务。当前测试容量不能证明 8 核/32GB 全市场生产能力；后续必须在真实明确范围上记录 P50/P95、RSS、provider latency，并保留正确性检查。
