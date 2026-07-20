# Phase 1 Round 1B 原生 LEAN 基础设施最终验收报告

状态：**PASS WITH LIMITATIONS**

## 本轮技术结果与边界

本轮技术执行结果为 **PASS WITH LIMITATIONS**。已在最终 Git HEAD 的全新
clean-source 目录完成 Python 3.12 检查、原生 C# deterministic build 与真实
Native LEAN Run C；正式 Review Bundle 已生成并提交独立审阅。在外部独立审阅
通过前，Round 1B 尚未冻结；Round 1C 尚未启动。

最终 HEAD、`origin/main`、分支和工作区状态均由 Native `EVIDENCE.json` 与
Bundle 内 Git 证据记录：分支为 `main`、`HEAD == origin/main`、工作区干净，
Round Base 始终为 `ec7bba61c6d190c7e6624a467c4a45a6911c46e1`。

Run C 使用项目提交的 SYNTH fixture、项目自有 C# smoke algorithm，以及固定到
`0269115d3cfbf691c7a0b7cfcc9ed412cafb91f6` 的外部官方 LEAN 源码。launcher
exit code 为 0，成功加载有效数据并处理 22 个数据点；恰有 2 笔 Filled 模拟订单，
顺序为 buy → sell，最终持仓为 0，最终现金和组合价值均为 977.20。Golden orders
与 Golden statistics 的语义比较均为 PASS，deterministic build identity 为 PASS。

## 身份与可复现性证据

- Python：3.12.x；.NET SDK：`10.0.302`
- LEAN commit：`0269115d3cfbf691c7a0b7cfcc9ed412cafb91f6`
- 算法源文件、csproj、fixture ZIP、fixture 解压内容、map、factor、manifest、
  config、Golden orders、Golden statistics 与 deterministic DLL 的 SHA-256 均记录
  于 Native `EVIDENCE.json`，并与 `algorithm-build-identity.json` 一致。
- deterministic DLL SHA-256：
  `9ca5b943c67e4ac0c41ae0fba16b54483a4859aa84e821284618ca58da40a5f8`
- 未连接 Alpaca；未启用 Paper Trading 或 Live Trading；未发送真实订单；未验证
  任何盈利能力。

## 最终检查

| 检查 | 结果 |
| --- | --- |
| Python 3.12 clean-source：Ruff、format、mypy、pytest、build、CLI、git diff --check | PASS |
| Native C# deterministic build | PASS WITH LIMITATIONS |
| Native LEAN Run C | PASS WITH LIMITATIONS |
| Filled 模拟订单 | PASS（恰好 2 笔；buy → sell） |
| 最终持仓 | PASS（0） |
| Golden orders | PASS |
| Golden statistics | PASS |
| deterministic build identity | PASS |
| fixture、map、factor、manifest 与 config 身份 | PASS |
| Native EVIDENCE 强制字段与 SHA-256 格式 | PASS |
| Bundle candidate 内容验证 | PASS（仅打包前候选内容） |
| 最终 ZIP 的外部 `validate_bundle` 验证 | PASS |

## 限制与后续动作

1. LEAN 上游依赖在原生 restore/build 时仍产生安全公告。
2. LEAN 对可选参考数据（包括 interest rates）的请求会产生非致命警告。
3. 本轮验证原生 C# 基础设施，不验证 LEAN Python algorithm。
4. Alpaca 尚未连接；Paper Trading 与 Live Trading 未获批准且保持禁用。
5. 本轮不构成盈利能力或投资建议验证。

Review Bundle：**REQUIRED**。Bundle 内嵌的 `BUNDLE_VALIDATION_RESULT.md`
明确仅描述打包前 candidate 内容；最终 ZIP 的独立外部验证结果、真实路径与
SHA-256 在交付记录中单独报告。`FULL_DIFF.patch` 完整保留；其中单独的通用盘符
测试字面量不是具体本机路径。Round 1C 尚未启动。
