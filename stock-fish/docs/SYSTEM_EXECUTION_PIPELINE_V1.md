# 日常执行路径V1

默认 `codex_daily_v2.py premarket` 保留原题材执行器；`--engine shared` 是显式共享计算候选。小样本全流程曾测得回退，因此没有将其自动设为默认，也没有据此宣称真实新清单提速。

较新的 [完整盘前执行V1](PREMARKET_SHARED_EXECUTION_V1.md) 将共享题材接入实际完整工作流，并提供 `codex_daily_v2.py premarket-plan/premarket`。本文原V1查询、独立theme-run和历史实测边界保持；新完整流程不以本文402→134解码计数直接宣称真实提速。

这轮解决重复工作的执行结构：已保存成果只做成果完整性验证；需要新事实的计算重新认证来源，并在同一次调用中共享解析和验证会话。入口为 `scripts/codex_daily.py`，新模块均位于scripts，保留历史core源码集合、策略、原run及原始文件。

## 已保存成果

在业务目录执行，入口自动转入固定登记的解释器与源码位置：

```sh
python scripts/codex_daily.py status
python scripts/codex_daily.py views
python scripts/codex_daily.py show
python scripts/codex_daily.py show --view-id VIEW_ID --role observation.json
python scripts/codex_daily.py plan
python scripts/codex_daily.py plan --target-session YYYY-MM-DD
```

`show` 默认选择封存视图中观察日、截止最新的一份，不把旧观察日改成今天。可显式传入 `--observation-date`、`--target-session`，不匹配即拒绝。支持summary以及原observation.json、observation.csv、all.csv、focus.csv、unselected.csv。JSON输出中的content是原UTF-8导出文本；五份文件每次均核对完整SHA和七字段物理身份。

首次没有视图时，对已登记且受支持的原归档显式执行：

```sh
python scripts/codex_daily.py publish --archive-id ARCHIVE_ID --profile FULL_CORE_ARCHIVE_READONLY_V1
python scripts/codex_daily.py audit --archive-id ARCHIVE_ID --profile FULL_CORE_ARCHIVE_READONLY_V1
```

V2归档对应 `FULL_CORE_ARCHIVE_READONLY_V2`，不能混用。首次publish调用原完整reuse认证、随后签封小视图；重复publish复用同一视图和原认证时间。audit始终走原完整认证，不将一次普通查询升级为新来源能力。

视图保存摘要、原导出引用、原观察日/目标日/截止和最后完整认证时间。HMAC-SHA256密钥在共享report根的私有目录，目录0700、文件0600；密钥丢失且已有封存时拒绝重新生成。其信任范围是本地操作系统账户和密钥完整性，不是供应商签名，也不能对抗已控制该账户的攻击者。它不复制RAW、input或canonical run，不写业务库，不恢复历史PIT。

所有读取明确返回 `source_reauthenticated_now=false`、`source_authority_restored=false`、`currentness_recomputed=false` 和 `last_full_authenticated_at`。原来源后来缺失时，保存成果可以在本地封签和全部原导出仍完整的前提下被读取，但不得用它替代新计算的完整来源认证。

## 依赖与空间

计划按业务日、截止、策略/来源/配置版本及完整父依赖身份决定REUSE、BUILD、UNKNOWN或BLOCKED。已保存facts/core节点只是保存结果中原输入/核心身份的引用；不声称本次重验input全文。复用必须有已验证成果载体；自声明JSON或公开hash不够。构建中的父节点尚无真正内容hash时，下游不把任务key当作成果hash。

当前日查询使用同一封签下的原五份导出；新交易日缺market、后验缺当日输入时保持UNKNOWN。计划只读，`execution_authorized=false`，没有采集、worker启动、自动重试或来源能力。一次容量盘点提供给整个图，状态只反映真实空间；不替代数据就绪判断。

空间方面优先避免新增完整副本。真实视图仅5,530字节，加32字节私有密钥，未复制原大输入或canonical run；测量期间的导出stdout副本已在逐字节核对后清理，只留下小型hash/计量记录。没有删除、重压缩或搬迁旧缓存。原64 MiB缓存门禁、1 GiB管理总额及磁盘余量政策不扩大；旧缓存超过门禁的问题仍存在。

## 同次67会话计算

```sh
python scripts/codex_daily.py theme-run --request PRIVATE_REQUEST.json
```

请求严格使用以下字段，路径必须指向原保存交付协议所需的真实原件；run-manifest不是任意核心manifest，也不能直接用压缩canonical run替代：

```json
{
  "schema_version": "CODEX_DAILY_THEME_REQUEST_V1",
  "run_path": "/absolute/path/to/original-run.json",
  "run_manifest_path": "/absolute/path/to/original-run-manifest.json",
  "input_path": "/absolute/path/to/original-input.json",
  "sources_manifest_path": "/absolute/path/to/sources-v2.json",
  "context_cutoff": "2026-10-26T18:00:00+08:00",
  "membership_mode": "DATED_ONLY"
}
```

仅真实 `AuthenticatedSourceArtifactsV2` owner可发行来源能力。原run/input、五份导出、真实RAW及来源绑定先完整验真，运行和末尾保留完整认证；不能从保存视图、plan或普通JSON恢复能力。默认请求完整日历最后67会话；短日历和缺日保留UNKNOWN，不压缩窗口。显式 `CURRENT_COHORT_BACKVIEW` 使用当前成员回看并保留其覆盖解释。

新 `codex_daily_session_v1.py` 在一次调用中让market/master各解码一次，同时形成原市场统计与价格panel；有界facts临时文件将同一会话投影交给原V2主题状态机消费。旧数学、阈值、完整顺序、source_refs及effective_input_hash保持。借用对象和嵌套row迟变、源码/来源SHA与七字段物理身份均有末验。临时facts不是跨请求来源能力。

内部预算与原私有配置取较小值；本机原max_seconds=240、core读取512 MiB、theme读取64 MiB、output32 MiB、self RSS1 GiB、tree RSS1.5 GiB没有提高。外层独立监督真实进程树、输出、控制台和时间，10秒仅为监督清理余量，不扩大内部计算预算。只清理本次start_new_session创建的进程组，保留失败退出码、固定原因和请求引用，不自动重试。父进程核对精确输出路径、请求、入口源码及最终hash后才写COMPLETED。pipeline内部manifest单独存在不构成交付成功。

该命令生成主题结果及完成回执，`complete_daily_delivery=false`；它不是完整经济方向报告，也没有替换旧完整交付命令或生产服务。

## 同次后验执行

`outcomes` 保留原显式后验CLI。`outcomes-shared` 将原run命令交给新增共享执行器，把计划与worker放在同一store、同一线程、同一次请求的真实验证会话中；完整父来源、原认证、借用数据末验及事务内末验继续执行。300秒、2 GiB RSS、8 GiB实际读取等额度从计划开始累计，不能把原本两段认证预算叠加或选择900秒档位冒充默认达标。300秒仍是原session的协作检查阈值，不声称硬进程墙钟上限。

```sh
python scripts/codex_daily.py outcomes-shared r2-daily-run \
  --db EXISTING_DB --state EXISTING_STATE --run-id ORIGINAL_RUN --input-id AUTHENTICATED_INPUT \
  --knowledge-cutoff AWARE_CURRENT_CUTOFF
```

只支持原 `r2-daily-run`、`r2-restored-daily-run`、`r2-closed-session-run`、`r2-late-completed-run`、`r2-local-copy-late-run`、`r2-dated-late-run`；后五种各自需要的原协议、manifest及SHA、业务日和截止参数不省略，以原命令help为准。daily额外支持明确knowledge-cutoff以固定同一次请求；省略时取原实际时钟。同步执行不接受后台wait-seconds，也不分发import、retry、cycle或采集命令。

同一请求的已完成账本复用明确标为本次未重新认证；QUEUED、RUNNING、FAILED或提交不明不自动恢复/重试。新执行版本、jobkind和ledger与旧执行分开。保留原JobService的OS锁与同步状态机，不新建数据库或启动服务。

## 真实测量与验证范围

2026-10-26，固定登记daily/review Python、原发布检出、每次独立进程和全新不存在的字节码查找路径，使用同一原V1归档：

| 操作 | 真实耗时 | 采样进程树峰值RSS | 含义 |
|---|---:|---:|---|
| 首次publish | 72.005秒 | 1,323,413,504字节 | 原完整来源认证及新增小视图 |
| show第1次 | 0.931秒 | 26,148,864字节 | 封签及五原导出完整性验证 |
| show第2次 | 0.791秒 | 25,890,816字节 | 同上 |
| show第3次 | 0.790秒 | 28,336,128字节 | 同上 |
| 重复publish | 0.637秒 | 未单独采样 | 幂等复用，不重写认证时间 |

这两类操作验证范围不同，不能说完整来源认证自身变快了90倍。收益是普通查询不再承担一次全量重建。五种role实际输出与原文件逐字节一致，退出均0；原run、3045全池、WATCH64、FOCUS0、UNKNOWN1、2039规范化缺口、RECONSTRUCTED_HISTORICAL和DAILY_V1未知均保持。当前计划三节点REUSE、后验UNKNOWN；下一日旧节点继续REUSE，新facts/core/result及后验UNKNOWN。

首次plan整CLI检查因输出entry_version重复关键字退出1，已修复后原命令退出0；保留初次失败记录，没有把它计为通过。

将新执行器置于scripts并同步本轮文档后，又实际执行原V1完整audit：退出0、48.696秒、原summary完全一致。这是历史源码集合兼容性复验；其时系统负载与缓存条件不同，未当作新旧性能A/B。

67会话两种成员模式的原实现对照中，新路径RAW解析134次、投影67次；原路径market268次、master134次，共402次。计数与精确等价属于TEST_FIXTURE工程验证，不能换算成真实全市场执行时间。真实新67会话全链路、真实日期化后验默认300秒、连续自动交付尚未验收；原真实900秒单/双run412.798／601.295秒不能移作新版本性能结果。投资参数尚未验证，MARKET_EDGE=NOT_ESTABLISHED。

最终在发布检出的固定daily/review环境运行以下组合，99 passed、14 subtests passed，pytest 165.54秒，外层167.194秒，退出0。下列路径已占位化；私有验收记录保存实际解释器、全新字节码路径、临时fixture路径和原命令：

```sh
"$STOCKFISH_PYTHON" -I -B -X pycache_prefix=FRESH_ABSENT_PATH scripts/run_isolated_tests.py \
  -q --basetemp FRESH_TEST_FIXTURE_DIR \
  tests/unit/test_codex_result_view_v1.py \
  tests/unit/test_codex_daily_plan_v1.py \
  tests/unit/test_codex_daily_entry_v1.py \
  tests/yaogu/test_daily_session_pipeline_v1.py \
  tests/yaogu/test_shared_outcome_execution_v1.py
```

覆盖原V1/V2归档鉴证→封存→五份导出回读、HMAC/密钥/字节/物理身份篡改、依赖与容量、原67会话两模式精确等价、真实issuer及来源/借用对象末验、独立子进程监督和失败状态。共享后验使用两份真正经过原bridge鉴证的TEST_FIXTURE归档：每份仅归一化一次，共一个genuine session；原计划末验、worker提交前末验、事务内末验均执行，数值H1和三类产品与原实现一致。线程/store/request变化、源码SHA及同SHA换物理身份、迟变、取消、预算、整批回滚和late跨收盘准入均检查。restored/local-copy/dated各自的完整端到端共享执行未逐类重跑，不能从daily用例外推。

开发中原共享测试曾因测试执行期间源码变化被拒绝；后续冻结源码重跑，最终以上99项组合为交付依据。没有删除失败断言、扩大skip或放宽预算。此次没有执行全仓回归、真实新采集、真实日期化后验、生产切换或投资验证。

保护基线覆盖开发、发布检出、共享数据和登记目录，共7,545份既有常驻普通文件；其中24份是两检出中授权修改的12份说明文档，其余7,521份完整SHA及七字段物理身份保持，无额外变更。6个云占位或非普通文件继续跳过，不触发下载；发布检出原有fixture删除/云占位脏状态不纳入提交。只发布23个明确列出的源码、测试和说明文件，旧策略说明、许可证和第三方通知保持，不提交密钥、真实DB、行情、日志或完整本机审阅包。
