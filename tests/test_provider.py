import json
import logging
from pathlib import Path

import httpx
import pytest

from src.provider import MockProvider, Provider, ProviderError, load_config, split_reasoning

ENV = "MAYA_TEST_FAKE_KEY"   # test-only variable; the real key env var is never touched
FAKE_KEY = "fake-key-for-tests-not-real"
ROOT = Path(__file__).resolve().parent.parent


def provider(handler):
    return Provider("https://llm.invalid/v1", "m", ENV, transport=httpx.MockTransport(handler))


def ok(message, usage=None, finish="stop"):
    return lambda req: httpx.Response(200, json={
        "choices": [{"message": message, "finish_reason": finish}],
        "usage": usage or {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12}})


def test_config_has_no_secrets():
    cfg = load_config(ROOT / "config.yaml")
    assert cfg == {"base_url": "https://inference.baseten.co/v1",
                   "model": "meta-models/muse-spark-open-1026",
                   "api_key_env": "BASETEN_API_KEY"}


def test_error_when_key_unset(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    with pytest.raises(ProviderError, match=f"{ENV} is not set"):
        provider(lambda r: pytest.fail("must not make a request")).chat([])


def test_request_shape(monkeypatch):
    monkeypatch.setenv(ENV, FAKE_KEY)
    seen = {}

    def handler(req):
        seen["url"], seen["auth"], seen["body"] = str(req.url), req.headers["authorization"], json.loads(req.content)
        return ok({"content": "hi"})(req)

    provider(handler).chat([{"role": "user", "content": "x"}])
    assert seen["url"] == "https://llm.invalid/v1/chat/completions"
    assert seen["auth"] == f"Bearer {FAKE_KEY}"
    assert seen["body"]["max_tokens"] >= 8192


@pytest.mark.parametrize("message,answer,reasoning", [
    ({"content": "Hello!", "reasoning_content": "think A"}, "Hello!", "think A"),
    ({"content": "Hello!", "reasoning": "think B"}, "Hello!", "think B"),
    ({"content": "<think>think C</think>\n\nHello!"}, "Hello!", "think C"),
    ({"content": "think D</think>Hello!"}, "Hello!", "think D"),        # opening tag omitted
    ({"content": "Hello!"}, "Hello!", None),
    ({"content": "Hello!", "reasoning_content": None}, "Hello!", None),
])
def test_split_reasoning_shapes(message, answer, reasoning):
    assert split_reasoning(message) == (answer, reasoning)


def test_chat_separates_reasoning(monkeypatch):
    monkeypatch.setenv(ENV, FAKE_KEY)
    r = provider(ok({"content": "<think>secret plan</think>Hello!"})).chat([])
    assert (r.answer, r.reasoning_present, r.usage["total_tokens"]) == ("Hello!", True, 12)
    assert "secret plan" not in repr(r)


def test_length_cutoff_is_clear_error(monkeypatch):
    monkeypatch.setenv(ENV, FAKE_KEY)
    with pytest.raises(ProviderError, match="max_tokens"):
        provider(ok({"content": "", "reasoning_content": "long..."}, finish="length")).chat([])


@pytest.mark.parametrize("handler", [
    lambda req: httpx.Response(401, text=f"bad key {req.headers['authorization']}"),  # server echoes key
    lambda req: (_ for _ in ()).throw(httpx.ConnectError(f"boom {req.headers['authorization']}")),
    lambda req: httpx.Response(200, text="not json"),
])
def test_key_never_in_errors_or_logs(monkeypatch, caplog, handler):
    monkeypatch.setenv(ENV, FAKE_KEY)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ProviderError) as exc:
        provider(handler).chat([])
    assert FAKE_KEY not in str(exc.value)
    assert FAKE_KEY not in caplog.text


def test_mock_provider_is_demo():
    r = MockProvider().chat([{"role": "user", "content": "hi"}])
    assert r.demo and r.answer and not r.reasoning_present
