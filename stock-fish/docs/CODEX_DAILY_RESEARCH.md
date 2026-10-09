# Codex日常研究协议

日常用户入口为[使用说明](../../USER_GUIDE.md)，运行维护见[部署说明](../../DEPLOYMENT.md)，当前未达标项目见[当前状态](../../CURRENT_STATUS.md)。本协议用于调用已有逻辑；详细规则与架构按[技术索引](README.md)展开。

## 每次执行

1. 核对真实实例、配置地址、DB/state、交易日历与保存运行。查询实际地址的健康和轻量状态；只读请求不调用可能改变计划或epoch的维护入口。
2. 说明目标交易日、实际观察日、资料截止、生成时间、来源可见时点、范围和run_id。“明天”按上海日期与正式交易所日历解释，日历越界不猜节假日。
3. 优先复用同一保存运行、详情、正式导出及既有私有报告。历史查询保留原实现/配置/源身份，不给旧榜换日期，不用今天规则重算冒充昨天。
4. 需要新资料时核对授权、预算和在途任务，只执行一次有界续采/计算；响应不明先查job，不盲目重发。成功、资料齐备、实际激活分别报告；缺少来源不造合法EMPTY。
5. 日常primary只调用active V2的DAILY_PRIMARY_FAST；PV沿用已确认实验上下文。只有明确的多策略对照或深度研究走RESEARCH_FULL。DAILY_V1独立报告可用性。
   明确选择短线强势版本时，使用已登记的 `SHORT_HORIZON_STRENGTH_R1` 单策略研究入口，沿用原实验作业、保存运行和导出体系，不重跑默认多策略套件。观察选择与正式FOCUS独立；新规则回算旧日期必须标为事后研究。规格见[短线强势R1](SHORT_HORIZON_STRENGTH_R1.md)。
6. 从同一run读取全部/焦点数量、解释、JSON/CSV导出与后验；聊天可节选，完整保存结果不截断、不凑数。未成熟或缺失后验保留null。
7. 跟踪更新原run，需要合法后续输入才追加标签/修订；原排序与原信号不覆写。Codex解释和未知另列。
8. 私有报告保存在被Git忽略的reports/codex/，响应注明run_id、路径、覆盖和阻塞。普通研究不创建空提交或公开行情。

## 调用入口

Python使用[scripts/codex_research.py](../scripts/codex_research.py)薄封装和既有API；参数以实际--help为准，地址从本次核验取得。全局--base-url/--env-file/--timeout放在子命令前。Key仅经既有私有配置传入，不放命令行或日志。

| 请求 | 已有入口 |
|---|---|
| 状态、配置、运行列表 | status / rules / list |
| 原run详情和正式导出 | detail RUN_ID SECURITY / export RUN_ID / report RUN_ID |
| 查询每日交付与后验 | daily-report / daily-outcomes / daily-observation |
| 已确认研究或合法更新 | select / run / capture / track；执行前区分只读与写入 |

封装不复制策略、不另建库、不自动切换运行服务。当前Go候选命令与写入边界见[终端说明](../../stock-fish-go/delivery/USER_GUIDE.md)，不把Python实例与Go隔离实例混作同一部署。

## 规则、资料与权限

主动核心保持[STAGE_AWARE_MOMENTUM_V2](STAGE_AWARE_MOMENTUM_V2.md)，V1为历史只读兼容，R1为显式研究；公式/配置及旧结果保留。先核验证券身份、业务日和父来源时点，再使用价格、题材、资金或风险。未知不当FALSE，缺结果不计零收益，资料不足不称正式FOCUS。

源码、模型、已计划的20个会话和历史下载均不证明当前数据就绪或连续使用可靠。MARKET_EDGE=NOT_ESTABLISHED；DAILY_RECOMMENDATION_SCORE_V1仅读后验，不自动调参。

普通名单、日报、查询和跟踪不自动授权改生产代码/正式策略、提交推送、购买服务、升级模型、扩大预算或部署切换。沿用用户当前模型；无需新增应用内LLM凭据。经明确授权的工程交付按[AGENTS](../../AGENTS.md)和[工程约定](../../ENGINEERING_BASELINE.md)检查、中文提交并推送，不扩大到后续普通研究。

资料缺失仍可读取最后真实结果并标日期；服务离线先按[部署维护](../../DEPLOYMENT.md)核对实例。备份包含库和文件证据，恢复到新目录。新源码落盘不等于服务已重新加载；不覆盖现有配置或数据。

授权改变策略、来源、存储、运行或协作边界时，同步对应现状及[项目综述](PROJECT_OVERVIEW.md)。没有自动综述维护或已验证的新模型会话加载流程。过去机器路径、端口、PID与逐轮测试记录见[原协议归档](archive/before-cleanup-20261005/stock-fish/docs/CODEX_DAILY_RESEARCH.md)，不能据此推断当前实例。
