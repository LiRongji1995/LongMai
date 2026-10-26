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

    def test_same_bytes_new_inode_is_rejected_for_authenticated_calendar(self):
        path = Path(self.calendar.metadata["raw"]["path"])
        replacement = path.with_name("TEST_FIXTURE-replacement")
        replacement.write_bytes(path.read_bytes())
        replacement.replace(path)
        with self.assertRaises(ValueError):
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
