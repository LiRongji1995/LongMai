# 独立审阅复现索引

主报告：[StockRecommendation阶段感知Astra独立审阅](../StockRecommendation_STAGE_AWARE_Astra_Independent_Review_20260907.md)。审阅基线为`main@a9c6deec75319ce0fb2d6fabca0a4c6021323c05`；不修改正式策略，不自动修复发现的问题。

## 文件与证据等级

| 文件 | 内容与等级 |
|---|---|
| [case_recompute.py](case_recompute.py) / [case_evidence.json](case_evidence.json) | ACTUAL_EXECUTION / HISTORICAL_REPLAY：冻结晚取输入复算两例、525行PV、17焦点及较早Top20；输入前后hash未变 |
| [semantic_findings.md](semantic_findings.md) / [semantic_counterexamples.py](semantic_counterexamples.py) / [semantic_counterexamples.json](semantic_counterexamples.json) | STATIC_REVIEW / SYNTHETIC_COUNTEREXAMPLE：阶段语义与14组断言；断言确认实际行为，其中包括待修错误，不是产品验收通过 |
| [pit_findings.md](pit_findings.md) / [pit_probes.py](pit_probes.py) / [pit_probe_results.json](pit_probe_results.json) | STATIC_REVIEW / SYNTHETIC_COUNTEREXAMPLE：资金时点/来源/去重、主题拼接、版本碰撞、后验混样；观测脚本无断言，退出0只代表执行记录成功，须核对JSON中的现象 |
| [evaluation_findings.md](evaluation_findings.md) / [evaluation_recompute.py](evaluation_recompute.py) / [evaluation_actual_summary.json](evaluation_actual_summary.json) | ACTUAL_EXECUTION：全实验重算、全池原因组合、10单条件ablation、共同池及成熟分母。不是前瞻胜率验证 |
| [external_sources.md](external_sources.md) | EXTERNAL_SOURCE：官方规则和模型说明、案例盘后报道；未写回正式输入 |
| [verification_summary.json](verification_summary.json) | 发布前范围、轻量文件hash、语法/链接/数值/敏感性与Git核验记录 |

所有生成的合成证券/价格明确为TEST_FIXTURE。日期标签本身不证明真实历史；两只真实个案也未获得完整原始前瞻run。真实输入、完整策略JSON、数据库、pytest临时目录、日志和大缓存不提交。review根被.gitignore整体忽略，本次仅显式纳入本索引中的安全轻量文件及主报告。

## 复现命令

在实际仓库根运行，使用既有项目环境，不安装新依赖：

```powershell
& ./stock-fish/.venv/Scripts/python.exe -B -X utf8 ./review/astra-stage-aware-20260907/case_recompute.py
& ./stock-fish/.venv/Scripts/python.exe -B -X utf8 ./review/astra-stage-aware-20260907/evaluation_recompute.py
& ./stock-fish/.venv/Scripts/python.exe -B -X utf8 ./review/astra-stage-aware-20260907/semantic_counterexamples.py
& ./stock-fish/.venv/Scripts/python.exe -B -X utf8 ./review/astra-stage-aware-20260907/pit_probes.py
```

前两项依赖**本机已存在、未发布**的`stock-fish/reports/codex/pv_observation_20260904_rebuilt_20260907T091708`、`stage_aware_momentum_v1_initial_audit_20260907T111840`资料；缺文件应报告NOT_VERIFIED，不重新采集或伪造。后两项只需当前代码及仓库TEST_FIXTURE，不能补足真实五日历史需求。

脚本会更新本目录自身JSON。`pit_probes.py`还写本目录专用`pit_identity_fixture.sqlite3`以验证同内容幂等和异内容ImmutableConflict；固定当前HEAD串行重复运行安全，内存参数在finally中恢复。后续源码或fixture变化可能触发原隔离库身份冲突，须使用新的审阅证据目录/隔离库，不能为使脚本运行而更改生产不可变存储。此SQLite不属于发布清单。

本轮命令退出码均0。阶段现有测试10 passed；来源/时点/跟踪等相关测试162 passed（与阶段测试有重叠）；评价测试23 passed。精确测试命令、首次临时目录/脚本搭建错误及修正后的执行记录见分项报告；没有用这些工程通过数声称投资有效。

为使等频排除原因组合有稳定展示顺序，主审只将**审阅脚本**中的集合/分组排序固定，并重新执行；这没有修改正式算法、样本、阈值或统计值。

## 未验证项目

最近5个信号日比较、完整9月4日前瞻原run、真实长期MA/未来公司行为增量不变性、官方逐条龙虎榜及PIT主题/席位、未选四格后续涨跌和消融收益、Case Rescue新增群体收益、设计外验证、真正前瞻AI记录及当前HEAD在线部署。只在一个晚取保存信号日上完成程序复算，不把缺失当0或当投资FAIL。

下一步修复须由用户另行决定；本任务不继续开发。
