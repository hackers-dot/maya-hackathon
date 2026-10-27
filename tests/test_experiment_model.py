import base64
import json
import socket
import struct
import zlib

import httpx
import pytest

from scripts import experiment_model as ex
from src.severity import TABLE

FAKE_KEY = "fake-key-for-tests-not-real"


@pytest.fixture
def bodies():
    return dict(ex.build_bodies("test-model"))


def test_four_bodies_in_order(bodies):
    assert list(bodies) == ["A_hello_low_effort", "B_tool_call", "C_json_schema", "D_vision"]
    for b in bodies.values():
        assert b["model"] == "test-model" and b["max_tokens"] == 1500


def test_a_low_effort(bodies):
    a = bodies["A_hello_low_effort"]
    assert a["reasoning_effort"] == "low"
    assert a["messages"] == [{"role": "user", "content": "Say hello in one sentence."}]


def test_b_tool_schema(bodies):
    b = bodies["B_tool_call"]
    assert b["tool_choice"] == "auto" and b["reasoning_effort"] == "low"
    fn = b["tools"][0]["function"]
    assert fn["name"] == "run_check"
    props = fn["parameters"]["properties"]
    assert props["check_id"]["enum"] == ex.TOOL_CHECKS and len(ex.TOOL_CHECKS) == 3
    assert set(ex.TOOL_CHECKS) <= set(TABLE)  # only real check ids
    assert props["path"] == {"type": "string"}
    assert "/login" in b["messages"][0]["content"]


def test_c_strict_schema(bodies):
    rf = bodies["C_json_schema"]["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True
    schema = rf["json_schema"]["schema"]
    assert schema["properties"]["severity"]["enum"] == ["critical", "high", "medium", "low", "info"]
    assert schema["properties"]["reason"]["maxLength"] == 120
    assert schema["required"] == ["severity", "reason"] and schema["additionalProperties"] is False


def test_d_image_is_a_valid_solid_red_png(bodies):
    content = bodies["D_vision"]["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "What color is this image? One word."}
    url = content[1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    png = base64.b64decode(url.split(",", 1)[1])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    pos, chunks = 8, {}
    while pos < len(png):
        (length,) = struct.unpack(">I", png[pos:pos + 4])
        kind, data = png[pos + 4:pos + 8], png[pos + 8:pos + 8 + length]
        (crc,) = struct.unpack(">I", png[pos + 8 + length:pos + 12 + length])
        assert crc == zlib.crc32(kind + data) & 0xFFFFFFFF
        chunks[kind] = data
        pos += 12 + length
    w, h, depth, color = struct.unpack(">IIBB", chunks[b"IHDR"][:10])
    assert (w, h, depth, color) == (64, 64, 8, 2)
    assert zlib.decompress(chunks[b"IDAT"]) == (b"\x00" + b"\xff\x00\x00" * 64) * 64


def test_dry_run_no_network_no_key(monkeypatch, capsys):
    def boom(*a, **k):
        raise AssertionError("network used in dry run")
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(httpx.Client, "send", boom)
    monkeypatch.setenv("BASETEN_API_KEY", FAKE_KEY)  # test-process only; must not appear in output
    assert ex.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert FAKE_KEY not in out
    assert out.count("Bearer [REDACTED]") == 4
    assert all(n in out for n in ["A_hello_low_effort", "B_tool_call", "C_json_schema", "D_vision"])


# --- evaluators ---

def tool_msg(name="run_check", args='{"check_id": "missing_csp", "path": "/login"}'):
    return {"content": None, "tool_calls": [{"type": "function", "function": {"name": name, "arguments": args}}]}


@pytest.mark.parametrize("msg,ok", [
    (tool_msg(), True),
    (tool_msg(args='{"check_id": "exploit_it", "path": "/login"}'), False),
    (tool_msg(args="{not json"), False),
    (tool_msg(name="other"), False),
    ({"content": "I would call run_check with missing_csp"}, False),
])
def test_eval_b(msg, ok):
    passed, detail = ex.eval_b(msg, "stop")
    assert passed is ok
    if "tool_calls" not in msg:
        assert "mentions a call: yes" in detail and "finish_reason=stop" in detail


@pytest.mark.parametrize("content,ok", [
    ('{"severity": "medium", "reason": "No CSP on login."}', True),
    ('<think>hidden</think>{"severity": "high", "reason": "x"}', True),
    ('{"severity": "urgent", "reason": "x"}', False),
    ('{"severity": "low", "reason": "x", "extra": 1}', False),
    ('{"severity": "low", "reason": "' + "x" * 121 + '"}', False),
    ("not json", False),
])
def test_eval_c(content, ok):
    assert ex.eval_c({"content": content})[0] is ok


def test_eval_a_and_d():
    assert ex.eval_a({"content": "Hi!"}, {"completion_tokens": 100})[1].endswith("(-557)")
    assert ex.eval_d({"content": "Red."})[0] is True
    assert ex.eval_d({"content": "<think>red? maybe</think>Blue"})[0] is False  # reasoning never counted


# --- run(): mocked transport, fake key in the test process only ---

def test_run_aborts_on_first_http_error(monkeypatch, capsys):
    monkeypatch.setenv("BASETEN_API_KEY", FAKE_KEY)
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(500, text=f"server echoed {req.headers['authorization']}")

    assert ex.main([], transport=httpx.MockTransport(handler)) == 1
    out = capsys.readouterr().out
    assert len(calls) == 1
    assert "HTTP 500. Aborting." in out
    assert FAKE_KEY not in out and "echoed" not in out  # status only, never the body


def test_run_all_pass(monkeypatch, capsys):
    monkeypatch.setenv("BASETEN_API_KEY", FAKE_KEY)
    replies = iter([
        {"content": "<think>secret reasoning</think>Hello there!"},
        tool_msg(),
        {"content": '{"severity": "medium", "reason": "No CSP."}'},
        {"content": "Red"},
    ])

    def handler(req):
        return httpx.Response(200, json={
            "choices": [{"message": next(replies), "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 50,
                      "completion_tokens_details": {"reasoning_tokens": 30}}})

    assert ex.main([], transport=httpx.MockTransport(handler)) == 0
    out = capsys.readouterr().out
    assert out.count("PASS:") == 4 and "Summary: 4/4 PASS" in out
    assert "reasoning_tokens=30" in out
    assert "secret reasoning" not in out and FAKE_KEY not in out


def test_run_without_key(monkeypatch, capsys):
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    assert ex.main([], transport=httpx.MockTransport(lambda r: pytest.fail("no request without key"))) == 2
    assert "is not set" in capsys.readouterr().out
