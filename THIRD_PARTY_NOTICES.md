# 第三方来源与许可证

本仓库整合下列上游源码及局部 Windows 兼容修改。各组件保留其版权、许可证和通知；不将整个源码集合声明为单一 MIT 许可。上游源码快照使用普通目录存储，不要求初始化 Git 子模块。

| 目录 / 组件 | 来源与版本 | 许可 |
|---|---|---|
| `stock-fish/`，下述嵌入组件除外 | [freenowill/stock-fish](https://github.com/freenowill/stock-fish)，`master`，`f1feea00238ba39804551d144e5a9ac7ee88f443` | MIT；见 [原许可证](stock-fish/LICENSE)，Copyright (c) 2026 freenowill |
| `qlib/` | [microsoft/qlib](https://github.com/microsoft/qlib)，`main`，`79633dd9506ea689e5400dea0197717b5b3d74b7` | MIT；见 [原许可证](qlib/LICENSE)，Copyright (c) Microsoft Corporation |
| `stock-fish/MiroFish/` | StockFish 已嵌入的 [666ghj/MiroFish](https://github.com/666ghj/MiroFish) 源码 | AGPL-3.0；按原声明未扩展为 or-later（SPDX：AGPL-3.0-only）。见 [组件许可证](stock-fish/MiroFish/LICENSE) |

MiroFish 的原嵌入副本在 `backend/pyproject.toml` 声明 AGPL-3.0，但缺少许可证正文；本仓库补入 [MiroFish 官方 LICENSE](https://github.com/666ghj/MiroFish/blob/main/LICENSE)。保留 MiroFish 源码和原 Docker 路径不表示该服务已部署或验收。

## 本仓库的兼容修改

StockFish 在原快照基础上增加 Windows 原生 CSI300 推理入口、可配置数据路径及兼容修复，随后新增 `stockfish_core`，统一可信数据、模型登记、量化、确定性推荐与风控、持久任务和不可变历史；缺失的分析归档模块已补齐。当前源码同步自本地 StockFish 提交 `e7e8d51`，该提交标识本地修改版本，不是上游版本或运行验收声明。架构及边界见 [工程基线](stock-fish/ENGINEERING_BASELINE.md)。根目录增加进程管理脚本、Windows 配置样例、依赖快照和部署说明。Microsoft Qlib 主体源码保留上游内容；实际运行使用 PyPI 的 `pyqlib==0.9.7` wheel。

发布时排除本地 `.env`、虚拟环境、下载模型、行情数据缓存、日志和运行状态，亦排除 StockFish 上游已生成的 `batch_results/*.json`、`data/sentiment_history/600519.json` 与 `.claude` 工具规则。上游跟踪的小型示例 CSV / pickle 数据仍随源码保留，其原有许可及说明继续适用。

## 外部模型、数据和依赖

- 历史原生推理验收所用模型来自 [StockFish CSI300 Alpha158 发布](https://github.com/freenowill/stock-fish/releases/tag/csi300-alpha158)，实际目录名为 `2026-06-21-csi300-alpha158`。模型下载包不随仓库发布；当前核心需显式登记，登记后保持 `RESEARCH_ONLY`。
- 历史原生推理验收所用 Qlib binary 行情来自第三方 [chenditc/investment_data 的 2026-09-04 发布](https://github.com/chenditc/investment_data/releases/tag/2026-09-04)。完整行情下载包和解压数据不随仓库发布；数据来源不等同于 Microsoft 官方数据。
- `docs/verification/` 仅保存历史执行清单与模型输出评分，用于复核 2026-09-04 预测日的旧流程运行结果。它们不是模型权重或原始行情包，也不代表当前核心的部署验收。
- Python 及其他依赖各自适用其许可证。`requirements-windows.lock.txt` 是已安装版本快照，不替代依赖项目的版权和许可文本。

模型和行情的下载、使用或再分发应遵循其发布源对应条款；本文件不为第三方资产另行授予权利。

## 独立 Go R1 迁移模块

`stock-fish-go/` 保留既有 StockFish 规则的迁移来源追踪，不改变上述组件的版权或许可。新增依赖固定为 `modernc.org/sqlite v1.36.3`（BSD-3-Clause）及其锁定传递依赖；版本、官方许可来源和随附许可副本见 [Go 依赖许可清单](stock-fish-go/LICENSES.md)。Go 工具链和本机研究二进制不随源码分发。该模块目前仅提供未完成验收的迁移诊断，不代表生产切换。
