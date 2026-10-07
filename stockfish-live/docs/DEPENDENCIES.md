# 依赖与发布边界

Live 使用独立 Python 3.12 虚拟环境，不修改 Research requirements 或 pyqlib 0.9.7 wheel 的运行方式。直接依赖包括 Pydantic、PyArrow、DuckDB、FastAPI、Uvicorn 和 wsproto（真实 WebSocket 运行后端）；测试使用 pytest、HTTPX。版本约束以 `pyproject.toml` 为准，实际安装组合由 `requirements-test.lock.txt` 和验证记录绑定。历史相似、baseline、评价指标使用 Python 标准库，不要求 NumPy、scikit-learn、LightGBM、GPU 或外部 LLM。

Dashboard 为本地 HTML/CSS/JavaScript，不要求 Node.js 构建、CDN 或第三方脚本。HTTP feed 适配器使用标准库 HTTP 客户端和显式 JSON 合同，不内置收费供应商 SDK、账户凭据或默认外部端点。

V2 流式状态与 V1 无状态参考实现使用同一独立依赖环境，特征路径由私有 research-config 的 forecast.feature_version 选择。新 demo 默认 V2，不要求 NumPy 或新增在线服务。wheel 包含 stockfish_live/frontend 资源；源码、安装包、数据集与 run 各自保留版本/hash，安装新版不把旧预测或 V1 训练材料改写成 V2。

第三方包保留其 dist-info 中的许可与通知；Pydantic、pytest、FastAPI、Uvicorn、HTTPX 等常用包的确切版本和许可以实际安装包为准，PyArrow 的发布许可遵循其 Apache-2.0 通知，DuckDB 以 wheel 许可证文本为准。锁文件不替代各依赖许可，也不为供应商数据使用或再分发授予权利。

本轮没有引入 StockFish、Qlib 或 MiroFish 源码副本。根 `THIRD_PARTY_NOTICES.md` 和各组件 LICENSE 保留，MiroFish 为 AGPL-3.0-only，整个仓库不是单一 MIT 项目。语义参考与隔离边界见 `REPO_INTEGRATION_AUDIT.md`；其中 Phase 0/1 的历史审计结论按原范围理解。

仅发布源码、文档、明确 TEST_FIXTURE 测试和可分享配置合同。`.venv`、真实 `.env`/key、行情 Parquet、原始供应商回执、研究输出、日志、私有数据库、模型及完整本机审阅包均留在私有目录。合成 fixture 生成物也不随源码发布。真实 feed 的授权、条款、publication 语义和再分发边界须独立核验。
