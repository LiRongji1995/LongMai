# Phase 2 Round 01 Corrective：Governed Bar Contract Runtime Kernel（Synthetic Only）

## Round 决策

**PASS WITH LIMITATIONS**

本报告是 Phase 2 Round 01 的 corrective 交付记录。原 Candidate
`b7da13ff145a3453d31b4015e003ac98086cb9ee` 已被外部独立审阅判定为
`FAIL`，原因集中在 fail-closed quality、quality policy identity、manifest
phase-A/phase-B verification、quarantine evidence 与 Windows timezone
toolchain 闭环。本轮只修复这些缺陷，不扩大到真实数据、策略或执行能力。

新 Candidate 的确切 commit 由本轮 Review Bundle 的 delivery manifest 记录；
本报告不自引用新 commit，Bundle 中的 Git 状态、完整 diff、文件 SHA-256 和
外部验证结果是该值的唯一交付证据。

## 基线、边界与恢复

- Phase：`Phase 2`
- Round：`Round 01 Corrective`
- 权威仓库：`D:\us-equity-quant-system`
- 原 Candidate：`b7da13ff145a3453d31b4015e003ac98086cb9ee`
- 原 Candidate 外部结论：`FAIL`
- 本轮提交：原 Candidate 之上的单一 corrective commit；确切 SHA 由 Bundle 记录
- branch：`main`
- 目标：恢复 D 盘 Candidate 基线，并交付 fail-closed quality 与 manifest
  integrity 的可复核 runtime kernel
- 恢复证据：外部目录中的 `candidate-baseline-recovery-*` 证据目录
- 旧 C 盘仓库：未读取、未使用、未恢复、未复制其 source 或 ZIP

本轮没有执行 reset、restore、clean、stash、rebase、cherry-pick、非快进合并、
force push 或 amend。D 盘基线先通过 `git ls-remote`、一次 `fetch --prune` 和
`merge --ff-only origin/main` 恢复到原 Candidate；随后所有修改均建立在该
Candidate 之上。

## 单一目标与实际修改范围

唯一目标是：在 Synthetic-only 范围内，使 Bar quality gate、policy identity、
manifest verification 和 quarantine evidence 在失败或不确定时 fail closed。

实际修改仅限以下 allowlist 文件：

- `src/quant_system/data/quality.py`
- `src/quant_system/data/manifest.py`
- `tests/unit/test_data_quality.py`
- `tests/unit/test_data_manifest.py`
- `pyproject.toml`：仅加入 Windows 条件依赖
  `tzdata>=2024.1,<2026; sys_platform == 'win32'`
- 本报告
- `docs/DOCUMENTATION_MAP.md`：仅更新当前报告状态/路径

没有修改 Bar model、canonical contract、Phase 1 文档、README、CURRENT_PHASE、
DATA_CONTRACTS、fixture 目录、业务依赖、Bundle generator、LEAN、策略、Broker
或执行接口；没有新增 fixture 或数据目录。

## 修复内容

### 1. QualityPolicy 完整 identity

`QualityPolicy` 现在以不可变、确定性的五字段 mapping 作为 canonical identity：
`policy_id`、`label`、`late_after_microseconds`、`stale_after_microseconds`、
`allowed_suspect_issues`。allowlist 只能包含 contract reason code，禁止
`INVALID`；`LATE`、`STALE` 的 allowance 仍必须分别提供显式 threshold；默认
allowlist 为空。canonical mapping 的 SHA-256 为 `policy_sha256`。

`QualityResult` 记录 `policy_id`、`policy_sha256`、完整 policy mapping 的结果及
record-specific quality issues。manifest build 会拒绝同一 `policy_id` 但 threshold
或 allowlist 不同的 policy，避免只比较短 policy ID 而发生 identity collision。

### 2. Fail-closed quality 与 downstream

- `QUARANTINED`、`REJECTED`、`INVALID` 永不产生 downstream records。
- `SUSPECT` 只有在存在非空 issues、policy identity 完整且所有 dataset issues
  都被显式 allow 时才允许 downstream；无 issues 的 `SUSPECT` 仍拒绝。
- `MISSING`、`DUPLICATE` 只有显式 allow 才可放行；identical duplicate 的
  idempotent case 也不绕过 policy。
- 输入 Bar 已有的 `REJECTED`、`QUARANTINED`、`SUSPECT` state 与已有 issues
  被保留；已有 `LATE`/`STALE` 不被洗成 `VALID`。
- late/stale 超过显式 threshold 时始终 quarantine，allowlist 不能绕过 threshold。
- out-of-order、duplicate conflict 和既有 invalid 仍以 rejection 处理。

### 3. Manifest phase-A / phase-B integrity

manifest 会写入 canonical `bound_record_ids`，只有 quality gate 明确允许的 records
才可绑定 lineage；失败或未获准的 quality result 可以保留 audit manifest，但
`bound_record_ids` 和 `bound_records` 为空。

`bind_manifest` 会重新运行同一 policy 与 quality gate，比较 canonical
`QualityResult` 和 bound list，不能借 manifest metadata 绕过质量门禁。
`verify_manifest` 每次都从 manifest 独立重建 phase-A descriptor、重新计算
`data_version`，再验证 phase-B manifest SHA，并可独立比较输入 records。只修改
`data_version`、quality policy/result、ordered records 后重新计算 manifest SHA
仍会失败。

### 4. Record-specific quarantine evidence

quarantine evidence 只写对应 record 的 issues，不再把 dataset-level issue 伪造为
record issue，也不会为没有 `INVALID` 的 record 补写 `INVALID`。测试覆盖 late-only、
stale-only、late+stale、duplicate conflict、out-of-order、existing INVALID 和
existing QUARANTINED，并验证 deterministic order 与 fail-closed 写入行为。

## 外部独立验证

验证使用 D 盘仓库与外部全新 Python 3.12 venv。环境变量 `PYTHONTZPATH` 被清除；
editable install 使用 `python -m pip install -e ".[dev]"`，Windows venv 实际安装
bounded `tzdata`，并通过 `ZoneInfo("America/New_York")` probe。pip、freeze、tzdata
版本、环境证明、每条命令的 stdout/stderr/exit code 均保存在本轮外部证据目录。

最终门禁结果：

| 检查 | 结果 |
|---|---|
| `python -m ruff check .` | PASS |
| `python -m ruff format --check .` | PASS |
| `python -m mypy src scripts` | PASS |
| `python -m mypy src` | PASS |
| `python -m pytest` | PASS；158 passed，coverage 93.97% |
| `python -m build` | PASS；产物位于仓库外 build output |
| `python -m quant_system.cli status` | PASS；offline，Paper/Live 均为 false |
| `git diff --check` | PASS |

测试没有访问 market-data、broker、Alpaca 或其他 trading API。pip package index
访问仅用于安装开发工具与 Windows timezone data，不是市场数据访问。

## A/B 与剩余限制

独立 external regression probe 与 A/B run 均在仓库外执行。A/B 比较 policy canonical
bytes、policy SHA、QualityResult mapping、data_version、manifest bytes/SHA、bound
list、record-specific quarantine issues、output file list 与全部 file SHA-256；结果
及路径由 Bundle manifest 和 supplementary evidence 记录。

本 Round 仍是 synthetic/project-owned fixture only。没有真实 market data、真实
instrument registry、production calendar、adjusted/corporate-action pipeline、
strategy、Broker、Alpaca、Paper 或 Live path。未解决的未来事项仍包括生产数据
来源与许可、生产 late/stale policy、instrument registry、Decimal precision/scale/
rounding、Adjusted/Corporate Action 与 LEAN adapter；这些不属于本 Round corrective
范围。

本 Candidate 仍待外部独立审阅，不能视为冻结，也不能作为正式策略回测或交易数据
准入证明。

## Review Bundle 决策

**Review Bundle: REQUIRED**。本 Round 涉及 material code、dependency/toolchain、
clean-source verification、manifest identity 与正式候选交付；因此必须由官方
Bundle generator 生成，并由独立 extraction、manifest/checksum 与 scope validation
验证。screenshots 或本报告本身不能替代 Review Bundle。

## 下一动作

外部 reviewer 应基于 Bundle delivery manifest 中记录的 corrective Candidate、
完整 diff、独立探针、A/B、build hashes、Git remote equality 与 extraction validation
进行复核。复核通过前保持 Candidate 未冻结；不得接入真实数据、启用策略、连接
Broker/Alpaca 或开启 Paper/Live。
## Phase 2 Round 01 Corrective 2 Addendum

**Status: PASS WITH LIMITATIONS**

This addendum preserves the original Phase 2 Round 01 Candidate failure history above and records the current corrective round. The authoritative root is `D:\us-equity-quant-system`; the base Candidate is `a6946603c3aa9d5cfef1a1e467cc037e21078639`. The single objective is to bind manifest quality results to the exact current records and enforce truthful metadata, checksum, bound-record, and quarantine evidence invariants.

The implementation remains within the current round allowlist: canonical identity, quality, manifest, and their unit tests. No Bar schema fields, fixtures, project dependency declarations, Bundle generator, strategy, Broker, Alpaca, Paper, Live, or real-data path was changed. The current evidence is synthetic-only and uses no market-data/API/key/secret material.

Corrective 2 closes the stale `QualityResult` bypass by rerunning the quality gate for the current records and comparing the complete deterministic result. Manifest construction and verification now enforce exact source, timezone, interval, schema/version, inferred or exact instrument coverage, non-empty safe input checksums, and canonical `bound_record_ids`. Quarantine evidence is restricted to valid lowercase SHA-256 data versions, `QUARANTINED`/`REJECTED` status, supported non-empty issue codes, deterministic reasons, safe record IDs, source hashes, and non-empty commits; missing records do not produce fabricated Bars or files.

The current round must be judged from its fresh external evidence and Review Bundle, not from the earlier corrective evidence. Review Bundle: REQUIRED. New Candidate status remains pending independent review and is not frozen. Remaining TBD items are production data-source/license approval, production late/stale policy, instrument registry, calendar, corporate-action/adjustment semantics, precision/rounding, and future LEAN/strategy/execution integration.
