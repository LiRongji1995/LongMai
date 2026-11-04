# 工程发布输入、健康检查与恢复演练 V1

本轮新增独立工程工具，保留旧 core、已认证 scripts、策略配置、依赖锁和历史结果。发布源码不会启动服务、切换登记、采集行情、重试失败请求或创建交易连接。

## 健康与自动化检查

Docker 的健康探针改为带原鉴权头的 `/health/ready`。应用可响应而调度尚未就绪时，该接口返回 503；`/health/live` 继续只表示进程可响应。容器 ready 不等于当日数据齐备、完整盘前成功或 DAILY_V1 就绪。

根 `.github/workflows/engineering-gates.yml` 使用只读仓库权限、固定 Actions commit，不读取用户密钥，不使用 `pull_request_target`，不做部署。PR/main push 按实际变化路由：

- Python：来源与 UNKNOWN 合同、HTTP 鉴权与容量、readiness、账户约束、备份和真实隔离子进程监督；另运行本次变更测试文件。
- Go 候选：先由独立 Python 路径生成 TEST_FIXTURE oracle，再设置 `STOCKFISH_FORMAL_ACCEPTANCE=1`，执行 race 与 vet；缺 oracle 必须失败。
- Go 边界模块：独立 race 与 vet。
- Live：新隔离环境、现有版本约束与独立测试。不把无工件哈希的安装声明为可复建发行版。

daily/review 仅使用原锁覆盖的原生 `macos-15-intel` / Python 3.12；不会在 Linux 或 ARM 上复用 macOS x86_64 工件锁。手动触发没有 diff base 时运行全部组件固定门禁，私人路径扫描明确为 `NOT_CHECKED_NO_DIFF_BASE`；不会把仓库保留的上游历史模型重分类为本轮新发布数据。普通 PR/push 的新增或变化路径仍拒绝真实 `.env`、DB、模型权重和运行环境目录，许可文件缺失也失败。

这些是定向工程门禁。历史完整兼容套件的 golden/依赖失败、真实业务验收和投资有效性分别记录；不更新旧 golden、删除失败样本或扩大 skip 来取得绿色结果。GitHub 运行结果与本机测试结果分开，配置了工作流不代表远端已执行通过。

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

本机额外检查未下载工具链或依赖：现有Go为1.20.4，候选模块的 `go test -race ./... -count=1` 与 `go vet ./...` 均在其1.26.0声明解析处退出1，尚未运行候选测试。go-core的同两条命令分别退出0（17.685/10.415秒，race覆盖3个测试包），但该工具链低于模块声明的1.24，不能作为声明平台验收。原Python oracle独立生成14个TEST_FIXTURE案例、每例8只，31.118秒退出0；这不等于Go差分通过。Live因现有环境缺pyarrow、duckdb、fastapi、uvicorn、wsproto、httpx未运行；本机也无Docker命令。目标工具链及独立运行环境由上述CI描述，远端实际结果仍须另验。

未在本轮安装/升级环境、执行 Docker build、运行真实备份、连接市场来源、切生产或认证投资有效性。Actions 固定版本与 GitHub 官方 runner 标签已核对，远端工作流验收另记。
