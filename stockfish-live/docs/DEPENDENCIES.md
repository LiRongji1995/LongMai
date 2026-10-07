# 依赖与发布边界

Live 使用独立虚拟环境。运行直接依赖 Pydantic、PyArrow 和 DuckDB，测试依赖 pytest；版本范围见 pyproject.toml，本轮实际版本见 requirements-test.lock.txt。没有更改 Research requirements 或 Qlib 安装方式。

第三方包保留各自 dist-info 中的许可与通知：Pydantic/pytest 的本轮包元数据标记 MIT，PyArrow 标记 Apache-2.0；DuckDB 随 wheel 的许可证文本为准。锁文件不替代依赖许可证，也不为外部行情再分发授予权利。

本轮没有引入 StockFish、Qlib 或 MiroFish 的源码副本。语义参考路径登记在 REPO_INTEGRATION_AUDIT.md。根 THIRD_PARTY_NOTICES.md 与各组件 LICENSE 保留，MiroFish 为 AGPL-3.0-only，整个仓库不是单一 MIT 项目。

只发布源码、文档、明确标记的工程测试与可分享配置合同。不发布 .venv、.env、凭据、源行情、Parquet/DuckDB、原始供应商回执、真实研究输出或完整本机审阅包。fixture 生成结果也留在被忽略的私有运行目录。
