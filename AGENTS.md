# Agent Rules — LongMai (龙脉), Personal Short-Term Stock Research

This is a single-user, local tool for A-share (SSE/SZSE main board) short-term screening: data update,
pre-market candidate lists with a market-temperature read and hit-rate review, plus a call-auction snapshot of the listed
stocks (one pre-registered label, 竞价否定; the person reads the auction and decides). It
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
   **One valid list per trading day (user decision, 2026-11-12):** the list that counts for a target day is the last
   one saved before 09:25 Shanghai on that day (`listing.valid_run`); review, auction and page use it. After the
   cutoff, `sf prune` moves the day's other runs, the auction snapshots taken against them and old pages to the macOS
   Trash and logs them in `meta/prune.jsonl` (R11). Nothing else is removed.

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
- **Stored history is used up (user decision, 2026-11-13).** The test segments (from 2025-02-06) have been seen by
  V1-V11 and are no longer clean. New rule ideas go to `docs/HYPOTHESES.md` with the date they were raised and are
  tested on data after that date (forward record); do not pick thresholds on the stored history again. Pre-registrations
  already written (e.g. `docs/STOPLOSS_V1_PREREG.md`) run as written, with the caveat stated.
- **Liquidity floor (user decision, 2026-11-12, `SF_LONGMAI_V12`).** Every listed stock (all groups) must have traded at
  least 6/10000 of the whole-market 20-session average daily amount (~1e9 in 2026-11) on the observation day or on 2 of
  the last 5 sessions (a one-price limit-up day counts as meeting it). Imposed by the user without a backtest; judge it on the forward record only.
  Dropped stocks are listed in the header and `summary.json`. Spec: `docs/STRATEGY_LONGMAI_V12.md`.
- **Volume-surge note (user decision, 2026-11-12, `SF_LONGMAI_V13`).** Every listed stock (all groups, sealed or not) whose
  observation-day amount is >= 3x the mean of its previous 5 sessions gets a `放量：…` risk note (with < 3 known prior
  days: `前5日成交额缺失，放量未检查`). A note only: no group, order or slot changes. Spec: `docs/STRATEGY_LONGMAI_V13.md`.
- Descriptive layers (历史同类, 题材线索, 位置, 高标结局, page) never change a group or an order. Changing a rule
  threshold that a note uses (e.g. the 1e8 "流动性差" note) is a rule change and needs a pre-registration.

## Data and files

- All generated data lives outside the repo, next to the checkout: `../stockfish-data/` (override with
  `SF_HOME`). Paths come from arguments, environment
  or script location — never hard-code a personal path.
- Never commit data, lists, credentials or environments.

## Engineering

- **Language.** User-facing documents (README, USER_GUIDE, CHANGELOG, everything under `docs/`) and CLI
  output are written in Simplified Chinese; keep them Chinese when adding or editing. Code, comments,
  docstrings and this file stay in English. Commit messages are written in Simplified Chinese (user decision,
  2026-11-12). The local page shows Chinese only (codes, versions and stamps are translated; stock and board names are
  kept as they are).
- Python 3.12. Runtime deps in `stock-fish/requirements.txt`; keep them minimal.
- Tests for `sf` live in `stock-fish/tests/sf/` and must run offline with synthetic data.
- Publish target: `LiRongji1995/longmai` (renamed from `stockfish-qlib-quant-system` on 2026-11-10), branch `main`. Commit only the files of the
  task; no force push or history rewrite. Verify the remote SHA after pushing. Commit messages in Chinese.
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
4. The header also shows 题材线索 (theme threads: mainline boards linked across days by their limit-up stocks; the summary keeps a
   per-day record shown in the page's thread dialog; all name hype is merged into one 玄学 thread (`SF_XUAN_V1`:
   the unchanged daily test or a 20-session cumulative test, family-wise 1%; traced back to the leader's streak before
   statistical confirmation), shown first; the heat board (`SF_HEAT_V1`: boards by 3-session limit-up count, which
   concentration-ranked mainlines under-rate for big boards); with
   age, today's vs peak limit-ups), the `位置` column (vs MA250 and the one-year high), the amount as a share of
   the whole market, and 高标结局 (yesterday's tallest boards on the observation day). All description only;
   `【数据缺失】` is now the second header line.
5. The `历史同类` column gives the historical outcomes of the stock's cell (role x board height x one-price) from
   the latest `sf baserates` table with cutoff <= observation date (description only). If the header reports the
   table missing, run `bin/sf baserates` (~5 min) and rebuild with `--no-update`; refresh it weekly.

### R6. Intraday minute bars (`sf intraday`)
1. Capture runs inside `sf premarket` / `sf update`. On its own: `bin/sf intraday` (latest closed session) or
   `bin/sf intraday --backfill` (stored sessions among the last 5 not captured yet, and, for captured days with
   missing stocks, only those stocks as a new supplement file). The source keeps about 5
   sessions only: if a capture failed, rerun it the same evening or the next day, and tell the user which
   days are lost if they fall out of the window.
2. Files are written once (`intraday/raw|minute|universe|capture/`, and `intraday/supplement/` for listed stocks
   fetched after the evening capture); never edit or delete them. Failed, incomplete or absent stocks are listed
   in `intraday/capture/<day>.json` and the supplement capture logs.
   Stocks the 1-minute source still lacks get BaoStock 5-minute bars as a coarse fallback (`sf/minute5.py`,
   `intraday/bs5/`, write-once, own capture logs): fetched by `--backfill` and while a list is built. The list
   marks such notes `5分钟线（粗）`; the 5-minute layer is never mixed into the 1-minute study tables.
3. `bin/sf intraday report` prints the descriptive tables of docs/INTRADAY_STUDY.md. Before 40 captured
   sessions, report them as description only: no conclusions, no rule changes. Any rule built on them is a new
   strategy version with its own forward test, and follows the pre-registration exactly (no threshold tuning).

### R7. Call-auction snapshot (`sf auction`)
1. Between 09:25 and 09:30 Shanghai time on the list's target day: `bin/sf auction` (latest saved list; `--date` for
   another target). It fetches quotes for the listed stocks and the prior session's limit-ups and writes a new
   snapshot under `auction/<day>/<stamp>/` (never overwritten). Run after 09:30 it still records the open gap, but the
   auction amount is reported missing.
2. Report the header lines (especially `数据缺失`) and the table. `auction.ADOPTED` (exam 2026-11-12) enables only
   the deny label, for leader/high-vol and ladder: it means "less likely to break out within 5 sessions", not
   "more dangerous" (same-day big loss after the open was lower). High open (confirm) failed and is never shown as
   positive; the observe group is description only. No regrouping. The `同类开盘后（历史）` column gives open-anchored
   historical bands for buyable stocks (medians of high / low / close vs the open): report them as history, never as
   buy or sell levels. Never present it as a buy/sell signal; the person decides every trade.
3. `bin/sf evaluate auction` runs the pre-registered exam (slow, ~10 min). Run it only after the user has confirmed
   the pre-registration and the sha256 of the prereg file and `sf/auction.py` are recorded; write the result into the
   prereg file's result section whatever it is. Auction amount / unmatched volume rules need >= 40 captured sessions
   and their own pre-registration (a new version).

### R8. Scheduled capture (`sf schedule`)
1. `bin/sf schedule install` writes launchd user agents (`~/Library/LaunchAgents/com.longmai.sf.<job>.plist`) for:
   auction 09:26 (+ `prune --yes`), premarket 18:40 (+ page), intraday backfill 21:30 and 08:40 on weekdays; baserates and themes on
   Saturday. Install only when the user asks; `bin/sf schedule uninstall` removes them.
2. `bin/sf schedule status` shows each job's last run. Logs: `schedule/logs/`; latest status: `schedule/status/`.
   A failed job posts a local macOS notification and appears at the top of the local page.
3. When the minute-bar source drops connections, retry at another time only. Never switch to undocumented hosts,
   spoof headers or otherwise work around the source's limits.

### R9. Judgment diary and trade log (`sf judge`, `sf trade`)
1. Record the user's own calls with `bin/sf judge add "<their words>" --kind ... --subject ... [--days N] [--not]`
   only when they state a call and agree to record it; never invent judgments. Kinds: stock_lu, stock_bigloss,
   stock_out5, theme_fade (Eastmoney board name), free.
2. `bin/sf judge score` scores them from stored bars (writes `journal/scores/<stamp>.csv`). Records are write-once.
3. Trades are entered by the user (or by you from the user's explicit message): `bin/sf trade add <date> <code>
   buy|sell <price> <qty> --reason ...`. `bin/sf trade report` pairs FIFO and groups by the list of the buy day.
   Never connect to a broker; never present results as advice.

### R10. Local page (`sf page`)
1. `bin/sf page [target] --open` writes `pages/<target>/<stamp>.html` from the valid list's saved files only (written
   by premarket and auction automatically). Report its path. The page is description; data gaps are listed first.
2. One page per target day: a new page replaces the day's older page (pages are renderings, not saved results).
   `pages/index.html` (one row per day, with the list's save time, version and an `开盘后生成` flag) and
   `pages/latest.html` are navigation only and are rewritten with every new page, as are the `pages/go/prev-<day>` /
   `next-<day>` redirects behind each page's back / previous / next buttons.

### R11. Keep one list per day (`sf prune`)
1. `bin/sf prune` is a dry run: per target day past its 09:25 cutoff it prints the run kept and every path to remove
   (other list runs, auction snapshots that used them, pages). Days before their cutoff are skipped; a day with no
   run saved before the cutoff keeps everything.
2. Run `bin/sf prune --yes` only when the user asks (the scheduled auction job runs it daily). Files go to
   `~/.Trash/longmai-prune-<stamp>/`, never deleted outright; the log is `meta/prune.jsonl`. Report the trash folder.
   Emptying the Trash is the user's decision.

### R5. Rebuild data from scratch (no backup available)
Takes about 2 hours on the user's 2017 laptop, network bound: `bin/sf update --sessions 320 --workers 4`
(~70 min), `bin/sf themes` (~50 min, can run in parallel), `bin/sf seats --backfill 320` (~6 min), then R4.
Pools, saved lists, hype notes, board snapshots and intraday data from before the rebuild cannot be recovered
(intraday: only the last ~5 sessions can be fetched with `bin/sf intraday --backfill`).
