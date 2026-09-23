# StockFish + Qlib Quant System

## Phase 1 每日荐股闭环（2026-09-23）

【事实】本轮源码增加同一原 run 的四类冻结快照、版本身份、证据解释、T+1..T+20 价格观察与日报出口。使用 `/daily-research` 页面或 Codex 的 `daily-report` / `daily-outcomes` 入口；主动策略仍为 Python `STAGE_AWARE_MOMENTUM_V2`，Go 仍是影子验证。完整口径见 [Phase 1 每日荐股闭环](stock-fish/docs/PHASE1_DAILY_RESEARCH_LOOP.md)。

【需要验证】源码交付不代表已切换生产服务或完成真实 20 会话验证；不强制荐股数量、不修改策略或自动交易，`MARKET_EDGE=NOT_ESTABLISHED`。以下旧版本与验收内容按各自日期保留。

## 直接在 Codex 里使用

在本项目的 Codex 对话中说“给我下一交易日完整研究名单”“读取昨天名单并继续跟踪”或“写一份限定数据范围的短线报告”。Codex 使用现有保存记录与产品 API；用户无需手动运行命令，网页仍是可选入口。

- [主板研究 README](stock-fish/README.md)：日常示例、启动停止、数据缺失和模型边界。
- [Codex 日常研究协议](stock-fish/docs/CODEX_DAILY_RESEARCH.md) 与 [项目 Agent 指引](AGENTS.md)：自然语言请求、持久化及权限。
- [新版板块资金趋势说明](stock-fish/docs/THEME_CAPITAL_TREND_V1.md)：`THEME_CAPITAL_TREND_V1` 的硬性要求、尚未验证的初始参数、首轮真实输入审计与国芳/金健路径。
- [全面中文项目综述](stock-fish/docs/PROJECT_OVERVIEW.md)：自包含背景、数据、全部策略与公式、跟踪口径、当前证据和新对话提示词。
- [首用验证记录](stock-fish/docs/CODEX_FIRST_USE_VERIFICATION.md)：本次实际读取、导出、报告与相关测试结果。

普通研究不自动授权修改生产代码、策略或 Git 提交推送。报告和完整导出在本机 `stock-fish/reports/codex/`，数据库与运行数据不随仓库发布。新版板块资金趋势策略已独立版本化；旧 PV/焦点/DAILY 结果保留，初始参数标注 **尚未验证**。历史文档核对基础为 2026-09-07、发布 main 的 `bff8859`（运行代码同 `d88f1f0`）；当前交付提交以 Git 历史为准。

下文保留较早 R03 的流程与验收记录；当前能力、实际本机实例、数据限制和使用方式以以上 R05 手册与首用验证为准。

面向 Windows 的沪深主板 A 股候选研究系统。默认 Web/API 使用确定性规则发现、排序、解释与跟踪强势股，提供首板后 1进2、2进3、高标接力、N天M板和断板恢复五类观察。无需现金、持仓、LLM 或旧 Alpha158 推理；系统不连接券商、不自动下单。

**R03 真实验收仍为 UNAVAILABLE，尚不能显示完整真实 DAILY_V1 候选或证明合法 EMPTY。** 全主板采集、保存、默认 UI/API、导出和重启已实际接通；必要历史身份和限价事实不足时，页面展示真实覆盖与缺口，不生成示例榜、不建立虚拟账户。详见 [R03 发布说明](docs/r03-release.md)。

## 默认研究流程

历史证券资格 → 全池原始行情与结构历史 → 来源、时点和覆盖核验 → 涨停事件、量价、主题与情绪 → 五类规则 → 不可变 CandidateRun → 页面、历史、竞价复核与交易日观察。

- 策略版本为 YAOGU_V1.1.0，特征版本为 YAOGU_FEATURES_V1_R03。配置保存新版本，旧榜不覆盖。
- 六分项是板结构、量价、板块共振、板块相对强度、历史股性、市场情绪；分数表示规则匹配度，不是涨停概率或胜率。
- 首页最多显示 20 只观察对象、5 只重点对象，按五类轮询展示；这是展示数量，不是组合仓位或跨族成功概率排名。
- 资金增强使用独立 FUNDS_V1 口径；来源、窗口和全族覆盖不足时不发布资金排行。
- 缺真实 09:25 撮合数据时竞价复核不可用。未成熟或缺失后验结果为 NULL，不计作零收益。

批准公式与边界见 [策略规格](stock-fish/STRATEGY_SPEC_V1.md)。旧组合模拟有独立账户约束；可选 Qlib/Alpha158 保留研究对照用途，不决定默认规则候选。

## 安装与运行

需要 Windows x64、Python 3.12、PowerShell 7 和 Git。按 [部署指南](DEPLOYMENT.md) 建立项目虚拟环境与本地配置；默认规则入口不要求先下载模型或 Qlib binary 行情。

~~~powershell
git clone https://github.com/LiRongji1995/stockfish-qlib-quant-system.git
Set-Location ./stockfish-qlib-quant-system
~~~

配置完成后，从仓库根目录运行：

~~~powershell
pwsh -NoProfile -File ./Start-StockFish.ps1
pwsh -NoProfile -File ./Logs-StockFish.ps1 -Follow
pwsh -NoProfile -File ./Stop-StockFish.ps1
~~~

打开 [本地页面](http://127.0.0.1:8000/)，点击“采集 / 续采全主板数据”。前后端共用 Flask 服务，无需 Node 构建或外部数据库。三个脚本支持可移植的 -Root，默认值为脚本所在目录。新检出不会迁移、覆盖或重启已有部署；同一 SQLite 数据库只运行一个任务服务，默认端口为 8000。

健康检查表示应用、持久内容和任务服务状态；行情覆盖与策略就绪由 /api/universe/coverage 单独报告。服务停止时不会继续采集，错过时点不倒填假快照。

## 项目结构

~~~text
stock-fish/stockfish_core/yaogu/  规则、证据、采集、持久候选与观察
stock-fish/config/               版本化策略初始配置
stock-fish/tests/                隔离回归测试
stock-fish/STRATEGY_SPEC_V1.md    策略公式与语义
qlib/                           Microsoft Qlib 源码参考
Start-StockFish.ps1              启动及健康检查
Stop-StockFish.ps1               核验进程身份后停止
Logs-StockFish.ps1               本地日志
docs/r03-release.md              R03 变更、验证与真实阻塞摘要
docs/verification/              早期 CSI300 推理历史记录
~~~

R03 原工作环境最终业务测试为 **651 passed、0 failed、0 skipped**，单独打包测试为 23 passed；它们不证明市场优势。真实完整历史投资验证、竞价采集和未来成熟样本尚未完成，MARKET_EDGE=NOT_ESTABLISHED。

早期 [CSI300 执行清单](docs/verification/inference-execution.json) 和模型排名保留历史语境，不是 R03 主板候选，也不表示旧模型已通过样本外验证。运行时采用 pyqlib 0.9.7 wheel，不自动安装相邻 Qlib main。

## 许可与数据

保留各上游版权、许可证与 [第三方通知](THIRD_PARTY_NOTICES.md)。StockFish 和 Qlib 的主要源码采用 MIT，嵌入 MiroFish 组件适用 AGPL-3.0；不能把整个整合仓库声明为纯 MIT。

真实 .env、凭据、私人数据库/日志、虚拟环境、模型权重、完整行情及本机审阅包不公开。SDK 开源不代表底层行情可以任意再分发；数据和模型遵循原来源使用条件。
