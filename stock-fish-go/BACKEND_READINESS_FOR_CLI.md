# 后台能力交接：缓存指标与验收边界（2026-10-03）

2026-10-04 收尾结论：当前源码全套普通/race、正式 oracle、vet、Python 工具测试和原生/Windows 交叉构建通过。80 证券两种背景 fixture 的冷暖完整明细差分为零，暖命中均实测 422610 字节。旧五个真实 run、源/备份/账本身份和当前 PUBLISHED 状态重新核对通过。

完整规模 A/B 增量仍因合法后续源/50 GiB 容量阻塞。唯一新增真实全池尝试遇到墙钟与单调时钟巨大间隙，监督器终止，保存 CANCELLED，没有进入缓存或新 run 发布。保守计费墙钟 14411.776 秒已超过本轮 5400 秒累计研究额度，未减去暂停或自动续跑。因此当前源码的成功真实全池指标/语义回归仍未完成。精确状态见私有 acceptance.json，不能由工程 fixture 推定真实 PASS。

`elapsed_seconds` 使用 Go 单调时钟；监督账本另记墙钟。环境暂停时两者可出现差异，内层短耗时不替代外层预算。此次任务自身崩溃租约经过 PID、创建时间、路径、完整 owner 字节、终态任务、无活跃进程/文件持有者联合核对后归档；非通用自动清理，旧数据均保留。随后旧 run 的新进程校验和研究包校验通过。

本轮承接 `b728c60`，保持现有 Go/SQLite V2 架构。验收结束时改动留在工作区；2026-10-04 用户后续明确授权将本轮源码、测试和维护文档提交推送到远端 main，未授权生产切换。机器证据位于私有 `reports/codex/stockfish-v2-rebuild/backend-acceptance-20261003/`：`acceptance.json`、`evidence-index.json`、`capacity-plan.json`、`budget.json`。以这些文件的实际命令、退出码、源码和二进制身份为验收依据。上一轮记录保持原样。

| 能力 | 实现和交接边界 |
|---|---|
| 保存结果查询、解释、导出 | 现有 show / summary / selected / detail / export / verify-run 读取原保存结果；不能用重新运行替代旧结果。Python 原结果通过已验证 bundle 保存。解释只能基于保存的条件、事实与未知。 |
| 新库独立消费冻结输入 | NativeEngine、研究编排、V2 history adapter 和 SQLite 缓存已贯通；无需 Python 策略解释器。真实输入原观察日和 cutoff 保持，资料不完整不等于实现缺失。 |
| 现有 Go 导入 | 已有冻结输入注册、旧输入导入、bundle 导入及对应投影/发布合同。不能据此宣称已经实现供应商网络采集或日常 refresh。 |
| 全规模旧库增量 | 现有 Incremental 对完整键/内容集合核对并保留原生应用资料；小型 fixture 有增量、检查点恢复、幂等和发布验证。真实规模本轮受容量阻塞，不能以幂等空跑或小测试代替。 |
| 增量后的生命周期身份 | 未完成真实新 epoch 的 source / verification / active database 控制面联动验收。不得手改 checksum 绕过配置冲突。 |
| Provider / daily refresh | 本轮未开发日常数据 provider、init / picks / daily / explain 产品入口、安装包及 refresh 编排；网络采集也未执行。 |
| 工程入口只读限制 | 当前工程命令打开数据库时可能取得写租约、初始化扩展；cache lookup 也可能更新访问时间或删除坏缓存。这不是承诺无写入的用户只读 CLI。后续任务应提供明确只读模式。 |
| 平台 | 本机原生测试及 Windows 交叉构建单列；Windows 原生运行没有验证。 |

完整增量未通过不自动阻止开发保存结果的只读展示与导出；需要日常同步、迁移或恢复的功能必须单独满足对应门禁。DAILY_V1 仍为冻结主策略；Go 默认主动策略仍为 V2，R1 保持 RESEARCH_ONLY，production_promotion=DISABLED，MARKET_EDGE=NOT_ESTABLISHED。本轮未做投资有效性验证。

## 指标合同

`NewBudget` 在创建时把自身绑定到派生 context，`Ctx()` 和 NativeEngine.Start 返回同一预算链。保留父 context 值、取消、较早 deadline 与进度回调；没有第二个预算或监控 goroutine。源、事实计算、缓存 key 和策略版本没有变化，不必清空或重签旧 run。

`cache_read_bytes` 是实际取出的 payload 逻辑字节，坏 payload 也计入实际读取，未命中不增加。`cache_hits / cache_misses / cache_corrupt / cache_errors` 与占用分别记录；`cache_bytes=null` 表示未测得，不以 0 代替未知。占用指缓存 payload 的逻辑容量，既不是 SQLite 文件大小，也不是物理磁盘 IO。`cache_query / cache_integrity / cache_decode` 为实测耗时，可能小到计时分辨率以内；component 与 stage 存在嵌套，不能相加当总耗时。

保存 summary 中的 metrics 是保存前快照，`final_resource_scope=false`。正式日志最后一条 `final_resource_scope=true` 在资源 scope 关闭时输出，包含保存/复核期间计数与耗时，绑定 run_id、input_id、input_bytes_hash、go_source_hash；须结合 job 终态判断成功。执行尚未生成 run ID 时，失败日志允许没有 run_id。二进制 hash 由外层验收账本绑定，不回写旧结果。

日志写入错误计入 `log_write_errors`，不改变策略结果或把指标作为筛选门槛。若输出端始终故障，则最终日志本身无法送达；需保留调用方错误/伴随日志，不能声称丢失的日志已经保存。现有编码仍累积 `out.Items`，逐股释放和 FreeOSMemory 缓解峰值，不是严格常量内存流式保存。

## 复核命令

路径由调用者显式提供；真实大库操作先读取 capacity-plan，不直接重跑基础迁移 Verify。以下使用现有工具，不建立新日常 CLI：

```sh
STOCKFISH_FORMAL_ACCEPTANCE=1 STOCKFISH_R1_ORACLE="$ORACLE_DIR" "$GO" -C stock-fish-go test -p 1 ./... -count=1 -timeout 30m
STOCKFISH_FORMAL_ACCEPTANCE=1 STOCKFISH_R1_ORACLE="$ORACLE_DIR" "$GO" -C stock-fish-go test -race -p 1 ./... -count=1 -timeout 30m
"$GO" -C stock-fish-go vet ./...
python3 stock-fish-go/tools/build.py --go "$GO" --command stockfish --output "$OUTPUT/stockfish"
"$OUTPUT/stockfish" show --db "$ISOLATED_DB" --id "$SAVED_RUN"
"$OUTPUT/stockfish" verify-run --db "$ISOLATED_DB" --id "$SAVED_RUN"
```

真实研究预算仍默认 300 秒；验收需要时显式 1800 秒。新额度与历史六次账本分开，禁止重复使用旧额度。缓存回归有隔离 TEST_FIXTURE 标记；不能混入日常结果。
