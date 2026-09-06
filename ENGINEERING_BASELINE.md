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
