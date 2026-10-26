# 项目协作说明

- 2026-09-07 权限修订：普通名单、日报、查询和跟踪请求不自动授权修改生产代码、正式策略或提交推送。当前任务明确授权的工程交付，完成对应检查后直接中文提交推送，不重复询问；本次Codex日常入口与项目综述任务已获授权。无文件改动不创建空提交。
- 发布目标固定为 https://github.com/LiRongji1995/stockfish-qlib-quant-system 的 main 分支。本机业务开发目录是 stock-fish/，发布检出目录是 publish/stockfish-qlib-quant-system/。发布前核对实际远端与工作区变化，只提交本次授权范围内的改动，保护用户其他修改。
- 各任务较新的明确授权优先；不得把本次工程交付授权扩大为以后普通研究的自动提交权限。
- 当前文档入口以 README、USER_GUIDE、CURRENT_STATUS、CURRENT_STRATEGY 和 DEPLOYMENT 为准；各轮记录见历史归档。日常先读 [执行协议](stock-fish/docs/CODEX_DAILY_RESEARCH.md)，用户入口见 [README](README.md)，按需展开 [全面综述](stock-fish/docs/PROJECT_OVERVIEW.md)。无需用户打开网页；Codex调用现有逻辑，优先复用保存结果。不能用较早R03/R04历史记录推断当前数据就绪。
- 默认规则入口不以账户、LLM、旧 Alpha158 或下载模型为前提。保留五族公式、权重、阈值及初始假设标记；修改语义必须登记新配置/特征版本，不覆写旧 run。
- 当前阶段感知主动核心为 `STAGE_AWARE_MOMENTUM_V2`，说明见 [stock-fish/docs/STAGE_AWARE_MOMENTUM_V2.md](stock-fish/docs/STAGE_AWARE_MOMENTUM_V2.md)；`STAGE_AWARE_MOMENTUM_V1` 冻结为历史只读兼容和审计材料。V2 保留旧策略对照，先分离事实、setup、行为确认、市场/题材/资金情境和关键反证，再给出 `FOCUS/WATCH/REJECTED/UNKNOWN`；A/CLV 不作所有阶段统一硬门槛，初始参数必须标为“尚未验证”，`MARKET_EDGE=NOT_ESTABLISHED`。
- 先校验证券身份、业务日期和父来源实际可见时间，再使用价格、主题、资金或风险字段。历史下载不能伪装成过去已经取得；未知事件不得当 FALSE，缺结果不计零收益。
- RAW 行情、限价事件、历史资格和主题完整性分别记录。采集完成、作业成功、health 正常都不等于 DAILY_V1 就绪；未知不能伪装成合法 EMPTY。
- stock-fish/ 与 qlib/ 保留上游源码及本项目修改。运行采用项目虚拟环境中的 pyqlib 0.9.7 wheel；未经验证不改为相邻 Qlib main 的 editable install。旧模型维持研究用途和原 hash。
- 路径通过环境变量或管理脚本 -Root 传入，根目录默认使用脚本位置。不要硬编码个人目录、代理或本机私有路径；运行数据与源码发布目录分开。
- 不提交真实 .env、凭据、私人数据库、日志、缓存、虚拟环境、模型权重、全市场数据或完整本机审阅包。保留许可与可分享的配置模板。
- 先读当前 DEPLOYMENT.md、ENGINEERING_BASELINE.md 和相关项目说明。检查与改动相称，真实数据、工程测试和投资验证分别报告；不得为了通过检查伪造数据或削弱正确断言。
- 提交和推送不授权强制推送、破坏性 Git 清理、覆盖用户数据或自动切换运行服务。推送完成后核对远端提交并告知结果；确有失败或阻塞时如实报告。
- 测试使用独立 DB/state/artifact 和显式 TEST_FIXTURE，不混入真实历史。保留原账户/模拟执行安全断言；不靠降低覆盖门槛、删除失败样本或扩大 skip 获得通过。报告实际命令、退出码和未执行范围。
- 系统不连接券商、不下单。外部消息、收费服务和部署切换仍按用户明确授权执行。
- 保留上游版权、各目录许可证和根 THIRD_PARTY_NOTICES.md。MiroFish 适用 AGPL-3.0，不把整个仓库称为纯 MIT。

## 日常研究协议

- 日常量价请求沿用已确认的PV实验上下文，DAILY_V1仍是冻结主策略并单独报告可用性。观察日、资料截止、范围、全部/焦点数量与run_id必须可追溯；不把陈旧数据冒充最新、不凑数量、不混合成数据。
- 查询原run、详情、正式导出及后验；Codex解释与未知另列。已封存成果优先按[统一执行协议](stock-fish/docs/SYSTEM_EXECUTION_PIPELINE_V1.md)调用 `codex_daily.py show`，保留原日期和最后完整认证时间，不把本地成果完整性当作来源本次重认；首次封存及新计算仍完整认证。普通报告不触发全历史回测，只有缺新数据且已有授权/预算时才续采。采集和原作业跟踪继续使用[共享聊天入口](stock-fish/scripts/codex_chat.py)，按[共享协议](stock-fish/docs/CODEX_CHAT_WORKSPACE_V1.md)复用固定登记根、原运行环境和同一请求标识；旧[薄封装](stock-fish/scripts/codex_research.py)保留离线研究与历史兼容，不引入平行名单或数据库。
- 用户当前模型保持不变；较低成本模型仅是初始尝试建议，不自动升级、购买服务、扩大预算或要求应用内LLM凭据。普通研究可保存私有报告；提交研究数据另查许可与敏感性。
- 经授权改变策略、关键配置、来源、存储、运行方式或协作边界时，同步维护综述相关章节；日常报告写各自研究产物。没有自动更新综述进程，也未通过本文件声明验证新的模型会话自动加载。
- 发布前检查实际远端、分支、dirty和文件差异，仅同步本任务文件；禁止整体覆盖开发目录、盲目全量暂存、新分支/worktree及改写历史。工程检查、真实数据验证与投资结论分别报告。
