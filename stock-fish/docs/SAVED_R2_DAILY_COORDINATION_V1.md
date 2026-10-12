# 完整保存 R2 的当日交付协调 V1

显式合同 `SAVED_R2_DAILY_COORDINATION_V1`，特征 `AUTHENTICATED_CURRENT_DAY_COMPLETE_SAVED_R2_DELIVERY_V1`。它完成“已经保存的完整 R2 → 当次原件日历核对 → V3 原生完整交付”这一阶段；不采集、不提交 selector、不创建业务库或平行名单，不接默认服务。初始参数尚未验证，`MARKET_EDGE=NOT_ESTABLISHED`；工程 TEST_FIXTURE 不认证连续无人值守运行、历史 PIT 或投资优势。

## 上游与目标日期

调用方先核对已有实例、在途作业和最新业务日，通过既有 `capture/job` 取得已登记真实 input，必要时显式 `run --input-id EXACT --strategy SHORT_HORIZON_STRENGTH_R2`，再取得同一权威 run 的完整自包含 JSON 和原 input；不以摘要或 detail_ref 代替全池。输入资格、历史 owner、价格父来源的上游认证仍属于已有采集/策略路径，本组件不从保存 JSON 恢复这些权限。

实际接点为 `POST /api/candidate-data-pipeline/capture` 的空请求（服务器权威时间、来源和预算），以及 `POST /api/strategy-experiments/run` 的已登记 input_id/strategy_id。原 run 可以从 `GET /api/strategy-experiments/{run_id}/export?format=json` 取得，必须不传 strategy_id/selection 才保留原 content_hash。当前薄封装 run/export 使用的带筛选 official_exports 是 view：删原 content_hash、写 export_view/view_content_hash；它不能直接作为此组件的原 run，不能拼回或重 hash 冒充原件。当前 candidate API 没有完整原 input 的导出路由；调用方还须从已有合法归档取得同 input，历史 owner 和新 source issuance 的端到端接通仍未完成。

服务 `_build_short_horizon_strategy_result` 已在 native 之后填入 target_session、同步 prospective_validation 并重算 content_hash：目标来自 input 中后续会话，或本地交易所日历回退。这说明原保存声明已绑定全文；不等于真实日历原件当时可见，也没有替原 native run_id 重新纳入原件日历身份。协调器不修改该 run、target、截止或前瞻身份。

源清单必须有 `SAVED_R2_DELIVERY_SOURCES_V2` 的真实原件及回执，还必须在 captures 中登记完整 calendar trio。沿用既有 `_authenticate_capture` 与 `SourceArtifactReader.target_calendar_evidence` 认证原件；日历覆盖尾 event_time 是已登记日程元数据，按原发布时间别名/实收/摄入及完工钟判可见，普通行情四钟没有放宽。

按当次真实 Asia/Shanghai 时点和 15:00 收盘界，完整日历必须覆盖当前自然日；观察日必须是最后已完成会话，原 run 目标必须是下一真实会话。日历缺日、重复日、同钟冲突、非交易观察日、覆盖不足、目标缺失或跳日均拒绝。执行终态再次核当前已完成会话，跨过新收盘不能将旧日结果称当日最新。较晚取得的日历只认证新交付情境，不回填原 run 或 PIT。

原保存日线 decision_cutoff 必须不早于观察日 15:00；generated_at 与原 cutoff 的实际可见上界必须不晚于本次真实 context。原 input/run 覆盖类型严格一致。worker 之后外层还要用原 trio 再认证日历，包括最终 seal 后收盘跨界检查，不能用 worker 提前一秒的日期代替外层交付日期。

## 准备和唯一执行

模块有独立显式 CLI，使用现有项目 Python 从业务目录调用，无个人目录硬编码：

```sh
.venv/bin/python -B -m stockfish_core.yaogu.saved_r2_daily_coordination_v1 --mode prepare \
  --run FULL_RUN --run-sha256 EXACT_SHA \
  --input ORIGINAL_INPUT --input-sha256 EXACT_SHA \
  --sources-manifest SOURCES_V2 --sources-manifest-sha256 EXACT_SHA \
  --output-dir NEW_PRIVATE_JOB --membership-mode DATED_ONLY
.venv/bin/python -B -m stockfish_core.yaogu.saved_r2_daily_coordination_v1 --mode execute \
  --plan NEW_PRIVATE_JOB/registered-plan.json --plan-sha256 REGISTERED_SHA
```

prepare 仅流式整件 SHA/完整指纹登记，不解析巨大 run/input，不发行 capability。登记单次 120 秒，256MiB 是 OS peak RSS 上限，不是累计读取返回量；原件单份 8MiB、run/input 单份 256MiB、独立源总量 2GiB；运行环境二进制另作身份登记，不扩行情单份限。源/实现初末变化拒绝；最后计划写入后也核硬上限，出现 prepare-failure 的计划不能执行。输出必须新目录，建议系统 TMP 独占目录，不能覆盖旧计划或产物。

execute 以 exclusive claim 保证同计划最多一次真实 child。child 在原 1800 秒、单进程 2.5GiB、采样进程树 3GiB 内，从实际完整 run/input 核全部保存资格集合、每行、决策及 selected ID 顺序，动态生成旧 `SAVED_R2_DELIVERY_RUN_MANIFEST_V1`；没有固定日期、run_id 或股票数量。旧 V3 的完整冻结验证器再核原池、名字、数值、配置/实现身份和正式导出；保存集合验真不替代上游历史资格 owner 的认证。

随后只调用一次 `deliver_saved_r2_stream`。V3 自己认证原 RAW/receipt 并发行当次 reader，完整 67 会话窗口不压缩，经济解释/归档展示按原合同处理；普通 JSON/计划不能恢复 capability。完整观察股、模型、发酵、活跃分歧及 UNKNOWN 不遗漏；整体经济方向梯度、限价、资金、龙虎榜、可成交性与历史成员缺口仍按原合同保留。

父进程在原生 producer 运行期间实时观察整个新任务树（含 journal、NON_DELIVERY_AUDIT、console、正式导出和控制），合计 512MiB、单件 256MiB，保留控制空间，超界或 ps 失败 kill/reap。终态再次检查文件大小、时间、实际 child OS 峰值、同池绑定、返回次数、完整源和 plan/claim SHA/指纹。RSS 是实际 ru_maxrss 与采样树检查，不冒充 OS 硬 RLIMIT；物理磁盘 IO 未测。

ps 有 2 秒 timeout，空表、坏行或活跃 worker 缺测均为 UNKNOWN 失败。父 RUSAGE_CHILDREN 终态峰值包括所有已回收子进程（含 ps），记录前后原值，保守要求终态不超限，不作差、不冒充唯一 worker 峰值；因此 result 最后写入后的真实内存峰也不能靠低采样逃过上限。原件、实现、运行环境、plan/claim、worker-result 和 detailed actual 在 actual 写入后再做整 SHA/完整指纹闭合；seal 后再核完整指纹。claim 缺失保存 null 与 changed_fields，并封 FAILED，不在诊断构造时跳过封存。

执行 claim 后预分配固定 16KiB 的 execution-seal.json，作为最终权威；它包括在原输出预算内。worker 最后写结果后，outer 还要核整个输出树、wall/RSS 和实际资源。actual-execution.json 是详细诊断，不能越过最终 seal 的 FAILED。采样之间突发增长若超界，只封存失败，不扩预算或删除现场；固定 seal 原地更新不增加文件大小。核心守卫提前最多 2 秒给控制封存留时，最终 seal 完工后再核硬上限。

失败产物封存且不自动重试、扩额或删除。源/身份/时间错误不降级为完整成功；V3 自己允许的新解释 UNKNOWN 仍只代表原完整清单可交付。ECONOMIC_DISPLAY_ARCHIVE_CHANGED 的唯一许可分支按实际 display unwind 记账，其他 native 异常不能作为正常返回。原生尝试/返回、外层成功、数据就绪、通知调度及投资验证分别记账。

## 尚未闭合的全链路

本组件不自动发现权威服务地址、续采当天缺口、导出完整 input/history owner 或提交新 R2 作业；这些继续使用已有采集/策略 API，须逐次验收。SOURCES_V2 是真实市场/题材交付能力，不是通用价格与历史资格 owner。市场解释附件、资金/席位默认接线、跨方向梯度、原聊天实际通知和连续交易日可靠性仍是独立节点；此阶段完成不能宣称整个无人值守每日工作流完成。

## 小样本工程验证

TEST_FIXTURE 使用独立系统 TMP、既有健康 Python 环境和 unittest；不安装依赖，不写业务 DB，不读取真实 RAW 或旧 run。测试覆盖动态 0/2/17 全池及 13 股完整输出、原件日历的收盘/周末/节假日边界、目标全文 hash 与内外声明冲突、重复/缺失/晚到日历、同钟冲突、陈旧目标零 native 调用、未来生成与收盘前 cutoff、outer 日历跨界、同时 execute 只产生一个真实 V3 child、失败后的重复 claim、指纹变化字段、ps 失败与 kill/reap、空坏 ps、实时 journal 增长、输出终态超界、详细 actual 之后增长、claim 缺失/最后变化、result 后 RSS 漏采样、prepare 最后写入超时和残留子进程。监督器资源反例替换为微型真实子进程，只验证外层守卫；不称其为真实日常 native 作业或投资验证。
