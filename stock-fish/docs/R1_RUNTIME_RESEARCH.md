# R1 离线研究运行预算（2026-10-02）

此次变更只影响显式 `research-r1` 离线研究入口。选股规则、R1 配置、V2 默认入口、调度及生产数据库不变。

原入口在收集输入描述和正式计算时各校验一次完整历史分片；真实大档案在进入选择前消耗大量重复校验时间。现在描述阶段仍解析、验证 MarketInput 并冻结完整输入及外壳哈希，正式计算在同一只读事务中校验一次分片索引及每个实际消费的 payload，随后仍核对输入外壳没有变化。没有跳过正式历史完整性验证。

`--history-seconds` 显式设置每份输入的历史读取时间预算，默认 300 秒，有效范围 `0 < seconds <= 7200`。预算从历史只读事务开始计时，涵盖验证和后续读取，实际值写入结果 `history_read_metrics.time_budget_seconds`。它不是选股参数，不改变输入身份、阈值、2 GiB 读取字节上限和可测时的 1 GiB RSS 上限。NaN、无穷大、零、负数及越界值在读取输入前拒绝。预算超限仍明确失败，不输出合法空名单。

大档案可在有明确预算的研究任务中使用例如 `--history-seconds 1800`；这不保证数据就绪或一定存在候选。真实研究资料仍保存于私有目录，代码发布不携带名单、行情或数据库。补充来源必须由原规范化逻辑验证，并形成新的输入及真实 cutoff；不得把字段人工置为已验证。

验证包括：先复现重复校验和缺少显式预算的失败回归，再验证单次历史校验、预算传递与非法值拒绝；原 R1 只读快照、损坏证据拒绝和 V2 等价性回归继续适用。实际测试命令、结果及真实研究状态记录在交付报告中。

## 已验证窗口的临时缓存

真实全池第一次延长至 1800 秒后仍在 SQLite 读取阶段超时，不能据此宣称零候选。新增回归进一步复现：内存 LRU/L2 淘汰后，同一已验证窗口会再次读库、解析并核对来源。

只在 `BudgetHistory` 管理的离线研究上下文中增加 256 MiB 上限的私有临时磁盘缓存。缓存存放原 `InstrumentBars` 已完成资格、时点、修订选择后的窗口，不替代首次分片及 payload 校验，也不跨输入、cutoff 或运行复用。回读先核对大小与本轮写入时保存的 SHA-256，再解码本进程自己产生的内容；来源档案或外部文件不能充当缓存。超限或损坏明确失败，关闭只读快照时删除临时目录。一般 HistoryArtifactStore 调用继续原路径。

SQLite 页缓存仅在该只读连接设置为 64 MiB，不写 schema、数据或持久化 pragma；内存、源读取字节上限不变。结果记录页缓存预算、临时文件预算/字节、窗口数和磁盘命中数。测试覆盖淘汰重读、不同大小/相同大小内容损坏、容量超限、关闭后拒读、自动清理及原 V2 结果等价。

## 工程验证

修改前，新增预算/重复验证回归为 8 failed；新增缓存回归为 2 failed、8 passed。最终冻结源码批次 **211 passed，退出码 0，285.31 秒**（不累加中间重复批次），`git diff --check` 通过。

```sh
stock-fish/.venv/bin/python -m pytest \
  stock-fish/tests/r05_product/test_research_r1_runtime.py \
  stock-fish/tests/r05_product/test_research_r1_repairs.py \
  stock-fish/tests/r05_product/test_research_r1_g_repairs.py \
  stock-fish/tests/r05_product/test_research_r1.py \
  stock-fish/tests/r05_product/test_stage_aware_momentum_v2_semantic_completion.py \
  stock-fish/tests/r05_product/test_stage_aware_market_cache_r12.py -q
```

未运行全仓测试、原生 Windows 验证或真实收益/账户回测。工程 fixture 与真实研究分别保存，未将 fixture 混入当期研究输入。

真实历史分片输入已实际跑通 `diagnose` 的 R1_FULL 与 V2_CONTROL，退出码 0；成功一轮 CLI 墙钟 1390.64 秒。该时间不是本次从查找、失败复现、修复到报告交付的总耗时。完整清单、冻结输入、运行身份、原始来源、失败尝试和全流程计时保留在私有研究产物，没有随代码发布；不据此认定市场优势或正式生产策略。
