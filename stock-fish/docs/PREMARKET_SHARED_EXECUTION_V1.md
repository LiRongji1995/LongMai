# 完整盘前准入与共享计算候选 V1

本轮将同次共享题材计算接入完整原生盘前流程，并补齐执行前的全流程缺口检查。默认仍使用原成熟题材执行器，`--engine shared`才选择共享候选；小样本全流程曾测得回退，不能把解码计数下降当成加速证据。它不改变 R2 公式、日期化资格、原价格基准或 SAM V2 默认策略，也不把封存查询耗时换算成新清单提速。真实下一交易日新清单仍须补齐来源并通过原预算；工程检查、真实数据状态与投资验证分别记录。

## 入口

在已登记项目解释器下使用下列命令。Codex负责执行，用户不需要打开网页、另建数据库或配置LLM凭据。

```sh
python scripts/codex_daily_v2.py premarket-plan
python scripts/codex_daily_v2.py premarket \
  --observation-date YYYY-MM-DD --target-session YYYY-MM-DD \
  --request-key premarket-OBSERVATION-target-TARGET
```

`premarket-plan`只读：核原交易日历、原写入配置、实际容量和逐阶段来源范围。诊断使用单独受限的只读来源检查额度，不能将该额度用于采集；输出同时列出原写入预算。当前行情缺失时仍继续列出官方证券目录、25日核心行情、其余42日题材行情、完整复权范围、资格补查、题材目录/成员和可选新闻的成本，不在第一个缺口处把后续阶段省略。

索引声明、已认证原件、范围未知、失败/未完成请求分别标注。旧证券池只能给条件性估算，不能替代当日精确范围；新闻按原最大GET次数预留，题材成员规模未知时拒绝准入。全部原件齐备且预算通过的路径可以准入，`execution_admitted=true`仍不等于已经生成清单或保证运行资源足够，实际运行继续受进程监督和原预算限制。

`premarket`复用固定登记根、原解释器与配置，默认 `--engine original`；共享性能实验须显式 `--engine shared`。engine纳入执行身份，换engine不能绕过同请求的已完成/失败/不明记录。计划返回 `execution_admitted=false` 时退出4、`BLOCKED_BEFORE_COLLECTION`，不启动worker、不采集、不创建提交中的请求回执。已开始的请求由原共享运行协调器持久记录；相同请求复用，失败/不明状态不重试。即使源码或配置版本改变，同一request-key也不能绕过旧请求；跨交易日复用key会被拒绝。原已完成作业继续按原版本查询，不能冒充本次新采集。

V2的 `show/publish/audit/plan/outcomes` 等已有命令委托原V1实现；旧 `codex_daily.py`、`codex_chat.py` 及所有旧 `stockfish_core` 文件保持原字节。保存成果快速查询仍优先 `codex_daily.py show`，并保留原观察日、目标日、资料截止与最后完整认证时间。[原查询协议](SYSTEM_EXECUTION_PIPELINE_V1.md)继续适用。

## 实际完整执行

新 `codex_premarket_shared_v1.py` 在独立进程中连接原采集、真实NativeCore、新闻、题材来源、所选题材执行器、原展示和完整终验。原/共享两种模式均有完整入口，新完成控制记录明确的 `execution_engine`。新完成控制使用 `CODEX_PREMARKET_SHARED_WORKFLOW_V1`，配置仍采用原 `STANDALONE_PREMARKET_WORKFLOW_V1`，不覆盖旧run。

显式共享调用通过本次函数的局部绑定选择共享题材实现；没有使用仅允许TEST_FIXTURE的适配器注入通道。共享适配器核对真实SOURCES_V2 issuer及其原reader，再将行业reader的相同receipt/path范围接入同次67会话投影。临时facts、price-journal、manifest和指标文件全部留在本次job输出限额内；结束后scope失效，保存JSON不能恢复来源能力。

原核心、日期、输入/父来源、完整67会话顺序、缺日UNKNOWN、借出对象迟变、源码及原件SHA/七字段物理身份继续校验。共享facts不取代原RAW证据。展示只接受本次准确的新题材版本，原选择集合、排序及正式导出不改。完整流程失败不得标为整体完成；原独立核心如已核准仍按其自己的产物和边界说明。

父进程启动全新 `-I -B` 子进程，使用不存在的字节码查找路径；原worker预算、实际进程树RSS、输出、控制台及进程组清理保持。终验读取耗时也计入监督总时间。聊天入口的首次执行和回执验证共用同一个父读预算及从action开始的合作式时限，监督读取量与外围验证读取量累计；重复读取另用本次只读验证预算。准入扫描是独立只读诊断，不计为策略执行提速。该合作式外层检查不宣称能在任意阻塞系统调用期间硬中断。

新源码闭包从明确入口递归解析静态scripts导入，并与原core闭包一起记录；新增无关脚本不会自动进入本次闭包。历史文件不搬迁、不重压缩、不删除，旧源物理身份不替换。没有生产服务、自动任务、策略版本或私有预算切换。

## 真实环境及数据阻塞

本轮真实登记入口 `premarket-plan` 退出2，原因 `REGISTERED_RUNTIME_PHYSICAL_IDENTITY_CHANGED`。只读核对发现登记的设备号为16777220，当前为16777224；Python、state、reports、源码和两个存储根共6项绑定均不符，路径及inode仍相同。没有改写登记或历史SHA/物理指纹，也没有跳过检查。本次真实日历、来源和归档尚未重新认证，不能把上轮验收当成本次成功。

此前最后完整认证的日历指向观察日2026-10-26、目标2026-10-27。后续只读索引盘点显示缺当天2份行情/主表、题材额外42会话中的82份历史行情/主表，以及27日官方目录；这些是原日期和索引范围说明，并非本次来源重认。缓存索引复权仅覆盖至23日。实际缓存196,285,859字节，原写入上限67,108,864字节；完整源和导出还需分别满足原监督/阶段读取限额，不能只增加调用次数便宣布可运行。

## 数据瓶颈与增量边界

现有BaoStock复权接口逐证券调用。原完整窗口为1990年至观察日；R2虽只消费25个交易日价格，原可比性规则要求同一完整因子快照覆盖证券左锚及每个消费日，并识别历史有效日修订。仅有旧完整窗口和新增尾窗不能证明旧有效日未被修订，因此不能直接拼接后授予新日价格能力。

缩短请求起始日期也不会减少逐证券SDK调用。以旧索引中3,194只证券、每批50只估算，64批仍需3,322次SDK；这只是旧范围条件性估算，不是今日精确请求量。改用其他数据来源还需要新的来源、价格基准和权限验收，本轮没有进行该切换。

真正的跨日采集降量需要可验证的批量因子快照/历史修订证明，或明确批准原完整范围所需的资源。单纯提高缓存上限或只补当天两份行情，不能解决整个新清单依赖。原64MiB写入缓存、64次SDK及240秒限制没有自动扩大。

## 本轮验证记录

工程夹具明确标记TEST_FIXTURE，使用独立DB/state/artifact和固定daily/review Python 3.12；不含真实投资回测。发布检出最终组合 **83 passed，162.86秒，退出0**；共享原生题材的独立完整检查 **18 passed，239.42秒，退出0**。没有因失败删除断言、扩大skip或放宽预算。

发布检出同一进程内、同一67会话合成夹具，顺序执行采集适配、原生核心、题材、展示及内部终验，得到：

| 路径 | 耗时 | 本次job输出 | 题材market/master解码 |
|---|---:|---:|---:|
| 原工作流 | 11.942秒 | 1,775,610字节 | 402次 |
| 新入口默认original | 12.833秒 | 1,783,904字节 | 402次 |
| 显式shared候选 | 15.269秒 | 2,459,495字节 | 134次 |

原核心自身还有相同的10次解码，未混入题材计数。三路径原run_id、五份正式导出字节及题材数值、来源引用与展示选择一致。共享候选相对原工作流耗时增加27.86%、输出增加38.52%，所以默认继续原执行器；这次没有达到新名单提速目标。样本规模、一次顺序测量、进程内测试和外层真实网络/监督开销分别有边界，不能换算为全市场性能。C编码优化仍对每次完整值做哈希，且保留初末原件、借用对象和物理身份检查。

实际组合命令（解释器/临时路径以环境变量或占位展示；私有审阅记录保存完整实际命令）：

```sh
"$STOCKFISH_PYTHON" -I -B -X pycache_prefix=FRESH_ABSENT_PATH scripts/run_isolated_tests.py \
  -q -s --basetemp TEST_FIXTURE_ABSOLUTE_DIR \
  tests/unit/test_codex_daily_entry_v2.py \
  tests/unit/test_codex_premarket_preflight_v1.py \
  tests/yaogu/test_premarket_shared_workflow_v1.py \
  tests/yaogu/test_daily_session_pipeline_v1.py \
  tests/yaogu/test_premarket_workflow_v1.py
```

独立题材检查用同一解释器及另一个不存在的字节码目录运行 `scripts/run_isolated_tests.py -q tests/yaogu/test_codex_native_theme_shared_v1.py`。覆盖概念/行业完整及缺日数值对照、真实issuer、源与facts/journal/manifest/结果同SHA换inode、嵌套receipt/借出row迟变、跨日门禁、实际owned子进程失败、来源/输出/时间/读取预算、同请求防隐式重试和默认engine。动态审阅发现的锁外准入竞态与回执落盘后时限遗漏均已修复并回归。

真实新 `premarket-plan` 和未修改的原 `codex_daily.py show` 均因登记设备号不符退出2，未触发采集或新作业。没有伪造新日清单、改写原物理指纹、调整预算或切换服务。没有执行全仓回归、真实新采集、真实新清单生成、默认300秒后验或投资有效性验证。参数尚未验证，`MARKET_EDGE=NOT_ESTABLISHED`。
