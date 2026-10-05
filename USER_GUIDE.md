# 使用说明

## 直接提出请求

在本项目的 Codex 对话中，你可以说：

- “读取最近一次保存名单，说明观察日期和资料缺口。”
- “解释这只股票为什么入选或没有入选。”
- “看看昨天关注的股票后来怎样了。”
- “比较两个保存名单的变化。”
- “导出这个运行的完整名单。”

日常先复用已有结果。需要补资料或重新计算时，核对实际实例、数据日期和预算后再执行。请求名单不自动授权改策略、发布代码或切换服务。

## 看结果时抓住五件事

| 要看什么 | 为什么 |
|---|---|
| 观察日和资料截止 | 判断是否仍是旧数据 |
| 全部数量与焦点数量 | 焦点是额外条件通过者，数量由规则决定 |
| 入选理由、缺失资料和反证 | 理解判断依据及什么变化会使判断失效 |
| 运行编号 run_id | 回到同一份原始名单、详情和导出 |
| 后续观察是否成熟 | 缺失或未成熟不能计作零收益 |

FOCUS 表示满足当前焦点条件，WATCH 表示继续观察，REJECTED 表示未通过，UNKNOWN 表示关键资料不足。这些是研究分类；R1 的 CORE_PASS 使用自己的研究口径，不能直接当作 V2 焦点。

## 网页和终端

网页读取既有 Python 服务的保存结果，地址以实际配置为准。[部署维护](DEPLOYMENT.md)说明如何核对实例。

Go 候选工具支持查询、解释、导出、比较和复盘。已有包与新候选包各有自己的身份和检查记录；使用你的明确配置和保存运行：

```sh
stockfish --version
stockfish --help
stockfish doctor --config CONFIG --format json
stockfish picks --config CONFIG --run-id RUN --format table
stockfish explain SECURITY --config CONFIG --run-id RUN --format markdown
```

CONFIG、RUN、SECURITY 都需替换为自己的实际值。上面的查询不启动研究或在线采集。系统主动核心为V2，终端本地配置另有策略偏好；init省略--strategy时默认保存R1偏好，要用V2请明确传--strategy v2。已有配置用doctor核对preferred_strategy。完整命令、导出格式和写入复盘的条件见[终端详细说明](stock-fish-go/delivery/USER_GUIDE.md)。

## 更新和失败时怎么办

需要新行情、后续标签或重新计算时明确提出更新请求。旧结果保留，失败和资料不足会单独说明；连续每日更新目前尚未验证可靠，查询也仍比原定目标慢，见[当前状态](CURRENT_STATUS.md)。

服务未运行时先核对已有部署；缺资料时仍可读取历史结果并注明日期。备份和恢复见[部署维护](DEPLOYMENT.md)。Codex 的调用与保存约定见[日常研究协议](stock-fish/docs/CODEX_DAILY_RESEARCH.md)。
