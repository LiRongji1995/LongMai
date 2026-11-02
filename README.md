# StockFish

新增[SDK 子进程树监督 V2 与本轮盘前验收记录](stock-fish/docs/SDK_TREE_SUPERVISED_CURRENT_PREMARKET_V2.md)：监督与回收采用版本化修复，保留实际执行的 V1 原件。11 月 2 日固定请求已补齐当前日历、原主板范围 3,194 只/64 批完整复权并生成核心产物，但后续流程因采样超时与 UTC 总限时失败，根进程当时的回收未获确认；10 项题材 PARTIAL 保留，完整盘前尚未通过。V2 仅完成隔离工程验收，未重跑真实业务；原三小时预算、登记、策略、历史结果与全部失败记录保持，未切换服务。

新增[原失败请求显式复权恢复 V1](stock-fish/docs/EXPLICIT_FACTOR_FAILURE_RECOVERY_V1.md)：28→29 原主板范围 3,194 只/64 批来源已补齐，复用 800 只、真实补采 2,394 只；194 项工程检查和独立原生回查通过。沿用原登记与累计预算，旧失败、策略和历史结果保留。新首见时间不追认历史 PIT；SDK 子树 RSS 监督与最新完整盘前交付仍待完成，边界见记录。

**2026-10-30 完整复权工程V3：**新增[完整窗口采集、真实首见时间与原请求接线](stock-fish/docs/FACTOR_FULL_WINDOW_CHECKPOINT_V3.md)。新源码副本在每证券完整接收后持久化检查点，超时保留原钟和PARTIAL；显式入口沿用原恢复登记、防重及预算。旧策略、历史原件和失败保持，本轮尚未执行全市场补采。

新增[P0交付与研究诊断](stock-fish/docs/P0_DAILY_RELIABILITY_AND_EVALUATION_V1.md)：通过 `scripts/codex_p0.py` 只读核对原请求终态、完整R2全池及后验覆盖，并列出执行评价缺口。原策略、历史结果、original默认入口和运行服务保持；首批工程不等于连续每日交付或投资优势已验证。

**2026-10-29 空间实测：**已将一份失败题材日志完整归档并验证恢复，470.70 MiB 的原日志改为约 44.52 MiB 的归档及必要记录；四个管理目录扣除本轮审计和测试后净减少约 404 MiB（25.6%）。原行情、核心及失败状态保持，日常重复写入的持续增长仍待处理。见[实际冷存与计量](stock-fish/docs/FAILED_THEME_COLD_STORAGE_V1.md)。

**2026-10-28 恢复现状：**原 daily/review 环境已重新验收，137 份旧采集元数据已按真实原件重认，并以显式恢复登记运行 original 全流程；旧 default 与失败记录保留。26→27 已完成完整复权和原核心，后续题材阶段触全流程限时；28→29 在复权采集遇到部分失败和缺原始首见时间，与合盖休眠重叠，尚无完整清单。真实提速比例仍未测得。详见[环境恢复、真实执行与性能边界](stock-fish/docs/PREMARKET_ENVIRONMENT_RECOVERY_V1.md)。

默认 `codex_daily_v2.py premarket` 保留原题材执行器；`--engine shared` 是显式共享计算候选。小样本全流程曾测得回退，因此没有将其自动设为默认，也没有据此宣称真实新清单提速。

新盘前生成先用 [完整盘前准入与共享执行](stock-fish/docs/PREMARKET_SHARED_EXECUTION_V1.md) 的 `codex_daily_v2.py premarket-plan` 核对全流程数据与预算；准入通过后使用同入口 `premarket`。新路径实际连接采集、原核心、共享题材、展示和终验。真实跨日数据未齐时会在采集前阻塞；不能把已保存名单的约0.8秒读取当作新名单生成速度。

日常已有成果统一从 [执行入口V1](stock-fish/docs/SYSTEM_EXECUTION_PIPELINE_V1.md) 读取：`codex_daily.py show` 核对本地封签和五份原导出，明确显示原日期与最后完整认证时间；`plan` 说明哪些可复用、哪些缺资料，新计算保留完整来源认证。真实三次摘要读取为0.93／0.79／0.79秒，首次完整认证并封存72.01秒；仅增加5.4 KiB视图，不复制原输入。原作业和分阶段采集继续复用已登记的 [共享聊天入口](stock-fish/docs/CODEX_CHAT_WORKSPACE_V1.md)，不另建数据库或环境。

[第二阶段：共享环境恢复与增量研究](stock-fish/docs/SHARED_RUNTIME_INCREMENTAL_V1.md)已恢复并验收本机固定的 daily/review 58 包环境，登记为聊天默认实例 `shared-daily`。运行位置和环境验收不代表最新行情或盘前清单已就绪；有界行情增量、相同来源的核心计算复用及真实小样本结果按第二阶段说明分别报告。第一阶段隔离样本事实保留，生产服务未切换，策略保持。

沪深主板股票研究工具，用于读取候选名单、解释筛选依据、跟踪后续表现。你可以直接在本项目的 Codex 对话里提出请求，网页和终端是其他入口。

新增日期化身份输入已完成真实单／双 run 后验、幂等、三产品回查及独立数值 H1：15→16 的原全池 3,041 只、观察集合 25 只均可逐项复算；19 日及后续身份缺口仍保留 UNKNOWN。默认 300 秒性能尚未达标，生产服务未切换，参数尚未验证，MARKET_EDGE=NOT_ESTABLISHED。[当前状态](CURRENT_STATUS.md)和[本轮验收与蓝图](stock-fish/docs/DATED_IDENTITY_NUMERIC_H1_20261024.md)分别记录数值、工程检查、性能试验与剩余缺口。

## 从这里开始

| 你想了解什么 | 看哪份说明 |
|---|---|
| 每天怎么用、怎么查看名单与复盘 | [使用说明](USER_GUIDE.md) |
| 安装、启动、停止、备份和恢复 | [部署维护](DEPLOYMENT.md) |
| 哪些已经可用、哪些未达标、下一步做什么 | [当前状态与待办](CURRENT_STATUS.md) |
| 当前选股思路、资料要求和旧策略的关系 | [当前策略](CURRENT_STRATEGY.md) |
| 独立盘中市场研究、分钟回放、预测与 Dashboard | [StockFish Live](stockfish-live/README.md) |

先查看保存结果的观察日、资料截止和覆盖范围。旧名单可作历史研究，缺资料不代表没有机会，程序成功也不代表股票值得买。

需要脱离Codex执行本轮R2盘前研究，可在项目Python环境、`stock-fish/`目录运行 `python scripts/stockfish.py premarket --theme-read-gib 12 --max-requests 768 --max-seconds 0`。它本地协调采集、原策略、67日题材及3亿元展示，不要求Web服务或LLM；失败和未知分别保存。预算、缓存、导出与验收范围见[独立终端入口](stock-fish/docs/STANDALONE_PREMARKET_WORKFLOW_V1.md)。

开发维护按[工程约定](ENGINEERING_BASELINE.md)和[AGENTS](AGENTS.md)执行；深入资料见[技术索引](stock-fish/docs/README.md)，过去各轮记录见[历史归档](stock-fish/docs/archive/README.md)。

真实配置、密钥、数据库、行情、模型和本机报告保留在私有运行目录。版权与许可见[第三方通知](THIRD_PARTY_NOTICES.md)：StockFish/Qlib 保留各自许可，MiroFish 适用 AGPL-3.0，整个仓库不称为纯 MIT。

最新日采集与首次核心执行采用显式[有界入口V1](stock-fish/docs/FIRST_RUN_LATEST_CAPTURE_V1.md)：先核最新已闭市日缺口，只补原始所需请求；25日核心来源与67会话题材附件分阶段，UNKNOWN保留。`latest-plan/latest-sync`、`core-sources-plan/core-sources-build`和`core-first-run`均走已登记共享环境；新执行档位不改旧配置、策略、原件或已存run。首次计算与缓存复用分别验收，完整复权缺口仍须原来源补齐。
