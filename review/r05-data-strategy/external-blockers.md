# R05 外部阻塞

- Tushare adapter 已实现，但当前环境未提供可用于真实验证的 token；状态为 `AUTH_REQUIRED / REAL_NOT_VERIFIED`。
- `DNA_RERANK_V1` 需要历史涨停/触板事件与次日样本，本轮 PV 专用采集没有完整覆盖，状态为 `INSUFFICIENT_COVERAGE`。
- `THEME_CONTEXT_V1` 需要冻结主题成员与同伴行情，本轮 PV 专用采集未抓题材，状态为 `UNAVAILABLE`。
- 原 `DAILY_V1` 未被 R05 宣告完整可用；完整链路仍需要限价事件、主题、资金/情绪与后验数据持续接通。
- 直接全量 `pytest -q` 会收集旧 MiroFish 脚本测试并因缺少 `locales/languages.json` 中断；核心业务测试脚本已通过。
