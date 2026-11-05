# Python业务维护入口

**2026-11-05 工程修复候选：**[共同请求权威、恢复与研究隔离实施记录](docs/REMEDIATION_20261105.md)提供新的主动执行树、事务防重、多代恢复、持久阶段计量、标签隔离及专项 CI 映射。新执行树通过精确输入清单和责任层接线约束旧兼容入口；原已认证源码与登记保持。**源码发布不代表实际登记启用；旧运行路径的缺口尚不能据此宣布消除。**启用前须完成完整历史导入、有限预算及环境预检并取得明确切换授权；不能直接用下面历史命令重试旧失败。连续 20 个真实交易日、真实执行来源与独立投资验证未完成，`MARKET_EDGE=NOT_ESTABLISHED`。

新增[架构、交付与量化评价优化V1](docs/ARCHITECTURE_OPTIMIZATION_DELIVERY_V1.md)：提供内外层原生监督V3、统一运行/交付/覆盖审阅、冻结研究与条件评价、显式组件清单、存储实验、根级CI和新目录恢复演练。旧策略/核心及默认original入口保持。当前环境七步复验通过，但原登记物理身份变化仍阻塞真实准入；最新完整盘前、连续20交易日、真实执行证据和独立投资验证未通过，源码发布不切换服务或重试旧请求。

默认 `codex_daily_v2.py premarket` 保留原题材执行器；`--engine shared` 是显式共享计算候选。小样本全流程曾测得回退，因此没有将其自动设为默认，也没有据此宣称真实新清单提速。

[完整盘前执行V1](docs/PREMARKET_SHARED_EXECUTION_V1.md)新增scripts版本，完整原/新流程等价与实际进程监督分别验证。性能比较必须测整个流程，不能用解码次数代替耗时；真实缺数据及预算阻塞不改判。旧core源码和历史run保持。

[统一执行路径V1](docs/SYSTEM_EXECUTION_PIPELINE_V1.md)新增代码限定scripts层，旧core源码与动态认证集合保持。封存查询、依赖计划和有界新计算分开验收；局部解析次数下降不能冒充真实全链路或默认300秒后验通过。

新增[共享结果、压缩及复权尾窗V2](docs/SHARED_RESULT_COMPACT_INCREMENTAL_V2.md)采用独立版本和TEST_FIXTURE检查；旧核心及来源文件保持。完整归档复用、新输出压缩与尾窗诊断分别验证，不因只读档位扩大原写入预算，不将尾窗升级为完整策略输入。

## Codex 共享入口增量

2026-10-25 的 [共享入口与容量管理](docs/CODEX_CHAT_WORKSPACE_V1.md)使用独立 scripts 层和显式 TEST_FIXTURE。旧 `codex_research.py`、core、策略配置及历史 run 保留；新入口登记既有运行环境，不复制数据库或自动修复依赖。检查范围、实测节省及失败记录见该说明，不视为全仓回归、真实盘前提速或投资验证。

统一维护约定见[根工程基线](../ENGINEERING_BASELINE.md)，部署与备份见[部署维护](../DEPLOYMENT.md)，现状见[当前状态](../CURRENT_STATUS.md)。

Python日常主动核心为V2，保存运行、冻结配置与来源时间口径保持。规则、证据、历史存储和复现说明见[技术索引](docs/README.md)。

历次工程补充及旧测试数字可从[原工程记录的固定Git版本](https://github.com/LiRongji1995/stockfish-qlib-quant-system/blob/16d2fb810c7c921f55cd9b3f2f730627faec0d35/stock-fish/docs/archive/before-cleanup-20261005/stock-fish/ENGINEERING_BASELINE.md)追溯，不作为当前机器已通过的证明。

最新日采集与首次核心执行采用显式[有界入口V1](docs/FIRST_RUN_LATEST_CAPTURE_V1.md)：先核最新已闭市日缺口，只补原始所需请求；25日核心来源与67会话题材附件分阶段，UNKNOWN保留。`latest-plan/latest-sync`、`core-sources-plan/core-sources-build`和`core-first-run`均走已登记共享环境；新执行档位不改旧配置、策略、原件或已存run。首次计算与缓存复用分别验收，完整复权缺口仍须原来源补齐。
