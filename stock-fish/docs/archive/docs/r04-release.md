> 历史归档：保留当时的说明、数字、失败和限制，仅调整文档相对链接。当前使用请看[当前状态](../../../../CURRENT_STATUS.md)；原路径可在[归档索引](../README.md)查找。

# R04 发布说明

这是公开源码的 R04 变更与验收摘要，不包含本机私人数据库、原始全市场响应、provider cache、venv、日志或凭据。验收日期为 2026-09-06，实际烟测观察日为 2026-09-04。

## 已实现

- 默认全主板 DAG 增加 `daily_limits_eastmoney` 直接限价批次，为每 20 只当前主板证券登记 Eastmoney 单股 `qt/stock/get` f51/f52 请求。
- Provider worker 对单股失败做 per-security gap 记录，成功行携带 instrument/date/close/limit_up/limit_down/source timestamp/field mapping。
- Normalizer 优先消费 `DIRECT_PUBLISHED_EASTMONEY_F51_F52_R04` 限价行，并要求与 RAW close、证券和日期一致。
- `rules.py` 新增 `derive_verified_limits`，只有在官方规则、普通/豁免状态、参考价语义、来源 hash、验证套件和样本证据齐备时才返回 `RULE_DERIVED_VERIFIED`。
- CandidateRun 新增分页 summary API 与单证券 detail API；默认页面先读 summary，不再首屏下载完整大记录。完整 run/export 仍保留用于审计。
- `scripts/capture_mainboard_data.py --rounds` 支持连续有限预算续采。

## 实际结果

R04 低预算真实网络烟测没有产出完整 DAILY：

| 项目 | R04 实测 |
|---|---:|
| 当前主板证券 | 3,194 |
| 队列请求 | 399 |
| 本轮网络调用 | 8 |
| 剩余请求 | 393 |
| 剩余直接限价批次 | 160 |
| 候选数 | 0 |
| consumer_status | UNAVAILABLE |

单股直接限价探针当前因 `PROXY_CONNECTION_FAILED` 失败。因此本次不是合法 EMPTY，也不是完整真实榜单；它证明了新连接器、失败分类和持久 pending 队列路径。

## 验证

开发工作区全量业务测试：`656 passed in 179.94s`。另通过 `pip check`、`node --check static/core.js`、`python -m compileall -q stockfish_core app.py scripts`、`git diff --check`（仅 CRLF 提示）。

默认服务烟测：根目录启动脚本启动成功，`GET /`、`GET /api/health`、`GET /api/candidate-data-pipeline`、`GET /api/candidate-runs` 返回 200；候选研究状态仍如实为 `UNAVAILABLE`；停止脚本停止托管进程。

发布检出目录烟测：R04/product + R03 DAG + R02 candidate path 子集 `44 passed in 77.76s`，`node --check`、`compileall`、`git diff --check` 通过。

`MARKET_EDGE` 仍为 `NOT_ESTABLISHED`。R04 是工程与数据链路推进，不是投资收益证明。
