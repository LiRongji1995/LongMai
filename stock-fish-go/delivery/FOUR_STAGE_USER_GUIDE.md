# 四阶段候选源码使用说明

本轮容量准入阻塞，尚未构建新包，也未通过运行测试。以下是本轮源码提供的命令接口，**不能当作已经可日用的候选包**。当前已保存旧 run 可继续使用原已验收包显式只读查询；旧包不包含新的 review/research-lab/diff。

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

这些 review/research-lab 查询只读，要求此前已显式初始化扩展。有写入权限并完成候选包检查后，以下操作会写 SQLite V2，均不联网；标签只能来自已登记的合法冻结输入。

```sh
stockfish review init --db DB
stockfish review register --db DB --run RUN
stockfish review evaluate --db DB --run RUN --label-input FOLLOWUP_INPUT
stockfish research-lab propose --db DB --file PREDECLARED_CANDIDATE.json
stockfish research-lab compare --db DB --candidate CANDIDATE --baseline BASELINE --key NEW_ATTEMPT --as-of RFC3339
stockfish research-lab transition --db DB --candidate CANDIDATE --to REJECTED --reason REASON
```

评价固定展示 1/3/5/10 个交易会话。UNKNOWN/未成熟不计零收益，资料修订追加新 outcome；登记、比较和拒绝不改原清单，不训练、不搜索参数，不输出个股成功概率。未满足独立证据要求只给阻塞/证据不足/拒绝/待评审，production_promotion 始终 DISABLED。

server 模式中 `stockfish daily --config CONFIG --job-id JOB_ID` 查询已有作业；加 `--cancel` 才显式请求管理员取消。客户端断网、503、退出或停止等待不自动取消共享作业，错误中保留 job_id 和查询方法。

`daily` 本地计算会写 job/run/cache。`daily --refresh` 调用固定 Python 采集链并联网，另需采集/研究预算与运行时 HTTP 硬限额；doctor 的 SDK 检查离线。有 Go 历史时安全桥未实现，网络前阻塞；只有无历史且明确 `--refresh --refresh-cold` 才允许冷采。不能把 SDK 通过、PARTIAL 或 CLI 可启动解释成 ONLINE_REFRESH 成功。本轮未执行 refresh 或生产激活。
