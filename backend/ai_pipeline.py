import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
import httpx
import openai
from openai import AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError

from llm_transport import LLMError, combine_failures, error_for_status, gemini_generate, gemini_models, with_backoff

log = logging.getLogger("unlisted.pipeline")

# Ensure .env is loaded
load_dotenv(Path(__file__).resolve().parent / ".env")
load_dotenv()


# ============================================================================
# Pydantic Schemas
# ============================================================================

class EvidenceItem(BaseModel):
    claim: str = Field(..., description="Concrete technical capability or achievement backed by code")
    source_repo: str = Field(..., description="The repository name that demonstrates this claim")


class SynthesisOutput(BaseModel):
    skills: List[str] = Field(..., min_length=1, description="List of primary technical competencies")
    notable_projects: List[str] = Field(..., min_length=1, description="Top standout projects with brief impact")
    evidence: List[EvidenceItem] = Field(..., min_length=1, description="Direct claims tied to specific repositories")


class SupportingEvidenceItem(BaseModel):
    claim: str = Field(..., description="Specific capability or alignment claim")
    source: str = Field(..., description="Must cite either a repository name or a company signal source")


class ThesisOutput(BaseModel):
    role_title: str = Field(..., min_length=3, description="Exact high-conviction job title for the candidate")
    justification: str = Field(..., min_length=10, description="Why this role bridges the candidate's proved skills with the company's urgent needs")
    supporting_evidence: List[SupportingEvidenceItem] = Field(..., min_length=1, description="Claims mapped to sources")


class PitchOutput(BaseModel):
    subject: str = Field(..., description="Punchy, compelling email/DM subject line")
    outreach_message: str = Field(..., description="High-conversion outreach pitch referencing verified evidence")
    highlighted_claims: List[str] = Field(..., description="Key verified proof points included in the pitch")
    call_to_action: str = Field(..., description="Crisp, low-friction next step")


class PipelineError(Exception):
    """Raised when an AI pipeline stage fails after retries."""
    pass


# ============================================================================
# Helpers & Dual-Provider LLM Engine
# ============================================================================

def clean_json_string(text: str) -> str:
    """Strip markdown backticks and clean JSON string."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        return match.group(0)
    return cleaned


def get_default_model() -> str:
    return os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def get_openai_client(api_key: Optional[str] = None) -> AsyncOpenAI:
    key = api_key or os.getenv("OPENAI_API_KEY")
    return AsyncOpenAI(api_key=key or "dummy-key")


GROQ_BASE_URL = "https://api.groq.com/openai/v1"


def get_groq_model() -> str:
    return os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")


def get_llm_provider() -> str:
    """Which OpenAI-compatible provider runs first: LLM_PROVIDER=openai (default) or groq."""
    provider = os.getenv("LLM_PROVIDER", "openai").strip().lower()
    if provider not in ("openai", "groq"):
        log.warning("Unknown LLM_PROVIDER=%r, using openai", provider)
        return "openai"
    return provider


# When OpenAI reports 429 we stop calling it for a while instead of for the life of the process,
# so topping up credits takes effect without a restart.
OPENAI_QUOTA_RECHECK_SECONDS = float(os.getenv("OPENAI_QUOTA_RECHECK_SECONDS", "300"))
_openai_quota_hit_at: Optional[float] = None


async def _chat_json(client: AsyncOpenAI, provider: str, model: str,
                     system_prompt: str, user_prompt: str, temperature: float) -> str:
    """One JSON-mode chat completion against any OpenAI-compatible API (OpenAI, Groq)."""
    async def once() -> str:
        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                response_format={"type": "json_object"},
                temperature=temperature
            )
        except openai.APIStatusError as err:
            raise error_for_status(provider, model, err.status_code, str(err))
        except openai.APIConnectionError as err:  # includes timeouts
            raise LLMError("unavailable", provider, f"{provider} ({model}) did not respond in time", str(err), model)
        return resp.choices[0].message.content or "{}"

    return await with_backoff(once)


async def _try_openai(client, api_key, system_prompt, user_prompt, temperature, failures) -> Optional[str]:
    global _openai_quota_hit_at
    key = api_key or os.getenv("OPENAI_API_KEY")
    if not key:
        return None
    model = get_default_model()
    if _openai_quota_hit_at and time.monotonic() - _openai_quota_hit_at < OPENAI_QUOTA_RECHECK_SECONDS:
        failures.append(LLMError("quota", "OpenAI", f"API quota exhausted for OpenAI ({model})",
                                 "skipped: 429 seen within the recheck window", model))
        return None
    try:
        o_client = client or AsyncOpenAI(api_key=key, max_retries=0, timeout=20.0)
        result = await _chat_json(o_client, "OpenAI", model, system_prompt, user_prompt, temperature)
        _openai_quota_hit_at = None
        return result
    except LLMError as err:
        if err.kind == "quota":
            _openai_quota_hit_at = time.monotonic()
        log.warning("OpenAI failed (%s), falling back: %s", err.kind, err.detail[:300])
        failures.append(err)
        return None


async def _try_groq(system_prompt, user_prompt, temperature, failures) -> Optional[str]:
    key = os.getenv("GROQ_API_KEY")
    if not key:
        return None
    model = get_groq_model()
    try:
        g_client = AsyncOpenAI(api_key=key, base_url=GROQ_BASE_URL, max_retries=0, timeout=30.0)
        return await _chat_json(g_client, "Groq", model, system_prompt, user_prompt, temperature)
    except LLMError as err:
        log.warning("Groq failed (%s), falling back: %s", err.kind, err.detail[:300])
        failures.append(err)
        return None


async def generate_llm_json(
    system_prompt: str,
    user_prompt: str,
    client: Optional[AsyncOpenAI] = None,
    api_key: Optional[str] = None,
    temperature: float = 0.2
) -> str:
    """
    Generate structured JSON. Provider order:
      LLM_PROVIDER=openai (default): OpenAI -> Groq -> Gemini models
      LLM_PROVIDER=groq:             Groq -> Gemini models
    Any OpenAI failure, including 429 insufficient_quota, falls through to Groq when GROQ_API_KEY is set.
    Transient errors are retried with backoff inside each attempt; 429s are not retried.
    If everything fails, raises one LLMError naming every provider's reason.
    """
    failures: List[LLMError] = []

    attempts = [("OpenAI", get_default_model(), lambda: _try_openai(client, api_key, system_prompt, user_prompt, temperature, failures)),
                ("Groq", get_groq_model(), lambda: _try_groq(system_prompt, user_prompt, temperature, failures))]
    if get_llm_provider() == "groq":
        attempts = attempts[1:]
    for provider, model, attempt in attempts:
        result = await attempt()
        if result is not None:
            log.info("LLM request served by %s (%s)%s", provider, model,
                     f" after: {'; '.join(str(f) for f in failures)}" if failures else "")
            return result

    # Last resort: Gemini, model by model
    gemini_key = os.getenv("GEMINI_API_KEY")
    if not gemini_key:
        if failures:
            raise combine_failures(failures)
        raise LLMError("auth", "none", "No LLM provider key (OPENAI_API_KEY, GROQ_API_KEY, GEMINI_API_KEY) is configured on the server")

    for model in gemini_models():
        try:
            result = await gemini_generate(gemini_key, model, f"{system_prompt}\n\n{user_prompt}", temperature)
        except LLMError as err:
            failures.append(err)
            continue
        log.info("LLM request served by Gemini (%s)%s", model,
                 f" after: {'; '.join(str(f) for f in failures)}" if failures else "")
        return result

    raise combine_failures(failures)


# ============================================================================
# Stage 2: Synthesis
# ============================================================================

async def run_synthesis(
    github_data: Dict[str, Any],
    api_key: Optional[str] = None,
    client: Optional[AsyncOpenAI] = None
) -> SynthesisOutput:
    """
    Synthesize GitHub repository artifacts into structured skills, notable projects,
    and verified evidence items. Retries once on malformed JSON.
    """
    openai_client = client or get_openai_client(api_key)
    model = get_default_model()

    # Prepare compact repo data for the LLM
    repos_summary = []
    known_repos = []
    for r in github_data.get("repos", []):
        r_name = r.get("name", "unknown")
        known_repos.append(r_name)
        repos_summary.append({
            "name": r_name,
            "description": r.get("description", ""),
            "language": r.get("language", ""),
            "stars": r.get("stars", 0),
            "topics": r.get("topics", []),
            "readme_excerpt": (r.get("readme_snippet") or "")[:800]
        })

    system_prompt = (
        "You are an elite Engineering Evaluator. Analyze the candidate's GitHub repositories.\n"
        "Produce an objective synthesis in strict JSON matching this schema:\n"
        "{\n"
        '  "skills": ["<skill 1>", "<skill 2>", ...],\n'
        '  "notable_projects": ["<project name: 1-sentence impact>", ...],\n'
        '  "evidence": [\n'
        '    {"claim": "<concrete capability>", "source_repo": "<exact repo name>"}\n'
        "  ]\n"
        "}\n\n"
        f"CRITICAL: The 'source_repo' in each evidence item MUST match one of these repo names: {json.dumps(known_repos)}.\n"
        "Do not invent repositories. Provide only factual, code-backed claims."
    )

    user_prompt = f"Candidate Username: {github_data.get('username')}\nRepos:\n{json.dumps(repos_summary, indent=2)}"

    # Attempt up to 2 times
    last_err = None
    prompt_attempt = user_prompt
    for attempt in range(2):
        try:
            raw_text = await generate_llm_json(
                system_prompt=system_prompt,
                user_prompt=prompt_attempt,
                client=client,
                api_key=api_key,
                temperature=0.2
            )
            cleaned = clean_json_string(raw_text)
            parsed = json.loads(cleaned)
            return SynthesisOutput.model_validate(parsed)
        except (json.JSONDecodeError, ValidationError) as err:
            last_err = err
            prompt_attempt = f"{user_prompt}\n\nERROR ON PREVIOUS ATTEMPT: {err}. Please return strictly valid JSON matching the exact schema."

    log.warning("Synthesis output invalid twice: %s", last_err)
    raise PipelineError("The model's synthesis output didn't match the expected format, even after a retry.")


# ============================================================================
# Stage 4: Thesis
# ============================================================================

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().strip("\"'“”‘’`").lower())


def signal_labels(company_evidence: List[str]) -> List[str]:
    """Each company signal reads "<Label>: '<quote>'"; the label is what the model is asked to cite."""
    return [e.split(":", 1)[0].strip() for e in company_evidence if ":" in e]


def source_matches_repo(source: str, valid_repos: List[str]) -> Optional[str]:
    """Exact repo name, or the repo name as a whole token inside the source (e.g. "fastapi (README)")."""
    s = _norm(source)
    for repo in valid_repos:
        r = repo.lower()
        if s == r or re.search(rf"(?<![\w.-]){re.escape(r)}(?![\w.-])", s):
            return repo
    return None


MIN_QUOTE_CHARS = 20


def source_matches_signal(source: str, company_evidence: List[str]) -> Optional[str]:
    """
    A company-signal source must be traceable to the actual evidence text: either it names a signal's
    label (e.g. "Supabase Systems Blog"), or it quotes at least MIN_QUOTE_CHARS of a signal verbatim.
    Keyword heuristics ("job", "blog", ...) are deliberately not accepted.
    """
    s = _norm(source)
    for ev in company_evidence:
        label = _norm(ev.split(":", 1)[0]) if ":" in ev else ""
        if label and (s == label or s.startswith(label + ":") or s.startswith(label + " (")):
            return ev
        if len(s) >= MIN_QUOTE_CHARS and s in _norm(ev):
            return ev
    return None


def validate_thesis_sources(
    thesis: ThesisOutput,
    valid_repos: List[str],
    company_evidence: List[str]
) -> List[str]:
    """
    Verify that every claim in supporting_evidence traces back to either a valid repo
    or a real company evidence string. Returns list of invalid claims/sources.
    """
    invalid = []
    for item in thesis.supporting_evidence:
        if not item.source.strip():
            invalid.append(f"Empty source for claim: '{item.claim}'")
            continue
        if not (source_matches_repo(item.source, valid_repos) or source_matches_signal(item.source, company_evidence)):
            invalid.append(f"Unverified source '{item.source}' for claim: '{item.claim}'")
    return invalid


async def run_thesis(
    synthesis: SynthesisOutput,
    company: Dict[str, Any],
    valid_repos: List[str],
    conservative: bool = False,
    api_key: Optional[str] = None,
    client: Optional[AsyncOpenAI] = None
) -> ThesisOutput:
    """
    Cross-reference synthesis output + selected company's evidence to create a targeted role thesis.
    Validates that every claim traces back to either a repo or company evidence string.
    Retries once if claims are unreferenced or JSON is malformed.
    """
    company_name = company.get("name", "Target Company")
    company_evidence = company.get("evidence", [])

    tone_instruction = ""
    if conservative:
        tone_instruction = (
            "\nATTENTION: A previous audit found the thesis OVERREACHING. "
            "You MUST be significantly more conservative. Target a pragmatic, directly evidenced role title "
            "(e.g., Senior Systems Engineer or Backend Infrastructure Engineer instead of Principal/Staff/VP). "
            "Only make claims that have 100% indisputable backing in the repositories."
        )

    system_prompt = (
        "You are a Strategic Tech Talent Matchmaker. "
        "Cross-reference the candidate's verified GitHub evidence with the company's real engineering signals.\n"
        "Formulate a razor-sharp thesis in JSON matching this schema:\n"
        "{\n"
        '  "role_title": "<precise targeted role title>",\n'
        '  "justification": "<2-3 concise sentences showing how the candidate solves high-priority challenges at the company>",\n'
        '  "supporting_evidence": [\n'
        '    {"claim": "<specific capability>", "source": "<repository name OR company signal source>"}\n'
        "  ]\n"
        "}\n\n"
        "STRICT EVIDENCE RULE:\n"
        "Every single claim in 'supporting_evidence' MUST explicitly cite its source.\n"
        f"- Valid repo sources: {json.dumps(valid_repos)}\n"
        f"- Valid company sources (cite the label exactly): {json.dumps(signal_labels(company_evidence))}\n"
        "Any other source (e.g. a job posting, press release or blog not in these lists) will be rejected.\n"
        "Do NOT invent unreferenced claims."
        f"{tone_instruction}"
    )

    user_payload = {
        "candidate_skills": synthesis.skills,
        "candidate_notable_projects": synthesis.notable_projects,
        "candidate_evidence": [e.model_dump() for e in synthesis.evidence],
        "company_name": company_name,
        "company_description": company.get("description", ""),
        "company_signals": company_evidence
    }

    base_user_prompt = json.dumps(user_payload, indent=2)
    prompt_attempt = base_user_prompt

    last_err = None
    last_valid_thesis: Optional[ThesisOutput] = None
    for attempt in range(2):
        # LLMError (quota/outage) propagates: retrying the prompt can't fix the provider.
        raw_text = await generate_llm_json(
            system_prompt=system_prompt,
            user_prompt=prompt_attempt,
            client=client,
            api_key=api_key,
            temperature=0.2 if not conservative else 0.1
        )
        try:
            thesis = ThesisOutput.model_validate(json.loads(clean_json_string(raw_text)))
        except (json.JSONDecodeError, ValidationError) as err:
            last_err = err
            prompt_attempt = f"{base_user_prompt}\n\nCORRECTION REQUIRED: {err}. Please return strictly valid JSON matching the schema."
            continue

        invalid_sources = validate_thesis_sources(thesis, valid_repos, company_evidence)
        if not invalid_sources:
            return thesis
        last_err = f"Claims with invalid sources detected: {invalid_sources}"
        last_valid_thesis = thesis
        log.warning("Thesis attempt %d cited unverifiable sources: %s", attempt + 1, invalid_sources)
        prompt_attempt = (
            f"{base_user_prompt}\n\nCORRECTION REQUIRED: {last_err}. Every source must be one of the listed repo "
            "names or company signal labels, exactly. Drop any claim you cannot source that way."
        )

    # Still citing fabricated sources after the retry: keep only the claims that trace to real
    # repos/signals. Never relabel a bad source as a real one.
    if last_valid_thesis is not None:
        kept = [item for item in last_valid_thesis.supporting_evidence
                if source_matches_repo(item.source, valid_repos) or source_matches_signal(item.source, company_evidence)]
        if kept:
            log.warning("Dropped %d unsourced thesis claim(s)", len(last_valid_thesis.supporting_evidence) - len(kept))
            return last_valid_thesis.model_copy(update={"supporting_evidence": kept})
        raise PipelineError("Every claim in the thesis cited a source that doesn't exist in the repos or company signals.")

    log.warning("Thesis output invalid twice: %s", last_err)
    raise PipelineError("The model's thesis output didn't match the expected format, even after a retry.")


# ============================================================================
# Stage 6: Pitch
# ============================================================================

async def run_pitch(
    thesis: ThesisOutput,
    verified_claims: List[Dict[str, str]],
    company: Dict[str, Any],
    candidate_name: str,
    api_key: Optional[str] = None,
    client: Optional[AsyncOpenAI] = None
) -> PitchOutput:
    """
    Draft a high-conversion, professional cold outreach message.
    Uses ONLY claims that have passed verification.
    """
    company_name = company.get("name", "the engineering team")

    system_prompt = (
        "You are an executive talent strategist drafting a bespoke, ultra-compelling cold outreach message "
        f"for {candidate_name} to the engineering leadership at {company_name}.\n\n"
        "CRITICAL RULES:\n"
        "1. Mention ONLY the verified claims provided. Do NOT claim or fabricate any other accomplishments.\n"
        "2. Keep it punchy, technical, humble, yet confident (under 180 words).\n"
        "3. Focus on how the candidate's proved engineering artifacts directly reduce latency/risk/workload for the company's known technical focus.\n"
        "4. Respond strictly in JSON matching:\n"
        "{\n"
        '  "subject": "<Compelling, non-spammy subject line>",\n'
        '  "outreach_message": "<The email/LinkedIn DM body text>",\n'
        '  "highlighted_claims": ["<Claim 1>", "<Claim 2>"],\n'
        '  "call_to_action": "<Crisp 15-minute sync request>"\n'
        "}"
    )

    user_payload = {
        "candidate_name": candidate_name,
        "target_role": thesis.role_title,
        "justification": thesis.justification,
        "verified_supporting_claims": verified_claims,
        "target_company": company_name
    }

    raw_text = await generate_llm_json(
        system_prompt=system_prompt,
        user_prompt=json.dumps(user_payload, indent=2),
        client=client,
        api_key=api_key,
        temperature=0.3
    )

    cleaned = clean_json_string(raw_text)
    parsed = json.loads(cleaned)
    return PitchOutput.model_validate(parsed)
