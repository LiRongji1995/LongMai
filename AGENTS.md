# Agent Rules — LongMai (龙脉), Personal Short-Term Stock Research

This is a single-user, local tool for A-share (SSE/SZSE main board) short-term screening: data update,
pre-market candidate lists with a market-temperature read and hit-rate review (the call auction is read by the person). It
never connects to a broker and never places orders; a person decides every trade intraday. It is not a commercial product: no web/API, multi-user, permission or
security platform work.

## Entry points

- **Primary:** the lean CLI `sf` (`stock-fish/sf/`, wrapper `bin/sf`). See [USER_GUIDE](USER_GUIDE.md).
- **Legacy:** the R2/qlib pipeline, the next-open backtests and the V1–V6 specs and code were removed from `main` on
  2026-11-10; they are preserved at git tag `legacy-r2`.

## Three invariants (non-negotiable)

1. **Point-in-time correctness.** A decision for session T may only use data visible before the
   decision time (e.g. T-1 close data for the pre-market list). Outcome labels (future returns) must never feed back into features or selection.
2. **Missing data is explicit.** Missing or failed data is reported as missing (`NaN`/`UNKNOWN`), never
   imputed as zero, never treated as "no event", never silently dropped from counts.
3. **Reproducible results.** Every saved list stores its inputs' identity (observation date, strategy
   version, parameters) and the full per-stock fact table, so a list can be re-derived and reviewed later.
   Saved lists and review outputs are never overwritten in place; a re-run writes a new version.

Everything else (re-hashing every file at every stage, cumulative request budgets, one-shot retry
authorizations, subprocess supervisor trees) is not required. Network failures may be retried with a
bounded, logged retry.

## Strategy changes

- Strategy rules and parameters live in versioned code/config under `stock-fish/sf/`. A change to rules
  or thresholds bumps the strategy version recorded in saved outputs.
- Validate changes with entry-free hit rates (`sf evaluate`: did listed stocks break out, were the
  realized 妖股 listed, did the temperature calls come true), train segment first, then the tests and
  the forward record. Next-open P&L backtests are not used: entries are chosen intraday by a person.
- Engineering test results, real-data checks and investment conclusions are reported separately.
- **Hit rate vs big loss (user decision, 2026-11-11, for versions after V9).** When a pre-registration compares a
  new rule with the current one, the default acceptance is: `out5` (breakout) at least +2 percentage points, and
  `bigloss1` (next-day big loss) up by no more than half of the `out5` gain and never more than +2 points. Write
  this rule into each new pre-registration before any result; it does not re-judge V9 retroactively.

## Data and files

- All generated data lives outside the repo, next to the checkout: `../stockfish-data/` (override with
  `SF_HOME`). Paths come from arguments, environment
  or script location — never hard-code a personal path.
- Never commit data, lists, credentials or environments.

## Engineering

- **Language.** User-facing documents (README, USER_GUIDE, CHANGELOG, everything under `docs/`) and CLI
  output are written in Simplified Chinese; keep them Chinese when adding or editing. Code, comments,
  docstrings, this file and commit messages stay in English.
- Python 3.12. Runtime deps in `stock-fish/requirements.txt`; keep them minimal.
- Tests for `sf` live in `stock-fish/tests/sf/` and must run offline with synthetic data.
- Publish target: `LiRongji1995/longmai` (renamed from `stockfish-qlib-quant-system` on 2026-11-10), branch `main`. Commit only the files of the
  task; no force push or history rewrite. Verify the remote SHA after pushing. Commit messages in English.
- Keep existing licenses and [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md).
- **Two sets of instructions.** Every operation has a human guide in [USER_GUIDE](USER_GUIDE.md) (Chinese)
  and an agent runbook below (English). When an operation changes, update both in the same commit.

## Runbooks (for AI agents)

A new conversation may start with no context. When the user asks for one of these operations (in any
wording, e.g. "恢复数据", "换电脑了", "备份一下", "生成今天的清单"), follow the runbook step by step,
report each check's result, and stop and ask at every **STOP**. Run commands from the repository root.
`bin/sf` uses `../stockfish-env` and `../stockfish-data` next to the checkout (override with
`SF_PYTHON` / `SF_HOME`); relative file arguments are relative to the caller's directory.

### R1. Set up a new machine (code and environment)
1. Clone `LiRongji1995/longmai` (ask the user where; default: any folder they name).
2. Python 3.12 is required. If `python3.12` is missing, install it without touching system settings:
   `python3 -m pip install --user uv`, then `<user-base>/bin/uv python install 3.12` and use
   `$(uv python find 3.12)` (Homebrew may be outdated on the user's Mac; do not run `brew update` unasked).
3. `<python3.12> -m venv ../stockfish-env && ../stockfish-env/bin/pip install -r stock-fish/requirements-dev.txt`.
4. Check: `cd stock-fish && SF_HOME=$(mktemp -d) ../../stockfish-env/bin/python -m pytest -q tests/sf` — all
   pass. Otherwise **STOP** and report.

### R2. Restore data from a backup archive (`sf restore`)
1. The user downloads `longmai-data-<stamp>.tar.gz` and its `.sha256` file (e.g. from Baidu Netdisk) into one
   folder. Ask for the path if not given. Use the newest archive unless the user names one.
2. `bin/sf restore <archive>`. It checks the archive's sha256 against the sidecar, never overwrites existing
   files, and verifies every file. Exit 0 = fine. Exit 1: report the printed conflicts / damaged / missing
   files and **STOP**; never delete or overwrite data files to resolve a conflict.
3. `bin/sf status` — report the bar range and pool coverage.
4. Fill sessions after the backup date: `bin/sf update --sessions 320` (network; about 15 s per missing
   session). Seats for those sessions: `bin/sf seats --backfill <N>` with N = missing sessions.
5. Data that cannot be re-fetched (limit-up / failed-board / limit-down / LHB pools for old sessions, saved
   lists, hype notes, archived board snapshots, intraday raw data) exists only in the archive; if missing,
   say so — never fill it with placeholders.

### R3. Monthly backup (`sf backup`)
1. Make sure no other `sf` command is running.
2. `bin/sf backup` (or `--out <folder>`). It writes `longmai-data-<UTC stamp>.tar.gz` plus `.sha256` into
   `../stockfish-backups/` by default and never overwrites an archive.
3. Report the archive path, size, file count and bar range, and tell the user to upload both files.
   Uploading to a cloud drive is done by the user; never upload or send data anywhere yourself.
4. Old archives are the user's to delete; do not delete them.

### R4. Build the pre-market list
1. After 18:00 Shanghai time: `bin/sf premarket` (updates data, captures pools, seats and intraday bars,
   then builds the list). `--no-update` builds from stored data only.
2. Report the header lines, especially any `数据缺失` line, and the saved path. Never present the list as
   investment advice; the person decides every trade.
3. The `昨日分时` column describes the observation day's intraday facts (description only; it never changes a group
   or order). Listed stocks the evening capture missed are fetched once while the list is built (not with
   `--no-update`). Before 40 captured sessions, never read it as a forecast for the next session.

### R6. Intraday minute bars (`sf intraday`)
1. Capture runs inside `sf premarket` / `sf update`. On its own: `bin/sf intraday` (latest closed session) or
   `bin/sf intraday --backfill` (stored sessions among the last 5 not captured yet). The source keeps about 5
   sessions only: if a capture failed, rerun it the same evening or the next day, and tell the user which
   days are lost if they fall out of the window.
2. Files are written once (`intraday/raw|minute|universe|capture/`, and `intraday/supplement/` for listed stocks
   fetched after the evening capture); never edit or delete them. Failed, incomplete or absent stocks are listed
   in `intraday/capture/<day>.json` and the supplement capture logs.
3. `bin/sf intraday report` prints the descriptive tables of docs/INTRADAY_STUDY.md. Before 40 captured
   sessions, report them as description only: no conclusions, no rule changes. Any rule built on them is a new
   strategy version with its own forward test, and follows the pre-registration exactly (no threshold tuning).

### R5. Rebuild data from scratch (no backup available)
Takes about 2 hours on the user's 2017 laptop, network bound: `bin/sf update --sessions 320 --workers 4`
(~70 min), `bin/sf themes` (~50 min, can run in parallel), `bin/sf seats --backfill 320` (~6 min), then R4.
Pools, saved lists, hype notes, board snapshots and intraday data from before the rebuild cannot be recovered
(intraday: only the last ~5 sessions can be fetched with `bin/sf intraday --backfill`).
