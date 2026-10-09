# 当前策略说明

## 当前主线

主动核心为 `STAGE_AWARE_MOMENTUM_V2`：研究沪深主板普通A股的强势股阶段，区分启动、加速、高位修复和趋势回调。先看证券身份、数据日期和资料是否真实可见，再判断形态、行为确认、市场与题材资金背景，以及关键反证。

最终输出 FOCUS、WATCH、REJECTED、UNKNOWN。不同阶段使用各自条件；成交额放大和收盘位置不作为所有阶段统一的硬门槛。详细条件及初始参数见[完整V2规则](stock-fish/docs/STAGE_AWARE_MOMENTUM_V2.md)，冻结配置见[策略配置](stock-fish/config/stage_aware_momentum_v2.yaml)。

## 短线强势研究版本

2026-10-09 新增 `SHORT_HORIZON_STRENGTH_R1`，用于已明确选择的短线强势盘前研究。它独立识别强启动、强势延续和强势段后的首次回撤，避免把一般性转暖与强势启动混在一起。启动要求绝对价格推进、相对市场推进、活动扩张和短窗突破；延续先要求已有强势段；首次回撤先验证此前连续上涨，再观察回撤深度和价格支持。

该版本不继承原 START/HIGH 标签，不以行业、证券名单、统一换手率或长期均线状态排除股票。不同阶段使用不同条件；零振幅的正收益 RAW 价位事实不等于已核验涨停，可成交性仍需另证。完整公式和参数见[短线强势R1](stock-fish/docs/SHORT_HORIZON_STRENGTH_R1.md)与[新配置](stock-fish/config/short_horizon_strength_r1.yaml)。

研究观察选择与正式 FOCUS 分开，保留全池、未通过项与未知项。参数来自已见样本的风格校准，尚未通过独立日期或后验验证，`MARKET_EDGE=NOT_ESTABLISHED`。源码登记不自动切换已有服务、调度、数据库或 Go 策略；V2、DAILY_V1 和旧运行保持原身份。

## 资料不足如何处理

缺证券资格、合法价格、可比基准、历史窗口或来源证明时，保留未知和原因。主题、资金或事件资料缺失不能假装为否定事实。采集成功、服务健康和策略资料齐备分别判断。

历史下载保留实际取得时间；后取资料不能伪装成当时已知。旧运行保留原规则、输入、截止、排序与理由，复盘追加后续观察，缺失不计零收益。

## 保留的研究与对照

| 规则 | 用途 |
|---|---|
| V2 | 当前主动核心；已有Python日常入口及独立Go候选实现 |
| SHORT_HORIZON_STRENGTH_R1 | 显式短线强势研究版本；独立三阶段规则、研究观察选择与全池解释 |
| R1 | 显式选择的研究策略，CORE_PASS使用独立口径，生产晋级关闭 |
| DAILY_V1 | 冻结五族主策略，单独判断资料是否可用 |
| PV、DNA、THEME及旧V1 | 已有研究实验、对照和历史结果解释 |

R1完整定义见[R1规格](stock-fish/docs/STAGE_AWARE_RESEARCH_R1.md)及[冻结修订合同](stock-fish/docs/R1_REPAIR_SPEC_V3.md)。DAILY五族公式见[策略规格](stock-fish/STRATEGY_SPEC_V1.md)；其他规则与详细公式由[项目综述](stock-fish/docs/PROJECT_OVERVIEW.md)统一定位。

旧策略的公式、权重、阈值和保存运行保持原版本。新增短线强势R1采用独立配置和来源身份；所有初始参数仍标“尚未验证”，MARKET_EDGE=NOT_ESTABLISHED；系统不连接券商、不下单。
