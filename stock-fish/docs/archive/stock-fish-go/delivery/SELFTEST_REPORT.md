> 历史归档：保留当时的说明、数字、失败和限制，仅调整文档相对链接。当前使用请看[当前状态](../../../../../CURRENT_STATUS.md)；原路径可在[归档索引](../../README.md)查找。

# SELFTEST_REPORT — 2026-10-04 原生终端候选包

业务源码 SHA-256：`e31cf80666b9bf1d900da17e116a0422ec451c1d00c0b7635962b99c3849bdab`。二进制 SHA-256：`2eeeed57c0b8cfffca3af86e363325854d178e0bbd9d951c2a0a6e5d07703543`。平台 darwin/amd64，Go 1.27.1，SQLite engine 3.53.4 / modernc 1.60.1。基线 Git `6205848325b0618e8c106ec19660723efb2694fc`，验收时仅本地修改，用户随后明确授权将源码、测试与说明提交推送；生产仍未激活。详细私有命令、数据库路径和证据在操作者交接目录，不随包分发。

| 验收项 | 状态 | 实测范围与限制 |
|---|---|---|
| LOG_BACKPRESSURE_AND_CANCELLATION | PASS | 真实 io.Pipe 堵塞/恢复、永久阻塞、短写/错误；包外 OS stderr 堵塞超时、Ctrl-C 和关闭 stdout 消费者，进程及写租约收尾。 |
| CACHE_METRICS_AND_SAMPLING_COST | PASS | SQLite 6 次 lookup 的占用 SUM 为 0，原计划 SCAN app_fact_cache；Native 冷暖/清缓存一致。真实暖跑 3040 hits、16853626 payload 字节、起止 2 samples，采样 2.374626549 秒。 |
| CLOCK_AND_SUSPEND_BOUNDARY | PASS_TESTED_BOUNDARIES | 前跳、回拨、wait 超时、父 context 取消及发布前拒绝测试；十秒环境检查和真实执行一致。旧 14411.776 秒保留，原因 UNKNOWN；整机休眠期间不承诺准点终止。 |
| READ_ONLY_PICKS_EXPLAIN_EXPORT | PASS | 真实保存结果/解释/CSV/JSON/重启校验，原 run 和 jobs 保持；实际研究运行期间并发查询成功。mode=ro 一致事务完整性校验，无写租约/缓存修改。 |
| CLI_CONFIGURATION_AND_DATES | PASS | init 不覆盖/非TTY、配置优先级/相对锚定、中文空格路径、有限官方日历/盘中阻塞、策略隔离、全候选/显式截断、多格式和 server 不串库/不回退。 |
| DAILY_LOCAL_WORKFLOW | PASS_LOCAL_FROZEN_INPUT | 包外 TEST_FIXTURE 真 daily/重复请求；真实最新源码全池完成后 dry-run、daily 和重启复用同一真实 run，零新增作业。不是新在线资料研究。 |
| ONLINE_REFRESH | BLOCKED_EXISTING_SDK_DEPENDENCY | 唯一实际 refresh 批次 exit 1；现有 Python 3.12 pydantic 缺 _pydantic_core 编译扩展，在网络前失败。HTTP 0，1.300180912 秒，无 frozen/new input/job/run；回执保留。模块 find_spec 检查不是完整依赖可执行性证明。未重试、未改环境。 |
| PACKAGED_BINARY_NO_LLM | PASS_NATIVE_LOCAL_PATH | darwin/amd64 包外 23 项命令；空 PATH、禁止全部网络及其他 exec，真实查询与全池可运行，未观察到 AI 子进程/模型网络。在线模式只允许 Go 与固定 SDK Python，实际仅运行到失败导入。 |
| CURRENT_BUILD_REAL_SCALE | PASS_SAME_INPUT_HISTORICAL | 3040 证券同输入 R1，832.178990126 秒，RSS peak 953102336 字节；3040 原始明细 hash 和全部字段一致，6 CORE_PASS/41 CORE_UNKNOWN/2993 CORE_FAIL。manifest 新源码 hash 和覆盖标记差异原样保留。资料仍 INCOMPLETE。 |
| FULL_SCALE_LEGACY_INCREMENTAL | BLOCKED_EXISTING_SOURCE_AND_CAPACITY | 缺合法后续 SourceS1；上一轮完整增量保守峰值 59767729718 字节超过 50 GiB，本轮未增加迁移/复制/增量预算，未重跑准入计划。 |
| MANUAL_ACCEPTANCE | NOT_RUN_BY_USER | 约六步说明已提供；自动测试不代替用户人工确认。 |
| PRODUCTION_ACTIVATION | NOT_AUTHORIZED | 仅本地工作区和隔离实例验收；后续源码提交推送获用户明确授权，仍未部署、未切换生产或创建定时任务。 |

## 工程检查与实际执行

下列完整 Go 检查绑定上述已验收候选源码；原可执行包和验收证据保持。发布前仅格式化集成测试，发布源码身份为 `0873c035ff05b586a5104628aafc95c7102a83517d634e7267d905799e818bb3`，运行代码与策略没有变化；发布定向复核另列，不将候选包重签为新身份。`STOCKFISH_FORMAL_ACCEPTANCE=1` 与独立 `STOCKFISH_R1_ORACLE` 配合。

| 命令 | 退出码 | 秒 |
|---|---:|---:|
| `go test -p 1 ./... -count=1 -timeout 30m` | 0 | 88.457 |
| `go vet ./...` | 0 | 2.838 |
| `GOOS=windows GOARCH=amd64 python3 tools/build.py --command stockfish ...` | 0 | 6.443 |
| `go test -race -p 1 ./... -count=1 -timeout 30m` | 0 | 866.404 |

发布时 gofmt、`git diff --check`、Python AST/JSON/安装脚本语法与文件范围检查通过；格式化后的 `TestNativeInputResearchRestartAndCacheLifecycle` 在正式 oracle 下定向普通/race 复核均退出 0（测试包耗时 7.522 / 72.626 秒）。原候选完整测试记录保持。

已执行 gofmt，`git diff --check` 通过；固定采集 worker 的三项离线 TEST_FIXTURE 测试通过。正式 oracle 纳入普通与 race 全套；Native cache lifecycle 与实际 SQLite query plan 另有日志。Windows 只有交叉构建通过，原生运行 NOT_RUN；当前非 Unix CommandFile 明确不支持，不交付 Windows 日用就绪声明。

包外命令覆盖 help/version/init/doctor/dry-run/daily/reuse/picks/all/explain/json/csv/markdown/no-overwrite/restart/provider 缺失/策略隔离/临时 install/no-overwrite/真实堵塞 pipe/Ctrl-C/关闭消费者。只读和本地研究不要求 Python。可选在线 worker 的配置流程已提供；现有本机 SDK 依赖失败，联网效果仍未验收。

## 真实输入和差异

输入 `frozen_42a2ac0f7061ce3e6aecc62f6e8706e1161be51855478f125b6703d6326b7abc`；观察日 `2026-09-30`，资料 cutoff `2026-10-02T20:10:00.478775+08:00`。新 run `go_run_f3de7b267bd8e69167d1d8eec0b9e580`；复用对照 `go_run_491f9dc37ce8bbd34940533068b91d43`。同一份已保存历史输入用于当前版本验收，不能称为新采集的最新名单。

严格比較器核验两个 header、全部明细 raw hash 和有序成员集合，比较 6,268,875 个值，3040 条明细无业务差异。它如实退出 1 并记录两处 manifest 差异：`go_source_hash` 换为本轮身份；`coverage_type=PROSPECTIVE_CAPTURED` 从已有输入显式写入 manifest。原差异未删除；summary 的完整覆盖统计一致，只有 metrics/fact_cache_hits/fact_cache_misses 属于操作指标排除。

完整 universe、资料/背景 INCOMPLETE、selection_decidability INCOMPLETE 与 `NOT_EVALUABLE_AS_COMPLETE_STRATEGY` 均保留。R1 RESEARCH_ONLY，promotion DISABLED，参数尚未验证，MARKET_EDGE=NOT_ESTABLISHED。这是工程一致性验收，未做投资优势验证。

## 新轮次预算与保留证据

真实全池 1/2 次，用时 832.179/3600 秒，每次上限 1800 秒；第二次未用。在线 1/1 批次，用时 1.300/600 秒，HTTP 0/100。真实只读监督记录 125.477 秒，另计 60 秒薄元数据审计保守额度，合计 185.477/1800 秒。合成/构建另计。最终容量审计在私有 acceptance.json；新非权威产物低于 2 GiB、项目低于 50 GiB、各涉及卷保留至少 10 GiB。

旧墙钟 14411.776 秒账本、旧 run、失败构建/包测试和本轮严格 manifest 差异/SDK 故障均保留。初次全套曾遇同进程前序 Native SQLite 内存残留并触发 RSS 保护；256 MiB 样本仍原样使用新进程运行，容量/RSS/损坏断言不降低。包测试驱动的路径规范化、空 run-id/字段名修正及 Unicode sandbox 前置失败独立留档，不冒充产品通过；在线失败不重试。

日志输出的 final_resource_scope 表示资源 scope 摘要；观察到完整行才证明收到该行，内存送达状态在写完成后更新。外部不可取消 writer 不启动隐藏 goroutine；普通文件/kernel 阻塞不能承诺任意强制取消。完整所有评价记录若超过 128 MiB 导出上限明确失败，本次 3040 全明细展开 228050910 字节，通过逐条验证比较；6 个候选完整导出已验证。
