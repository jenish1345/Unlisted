import json
from pathlib import Path

results_file = Path(__file__).resolve().parent / "results.json"
with open(results_file, "r") as f:
    data = json.load(f)

print(f"Total Runs Logged: {len(data)}")
successes = [d for d in data if d.get("status") == "success"]
failures = [d for d in data if d.get("status") != "success"]
print(f"Successful Runs: {len(successes)}")
print(f"Failed Runs: {len(failures)}")

# Breakdown of failures
fail_stages = {}
for f in failures:
    stg = f.get("failed_stage", "unknown")
    fail_stages[stg] = fail_stages.get(stg, 0) + 1
print(f"Failure Breakdown by Stage: {fail_stages}")

print("\n================== SUCCESSFUL RUN DETAILS ==================")
for idx, s in enumerate(successes, 1):
    cand = s["candidate"]
    comp = s["company_name"]
    verdict = s["verification"]["verdict"]
    reason = s["verification"]["reason"]
    thesis = s["thesis"]
    role = thesis["role_title"]
    just = thesis["justification"]
    claims = thesis["supporting_evidence"]
    latencies = s["stage_latencies"]
    total_sec = s["total_latency_seconds"]

    print(f"\n[{idx}] {cand} -> {comp}")
    print(f"  Role Title: {role}")
    print(f"  Verdict: {verdict}")
    print(f"  Reason: {reason}")
    print(f"  Justification: {just}")
    print("  Claims & Sources:")
    for c in claims:
        print(f"    * Claim: {c['claim']}")
        print(f"      Source: {c['source']}")
    print(f"  Stage Latencies: {latencies}")
    print(f"  Total Latency: {total_sec}s")
    print(f"  Pitch Subject: {s['pitch']['subject']}")
