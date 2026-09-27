import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from ai_pipeline import (
    PipelineError,
    ThesisOutput,
    run_pitch,
    run_synthesis,
    run_thesis,
)
from github_extractor import (
    GitHubExtractionError,
    GitHubNoPublicReposError,
    GitHubRateLimitError,
    GitHubUserNotFoundError,
    extract_github_profile,
)
from llm_transport import LLMError
from verifier import VerificationResult, VerifierError, verify_thesis_with_gemini

# Load environment variables
load_dotenv()

log = logging.getLogger("unlisted")
if not log.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log.addHandler(_handler)
    log.setLevel(logging.INFO)
    log.propagate = False

app = FastAPI(
    title="Unlisted API",
    description="Full-stack AI talent matching pipeline connecting proven GitHub evidence with unlisted company roles.",
    version="1.0.0"
)

# CORS configuration for local frontend development and Cloud Run deployment
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent


# ============================================================================
# Request & Response Models
# ============================================================================

class AnalyzeRequest(BaseModel):
    github_username: str = Field(..., min_length=1, description="GitHub username to evaluate")
    company_id: str = Field(..., min_length=1, description="Target company ID from company_signals.json")
    demo: Optional[bool] = Field(False, description="Explicitly enable demo safety playback mode")


class CompanyBrief(BaseModel):
    id: str
    name: str
    description: str


# ============================================================================
# Dynamic Data Loaders (Request-Time)
# ============================================================================

def load_company_signals() -> List[Dict[str, Any]]:
    """
    Load company signals dynamically from disk at request time.
    Enables instant modifications between practice runs without redeployment.
    """
    signals_file = BASE_DIR / "company_signals.json"
    if not signals_file.exists():
        raise HTTPException(status_code=500, detail="company_signals.json is missing on the server.")
    with open(signals_file, "r", encoding="utf-8") as f:
        return json.load(f)


def load_demo_cache() -> Dict[str, Any]:
    """Load pre-computed fallback response from demo_cache.json."""
    cache_file = BASE_DIR / "demo_cache.json"
    if not cache_file.exists():
        raise HTTPException(status_code=500, detail="demo_cache.json is missing on the server.")
    with open(cache_file, "r", encoding="utf-8") as f:
        return json.load(f)


# ============================================================================
# API Endpoints
# ============================================================================

@app.get("/api/health")
async def health_check():
    """Service health check endpoint."""
    return {
        "status": "healthy",
        "service": "unlisted-backend",
        "openai_configured": bool(os.getenv("OPENAI_API_KEY")),
        "gemini_configured": bool(os.getenv("GEMINI_API_KEY")),
        "github_token_configured": bool(os.getenv("GITHUB_TOKEN")),
    }


@app.get("/api/companies", response_model=List[CompanyBrief])
async def get_companies():
    """
    Returns list of hardcoded companies for the frontend dropdown.
    Loaded dynamically from company_signals.json.
    """
    try:
        data = load_company_signals()
        return [
            CompanyBrief(
                id=c["id"],
                name=c["name"],
                description=c.get("description", "High-growth engineering organization")
            )
            for c in data
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load company signals: {str(e)}")


async def demo_stream_generator() -> AsyncGenerator[str, None]:
    """Replay pre-recorded successful run stage-by-stage with smooth pacing."""
    cache = load_demo_cache()
    stages = cache.get("stages", {})

    stage_order = ["extraction", "synthesis", "thesis", "verification", "pitch"]

    for stage_name in stage_order:
        await asyncio.sleep(0.7)  # Realistic pacing for live demo presentations
        stage_data = stages.get(stage_name)
        if stage_data:
            payload = {
                "stage": stage_name,
                "status": "done",
                "data": stage_data
            }
            yield f"data: {json.dumps(payload)}\n\n"


def sse(payload: Dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def describe_error(stage: str, err: Exception) -> tuple:
    """
    (error_kind, message) that is safe to show users. Raw provider text and tracebacks go to the
    server log only.
    """
    if isinstance(err, LLMError):
        log.warning("%s failed [%s]: %s | raw: %s", stage, err.kind, err, err.detail[:1000])
        return err.kind, str(err)
    if isinstance(err, GitHubUserNotFoundError):
        return "not_found", str(err)
    if isinstance(err, GitHubNoPublicReposError):
        return "no_repos", str(err)
    if isinstance(err, GitHubRateLimitError):
        return "rate_limit", str(err)
    if isinstance(err, GitHubExtractionError):
        log.warning("%s failed (GitHub): %s", stage, err)
        return "upstream", "GitHub returned an unexpected error. Try again in a moment."
    if isinstance(err, (PipelineError, VerifierError)):
        log.warning("%s failed: %s", stage, err)
        return "invalid_output", str(err)
    if isinstance(err, (json.JSONDecodeError, ValidationError)):
        log.warning("%s produced invalid output: %s", stage, err)
        return "invalid_output", "The model's output didn't match the expected format."
    log.exception("%s failed unexpectedly", stage, exc_info=err)
    return "internal", f"Unexpected server error during {stage}."


def error_event(stage: str, err: Exception) -> str:
    kind, message = describe_error(stage, err)
    return sse({"stage": stage, "status": "error", "error_kind": kind, "message": message})


def unresolved_event(message: str, audit_round: int, verification: Optional[VerificationResult] = None) -> str:
    """No role survived verification: say so instead of pitching an unverified claim."""
    return sse({
        "stage": "verification",
        "status": "unresolved",
        "round": audit_round,
        "message": message,
        "data": verification.model_dump() if verification else None,
    })


def supported_claims(thesis: ThesisOutput, verification: VerificationResult) -> List[Dict[str, str]]:
    """Only the claims Gemini marked SUPPORTED individually (PARTIALLY_SUPPORTED is not enough)."""
    ok = {cv.index for cv in verification.claim_verdicts if cv.verdict == "SUPPORTED"}
    return [item.model_dump() for i, item in enumerate(thesis.supporting_evidence) if i in ok]


async def live_pipeline_stream_generator(
    github_username: str,
    company_id: str
) -> AsyncGenerator[str, None]:
    """
    Execute the multi-stage AI talent matching pipeline sequentially.
    Emits SSE events in the exact contract shape. Stops gracefully on any error.
    """
    # 1. Company Verification
    try:
        companies = load_company_signals()
        target_company = next((c for c in companies if c["id"] == company_id), None)
        if not target_company:
            yield sse({
                "stage": "extraction",
                "status": "error",
                "error_kind": "not_found",
                "message": f"Company ID '{company_id}' is not recognized in company_signals.json."
            })
            return
    except Exception as err:
        yield error_event("extraction", err)
        return

    # STAGE 1: EXTRACTION
    try:
        github_data = await extract_github_profile(github_username)
        yield sse({"stage": "extraction", "status": "done", "data": github_data})
    except Exception as err:
        yield error_event("extraction", err)
        return

    # STAGE 2: SYNTHESIS
    try:
        synthesis_result = await run_synthesis(github_data)
        yield sse({"stage": "synthesis", "status": "done", "data": synthesis_result.model_dump()})
    except Exception as err:
        yield error_event("synthesis", err)
        return

    # STAGE 4: THESIS
    valid_repos = [r["name"] for r in github_data.get("repos", [])]
    try:
        thesis_result = await run_thesis(
            synthesis=synthesis_result,
            company=target_company,
            valid_repos=valid_repos,
            conservative=False
        )
        yield sse({"stage": "thesis", "status": "done", "data": thesis_result.model_dump()})
    except Exception as err:
        yield error_event("thesis", err)
        return

    async def audit(thesis: ThesisOutput) -> VerificationResult:
        # Strict isolation: Gemini only sees raw thesis claims + evidence, never the reasoning behind them
        return await verify_thesis_with_gemini(
            role_title=thesis.role_title,
            claims=[{"index": i, **item.model_dump()} for i, item in enumerate(thesis.supporting_evidence)],
            developer_evidence=[e.model_dump() for e in synthesis_result.evidence],
            company_name=target_company["name"],
            company_evidence=target_company.get("evidence", [])
        )

    # STAGE 5: VERIFICATION (Independent Gemini Audit)
    try:
        verification_result = await audit(thesis_result)
    except Exception as err:
        yield error_event("verification", err)
        return

    active_thesis = thesis_result
    audit_round = 1
    if verification_result.verdict == "OVERREACHING":
        # Show the failed audit, rewrite conservatively once, then audit the rewrite too.
        yield sse({"stage": "verification", "status": "done", "round": 1, "data": verification_result.model_dump()})
        try:
            active_thesis = await run_thesis(
                synthesis=synthesis_result,
                company=target_company,
                valid_repos=valid_repos,
                conservative=True
            )
        except Exception as err:
            _, why = describe_error("thesis", err)
            yield unresolved_event(
                f"The first thesis was judged OVERREACHING and the conservative rewrite failed ({why}). "
                "No verified role to pitch.", 2)
            return
        yield sse({"stage": "thesis", "status": "done", "data": active_thesis.model_dump(),
                   "note": "Regenerated with conservative calibration"})
        audit_round = 2
        try:
            verification_result = await audit(active_thesis)
        except Exception as err:
            _, why = describe_error("verification", err)
            yield unresolved_event(
                f"The conservative rewrite could not be re-audited ({why}), so it is not presented as verified.", 2)
            return
        if verification_result.verdict == "OVERREACHING":
            yield unresolved_event(
                "Gemini judged the conservative rewrite OVERREACHING as well. "
                "We couldn't produce a role the evidence confidently supports.", 2, verification_result)
            return

    pitch_claims = supported_claims(active_thesis, verification_result)
    if not pitch_claims:
        yield unresolved_event(
            "Gemini didn't mark any individual claim as SUPPORTED, so there is nothing verified to build a pitch from.",
            audit_round, verification_result)
        return
    yield sse({"stage": "verification", "status": "done", "round": audit_round, "data": verification_result.model_dump(),
               **({"note": "Re-verified after conservative rewrite"} if audit_round == 2 else {})})

    # STAGE 6: PITCH, built only from claims that passed the audit
    try:
        pitch_result = await run_pitch(
            thesis=active_thesis,
            verified_claims=pitch_claims,
            company=target_company,
            candidate_name=github_data.get("name") or github_username
        )
        yield sse({"stage": "pitch", "status": "done", "data": pitch_result.model_dump(), "claims_used": pitch_claims})
    except Exception as err:
        yield error_event("pitch", err)
        return


@app.post("/api/analyze")
async def analyze(
    request: Request,
    demo: Optional[bool] = Query(False)
):
    """
    SSE stream endpoint for analyzing a developer against a company's signals.
    Supports ?demo=true query parameter or demo: true in request JSON body.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    req_demo = demo or body.get("demo", False)
    username = body.get("github_username", "").strip()
    company_id = body.get("company_id", "").strip()

    # DEMO SAFETY MODE:
    # If ?demo=true is provided, or if neither username/company is given, or explicitly flagged
    if req_demo or not (username and company_id):
        return StreamingResponse(
            demo_stream_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )

    # LIVE PIPELINE MODE
    return StreamingResponse(
        live_pipeline_stream_generator(username, company_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


# Optional Unified SPA Serving (for Cloud Run single-container deployment)
frontend_dist = BASE_DIR / "static"
if not frontend_dist.exists():
    frontend_dist = BASE_DIR.parent / "frontend" / "dist"

if frontend_dist.exists():
    from fastapi.staticfiles import StaticFiles
    from starlette.responses import FileResponse

    assets_dir = frontend_dist / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        if full_path.startswith("api"):
            raise HTTPException(status_code=404, detail="API route not found")
        file_path = frontend_dist / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(frontend_dist / "index.html")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    host = os.getenv("HOST", "0.0.0.0")
    uvicorn.run("main:app", host=host, port=port, reload=True)
