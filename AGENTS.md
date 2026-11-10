# Agent Rules — Personal Short-Term Stock Research

This is a single-user, local tool for A-share (SSE/SZSE main board) short-term screening: data update,
pre-market candidate lists with a market-temperature read, auction re-ranking and hit-rate review. It
never connects to a broker and never places orders; a person decides every trade intraday. It is not a commercial product: no web/API, multi-user, permission or
security platform work.

## Entry points

- **Primary:** the lean CLI `sf` (`stock-fish/sf/`, wrapper `bin/sf`). See [USER_GUIDE](USER_GUIDE.md).
- **Legacy:** the R2/qlib pipeline and strategy versions V1–V6 code were removed from `main` on
  2026-11-10; they are preserved at git tag `legacy-r2`.

## Three invariants (non-negotiable)

1. **Point-in-time correctness.** A decision for session T may only use data visible before the
   decision time (e.g. T-1 close data for the pre-market list, 09:25 auction data for the auction
   re-rank). Outcome labels (future returns) must never feed back into features or selection.
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

## Data and files

- All generated data lives outside the repo, next to the checkout: `../stockfish-data/` (override with
  `SF_HOME`). Paths come from arguments, environment
  or script location — never hard-code a personal path.
- Never commit data, lists, credentials or environments.

## Engineering

- Python 3.12. Runtime deps in `stock-fish/requirements.txt`; keep them minimal.
- Tests for `sf` live in `stock-fish/tests/sf/` and must run offline with synthetic data.
- Publish target: `LiRongji1995/stockfish-qlib-quant-system`, branch `main`. Commit only the files of the
  task; no force push or history rewrite. Verify the remote SHA after pushing. Commit messages in English.
- Keep existing licenses and [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md).
