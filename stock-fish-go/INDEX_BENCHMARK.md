# 可复现的隔离索引证据

`infrastructure/sqlitev2/index_benchmark_test.go` 提供显式 `BenchmarkControlledIndexEvidence`。普通 `go test` 不运行该基准。基准只用 `testing.TempDir` 创建三个独立数据库；环境变量只指定以 O_EXCL 新建的私有报告，不能传入数据库路径。没有修改正式 schema 或真实目标索引。

在 `stock-fish-go/` 中执行：

```sh
STOCKFISH_INDEX_BENCHMARK_REPORT="$PWD/../reports/codex/stockfish-v2-rebuild/private/index-benchmark-test-fixture.json" \
  go test ./infrastructure/sqlitev2 -run '^$' \
  -bench '^BenchmarkControlledIndexEvidence$' -benchtime=1x -count=1 -timeout=4m
```

已有报告时拒绝覆盖；新一次运行选择新的报告文件名。需要项目支持的 Go 工具链。该基准不作为普通测试或正式策略 oracle 的替代品。

样本是明确标记 TEST_FIXTURE 的 300 个证券标识 × 100 个合成日期、20000 条保存结果形状的摘要和明细。数据库不发布，run 保持 PREPARING；不执行策略，不混入真实池。比较无对应查询索引、当前实际索引、只在 fixture 添加的覆盖索引三个配置。

每个配置运行相同 SQL 和参数，保存真实 `EXPLAIN QUERY PLAN`、完整 `EXPLAIN` VM、每次查询的结果行数与 SHA256。证券窗口、日期截面及摘要筛选各先预热一次，再轮转配置顺序测量15次完整读取。耗时包含 SQL 准备/读取/Scan/关闭，不包含结果序列化和哈希。索引空间来自 SQLite dbstat；插入计时包括同一批生成好的数据和一次 FULL/WAL 持久事务提交。所有实例保持相同16MiB SQLite cache。

数据库/OS缓存没有强制清空，机器未独占，因此不是冷启动或生产延迟结论。写入每个配置只有一次批量事务，比例仅为本次观察，不能宣称统计显著。15次读取的分位数用最近样本 `round(p*(n-1))`，也不构成生产p95承诺。

当前三条索引对这些 SELECT 列均非完整覆盖。是否覆盖必须看实际计划；实验中即使创建包含所需列的索引，优化器也可能选择其他索引。报告保留全部候选索引的空间成本，包括未被选用的索引，不因未选用而隐藏写入/存储代价。不得根据这组fixture结果自动添加生产索引。
