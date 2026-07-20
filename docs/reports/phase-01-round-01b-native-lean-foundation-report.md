# Phase 1 Round 1B Native LEAN Foundation Report

Status: **PASS WITH LIMITATIONS**

## Final deterministic closure

Round 1B closed from a fresh clean-source checkout of the final committed Git
HEAD. Python 3.12 clean-source checks passed, the native C# deterministic build
passed, and a fresh native LEAN Run C passed. The final verification targets the
same Git HEAD archived in the official Clean Review Bundle; the Git evidence in
that bundle records `main`, a clean working tree, and `HEAD == origin/main`
after push.

Run C used the committed synthetic SYNTH fixture and the project-owned C# smoke
algorithm with the external official LEAN checkout pinned to
`0269115d3cfbf691c7a0b7cfcc9ed412cafb91f6`. The launcher exited 0 and loaded
valid data. It processed 22 points and produced exactly two Filled simulated
orders: first a buy of 10 at 100.01, then a sell of 10 at 97.73. Final holdings
were 0, final cash and portfolio value were 977.20, and normalized orders and
statistics semantically matched the committed Golden files.

The deterministic build identity matched `algorithm-build-identity.json`:
algorithm source SHA-256, project SHA-256, fixture ZIP SHA-256, extracted
fixture-content SHA-256, map SHA-256, factor SHA-256, LEAN commit, .NET SDK,
and deterministic algorithm DLL SHA-256 were all verified. The final DLL SHA-256
was `9ca5b943c67e4ac0c41ae0fba16b54483a4859aa84e821284618ca58da40a5f8`.

No Alpaca connection occurred. Paper Trading and Live Trading remained disabled,
and no real order was submitted. This infrastructure smoke test does not
validate profitability.

## Deterministic fixture evidence

- Generator: LEAN ToolBox RandomDataGenerator (offline)
- Security: USA Equity, Daily, Dense; ticker `SYNTH`
- Date range: 2020-01-02 through 2020-01-31
- Seed: `11399`; IPO, rename, split, dividend, quarterly-dividend, and coarse
  generation disabled
- Committed daily fixture SHA-256:
  `fe5c2883599220d8f4c9354178f58a61c1fc7443df25dad9eabb19c634070e0f`
- Daily extracted content SHA-256:
  `e380d71e59d7de464d31c18117754b4413d8331be8995f1e8d8643a94db24248`

## Final checks

| Check | Result |
| --- | --- |
| External LEAN source clean at pinned commit | PASS |
| Python 3.12 clean-source Ruff, format, mypy, pytest, and CLI checks | PASS |
| Review Bundle unit tests | PASS |
| Native C# deterministic build | PASS WITH LIMITATIONS (upstream dependency advisories) |
| Native LEAN Run C | PASS WITH LIMITATIONS (optional-reference-data warnings) |
| Filled simulated orders: buy then sell | PASS (exactly 2) |
| Final holdings | PASS (0) |
| Golden orders and statistics semantic comparison | PASS |
| Deterministic build identity and fixture/manifest/config identities | PASS |
| Git push and `HEAD == origin/main` | PASS |
| Final Clean Review Bundle integrity | PASS |

## Limitations and next action

1. The LEAN upstream dependency graph still reports security advisories during
   native build.
2. LEAN may request optional reference data, including interest rates, and emits
   non-fatal warnings when those optional files are absent.
3. This Round verifies native C# foundation infrastructure, not a LEAN Python
   algorithm.
4. Alpaca has not been connected.
5. Paper Trading and Live Trading have not been approved or enabled.
6. No profitability capability has been validated.

Review Bundle: **REQUIRED**. The final official bundle is Git-object-derived
from the final committed HEAD, contains the formal report and fresh native
evidence, and has a ZIP integrity status of PASS. Round 1C has not been started.
