# 技术资料索引

新增[共享结果复用、新输出压缩与复权尾窗V2](SHARED_RESULT_COMPACT_INCREMENTAL_V2.md)：统一聊天入口的完整归档登记／复用、显式新输出压缩和复权只读／诊断合同。旧来源闭包与策略不改，尾窗不自动晋级为完整因子；实际测量和未验范围在该文独立记录。

本轮优先查看[日期化输入与真实数值H1](DATED_IDENTITY_NUMERIC_H1_20261024.md)和[当前状态](../../CURRENT_STATUS.md)：15→16的全池3,041／观察25已独立复算，单／双run闭环、幂等与三产品回查通过；19日及后续身份未知保持，默认300秒性能尚未达标，生产未切换。[前轮继续交付](CONTINUATION_DELIVERY_20261023.md)与[性能原记录](REAL_DAILY_ACCEPTANCE_PERFORMANCE_V1.md)中的全部UNKNOWN与42文件保留为历史事实。

三份来源/执行协议各有独立语义：[同路径字节恢复](SEALED_RESULT_BYTES_RESTORED_V1.md)、[不同路径本地复制](SEALED_RESULT_LOCAL_COPY_V1.md)、[迟到完成会话](LATE_COMPLETED_SESSION_CLOSURE_V1.md)。协议与测试准备不代替真实验收，历史记录按各自当时状态保留。

## 当前参考

### 执行与维护

- [独立环境与 R2 闭环交付](RUNTIME_R2_CLOSURE_DELIVERY_V1.md)：归档鉴证、服务库衔接、每日后验的操作、验收及剩余缺口。
- [历史闭环部分验收（2026-10-20）](CLOSED_SESSION_PARTIAL_ACCEPTANCE_20261020.md)：16文件/676项工程检查；同源码隔离克隆的历史单 run 显式重试、幂等、三产品回读和独立 H1 已通过，原正式失败保持。双 run 两次超300秒、无新增；10月20日重采缺原始首见钟，未产新 run。
- [10月21日盘前限额重试事实](PREMARKET_BOUNDED_RETRY_20261021.md)：独立bundle真实调用退出4；3个官方目录COMPLETE，读取超本次额度60,837字节后停止、capture余额0，无新run/名单/后验，factor分支未覆盖。归档字节校验不把失败改为成功。
- [盘前因子 checkpoint 与独立源码 bundle V2](PREMARKET_FACTOR_CHECKPOINT_SOURCE_BUNDLE_V2.md)：显式构建/验证合同及既有32项工程检查；已用于上述限额尝试，不补旧钟或替换原 producer 路径，真实factor修复仍未验收。
- [当日可信输入、每日验收与性能原记录](REAL_DAILY_ACCEPTANCE_PERFORMANCE_V1.md)：保留10月16日链路的采集、导入、失败与275项工程记录；后续历史重放结论另见上项，不改写原失败。
- [独立 daily/review 环境](ISOLATED_DAILY_RUNTIME_V1.md)：新目录、官方工件哈希锁、原生平台范围和实际运行检查。

- [独立终端盘前研究V1](STANDALONE_PREMARKET_WORKFLOW_V1.md)：单命令采集、原R2、67会话题材、报道和3亿元展示；不需要Codex或Web服务。

- [CODEX_DAILY_RESEARCH.md](CODEX_DAILY_RESEARCH.md)
- [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md)
- [REPRODUCE.md](REPRODUCE.md)

### 策略、解释与后验

- [STAGE_AWARE_MOMENTUM_V2.md](STAGE_AWARE_MOMENTUM_V2.md)
- [STAGE_AWARE_RESEARCH_R1.md](STAGE_AWARE_RESEARCH_R1.md)
- [R1_REPAIR_SPEC_V3.md](R1_REPAIR_SPEC_V3.md)
- [R1_REPAIR_SPEC_V2.md](R1_REPAIR_SPEC_V2.md)
- [R1_RUNTIME_RESEARCH.md](R1_RUNTIME_RESEARCH.md)
- [THEME_CAPITAL_TREND_V1.md](THEME_CAPITAL_TREND_V1.md)
- [DAILY_RECOMMENDATION_SCORE_V1.md](DAILY_RECOMMENDATION_SCORE_V1.md)
- [PHASE1_DAILY_RESEARCH_LOOP.md](PHASE1_DAILY_RESEARCH_LOOP.md)
- [PHASE1_OBSERVATION_MONITOR.md](PHASE1_OBSERVATION_MONITOR.md)
- [STAGE_AWARE_MOMENTUM_V1.md](STAGE_AWARE_MOMENTUM_V1.md)

### 来源、缓存及兼容合同

- [DAILY_PRIMARY_CRITICAL_CAPTURE_BOUNDARY_V1.md](DAILY_PRIMARY_CRITICAL_CAPTURE_BOUNDARY_V1.md)
- [DAILY_PRIMARY_FAST_DECISION_V1.md](DAILY_PRIMARY_FAST_DECISION_V1.md)
- [R12_HISTORY_ARTIFACT_REPORT.md](R12_HISTORY_ARTIFACT_REPORT.md)
- [R14_DAILY_FAST_PATH.md](R14_DAILY_FAST_PATH.md)
- [R16_R1_ENGINEERING.md](R16_R1_ENGINEERING.md)
- [R16_R1_PRODUCTION_ORACLE.md](R16_R1_PRODUCTION_ORACLE.md)
- [R16_R1_CORRECTED_HISTORY.md](R16_R1_CORRECTED_HISTORY.md)
- [R16_R1_LIVE_INCREMENTAL.md](R16_R1_LIVE_INCREMENTAL.md)
- [R05_DATA_AND_DOCKER_RECTIFICATION.md](R05_DATA_AND_DOCKER_RECTIFICATION.md)
- [R05_RECTIFICATION_AND_REPLAY.md](R05_RECTIFICATION_AND_REPLAY.md)
- [V2_PROSPECTIVE_VALIDATION_PROTOCOL.md](V2_PROSPECTIVE_VALIDATION_PROTOCOL.md)
- [architecture/ADR_GO_CORE_BOUNDARY.md](architecture/ADR_GO_CORE_BOUNDARY.md)
- [architecture/FEATURE_EVIDENCE_CONTRACT_V1.md](architecture/FEATURE_EVIDENCE_CONTRACT_V1.md)

版本号较旧的冻结规则和兼容合同仍用于解释既有结果；其中的历史测试数字仅在对应日期成立。R1规格文件属于实现身份的一部分，本次保持原路径和原字节。Go资料见[Go入口](../../stock-fish-go/README.md)，五族规格见[STRATEGY_SPEC_V1](../STRATEGY_SPEC_V1.md)，数据口径见[DATA_CONTRACT](../stockfish_core/DATA_CONTRACT.md)。

## 历史归档

[归档索引](archive/README.md)按原路径列出旧报告和整理前完整手册，保存原数字、失败与限制。阅读当前状态不要从旧报告推断最新数据或本机进程。
