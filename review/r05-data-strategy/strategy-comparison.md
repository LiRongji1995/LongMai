# R05 策略对照

| 策略 | 本轮状态 | 数量 | 说明 |
| --- | ---: | ---: | --- |
| DAILY_V1 | 未因 R05 重命名或降级 | 未重新声明完整可用 | 原五族仍需完整限价事件、主题、资金/情绪等链路。 |
| PV_OBSERVATION_V1 | PARTIAL | 525 | 真实 2026-09-04 量价控制组；字段完整 3039，Top20 见 `pv-observation-top20.csv`。 |
| DNA_RERANK_V1 | INSUFFICIENT_COVERAGE | 0 | 当前缺历史涨停/触板和次日样本覆盖，不回写 S1。 |
| THEME_CONTEXT_V1 | UNAVAILABLE | 0 | 当前 R05 PV 专用采集未抓题材成员，不回写 S1。 |

PV Top20：601086.SH, 002909.SZ, 002059.SZ, 000892.SZ, 603138.SH, 600657.SH, 603270.SH, 600865.SH, 601999.SH, 001299.SZ, 002868.SZ, 002300.SZ, 600828.SH, 600540.SH, 603613.SH, 600185.SH, 603161.SH, 605088.SH, 002403.SZ, 001222.SZ

所有新增策略均为初始假设，`MARKET_EDGE` 仍为 `NOT_ESTABLISHED`。
