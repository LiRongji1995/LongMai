"""TEST_FIXTURE: small dependency plans; isolated files, no real DB/network."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import codex_daily_plan_v1 as daily


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


class DailyPlanFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="TEST_FIXTURE_daily_plan_")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.scope = "TEST_FIXTURE-current-scope"
        self.request = {"business_date": "2026-10-23", "cutoff": "2026-10-23T16:00:00+08:00",
                        "strategy_id": "TEST_FIXTURE", "strategy_version": "V1",
                        "source_version": "SOURCE_V1", "config": {"TEST_FIXTURE": True}}
        self.source = self.market("2026-10-23")
        self.nodes = [self.node("facts/23", [self.source["id"]]),
                      self.node("core/26", ["facts/23"]),
                      self.node("result/view", ["core/26"]),
                      self.node("outcomes/26", ["result/view"])]
        self.policy = {"max_managed_bytes": 1024, "min_free_bytes": 16}
        self.capacity = {"scan_complete": True, "totals": {"unique_logical_bytes": 100},
                         "roots": [{"name": "TEST_FIXTURE", "filesystem": {"free_bytes": 4096}}]}

    def market(self, day):
        return {"id": "market/" + day, "reference_status": "VERIFIED", "authentication": "ARCHIVED_FACTS",
                "identity": {"content_hash": sha("market" + day), "origin_id": "fixture-provider",
                             "source_version": "V1", "business_date": day,
                             "pit": {"coverage_type": "TEST_FIXTURE", "actual_visible_at": day + "T15:05:00+08:00",
                                     "receipt_hash": sha("receipt" + day)}}}

    def node(self, node_id, parents, **extra):
        return {"id": node_id, "parents": parents, "kind": "TEST_FIXTURE", "version": "V1",
                "config": {"window": 25}, "estimate": {"output_bytes": 10, "temporary_peak_bytes": 20,
                                                        "cache_growth_bytes": 0}, **extra}

    def plan(self, **kwargs):
        values = dict(request=self.request, nodes=self.nodes, source_observations=[self.source],
                      scope_id=self.scope, policy=self.policy, capacity_observation=self.capacity,
                      verify_result=self.verify)
        values.update(kwargs)
        return daily.build_plan(**values)

    def verify(self, manifest):
        artifact = manifest["artifact"]
        raw = Path(artifact["path"]).read_bytes()
        return len(raw) == artifact["bytes"] and hashlib.sha256(raw).hexdigest() == artifact["sha256"]

    def result(self, item, status="SUCCEEDED"):
        artifact = None
        if status == "SUCCEEDED":
            raw = ("TEST_FIXTURE result " + item["dependency_key"]).encode()
            path = self.root / (item["dependency_key"] + ".result")
            path.write_bytes(raw)
            artifact = {"path": str(path), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        return daily.make_result_manifest(item, status=status, scope_id="TEST_FIXTURE-prior-scope",
                                          recorded_at="2026-10-23T17:00:00+08:00", artifact=artifact)

    def complete(self, **kwargs):
        results = list(kwargs.pop("prior_results", []))
        for _ in range(20):
            plan = self.plan(prior_results=results, **kwargs)
            ready = [node for node in plan["nodes"] if node["status"] == "BUILD" and node["ready"]]
            if not ready:
                return plan, results
            results.extend(self.result(node) for node in ready)
        self.fail("fixture graph did not converge")

    def test_complete_graph_reuses_across_scope_without_granting_source_authority(self):
        plan, results = self.complete()
        self.assertEqual([node["status"] for node in plan["nodes"]], ["REUSE"] * 4)
        self.assertTrue(all(node["needs_authentication"] for node in plan["nodes"]))
        new = self.plan(prior_results=results, scope_id="TEST_FIXTURE-another-scope")
        self.assertTrue(all(node["status"] == "REUSE" for node in new["nodes"]))
        self.assertFalse(new["execution_authorized"])
        self.assertEqual(new["source_authentication"], "NOT_PERFORMED_BY_PLANNER")
        self.assertEqual(new["new_authentication_scope_count"], 0)

    def test_build_parent_output_is_unknown_until_recorded(self):
        plan = self.plan()
        by_id = {node["node_id"]: node for node in plan["nodes"]}
        self.assertTrue(by_id["facts/23"]["ready"])
        self.assertEqual(by_id["core/26"]["status"], "BUILD")
        self.assertFalse(by_id["core/26"]["ready"])
        self.assertIsNone(by_id["core/26"]["dependency_key"])
        with self.assertRaisesRegex(daily.PlanError, "RESOLVED_NODE"):
            daily.make_result_manifest(by_id["core/26"], status="UNKNOWN", scope_id=self.scope)

    def test_source_change_propagates_only_downstream(self):
        other = self.market("2026-10-22")
        nodes = self.nodes + [self.node("independent", [other["id"]])]
        _, results = self.complete(nodes=nodes, source_observations=[self.source, other])
        changed = deepcopy(self.source)
        changed["identity"]["content_hash"] = sha("new-content")
        plan = self.plan(nodes=nodes, source_observations=[changed, other], prior_results=results)
        by_id = {node["node_id"]: node for node in plan["nodes"]}
        self.assertEqual(by_id["independent"]["status"], "REUSE")
        self.assertTrue(all(by_id[node["id"]]["status"] == "BUILD" for node in self.nodes))

    def test_same_bytes_different_origin_pit_and_parent_set_do_not_reuse(self):
        _, results = self.complete()
        for path, new in (("origin_id", "different-provider"), ("source_version", "V2"),
                          ("receipt_hash", sha("different-receipt")),
                          ("actual_visible_at", "2026-10-23T15:06:00+08:00")):
            with self.subTest(path=path):
                source = deepcopy(self.source)
                target = source["identity"]["pit"] if path in {"receipt_hash", "actual_visible_at"} else source["identity"]
                target[path] = new
                plan = self.plan(source_observations=[source], prior_results=results)
                self.assertEqual(plan["nodes"][0]["status"], "BUILD")
        extra = self.market("2026-10-22")
        nodes = deepcopy(self.nodes)
        nodes[0]["parents"].append(extra["id"])
        self.assertEqual(self.plan(nodes=nodes, source_observations=[self.source, extra],
                                   prior_results=results)["nodes"][0]["status"], "BUILD")

    def test_configuration_version_date_and_cutoff_are_identity(self):
        _, results = self.complete()
        for key, value in (("business_date", "2026-10-26"), ("cutoff", "2026-10-23T16:01:00+08:00"),
                           ("strategy_version", "V2"), ("source_version", "V2"), ("config", {"window": 50})):
            with self.subTest(key=key):
                request = {**self.request, key: value}
                self.assertEqual(self.plan(request=request, prior_results=results)["nodes"][0]["status"], "BUILD")
        nodes = deepcopy(self.nodes)
        nodes[0]["config"]["window"] = 30
        self.assertEqual(self.plan(nodes=nodes, prior_results=results)["nodes"][0]["status"], "BUILD")

    def test_cross_day_plan_keeps_complete_old_fact_branch(self):
        old_context = {key: self.request[key] for key in ("business_date", "cutoff")}
        old_nodes = [self.node("facts/23", [self.source["id"]], context=old_context)]
        _, results = self.complete(nodes=old_nodes)
        new_source = self.market("2026-10-26")
        request = {**self.request, "business_date": "2026-10-26", "cutoff": "2026-10-26T16:00:00+08:00"}
        nodes = old_nodes + [self.node("facts/26", [new_source["id"]]),
                             self.node("core/27", ["facts/23", "facts/26"]),
                             self.node("result/27", ["core/27"]), self.node("outcomes/27", ["result/27"])]
        first = self.plan(request=request, nodes=nodes, source_observations=[self.source, new_source], prior_results=results)
        self.assertEqual(next(node for node in first["nodes"] if node["node_id"] == "facts/23")["status"], "REUSE")
        final, snapshots = self.complete(request=request, nodes=nodes, source_observations=[self.source, new_source], prior_results=results)
        self.assertEqual(len(snapshots), 5)
        self.assertTrue(all(node["status"] == "REUSE" for node in final["nodes"]))
        self.assertEqual(sum(snapshot["node_id"] == "facts/23" for snapshot in snapshots), 1)

    def test_unknown_missing_failed_and_visible_after_cutoff_are_not_empty(self):
        for status in ("UNKNOWN", "FAILED"):
            source = {"id": self.source["id"], "reference_status": status}
            plan = self.plan(source_observations=[source])
            expected = "BLOCKED" if status == "FAILED" else "UNKNOWN"
            self.assertTrue(all(node["status"] == expected for node in plan["nodes"]))
        self.assertTrue(all(node["status"] == "UNKNOWN" for node in self.plan(source_observations=[])["nodes"]))
        source = deepcopy(self.source)
        source["identity"]["pit"]["actual_visible_at"] = "2026-10-23T17:00:00+08:00"
        self.assertEqual(self.plan(source_observations=[source])["nodes"][0]["status"], "UNKNOWN")

    def test_failed_and_unknown_attempts_do_not_retry(self):
        first = self.plan()["nodes"][0]
        for status in ("FAILED", "UNKNOWN"):
            plan = self.plan(prior_results=[self.result(first, status)])
            self.assertTrue(all(node["status"] == "BLOCKED" for node in plan["nodes"]))

    def test_every_reuse_calls_artifact_verifier_and_tamper_is_unknown(self):
        _, results = self.complete()
        calls = []
        def verify(value):
            calls.append(value["node_id"])
            return self.verify(value)
        self.plan(prior_results=results, verify_result=verify)
        self.assertEqual(len(calls), 4)
        Path(results[0]["artifact"]["path"]).write_bytes(b"TEST_FIXTURE-tampered")
        self.assertEqual(self.plan(prior_results=results)["nodes"][0]["status"], "UNKNOWN")
        self.assertEqual(self.plan(prior_results=results, verify_result=None)["nodes"][0]["status"], "UNKNOWN")

    def test_current_authentication_only_matches_current_scope(self):
        source = {**self.source, "authentication": "CURRENT_SCOPE_FULL", "verified_scope_id": self.scope}
        self.assertFalse(self.plan(source_observations=[source])["nodes"][0]["needs_authentication"])
        self.assertTrue(self.plan(source_observations=[source], scope_id="other")["nodes"][0]["needs_authentication"])

    def test_persistent_snapshot_is_small_immutable_and_tamper_checked(self):
        snapshot = self.result(self.plan()["nodes"][0])
        destination = self.root / "snapshots" / "result.json"
        daily.record_result_snapshot(destination, snapshot)
        self.assertEqual(daily.load_result_snapshots([destination]), [snapshot])
        with self.assertRaises(ValueError):
            daily.record_result_snapshot(destination, snapshot)
        value = json.loads(destination.read_text())
        value["dependency_identity"]["request"]["business_date"] = "2026-10-26"
        destination.write_text(json.dumps(value))
        with self.assertRaisesRegex(daily.PlanError, "HASH_MISMATCH"):
            daily.load_result_snapshots([destination])

    def test_parent_order_stable_duplicate_cycle_and_incomplete_pit_rejected(self):
        other = self.market("2026-10-22")
        nodes = [self.node("facts", [self.source["id"], other["id"]])]
        first = self.plan(nodes=nodes, source_observations=[self.source, other])
        nodes[0]["parents"].reverse()
        second = self.plan(nodes=nodes, source_observations=[other, self.source])
        self.assertEqual(first["nodes"][0]["dependency_key"], second["nodes"][0]["dependency_key"])
        for bad in ([self.node("a", ["b"]), self.node("b", ["a"])],
                    [self.node("a", [self.source["id"], self.source["id"]])]):
            with self.assertRaises(daily.PlanError):
                self.plan(nodes=bad)
        source = deepcopy(self.source)
        del source["identity"]["pit"]
        with self.assertRaisesRegex(daily.PlanError, "PIT_IDENTITY"):
            self.plan(source_observations=[source])

    def test_capacity_consumes_observation_once_preserves_explicit_policy_and_cache_gate(self):
        with patch("scripts.codex_capacity.storage_report", side_effect=AssertionError("must not scan")):
            budget = self.plan()["budget"]
        self.assertEqual(budget["metadata_scans_performed"], 0)
        self.assertEqual(budget["max_managed_bytes"], 1024)
        self.assertEqual(budget["projected_peak_logical_bytes"], 160)
        self.assertTrue(budget["allowed"])
        self.assertFalse(self.plan(policy=None)["budget"]["allowed"])
        self.assertFalse(self.plan(policy={"max_managed_bytes": 150, "min_free_bytes": 16})["budget"]["allowed"])
        nodes = deepcopy(self.nodes)
        nodes[0]["estimate"]["cache_growth_bytes"] = 5
        self.assertEqual(self.plan(nodes=nodes)["budget"]["cache_gate"], "UNKNOWN")
        self.assertEqual(self.plan(nodes=nodes, cache_admission={"allowed": False})["budget"]["cache_gate"], "BLOCKED")
        self.assertTrue(self.plan(nodes=nodes, cache_admission={"allowed": True})["budget"]["allowed"])
        self.assertFalse(self.plan(capacity_observation={"scan_complete": False})["budget"]["allowed"])

    def test_saved_view_default_never_promotes_new_session_or_outcomes(self):
        artifact = self.result(self.plan()["nodes"][0])["artifact"]
        view = {"status": "VERIFIED_SAVED_RESULT_VIEW", "view_id": sha("view"), "archive_id": sha("archive"),
                "archive_profile": "TEST_FIXTURE_ARCHIVE", "local_seal_authenticated_now": True,
                "all_five_exports_verified_now": True, "last_full_authenticated_at": "2026-10-25T16:00:00+08:00",
                "summary": {"run_id": "TEST_FIXTURE-run", "input_hash": sha("input"),
                            "canonical_content_hash": sha("canonical"), "observation_date": "2026-10-23",
                            "target_session": "2026-10-26", "decision_cutoff": self.request["cutoff"],
                            "strategy_id": "TEST_FIXTURE", "coverage_type": "TEST_FIXTURE"},
                "artifacts": {name: artifact for name in (
                    "observation.json", "observation.csv", "all.csv", "focus.csv", "unselected.csv")}}
        kwargs = daily.saved_view_plan_request(view)
        current = daily.build_plan(**kwargs, verify_result=self.verify)
        self.assertEqual([node["status"] for node in current["nodes"]], ["REUSE", "REUSE", "REUSE", "UNKNOWN"])
        future = daily.build_plan(**daily.saved_view_plan_request(view, "2026-10-27"), verify_result=self.verify)
        new = [node for node in future["nodes"] if "target-" in node["node_id"] or node["node_id"] == "core/2026-10-27"]
        self.assertTrue(new)
        self.assertTrue(all(node["status"] == "UNKNOWN" for node in new))
        self.assertTrue(all(node["needs_authentication"] for node in current["nodes"]))

    def test_cli_is_read_only_and_cannot_accept_a_verifier_from_json(self):
        path = self.root / "TEST_FIXTURE_request.json"
        path.write_text(json.dumps(dict(request=self.request, nodes=self.nodes,
                                       source_observations=[self.source], scope_id=self.scope)))
        before = sorted(item.name for item in self.root.iterdir())
        process = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/codex_daily_plan_v1.py"),
                                  "plan", "--input", str(path)], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout)["source_authentication"], "NOT_PERFORMED_BY_PLANNER")
        self.assertEqual(before, sorted(item.name for item in self.root.iterdir()))


if __name__ == "__main__":
    unittest.main()
