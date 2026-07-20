# Agent Development Constitution

## Mandatory task-start reading

Before any write, every Agent must read: `AGENTS.md`, `README.md`,
`docs/PROJECT_CHARTER.md`, `docs/ROADMAP.md`, `docs/ARCHITECTURE.md`,
`docs/RISK_POLICY.md`, `docs/REVIEW_BUNDLE_POLICY.md`, the current Phase's
latest formal report, and the current user prompt's baseline and boundary.
Then report Phase, Round, branch, Base commit, `origin/main`, worktree status,
the single objective, and whether a Review Bundle is mandatory. Missing policy
material is a stop-and-report condition, not permission to skip it.

When documents conflict, precedence is: explicit current user instruction,
then `AGENTS.md`, then Charter/Risk Policy, then Review Bundle Policy, then
Roadmap, current report, and README.

1. Each work round has one explicit primary objective; do not expand scope or silently lower acceptance.
2. Establish Git/tool/test baseline before edits. Preserve user work; never delete tests to pass, fake a
   test/API result, or claim unverified capability.
3. High-risk data, timestamp, risk, execution, reconciliation, config, and live-gate logic is test-first.
4. After edits run `python -m ruff check .`, `python -m ruff format --check .`, `python -m mypy src`,
   `python -m pytest`, `git diff --check`, and clean-source validation. Report any blocked check exactly.
5. Every backtest binds Git commit, configuration, data manifest, interval, cost model, strategy/risk version;
   financial metrics identify interval, costs, and benchmark.
6. Live and Paper Trading default to off. Strategies cannot bypass risk; only independent controls own the
   kill switch. LLMs never participate in real-time signals, risk, or order submission.
7. Never request, expose, commit, log, or put real secrets in prompts. Never connect an account or send an order
   without explicit phase approval.
8. One independent Git commit per completed work round. Do not auto-push `main`, force-push, rewrite history,
   delete user commits, or migrate data without tested backup/recovery.
9. Do not commit data, caches, logs, DB files, restricted reports, build output, or machine-specific settings.
10. Each report states completed/incomplete work, files, actual checks, residual risks, commit, and next action
    using only `PASS`, `PASS WITH LIMITATIONS`, `BLOCKED`, or `FAIL`.

## Mandatory task-end decision

Every Round must explicitly report `Review Bundle: REQUIRED`, `OPTIONAL`, or
`NOT REQUIRED` and why. When REQUIRED, an ungenerated or unvalidated bundle
prevents formal completion and entry to the next Round; screenshots alone are
not acceptance evidence.
