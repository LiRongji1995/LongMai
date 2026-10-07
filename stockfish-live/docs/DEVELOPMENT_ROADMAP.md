# 持续开发与成熟门槛

2026-10-07 用户明确授权：先自检，通过后继续开发，直到成熟、可用。沿用原阶段顺序，工程闭环、真实数据就绪与统计预测价值分别验收；不以测试 fixture 或界面完整宣称实盘成熟。

## 自检

基线 6eee881 原有159项测试通过；独立复核发现取消回放、summary写入失败未标记FAILED两项缺口。新负例先复现失败，再修复BaseException和汇总发布边界；定向56项通过，退出0。中断时磁盘本身不可写只能保留原始异常，缺summary仍代表未完成。

## 本轮工程路径（现已实现，实测证据见验证记录）

1. Phase 2：明确PIT context合同、指数/宽度/情绪/行业状态、缺口/分母/边际强度；无上下文不猜前收、限价、资格或成员。
2. Phase 3：NN与成熟历史结果，分位horizon、经验概率、置信度、similar states、同目标价格修订；历史标准化仅训练窗。
3. Phase 4：独立FastAPI/WebSocket及离线可用Dashboard，任意历史as_of严格截断实际路径、预测版本与输入；显著标记fixture/重建和未知。
4. Phase 5：expanding walk-forward、naive baselines、校准与coverage，真实数据未就绪时不填模型效果。测试fixture评价与正式投资评价分开。
5. Phase 6：上述回放闭环通过后接入有界实时适配器，实际捕获时钟不回填；无key/来源失败明确UNAVAILABLE。公网来源可用性和单位/聚合依据单独验证。
6. 运行与性能：独立storage、append-only forecast/evaluation、恢复/损坏检测、实际全日重放和局部容量基准，发现热点后优化并登记版本；不跳过正确性检查换取速度。

## 本轮允许修改

`stockfish-live/` 的源码、测试、独立依赖、frontend与文档；根README及项目综述仅同步独立入口/边界。保留现有Research策略/运行/依赖、旧run和用户iCloud变化；不建分支/worktree，不部署切换、不连接券商、不购买服务、不做无界全历史采集。

阶段完成后记录实际命令、退出码、fixture vs真实范围和未验证项，按既有授权中文提交推送main并核验远端。参数、模型、上下文与存储语义变化登记新版本/config hash，不覆写Phase1快照或预测。

`MARKET_EDGE=NOT_ESTABLISHED`、`LIVE_TRADING=DISABLED`、`BROKER_CONNECTION=NONE`。

## 后续成熟门槛

Phase 2—6 的可测试实现已经落地；验收事实统一见 [当前状态](../CURRENT_STATUS.md)及 [V1 验证](VALIDATION_V1.md)。不把实现存在视为真实供应商连接、全市场覆盖、跨平台验收或投资价值成立。下一步必须获得合法真实历史与实时留档，再完成实际 PIT/覆盖、长期 OOS、校准、连续运行和容量验证。缺少真实来源是验证边界，不用合成材料替代。
