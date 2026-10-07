# StockFish Live 当前状态

核对日期：2026-10-07。版本 0.1.0；审计基线为 `a3e06ee8c1d29b9e66ec5c4a22ba2778c3b6960c`。本文件只描述 Live，不覆盖根目录 Research 状态。

```text
MARKET_EDGE=NOT_ESTABLISHED
LIVE_TRADING=DISABLED
BROKER_CONNECTION=NONE
```

| 阶段 | 当前判断 |
|---|---|
| Phase 0 Repository Audit | 已完成；代码/当前文档/测试定义交叉核验 |
| Phase 1 Contracts + Replay | 已完成工程交付；独立环境、分钟/来源/快照合同、显式日历、Parquet/DuckDB、严格 PIT、doctor、单日/范围 replay |
| Phase 2 Market State | 未实现；指数状态/全市场宽度/情绪/行业仍不可用 |
| Phase 3 Similarity / Forecast | 未实现；尚无模型、分位路径、相似状态或 forecast revision |
| Phase 4 Dashboard / API | 未实现；没有启动新网页或服务 |
| Phase 5 Walk-Forward Evaluation | 未实现；没有 MAE/Brier/coverage 或投资效果结论 |
| Phase 6 Real Live Provider | 未实现；live 明确失败，replay 不依赖凭据 |

完整工程测试及 CLI 结果见 [VALIDATION](docs/VALIDATION.md)：159 项通过，退出 0。已完成一个明确 TEST_FIXTURE 全日回放，240 个结束标记分钟、242 ticks、5 个指数和 3 个股票身份、1,920 rows；两次快照逐字节一致、序列 hash 一致，09:45 仅累计 120 条当时可见记录。双日范围回放也验证每日状态隔离与失败证据保留。

真实分钟历史与 VERIFIED_PIT 覆盖为 **UNAVAILABLE**，真实 provider、Windows 原生、长时间运行、全市场容量和实时 SLA 均未验证。内部处理含持久化 P95 在该小型 fixture 两次分别约 62ms/86ms；这是基础层工程观测，没有 forecast 或 dashboard push，不能作为 V1 全链路验收。

现有 Research 生产代码、策略、数据库、调度与运行实例不变；根 README 与项目综述只增加独立入口和边界。用户既存 iCloud fixture 删除/占位文件未纳入本次修改。

下一阶段按[计划](docs/IMPLEMENTATION_PLAN.md)推进 Market State，先取得来源明确、逐时可见、范围与资格可解释的真实分钟输入，再登记宽度/行业分母与规则版本。当前完整 V1 尚未完成，所有模型参数与市场可预测性仍待验证。
