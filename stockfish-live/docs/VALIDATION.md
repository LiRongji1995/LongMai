# Phase 0 / Phase 1 验证记录与后续评价合同

日期：2026-10-07。当前交付为独立分钟回放基础层，不是完整 V1。所有本轮数据验证使用显式 TEST_FIXTURE，真实分钟、实时来源和投资有效性分别保持未验证。

## 本轮实际环境与检查

独立 Python 3.12.14，Pydantic 2.13.5、PyArrow 23.0.1、DuckDB 1.5.6、pytest 9.1.1。只在 `stockfish-live/.venv` 安装新依赖，没有升级或修复 Research 环境。旧 Research venv 的 Pydantic 二进制导入失败已记录在审计中，不用元数据版本替代实际可运行性证据。

以下路径相对仓库根目录，除单独说明外退出码均为 0：

| 实际命令 / 检查 | 结果 |
|---|---|
| `stock-fish/.venv/bin/python -m venv stockfish-live/.venv` | 创建独立 Python 3.12 环境；不变更旧 venv |
| `stockfish-live/.venv/bin/python -m pip install -e './stockfish-live[test]'` | 安装成功；实际版本登记为独立锁文件 |
| `stockfish-live/.venv/bin/python -m pip check` | No broken requirements found |
| `stockfish-live/.venv/bin/python -m compileall -q stockfish-live/stockfish_live` | 通过 |
| `stockfish-live/.venv/bin/python -m pytest -q stockfish-live/tests` | 最终 159 passed in 17.87s，退出 0；含真实双日范围回放与中途失败保留证据 |
| `git diff --check` | 通过；最终暂存后再核对本轮文件范围 |

测试覆盖：有限日历/时区/分钟结束语义、显式单位和部分 OHLC 矛盾、未知不补零、父回执与真实收件、09:45 无法请求09:46、逐项未来时钟隐藏、归档篡改、单写者、路径 symlink 拒绝、发布中断重试、不可覆盖、乱序/重复/跨日、缺09:38不伪造、timeout/SDK错误/malformed返回后恢复、provider质量传播、PIT等级不升级、完整日确定性、scope/RAW/类别与 CLI 错误。

双日范围集成使用真实 CLI/Parquet/DuckDB/ReplayEngine：各日分别 242 snapshots，新日开盘 bar_count=0/cutoff=None，不串用前日输入；缺价保留 GAPPED。第二日缺档案时退出 2，保留第一日完整快照并在范围根追加 `FAILED=REPLAY_RANGE_INCOMPLETE`，不声称范围完成。

开发中保留的失败及修复：

- 全套首轮 `78 passed, 1 failed`、随后 `137 passed, 1 failed`（均退出 1）：加强 `bar.timestamp <= parent.received_at` 后，原 storage 冲突测试的人工父时钟不合法。改为合法收件时间继续测试相同的 `SOURCE_RECEIPT_CONFLICT` 断言，没有放宽时序约束。
- 定向 storage/replay 回归 `42 passed, 1 failed`（退出 1）：INVALID provider snapshot 的来源警告在拒绝前丢失。修正传播顺序，保留失败断言；随后全套 `148 passed` 和扩展后的 `157 passed`（退出 0）。

## 实际 CLI 完整日烟测

所有产物保留在被 Git 忽略的 `work/stockfish-live-phase1-20261007/`，不发布原始数据或完整本机包。

```sh
stockfish-live/.venv/bin/stockfish-live fixture --date 2025-06-18 \
  --data-root work/stockfish-live-phase1-20261007/fixture
stockfish-live/.venv/bin/stockfish-live doctor \
  --data-root work/stockfish-live-phase1-20261007/fixture
stockfish-live/.venv/bin/stockfish-live replay --date 2025-06-18 --speed max \
  --data-root work/stockfish-live-phase1-20261007/fixture \
  --output work/stockfish-live-phase1-20261007/replay-a
stockfish-live/.venv/bin/stockfish-live replay --date 2025-06-18 --speed max \
  --data-root work/stockfish-live-phase1-20261007/fixture \
  --output work/stockfish-live-phase1-20261007/replay-b
stockfish-live/.venv/bin/stockfish-live live \
  --data-root work/stockfish-live-phase1-20261007/fixture
```

前四条退出 0；live 预期退出 **2**，错误为 `LIVE_PROVIDER_UNAVAILABLE`。doctor 实测 1,920 archived rows / 1,920 strict visible rows、gap=0，明确 test_fixture_only=true、research_ready=false、model=NOT_IMPLEMENTED_PHASE1。

两次 replay 各保存 242 个快照，最终 1,920 bars、gap=0、quality=VALID、failed_cycles=0。开盘尚无完整 bar，汇总保留初始 DATA_PARTIAL。09:45 快照累计 15×8=120 bars，收到的记录 event/available 均不晚于 as_of。所有 242 对快照逐字节一致，序列 hash 均为：

```text
3d425c105fa9b5085a3e63c3bad8b83a20bbf9736906bd5299237db34701eacc
```

原始命令、退出码、stdout/stderr 和 wall time 在私有 `smoke-results.json`；确定性比对脚本断言退出 0。不把 fixture 日期当作官方交易日证明。

## 工程延迟观测

8 个测试身份、1 个合成会话、240 分片；内部计时包括规范化合同校验、内存状态、完整输入 hash 和快照落盘，不包括 provider 调用。归档校验/加载在开盘前单列。

| 测量 | replay-a | replay-b |
|---|---:|---:|
| 进程总 wall time | 17.421s | 20.097s |
| 归档校验/加载 | 6.840s | 6.949s |
| 内部含持久化 P50 | 33.771ms | 38.619ms |
| 内部含持久化 P95 | 61.770ms | 86.100ms |
| 内部含持久化最大值 | 88.659ms | 148.382ms |

这些是实测基础层结果，没有 feature/forecast/dashboard push；测试期间存在其他工程检查，不能据此外推真实生产 SLA。provider 读取来自本地归档，合成 event->available 为零，不代表真实 provider 零延迟。全日 fixture 构造采取反复核验既有分片的保守路径，未测量其 wall time，不能用 replay 每 tick 延迟代表导入吞吐。

当前 DuckDB 全日核验再 Arrow 注册，Parquet append 每批重新核验该日已发布分片；状态也重新计算完整内容 hash。大规模导入/全市场 replay 的 I/O 与复杂度仍需 profiling。没有通过跳过完整性检查宣称性能达标。

## Phase 5 正式评价门槛（未实现、未运行）

采用 expanding / walk-forward，仅从过去训练到未来测试，标准化和 K 选择只发生在训练/内层验证窗；禁止随机 train/test split。按实际覆盖动态选择年度或滚动窗，至少覆盖09:45、10:00、10:30、13:30决策时点，严格区分未知、缺失、未成熟和零收益。

必须对照 Zero Return、Current Trend Continuation、Historical Time-of-Day Average、Previous Day 和 empirical/random prior。报告 return MAE/RMSE/direction accuracy/Spearman，概率 Brier/log loss/calibration，quantile pinball/50%与80% interval coverage，regime confusion matrix/transition accuracy/calibration。随机 prior 固定种子并登记概率分布。

历史样本与未来结果必须有完整 lineage、真实标签成熟时间和配置身份；评价 append-only，不覆盖预测。复杂模型没有稳定增量改善时明确 `NO_EVIDENCE_OF_INCREMENTAL_EDGE`。当前上述指标全部 **NOT_EVALUATED**，不填人工数值。

## 未执行范围

真实分钟历史核验、真实 live provider、商业来源权限、全市场/行业 PIT 成员、特征和模型、预测分位、API/WebSocket/Dashboard、forecast revision、正式 OOS 评价、长期前瞻研究、Windows 原生、8核/32GB容量验收、服务部署切换、现有 Research 全套测试均未执行。Research 仅有入口文档改动，生产源码/策略/依赖/数据库未修改；本轮工程测试不代表其旧环境已恢复。

`MARKET_EDGE=NOT_ESTABLISHED`，无券商连接、无下单、无账户与仓位管理。下一阶段见[实施计划](IMPLEMENTATION_PLAN.md)。
