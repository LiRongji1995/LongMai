# StockFish Python业务目录

新增[架构、交付与量化评价优化V1](docs/ARCHITECTURE_OPTIMIZATION_DELIVERY_V1.md)：提供内外层原生监督V3、统一运行/交付/覆盖审阅、冻结研究与条件评价、显式组件清单、存储实验、根级CI和新目录恢复演练。旧策略/核心及默认original入口保持。当前环境七步复验通过，但原登记物理身份变化仍阻塞真实准入；最新完整盘前、连续20交易日、真实执行证据和独立投资验证未通过，源码发布不切换服务或重试旧请求。

默认 `codex_daily_v2.py premarket` 保留原题材执行器；`--engine shared` 是显式共享计算候选。小样本全流程曾测得回退，因此没有将其自动设为默认，也没有据此宣称真实新清单提速。

新盘前研究先执行 [完整盘前准入与共享执行](docs/PREMARKET_SHARED_EXECUTION_V1.md) 的 `scripts/codex_daily_v2.py premarket-plan`，通过后用同入口 `premarket`，沿用固定运行登记、原配置与request-key。保存成果继续由原 `codex_daily.py show` 读取。

已保存结果优先使用 [统一执行入口V1](docs/SYSTEM_EXECUTION_PIPELINE_V1.md) `python scripts/codex_daily.py show`；它转入原登记环境，约1秒核对并读取原成果，明示原日期和最后完整认证时间。`plan`查看依赖缺口，`audit`重新完整审计，计算命令保留原预算及来源终验。采集仍沿用原共享入口。

日常使用从[项目首页](../README.md)和[使用说明](../USER_GUIDE.md)进入；当前能力及未达标项目见[当前状态](../CURRENT_STATUS.md)，启动、停止与备份恢复见[部署维护](../DEPLOYMENT.md)。

现有Python Web/API与Codex薄调用封装使用同一保存运行。日常主动核心为V2，旧PV等实验保留独立身份；网页可选，用户可直接在本项目中提出研究请求。真实DB/state、配置和端口需当场核对，源码更新不代表已切换运行服务。

Codex执行先读[日常协议](docs/CODEX_DAILY_RESEARCH.md)；规则和模块说明见[项目综述](docs/PROJECT_OVERVIEW.md)，维护约定见[根工程基线](../ENGINEERING_BASELINE.md)。源码、数据与结果口径见[技术索引](docs/README.md)。

原R05操作手册可从[固定Git历史版本](https://github.com/LiRongji1995/stockfish-qlib-quant-system/blob/16d2fb810c7c921f55cd9b3f2f730627faec0d35/stock-fish/docs/archive/before-cleanup-20261005/stock-fish/README.md)追溯；各轮数字、机器路径和验收只适用于对应日期。上游英文说明[README_EN](README_EN.md)保留原语境。

最新日采集与首次核心执行采用显式[有界入口V1](docs/FIRST_RUN_LATEST_CAPTURE_V1.md)：先核最新已闭市日缺口，只补原始所需请求；25日核心来源与67会话题材附件分阶段，UNKNOWN保留。`latest-plan/latest-sync`、`core-sources-plan/core-sources-build`和`core-first-run`均走已登记共享环境；新执行档位不改旧配置、策略、原件或已存run。首次计算与缓存复用分别验收，完整复权缺口仍须原来源补齐。
