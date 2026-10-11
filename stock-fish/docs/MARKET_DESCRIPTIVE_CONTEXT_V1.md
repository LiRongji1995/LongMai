# 原生全市场描述事实 V1

`MARKET_DESCRIPTIVE_CONTEXT_V1` / `WHOLE_MARKET_DESCRIPTIVE_TREND_EXACT_NATIVE_V1`，公式规格 `MARKET_DESCRIPTIVE_CONTEXT_SPEC_1`。这是显式研究附件，参数均为**尚未验证的初始描述口径**，没有新信号分数或选择门槛。未接默认入口、R1/R2、主题 FSM、数据库或预测排名；旧 run 与源语义不改。

## 调用与能力

```python
from stockfish_core.yaogu.market_descriptive_context import (
    run_market_descriptive_context, validate_market_descriptive_context,
)

context = run_market_descriptive_context(
    bundle, cutoff=cutoff, market_stream=authenticated_native_stream,
    sessions=observed_sessions, evidence_directory=new_private_directory,
)
validate_market_descriptive_context(context, bundle=bundle, cutoff=cutoff)
facts = context.context  # 独立副本；不是授权能力
```

必须传真实 `SourceMarketStream` 与其真实 `SourceArtifactReader`，不接受相同字段的 dict、duck type 或覆盖原件读取的子类。原生 stream 绑定原 input、cutoff、完整已观察交易日历、receipt inventory、源实现及逐日 panel hash。生产者先用冻结原逻辑认证原件 SHA、完整 envelope、日期、主目录范围、交易状态、adjustflag3 和实际 first_seen，然后核对同日 panel 的证券、父引用、OHLC/preclose/amount/volume、完整范围及四时钟。参数实际可只有 date；显式 adjustflag 必须为3。

返回冻结的 `MarketDescriptiveContext` 原生能力，`.context`、`.context_hash`、`.source_identity`、`.artifact_files` 提供保存事实与索引；`validate_binding()` 再认证全部已消费 RAW 原件、receipt 原字段/时钟、原路径、当前源码/spec、输入及所有保存文件。普通 JSON、自 hash、status=PASS 或版本文字都不能构造此能力。没有增加归档能力恢复或新 composer。

能力完整性版本 `ISSUED_SOURCE_AND_FILE_REGISTRY_DIGESTS_V1`：发行时分别固定内部消费来源及完整产物登记表的摘要。每次验证先核对摘要，要求来源登记表与 context 的 hash-bound consumed_sources 相同，且文件登记必须完整包含 context/descriptor/sources/manifest 与原 journal 的全部段、index、seal；逐个 journal hash/字节数再与 context 的原审计 manifest 对照。清空、删除、添加或替换内部登记、改嵌套 receipt/meta、仅移除 journal 登记都拒绝，不能缩减循环逃过原件/文件检查。验证结束再查登记摘要；getter 的副本不是内部完整性的依据。冻结 dataclass 本身不能冻结嵌套 dict，因此该发行摘要与交叉绑定不可省略。

## 精确公式与未知

- 当日 M 为已认证交易状态1的全部四板代码族 RAW amount 之和。状态0按原 master/RAW 验证排除，记录数量与 population hash，不能变成补零交易行。交易总体为空时 M 和所有统计为 UNKNOWN，交易家数0是已知事实。
- 每行日收益为 RAW close/preclose−1；上涨/下跌/平盘计数和广度要求全部交易行有合法同父收益。中位数、Q25、Q75、IQR 用排序后的精确 Fraction、Type7 位置 p×(N−1) 插值；IQR=Q75−Q25。它们是**日收益分布**，不是绝对股价中位数，也不是指数/复权基准。
- Top10 占比为最大 min(10,N) 个交易 amount 之和/M。M=0时 amount=0可以是原件事实，份额为 UNKNOWN。Top10 只是集中度统计，不是选股参数。
- 前20日基准为完整已观察交易日历 T−20…T−1 的 M 中位数，排除 T。缺日、未在 stream 请求范围内、空交易总体、未知金额都不压缩或跳过；窗口不足20记左端截断。基准0允许保存，但 M/基准为 UNKNOWN。不替换冻结 R2 的含当日 median5 软参考。
- 成交额日变化为 M(T)/M(前一个实际交易日)−1；广度、Top10、中位收益和 IQR 的日变化为当前−前一个实际交易日。缺相邻日/轴或金额分母非正为 UNKNOWN。保留完整依赖日历、每轴原因、证券总体 hash 与数量；总体改变不会冒称固定成员比较。
- 同父已登记 BaoStock `turn` 是流通股换手百分数，basis=`BAOSTOCK_TURN_PERCENT_FLOAT_SHARES`；有效范围0…1000。仅全部交易行合法时给中位百分数与≥5%家数，缺失/非法不补0。这里从 RAW 精确计算，不把值写入 DailyBar 或改变 R2 活跃门槛；5%仍是未验证的描述边界。

RAW JSON 数字以 Decimal 解码，字符串直接解析 Decimal，再转 Fraction，不用浮点统计。有限十进制文本上限256字符、指数绝对值上限128是表示守卫，不是交易门槛，不放开全局整数转换限制。每个 panel 字段与冻结 reader 的同一 RAW 行对照；如果原 JSON 数字已在冻结标准解析中损精度，相应金额/收益轴保持 UNKNOWN，不能声称圆整数值精确。原 RAW 字符串高精度保持精确，turn 描述也不经过 DailyBar float。

原件中的非交易行 amount/volume 也重新按精确十进制确认零值。JSON 极小正数被冻结标准解析下溢为0不能授予合法非交易排除；此类冲突拒绝，不隐藏成交。

## 保存与终态检查

evidence_directory 必须为新目录。保存 `context.json`、`source-descriptor.json`、`consumed-sources.json`、`manifest.json` 和完整 `raw-field-audit/`。复用冻结 canonical row journal 的分段/index/行 SHA/文件 SHA，记录所有已消费父原件的解码行，不抽样；其角色是完整原始行审计，不能解读为 selector 合格行。原 RAW 字节 SHA 与 receipt 时钟仍是来源权威。

每个 context 同时绑定新公式/spec/源码 identity、原 stream descriptor/hash、input hash、actual cutoff、receipt 内容/原路径、逐日 panel/source refs/原时钟、计算与窗口。保存前和返回前再次读取全部已消费原件。每次能力验证先查输出/登记摘要并重验原件，再核验全部保存文件；文件核验结束后再次核对完整登记、重读全部原件，最后检查登记和完整 context 摘要。这样大 journal hash 期间发生的 RAW 或内部事实变更也会被拒绝。中途改 RAW/receipt/path/source/spec/输入或输出则失败，不返回能力。失败保留部分产物和 failure.json，不覆盖旧产物。

本模块逐日消费，完整行审计索引仍随数据量增长，原 SourceArtifactReader 读取完整单个 RAW JSON。它不是保证 process RSS/运行时间有界的独立服务；大范围真实研究须由调用方使用原私有预算与进程限制。测试只用隔离 TEST_FIXTURE；本文件不宣称真实183日、PIT或投资验证完成。

## 解释边界

代码族全市场范围不是历史主板/ST/IPO/退市期/限价资格；全市场交易家数不是规则合格家数。成交额不是净流入，广度/集中度不是资金因果。历史身份及预测优势不由采集成功认证：`historical_strategy_qualification=UNKNOWN`、`official_pit_validation_count=0`、`authenticated_emotion_cycle_count=0`、`MARKET_EDGE=NOT_ESTABLISHED`。实际晚收件不改成历史 first_seen。未来题材比较必须先验真实父能力和逐日同来源/范围/收益口径/时钟，不同来源冲突为 UNKNOWN；本版本不执行该合并。
