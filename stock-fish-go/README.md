# StockFish Go候选工具

当前候选工具提供保存结果查询、解释、导出、比较、基础复盘与受控研究。生产未切换；查询速度、真实暖计算和跨日验证仍有缺口，见[当前状态](../CURRENT_STATUS.md)。

- 使用：[终端详细说明](delivery/USER_GUIDE.md)
- 实际工程结果与真实限制：[交付报告](delivery/DELIVERY_REPORT.md)
- 用户操作步骤：[人工验收清单](delivery/MANUAL_ACCEPTANCE.md)，仍为NOT_RUN_BY_USER
- 设计与合同：[架构](ARCHITECTURE_V2.md)、[证据](EVIDENCE_CONTRACT.md)、[策略差分](STRATEGY_PARITY.md)、[HTTP兼容](HTTP_COMPATIBILITY.md)
- 数据维护：[迁移](MIGRATION.md)、[隔离恢复与回退](ISOLATED_LIFECYCLE.md)、[依赖许可](LICENSES.md)

## 构建

go.mod要求Go≥1.26，当前验证工具链为Go1.27.1；SQLite固定modernc.org/sqlite v1.60.1，CGO不作为运行前提。使用自己的明确工具链路径，从仓库根执行：

```sh
python3 stock-fish-go/tools/build.py --go "$GO" --command stockfish --output work/stockfish
python3 stock-fish-go/tools/package.py --go "$GO" --output /你的新候选目录
```

输出目录须全新。构建记录Go/runtime、业务、事实及固定worker身份；直接开发构建不继承正式包的验收。打包脚本把当前delivery三份说明映射为包内USER_GUIDE.md、MANUAL_ACCEPTANCE.md、SELFTEST_REPORT.md。

## 查询与本地研究

init绑定明确的已发布V2库并创建新配置。系统主动核心为V2；init省略--strategy时本地偏好默认为R1。使用V2时明确传--strategy v2，已有配置用doctor核对preferred_strategy。偏好不改变系统主策略。具体历史run可显式查询。picks/explain/diff、doctor和daily --dry-run通过只读路径读取，不联网、不初始化schema或取得写租约。

daily/research、review写操作、迁移与服务维护按各自命令合同取得租约并保存任务；同库遵守单写者，服务运行时使用相应HTTP入口。server提交后的等待中断不自动取消共享作业。配置、DB与run均由操作者明确选择，禁止用空库替代既有部署。

R1为RESEARCH_ONLY，production_promotion=DISABLED；CORE_PASS不等于V2焦点。研究默认300秒、单次最高1800秒，历史原始读取2GiB、RSS1GiB、Go堆软目标512MiB；具体任务还受累计预算和次数约束。旧任务额度不自动延续到新任务。

## 检查与验收

正式检查使用独立DB/state/artifact及已登记的独立oracle；缺oracle必须失败。Windows交叉编译不等于原生运行，fixture不证明真实在线或连续交易日稳定。根据改动运行必要检查，实际命令与失败见[交付报告](delivery/DELIVERY_REPORT.md)和[历史索引](../stock-fish/docs/archive/README.md)。

生产切换、在线采集、真实研究和新增预算分别授权。MARKET_EDGE=NOT_ESTABLISHED，初始参数尚未验证，许可见[第三方通知](../THIRD_PARTY_NOTICES.md)。
