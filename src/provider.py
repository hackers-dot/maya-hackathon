"""OpenAI-compatible chat client. The API key is read from the environment at call time only
and is never logged; model reasoning is separated from the final answer and never logged."""
import os
from dataclasses import dataclass, field
from pathlib import Path

import httpx

DEFAULT_MAX_TOKENS = 16384  # reasoning eats budget before the answer starts
REASONING_FIELDS = ("reasoning_content", "reasoning")


class ProviderError(RuntimeError):
    pass


@dataclass
class ChatResult:
    answer: str
    reasoning_present: bool
    usage: dict
    demo: bool = False
    reasoning: str | None = field(default=None, repr=False)  # kept out of repr so it never lands in logs


def load_config(path: str | Path = "config.yaml") -> dict:
    # ponytail: flat "key: value" only, so no pyyaml dependency; add it if config grows nesting
    cfg = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            key, _, value = line.partition(":")
            cfg[key.strip()] = value.strip()
    return cfg


def split_reasoning(message: dict) -> tuple[str, str | None]:
    """Return (answer, reasoning). Handles a separate reasoning field and inline <think>...</think>."""
    content = message.get("content") or ""
    if not isinstance(content, str):
        raise ProviderError(f"unexpected content type: {type(content).__name__}")
    found = [str(message[f]) for f in REASONING_FIELDS if message.get(f)]
    if "</think>" in content:  # some models omit the opening tag, so split on the closing one
        head, _, content = content.rpartition("</think>")
        found.append(head.replace("<think>", "").strip())
    reasoning = "\n".join(r for r in found if r.strip())
    return content.strip(), reasoning or None


def _scrub(text: str, secret: str) -> str:
    return text.replace(secret, "[REDACTED]")


class Provider:
    demo = False

    def __init__(self, base_url: str, model: str, api_key_env: str,
                 max_tokens: int = DEFAULT_MAX_TOKENS, timeout: float = 300.0, transport=None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key_env = api_key_env
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.transport = transport  # tests inject httpx.MockTransport

    @classmethod
    def from_config(cls, path: str | Path = "config.yaml", **kw) -> "Provider":
        c = load_config(path)
        return cls(c["base_url"], c["model"], c["api_key_env"], **kw)

    def chat(self, messages: list[dict], max_tokens: int | None = None) -> ChatResult:
        key = os.environ.get(self.api_key_env)
        if not key:
            raise ProviderError(f"environment variable {self.api_key_env} is not set; "
                                "set it as a user environment variable (never in a file)")
        body = {"model": self.model, "messages": messages, "max_tokens": max_tokens or self.max_tokens}
        try:
            with httpx.Client(transport=self.transport, timeout=self.timeout) as client:
                r = client.post(f"{self.base_url}/chat/completions", json=body,
                                headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError as e:
            raise ProviderError(f"request failed: {type(e).__name__}: {_scrub(str(e), key)}") from None
        if r.status_code != 200:
            raise ProviderError(f"HTTP {r.status_code}: {_scrub(r.text[:300], key)}")
        try:
            data = r.json()
            choice = data["choices"][0]
            message = choice["message"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise ProviderError("unexpected response shape: no choices[0].message") from None
        answer, reasoning = split_reasoning(message)
        if not answer and choice.get("finish_reason") == "length":
            raise ProviderError("hit max_tokens before the final answer; raise max_tokens")
        return ChatResult(answer, reasoning is not None, data.get("usage") or {}, reasoning=reasoning)


class MockProvider:
    demo = True

    def __init__(self, answer: str = "Hello from MAYA's mock provider."):
        self.answer = answer

    def chat(self, messages: list[dict], max_tokens: int | None = None) -> ChatResult:
        return ChatResult(self.answer, False,
                          {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, demo=True)
