# StockFish + Qlib Quant System

面向 Windows 的 A 股量化研究系统：整合 StockFish Web/API、Microsoft Qlib 源码及 CSI300 Alpha158 / LightGBM 原生推理适配。当前重点是使用已发布模型读取真实行情、生成股票评分，并在页面返回候选列表。

**已验证核心流程：Web → CSI300 数据 → 300 × 158 特征 → 模型推理 → 300 股评分 → Top 20 回填。** 训练、微调及 LLM 调用尚未验收。

## 项目结构

```text
stock-fish/                      StockFish 源码及 Windows 兼容修改
  .env.windows.example           无密钥的 Windows 配置样例
  qlib-zh/native_predict.py      CSI300 Alpha158 原生推理入口
qlib/                            Microsoft Qlib main 源码快照
Start-StockFish.ps1               启动并检查服务
Stop-StockFish.ps1                校验进程身份后停止服务
Logs-StockFish.ps1                查看日志
requirements-windows.lock.txt    已验证 Windows 环境依赖快照
docs/verification/               2026-09-04 预测日的脱敏验收结果
```

运行时使用 `stock-fish/.venv` 中的 **pyqlib 0.9.7 Windows wheel**；旁边的 `qlib/` 为源码参考和后续开发保留，不会自动替代运行时包。模型、完整行情数据、虚拟环境、日志和真实 `.env` 均不随仓库发布。上游随源码提供的小型示例数据保留。

## 安装与运行

需要 Windows x64、Python 3.12、PowerShell 7、Git，以及可下载依赖和发布资产的网络。克隆到一个空目录，按 [部署指南](DEPLOYMENT.md) 创建虚拟环境、填写路径并下载模型和数据。

```powershell
git clone https://github.com/LiRongji1995/stockfish-qlib-quant-system.git
Set-Location .\stockfish-qlib-quant-system
```

配置完成后，在仓库根目录运行：

```powershell
pwsh -NoProfile -File .\Start-StockFish.ps1
pwsh -NoProfile -File .\Logs-StockFish.ps1 -Follow
pwsh -NoProfile -File .\Stop-StockFish.ps1
```

打开 [StockFish Web](http://127.0.0.1:8000/)。前后端共用 Flask 服务，无需 Node 构建或外部数据库。三个脚本的 `-Root` 默认为脚本所在目录，也可显式指定部署根目录。

原本的 `D:\StockRecommendation` 部署与发布用检出目录相互独立；发布代码不会迁移或重启原有服务。新检出目录不要覆盖该运行目录。默认端口同为 8000，同一时刻只能启动一个使用此端口的实例。

## 实际验收

2026-09-05 在 Windows 11 上完成以下检查，预测日为 **2026-09-04**：

| 项目 | 结果 |
|---|---|
| Python / pyqlib / LightGBM | 3.12.14 / 0.9.7 / 4.7.0 |
| Web、配置、健康及模型接口 | HTTP 200；最终健康报告错误数为 0 |
| 真实行情 | Qlib 与 Baostock 均读取到 10 个交易日行情 |
| CSI300 Alpha158 | 300 只成分股，158 个特征 |
| 已发布模型推理 | 约 96 秒完成；特征表达式、顺序及维度核验通过 |
| 页面交互 | 从 Web 发起推理并回填 20 只股票；控制台无错误或警告 |
| 进程管理 | 启动、重复启动、停止、重启已验证 |

模型原始排名前五名：

| 排名 | 股票代码 | Score |
|---:|---|---:|
| 1 | 688256 | 0.432629 |
| 2 | 600015 | 0.390850 |
| 3 | 300274 | 0.388415 |
| 4 | 601600 | 0.377634 |
| 5 | 000807 | 0.348765 |

Score 用于该模型内的相对排序，不代表收益率。Strategy B 会进一步筛选候选，因此其结果可能与原始排名不同。结果是本次新执行的推理输出，没有重新训练模型。

- [完整 300 股排名](docs/verification/CSI300-ranking-2026-09-04.csv)
- [执行清单与模型指纹](docs/verification/inference-execution.json)

## 当前边界

原生路径仅验收 CSI300 Alpha158 已发布 LightGBM 模型推理。训练/微调仍依赖上游 Docker 路径；本机 Docker Desktop 未能启动。Qlib main 源码扩展构建需要额外的 C++ 编译工具，当前使用已验证 wheel。LLM、MiroFish、Bot 等需要各自配置，未进行真实调用验收。上游缺失的分析归档模块仍待补齐。

详细配置、数据范围和已知限制见 [DEPLOYMENT.md](DEPLOYMENT.md)。源码来源、嵌入组件和许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)；本整合仓库包含不同许可证的组件。
