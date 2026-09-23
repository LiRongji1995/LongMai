# StockRecommendation Phase 1：每日荐股闭环升级方案

日期：2026-09-23，Asia/Shanghai。本文记录本轮源码实现与使用合同；不代表本机生产服务已经升级。

## 1. 目标与边界

【事实】Phase 1 将已有 Daily Primary、不可变 run、证据和后验入口连成“产生 → 保存 → 解释 → 逐日观察 → 描述性评价”的每日研究闭环。研究范围保持沪深主板普通 A 股；四类输出为 `FOCUS / WATCH / UNKNOWN / REJECTED`。没有合格候选时保留零候选，没有就绪输入时报告阻塞。

【事实】`STAGE_AWARE_MOMENTUM_V2`、四个 setup、原证据标准、readiness、PIT/provenance 和既有 `DAILY_RECOMMENDATION_SCORE_V1` 不由新投影或后验消费者修改。系统不连接券商、不下单，不模拟本期推荐的真实成交，不自动调参；`MARKET_EDGE=NOT_ESTABLISHED`。

【推断】本阶段有意义的验收是“每天是否有可追溯的交付或失败记录、每个结论能否回到原证据、到期观察是否完整”，不是荐股数量或收益必须达到某个值。

## 2. 复用架构与新增模块

【事实】权威数据仍在现有 SQLiteStore 和 state/artifact 路径。生产策略仍由 Python Daily Primary 执行；Go 保留原事实契约和影子校验角色，本期没有 Go 生产切换。

| 模块 | 本期职责 |
|---|---|
| [daily_research.py](../stockfish_core/yaogu/daily_research.py) | 冻结 run 的轻量身份、完整四类快照、原规则解释和 Markdown 日报；纯投影，无新行情或策略计算 |
| [daily_research_outcomes.py](../stockfish_core/yaogu/daily_research_outcomes.py) | 原 FOCUS/WATCH 的 T+1..T+20 价格观察、revision 与分组统计 |
| [daily_research_service.py](../stockfish_core/yaogu/daily_research_service.py) | 在现有服务/数据库中保存和读取派生产物，按日期合并交付状态，提交后台观察任务 |
| [scheduler.py](../stockfish_core/yaogu/scheduler.py) | 日常 critical capture、有界重试、漏跑识别及后台研究调度 |
| [candidate_api.py](../stockfish_core/yaogu/candidate_api.py) / [codex_research.py](../scripts/codex_research.py) | 同一权威结果的 API、网页与 Codex CLI 出口 |

【事实】新 run 的快照与原 run、detail/projection 在同一事务保存；不产生第二套荐股数据库。旧 run 如无新快照 artifact，可只读派生，不重新跑策略、不在 GET 写入。后验 revision 追加保存，原推荐不因未来涨跌或资料修订被覆盖。

## 3. Research Identity 与 Recommendation Snapshot

【事实】快照版本为 `DAILY_RECOMMENDATION_SNAPSHOT_V1`。轻量 `identity` 包含：

| 字段 | 含义 |
|---|---|
| `run_id` | 原冻结运行，不以当天重新计算替代旧 run |
| `observation_date` | 产生信号的行情观察日 T |
| `target_session` | 该记录对应的目标交易会话，来自冻结字段或已核验日历 |
| `strategy_version` | 原 run 保存的策略实现版本 |
| `config_version` | 原冻结配置身份/哈希 |
| `data_cutoff` | 原决策实际资料截止，不以报告下载时间替换 |
| `generated_at` | 原运行生成时间 |
| `execution_status` | 对应 attempt 的执行状态；仅能证实旧 run 已保存时为 `SAVED`，不能借此声称通过本期 readiness |

【事实】原 `spec_revision`、input/hash、epoch、source hash、coverage/prospective 身份继续保留引用。缺失历史字段为 null/UNKNOWN，不从当前配置补写。`snapshot_hash` 校验快照内容，`source_run_hash` 回指原运行内容；这些是内容一致性证据，不是投资有效性证明。

【事实】每股保存 `run_id`、证券 ID（含交易所）、原 `decision_status`、`primary_setup`、reason codes、简明原判定、关键 evidence、风险/未知、原 detail 引用/哈希和冻结可比收盘基准。完整四类保留原顺序；日报展示完整 FOCUS/WATCH，并汇总 UNKNOWN/REJECTED 的原原因，不把 `unselected` 整组当成 WATCH。

【事实】解释由原 reason codes/conditions 派生。关键证据可展示原 MA 数值/斜率、量价事实、所选 setup 与行为确认；逐股 detail 引用和 run 级来源分开。没有未来证据、模型补写或独立投资判断进入原解释。

## 4. T+1 至 T+20 的价格观察口径

【事实】对原 FOCUS/WATCH，每个后续交易会话保存一个逻辑位置，共 20 个。T 是 `observation_date`，T+h 来自已核验交易所日历，不按自然日或工作日猜测；缺未来日历时该位置不可评价。停牌也不会令 T+h 顺延到证券下一次成交日。

【事实】令 `P0` 为原快照冻结的 `comparable_close`，`C_h / H_j / L_j` 为同一可比基准下 T+h 的收盘和各日最高/最低：

```text
close_reference_change(h) = C_h / P0 - 1
cumulative_high(h)        = max(H_1, ..., H_h)
cumulative_low(h)         = min(L_1, ..., L_h)
mfe_bound(h)             = cumulative_high(h) / P0 - 1
mae_bound(h)             = cumulative_low(h) / P0 - 1
```

【事实】返回值使用小数变化口径，例如 `0.05` 为 5%。MFE/MAE 保留有符号极值，不强制以零截断；窗口只含 T+1..T+h。这不是最大回撤、资金曲线或可成交收益，P0 也不是假设买入价。

【事实】终点收盘变化与窗口路径完整性分别评价：中间缺一天时，若 T+h 本身合法，终点变化仍可有值；MFE/MAE 必须等该窗口所有会话的身份、价格和 comparable high/low 合格后才有值。原基准缺失不能从后来下载的价格补回。来源晚到按实际取得时钟产生新 revision，不伪装成当时已经可得。

| 观察状态 | 含义 |
|---|---|
| `NORMAL` | 本会话终点所需合法证据可用；仍需另看 `path_status` 和 high/low |
| `NOT_MATURED` | 本会话尚未收盘成熟 |
| `MISSING` | 到期但缺价格观察 |
| `SUSPENDED` | 有日期化停牌证据，保留该位置 |
| `UNAVAILABLE` | 日历、身份、来源可见性、退市/价格可比性等证据不能支持计算，具体原因另存 |

【事实】未来、缺失、停牌、退池、不可比和无效修订不填零，也不从信号分母删除。数值、price/path 状态、reason codes、来源引用、input/hash、评估时钟和逻辑 observation/revision ID 一起保存。按 `as_of` 选当时可见的最新 revision，包括后来将原数值判为不可用的修订。

【事实】当前 `expectation_status=NOT_DEFINED`：原策略没有预先冻结可直接用于本闭环的“达到预期”判据，系统不会根据后来的涨跌补写预期成功/失败。

【建议】若以后需要 setup 预期命中率，应先独立版本化研究观察判据，再用于新记录；不得改旧策略、回填旧预期或把普通上涨比例称为预期验证。

## 5. 统计分组与分母

【事实】分组保留 strategy/config、validation epoch、coverage/evidence type、原决策层级、setup 和 h；正式前瞻、预备研究、历史重建、TEST_FIXTURE 分开。不同版本或证据身份不会为凑样本合并。相同快照重复读取不增加样本，但同股不同日推荐仍是不同信号，并披露独立证券数和信号日数。

【事实】历史 `as_of` 统计要求所有已保存的 cutoff/生成/发布/采集完成时间都不晚于该时点；服务还检查已知任务的原子完成时间，生成后但未完成保存的结果不能提前显示。可用时间全缺时不纳入统计，原快照仍可直接读取。`snapshot_availability` 分别记录纳入、尚未可见和可用时间未知的快照数量，不以当前已存在倒推过去已可见。

【事实】每个 h 的 `signals` 分母包含该组全部原 FOCUS/WATCH 信号；`matured` 按交易会话收盘是否已到确定，与有没有价格分开。另列 `not_matured`、`calendar_unavailable`、`missing_labels`、有效终点和完整路径数量，以及到期仍缺有效结果的积压。

```text
maturity_rate     = matured / signals（存在未知日历时不计算）
valid_outcome_rate = valid_return / matured（无成熟样本时为 null）
up_rate           = positive / valid_return（无有效终点时为 null）
```

【事实】报告展示收益、MFE/MAE 的描述性分布和成熟/有效覆盖。上涨比例只是有效价格终点上涨占比，不是交易胜率；T+1..T+20 不是 20 组独立交易。输出 `ranked=false`、`validation_status=NOT_VERIFIED`，不自动给 setup 排赚钱能力名次。

【推断】只有把样本数、独立日期、缺失率和资料性质一起看，描述性统计才可能帮助决定哪些 setup 值得后续研究；当前数据不能证明某个 setup 有市场优势。

## 6. 每日任务及失败闭环

【事实】关键路径继续是：交易日历/新鲜度 → `DAILY_PRIMARY_CRITICAL` 数据刷新 → 原 feature/evidence 与 readiness → active V2 → 原子保存 run/快照/待办 → 可读日报。readiness 未通过仍为 `NOT_READY`；策略、保存或导出等失败为 `FAILED`，不制造策略 run 或合法空名单。

【事实】源码保留 20:30 晚间触发并增加 `DAILY_RESEARCH_LOOP_SCHEDULE_V1`：初次加最多两次重试，5/15 分钟退避，目标交易日 08:45 后停止自动重试。已发布结果、用户取消以及确定性配置/存储冲突不自动重试。每 attempt 有 30 分钟协作式期限；provider 自身有限超时继续有效，不声称能硬中断任意阻塞代码。

【事实】已有 generic 人工 retry 纳入同一原任务族；worker 开始时若该族已发布，则复用原 run。离线漏跑登记保留原计划时间和实际发现时间，只处理已经登记的计划，不为未登记历史补造采集。单 worker 已经执行中的旧任务不会被强行抢占。

【事实】未运行/漏跑、排队、运行中、未就绪和失败均可在按目标会话的日报中解释。`MISSED` 可由只读查询对已登记计划、已过交付截止却无尝试的会话给出；无登记计划的历史日期为 `NOT_READY / NO_REGISTERED_DAILY_PRIMARY_PLAN`，不能虚构过去漏跑。正在重试时显示 RUNNING/QUEUED 并保留上一尝试原因；已完成发布的 READY 不被后来失败撤销。报告保留原原因、阶段、时间、blocker、缺失 evidence 与保存的错误。

【事实】后台 outcome、主题/资金等研究工作不阻塞 readiness 或撤销已发布名单。当前代码在交易日 21:15 后允许有界后台采集，最多三批，每批预算为 32 个请求/120 秒；原 provider 队列、receipt 和正常来源规则继续约束预算。后验每个 tick 最多提交 5 项，primary 未完成及可重试窗口优先，后台按原 run/input/policy 去重，积压和缺失保留。

【需要验证】调度只在现有服务进程运行时有效。本轮没有建立开机系统服务，没有切换用户现有实例；上述重试、预算和协作式期限是源码合同，真实 provider 连续多日的完成率、排队延迟和覆盖仍须运行验证。

## 7. API、网页与 Codex CLI

【事实】在核验后的同一服务 base URL 使用以下入口；示例路径不代表本机此刻端口或进程已验证。

| 方法/路径 | 用途 |
|---|---|
| `GET /daily-research` | 用户日报页；日期或 run_id 二选一，只读系统 Markdown，可下载 |
| `GET /api/daily-research/report?target_session=YYYY-MM-DD` | 按目标交易日读取交付状态/快照/历史统计；可没有 run |
| `GET /api/daily-research/report?run_id=RUN_ID` | 读取指定原运行；不能同时传 target_session |
| `GET /api/daily-research/runs/RUN_ID/snapshot` | 原冻结四类快照 |
| `GET /api/daily-research/runs/RUN_ID/outcomes` | T+1..T+20 保存后验；可传 `as_of`、`instrument_id` |
| `GET /api/daily-research/analysis` | 分组统计；可传 `as_of`、`strategy_version`、`run_id` |
| `POST /api/daily-research/runs/RUN_ID/outcomes` | 显式提交追加观察任务；body 只接受已登记 `input_id`，不接受人工收益/状态 |

【事实】日报支持 `as_of`（带时区）和 `format=markdown`；默认 JSON 包含 `report_as_of`、`delivery`、`snapshot`、`history`、`markdown`。日报页只用文本展示服务报告；API Key 如需要，仅在页面内存使用。只读 GET 不触发行情采集、重新选股或历史 artifact 写入。

【事实】单股后验查询返回 `instrument_id`，其统计仍明确标记 `summary_scope=RUN`。无 FOCUS/WATCH 的 run 返回 `NO_TRACKED_RECOMMENDATIONS`；查询 UNKNOWN/REJECTED 返回 `NOT_TRACKED_DECISION`，不制造后验采集待办。

【事实】CLI 在业务目录运行，沿用现有 base URL/凭据管理；全局 `--base-url` 等参数放在子命令前。`RUN_ID`、`INPUT_ID` 必须取真实响应，日期必须用已核验交易日。用户可继续用自然语言请求，由 Codex 执行这些现有入口。

```powershell
./.venv/Scripts/python.exe -X utf8 scripts/codex_research.py daily-report --target-session YYYY-MM-DD
./.venv/Scripts/python.exe -X utf8 scripts/codex_research.py daily-report --run-id RUN_ID
./.venv/Scripts/python.exe -X utf8 scripts/codex_research.py daily-outcomes RUN_ID
./.venv/Scripts/python.exe -X utf8 scripts/codex_research.py daily-outcomes RUN_ID --instrument-id INSTRUMENT_ID
# 显式追加任务；只读查询不要添加 --update
./.venv/Scripts/python.exe -X utf8 scripts/codex_research.py daily-outcomes RUN_ID --update --input-id INPUT_ID --wait-seconds 30
```

【事实】`daily-report` 将 `report.json`、`report.md`、文件哈希 manifest 保存到被忽略的 `reports/codex/daily-loop/` 新目录；`--output` 指定目录必须不存在。API 只读与本机保存私有报告是两个动作，不覆盖旧文件或提交研究数据。`daily-outcomes` 默认只读；202 只表示任务已受理，不表示后验完整。

## 8. 工程验证与待验证范围

【事实】本轮最终整合回归为 297 passed、退出码 0；实际完整命令、状态边界和受保护文件哈希见 [工程验收记录](PHASE1_DAILY_RESEARCH_VALIDATION.md)。下文 20 项为较早的快照模块定向检查，不是最终总数。

【事实】本轮快照/renderer/HTML 定向检查实际执行：

```powershell
./.venv/Scripts/python.exe -m pytest tests/r05_product/test_daily_research_snapshot.py -q
```

【事实】该次结果为退出码 0、20 passed in 0.28s。HTML 内联脚本提取后执行 `node --check`，退出码 0。测试覆盖四类原判定、旧身份/冻结基准、完整列表、未来详情不覆盖、失败日、T+1..T+20 展示、数值来源与只读页面边界；输入为显式 TEST_FIXTURE。其他模块的本轮最终测试结果由统一交付验证记录补充，不复用旧文档测试数。

【需要验证】本轮未升级或重启生产服务，未改真实 DB/state，未执行在线 provider 采集验收，未完成自然成熟的 20 会话观察，也未证明策略盈利、setup 优势或真实盘前连续交付 SLA。工程测试通过、任务成功、health 正常、readiness 就绪和投资有效性分别报告。

【建议】后续真实使用先核对实例/代码/数据库身份，在获得部署授权后进入连续观察；每天记录应交付会话、实际交付/失败、FOCUS/WATCH/UNKNOWN/REJECTED 数量和后验积压。禁止强制股票数量、自动修改策略或将 UNKNOWN/缺失收益填零。
