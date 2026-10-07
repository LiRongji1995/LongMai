# StockFish Live V1 分阶段实施计划

本轮交付 Phase 0 / Phase 1。各阶段独立验收后推进；Phase 1 不输出预测、不启动实时服务、不改变 Research 策略或运行实例。

| 阶段 | 交付与门槛 |
|---|---|
| 0 | 代码、当前说明和测试交叉审计；完成 REPO_INTEGRATION_AUDIT.md，确认依赖与数据真实性边界 |
| 1（本轮） | 独立 Python 包、冻结分钟/来源/快照合同、显式日历、Parquet 与 DuckDB、PIT 守卫、逐分钟 replay、doctor；通过未来访问拒绝、确定性、缺分钟、异常行情、provider timeout、不可覆盖存储测试 |
| 2 | 指数、全市场宽度、Live 专属情绪、PIT 行业成员与边际强度；缺字段保留 UNAVAILABLE；版本化规则 |
| 3 | MARKET_FEATURES_V1 / SIMILARITY_MODEL_V1 / INTRADAY_PATH_V1，历史归一化仅 fit 训练窗、成熟标签、同时间相似状态、分位路径、概率、置信度、追加预测与修订 |
| 4 | 独立 FastAPI / WebSocket / Dashboard，实际路径与概率扇形、revision、regime、宽度、情绪、行业、warning、top 10 相似状态 |
| 5 | expanding walk-forward，多个决策时间、naive baselines、校准与 coverage、VALIDATION_REPORT_V1.md；缺真实历史时报告不可评价，不制造效果数字 |
| 6 | replay 门槛通过后接入有来源时钟/权限证明的实时适配器，沿用同一采集与消费路径，区分 provider latency 与内部 latency |

## 本轮文件范围（编码前登记）

- 新增 `stockfish-live/pyproject.toml`、`.gitignore`、`README.md`、`CURRENT_STATUS.md`。
- 新增 `stockfish_live/common.py`、`calendar.py`、`contracts/{market,quality}.py`。
- 新增 `providers/{base,replay,mock}.py`、`ingest/{normalizer,collector}.py`。
- 新增 `storage/{parquet_store,duckdb_store}.py`、`replay/engine.py`、`cli.py`、`__main__.py`。
- 新增对应独立 `tests/`，显式 TEST_FIXTURE，只在临时/私有目录产生数据。
- 新增架构、数据合同、PIT、模型阶段规划与验证文档；审计文档绑定当前代码，不把历史测试当本轮结果。
- 根 README 仅增加入口；`stock-fish/docs/PROJECT_OVERVIEW.md` 仅同步独立子项目边界。不修改根 CURRENT_STATUS / CURRENT_STRATEGY 或业务代码。

## 设计决定

独立 runtime 和数据目录；运行时不 import `stockfish_core`、Research Flask、SQLite、scheduler 或 Qlib。语义兼容采用少量独立纯函数与合同测试，暂不抽 shared package。

分钟统一为结束标记 RAW OHLC，240 根完整分钟；09:30/13:00 为收集时钟，不伪造零长度 bar。日历必须提供明确 sessions、交易所范围、来源与可见时间，未知覆盖拒绝。

严格 replay 只交付 event/source/actual available 三时钟及父回执均已可见的记录。事后下载保留真实 available_at，不以重建例外放行。fixture 使用单独显式命令生成，不是无凭据时的自动替代。

Parquet 是不可覆盖的数据分片，DuckDB 使用独立进程内只读查询。单写者锁与原子发布保护分片；原始来源字节和回执以 hash 绑定。快照输出追加保存，延迟统计不参与确定性内容 hash。

## 验证和发布

先运行 Phase 1 单元/集成/PIT 测试，再执行完整 fixture 日回放、重复回放和 CLI 失败烟测，记录命令、退出码及实际延迟。此证据只证明工程行为；真实分钟覆盖、投资有效性和实时 SLA 单独保持未验证。

只暂存本轮文件，保护既存 iCloud fixture 变化。不切分支、不创建 worktree、不改历史。核对 main 发布检出与远端后按项目约定中文提交、推送并核对远端 SHA；若不存在安全发布路径，如实报告阻塞。
