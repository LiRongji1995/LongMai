"""Whole admission with isolated TEST_FIXTURE originals and denied network."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from unittest.mock import patch

from tests.unit.test_codex_core import Base
from tests.yaogu.test_premarket_capture_cache_v1 import FixtureBudget, FixtureExecutor
from scripts import codex_premarket_preflight_v1 as preflight
from scripts import codex_core_sources as sources
from stockfish_core.yaogu import premarket_capture_cache_v1 as native
from stockfish_core.yaogu import premarket_runtime_v1 as runtime_support
from stockfish_core.yaogu.premarket_runtime_v1 import BudgetExceeded


class PreflightTests(Base):
    def setUp(self):
        super().setUp()
        self.executor = FixtureExecutor()
        self.cache = native.CaptureCache(self.state / "cache", FixtureBudget(),
            config=dict(allow_test_fixture=True), executor=self.executor)
        today = datetime.now(sources.SH).date()
        self.local = str(today)
        self.calendar = self.add("calendar", dict(start_date=str(today-timedelta(days=180)),
            end_date=str(today+timedelta(days=10))), self.local, role="CALENDAR")
        self.observed, self.target, self.core, self.theme, _ = sources._calendar(
            self.cache.verify(self.calendar), datetime.now(timezone.utc))
        self.config(capture=dict(offline=True), limits=dict(cache_bytes=64*1024**2,
            output_bytes=4*1024**2, max_requests=16, max_sdk_calls=64, max_http_attempts=48))

    def config(self, **updates):
        path = Path(self.runtime.profile["config"])
        value = json.loads(path.read_bytes())
        value.update(updates)
        path.write_text(json.dumps(value))

    def add(self, endpoint, params, day, **kwargs):
        return self.cache.capture_one(native.RequestSpec(endpoint, params, day, **kwargs))

    def plan(self, **kwargs):
        return preflight.plan(self.runtime, allow_test_fixture=True, **kwargs)

    def complete(self):
        for endpoint in ("mainboard_sse", "master_szse", "delisting_sse"):
            self.add(endpoint, {}, self.local, role="CURRENT_OFFICIAL", current=True)
        for day in self.theme:
            for endpoint in ("market_daily", "master_all"):
                self.add(endpoint, dict(date=day), day, role="FULL_MARKET_CONTEXT")
        self.add("stock_basic_all", {}, self.local, role="CURRENT_BASIC_ATTEMPT", current=True)
        self.add("adjustment_factors", dict(codes=["sh.600001"], start_date="1990-01-01",
            end_date=self.observed), self.observed, role="FULL_ANCHORED_FACTOR_WINDOW")
        for endpoint, member, nodes in (
            ("industry_list_sina", "industry_members_sina", ["hangye_TEST_FIXTURE_A", "hangye_TEST_FIXTURE_B"]),
            ("concept_list_sina", "concept_members_sina", ["gn_TEST_FIXTURE"])):
            self.add(endpoint, {}, self.local, role="CURRENT_THEME_DIRECTORY", current=True)
            for node in nodes:
                self.add(member, dict(symbol=node), self.local, role="WHOLE_CURRENT_THEME_MEMBERS", current=True)

    def refs(self):
        return {str(path): sources.original._reference(path) for path in self.root.rglob("*") if path.is_file()}

    def complete_without_latest_market(self):
        add = self.add
        def other_sources(endpoint, parameters, day, **options):
            if endpoint == "market_daily" and day == self.observed:
                return None
            return add(endpoint, parameters, day, **options)
        with patch.object(self, "add", side_effect=other_sources):
            self.complete()
        self.online_collection()
        return native.RequestSpec("market_daily", dict(date=self.observed), self.observed,
                                  role="FULL_MARKET_CONTEXT")

    def test_explicit_collection_accepts_only_latest_market_missing_with_known_master(self):
        self.complete_without_latest_market()
        value = self.plan(collect_missing=True)
        self.assertTrue(value["collection_admitted"], value["collection_reasons"])
        self.assertFalse(value["execution_admitted"])
        rows = [row for stage in value["stages"] for row in stage["requests"] if row["status"] == "MISSING"]
        self.assertEqual([(row["request_spec"]["endpoint"], row["request_spec"]["business_session"])
                          for row in rows], [("market_daily", self.observed)])

    def test_explicit_collection_latest_market_unfinished_is_not_missing(self):
        spec = self.complete_without_latest_market()
        attempt = self.cache.root / "raw" / spec.key
        attempt.mkdir()
        (attempt / "TEST_FIXTURE-unfinished").write_bytes(b"retained failure")
        value = self.plan(collect_missing=True)
        self.assertFalse(value["collection_admitted"])
        self.assertIn("COLLECTION_SOURCE_NOT_RETRYABLE:"+spec.key, value["collection_reasons"])

    def test_explicit_collection_future_latest_market_is_not_missing(self):
        spec = self.complete_without_latest_market()
        cutoff = datetime.now(timezone.utc)
        self.cache.capture_one(spec)
        value = self.plan(collect_missing=True, _now=cutoff)
        self.assertFalse(value["collection_admitted"])
        self.assertIn("COLLECTION_SOURCE_NOT_RETRYABLE:"+spec.key, value["collection_reasons"])

    def test_blocked_still_reports_official_factors_and_all_67_sessions_without_payload_reads(self):
        prior = self.core[-2]
        self.add("adjustment_factors", dict(codes=["sh.600001", "sz.000001"],
            start_date="1990-01-01", end_date=prior), prior, role="FULL_ANCHORED_FACTOR_WINDOW")
        before = self.refs()
        with patch.object(native.CaptureCache, "capture_one", side_effect=AssertionError("NO_WRITE")):
            value = self.plan()
        self.assertEqual(before, self.refs())
        self.assertFalse(value["execution_admitted"])
        self.assertEqual(value["observation_date"], self.observed)
        self.assertEqual(value["target_session"], self.target)
        self.assertEqual(len(value["core_sessions"]), 25)
        self.assertEqual(len(value["theme_sessions"]), 67)
        stages = {row["name"]: row for row in value["stages"]}
        self.assertEqual(len(stages["CURRENT_OFFICIAL"]["requests"]), 3)
        self.assertEqual(len(stages["THEME_MARKET_ADDITIONAL_42"]["requests"]), 84)
        conditional = stages["FULL_ANCHORED_FACTORS"]["conditional_prior_universe_estimate"]
        self.assertEqual(conditional["sdk_reserved"], 4)
        self.assertFalse(conditional["included_in_known_totals"])
        self.assertEqual(value["source_authentication"]["authenticated_original_count"], 1)
        self.assertFalse(value["factor_inventory"]["tail_composition_allowed"])
        self.assertEqual(value["totals"]["sdk_reserved"], 134*3)
        self.assertEqual(value["network_calls"], 0)
        self.assertEqual(value["strategy_calls"], 0)
        self.assertFalse(value["full_premarket_ready"])

    def test_complete_originals_admit_without_files_or_strategy(self):
        self.complete()
        before = self.refs()
        value = self.plan()
        self.assertTrue(value["execution_admitted"], value["reasons"])
        self.assertEqual(value["reasons"], [])
        self.assertEqual(value["totals"]["requests"], 0)
        self.assertTrue(value["totals"]["total_exact"])
        self.assertFalse(value["full_premarket_ready"])
        self.assertEqual(value["source_authentication"]["unresolved_graph_requests"], 0)
        self.assertEqual(before, self.refs())

    def test_original_writer_cache_limit_blocks_even_diagnostic_can_read(self):
        self.config(capture=dict(offline=True), limits=dict(cache_bytes=1, output_bytes=1024))
        value = self.plan()
        self.assertIn("ORIGINAL_WRITER_CACHE_LIMIT_EXCEEDED", value["reasons"])
        self.assertEqual(value["original_writer_limits"]["cache_bytes"], 1)
        self.assertFalse(value["diagnostic_limits_authorize_writer"])

    def test_whole_capacity_is_distinct_from_writer_cache(self):
        with patch.object(self.runtime, "preflight", return_value=dict(allowed=False,
                reason_codes=["TEST_FIXTURE_CAPACITY"])):
            value = self.plan()
        self.assertIn("REGISTERED_STORAGE_CAPACITY_BLOCKED", value["reasons"])

    def test_calendar_same_bytes_replacement_passes_and_tamper_fails(self):
        path = Path(self.calendar.metadata["raw"]["path"])
        replacement = path.with_name("TEST_FIXTURE-replacement")
        replacement.write_bytes(path.read_bytes())
        replacement.replace(path)
        self.plan()
        raw = path.read_bytes()
        path.write_bytes(b"X" + raw[1:])
        with self.assertRaisesRegex(BudgetExceeded, '^FILE_SHA256_CHANGED$'):
            self.plan()

    def test_tampered_content_addressed_index_is_rejected(self):
        path = next((self.cache.root / "index").glob("*/*.json"))
        path.write_bytes(path.read_bytes()+b" ")
        with self.assertRaisesRegex(ValueError, "CACHE_INDEX_CONTENT_ADDRESS_CHANGED"):
            self.plan()

    def test_unfinished_attempt_is_not_automatically_retry_budgeted(self):
        spec = native.RequestSpec("market_daily", dict(date=self.observed), self.observed,
                                  role="FULL_MARKET_CONTEXT")
        directory = self.cache.root / "raw" / spec.key
        directory.mkdir()
        (directory / "TEST_FIXTURE-unfinished").write_text("unfinished")
        value = self.plan()
        row = next(row for stage in value["stages"] for row in stage["requests"]
                   if row["request_key"] == spec.key)
        self.assertEqual(row["status"], "UNFINISHED_NO_IMPLICIT_RETRY")
        self.assertFalse(value["totals"]["total_exact"])
        self.assertFalse(value["execution_admitted"])

    def test_online_news_has_bounded_cost_and_can_still_admit(self):
        self.complete()
        self.config(capture=dict(offline=False), news=dict(max_gets=4))
        value = self.plan()
        self.assertTrue(value["execution_admitted"], value["reasons"])
        self.assertEqual(value["totals"]["requests"], 4)
        self.assertEqual(value["totals"]["http_reserved"], 4)
        self.assertFalse(value["totals"]["total_exact"])

    def test_news_reservation_does_not_borrow_another_budget(self):
        self.complete()
        self.config(capture=dict(offline=False), news=dict(max_gets=4),
                    limits=dict(max_requests=3, output_bytes=1024))
        value = self.plan()
        self.assertFalse(value["execution_admitted"])
        self.assertIn("KNOWN_REQUEST_COST_EXCEEDS_ORIGINAL_LIMIT:max_requests", value["reasons"])

    def test_production_does_not_accept_test_fixture_sources(self):
        with self.assertRaisesRegex(ValueError, "CORE_SOURCE_REAL_AND_FIXTURE_SEPARATION"):
            preflight.plan(self.runtime)

    def test_production_clock_override_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "REAL_CLOCK_OVERRIDE_FORBIDDEN"):
            preflight.plan(self.runtime, _now=datetime.now(timezone.utc))

    def test_missing_calendar_never_claims_zero_exact_downstream_cost(self):
        for path in (self.cache.root / "index").glob("*/*.json"):
            path.unlink()
        value = self.plan()
        self.assertFalse(value["execution_admitted"])
        self.assertIsNone(value["observation_date"])
        self.assertIsNone(value["target_session"])
        self.assertEqual(value["stages"][0]["name"], "CALENDAR")
        self.assertEqual(value["totals"]["sdk_reserved"], 3)
        self.assertFalse(value["totals"]["total_exact"])
        self.assertTrue(value["totals"]["unknown_components"])


    def online_collection(self):
        self.config(capture=dict(offline=False), limits=dict(max_requests=512,
            max_sdk_calls=10000, max_http_attempts=32768, cache_bytes=1024**3, output_bytes=4*1024**2))

    def test_explicit_collection_authenticates_present_history_without_writes(self):
        self.online_collection()
        self.add("market_daily", dict(date=self.core[0]), self.core[0], role="FULL_MARKET_CONTEXT")
        before = self.refs()
        with patch.object(native.CaptureCache, "capture_one", side_effect=AssertionError("NO_WRITE")):
            value = self.plan(collect_missing=True)
        self.assertTrue(value["collection_admitted"], value["collection_reasons"])
        self.assertFalse(value["execution_admitted"])
        self.assertFalse(value["full_premarket_ready"])
        self.assertEqual(value["source_authentication"]["authenticated_original_count"], 2)
        self.assertEqual(value["network_calls"], 0)
        self.assertEqual(value["strategy_calls"], 0)
        self.assertEqual(before, self.refs())

    def test_ordinary_online_missing_plan_does_not_expand_authentication(self):
        self.online_collection()
        self.add("market_daily", dict(date=self.core[0]), self.core[0], role="FULL_MARKET_CONTEXT")
        value = self.plan()
        self.assertFalse(value["collection_admitted"])
        self.assertEqual(value["collection_reasons"], ["EXPLICIT_MISSING_COLLECTION_PLAN_REQUIRED"])
        self.assertEqual(value["source_authentication"]["authenticated_original_count"], 1)

    def test_explicit_collection_rejects_tampered_old_market_while_latest_missing(self):
        self.online_collection()
        record = self.add("market_daily", dict(date=self.core[0]), self.core[0], role="FULL_MARKET_CONTEXT")
        path = Path(record.metadata["raw"]["path"])
        raw = path.read_bytes()
        path.write_bytes(b"X"+raw[1:])
        with self.assertRaisesRegex(BudgetExceeded, "FILE_SHA256_CHANGED"):
            self.plan(collect_missing=True)

    def test_explicit_collection_authenticates_complete_prior_listing_before_new_parents(self):
        self.online_collection()
        self.add("stock_basic_all", {}, self.local, role="CURRENT_BASIC_ATTEMPT", current=True)
        before = self.refs()
        with patch.object(native.CaptureCache, "capture_one", side_effect=AssertionError("NO_WRITE")):
            value = self.plan(collect_missing=True)
        self.assertTrue(value["collection_admitted"], value["collection_reasons"])
        self.assertEqual(value["source_authentication"]["authenticated_original_count"], 2)
        self.assertEqual(value["network_calls"], 0)
        self.assertEqual(before, self.refs())

    def test_explicit_collection_rejects_tampered_prior_listing_before_new_parents(self):
        self.online_collection()
        record = self.add("stock_basic_all", {}, self.local, role="CURRENT_BASIC_ATTEMPT", current=True)
        path = Path(record.metadata["raw"]["path"])
        raw = path.read_bytes()
        path.write_bytes(b"X"+raw[1:])
        before_calls = len(self.executor.calls)
        with patch.object(native.CaptureCache, "capture_one", side_effect=AssertionError("NO_WRITE")):
            with self.assertRaisesRegex(BudgetExceeded, "FILE_SHA256_CHANGED"):
                self.plan(collect_missing=True)
        self.assertEqual(len(self.executor.calls), before_calls)

    def test_explicit_collection_rejects_unfinished_request(self):
        self.online_collection()
        spec = native.RequestSpec("market_daily", dict(date=self.observed), self.observed,
                                  role="FULL_MARKET_CONTEXT")
        directory = self.cache.root / "raw" / spec.key
        directory.mkdir()
        (directory / "TEST_FIXTURE-unfinished").write_text("unfinished")
        value = self.plan(collect_missing=True)
        self.assertFalse(value["collection_admitted"])
        self.assertIn("COLLECTION_SOURCE_NOT_RETRYABLE:"+spec.key, value["collection_reasons"])

    def test_explicit_collection_rejects_existing_failed_qualification(self):
        self.online_collection()
        self.executor.basic_failure = True
        self.add("stock_basic_all", {}, self.local, role="CURRENT_BASIC_ATTEMPT", current=True)
        self.complete()
        value = self.plan(collect_missing=True)
        self.assertFalse(value["collection_admitted"])
        self.assertTrue(any(reason.startswith("COLLECTION_SOURCE_NOT_RETRYABLE:")
                            for reason in value["collection_reasons"]))

    def test_explicit_collection_requires_existing_authenticated_calendar(self):
        self.online_collection()
        for path in (self.cache.root / "index").glob("*/*.json"):
            path.unlink()
        value = self.plan(collect_missing=True)
        self.assertFalse(value["collection_admitted"])
        self.assertIn("COLLECTION_AUTHENTICATED_CALENDAR_REQUIRED", value["collection_reasons"])
        self.assertEqual(value["network_calls"], 0)

    def test_explicit_collection_rejects_unexplained_unknown_dependency(self):
        self.online_collection()
        actual = preflight._scope
        def unknown(*args):
            value = actual(*args)
            value["stages"][0]["unknown_dependencies"].append("TEST_FIXTURE_UNEXPLAINED")
            return value
        with patch.object(preflight, "_scope", side_effect=unknown):
            value = self.plan(collect_missing=True)
        self.assertFalse(value["collection_admitted"])
        self.assertIn("COLLECTION_UNEXPLAINED_SCOPE:CURRENT_OFFICIAL", value["collection_reasons"])

    def test_diagnostic_cache_uses_registered_writer_ceiling_without_changing_writer(self):
        self.online_collection()
        root, inventory = sources._existing_cache(self.state / "cache")
        inventory = dict(inventory, logical_bytes=300*1024**2)
        with patch.object(sources, "_existing_cache", return_value=(root, inventory)):
            value = self.plan()
        self.assertEqual(value["read_only_diagnostic_limits"]["cache_bytes"], 1024**3)
        self.assertEqual(value["original_writer_limits"]["cache_bytes"], 1024**3)
        self.assertFalse(value["diagnostic_limits_authorize_writer"])

    def test_readonly_authentication_does_not_run_periodic_writer_tree_scans(self):
        self.complete()
        before = self.refs()
        with patch.object(runtime_support, "directory_bytes",
                side_effect=AssertionError("TEST_FIXTURE_NO_PERIODIC_WRITER_SCAN")):
            value = self.plan()
        self.assertTrue(value["execution_admitted"], value["reasons"])
        self.assertEqual(value["cache_inventory"], value["cache_inventory_at_entry"])
        self.assertEqual(value["diagnostic_cache_observation"],
                         "ENTRY_AND_TERMINAL_FULL_INVENTORY_READONLY")
        self.assertEqual(before, self.refs())

    def test_terminal_cache_growth_over_writer_limit_blocks_collection(self):
        self.online_collection()
        _, initial = sources._existing_cache(self.cache.root)
        config = json.loads(Path(self.runtime.profile["config"]).read_bytes())
        config["limits"]["cache_bytes"] = initial["logical_bytes"] + 1
        self.config(limits=config["limits"])
        actual = native.CaptureCache.terminal
        def concurrent_growth(cache):
            result = actual(cache)
            (cache.root / "TEST_FIXTURE_concurrent_growth").write_bytes(b"X" * 4096)
            return result
        before_calls = len(self.executor.calls)
        with patch.object(native.CaptureCache, "terminal", concurrent_growth):
            value = self.plan(collect_missing=True)
        self.assertFalse(value["collection_admitted"])
        self.assertIn("ORIGINAL_WRITER_CACHE_LIMIT_EXCEEDED", value["collection_reasons"])
        self.assertGreater(value["cache_inventory"]["logical_bytes"],
                           value["original_writer_limits"]["cache_bytes"])
        self.assertEqual(value["cache_inventory_at_entry"]["logical_bytes"], initial["logical_bytes"])
        self.assertEqual(value["network_calls"], 0)
        self.assertEqual(len(self.executor.calls), before_calls)

    def test_terminal_cache_growth_over_diagnostic_limit_raises_before_admission(self):
        _, initial = sources._existing_cache(self.cache.root)
        self.config(limits=dict(cache_bytes=initial["logical_bytes"] + 512))
        actual = native.CaptureCache.terminal
        def concurrent_growth(cache):
            result = actual(cache)
            (cache.root / "TEST_FIXTURE_concurrent_growth").write_bytes(b"X" * 4096)
            return result
        limits = dict(sources.LIMITS, cache_bytes=initial["logical_bytes"] + 1024)
        with patch.object(sources, "LIMITS", limits), \
                patch.object(native.CaptureCache, "terminal", concurrent_growth):
            with self.assertRaisesRegex(ValueError, "PREFLIGHT_READONLY_CACHE_LIMIT_EXCEEDED"):
                self.plan()

    def test_terminal_capacity_change_blocks_collection(self):
        self.online_collection()
        with patch.object(self.runtime, "preflight", side_effect=[
                dict(allowed=True, reason_codes=[]),
                dict(allowed=False, reason_codes=["TEST_FIXTURE_CONCURRENT_CAPACITY_CHANGE"])]) as capacity:
            value = self.plan(collect_missing=True)
        self.assertFalse(value["collection_admitted"])
        self.assertIn("REGISTERED_STORAGE_CAPACITY_BLOCKED", value["collection_reasons"])
        self.assertFalse(value["capacity"]["allowed"])
        self.assertEqual(capacity.call_args_list[0], capacity.call_args_list[1])
        self.assertEqual(value["network_calls"], 0)

    def test_terminal_inventory_failure_cannot_return_admission(self):
        self.online_collection()
        actual = sources._existing_cache(self.cache.root)
        with patch.object(sources, "_existing_cache", side_effect=[actual,
                ValueError("CORE_SOURCE_CACHE_INVENTORY_INCOMPLETE")]):
            with self.assertRaisesRegex(ValueError, "CORE_SOURCE_CACHE_INVENTORY_INCOMPLETE"):
                self.plan(collect_missing=True)

    def test_readonly_source_byte_and_time_budgets_remain_enforced(self):
        for field, limit, reason in (("capture_read_bytes", 1, "READ_LIMIT_EXCEEDED:capture"),
                                     ("max_seconds", 1e-9, "TOTAL_TIME_LIMIT_EXCEEDED")):
            with self.subTest(field=field), patch.object(sources, "LIMITS", dict(sources.LIMITS, **{field: limit})):
                with self.assertRaisesRegex(BudgetExceeded, reason):
                    self.plan()

    def test_terminal_source_content_check_is_preserved(self):
        actual = native.CaptureCache.terminal
        path = Path(self.calendar.metadata["raw"]["path"])
        def tampered_source(cache):
            raw = path.read_bytes()
            path.write_bytes(b"X" + raw[1:])
            return actual(cache)
        with patch.object(native.CaptureCache, "terminal", tampered_source):
            with self.assertRaisesRegex(BudgetExceeded, "FILE_SHA256_CHANGED"):
                self.plan()

    def test_explicit_collection_cannot_bypass_capacity_or_known_budget(self):
        self.online_collection()
        with patch.object(self.runtime, "preflight", return_value=dict(allowed=False,
                reason_codes=["TEST_FIXTURE_CAPACITY"])):
            value = self.plan(collect_missing=True)
        self.assertFalse(value["collection_admitted"])
        self.assertIn("REGISTERED_STORAGE_CAPACITY_BLOCKED", value["collection_reasons"])
        self.config(capture=dict(offline=False), limits=dict(max_requests=1, cache_bytes=1024**3))
        value = self.plan(collect_missing=True)
        self.assertFalse(value["collection_admitted"])
        self.assertIn("KNOWN_REQUEST_COST_EXCEEDS_ORIGINAL_LIMIT:max_requests", value["collection_reasons"])

    def test_explicit_collection_rejects_offline_and_retry_policy(self):
        self.config(capture=dict(offline=False, retry_failed=True),
                    limits=dict(max_requests=512, max_sdk_calls=10000, max_http_attempts=32768))
        value = self.plan(collect_missing=True)
        self.assertFalse(value["collection_admitted"])
        self.assertIn("COLLECTION_RETRY_POLICY_FORBIDDEN", value["collection_reasons"])
        self.config(capture=dict(offline=True))
        value = self.plan(collect_missing=True)
        self.assertFalse(value["collection_admitted"])
        self.assertIn("COLLECTION_ONLINE_POLICY_REQUIRED", value["collection_reasons"])

    def test_cost_is_per_security_not_date_range(self):
        for start in ("1990-01-01", self.observed):
            spec = native.RequestSpec("adjustment_factors", dict(codes=["sh.600001", "sz.000001"],
                start_date=start, end_date=self.observed), self.observed)
            self.assertEqual(preflight.request_cost(spec.to_dict(), 1),
                             dict(requests=1, sdk_reserved=4, http_reserved=0))
        self.assertEqual(preflight.request_cost(native.RequestSpec("mainboard_sse", {}, self.local,
            current=True).to_dict(), 3), dict(requests=1, sdk_reserved=0, http_reserved=3))

    def test_unsupported_cost_domain_rejected(self):
        with self.assertRaisesRegex(ValueError, "UNSUPPORTED_ENDPOINT"):
            preflight.request_cost(dict(endpoint="invented", parameters={}), 1)
        with self.assertRaisesRegex(ValueError, "HTTP_POLICY_REQUIRED"):
            preflight.request_cost(native.RequestSpec("calendar", dict(start_date=self.local,
                end_date=self.local), self.local).to_dict(), True)
