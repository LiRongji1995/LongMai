# StockFish：Codex日常研究操作约定

本文件适用于此业务 Git 根目录及其子目录。当前任务中的用户指令优先；普通研究和工程修改分开授权。

- 日常先读 [执行协议](docs/CODEX_DAILY_RESEARCH.md)，用户入口见 [README](README.md)。[全面项目综述](docs/PROJECT_OVERVIEW.md) 供规则解释与新对话使用，不必每次全读。
- Codex是自然语言日常入口，网页可选。用户无需自己执行命令；已封存成果优先按[统一执行协议](docs/SYSTEM_EXECUTION_PIPELINE_V1.md)调用 `codex_daily.py show`，明示原日期、截止及最后完整认证时间；完整来源审计和新计算单独执行。采集与原作业跟踪继续调用[codex_chat.py](scripts/codex_chat.py)，按[共享协议](docs/CODEX_CHAT_WORKSPACE_V1.md)使用固定登记根和原请求标识；旧[codex_research.py](scripts/codex_research.py)保留原字节及历史兼容，不复制策略或后验算法。
- 市场限沪深主板普通A股，沿用日期化资格、ST/停牌/上市时长等证据门槛。日常PV是已确认的量价研究实验；DAILY_V1保持冻结五族主策略身份。不得用实验替代不可用的DAILY结论，不凑17/20/30只。
- 阶段感知主动核心为 `STAGE_AWARE_MOMENTUM_V2`，说明见 [STAGE_AWARE_MOMENTUM_V2.md](docs/STAGE_AWARE_MOMENTUM_V2.md)；`STAGE_AWARE_MOMENTUM_V1` 冻结为历史只读别名。先识别 `START/ACCELERATION/HIGH_LEVEL_REPAIR/TREND_PULLBACK`，再分离事实、setup、行为确认、市场/题材/资金情境、关键反证和最终 `FOCUS/WATCH/REJECTED/UNKNOWN` 决策；A/CLV 不作全阶段统一硬门槛，初始参数必须标明“尚未验证”，`MARKET_EDGE=NOT_ESTABLISHED`。
- 普通请求允许读代码/配置/结果、在现有来源和预算内采集、调用正式逻辑、保存名单/报告/后验。它不授权改策略公式、权重、窗口、资格、生产代码、依赖、交易口径、Git历史，也不自动授权提交推送。
- 工程交付只有在当前任务明确授权时提交推送；本次入口与综述任务已获授权，检查后直接中文提交。不得强推、切分支、建worktree、pull合并未知工作、reset/clean/rebase或全量盲目暂存。当前开发dirty是既存交付，不整体提交到开发远端。
- 每次先核对实际仓库、分支、HEAD、远端、运行路径和工作区变化。约定发布目标为 LiRongji1995/stockfish-qlib-quant-system 的 main；本机发布检出在上级 publish/stockfish-qlib-quant-system，实际远端仍须重新核验，仅同步本任务文件。
- “明天”按Asia/Shanghai及已有交易所日历解释，返回目标交易日、实际观察日、资料截止、覆盖、策略/版本、完整/焦点数量与run_id。日期旧、合法空、数据不全、未成熟和未运行分别报告。禁止混入合成输入。
- 详情、导出、Codex回复从同一保存运行派生；AI可解释并提出标明的独立意见，不改正式排序。旧候选用原名单及新后验，不用今日规则重算冒充昨日。缺失不补零、退出关注不删历史。
- 沿用用户当前模型；用户偏好的5.5或较低成本模型仅是可试用分工，不自动切换/升级、不申请第二套LLM API凭据、不编造费用。普通研究不重跑全仓测试或历史回放。
- 源码/规格矛盾先定向核对并说明，不擅自修策略。文档过时可修对应说明；经授权改变策略、关键配置、数据、存储、运行或协作边界时同步综述相关章节。没有自动综述更新服务。
- 保留真实.env、凭据、数据库、数据卷、缓存、原始行情、模型权重及研究产物的私有属性；不默认推送。禁止删除/覆盖数据库、数据卷和未知工作，不停止其他项目服务。保留许可和第三方通知，MiroFish不等于MIT组件。
- 检查与变更相称：文档检查事实/链接，封装做定向测试与真实保存结果烟测。工程测试、真实数据和投资有效性分别报告；不得复制旧测试数字为本次结果。
