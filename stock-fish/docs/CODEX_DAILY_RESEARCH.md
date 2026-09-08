# Codex 日常研究操作协议

核对日期：2026-09-08（Asia/Shanghai）。供项目中的 Codex 按需读取；用户直接说研究请求即可。完整背景见 [项目综述](PROJECT_OVERVIEW.md)，页面与部署细节见 [README](../README.md)，本次实测见 [首用验证](CODEX_FIRST_USE_VERIFICATION.md)。

## 1. 请求与权限

| 用户怎样说 | Codex 应怎样做 |
|---|---|
| 给我下一交易日的完整研究名单 | 核对本机服务、交易日历、最后结束日和保存运行；本项目已确认使用 PV 观察链路作为日常研究语境，明确其为 R05 实验，不修改正式 DAILY 默认配置；读同一 run 的全名单、焦点与跟踪 |
| 给我明天的名单 | 将“明天”先解释为上海日期的下一自然日，再按交易所日历定位该日或之后首个交易日；同时说明最近结束的观察日，不把未来目标日当成已有行情 |
| 写一份短线市场和个股分析 | 先读保存名单与覆盖，生成私有报告；事实、程序计算、Codex 推断和未知分开。只有局部主板数据时，报告标题与结论注明范围，不能推断全市场、题材或资金共识 |
| 昨天那些股票后来怎样，继续跟踪 | 找原信号日的原 run，不用今天规则重算；读后验、episode 和复盘。只有新合格输入时才更新原跟踪；未成熟保留空值 |
| 某股为什么入选／没有入选 | 读该 run 的详情和正式条件；区分未入全名单、未入焦点、数据不足和不在研究范围。没有可核验的排除证据时写未知 |
| 解释规则、指标、权重和理念 | 先读当前配置与保存运行的版本；详细公式查综述和对应源码。解释旧 run 时以其冻结快照为准 |
| 给我最新项目综述，供新对话讨论 | 指向仓库中的 PROJECT_OVERVIEW.md；若发现受本次授权变更影响的内容过期，同步相关段落，不重做全仓审计 |
| 讨论要不要调整策略 | 仅分析假设与代价。用户明确要求实施后，才进入工程变更与相应测试 |

普通研究请求允许读取、必要的现有免费采集/计算、保存报告及按请求更新研究状态；不自动授权改正式策略、改生产源码、提交或推送。付费数据、额外付费研究、模型升级或增加预算另需明确授权。本次入口与综述交付已获正常提交推送授权，不能延伸为以后所有查询的 Git 授权。

## 2. 每次执行的最短路径

1. 读取本协议及所需配置。先 GET `/api/health`，只汇报应用、作业、研究、跟踪与模型状态；不要输出环境、密钥或无关旧组合任务。地址必须来自当轮实际核验；历史默认是 `http://127.0.0.1:8000/`，R11 本机验证因 8000 被占用使用 `http://127.0.0.1:8001/`。
2. 读取交易所日历和真实保存运行。分别列出请求目标交易日、最后可用观察日、生成时间、决策截止与输入水位。日历超出覆盖时停止推断，不按周一至周五猜节假日。
3. 优先复用保存结果。核对 input_id/input_hash、策略/配置、实现一致性、截止和覆盖；旧输入已有不匹配结果时解释差异，不自行覆盖或悄悄再算。历史查询无需满足当前实现版本，仍必须保留原版本。
4. 需要新资料才使用现有采集入口；检查是否有在途任务，单次有界执行并等待或返回 job_id。HTTP 202 只是已受理，SUCCEEDED 也不等于数据合格；必须另看质量、激活和覆盖。鉴权失败不循环重试。
5. 新运行仅使用已登记、已核验的真实输入。现有服务没有原子幂等运行接口，CLI 的 `run` 只精确查找可复用运行；若返回 `INPUT_VERIFICATION_REQUIRED`，由 Codex 核对来源、日期、覆盖、现有任务与用户范围后，调用一次原有 POST `/api/strategy-experiments/run`。响应不明时先查任务/运行，不盲目重发。不要为增加重复样本改截止。
6. 同一 run 读取全量、焦点、个股详情、官方 JSON/CSV 导出；检查证券顺序、数量和源内容哈希。聊天可摘录，保存的完整名单不可截断。数量来自正式条件，不凑 17、20 或 30 只。
7. 按需读取跟踪、后验和复盘；有新数据且用户要求继续跟踪时使用现有更新入口。不把缺失/未成熟计作零或失败，不把多个标签周期数当独立股票样本数。
8. 报告保存到被 Git 忽略的 `reports/codex/`；回应包含 run_id、路径、覆盖和阻塞。普通查询不创建空提交，不公开运行数据。

## 3. 当前调用入口与安全边界

薄封装是 [scripts/codex_research.py](../scripts/codex_research.py)，由 Codex 在业务目录使用现有 Python 运行；用户无需自己执行。它调用现有产品 API，官方公式与名单计算仍在服务端。参数以 `--help` 为准；没有聊天服务、自动模型路由、新的行情提供商或券商连接。

供 Codex 调用的命令合同（工作目录为 `stock-fish`，以下 `RUN_ID` / `INPUT_ID` 必须来自实际响应）：

```powershell
./.venv/Scripts/python.exe -X utf8 scripts/codex_research.py status
./.venv/Scripts/python.exe -X utf8 scripts/codex_research.py rules
./.venv/Scripts/python.exe -X utf8 scripts/codex_research.py list
./.venv/Scripts/python.exe -X utf8 scripts/codex_research.py select --profile PV_OBSERVATION_V1 --target next-session
./.venv/Scripts/python.exe -X utf8 scripts/codex_research.py select --profile STAGE_AWARE_MOMENTUM_V2 --target next-session
./.venv/Scripts/python.exe -X utf8 scripts/codex_research.py detail RUN_ID 601086.SH
./.venv/Scripts/python.exe -X utf8 scripts/codex_research.py export RUN_ID
./.venv/Scripts/python.exe -X utf8 scripts/codex_research.py report RUN_ID
./.venv/Scripts/python.exe -X utf8 scripts/codex_research.py track RUN_ID
```

新版《StockRecommendation：阶段感知的强势股研究框架与落选反思》对应主动核心 `STAGE_AWARE_MOMENTUM_V2`；2026-09-08 已提升为 `R11_STAGE_AWARE_MOMENTUM_V2_VALIDATION_SCORING` / `R11_VALIDATION_SCORING`。`STAGE_AWARE_MOMENTUM_V1` 冻结为历史审计和只读兼容。它不重写旧 PV/焦点历史，也不删除上一版 `THEME_CAPITAL_TREND_V1` 对照；初始研究参数必须标明 **尚未验证**，`MARKET_EDGE=NOT_ESTABLISHED`，并与用户硬性条件分开说明。日常可直接说：

- “把下一个交易日的荐股清单给我。”
- “给我一份当前短线强势股和市场状态报告。”
- “为什么国芳集团当时没有进焦点？”
- “金健米业为什么被A过滤？”
- “哪些股票处于高位分歧，但修复很强？”
- “哪些股票属于趋势整理？”
- “最近一周没选上的股票是不是反而更强？”
- “比较原PV、旧焦点和阶段感知策略。”
- “给我最近一次 DAILY_RECOMMENDATION_SCORE_V1 成绩卡。”
- “给我策略调整建议，但不要修改正式策略。”

执行时先查保存 run 或 `reports/codex/` 派生产物是否已经包含同一输入 hash、同一 `STAGE_AWARE_MOMENTUM_V2` implementation/spec revision。没有合格长窗价格、题材、资金、市场覆盖或行为确认时，输出 `WATCH/UNKNOWN/INSUFFICIENT_EVIDENCE` 和具体缺口，不用旧 PV 冒充阶段感知正式名单。完整规格见 [STAGE_AWARE_MOMENTUM_V2.md](STAGE_AWARE_MOMENTUM_V2.md)，旧 V1 首轮审计保留在 [STAGE_AWARE_MOMENTUM_V1.md](STAGE_AWARE_MOMENTUM_V1.md)。上一版板块资金趋势对照仍见 [THEME_CAPITAL_TREND_V1.md](THEME_CAPITAL_TREND_V1.md)。

2026-09-08 R11 交付后，旧 R05 V4 PV 保存运行可按已知源码哈希白名单只读复用；这只适用于 `PV_OBSERVATION_V1`，不能让旧 run 假装已经包含新版策略。R10 的 2026-09-07 V2 输入 `market_ac9666cc9d0ddc4f60a270828cadfc9aa37f940572148045f1b531b54c0ce0fd` 给出 FOCUS 0、WATCH 40、REJECTED 2887、UNKNOWN 114，若日常回复引用该清单，必须说明它是 WATCH 研究队列，不是正式 FOCUS 推荐。R11 的 2026-09-08 bounded capture input hash 为 `b7bfe39df96b574b357348f3a506ea1e4edfa519a7053103976c132be6ff12c1`，但当前 eligible/FOCUS/WATCH 均为 0，等待 20:30 official primary。

“非正式 FOCUS”只允许作为口语解释：9.7 输入是盘后手工/预备研究输入，不是 20:30 scheduler 写入的 official primary，且 FOCUS 硬条件中长窗趋势、市场兼容、PIT 题材/资金仍为 UNKNOWN；因此只能给 next-session research list 或 WATCH queue，不能说成正式 FOCUS 推荐。

`select` 会保存全量导出及事实简报；`report` 读取指定原运行生成事实简报，Codex 可在同目录另写注明推断边界的分析，不改官方 source.json。`--target tomorrow` 与 `next-session` 不同；历史原榜用 `list` 找 run 后读取详情/导出。`run --profile PV_OBSERVATION_V1 --input-id INPUT_ID` 只做精确输入复用。`capture --wait-seconds 30`、`track RUN_ID --update --input-id INPUT_ID --wait-seconds 30` 是显式写操作，不在只读查询中默认执行；等待最多 60 秒，随后用 `job JOB_ID` 继续查看。

`--base-url`、`--env-file`、`--timeout` 等全局参数必须放在子命令前。默认不读取其他环境文件；需要 Key 时可选已有私有文件且只提取 `STOCKFISH_API_KEY`。输出目录可用 `--output` 指定，必须不存在；拒绝覆盖旧报告。完整响应保存在私有产物，日常回复只展开需要的字段。

默认重复查询会校验原 run、源内容哈希、目标交易会话、当前复盘和既有文件哈希；一致时复用本机目录，返回 `reused_local_bundle=true`，避免反复复制大导出。旧简报保留创建时文案，不因模板措辞更新而复制行情；需要新的解释时另写小型分析。采集/跟踪先复用已在途的同类任务；任务列表窗口已满且无法排除更早在途任务时返回 `JOB_WINDOW_FULL_CHECK_INFLIGHT_REQUIRED`，由 Codex 进一步核对，不据此盲目再发。

| 操作 | 已有产品入口 |
|---|---|
| 状态、数据缺口 | GET `/api/health`、`/api/candidate-data-pipeline`、`/api/universe/coverage` |
| 规则与保存运行 | GET `/api/strategy-configs/current`、`/api/strategy-experiments/specs`、`/api/strategy-experiments` |
| 指定 run 的摘要、详情、导出 | GET `/api/strategy-experiments/<run_id>/summary`、`/items/<instrument_id>`、`/export` |
| 原运行跟踪与评价 | GET 同一 run 的 `/tracking`、`/outcomes`、`/review`；有新合格输入时 POST 现有更新入口 |
| 每日推荐成绩 | GET `/api/recommendation-scores/latest`、`/api/recommendation-scores`、`/api/recommendation-scores/<run_id>`；只读保存的 score revision，不接受公式覆盖参数 |
| 有界采集和运行 | POST `/api/candidate-data-pipeline/capture`、`/api/strategy-experiments/run`；GET `/api/jobs/<job_id>` 检查终态 |

`DAILY_RECOMMENDATION_SCORE_V1` 是表现观察，不是策略输入。它只在 outcome 成熟且保存了 score revision 后显示分数；没有 FOCUS、未成熟、benchmark coverage 不足或没有 official primary 时，显示 `N/A/NOT_MATURED/INSUFFICIENT_BENCHMARK_COVERAGE/NO_SCORE_REVISION`，不得把缺失当 0，也不得用 score 自动调参。

`selection=all` 是全名单，`focus` 是额外门槛通过者，不是全名单前 N 名。正式导出 JSON 中实现 source_hash 与名单源内容哈希不同，按 `export_view.source_content_hash` 对照运行 source_run_hash，不能把不同用途的哈希判作失败。

本机 API Key 若需要，由已有安全环境提供给进程；不得把真实 Key 放命令行、聊天或报告。输入登记不是 HTTP 上传功能。已有合法历史文件只能通过 [yaogu_admin.py](../scripts/yaogu_admin.py) 按部署规范登记；数据库单写入服务锁必须尊重，测试/合成输入只进隔离状态。普通研究不自动停服务登记文件或切换数据库。

## 4. 启停、自动步骤和持久化

本机历史运行目录是 `D:/StockRecommendation/stock-fish`，发布目录是 `D:/StockRecommendation/publish/stockfish-qlib-quant-system`，二者不能仅凭目录名视为同一实例。R11 当前验证在 `D:/stockfish-qlib-quant-system/stock-fish` 启动本地 Python 服务；因 8000 被其他进程占用，验证地址为 [http://127.0.0.1:8001/](http://127.0.0.1:8001/)。

电脑重启后，可直接对 Codex 说“核对并启动 StockFish，读取上次名单”。由 Codex 先查正确实例；没有运行时按现有根目录 `Start-StockFish.ps1` 启动，停止用 `Stop-StockFish.ps1`。本机命令为 `pwsh -NoProfile -File D:/StockRecommendation/Start-StockFish.ps1` 和对应 Stop 脚本。发布检出脚本支持 `-Root`，但本机发布目录没有现成运行环境/私有数据，不能误启动空库替代现用库。详细部署见仓库根目录 DEPLOYMENT.md。

本机 SQLite 为 `data/stockfish.sqlite3`，证据与状态为 `data/core/`，报告为 `reports/codex/`；这些数据不随源码推送。关闭浏览器不会停止服务或删除名单。停止服务期间没有采集；重开 Codex/浏览器后读取保存 run。备份同时包含库和状态证据，使用 README 中的现有备份工具，不能复制正在写入的裸 SQLite 文件代替一致性备份。

在线调度按现有交易日历在 16:10、20:30 进行盘后采集/DAILY 或 FUNDS 流程，09:26 竞价复核依赖有效快照；不是开机启动项，也不补造错过的数据。已有 PV 的盘后研究可更新后验、跟踪与反馈，**没有自动生成新 PV 名单的每日调度**。要更新 PV，可说“检查新数据，按既有规则更新下一交易日研究名单”；Codex 先复用，必要时采集、检查后再运行。

网页是可选入口：点击“采集 / 续采全主板数据”，就绪后点 R05 运行；历史选择器读取前一天结果，个股详情和持续跟踪入口查看原记录。不能以页面里显示的旧日期直接冒充今天的新计算。

## 5. 输出格式与失败时仍可用的功能

每份名单/报告至少标明：目标交易日；观察日、生成时间、截止与水位；策略与版本、run_id、真实/重建性质；覆盖分母和缺口；全名单与焦点数量；至少一个可解释个股；原运行跟踪成熟度；完整保存位置。涉及模型推断时另标其来源范围、时间和不确定性。

| 情况 | 继续做什么 | 不得声称什么 |
|---|---|---|
| 缺新数据、供应商网络失败 | 读最后真实保存名单、规则、详情、导出、已有跟踪；展示最后日期和缺口 | 新行情已更新、失败就是合法零候选 |
| 合格输入计算后确为零 | 保存并解释 EMPTY 及覆盖 | 为可展示而填充样本 |
| 只有历史重建、局部主板覆盖 | 保留 RECONSTRUCTED_HISTORICAL / LIMITED_SCOPE 标签，做限定范围研究 | 当时可获得的前瞻信号或全 A 市场结论 |
| 后验未成熟、模拟成交不可算 | 继续名单和研究；分别显示 NOT_MATURED、缺可比价、缺可成交性 | 零收益、失败样本、已验证胜率 |
| 应用 LLM 未配置 | 规则计算、历史读取、导出与跟踪照常；当前 Codex 可根据证据写报告 | 应用内 AI 已接通，填旧 LLM 变量就必然可用 |
| 服务离线 | 核对后使用既有启动脚本；无法启动时报告具体依赖/端口/合法凭据阻塞 | 改源码、删库或构建新 Docker 镜像是默认解决方式 |

Codex 对话消耗其自身账户用量，确定性 API 无需另调应用模型。日常与文档可先由用户选择 5.5 或较便宜模型；本协议不改变当前模型，也不固定价格、可用型号或推理参数。明确失败且普通核验解决不了时，再说明问题和预算申请定向更强模型。

## 6. 指引加载与维护

项目的 AGENTS.md 指向本协议，Codex 先读短协议，按任务读取综述的相关章节。依据 [Codex 官方 AGENTS.md 说明](https://learn.chatgpt.com/docs/agent-configuration/agents-md)，新会话按项目根目录到工作目录构建指引链，近目录指令可补充或覆盖上级；不能把刚写入文件等同于已验证新模型会话自动加载。

本机根目录、开发 Git 根目录和发布 Git 根目录均有相应指引。新会话应选择这个项目，并可直接说“按项目日常研究协议执行”。如用户在其他目录/网页对话开始，不能保证这些本地指引可见。

后续授权变更功能、来源、配置、策略语义、保存方式或权限时，同步综述对应段落、核对时间与验证范围；普通日报不触发重写综述或全仓审计。当前没有自动维护综述的后台任务。
