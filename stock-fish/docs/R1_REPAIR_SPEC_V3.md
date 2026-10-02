# R1 评价合同 V3：G01—G05

本修订改变执行证据有效性、账户评价窗口、推断资格、资源能力和背景缺口说明，不改变 V2/R1 选择规则、十二组消融或默认参数。参数尚未验证，MARKET_EDGE=NOT_ESTABLISHED，production_promotion=DISABLED。旧 V2 规格、manifest、run 和报告保留旧口径，不重写。

## 行情用途合同

`R1_QUOTE_EVALUATION_V3` 对适配后实际消费的字典再次校验。INVALID 一律拒绝；VALID 声明不能代替数值检查。PARTIAL 或未声明质量仅可提供经过校验的字段级证据（VALIDATED_OPEN_ONLY / CLOSE_ONLY / BUDGET_ONLY），绝不升级成完整日线。DAILY 还要求全部 OHLC；没有补齐缺失字段。所有提供的原始 OHLC 必须有限、正值、区间一致；金额和股数有限非负，布尔值及非数值类型拒绝。RAW_CNY 与明确 TEST_FIXTURE_NO_ACTION 分开，未知/冲突基准或非 CNY 单位拒绝。可比复权字段不用于原始执行，不与原始价格混比。

归档继续要求字节 hash、SourceReceipt、证券/日期、可见时点、规则、限价、无行动完整覆盖。解析后的数值用于成交；预算和净值独立检查相应字段。后验矛盾限制评价，不改变下单前预算、数量、信号快照或候选。入场不明保留最大资本承诺，退出不明保留持仓；缺标记不填零、不沿用旧标记。任何必要净值缺口限制完整账户收益。

## 冻结窗口和推断

`R1_EXPERIMENT_MANIFEST_V3` 保存 signal_window、planned_completion_end、account_evaluation_window、evidence_as_of、日历 hash 和尾部政策。主窗口由首信号日到末信号日 + 有效 exit_offset 的交易所会话。可在执行配置 JSON 中预登记 account_evaluation_end，必须是日历内且不早于计划终点；在读取收益前冻结，各组和滑点共享。尾部只管理已有持仓，不要求新信号。信号窗口缺日报阻止完整比较；原 session_index 不重新编号。

执行引擎在统一终点停止；截止未到尾部、日历不足、缺价格或净值均不能生成未来完整收益。统计独立显式裁取冻结窗口，并保存实际曲线区间、expected/observed 会话数、首尾、尾部数、缺日报/净值和未了结交易。账户累计收益/回撤与日收益采用同一窗口；信号持有期收益另列。合法现金日为零；缺输入与未知行情不是现金零收益。

`R1_PAIRED_RESULT_STATISTICS_V3` 从账户实际交易或信号目标实际成员计算会话延误。达到冻结块长即拒绝推断；未了结、延误字段未知或冲突亦拒绝。诊断包含 trade_id、阈值和原因，资格在估计器前检查；描述性已知子集保留，Holm 族不缩小。默认种子20261001、20会话块、2000次、至少两个完整块、全部预登记估计目标均不变。

结果使用 R1_SHARED_RESULT_V3，旧 V2/V3 saved summarize 都校验原 manifest、completion 和共享对象，只读消费保存统计；不按新代码重算旧结果。研究 identity 覆盖本规格与全部 research_r1_* helper。

## 资源能力和背景

资源采样输出字节、PROCESS范围、method、current/peak 和 status。Unix getrusage 为峰值，macOS 字节、其他 Unix KiB转字节；Windows GetProcessMemoryInfo 为当前/峰值 working set。达到采样点仍执行1GiB峰值预算。不可采样标 MEMORY_MEASUREMENT_UNAVAILABLE，采用 ENFORCE_WHEN_AVAILABLE_ELSE_BYTE_TIME_BUDGETS：继续批量、2GiB消费字节和300秒预算，明确不保证硬内存上限。异常路径释放只读事务。原生 Windows 未验证须单独报告。

背景合同 R1_FACT_SELECTION_COVERAGE_V3 忽略空 reason_codes、warnings、counterevidence；依据明确 UNKNOWN/缺数据/查询未完成/必要来源未解决报告实际字段和 gap_type。完整查询且有来源的空 records 合法，但不宣称资金支持。适用性为 false 的节点不构成缺口；来源可继承父节点。聚合 PASS 不机械要求重复 source_refs；NO_RECORDED_GAP 仅代表没有记录到缺口，不代表供应商完整覆盖。背景扫描不参与 CORE 选择。
