> 历史归档：保留当时的说明、数字、失败和限制，仅调整文档相对链接。当前使用请看[当前状态](../../../../../CURRENT_STATUS.md)；原路径可在[归档索引](../../README.md)查找。

# R12 Prospective Integrity Report

本轮从实际干净 main 基线 `0707df6691faa24ad380912b05f30e26c85f8f5e` 开始，仓库与远端再次 fetch 核对一致；没有强制切换、覆盖或修改交易参数。实际仓库为 D:/stockfish-qlib-quant-system；初始 C: 同名空目录不是业务库。最终提交及远端字节绑定由推送后的 Review Bundle provenance.json 记录。

## 当前身份与真实截止协议

- validation_epoch_id: `validation_epoch_dc93276142daab1c1345ef13f0e21e986e8da91988aceec1562878a27b1dc1cf`
- implementation: `R12_STAGE_AWARE_MOMENTUM_V2_INTEGRITY_HISTORY`
- spec: `R12_INTEGRITY_HISTORY`
- signal source hash: `0fa40f26aa474fa154d3e4a7cc58409ebecd0d6dd75e4910079a435113bb3828`
- validation protocol hash: `2a430237d984f0a2d9683f479e8b761e7657044821b79ad1b5afc9eb79ed1dbc`
- score spec identity: `f09940a6725dcc52b1e28bdad9d3f4f5b5cf004d809d4445d4f96bc3fea8d4c8`
- cutoff policy: `SCHEDULED_2030_ACTUAL_COMPLETION`
- protocol: `SCHEDULED_TRIGGER_20_30_ACTUAL_DECISION_CUTOFF`

20:30 是调度触发时间，实际完成时间是 decision cutoff。记录 scheduled/start/completed/actual 四时钟与启动、采集、总延迟。排队、采集和信号计算期间若源码/协议 epoch 改变，旧作业失败并保留审计，不能用旧 epoch 作业保存新身份正式信号。EVENING job/event ID 也包含 epoch，避免同日旧作业阻塞新 epoch。

## 持久服务实测

服务已通过仓库 Start-StockFish.ps1 恢复为单进程 Waitress；重复启动复用相同 PID，第二 JobService 访问同一真实 DB 被 OS 锁拒绝，原因 another job worker service owns this database。未触碰占用8000的无关进程。

| 核验对象 | 实际值 |
|---|---|
| HTTP | http://127.0.0.1:8001 |
| PID | 15908（venv launcher 37432） |
| Process creation time | 2026-09-08T17:00:42.763957+08:00 |
| Repo/root | D:\stockfish-qlib-quant-system |
| DB | D:\stockfish-qlib-quant-system\stock-fish\data\stockfish.sqlite3 |
| Candidate state root | D:\stockfish-qlib-quant-system\stock-fish\data\core\yaogu |
| Scheduler thread | stockfish-candidate-schedule / ident 38452 / native 38452 |
| DB job owner | worker_015e60db0d0645e7aa521e48f59dad82 |
| Process/config registration | work/stockfish-process.json、work/stockfish-service.json（忽略的本机文件） |

核心 application/tracker/jobs 健康且 scheduler 线程真实存活；总 health 可因可选模型/旧组合依赖显示 degraded / production_ready=false，这不是行情已就绪。服务独立于 Codex 任务存活；本轮未创建 Windows 登录自启动或系统级服务注册，关机/登出后的恢复需运行既有启动脚本。

## 截止前实测

本轮开始恢复服务时为2026-09-08 17:00 Asia/Shanghai，尚未到当日合法20:30。真实计划的 signal_session=2026-09-08，target_session=2026-09-09，scheduled_at=2026-09-08T20:30:00+08:00。

当前 epoch 的 primary_capture_attempted_days=0、evaluable_official_signal_days=0、data_unavailable_primary_days=0、valid_no_focus_days=0；latest actual primary 尚不存在，不能提前声称 first official signal exists。当天R11旧16:12 preliminary仅是历史诊断，不是R12 official primary。

Post-cutoff入口 `stock-fish/scripts/r12_post_cutoff_check.py` 只读核对当前 epoch 当日 event/job、保存的run summary、input实际SHA256、capture/countable、质量、各分类计数和Score；失败也记录实际错误类型。真实验证输出 pre-cutoff-check.json 为 BEFORE_CUTOFF_NO_PRIMARY_CLAIM。

## 验证证据

真实运行、数据续采、最终测试与成绩状态在本页最终核验附录及同目录交付摘要中记录。浏览器已实际打开服务页面并核对 epoch、Evidence Type、Actual Signal Cutoff、Data Quality Status、四个计数与N/A状态，五个Scorecard方向保留。历史重构、数据不可评价和正式前瞻不互换身份。

本机证据根目录：reports/codex/2026-09-08/r12_integrity_history/。源文件提交以后先push并verify HEAD=origin/main=live remote main，再由 build_r12_review_bundle.py 从 git show FINAL:path 构建source目录和逐文件SHA256/Git object manifest；runtime evidence单列，绝不冒充commit源码。

`MARKET_EDGE=NOT_ESTABLISHED`。工程正确性及服务存活不证明投资优势；历史和正式可评价天数仍按真实证据报告。

## 最终核验附录（2026-09-08 17:17—17:20 Asia/Shanghai）

真实ON_DEMAND_CAPTURE作业 capture_4c6199ac6b464389bc1fd5ddd17f2ed5 于17:13:53 SUCCEEDED，quality=UNAVAILABLE、activated=false；不是正式晚间primary。实际98次task admission、74次HTTP admission、179次SDK operation、57次cache hit，保持128/600秒原取数预算；包含归一化与计算总耗时767.454秒。Waitress PID15908采样peak RSS923,172,864bytes（OS peak working set923,463,680bytes）；与流式迁移130,613,248bytes及合成new159,555,584bytes是三个不同工作负载，不能互相替代。

明确选择此次保存input `market_e642f51a9803358574ffde5f809b0b09e93f08e4c5e7500ef4dbae8174225b5c`，通过既有API运行只读研究策略，保存run `strategy_experiment_f9ee0408e365542054c79f32345e26ee4aa2de75096004da4ab1eb8cf51e96d5`；覆盖PROSPECTIVE_CAPTURED，但角色MANUAL_OR_REPLAY，非official。其eligible/evaluated/FOCUS/WATCH/REJECTED/UNKNOWN均0，signal_data_quality=SIGNAL_DATA_UNAVAILABLE：缺日期化资格和完整母体证据，原策略EMPTY字样不被计为合法no-focus。

后验作业SUCCEEDED，实际observations=0，并保存score `recommendation_score_a034804af869480e44adfb7a9328563ed408b8e8e28d3f402375a3d85f212676`；evidence_type=PRELIMINARY_RESEARCH，headline_status=N_A_SIGNAL_DATA_UNAVAILABLE，Score=N/A，实际研究cutoff=2026-09-08 17:15:57.056044 Asia/Shanghai。此成绩不进入正式曲线，正式信号数及四项primary统计仍全为0。最新实际primary仍不存在；服务已登记20:30计划且线程存活。

最后20日replay基于续采后的真实缓存重新尝试，仍1个诊断构造、0可评价；长期RAW非空会话26→67/255。当前master/RAW仍为零行，缺口不以0收益或no-selection替代。MA20/60/120/250均0 observed / 0合格评价分母、比例null（UNKNOWN）；两份独立数据报告保存逐日source-contract blocker。

Scorecard文件保存于本机reports/codex/2026-09-08/r12_integrity_history/recommendation-score/：DAILY_RECOMMENDATION_SCORECARD.svg、DAILY_SCORE_TREND.svg、FOCUS_VS_WATCH.svg、SETUP_SCORE_HEATMAP.svg、SCORE_COVERAGE.svg。浏览器最终核验显示N_A_SIGNAL_DATA_UNAVAILABLE、PRELIMINARY_RESEARCH与实际cutoff，Focus/Watch不可计算比较显示N/A；没有将数据缺失显示成NO FOCUS SIGNAL。

### 最终测试

| Suite | Passed | Failed | Errors | Skipped | 秒 |
|---|---:|---:|---:|---:|---:|
| scoring | 67 | 0 | 0 | 0 | 1.684 |
| stage-aware | 44 | 0 | 0 | 0 | 14.888 |
| outcome-tracking | 93 | 0 | 0 | 0 | 46.361 |
| scheduler | 47 | 0 | 0 | 0 | 66.716 |
| pipeline-queue | 47 | 0 | 0 | 0 | 26.672 |
| pit-source | 70 | 0 | 0 | 0 | 2.619 |
| api-ui | 44 | 0 | 0 | 0 | 4.834 |
| full-core | 970 | 0 | 0 | 1 | 220.402 |
| full-repository | 0 | 0 | 1 | 0 | 3.532 |

可复现命令为 `stock-fish/.venv/Scripts/python.exe stock-fish/scripts/r12_validate.py --output <new-directory>`；它逐组记录实际cwd、完整pytest command、JUnit计数及进程耗时。本机 TEST_RESULTS.md / test-results-consolidated.json 保存每组完整命令，附于Review Bundle运行证据。970项核心通过，唯一skip为Windows普通用户符号链接权限；全仓收集的1个error是可选MiroFish缺openai，未与核心失败混写。最后JS小型N/A呈现修正已通过node --check、44项API/UI回归和浏览器视觉复核；未再次修改策略源码或epoch。

本轮按既有.gitattributes将触及的Python文件统一为LF，避免Windows工作区CRLF与git show最终字节导致不同source hash；因此部分diff包含换行规范化。提交前逐一核对19个改动的核心/配置文件staged bytes等于当前运行文件bytes，epoch保持不变。审核业务差异可使用git diff --ignore-space-at-eol。
