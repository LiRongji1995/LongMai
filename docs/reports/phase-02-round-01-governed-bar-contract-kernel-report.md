# Phase 2 Round 01 — Governed Bar Contract Runtime Kernel（Synthetic Only）

## Round 状态

**PASS WITH LIMITATIONS**

## Remediation closure evidence

This remediation preserved the uncommitted implementation and stayed within the original 22-file allowlist: 10 named allowlist files plus exactly 12 JSON files under `tests/fixtures/phase2_bar_contract/`. The repository remained on `main` at base `899a6e365c1b452ba1a72f994e9ffdf4097a5d77`, with `origin/main` equal to that base before delivery work.

Toolchain evidence was produced outside the repository under the remediation evidence directory. A fresh Python 3.12.10 virtual environment was created outside the repository and installed only with `python -m pip install -e ".[dev]"`; this used package-index network access and is classified as `TOOLCHAIN PACKAGE NETWORK USED`. No market-data, broker, Alpaca, or other trading API network was used.

Ruff 0.16.1 was run from the same venv against both the Git-archive Base snapshot and the Candidate tree. `python -m ruff check . --output-format=concise` passed for both. Git-derived targeted checks covered all 8 changed Python files; both `ruff check` and `ruff format --check` passed. No Base-inherited Ruff violation remained under this toolchain, and no Candidate Ruff violation remained after changing the five string enums to `StrEnum` and applying the allowlisted format-only correction.

The final venv gates were: full Ruff PASS; full Ruff format PASS; `mypy src scripts` PASS; build PASS; offline CLI status PASS with Paper/Live false; `git diff --check` PASS. The first exact `python -m pytest` invocation exposed that the declared `[dev]` extras omit `tzdata`; it failed only because Windows `zoneinfo` could not load `America/New_York`. The same exact pytest command then passed in the same venv with `PYTHONTZPATH` pointed to an existing external IANA tzdata directory: 125 passed and coverage 91.28%. This external timezone-data dependency is the remaining toolchain limitation; `pyproject.toml` was not expanded because it is outside the allowlist.

The external build directory contains only the wheel and sdist, with SHA-256 recorded in the remediation evidence. Determinism A/B passed in fresh external `A` and `B` directories: data version, canonical manifest bytes and SHA-256, quality result, quarantine reason, output file list, and every output SHA-256 were equal. All fixtures and generated outputs are synthetic/project-owned only.

Candidate commit: the single commit containing this report and the allowlisted implementation; its exact immutable SHA is recorded in the final delivery manifest and handoff. The official generator is frozen and has no truthful Phase 2 source-change evidence mode (`document` rejects `src/` changes and `native` requires unrelated LEAN evidence). The final outer Review ZIP therefore distinguishes the generator output, if accepted by its supported mode, from this supplementary Phase 2 evidence. `Review Bundle: REQUIRED` because this is formal material code and clean-source acceptance work.

本报告记录本 Round 的候选实现、实际验证结果和未完成 gate。当前工作未
创建 candidate commit，也未宣称本 Round 已冻结或 Phase 2 真实数据基础已
完成。

## 基线与唯一目标

- Round ID：`phase-02-round-01-governed-bar-contract-kernel`
- Phase：Phase 2
- branch：`main`
- Base commit：`899a6e365c1b452ba1a72f994e9ffdf4097a5d77`
- `origin/main`：`899a6e365c1b452ba1a72f994e9ffdf4097a5d77`
- 起始 ahead/behind：`0/0`
- 起始工作树：clean
- 唯一目标：实现供应商无关、Synthetic-only 的 Governed Daily Bar Contract
  Runtime Kernel。

起始基线命令全部满足任务要求：HEAD 等于规定 Base，HEAD 等于
`origin/main`，分支为 `main`，ahead/behind 为 `0/0`，暂存区与工作树均为
空。

## 已实现范围

本 Round 只实现 Bar runtime、canonical identity、quality gate、manifest 和
项目自有 synthetic fixture：

- `Bar` 的全部合同字段、严格 enum、UTC `Z` 时间、IANA timezone、
  `event_time == bar_end_time`、独立 `availability_time`、
  `ingestion_time` 和 `as_of_time`。
- 仅接受 `resolution=1d`、`session=REGULAR`、`price_basis=RAW` 和
  `adjustment_method_version=null`。
- JSON 边界价格只能是 Decimal string；拒绝 JSON number、scientific
  notation、非有限数、负值、空字符串、float 和 bool 伪装的 volume。
- 合成 fixture 使用固定不透明 `instrument-synth-*` ID；不从 symbol 生成
  正式 instrument ID。
- record identity、同 payload 幂等 replay、record conflict、duplicate
  interval 和 out-of-order fail-closed 验证。
- 两阶段 identity：先生成不含派生 lineage 字段的 `data_version`，再生成
  不含 `data_manifest_sha256` 的 canonical manifest 和 manifest SHA-256。
- quality reason code 仅为 `MISSING`、`DUPLICATE`、`LATE`、`STALE`、
  `INVALID`；quality policy 必须显式提供 `policy_id`，本 Round 只允许
  `SYNTHETIC_TEST_ONLY` policy。
- `MISSING` 只进入 dataset/interval quality result，不伪造 Bar；
  `QUARANTINED`、`REJECTED` 和含 `INVALID` 的记录不进入 downstream。
- quarantine root 由调用者传入；evidence 包含 data version、record ID、
  quality status/issues、确定性 reason、source fixture SHA-256 和候选
  commit placeholder；quarantine 写失败 fail closed。

未实现供应商 adapter、真实 instrument registry、生产 calendar、Tick、
Corporate Action、Feature、Adjusted pipeline、数据库、策略、Broker、
Alpaca、Paper 或 Live。

## 修改文件

仅涉及任务 allowlist：

- `src/quant_system/data/models.py`
- `src/quant_system/data/canonical.py`
- `src/quant_system/data/quality.py`
- `src/quant_system/data/manifest.py`
- `src/quant_system/data/__init__.py`
- `tests/unit/test_data_models.py`
- `tests/unit/test_data_quality.py`
- `tests/unit/test_data_manifest.py`
- `tests/fixtures/phase2_bar_contract/` 下 12 个 JSON fixture
- `docs/DOCUMENTATION_MAP.md`
- `docs/reports/phase-02-round-01-governed-bar-contract-kernel-report.md`

没有修改 `DATA_CONTRACTS.md`、Phase 1 文件、README、CURRENT_PHASE、
`pyproject.toml`、Bundle generator、Bundle tests、LEAN 文件或 Phase 1
fixture/Golden。

## Synthetic fixture 清单

```text
valid_regular_est.json
valid_regular_edt.json
valid_half_day.json
missing_session_record.json
duplicate_idempotent.json
duplicate_conflict.json
out_of_order.json
invalid_ohlc.json
invalid_decimal.json
invalid_time_relationship.json
late_synthetic_policy.json
stale_synthetic_policy.json
```

全部 fixture 标注 `synthetic=true` 和 `SYNTHETIC_TEST_ONLY`，使用固定时间、
固定 opaque identity 和项目自有合成值；没有供应商 API response、真实历史
行情或真实证券行情声明。测试对每个 fixture 计算并验证 SHA-256。

## 实际检查

`mypy`、`pytest` 和 CLI 结果来自 Python 3.12.10。由于 Python 3.12 环境
没有安装完整开发工具，本地只读复用了已存在的 Python 3.10 site-packages；
Ruff 使用当前默认 Python 3.10.11 的既有安装。没有联网安装任何包。

| 命令 | exit code | 结果 |
|---|---:|---|
| `python -m ruff check .` | 1 | BLOCKED：既有禁止修改的 `scripts/build_review_bundle.py` 在 616、833 行各有 `UP038`；新增 allowlist 源码和测试的 targeted Ruff 为 PASS |
| `python -m ruff format --check .` | 0 | PASS；20 files already formatted |
| `python -m mypy src scripts` | 0 | PASS；19 source files |
| `python -m pytest` | 0 | PASS；125 passed，coverage 91.28%，达到现有 90% threshold；仅有跨 Python 版本复用 coverage tracer 的 warning |
| `python -m build` | 1 | BLOCKED：当前 Python 3.12 环境没有 `build` module；Python 3.10 基线同样缺失该 module |
| `python -m quant_system.cli status` | 0 | PASS；offline，Paper/Live 均为 false |
| `git diff --check` | 0 | PASS |

因此核心代码、测试和 clean diff 检查已通过，但 Round 的全仓工具 gate
仍不完整；不能诚实地声明候选 Round 完成。

## A/B deterministic evidence

同一 `late_synthetic_policy.json` 在两个全新外部临时目录运行 canonicalization、
quality、data_version、manifest 和 quarantine 后，得到：

```json
{
  "all_file_sha_equal": true,
  "data_version_equal": true,
  "manifest_bytes_equal": true,
  "manifest_sha_equal": true,
  "quality_equal": true,
  "quarantine_reason_equal": true,
  "output_files_equal": true,
  "data_version": "c2c1ebd0d08f7174700e39ee3ca18aa3453fc70818525fdbd81de4c192720c42",
  "manifest_sha256": "4acb0afce5f835600bb12d654e1663b9ee2d51b53eafb15d321953cc27768f40",
  "quarantine_files": [
    "quarantine/c2c1ebd0d08f7174700e39ee3ca18aa3453fc70818525fdbd81de4c192720c42/record-synth-late-0001.json"
  ]
}
```

## 安全、数据和限制声明

- 未调用网络 API。
- 未获取 API key、credential 或供应商账户。
- 未下载市场数据；没有真实 Raw 数据，也没有批准的数据源。
- 没有 Adjusted、Corporate Action 或 LEAN market-data adapter。
- 没有启用策略、Broker、Alpaca、Paper 或 Live。
- `TBD-DATA-001`、`TBD-DATA-004`、`TBD-DATA-006` 仍未解决。
- `TBD-DATA-007` 仅实现 synthetic test policy；没有生产 late/stale 阈值。
- `TBD-DATA-008` 的真实 instrument registry 仍未解决。
- `TBD-DATA-009` 的生产 Decimal precision、scale 和 rounding policy 仍未解决。
- 通过本 Round 不代表真实数据准入，也不代表可用于策略回测的正式市场
  数据集已经完成。

## Candidate 与 Review Bundle

- Candidate commit：**未创建（BLOCKED）**。
- `git push origin main`：未执行；没有 candidate commit，且 Round 的强制
  工具 gate 尚未完成。
- Review Bundle：**REQUIRED**。本 Round 属于正式候选实施，涉及数据格式、
  manifest、quality 和 clean-source acceptance；但由于 mandatory checks
  被环境/既有禁止修改文件阻塞，未生成标注 PASS 的 Clean Review Bundle。

下一步是提供可用的离线 Python 3.12 `build` 工具，并由治理负责人决定如何
处理既有 Bundle generator 的 UP038 基线违规；在这些 gate 解除前，不应提交、
推送或冻结本 Round。
