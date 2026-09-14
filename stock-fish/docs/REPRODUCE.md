# R16-R1 复现

从 Review Bundle 解压出的 `final-source` 开始。包根 `REVIEW_METADATA.json`、`SOURCE_BINDING.json`、`SHA256SUMS`、`INCLUSION_MANIFEST.json` 与 `packaging-smoke` 记录实际提交、工具链、字节和退出码。四个已存在的上游 GRU/LSTM 示例模型权重按 `git/EXPLICIT_SOURCE_EXCLUSIONS.json` 明确排除，保留各自 Git blob/SHA/路径；应用源码与依赖闭包完整，完整未过滤 archive 只在包外引用。源码无 `.git` 时工具应输出 UNKNOWN/null 的 Git 身份；通过包的 commit/archive binding 核对，不能继承外层仓库身份。

## 环境

本次 Windows 验收用项目 Python 3.12.10 虚拟环境、Go 1.24.3、Node 22.18.0（内置 node:sqlite/dbstat）、psutil 7.2.2。确切依赖以 `stock-fish/requirements*.txt`、`pyproject.toml`（如有）、`go-core/go.mod` 和 bundle 的 pip/toolchain raw 为准。保留 pyqlib 0.9.7 wheel，不切换相邻 Qlib main editable。安装可用公开依赖后先 `python -m pip check`；不要复制私有 .env、credentials、虚拟环境或生产 DB 到源码。

```powershell
$Python = (Get-Command python).Source # 使用已安装上述依赖的 Python 3.12 环境
$Evidence = Join-Path $env:TEMP ('r16-r1-reproduce-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $Evidence | Out-Null
& $Python stock-fish/scripts/run_core_tests.py tests/r05_product/test_r16_r1_contract_regressions.py tests/r05_product/test_r16_r1_production_oracle.py tests/r05_product/test_r16_r1_history.py tests/r05_product/test_r16_r1_e2e.py tests/r05_product/test_r16_r1_dependency_work.py tests/r05_product/test_r16_r1_live_incremental.py tests/r05_product/test_r16_r1_prospective_audit.py -q
& $Python stock-fish/scripts/r16_production_oracle.py --output-dir "$Evidence/oracle"
& $Python stock-fish/scripts/r16_service_path_trace.py --fixture --mode data-ready --output-dir "$Evidence/e2e" --budget-seconds 60
Push-Location go-core
go build -o "$Evidence/stockfish-go-core.exe" ./cmd/stockfish-go-core
go test ./...
go vet ./...
Pop-Location
```

上述命令均预期退出 0。oracle 默认读取已冻结的 input/expected 文件，禁止为了通过而重建 golden。`BASIS_UNSPECIFIED_NEGATIVE` 的事实比较必须产生预期 mismatch，只有检测到该不支持域并拒绝注入，负例包装器才退出 0。正常 unexpected mismatch 非零。`test_r16_r1_contract_regressions.py` 直接导入真实模块，覆盖 schema、comparator 正反例和 mutation；Go duplicate member CLI 预期原始非零，Gate A 保存该失败。

已有 snapshot（包含 `feature_evidence_manifest_v1.json`、`feature_evidence_facts_v1.jsonl`）可直接执行：

```powershell
& $Python stock-fish/scripts/r16_validate_contract_schema.py --snapshot-dir $Snapshot --output-json "$Evidence/schema.json"
& $Python stock-fish/scripts/r16_compare_contract_snapshots.py --left $Left --right $Right --output-json "$Evidence/comparison.json"
```

合法匹配退出 0；非法、重复、缺/多行或 provenance/unit/value mismatch 退出 2。oracle 输出中的 `python_production_facts.jsonl` / `go_facts.jsonl` 与各自 manifest 文件保留无损 provenance，具体文件名及 fixture 原始路径以运行目录为准。真实抽取样本使用 `--input <real-extract.json>`，不可改标 TEST_FIXTURE。

## 全市场所需外部资产

小包不包含 GB 生产 DB 或整套 provider cache。需要原冻结 T/旧坏 T+1 candidate_inputs、全部引用的 history manifests/partitions/bar/security rows、原 source receipts、active config，以及 `raw_asset_inventory.json` 逐文件 SHA 对应的本地 capture 资产。具体 IDs、绝对路径、checksum、覆盖与原始 clocks 见构建结果、source-selection、base proof 和 inclusion 外部引用清单。完整 source closure 复制到隔离 DB，不写 production。

```powershell
& $Python stock-fish/scripts/r16_r1_build_corrected_history.py --source-db $ProductionReadOnlyDB --inventory $Inventory --output "$Evidence/corrected" --budget-seconds 600
$Build = Get-Content "$Evidence/corrected/build-result.json" -Raw | ConvertFrom-Json -DateKind String # PowerShell 7.5+ 保留 ISO 时点字符串
& $Python stock-fish/scripts/r16_history_stability_audit.py --db $Build.target_db --input-id-t $Build.input_t_corrected --input-id-t-plus-1 $Build.input_t_plus_1_corrected --cutoff-t $Build.common_information_cutoff --cutoff-t-plus-1 $Build.common_information_cutoff --checkpoint-json "$Evidence/history.checkpoint.json" --output-json "$Evidence/history.json" --output-md "$Evidence/history.md"
& $Python stock-fish/scripts/r16_r1_base_basis_proof.py --db $Build.target_db --history-ref $Build.history_t_ref --inventory $Inventory --cutoff $Build.common_information_cutoff --output "$Evidence/base-basis" --budget-seconds 1200
& $Python stock-fish/scripts/r16_r1_readiness.py --db $Build.target_db --input-id $Build.input_t_plus_1_corrected --cutoff $Build.common_information_cutoff --output "$Evidence/readiness.json" --checkpoint-json "$Evidence/readiness.checkpoint.json" --budget-seconds 1200
```

`r16_r1_base_basis_proof.py` 的旧 77bcb67 结果仅验证每行自身旧 refs 的 RAW/factor 算术，未证明跨 complete capture 的修订兼容。保留旧报告和 603083.SH 真实反例；typed review 修复冻结后，先运行 `test_r16_r1_factor_revision_review.py`，再通过上面的原 corrected builder 入口重构；其 opt-in hook 实际调用 `factor_revision_review.py`，无需另造 OHLC 或新 factor 数学规则。复核 normalization report 的 `factor_revision_review`：`reviews` 中旧/新行与 hash、null comparable/basis、真实 revision clocks；`outside_factor_scope` 的独立 provider；`retained_inline_originals` 的原 clocks。然后补跑冲突范围/来源/clocks 证明和受影响 history/PIT/readiness，再测这些新 input IDs 的 E2E/cache/benchmark。不把旧 PASS/旧性能数字搬入新验收。

真实因子修订的独立消费者证明使用以下入口。它只投影冲突证券的原始字段，保留原 calendar/receipts/history ref，调用原 canonical resolver 和生产 `_bar`、return、MA，输出每次实际读取键；它不运行全市场资格或排名。唯一候选和单次视图各限 1000 行，多截止时间重复验证累计限 6000 次，60 秒硬预算。完整 corrected history/readiness 审计仍须另跑。

```powershell
& $Python stock-fish/scripts/r16_r1_factor_review_witness.py --build-result "$Evidence/e2e-offline/corrected-build/build-result.json" --output "$Evidence/factor-review-proof" --budget-seconds 60 --max-rows 1000 --max-visits 6000
```

不要使用 `--instrument-limit` 或 `--partition-limit` 宣称全量。history audit 原 invariant FAIL 返回非零；旧坏负例加 `--expect-invariant-failure`，只有真实 invariant 失败且 canonical 复核通过才包装为退出 0，raw 数据结果仍 FAIL。每个全量 audit 用 supervisor 设置初始 1200 秒预算，stdout/stderr/checkpoint 保留；超时保留而非删除重跑。

```powershell
& $Python stock-fish/scripts/r16_r1_supervise.py --output "$Evidence/history-process" --registry "$Evidence/active-job.json" --budget-seconds 1200 --label HISTORY_FULL -- $Python stock-fish/scripts/r16_history_stability_audit.py --db $Build.target_db --input-id-t $Build.input_t_corrected --input-id-t-plus-1 $Build.input_t_plus_1_corrected --cutoff-t $Build.common_information_cutoff --cutoff-t-plus-1 $Build.common_information_cutoff --output-json "$Evidence/history-full.json" --output-md "$Evidence/history-full.md"
& $Python stock-fish/scripts/r16_service_path_trace.py --db $Build.target_db --input-id $Build.input_t_plus_1_corrected --mode data-ready --output-dir "$Evidence/e2e-real" --budget-seconds 900
& $Python stock-fish/scripts/r16_r1_prospective_audit.py --repo-root . --db $ProductionReadOnlyDB --output "$Evidence/prospective" --port 8001 --budget-seconds 300
```

live-attempt 与 offline-replay 的原始命令见对应 run.json；offline 不能替代 live。既有 live-attempt 成功只证明 provider 边界，不能把缺少编排说成 provider 失败。完整有界 live 路径需在最终源码冻结后另起一次新请求：

```powershell
& $Python stock-fish/scripts/r16_r1_supervise.py --output "$Evidence/live-incremental-process" --registry "$Evidence/live-incremental-registry.json" --label LIVE_INCREMENTAL --budget-seconds 960 --input-manifest $Inventory -- $Python stock-fish/scripts/r16_service_path_trace.py --mode live-incremental --db $Build.target_db --input-id $Build.input_t_plus_1_corrected --inventory $Inventory --output-dir "$Evidence/live-incremental" --budget-seconds 900
```

该命令只接受已验证且已完成 factor review 的非 official 隔离 corrected 基底，选择真实最近收市日并请求一次 baostock market_daily，再经原 normalizer/history/input 注册进入真实 Daily Primary API、job 和结果序列化。factor review hook 同时启用，但正常增量必须幂等；旧分区出现任何变化都拒绝并要求先独立 rectification。修复 writer/存储增长单独报告，不以放宽守卫把它归为正常日增量。旧 master 的 prior-input reference scope 不自动证明新日资格；严格保留模式在写 row 前降为 UNKNOWN，保留缺价交易日、factor 窗口外 UNKNOWN 和 RECON。eligible=0 仍保存真实 API/持久化结果，但必须标非空 feature 未触达；正例与已有非空 offline/data-ready 证据单列。具体边界见 [R16_R1_LIVE_INCREMENTAL.md](R16_R1_LIVE_INCREMENTAL.md)。

保存 HTTP attempts、SDK operations、调用次数与实际原始 receipt；底层总网络请求不可测为 null/NOT_MEASURED。按 trace 的精确 document_id/job_id/run_id 从隔离库只读回读持久化 bytes/hash 与 RESULT_READY，失败不得写 VERIFIED。provider 实际失败时保留非零与原始错误，不以全历史重试替代。

prospective 修复后的只读审计必须独立重跑：最新 score 分 signal run/epoch/revision group，核对 document/record 绑定，并保留 SQL JSON missing/null presence。父重算 epoch 的 NO_SAVED_SCORE 不得被其他 epoch 的记录填满；不重新计算或修改 official score。旧审计输出保留，saved plan/epoch 仍不证明 live scheduler 已载入或正在执行。

## 性能与最终检查

用 `r16_r1_source_binding.py` 校验 R16 Git archive/逐文件 SHA 后，将其 root 与 binding 传给 `r16_r1_benchmark_suite.py --corrected-build ... --baseline-source-root ... --baseline-binding ... --output ... --registry ...`。可选 `--old-bad-source-db` 增加独立压力项。每版 cold 一次、warm 三次；真实增量从 Day-T cache 新副本起步。不要重跑到同一 output，也不要把已处理 T+1 的 store 当首次增量。

`r16_r1_profile_source_work.py --before-source-directory <包内preoptimization两源码目录> --output <新目录>` 重跑精确 token/selected/dependency 字节对照的小 profile。它与完整 baseline/final benchmark 是不同实验。

`r16_r1_memory_batch_probe.py --source-root . --instruments 300 --output <新目录> --cprofile` 测量完整 feature ensure 分批内存控制。另一次独立进程加 `--implementation-file <包内pre-memory-fix-source/derived_feature_store.py>`，其余参数及共享源码不变；各自用 supervisor 记录 RSS。逐行缓存哈希排除 created_at，比较 runtime features、effective dependencies 和 PIT 拒绝记录；本实验是明确的 TEST_FIXTURE，不代替全市场 Strategy。baseline benchmark 如终止失败，保留原始失败与跳过的依赖样本，独立 final 测量继续；缺失的跨版本比率/等价保持 null/NOT_VERIFIED，整体不升级 PASS。

最终完整工程验收命令包括 `git diff --check`、`python -m compileall -q stock-fish/stockfish_core stock-fish/scripts`、`python stock-fish/scripts/run_core_tests.py tests -q`、`go build/test/vet`、`node --check stock-fish/scripts/r16_r1_sqlite_pages.cjs` 和 `pip check`。测试日志记录实际通过/skip/未执行范围，不能沿用旧数字。source freeze 后任何执行相关源码变动都需要新 commit 与受影响复跑。

最终 bundle 通过实际解压 checksum 与解压源码小型 contract/oracle/Go smoke 验证。打包脚本接受明确 inclusion spec，保留 JSONL、profile、失败日志；排除 DB/credentials/cache 并将必要大资产列为外部引用。最终 ZIP 的 SHA-256 位于外部侧车，避免自引用。
