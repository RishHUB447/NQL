import httpx
import openai
import pytest

from nql.llm import LLMError, OpenAICompatibleLLM


def _status_error(code: int, message: str) -> openai.APIStatusError:
    req = httpx.Request("POST", "https://example.test/v1/chat/completions")
    resp = httpx.Response(code, request=req)
    return openai.APIStatusError(message, response=resp,
                                 body=[{"error": {"code": code, "message": message}}])


class _Choice:
    def __init__(self, text):
        self.message = type("M", (), {"content": text})()


def _llm(monkeypatch, outcomes: dict) -> tuple[OpenAICompatibleLLM, list]:
    llm = OpenAICompatibleLLM("https://example.test/v1", "k", "main", ["backup"])
    tried = []

    def create(model, **_):
        tried.append(model)
        out = outcomes[model]
        if isinstance(out, Exception):
            raise out
        return type("R", (), {"choices": [_Choice(out)]})()

    monkeypatch.setattr(llm.client.chat.completions, "create", create)
    return llm, tried


def test_overloaded_model_falls_back(monkeypatch):
    llm, tried = _llm(monkeypatch, {"main": _status_error(503, "The model is overloaded."),
                                    "backup": "SELECT 1"})
    assert llm.chat("s", []) == "SELECT 1"
    assert tried == ["main", "backup"]


def test_timeout_falls_back_and_slow_model_is_skipped_next_time(monkeypatch):
    timeout = openai.APITimeoutError(httpx.Request("POST", "https://example.test/v1/x"))
    llm, tried = _llm(monkeypatch, {"main": timeout, "backup": "SELECT 1"})
    assert llm.chat("s", []) == "SELECT 1"
    assert llm.chat("s", []) == "SELECT 1"
    assert tried == ["main", "backup", "backup"]  # main is cooling down on the 2nd call


def test_bad_key_does_not_try_other_models_and_explains(monkeypatch):
    llm, tried = _llm(monkeypatch, {"main": _status_error(400, "API key not valid."),
                                    "backup": "SELECT 1"})
    with pytest.raises(LLMError, match="API key not valid.*invalid"):
        llm.chat("s", [])
    assert tried == ["main"]


def test_all_models_failing_reports_each(monkeypatch):
    err = _status_error(503, "The model is overloaded.")
    llm, _ = _llm(monkeypatch, {"main": err, "backup": err})
    with pytest.raises(LLMError, match="main: HTTP 503.*backup: HTTP 503.*overloaded right now"):
        llm.chat("s", [])
