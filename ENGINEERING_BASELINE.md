# R05 工程基线

当前默认产品仍是沪深主板无账户规则候选研究。R05 新增独立实验层和数据供给 adapter，不改变 `DAILY_V1` 的名称、角色、五族语义或参数状态；`PV_OBSERVATION_V1`、`DNA_RERANK_V1`、`THEME_CONTEXT_V1` 均为 `INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED`。

R05 主要工程变更：

- 新增 `PV_OBSERVATION_V1` 真实量价观察控制组，按当前可信主板普通股合格池、六日完整字段、中位百分位和固定公式排序；
- 新增 `DNA_RERANK_V1` 与 `THEME_CONTEXT_V1`，只对 PV 入围项追加股性和题材上下文，覆盖不足时不回写 S1；
- 新增 Tushare Pro HTTP adapter 端点表、参数校验、字段白名单、分页、token 缺失错误和响应脱敏，当前状态为 `AUTH_REQUIRED / REAL_NOT_VERIFIED`；
- Eastmoney 直接限价按 Asia/Shanghai 业务日校验，worker 超时保留成功子项；
- 规则推导 VERIFIED 必须携带本地认证哈希和直接样本，CandidateRun summary 默认读取轻量投影；
- 首页新增“R05实验策略”区域，并暴露策略规格、实验运行和最近策略对照 API。

R05 实测状态：观察日 `2026-09-04`，真实 PV 采集 43/43 请求完成；当前主板合格池 3,043，只 3,039 只完成 PV 必需字段核验，缺失 4 只，最终入围 525 只。`DNA_RERANK_V1=INSUFFICIENT_COVERAGE`，`THEME_CONTEXT_V1=UNAVAILABLE`，`MARKET_EDGE=NOT_ESTABLISHED`。详见 `review/r05-data-strategy/final-report.md`。

验收：R04/R05 相关业务子集 213 项通过，核心业务测试脚本 665 项通过；直接 `pytest -q` 仍被旧 MiroFish `locales/languages.json` 缺失阻塞。运行数据、真实 `.env`、provider cache、私人 DB、日志、模型权重、完整市场输入和完整本机审阅包不属于源码发布内容。

---

# R04 工程基线

当前默认产品是沪深主板无账户规则候选研究，版本仍为 `YAOGU_V1.1.0` / `YAOGU_FEATURES_V1_R03`。五族公式、权重、阈值、展示语义和 `MARKET_EDGE=NOT_ESTABLISHED` 不因 R04 改动而改变。

R04 主要工程变更：

- 新增 `daily_limits_eastmoney` 直接逐日限价来源，默认 DAG 为全主板当前对象登记分批请求；
- direct published 限价行进入 normalizer，并要求证券、日期、close 与 RAW 行一致；
- `RULE_DERIVED_VERIFIED` 只在官方规则、普通/豁免状态、参考价语义、来源 hash、验证套件和样本证据齐备时成立；
- CandidateRun 默认读取改为分页 summary，单证券完整证据通过 detail API 延迟加载；
- CLI `capture_mainboard_data.py --rounds` 支持连续有限预算续采。

R04 实测状态：开发工作区全量业务测试 `656 passed in 179.94s`，默认服务烟测可启动/访问/停止；低预算真实网络烟测仍为 `consumer_status=UNAVAILABLE`，不是合法 EMPTY，也不是完整真实榜单。详见 `docs/r04-release.md`。

运行数据、真实 `.env`、provider cache、私人 DB、日志、模型权重和完整本机审阅包不属于源码发布内容。
