# Clean Review Bundle Policy

## 1. Purpose

A Clean Review Bundle enables independent external review of one exact Git
commit. It supplies the full diff, test, clean-source, and security evidence
without uploading a virtual environment, caches, market data, or local
configuration.

## 2. Authority

Git commit history is the authoritative source version. A bundle source must
come from committed `HEAD`, never the current working directory; it cannot
replace Git history and its ZIP must not be committed to Git.

## 3. Mandatory triggers

A bundle is required when a Phase or Round is presented for acceptance, an
external review is requested, a result is declared `PASS` or `PASS WITH
LIMITATIONS`, material code is complete, or work is prepared for `main`.

It is also required after changing data format/loading, backtest or result
normalization logic, Golden files, manifests, strategy/portfolio/risk/order or
broker code, configuration/secret handling, dependencies/toolchain, CI,
Docker/deployment, database schema/migrations, or bundle machinery. It is
required whenever clean-source validation is an acceptance criterion or the
output will be handed to an external model or human reviewer.

## 4. Optional triggers

Bundles are optional for intermediate milestones, high-risk WIP, cross-machine
handoff, a long pause, or a reproducible snapshot after complex diagnosis.

## 5. Permitted skip cases

Only typo/format-only changes with unchanged meaning, read-only investigation,
no repository modification, clearly labelled unaccepted WIP, or an external
environment check with no repository modification may skip a bundle. The final
report must say `Review Bundle: NOT REQUIRED` and state the reason.

## 6. Status rules

`PASS` means every mandatory check passed. `PASS WITH LIMITATIONS` means core
integrity passed and every limitation is recorded. `BLOCKED` means core
acceptance is incomplete; `FAIL` means integrity, security, or truthfulness
failed. BLOCKED/FAIL work cannot produce a bundle labelled PASS; a diagnostic
bundle must be labelled with its actual status.

## 7. Bundle source rules

The working tree must be clean. Record Base at task start and use final Head at
delivery; include the `Base..Head` diff only. Never include uncommitted source,
archive a whole worktree, or include `.git`.

## 8. Required contents

Every official bundle contains `source/`, `REVIEW_README.md`,
`ROUND_METADATA.json`, `CHANGE_SUMMARY.md`, `CHANGED_FILES.csv`,
`FULL_DIFF.patch`, `DIFF_STAT.txt`, `TEST_RESULTS.md`, `COMMAND_RESULTS.txt`,
`CLEAN_SOURCE_RESULT.md`, `SECURITY_SCAN_RESULT.md`,
`BUNDLE_VALIDATION_RESULT.md`, `KNOWN_LIMITATIONS.md`, `REPRODUCE.md`,
`TRACKED_FILES.txt`, `FILE_MANIFEST.json`, `CHECKSUMS.sha256`, `EVIDENCE.json`,
and `BUNDLE_INFO.txt`.

Backtest-related rounds also include fixture/data manifests; algorithm,
configuration, and data hashes; normalized Run evidence; Golden files;
backtest manifests; and final-holdings/order assertions.

## 9. Excluded contents

Never include `.git`, virtual environments, Python/.NET runtimes, NuGet cache,
Docker images/volumes, LEAN source/runtime copies, `node_modules`, caches,
`.env`, secrets, real market data, databases, full local logs, large complete
backtest output, local user files, absolute local paths, or older bundles.

## 10. Validation

Validate from final Head: clean-source evidence, independent ZIP extraction,
checksums, manifest and tracked-file consistency, source-to-Git-object
comparison, secret scan, status consistency, an offline minimum check, and a
reported external ZIP SHA-256.

## 11. Naming

Use `review-bundle-<PHASE>-<ROUND>-<DESCRIPTION>-<SHORT_COMMIT>-<UTC_TIMESTAMP>.zip`.

## 12. Retention

Store ZIPs in `artifacts/review-bundles/`, which is Git-ignored. Never
overwrite old bundles; retain the final official bundle per acceptance round and
label replaced bundles `superseded` without committing historical ZIPs.

## 13. External review workflow

Normally the user uploads only the latest official Review Bundle ZIP. If it
contains the official report, do not separately upload that same report. Never
ask reviewers for `.env`, credentials, environments, images, market data,
databases, or full local logs.

## 14. Policy freeze

This policy is frozen after adoption. Change it only on explicit user request,
for an actual security defect, or when bundle machinery cannot complete
acceptance; do not expand governance on every Round.
