# 架构、交付与量化评价优化 V1

2026-11-04。本轮根据项目全面审阅后的明确工程授权实施。旧核心、策略配置、来源producer、已认证旧脚本、原run与失败请求保持；新能力位于scripts及其contracts子目录。默认日常入口仍为 `codex_daily_v2.py`，默认 `engine=original`。源码发布不自动切换登记、服务、调度或重试业务。

## 建议与实际工程对应

| 审阅建议 | 本轮实现 | 真实验收边界 |
|---|---|---|
| 统一进程监督 | `codex_sdk_tree_supervisor_v3.py`、`codex_metered_bundle_v2.py`、`codex_supervised_premarket_v3.py` 把内外层接到原生进程身份、采样与唯一waitpid回收 | 真实TEST_FIXTURE子进程及原完整验证器另验；最新市场流程未因工程通过而成功 |
| 全池与每日交付合并 | `codex_operational_report_v1.py delivery-report` 组合原交付审计和完整coverage，要求同一作业内原canonical载体、run、日期、策略及完整hash绑定 | 原最终FAILED不能被core PASS覆盖；送达、当前来源重认与DAILY就绪保持独立 |
| 执行证据与研究评价 | `codex_research_protocol_v1.py` 提供冻结、成对阶段分析、覆盖、真实日历支撑检查、区块敏感性、风险分层与条件执行API | 保存JSON只证明一致性；R1来源能力、真实执行来源、容量、PIT和独立市场优势未建立 |
| 版本及组件收敛 | 显式运行清单、组件依赖清单及统一显式路由；新模板不放进旧config动态扫描范围 | 显式清单只认证声明文件/图，不替代旧完整闭包或自动证明动态依赖完整性 |
| 全流程性能与存储 | 原操作回执耗时汇总保留失败/缺值；增加受限ABBA分块格式实验和独立逐行恢复 | TEST_FIXTURE耗时、Python分配峰值与真实端到端/RSS分列；未采用新生产格式、迁库或删除旧数据 |
| 发布可复现性 | 根级按组件CI、固定关键测试、发行profile检查、隔离runner文档 | 当前锁仅覆盖原目标平台；Docker/Live/Go尚缺的制品验收保持UNVERIFIED |
| 健康与业务恢复 | Docker检查现有 `/health/ready`；新目录恢复演练绑定原备份与指定文档probe | readiness不等于每日数据就绪；RTO实测，未有独立源水位时不编造RPO |

## 统一显式入口

从业务目录使用已核验的Python。命令中的变量均为本次明确选择的实际路径；不内置个人目录。`--output` 为父目录已存在的新私有JSON文件，已有文件拒绝覆盖。

```sh
"$PYTHON" -I -B scripts/codex_operational_report_v1.py runtime-manifest \
  --runtime "$EXISTING_RUNTIME" --registry-root "$REGISTRY" \
  --output "$NEW_RUNTIME_REPORT"

# 恢复登记必须显式增加 --recovered-runtime，不自动回退或新建登记。
"$PYTHON" -I -B scripts/codex_operational_report_v1.py delivery-report \
  --job "$ORIGINAL_JOB" --run "$ORIGINAL_CANONICAL_RUN_GZIP" \
  --run-file-sha256 "$ORIGINAL_CARRIER_SHA256" --output "$NEW_REPORT"

"$PYTHON" -I -B scripts/codex_operational_report_v1.py component-manifest \
  --source-root "$SOURCE_ROOT" --spec scripts/contracts/operational_components_v1.json \
  --spec-file-sha256 "$EXACT_SPEC_SHA256" --output "$NEW_COMPONENT_REPORT"

"$PYTHON" -I -B scripts/codex_operational_report_v1.py performance-report \
  --operation "$ORIGINAL_OPERATION" --state-root "$ORIGINAL_STATE_ROOT" \
  --output "$NEW_PERFORMANCE_REPORT"
```

`delivery-report` 可以另传完整 `--outcomes` 与 `--outcomes-file-sha256`，沿用原五期限和未知语义。未传后验记NOT_PROVIDED。完整报告生成成功只表示审阅成功，不能把命令退出0作为业务流程成功。若已保存载体超过原诊断预算则拒绝，不能截断或自动扩额。

统一入口还提供以下严格转发；分隔符 `--` 后参数原样交给各原版本CLI，不补request-key、不改预算、不重试。各模块原命令仍兼容。

```sh
"$PYTHON" -I -B scripts/codex_operational_report_v1.py research -- --help
"$PYTHON" -I -B scripts/codex_operational_report_v1.py release-check -- --profile daily-review --require-hashed-inputs
"$PYTHON" -I -B scripts/codex_operational_report_v1.py recovery-drill -- --help
"$PYTHON" -I -B scripts/codex_operational_report_v1.py layout-benchmark -- --destination "$NEW_TEST_FIXTURE_DIRECTORY"
"$PYTHON" -I -B scripts/codex_operational_report_v1.py supervised -- premarket-plan --help
```

`supervised` 显式路由V3，仍须原登记、原bundle、完整准入、原日期槽、同请求防重和累计预算。既有V1/V2成功、失败或未知槽均不因V3重新授权。它不替换默认入口。新采样的记录明确为原生数值快照；异常遇子进程恰好退出仍保持失败，不伪造回收退出码。进程抽样不是OS内存硬配额，也不能回溯未观察到的短命脱离子进程。

## 研究协议与证据

模板为 `scripts/contracts/research_protocol_v1.template.json`，故意保留需由实际证据填充的空项。`freeze` 使用实际当前时钟，登记发现资料、配置身份、验证/holdout窗口、完整尝试账本、比较家族、期限、区块长度、风险匹配和停止条件。旧样本事后冻结必须标记RETROSPECTIVE；模板本身不是已冻结市场样本。

`pair` 完整核对R1/R2原父、原资格池、基础数值及后验事实；`study` 使用原统计实现，在原后验日历证据与有限官方2026日历相符时检查完整交易日支持。不得把调用方漏列的日期用enumerate压缩成连续会话。缺后验、缺日历、窗口外资料均不能产生市场优势结论。日期化样本、当前题材成员回看和历史PIT保持各自角色。

风险匹配只使用保存run中已存在并通过原校验的字段；缺规模、行业历史身份或其他风险字段不从当前资料补造。区块长度敏感性全部报告，不能挑最好的一组；两个完整区块只是可计算下限。原统计的Holm校正保留。执行情景复用原账户引擎，资金规模/滑点情景不冒充已认证的竞价参与率或冲击容量模型；正式策略账户收益仍不由保存归档升级。

## 当前真实环境与未完成范围

本轮另运行12,000行生成夹具的ABBA存储实验，各布局两次。单文件/分块的墙钟中位数分别为3.926/4.231秒，CPU为2.358/2.407秒，Python分配峰值为4,344,887/3,014,513字节，落盘为89,965/95,297字节，臂内读取为179,930/190,594字节。两种布局逐行数值与UNKNOWN等价；分块的Python峰值约降30.6%，墙钟约增7.8%，落盘及臂内读取约增5.9%。未测OS RSS，未消除同进程与文件系统缓存效应，此结果不能外推真实市场。

另外保留完整集合终验：发布前0.030秒、发布期间两次0.135/0.076秒，三次各读370,524字节；累计产物读取1,852,620字节，报告发布读取31,478字节。它们单列，未从端到端成本中隐去。没有据此采用生产分块格式或删除原件。

本轮先执行既有 `codex_daily_v2.py --runtime shared-daily premarket-plan`，实际退出2，`REGISTERED_RUNTIME_PHYSICAL_IDENTITY_CHANGED`。显式读取已有恢复登记也同样拒绝。只读定位发现：旧default多处设备身份不同，恢复登记的解释器目标inode也与当前不同。未覆盖旧登记、补写旧物理身份或通过换key启动业务。

随后使用原 `codex_runtime_recovery_v1.verify_environment` 对现有共享venv重新验收：平台身份、pip check、安装身份、rules/daily/review三个doctor和独立应用/CLI/SQLite烟测七步全部退出0，总94.324秒，状态ACCEPTED；没有安装包、打开真实业务DB或改登记。此结果只认证当前环境，不能覆盖旧登记中的物理身份，也不恢复来源/PIT。

现有恢复V1只支持从原V1登记创建首代epoch，明确不支持再次恢复已有恢复epoch。新解释器身份接入需要保留全部祖先失败请求的多代恢复协议及实际来源再认证；本轮未伪造这一能力、另起无祖先登记或借旧失败余额重跑。因此，真实V3最新盘前、连续20交易日、真实成交来源、完整独立holdout、市场优势和生产切换均未完成。未来20交易日无法在一次工程验收中提前通过。

## 检查与发布记录

本轮检查使用既有锁定Python、`-I -B`、全新不存在的字节码查找路径、独立TEST_FIXTURE根与JUnit记录。旧失败、初次夹具准备错误和修复后结果分别保留；发布组合及精确变更清单以最终记录为准，不累加不同轮次为一次全仓通过数。

发布检出最终定向组合：**432 passed，234.25秒，退出0**。范围是 `engineering_ci_scope_v1.DAILY_TESTS` 的20个完整测试文件，加旧覆盖、R2执行两个完整文件和旧Holm/退出延迟四项。该结果来自发布检出的一次实际运行，不是累计各代理轮次；未把TEST_FIXTURE升级为真实数据、生产运行或投资验收。

实际使用命令如下。环境变量只替代私有绝对路径；Python为现有已登记解释器，三个输出路径均为当次全新路径。完整argv、cwd、stdout、退出码和JUnit保留在本次私有工程记录。

```sh
"$PYTHON" -I -B -X "pycache_prefix=$NEW_PYCACHE_PATH" stock-fish/scripts/run_isolated_tests.py -q \
  tests/unit/test_engineering_ci_scope_v1.py tests/unit/test_release_profile_manifest_v1.py \
  tests/unit/test_recovery_drill_v1.py tests/unit/test_codex_operational_report_v1.py \
  tests/unit/test_benchmark_artifact_layout_v1.py tests/unit/test_codex_research_protocol_v1.py \
  tests/contracts/test_http_security_capacity_v1.py tests/contracts/test_tushare_transport_security_v1.py \
  tests/integration/test_health_contract_separation.py tests/risk/test_deterministic_risk.py \
  tests/unit/test_research_contract.py tests/r02_correctness/test_backup_restore.py \
  tests/unit/test_codex_sdk_tree_supervisor_v2.py tests/unit/test_sdk_tree_supervisor_v2_reference.py \
  tests/unit/test_codex_sdk_tree_supervisor_v3.py tests/unit/test_sdk_tree_supervisor_v3_reference.py \
  tests/unit/test_codex_supervised_premarket_v3.py tests/unit/test_supervised_premarket_v3_reference.py \
  tests/unit/test_codex_metered_bundle_v2.py tests/unit/test_codex_metered_bundle_v1.py \
  tests/unit/test_codex_research_coverage_v1.py tests/unit/test_codex_r2_execution_v1.py \
  tests/r05_product/test_research_r1.py::test_paired_statistics_missing_blocks_and_holm \
  tests/r05_product/test_research_r1_g_repairs.py::test_g03_delay_boundaries \
  --basetemp "$NEW_TEST_FIXTURE_ROOT" --junitxml "$NEW_JUNIT" --tb=short
```

统一入口的发行检查及研究/恢复/监督帮助命令退出0；组件清单首次因包含一份未发布的本地旧文件而退出2，收敛到已发布共享基线后，实际完整清单命令退出0。未把该无关本地文件同步发布。新源码20份AST、3份JSON、文档入口链接和暂存差异空白检查通过。765份初始文件基线中754份字节保持，其余11份仅为本轮文档及compose改动；另两份既有入口文档也核对了发布前原件。旧核心、配置、137份旧协调脚本、许可证及用户其他改动未变，发布清单精确为39份文件。

未执行完整历史golden套件、全仓真实市场回测、真实SDK采集、跨平台实际部署或券商操作。远端CI实际验收与本机组合分开，不能仅凭已增加workflow宣称远端通过。

发行profile、CI与恢复演练详见 [ENGINEERING_RELEASE_RECOVERY_V1](ENGINEERING_RELEASE_RECOVERY_V1.md)。原[完整盘前协议](PREMARKET_SHARED_EXECUTION_V1.md)、[日常协议](CODEX_DAILY_RESEARCH.md)、[部署](../../DEPLOYMENT.md)和许可边界继续有效。

首次39文件提交后的远端CI暴露了Live发行包未安装及macOS托管解释器严格argv/cwd不一致，两者均保留失败。后续在原授权内补齐非editable wheel安装与两份只读原生解释器诊断/测试文件，累计发布范围增至41文件；具体证据及未决项见上述工程记录。原432项本机结果只归属于其实际版本，不能替代后续远端验收。

macOS解释器诊断保留host与新venv的固定启动命令、实际`sys.orig_argv`、cwd、解释器路径、framework及`_NSGetExecutablePath`，分别检查最小环境/继承环境中的真实子进程和孙进程；不输出环境变量全集。新venv在任一argv/cwd不一致时拒绝进入工程门禁，不归一化身份、不修改旧V2/V3守卫、不跳过旧测试。

本机另用已有Python.org framework 3.10.6做启动机制对照，仅在两个全新TEST_FIXTURE目录创建无pip空venv：launcher创建者的子/孙进程原始argv0被改为框架Python.app路径，探针退出3；从`_NSGetExecutablePath`实际Mach-O直接创建者保持原始argv0，探针退出0，cwd相同。这不是3.12或项目运行环境验收。CI据此在创建前核对真实Mach-O存在且可执行，清除`__PYVENV_LAUNCHER__`与`PYTHONEXECUTABLE`，仅一次启动原`bootstrap_daily_runtime.py create`，仍完整安装与核验原锁；不修补既有venv、不回退或重试。托管3.12.10的完整验收仍待远端实际结果，不能由上述3.10机制对照替代。

第二轮远端host探针已确认cwd相同而framework launcher改写argv0；直接Mach-O启动原builder后，其复制出的`bin/Python`在`ensurepip`阶段收到SIGKILL 9，未完成锁安装或准入。日志没有证实该信号的根因，不能将其归因为已确认的codesign或资源问题。

后续CI候选增加`bootstrap_ci_native_runtime_v1.py`：只接受全新`TEST_FIXTURE_`目录，直接使用原锁校验、平台身份、带记录的安装调用和完整`verify`；创建方式明确为symlinks=True且禁止stdlib复制回退，解释器链接必须指向当前实际Mach-O，原`installed_identity`返回的目标与SHA256必须相同。只有全部原验收及严格子/孙argv/cwd探针成功才写ACCEPTED。既有runtime、原builder、来源文件守卫及旧监督守卫均不修改，失败目录保留且同目标不能重试。本机3.10.6独立空symlink venv机制对照的创建、严格探针及原`installed_identity`全部退出0，prefix准确、链接目标准确；没有安装第三方包，仍不替代托管3.12完整验收。

CI并发组增加提交SHA并关闭自动取消；每个commit使用独立runner临时目录，保留正在进行的旧Go验收和各次失败证据。各job既有上限保持（daily和Go候选45分钟），不同提交的验收消耗分别计入，不自动重试。新提交通过也不改写旧commit的失败或未完成记录。新增CI builder及其测试后，累计发布范围为43文件。

第三轮托管daily已实际完成原锁安装、symlink新venv完整验收和严格身份探针；398项测试中396通过、2失败。V2采样证据测试在固定1.5秒寿命内仅获得46次真实采样，未达到原`sample_count > 64`；V3同项报告FAILED，但原断言未打印完整报告，原因仍不能据该日志确定。本机先仅增强V3完整诊断、保留原夹具执行，实际1项通过，未复现该FAILED。

在本任务工程授权范围内，仅调整旧`test_codex_sdk_tree_supervisor_v2.py`的这一项测试夹具，并在V3镜像同步；监督生产源码、其他旧测试和原正确断言不变。child改为等待parent的第65次真实`after_sample`握手后退出，child自身8秒截止、原监督总限10秒保持。每版增加真实原生快照前固定30ms延迟的独立反例；不缩小样本门槛，仍保留64条证据，并核对全部真实样本的monotonic/UTC最大间隔、总采样时长、正RSS及真实回收。夹具枚举仅在构造PID已不存在时返回空，AccessDenied等其他错误仍传播；原生快照逻辑保持，未把这项修复认定为已证实的远端V3根因。

上述V2/V3正常及30ms慢采样四个独立夹具，登记Python隔离实跑为**4 passed，9.63秒，退出0**。完整value在失败时输出，旧1.5秒失败证据保留。该结果只验证夹具同步与原采样断言，不替代下一次远端完整门禁。

本次将既有V2测试文件的上述单项夹具纳入发布范围，累计为44文件；此处不改变早先39/41/43文件版本的验收归属。

发布检出进一步执行V2/V3两个完整监督测试文件：**82 passed，37.38秒，退出0**，使用原登记Python、隔离runner及独立TEST_FIXTURE根；未将这次结果累加进早先432项组合。

第四轮托管daily（run `41932442083`）实际为**400项中398通过、2失败**，两项均为新增的 `native-delayed-30ms` 参数；两版未加延迟的 `native` 参数均通过。完整失败报告确认V2/V3分别取得64/55次真实采样，child均因自己的8秒截止退出23，报告原因均为 `SUPERVISOR_CHILD_EXIT_NOT_ACCEPTED`，实际UTC耗时分别为8.191293/8.412964秒。这是child存活时间假设不足的直接证据，不能写成监督10秒守卫已经触发；原失败日志继续保留。

本轮仅给新增的30ms延迟采样容量参数显式声明监督30秒、child 25秒的固定测试预算；未加延迟参数仍为监督10秒、child 8秒。这里检验超过64次采样后的证据保留与汇总，不能据此宣称65次采样必在10秒完成。第65次真实采样握手、`sample_count > 64`、保留64条、全部样本的monotonic/UTC最大间隔和总采样时间、原生身份与RSS、实际退出回收断言全部保留，并核对封存claim的本次预算和发布终验耗时。没有自适应扩额、重试、降低采样数或修改生产监督守卫。

每版另增同样30ms真实快照下固定1秒预算的独立反例：65次延迟本身已超过1秒，child仍等待同样第65次握手且自身25秒截止，必须由监督报告 `DEADLINE_EXCEEDED`，握手不得发生，并核对真实信号退出、根进程回收、无遗留已观察后代、无清理错误和零业务预算延长。原其他截止及权限反例保持不动；本次变更不会把清理耗时伪称为业务预算内完成。

修复后在本机开发目录以登记Python、全新字节码查找路径及独立TEST_FIXTURE实跑V2/V3两个完整文件：**84 passed，40.15秒，退出0**；外层进程计时42.611秒。两份生产监督源码与本轮修改前SHA256一致，原有函数除这项采样容量夹具外逐段保持一致；每版只新增上述短预算反例。完整argv、cwd、stdout、退出码和JUnit保留在独立私有记录，实际命令形式如下。该结果不替代修复版本的下一次远端门禁，也不累加为此前82/432项版本的验收。

```sh
"$PYTHON" -I -B -X "pycache_prefix=$NEW_PYCACHE_PATH" stock-fish/scripts/run_isolated_tests.py -q \
  tests/unit/test_codex_sdk_tree_supervisor_v2.py tests/unit/test_codex_sdk_tree_supervisor_v3.py \
  --basetemp "$NEW_TEST_FIXTURE_ROOT" --junitxml "$NEW_JUNIT" --tb=short
```

第五轮托管daily实际为**402项中401通过、1失败**，仅旧V2的 `native` 容量夹具失败；两版30ms延迟参数与1秒拒绝反例均通过。失败前已取得第65个真实live样本，随后观察中 `snapshot_root=null`、fresh为匹配身份的live/idle，唯一RootAnchor的真实非阻塞wait也仍报告 `LIVE_DIRECT_CHILD`，故原守卫拒绝为 `SUPERVISOR_ROOT_IDENTITY_LOST`。后续清理才观察到zombie并回收退出码0；报告总UTC约1.486秒。最终退出0不能撤销先前证据缺口，日志也未指出快照内具体哪个系统调用造成漏行，不能将退出过渡推断写成已确认的psutil根因。

本轮仅在V2/V3容量夹具的第65次握手后、同一 `after_sample` hook中同步测试child的自然退出：有限轮询真实原生身份和zombie状态，固定最多2秒，并受调用 `run` 前捕获的本次monotonic起点及原10/30秒总截止共同约束。缺进程观察继续保留为未知，不能视作退出成功；身份冲突或其他观察错误失败，未见匹配zombie则明确超时失败。每次观察保存到独立TEST_FIXTURE记录；不调用poll/wait/waitpid、不改返回码或快照、不自行回收，仍由唯一真实RootAnchor证明wait status 0并回收，最终无清理信号。

这段等待只同步容量夹具，不是生产监督器的新重试策略。未加延迟参数10秒/child 8秒、延迟参数30秒/child 25秒、全部样本汇总与64条保留要求保持；等待自然计入后续样本间隔及总耗时。原1秒拒绝、身份丢失、PID复用、ECHILD和终态不能替代live RSS等负例保持不变，生产V2/V3监督守卫不修改。

退出同步初版以原登记Python隔离运行两个完整文件，实际**84 passed，39.13秒，退出0**。随后补充观察后的截止检查，确保一次系统状态读取即使跨过同步截止，也不能事后接受为成功；最终字节版首轮实际**83 passed、1 failed，37.16秒，退出1**。失败为V2延迟参数在第45次采样UTC间隔校验时报告 `SUPERVISOR_UTC_CLOCK_MOVED_BACKWARD`，尚未到第65次退出握手，同步记录为空；清理真实SIGTERM回收为-15，未放宽断言。

该失败保留44条已完成样本，失败样本的UTC时间在原实现抛错前没有持久化，因此不能重建精确回拨量。原完成记录的UTC起止为23:39:12.425587至23:39:14.614251，报告UTC耗时2.188615秒、monotonic耗时2.239597897秒；这些是记录值，不把差额冒充缺失的单次时间戳。随后5秒只读真时钟观察91次未见负UTC间隔，不据此声明环境必然稳定。

经明确授权只做一次同字节、全新TEST_FIXTURE确认，未替换UTC时钟、修改守卫或扩大预算，最终两个完整文件实际**84 passed，37.39秒，退出0**，外层40.029秒。原失败、只读时钟观察、每次完整命令/退出/JUnit、输入文件哈希与四个容量夹具退出同步记录分别保留；没有反复运行到通过。两份生产监督源码哈希不变，两个测试文件在容量函数之外的全部字节一致。最终通过只属于这一次本机确认，远端完整门禁继续独立验收。

## Go 实测热点的最小优化

两轮原完整串行race分别保留在CI run `41928900984` 与 `41930441102`。第二轮15个测试包通过，research/core在默认600秒整包期限超时；第三轮16个测试包通过，仅research超时，core完整通过（584.076秒）。第三轮research的全部78条增量差分路径已通过（522.56秒），之后其他顶层测试继续推进才触包期限。两轮均未出现测试断言失败、DATA RACE或Test级skip；无测试文件的包正常记录为package skip。原capacity样本及CLI生命周期检查通过；原流水线race非零后未执行vet，不能把脚本中的命令回显当作通过。

在全新私有TEST_FIXTURE工具目录下载并校验官方Go 1.27.1 Darwin amd64（71,621,873字节，SHA256 `8f8f52c6649542cf027bbc9b9c68d1ec042f9f34808a40413f0b8b3f66f3caa4`）；工具链、模块缓存及telemetry状态都与全局环境分开。原Go全部270文件与发布提交逐字节一致后，对R1/same_input真实冷计算、增量计算、完整重算三臂做race CPU/alloc采样。父receipt重复canonical哈希单行占总CPU采样约6.09%、累计分配采样约18.87%。

本轮因此仅修改 `stock-fish-go/application/research/fact_dependencies.go`：本次函数调用内，每个父ref只计算一次哈希；用map存在标记保留空哈希行为，不跨调用缓存。当前父来源重认、冲突/缺失/可见性检查、raw bindings、策略、阈值及最终key构造保持。没有同时改动尚无足够热点证据的rolling proof或qualification路径。新增专用测试固定原版7组key/scope，并覆盖同一Resolver的字段、原字节与同ref对象修改；真实缺价父、冲突价父、未来价父、缺master及冲突master都比较暖缓存与无缓存的完整分析和拒绝结果。该定向race检查退出0（4个顶层测试、14个子例），不将顶层和子例相加为独立用例数。

相同子例单次对照：用例时间23.09→20.66秒，CPU样本20.54→18.73秒，累计分配采样843.13→670.87MB；factCache累计CPU为2.39→1.30秒、分配250.32→84.12MB。父hash行分配159.13→1.00MB；新版此行CPU未被采到，不能记为零耗时。两轮均使用 `-race -p=1 -mod=readonly -count=1 -timeout=600s`；原版首次编译用新缓存，候选复用该私有缓存，因此仅比较用例时长，未使用包含编译的外层时间。没有ABBA重复或全市场结论；累计分配不是峰值RSS。

完整Go验收调整为research、core、其余包三组独立作业，保留每个包的全部测试及同一test binary生命周期。两重包显式30分钟测试装载期限，其余包保留Go默认10分钟；每作业45分钟，三组累计配置上限为135分钟，不能称资源预算未增加。包全集由真实 `go list -json ./...` 获取，三个分区必须精确覆盖、无重复、无遗漏，重包缺失直接失败；没有按测试名过滤、修改30秒容量样本或CLI业务期限。race与vet分别执行并保留真实非零退出，终态与Test级skip也核验。新完整包配置是否通过须看其实际远端结果；上述单例和旧轮部分通过均不替代它。

本轮补充发布范围为48文件：新增Go完整包验收helper及其测试、既有Go父receipt哈希函数和新回归文件，其余变更均在此前44文件内。CI固定daily门禁为23个完整文件；分区合同最终42项通过、7.84秒、退出0，工作流12个run步骤的bash语法检查均退出0。原765文件基线仍为753未变、12项授权修改、0删除；Go另以完整270文件发布基线核对，仅上述生产函数修改并新增测试。后续远端结果另行登记。

同一CI分组候选在发布检出进一步实跑两个完整合同文件：42 passed，8.57秒，退出0；仅统计此次独立执行，不累加开发检查。
