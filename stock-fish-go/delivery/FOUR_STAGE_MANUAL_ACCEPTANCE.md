# 四阶段候选包人工验收：NOT_RUN_BY_USER

构建完成不等于运行验收通过。先读取包内 SELFTEST_REPORT.md 的当前状态与缺口，核对 BUILD.json、stockfish.identity.json 和 checksums.sha256。不要把原有日常包的测试成绩套用到新包。

所有路径由操作者明确指定；只读步骤使用已有已发布数据库。真实研究、写复盘记录和在线刷新各需原任务额度，不能由查询步骤自动触发。生产服务、正式规则和旧结果保持原状态。

1. 用新进程执行 `./stockfish --version` 和 `./stockfish --help`，核对完整源码、业务实现及事实实现身份；验证包内校验和。
2. 使用新配置执行 `doctor`、`daily --dry-run`。未知旧实现应提示显式历史查询；缺配置或资料应清晰阻塞。
3. 对明确保存 run 分别执行 `picks --run-id RUN`、`explain SECURITY --run-id RUN`，检查观察日、cutoff、覆盖、全部命中形态和未知原因。默认 V2 要核对保存的主形态、其他选择及每形态状态，不能将 UNKNOWN 当 PASS；未选中的 PASS 不追加为命中，选择资料有缺口且无确认命中时输出未知，不能当合法空集合。
4. 分别导出 table、JSON、CSV、Markdown 到新文件，核对精确值及完整数量。对两个明确保存 run 执行 `diff --from-run-id A --to-run-id B`；多项变化不得给唯一因果结论。
5. 退出后新进程重复读取同一 run，确认没有新增 job、run、cache 或 schema。server 在已收到有效提交响应后的等待中断应保留已知 job_id；提交响应丢失不保证客户端取得 ID。只有明确 `--cancel` 才请求管理员取消。
6. 在独立、明确 TEST_FIXTURE 数据库完成 `review init`、`review register --run RUN`、`review evaluate --run RUN --label-input FOLLOWUP_INPUT`、重启 `review list/summary/export`。标签必须来自已登记合法后续输入，不能上传价格断言绕开来源校验。核对重复登记/评价幂等、资料修订追加历史、未成熟和缺失为 null、全部 1/3/5/10 会话及已知资格排除/资格未知/缺席身份的独立分母。合格股票收益对照不能纳入资格排除。历史回放明确显示假设可用时点和实际发布时间未知，不计入真实前瞻成绩。真实库不得混入 fixture。
7. 在同一 fixture journal 用 `research-lab propose --file FILE` 登记一个基线和最多两个候选；固定合法实现/配置/calendar/合同 SHA256、`time_split_contract=GO_PURGED_FORWARD_EVALUATION_V2`、主评价窗口及 `stability_contract=GO_INDEPENDENT_HOLDOUT_V1`、holdout 起止。基线与候选 holdout/calendar 相同，主窗口与 holdout 至少隔离 10 个 calendar 会话。运行 `compare --key PRIMARY_ATTEMPT` 和 `stability --key HOLDOUT_ATTEMPT`；旧无 holdout 声明、修改声明、未来/倒退 as-of、混 input、未成熟/缺标签、样本/日期/时间块不足、窗口污染和历史最佳重选应拒绝或报告不足，并保存失败尝试。真实最低门槛不能被 fixture 门槛降低。
8. 仅在两份最新比较均满足固定合同后，显式执行 OFFLINE_EVALUATED → REVIEW_REQUIRED（`--attempt PRIMARY_ATTEMPT`）→ SHADOW_ELIGIBLE → SHADOW_OBSERVING（`--attempt HOLDOUT_ATTEMPT`）。核对影子事件绑定主/holdout 记录 ID/hash，job 数量及原清单保持不变，production_promotion=DISABLED。新增失败或新比较后旧影子准入撤销；后续成功不得自动恢复旧观察事件，重新进入需完整显式状态链。REJECTED 后重启仍保留原预测、标签修订、全部失败尝试和状态历史；不得晋级生产。
9. 核对显式 `review init` 新增 GO_CANDIDATE_AVAILABILITY_V1 时不改旧 JOURNAL_V1 字节；旧库只读缺扩展不会初始化。真实 `propose` 只有声明提交后的新读事务完成验证所观察到的可用时间上界可用于证明，不接受上传时间或用 `registered_at` 代替。旧无 proof、开盘后补 proof 仍阻塞；proof 写失败显示 PARTIAL_DECLARATION_SAVED/非零，同声明 retry 幂等且不能倒填早时间。复核 schema 部分/额外成员、proof 丢失/多余成员/hash 损坏/ledger 缺项均被拒绝，schema、内容和 ledger 使用同一事务快照。此项应使用独立工程 fixture 验证实现，不能为演示往真实库写虚构证明。

以上描述验收操作和期望合同，不表示操作者或工程测试已执行成功；**人工验收状态仍为 NOT_RUN_BY_USER**。fixture 结果只能报告 TEST_FIXTURE 工程状态，运行 PASS 需引用本轮实际命令、退出码和报告。在线历史桥、跨日递推、完整旧库增量、真实性能和市场优势分别以当前报告为准；没有验证结果的项目保持 NOT_RUN / BLOCKED。输入分布诊断目前为 NOT_EVALUATED。操作者尚未亲自执行本清单，不替用户标 PASS，也不自动安装或激活生产服务。
