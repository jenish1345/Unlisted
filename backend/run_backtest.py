import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv

from ai_pipeline import (
    PipelineError,
    ThesisOutput,
    run_pitch,
    run_synthesis,
    run_thesis,
)
from github_extractor import (
    GitHubExtractionError,
    extract_github_profile,
)
from verifier import VerifierError, verify_thesis_with_gemini

# Load environment variables from .env
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=True)

# 5 Real Teammates & Accounts
TEST_CANDIDATES = [
    "jenish1345",
    "samuveljohnson1416",
    "Berosin",
    "JOSEMICHAELRAJ",
    "george-o5",
]

# Estimated token costs (GPT-4o-mini: $0.15/1M input, $0.60/1M output; Gemini-1.5-flash: $0.075/1M input, $0.30/1M output)
def estimate_cost(input_chars: int, output_chars: int, is_gemini: bool = False) -> float:
    input_tokens = input_chars / 4.0
    output_tokens = output_chars / 4.0
    if is_gemini:
        return (input_tokens * 0.000000075) + (output_tokens * 0.00000030)
    return (input_tokens * 0.00000015) + (output_tokens * 0.00000060)


async def execute_single_run(
    candidate: str,
    company: Dict[str, Any],
    github_cache: Dict[str, Any]
) -> Dict[str, Any]:
    run_record: Dict[str, Any] = {
        "candidate": candidate,
        "company": company["id"],
        "company_name": company["name"],
        "status": "pending",
        "stages_completed": [],
        "failed_stage": None,
        "error_message": None,
        "stage_latencies": {},
        "thesis": None,
        "verification": None,
        "pitch": None,
        "total_latency_seconds": 0.0,
        "cost_estimate_usd": 0.0,
        "retries_occurred": 0
    }

    start_total = time.perf_counter()
    total_cost = 0.0

    # 1. Extraction (re-use candidate extraction if already fetched to respect GitHub limits)
    t0 = time.perf_counter()
    try:
        if candidate in github_cache:
            github_data = github_cache[candidate]
            run_record["stage_latencies"]["extraction"] = round(time.perf_counter() - t0, 3)
        else:
            github_data = await extract_github_profile(candidate)
            github_cache[candidate] = github_data
            run_record["stage_latencies"]["extraction"] = round(time.perf_counter() - t0, 3)

        run_record["stages_completed"].append("extraction")
    except Exception as err:
        run_record["stage_latencies"]["extraction"] = round(time.perf_counter() - t0, 3)
        run_record["status"] = "failed"
        run_record["failed_stage"] = "extraction"
        run_record["error_message"] = str(err)
        run_record["total_latency_seconds"] = round(time.perf_counter() - start_total, 3)
        return run_record

    # 2. Synthesis
    t0 = time.perf_counter()
    try:
        synthesis_result = await run_synthesis(github_data)
        run_record["stage_latencies"]["synthesis"] = round(time.perf_counter() - t0, 3)
        run_record["stages_completed"].append("synthesis")
        total_cost += estimate_cost(4000, 800)
    except Exception as err:
        run_record["stage_latencies"]["synthesis"] = round(time.perf_counter() - t0, 3)
        run_record["status"] = "failed"
        run_record["failed_stage"] = "synthesis"
        run_record["error_message"] = str(err)
        run_record["total_latency_seconds"] = round(time.perf_counter() - start_total, 3)
        return run_record

    # 3. Thesis Formulation
    valid_repos = [r["name"] for r in github_data.get("repos", [])]
    t0 = time.perf_counter()
    try:
        thesis_result = await run_thesis(
            synthesis=synthesis_result,
            company=company,
            valid_repos=valid_repos,
            conservative=False
        )
        run_record["stage_latencies"]["thesis"] = round(time.perf_counter() - t0, 3)
        run_record["stages_completed"].append("thesis")
        run_record["thesis"] = thesis_result.model_dump()
        total_cost += estimate_cost(3000, 600)
    except Exception as err:
        run_record["stage_latencies"]["thesis"] = round(time.perf_counter() - t0, 3)
        run_record["status"] = "failed"
        run_record["failed_stage"] = "thesis"
        run_record["error_message"] = str(err)
        run_record["total_latency_seconds"] = round(time.perf_counter() - start_total, 3)
        return run_record

    # 4. Independent Gemini Verification Audit
    t0 = time.perf_counter()
    try:
        raw_claims = [item.model_dump() for item in thesis_result.supporting_evidence]
        dev_evidence = [e.model_dump() for e in synthesis_result.evidence]

        verification_result = await verify_thesis_with_gemini(
            role_title=thesis_result.role_title,
            claims=raw_claims,
            developer_evidence=dev_evidence,
            company_name=company["name"],
            company_evidence=company.get("evidence", [])
        )
        run_record["stage_latencies"]["verification"] = round(time.perf_counter() - t0, 3)
        run_record["stages_completed"].append("verification")
        run_record["verification"] = verification_result.model_dump()
        total_cost += estimate_cost(2500, 300, is_gemini=True)
    except Exception as err:
        run_record["stage_latencies"]["verification"] = round(time.perf_counter() - t0, 3)
        run_record["status"] = "failed"
        run_record["failed_stage"] = "verification"
        run_record["error_message"] = str(err)
        run_record["total_latency_seconds"] = round(time.perf_counter() - start_total, 3)
        return run_record

    # Conservative calibration if OVERREACHING
    active_thesis = thesis_result
    if verification_result.verdict == "OVERREACHING":
        try:
            conservative_thesis = await run_thesis(
                synthesis=synthesis_result,
                company=company,
                valid_repos=valid_repos,
                conservative=True
            )
            active_thesis = conservative_thesis
            run_record["retries_occurred"] += 1
            run_record["thesis_calibrated"] = active_thesis.model_dump()
            total_cost += estimate_cost(3000, 600)
        except Exception:
            pass

    # 5. Targeted Pitch Outreach
    t0 = time.perf_counter()
    try:
        candidate_name = github_data.get("name") or candidate
        supported_claims = [item.model_dump() for item in active_thesis.supporting_evidence]

        pitch_result = await run_pitch(
            thesis=active_thesis,
            verified_claims=supported_claims,
            company=company,
            candidate_name=candidate_name
        )
        run_record["stage_latencies"]["pitch"] = round(time.perf_counter() - t0, 3)
        run_record["stages_completed"].append("pitch")
        run_record["pitch"] = pitch_result.model_dump()
        total_cost += estimate_cost(2000, 400)
        run_record["status"] = "success"
    except Exception as err:
        run_record["stage_latencies"]["pitch"] = round(time.perf_counter() - t0, 3)
        run_record["status"] = "failed"
        run_record["failed_stage"] = "pitch"
        run_record["error_message"] = str(err)

    run_record["total_latency_seconds"] = round(time.perf_counter() - start_total, 3)
    run_record["cost_estimate_usd"] = round(total_cost, 6)
    return run_record


async def main():
    # Reload env
    load_dotenv(BASE_DIR / ".env", override=True)

    with open(BASE_DIR / "company_signals.json", "r", encoding="utf-8") as f:
        companies = json.load(f)

    print(f"==================================================")
    print(f"STARTING UNLISTED HONEST BACKTEST")
    print(f"Candidates ({len(TEST_CANDIDATES)}): {TEST_CANDIDATES}")
    print(f"Companies ({len(companies)}): {[c['id'] for c in companies]}")
    print(f"Total Runs: {len(TEST_CANDIDATES) * len(companies)}")
    print(f"OpenAI Key set: {bool(os.getenv('OPENAI_API_KEY'))}")
    print(f"Gemini Key set: {bool(os.getenv('GEMINI_API_KEY'))}")
    print(f"GitHub Token set: {bool(os.getenv('GITHUB_TOKEN'))}")
    print(f"==================================================\n")

    results: List[Dict[str, Any]] = []
    github_cache: Dict[str, Any] = {}

    run_index = 0
    for candidate in TEST_CANDIDATES:
        for company in companies:
            run_index += 1
            print(f"[{run_index}/25] Running: {candidate} -> {company['name']}...", end=" ", flush=True)
            result = await execute_single_run(candidate, company, github_cache)
            results.append(result)
            if result["status"] == "success":
                verdict = result["verification"]["verdict"] if result.get("verification") else "N/A"
                print(f"DONE in {result['total_latency_seconds']}s (Verdict: {verdict})")
            else:
                print(f"FAILED at {result['failed_stage']}: {result['error_message']}")

    # Save to results.json
    output_path = BASE_DIR / "results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print(f"\nSaved all 25 run results to {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
