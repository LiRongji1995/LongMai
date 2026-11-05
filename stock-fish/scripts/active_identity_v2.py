"""Explicit active strategy/execution identities; frozen archives remain unchanged.

The role catalog is a reviewed allowlist, not an import-reachability heuristic.
Runtime semantic bytes are checked against the active output manifest. Full
execution verification, authorization and source authority remain the gateway's
responsibility; this module never activates a runtime or authenticates markets.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

VERSION = "ACTIVE_STRATEGY_IDENTITY_V2"
MAX_FILE = 8 * 1024**2
MAX_SEMANTIC_BYTES = 32 * 1024**2

def _names(value):
    return frozenset(value.split())

# Frozen published inventory, explicitly admitted as execution/support unless
# assigned to one of the reviewed semantic/source/presentation sets below.
# Adding a new file to the builder manifest is not automatic classification.
BASE_CATALOG = _names("""
app.py
requirements-rules.txt
requirements-daily.txt
requirements-review.txt
runtime-locks/build-tools-py312.lock
runtime-locks/daily-review-py312-macos-x86_64.lock
runtime-locks/daily-review-py312-macos-x86_64.json
LICENSE
config/codex-chat-api.example.json
config/codex-chat-premarket.example.json
config/premarket.example.json
config/r05_completion_requirements.yaml
config/r05_source_lock.json
config/r05_strategy_experiments.yaml
config/short_horizon_strength_r1.yaml
config/short_horizon_strength_r2.yaml
config/stage_aware_momentum_v1.yaml
config/stage_aware_momentum_v2.yaml
config/stage_aware_research_r1.json
config/theme_capital_trend_v1.yaml
config/theme_evolution_r1.yaml
config/yaogu_v1.defaults.yaml
docs/2026_SHORT_TERM_MAINLINE_REPORT.md
docs/ARCHITECTURE_OPTIMIZATION_DELIVERY_V1.md
docs/BASELINE_OUTCOME_PERFORMANCE_V1.md
docs/CLOSED_SESSION_PARTIAL_ACCEPTANCE_20261020.md
docs/CLS_NEXT_DATA_OPTIONAL_BYLINE_V2.md
docs/CODEX_CHAT_WORKSPACE_V1.md
docs/CODEX_DAILY_RESEARCH.md
docs/COHORT_PARTICIPATION_TRENDS_V1.md
docs/CONTINUATION_DELIVERY_20261023.md
docs/DAILY_PRIMARY_CRITICAL_CAPTURE_BOUNDARY_V1.md
docs/DAILY_PRIMARY_FAST_DECISION_V1.md
docs/DAILY_RECOMMENDATION_SCORE_V1.md
docs/DATED_IDENTITY_NUMERIC_H1_20261024.md
docs/ECONOMIC_DIRECTION_ARCHIVED_DISPLAY_V1.md
docs/ECONOMIC_DIRECTION_EXPLANATION_V1.md
docs/ENGINEERING_RELEASE_RECOVERY_V1.md
docs/EVIDENCE_AUDIT_SPOOL_V2.md
docs/EVIDENCE_AUDIT_SPOOL_V3.md
docs/EVIDENCE_AUDIT_SPOOL_V4.md
docs/EXPLICIT_FACTOR_FAILURE_RECOVERY_V1.md
docs/FACTOR_FULL_WINDOW_CHECKPOINT_V3.md
docs/FAILED_THEME_COLD_STORAGE_V1.md
docs/FIRST_RUN_LATEST_CAPTURE_V1.md
docs/HISTORICAL_FACTOR_RESUME_LINEAGE_V1.md
docs/HISTORICAL_FACTOR_VALUE_FACTS_V2.md
docs/HISTORICAL_REPLAY_TURNOVER_V1.md
docs/INDUSTRY_SINA_CONTEXT_V1.md
docs/ISOLATED_DAILY_RUNTIME_V1.md
docs/LATE_COMPLETED_SESSION_CLOSURE_V1.md
docs/LHB_SEAT_EVIDENCE_V1.md
docs/MAINLINE_CYCLE_RESEARCH_STANDARD_V1.md
docs/MARKET_DESCRIPTIVE_CONTEXT_V1.md
docs/MARKET_EXPLANATION_V1.md
docs/ORIGINAL_CANONICAL_PERFORMANCE_V1.md
docs/ORIGINAL_MARKET_INPUT_EXPORT_V1.md
docs/ORIGINAL_PARENT_ASOF_LATE_DERIVATION_V1.md
docs/ORIGINAL_PARENT_FIELD_DERIVATION_V2.md
docs/ORIGINAL_PARENT_HISTORY_FIELD_DERIVATION_V1.md
docs/ORIGINAL_PARENT_HISTORY_FIELD_DERIVATION_V2.md
docs/P0_DAILY_RELIABILITY_AND_EVALUATION_V1.md
docs/PHASE1_DAILY_RESEARCH_LOOP.md
docs/PHASE1_OBSERVATION_MONITOR.md
docs/PHYSICAL_HISTORICAL_PAIR_V1.md
docs/PHYSICAL_HISTORICAL_PAIR_V2.md
docs/PHYSICAL_HISTORICAL_RESEARCH_V1.md
docs/PREMARKET_BOUNDED_RETRY_20261021.md
docs/PREMARKET_ENVIRONMENT_RECOVERY_V1.md
docs/PREMARKET_FACTOR_CHECKPOINT_SOURCE_BUNDLE_V2.md
docs/PREMARKET_SHARED_EXECUTION_V1.md
docs/PROJECT_OVERVIEW.md
docs/R05_DATA_AND_DOCKER_RECTIFICATION.md
docs/R05_RECTIFICATION_AND_REPLAY.md
docs/R12_HISTORY_ARTIFACT_REPORT.md
docs/R14_DAILY_FAST_PATH.md
docs/R16_R1_CORRECTED_HISTORY.md
docs/R16_R1_ENGINEERING.md
docs/R16_R1_LIVE_INCREMENTAL.md
docs/R16_R1_PRODUCTION_ORACLE.md
docs/R1_REPAIR_SPEC_V2.md
docs/R1_REPAIR_SPEC_V3.md
docs/R1_RUNTIME_RESEARCH.md
docs/README.md
docs/REAL_DAILY_ACCEPTANCE_PERFORMANCE_V1.md
docs/REPRODUCE.md
docs/RUNTIME_R2_CLOSURE_DELIVERY_V1.md
docs/SAVED_R2_COMPLETE_DELIVERY_V1.md
docs/SAVED_R2_COMPLETE_DELIVERY_V2.md
docs/SAVED_R2_COMPLETE_STREAM_DELIVERY_V3.md
docs/SAVED_R2_DAILY_COORDINATION_V1.md
docs/SDK_TREE_SUPERVISED_CURRENT_PREMARKET_V2.md
docs/SEALED_RESULT_BYTES_RESTORED_V1.md
docs/SEALED_RESULT_LOCAL_COPY_V1.md
docs/SHARED_RESULT_COMPACT_INCREMENTAL_V2.md
docs/SHARED_RUNTIME_INCREMENTAL_V1.md
docs/SHORT_HORIZON_ARCHIVE_BRIDGE_V1.md
docs/SHORT_HORIZON_DAILY_CLOSURE_V1.md
docs/SHORT_HORIZON_OUTCOME_V1.md
docs/SHORT_HORIZON_STRENGTH_R1.md
docs/SHORT_HORIZON_STRENGTH_R2.md
docs/SPACE_TIME_OPTIMIZATION_V1.md
docs/STAGE_AWARE_MOMENTUM_V1.md
docs/STAGE_AWARE_MOMENTUM_V2.md
docs/STAGE_AWARE_RESEARCH_R1.md
docs/STANDALONE_PREMARKET_VALIDATION_V1.md
docs/STANDALONE_PREMARKET_WORKFLOW_V1.md
docs/SYSTEM_EXECUTION_PIPELINE_V1.md
docs/THEME_CAPITAL_TREND_V1.md
docs/THEME_EVENT_EVIDENCE_V1.md
docs/THEME_EVOLUTION_R1.md
docs/THEME_EVOLUTION_STREAMING_V2.md
docs/THEME_EVOLUTION_STREAMING_V3.md
docs/THEME_EVOLUTION_STREAMING_V4.md
docs/THEME_OBSERVATION_DIAGNOSTICS_V1.md
docs/THEME_STREAMING_RESEARCH_R1.md
docs/V2_PROSPECTIVE_VALIDATION_PROTOCOL.md
docs/architecture/ADR_GO_CORE_BOUNDARY.md
docs/architecture/FEATURE_EVIDENCE_CONTRACT_V1.md
docs/archive/BEFORE_AFTER_RESULT_EQUIVALENCE.md
docs/archive/DATA_READINESS.md
docs/archive/Daily_Primary_Fast_Decision_V1_Report.md
docs/archive/Health_Contract_Separation_Report.md
docs/archive/PRE_R14_WORKTREE_STATE.md
docs/archive/R14_CPROFILE_SUMMARY.md
docs/archive/R14_PERFORMANCE_BASELINE.md
docs/archive/R14_PERFORMANCE_RESULT.md
docs/archive/R14_PERFORMANCE_RESULT_COLD.md
docs/archive/R14_PERFORMANCE_RESULT_WARM.md
docs/archive/R14_PERFORMANCE_RESULT_WARM_PROFILED.md
docs/archive/R15_CACHE_IDENTITY_AUDIT.md
docs/archive/R15_DERIVED_STORE_GROWTH_AUDIT.md
docs/archive/R15_PERCENTILE_COMPLEXITY_AUDIT.md
docs/archive/R15_PERFORMANCE_RESULT.md
docs/archive/R15_TRUE_INCREMENTAL_ARCHITECTURE.md
docs/archive/README.md
docs/archive/SQLITE_SIZE_AUDIT.md
docs/archive/docs/r03-release.md
docs/archive/docs/r04-release.md
docs/archive/implementation_plan.md
docs/archive/phase1_observation_monitor_report.md
docs/archive/stock-fish-go/BACKEND_READINESS_FOR_CLI.md
docs/archive/stock-fish-go/INDEX_BENCHMARK.md
docs/archive/stock-fish-go/VALIDATION.md
docs/archive/stock-fish-go/delivery/MANUAL_ACCEPTANCE.md
docs/archive/stock-fish-go/delivery/SELFTEST_REPORT.md
docs/archive/stock-fish-go/delivery/USER_GUIDE.md
docs/archive/stock-fish/docs/CODEX_FIRST_USE_VERIFICATION.md
docs/archive/stock-fish/docs/MEMORY_PROFILE.md
docs/archive/stock-fish/docs/PHASE1_DAILY_RESEARCH_VALIDATION.md
docs/archive/stock-fish/docs/R05_REVIEW_BUNDLE.md
docs/archive/stock-fish/docs/R12_MEMORY_FOLLOWUP_20260909.md
docs/archive/stock-fish/docs/R12_PROSPECTIVE_INTEGRITY_REPORT.md
docs/archive/stock-fish/docs/R12_SCORE_INTEGRITY_REPORT.md
docs/archive/stock-fish/docs/R16_R1_ISSUE_MATRIX.md
docs/archive/stock-fish/docs/R1_G_REPAIR_RESULT.md
docs/archive/stock-fish/docs/R1_IMPLEMENTATION_RESULT.md
docs/archive/stock-fish/docs/R1_REPAIR_RESULT.md
docs/archive/stock-fish/docs/SLIMMING_REVIEW_AND_RESULT.md
docs/archive/stock-fish/docs/STAGE_AWARE_MOMENTUM_V2_IMPLEMENTATION_REPORT.md
docs/archive/stock-fish/docs/V2_DATA_READINESS.md
docs/archive/stock-fish/docs/V2_HISTORICAL_REPLAY_REPORT.md
docs/archive/stock-fish/docs/V2_PROSPECTIVE_READINESS.md
docs/archive/stock-fish/docs/V2_SEMANTIC_COMPLETION_REPORT.md
premarket
premarket.ps1
scripts/backup_restore_state.py
scripts/benchmark_artifact_layout_v1.py
scripts/benchmark_capture_decoded_v1.py
scripts/benchmark_codex_chat_fixture.py
scripts/benchmark_daily_v2.py
scripts/benchmark_premarket_stages_v1.py
scripts/benchmark_premarket_stages_v2.py
scripts/benchmark_real_premarket_v1.py
scripts/benchmark_real_premarket_v2.py
scripts/bootstrap_ci_native_runtime_v1.py
scripts/bootstrap_daily_runtime.py
scripts/build_premarket_source_bundle_v2.py
scripts/build_premarket_source_bundle_v3.py
scripts/build_r12_review_bundle.py
scripts/build_r14_reports.py
scripts/build_r16_review_bundle.py
scripts/capture_acceptance.py
scripts/capture_mainboard_data.py
scripts/capture_yaogu_data.py
scripts/codex_capacity.py
scripts/codex_capture_compact_v1.py
scripts/codex_capture_decoded_v1.py
scripts/codex_capture_epoch_v1.py
scripts/codex_chat.py
scripts/codex_compact_artifacts.py
scripts/codex_compact_core.py
scripts/codex_compact_core_v2.py
scripts/codex_core.py
scripts/codex_core_archive.py
scripts/codex_core_archive_v2.py
scripts/codex_core_sources.py
scripts/codex_current_calendar_v1.py
scripts/codex_current_calendar_v2.py
scripts/codex_daily.py
scripts/codex_daily_fast_v1.py
scripts/codex_daily_plan_v1.py
scripts/codex_daily_session_v1.py
scripts/codex_daily_v2.py
scripts/codex_delivery_audit_v1.py
scripts/codex_explicit_failure_recovery_v1.py
scripts/codex_factor_capture_v3.py
scripts/codex_factor_checkpoint_premarket_v1.py
scripts/codex_factor_incremental.py
scripts/codex_failed_theme_coldstore_v1.py
scripts/codex_first_core.py
scripts/codex_first_core_v2.py
scripts/codex_full_factor_recovery_v1.py
scripts/codex_incremental.py
scripts/codex_latest_capture.py
scripts/codex_metered_bundle_v1.py
scripts/codex_metered_bundle_v2.py
scripts/codex_native_theme_shared_v1.py
scripts/codex_operational_report_v1.py
scripts/codex_optimize.py
scripts/codex_outcome_profile_v1.py
scripts/codex_p0.py
scripts/codex_premarket.py
scripts/codex_premarket_fast_v1.py
scripts/codex_premarket_preflight_v1.py
scripts/codex_premarket_shared_v1.py
scripts/codex_r2_execution_v1.py
scripts/codex_recovered_premarket_v1.py
scripts/codex_research.py
scripts/codex_research_coverage_v1.py
scripts/codex_research_protocol_v1.py
scripts/codex_result_view_v1.py
scripts/codex_runtime_recovery_v1.py
scripts/codex_scheduler_query_v1.py
scripts/codex_sdk_tree_supervisor_v1.py
scripts/codex_sdk_tree_supervisor_v2.py
scripts/codex_sdk_tree_supervisor_v3.py
scripts/codex_shared_outcomes_v1.py
scripts/codex_storage_growth_v1.py
scripts/codex_supervised_premarket_v1.py
scripts/codex_supervised_premarket_v2.py
scripts/codex_supervised_premarket_v3.py
scripts/codex_theme_grouped_v1.py
scripts/codex_verification_v2.py
scripts/codex_workspace.py
scripts/contracts/operational_components_v1.json
scripts/contracts/release_profiles_v1.json
scripts/contracts/research_protocol_v1.template.json
scripts/core_admin.py
scripts/engineering_ci_scope_v1.py
scripts/engineering_go_packages_v1.py
scripts/full_market_data_rectification.py
scripts/generate_recommendation_score_report.py
scripts/master_audit.py
scripts/native_python_identity_probe_v1.py
scripts/native_quant_smoke.py
scripts/r02_evaluation_golden.py
scripts/r03_strategy_probe.py
scripts/r05_build_review_bundle.py
scripts/r05_capture_replay_inputs.py
scripts/r05_compare_replay_semantics.py
scripts/r05_evidence_runner.py
scripts/r05_independent_outcome_audit.py
scripts/r05_independent_primary_audit.py
scripts/r05_probe_tushare_status.py
scripts/r05_production_memory_probe.py
scripts/r05_pv_observation_capture.py
scripts/r05_replay_supply_audit.py
scripts/r05_replay_ui.py
scripts/r05_summary_performance.py
scripts/r05_tracking_service_checks.py
scripts/r05_verify_docker.py
scripts/r05_verify_replay_api.py
scripts/r05_weekly_replay.py
scripts/r05_write_replay_report.py
scripts/r12_historical_replay.py
scripts/r12_history_memory_profile.py
scripts/r12_post_cutoff_check.py
scripts/r12_validate.py
scripts/r16_build_oracle_fixtures.py
scripts/r16_compare_contract_snapshots.py
scripts/r16_contract_export.py
scripts/r16_extract_oracle_fixture.py
scripts/r16_go_shadow_equivalence.py
scripts/r16_history_stability_audit.py
scripts/r16_production_oracle.py
scripts/r16_r1_base_basis_proof.py
scripts/r16_r1_benchmark_suite.py
scripts/r16_r1_benchmark_worker.py
scripts/r16_r1_build_corrected_history.py
scripts/r16_r1_evidence_assets.py
scripts/r16_r1_factor_review_witness.py
scripts/r16_r1_gate_a.py
scripts/r16_r1_memory_batch_probe.py
scripts/r16_r1_package_review.py
scripts/r16_r1_profile_source_work.py
scripts/r16_r1_prospective_audit.py
scripts/r16_r1_readiness.py
scripts/r16_r1_source_binding.py
scripts/r16_r1_source_inventory.py
scripts/r16_r1_supervise.py
scripts/r16_r1_verify_protected.py
scripts/r16_service_path_trace.py
scripts/r16_validate_contract_schema.py
scripts/recovery_drill_v1.py
scripts/release_profile_manifest_v1.py
scripts/restore_shared_runtime.py
scripts/run_core_tests.py
scripts/run_isolated_tests.py
scripts/runtime_doctor.py
scripts/runtime_environment_smoke.py
scripts/stockfish.py
scripts/v2_scheduler_state.py
scripts/v2_semantic_delivery.py
scripts/verify_runtime.py
scripts/yaogu_admin.py
scripts/yaogu_fixture_evidence.py
scripts/yaogu_history_study.py
static/candidate.css
static/core.js
static/index.html
static/portfolio.html
static/portfolio.js
stockfish_core/__init__.py
stockfish_core/api.py
stockfish_core/backtest.py
stockfish_core/baostock_worker.py
stockfish_core/common.py
stockfish_core/data.py
stockfish_core/data_validation.py
stockfish_core/exchange_calendar_2026.json
stockfish_core/experiments.py
stockfish_core/jobs.py
stockfish_core/model_registry.py
stockfish_core/observed_data.py
stockfish_core/provider_classification.json
stockfish_core/quant.py
stockfish_core/quant_worker.py
stockfish_core/recommendation.py
stockfish_core/research.py
stockfish_core/risk.py
stockfish_core/scheduler_health.py
stockfish_core/service.py
stockfish_core/snapshot_backup.py
stockfish_core/storage.py
stockfish_core/tracker.py
stockfish_core/yaogu/__init__.py
stockfish_core/yaogu/auction.py
stockfish_core/yaogu/auction_input_selector.py
stockfish_core/yaogu/candidate_api.py
stockfish_core/yaogu/candidate_service.py
stockfish_core/yaogu/cls_optional_byline_v2.py
stockfish_core/yaogu/cohort_participation_trends.py
stockfish_core/yaogu/config.py
stockfish_core/yaogu/contracts.py
stockfish_core/yaogu/coverage_diagnostics.py
stockfish_core/yaogu/daily_observation.py
stockfish_core/yaogu/daily_primary_fast_decision.py
stockfish_core/yaogu/daily_primary_readiness.py
stockfish_core/yaogu/daily_read_context.py
stockfish_core/yaogu/daily_research.py
stockfish_core/yaogu/daily_research_outcomes.py
stockfish_core/yaogu/daily_research_service.py
stockfish_core/yaogu/data_pipeline.py
stockfish_core/yaogu/dated_identity_composite_input_v1.py
stockfish_core/yaogu/dated_identity_outcome_closure_v1.py
stockfish_core/yaogu/derived_feature_store.py
stockfish_core/yaogu/e2e_tracing.py
stockfish_core/yaogu/economic_direction_display.py
stockfish_core/yaogu/economic_direction_explanation_v1.py
stockfish_core/yaogu/emotion.py
stockfish_core/yaogu/emotion_history.py
stockfish_core/yaogu/engine.py
stockfish_core/yaogu/evaluation.py
stockfish_core/yaogu/events.py
stockfish_core/yaogu/evidence_audit_spool.py
stockfish_core/yaogu/evidence_audit_spool_v2.py
stockfish_core/yaogu/evidence_audit_spool_v3.py
stockfish_core/yaogu/evidence_audit_spool_v4.py
stockfish_core/yaogu/evidence_service.py
stockfish_core/yaogu/execution_research_r1.py
stockfish_core/yaogu/factor_fact_consumer.py
stockfish_core/yaogu/factor_revision_review.py
stockfish_core/yaogu/feature_evidence_contract.py
stockfish_core/yaogu/features.py
stockfish_core/yaogu/funds.py
stockfish_core/yaogu/historical_replay.py
stockfish_core/yaogu/historical_replay_factor_facts.py
stockfish_core/yaogu/historical_replay_factor_lineage.py
stockfish_core/yaogu/historical_replay_turnover.py
stockfish_core/yaogu/history_artifacts.py
stockfish_core/yaogu/history_base_selection.py
stockfish_core/yaogu/industry_sina_context_v1.py
stockfish_core/yaogu/lhb_seat_evidence.py
stockfish_core/yaogu/market_descriptive_context.py
stockfish_core/yaogu/market_explanation.py
stockfish_core/yaogu/market_explanation_artifacts.py
stockfish_core/yaogu/original_market_input_export_v1.py
stockfish_core/yaogu/original_parent_field_derivation_v2.py
stockfish_core/yaogu/original_parent_history_field_derivation_v1.py
stockfish_core/yaogu/original_parent_history_field_derivation_v2.py
stockfish_core/yaogu/original_parent_late_derivation.py
stockfish_core/yaogu/outcomes.py
stockfish_core/yaogu/paper_execution.py
stockfish_core/yaogu/physical_historical_pair_v1.py
stockfish_core/yaogu/physical_historical_pair_v2.py
stockfish_core/yaogu/physical_historical_research.py
stockfish_core/yaogu/premarket_capture_cache_v1.py
stockfish_core/yaogu/premarket_native_core_v1.py
stockfish_core/yaogu/premarket_news_v1.py
stockfish_core/yaogu/premarket_presentation_v1.py
stockfish_core/yaogu/premarket_runtime_v1.py
stockfish_core/yaogu/premarket_theme_context_v1.py
stockfish_core/yaogu/premarket_workflow_v1.py
stockfish_core/yaogu/provider_budget.py
stockfish_core/yaogu/provider_comparability.py
stockfish_core/yaogu/provider_funds.py
stockfish_core/yaogu/provider_normalization.py
stockfish_core/yaogu/provider_requests.py
stockfish_core/yaogu/provider_requests_worker.py
stockfish_core/yaogu/provider_worker.py
stockfish_core/yaogu/providers.py
stockfish_core/yaogu/r2_closure_cli.py
stockfish_core/yaogu/recommendation_score_artifacts.py
stockfish_core/yaogu/recommendation_scoring.py
stockfish_core/yaogu/replay_statistics.py
stockfish_core/yaogu/request_queue.py
stockfish_core/yaogu/research_context_artifacts.py
stockfish_core/yaogu/research_r1.py
stockfish_core/yaogu/research_r1_adapter.py
stockfish_core/yaogu/research_r1_contracts.py
stockfish_core/yaogu/research_r1_diagnostics.py
stockfish_core/yaogu/research_r1_io.py
stockfish_core/yaogu/research_r1_statistics.py
stockfish_core/yaogu/research_r1_storage.py
stockfish_core/yaogu/rules.py
stockfish_core/yaogu/saved_r2_daily_coordination_v1.py
stockfish_core/yaogu/saved_r2_delivery.py
stockfish_core/yaogu/saved_r2_sources_v2.py
stockfish_core/yaogu/saved_r2_stream_delivery_v3.py
stockfish_core/yaogu/scheduler.py
stockfish_core/yaogu/sealed_result_local_copy_v1.py
stockfish_core/yaogu/sealed_result_restoration_v1.py
stockfish_core/yaogu/session_payload_fusion_v1.py
stockfish_core/yaogu/short_horizon_archive_bridge_v1.py
stockfish_core/yaogu/short_horizon_daily_closure_v1.py
stockfish_core/yaogu/short_horizon_outcomes_v1.py
stockfish_core/yaogu/short_horizon_participation.py
stockfish_core/yaogu/short_horizon_profiles.py
stockfish_core/yaogu/short_horizon_resource_profiles_v1.py
stockfish_core/yaogu/short_horizon_strength.py
stockfish_core/yaogu/source_resolution.py
stockfish_core/yaogu/stage_aware_momentum.py
stockfish_core/yaogu/strategy_experiments.py
stockfish_core/yaogu/strategy_tracking.py
stockfish_core/yaogu/theme_capital_trend.py
stockfish_core/yaogu/theme_cycle_streaming.py
stockfish_core/yaogu/theme_cycle_validation.py
stockfish_core/yaogu/theme_event_evidence.py
stockfish_core/yaogu/theme_evolution.py
stockfish_core/yaogu/theme_evolution_streaming.py
stockfish_core/yaogu/theme_evolution_streaming_v2.py
stockfish_core/yaogu/theme_evolution_streaming_v3.py
stockfish_core/yaogu/theme_evolution_streaming_v4.py
stockfish_core/yaogu/theme_market_stream.py
stockfish_core/yaogu/theme_observation_diagnostics.py
stockfish_core/yaogu/themes.py
stockfish_core/yaogu/validation_identity.py
stockfish_core/yaogu/visibility.py
templates/daily_research.html
""")
EXTENSIONS = _names("""
scripts/active_business_gateway_v1.py
scripts/active_runtime_v1.py
scripts/active_worker_bootstrap_v1.py
scripts/active_service_host_v1.py
scripts/codex_active_v1.py
scripts/active_identity_v2.py
scripts/active_delivery_bridge_v1.py
scripts/active_source_bootstrap_v1.py
scripts/active_outcome_pipeline_v1.py
scripts/active_outcome_input_v1.py
scripts/active_environment_acceptance_v1.py
scripts/active_read_control_v1.py
scripts/business_authority_v1.py
scripts/codex_runtime_recovery_v2.py
scripts/restricted_history_v1.py
scripts/durable_stage_metrics_v1.py
scripts/daily_delivery_contract_v1.py
scripts/codex_research_protocol_v2.py
scripts/research_trial_ledger_v1.py
scripts/execution_evidence_v1.py
""")
# Numerical decisions and the semantics that establish which facts are usable.
# Provider transports, jobs, SQL, CLI formatting and run registration are not
# strategy formulas. Their exact bytes remain in execution/source identities.
EXPERIMENTS = _names("""
stockfish_core/yaogu/strategy_experiments.py
stockfish_core/yaogu/stage_aware_momentum.py
stockfish_core/yaogu/theme_capital_trend.py
stockfish_core/yaogu/contracts.py
stockfish_core/yaogu/visibility.py
stockfish_core/yaogu/events.py
stockfish_core/yaogu/history_artifacts.py
stockfish_core/yaogu/history_base_selection.py
stockfish_core/yaogu/source_resolution.py
stockfish_core/yaogu/derived_feature_store.py
stockfish_core/yaogu/coverage_diagnostics.py
stockfish_core/yaogu/data_pipeline.py
config/r05_strategy_experiments.yaml
config/r05_completion_requirements.yaml
config/r05_source_lock.json
config/yaogu_v1.defaults.yaml
config/theme_capital_trend_v1.yaml
config/stage_aware_momentum_v2.yaml
""")
R1 = EXPERIMENTS | _names("""
stockfish_core/yaogu/short_horizon_strength.py
config/short_horizon_strength_r1.yaml
docs/SHORT_HORIZON_STRENGTH_R1.md
""")
R2 = R1 | _names("""
stockfish_core/yaogu/short_horizon_participation.py
stockfish_core/yaogu/short_horizon_profiles.py
stockfish_core/yaogu/theme_evolution.py
stockfish_core/yaogu/research_context_artifacts.py
config/short_horizon_strength_r2.yaml
config/theme_evolution_r1.yaml
docs/SHORT_HORIZON_STRENGTH_R2.md
docs/THEME_EVOLUTION_R1.md
""")
SEMANTIC = {"EXPERIMENTS": EXPERIMENTS, "R1": R1, "R2": R2}
SOURCE = _names("""
stockfish_core/observed_data.py
stockfish_core/data.py
stockfish_core/data_validation.py
stockfish_core/provider_classification.json
stockfish_core/exchange_calendar_2026.json
stockfish_core/yaogu/data_pipeline.py
stockfish_core/yaogu/provider_normalization.py
stockfish_core/yaogu/provider_comparability.py
stockfish_core/yaogu/provider_funds.py
stockfish_core/yaogu/provider_requests.py
stockfish_core/yaogu/provider_requests_worker.py
stockfish_core/yaogu/provider_worker.py
stockfish_core/yaogu/providers.py
stockfish_core/yaogu/premarket_capture_cache_v1.py
stockfish_core/yaogu/visibility.py
stockfish_core/yaogu/events.py
stockfish_core/yaogu/contracts.py
stockfish_core/yaogu/history_artifacts.py
stockfish_core/yaogu/history_base_selection.py
stockfish_core/yaogu/source_resolution.py
stockfish_core/yaogu/derived_feature_store.py
scripts/codex_current_calendar_v1.py
scripts/codex_current_calendar_v2.py
""")
PRESENTATION = _names("""
stockfish_core/yaogu/premarket_presentation_v1.py
stockfish_core/yaogu/economic_direction_display.py
stockfish_core/yaogu/saved_r2_delivery.py
stockfish_core/yaogu/saved_r2_stream_delivery_v3.py
scripts/codex_result_view_v1.py
static/candidate.css
static/core.js
static/index.html
static/portfolio.html
static/portfolio.js
templates/daily_research.html
""")


def need(value, reason):
    if not value:
        raise ValueError(reason)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def classify(paths):
    paths = set(paths)
    need(paths <= BASE_CATALOG | EXTENSIONS, "ACTIVE_IDENTITY_UNCLASSIFIED_DEPENDENCY")
    for path in paths:
        relative = PurePosixPath(path)
        need(str(relative) == path and not relative.is_absolute() and ".." not in relative.parts
             and "\\" not in path, "ACTIVE_IDENTITY_NONCANONICAL_DEPENDENCY")
    return {"strategy": sorted(paths & R2), "source": sorted(paths & SOURCE),
            "presentation": sorted(paths & PRESENTATION), "execution": sorted(paths)}


def component_identities(files):
    """Builder API: classify every file, reject unknown additions, hash bytes."""
    need(type(files) is dict and all(type(raw) is bytes for raw in files.values()),
         "ACTIVE_IDENTITY_SOURCE_BYTES_REQUIRED")
    roles = classify(files)
    return {role: {"sha256": digest({path: sha(files[path]) for path in paths}), "files": paths}
            for role, paths in roles.items()}


def _read(root, relative):
    path = root / relative
    need(path.resolve(strict=True) == path and path.is_relative_to(root), "ACTIVE_IDENTITY_PATH_ALIAS")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= MAX_FILE,
             "ACTIVE_IDENTITY_FILE_TYPE_OR_SIZE")
        chunks, total = [], 0
        while True:
            chunk = os.read(fd, min(65536, MAX_FILE + 1 - total))
            if not chunk: break
            chunks.append(chunk); total += len(chunk)
            need(total <= MAX_FILE, "ACTIVE_IDENTITY_READ_LIMIT")
        after = os.fstat(fd)
        now = path.stat(follow_symlinks=False)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        need(identity(before) == identity(after) == identity(now) and total == before.st_size,
             "ACTIVE_IDENTITY_CHANGED_DURING_READ")
        return b"".join(chunks), identity(before)
    finally:
        os.close(fd)


def bind_identity(legacy, *, root, kind):
    """Active-only return hook. Never reinterpret a frozen archive's identity.

    source_hash hashes actual semantic files using the original digest format.
    execution_identity binds every declared active output, including all CLI,
    storage, guard, metrics and provider files; the gateway verifies that tree.
    """
    need(kind in SEMANTIC and type(legacy) is dict and
         type(legacy.get("source_file_hashes")) is dict and
         legacy.get("source_hash") == digest(legacy["source_file_hashes"]),
         "ACTIVE_IDENTITY_LEGACY_CONTRACT_REQUIRED")
    need("identity_contract" not in legacy, "ACTIVE_IDENTITY_ALREADY_BOUND")
    root = Path(root).absolute()
    need(root.resolve(strict=True) == root and root.is_dir(), "ACTIVE_IDENTITY_CANONICAL_ROOT")
    manifest_bytes, manifest_fp = _read(root, "ACTIVE_RUNTIME_MANIFEST.json")
    manifest = json.loads(manifest_bytes)
    outputs = manifest.get("output_sha256")
    need(manifest.get("schema") == "ACTIVE_RUNTIME_V1" and type(outputs) is dict and outputs and
         all(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) for value in outputs.values()),
         "ACTIVE_IDENTITY_OUTPUT_MANIFEST_REQUIRED")
    roles = classify(outputs)
    need(SEMANTIC[kind] <= set(outputs), "ACTIVE_IDENTITY_SEMANTIC_DEPENDENCY_MISSING")
    hashes, references, used = {}, {}, 0
    for name in sorted(SEMANTIC[kind]):
        raw, fp = _read(root, name)
        used += len(raw)
        need(used <= MAX_SEMANTIC_BYTES, "ACTIVE_IDENTITY_SEMANTIC_READ_LIMIT")
        need(sha(raw) == outputs[name], "ACTIVE_IDENTITY_SEMANTIC_BYTES_CHANGED")
        hashes[name], references[name] = sha(raw), fp
    # Close the multi-file inspection window without rereading full contents.
    for name, fp in references.items():
        value = (root/name).stat(follow_symlinks=False)
        need(fp == (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns),
             "ACTIVE_IDENTITY_CHANGED_DURING_BINDING")
    current, current_fp = _read(root, "ACTIVE_RUNTIME_MANIFEST.json")
    need(current == manifest_bytes and current_fp == manifest_fp, "ACTIVE_IDENTITY_MANIFEST_CHANGED")
    result = deepcopy(legacy)
    result.update(identity_contract=VERSION, source_hash=digest(hashes), source_file_hashes=hashes,
        semantic_identity_kind=kind,
        legacy_execution_identity=deepcopy(legacy),
        execution_identity={"schema": "ACTIVE_EXECUTION_IDENTITY_V1", "source_hash": digest(outputs),
            "manifest_sha256": sha(manifest_bytes), "file_count": len(outputs),
            "scope": "ALL_DECLARED_ACTIVE_OUTPUTS; PHYSICAL_EXECUTION_VERIFICATION_OWNED_BY_GATEWAY"},
        source_component_identity={"source_hash": digest({p: outputs[p] for p in roles["source"]}),
            "files": roles["source"]},
        source_identity_migration="NEW_ACTIVE_RUNS_ONLY; FROZEN_RUNS_NOT_REWRITTEN")
    return result
