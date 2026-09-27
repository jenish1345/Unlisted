import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from dotenv import load_dotenv
import httpx
from pydantic import BaseModel, Field, ValidationError

from llm_transport import LLMError, combine_failures, gemini_generate, gemini_models

# Ensure .env is loaded
load_dotenv(Path(__file__).resolve().parent / ".env")
load_dotenv()


ClaimVerdictValue = Literal["SUPPORTED", "PARTIALLY_SUPPORTED", "UNSUPPORTED"]


class ClaimVerdict(BaseModel):
    index: int = Field(..., description="Index of the claim in the thesis's supporting_evidence list")
    verdict: ClaimVerdictValue
    note: str = ""


class VerificationResult(BaseModel):
    verdict: Literal["SUPPORTED", "PARTIALLY_SUPPORTED", "OVERREACHING"]
    reason: str = Field(..., description="Objective assessment of whether evidence directly supports the proposed role.")
    # Per-claim audit. Empty for the recorded demo, which predates it.
    claim_verdicts: List[ClaimVerdict] = Field(default_factory=list)


def _normalize_claim_verdict(raw: Any) -> str:
    v = str(raw or "").strip().upper()
    if "PARTIAL" in v:
        return "PARTIALLY_SUPPORTED"
    if "UNSUPPORT" in v or "OVERREACH" in v or v in ("NO", "FALSE"):
        return "UNSUPPORTED"
    if "SUPPORT" in v:
        return "SUPPORTED"
    return "UNSUPPORTED"  # unknown wording never counts as verified


class VerifierError(Exception):
    """Raised when Gemini verification call fails."""
    pass


def parse_gemini_json_response(raw_text: str) -> VerificationResult:
    """
    Safely parse Gemini response into VerificationResult.
    Strips code fences if present and validates against schema.
    """
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as err:
        # Try extracting JSON object via regex if there's any stray text
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            data = json.loads(match.group(0))
        else:
            raise VerifierError("Gemini's verdict wasn't valid JSON.") from err

    # Standardize verdict capitalization
    raw_verdict = str(data.get("verdict", "")).strip().upper()
    if raw_verdict not in ["SUPPORTED", "PARTIALLY_SUPPORTED", "OVERREACHING"]:
        if "PARTIAL" in raw_verdict:
            raw_verdict = "PARTIALLY_SUPPORTED"
        elif "OVERREACH" in raw_verdict:
            raw_verdict = "OVERREACHING"
        elif "SUPPORT" in raw_verdict:
            raw_verdict = "SUPPORTED"
        else:
            raw_verdict = "PARTIALLY_SUPPORTED"

    data["verdict"] = raw_verdict

    claim_verdicts = []
    for cv in data.get("claim_verdicts") or []:
        if isinstance(cv, dict) and isinstance(cv.get("index"), int):
            claim_verdicts.append({"index": cv["index"], "verdict": _normalize_claim_verdict(cv.get("verdict")),
                                   "note": str(cv.get("note") or "")})
    data["claim_verdicts"] = claim_verdicts
    return VerificationResult.model_validate(data)


async def verify_thesis_with_gemini(
    role_title: str,
    claims: List[Dict[str, str]],
    developer_evidence: List[Dict[str, str]],
    company_name: str,
    company_evidence: List[str],
    api_key: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None
) -> VerificationResult:
    """
    Independently score the candidate's thesis against evidence using Google Gemini.

    CRITICAL ISOLATION RULE:
    This call does NOT receive OpenAI's reasoning or justification.
    It only evaluates the raw proposed role title, the claims, developer code evidence,
    and verified company signals.
    """
    gemini_key = api_key or os.getenv("GEMINI_API_KEY")
    if not gemini_key:
        raise VerifierError(
            "GEMINI_API_KEY is not configured in environment. "
            "Please provide a Gemini API key or use demo mode (?demo=true)."
        )

    system_instruction = (
        "You are an impartial, highly skeptical Technical Talent Auditor. "
        "Your task is to independently verify whether a proposed candidate role and associated claims "
        "are genuinely supported by the provided developer code evidence and company signals.\n\n"
        "Rules:\n"
        "1. DO NOT assume skills that lack concrete repo evidence.\n"
        "2. If the claims exaggerate capabilities beyond the code artifacts, mark as 'OVERREACHING'.\n"
        "3. If some claims are proven but the role title is slightly ambitious, mark as 'PARTIALLY_SUPPORTED'.\n"
        "4. If all claims directly correspond to documented repositories and match company signals, mark as 'SUPPORTED'.\n"
        "5. Judge every claim individually too: SUPPORTED only if the evidence directly backs it, "
        "PARTIALLY_SUPPORTED if it is stretched, UNSUPPORTED otherwise.\n"
        "6. Respond ONLY with valid JSON matching: "
        '{"verdict": "SUPPORTED" | "PARTIALLY_SUPPORTED" | "OVERREACHING", "reason": "<succinct 2-sentence rationale>", '
        '"claim_verdicts": [{"index": <claim index>, "verdict": "SUPPORTED" | "PARTIALLY_SUPPORTED" | "UNSUPPORTED", '
        '"note": "<one short sentence>"}]} with one entry per claim.'
    )

    evaluation_payload = {
        "candidate_proposed_role": role_title,
        "claims_made": claims,
        "verified_developer_evidence": developer_evidence,
        "target_company": company_name,
        "target_company_signals": company_evidence
    }

    user_prompt = (
        f"Audit this proposed role placement:\n\n"
        f"{json.dumps(evaluation_payload, indent=2)}\n\n"
        f"Return strictly JSON with keys 'verdict', 'reason' and 'claim_verdicts'."
    )

    failures: List[LLMError] = []
    for model_name in gemini_models():
        try:
            text = await gemini_generate(gemini_key, model_name, f"{system_instruction}\n\n{user_prompt}",
                                         temperature=0.1, timeout=45.0, client=client)
        except LLMError as err:
            failures.append(err)
            continue
        return parse_gemini_json_response(text)
    raise combine_failures(failures)
