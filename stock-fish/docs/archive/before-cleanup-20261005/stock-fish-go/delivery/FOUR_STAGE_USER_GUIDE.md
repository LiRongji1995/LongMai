> 历史归档：保留当时的说明、数字、失败和限制，仅调整文档相对链接。当前使用请看[当前状态](../../../../../../CURRENT_STATUS.md)；原路径可在[归档索引](../../../README.md)查找。

# 四阶段候选源码使用说明

构建、测试和包身份以本轮 SELFTEST_REPORT.md、BUILD.json 与校验和的实际记录为准。本文说明当前候选源码的命令和合同，不预先声明运行 PASS；用户人工验收仍为 **NOT_RUN_BY_USER**。当前已保存旧 run 可使用原已验收包显式只读查询；旧包不包含新的 review/research-lab/diff。

用自己的明确路径替换 CONFIG、DB、RUN、FOLLOWUP_INPUT。init 只创建新配置，不创建业务库；不会覆盖配置。系统默认 V2；R1 必须明确指定。

```sh
stockfish --help
stockfish --version
stockfish init --config CONFIG --db DB --artifacts ARTIFACTS --strategy r1
stockfish doctor --config CONFIG --format json
stockfish picks --config CONFIG --run-id RUN --format table
stockfish explain 600000.SH --config CONFIG --run-id RUN --format json
stockfish explain 600000.SH --config CONFIG --run-id RUN --format markdown
stockfish diff --config CONFIG --from-run-id RUN_A --to-run-id RUN_B --format json
stockfish daily --config CONFIG --strategy r1 --dry-run --format json
```

picks/explain/diff、doctor、daily --dry-run 只读；不联网、不研究、不初始化 schema。省略 run-id 的默认结果需满足本轮业务兼容身份，未知旧实现会拒绝自动复用，须显式读取历史。中文解释严格使用保存条件树；JSON 保留原数值和明细。完整明细超限失败，不会静默截取后宣称完整导出。

```sh
stockfish review --help
stockfish review list --db DB --kind OUTCOME --format json
stockfish review summary --db DB --run RUN --as-of RFC3339 --format json
stockfish review export --db DB --run RUN --output NEW_FILE --format csv
stockfish research-lab --help
stockfish research-lab list --db DB --format json
stockfish research-lab diagnose --db DB --run RUN --as-of RFC3339
```

这些 review/research-lab 查询只读，要求已存在 GO_REVIEW_JOURNAL_V1。旧库缺少可用时间证明扩展时，读取不会自动初始化或补证明。有写入权限并完成候选包检查后，以下操作会写 SQLite V2，均不联网；标签只能来自已登记的合法冻结输入。`review init` 是显式初始化/升级入口，保留旧 journal schema 与记录字节，新增独立 GO_CANDIDATE_AVAILABILITY_V1 扩展。

```sh
stockfish review init --db DB
stockfish review register --db DB --run RUN
stockfish review evaluate --db DB --run RUN --label-input FOLLOWUP_INPUT
stockfish research-lab propose --db DB --file PREDECLARED_CANDIDATE.json
stockfish research-lab compare --db DB --candidate CANDIDATE --baseline BASELINE --key NEW_ATTEMPT --as-of RFC3339
stockfish research-lab stability --db DB --candidate CANDIDATE --baseline BASELINE --key NEW_HOLDOUT_ATTEMPT --as-of RFC3339
stockfish research-lab transition --db DB --candidate CANDIDATE --to OFFLINE_EVALUATED --attempt NEW_ATTEMPT --reason REASON
stockfish research-lab transition --db DB --candidate CANDIDATE --to REVIEW_REQUIRED --attempt NEW_ATTEMPT --reason REASON
stockfish research-lab transition --db DB --candidate CANDIDATE --to SHADOW_ELIGIBLE --attempt NEW_HOLDOUT_ATTEMPT --reason REASON
stockfish research-lab transition --db DB --candidate CANDIDATE --to SHADOW_OBSERVING --attempt NEW_HOLDOUT_ATTEMPT --reason REASON
stockfish research-lab transition --db DB --candidate CANDIDATE --to REJECTED --reason REASON
```

评价合同 GO_PRICE_REVIEW_V1 固定展示 1/3/5/10 个交易会话，以第 5 会话为主指标。参考价为冻结观察日可比收盘价，完整合格池中位数为对照；0/10/30 bps 是假设成本扣减，不代表成交、滑点模型或账户收益。停牌/未知会话、不可比价格、缺资料和未成熟标签保留原因与 null；资料修订追加 outcome，不回写原决策。真实合同最低要求 20 个有效样本、5 个观察日、3 个不重叠时间块；显式 fixture 合同可以降低工程演示样本要求，但不冒充真实证据。同日与重复股票的依赖仍明确保留，不输出 IID 置信度、个股成功概率或无合同的组合指标。

信号记录分别保留原 run 创建时间、原截止时间、实际发布时间和验证后观察到的可用时间上界。实际发布时间未知时不补写旧 manifest；历史回放以原 cutoff 为假设可用时点，标为 HISTORICAL_REPLAY，不能计入候选真实前瞻比较。已知资格排除、资格未知和缺席身份分母分别输出；未知不当已知排除，资格排除不混入合格股票收益对照。

候选 JSON 必须声明角色、父基线、假设、独立版本变化、范围，及与保存信号相符的实现/配置 SHA256；calendar input、calendar SHA256、评价合同版本/SHA256、允许资料 cutoff、选择期和主评价起止也必须固定。主指标为 `PRIMARY_WINDOW_EXCESS_MEDIAN`，探索指标固定为 `ALL_FIXED_WINDOWS`、`NEGATIVE_TAIL`、`FIXED_COST_SENSITIVITY`；`time_split_contract` 为 GO_PURGED_FORWARD_EVALUATION_V2，`production_promotion` 为 DISABLED。每个基线最多两个研究候选。声明的实现、配置、calendar 和合同身份必须是真实登记的 64 位 SHA256，不能使用任意文字或把未登记旧身份变为可信候选。

真实声明需在首评价会话上海 09:30 前完成保存和提交后证明。Store 在声明提交后，用新只读事务验证 schema、完整记录和 ledger，完成读取后取得保守时间上界，再将绑定声明 hash 的证明耐久追加。CLI 不接受手动证明时间；`registered_at` 本身不证明落盘时间。证明提交失败时输出 `PARTIAL_DECLARATION_SAVED` 并返回非零；相同声明可幂等重试补缺证明，但晚于开盘的观测只能保存为真实晚证据并阻塞准入。旧无 proof 真实候选继续 `PREREGISTRATION_PUBLICATION_UNPROVEN`；初始化不倒填旧证明。TEST_FIXTURE 明确标注不是真实预登记证明。

要申请研究影子状态，基线和候选必须在原声明中同时固定 `stability_contract: "GO_INDEPENDENT_HOLDOUT_V1"`、`stability_start`、`stability_end`。两者 holdout 和 calendar 身份必须一致；选择期与主窗口、主窗口与 holdout 均按已登记交易 calendar 隔离至少 10 会话。评价区间结束日排他，跨区间标签不能参与准入。旧声明没有 holdout 时保持阻塞，不能事后追加或用修改后的 Candidate 字段替换原声明。`stability` 只读取该固定 holdout 的既有信号/结果，要求同 input 配对、完整合格池、成熟标签和最低样本/日期/时间块；不复用主窗口或历史最佳尝试。

主比较与独立 holdout 分别采用最新尝试，holdout 必须晚于最新主比较，`--as-of` 不接受未来或相对已保存尝试倒退的时间，也不能早于真实预登记证明。有效比较仍仅给 REVIEW_REQUIRED 等研究状态，统计独立性和 MARKET_EDGE 均未成立。SHADOW_ELIGIBLE / SHADOW_OBSERVING 绑定两份当时最新评价记录的 ID 与 hash；新尝试撤销旧准入，后续成功须重新显式走 OFFLINE_EVALUATED → REVIEW_REQUIRED → SHADOW_ELIGIBLE → SHADOW_OBSERVING，旧观察事件不会自动复活。拒绝/停止观察/失败均保留全部历史；失败比较追加尝试且返回非零。

影子状态仅登记研究准入，不启动作业、定时任务或训练，不改原日常清单、排序或生产服务；production_promotion 始终 DISABLED。`diagnose` 分开输出资料/工程、输入分布、成熟表现三个类别；输入分布目前没有预登记参考，明确 NOT_EVALUATED，不能将覆盖下降或单股涨跌解释为策略失效。工程 fixture、真实数据验证、人工验收与投资结论分别报告。

V2 命中只来自保存的主/备选形态且对应候选状态为 PASS；未被选择的 PASS 不追加为命中。选择资料有缺口且没有确认命中时输出 `null/未知`，不能当作合法空集合。导出和解释保留原选择、候选状态及缺口。

server 模式中 `stockfish daily --config CONFIG --job-id JOB_ID` 查询已有作业；加 `--cancel` 才显式请求管理员取消。客户端断网、503、退出或停止等待不自动取消共享作业。收到有效提交响应后的等待错误保留已知 job_id 和查询方法；提交响应丢失时不能保证客户端已经取得 job_id。

`daily` 本地计算会写 job/run/cache。`daily --refresh` 调用固定 Python 采集链并联网，另需采集/研究预算与运行时 HTTP 硬限额；doctor 的 SDK 检查离线。已有 Go 历史通过受验证的父身份、范围、calendar、修订和可比基准桥接复用，兼容性不能证明时明确阻塞；无历史且明确 `--refresh --refresh-cold` 才允许冷采。真实在线调用状态以当前执行报告为准，不能把离线桥 fixture、SDK 通过、PARTIAL 或 CLI 可启动解释成 ONLINE_REFRESH 成功，本文不声明生产激活。
