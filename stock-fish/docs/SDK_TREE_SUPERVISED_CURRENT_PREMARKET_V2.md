# SDK 子进程树监督 V2 与 11 月 2 日盘前验收记录

本轮新增外层 SDK 进程树监督、当前日历入口、累计预算适配和完整 original 工作流桥接。2026-11-02 的固定请求已真实执行一次：最新日历、主板范围完整复权和核心采集完成；完整盘前流程失败。失败后只进行工程修复、隔离检查和只读取证，没有再次采集、增加原预算或追认失败为成功。P0 尚未全部闭环。

## 实际结果与剩余缺口

观察日为 2026-10-30，目标交易日为 2026-11-02，固定 request-key 为 `premarket-2026-10-30-target-2026-11-02`。继续使用原恢复登记 `shared-daily-recovered-20261027`、原配置、原锁、原状态根、冻结 V3 source bundle 和 `engine=original`。原 `max_seconds=10800`，请求/SDK/HTTP 上限分别为 1024/10000/32768；未扩大。

| 范围 | 实际记录 | 验收边界 |
| --- | --- | --- |
| 当前日历 | 833 个自然日，范围 2024-08-24 至 2026-12-04；1 请求、3 次实际 SDK；独立外层监督 COMPLETE、根已回收 | 首见时间为 2026-11-01T21:14:42.538103Z；不回填为过去可见 |
| 主板核心采集 | 原采集 summary 为 COMPLETE、ready_for_core=true；25 个价格会话；3,194 个证券身份、64 批全窗复权因子查询，窗口 1990-01-01 至 2026-10-30；64 批均 COMPLETE 且 pagination_complete=true | 这是原研究范围，RAW 并集 5,227 不能替代复权分母，也不代表全 A 股都已就绪 |
| 核心成果 | 原核心 manifest 为 PASS，3,043 个 eligible/evaluated 身份；已生成产物 | 完整盘前未完成，不能单独升级为最终清单或 DAILY_V1 就绪 |
| 题材采集 | 84 个行业与 175 个概念成员请求均有保存记录；行业 84 COMPLETE，概念 165 COMPLETE、10 PARTIAL | 10 项保留 `PAGINATION_OR_EMPTY_RESULT_NOT_VERIFIED`；不改成 COMPLETE/EMPTY，不自动重试 |
| 完整流程 | 内层 final-control 为 FAILED；外层为 FAILED_OR_UNKNOWN；命令退出 2 | 没有完整工作流 completion、最终展示或完整盘前验收通过 |

题材的 10 个 PARTIAL 节点为 `gn_jxlg`、`gn_zczr`、`gn_sgqgg`、`gn_szbd`、`gn_qszc`、`gn_sbzc`、`gn_bxzc`、`gn_jjzc`、`gn_shbd`、`gn_jrcg`。记录只证明分页或空结果的完整性未被验证，不能据此推断服务截断、条数上限或网络故障。题材阶段共 2 个目录与 259 个成员请求；该 261 是阶段分母，不是整条作业请求数。

全程覆盖类型继续是 `RECONSTRUCTED_HISTORICAL`。晚到下载使用实际首见时间，`historical_pit_restored=false`；既有策略、公式、权重、阈值和历史 run 保持。`DAILY_V1=UNKNOWN_NOT_EXECUTED`，`MARKET_EDGE=NOT_ESTABLISHED`，未做新增投资有效性验证。

## 真实失败与时钟边界

本次实际入口使用 V1，所有对应源码和控制记录保留。外层命令从 2026-11-01T21:14:20.456779Z 到 2026-11-02T00:51:48.038098Z：UTC 经过 13,047.581319 秒，monotonic 经过 5,627.878158 秒，两时钟相差 7,419.703161 秒。控制记录不足以单独确定系统暂停或时钟修正的原因；只依据 monotonic 无法反映实际 UTC 差值。外层终验时以 `SUPERVISOR_UTC_DEADLINE_EXCEEDED` 拒绝成功。

内层原作业另记录 `TimeoutExpired`：数值进程采样命令 `ps -axo pid=,ppid=,rss=` 在 5 秒内未返回。两个失败按各自层次保存；不以日志推断一个必然导致另一个。

工作流监督有 17,506 个样本，观测树 RSS 峰值 1,912,004,608 字节，低于原 3 GiB 上限。本次真实失败不是已确认的 RSS 超限事件。内存超限后的 TERM/KILL/回收验证属于隔离工程测试。

V1 清理记录为 `root_reaped=false`、`child_exit_code=null`、`SUPERVISOR_ROOT_REAP_TIMEOUT`；同时保存 `remaining=[]` 和观察到的进程已退出标记。当前只读内核核对未发现原记录的进程 PID，但不能用当前不存在补证当时的 `waitpid`。该真实回收缺口仍保留为失败。

V1 的 `max_sample_gap_seconds` 是 monotonic 间隔，不能当作 UTC 连续监督的证据。最后两条样本 UTC 从 23:51:45 跳至 00:51:41，而 monotonic 只前进约 1.74 秒。V2 将两个时钟的最大采样间隔分别记录。

完整成功验收器未继续执行：原业务三小时预算已经耗尽，且前置完整交付不成立。失败后只读取小型控制文件核对保存事实，未进行新的 SDK/SQL/原生来源能力认证。不得以剩余请求次数换取新的执行时间，或借用旧失败恢复预算。

## V2 清理修复

V1 有两个需要工程修复的风险。第一，后代等待与根进程回收共用 kill grace，前者可能耗尽后者，只剩约 10 ms。第二，已登记 macOS/psutil 的公开 `create_time()` 会受启动时间/系统时钟修正影响；进程表缓存实例与新实例可能报告不同值。V1 用它比较所有权，可能把原进程误判为身份变化。真实清理瞬间没有保存足够的新身份证据，因此不能把第二点写成已经证实的本次故障根因。

V2 使用稳定的内核启动身份、PID 和 UID 识别所有权；仅接受已登记 psutil 7.2.2 的明确平台 API，不以墙钟时间降级。identity 的 create_time 字段由 identity_basis 明确标记为 native monotonic token，不是公开的墙钟创建时间。根与后代清理、终末内核回收和异常路径分别接受有界检查。只接受真实内核 wait 结果，不以 ECHILD、PID 消失或伪造的零退出码证明回收。原失败优先，清理成功不能把超限或采样失败变成业务成功。

准入要求 SIGCHLD 为默认处置；该模块必须是根进程唯一的回收者。macOS 无法通过此接口原子排除同进程任意外部 waitpid 线程，因此不对违反该调用合同的环境宣称完整保障。本轮工程验证平台为本机 macOS，未验证 Linux 或 Windows 的真实运行。

退出瞬间的进程表缺行另有严格路径：仅在未见明确身份冲突或权限不明时，允许唯一 RootAnchor 通过真实 waitpid 取得本子进程终态；仍存活、ECHILD 或无终态证据均不通过。根已回收后，旧 SID/PGID/父 PID 不再授权发现新身份，只处理此前已认证的后代。没有真实活跃 RSS 样本仍不得成功。

V2 只作为新版本增加，不覆盖实际执行 V1、原 bundle 或旧 producer。V2 日历与盘前入口仍使用同一个版本无关的日期槽、原登记、原 request-key 与排他锁。已有 V1 成功、失败或不明 claim 均阻止 V2 再次执行；升级版本不产生新预算或重试授权。本次没有用 V2 重跑真实业务。

监督是主动抽样，默认等待 250 ms 再加采样成本，不是操作系统内存硬配额。已观察后代即使改变 PGID/SID 仍按精确身份跟踪；从未观察且短时脱离的后代、采样间隙瞬时峰值不作完整覆盖声明。系统暂停期间无法采样，恢复后仍按真实 UTC 截止拒绝成功。

## 接线与累计预算

入口先只读规划，持久化固定当日 claim 后才可能调用 SDK。真实继承 pipe 传递一次性票据，工作进程校验父子身份、argv、目录、claim、源码全文 SHA256 及七字段物理指纹。保存 JSON 不能代替活进程票据；进程上下文也不能授予来源真实性或历史可见性。

新增 helper 在冻结 bundle 外按明确路径加载，不更改 bundle 的 `scripts` 搜索路径。计量适配器仅创建私有函数上下文，不覆写原模块全局；同一次 run 的日历门禁、执行与验证共享一个原生 Budget，不在每次函数调用时重新发放额度。未知实际调用数保持未知。

原 8 GiB supervisor 读取额度内预留：外层进程树 3 GiB、父桥接 256 MiB、子桥接 512 MiB；原内部监督使用扣除上述及此前消耗后的余额。父协调器已用读取 P 与内部尚未归账的读取 S 在每次原回调后共同检查 P+S，返回后只归账一次。run_once 的 8 MiB 和最终控制的 16 KiB 为明确的保守准入预留，不冒充实际 IO。

原 768 MiB 输出额度内分别预留外层监督 8 MiB、父控制 8 MiB。规划、日历、完整流程按一个累计实际时间扣减，保留 30 秒最终处理空间。清理只用于停止与回收，不重新开启业务时间。原 self RSS 2.5 GiB、tree RSS 3 GiB、各业务读取与储存上限保持。派生配置只能缩减 limits，其余策略、状态与来源配置必须一致。

外层 COMPLETE 仅证明监督层通过。完整盘前仍需原工作流的来源、日期、最终控制、产物、原始退出码和末验全部通过。候选完成文件不能覆盖晚期失败，缺失或被替换的控制文件不返回成功。

## 工程检查与保留的失败

实际执行前，发布检出的 15 文件组合为 **281 passed，退出 0**；pytest 203.07 秒，外层 UTC 207.112762 秒。该组合覆盖监督、日历、桥接、计量适配、原复权恢复、原盘前入口、原工作流与 CaptureCache。另一次真实 pipe → 冻结 bundle → 原 gateway/run_once 的 TEST_FIXTURE 接线在 SDK 前显式停止，监督 COMPLETE、根与已观察后代均已回收；它不构成真实市场成功。

发布检出的最终 V2 六文件组合为 **143 passed，55.43 秒，退出 0**；外层 UTC 59.227880 秒、monotonic 59.228537 秒。命令实际运行于 2026-11-02T12:31:11.983557Z 至 12:32:11.211437Z，未自动重试。另一次 V2 实际 pipe → 冻结 bundle → 原 run_once 的 TEST_FIXTURE 接线通过，退出 0，47 个样本、树 RSS 峰值 41,033,728 字节，监督终验 UTC 17.200087 秒；根进程及已观察后代均已回收，SDK/SQL/策略调用均为 0。V2 真实采集、完整盘前验收没有执行，不计入通过项数。

原件保护检查覆盖 6,566 个基线条目：6,543 个 SHA256 与七字段物理指纹原样保持，22 个文档仅在首标题后增加本轮说明。唯一其他差异是原运行器的可变 `premarket.lock`：原生 JobLock 正常取得锁后将其改写为本次 worker PID 与启动时间，同 inode、权限和载体继续保留；它不是历史结果。严格全件原样检查因此退出 1，其 FAIL 回执原样保留；补充核对绑定原源码、实际根身份及锁时间后退出 0，明确识别这一正常运行变化，没有修改断言或倒写锁文件。历史 latest-job 指针保持原样。

本轮真实 V1 的 10 个新增 Python 文件在开发与发布检出共 20 次 SHA 核对通过，实际 claim 绑定的 4 个 helper 同时保持原七字段物理身份。新版本另存，不改其来源身份。失败控制取证第二轮退出 0；首轮配置补默认值比较的工程断言失败保留。两轮仅读取控制证据，合计约 13.67 MB（首轮未发布完整计量回执，其读取数按已选控制长度重建并注明），不属于新的业务采集或来源能力认证。

测试使用独立 TEST_FIXTURE DB/state/artifact，不混入真实历史，没有删除失败样本、扩大 skip 或降低正确性断言。早期组合、枚举终态竞态失败、计划超限失败和本次真实失败均保留在私有审计目录。V2 曾出现 41 passed / 1 failed 的真实终态竞态，以及 39 passed / 1 failed 的测试准备时序问题：后者原先用固定短睡眠假定根已退出，后改为有界等待内核实际僵尸态，不提前 wait/reap；零 RSS、失败状态、退出码和无信号断言均保留。一次后续 59 项组合为 58 passed / 1 failed：新 worker 票据身份正例实际耗时 3.298 秒，超过该例原 3 秒夹具限制，监督器正确拒绝成功；此例验证身份而非性能，因此仅其 TEST_FIXTURE 限时改为 10 秒。生产预算、超时反例与所有正确性断言保持。最终检查使用这些明确修正后的版本。测试通过不能改判真实失败。


最终 V2 组合命令（路径变量对应本次明确选择的发布检出、登记 Python 和新私有验收目录）：

```sh
cd "$PUBLISHED_CHECKOUT"
"$REGISTERED_PYTHON" -I -B \
  -X "pycache_prefix=$PRIVATE_ARTIFACTS/publication-v2-final-absent" \
  stock-fish/scripts/run_isolated_tests.py -q \
  tests/unit/test_codex_sdk_tree_supervisor_v2.py \
  tests/unit/test_sdk_tree_supervisor_v2_reference.py \
  tests/unit/test_codex_current_calendar_v2.py \
  tests/unit/test_codex_supervised_premarket_v2.py \
  tests/unit/test_supervised_premarket_v2_reference.py \
  tests/unit/test_codex_metered_bundle_v1.py \
  --basetemp "$PRIVATE_ARTIFACTS/TEST_FIXTURE_publication_v2_final" \
  --junitxml "$PRIVATE_ARTIFACTS/publication-v2-final.xml" --tb=short
```

所有测试、接线与只读取证的原命令、退出码和失败日志分别保存；未执行真实 V2 SDK 采集、完整最新盘前终验、历史收益回测、券商连接或跨平台部署验证。

## 入口与后续验收条件

默认日常入口与 `engine=original` 保持；本轮源码发布不切换服务、登记或调度。显式 V2 入口为 `scripts/codex_supervised_premarket_v2.py`，先 `premarket-plan`，在原准入、预算和同日 claim 允许时才 `premarket`。路径由原登记与环境变量传入，不内置个人目录、代理或凭据。

2026-11-02 的已有槽已失败，V2 也必须拒绝。剩余 P0 是在有效业务日期与明确可用的原预算内完成真实连续监督、题材完整性及最终 original 盘前验收；本次不通过换版本、换 key、换 engine 或续用其他请求预算绕过它。旧失败恢复成果、历史策略、历史输出与实际首见时间继续保留。

源码发布只包含新增脚本、隔离测试和本文档；私有登记、日志、真实市场数据、数据库、模型、凭据及完整审阅包不入库。
