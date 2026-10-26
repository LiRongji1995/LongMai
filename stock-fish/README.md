# StockFish Python业务目录

已保存结果优先使用 [统一执行入口V1](docs/SYSTEM_EXECUTION_PIPELINE_V1.md) `python scripts/codex_daily.py show`；它转入原登记环境，约1秒核对并读取原成果，明示原日期和最后完整认证时间。`plan`查看依赖缺口，`audit`重新完整审计，计算命令保留原预算及来源终验。采集仍沿用原共享入口。

日常使用从[项目首页](../README.md)和[使用说明](../USER_GUIDE.md)进入；当前能力及未达标项目见[当前状态](../CURRENT_STATUS.md)，启动、停止与备份恢复见[部署维护](../DEPLOYMENT.md)。

现有Python Web/API与Codex薄调用封装使用同一保存运行。日常主动核心为V2，旧PV等实验保留独立身份；网页可选，用户可直接在本项目中提出研究请求。真实DB/state、配置和端口需当场核对，源码更新不代表已切换运行服务。

Codex执行先读[日常协议](docs/CODEX_DAILY_RESEARCH.md)；规则和模块说明见[项目综述](docs/PROJECT_OVERVIEW.md)，维护约定见[根工程基线](../ENGINEERING_BASELINE.md)。源码、数据与结果口径见[技术索引](docs/README.md)。

原R05操作手册可从[固定Git历史版本](https://github.com/LiRongji1995/stockfish-qlib-quant-system/blob/16d2fb810c7c921f55cd9b3f2f730627faec0d35/stock-fish/docs/archive/before-cleanup-20261005/stock-fish/README.md)追溯；各轮数字、机器路径和验收只适用于对应日期。上游英文说明[README_EN](README_EN.md)保留原语境。

最新日采集与首次核心执行采用显式[有界入口V1](docs/FIRST_RUN_LATEST_CAPTURE_V1.md)：先核最新已闭市日缺口，只补原始所需请求；25日核心来源与67会话题材附件分阶段，UNKNOWN保留。`latest-plan/latest-sync`、`core-sources-plan/core-sources-build`和`core-first-run`均走已登记共享环境；新执行档位不改旧配置、策略、原件或已存run。首次计算与缓存复用分别验收，完整复权缺口仍须原来源补齐。
