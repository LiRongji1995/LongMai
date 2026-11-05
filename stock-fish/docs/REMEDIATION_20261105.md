# 2026-11-05 八项整改实施报告

**状态：LOCAL_ENGINEERING_PASSED，等待本次发布的远端CI核验。最终发布检出15个受影响模块266项通过（1430.78秒、退出0、零跳过）；不能据此宣称生产已启用、连续交付或投资有效性通过。** 新能力位于 `ACTIVE_RUNTIME_V1`、V2 恢复合同和新增控制模块；旧已认证源码、旧运行登记、历史请求和旧策略保持原身份。**新版本未正式启用前，既有运行实例不能视为已修复。** `MARKET_EDGE=NOT_ESTABLISHED`。

基线读取始于2026-11-05；最终本地组合记录始于04:29 UTC。继续发布前再次核对开发/发布检出、源码SHA及远端main未变。远端提交与CI将据实际结果补录，不能用本地子模块通过替代。

| 最终验收项 | 当前记录 |
| --- | --- |
| 本轮授权文件清单及完整 SHA manifest | 62个授权文件；私有release-final-source-manifest记录逐文件SHA；不发布私有日志/DB/原始数据/完整本机包 |
| 发布检出的最终 commit / 远端 main SHA | PENDING_ROOT_COMMIT_AND_REMOTE_VERIFICATION |
| 主动树实际构建及最终 `verify` 回执 | TEST_FIXTURE 实际构建469个冻结原件、489个输出；verify与独立身份/依赖图重算通过，exit 0；manifest SHA `e3205c82704916728178805d96f58b4b7e70fb62905ec5472815c500a7cf0ab3` |
| 主动桥接全调用链与原专项测试组合 | 发布检出15模块266 passed，exit 0，0 skip；含新Authority家族、两层环境、原生后验、只读CLI及CI分组 |
| 最终同夹具完整成本比较 | 未建立最终同预算/完整成本的真实对照；原小夹具只用于数值与边界检查，不作为提速验收 |
| 最终测试退出码、失败、skip、时限 | 1430.78s（外层1432.617s）；JUnit SHA `5a05d5ea444414175a1970f48e5d97b7371d3012403b6ec774d002149f1d9579`；此前失败原件保留 |
| GitHub Actions run URL、目标 commit、各 job 结果 | PENDING_ROOT_CI_RECORD |
| 源码推送 | PENDING_ROOT_PUSH；未在初稿中预写成功 |
| 真实 authority / epoch / 服务切换 | NOT_EXECUTED；只做私有TEST_FIXTURE工程登记 |
| 当前业务完整盘前与连续 20 交易日验收 | NOT_ESTABLISHED / NOT_RUN |
| 独立投资有效性与真实账户曲线 | NOT_ESTABLISHED / BLOCKED |

## 一、范围、版本和真实基线

已阅读适用 AGENTS、部署及工程基线，复核当前策略约束。编写初稿时只读 Git 定位结果为：开发检出 `review`，HEAD `2064173d15c478093fc9b3db90931e2aa04391e4`；独立发布检出 `main`，HEAD `2bfbbbb8bd08b0f49c081b26ec77f7da61c7345e`。两者 origin 均指向 `LiRongji1995/stockfish-qlib-quant-system`。这些是**编写时定位**，不是本轮最终提交。开发 `stock-fish/` 中的用户已有改动不能整体覆盖到发布检出；最终只同步授权清单。

开发基线共有 442 项既有工作区变化（40 修改、10 删除、392 未跟踪）；这些不是本轮可整体发布的文件。发布检出初始 clean。第一次受保护文件核验因误把 10 项原有删除计作新增缺失而退出 1；修正为与原始缺失状态比较后退出 0，未恢复或删除用户文件。最终再按授权清单核对。

本轮独立核对过的真实保存样本，观察日为 **2026-10-30**、目标交易日为 **2026-11-02**，资料截止为 **2026-11-01T22:07:15.891409+00:00**，生成时间为 **22:10:49.725898+00:00**。其保存覆盖为 `RECONSTRUCTED_HISTORICAL`；master 5,227 条分为 eligible 3,043、已排除 1,562、身份 UNKNOWN 622。合格池观察选择 20、FOCUS 0，不能据合格池内决策未知为零推断全市场身份完整。原核心 PASS，原全流程 final FAILED；DAILY_V1 为 `UNKNOWN_NOT_EXECUTED`。核对了 manifest 与 observation 的保存内容绑定，但没有在本次工程中重新认证所有原始来源，也没有重新运行该失败业务。该样本不是 11 月 5 日的新盘前名单。

本次可确认的成果主要是：共同业务身份、完整范围核验后的失败继承机制、版本化主动树、持久阶段证据、标签隔离和条件账户对账。不能确认的是：最新真实市场完整交付、连续日常稳定性、来源/许可充分性、真实成本和容量、独立样本优势。没有启动获准的真实业务验收、全市场回测或交易操作；但本轮完整链测试发生一次未经授权的真实 provider 调用事故，详见下段，不能把它隐去或计作成功。

主负责人另已完成一次**真实登记的只读预检**：182 份 codex-chat 控制 JSON 的原字节前后 SHA 保持；13 个旧 operation 中，4 个 FAILED_OR_UNKNOWN 盘前业务可直接辨认，另 9 个 RECORDED 使用旧原生核心计算、复用及归档认证的复合 identity，不能无歧义映射到新业务身份。inventory 明确返回 `BLOCKED / BUSINESS_OPERATION_EXPLICIT_REQUIRED`。私有记录为 `real-recovery-readonly-precheck.json`、`real-recovery-mapping-gaps.json`；最终 SHA 与准确命令待主负责人补录。没有据此初始化真实 authority、激活 epoch 或重新认证来源。

因此，启用前至少还需：逐项核实这 9 个旧复合操作的真实业务语义并形成有证据的 mapping；由运维确认全部登记根及祖先分支已经纳入；核验旧操作真实累计预算和未知支出。4 个已识别旧失败继续阻塞。**不能把“获得授权”本身写成“即可启用”；当前真实恢复准入仍未满足。**

新的硬门禁同时覆盖旧网络范围未知：`FULL_LEGACY_IMPORT_PENDING` 在旧 provider queue、PARTIAL 和传输副作用尚未完成证据对账时保持 `LEGACY_SOURCE_SCOPES_UNRECONCILED`。恢复环境、换 epoch 或申请另一日期/范围都不能发行 network/provider 能力。当前没有接受操作者自报 `true` 的解锁开关；要恢复真实来源写权限，还需基于实际旧队列/传输原件实现并验收细粒度导入。这一工程与证据缺口没有被新建 authority 掩盖。 进一步核对原入口发现：`provider_budget.charge(path=None)` 可以不写传输预算；原 `provider_requests.execute_request` 的 budget/queue 参数可为空，而且原请求/结果原件在 worker 返回后才归档。网络已经发生、归档尚未完成时崩溃，可能没有足够条目；旧 transport 表也缺少参数和业务窗口。因而导入已找到的 queue/attempt 只能给出有限范围，不足以证明所有旧副作用都已登记。当前全局来源阻塞覆盖这一未知；不能接受操作者声明 complete=true 解锁。

### 隔离测试事故（保留原失败证据）

`TEST_FIXTURE_source_bootstrap_native_full_1hndsher` 的测试适配器只覆盖 original.main，但实际 shared 适配路径直接调用 execute_workflow，导致该隔离进程误走原 provider。180 秒原限额终止前，计量记录 6 个来源请求，其中 5 个完成；已观测 6 次 SDK、3 次 HTTP，另有 3 次 SDK 预留未能确认实际完成，最后 master_all 的真实调用成本 UNKNOWN。私有原件确认真实返回日历 833 行、交易所名单 1,700/2,906 行、退市接口空结果及 2026-07-27 行情 5,202 行；另一个 master_all 请求只有 claim、未完成。这些是计量记录的范围，不能外推未观测库内部调用。没有将事故结果用于研究结论或正式运行，也不将这些真实返回重新标成 TEST_FIXTURE。

V3 监督原报告为 FAILED、child exit -15，root_reaped 与 all_observed_descendants_gone 均 true，cleanup.remaining/errors 均空；临时 authority 保留 FAILED_OR_UNKNOWN。未写正式登记/业务库、未交易；已列端点没有收费服务调用证据。原件只留私有事故包，不发布、不重试原 attempt。修正后的新夹具在控制器/引导最早安装 Python 拒网钩子，私有默认 provider 硬拒，并覆盖 original/shared 实际 execute_workflow；实际计算 worker 及其后代另用单层 macOS `sandbox-exec deny network*`，libc 连接探针实得内核 EPERM。原监督器需 `/bin/ps` 采样，未置于 OS sandbox 内，不能宣称所有进程获得恶意代码隔离。新全链夹具 1 passed/87.51s、其余 17 passed/210.02s，exit 0；140 次明确 fixture executor 调用，无真实 provider 请求。日历/核心/展示 COMPLETE，新闻与题材 UNKNOWN/PARTIAL 保留。先前嵌套 sandbox 失败、采样失败、fixture 字段版本错误亦保留；新通过不抹除事故。

## 二、八项状态矩阵

“工程”表示新版本代码及相应隔离检查的状态；“反例”表示已验证的具体拒绝或恢复行为；“兼容”表示保留旧材料及版本边界；“真实”和“投资”必须独立验收。下列局部通过不能覆盖仍在变化的主集成源码。

| ID / 工作项 | 工程状态 | 反例状态 | 兼容状态 | 真实业务状态 | 投资状态 |
| --- | --- | --- | --- | --- | --- |
| P1-01 统一业务请求防重 | 共同 authority、effect、阶段能力已实现；启动前预留、同socket认证及跨后验修订家族累计约束已通过发布组合 | 换 key/engine/version、跨入口 provider 重复、旧失败继承已有局部测试；实际并发预 fork 反例已通过 | 不改旧登记；新主动树接入同一业务权威 | 未激活真实 authority / worker | 不授投资有效性 |
| P1-02 多代恢复与受限历史读取 | V2全祖先DAG、单活动epoch、双层环境与受限历史/主动树审计已通过发布组合；固定Authority载体灾备另受限制 | 祖先分支遗漏、登记迟变、三处 crash、旧失败换代、受限载体越界已有局部测试 | 旧字节/失败保留，新状态另建；历史读取不恢复 owner/PIT | 真实只读预检 BLOCKED：9 项核心计算/复用/归档操作待映射、根完备性/预算待核；未迁移 | 不恢复历史 PIT 或优势 |
| P1-03 真实交付闭环工程能力 | 单日/20日合同、源自举、逐日原生后验合成与受限审计已通过发布组合；五期限成熟另有11档原件认证夹具 | 全 UNKNOWN 格子误计成功已修；首观察日前驱、缺终态、缺清理、缺日均拒绝 | 不追认旧失败或迟交为当时盘前成功 | 连续 20 个真实交易日 NOT_RUN | 完成交付也不等于优势 |
| P1-04 真实执行证据与账户验证 | 有界证据接口与条件对账已实现，正式来源适配仍阻塞 | 部分/未成交、T+1、费用、重复成交、延迟退出、UNKNOWN 资本/状态等通过；跨日补买被拒绝 | 复用旧费用函数及安全断言，不改旧账户核心 | 缺真实原生执行来源、资本和容量证据；正式曲线 null | `BLOCKED_PROVIDER_AUTHORITY` |
| P2-05 标签隔离与研究试验账本 | V2 隔离与追加账本已实现，作者组合测试通过 | H10 跨窗、未知历史 span、同 plan 重复窥看、读取前 CLAIM、失败不可重试、恢复末验已覆盖 | 旧 V1 只保留原身份；V2 不重写旧结果 | 未开始新的真实独立验证 | 离线探索完整性 UNKNOWN，优势未建立 |
| P2-06 主动架构收敛 | 统一gateway/薄路由、显式身份和469→489新树构建及发布组合通过 | 原树不改、额外/遗漏依赖拒绝、策略/执行身份分离、跨树 fallback 拒绝已有局部测试 | 旧核心保留；只在新主动树生成有限 AST 接线 | 没有切换真实服务或默认登记 | 不改公式、门槛或策略结论 |
| P2-07 失败阶段持久计量 | stage journal 与失败尾已实现；control/publication 尾计量与 API job 日志已补齐可观测部分，最后不可观测尾明确 UNKNOWN | 采样失败、开着的 stage、缺终态、同字节换 inode、identity 不匹配等已有局部测试 | 新日志不替换原控制件；原终态继续核验 | 新真实端到端成本未测得 | 无收益或提速承诺 |
| P2-08 源码依赖到专项测试映射 | V2映射、120模块40/40/40分组及选测反例已通过本地发布组合；远端CI待本次提交核验 | 整模块选择、固定安全底线、未知依赖保守并集、scope 重算拒绝篡改已有局部测试 | V1 必跑底线保留，不删断言/扩 skip | CI 通过也不等于业务就绪 | 不构成投资验证 |

## 三、根因、实现和验收条件

### P1-01：业务身份不能等同于调用外壳

根因是 request-key、入口、engine、版本和物理登记是调用定位，不是同一业务或同一外部副作用的唯一身份。仅在各入口局部防重，会留下从其他入口重新创建旧失败的路径。

新实现见 [business_authority_v1.py](../scripts/business_authority_v1.py) 的 `canonical_business`、`Authority.claim/consume_once/charge`，以及 [active_business_gateway_v1.py](../scripts/active_business_gateway_v1.py) 的 `run_once/provider_scope/job_dispatch/bind_http`。authority 用稳定业务字段登记请求和终态，以独立阶段能力约束写库、网络、worker 与计量；provider effect 在跨 PREMARKET/CAPTURE 的共同范围防重，预算累计而非随换入口清零。HTTP host 另见 [active_service_host_v1.py](../scripts/active_service_host_v1.py)：固定角色身份不随 descriptor、名称、授权文本、epoch 或 inode 改变。

局部已验收：旧失败和未知不因换 key/engine/version 重开；无适当阶段能力的 worker、HTTP POST 和数据写入被拒；成功 host 重启须绑定上一成功 attempt/receipt SHA 与维护序号。**交叉审发现并修复了 fork 后才预留的时序缺口。现在先预留 supervisor、再在原 worker 启动前预留 WORKER_START，并以 live pipe、PID/出生身份及配置绑定交接。真实两个并发 controller、同 capability、不同 job 目录的反例只有一个 supervisor 和一个 worker 启动。** 不能用已有 authority 单测代替这条调用链的验收。

此外，失败的 `[A,B]` 证券批次改为 `[A]`、与其他证券重新拼批或扩日期窗，会由事务性的 `source_scopes` 重叠检查拒绝；证券/日期别名先规范化。不同题材 query、互不重叠证券/日期可以构成合法新任务。只有真实 provider 返回 `COMPLETE` 且分页完整，才结束同请求的传输预留；PARTIAL/异常保留未知。该结束证明传输返回，不授予市场来源或历史 PIT。

另一独立反例发现复制 authority SQLite 后，两份数据库都能声称 ACTIVE 并领取同一业务。修前脚本退出 0、两次 CLAIMED；修后持久 `original_carrier` 绑定 canonical path/device/inode，并在构造、每次连接和提交前校验，原脚本以 `AUTHORITY_ORIGINAL_CARRIER_MISMATCH` 退出 1，验收 harness 退出 0、原数据库新增请求为零。绑定用于封锁副本写权威，业务 ID 仍由逻辑业务字段生成，不能把物理 inode 当作业务身份。Authority + Recovery 定向 67 passed / 14.97s / exit 0；后续组合单独记录。

原聊天入口和旧薄封装的 POST 均先执行新服务的随机 nonce/HMAC 身份核验，并在同一持久连接上发送 POST；旧服务、伪签名或验证后断开的连接均收到零次 POST。新服务需支持持久 HTTP 连接。新树不允许普通旧 `register`/retry 或跨树 Python worker 回退。

最终验收：两次并发、跨入口/版本/engine/key 的同一业务最多产生一个副作用；已 fork 的未知子进程不得因新 capability 再启动；预算未知保留；终态和 cleanup 必须归属同一 business/attempt。新代码未启用前不声称旧实例已有该保护。跨日后验修订还使用同一 run 的公共原子预算约束；同材料换合成算法/key/登记不是新任务，新日原始事实才可能形成新修订。旧失败/未知/运行中家族不因加入另一input而绕过；累计消耗不得因新修订清零，当前单个immutable run为家族；新多run批量明确拒绝，历史批量可读且关联新动作须对账。`outcome_family_budget(run_id)`只读报告真实行消耗之和与家族余额；原`remaining_limits`明确是THIS_BUSINESS，不能当作实际总余额。原生新管道另将声明max_seconds纳入`outcome_wall_seconds`累计；旧HTTP全时间/SQL/内核IO仍未知，改变额度集合会被共同闸拒绝，不能借HTTP刷新native预算。最后不可自观测尾仍UNKNOWN。

当前公共业务合同映射原生 PREMARKET、同步 `active-outcome` 五期限 OUTCOME、`POST /api/strategy-experiments/run` 的 CORE_COMPUTE、`POST /api/daily-research/runs/<id>/outcomes` 的 OUTCOME，以及同业务取消。旧空采集、训练、finetune、通用配置写、任意 retry/register 等未映射写操作在主动树明确拒绝，不等于全部旧写功能已恢复。CAPTURE 的重叠保护属于底层权威能力，当前没有新的公共单阶段 CAPTURE 入口。

本轮写入口清点按实际责任层复核，不能只依赖新 CLI：

| 写入口 / 责任层 | 主动树中的约束与兼容状态 |
| --- | --- |
| `codex_daily.py`、`codex_daily_v2.py`、original/shared workflow | 原 `Runtime.run_once` 接共同请求；supervisor/worker 在启动前预留，同 attempt 的原始日期/配置/原生 PID 绑定 |
| `codex_chat.py`、`codex_research.py` 的 POST | 映射业务身份、原 capability、同一 socket 的 host 认证；GET 不授写权限 |
| HTTP CORE_COMPUTE、OUTCOME 与 `JobService.create/start/_run` | handler 和实际 worker 均核同业务；接受 HTTP 不先记业务成功；其他未映射 POST 拒绝 |
| 独立旧 workflow/compact/decoded/factor 等兼容脚本 | 新树内统一 audit/已声明子进程规则；无同一有效 capability 的旧独立写入口拒绝，不能借换脚本重开任务 |
| `CaptureCache`、provider parent/child、RequestQueue | capture 前日历阶段门、provider/SDK/HTTP 在副作用前事务预留；旧 PARTIAL/retry 拒绝，完整返回才收束传输状态 |
| `SQLiteStore` 与直接 SQLite / 文件 / subprocess 责任层 | 实际写与子进程需当前阶段能力；只读 URI 独立验证；窄内存 schema oracle 不允许 ATTACH/扩展等逃逸 |
| 旧 register / 自动恢复重跑 / 通用配置和训练写 | 主动树明确拒绝；未通过新薄入口替旧入口掩盖可写绕过 |
| `active_outcome_pipeline_v1` 原生五期限后验 | 两个原始档案、各自 PREMARKET 权威和共同 OUTCOME 请求；离线原 evaluator、阶段计量和唯一原件发布，换 key/目录不能重做失败 |
| authority 初始化、V2 recovery apply、host bootstrap | 显式控制面；固定载体、完整历史和单活动 epoch；不会自动切服务或把旧失败再运行 |

这些约束针对校验后的受支持程序和受控目录；具有任意源码/数据库修改权限的操作者仍属于信任边界，不能据此声称操作系统级恶意代码隔离。

### P1-02：恢复必须继承完整历史，读取不能冒充重新授权

根因是只复制当前登记或少数成功回执，可能遗漏祖先失败、其他分支、运行中请求及累计预算；文件字节恢复也不等于原 inode、owner、来源首见时间或历史 PIT 恢复。

实现见 [codex_runtime_recovery_v2.py](../scripts/codex_runtime_recovery_v2.py) 的 `inventory/plan_recovery/_check/apply_recovery/load_runtime`。计划固定全部登记根、祖先分支、环境回执和主动树 manifest；切换前重扫，使用多代 DAG 和单活动 epoch 事务。受限入口 [restricted_history_v1.py](../scripts/restricted_history_v1.py) 只读指定 SHA 的有界载体，不初始化业务 DB，不发行来源能力。

局部反例覆盖连续两次迁移、全部祖先/分支、三处中断、迟增兄弟登记、旧失败导入、映射重绑定、计划漏根、未知预算、历史载体越界/压缩膨胀等。最终验收须在独立 TEST_FIXTURE 验证原件不变、失败全过程保留、任意中断不形成两个活动 epoch；真实迁移另核完整私有 inventory。**失败 host bootstrap 当前没有自助恢复 factory**，必须人工对账；换名称或授权字符串不是恢复方式。

环境采用两层验收：`active_environment_acceptance_v1.py` 在操作系统限制下执行精确已发布 preimage 的原七步校验，另核新主动树完整 manifest 与 Authority 原载体。真实 doctor/smoke 不在未领取业务能力的 active 树冒充执行成功；回执明确 `active_overlay_smoke_executed=false`、服务/来源权限均 false。独立真实生成树的 V2 plan/apply/load 和旧 app 在配置导入前拒绝另有测试。只支持已有 Darwin/x86_64/3.12 锁，其他平台明确拒绝。`active_read_control_v1.py` 在匹配的主动树执行审计，Authority 仅 mode=ro/query_only/SQL authorizer；调用过程没有广泛控制豁免、业务 claim、网络或写数据库能力。

### P1-03：核心成功不是完整交付，更不是 20 日验收

实现见 [daily_delivery_contract_v1.py](../scripts/daily_delivery_contract_v1.py) 的 `bind_terminal_evidence/audit_delivery/audit_sequence`、[active_delivery_bridge_v1.py](../scripts/active_delivery_bridge_v1.py) 和 [codex_active_v1.py](../scripts/codex_active_v1.py) 的 `delivery_main`。同一业务终态需绑定原 job、canonical run、writer receipt、durable journal 和独立原生 supervisor cleanup。

独立最小反例曾构造“每日完整五期限格子，但所有标签 UNKNOWN”：旧接受谓词计入 20/20。修后运行完成率可单列 20/20，**总验收必须 0/20，成熟标签 0/20**。另一个隔离反例发现仅内容一致的 MATURE 可计入 accepted；现必须绑定原生 OUTCOME 回执、信号/后验档案各自 PREMARKET 登记、精确原件、原生 journal 与原 evaluator 全部五期限重算。缺权威时成熟计数仅供描述，不取得验收资格。两个反例均隔离接受谓词，没有伪造完整原生来源认证。首观察日还必须是原生日历中首目标日的直接前驱。

V1 保留原 `PROSPECTIVE_CAPTURED` 严格条件。新 V2 必须事前显式冻结 `PROSPECTIVE_OPERATIONAL_DELIVERY`，只验原定日期原生执行、及时封存、本地来源链与成熟原生后验；原 `RECONSTRUCTED_HISTORICAL` 不改写，历史特征 PIT=false。显式 `STRICT_PROSPECTIVE_PIT` 仍受原历史覆盖约束。省略 scope 保留 V1，不能隐式升级旧协议。运行取得时间需位于观察日来源释放时间18:00至约定交付截止之间；协议冻结须在首观察日15:00前。

最终验收要求真实自然经过的连续 **20 个交易会话**，不是 20 个自然日、20 次重放或测试夹具日期。须在首观察日收盘前冻结协议；每个原定日报告 NOT_RUN/FAILED/UNKNOWN/COMPLETE/COMPLETED_WITH_UNKNOWNS、原生运行终态、声明预算和干预信息。用户收讫始终为 UNKNOWN_NOT_OBSERVED；干预次数为显式自报或 UNKNOWN。准时性相对于事前冻结的调用方截止时间（默认09:00），不自动证明交易所盘前 SLO。当前 REAL_DAILY_ACCEPTANCE_ESTABLISHED 仅指该运行/标签合同，不证明用户已收到或全部成本已观测。全池身份分区与 security × H1/H2/H3/H5/H10 必须完整计数。PENDING、UNKNOWN、NOT_PROVIDED 均不能进入成熟验收；末日 H10 还需额外真实交易日。

双日期的实际原生验证进一步暴露了缺口：晚日原 normalizer 会将历史日证券板块资格置 UNKNOWN；10月15日信号与10月29日末档案即使两端均认证，也无法证明中间9日身份，实际10格全部UNKNOWN。相邻15→16的原生链则得到1格H1成熟、另一证券缺行及其余期限UNKNOWN（1 MATURE + 9 UNKNOWN）；这是TEST_FIXTURE参考价格结果，缺失值未填0。新增逐日输入组合器逐份重验原生档案，只取各自观察日的原始证券状态和价格，保留原时钟/receipt，缺日不补；共同OUTCOME绑定全部实际daily input IDs，原评估器不改。组合产物具有新的派生输入身份，不能作为历史策略PIT来源。原两个端点的失败期待与实际UNKNOWN产物完整保留。

冻结的原生日历窗口必须预先覆盖观察、末日 H10 成熟及最终复核日期。默认 32 个自然日前瞻可能不够；不能最终扩窗换 hash 冒充原事前协议。当前没有 2027 已登记权威日历时不得外推，须取得权威资料并按新版本接入。没有足够原生日历仍可保存计划，正式验收保持阻塞。

### P1-04：明确条件算术与真实执行权威的距离

实现见 [execution_evidence_v1.py](../scripts/execution_evidence_v1.py) 的 `reconcile/audit_dossier` 及两个执行证据模板。精确载体分别提供取得时间、日历、身份、规则、费用、公司行动覆盖、订单、实际成交、终态与估值。禁止用 JSON 自声明 `verified=true` 授予来源权限。

独立算术例保留 300 股分两笔买入、分两日退出，按订单/会话累计最低佣金，末笔延迟一交易日；100,000 情景资本条件终值为 100,133.3635。该数值仅是 **TEST_FIXTURE 算术 oracle**，不是投资收益。未知订单状态使条件现金与 NAV 保持未知；跨目标日补买明确拒绝。未成交、撤单、剩余持仓、费用不符、T+1、限价、停牌/规则缺口和公司行动覆盖分别核验。

真实验收还缺有权消费的原生执行来源、实际资金/仓位依据、费用计收与取整规则、公司行动、流动性/滑点/容量与压力证据。**来源、费用或许可范围不明时，不生成正式账户曲线。** 目前即使条件对账完成，`account_curve` 和 `strategy_account_return` 仍为 null，`capital_evidence=UNVERIFIED_CALLER_INITIAL_CASH_SCENARIO`。不能把条件零成交收益或参考收盘变化当成真实可交易回报。

### P2-05：信号窗不重叠仍可能共享后验信息

原反例：validation 为 11 月 9—20 日、holdout 为 11 月 23 日—12 月 4 日，H10 信号窗虽然不重叠，11 月 20 日标签结束在 12 月 4 日。V1 接受了该声明，但原投资有效性仍为 false；这证明缺少隔离检查，不证明已有收益被伪造。

实现见 [codex_research_protocol_v2.py](../scripts/codex_research_protocol_v2.py) 的 `label_support/freeze_plan/evaluate_study` 和 [research_trial_ledger_v1.py](../scripts/research_trial_ledger_v1.py) 的 `claim_study/finish_study`。新协议保留全部原信号，标记实际 label_start/end、purge、embargo；未知日历/历史已消费标签跨度不能取得隔离资格。旧 H10 探索不能被新 H1 计划缩短。

每个新 plan 需完整历史及新的设计绑定 attempt；读取后验前追加唯一 plan/phase/input CLAIM。同一 phase 换 pairs、换输出路径或运行失败不能重窥看；结果已提交但输出失败时，只读 `recover-study` 恢复原结果。额外阶段窥看和 UNKNOWN 计入本地试验族。全局线下/其他 scope 仍 UNKNOWN，`discovery_labels` 仍是明确本地声明，不能因此宣称独立样本或全局多重检验完备。

验收保留旧 H10 反例、新日历节假日边界、未知跨度、CLAIM 前后故障、并发追加和恢复末验。原 V1 估计器/费用/策略实现保持，V2 条件统计不是已验证市场优势。

### P2-06：以新主动树收敛责任，不重写旧认证版本

[active_runtime_v1.py](../scripts/active_runtime_v1.py) 对精确冻结的 469 个 preimage 文件生成新的主动树，仅在登记的职责层应用 AST 接线；额外文件、缺文件、未知 hook 或 hash 不符拒绝。旧树不覆盖。运行入口复用原 `codex_daily_v2`，生成主动树中受支持的业务执行、存储、网络和 HTTP 汇入共同 gateway，而不是再建平行名单或执行算法。

[active_identity_v2.py](../scripts/active_identity_v2.py) 显式分类策略语义、来源、展示与完整执行文件。实际新 R1/R2/实验 identity 返回同时携带策略语义身份和全执行身份；未知新增依赖拒绝，不能靠 import 可达性把所有运维变动算作策略改动，或把来源语义变动排除在策略证据外。仅作用于新主动运行，不重写旧 run/hash。

[codex_active_v1.py](../scripts/codex_active_v1.py) 要求显式 source-root、原 V2 runtime 和 registry-root；用登记 Python `-I -B` 启动原日常入口一次，默认 original，失败不换树/key/engine。生成主动树内只能指向自身，并复用 gateway 的窄范围绑定读取；这不扩大 worker 权限。离线研究、构建和恢复控制工具仍从发布控制平面调用。兼容验收包括实际生成树、真实隔离 authority SQLite 和 gateway 边界；登记就绪对象/child 执行使用替身的测试必须如实标注，不能称真实服务已验收。

运行边界针对经过校验的受支持程序，并非对恶意 Python 的操作系统沙箱。Python 官方明确说明 audit hook 不是安全沙箱；本方案仍依赖源码/登记目录所有权与受控进程环境，不能把 HMAC/manifest 或审计 hook 称为多租户隔离。核验日期 2026-11-05：[Python 3.12 sys.addaudithook](https://docs.python.org/3.12/library/sys.html#sys.addaudithook)。SQLite 只读连接的 URI 语义须由真实 `uri=True` 调用建立，不能由文件名含 `mode=ro` 代替；已保留该反例：[SQLite URI](https://www.sqlite.org/uri.html)。

### P2-07：失败阶段也必须留下可核对的成本和身份

[durable_stage_metrics_v1.py](../scripts/durable_stage_metrics_v1.py) 的 StageJournal/audit_journal 记录 business/attempt、原生进程身份、UTC/单调钟、可观察的进程 CPU/RSS、原 Budget 提供时的累计返回读取及阶段开始/结束。未接入读取计数的部分保持未知；budget_known 只表示声明预算累计计数状态，不等于全部 I/O 或成本已知。新文件独占发布、fsync、hash 链和终态检查，使采样失败、半写尾、缺 terminal、未闭合 stage 保持 UNKNOWN/FAILED。

进程峰值 RSS 不是整棵树瞬时 RSS；返回读取字节不是全部内核 I/O。日志本身不能证明 worker 已回收。盘前子进程的 writer receipt 和独立原生 cleanup 必须同时绑定；HTTP 作业线程属于仍存活的共享服务进程，不声明独立进程退出或整树回收。SQLite、queue 和 provider 的副作用守卫不等于每类维护写入都独立记录完整 StageJournal。控制读取、最终 publication 与 journal 收尾本身也要计量，不能藏在“计量结束”之后。这些尾部已补入独立回执并在后续读取时重新验证。无法自证的最后发布耗时明确为 UNKNOWN/lower bound；API 线程 CPU/RSS 标明共享进程范围，SQL 返回读取及内核 I/O 保持 null，不能写成零。

验收不要求失败变成成功，而要求故障注入后仍能定位失败阶段、累计已知成本、未知范围及能被独立证据确认的进程终态；硬终止后无法确认的部分继续 UNKNOWN。本轮没有新的真实全市场速度或内存改善测量。

主负责人在 `active7` 的**早期同夹具** core/theme/display 比较记录如下。样本仅两证券、67 会话，且不是完整控制平面；后续 bridge 增强后需要另测，因此这不是最终性能验收：

| 早期夹具字段 | 原路径 | active 路径 |
| --- | ---: | ---: |
| wall 秒 | 12.476 | 15.352 |
| CPU 秒 | 11.857 | 14.876 |
| capture 返回读取字节 | 71 | 71 |
| core 返回读取字节 | 15,465,583 | 46,065,215 |
| theme 返回读取字节 | 60,041,617 | 143,681,187 |
| job 目录记录字节数 | 1,849,418 | 1,932,736 |
| 原记录 RSS 字节字段 | 61,145,088 | 62,947,328 |

该记录显示身份核验和计量引入了成本，没有证明提速。列示读取不是全部内核 I/O；未覆盖真实全市场数据、全部控制面成本、长任务恢复代价或容量。最终测量须标明 RSS 的具体采样作用域、完整阶段和最终源码身份，不能将这组小样数字直接外推。此前实际原生桥接夹具快照另有记录：原 in-process wall 19.771s、CPU 16.957s；active 完整 native 链 wall 37.929s、controller CPU 3.625s、已结束子进程 CPU 43.329s。两臂范围不对称且同时存在其他隔离测试争用，不能据此计算提速或把 controller RSS 解释为整树内存下降。完整请求准入和最终 authority 审核仍不在该对比计时内；全市场性能未验证。

### P2-08：测试选择必须随依赖变化，不能只看新增测试文件

[engineering_ci_scope_v2.py](../scripts/engineering_ci_scope_v2.py) 保留 V1 强制安全底线，按源码职责选择整份专项模块；入口、来源、后验、恢复、策略、执行和 runtime 依赖分别映射。未分类的实现改动选保守专项并集，不能无声跳过；执行 scope 前重新计算并核对映射。根 [.github/workflows/engineering-gates.yml](../../.github/workflows/engineering-gates.yml) 已在源码中引用 V2。

验收应覆盖“只改源码、未新增测试”的选择反例，mandatory 不丢失，scope 篡改拒绝，独立 oracle/账户/HTTP 安全原断言保留；测试时限与资源不足不得通过删样本或扩大 skip 掩盖。**本地最终15模块已通过；实际提交的Git diff选测及远端各作业仍须逐一核验。** 本地映射函数通过不等于 GitHub job 已运行。

此次最终候选选择120个完整测试模块。CI 按完整测试模块确定性分为 3 个 daily 作业（40/40/40），联合集合保留全部固定门禁，每模块恰执行一次，`partition.json` 记录完整/实际列表和提交；源 diff 选择结果在每组执行前重算。实测旧 81 模块约24分钟、新增15模块约6分钟、原 archive bridge约7分钟，尚未加入新增整链，不能继续假定单45分钟充分。每组工程装载上限45分钟、总分配上限135分钟；这不改变任何业务请求预算。没有跳过或选单个方便断言。

## 四、已执行验证与尚待填入的证据

本轮本地测试解释器为项目 `daily-review` 环境：Darwin / x86_64 / CPython 3.12.14；只读发布输入检查为 58 artifacts、`HASHED_INPUTS_VERIFIED`，exit 0，未安装或改变环境。该规则/工程测试环境没有 pyqlib distribution；直接版本查询退出 1、独立环境清单查询退出 0。未以此宣称 Qlib 模型或 pyqlib 0.9.7 运行验收，未改 editable install 或模型权重。

以下包均为独立 DB/state/artifact 的显式 TEST_FIXTURE；私有原日志、路径、环境和完整审阅包不随源码发布。下表的证据 ID 是本机保存包标识，最终发布负责人应维护私有定位表。

| 证据 ID | 实际范围与结果 | 可支持的结论 / 限制 |
| --- | --- | --- |
| `TEST_FIXTURE_quant_complete_fxupz6_e` | 7 文件组合，**185 passed，69.52 s，退出 0，零失败/零 skip**；外层 71.540 s；12 个交付文件 SHA 在运行期间未变；8 AST、3 JSON、9 CLI help 通过 | 新协议、账本、执行证据、薄路由与原 V1/账户/后验回归；不含后续所有主桥接修改 |
| `TEST_FIXTURE_quant_independent_final_bti49m3z` | 独立 4 passed，3.99 s，退出 0 | 同 plan 重窥看拒绝、读取前 CLAIM、历史长 span、UNKNOWN 现金；不等于全量独立回归 |
| `TEST_FIXTURE_quant_crossreview_rcpusa4y` | 原跨日 BUY 最小反例条件对账成功；修后同反例明确拒绝 `EXECUTION_BUY_FILL_OUTSIDE_FROZEN_TARGET_SESSION` | 保留修前/修后证据；正式曲线始终 null |
| `TEST_FIXTURE_delivery_unknown_review_shesm3dn` / `TEST_FIXTURE_delivery_unknown_recheck_ozpmigc6` | 修前/修后谓词探针均退出 0；accepted_sessions 从 20 改为 0，operational 仍单列 20/20 | 原生 authority/calendar 被刻意排除以隔离接受谓词；不能说伪造过完整真实验收 |
| `TEST_FIXTURE_architecture_crossreview_fix_krpy7ypu` | 先前 5 文件 83 passed，28.85 s，退出 0 | durable/合同的局部稳定快照；后续尾计量修改需新 manifest |
| `TEST_FIXTURE_active_identity_v2_fix2_m7t0xbrl` | 10 passed，23.98 s，退出 0 | 真实生成 identity 函数；主集成另验 |
| `TEST_FIXTURE_active_delivery_bridge_qymzr2z2` | 10 passed，5.38 s，退出 0 | bridge 独立协议；不替代 root 的实际全链接线 |
| `TEST_FIXTURE_engineering_final_junit_20261105.xml` | 先前 5 文件 61 passed，139.03 s，退出 0 | authority、恢复、受限读取、安全 gateway、host 的局部快照；属于早期快照，后续修复由后面的最终发布组合覆盖，不合并计数 |
| root `active7` / `active8` 集成记录 | active7：20 passed，77.91 s；active8：19 passed、1 failed，原失败保留 | shared 路径的新 bridge 丢失隔离 `-X pycache_prefix`，已修复并经后续真实桥接复验；没有放宽原断言。original 原工作流的真实代码失败路径及完整回收已在隔离夹具通过，不是一次真实市场盘前成功 |
| `real-recovery-readonly-precheck.json` / `real-recovery-mapping-gaps.json` | 真实只读扫描 182 控制 JSON 前后 SHA 不变；4 个可识别旧失败、9 个复合身份映射缺口，BLOCKED | 证明当前启用前置尚未满足；未创建真实 authority、未激活、未重认来源；预检 JSON SHA256：`caf52318c02431ef1b15efbb0570de890ee71ff19fdf23bb20e13d7c6051feb7` |
| 来源自举实际整链及余项 | 1 passed/87.51s 与 17 passed/210.02s，均 exit 0 | 全新严格隔离；原事故/失败保留，真实采集不获追认 |
| 共同权威/恢复/host 冻结组合 | 103 passed/248.52s/exit 0 | 后增环境层另验；多代与原件遗漏的修前反例保留 |
| 两层环境与实际旧入口 | 10 passed/98.04s/exit 0 | 原七步全部0，生成树实际V2加载/旧app拒绝；上一轮112 passed/1 failed的builder漏选原失败保留；随后发布私有目录沙箱失败另记录 |
| 第二来源及成熟权威 | 23 passed/37.52s + actual native 1 passed/87.46s，exit 0 | 此时原生正例为同档案5/5 UNKNOWN；不能计独立标签或真实成熟验收 |
| 原新增依赖专项与档案桥 | 15模块275 passed/34 subtests/349.72s；原bridge253 passed/396.29s；exit 0 | 保留原实现和全部原断言，不与其他重叠组合累加 |
| 最后量化边界交叉审 `TEST_FIXTURE_final_quant_boundaries_rpwvu_7p` | 独立 probe exit 0；H10 跨窗剔除、节假日 H1、跨年日历不足 UNKNOWN、未终态 trial 不可重试、延迟退出及自报来源字段拒绝 | 不把终态 BLOCKED_DATA 后允许的一次 holdout 诊断称为独立样本资格；正式曲线/收益均 null |
| 发布候选第一次组合 | 19 文件、282 passed、699.96s、exit 0；外层 702.529s | 精确 51 文件快照；不覆盖随后 carrier、host、恢复映射、自举修改 |
| 发布21模块首次整合 | 307 passed、3 failed、12 setup errors，1156.16s，exit 1 | 3失败分别为home沙箱、测试字段名、BLOCKED兼容；12setup含9个同沙箱连带错误和3个测试保护误拒本地HTTP名称解析；原记录保留，按根因修复，不作为通过 |
| home目录完整环境重验 | 11 passed/139.09s/exit 0 | 精确owned读例外；邻私人目录、别名、环境文件、外部DB/写/网络仍拒绝 |
| BLOCKED兼容原反例 | 修前1 failed/18.92s/exit 1；原断言修后1 passed/15.22s/exit 0 | 改source注解责任层，不覆盖旧权威BLOCKED，拒绝逻辑不放宽 |
| 逐日合成原件链 | 11档、10项测试，107.82s、exit 0 | 完整10 MATURE；缺第5日4 UNKNOWN；末日单档10 UNKNOWN；新session重验manifest不变 |
| 多日后验合同 | 52 passed/77.17s/exit 0 | 所有daily权威、原cap、manifest与原eval重算及末验；假来源/多加signal ID拒绝 |
| 实际双日合成与旧入口复用 | 1 passed/220.25s/exit 0 | 原V3双档案、1MATURE+9UNKNOWN、同B只1次OUTCOME worker；后增wall计量由最终发布组合再验 |
| OUTCOME家族原子额度 | 最后Authority+Recovery93 passed/27.66s；前版含Host102 passed/112.47s；exit 0 | 新revision、worker、job-create、显式resume耗尽前置拒绝；11条修前失败保留，未改旧schema |
| 发布最终本地组合 | 15模块266 passed，1430.78s，exit 0，0 skip | 精确发布源码快照；受影响主链与原断言通过，不等于全仓/市场验证 |

量化最终包可复核摘要：`delivery-manifest.json` SHA256 为 `0a26a1dfa08a07750b218fd1d321626ee216cba5a620f767952dcd83021ef020`，`command.json` 为 `fcc2cecfa9cfdb05e69f417b5d0d358d578e2738aed13c56d64db2c5d7d001a7`，`junit.xml` 为 `be3bf683607914c2b43323a2a02b273404f0b00d3190e289358acfadef2f2bac`。这些摘要只绑定该次私有工程记录，不是市场来源签名。

该 185 项实际命令使用项目 Python `-I -B -X pycache_prefix=<该包新目录>`，调用 `stock-fish/scripts/run_isolated_tests.py -q`，完整文件列表为：

```text
tests/unit/test_research_trial_ledger_v1.py
tests/unit/test_codex_research_protocol_v2.py
tests/unit/test_execution_evidence_v1.py
tests/unit/test_codex_active_v1.py
tests/unit/test_codex_research_protocol_v1.py
tests/unit/test_codex_r2_execution_v1.py
tests/yaogu/test_short_horizon_outcomes_v1.py
```

同时传入该包全新 `--basetemp`、`--junitxml` 和 `--tb=short`。准确 argv、原解释器和临时路径见上述 `command.json`，不在可发布文档硬编码个人目录。失败及中间检查不删除、不与最后通过项数重复累加。

本轮扩大映射后的第一次完整旧专项组合为 **81 文件、1,592 passed、3 failed、36 subtests passed，退出 1，1,447.84s**；原 JUnit/输出完整保留。三个失败均独立核对，不能删除后宣称首次全绿：

- D/E 两个真实 JobService 夹具在测试轮询 20s 时被 finally 取消；未改发布源码的独立有界诊断等待真实终态后，原 UNKNOWN、非 REJECTED、READY 和 typed reason 断言全部通过。仅这两条测试显式等待 180s，其他仍 20s；业务预算与原业务断言不变。定向 2 passed/117.07s/exit 0；未宣称达到 20s 性能目标。
- shared late-admission 的合成档案使用固定十月业务时间，却在十一月造 fixture 时使用当前封存钟，正确触发 `ARCHIVE_SEALED_AFTER_CUTOFF`，未走到待测准入分支。只在创建该明确 TEST_FIXTURE 之前冻结其合成钟，没有封签后改时间、没有 mock 认证器或放宽原错误码断言。整模块 21 passed/195.04s/exit 0。
- 新完整 native differential 曾被严格预 fork 门禁拒绝；测试改为真实 supervisor/live pipe 后，又发现桥错误拒绝合法带 UNKNOWN 终态。桥修复后原所有数值/UNKNOWN 断言与终态绑定均通过，1 passed/107.62s/exit 0。

## 五、经 CLI 源码核实的操作接口

**以下是接口说明，不是本轮已经执行的真实操作。** 真实生产登记、epoch 和服务没有执行切换；构建、初始化、恢复和运行的工程能力在全新 TEST_FIXTURE 中实际执行。发生的隔离采集事故已在前文单列，不能据本段否认。所有尖括号是必须由已核验私有登记提供的值；`python` 表示已验收项目解释器。控制命令从发布源码所在业务目录调用。

### 1. 初始化与构建新主动树

初始化只在已有明确授权、完整 onboarding 已审核时执行；不是普通查询的前置自动动作：

```text
python -I -B scripts/business_authority_v1.py initialize \
  --database <新authority数据库> --workspace-id <固定工作区ID> \
  --onboarding <完整onboarding.json> --onboarding-sha256 <SHA> \
  --authorize-control-initialization

python -I -B scripts/codex_active_v1.py build \
  --base-root <精确冻结base根> --destination <全新主动树目录> \
  --authority-database <同一authority数据库> --workspace-id <同一工作区ID>

python -I -B scripts/codex_active_v1.py verify <同一主动树目录>
```

`build` 依赖已存在 authority，仅构建新树，不激活 epoch、不覆写 base。manifest 与全部声明文件一致才可进入下一步。冻结目录原件来自指定已发布基线，额外未声明本地实现拒绝；6 个原 requirements/lock 文件及原 app.py 也保留原 hash，不能从开发目录私自补版本。

环境验收先于恢复，且不会发放业务权限：

```text
python -I -B scripts/active_environment_acceptance_v1.py accept \
  --python <当前同一隔离解释器> --active-root <新主动树> --base-root <精确冻结原件树> \
  --destination <不存在的验收目录> --authority-database <原authority载体> --workspace-id <固定工作区ID>
```

### 2. 完整恢复计划与显式激活

```text
python -I -B scripts/codex_active_v1.py recovery plan \
  --authority-database <固定authority数据库> --workspace-id <固定工作区ID> \
  --registry-root <全部旧登记根之一> --registry-root <其他相关登记根> \
  --profile <新profile.json> --environment-receipt <匹配新树的环境回执> \
  --parent-epoch <已核祖先epoch> --purpose <明确工程用途> --output <新恢复计划.json>

python -I -B scripts/codex_active_v1.py recovery apply \
  --authority-database <同一authority数据库> --workspace-id <同一工作区ID> \
  --plan <原恢复计划.json> --plan-sha256 <原SHA> \
  --authorize-activation --authorization-id <本次明确授权标识>

python -I -B scripts/codex_active_v1.py recovery read \
  --authority-database <同一authority数据库> --workspace-id <同一工作区ID> \
  --business-id <待核原business_id>
```

`--registry-root`、`--parent-epoch` 支持重复，必须完整覆盖实际范围；可选 `--mappings`、`--budget-evidence` 只能提供待核事实，不能凭声明抹除未知。省略 `--business-id` 的 read 读取当前 epoch。**本轮未执行真实 apply。** 源码发布不自动成为环境激活授权。

### 3. 完整只读预检与实际运行

```text
python -I -B scripts/codex_active_v1.py premarket-plan \
  --source-root <已构建且与登记一致的主动树> \
  --runtime <原V2登记名> --registry-root <原固定登记根>

python -I -B scripts/codex_active_v1.py premarket \
  --source-root <同一主动树> --runtime <同一登记名> --registry-root <同一登记根> \
  --observation-date <原观察日> --target-session <原目标交易日> \
  --request-key <原固定请求标识> --engine original
```

先 plan、后在真实授权和原预算内运行；失败/UNKNOWN 不自动继续。不得以改变 engine、版本、key、目录或登记来尝试旧失败。预检索引成本不等于来源已认证，core 成功不等于最终交付。

五期限原生后验调用，所有档案必须已存在且经过原始认证，不采集新数据：

```text
python -I -B scripts/codex_active_v1.py outcomes \
  --source-root <同一主动树> --runtime <同一V2登记名> --registry-root <原固定登记根> \
  --parent-business-id <信号PREMARKET业务ID> \
  --signal-job <信号job> --signal-final-sha256 <信号SHA> \
  --outcome-job <后验来源job> --outcome-final-sha256 <后验来源SHA> \
  --outcome-business-id <后验来源PREMARKET业务ID> \
  --destination <新独占输出目录> --request-key <固定请求标识>
```

完整多期限合成时另加 `--daily-archives <原逐日描述.json> --daily-archives-sha256 <原SHA>`。描述为 `{ "archives": [{ "business_id": "...", "job": "...", "final_sha256": "..." }] }`，1–32份；每份必须晚于信号，主outcome档案须是唯一末日，缺日不补。单日合成和旧单档案同输入绑定同请求，不能借算法/描述模式重试。不同后验档案必须有自己的 PREMARKET 成功业务；保存结果复用会明确 `source_reauthenticated_now=false`，不冒充本次来源重认。

### 4. 持久计量、单日与连续日审计

```text
python -I -B scripts/codex_active_v1.py metrics <原journal目录>

python -I -B scripts/codex_active_v1.py delivery-audit \
  --source-root <登记主动树> --runtime <原V2登记名> --registry-root <原固定登记根> \
  --authority-db <原authority数据库> --workspace-id <固定工作区ID> \
  --business-id <原business_id> --job <原job目录> --journal <原journal目录> \
  --run <原run载体> --run-sha256 <SHA> --outcomes <原后验载体> --outcomes-sha256 <SHA> \
  --outcome-business-id <原OUTCOME业务ID> --reauthenticate-signal

python -I -B scripts/codex_active_v1.py delivery-protocol plan \
  --first-target-session <首目标交易日> --acceptance-scope PROSPECTIVE_OPERATIONAL_DELIVERY \
  --output <新协议.json>

python -I -B scripts/codex_active_v1.py delivery-protocol inspect-sequence \
  --source-root <登记主动树> --runtime <原V2登记名> --registry-root <原固定登记根> \
  --protocol <原协议.json> --protocol-sha256 <SHA> \
  --requests <原生请求描述.json> --requests-sha256 <SHA> \
  --authority-database <原authority数据库> --workspace-id <固定工作区ID> \
  --protocol-business-id <原事前冻结业务ID> \
  --calendar-state-root <原state根> --calendar-record <原日历记录> --calendar-record-sha256 <SHA> \
  --output <新审计.json>
```

请求描述为 `{ "requests": [...] }`，逐日提供原 `audit_delivery` 定位；缺日保留 NOT_RUN。`plan` 可同时提供完整三项原生日历定位。真正事前冻结是单独的 `delivery-protocol freeze`，还需 `--protocol/--protocol-sha256`、`--authority-database/--workspace-id`、`--epoch-id`、`--authorize-freeze`、`--authorization-id`、`--output`，不会被 plan 或 inspect 隐式调用。

### 5. 恢复验证与回退边界

独立字节/指定文档恢复演练接口已存在：

```text
python -I -B scripts/recovery_drill_v1.py \
  --backup <已验证备份目录> --destination <全新隔离恢复目录> \
  --probe <RECOVERY_PROBE_V1描述.json> --max-bytes <已批准逻辑字节上限>

python -I -B scripts/codex_active_v1.py restricted-history \
  --path <原历史载体> --root <获准历史根> --sha256 <原SHA> \
  --max-carrier-bytes <载体上限> --max-decoded-bytes <解码上限>
```

恢复到新目录不恢复原物理身份、owner、历史 PIT 或来源能力。新 authority 本身的数据库载体不能通过复制、改名或同路径替换迁移；没有自动 rebinding 入口。当前多代恢复适用于围绕固定 authority 的源码、解释器和业务登记迁移；原 authority 丢失/移动后的写恢复还需要能封锁原主的独立协议，当前应停写并使用受限历史读取，不能宣称整机灾难恢复已完成。probe 只验声明文档，不等于全部业务恢复。不能执行 Git reset、覆盖旧 DB、删除失败登记来“回退”。

**没有可直接调用的“一键 rollback/host resume”子命令，不应编造命令。** 若确需退回兼容执行版本，操作接口仍是：检查原 authority → 为兼容版本构建/核验新的主动树 → 对完整祖先范围执行上述 `recovery plan`（新 profile、真实环境回执、明确回退用途）→ 独立复核 → 显式授权 `recovery apply` 形成新一代 epoch。不能重用已退役登记或省略失败历史。真实服务停止/切换和失败 host 的人工对账是另行授权操作；本轮没有执行。

HTTP host 当前 CLI 仅有 `active_service_host_v1.py plan --execution-name ... --registry-root ... --api-profile ... --authorization-id ... --output ...` 与 `read --descriptor ...`；它们不启动 listener。显式 WSGI factory `load_wsgi_app(..., authorization_id=..., authorize_bootstrap=True)` 也不等于默认服务已切换。失败 bootstrap 保持 FAILED_OR_UNKNOWN，当前没有通过重命名重新启动的合法捷径。

### 6. 最终 CI scope 的复核接口

在仓库根执行；实际选测记录须对应发布提交：

```text
python -I -B stock-fish/scripts/engineering_ci_scope_v2.py \
  --base <明确base提交SHA> --head <明确head提交SHA> --output <新scope.json>

python -I -B stock-fish/scripts/engineering_ci_scope_v2.py \
  --run-scope <原scope.json> --basetemp <全新TEST_FIXTURE目录> --junitxml <新JUnit路径>
```

实际 scope 必须来自最终 diff。各原入口退出码原样保留；0 只表示该命令对应工程条件通过，不能自动解释为业务就绪或投资有效。阻塞结果、失败和未知不能从统计中删除。

## 六、真实运行的剩余放行条件

1. 本轮pre-fork、journal/publication、HTTP作业日志和终态桥接已由最终本地组合核验。真实来源、连续运行和投资缺口不能据此关闭。
2. 本轮已保留全部失败及退出码，主动树与依赖身份通过独立重算，原受保护文件未变；远端最终选测与结果据实际提交单独记录。
3. 仅62个授权文件进入既有发布检出；指定main须核实远端提交与CI。源码发布不代表服务激活。
4. 后续真实运行须先解决实际预检的 9 项复合身份映射、全部登记根/祖先完备性及旧预算证据缺口，并核新环境和完整恢复计划，再单独判断是否具备显式启用条件；旧失败/UNKNOWN 与原预算继续继承。连续日验收等待自然交易日及 H10 成熟，不能用 TEST_FIXTURE、历史重放或旧核心成功代替。
5. 来源、费用、资本、公司行动、成交约束和数据许可未明确之前，正式账户曲线保持阻塞；所有策略初始参数仍尚未验证，`MARKET_EDGE=NOT_ESTABLISHED`。第三方许可证及 [THIRD_PARTY_NOTICES](../../THIRD_PARTY_NOTICES.md) 保留，MiroFish 不能被描述为 MIT 组件。

最终结论由上述独立层次共同决定：**工程修复可构建、可测、可追溯，不自动等于旧生产实例已升级；真实日常交付通过，不自动等于研究假设或投资优势成立。**


## 七、剩余条件、顺序与停止规则

| 时段 | 可执行顺序与前置条件 | 完成证据 |
| --- | --- | --- |
| 接下来两周 | 先对实际 9 项复合旧业务逐一给出可核 identity；盘点所有登记根、祖先与可得 provider/transport 原件。无法重建的旧副作用保留 UNKNOWN 与全局来源闸。准备固定 Authority 载体的备份、失主隔离和新的人工恢复协议，不自动启用 | 私有完整映射、漏项检测、前后原件 SHA、预算已知/未知分类；环境回执不能代替来源恢复 |
| 一个月 | 在来源和运行授权具备之后，审核完整恢复计划及切换/回退方案，先小范围验证，再事前冻结 operational 20 会话协议；每个自然会话保留终态、延迟、缺口、干预及成本 | 原 business/attempt、原生来源/监督回收/五期限原件、未成熟尾和缺日保留；不是把一月日历当作保证完成期限 |
| 三个月 | 在连续运行和标签真实成熟后，按 V2 冻结设计进行滚动独立评估；接入有权且日期化的成交、费用、公司行动及资金证据，再评估可交易性、集中度、容量和压力 | 试验账本含失败与选择，独立样本/多重探索范围清楚；没有证据仍禁止收益曲线和市场优势声明 |

应保留：原策略公式/阈值及 UNKNOWN 语义、原 supervisor/cleanup 和账户安全断言、原件身份与首见时间、独立数值 oracle、锁定环境与上游许可。可以简化：只保留一个主动业务 gateway 和薄 CLI，旧版本作为精确输入的受约束兼容包；删减入口须先证明对应能力已迁移，不能删除历史证据。应停止投入：继续复制整段执行器、没有完整成本对照的 engine 提速叙事、未登记反复调参、用核心成功或模拟收盘后验代替完整交付/执行证明。

如果只能优先解决三件事：**第一，核清旧业务/来源/预算并保持共同权威不可绕过**，否则每次恢复都可能重做旧失败；**第二，取得授权后完成真实20会话及成熟五期限闭环**，否则工程通过仍不能说明日常可靠；**第三，建立独立标签和可授权执行证据的样本**，否则收益算术无法区分研究假设与真实可交易优势。这三项按前置依赖推进，不能用新增脚本数量替代验收。


## 八、冻结责任层定位

| 问题 | 文件、行号及主要符号 |
| --- | --- |
| P1-01 | [business_authority_v1.py:236](../scripts/business_authority_v1.py#L236) `Authority`；[active_business_gateway_v1.py:446](../scripts/active_business_gateway_v1.py#L446) `run_once` |
| P1-02 | [codex_runtime_recovery_v2.py:142](../scripts/codex_runtime_recovery_v2.py#L142) `plan_recovery`；[active_environment_acceptance_v1.py:169](../scripts/active_environment_acceptance_v1.py#L169) `accept_environment`；[active_read_control_v1.py:200](../scripts/active_read_control_v1.py#L200) `dispatch` |
| P1-03 | [active_source_bootstrap_v1.py:93](../scripts/active_source_bootstrap_v1.py#L93) `annotate_preflight`；[active_outcome_pipeline_v1.py:149](../scripts/active_outcome_pipeline_v1.py#L149) `execute`；[active_outcome_input_v1.py:116](../scripts/active_outcome_input_v1.py#L116) `compose`；[daily_delivery_contract_v1.py:754](../scripts/daily_delivery_contract_v1.py#L754) `audit_sequence` |
| P1-04 | [execution_evidence_v1.py:66](../scripts/execution_evidence_v1.py#L66) `reconcile` |
| P2-05 | [codex_research_protocol_v2.py:111](../scripts/codex_research_protocol_v2.py#L111) `freeze_plan`；[research_trial_ledger_v1.py:261](../scripts/research_trial_ledger_v1.py#L261) `claim_study` |
| P2-06 | [active_runtime_v1.py:400](../scripts/active_runtime_v1.py#L400) `build`；[active_identity_v2.py:625](../scripts/active_identity_v2.py#L625) `component_identities` |
| P2-07 | [durable_stage_metrics_v1.py:99](../scripts/durable_stage_metrics_v1.py#L99) `StageJournal` |
| P2-08 | [engineering_ci_scope_v2.py:78](../scripts/engineering_ci_scope_v2.py#L78) `classify`；[engineering_ci_scope_v2.py:110](../scripts/engineering_ci_scope_v2.py#L110) `partition_tests` |


## 九、本地最终命令与文件保护

实际使用项目daily-review Python，以 `-I -B -X pycache_prefix=<新TEST_FIXTURE目录>` 启动保护引导，再执行 `stock-fish/scripts/run_isolated_tests.py -q` 及下列完整模块；显式传入新的 `--basetemp`、`--junitxml`、`--tb=short`。外层只允许本地HTTP测试loopback并避免反向DNS，真实采集/计算worker另有OS拒网。准确argv保存在私有 `release-acceptance-command.json`，exit 0。

```text
tests/unit/test_active_environment_acceptance_v1.py
tests/unit/test_active_gateway_security_v1.py
tests/unit/test_active_identity_v2.py
tests/unit/test_active_job_metrics_v1.py
tests/unit/test_active_outcome_input_v1.py
tests/unit/test_active_outcome_pipeline_v1.py
tests/unit/test_active_read_control_v1.py
tests/unit/test_active_runtime_v1.py
tests/unit/test_active_service_host_v1.py
tests/unit/test_business_authority_v1.py
tests/unit/test_codex_active_v1.py
tests/unit/test_codex_runtime_recovery_v2.py
tests/unit/test_daily_delivery_contract_v1.py
tests/unit/test_daily_outcome_authority_v1.py
tests/unit/test_engineering_ci_scope_v2.py
```

`git diff --check`、最终AST/JSON检查、独立build/verify和用户文件保护检查均退出0。初始开发review HEAD `2064173d15c478093fc9b3db90931e2aa04391e4` 保持；发布基线main `2bfbbbb8bd08b0f49c081b26ec77f7da61c7345e`。开发原2043个非任务文件与10项既有删除保持，11个已有文件的修改属于本任务，另51个新增任务文件；额外native_r2本地实现未发布。发布检出未纳入任何非任务变化。

SSH与无凭据HTTPS远端查询此前均退出128；没有搜索凭据。发布使用已连接GitHub工具，精确Git树并在更新main时校验预期原SHA，禁止force；远端实际结果见报告首表。未执行生产切换、旧请求重试、真实20会话、全市场回测、账户真实执行或投资验证。测试误采集事故及其未知成本已在第一节单列，不能把本段误读为本轮从未发生真实网络。
