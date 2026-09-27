"""
Shared plumbing for every LLM provider call.

- Transient failures (HTTP 5xx, timeouts, dropped connections) are retried with exponential backoff.
- Quota exhaustion (HTTP 429) is never retried: it won't clear in seconds.
- Every failure becomes an LLMError whose str() is short and safe to show users. The raw provider
  response is kept on `.detail` and written to the server log, never sent to the browser.
"""
import asyncio
import logging
import os
from typing import Awaitable, Callable, List, Optional, TypeVar

import httpx

log = logging.getLogger("unlisted.llm")

T = TypeVar("T")

TRANSIENT_STATUSES = {500, 502, 503, 504}
MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))
BACKOFF_BASE_SECONDS = float(os.getenv("LLM_BACKOFF_BASE_SECONDS", "1.0"))  # waits 1s, 2s, 4s

# Tried in order. Each Gemini model has its own quota bucket, so a 429 on one still lets the next try.
DEFAULT_GEMINI_MODELS = "gemini-2.5-flash,gemini-3.8-flash,gemini-3.5-flash"


def gemini_models() -> List[str]:
    return [m.strip() for m in os.getenv("GEMINI_MODELS", DEFAULT_GEMINI_MODELS).split(",") if m.strip()]


class LLMError(Exception):
    """
    A provider call failed.
    kind: "quota" | "unavailable" | "auth" | "not_found" | "bad_response"
    """

    def __init__(self, kind: str, provider: str, message: str, detail: str = "", model: str = ""):
        super().__init__(message)
        self.kind = kind
        self.provider = provider
        self.model = model
        self.detail = detail


def error_for_status(provider: str, model: str, status: int, raw: str) -> LLMError:
    label = f"{provider} ({model})" if model else provider
    if status == 429:
        return LLMError("quota", provider, f"API quota exhausted for {label}", raw, model)
    if status in (401, 403):
        return LLMError("auth", provider, f"{label} rejected the API key", raw, model)
    if status == 404:
        return LLMError("not_found", provider, f"{label} is not available (model retired or misnamed)", raw, model)
    if status in TRANSIENT_STATUSES:
        return LLMError("unavailable", provider, f"{label} is overloaded or down (HTTP {status})", raw, model)
    return LLMError("bad_response", provider, f"{label} returned an unexpected error (HTTP {status})", raw, model)


async def with_backoff(call: Callable[[], Awaitable[T]], retries: Optional[int] = None) -> T:
    """Run call(); retry only LLMError(kind="unavailable"), doubling the wait each time."""
    retries = MAX_RETRIES if retries is None else retries
    for attempt in range(retries + 1):
        try:
            return await call()
        except LLMError as err:
            if err.kind != "unavailable" or attempt == retries:
                if err.kind == "unavailable" and retries:
                    err.args = (f"{err.args[0]}, still failing after {retries} retries",)
                raise
            delay = BACKOFF_BASE_SECONDS * (2 ** attempt)
            log.warning("%s transient failure (attempt %d/%d), retrying in %.1fs: %s",
                        err.provider, attempt + 1, retries + 1, delay, err.detail[:300])
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")


async def gemini_generate(
    api_key: str,
    model: str,
    prompt_text: str,
    temperature: float,
    timeout: float = 30.0,
    client: Optional[httpx.AsyncClient] = None,
) -> str:
    """One Gemini generateContent call (JSON mode), with backoff on transient failures. Returns the text part."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt_text}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": temperature},
    }

    async def once() -> str:
        try:
            if client is not None:
                resp = await client.post(url, params={"key": api_key}, json=body, timeout=timeout)
            else:
                async with httpx.AsyncClient(timeout=timeout) as c:
                    resp = await c.post(url, params={"key": api_key}, json=body)
        except (httpx.TimeoutException, httpx.TransportError) as err:
            raise LLMError("unavailable", "Gemini", f"Gemini ({model}) did not respond in time",
                           f"{type(err).__name__}: {err}", model)
        if resp.status_code != 200:
            raise error_for_status("Gemini", model, resp.status_code, resp.text)
        try:
            parts = resp.json()["candidates"][0]["content"]["parts"]
            return parts[0]["text"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise LLMError("bad_response", "Gemini", f"Gemini ({model}) returned an empty or blocked response",
                           resp.text[:2000], model)

    return await with_backoff(once)


def combine_failures(failures: List[LLMError]) -> LLMError:
    """
    Every provider/model failed. Build one short, clean error grouped by provider, e.g.
    "API quota exhausted for OpenAI. Gemini overloaded (gemini-3.8-flash; HTTP 5xx after 3 retries each)."
    kind is "unavailable" if anything was merely overloaded (worth retrying later), else "quota" if
    any provider was out of quota, else the first failure's kind.
    """
    for f in failures:
        log.error("LLM failure [%s/%s %s]: %s | raw: %s", f.provider, f.model, f.kind, f, f.detail[:1000])
    # Retired-model 404s are config noise once another model produced a more useful reason.
    useful = [f for f in failures if f.kind != "not_found"] or failures
    kinds = {f.kind for f in useful}
    kind = "unavailable" if "unavailable" in kinds else "quota" if "quota" in kinds else useful[0].kind

    groups: dict = {}
    for f in useful:
        models = groups.setdefault((f.provider, f.kind), [])
        if f.model and f.model not in models:
            models.append(f.model)
    phrases = {
        "quota": "API quota exhausted for {p}",
        "unavailable": "{p} overloaded or unreachable",
        "auth": "{p} rejected the API key",
        "not_found": "{p} model not available",
        "bad_response": "{p} returned an unusable response",
    }
    parts = []
    for (provider, k), models in groups.items():
        text = phrases.get(k, "{p} failed").format(p=provider)
        detail = ", ".join(models) if provider == "Gemini" else ""
        if k == "unavailable":
            detail = f"{detail}; " if detail else ""
            detail += f"HTTP 5xx or timeout after {MAX_RETRIES} retries each"
        parts.append(f"{text} ({detail})" if detail else text)
    provider = useful[0].provider if len({f.provider for f in useful}) == 1 else "multiple"
    return LLMError(kind, provider, ". ".join(parts) + ".", "\n".join(f.detail[:500] for f in failures))
