import json
import logging

import httpx
import pytest

import ai_pipeline
from ai_pipeline import SynthesisOutput, run_synthesis

SYNTHESIS = {"skills": ["Python"], "notable_projects": ["fastapi: web framework"],
             "evidence": [{"claim": "Built an async web framework", "source_repo": "fastapi"}]}
GITHUB = {"username": "u", "repos": [{"name": "fastapi"}]}


def _chat_ok(body):
    return httpx.Response(200, json={
        "id": "x", "object": "chat.completion", "created": 0, "model": "m",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(body)}}],
    })


@pytest.fixture
def providers(monkeypatch):
    """Route OpenAI/Groq SDK traffic to scripted responses; record which hosts were called."""
    hits = []
    script = {"api.openai.com": lambda: _chat_ok(SYNTHESIS), "api.groq.com": lambda: _chat_ok(SYNTHESIS)}

    def handler(request):
        hits.append(request.url.host)
        assert json.loads(request.content)["response_format"] == {"type": "json_object"}
        return script[request.url.host]()

    real = ai_pipeline.AsyncOpenAI
    monkeypatch.setattr(ai_pipeline, "AsyncOpenAI",
                        lambda **kw: real(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), **kw))
    monkeypatch.setattr(ai_pipeline, "_openai_quota_hit_at", None)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    return hits, script


@pytest.mark.asyncio
async def test_openai_quota_falls_back_to_groq_and_logs_it(providers, monkeypatch, caplog):
    hits, script = providers
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    script["api.openai.com"] = lambda: httpx.Response(429, json={"error": {
        "message": "You exceeded your current quota", "type": "insufficient_quota", "code": "insufficient_quota"}})
    # main.py stops "unlisted" loggers propagating to root, so attach the capture handler directly.
    ai_pipeline.log.addHandler(caplog.handler)
    monkeypatch.setattr(ai_pipeline.log, "level", logging.INFO)
    try:
        result = await run_synthesis(GITHUB)
    finally:
        ai_pipeline.log.removeHandler(caplog.handler)
    assert hits == ["api.openai.com", "api.groq.com"]  # 429 not retried
    assert result == SynthesisOutput.model_validate(SYNTHESIS)
    assert any("served by Groq (openai/gpt-oss-120b)" in r.message and "API quota exhausted for OpenAI" in r.message
               for r in caplog.records)


@pytest.mark.asyncio
async def test_llm_provider_groq_never_calls_openai(providers, monkeypatch):
    hits, _ = providers
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    await run_synthesis(GITHUB)
    assert hits == ["api.groq.com"]


@pytest.mark.asyncio
async def test_openai_default_serves_when_healthy(providers, monkeypatch):
    hits, _ = providers
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    await run_synthesis(GITHUB)
    assert hits == ["api.openai.com"]


@pytest.mark.asyncio
async def test_same_json_parses_identically_from_either_provider(providers, monkeypatch):
    outputs = {}
    for provider in ("openai", "groq"):
        monkeypatch.setenv("LLM_PROVIDER", provider)
        outputs[provider] = await run_synthesis(GITHUB)
    assert outputs["openai"] == outputs["groq"]
