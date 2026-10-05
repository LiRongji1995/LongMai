> 历史归档：保留当时的说明、数字、失败和限制，仅调整文档相对链接。当前使用请看[当前状态](../../../../../CURRENT_STATUS.md)；原路径可在[归档索引](../../README.md)查找。

# R05 审阅包生成与核验

`scripts/r05_build_review_bundle.py` 从当前文件字节及本次真实执行目录生成审阅 ZIP，不执行 Git、不访问行情源、不重跑或改写历史测试。先完成正式回放、最终 Docker 验证和报告，再指定新的输出文件：

```powershell
.venv/Scripts/python.exe scripts/r05_build_review_bundle.py `
  --evidence-root ../review/r05-rectification `
  --replay-root ../review/r05-rectification/replay `
  --evidence-file ../review/r05-rectification/supply_audit.json `
  --output ../deliverables/StockRecommendation_R05_Rectification_Review.zip
```

最终报告、工作区/Git 核对记录可分别通过重复 `--evidence-file`、`--workspace-record` 添加。`--publish-root` 可追加发布目录当前源码独立字节清单。参数路径与包内容均可审阅，已存在的 ZIP 不覆盖。脚本按流式方式写入大体积派生回放文件，单文件默认上限 256 MiB，可通过 `--max-file-mib` 显式调整；没有静默截断。

包内保留完整 `stockfish_core`、当前业务 Python 源码、前端、配置/锁文件、迁移、部署脚本、全部 `tests` 及夹具、项目许可、原始规格/审阅文本和原样的本次 29/旧 8 反例源码。额外保留已出现收集失败的原始 MiroFish 测试及 locale 代码，并记录 `MiroFish/locales/languages.json` 在打包时是否存在。完整可选 MiroFish/BettaFish 依赖树不在该 R05 源码范围内；其实际收集失败的命令、退出码和日志仍在索引中。

`evidence-index.json` 扫描真实 `execution.json`、`result.json`、`run.json` 与 JUnit；`SOURCE_RUN` 记录命令、时间、退出码、环境、原有源码身份和日志 SHA-256。成功、失败、超时或缺字段记录分别呈现，声明的日志哈希若不符会标记 `MISMATCH`。JUnit 总数包含重复执行次数，不能当作去重后的测试数量，也不能作为投资效果验证。无实际执行字段的 `PASS` 字样不会变为成功执行。

只收录执行记录关联的脱敏 stdout/stderr/声明日志、公开来源文档及派生回放文件；`.env`、凭据、数据库、缓存、虚拟环境、模型、node_modules、全市场原始行情均不打包。`supply` 默认只进入 `private-input-references.json`，记录本地精确路径、原字节 SHA-256 和获取方式。用户需拥有这些相同字节的本地输入才能精确复算，因此审阅包明确不是自含的真实数据包；免费提供方以后返回的修订数据不保证哈希相同。

`source_manifest.json` 是打包时当前源码的原字节哈希；每次历史运行自身的源码身份仍单独保留，二者不能混同。ZIP 内 `manifest.json` 覆盖除自身以外每个条目，并保存脱敏前来源哈希与归档字节哈希。写包后逐条校验长度、SHA-256、CRC、重复路径和条目集合，只有实际核验通过才生成最终 ZIP 和相邻 `.verification.json`。脚本不会写入整体工程完成、HTTP 200、Docker 成功或固定测试次数等预设结论。
