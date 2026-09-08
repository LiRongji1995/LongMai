# V2 Prospective Validation Protocol — R12

本协议保留 V2 交易语义、参数、Setup 与优先级。`MARKET_EDGE=NOT_ESTABLISHED`。

- strategy_id: `STAGE_AWARE_MOMENTUM_V2`
- implementation_version: `R12_STAGE_AWARE_MOMENTUM_V2_INTEGRITY_HISTORY`
- spec_revision: `R12_INTEGRITY_HISTORY`
- protocol_id: `SCHEDULED_TRIGGER_20_30_ACTUAL_DECISION_CUTOFF`
- primary_cutoff_policy_id: `SCHEDULED_2030_ACTUAL_COMPLETION`
- scheduled_at: 交易会话当日 `20:30 Asia/Shanghai`
- checkpoints: `[5, 20, 60, 120]` 个可评价正式信号日

当前 provider 在触发后继续采集，不能保证 20:30 前取得核心证据。因此本协议采用真实完成时点；例如 20:38 完成的 primary，decision_cutoff 与 actual_decision_cutoff 均为 20:38。每次保存 scheduled_at、capture_started_at、capture_completed_at、actual_decision_cutoff、启动延迟、采集耗时和总延迟。证据仍通过 event_time/source_published_at/first_seen_at 与来源合同核验，20:38 后可见的资料不能进入该信号。未来切换 EXACT_2030 必须生成新 epoch。

## Epoch 与成绩身份

`validation_epoch_id = validation_epoch_ + digest(identity)`。identity 包含 V2 implementation/spec/config/source hash、上游共同资格与可见性实现身份、validation protocol hash（包括 cutoff policy 与协议源码）、score spec identity（包括 scorer 源码）。任何这些字节或身份改变都会产生不同 epoch；日行情 artifact hash 绑定单日信号证据，不因正常每日增量而创建新 epoch。

已保存 epoch、signal 与 score revision 不可变。默认 API、20D/60D 曲线、调度统计只读当前 epoch；显式 validation_epoch_id 可只读查看旧 epoch。旧 scorer 源码已不可用时，不用新 scorer 重算旧成绩。GET /api/candidate-schedule 返回完整当前身份与运行状态，GET /api/recommendation-scores 默认按当前 epoch 服务端过滤。

## Capture 与可计数信号

`official_primary_capture` 仅表示真实晚间调度产生并保存的 PROSPECTIVE_CAPTURED primary。`official_validation_signal_countable` 另外要求日期化资格范围、实际完成策略评价、可核验当前价格及核心条件、无致命身份或来源失败、输入完整性验证和成功持久化。旧兼容字段 official_primary_signal_countable 与后者一致。

theme/funds 等 context UNKNOWN 不自动否决核心已知的评价；核心 price/trend/source UNKNOWN 仍是不可评价。合法硬条件 FAIL 是已评价结果。eligible=0 只有完整已知的全母体排除证据才能成为合法空集；来源失败造成的空集为 N_A_SIGNAL_DATA_UNAVAILABLE，不得包装成无推荐。

统计独立保存 primary_capture_attempted_days、evaluable_official_signal_days、data_unavailable_primary_days、valid_no_focus_days。失败或中断的真实调度尝试计 operational/data-quality；不可评价日期不进入 score rolling 或 focus coverage 分母。历史重建身份优先，错误 official flag 不能使其进入 prospective。

## 真实服务与截止后核验

Windows 使用仓库 Start-StockFish.ps1 启动单进程 Waitress，端口、既有 DB、state root 写入忽略的 work/stockfish-service.json。进程登记保存 PID、创建时间、根目录及 scheduler thread identity；OS DB 锁阻止另一个 worker/scheduler 写同一 DB。正常停机使用 Stop-StockFish.ps1。

2026-09-08 实际服务证据与 epoch 见 [R12_PROSPECTIVE_INTEGRITY_REPORT.md](R12_PROSPECTIVE_INTEGRITY_REPORT.md)。尚未到 cutoff 时只报告真实计划与存活线程；不补造或提前宣布正式信号。截止后只读核验入口：

```powershell
& ./stock-fish/.venv/Scripts/python.exe ./stock-fish/scripts/r12_post_cutoff_check.py
```

输出包含实际晚间 event/job、保存的 input/run 身份与质量、capture/countable、epoch、cutoff 和成绩状态。服务关闭期间不继续采集；错过窗口不倒填正式 primary。后验只追加 revision，历史重建与正式前瞻始终分别统计。
