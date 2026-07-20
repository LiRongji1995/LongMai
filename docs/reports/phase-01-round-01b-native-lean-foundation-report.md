# Phase 1 Round 1B Native LEAN Foundation Report

Status: **PASS WITH LIMITATIONS**

## Scope and safety boundary

This is an infrastructure-only deterministic smoke validation, not a trading
strategy or profitability evaluation. No Alpaca connection, Paper Trading, Live
Trading, real-market-data download, or real order submission occurred.

The external LEAN source checkout remained clean at
`0269115d3cfbf691c7a0b7cfcc9ed412cafb91f6`. An isolated external ToolBox
runtime copy was used. Its JSON-with-comments `config.json` controlled
`data-folder`; ToolBox's `--destination-dir` did not override that field. No
SYNTH files were written to the LEAN source data directory.

## Deterministic fixture evidence

- Generator: LEAN ToolBox RandomDataGenerator (offline)
- Security: USA Equity, Daily, Dense; ticker `SYNTH`
- Date range: 2020-01-02 through 2020-01-31
- Seed: `11399`; IPO, rename, split, dividend, quarterly-dividend, and coarse
  generation disabled
- Required output: daily ZIP, map CSV, factor CSV
- Generation A/B: map and factor byte-identical; ZIP payload byte-identical.
  ZIP container hashes differed only because the entry timestamps differed.
- Committed daily fixture SHA-256:
  `fe5c2883599220d8f4c9354178f58a61c1fc7443df25dad9eabb19c634070e0f`
- Daily extracted content SHA-256:
  `e380d71e59d7de464d31c18117754b4413d8331be8995f1e8d8643a94db24248`

## Native backtest evidence

The independent C# smoke algorithm built against the pinned external source.
Runs A and B each exited 0, processed 22 points, and had one succeeded data
request. Their trusted order-event files normalized exactly to two Filled orders:
a buy of 10 at 100.01 and a sell of 10 at 97.73. Final holdings were zero and
final cash/portfolio value was 977.20. Golden orders, statistics, and a run
manifest are checked in under `integrations/lean/expected/`.

## Actual checks

| Check | Result |
| --- | --- |
| External LEAN source clean at pinned commit | PASS |
| Isolated ToolBox A/B generation | PASS |
| Fixture ZIP/map/factor validation | PASS |
| Native C# build | PASS WITH LIMITATIONS (upstream dependency advisories) |
| Native LEAN A/B run and normalized equality | PASS |
| Ruff, mypy, pytest, CLI | PASS |
| Clean-source Run C, Git commit/push, review bundle | BLOCKED |

## Limitations and next action

The LEAN runtime still requested optional reference datasets (for example,
interest rates), producing non-fatal auxiliary data-request warnings. The
synthetic fixture itself loaded successfully. Run C, clean-source validation,
commit/push, and a review bundle remain intentionally unclaimed until they are
performed from a clean checkout.
