# R16-R1 corrected history

This is an engineering reconstruction from verified local capture files. It is
`RECONSTRUCTED_HISTORICAL` and makes no claim that later downloads were available
on the original business date. No provider request, full-history backfill,
official signal/score write, or production strategy change is part of this path.

## Source and comparison boundaries

The frozen R16 T input is
`market_63f83798f5098898debce30e0810a0c54f6a0daffdc75ac6404d47d5f8488a23`;
the old bad T+1 input is
`market_04d95df6f6f699c7f88353d181308159526865877a666a7c3efb84be01b9c08d`.
Both remain immutable. The source database is opened through SQLite URI
`mode=ro`, `query_only=ON`; its store initializer never runs migrations.

The local asset inventory binds exact file bytes, canonical result hashes,
request receipt IDs, source content hashes and original clocks. The 33
additional `market_daily` receipt IDs in old T+1 are **30 FETCH_FAILED captures
with no observations**, two complete historical captures (2026-08-27 and
2026-08-28), and one complete new-day capture (2026-09-09). In particular the
failed 09-04/09-08 attempts precede the successful captures already present in
T. They do not retract those later complete observations. Entire complete
capture files enter normalization; no complete declaration is retained over a
truncated row subset.

The builder first copies the explicitly frozen T dependency closure into an
isolated database. It checks every source row hash, partition membership/hash,
and existing target tuple. Conflicting resume state is rejected rather than
overwritten. Only the selected input/index/history and active config records
are copied; official records, jobs and derived features are excluded.

Four inputs are kept distinguishable:

1. Original T, with unchanged bytes and history.
2. A T wrapper changing only the coverage label. This policy change may change
   PIT fingerprints and is not a performance baseline for ordinary increment.
3. Corrected T retains the original T current RAW prices, qualification fields
   and their original source evidence. The real normalizer builds the two
   historical additions, with verified receipt union and RECON coverage.
   This is not a promise to preserve every comparable field exactly. A complete
   new factor capture that contradicts the old basis requires a typed review in
   its proven affected range: comparable values become UNKNOWN with the old
   RAW/basis authority, new conflicting receipt and actual dependency clocks.
   The complete original T current RAW capture is verified without inventing a
   recapture. The typed review uses the module and exact fields below; final
   acceptance still requires a source-frozen rerun.
   One preserved precommit counterexample omitted T current states and produced
   zero eligible stocks. A second regenerated current prices with an additional
   factor receipt at unchanged revision clocks: 600415.SH became CONFLICT even
   though all numerical fields were identical. The resolver correctly remains
   fail-closed. Retaining the original RAW/qualification evidence avoids this
   invented recapture; it does not authorize keeping known comparable values
   after a separately proven factor contradiction. The builder requires nonzero
   original/corrected eligible sets to agree and verifies their current flags
   and RAW fields through production resolution. These qualification checks do
   not prove factor-revision compatibility.
4. Corrected T+1: corrected T as the base plus the complete new-day capture and
   original T+1 metadata/receipts, through the same normalizer. Its base must
   include the same verified historical review knowledge before ordinary
   append stability is claimed.

Corrected T and T+1 use one actual reconstruction cutoff later than all selected
capture completion clocks. Their source receipt clocks remain original. The
original-T to corrected-T+1 comparison measures repair; corrected-T to
corrected-T+1 measures the new day at a common information cutoff. A strict
append invariant can fail for the repair pair when historical additions occur.
Such additions require separate raw-capture/normalization/membership proof;
newer clocks alone never establish a legitimate source revision.

## Fail-closed boundaries

The base must precede the target date, have intact referenced partitions inside
its calendar, aware source clocks available at build cutoff, and compatible
provider identities with retained field versions. Automatic selection ranks
observation date and aware source/registration instants, never hash text.
Different bases tied at the same clocks are ambiguous and require an explicit
selection. Artifact integrity does not establish DAILY/PV/MA readiness. Each
row's price basis is preserved; consumer source/basis checks remain mandatory.

The saved 77bcb67 base-basis proof checked each member against that member's
own referenced RAW/factor series. Its arithmetic PASS did not test consistency
with other complete captures containing later contradictory knowledge. The
real 603083.SH counterexample demonstrates this missing proof boundary. Preserve
that original report and its counterexample; do not relabel the old result as
full cross-capture compatibility. The bounded typed-review repair must preserve
RAW/qualification, downgrade only proven conflicting comparable fields, and
carry all real supporting/rejecting refs and clocks. No fabricated later clock
may be used to force a winner; same-instant ambiguity remains fail-closed.
`factor_revision_review.py` uses the existing `factor_series` series-level
refusal. It never substitutes the new factor's numeric value. Scope is restricted
to the `DEFINITION:iid:` basis or explicit dependency on `adjustment_factors`
from the same factor provider. Independent Tushare/direct-capture authority is
reported in `outside_factor_scope` and retained. The implementation selects one
canonical visible revision per key and rejects equal-clock ambiguity.

The new typed DailyBar sets `comparable_open/high/low/close` and
`comparable_basis` to null/None. It retains RAW, qualification and `event_time`,
unions old/new source refs, and sets `source_published_at`, `first_seen_at` and
`ingested_at` to each field's actual dependency maximum. It requires strict
revision advance and visible parents; build time cannot manufacture authority.
Any substituted original inline variants absent from history are first retained
verbatim with their original clocks, including variants older than an already
UNKNOWN selected row. T-envelope replacement is bound to the actual selected
key/hash in `reviews` or `already_unknown`, never any UNKNOWN row sharing an iid.
The report retains full `old_row`/`review_row`, hashes, `changed_fields`, refs,
`actual_revision_clocks`, `retained_inline_originals` and bounded read counters.

The existing per-row arithmetic proof and cross-capture compatibility proof
remain separate obligations. Affected source-frozen acceptance is pending.
Rectification writer/storage cost is reported separately from normal day append;
the live path requires an already reviewed corrected base and still rejects
every old-partition change rather than treating repair as normal increment.

RAW × backward factor now requires a complete factor window containing both the
immutable left anchor and the price session. A capture ending on 09-08 cannot
certify absence of a new action on 09-09. Missing bounds and disconnected later
windows do not prove coverage. Current prices without such evidence retain
unknown comparability and precise reason codes. An earlier test that applied
a factor beyond its captured end was corrected to test its covered date; the
original failing example is retained as a baseline negative. Unsupported
tombstones/retractions are rejected; failed fetches remain failed receipts.

The history audit reads both stores without migration. Comparability loss or a
source addition/revision is not assigned a specific build cause without proof.
`UNVERIFIED_*` remains an invariant violation, and the CLI exits nonzero when
the invariant or canonical checks fail. `--expect-invariant-failure` is an
explicit negative-test wrapper; it succeeds only if failure actually occurs.
For a common-information reconstruction comparison, the independent future
session PIT probe uses Day T end and records that cutoff separately.

## Reproduction entry points

Run from repository root, using the project virtual environment. Output paths
must be new isolated directories outside `stock-fish/data`. Preserve prior
outputs. Inspect the output's `run_registry.json`, checkpoint, logs and existing
process before resuming; matching active process identity is rejected.

```powershell
& ./stock-fish/.venv/Scripts/python.exe -X utf8 stock-fish/scripts/r16_r1_source_inventory.py --help
& ./stock-fish/.venv/Scripts/python.exe -X utf8 stock-fish/scripts/r16_r1_build_corrected_history.py --source-db stock-fish/data/stockfish.sqlite3 --inventory work/r16-r1/c-planning/inventory-20260914-v1/raw_asset_inventory.json --output work/r16-r1/NEW-corrected --budget-seconds 600
```

The builder records command, Python/Git identity, PID/create time, actual
start/end/cutoff, source-selection and normalization report hashes, input and
history IDs. It exposes progress callbacks at copy/asset/normalization/register
boundaries for the actual outer tracing integration. This builder's stage
events by themselves are not an E2E trace or live provider refresh.

Read `build-result.json` with `ConvertFrom-Json -DateKind String` on PowerShell
7 (otherwise ISO timestamps can silently become locale-formatted DateTimes).
Use its `input_t_corrected`, `input_t_plus_1_corrected`, `target_db` and common
`cutoff` with:

```powershell
& ./stock-fish/.venv/Scripts/python.exe stock-fish/scripts/r16_history_stability_audit.py --help
& ./stock-fish/.venv/Scripts/python.exe stock-fish/scripts/r16_r1_readiness.py --help
```

Start with `--instrument-limit 16`, then run the separately budgeted full audit.
The readiness tool consumes canonical PIT-selected prices and the original
production `_complete_pv_row`/`_ma_value` functions, preserving actual required
and missing sessions for MA20/60/120/250, plus eligible/SSE/SZSE denominators.
Its Trend figure means long-MA input availability only. Actual Trend decisions
and Market/Sector/Capital/Theme/Limit/Auction/Intraday contexts remain
`NOT_VERIFIED_NOT_RUN` until the production strategy run is separately audited.
Final acceptance requires post-commit rebuilt artifacts, full validation and
the independently measured E2E/performance evidence; precommit smoke is not
that acceptance evidence. After the factor-review implementation is frozen,
rerun the affected corrected build, cross-capture review proof, history/PIT and
readiness comparisons, then E2E/cache/benchmark cases using those changed input
IDs. Do not reuse old numbers or the old arithmetic PASS as final closure. `MARKET_EDGE=NOT_ESTABLISHED` remains unchanged.
