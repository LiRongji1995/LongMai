# R16-R1 source closure plan

Post-commit raw acceptance is pending. This tracked plan does not declare final PASS. The Review Bundle contains the final evidence-bound matrix.

| ID / review | Problem and root cause | Implementation / regressions | Final evidence |
|---|---|---|---|
| A1 / C01 | ret1_pct fraction exported as percent: Original exporter/shadow unit mismatch | feature_evidence_contract.py; go-core/internal/shadow/shadow.go; test_r16_r1_contract_regressions.py; UNIT_AND_ROUNDING | baseline/; gate-a/; oracle/ |
| A2 / C02 | Missing/null -> 0+KNOWN: Numeric zero-value decoding lost missingness | go-core/internal/contract; feature_evidence_contract.py; test_r16_r1_contract_regressions.py; Go tests | baseline/; gate-a/ |
| A3 / C02 | Unverified provenance -> KNOWN: Truthiness substituted for evidence validation | feature_evidence_contract.py; go-core/internal/contract; test_r16_r1_contract_regressions.py | gate-a/; oracle/ |
| A4 / C03 | Duplicate key/schema/unit/status/provenance tolerance bypass: Dictionary overwrite and early tolerance success | feature_evidence_contract.py; r16_compare_contract_snapshots.py; schema; test_r16_r1_contract_regressions.py | baseline/; gate-a/ |
| A5 / C03 | value/value_json conflict: Two unchecked value authorities | feature_evidence_contract.py; go-core/internal/contract; test_r16_r1_contract_regressions.py | gate-a/ |
| A6 / C04 | OHLC mapping used wrong nesting: daily_path fields were read from item top-level | feature_evidence_contract.py; test_r16_r1_contract_regressions.py | gate-a/; oracle-real/ |
| A7 / C04 | Manifest/calendar/provenance incomplete: Observation hash substituted for calendar identity; lossy provenance | feature_evidence_contract.py; r16_production_oracle.py; schema; test_r16_r1_production_oracle.py | oracle/; oracle-real/ |
| B1 / C05 | New Python shadow as only oracle: Two newly written algorithms can share same error | r16_production_oracle.py; fixtures/r16_r1_equivalence; test_r16_r1_production_oracle.py | oracle/; oracle-real/ |
| B2 / C05 | Hash match without consumed injected facts: No actual Strategy input intervention | r16_production_oracle.py; BUSINESS_SLICE_RANK_TIE; injection negative tests | oracle/ |
| H1 / Review 4 | Corrected history absent: Old immutable broken T1 was rerun instead of rebuilt | r16_r1_build_corrected_history.py; provider_normalization.py; test_r16_r1_history.py | corrected-build/; history-full/; history-old-bad/ |
| H2 / Review 4 | Base compatibility/PIT/basis insufficient: Hash ordering/nonempty schema/unsafe factor coverage | history_base_selection.py; provider_comparability.py; r16_r1_base_basis_proof.py; test_r16_r1_history.py; test_r16_r1_base_basis_proof.py | base-basis-full/ proves per-row own-ref arithmetic only; cross-capture closure also requires H4 |
| H3 / Review 4/7 | Readiness and missing sessions unclear: Coverage counts without actual production window evidence | r16_r1_readiness.py; test_r16_r1_history.py | readiness-original-t/; readiness-old-bad/; readiness-corrected-t/; readiness-corrected-t1/ |
| H4 / Review 4 | Old per-row basis proof misses later complete-capture contradiction: 603083.SH real counterexample | factor_revision_review.py; existing factor_series refusal scoped by DEFINITION/dependent provider; typed comparable prices+basis null, original inline preservation, actual clocks, selected key/hash envelope replacement; r16_r1_factor_review_witness.py and actual-consumer/forged-old-hash regressions | Preserve old 77bcb67 proof/counterexample; new factor-review-proof/ plus rebuilt revision/history/PIT/readiness evidence after source freeze |
| D1 / Review 6 | 0ms placeholders/internal timing called E2E: No actual outer request/transaction/serialization boundary | e2e_tracing.py; candidate_api.py; candidate_service.py; jobs.py; test_r16_r1_e2e.py | e2e-offline/; e2e-data-ready/; live-attempt/ proves provider boundary only |
| D2 / Review 6 | Successful live provider boundary was not connected to normal incremental API/result readiness | r16_service_path_trace.py live-incremental; provider_normalization.py retained-identity gate; test_r16_r1_live_incremental.py; real persistence-failure negative | One new bounded capture through original normalizer/API, with explicit new-day identity UNKNOWN and missing sessions; actual final live run/readback pending |
| E1 / Review 5 | Repeated source/token/dependency work: Repeated provenance and overlapping token serialization | source_resolution.py; derived_feature_store.py; test_r16_r1_dependency_work.py | source-micro/; benchmark/ |
| E2 / Review 5 | Performance/RSS/store classification unreliable: Incomplete boundary and payload/metadata/page attribution | r16_r1_benchmark_worker.py; r16_r1_benchmark_suite.py; r16_r1_sqlite_pages.cjs; TEST_FIXTURE suite; test_r16_r1_evidence_runner.py | benchmark/ |
| P1 / Review 7 | Prospective counts/live state unproven; cross-epoch grouping and missing/null projection could distort audit | r16_r1_prospective_audit.py V2_EPOCH_SCOPED; run/epoch/group selection, document-record binding, JSON presence preservation; test_r16_r1_prospective_audit.py; protected read-only verification | New prospective raw audit pending; old summaries cannot certify corrected epoch/presence semantics; no official score recomputation |
| PK1 / Review 8 | Bundle lacked complete sources/JSONL/reproduction: Partial file selection and no extraction smoke | r16_r1_package_review.py; REPRODUCE.md; actual extraction contract/comparator/Go oracle smoke | packaging-smoke/; final verification sidecar |

All rows: `SOURCE_IMPLEMENTED_FINAL_ACCEPTANCE_PENDING`. H4 requires newly rebuilt revision/history/PIT/readiness evidence; rectification writer cost is separate from ordinary increment. No row declares final PASS.

- BASIS_UNSPECIFIED: original production return/MA disagree; unsupported current Go injection domain, no semantic repair
- Live normal incremental refresh requires its own source-frozen live-incremental run; offline replay and a successful provider-only attempt do not close it
- eligible=0 can exercise the real API/persistence path but cannot certify nonempty feature consumption; keep targeted current-identity and existing offline evidence separate
- Old 77bcb67 basis arithmetic PASS is scoped to each row's own refs; 603083.SH cross-capture compatibility requires new proof
- Readiness and performance targets require actual measurements; zero coverage not full-history absence
- Agent scheduling, TCP delivery, live scheduler thread/loaded epoch unobservable from isolated CLI or saved DB

See [engineering boundaries](R16_R1_ENGINEERING.md), [oracle](R16_R1_PRODUCTION_ORACLE.md), [corrected history](R16_R1_CORRECTED_HISTORY.md), and [REPRODUCE](REPRODUCE.md).
