"""Thin wrapper around any OpenAI-compatible chat endpoint (Gemini by default).

Gemini, Groq, OpenRouter, Ollama and OpenAI all speak this protocol, so switching
providers is only a matter of base URL, key and model name.
"""

import time
from typing import Protocol

import openai
from openai import OpenAI


class LLM(Protocol):
    def chat(self, system: str, messages: list[dict]) -> str: ...


class LLMError(RuntimeError):
    pass


# Status codes where trying another model can help (overloaded, rate-limited, unknown model).
_TRY_NEXT_MODEL = {404, 429, 500, 502, 503, 504}

_HINTS = {
    400: "The provider rejected the request. For Gemini this usually means the API key is invalid.",
    401: "The API key was rejected. Check it in Settings.",
    403: "The API key isn't allowed to use this API. Check the key or enable the API for its project.",
    404: "The model name wasn't found. Check the model in Settings.",
    429: "Rate limit or free-tier quota reached. Wait a minute, or switch model in Settings.",
    503: "The provider is overloaded right now (not a problem with your key). Try again shortly, "
         "or pick another model in Settings.",
}


def _describe(e: Exception, model: str) -> tuple[int | None, str]:
    """(status code, readable message) for a provider error."""
    if isinstance(e, openai.APIStatusError):
        status = e.status_code
        detail = e.message
        body = e.body
        if isinstance(body, list) and body:  # Gemini wraps errors in a list
            body = body[0]
        if isinstance(body, dict):
            detail = (body.get("error") or body).get("message") or detail
        hint = _HINTS.get(status, _HINTS[503] if status >= 500 else "")
        return status, f"{model}: HTTP {status}: {detail}. {hint}".strip()
    if isinstance(e, openai.APITimeoutError):
        return None, f"{model}: timed out (the model is probably overloaded)."
    if isinstance(e, openai.APIConnectionError):
        return None, f"{model}: could not reach the LLM provider ({e}). Check the base URL and your internet."
    return None, f"{model}: {e}"


COOLDOWN_SECONDS = 300  # skip a model this long after it was overloaded or timed out


class OpenAICompatibleLLM:
    def __init__(self, base_url: str, api_key: str, model: str,
                 fallback_models: list[str] | None = None, timeout: float = 30):
        if not api_key:
            raise LLMError("No LLM API key configured. Add one in Settings or set GEMINI_API_KEY.")
        self.models = [model] + [m for m in (fallback_models or []) if m and m != model]
        # One quick SDK retry; beyond that, moving to a fallback model is faster.
        self.client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout, max_retries=1)
        self._cooling: dict[str, float] = {}  # model -> monotonic time it may be retried

    def _ordered_models(self) -> list[str]:
        now = time.monotonic()
        ready = [m for m in self.models if self._cooling.get(m, 0) <= now]
        return ready + [m for m in self.models if m not in ready]  # cooled ones as last resort

    def chat(self, system: str, messages: list[dict]) -> str:
        errors = []
        for model in self._ordered_models():
            try:
                resp = self.client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": system}, *messages],
                    temperature=0,
                )
                self._cooling.pop(model, None)
                return (resp.choices[0].message.content or "").strip()
            except Exception as e:
                status, msg = _describe(e, model)
                errors.append(msg)
                transient = status in _TRY_NEXT_MODEL or isinstance(e, openai.APIConnectionError)
                if not transient:
                    break  # bad key or bad request: another model won't help
                self._cooling[model] = time.monotonic() + COOLDOWN_SECONDS
        raise LLMError("LLM request failed. " + " | ".join(errors))
