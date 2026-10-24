# StockFish

沪深主板股票研究工具，用于读取候选名单、解释筛选依据、跟踪后续表现。你可以直接在本项目的 Codex 对话里提出请求，网页和终端是其他入口。

本轮已完成独立运行基线、可信来源接库和显式900秒的单/双run后验工程闭环；原生默认300秒单/双run均真实超时，性能目标尚未达标。所有后验数值仍为UNKNOWN/null，参数尚未验证，MARKET_EDGE=NOT_ESTABLISHED。[当前状态](CURRENT_STATUS.md)列明可用范围与缺口，[交付蓝图](stock-fish/docs/CONTINUATION_DELIVERY_20261023.md)保留实际失败和下一步。

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
