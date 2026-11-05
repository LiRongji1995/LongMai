# StockFish：个人本机研究

用于沪深主板股票数据更新、量价研究、候选筛选、原因与未知项报告、历史查询和后验分析。不连接券商，不自动下单。

主要入口是 Codex 或命令行：

```sh
python stock-fish/scripts/codex_daily_v2.py --help
```

保留已确认的 PV 研究、主动 STAGE_AWARE_MOMENTUM_V2、冻结 DAILY_V1 与原日常 R2 流程。策略参数尚未验证，`MARKET_EDGE=NOT_ESTABLISHED`；数据不齐时如实输出 UNKNOWN，不补数或凑名单。

- [使用说明](USER_GUIDE.md)：计划、研究、历史结果、导出与后验。
- [部署维护](DEPLOYMENT.md)：依赖、原登记、诊断和备份恢复。
- [当前策略](CURRENT_STRATEGY.md)：保留策略及其边界。
- [当前状态](CURRENT_STATUS.md)：本次验证及未验证范围。
- [工程基线](ENGINEERING_BASELINE.md)：隔离测试与发布流程。
- [本次精简记录](CHANGELOG.md)：删除范围、统计口径和历史定位。

只有 Python 研究实现；网页、Go/Live、LLM、Qlib 源码镜像、容器和旧安装工具已删除。旧源码与操作记录可从精简前 Git 提交恢复；私人数据与旧请求账本仍原位保留。原版源码缺失时可查看已有封存结果，不能把当前树当成历史来源认证。

源码与第三方依赖各有许可，见 [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md)。
