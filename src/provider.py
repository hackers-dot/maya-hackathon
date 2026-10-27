"""OpenAI-compatible chat client. The API key is read from the environment at call time only
and is never logged; model reasoning is separated from the final answer and never logged."""
import os
from dataclasses import dataclass, field
from pathlib import Path

import httpx

DEFAULT_MAX_TOKENS = 1500
DEFAULT_REASONING_EFFORT = "low"
REASONING_FIELDS = ("reasoning_content", "reasoning")
ZERO_USAGE = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


class ProviderError(RuntimeError):
    pass


@dataclass
class ChatResult:
    answer: str
    reasoning_present: bool
    usage: dict  # OpenAI-style, may include completion_tokens_details.reasoning_tokens (fed to TokenBudget)
    demo: bool = False
    tool_calls: list = field(default_factory=list)
    finish_reason: str | None = None
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

    def __init__(self, base_url: str, model: str, api_key_env: str, max_tokens: int = DEFAULT_MAX_TOKENS,
                 reasoning_effort: str | None = DEFAULT_REASONING_EFFORT, timeout: float = 300.0, transport=None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key_env = api_key_env
        self.max_tokens = max_tokens
        self.reasoning_effort = reasoning_effort
        self.timeout = timeout
        self.transport = transport  # tests inject httpx.MockTransport

    @classmethod
    def from_config(cls, path: str | Path = "config.yaml", **kw) -> "Provider":
        c = load_config(path)
        kw.setdefault("max_tokens", int(c.get("max_tokens", DEFAULT_MAX_TOKENS)))
        kw.setdefault("reasoning_effort", c.get("reasoning_effort", DEFAULT_REASONING_EFFORT))
        return cls(c["base_url"], c["model"], c["api_key_env"], **kw)

    def chat(self, messages: list[dict], max_tokens: int | None = None, tools: list | None = None,
             tool_choice: str | dict | None = None, response_format: dict | None = None,
             reasoning_effort: str | None = None) -> ChatResult:
        key = os.environ.get(self.api_key_env)
        if not key:
            raise ProviderError(f"environment variable {self.api_key_env} is not set; "
                                "set it as a user environment variable (never in a file)")
        body = {"model": self.model, "messages": messages, "max_tokens": max_tokens or self.max_tokens}
        if reasoning_effort or self.reasoning_effort:
            body["reasoning_effort"] = reasoning_effort or self.reasoning_effort
        if tools:
            body["tools"] = tools
            body["tool_choice"] = tool_choice or "auto"
        if response_format:
            body["response_format"] = response_format
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
        tool_calls = message.get("tool_calls") or []
        finish = choice.get("finish_reason")
        if not answer and not tool_calls and finish == "length":
            raise ProviderError("hit max_tokens before the final answer; raise max_tokens")
        return ChatResult(answer, reasoning is not None, data.get("usage") or {}, tool_calls=tool_calls,
                          finish_reason=finish, reasoning=reasoning)


class MockProvider:
    """Offline provider (demo=True). With `script`, returns those ChatResults in order (tests, offline demos)."""
    demo = True

    def __init__(self, answer: str = "Hello from MAYA's mock provider.", script: list[ChatResult] | None = None):
        self.answer = answer
        self.script = list(script or [])
        self.calls: list[dict] = []

    def chat(self, messages: list[dict], **kw) -> ChatResult:
        self.calls.append({"messages": [dict(m) for m in messages], **kw})
        if self.script:
            return self.script.pop(0)
        return ChatResult(self.answer, False, dict(ZERO_USAGE), demo=True)
