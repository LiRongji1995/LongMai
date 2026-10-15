# 独立盘前入口 V1 检查记录

本轮为独立终端协调工程，不修改原策略公式、阈值、资格、默认主动V2、旧run或业务DB。入口、预算和数据边界见[使用规格](STANDALONE_PREMARKET_WORKFLOW_V1.md)。私人解释器、状态和原件路径不发布；以下 `PROJECT_PYTHON` 表示实际使用的既有固定项目依赖环境，未安装或升级依赖，未改Qlib为editable。

## 隔离工程检查

| 实际命令/范围 | 结果 |
|---|---|
| `PROJECT_PYTHON -B -m unittest discover -s tests/yaogu -p 'test_premarket*_v1.py' -v` | 62项，退出码0，51.407秒 |
| 最后收紧locator schema后，`PROJECT_PYTHON -B -m unittest tests.yaogu.test_premarket_native_core_v1 tests.yaogu.test_premarket_workflow_v1 -v` | 19项补验，退出码0，37.327秒；与前行有重叠，不计新增19项 |
| 最后修正报道采集状态后，`PROJECT_PYTHON -B -m unittest tests.yaogu.test_premarket_news_v1 tests.yaogu.test_premarket_workflow_v1 -v` | 20项，退出码0，29.882秒；与前行重叠，新增2个不同fixture |
| 首轮文件冻结后，`PROJECT_PYTHON -B -m unittest discover -s tests/yaogu -p 'test_premarket*_v1.py' -q` | 当轮66项，退出码0，55.709秒；包含缓存到核心的六字段locator和canonical/五导出同源校验；不与前轮数量相加 |
| 后续67项合并检查的初次执行，使用下列仓库根命令 | 退出码1，101.331秒；固定的current请求测试日期为上海10月15日，而fixture实际捕获已跨上海10月16日，原会话守卫正确拒绝 |
| 仅修正三个current请求fixture的业务会话为实际上海日期后，定向缓存测试及67项合并检查 | 缓存13项退出码0，23.037秒；合并67项退出码0，128.158秒；日期化历史行情测试与全部生产守卫不变 |
| 新增未知/多关系文本展示fixture后，使用下列仓库根命令 | 69项，退出码0，121.224秒 |
| `PYTHONPATH=stock-fish PROJECT_PYTHON -B -m unittest discover -s stock-fish/tests/yaogu -p 'test_premarket*.py' -v` | 目录扫描和阶段边界修复及四个新增资源fixture后，73项，退出码0，76.363秒；不与此前各轮相加，也不作为真实资料吞吐基准 |
| `pytest -q tests/yaogu/test_industry_sina_context_v1.py tests/yaogu/test_theme_evolution_streaming_v2.py tests/yaogu/test_saved_r2_delivery_v3.py tests/yaogu/test_short_horizon_strength_r2.py` | 247项，退出码0，153.40秒 |
| `sh -n stock-fish/premarket`、启动器 `--help`、AST与JSON模板解析、`git diff --check` | 退出码0 |

新增67、69、73项合并检查均在仓库根按上表73项的完整命令执行；此前66项命令保留当轮工作目录和参数形式。

pytest实际由同一固定环境启动，先加载该环境的pydantic，再将另一既有隔离环境中纯Python的pytest所在目录追加到 `sys.path`，并设置 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`；没有安装包或覆盖项目依赖。实际调用为 `pytest.main([...上表四文件...])`。新入口测试均为标准unittest，无需pytest。

全部新工程用独立临时目录和明确 `TEST_FIXTURE` 原件；真实SDK/网络/业务DB禁止在这些fixture中调用。检查包括一次原生R2、真实SOURCES_V2、完整67会话V2与全RAW市场分母、原诊断和真实formatter的组合；还检查source/owner/JSON伪造拒绝、原件和journal篡改、当前成员回看、完整原入选保留、3亿元含等号及未知金额、低额姓名/代码不泄漏、原风险三值、跨18点门禁、失败不重试、全阶段预算预飞、资源计量、真实子进程失败/超时/退出后后代清理和最终状态。

最终报道组合fixture使用真实collector及模拟HTTP原件，完整声明采样得到workflow `COMPLETE`，经济归因和EVENT仍为UNKNOWN；离线、缺正文业务日期和未取得文章继续未知。采集完成与投资事件确认没有合并为同一状态。

开发中的失败记录未追认为通过：fixture未建立独占job目录导致的失败由修正fixture准备解决；并行修改代码触发的实现epoch拒绝保留，冻结后另行补验。没有删除样本、扩大skip、降低覆盖或弱化原断言。上述247项为相关旧模块回归，不代表全仓测试。

## 本次工程修复

- 为成熟V2市场stream接入真实 `_source_terminal_validator=validate_sources`，消费活核心owner、真实来源发行器及完整原件终验；不以普通JSON、恒真回调或旧附件代替来源能力，原67会话与数学保持。
- 文本中未取得可用原生诊断时明确写“原生诊断关联未知”；多条关联分别标出原篮子名称、阶段及风险，不再输出无法区分来源的阶段串。完整原入选和关系不改，低额姓名/代码仍不得在展示中重新出现。
- 资源目录计数改用 `os.scandir` / `DirEntry`，每项只作一次不跟随子链接的stat；显式根目录别名仍计入其目标，子文件链接、子目录链接及悬空链接不计入，也不递归，普通文件增长完整计入。文件系统检查的节流基准放在本次全扫描完成后，避免慢扫描使每次小块读取都重复遍历整树；阶段入口、出口另强制完整检查。原读字节、时间、内存、SDK/HTTP和失败粘性守卫不变，主异常保留且退出检查失败不能被随后忽略。16项runtime定向测试退出码0，0.343秒；已包含于73项合并检查。

以上为工程修复和隔离fixture结果；本次真实暖缓存执行的独立终态记录如下，不以工程测试或中间核心替代真实验收。

## 真实资料与终端烟测

- 使用本次真实2026-10-15完整RAW行情的一份原result/RAW/receipt，通过新缓存导入、命中和成熟原件校验；保留5226条原行，实际返回读取24,921,459字节，SDK/HTTP请求均0，未重新计算core/theme或打开DB。此烟测在最终构造策略一致性守卫之前完成，最后新增守卫另经fixture；不声称已验证所有旧版本归档导入。
- 此真实缓存的六字段locator另经最终核心绑定器直接验证，持久目录身份与原link7FP保持，退出0；无新增网络、normalizer、selector或题材计算。旧五字段接口不靠删除目录基线来兼容。
- 对财联社、证券时报两个真实公开主页进行有界烟测：实际URLLIB GET调用2，文章GET0，返回读取1,150,006字节。该次使用早期news候选，原失败/响应及版本范围保留；最终Content-Length/预算上限修订以fixture补验，不重复联网。没有把两主页成功当作当日新闻、经济归因或EVENT完整。
- 已执行冷缓存 `--offline`，实际子进程退出3并由父进程回收、封存失败，未发起网络请求；缺来源未伪装为EMPTY。启动器使用已有解释器的环境变量委派 `--help` 实际退出0。

## 真实暖缓存端到端验收

在仓库根实际执行：

```sh
./stock-fish/premarket --theme-read-gib 12 --max-requests 768 --max-seconds 0 --retry-failed
```

本次复用重新验真的真实缓存，另为11个原失败范围各新增一次明确授权的attempt，并取得6次直接报道HTTP请求；不是全新冷启动，也没有自动重试循环。终端外部退出码为2，父进程确认worker退出码2且已回收，最终于 `2026-10-15T18:21:00.910160Z` 封存为 `COMPLETED_WITH_UNKNOWNS`，实际总耗时4092.403秒。该状态表示本次登记流程完成并保留研究未知，不表示所有来源或经济方向已完整。

| 实际范围 | 封存结果 |
|---|---|
| 日期与同一核心 | 观察日2026-10-15，下一交易日2026-10-16；run_id为 `strategy_experiment_cfd3f30d0e1c2408bed5ebfd`；原选择截止 `2026-10-15T17:15:36.686438Z`，题材情境截止 `2026-10-15T17:26:06.125835Z`，两者分别保留 |
| 核心与展示 | 完整原25只WATCH、FOCUS0，canonical run、原input及五份正式导出保持；成交额≥3亿元展示17只，8只低额仅在展示层省略，方向未知13只，不将其补成已确认主线；展示实际生成于上海时间 `2026-10-16T02:19:13.182855+08:00` |
| 真实67会话 | 完整全RAW市场stream及V2联合日志审计完成，25个当前来源篮子各保留67行，共1675行；真实来源终验callback完成，未压缩缺失会话或裁掉市场分母 |
| 当前来源范围 | 54个潜在相关范围中25个通过原生核验并建模、28个未知、1个资格类别排除；当前成员回看不证明历史成员或完整经济题材覆盖 |
| 观察日原生分类 | 25个建模篮子中20个 `MIXED_PENDING`、5个 `WEAKENING`；V4四类主动诊断均0符合原规则，不能据此称全市场没有主线；轴缺失数0也不覆盖上述28个来源未知 |
| 最终认证 | `core_verified`、`implementation_epoch_verified`、`source_terminal_binding_verified` 均为true，终验登记来源引用1342份；普通保存JSON没有恢复来源能力 |

既有 `premarket-verify` 命令另实际退出0，`archived_bytes_verified=true`、`currentness_recomputed=false`、`source_authority_restored=false`。独立终态轻核退出0，实际读取18,595,830字节，仅核元数据、五份正式导出和三份展示：SHA/7FP初终、25只完整值与顺序、17只展示原值及3亿元过滤、8只低额禁显和未知13只末组均通过；未重读RAW/journal或重新计算模型。

本次实际返回读取分别为：

| 计量域 | 字节 |
|---|---:|
| worker采集 | 2,648,228,125 |
| worker核心 | 1,439,123,486 |
| worker题材 | 11,405,183,679 |
| worker展示 | 0 |
| 父进程独立验收 | 951,045,823 |

worker自身RSS峰值2,048,339,968字节，父进程观测的进程树RSS峰值1,963,995,136字节；两者为不同采样口径，不相加。新增逻辑请求17个，报告HTTP实际36、预占上界710，已报告SDK调用0；pending0、资源失败数组为空。缓存原先取得所耗的请求不转记为本次零成本，返回读取范围也不是全部内核或提供方子进程I/O。实际值均在本次登记profile内，未扩大限额或删减认证。

此前两次尝试均由操作者有序中断：父命令退出130、worker退出-15、已回收且cleanup错误为null；原控制和缓存保留，不追认为本次完成，也不把中断写成行情来源失败。此前247项旧模块回归与最终73项隔离检查各属原记录，不与本次真实执行合并成测试数量或投资样本。

真实网络的全新冷启动、Windows原生运行、跨多个交易日持续可靠性仍未验收。报道正文业务日期、经济归因、EVENT、历史PIT及完整经济题材覆盖按各自证据保留未知；`COMPLETED_WITH_UNKNOWNS` 不能替代这些认证。此前其他私有脚本的真实结果和失败保持各自身份。此工程没有执行全历史回测、生产部署切换、下单、收费服务或投资有效性验证。参数尚未验证，`MARKET_EDGE=NOT_ESTABLISHED`；DAILY_V1单独报告。
