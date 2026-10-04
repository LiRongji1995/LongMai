# StockFish 终端候选包

先执行 `./stockfish --version` 和 `./stockfish --help`。不需要 Codex、AI 代理、模型密钥、Python 或 Web 服务；可选在线采集 worker 另行配置。

首次绑定已有已发布 V2 隔离库：`./stockfish init --db /你选择的已有库 --config /你选择的配置文件`。也可指定 `--instance-root` 绑定既有隔离控制目录。init 不创建或迁移数据库，不研究，不联网，不覆盖配置，非交互环境没有提问。配置文件 0600。配置路径优先级：命令 `--config`、环境 `STOCKFISH_CONFIG`、OS 用户配置目录；其他命令选项覆盖该配置。配置内相对路径锚定配置文件所在目录。日常默认 R1；库的 V2 全局默认保持原样。

`doctor` 分列只读查询、本地研究和采集配置状态；默认不联网或深扫历史。`picks --strategy r1` 只查严格匹配的已发布结果，无结果不会退回其他策略或日期。`picks --run-id ID` 是明确历史查询。`explain 证券ID --run-id ID` 读取同一保存运行的实际条件、阈值、全部形态、理由与证据；未知保持未知。范围外与范围内未评价分别报错。

默认展示所有 CORE_PASS 或 V2 FOCUS/WATCH 候选、全部匹配形态；`--all` 包含已评价的拒绝及未知证券。内部读取全部记录。`--limit N` 仅截断屏幕显示并注明总数；指定 `--output 新文件` 时导出完整选择，拒绝覆盖。支持 `--format json|csv|markdown|table`；JSON 保留完整 run/manifest/summary/coverage/detail。目标日若来自旧保存观察日与官方日历，会明确标 DERIVED；解释/全评价记录不伪装为 CORE_PASS 候选。查询/导出上限 128 MiB，超过就明确失败，不悄悄丢页。CSV 的证券文本前缀为 `'`，公式起始字符也加文本前缀。

`daily --strategy r1 --dry-run` 只做计划。`daily --strategy r1` 先复用匹配结果，否则使用满足目标观察日、截止和证据合同的本地冻结输入执行一次现有 Go 应用服务。默认研究 300 秒，需要时显式 `--budget-seconds 1800`。幂等身份包含输入、目标、策略、配置、实现、证据政策及资源预算。失败不自动重试；已有工程 `resume` 保留显式尝试审计。没有当期资料就阻塞，不静默采集。

日期采用正式 2026 SH/SZ 日历及 Asia/Shanghai。盘前目标为当日，盘中/盘后为下一会话，休市为下一正式会话；目标所需观察日为前一交易会话。盘中当天日线尚未完成，研究阻塞，不能自动退回昨日。合法长假不会按自然文件年龄误判过期。日历越界拒绝推测。显示观察日、cutoff、生成时间；晚到资料成为新输入。

`daily --refresh` 只在显式配置 `FIXED_MAINBOARD_PUBLIC_V1`、对应固定 worker 及 Python 时采集，最长 600 秒，最多 100 HTTP（含重试）、单请求 40 秒、固定队列优先级与限速。它复用原 MainboardPipeline 的 DAILY_PRIMARY_CRITICAL 收集/规范化路径，Python 不执行策略。每批使用独立目录/HTTP账本，保留 PARTIAL/失败回执；成功冻结后调用相同 Go 服务。包内 `worker/setup-worker.sh PYTHON NEW_VENV_ROOT` 提供可重复、锁定依赖的可选配置，不会自动执行或更改系统 Python。首次 init 可用 `--provider ... --python ... --worker ...`。未实测在线成功不能称为线上就绪。

embedded 查询以 mode=ro、一致读事务校验全部保存明细/投影/成员集合，不初始化 schema、checkpoint、访问缓存或取写租约。长研究时可读取已发布历史结果。活跃库没有 immutable=1。新增输入改变当前发布库存摘要，仍验证库存；配置绑定的迁移/schema/路径身份保持独立。server 仅显式配置 loopback URL；核对协议、数据库身份与路径 hash，拒绝重定向和本地回退。写 API 令牌来自 0600 私有 token 文件，不经命令行或日志传递。

日志队列最多两条、单个 worker；每次 pipe 日志写最多 250ms，最终刷新最多一次。锁内不做日志 I/O。外部任意 io.Writer 必须实现可取消 ContextWriter，否则拒绝调用并记录不支持；拥有的 io.Pipe 可关闭收尾。CLI 借用 stdout/stderr 的重复句柄，临时开启 pipe 非阻塞并在关闭时恢复标志，不关闭原 stderr。操作系统或文件系统卡死的普通文件写不具备任意强制取消保证。丢弃/错误/最终送达独立记录，日志丢失不改变策略结果；观察到完整 final 行才证明该条已收到，内存最终送达状态在写成功后更新。

研究同时记单调经过时间与墙钟等待时间；超过 2 秒的不一致或明显回拨取消 scope，恢复后的首个检查点和发布前检查会拒绝继续发布。整机休眠时进程与监督器可能都不运行，无法保证休眠期间准点终止。Ctrl-C 会取消并按现有单写者事务/租约合同收尾；不会自动清理未知的崩溃租约。

R1 是 RESEARCH_ONLY，promotion=DISABLED，MARKET_EDGE=NOT_ESTABLISHED，参数尚未验证。CORE_PASS 不等于 FOCUS 或已验证投资优势。查询能力、资料完整性、线上采集、完整规模旧库增量与生产激活分别验收。许可见 licenses、LICENSES.md 和 THIRD_PARTY_NOTICES.md；仓库含 AGPL-3.0 组件，不统称纯 MIT。
