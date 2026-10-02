# 语义迁移与未解决事项

基线：`2e256883f1103eecb0014161c3de54b181890124`，起始分支 review，起始 dirty 为空；远端 main 经只读 ls-remote 核验相同。实现与验证完成后，用户另行明确授权提交推送源码；本次不部署，不修改旧 Python 行为。旧 go-core 影子模块保持不动。

| 原规则/事实 | 原源码/配置（stock-fish/ 下） | Go实现 | 检查/状态 |
|---|---|---|---|
| Fraction/Decimal、阈值、显示舍入后再次判断 | yaogu/strategy_experiments.py `_number`；research_r1.py `compare` | core/number.go | exact/half-even 单测；任意极端50位Decimal中间精度尚未全等价 |
| 完整/未知/失败/短路 | research_r1.py `all_of/any_of/adapt_row` | core/state.go | 四形态、多命中、顺序、短路单测；NOT_IMPLEMENTED 独立 |
| 四时钟、父来源、重建、范围 | yaogu/visibility.py `EvidenceResolver`；contracts.py | core/evidence.go | 范围/父来源/PARTIAL/重建/冲突测试；完整模型校验未完成 |
| 历史快照、manifest、分区及payload | yaogu/history_artifacts.py；storage.py | core/history.go | 只读事务、原字节哈希、索引完整性、损坏拒绝、关闭后可写fixture |
| 唯一日期化主板资格、60会话 | yaogu/strategy_experiments.py `_qualification/_prepare_context`；config/r05_strategy_experiments.yaml | core/research.go | fixture资格/未知ST、同原始输入差分 |
| 可比收益、MA、金额换手窗口 | yaogu/theme_capital_trend.py；stage_aware_momentum.py | core/facts.go | 无零填补；缺会话/基准错配；同输入精确收益和MA差分 |
| 完整资格池市场基准、等长3+3动量 | stage_aware_momentum.py `_market_return_at/_strength_momentum` | core/facts.go/research.go | 未成交额预筛基准；same raw fixture差分 |
| 题材RS用于HLR/回踩 | stage_aware_momentum.py `_sector_window_stats/_sector_rs_exact` | core/sector.go | 同题材、排自身、资格交集、完整性；不是完整sector背景 |
| START及旧三形态 | stage_aware_momentum.py `_stage_start/_stage_high_level_repair/_stage_trend_pullback`；config/stage_aware_momentum_v2.yaml | core/rules.go | 每种PASS/FAIL/UNKNOWN；5种原始fixture逐股状态差分 |
| R1加速替换、MA20简化、非跌停 | research_r1.py `acceleration/ma20_trend/adapt_row` | core/rules.go/state.go | 加速使用精确RS，旧形态使用原舍入RS；公共未知跌停保留 |
| 原始限价/行为/反证 | events.py；stage_aware_momentum.py `_daily_path/_behavioral_confirmation/_critical_counterevidence` | core/facts.go/rules.go/state.go | tick/价格范围/跌停三态、HLR行为、回踩长趋势 |
| 完整市场情境、sector标签、资金链、供应压力 | stage_aware_momentum.py 829–1788、2170–2223 | **尚未完成** | 运行整体为INCOMPLETE_IMPLEMENTATION，不能用常量UNKNOWN声称迁移 |
| coverage/evaluability与正式结果DAG | research_r1_contracts.py；research_r1_storage.py | **仅诊断版结果** core/report.go | 候选UNAVAILABLE，只有diagnostic-completion，不冒用旧source_hash |
| 总预算/取消/RSS | research_r1_storage.py `BudgetHistory`；用户本轮约束 | core/budget.go/rss_* | SQL实际取消、纯MA计算取消、字节/输出上限；标准库JSON整值编码的取消粒度仍有限 |

原路径 `yaogu/` 表示 `stockfish_core/yaogu/`。函数参考与上述基线绑定，配置原字节漂移会被CLI拒绝。

## 最小剩余项（按优先级）

1. 补齐完整背景计算与同题材资金链输出，及正式coverage/逐条reason/evidence引用合同；保持原R1硬条件不变。
2. 补齐Python输入模型默认值、类型约束、UTC日期规范化和数值canonical跨语言向量。目前原字节payload哈希已验证，不冒称Go重编码就是旧模型哈希。
3. 扩大差分至全部背景、全部关键原因、全部选用来源（当前检查资格、收益/RS/MA、公共/四形态状态、全部命中集合及fixture逐股选用原始证据哈希）。不能把当前状态相等称为完整等价。
4. JSON解析/编码进一步按记录流式处理并支持中途取消；正式研究completion验证（诊断逐股有界压缩NDJSON及引用验证已实现）；Windows原生RSS/取消/发布测试。
5. 上述全部完成后，冻结新代码身份并在剩余授权次数内完成同输入完整运行；诊断时间不作整流程提速依据。

未设置持久缓存，不存在跨cutoff缓存复用或磁盘淘汰语义。当前仅存本次输入内的公共事实；未来加缓存必须补身份/容量/损坏/淘汰测试。

## 本轮自审补充

- 真实第一次运行发现历史范围偏差后已取消；最终代码恢复315会话，对无history artifact的输入保留全部已有会话，并将fixture选用bar哈希加入差分，不靠删字段忽略该问题。
- 当前HLR百分位查询仍逐股遍历总体（O(N²)），并未宣称所有算法热点已优化；全池阶段计时应指导后续局部改进。
- 标准库JSON单个值的解析/编码内部不能被context中途抢占；已按股票拆分有界明细并在记录/读写块间检查，但完整输入大值仍是取消粒度限制。
- failure目前保存源码身份、预算、阶段与指标；未保证在所有早期失败场景都有完整输入/历史身份，强杀更不保证写出failure。
- RSS与Go heap分开采样；外部内存分项未独立测得，属于UNAVAILABLE，不能把RSS减Go heap当作精确外部内存。
