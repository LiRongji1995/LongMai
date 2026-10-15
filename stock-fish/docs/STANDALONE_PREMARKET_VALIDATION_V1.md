# 独立盘前入口 V1 检查记录

本轮为独立终端协调工程，不修改原策略公式、阈值、资格、默认主动V2、旧run或业务DB。入口、预算和数据边界见[使用规格](STANDALONE_PREMARKET_WORKFLOW_V1.md)。私人解释器、状态和原件路径不发布；以下 `PROJECT_PYTHON` 表示实际使用的既有固定项目依赖环境，未安装或升级依赖，未改Qlib为editable。

## 隔离工程检查

| 实际命令/范围 | 结果 |
|---|---|
| `PROJECT_PYTHON -B -m unittest discover -s tests/yaogu -p 'test_premarket*_v1.py' -v` | 62项，退出码0，51.407秒 |
| 最后收紧locator schema后，`PROJECT_PYTHON -B -m unittest tests.yaogu.test_premarket_native_core_v1 tests.yaogu.test_premarket_workflow_v1 -v` | 19项补验，退出码0，37.327秒；与前行有重叠，不计新增19项 |
| 最后修正报道采集状态后，`PROJECT_PYTHON -B -m unittest tests.yaogu.test_premarket_news_v1 tests.yaogu.test_premarket_workflow_v1 -v` | 20项，退出码0，29.882秒；与前行重叠，新增2个不同fixture |
| 全部最终文件冻结后，`PROJECT_PYTHON -B -m unittest discover -s tests/yaogu -p 'test_premarket*_v1.py' -q` | 最终66项，退出码0，55.709秒；包含缓存到核心的六字段locator和canonical/五导出同源校验；不与前轮数量相加 |
| `pytest -q tests/yaogu/test_industry_sina_context_v1.py tests/yaogu/test_theme_evolution_streaming_v2.py tests/yaogu/test_saved_r2_delivery_v3.py tests/yaogu/test_short_horizon_strength_r2.py` | 247项，退出码0，153.40秒 |
| `sh -n stock-fish/premarket`、启动器 `--help`、AST与JSON模板解析、`git diff --check` | 退出码0 |

pytest实际由同一固定环境启动，先加载该环境的pydantic，再将另一既有隔离环境中纯Python的pytest所在目录追加到 `sys.path`，并设置 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`；没有安装包或覆盖项目依赖。实际调用为 `pytest.main([...上表四文件...])`。新入口测试均为标准unittest，无需pytest。

全部新工程用独立临时目录和明确 `TEST_FIXTURE` 原件；真实SDK/网络/业务DB禁止在这些fixture中调用。检查包括一次原生R2、真实SOURCES_V2、完整67会话V2与全RAW市场分母、原诊断和真实formatter的组合；还检查source/owner/JSON伪造拒绝、原件和journal篡改、当前成员回看、完整原入选保留、3亿元含等号及未知金额、低额姓名/代码不泄漏、原风险三值、跨18点门禁、失败不重试、全阶段预算预飞、资源计量、真实子进程失败/超时/退出后后代清理和最终状态。

最终报道组合fixture使用真实collector及模拟HTTP原件，完整声明采样得到workflow `COMPLETE`，经济归因和EVENT仍为UNKNOWN；离线、缺正文业务日期和未取得文章继续未知。采集完成与投资事件确认没有合并为同一状态。

开发中的失败记录未追认为通过：fixture未建立独占job目录导致的失败由修正fixture准备解决；并行修改代码触发的实现epoch拒绝保留，冻结后另行补验。没有删除样本、扩大skip、降低覆盖或弱化原断言。上述247项为相关旧模块回归，不代表全仓测试。

## 真实资料与终端烟测

- 使用本次真实2026-10-15完整RAW行情的一份原result/RAW/receipt，通过新缓存导入、命中和成熟原件校验；保留5226条原行，实际返回读取24,921,459字节，SDK/HTTP请求均0，未重新计算core/theme或打开DB。此烟测在最终构造策略一致性守卫之前完成，最后新增守卫另经fixture；不声称已验证所有旧版本归档导入。
- 此真实缓存的六字段locator另经最终核心绑定器直接验证，持久目录身份与原link7FP保持，退出0；无新增网络、normalizer、selector或题材计算。旧五字段接口不靠删除目录基线来兼容。
- 对财联社、证券时报两个真实公开主页进行有界烟测：实际URLLIB GET调用2，文章GET0，返回读取1,150,006字节。该次使用早期news候选，原失败/响应及版本范围保留；最终Content-Length/预算上限修订以fixture补验，不重复联网。没有把两主页成功当作当日新闻、经济归因或EVENT完整。
- 已执行冷缓存 `--offline`，实际子进程退出3并由父进程回收、封存失败，未发起网络请求；缺来源未伪装为EMPTY。启动器使用已有解释器的环境变量委派 `--help` 实际退出0。

真实网络的全新冷启动、完整暖启动到67日全量题材、Windows原生运行、跨多个交易日持续可靠性尚未验收。此前其他私有脚本的真实R2结果和67日附件失败保持各自身份，不转记为新入口在线端到端通过。此工程没有执行全历史回测、生产部署切换、下单、收费服务或投资有效性验证。参数尚未验证，`MARKET_EDGE=NOT_ESTABLISHED`；DAILY_V1单独报告。
