# 沪深日期化物理原件历史对照 V2

状态：显式工程研究入口，未接入默认服务；初始参数尚未验证，
`MARKET_EDGE=NOT_ESTABLISHED`。旧物理工厂 V1、PAIR V1、原 R1/R2 selector、
原规则和已保存 run 冻结。V2 只登记新的来源、价格视图与资格边界。

## 入口与版本

```python
run_physical_historical_pair(
    sources_manifest_path, source_policy, authority_manifest_path,
    observation_date, registered_worker_deadline, history_sessions=26,
)
```

新实现为 `PHYSICAL_ORIGINAL_HISTORICAL_R1_R2_PAIR_V2`，特征为
`DATED_SH_SZ_APRIL_MAY_EXACT_PRICE_V2`，来源政策为
`SSE_MARCH13_APRIL_MAY_BOARD_AND_DELISTING_RULE_V1`。三个路径都指实际原件；
不能传入外部 MarketInput、已保存 run、reader、能力、validator 或覆盖回调。
来源清单继续使用真实 `SAVED_R2_DELIVERY_SOURCES_V2` 合同；基础政策目录继续
包含 V1 的八份原件。新增权威清单不复制行情、不建立另一套数据库。

`authority_manifest_path` 是严格 JSON：

```json
{
  "schema_version": "SSE_MARCH13_APR24_AUTHORITY_MANIFEST_V1",
  "sources": {
    "code_notice": {"body": {"path": "实际PDF", "sha256": "固定原件SHA"}, "receipt": {"path": "实际receipt", "sha256": "固定receiptSHA"}},
    "code_guide": {"body": {"path": "实际DOCX", "sha256": "固定原件SHA"}, "receipt": {"path": "实际receipt", "sha256": "固定receiptSHA"}},
    "listing_2025_notice": {"body": {"path": "实际HTML", "sha256": "固定原件SHA"}, "receipt": {"path": "实际receipt", "sha256": "固定receiptSHA"}},
    "listing_2025_rules": {"body": {"path": "实际DOCX", "sha256": "固定原件SHA"}, "receipt": {"path": "实际receipt", "sha256": "固定receiptSHA"}},
    "listing_notice": {"body": {"path": "实际HTML", "sha256": "固定原件SHA"}, "receipt": {"path": "实际receipt", "sha256": "固定receiptSHA"}},
    "listing_rules": {"body": {"path": "实际DOCX", "sha256": "固定原件SHA"}, "receipt": {"path": "实际receipt", "sha256": "固定receiptSHA"}}
  }
}
```

路径相对该清单所在目录解析。固定 whole SHA/URL 在模块登记，调用方只能选择
实际物理路径；不能自选替代文件哈希或时钟。清单上限64KiB，逐权威文件上限
1MiB，DOCX 的唯一 `word/document.xml` 解压上限2MiB。消费前后使用原 Registry
重新核 whole 字节、物理状态与来源映射；局部语义读取不替代 whole 认证。
SHA仅是本地完整性，不是交易所签名或历史 PIT 证明。

## 修复的资格缺口

V1 的 RAW 价格构造和观察日 MAIN 分类都只包含 SZSE `000001–004999`。
即使政策目录另有沪市文档，V1 也不会发行沪市普通主板输入。该限制保持冻结，
V2 在独立新工厂明确修复，不把旧沪市 UNKNOWN 解释成形态不合格。

已认证2026-03-13第4号业务指南第1次修订通知和 DOCX 的完整六行分类：
`600/601/603/605` 为主板A股，`688` 为科创A股，`689` 为科创存托凭证。
只允许 `2026-04-01 ≤ 观察日 < 2026-06-01` 的四个普通主板前缀；不泛化为
所有 `6xx`，不外推到3月前、6月、7月修订连续性或全年。观察日仍须同日目录、
同日真实RAW、无冲突原IPO日期、type=1及原上市时长门槛。深市原资格不变。
其他证券或缺身份继续 UNKNOWN，universe complete 仍为 false。

证券号必须是确切交易所前缀加六位ASCII数字。Python 的 `isdigit()`/正则
`\d` 会接受全角、阿拉伯等 Unicode 数字；V2 不沿用这一宽松身份判定。
新工厂先核全部注册的 RAW、同日/历史目录、stockbasic、因子行、因子请求和
逐股覆盖中的代码，再使用金额和价格。任一非ASCII或格式错误记录直接拒绝
整次构造，不能默默丢行、改成另一证券号或把完整分母缩小。该校验也覆盖
未选中/不支持板块的行；冻结 V1 与 R2 不作改写。

该预校验新增一轮实际 code-bearing whole payload 遍历，audit 单列读次数、
真实返回字节及开始/完成钟；该计量只覆盖ASCII预校验，发行、registry终验和
native各自的读取还须计入外层总预算。V1先前的时限/RSS/累计字节成绩不能
替代V2验收，也不自动授权扩大任何旧失败尝试的预算。

26个实际日历会话的价格窗口和观察日资格分开。观察日处于批准范围时，窗口
中的沪市四前缀历史价格独立接受完整同日 RAW/master、日期、身份、交易状态及
复权认证；不要求每个旧价格日都已在新批准资格窗口，也不据今日证券名单筛
过去成员。无法证明的旧价格、ST或停牌状态不填0或 FALSE。

## 沪市退市名称与版本过渡

[2025年上市规则正式页面](https://www.sse.com.cn/lawandrules/sselawsrules2025/repeal/rules/c/c_20250515_10816590.shtml)
明确上证发〔2025〕59号、2025-04-25发布和生效；
[2026年上市规则通知](https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/mainipo/c/c_20260424_10816589.shtml)
明确2026-04-24发布生效及旧版同时废止。两份完整 DOCX 第9.6.1条分别核验
同一强制“退市”前缀条款。因此批准的4/5月内同日真实沪市名称用 `退市…`
前缀，不能套深市 `…退` 后缀给出假 FALSE。空名称、未证明 MAIN 或缺规则
authority 仍 UNKNOWN。深市后缀规则沿用冻结实现。

4月24日日内版本切换时刻 UNKNOWN；这里只证明两份版本该条款相同，不能
推广为整份规则完全一致或其他过渡安排已核验。后来获得2025规则原件的首见
钟保留为2026-10-12，不能宣称研究系统在4月已经取得。March通知只有日期，
精确发布时间 UNKNOWN；批准观察窗晚于该日期，不补造 publication 钟。

## 保留的安全与数值合同

新 builder 明确保留 V1 的逐会话完整四板 RAW/master 验证、缺当前分母拒绝、
IPO冲突、ST/停牌三值、25前置会话不压缩、全部真实因子冲突 oracle、COMPLETE
数值父、原真实祖先及捕获钟、精确复权乘积、精确换手与金额门槛。
只增加价格范围/观察日 board 与已证沪市退市前缀；不修改公式或阈值。
无法验证的上限/限价、安全事件及历史主题成员保持 UNKNOWN。

一轮新真实 SourcesV2 发行、一个派生输入、一个实际截止，原 R1/R2各一次。
R2按原设计内部再评估R1，原 `_compare`核原证据、资格、运行身份及精确行为
差异。新 factory/seal 使用自己的 token、registry、PID、typed input hash、
policy与源码身份、截止、状态和一次尝试；失败占尝试，不能复制或恢复消费能力。
原件实际首见/入库/完成钟、派生完成钟和终验钟保留；截止不会倒填至历史。

主线程绝对截止继续使用冻结 `_Deadline`，活动调用方timer冲突直接拒绝。
OS/进程树内存、总来源返回字节和输出预算由外层隔离执行另行登记；本模块
不提供硬内存隔离或自动重试，不自动采集、接服务、写DB或运行整年回测。

输出仅 `ARCHIVE_ONLY_NOT_RESTORABLE_SOURCE_CAPABILITY`。TEST_FIXTURE 验证
与真实 SourcesV2/native 执行分别报告；`historical_PIT_count=0`、
`complete_independent_cycles=0`，投资优势尚未建立。扩大资格并不自动证明
存在完整年度主线、主题梯度或协同/竞争规律。
