# Go R1 迁移实现（尚未验收完成）

本模块是可构建、可独立读取冻结输入与 SQLite 的**迁移诊断实现**。目前不能交付正式 R1 盘前清单，也不替代 Python 生产入口。`run` 完成诊断后退出 **3**，`candidate_availability=UNAVAILABLE`；不会写研究成功的 `completion.json`。`verify` 仅验证诊断包完整性。

已实现：精确有理数和 half-even 舍入；来源四时钟、父回执、范围、历史重建证明、PARTIAL 字段使用、修订冲突；只读 SQLite 一致事务、manifest/分区索引/消费 payload 哈希；日期化资格；收益/均线/换手/市场基准/题材相对强弱；START、ACCELERATION、HIGH_LEVEL_REPAIR、TREND_PULLBACK 独立判断、行为确认与公共失败短路；协作预算、RSS 采样、输出验证。

未完成：完整市场/题材/资金/供应压力背景结果；所有 Python 模型默认值、类型约束与日期规范化；全量理由和引用等价；正式研究覆盖合同及成功发布。它们标为实现缺口，不能当数据 UNKNOWN。详见 [迁移表](MIGRATION.md)。

## 环境与命令

Go >=1.24，当前构建使用私有安装的官方 Go 1.27.1。SQLite 驱动 modernc.org/sqlite v1.36.3，无 CGO；依赖固定于 go.mod/go.sum。工具链、真实数据、二进制、输出不入库。不要升级系统 Go。依赖许可见 [LICENSES.md](LICENSES.md)。

从仓库根目录，给 `GO` 传入可用工具链路径。构建脚本只计算源码身份并调用 Go 编译器；研究二进制不需要 Python。

```sh
python stock-fish-go/tools/build.py --go "$GO" --output work/stockfish-r1
mkdir -p reports/codex/my-r1-parent
work/stockfish-r1 run \
  --input /path/to/frozen-input.json \
  --history-db /path/to/existing.sqlite3 \
  --output reports/codex/my-r1-parent/new-run \
  --config-dir stock-fish/config --budget-seconds 300
# 退出 3 表示诊断已保存，但研究未完成，不能用作盘前名单。
work/stockfish-r1 verify --output reports/codex/my-r1-parent/new-run
```

输出目录必须不存在；配置与当前冻结基线字节哈希不符时拒绝。`verify` 退出 0 仅证明诊断包哈希/输入身份一致，输出同时明确 RESEARCH_INCOMPLETE。退出 1 为错误，退出 2 为用法错误。逐股内容位于有界 `details.ndjson.gz`，仅用于迁移排查；`result.json` 保留身份、资格、基准和明细引用。

资源默认：总时间 300s，可显式设置至1800s；实际消费历史 payload 2GiB（含重复）、RSS 阈值1GiB、Go运行时软目标768MiB、磁盘结果128MiB、解压明细1GiB（单行4MiB）；无磁盘窗口缓存，缓存指标为0。RSS 为当前进程采样，并非OS硬限制；Go heap单列。Windows RSS当前 UNAVAILABLE，不能宣称内存保护已验证。每秒输出进度。协作取消会关闭事务/连接并尽力写 failure；强杀/断电无此保证。

## 对照测试

测试均使用独立 TEST_FIXTURE，不写真实库。Python仅生成隔离参考，不为Go预选证据或提供计算输入。

```sh
stock-fish/.venv/bin/python stock-fish-go/tools/oracle.py \
  --output reports/codex/new-TEST_FIXTURE-oracle
STOCKFISH_R1_ORACLE="$PWD/reports/codex/new-TEST_FIXTURE-oracle" \
  "$GO" -C stock-fish-go test -v ./...
"$GO" -C stock-fish-go vet ./...
STOCKFISH_R1_ORACLE="$PWD/reports/codex/new-TEST_FIXTURE-oracle" \
  "$GO" -C stock-fish-go test -race ./...
GOOS=windows GOARCH=amd64 CGO_ENABLED=0 "$GO" -C stock-fish-go build \
  -o ../work/stockfish-r1-windows.exe ./cmd/stockfish-r1
```

没有 `STOCKFISH_R1_ORACLE` 时，只跳过需显式生成的差分测试，普通单测仍运行；这不构成差分验收。Windows交叉编译不等于Windows原生运行，也不生成已记录源码身份的发布二进制。

## 性能边界

本轮诊断耗时不能与完整 Python R1 的历史耗时相除后宣称加速。当前算法将每只股票验证后的窗口计算一次，跨股复用小型事实，不先执行完整 V2；这是潜在优化依据，不是已通过完整等价验收的提速结论。采集、数据补齐、Python生产服务、已保存名单读取均未切换，因此当前生产盘前交付没有由此获得已证实的提速。
