# 本机部署与维护

只有 Python 3.12 命令行运行方式，不启动 HTTP 服务、容器、定时平台或模型平台。已有环境与登记优先复用；本次不自动替换运行解释器、迁库或切换服务。

## 安装与登记

新安装可在选定的私有位置创建一个环境：

```sh
python3.12 -m venv PRIVATE_ENV
PRIVATE_ENV/bin/python -m pip install -r stock-fish/requirements.txt
PRIVATE_ENV/bin/python -m pip check
```

运行直依赖只有 Pydantic、PyYAML、Requests、psutil、AKShare、BaoStock。SDK 自身的传递依赖由包管理器解析。工程验证另安装 `requirements-dev.txt`；不向原环境安装 Qlib、Web、LLM 或旧开发依赖。

唯一入口为 `stock-fish/scripts/codex_daily_v2.py`。已有登记不重建：`doctor` 查看当前解释器和依赖，`status` 核登记及容量，`operations` 查看原请求。使用自定义登记根时，固定 `STOCKFISH_CHAT_STATE` 或 `--registry-root`，不能每次聊天换目录。

新登记可参考 [codex-chat-premarket.example.json](stock-fish/config/codex-chat-premarket.example.json) 和 [premarket.example.json](stock-fish/config/premarket.example.json)，填写自己的绝对路径和明确有限预算，再执行 `register --config PRIVATE_PROFILE.json`。模板不是现有预算授权，不能直接覆盖旧配置。旧恢复登记继续读取原 recovery epoch、祖先账本及锁，不新建恢复执行树。

源码与数据分别管理。`source_root` 为明确的代码位置，`python` 为现有解释器，state/reports/storage 为私有原目录。不要硬编码他人的目录，不提交 `.env` 或凭据。旧来源清单要求原版代码时，可从 Git 历史或私有备份恢复精确字节，不能修改历史清单来接受当前实现。

## 备份与恢复

备份显式指定数据、RAW、报告、来源、请求登记/operations、HMAC 密钥和所需其他私有根，目标必须是新目录，且在源目录之外：

```sh
python stock-fish/scripts/codex_daily_v2.py backup \
  --artifact-root PRIVATE_STATE --artifact-root PRIVATE_REPORTS \
  --artifact-root PRIVATE_REGISTRY --destination NEW_BACKUP
python stock-fish/scripts/codex_daily_v2.py verify-backup --backup NEW_BACKUP
python stock-fish/scripts/codex_daily_v2.py restore --backup NEW_BACKUP --destination NEW_RESTORE
```

如使用业务 SQLite，再加 `--database EXISTING_DB`。SQLite 使用包含已提交 WAL 的在线备份，不能只复制活动主库。运行锁与内容校验保护一致性；活动任务或不稳定原件会拒绝备份。恢复只写新目录，核验 SHA 和长度，不覆盖原数据，不重写旧绝对引用、不自动把恢复件接管为运行实例。

## 验证与故障

```sh
PRIVATE_TEST_ENV/bin/python -I -B stock-fish/scripts/run_isolated_tests.py \
  -q --basetemp NEW_TEST_FIXTURE_DIRECTORY tests
```

测试目录须独立于真实 DB/state/artifact。CI 只运行这一套保留的离线测试，不采集或部署。默认环境缺包、资料不齐、预算不足、旧失败或源字节改变时，应先查看原错误和状态；不能换入口/key扩大预算或自动重试。

文件跨运行按 SHA-256 和长度校验；必要 OS 锁、进程回收、事务和原子写仍保留。详见 [自用校验规则](stock-fish/docs/SELF_USE_FILE_INTEGRITY_V1.md)。本机代码是否被真实登记采用、测试和数据验证范围见 [CURRENT_STATUS](CURRENT_STATUS.md)。
