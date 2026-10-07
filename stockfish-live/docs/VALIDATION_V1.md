# V1 工程验证记录

日期：2026-10-07，独立包 0.2.0，续开发基线 `6eee881e1edf6674d84309d9f5a036665e708b4c`。本轮完成可运行研究闭环及自检修复；所有业务数据为明确 TEST_FIXTURE。真实来源/数据完整性、工程正确性与投资有效性分别报告，`MARKET_EDGE=NOT_ESTABLISHED`。

## 环境与隔离

Python 3.12.14，Pydantic 2.13.5、PyArrow 23.0.1、DuckDB 1.5.6；FastAPI/Uvicorn 及测试依赖版本见独立锁文件。只使用 `stockfish-live/.venv`，未修改旧 Research venv、pyqlib wheel、生产源码、数据库或服务。原 Research 二进制导入问题没有被本轮“通过”覆盖。

所有运行产物位于被 Git 忽略的私有 `work/stockfish-live-*20261007/`。没有发布完整行情、运行包、路径档案或凭据。新目录运行，旧 run 不覆盖；用户 iCloud fixture 变动未暂存。

## 自检发现与修复

- 取消回放、summary 落盘失败、取消 demo 均保留明确未完成标记；磁盘不可写时保留原异常。
- 同 ID 不同历史状态与标签不能在合并运行结果时静默覆盖。
- 成熟标签必须具有实际 raw/receipt 父证据、完整未来分钟路径、正确锚点/目标/horizon/regime，且实际时钟不晚于标签；研究表自洽 hash 不能替代证据。
- 已缓存结果不允许 alias 修改输入；强制 `model_copy/model_construct` 绕过验证的非法字段在边界重新校验。
- HTTP EMPTY 历史按实际可见时刻查询；将来捕获的空响应不提前显示，后续 AVAILABLE 不抹去过去空响应事实。
- 秒/微秒真实捕获时间不回拨至名义整点；跨下一分钟的迟到决策不冒充准时预测。
- 训练、归一化和内层 K 选择只消费更早会话及当时成熟标签；PIT 等级覆盖全部 fit 候选，不能只取选中的 K 个来升级等级。

测试保留未来时钟、丢分钟、缺资格/成员、限价反证、数据源故障、篡改、单写者、重复/乱序/冲突及失败恢复断言；未删除失败样本或削弱正确约束来取得通过。

## 关键工程证据

冻结基础层使用 2025-06-18 合成 fixture 重新回放，实际命令：

```sh
stockfish-live/.venv/bin/python -m stockfish_live.cli replay --date 2025-06-18 \
  --speed max --foundation-only \
  --data-root work/stockfish-live-phase1-20261007/fixture \
  --output work/stockfish-live-v1-20261007/foundation-regression
```

退出 0：242 ticks、1,920 bars、gap=0、failed_cycles=0；242 快照序列与冻结 Phase 1 完全一致：

```text
3d425c105fa9b5085a3e63c3bad8b83a20bbf9736906bd5299237db34701eacc
```

局部优化等价性：2 日、126,450 个完整未来 PathPoint 的压缩前后 history hash 与 forecast hash 完全相同；完整 canonical 路径无损压缩并在 Top 10 重建，未用抽样、截短或删除路径优化。独立进程 prepared 峰值观测从旧版约 280 MB 降到约 213 MB（包含读取峰值），并发运行时间不可用来证明加速。

实际 localhost HTTP socket 测试通过：标准库真实 HTTP 收件、原始字节 SHA、解析完成后的本机 availability、缓存读取不触发请求，以及 503 后明确降级。它仍是 TEST_FIXTURE，不是外部真实行情接入证据。

打包验证使用真实 wheel 构建、隔离目标安装和 Python `-I` 外目录导入。静态 HTML/CSS/JS、CLI entry point、包/OpenAPI 版本、11 个 GET、WebSocket、未来 as_of=422、缺资源=404 和保存 run 字节不变均检查；未误从源码 editable 路径导入来宣称 wheel 通过。

## 性能与边界

推荐目标为特征 <=2 秒、forecast <=1 秒；原请求也允许未达目标时如实 profiling。内部计算、provider 网络/事件延迟、开盘前 archive/model 加载分开记录。小型声明池不代表全市场 SLA，合成零 provider 延迟不代表真实供应商零延迟。

5000 股票 + 5 指数、31 分钟局部压力已暴露参考 V1 的累计重算热点。V2 限制每证券最多 31 bars/15 returns、增量输入链、行业相关性 O(n×t)，保留逐分钟 V1/V2 事实对照。曾测得特征 P95 约 2.75 秒；并发完整历史任务时约 4.69 秒，最大 8.26 秒。后续数值等价优化需独占复测，不把这些数字宣称为已达 2 秒或完整全链路 SLA。

完整历史旧 loader 曾在 3.8M 路径节点上超过 2.5GB RSS 且长时间无预测；该次旧配置试跑中止，没有冒充成功。已改为逐分片完整校验、逐日来源证明、无损 compact prepared archive。中途因特征配置变更而取消的旧 V2 demo 单独标记 FAILED_DEMO，最终数据集另建目录。

## 未验证范围

真实供应商授权、外部连通、真实历史/全市场覆盖、正式申万 PIT 成员与资格、真实 VERIFIED_PIT OOS、长期前瞻与投资价值、Windows 原生、8核/32GB 容量和连续交易日运行均未完成。本轮没有启动或切换生产服务，也没有券商/交易执行。

本轮后续获得可用的应用内浏览器，已实际检查页面加载、五指数切换、09:45 历史时点、实际路径截止、未知状态与合成标识。检查使用默认视口，不等于所有设备与断点的视觉验收。已实现的情绪/行业转移是受限初始统计，未取得独立校准或 transition accuracy 实证；真实投资指标保持 NOT_EVALUATED。

原 Phase 0/1 的命令及失败修复记录保留在 [历史验证](VALIDATION.md)，当前功能与后续门槛见 [当前状态](../CURRENT_STATUS.md)。

## 完整历史与回放实跑

最终 V2 合成数据集使用新目录 `demo-v2-final`，60 个连续合成历史会话和目标日（2026-10-09），日期包含周末，不能用作官方日历。实际生成退出 0，用时 674.831 秒：117,120 条分钟行情、13,800 个状态、53,100 个成熟标签、3,793,500 个完整未来路径节点。五指数和三只股票均为 TEST_FIXTURE，不是全市场覆盖。

```sh
stockfish-live/.venv/bin/python -m stockfish_live.cli demo --date 2026-10-09 \
  --history-sessions 60 --data-root work/stockfish-live-v1-20261007/demo-v2-final
stockfish-live/.venv/bin/python -m stockfish_live.cli replay --date 2026-10-09 --speed max \
  --data-root work/stockfish-live-v1-20261007/demo-v2-final \
  --output work/stockfish-live-v1-20261007/replay-v2-a
```

首次完整回放退出 0：242 ticks、240 个完成分钟、1,920 bars、230 条追加预测、gap=0、failed_cycles=0。09:45 五指数的五个 horizon 均有 60 个可用历史候选、K=50、Top 10 完整未来路径；09:46 没有补造新预测。序列 hash：

```text
7db064fd27d2b9b7b41c90afe64cd19e2c85fd79c5ef6be9b6f3fd705ecc4aea
```

历史 hash 为 `052421b3827375ff38cd3cf3cfad824bbda7760a29e8d270f322b331492cb298`，pipeline 配置 hash 为 `7a545c629295cefcb5f654036bfdc7ac7e53758689e5f2ee09570db3d892d49a`。实际 archive_load=42.836 秒、model_prepare=424.474 秒；全 tick 内部含落盘 P50=23.759 ms、P95=13,639.784 ms、最大=21,747.280 ms。并发测试、doctor、服务冷启动影响资源；这些结果明确未达到盘中 SLA，不用非决策分钟低耗时掩盖预测分钟耗时。

独立完整 run 验证退出 0：逐个核验 242 快照 hash/序列、全部预测数量、Top 10 路径；11 个 GET 在 09:45 返回 200，实际行情每指数仅有当时 15 个分钟，未来 15:01 请求全部 422，WebSocket 和静态资源正常。读取前后所有 run 文件字节一致。首次 API 冷加载为 50.589 秒，后续各 GET 在 117.896—727.393 ms；不将 warm cache 指标宣称为启动耗时。

`doctor` 对全部原始归档、上下文和 53,100 个成熟标签来源实跑退出 0，回放就绪，但完整特征研究检查为 INSUFFICIENT_HISTORY。09:45/10:00/10:30/13:30 的完整特征候选分别为 47/49/49/59 日：UNKNOWN 情绪的六个 one-hot 保留 None，不能填零凑齐 50 日。实时预测按本次查询非空维度选择训练，因此其候选数可以更大。`research_ready=false`、`test_fixture_research_ready=false`，保留真实检查结论。

## 真实服务自检

真实浏览器连接暴露了 TestClient 未捕获的问题：Uvicorn 缺少 WebSocket 运行后端，返回 404。已将 wsproto 声明为运行依赖，并新增真正 localhost TCP/HTTP Upgrade 101 和完整 snapshot 帧的验收；保留原失败事实。`serve` 复用预校验的同一个应用实例，避免冷加载运行两遍。

未配置真实 feed 的 `live` 命令实际退出 2、LIVE_PROVIDER_UNAVAILABLE，没有创建伪运行或发起默认外部请求；非法评价决策时刻 12:00 实际退出 2、INVALID_EVALUATION_DECISION_TIMES。全部源码编译、脚本语法和独立环境 `pip check` 的最终结果随发布检查登记。

## 修订性能热点与等价性

全日 profiling 定位到旧 RevisionEngine 每次重新深度校验本指数全部旧 forecast，重复构建完整相似样本路径。Pipeline 已改用 PreparedRevisionHistory：每条新 forecast 完整校验一次，再保存脱离调用方对象的不可变标量；公开 compare 仍严格重验任意外部历史。共同目标、实际 as_of、5/15/30 个交易分钟、最近三次连续变化及午休两端的实际时间排序保持不变。完整 forecast 仍追加保存，没有截短历史路径或正式档案。

定向 `test_prepared_revision.py test_forecasting.py test_pipeline.py`：57 passed、18.61 秒、退出 0，包含新增的稳定重复时间排序、跨日隔离与失败接纳原子性检查。原 A 的全部 230 条保存预测逐条对照：revision payload/id 完全一致；242 快照携带的修订状态、snapshot_id 与序列 hash 不变。不可变摘要接纳累计 10.335 秒；每条 P95=158.16 ms，五指数每决策合计 P95=738.47 ms。该局部并发观测排除 JSON 初始加载、模型推理及落盘，不能充当全链路 SLA。

旧 strict 另做 125 条微观复核，全部一致，累计 233.245 秒；为减少重复工作主动中止该只读基准，退出 130。未把部分旧基准冒充完整速度对比。优化后的全日 C 独立运行，实际完整耗时另行登记。

## 最终源码回归与中文界面

实际命令 `stockfish-live/.venv/bin/python -m pytest -q stockfish-live/tests`：517 passed，494.37 秒，退出 0。收集完成后新增的三项修订边界检查另包含在上述 57 项定向回归中；没有把未收集项计入全套数量。唯一 warning 是 Starlette TestClient 对当前 HTTPX 的弃用提示，未隐藏或扩大 skip。真实 Uvicorn TCP/WebSocket 另测，避免由 TestClient 通过推断线上后端齐备。

`python -m compileall -q stockfish-live/stockfish_live`、独立 `pip check`、`node --check stockfish-live/stockfish_live/frontend/dashboard.js`、`git diff --check` 均退出 0。中文页面另跑 API/release 回归 59 passed、11.11 秒、退出 0。

主观察时点与滑块预览均显示上海完整日期时间（如 2026-10-09 09:45），避免把合成目标日期当作今天；Node 语法及 UTC 跨日格式检查退出 0。页面标题、图例、运行/数据质量、六类行情、情绪、行业边际、置信度、期限、缺数与来源解释优先中文。保留品牌、证券代码、Q50、模型版本和原始审计详情。应用内真实浏览器已验证中文完成状态、五指数选项、创业板/上证切换、09:45 历史观察、50 个样本的五期限分布、明确合成标识、未知与暂不可用的区别。浏览器默认视口检查不等于跨设备验收；截图保留在私有工作目录，不随源码发布。

最终中文与修订优化 wheel：`stockfish_live-0.2.0-py3-none-any.whl`，SHA256 `c9195f36019d311df17b94682d4ca2be94a42f555cc7dc02bd89bc22b353e419`。55 个包内 Python/HTML/CSS/JS 文件与冻结源码逐字节一致。全新 target 安装、`python -I` 外目录导入、中文 HTML 实际服务、prepared/strict 修订 canonical 一致、完整 60 个路径节点的 prepared/raw 等价、11 GET、TestClient WS、真实 Uvicorn HTTP 200 / WebSocket 101 / snapshot 帧均通过，退出 0。之前未包含最终观察日期、中文、修订优化或 WS 运行依赖的 wheel 已被取代，不混用旧 hash。完整私有报告不发布。

## 重复回放确定性

同一最终数据根新建 B 目录完整重复回放，退出 0；A/B 的 242 个快照逐文件逐字节相同，model/history/config hash 和 230 条预测计数相同，序列仍为 `7db064fd27d2b9b7b41c90afe64cd19e2c85fd79c5ef6be9b6f3fd705ecc4aea`。B 同为 240 完成分钟、gap=0、failed_cycles=0。B 使用旧修订路径作重复基线；全 tick 内部含落盘 P95=14,961.444 ms、最大=33,312.840 ms，archive_load=60.783 秒、model_prepare=708.252 秒。进程跨越本机睡眠/恢复且与其他校验并发，进程 elapsed 不用于性能承诺，独立 latency 与加载计时如实保留。

## 优化后完整 C 回放

相同数据根、新建 `replay-v2-c`，使用冻结的 prepared revision 实现，退出 0。242 ticks、240 完成分钟、1,920 bars、230 条追加预测、gap=0、failed_cycles=0。A/C 的 242 个快照逐字节一致，model/history/config hash 相同，序列 hash 不变。不是仅抽样核对或只比较最终价格。

C 全 tick 内部含落盘 P50=14.155 ms、P95=1,825.505 ms、最大=2,933.925 ms；46 个实际预测决策 tick 单独计算 P50=374.223 ms、P95（线性）=2,537.013 ms、最大=2,933.925 ms。archive_load=78.484 秒、model_prepare=657.089 秒，启动加载仍是明确性能限制。各轮并发负载不同，不用 A/B/C 的比值声称严格基准加速倍数；该结果仅属于五指数/三股票 TEST_FIXTURE，不能推断全市场或真实 provider SLA，也未证明所有预测都小于 1 秒。

## 完整 CLI 评价

```sh
stockfish-live/.venv/bin/python -m stockfish_live.cli evaluate \
  --as-of 2026-10-09T16:00:00+08:00 \
  --data-root work/stockfish-live-v1-20261007/demo-v2-final \
  --run-dir work/stockfish-live-v1-20261007/replay-v2-a
```

实际默认四时刻、五指数及内层 K 验证；未使用随机拆分。最终第二轮退出 0，859.076 秒，峰值 RSS 1,019,322,368 bytes。输入范围明确为默认决策时刻及原保存预测所需状态，未选择的归档分片仍做完整性校验；1,430 states、6,585 outcomes、61 sessions、13,725 个原始父回执通过标签来源证明。

合成 W/F 325 个 horizon 结果，95 个分组，五类 baseline 均有 325 个支持；MAE/RMSE/方向/Spearman、Brier/log loss/calibration、五分位 pinball/50%及80% coverage/width、六类 confusion/accuracy 等结构和实际分母完成核验。不足训练会话 1,000 项、不足成熟合格训练标签 720 项单独登记，没有记零收益。

220 次内层 K 选择全部为 INSUFFICIENT_INNER_VALIDATION：K=50 最多 9 个验证样本，K=100/200 为 0，故全部保留默认 50，**没有 K 胜出证据**。原保存预测 230 IDs 中 125 个具有成熟可用分布，形成 490 个合成 horizon 后验；395 个预测不足单独登记，不补造 baseline，不重训或改旧预测。真实与 PIT 未证明结果均为 0，投资评价为 NOT_EVALUATED，增量优势为 NO_EVIDENCE_OF_INCREMENTAL_EDGE。

首轮评价实际退出 2、STORAGE_CHANGED_DURING_READ（121.595 秒），没有报告。诊断确认研究分片 ctime 改变而 mtime 保持原值，具体后台修改进程未确定；没有把它武断归因于 iCloud，也未移除断言。保留失败与 metadata/SHA 证据，之后 240 份文件的 SHA/stat 复查无变化，原目录第二轮同 CLI 才通过。两轮跨越本机睡眠/恢复间隔，最终耗时只作观测，不称性能验收。
