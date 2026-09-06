# 沪深主板强势股候选策略 V1

本文件当前对应 R03 `YAOGU_V1.1.0`、`YAOGU_FEATURES_V1_R03` 和 `config/yaogu_v1.defaults.yaml`。旧 `YAOGU_V1.0.0` 已保存配置与运行原样可读，旧特征版本不能生成新榜；修正后重算必须新运行/版本。五族公式、权重和关注阈值未调优；R03更正三日d、覆盖分区、来源时点/资金窗口及摘要契约。参数状态为 **INITIAL_HYPOTHESIS_NOT_MARKET_VALIDATED**。本系统筛选、排序、解释、保存和跟踪候选；不连接券商、不下单，不创建账户或默认十万元虚拟本金。规则匹配分不是涨停概率、胜率或可实现收益。

## 1. 代码与证据入口

| 语义 | 实际实现 |
|---|---|
| 输入/输出、来源时钟、实体 | `stockfish_core/yaogu/contracts.py` |
| 参数、权重、R/Trap、强校验 | `stockfish_core/yaogu/config.py` |
| 资格、五个策略、组合分、层级、轮询 | `stockfish_core/yaogu/engine.py` |
| 涨跌停、未知/不适用、严格连板 | `stockfish_core/yaogu/events.py` |
| 量价、可比价、成熟股性 | `stockfish_core/yaogu/features.py` |
| 主题剔除自身、完整参考集排名 | `stockfish_core/yaogu/themes.py` |
| 市场覆盖、昨日全集晋级、情绪状态 | `stockfish_core/yaogu/emotion.py` |
| 三交易日持久历史选择 | `stockfish_core/yaogu/emotion_history.py`、`candidate_service.py` |
| 统一来源收据、行与历史证明校验 | `stockfish_core/yaogu/visibility.py` |
| 资金、龙虎榜去重、席位证据 | `stockfish_core/yaogu/funds.py`、`providers.py` |
| 交易所规则事实 | `stockfish_core/yaogu/rules.py` |
| 冻结竞价证伪、交易日观察 | `stockfish_core/yaogu/auction.py`、`outcomes.py` |
| 历史比较、重抽样和purge边界 | `stockfish_core/yaogu/evaluation.py` |
| 唯一持久发布、API、本地调度 | `stockfish_core/yaogu/candidate_service.py`、`candidate_api.py`、`scheduler.py` |
| 默认页面 | `static/index.html`、`static/core.js`、`static/candidate.css` |

实现验证与真实数据核验分开。当前证据索引位于工程相邻的 `review/r03-realdata-closure/`，上轮 `review/r02-fullscope/` 保留历史语境。交付包按自己的清单纳入报告、原始外部反例和最小真实证据，不依赖原机D盘链接。有关真实日期、实际覆盖、外部权限和测试退出码，以当前目录的源能力、运行核验、测试和最终审计为准；本规格不宣称有完整历史数据或市场优势。

## 2. 数据、时间与资格

默认池是当时有效且已取得的证券主表中：SSE/SZSE、MAIN、普通A股、非ST、上市至少60个交易所交易日、非退市整理且当天没有停牌。不能以代码前缀作为最终身份依据。未知身份、状态或规则不能猜测。`effective_from <= session < effective_to`；结束日为半开区间。上市、停牌和未知缺失各自保留原因。

ST开关选择独立ST研究池；次新开关选择独立次新观察池，两者不与主池混排，也不能同时选择。默认都关闭。次新从6交易日起可保留观察身份，但不能凭开关获得不存在的60日历史。没有PE、市值、累计涨幅或“超过几板永久禁止”的隐藏筛选。

每条证据保留 `event_time/source_published_at/first_seen_at/ingested_at`；运行另有 `observation_date/decision_cutoff/generated_at/market_session`。全部datetime必须有时区。正常捕获先检查来源收据与外层快照可见时间，再读证券身份和业务字段。20:30晚到的信息不能改变16:10榜。历史重建明确标记 RECONSTRUCTED_HISTORICAL，不因旧文件名或历史日期成为 PROSPECTIVE_CAPTURED。不可核验时点为 UNVERIFIABLE_AS_OF；合成为 TEST_FIXTURE。

R03统一 `R03_EVIDENCE_VISIBILITY_V1`：先解析父收据，再验证行身份、业务日历与时钟。历史重建只可凭带原始来源/证据hash/适用区间的 HistoricalTimeProof 放宽晚下载的取得时间；不能放宽来源事件或公布时间。外层未来收据不能被内层旧时间绕过。原始输入保留，实际采用的解析依赖进入有效摘要。

RAW价格为元，成交量为股，成交额为CNY，比例内部为小数。每个 FeatureValue 有 value、unit、window、as_of、source_refs、definition_version、quality。源收据包含请求参数脱敏归档、字段版本、条数、分页状态、来源及SHA256。普通本地hash只是内容标识，不是针对完全磁盘写权限者的安全认证。

当日限价优先使用可核验字段；自行计算必须绑定交易所/板块/ST状态/生效日/参考前收/tick与舍入规则，并完成来源样本交叉验证。注册器保留2026-07-06制度切换，不把主板ST历史永久写成5%。未核验的例外、前期制度边界为UNSUPPORTED。官方来源索引为[上交所2026交易规则](https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml)、[深交所2026交易规则原文](https://docs.static.szse.cn/www/lawrules/rule/trade/current/W020260424690713155663.pdf)；确切采集记录、规则fixture及适用限制见 `rules.py` 与审阅包源能力报告。交易所事实与可修改策略参数分离。

## 3. 事件及量价

事件值为 TRUE/FALSE/UNKNOWN/NOT_APPLICABLE。使用Decimal对应tick比较：C等于已验证涨停价为收盘封板；H等于该价为触板；触板但未收盘封板为触板未封住；O=H=L=C=涨停价为上限一字。超过验证限价、OHLC次序错误、价格偏离tick为未知/无效，不能算正常涨停。没有日内事件数据就没有开板次数、首次封板时刻、排队或可成交保证。

严格连板按交易所交易日计数。确认停牌和已知未涨停均打断，但原因不同；未知前一日不能造出“首板”。左端已涨停而前史不可知时保留 LEFT_CENSORED_AT_LEAST_n 和下界，精确值NULL。下界足以证明至少3板可命中HIGH_BOARD结构，其精确c依赖分项仍不可完整评分。M3/M5/M10/M20为最近对应交易所交易日的收盘封板次数；未知和无涨跌幅规则窗口不可伪装完整。确认停牌作为已知零次事件，标COMPLETE_WITH_KNOWN_SUSPENSION。

```
amount_ratio_5 = amount_t / mean(amount_(t-5)...amount_(t-1))
volume_ratio_5 = volume_t / mean(volume_(t-5)...volume_(t-1))
turnover_fraction = volume_shares / verified_float_shares
amplitude = (H-L) / reference_prev_close
close_location = (C-L)/(H-L)
upper_shadow = (H-max(O,C))/(H-L)
```

当天不进入5日放大比的分母。换手也可取经核验同口径供应商小数字段；股本时点不能晚于事件/截止时间。H=L时，close_location为上限一字1、下限一字0、其他合法单价0.5；upper_shadow为0并保留单价标记。

ret3/ret5/ret10、MA5、历史比较统一使用相同 `comparable_basis` 的可比价格；INVALID源行不会贡献价格、成交额、成交量或换手特征。cmax60是过去60交易日内可证明的最高严格板数，不以左删失窗口最大值冒充完整历史。G历史范围最多250日，不少于真实60日；当前事件的未来标签不能进入当前股性。

## 4. 五类结构命中

| 主类别 | 条件 |
|---|---|
| ONE_TO_TWO | 今天封板，严格连续板数=1，前一日状态明确 |
| TWO_TO_THREE | 今天封板，严格连续板数=2 |
| HIGH_BOARD | 今天封板，严格连续板数>=3；已证明下界也可保留结构预览 |
| TREND_NM | 今天不封板，M5>=2或M10>=3，C>=MA5且ret3>=0 |
| REBOUND_SETUP | 今天不封板，上一轮至少2板且结束距今1–3交易日，C>=MA5、C>=O、close_location>=0.6 |

同股主类别优先 HIGH_BOARD → TWO_TO_THREE → ONE_TO_TWO → REBOUND_SETUP → TREND_NM。其他命中保留secondary_tags，不重复计票。已经再封板且收复上轮结束可比收盘的票可在ONE_TO_TWO下标REBOUND_COMPLETED，不再当成未完成的setup。没有命中可以空池。

EMPTY须能对整个可交易主池判断结构触发：连板已知或可证明高板下界；不封板时近5/10日事件、MA5、ret3、当前价格与近3日结束轮次可判断。当日宽度完整但策略触发历史未知不能称EMPTY；记UNAVAILABLE或PARTIAL及structural_screening_unknown_ids。普通未命中股票不因此被强制取得候选评分才需要的全部主题或G历史。

## 5. 固定公式

`R(x;a,b)=100*clip((x-a)/(b-a),0,1)`，a<b。`Trap(x;a,b,c,d)`：x<=a或x>=d为0；(a,b)线性上升；[b,c]为100；(c,d)线性下降。缺失不是0。内部使用未舍入分排序，页面保留两位小数。

记c当前板数、MN最近N日封板数：

```
B_ONE_TO_TWO = .40 R(M10;0,3) + .30 R(M20;0,5) + .30 R(cmax60;0,4)
B_TWO_TO_THREE = .40 R(M10;1,5) + .30 R(M20;2,7) + .30 R(cmax60;0,5)
B_HIGH_BOARD = .40 R(c;3,8) + .30 R(M10;3,7) + .30 R(M20;3,10)
B_TREND_NM = .50 R(M5;0,3) + .30 R(M10;0,5) + .20 R(ret10;0,.30)
B_REBOUND_SETUP = .40 R(previous_run_streak;2,6) + .30 R(M10;1,5)
                 + .30 R(comparable_C_t/comparable_C_last_run_end;.90,1.02)

V = .30 Trap(amount_ratio_5; family_parameters)
  + .25 Trap(turnover_fraction;0,.03,.20,.45)
  + .25*100*close_location + .20 R(ret3;-.05,.15)
```

amount参数：ONE_TO_TWO [.3,.8,2.5,5]；TWO_TO_THREE/HIGH_BOARD [.1,.35,1.5,4]；TREND_NM/REBOUND_SETUP [.3,.8,2,4]。它们只是可证伪的量能偏好，不代表真实资金身份或买盘保证。

主题需已登记明确产业/事件定义，至少5个当期同池主板成员；纯交易资格标签和明确INDUSTRY_PROXY不能冒充主主题。剔除候选自身后有效同伴覆盖>=95%。INVALID特征和NaN/Inf不参与统计。

```
T_theme = .35 R(peer_close_limit_up_count;0,5)
        + .25 R(peer_streak_ge2_count;0,3)
        + .25 R(peer_positive_return_fraction;.40,.80)
        + .15 R(peer_median_ret3;-.03,.09)
T = max(可用主题T)，同分稳定theme_id选primary_theme
P(x) = 100*(rank_average_ascending(x)-1)/(n-1), n>=2
L = .50 P(M10) + .30 P(ret5) + .20 P(median_amount20)
```

L参考集为冻结主主题完整同池成员，含自身，不按TopN重算。主题证据保存完整成员、排自身同伴、覆盖、全部可选主题与选择理由。

观察价格日与主题成员日期分开保存：例如9月6日晚首次取得当前主题，可以在9月6日截止的新研究中配合最新9月4日收盘价使用，但标CURRENT_MEMBERSHIP_AT_DECISION、membership_as_of=9月6日；不称9月4日PIT成员。历史截止仍在9月4日则拒绝该未来主题。此项不改变T/L公式。

```
G = .50 R(limit_up_count_last60;0,8) + .50*100*(s+1)/(n+2)
```

n是已可观察下一交易所交易日收盘的历史封板事件数，s是可比收盘收益>0的次数。最多250日，只用真实可用区间。停牌/缺价单列censored，不能顺延。完整历史确实没有成熟事件时，原始success_rate=NULL且收缩先验.5；有历史事件但全部下一日结果被删失时为ALL_HISTORICAL_EVENTS_CENSORED、G不可用，不能伪装成没有事件。

市场M来自默认沪深主板非ST历史当期集合。N为本次实际核验价格/状态的交易观察集合，N_expected、N_status_known、N_tradable_expected、N_price_observed并列对账；至少98%覆盖，不能把98%说成100%。昨日涨停cohort身份100%保留，昨日老上市证券若缺当时身份，记录previous_identity_missing并使读数不可用，不能将它从分母删掉。

R03 `MAIN_NON_ST_PARTITION_V1`统一分区：master_expected → identity_observed → eligible / known_excluded / eligibility_unknown；eligible再分known_suspended、price_required、price_observed、price_missing。status_coverage=(eligible+known_excluded)/master_expected；price_coverage=price_observed/price_required。已知ST、新股或其他板块是排除而非缺失；50已知身份中3只ST、47只合法齐价时两覆盖都是100%。未知身份/状态/上市历史与未取得主表记录必须进入eligibility_unknown，不能静默删除。昨日涨停成员即使今天ST或停牌仍保留昨日cohort身份；停牌不晋级且不伪收益。

```
p = (s+1)/(n+2)
M = .30 R(p;.10,.50) + .25 R(m;-.03,.05)
  + .20 R(U/N;.003,.020) + .15 R(b;.30,.70)
  + .10*(100-R(D/N;.001,.010))
```

U/D为涨/跌停家数，b为上涨比例；此处n/s是冻结昨日涨停集合与今日继续封板数，m是该集合今日合法可比收益中位数。确认今日停牌仍留在n、不晋级；m按可比价单列样本量。完整零cohort时raw p/m均NULL，平滑p使用明确先验、m子分50标PRIOR_NO_EVENT；全cohort确认停牌时m原值NULL、子分50标PRIOR_NO_TRADABLE_EVENT。数据未取得不是零事件。原始1进2、2进3、高板晋级率、炸板家数及梯队另列，不把平滑p冒充原始晋级率。

| 家族 | B | V | T | L | G | M |
|---|---:|---:|---:|---:|---:|---:|
| ONE_TO_TWO |20|25|25|10|5|15|
| TWO_TO_THREE |25|20|25|10|5|15|
| HIGH_BOARD |20|15|25|15|5|20|
| TREND_NM |15|30|25|10|5|15|
| REBOUND_SETUP |20|30|20|10|5|15|

`S=sum(weight*component)/100`。每行100。DAILY_V1缺任一分项不能逐股删除该项重标满分。黄金例TWO_TO_THREE的B80/V76/T85/L80/G60/M62经生产评分函数得到 **76.75**。Qlib、LLM、PE、ROE均不进入上述分数。

## 6. 资金、状态、展示与证伪

FUNDS_V1影子计算：

```
F_money = .60 R(day_same_source_large_net/day_amount;-.10,.10)
        + .40 R(sum_3day_same_source_large_net/sum_3day_amount;-.10,.10)
F_lhb = R(nonoverlap_disclosed_net/same_window_amount;-.05,.05)
F = .60 F_money + .40 F_lhb
S_funds = .90 S_daily + .10 F
```

先冻结整个同族结构候选集合，全部资金口径/日窗/披露状态可比才发布该族FUNDS排行，否则整族不可用并保留DAILY对照。多上榜原因、日/三日重叠披露不能重复累计。完整披露全集确认未上榜时F_lhb中性50、原始净额NULL、NOT_DISCLOSED；FETCH_FAILED/NOT_PUBLISHED不是未上榜。席位与别名仅有来源、有效期和样本量的证据，不按知名名字加分。上榜后1/2/5/10日等结果字段不进特征白名单或LLM上下文。

资金消费者必须取得实际交易日历、权威观察日、receipt索引及FUNDS_EVIDENCE_V1定义。money_sessions必须恰为观察日结束的最后三个交易所交易日；披露窗不能晚于观察日；资金、披露全集、席位行与同窗成交额须解析到匹配类型/证券/日期/字段/金额单位/来源口径的完整收据。不存在receipt、三条任意年代日期、未来披露、失败分页或假分母均不可评分。

令 **d=M_t-mean(M_(t-1),M_(t-2),M_(t-3))**，其中t-1/2/3严格按输入的交易所日历。每一天从截止前成功发布且来源完整的持久market_emotions中选择唯一最新版本；策略/配置/特征、主池定义、覆盖政策和coverage_type须匹配。保存三个document_id、hash、日期、发布时间、截止、选取理由；不按自然日、不把同日三个revision当三日。任一天缺失则d=NULL、UNKNOWN_DELTA并列缺失日期，当天M照实保留。数值回归M=54.640957446808514、前3日0/0/70得到d=31.307624113475182与FERMENT。普通UI不提供历史，服务从权威发布集合装配；未来修订不能改旧运行。情绪按原优先级：RETREAT（d<=-12且M<55，或D/N>=.008且昨日n>=5、m<=-.02）；COLD（M<30）；REPAIR（30<=M<50且d>=8）；HOT（M>=75）；FERMENT（M>=50且d>=0）；其余完整为SPLIT，缺共同输入为UNKNOWN。

FOCUS要求S>=70、V>=45、T>=50、必需数据完整、无阻断/观察专用事件且市场不为COLD/RETREAT/UNKNOWN。WATCH是S>=55而未达FOCUS，或因观察专用公告降级；较低完整分仍保留STRUCTURAL_MATCH。高板>=7只提示风险；严重异常波动公告默认观察专用，不宣称一定停牌。

同族按未舍入S/T/V降序、ID升序。跨族不当成一个成功概率总榜。首页观察最多20、重点最多5，仅为展示；按ONE_TO_TWO、TWO_TO_THREE、TREND_NM、HIGH_BOARD、REBOUND_SETUP逐族轮询，完整同族集合持续可查。

Asia/Shanghai交易日16:10 PRELIMINARY、20:30 EVENING revision、次日09:26 AUCTION_REVIEW。服务未运行时不承诺采集，错过不回填伪快照；真实迟到数据按实际cutoff生成新revision。采集准备文件不等于授权激活；默认DAILY消费者仅COMPLETE/EMPTY可以激活，失败/取消/不可用新输入保留旧active。不可用结果可独立保存诊断，不能自称已验证。

旧active可读不等于当天齐备。默认发榜只接受最近已收盘交易日输入（Asia/Shanghai 15:00与已验证交易所日历）；陈旧输入显示STALE、实际观察日、expected_observation_date和交易日龄。历史研究须显式选择历史日期或input_id。未来日历不可核验不猜测。

前夜冻结对象、同伴、公式、阈值、条件版本；次日只有可验证09:25撮合结果可评估：gap<=-.03对接力族标GAP_WEAK并FOCUS降WATCH；auction_amount/previous_amount<.005标参与弱；比例>=.02且gap>-.03标参与确认但不加分；gap>=.08或撮合价在涨停价提示ENTRY_UNCERTAIN；冻结主题同伴上涨占比<.30且中位gap<-.02标主题逻辑减弱。缺样本为NOT_EVALUATED，不用09:30行情冒充。原分和原同族位置不改，非接力族不强套所有降级逻辑。

## 7. 结果、研究与边界

接力三族主目标是下一交易所交易日收盘仍在有效涨停价；TREND_NM是未来3交易日至少一天收盘涨停；REBOUND_SETUP是未来3交易日至少一天可比收盘收复上一轮结束收盘，另报3日封板。结构成功不等于可成交或可获利。

新TradingSessionObservation按1/2/3/5/10交易日追加：观察日收盘参考变化、下一日开盘缺口与另一参考变化、MFE/MAE边界、收盘最大回撤、再涨停/跌停、成熟/停牌/缺价/退市/不可比状态。未知用NULL，未成熟不进分母。日线无法证明止盈与止损先后，次日开盘不代表成交；T+1下次日open-close参考变化不称可实现日内收益。旧7/30/90自然日记录不改单位。

服务运行时，15:00后独立观察阶段为已发布且仍未完成后验的候选run追加上述结果，R03不再因超过旧20日回看窗口静默丢弃失败/迟到样本。来源是当天已激活输入或成功保存的调度诊断输入；按run、评价交易日和input_id持久去重，新输入revision可追加新评价，原记录不覆盖。每轮最多提交5个观察job，容量不足留到下一轮；观察阶段失败不撤销已成功的市场捕获。没有当天注册输入时记录NO_CURRENT_SESSION_REGISTERED_INPUT；有输入但缺旧候选行情/状态时仍为NULL/CENSORED/UNAVAILABLE。市场采集是否取得这些股票数据须另行核验，观察调度不会据此编造缺失行情。停机期间不创建假捕获或倒填评价时间。未来20交易日计划是前瞻登记范围，不是删除迟到结果的到期日。

历史比较是逐日逐族全集、仅板数、仅ret5、仅amount_ratio和固定seed=20260905的100次随机；保存全部样本，不挑最好随机结果。按交易日聚类/区块重抽样登记次数seed，区分月/情绪/覆盖/profile。±10%敏感性只研究，不自动部署最佳收益参数；120/40/20、步长20的实验边界按实际label_end purge。实际运行范围和不可用原因在研究报告中另列，不用合成输入宣称252日真实验证。

PRICE_ONLY是明确诊断profile：固定B40/V50/G10，tier=DIAGNOSTIC，daily_score仍NULL、不FOCUS、不声称完整六分项。这项独立诊断权重在feature-contracts/decisions中明示，DAILY_V1权重没有更改。无账户、无Qlib或LLM不阻塞规则核心；真实关键市场数据缺失仍会诚实不可用。

未来20交易日计划只登记计划和服务依赖，未来结果不得补造。本轮把STRATEGY_IMPLEMENTED、DATA_READY、HISTORICAL_EVALUATION_EXECUTED、AUCTION_LIVE_VERIFICATION和MARKET_EDGE分开报告；**MARKET_EDGE=NOT_ESTABLISHED**。

## 8. R03摘要与不可变版本契约

`hash_contract_version=R03_RAW_EFFECTIVE_INPUT_V1`：raw_bundle_hash是原始完整注册输入归档的hash（等于input_id去除market_前缀），包含未来但未采用的归档记录；服务装配持久情绪后不改变这个原始标识。effective_input_hash是通过来源解析而实际采用的价格/身份/主题、资金族状态及有效资金依赖、精确三日情绪版本、交易日历/规则字段、配置、资格分区和主题成员截至语义的规范摘要。R03 input_hash明确是effective_input_hash的兼容别名。

`semantic_hash_version=R03_BUSINESS_SEMANTIC_V1`：摘要覆盖确定性业务结果（含完整候选/分项/风险/冻结条件、情绪/层级/展示与不可用原因），排除随机run/条件ID、生成/发布/派生as_of时间噪声，以及未采用历史revision的拒绝审计。实际来源时钟与原始证据依旧保存在原始/有效依赖中。相同决策输入重复运行可有不同run_id、发布时间及完整run_hash，但业务语义一致。只改未来未采用证据应只改变raw；改变实际采用资金或三日情绪应改变effective，若业务结果改变则semantic也改变。

没有这些新字段的旧运行读取时标LEGACY_R02_NARROW_INPUT_V1 / LEGACY_R02_SEMANTIC_V1，不重算或覆盖旧hash。默认初始化仅从原始YAOGU_V1.0.0活动版本另建新注册/激活；用户自定义活动版本保留，若仍是旧特征版本则要求显式新注册后发布。R03新增回归位于tests/r03_strategy，证据在相邻review/r03-realdata-closure，原外部RED文件始终保留。

独立文件历史研究使用同一三日选择器，但授权类型明确为RECONSTRUCTED_EVALUATION_ARTIFACT。每scope逐日保存研究artifact、来源代码版本、真实生成/发布时刻、基于已核验历史来源推导的research_available_as_of以及逐份hash；这不是当时真实发布的候选。生产服务拒绝这种研究授权，不把重建M混进前瞻三日历史。来源不完整仍无有效d，不能用冷启动生成正常历史表现。脚本scripts/yaogu_history_study.py不改生产数据库。

R03真实全池探针暴露旧身份查找的平方复杂度：原来每股每历史日遍历整个主表。现HistoricalSecurityIndex只检索对应证券的有效区间，来源业务日范围/历史证明范围每次查询仍检查；只为当前默认池、昨日默认池及研究池的依赖并集构造事件。资格未知/已排除身份仍完整保留分区和原因，不能以优化为名缩分母。OUTCOME_ONLY历史跟踪对象保留在原始输入供结果评价，但不加入当前官方主表分母。测试包含3000身份、66000日线与完整资格未知门禁，不以固定墙钟秒数换取PASS。
