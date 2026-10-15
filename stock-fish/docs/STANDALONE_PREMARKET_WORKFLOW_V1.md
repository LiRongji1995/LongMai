# 独立终端盘前研究 V1

`STANDALONE_PREMARKET_WORKFLOW_V1` 将现有公开来源采集、原始证券身份校验、冻结R2计算、完整67会话题材诊断、当前行业/概念分组及导出接到同一个本地命令。它不需要Codex应用、网页服务、应用账户或LLM API。它是显式R2研究入口；服务的主动策略 `STAGE_AWARE_MOMENTUM_V2`、旧命令、DAILY_V1和所有既有run保持原身份。

## 执行

在已配置依赖的项目Python环境、业务目录 `stock-fish/` 中运行：

```sh
python scripts/stockfish.py premarket --theme-read-gib 12 --max-requests 768 --max-seconds 0
```

这里的 `python` 必须是项目环境的Python，例如已有 `.venv/bin/python` 或Windows `.venv/Scripts/python.exe`。不调用系统Python安装、升级依赖或切换Qlib。采集需要现有公开提供方依赖；Windows进程树监控还需要项目 `requirements-review.txt` 已锁定的psutil。首次环境配置见[部署说明](../../DEPLOYMENT.md)。

这条命令明确选择本次题材12GiB返回读取、最多768个新逻辑请求、无总时间上限。提供方worker单次最多90秒，报道HTTP的socket超时最多90秒，均零自动重试。冷启动须取得67个真实交易日、证券资料、最新截止的完整复权值和当前成员，可能耗时数小时；数据源错误不会因等待更久自动消失。默认较小配置为题材4GiB及512请求，预算不足会保留失败并非零退出，不自动扩容。

也可使用本地启动器：macOS/Linux运行 `./premarket --theme-read-gib 12 --max-requests 768 --max-seconds 0`，Windows运行 `./premarket.ps1 --theme-read-gib 12 --max-requests 768 --max-seconds 0`。启动器优先读取 `STOCKFISH_PYTHON`，其次读取 `STOCKFISH_PREMARKET_PYTHON_FILE` 指定的文件（默认 `reports/premarket-runtime/python.path`，第一行为已有项目解释器路径），最后使用本目录 `.venv`；不自动安装或改环境。私人路径文件保留在已忽略的reports目录，发布模板不包含个人目录。配置好解释器后，每次只执行这一条命令。

默认数据位于系统用户状态目录：macOS/Linux为 `${XDG_STATE_HOME:-~/.local/state}/stockfish/premarket`，Windows为 `%LOCALAPPDATA%/StockFish/premarket`。可通过 `STOCKFISH_PREMARKET_STATE`、`--state-root PRIVATE_DIRECTORY` 或私有JSON配置指定。建议使用稳定的本机目录；云盘的物理文件状态变化也会触发原件校验拒绝。源码和运行数据分开，不把缓存、原始全市场行情或研究产物提交Git。

可复制[配置模板](../config/premarket.example.json)到私有目录，修改后只执行：

```sh
python scripts/stockfish.py premarket --config PRIVATE_CONFIG.json
```

配置中的大预算是操作者的明确选择，不由旧一次性授权继承。费用、LLM、券商和交易执行均不属于此流程。

## 自动流程和结果

1. 认证原字节、物理回执、result、来源实现及实际时钟；完整自然日日历确定上海时区最新已完成资料日及下一交易日。采用18:00提供方发布门禁，不按周一至周五推测交易所开闭市。终验再次核对，跨资料日变化拒绝把旧结果称最新。
2. 复用通过重新验真的请求缓存，只补缺口。最近25个价格会话保留全RAW证券与原资格缺口，67会话保留全部市场分母。新的复权结束日不能用旧快照改日期替代。全证券基础资料失败时保留原记录，仅在已登记的不同端点范围内补充；旧正IPO/退市日期参考不认证当前状态。
3. 用同一真实来源在内存构造原输入，调用冻结 `SHORT_HORIZON_STRENGTH_R2`，保存完整canonical run、原input及五份正式导出。不新建业务数据库或并行选股逻辑。R2内部原有R1对照仍会运行，不能称所有底层计算仅调用一次。
4. 从真实目录读取完整当前行业/概念成员，再以完整原入选集合确定潜在相交范围。全部已取得父来源仍注册；完整分页、原始人数冲突、非支持证券与PARTIAL须经原生来源发行器核验，不能裁成员凑完整。使用真实 `SOURCES_V2`、行业来源、完整市场stream、V2证据日志和原诊断，当前成员回看明确标为 `CURRENT_COHORT_BACKVIEW`。普通JSON或布尔值不能恢复活owner与来源能力。
5. 有界读取财联社和证券时报的公开首页，发现并保存当日复盘等原文材料；实际抓取时间、原文发布时间和正文业务日期分列。解析或抓取失败保留UNKNOWN。该报道附件不直接发行EVENT能力，也不靠同篇共现推断个股经济方向。
6. 展示采用最近完整交易日的原始成交额 **≥3亿元**，未知成交额另计而非补零。只在展示层过滤，原入选数量、顺序、全部数值和五份导出保留。分组使用已认证的当前行业/概念原名，未知末列；细分经济主线缺独立证据时仍未知。阶段、风险、确认、发酵和分歧分别展示，不因新闻或行业关系新增入选、改变FOCUS或调权。

终端每10秒输出阶段，最后打印终态JSON及任务路径。一个任务目录包括：

| 路径 | 内容 |
|---|---|
| `configuration.json`、`code-registry.json` | 当次配置与实际实现身份 |
| `capture-summary.json`、`theme-source-summary.json` | 请求范围、缓存来源、完整性与原失败 |
| `core/` | 完整原run、input、全部/观察/焦点/未选CSV及观察JSON |
| `theme/` | 来源清单、完整证据日志、原生题材父和诊断 |
| `news/` | 原始网页、真实回执及独立报道附件 |
| `display/premarket.txt`、`.json`、`.csv` | 3亿元展示清单及未知分组 |
| `metrics.json`、`worker-completion.json` | 实际阶段、资源和错误 |
| `final-control.json` | 父进程实际回收子进程后封存的最终状态 |

读结果时以 `final-control.json` 为准；`UNSEALED`、中间文件存在或子文件自称PASS都不代表最终成功。核心独立完成但增强阶段失败时，保留核心与明确的未知/失败，不冒充完整题材或合法空集。

| 退出码 | 含义 |
|---|---|
| `0` | 已登记流程完成，具体研究未知仍看各字段 |
| `2` | 有可用核心和展示，但有阶段/来源缺口 |
| `3` | 执行或终验失败，读取原失败及已保存核心 |
| `4` | 触预算，保存实际失败，未自动扩容 |
| `130` | 用户中断，回收本命令的进程 |

## 缓存、恢复和只读核验

```sh
python scripts/stockfish.py premarket-status ORIGINAL_JOB_DIRECTORY
python scripts/stockfish.py premarket-verify ORIGINAL_JOB_DIRECTORY
python scripts/stockfish.py premarket --offline --config PRIVATE_CONFIG.json
```

归档核验只证明所列原字节，不能恢复来源能力或把旧日期变成今日。重复执行创建新任务，复用经过认证的成功原件。正在运行的同一状态目录由OS锁独占，崩溃不会留下伪锁owner；未完成claim和原FAILED记录仍保存。`--retry-failed` 是操作者明确选择本次为失败逻辑请求新增一次attempt，单次命令不自动再试。中断、数据错误和文件损坏不当作缓存未命中后悄悄重下载；损坏须先查原件及错误，不能用新结果覆盖历史。

既有SOURCES_V2/原生核心计划可通过重复 `--seed-sources ORIGINAL_MANIFEST` 导入真实归档。导入仅登记并验真原件引用，不复制股名单、重建缺失result或恢复旧能力。trio还须有实际匹配的三个来源实现文件；旧producer源码不在当前版本时须明确提供原 `producer_files` 三个原件引用，仅有旧hash或独立freeze声明不能冒充已核代码字节。缺此证明会明确拒绝，不能把所有旧私有产物视为可直接复用。RAW-only来源仍只作RAW/receipt来源；原core近25日需要真实完整trio，缺失则按当次预算采集。历史下载保留本次实际可见时间，覆盖标为事后重建，不能据此声称历史PIT。

## 资源和验收边界

新执行profile分别登记采集、核心、题材、展示返回读取限额（默认4/4/4/1GiB），父验收另有1GiB，互不借用。显式 `--theme-read-gib 12` 只改本次题材域。自身RSS上限2.5GiB，进程树3GiB，任务输出1GiB、请求缓存8GiB。新profile不覆写旧协调器的固定预算。父进程验证真实退出、来源与代码状态及输出；超限只停止本命令创建的进程，不能把晚到PASS越过失败。

返回读取计量覆盖受控Python文件读和gzip逻辑返回，提供方子进程内部I/O、解释器启动及内核I/O不在同一测量中。预留SDK/HTTP上界和实际提供方计数分别记录，提供方未报告的真实计数保留未知。控制文件、日志和证据目录计入输出，不靠删除完整分母、缩短67会话或减少原入选股绕预算。

目录大小使用完整 `DirEntry` 遍历计数，显式根目录别名计入目标，子链接不跟随。周期性文件系统检查从上次扫描完成后节流，避免单次慢扫描引发逐次读取重扫；每个阶段入口、出口仍强制完整核对输出与该阶段适用的缓存限额，追加增长不能因节流略过。读字节、时间、内存和请求守卫仍逐次执行，退出检查失败保持粘性并保留主异常。此优化不提高限额，也不减少原件SHA/7FP、来源或journal终验。

隔离 `TEST_FIXTURE` 测试检查真实R2、来源能力、67会话全市场分母、日志篡改拒绝、预算、进程、分组与过滤；它们不能单独证明真实资料执行或投资有效性。已完成一次真实暖缓存命令的原R2、完整67会话、全RAW市场分母、来源callback及父进程终验，实际外部退出2并封存为 `COMPLETED_WITH_UNKNOWNS`；完整25只原入选和3亿元展示17只分别保留，来源及方向未知没有转为合法空集。实际命令、资源、请求和两次130中断见[检查记录](STANDALONE_PREMARKET_VALIDATION_V1.md)。全新冷启动、Windows原生运行和连续交易日可靠性仍未验收；任何一次私有旧脚本的真实通过或失败也不转记为本入口的验收。初始参数尚未验证，`MARKET_EDGE=NOT_ESTABLISHED`；DAILY_V1单独报告，完整经济题材、公司业务/事件、席位与历史PIT仍按实际证据决定。
