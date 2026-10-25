# Python业务维护入口

新增[共享结果、压缩及复权尾窗V2](docs/SHARED_RESULT_COMPACT_INCREMENTAL_V2.md)采用独立版本和TEST_FIXTURE检查；旧核心及来源文件保持。完整归档复用、新输出压缩与尾窗诊断分别验证，不因只读档位扩大原写入预算，不将尾窗升级为完整策略输入。

## Codex 共享入口增量

2026-10-25 的 [共享入口与容量管理](docs/CODEX_CHAT_WORKSPACE_V1.md)使用独立 scripts 层和显式 TEST_FIXTURE。旧 `codex_research.py`、core、策略配置及历史 run 保留；新入口登记既有运行环境，不复制数据库或自动修复依赖。检查范围、实测节省及失败记录见该说明，不视为全仓回归、真实盘前提速或投资验证。

统一维护约定见[根工程基线](../ENGINEERING_BASELINE.md)，部署与备份见[部署维护](../DEPLOYMENT.md)，现状见[当前状态](../CURRENT_STATUS.md)。

Python日常主动核心为V2，保存运行、冻结配置与来源时间口径保持。规则、证据、历史存储和复现说明见[技术索引](docs/README.md)。

历次工程补充及旧测试数字可从[原工程记录的固定Git版本](https://github.com/LiRongji1995/stockfish-qlib-quant-system/blob/16d2fb810c7c921f55cd9b3f2f730627faec0d35/stock-fish/docs/archive/before-cleanup-20261005/stock-fish/ENGINEERING_BASELINE.md)追溯，不作为当前机器已通过的证明。
