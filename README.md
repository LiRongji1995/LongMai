# StockFish + Qlib Quant System

面向 Windows 的 A 股量化研究系统：保留 StockFish Web/API/SSE 外壳，整合 Microsoft Qlib 源码及 CSI300 Alpha158 / LightGBM 原生推理适配。当前由 `stockfish_core` 统一处理可信数据快照、量化、确定性推荐与风控、持久任务及不可变历史。

**当前默认研究模式。** 下载模型不等于完成登记或通过样本外验证；历史作者模型登记后保持 `RESEARCH_ONLY`。当前架构、输入契约、离线测试和使用边界见 [工程基线](stock-fish/ENGINEERING_BASELINE.md)。下文的原生推理结果属于核心更新前的历史验收，不能替代当前推荐流程的验收。

## 项目结构

```text
stock-fish/                      StockFish 源码及 Windows 兼容修改
  stockfish_core/                 可信数据、量化、推荐、风控、持久任务与历史
  ENGINEERING_BASELINE.md         当前核心架构、开发测试和运行边界
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

已有部署与新检出目录相互独立；发布代码不会迁移或重启原有服务。新检出目录不要覆盖现有运行目录。默认端口为 8000，同一时刻只能启动一个使用此端口的实例。

`/api/health` 分别报告应用、数据、量化、模型登记、研究、LLM、历史存储和任务组件。缺少已登记模型、可信快照或 LLM transport 时会明确报告不可用。SQLite 保存任务及历史；进程中断后任务恢复为 `INTERRUPTED`，显式重试创建新 ID。分析归档模块已补齐。

## 历史原生推理验收

2026-09-05 在 Windows 11 上对核心更新前的原生推理流程完成以下检查，预测日为 **2026-09-04**：

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

原生 Alpha158 适配范围是 CSI300 特征及成员历史，不等于完整全 A 股 RAW 数据或可交易性覆盖。当前核心包含训练/重拟合、回测和评估实现，其工程正确性不等于投资样本外验证通过。历史模型仍为研究用途；LLM transport、可信输入和部署备份需在实际环境配置。MiroFish、旧 Bot 等保持实验隔离，Linux 容器未完成运行验收。Qlib main 源码扩展构建需要额外的 C++ 编译工具，运行时继续使用 `pyqlib==0.9.7` wheel。

详细配置、数据范围和已知限制见 [DEPLOYMENT.md](DEPLOYMENT.md)。源码来源、嵌入组件和许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)；本整合仓库包含不同许可证的组件。
