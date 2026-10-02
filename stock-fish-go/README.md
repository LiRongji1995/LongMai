# StockFish Go V2

Go 主系统采用共享领域合同、证据校验、公共事实、独立 R1/V2 策略、应用服务与 SQLite V2 存储。统一入口是 `stockfish`；`stockfish-r1` 仅保留历史诊断兼容命令。默认策略为 `STAGE_AWARE_MOMENTUM_V2`，R1 必须显式选择 `STAGE_AWARE_RESEARCH_R1`，仍为 `RESEARCH_ONLY`、`production_promotion=DISABLED`。参数“尚未验证”，`MARKET_EDGE=NOT_ESTABLISHED`。

当前代码已实现；整体验收为 **BLOCKED_MIGRATION_BUDGET**。真实记录搬迁已完成，但累计迁移/验证已用7199.963s，达到本轮7200s上限，完整校验未通过、目标未发布。真实新库业务闭环、同输入全池差分、冷暖性能与隔离切换仍未完成。不能凭 fixture、`SUCCEEDED` 或 health 正常宣称全部验收完成。证据见 [VALIDATION](VALIDATION.md)。本轮不切换生产服务，不提交真实数据库、缓存、报告或凭据。

设计与合同：[架构](ARCHITECTURE_V2.md)、[迁移矩阵](MIGRATION.md)、[策略语义与14例差分](STRATEGY_PARITY.md)、[证据合同](EVIDENCE_CONTRACT.md)、[隔离切换/回退](ISOLATED_LIFECYCLE.md)、[依赖许可](LICENSES.md)。

## 构建

`go.mod` 要求 Go ≥1.26，当前验证工具链为私有官方 Go 1.27.1；不修改系统 Go。SQLite 使用固定的 `modernc.org/sqlite v1.60.1`，不依赖 CGO。设置 `GO` 为可用编译器路径；从仓库根目录构建：

```sh
python stock-fish-go/tools/build.py --go "$GO" --command stockfish --output work/stockfish
```

构建脚本计算所有 Go、SQL、嵌入官方交易日历 JSON 与 go.mod/go.sum 的源码身份并嵌入二进制；Python只用于构建辅助及离线参考测试。正式 Go 研究、数据库迁移、旧结果导入和 API 不启动 Python。直接 `go build` 的开发二进制标为 `UNRECORDED_DEVELOPMENT_BUILD`。

## 新库与统一应用入口

`--db` 必须显式指向已验证发布的 V2 数据库。原生研究从新库读取，`--comparison-history-db` 只为明确受控的旧库只读对照提供。示例路径均需替换为本任务私有路径；运行真实全池前遵守本次任务账本预算。

```sh
work/stockfish import --db /path/to/v2.sqlite --input /path/to/frozen-input.json
work/stockfish research --db /path/to/v2.sqlite --id INPUT_ID --key UNIQUE_ATTEMPT --budget-seconds 300
work/stockfish research --db /path/to/v2.sqlite --id INPUT_ID --strategy STAGE_AWARE_RESEARCH_R1 --key UNIQUE_R1_ATTEMPT --budget-seconds 300
work/stockfish runs --db /path/to/v2.sqlite --limit 50 --offset 0
work/stockfish show --db /path/to/v2.sqlite --id RUN_ID
work/stockfish summary --db /path/to/v2.sqlite --id RUN_ID --selection all --page 1 --limit 50
work/stockfish detail --db /path/to/v2.sqlite --id RUN_ID --instrument INSTRUMENT_ID
work/stockfish verify-run --db /path/to/v2.sqlite --id RUN_ID
work/stockfish export --db /path/to/v2.sqlite --id RUN_ID --output /path/to/new-export.json
```

`summary --selection` 支持 `all/focus/outcomes/tracking`，R1 另支持 `core_pass`；R1 查询须传对应 `--strategy`。摘要、详情、导出同源，查询不重新执行策略。每股 payload 包含事实、所选策略决策、coverage与引用；市场背景在 summary 中共享。发布前校验原字节/成员身份及查询投影，半成品不作为已发布 run 可见；导出目标必须不存在。

已有采集批次迁入新库后可登记为新应用输入，明确保留旧 ID 和原 cutoff：

```sh
work/stockfish import-legacy-input --db /path/to/v2.sqlite --id OLD_CANDIDATE_INPUT_ID --cutoff ORIGINAL_RFC3339_CUTOFF
work/stockfish legacy --db /path/to/v2.sqlite --namespace NAMESPACE --id OLD_ID
```

数据库迁移入口为 `database inventory/snapshot/migrate/incremental/verify/publish`；具体阶段及约束见 [MIGRATION](MIGRATION.md)。旧 Python R1 文件包独立迁入/校验/导出，原 run_id 和原字节保持：

```sh
work/stockfish bundle-import --db /path/to/v2.sqlite --input /path/to/old-run-directory
work/stockfish bundle-verify --db /path/to/v2.sqlite --id OLD_R1_RUN_ID
work/stockfish bundle-export --db /path/to/v2.sqlite --id OLD_R1_RUN_ID --output /path/to/new-archive-directory
```

## 任务、服务与资源

`jobs/job/cancel/recover/resume` 读取和管理持久任务；恢复会保留旧尝试与事件，不自动重新执行历史任务。`health` 为轻量检查，`readiness` 未就绪时返回错误；深度验证通过显式命令执行。`schedule` 读取计划，隔离 scheduler 默认暂停。`cache-clear` 只删除可重建派生缓存；事实缓存必须在原始证据完整性检查后使用。

```sh
work/stockfish health --db /path/to/v2.sqlite
work/stockfish readiness --db /path/to/v2.sqlite
work/stockfish serve --db /path/to/v2.sqlite --listen 127.0.0.1:18081
```

HTTP 与 CLI 使用同一应用服务。写 API 通过环境变量 `STOCKFISH_V2_API_TOKEN` 配置 bearer 凭据，HTTP 不接受任意文件路径；不把凭据写入命令日志或源码。已登记隔离实例可使用 `--instance-root` 读取已验证活动库；切换/回退仅操作带任务标记的私有目录，见 [ISOLATED_LIFECYCLE](ISOLATED_LIFECYCLE.md)。

CLI打开库可能初始化版本化扩展，因此查询命令与维护命令也共用数据库租约；`serve`占用该租约时，通过其HTTP查询，不另开CLI修改/初始化同库。实例配置在取得根租约后读取，并锁住该配置对应的目标，避免恢复切换与旧路径读取竞争。排队请求执行前、结果发布事务内分别复核就绪状态。

研究默认总预算 300s，最高1800s；消费历史原始 payload 2GiB、RSS 1GiB、Go软目标768MiB。RSS与Go heap分别报告；不可用指标不能当零。预算覆盖载入、验证、事实、所选策略、保存和校验，进度持续输出；取消沿context传播。迁移按全库大小分批处理，不套研究2GiB上限；本轮迁移操作累计7200s、新增文件50GiB、始终保留10GiB空间。全池策略次数/累计时间另由任务账本统筹。

## 检查与验收

测试使用独立 DB/state/artifact 和 `TEST_FIXTURE`。生成 oracle 的目录必须全新：

```sh
stock-fish/.venv/bin/python stock-fish-go/tools/oracle.py --output reports/codex/new-TEST_FIXTURE-oracle
STOCKFISH_R1_ORACLE="$PWD/reports/codex/new-TEST_FIXTURE-oracle" STOCKFISH_FORMAL_ACCEPTANCE=1 "$GO" -C stock-fish-go test ./...
"$GO" -C stock-fish-go vet ./...
STOCKFISH_R1_ORACLE="$PWD/reports/codex/new-TEST_FIXTURE-oracle" STOCKFISH_FORMAL_ACCEPTANCE=1 "$GO" -C stock-fish-go test -race -p 1 ./... -timeout 30m
GOOS=windows GOARCH=amd64 CGO_ENABLED=0 "$GO" -C stock-fish-go build -o ../work/stockfish-windows.exe ./cmd/stockfish
```

正式验收无 oracle 必须失败；普通开发不设置正式标志时可能 skip 的差分不算验收。Windows交叉编译不等于原生运行。已执行14例策略、背景及coverage差分的范围见 [STRATEGY_PARITY](STRATEGY_PARITY.md)。真实历史比较工具为 `tools/compare_saved.py`，可从 `--go-db --go-run-id` 流式读取新结果；明确比较已保存字段的语义投影，无法从旧结果恢复的原始历史选择哈希另列补充验证。
