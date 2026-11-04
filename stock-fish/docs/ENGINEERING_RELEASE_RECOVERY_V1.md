# 工程发布输入、健康检查与恢复演练 V1

本轮新增独立工程工具，保留旧 core、已认证 scripts、策略配置、依赖锁和历史结果。发布源码不会启动服务、切换登记、采集行情、重试失败请求或创建交易连接。

## 健康与自动化检查

Docker 的健康探针改为带原鉴权头的 `/health/ready`。应用可响应而调度尚未就绪时，该接口返回 503；`/health/live` 继续只表示进程可响应。容器 ready 不等于当日数据齐备、完整盘前成功或 DAILY_V1 就绪。

根 `.github/workflows/engineering-gates.yml` 使用只读仓库权限、固定 Actions commit，不读取用户密钥，不使用 `pull_request_target`，不做部署。PR/main push 按实际变化路由：

- Python：来源与 UNKNOWN 合同、HTTP 鉴权与容量、readiness、账户约束、备份和真实隔离子进程监督；另运行本次变更测试文件。
- Go 候选：三个独立原生 macOS job 分别运行 `application/research`、`core` 和全部其余包。每个 job 都由独立原锁 Python 环境生成 TEST_FIXTURE oracle，设置 `STOCKFISH_FORMAL_ACCEPTANCE=1`，执行完整包 race 与 vet；缺 oracle 必须失败。
- Go 边界模块：独立 race 与 vet。
- Live：新隔离环境安装现有依赖版本锁，再通过项目原 PEP 517 构建配置生成 wheel，以 `--no-deps --no-index` 非 editable 安装。测试前执行 `pip check`，在源码目录之外用 `python -I` 确认 `stockfish_live` 来自该 venv；保留真实 uvicorn HTTP/WebSocket 子进程测试。构建隔离所需工具遵循原 pyproject，工件哈希仍未锁定，不把这次安装声明为可复建发行版。

daily/review 仅使用原锁覆盖的原生 `macos-15-intel` / Python 3.12；不会在 Linux 或 ARM 上复用 macOS x86_64 工件锁。手动触发没有 diff base 时运行全部组件固定门禁，私人路径扫描明确为 `NOT_CHECKED_NO_DIFF_BASE`；不会把仓库保留的上游历史模型重分类为本轮新发布数据。普通 PR/push 的新增或变化路径仍拒绝真实 `.env`、DB、模型权重和运行环境目录，许可文件缺失也失败。

这些是定向工程门禁。历史完整兼容套件的 golden/依赖失败、真实业务验收和投资有效性分别记录；不更新旧 golden、删除失败样本或扩大 skip 来取得绿色结果。GitHub 运行结果与本机测试结果分开，配置了工作流不代表远端已执行通过。

### Go 完整包隔离与测试装载预算

`engineering_go_packages_v1.py` 在每个独立 job 中使用原生 Go 1.27.1 / darwin-amd64，固定 `GOTOOLCHAIN=local` 和 `GOFLAGS=-mod=readonly`。由实际 `go list -json ./...` 枚举全部包和测试文件，按两个完整重包名与 `remaining` 分区。重包缺失、重复包、分区重叠、未知组、集合遗漏、发现命令失败均拒绝；没有测试的包仍保留在编译和 vet 集合。包名通过验证后按 subprocess argv 传递，不拼接 shell，也不依赖 macOS bash 3.2 没有的 `mapfile`。

每个包的全部顶层/子测试仍在同一个测试二进制内执行，保留共享全局、池、清理和状态链；没有 `-run`、`-skip`、`-short`，不拆包内用例。三个 job 都使用 `-race -p 1 -json -count=1`。两个重包显式声明测试二进制累计装载期限 **30 分钟**；其余包继续使用 Go 默认 **10 分钟/包**。原测试中显式的容量样本 30 秒、CLI 上下文截止、业务 Budget 和所有断言保持。

每个 job 的总墙钟上限仍为 **45 分钟**，但 Go 候选检查已从一个 job 改为三个独立 job，**总分配上限由 45 分钟增加至 135 runner-minutes（3×45）**，最多并行 3 个 runner。不是原总资源预算不变，也不是生产吞吐 SLA。依赖准备、oracle、发现、race、vet 都占各自 job 额度；每臂实际耗时及合计 runner 时间应按当轮记录另外报告，不把上限当实际花费或把并发墙钟当累计耗时。

每臂上传新目录中的 `plan.json`、原生 race JSON stdout、独立 stderr、vet 的 `-json` 原始 stdout/stderr、逐命令开始/终态日志和 `execution.json`。plan 绑定完整集合、分区、当前提交、工具链、go.mod/go.sum hash；race 终态必须与所选包集合精确一致，拒绝 Test 级 skip、缺失/重复/未知包或失败事件，无测试包的原生 package skip 单独列示。vet 即使在 race 失败后仍覆盖同一组包；原 race 非零退出优先保留，vet 或事件核验失败也不能升级为成功。硬 job 超时可能只有开始记录和部分输出、没有最终 execution；这属于未完成，不能声称通过。每臂只有自身结果，全部三臂均成功且对应同一提交，才可报告该分区配置下完整包集合通过；旧单 job 失败不被覆盖。

## 发行输入清单

从仓库根使用明确选择的 Python：

```sh
"$REGISTERED_PYTHON" -I -B stock-fish/scripts/release_profile_manifest_v1.py
"$REGISTERED_PYTHON" -I -B stock-fish/scripts/release_profile_manifest_v1.py \
  --profile daily-review --target Darwin/x86_64/3.12 --require-hashed-inputs
```

工具只读 `scripts/contracts/release_profiles_v1.json` 和声明的源码/锁文件，不读取 `.env`、DB，不安装依赖，也不导入应用。

| profile | 当前可以确认 | 不能据此确认 |
| --- | --- | --- |
| daily-review | 原 58 包锁及需求文件的哈希一致、工件 URL/哈希完整时 `HASHED_INPUTS_VERIFIED` | 当前安装与运行验收、其他系统/架构、市场数据 |
| docker | Dockerfile、compose、需求文件的当前身份 | 基础镜像 digest、平台传递工件哈希和真实构建运行，仍 `UNVERIFIED` |
| live | pyproject 和现有版本列表身份 | 平台工件与构建工具哈希、当前运行验收，仍 `UNVERIFIED` |
| go | 两个 Go 模块和候选 go.sum 的当前身份 | 目标平台工具链工件哈希、独立 oracle 与当前运行验收，仍 `UNVERIFIED` |

`--require-hashed-inputs` 遇到未覆盖平台或 UNVERIFIED 返回 3；文件改动与旧锁不符直接失败。需要新平台时应另做新版本锁和验收，不能修改旧锁来追认旧运行。此清单不是来源签名或供应链认证机构。

## 显式备份恢复演练

工具只接受已选定的备份、全新输出目录和独立的已知结果探针。不会自动定位或打开原业务 DB；已有备份格式必须是 `wal-artifact-backup-v1`。先创建本次拥有的同字节 staging 副本，再复用原 `restore_state` / `verify_backup`；仅在副本上打开 SQLite。

空 WAL 与 SHM 可以保留，但源侧完整字节与物理身份必须在操作前后相同；非空 WAL/journal 拒绝，且不会 checkpoint、删除侧文件或修改源。路径逃逸、符号链接、已存在目标、超输入逻辑字节额度均拒绝。默认 1 GiB 是输入逻辑字节上限，不是 OS I/O/时间硬配额；staging 和 restored 都保留在新目录，需给两份副本及报告预留空间。

探针由已有独立已知结果填写，不应从待验恢复结果反向生成来证明自身。私有 `probe.json` 格式：

```json
{
  "schema": "RECOVERY_PROBE_V1",
  "data_class": "PRIVATE_OPERATIONAL",
  "documents": [
    {
      "namespace": "已知保存文档的namespace",
      "document_id": "已知原document_id",
      "expected_content_hash": "原保存文档的64位sha256，不是当前恢复结果反向生成的值"
    }
  ]
}
```

```sh
"$REGISTERED_PYTHON" -I -B stock-fish/scripts/recovery_drill_v1.py \
  --backup "$EXPLICIT_BACKUP" --destination "$NEW_PRIVATE_DRILL" \
  --probe "$PRIVATE_PROBE" --max-bytes 1073741824
```

`drill-report.json` 分开记录：

- 备份字节、SQLite 完整性和明确要求的保存文档是否可读；缺失不补零，不把 UNKNOWN 变为正常。
- `source_capability=REAUTHENTICATION_REQUIRED`，`historical_pit_restored=false`，原物理身份不恢复。结果可读不能升级为真实来源已重新认证。
- 实测 UTC 和 monotonic 恢复耗时，范围仅为验证、复制和请求文档读取；服务启动与业务重新可用的 RTO 仍 `NOT_ESTABLISHED`。
- 没有独立源事务水位时，RPO 为 `NOT_ESTABLISHED`、秒数为 null。备份年龄另列，不能代替丢失事务时长。

退出 0 仅表示声明的字节与文档范围通过，文档缺失或 hash 不符返回 2；执行异常保留失败报告并非零退出。准入前拒绝不会创建输出目录。真实备份演练、源事务水位、实际服务 RPO/RTO 仍需独立备份、已知探针和运行证据；本轮仅使用 TEST_FIXTURE。

## 本轮工程检查

首次关联组合为 42 passed / 3 failed，10.13 秒，退出 1：新工具最初一律拒绝旧备份验证留下的空 WAL/SHM。修正为上述独立副本与严格空 WAL 合同，新增非空 WAL 拒绝检查，旧备份源码与断言保持。

第二次关联组合为 46 passed，5.87 秒，退出 0；随后当时的 11 文件固定门禁为 165 passed，53.39 秒，退出 0。之后补充发布时源文件迟变撤销验证，重测出现 32 passed / 1 failed：独立诊断确认旧 fixture 备份构造的 sqlite 连接在 GC 时改变源 SHM 的 ctime，守卫正确拒绝。仅在 fixture 构造末尾显式回收遗留连接以提供真正已闭合备份，未 checkpoint、删侧文件或放宽守卫；新的四文件组合 33 passed，4.73 秒，退出 0。

新增的 ops、量化协议、V3 监督与完整编排测试也已加入固定 CI 门禁，最终扩大组合结果以本轮交付记录为准。上述检查均使用已登记 daily/review Python、`-I -B`、全新字节码路径和独立 TEST_FIXTURE DB/state/artifact；失败保留，不把不同轮次项数累加。

上述首次本机额外检查未下载工具链或依赖：当时现有Go为1.20.4，候选模块的 `go test -race ./... -count=1` 与 `go vet ./...` 均在其1.26.0声明解析处退出1，尚未运行候选测试。go-core的同两条命令分别退出0（17.685/10.415秒，race覆盖3个测试包），但该工具链低于模块声明的1.24，不能作为声明平台验收。原Python oracle独立生成14个TEST_FIXTURE案例、每例8只，31.118秒退出0；这不等于Go差分通过。Live因现有环境缺pyarrow、duckdb、fastapi、uvicorn、wsproto、httpx未运行；本机也无Docker命令。目标工具链及独立运行环境由上述CI描述，远端实际结果仍须另验。

首轮远端 Live 检查为 622 passed / 1 failed：真实 uvicorn 子进程无法导入 `stockfish_live`。当时只安装依赖，父测试进程注入的源码路径没有使子进程获得本项目发行包。修复仅补上述 wheel 构建、非 editable 安装、依赖检查和独立导入验证；没有设置 PYTHONPATH、过滤真实子进程用例、放宽锁检查或修改 Qlib wheel。补丁本机 CI 配置合同测试为 15 passed，2.84 秒，退出 0；Live 步骤的 `bash -n` 语法检查退出 0。首轮失败保留，补丁的远端结果另外验收；本机因缺 Live 依赖未运行 Live 测试，不安装环境来追认远端通过。

上述首次工程检查未安装/升级环境、执行 Docker build、运行真实备份、连接市场来源、切生产或认证投资有效性。后续 Go 诊断已在新的私有目录下载官方 1.27.1 工具链及按原 go.sum 校验的模块，未修改既有运行环境；其实际输入、资源与验收结果见[独立交付记录](ARCHITECTURE_OPTIMIZATION_DELIVERY_V1.md)。Actions 固定版本与 GitHub 官方 runner 标签已核对，远端工作流验收另记。

首轮远端daily另录371 passed / 4 failed：旧V2和新V3的两个真实worker身份检查分别在严格argv/cwd核验处拒绝。当前日志没有显示实际两值，不能将framework launcher假说冒充定论。新增 `native_python_identity_probe_v1.py` 以最小和继承环境观察真实child/grandchild的原始argv、cwd及原生可执行文件路径；不修改身份、不转印环境变量、不改变旧检查。默认诊断成功不是运行环境准入；`--require-exact` 在任何不一致时返回3。原门禁仍完整执行，诊断JSON作为独立CI工件保留。

首轮远端Go候选完整race退出1：application/research与core各触默认单包10分钟上限；sqlitev2完整容量样本的隔离子进程触30秒上限；CLI生命周期的一步触原上下文截止。未发现本轮源码变化导致的数值差分结论，不能因此免除这些失败。原历史交付也记录过完整race超时及定向补验，不能拿旧合并覆盖替代本次整条命令。后续仅将Go包级并发固定为 `-p 1` 并保留逐项 `-json` 日志，仍执行所有包、完整oracle和原断言；单包默认10分钟、样本30秒和job45分钟均保持，没有拆分内部状态链、过滤失败样本或扩大skip。是否消除资源争用须由下一轮真实结果确认；race失败后vet未执行继续明示。

随后两个同 Go 源码的完整 `-p 1` 运行均保留原始日志，均非通过：

| 运行 | research | core | capacity 30 秒原样本 | CLI 整包 | 正式步骤 / Go job 总墙钟 |
| --- | --- | --- | --- | --- | --- |
| `41928900984` | 600.079 秒包超时；R1 的 13 个差分子例已通过，V2 连续推进，当前子例仅运行 4 秒 | 600.134 秒包超时；RawEvidence 376.39 秒通过，FullSemantic 前 5 例通过，当前例运行 12 秒 | 27.69 秒通过 | 375.539 秒通过 | 36分30秒 / 40分48秒 |
| `41930441102` | 600.057 秒包超时；完整双策略差分顶层 522.56 秒通过，后续另一顶层子例只运行 2 秒 | 完整包 584.076 秒通过 | 18.89 秒通过 | 221.782 秒通过 | 29分36秒 / 32分02秒 |

第二轮 15 个测试包通过、2 包超时；第三轮 16 包通过、1 包超时；另各有 12 个无测试包的原生 package skip，没有 Test 级 skip。两轮均未观察到 DATA RACE 或具体断言失败；未完成的 research 测试不能算通过。原串行 shell 在 race 非零后退出，两轮 vet 均未执行，脚本预先回显的 `go vet` 不作为执行证据。

这些逐子例正常推进的证据支持把测试装载期限与单业务期限分开声明，但不能外推剩余测试耗时。新的三臂配置因此保留完整包内状态，在各自独立 job 内验收；30分钟包期限与135 runner-minutes总分配上限是显式新增工程预算，旧失败保留。新配置的真实远端结果另记，本机合同测试使用 TEST_FIXTURE 假 Go 进程验证分区、argv、退出传播与证据检查，不冒充原生 Go 性能或 race 通过。

三臂补丁的两个定向合同文件最终 **42 passed，7.84 秒，退出 0**；新增漏包/重复/未知包、发现失败、Test skip、缺失终态、race 与 vet 同时失败、race 信号退出、既有结果保护等反例。工作流全部 12 个 run 步骤的 `bash -n` 均退出 0。本机未为这次 CI 改动重新运行真实 Go 全套或下载工具链；远端三臂完整验收仍需当轮实际结果。
