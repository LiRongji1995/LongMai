# 2026-11-05 个人自用精简

当前工作树只保留本机股票研究所需实现。精简前已发布版本为 `099803532717c60c64f37f9285b56da5c63b83ce`；此前未提交源码及两份检出差异另有经过 SHA 验证的私有备份，不进入仓库。本轮保留流程依赖的既有 SHA/长度校验修订一并纳入交付。

## 保留模块的职责

| 模块组 | 必要功能 |
|---|---|
| `codex_daily_v2`、`codex_workspace`、`codex_capacity` | 唯一用户入口，原登记/operations/祖先账本锁、容量、防重与有限执行 |
| `codex_premarket*`、单个 V3 进程监督器 | 原盘前计划和工作流、超时/资源限制、拥有的进程树回收 |
| `codex_core*`、`codex_result_view`、`codex_verification` | 核心输入、保存成果内容认证、历史读取及导出；无候选执行树 |
| `yaogu/provider*`、`request_queue`、`premarket_capture/runtime*`、normalizer/来源/日期资格模块 | 更新及复用所需数据、父来源与时间完整性、采集账本 |
| `engine`、`strategy_experiments`、`stage_aware_momentum`、R1/R2及其事实/评分/题材数学依赖 | 原 PV、V2、冻结 DAILY 与既有 R2 的确定性研究和解释 |
| `candidate_service`、`storage`、`jobs`、结果存储/导出模块 | 不可变历史、原SQLite结构、业务作业状态和只读结果 |
| 后验/评分、archive bridge及旧manifest读取器 | 原run的成熟度、必要统计、来源鉴证与历史格式读取 |
| `snapshot_backup`、`file_integrity` | 含WAL/来源/账本的备份、新目录恢复与SHA/长度检查 |

## 实际移除

- Go、Live、Qlib上游镜像与训练/推理包装、MiroFish、LLM/飞书/模拟桥、网页/API与其依赖。
- `codex_chat`、旧daily/research/stockfish入口、shell/PowerShell包装、未启用active树和authority平台。
- 多代环境恢复/源码bundle构建/性能基准/共享候选engine/独立研究实验、重复监督版本及旧调度入口。
- Docker、Windows安装与跨平台发布工具、分层冗余requirements、复杂CI选测/多组件构建。
- 旧交付文档和archive源码文档。没有把删除内容搬入新的archive目录；真实结果、RAW、失败、预算、凭据与备份不清理。
- 仅服务已删除功能的测试随功能删除；有效策略/来源/存储/后验断言改为直接业务接口测试。测试删减不计入源码减量。

## 统计口径

以本轮开始时两份检出的私有SHA清单与最终工作树比较，不按较早Git版本抹掉既有未提交实现：

| 口径 | 精简前 | 精简后 |
|---|---:|---:|
| 项目应用Python源码 | 435文件／164,148行 | 103文件／41,349行 |
| 全仓实现源码，另含上游和多语言 | 1,002文件／296,245行 | 103文件／41,349行 |
| 根协作说明推荐的日常Python用户入口 | 4 | 1 |
| 日常直接运行依赖，递归展开requirements去重 | 13 | 6 |
| 原总requirements直接依赖 | 27 | 6 |
| 自有操作与规格文档 | 163 | 21 |
| 原登记环境已安装分发包，含传递及测试依赖 | 58 | 42 |

应用源码统计`stock-fish/`下`.py`，含维护脚本及被ignore遮住但仍被Git跟踪的旧记忆库，排除`tests/`和上游`MiroFish/`；物理行包括注释和空行。全仓实现统计py/go/js/ts/tsx/jsx/vue/html/css/sh/ps1/pyx/pxd，排除test/tests/testdata/fixtures路径、test_前缀和_test.go，也排除受保护的review历史证据复算脚本。两项均是当前工作树实现量，不声称全部旧代码曾日常运行，也不把删除测试计入源码减量。Qlib示例notebook另行删除，不折算为源码行；旧模型文件原字节留在本机，已从新发布树取消跟踪。

入口按本轮开始时根AGENTS的命令路径去重：daily_v2、daily、chat、research收敛为daily_v2。内部worker和测试运行器不算用户入口。文档只计md/rst，排除上游、Go/Live、测试说明及受保护历史研究结果。未将源码搬进archive，历史数据未删除。

明确补缺接入同一盘前入口的`--collect-missing`，不增设平行采集器。保留原预算、失败账本与锁；修复通用后验绕开R1/R2入口及旧tracking请求防重、恢复路径穿越与符号链接边界。策略公式及9件配置的原字节保持不变。验证和发布边界见 [CURRENT_STATUS](CURRENT_STATUS.md)。
# 2026-11-06 个股资讯采集初筛

在原盘前入口增加近5交易会话龙虎榜汇总及 `COLLECTION_PRESCREEN_V1`：龙虎榜或强成交额通道入围，候选之外不采个股资讯，未知单列。沿用原请求、有限预算和内容验证；全池行情、复权、题材、策略公式和原名单不裁减。无新依赖、数据库或运行树。具体使用及边界见 USER_GUIDE；工程验收与未执行范围见 CURRENT_STATUS。
