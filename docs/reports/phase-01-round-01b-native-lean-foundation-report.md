# Phase 1 Round 1B Native LEAN Foundation Report

Status: **PASS WITH LIMITATIONS**

## Final deterministic closure

The prior Run C blocker was resolved without changing algorithm trading
behavior, fixture content, or business Golden values. Golden comparison is now
schema-checked semantic JSON comparison with stable order normalization; Run
A/B were re-normalized from their preserved raw order events and produced the
canonical Golden files. All business values remain a buy of 10 at 100.01, a
sell of 10 at 97.73, final holdings of 0, and final cash of 977.20.

The old binary SHA mismatch was traced to Git source-revision metadata injected
into clean-clone builds. The algorithm project now has deterministic release
settings, a stable path map, fixed version metadata, no debug symbols, and
`IncludeSourceRevisionInInformationalVersion=false`. Two external directories
and the final clean clone produced the same algorithm DLL SHA-256:
`9ca5b943c67e4ac0c41ae0fba16b54483a4859aa84e821284618ca58da40a5f8`.
`algorithm-build-identity.json` records the source, project, fixture, LEAN,
SDK, and reference-assembly identities without local paths.

Final clean-source Run C built from the final source revision, loaded the
committed synthetic fixture, and semantically matched both canonical Golden
orders and statistics. It had exactly two Filled simulated orders (buy then
sell), no final holdings, no broker connection, and no Paper or Live trading.

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
