# StockFish

新增 [Codex 共享聊天入口与容量管理](stock-fish/docs/CODEX_CHAT_WORKSPACE_V1.md)：不同聊天复用同一运行目录、原作业和正式导出；重复请求不自动重采、重算或复制数据库。写入前检查容量，状态不明时停止重复提交。

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
