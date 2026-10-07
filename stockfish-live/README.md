# StockFish Live

A 股盘中 Market Weather / Intraday Forecasting 的独立研究子项目。当前已实现从严格分钟回放、PIT 上下文、指数/宽度/情绪/行业状态，到历史相似模型、追加预测与修订、本地 Dashboard、expanding walk-forward 的工程流程。已提供标准 HTTP feed 适配器；真实供应商尚未配置、未完成实际联网与长期运行验收。工程可运行与投资有效性分别判断，本轮实际检查结果见[验证记录](docs/VALIDATION_V1.md)和[当前状态](CURRENT_STATUS.md)。

```text
MARKET_EDGE=NOT_ESTABLISHED
LIVE_TRADING=DISABLED
BROKER_CONNECTION=NONE
```

Live 不连接 Research 数据库、Qlib、券商或下单接口。规则、初始参数及模型均保持“尚未验证”；缺历史、缺资格或缺来源时钟会明确返回未知或样本不足，不能用合成演示宣称策略成熟。

## 安装

使用独立 Python 3.12 虚拟环境，不修改现有 Research 环境。无需 GPU、LLM、应用账户或供应商 key 即可运行合成演示。

```sh
cd stockfish-live
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-test.lock.txt
.venv/bin/python -m pip install -e . --no-deps
```

Windows 使用 `.venv\Scripts\python.exe` / `.venv\Scripts\stockfish-live.exe`；Windows 原生验收以验证记录为准。数据根默认为用户目录 `.stockfish-live/`，支持环境变量 `STOCKFISH_LIVE_DATA_ROOT` 和各命令的 `--data-root`。请使用私有运行目录，与源码发布目录分开。

## 完整合成演示

`demo` 显式生成历史合成会话、目标日分钟、盘前上下文及经真实 feature/label 路径产生的训练材料。目标目录必须不存在。日期为连续合成日历日期，包含周末，**不是官方交易日历**；全部记录为 `TEST_FIXTURE`，三只股票仅代表声明池。完整未来路径的生成和校验可能耗时较长，实际数量和资源耗时写入 `demo-report.json`。

新演示默认采用 `MARKET_FEATURES_V2`：逐分钟调用流式状态引擎，保存首次观察到的过去快照，不用后来的资料重算过去。`research-config.json` 中 `forecast.feature_version` 决定运行使用 V2 流式增量或 V1 无状态参考路径；没有该文件时保留 V1 兼容默认。训练只匹配相同特征版本与特征配置 hash。新数据集、新运行单独登记 hash，不会升级或覆写已有 V1 run。

```sh
.venv/bin/stockfish-live demo --date 2026-09-30 --history-sessions 60 \
  --data-root /path/to/new-private-demo
.venv/bin/stockfish-live doctor --data-root /path/to/new-private-demo
.venv/bin/stockfish-live replay --date 2026-09-30 --speed max \
  --data-root /path/to/new-private-demo --output /path/to/new-private-run
.venv/bin/stockfish-live serve --run-dir /path/to/new-private-run --host 127.0.0.1 --port 8765
```

打开本地 [Dashboard](http://127.0.0.1:8765)，可查看五指数实际路径与未来分位扇形、regime、宽度、情绪、行业、revision、warning、top 10 相似状态和时间选择。API 文档位于 [本地 API](http://127.0.0.1:8765/api/docs)。服务读取指定运行目录，不自动采集行情或触发回测。它支持读取正在追加的运行；缺文件、失败状态和不晚于所选时点的数据分开显示。

默认 09:45 起每 5 分钟预测；`replay --early-forecasts` 显式启用 09:35/09:40，始终保留早盘低置信度提示。无历史或成熟标签时界面显示 `INSUFFICIENT_HISTORY`，不会替换为人为 forecast。`fixture` 仍提供不含历史与上下文的单日基础测试池，`replay --foundation-only` 保留冻结 Phase 1 基础层路径。细节见[合成演示说明](docs/DEMO.md)。

## 自有历史与评价

数据根需要 `calendar.json`、`dataset.json`、有 raw/receipt/manifest 的分钟归档；PIT 上下文与历史 feature/label 是独立可选输入，缺失会限制状态与预测。`ParquetStore.append` 接收单父来源批次；`append_many` 可导入单日多父来源，同时保留每个分钟的真实收件时间。不能直接复制没有 manifest 的 Parquet 冒充合法归档。来源与字段见[数据合同](docs/DATA_CONTRACT.md)。

```sh
.venv/bin/stockfish-live replay --start 2026-09-01 --end 2026-09-30 \
  --data-root /path/to/private-archive --output /path/to/new-private-range-run
.venv/bin/stockfish-live evaluate --as-of 2026-09-30T16:00:00+08:00 \
  --data-root /path/to/private-archive --run-dir /path/to/new-private-run
```

范围仅遍历显式日历 sessions，每日新状态，不推断工作日。运行目录必须为新目录；中断保留原产物并标记 `FAILED`。输出包含 `snapshots/HHMM.json`、独立 `latency.jsonl`、`summary.json`，完整 pipeline 另追加 feature 与 forecast Parquet。未来实际结果仅在成熟后进入评价，不重写旧预测。

每个 snapshot 的 `forecasts` 仅包含本轮 5 分钟决策新产生的预测，其他分钟为空；`latest_forecast_ids` 追踪各指数最近预测。完整预测追加保存在该 run 的私有 `ResearchStore`，API 按所选时刻读取已经产生的记录。启动和评价会核验成熟标签的原始父来源、完整未来分钟路径、锚点、目标及 regime，单独的研究表 manifest 不能替代这些证据。

`doctor` 分别报告归档回放、测试演示与真实研究就绪。研究检查验证标签来源，并按五指数 × 四个默认决策时刻 × 对应 horizon 核对同特征版本/config hash 的成熟历史会话数；不足不会因为有文件而通过。默认完整特征检查采用历史中曾有值的维度并集；实际预测仅采用本次查询有值的维度，两者样本数可能不同。UNKNOWN 情绪的 one-hot 保留为空，不填零来凑齐样本。TEST_FIXTURE 只参与 `test_fixture_research_ready`，`research_ready` 始终为 false；生成 60 日演示也不保证完整特征检查通过。

评价包含两部分：expanding walk-forward 是按历史时钟重新生成预测的回顾诊断；`saved_forecast_posterior` 读取归档和指定 run 中原样保存的预测，仅在实际结果成熟后追加后验，不重训、不重算、不改旧预测。后者保留原 forecast_id/model_hash/config/as_of，不为旧预测补造 baseline。评价默认取 `09:45,10:00,10:30,13:30`；可用 `--decision-times HH:MM,...`、`--all-decision-times`、`--min-train-sessions N`。默认进行仅使用更早资料的内层 K 验证；`--no-tune` 使用固定初始 K。结果分离真实 VERIFIED_PIT OOS、合成工程诊断和未证明 PIT 的研究结果，并按证券、决策时刻及 horizon 分组。没有真实 OOS 时为 `NOT_EVALUATED`，始终保留 `NO_EVIDENCE_OF_INCREMENTAL_EDGE`。重叠 horizon 不是独立样本；本轮没有交易成本或执行模型。详见[模型与评价](docs/MODEL_V1.md)。

## 显式配置的标准 HTTP feed

```sh
.venv/bin/stockfish-live live --data-root /path/to/private-feed \
  --output /path/to/new-private-live-run --max-ticks 240 --timeout 10
```

`live` 要求显式 `provider.json` 和对应日历、scope；PIT 上下文可选，缺失会限制状态和预测。未配置返回 `LIVE_PROVIDER_UNAVAILABLE`，不会选取外部端点或自动改用合成行情。适配器接收 `LIVE_HTTP_FEED_V1`，仅接受 END/SHARE/CNY/RAW 口径；可见时间由本机实际 HTTP 收件和解析时钟产生。供应商发布时间可信性须独立核验，配置声明本身不是证据。当前没有真实 endpoint 的连通、授权、数据完整性或连续交易日 SLA 验证。

## 边界

- 声明池宽度与已证明完整市场宽度分开；证券语法、采集成功和 health 正常都不能证明历史资格或数据就绪。
- 来源、事件和实际可见时钟共同受控；旧历史今天下载，不能用于过去当时的预测。
- 当前成员不得回填历史行业；缺资格、限价、同时段成交额基准或未来标签保留未知。未知事件不作 FALSE，缺结果不计零收益。
- 分位为经验历史分布，置信度表示样本支持和数据限制，不能解释为投资成功概率。初始阈值、K 候选和情绪/行业转移规则均尚未验证。
- 不提交凭据、原始供应商数据、私有 DB、日志、模型或运行产物；源码和真实数据的许可分别核对。整个 monorepo 不称为纯 MIT，沿用根[第三方通知](../THIRD_PARTY_NOTICES.md)。

检查命令：`.venv/bin/python -m pytest -q tests`。实际退出码、性能和未执行范围由[验证记录](docs/VALIDATION_V1.md)登记；设计见[架构](docs/ARCHITECTURE.md)、[PIT](docs/PIT_POLICY.md)、[实施计划](docs/IMPLEMENTATION_PLAN.md)及[依赖](docs/DEPENDENCIES.md)。
