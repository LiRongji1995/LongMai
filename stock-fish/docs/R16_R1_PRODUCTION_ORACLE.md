# R16-R1 production oracle and current Go slice

This is a test tool, imported by no production CLI/API. Production remains Python.
`scripts/r16_go_shadow_equivalence.py` forwards to `r16_production_oracle.py`; the old
newly-written Python shadow calculation is removed from the acceptance path.

## Authority and numeric boundary

The original production implementation is recorded against base commit
`e2855d695f8818bb13103997e2ba1f1552538d99`. Every execution records current commit,
tree, dirty state, source file hashes, command, Python and Go versions, configuration
hash, input hash and actual start/end timestamps. Import `strategy_experiments`
before `theme_capital_trend`: their existing module initialization has an import
order dependency; this work does not rewrite the production module graph.

The source-only adapter calls `_prepare_context` and `canonical_price_source_view`,
freezes the selected rows and full source tokens/receipts, then invokes the original
`theme_capital_trend._window_return_uncached` and `_ma_value_uncached` functions.
OHLC uses `strategy_experiments._bar_decimal`. No new Python moving-average or
return algorithm is used as an authority.

Production returns are exact `Fraction` values. `ret1_pct` is percent, rounded to
four decimal places through the original `_number` formatting helper (the same
mapping as `stage_aware_momentum._pct`). OHLC has eight decimal places; MA has six.
Formatting converts Fraction to Decimal with precision 50, then uses HALF_EVEN.
The contract retains reduced `exact_value` numerator/denominator strings in the
declared contract unit. Injection converts exact percent back to Fraction/100.
For example, exact fraction 1/115 is exact percent 20/23, public value 0.8696;
injecting only the rounded public value would silently alter the strategy input.

Covered fields: open, high, low, close, prev_close, comparable_price, price_basis,
ret1_pct, MA20, MA60. All other derived windows and strategy interpretation remain
the original Python implementation. Actual `MarketInput.sessions` content forms
the calendar revision; no observation-date-only identity is used. Full source
tokens and parent receipts are retained in `source_manifest.json`; each field's
provenance includes the manifest hash, selected source identity, clocks, calendar,
window sessions and dependency token hashes. Neither builder invents source clocks.

`source_label` is a documented runtime-only difference. Source, unit, status,
reason, calendar, cutoff, exact value and provenance remain comparison inputs.

## Same Strategy and actual consumption

The test-only entry `strategy_with_facts` validates schema and complete slice keys
before making a fresh context. It replaces only slice price fields and the existing
WINDOW_RETURN/MA_VALUE cache entries. It retains all other baseline facts and
does not install a partial derived-feature context that would null other windows.

The business fixture contains eight instruments and 280 sessions, with WATCH and
REJECTED results and an actual setup-rank tie. Acceptance compares native Python,
Python-facts injection and Go-facts injection through the same
`run_stage_aware_momentum` function, including complete business hashes and explicit
Setup, decision, ranking and reasons. RecordingDict retains actual reads. A
controlled MA60 intervention changes TEST000 from WATCH to REJECTED; a second
intervention demonstrates actual OHLC, prev_close, comparable price/basis, ret1
and MA20 consumption in concrete output fields. Interventions are synthetic tests,
not assertions that those changed values came from the original provider.

Wrong units, contradictory values/exact values and unverified provenance are
rejected before Strategy. Candidate versus baseline comparison is required before
the business equivalence path; metadata changes cannot disappear behind matching
business hashes.

## Frozen fixtures and preserved exception

`tests/fixtures/r16_r1_equivalence/manifest.json` lists actual input and expected
files for normal sources, a trading-session gap, missing/UNKNOWN fields, source
revision, future PIT rejection, inline higher revision, percent/HALF_EVEN rounding,
cross-offset revisions and business rank ties. Expected files contain explicit
latest values/status/reasons/exact values and a hash of all complete facts. They
are only read during verification. The separate fixture creation tool refuses to
overwrite an existing manifest.

The original production return function accepts two null comparable bases when
both comparable prices exist; its MA function rejects that basis. Go returns
UNKNOWN. `BASIS_UNSPECIFIED_NEGATIVE` preserves this mismatch, and the test-only
injection boundary rejects this domain. This production semantic boundary remains
NOT_CLOSED; no production strategy semantics were changed to force a match.
The verified numeric slice requires a stated comparable basis and visible source
evidence. A negative test passes only when the expected mismatch is detected.

Canonical revision selection had independently reproducible string-order error
across timezone offsets. R16-R1 compares UTC instants while retaining original
clock text. `CANONICAL_RESOLVED_SOURCE_VIEW_V2_R16_R1_PIT_INSTANT` and
`DERIVED_FEATURE_STORE_V2_FAMILY_SPECS_R16_R1_PIT_INSTANT` identify this correction.
Both older-first and same-instant conflict cases have tests. Existing immutable
inputs and old cache entries are not overwritten.

## Reproduce from a source archive

From the repository root, with its required Python dependencies and Go 1.24 toolchain:

```powershell
& stock-fish/.venv/Scripts/python.exe -B -m pytest -q stock-fish/tests/r05_product/test_r16_r1_production_oracle.py
& stock-fish/.venv/Scripts/python.exe -B stock-fish/scripts/r16_go_shadow_equivalence.py --output-dir <external-output>/oracle
```

Expected exit code is 0; nine equivalence cases PASS, and the explicitly unsupported
basis fixture reports EXPECTED_MISMATCH_UNSUPPORTED_DOMAIN. Unexpected mismatches
raise and exit nonzero, retaining comparator details and raw facts. The Go subprocess
has a 120-second budget per small case, and the runner flushes case progress.

Real source extraction uses `scripts/r16_extract_oracle_fixture.py`, SQLite
`mode=ro`, explicit input ID/cutoff and bounded instrument/session counts. It
retains original row hashes, parent receipts, manifest hash, clocks and universe
denominator. Real extracts stay in private evidence and are distinctly labelled
REAL_SOURCE_EXTRACT; they are not published as synthetic fixtures or official
captures. The review bundle may include this small sample as requested. Run it with:

```powershell
& stock-fish/.venv/Scripts/python.exe -B stock-fish/scripts/r16_production_oracle.py --input <private-fixture>/real_source.input.json --output-dir <external-output>/real-oracle
```

The 000001.SZ original T sample has open/high/low/close 11.66/11.81/11.65/11.78
and ret1_pct 0.6838. Its MA20/MA60 gaps remain UNKNOWN with actual reasons. This
sample does not claim full-market or full-history readiness. MARKET_EDGE stays
NOT_ESTABLISHED; this evidence proves only the named Go slice.
