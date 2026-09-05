"""Local-only HTTP acceptance evidence; observations and model outputs are real.

No orders are transmitted. Cash is an explicit paper-account assumption.
Use submit, then collect after the bounded durable task has finished; check again
after restarting app.py to verify that records and observations survived.
"""
import argparse
import json
from pathlib import Path
import sys

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
EVIDENCE = ROOT.parent / "review/astra-master"
client = requests.Session()
client.trust_env = False  # fixed loopback target only; never changes provider proxies.
BASE = "http://127.0.0.1:8000"


def call(method, path, **kw):
    response = client.request(method, BASE + path, timeout=30, **kw)
    response.raise_for_status()
    return response.json()


def save(name, value):
    path = EVIDENCE / name
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("submit", "collect", "restart-check"))
    parser.add_argument("--label", default="runtime-accepted")
    args = parser.parse_args()
    import re
    if not re.fullmatch(r"[a-z0-9-]+", args.label):
        raise ValueError("safe evidence label required")
    label = args.label
    if args.action == "submit":
        health = call("GET", "/api/health")
        assert health["policy_version"] == "quant-hard-risk-v2"
        snapshot = (ROOT / "data/core/active_snapshot.txt").read_text().strip()
        request = dict(model_id="csi300-alpha158-legacy", model_version="bc5480d90dbefc1d",
            snapshot_id=snapshot, instruments=["000001.SZ"], mode="RESEARCH", cash=100000, include_llm=True)
        job = call("POST", "/api/recommendations", json=request)
        save(label + "-submission.json", {"request": request, "job": job, "health": health})
        print(json.dumps(job))
        return
    submission = json.loads((EVIDENCE / (label + "-submission.json")).read_text(encoding="utf-8"))
    job = call("GET", "/api/jobs/" + submission["job"]["job_id"])
    if job["state"] != "SUCCEEDED":
        if job["state"] in ("FAILED", "CANCELLED", "INTERRUPTED"):
            save(label + "-failure.json", job)
        print(json.dumps({"state": job["state"], "progress": job["progress"], "error": job.get("error")}))
        raise SystemExit(2)
    final = job["final_recommendation"]
    rid, run_id = final["items"][0]["recommendation_id"], final["recommendation_run_id"]
    assert job["best_stock"] == job["top5"][0] == job["top20"][0] == final["items"][0]
    assert final["status"] == "RESEARCH_ONLY"
    assert job["quant"]["source_code_hash"] and job["quant"]["git_commit"]
    replay = call("POST", "/api/recommendation-runs/" + run_id + "/replay")
    assert replay == final
    legacy = call("POST", "/api/recommendation-runs/rec_365a610222d4cb4424633f309c436453/replay")
    assert legacy["policy"]["version"] == "quant-hard-risk-v1"
    if args.action == "collect":
        from stockfish_core.storage import SQLiteStore
        from stockfish_core.tracker import RecommendationTracker
        tracker = RecommendationTracker(SQLiteStore(ROOT / "data/stockfish.sqlite3"))
        # Today precedes the first eligible open: no future price is invented.
        outcomes = [tracker.evaluate(rid, [], [], horizon=h) for h in (7, 30, 90)]
        assert all(o["state"] == "NOT_MATURED" and o["return"] is None for o in outcomes)
        record = call("GET", "/api/recommendations/" + rid)
        assert len(record["evaluations"]) == 3
        save(label + "-result.json", job)
        save(label + "-record.json", record)
        save(label + "-verification.json", {"replay_equal": True, "legacy_replay_equal": True,
             "canonical_presentations_equal": True, "record": rid, "real_market_data": True,
             "cash_basis": "PAPER_ACCOUNT_ASSUMPTION", "actual_fills": False,
             "evaluation_states": [o["state"] for o in outcomes], "health": call("GET", "/api/health")})
    else:
        before = json.loads((EVIDENCE / (label + "-record.json")).read_text(encoding="utf-8"))
        after = call("GET", "/api/recommendations/" + rid)
        assert before == after
        assert any(r["recommendation_id"] == rid for r in call("GET", "/api/recommendations/history"))
        save(label + "-restart-verification.json", {"record_and_evaluations_unchanged": True,
            "job_state": job["state"], "exact_replay": True, "legacy_replay": True,
            "health": call("GET", "/api/health")})
    print(json.dumps({"record": rid, "run": run_id, "state": job["state"],
        "policy": final["policy"]["version"], "quant_score": final["items"][0]["quant_score"], "verified": args.action}))


if __name__ == "__main__":
    main()
