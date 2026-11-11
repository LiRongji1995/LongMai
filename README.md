# 龙脉（LongMai）— 个人 A 股短线选股工具

项目原名 StockFish，2026-11-10 起改名为龙脉，与核心选股策略同名。命令仍是 `sf`，数据目录仍是 `../stockfish-data`。

本地、单人使用的沪深主板短线辅助工具：每日数据、盘前清单（主线龙头、连板梯队、观察、高波动）、
市场温度判断和命中率复盘。09:25 的集合竞价由人来看，工具不判断。它不连接券商、不下单：每一笔交易都由人决定。

```sh
bin/sf premarket     # 18:00 以后：生成下一交易日的清单和温度
bin/sf crowding      # 最近 N 个交易日的短线资金水温
bin/sf evaluate      # 选股与温度判断的命中率（不按买入价算盈亏）
bin/sf --help
```

- [使用说明](USER_GUIDE.md)：命令、输出、数据目录。
- [Agent 规则](AGENTS.md)：时点正确、缺失显式、结果可复现。
- [当前选股规格（V9，在 V7 基础上）](docs/STRATEGY_LONGMAI_V9.md)、[V7 规格](docs/STRATEGY_LONGMAI_V7.md)和[架构评审](docs/ARCHITECTURE_REVIEW_2026-11-10.md)。
- [更新日志](CHANGELOG.md)

安装：Python 3.12，`python3.12 -m venv ../stockfish-env && ../stockfish-env/bin/pip install -r stock-fish/requirements.txt`。
测试：`cd stock-fish && ../../stockfish-env/bin/python -m pytest tests/sf`。

旧的 R2/qlib 流水线和 V1–V6 策略代码保存在 git 标签 `legacy-r2`。
许可证：[THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md)。
