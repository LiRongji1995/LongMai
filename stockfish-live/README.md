# StockFish Live

A 股盘中 Market Weather / Intraday Forecasting 的独立研究子项目。当前交付 **Phase 0 / Phase 1：仓库审计与严格分钟回放基础层**；完整 V1 尚未完成。

目标是研究当时可见的市场状态对剩余走势有多少可预测性。当前可以逐分钟输入已归档行情，检查身份、交易日、时钟、父来源和数据质量，追加保存可复现快照。预测、宽度/情绪/行业引擎、Dashboard、walk-forward 评价和真实实时 provider 按[分阶段计划](docs/IMPLEMENTATION_PLAN.md)继续交付，当前没有预测效果结论。

```text
MARKET_EDGE=NOT_ESTABLISHED
LIVE_TRADING=DISABLED
BROKER_CONNECTION=NONE
```

## 独立安装

使用 Python 3.12，在本目录创建自己的虚拟环境。不要升级现有 Research 环境，不需要 Qlib、GPU、账户、LLM 或 provider key。

```sh
cd stockfish-live
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-test.lock.txt
.venv/bin/python -m pip install -e . --no-deps
```

Windows 对应使用 `.venv\Scripts\python.exe` 和 `.venv\Scripts\stockfish-live.exe`。锁文件记录本轮 macOS/Python 3.12 验证版本，Windows 原生尚未验收。

## 无 API key 的工程演示

下面的数据为明确标记的合成 `TEST_FIXTURE`。日期只是测试会话标签，生成器不证明该日期为真实交易日。默认数据根是用户目录下 `.stockfish-live/`；可通过 `STOCKFISH_LIVE_DATA_ROOT` 或每条命令的 `--data-root` 指定私有目录。

```sh
.venv/bin/stockfish-live fixture --date 2025-06-18 --data-root /path/to/new-test-fixture
.venv/bin/stockfish-live doctor --data-root /path/to/new-test-fixture
.venv/bin/stockfish-live replay --date 2025-06-18 --speed max \
  --data-root /path/to/new-test-fixture --output /path/to/new-replay-output
```

`fixture` 拒绝已存在的目标目录；不会作为采集失败的自动替代。它生成 5 个指定指数身份和 3 个股票身份的 240 分钟共 1,920 条人工行情，仅供工程检查。生成器写入时会核验既有分片和来源，构建全日 fixture 可能较慢。

也支持 `python -m stockfish_live replay --date YYYY-MM-DD`。`--speed max` 不等待；数字如 `--speed 60` 表示每分钟数据约一秒进入，午休直接推进到下一收集时钟。

## 自有历史数据回放

数据根须包含 `calendar.json`、`dataset.json`、分钟 Parquet 分片及 `sources/` 下绑定的原始字节和回执。通过 `ParquetStore.append(bars, receipt, raw_payload)` 导入已规范化记录，不能随意复制无 manifest 的 Parquet 冒充合法归档。完整 schema 和导入边界见[数据合同](docs/DATA_CONTRACT.md)。

```sh
.venv/bin/stockfish-live doctor --data-root /path/to/private-archive
.venv/bin/stockfish-live replay --start 2025-06-01 --end 2025-06-30 \
  --data-root /path/to/private-archive --output /path/to/new-range-output
```

范围必须处于显式日历覆盖内；只遍历登记的 sessions，不猜工作日。每个交易日使用新内存状态。回放输出目录必须不存在；记录与后续尝试分别保存。全日无实际可见资料会失败，部分缺分钟会保留 GAPPED，不补造 bar。

输出包括每个 tick 的 `snapshots/HHMM.json`、单独的 `latency.jsonl` 和 `summary.json`。快照含输入 hash、配置 hash、实际 cutoff、有效 PIT 分级、收到的分钟记录、缺口与警告。非确定性延迟不进入内容 hash。服务异常保留已写快照，未完成运行留下 `FAILED`。

## Live 与后续入口

```sh
.venv/bin/stockfish-live live
```

Phase 1 明确返回 `LIVE_PROVIDER_UNAVAILABLE`，退出码 2，replay 仍可用。`serve` / `evaluate` 当前返回 `NOT_IMPLEMENTED_PHASE1`；本轮没有启动网页、网络采集或实时服务。ReplayProvider 和将来的 live provider 都经 `LiveMarketProvider -> Collector -> QualityMonitor` 消费，业务消费路径不按运行模式分叉。

## 边界与限制

- 运行时不导入 `stockfish_core`、Research Flask、CandidateService、SQLite recommendation store、scheduler 或 Qlib；Research 既有策略、数据和服务不变。
- 数据范围为 `DECLARED_SCOPE_ONLY`。测试池和当前主板名单都不代表全市场；证券身份语法不能证明资格、ST、停牌、指数成分或行业成员。
- 严格 replay 同时约束事件、来源、实际可见时间和父回执；事后下载保留真实收件时间，不能回填成过去已知。`RECONSTRUCTED/RESEARCH_ONLY` 不自动升级。
- 目前不接入任何真实分钟供应商，没有经过验证的真实分钟 PIT 覆盖。原始数据的使用和再分发遵循来源条款，源码发布不携带私有行情。
- 相同原始字节 hash 绑定唯一回执；不同取得时间冲突会拒绝。历史修订/乱序不静默替换，本阶段要求新归档版本后重新回放。
- Phase 1 读取整日分片做完整性校验，状态每次重新计算内容 hash；没有证明全市场性能、实时 SLA 或跨日长期稳定性。

测试命令：`.venv/bin/python -m pytest -q tests`。本轮结果与未执行范围见[验证记录](docs/VALIDATION.md)，当前阶段见[CURRENT_STATUS](CURRENT_STATUS.md)，设计见[架构](docs/ARCHITECTURE.md)、[PIT](docs/PIT_POLICY.md)、[模型规划](docs/MODEL_V1.md)、[仓库审计](docs/REPO_INTEGRATION_AUDIT.md)。

许可证边界沿用根[第三方通知](../THIRD_PARTY_NOTICES.md)，整个 monorepo 不称为纯 MIT。新增依赖与可分享范围见[依赖说明](docs/DEPENDENCIES.md)。
