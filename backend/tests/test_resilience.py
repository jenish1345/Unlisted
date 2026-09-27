import json

import httpx
import pytest
from fastapi.testclient import TestClient

import ai_pipeline
import llm_transport
import main
from ai_pipeline import SupportingEvidenceItem, SynthesisOutput, ThesisOutput, PitchOutput, validate_thesis_sources
from llm_transport import LLMError, gemini_generate, with_backoff
from verifier import VerificationResult, parse_gemini_json_response


# ---------------------------------------------------------------------------
# Source validation: fabricated company signals are rejected
# ---------------------------------------------------------------------------

SUPABASE_EVIDENCE = json.load(open(main.BASE_DIR / "company_signals.json"))[1]["evidence"]


def _thesis(*sources):
    return ThesisOutput(
        role_title="Senior Database Engineer",
        justification="Evidence-backed database platform work.",
        supporting_evidence=[SupportingEvidenceItem(claim=f"claim {i}", source=s) for i, s in enumerate(sources)],
    )


def test_fabricated_job_posting_is_rejected():
    bad = validate_thesis_sources(_thesis("Supabase Job Posting (Database Platform Engineer)"), ["fastapi"], SUPABASE_EVIDENCE)
    assert len(bad) == 1


@pytest.mark.parametrize("source", ["company", "engineering blog", "Supabase", "job posting", "signal"])
def test_keyword_only_sources_are_rejected(source):
    assert validate_thesis_sources(_thesis(source), ["fastapi"], SUPABASE_EVIDENCE)


@pytest.mark.parametrize("source", [
    "Supabase Systems Blog",                       # exact label
    "supabase ai post",                            # label, case-insensitive
    "Supabase Architecture: 'Supabase Realtime'",  # label + quote
    "streaming PostgreSQL Write-Ahead Log (WAL) changes",  # verbatim quote >= 20 chars
    "fastapi",
    "fastapi (README)",
])
def test_real_sources_are_accepted(source):
    assert validate_thesis_sources(_thesis(source), ["fastapi"], SUPABASE_EVIDENCE) == []


def test_demo_cache_sources_are_all_real():
    cache = json.load(open(main.BASE_DIR / "demo_cache.json"))["stages"]
    thesis = ThesisOutput.model_validate(cache["thesis"])
    repos = [r["name"] for r in cache["extraction"]["repos"]]
    assert validate_thesis_sources(thesis, repos, SUPABASE_EVIDENCE) == []


# ---------------------------------------------------------------------------
# Transport: 503 retried with backoff, 429 not retried, raw JSON kept out of messages
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    slept = []

    async def fake_sleep(s):
        slept.append(s)
    monkeypatch.setattr(llm_transport.asyncio, "sleep", fake_sleep)
    return slept


def _gemini_client(statuses):
    calls = []

    def handler(request):
        calls.append(request)
        status = statuses[min(len(calls) - 1, len(statuses) - 1)]
        if status == 200:
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]})
        return httpx.Response(status, json={"error": {"code": status, "message": "raw provider text", "status": "X"}})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), calls


@pytest.mark.asyncio
async def test_503_is_retried_with_exponential_backoff_then_succeeds(no_sleep):
    client, calls = _gemini_client([503, 503, 200])
    assert await gemini_generate("k", "m", "p", 0.1, client=client) == '{"ok": true}'
    assert len(calls) == 3
    assert no_sleep == [1.0, 2.0]


@pytest.mark.asyncio
async def test_503_gives_up_after_three_retries_with_clean_message(no_sleep):
    client, calls = _gemini_client([503])
    with pytest.raises(LLMError) as exc:
        await gemini_generate("k", "gemini-x", "p", 0.1, client=client)
    assert len(calls) == 4 and no_sleep == [1.0, 2.0, 4.0]
    assert exc.value.kind == "unavailable"
    assert "{" not in str(exc.value) and "raw provider text" not in str(exc.value)
    assert "raw provider text" in exc.value.detail


@pytest.mark.asyncio
async def test_429_fails_immediately_without_retry(no_sleep):
    client, calls = _gemini_client([429])
    with pytest.raises(LLMError) as exc:
        await gemini_generate("k", "gemini-x", "p", 0.1, client=client)
    assert len(calls) == 1 and no_sleep == []
    assert exc.value.kind == "quota"
    assert str(exc.value) == "API quota exhausted for Gemini (gemini-x)"


@pytest.mark.asyncio
async def test_with_backoff_does_not_retry_non_transient():
    n = 0

    async def call():
        nonlocal n
        n += 1
        raise LLMError("auth", "OpenAI", "bad key")
    with pytest.raises(LLMError):
        await with_backoff(call)
    assert n == 1


# ---------------------------------------------------------------------------
# Verification loop in the live SSE stream
# ---------------------------------------------------------------------------

SYN = SynthesisOutput(skills=["Python"], notable_projects=["fastapi"],
                      evidence=[{"claim": "async web framework", "source_repo": "fastapi"}])
PITCH = PitchOutput(subject="s", outreach_message="m", highlighted_claims=["c"], call_to_action="cta")


def _verdict(v, claim_verdicts):
    return VerificationResult(verdict=v, reason="r",
                              claim_verdicts=[{"index": i, "verdict": cv} for i, cv in enumerate(claim_verdicts)])


def _run(monkeypatch, verdicts, rewrite_error=None):
    """Drive the live pipeline with stubbed stages; returns (events, pitch_calls)."""
    async def extract(u):
        return {"username": u, "name": u, "repos": [{"name": "fastapi"}]}

    async def synth(g):
        return SYN

    async def thesis(synthesis, company, valid_repos, conservative=False):
        if conservative and rewrite_error:
            raise rewrite_error
        return _thesis("fastapi", "Supabase Systems Blog") if not conservative else _thesis("fastapi")

    queue = list(verdicts)

    async def verify(**kw):
        return queue.pop(0)

    pitch_calls = []

    async def pitch(thesis, verified_claims, company, candidate_name):
        pitch_calls.append(verified_claims)
        return PITCH

    monkeypatch.setattr(main, "extract_github_profile", extract)
    monkeypatch.setattr(main, "run_synthesis", synth)
    monkeypatch.setattr(main, "run_thesis", thesis)
    monkeypatch.setattr(main, "verify_thesis_with_gemini", verify)
    monkeypatch.setattr(main, "run_pitch", pitch)
    res = TestClient(main.app).post("/api/analyze", json={"github_username": "u", "company_id": "supabase"})
    events = [json.loads(l[6:]) for l in res.text.split("\n") if l.startswith("data: ")]
    return events, pitch_calls


def test_supported_pitch_uses_only_claims_marked_supported(monkeypatch):
    events, pitch_calls = _run(monkeypatch, [_verdict("PARTIALLY_SUPPORTED", ["SUPPORTED", "PARTIALLY_SUPPORTED"])])
    assert [e["stage"] for e in events] == ["extraction", "synthesis", "thesis", "verification", "pitch"]
    assert pitch_calls == [[{"claim": "claim 0", "source": "fastapi"}]]
    assert events[-1]["claims_used"] == pitch_calls[0]


def test_overreaching_rewrite_is_reverified_before_pitch(monkeypatch):
    events, pitch_calls = _run(monkeypatch, [_verdict("OVERREACHING", ["UNSUPPORTED", "UNSUPPORTED"]),
                                             _verdict("SUPPORTED", ["SUPPORTED"])])
    assert [(e["stage"], e["status"]) for e in events] == [
        ("extraction", "done"), ("synthesis", "done"), ("thesis", "done"),
        ("verification", "done"), ("thesis", "done"), ("verification", "done"), ("pitch", "done")]
    assert events[3]["round"] == 1 and events[3]["data"]["verdict"] == "OVERREACHING"
    assert events[4]["note"]
    assert events[5]["round"] == 2 and events[5]["data"]["verdict"] == "SUPPORTED"
    assert pitch_calls == [[{"claim": "claim 0", "source": "fastapi"}]]


def test_rewrite_still_overreaching_is_unresolved_and_not_pitched(monkeypatch):
    events, pitch_calls = _run(monkeypatch, [_verdict("OVERREACHING", ["UNSUPPORTED"] * 2),
                                             _verdict("OVERREACHING", ["UNSUPPORTED"])])
    assert (events[-1]["stage"], events[-1]["status"]) == ("verification", "unresolved")
    assert events[-1]["data"]["verdict"] == "OVERREACHING"
    assert pitch_calls == []


def test_rewrite_failure_is_unresolved_not_silently_shipped(monkeypatch):
    events, pitch_calls = _run(monkeypatch, [_verdict("OVERREACHING", ["UNSUPPORTED"] * 2)],
                               rewrite_error=LLMError("quota", "OpenAI", "API quota exhausted for OpenAI"))
    assert (events[-1]["stage"], events[-1]["status"]) == ("verification", "unresolved")
    assert "API quota exhausted for OpenAI" in events[-1]["message"]
    assert pitch_calls == []


def test_no_individually_supported_claims_is_unresolved(monkeypatch):
    events, pitch_calls = _run(monkeypatch, [_verdict("SUPPORTED", ["PARTIALLY_SUPPORTED", "UNSUPPORTED"])])
    assert (events[-1]["stage"], events[-1]["status"]) == ("verification", "unresolved")
    assert pitch_calls == []


def test_stage_errors_are_clean(monkeypatch):
    async def synth(g):
        raise LLMError("unavailable", "Gemini", "Gemini (m) is overloaded or down (HTTP 503)", detail='{"error": {...}}')
    monkeypatch.setattr(main, "run_synthesis", synth)

    async def extract(u):
        return {"username": u, "repos": [{"name": "fastapi"}]}
    monkeypatch.setattr(main, "extract_github_profile", extract)
    res = TestClient(main.app).post("/api/analyze", json={"github_username": "u", "company_id": "supabase"})
    events = [json.loads(l[6:]) for l in res.text.split("\n") if l.startswith("data: ")]
    assert events[-1] == {"stage": "synthesis", "status": "error", "error_kind": "unavailable",
                          "message": "Gemini (m) is overloaded or down (HTTP 503)"}


def test_parse_claim_verdicts_unknown_wording_is_not_supported():
    r = parse_gemini_json_response(json.dumps({"verdict": "SUPPORTED", "reason": "r", "claim_verdicts": [
        {"index": 0, "verdict": "supported"}, {"index": 1, "verdict": "maybe"}, {"verdict": "SUPPORTED"}]}))
    assert [(c.index, c.verdict) for c in r.claim_verdicts] == [(0, "SUPPORTED"), (1, "UNSUPPORTED")]
