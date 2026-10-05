# 当前策略说明

## 当前主线

主动核心为 `STAGE_AWARE_MOMENTUM_V2`：研究沪深主板普通A股的强势股阶段，区分启动、加速、高位修复和趋势回调。先看证券身份、数据日期和资料是否真实可见，再判断形态、行为确认、市场与题材资金背景，以及关键反证。

最终输出 FOCUS、WATCH、REJECTED、UNKNOWN。不同阶段使用各自条件；成交额放大和收盘位置不作为所有阶段统一的硬门槛。详细条件及初始参数见[完整V2规则](stock-fish/docs/STAGE_AWARE_MOMENTUM_V2.md)，冻结配置见[策略配置](stock-fish/config/stage_aware_momentum_v2.yaml)。

## 资料不足如何处理

缺证券资格、合法价格、可比基准、历史窗口或来源证明时，保留未知和原因。主题、资金或事件资料缺失不能假装为否定事实。采集成功、服务健康和策略资料齐备分别判断。

历史下载保留实际取得时间；后取资料不能伪装成当时已知。旧运行保留原规则、输入、截止、排序与理由，复盘追加后续观察，缺失不计零收益。

## 保留的研究与对照

| 规则 | 用途 |
|---|---|
| V2 | 当前主动核心；已有Python日常入口及独立Go候选实现 |
| R1 | 显式选择的研究策略，CORE_PASS使用独立口径，生产晋级关闭 |
| DAILY_V1 | 冻结五族主策略，单独判断资料是否可用 |
| PV、DNA、THEME及旧V1 | 已有研究实验、对照和历史结果解释 |

R1完整定义见[R1规格](stock-fish/docs/STAGE_AWARE_RESEARCH_R1.md)及[冻结修订合同](stock-fish/docs/R1_REPAIR_SPEC_V3.md)。DAILY五族公式见[策略规格](stock-fish/STRATEGY_SPEC_V1.md)；其他规则与详细公式由[项目综述](stock-fish/docs/PROJECT_OVERVIEW.md)统一定位。

本轮文档整理不修改任何规则、权重、阈值或配置。所有初始参数仍标“尚未验证”，MARKET_EDGE=NOT_ESTABLISHED；系统不连接券商、不下单。
