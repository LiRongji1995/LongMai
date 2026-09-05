# 第三方来源与许可证

本仓库整合下列上游源码及局部 Windows 兼容修改。各组件保留其版权、许可证和通知；不将整个源码集合声明为单一 MIT 许可。上游源码快照使用普通目录存储，不要求初始化 Git 子模块。

| 目录 / 组件 | 来源与版本 | 许可 |
|---|---|---|
| `stock-fish/`，下述嵌入组件除外 | [freenowill/stock-fish](https://github.com/freenowill/stock-fish)，`master`，`f1feea00238ba39804551d144e5a9ac7ee88f443` | MIT；见 [原许可证](stock-fish/LICENSE)，Copyright (c) 2026 freenowill |
| `qlib/` | [microsoft/qlib](https://github.com/microsoft/qlib)，`main`，`79633dd9506ea689e5400dea0197717b5b3d74b7` | MIT；见 [原许可证](qlib/LICENSE)，Copyright (c) Microsoft Corporation |
| `stock-fish/MiroFish/` | StockFish 已嵌入的 [666ghj/MiroFish](https://github.com/666ghj/MiroFish) 源码 | AGPL-3.0；按原声明未扩展为 or-later（SPDX：AGPL-3.0-only）。见 [组件许可证](stock-fish/MiroFish/LICENSE) |

MiroFish 的原嵌入副本在 `backend/pyproject.toml` 声明 AGPL-3.0，但缺少许可证正文；本仓库补入 [MiroFish 官方 LICENSE](https://github.com/666ghj/MiroFish/blob/main/LICENSE)。保留 MiroFish 源码和原 Docker 路径不表示该服务已部署或验收。

## 本仓库的兼容修改

StockFish 在原快照基础上增加 Windows 原生 CSI300 推理入口，支持配置数据路径，修复评分 SSE 输出及 Quote 对象健康检查，并将缺失分析归档模块的导入延迟到归档分支。根目录增加进程管理脚本、Windows 配置样例、依赖快照和部署说明。Microsoft Qlib 主体源码保留上游内容；实际运行使用 PyPI 的 `pyqlib==0.9.7` wheel。

发布时排除本地 `.env`、虚拟环境、下载模型、行情数据缓存、日志和运行状态，亦排除 StockFish 上游已生成的 `batch_results/*.json`、`data/sentiment_history/600519.json` 与 `.claude` 工具规则。上游跟踪的小型示例 CSV / pickle 数据仍随源码保留，其原有许可及说明继续适用。

## 外部模型、数据和依赖

- 已验证模型来自 [StockFish CSI300 Alpha158 发布](https://github.com/freenowill/stock-fish/releases/tag/csi300-alpha158)，实际目录名为 `2026-06-21-csi300-alpha158`。模型下载包不随仓库发布。
- 已验证 Qlib binary 行情来自第三方 [chenditc/investment_data 的 2026-09-04 发布](https://github.com/chenditc/investment_data/releases/tag/2026-09-04)。完整行情下载包和解压数据不随仓库发布；数据来源不等同于 Microsoft 官方数据。
- `docs/verification/` 仅保存本次执行清单与模型输出评分，用于复核 2026-09-04 预测日的运行结果。它们不是模型权重或原始行情包。
- Python 及其他依赖各自适用其许可证。`requirements-windows.lock.txt` 是已安装版本快照，不替代依赖项目的版权和许可文本。

模型和行情的下载、使用或再分发应遵循其发布源对应条款；本文件不为第三方资产另行授予权利。
