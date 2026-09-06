# R05 规格整改与真实历史重建

本次继续 R05。`DAILY_V1` 原五族策略保持冻结，量价、股性和题材使用 reference 原始规格；验收合同没有替代策略参数。历史 V2 实验运行以附加投影标记 `SPEC_NONCONFORMANT` 并从合规复盘排除，原运行和排名保留。当前量价实现为 `R05_STRATEGY_EXPERIMENTS_V4_CANONICAL_IDENTITY`。

量价资格逐证券、逐日期核验；上市时长使用交易会话。六日价格及成交额必须连续、字段完整。A 为 T 成交额／前五会话均额，R 为可比收盘五会话变化，H 为 T 日 `(RAW high−RAW low)／reference_prev_close`。按原门槛 A≥1.2、R>0 筛选，再按精确分数与 midrank 百分位计算 `0.45 PR(A)+0.35 PR(R)+0.20 PR(H)`；同分按证券标识稳定排序。

`PV_FOCUS_PREVIEW_V0.1` 从完整 PV 应用 A≥2、R≥0.10、RAW 收盘≥开盘、收盘位置≥0.70，没有名次配额。DNA 的成熟事件、Wilson、开盘先验、计数窗口与 60% 子集 PV＋40% G，以及题材组内重算、事件覆盖与封板门槛，都由原规格和独立 golden 核验。数据缺失不会产生替代排名。详细公式及官方上游代码/许可溯源见 [规格追踪表](../strategy_spec_traceability.md)。

正常页面提供保存 run 的历史、策略、完整/焦点、30/50/100 分页、单证券详情、CSV/JSON 和复盘。摘要读取独立持久投影；增强策略未形成排名的诊断行不会进入名单导出。板事件未知与风险未复核保持可见。无账户、Qlib 模型或 Tushare Token 也可以使用量价研究。

后验将价格、开盘、极值界和板事件分开判定。标签版本和指标政策显式登记，逻辑主键含 run、参数/实现、证券、h 和政策；同 run 重算只追加修订，按 cutoff 选最新有效修订。价格零变化计入有效分母但不算上涨。正式周期只覆盖焦点，不把普通重复推荐虚构成新周期；MA20、归档、重新进入、退出后 5/10 会话影子观察及 5/20/60 会话复盘分别保留。

实际历史重建采用 BaoStock 日期化记录与原始回执。现在采集的资料没有冒称当时存档；原采集时钟保留，通过有来源文件 hash 的明确历史证明消费。上海分类证据仅覆盖 2026-08-31 起的已认证范围；更早尝试只认证深圳范围。未知资格不默认合格，因此整体是 `LIMITED_SCOPE`。

在业务目录运行：

```powershell
.venv/Scripts/python.exe scripts/r05_capture_replay_inputs.py --output <新私有采集目录> --phase identity
.venv/Scripts/python.exe scripts/r05_capture_replay_inputs.py --output <同一采集目录> --phase daily
.venv/Scripts/python.exe scripts/r05_capture_replay_inputs.py --output <同一采集目录> --phase factors
.venv/Scripts/python.exe scripts/r05_weekly_replay.py --supply <同一采集目录> --sources <已核验官方资料目录> --output <新回放目录> --phase all
.venv/Scripts/python.exe scripts/r05_write_replay_report.py --replay <回放目录>
```

首次计划在结果计算前冻结日期、源码、h、共同资格池、单因子诊断和执行假设。已有信号计划和文件不覆盖；单独的后验执行计划绑定机械性能及标签合同修复后的实际源码。20 个观察日每个固定一个 run，主窗口为最新 5 个已结束交易日；成熟辅助窗口单列，不能替代主窗口。实际名单、价格标签、成熟度、同条件对照和模拟尝试以随附中文报告及其文件为准。

模拟采用 `NEXT_OPEN_HOLD_H_PAPER_V1`：T+1 开盘尝试买入，以尝试日 E 起到 E+h 收盘计划退出，h≥1；买卖合计 0/30/60bp 纯研究情景，30bp 为主。未核验的历史限价不能证明成交，必须保留 `FILL_UNVERIFIED`。价格上涨率与模拟交易胜率分开，受限持仓不能从风险记录删除。

队列按真实 HTTP、分页、重试、缓存和 SDK 操作分别记账；预算先扣后发，checkpoint 支持进程被终止后的成功子项恢复。Tushare 缺 Token 的真实状态为 `AUTH_REQUIRED`，并不阻断独立量价来源。参见 [数据与 Docker 核验](R05_DATA_AND_DOCKER_RECTIFICATION.md)、[审阅包生成](R05_REVIEW_BUNDLE.md)。

本机正式报告位于 `../review/r05-rectification/replay/过去一周实证回放与胜率口径报告.md`，综合验收位于 `../review/r05-rectification/统一整改验收报告.md`。公开源码不提交这些完整市场派生材料、原始缓存、私有数据库、环境文件或凭据；统一审阅包提供允许分享的派生结果及原始输入路径/hash，不声称包含全市场数据的完全自含包。

工程测试、真实供给、Docker 与历史价格观察分别验收。五个日期和重复证券的重叠标签不足以建立投资优势，`MARKET_EDGE=NOT_ESTABLISHED`；参数没有根据本次结果调整。

## 最终读取性能与运行来源

完整回放已使用冻结 V4 实际运行全部 20 日。最后实测 308,714 条后验记录时发现详情整库遍历超时，交付只在 `candidate_service.py` 增加按 run/证券的 SQLite 派生索引和完整性校验读取；4 个真实详情请求为 0.26—0.47 秒，后验与保存的 canonical 全字段相同。选择、后验、episode、模拟执行及统计算法均未改。`final-delivery-source-lineage.json` 逐函数 AST 和 60 文件 hash 区分回放源码与最终服务源码，回放时完整源码另保留在审阅包 provenance 中，不能把新服务文件字节声称为旧回放时字节。

冻结原 DAILY YAML 及 6 个运行文件有原始 CRLF 字节；局部 `.gitattributes` 明确保留这些文件，提交时不改写其内容 hash。其余普通文件同时记录原字节和 LF 规范化对应。独立审计包含主五日全部 15,192 条资格特征和全部 20 日 303,674 条规范价格标签，收益计算与执行可行性分别判断。
