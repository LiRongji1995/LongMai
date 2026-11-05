# 个人自用项目协作规则

本项目仅供个人本机股票研究、筛选、报告与后验。不连接券商、不下单，不建设 Web/API、多用户平台、模型平台或平行运行树。

- 先读 [README](README.md)、[使用说明](USER_GUIDE.md)、[部署维护](DEPLOYMENT.md) 和 [工程基线](ENGINEERING_BASELINE.md)。当前策略见 [CURRENT_STRATEGY](CURRENT_STRATEGY.md)，实际验证边界见 [CURRENT_STATUS](CURRENT_STATUS.md)。
- 日常只用 `stock-fish/scripts/codex_daily_v2.py`。沿用既有登记、固定 `STOCKFISH_CHAT_STATE`、scope、operations、数据库与数据路径；不能每个聊天另建运行树或账本。旧失败、SUBMITTING、不明请求和累计预算保留，换 key、版本或日期参数不能重试。
- 先计划再执行；优先复用已保存成果。普通研究不触发全市场采集、全历史回测、预算扩张或运行服务切换。只读状态不是数据就绪，UNKNOWN 不是 EMPTY，缺收益不是零收益。
- 保留 PV 已确认实验、主动 `STAGE_AWARE_MOMENTUM_V2`、冻结 `DAILY_V1` 和日常 R2 的现有公式、权重、阈值及排序。参数尚未验证，`MARKET_EDGE=NOT_ESTABLISHED`。策略改变须另行授权、登记版本，不覆写历史 run。
- 使用任何字段前，校验证券身份、业务日期、父来源实际可见时间及完整性。不得以历史下载冒充当时已经取得，标签不得泄漏到决策时间。
- 文件使用 SHA-256 与长度验证；旧 dev/inode/mtime/ctime 仅兼容元数据，不恢复跨运行设备身份门禁。并发锁、原子写和单次事务内对象一致性检查继续保留，详见 [内容校验](stock-fish/docs/SELF_USE_FILE_INTEGRITY_V1.md)。
- 保护真实 DB、RAW、run、报告、来源、失败记录、预算、凭据和备份。不提交它们，不清理未知或未跟踪文件。删除未被 Git 保存的唯一源码前，先建私有可恢复备份。
- 只保留必要流程依赖；已删除的平台、上游镜像、实验入口和旧文档从 Git 历史恢复，不因旧说明恢复当前复杂度。历史结果需要的是读取能力，不要求保留全部旧执行入口。
- 测试使用独立 DB/state/artifact 和显式 TEST_FIXTURE。保留正确性断言，不能降低覆盖、扩大 skip 或伪造数据。工程验证、真实数据验证和投资结论分别报告。
- 普通名单、报告、查询与跟踪不授权改生产代码、策略或提交发布。明确授权的工程任务完成检查后直接中文提交推送，不重复询问。
- 开发目录为本检出的 `stock-fish/`。发布检出为相邻 `../publish/stockfish-qlib-quant-system/`，发布前重新核对实际路径、远端、main、dirty 和差异。只同步本任务文件，保护既有修改；不整体覆盖、不盲目全量暂存、不新分支/worktree、不强推或改写历史。
- 发布目标固定为 https://github.com/LiRongji1995/stockfish-qlib-quant-system 的 main，推送后核对远端 SHA。源码发布不等于服务或真实登记自动采用；按实际行为报告。
- 路径取参数、环境或脚本位置，不硬编码私人路径。保留现有代码许可和 [第三方通知](THIRD_PARTY_NOTICES.md)。外部消息、收费服务和部署切换仍须明确授权。
