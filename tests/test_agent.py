import json
import sqlite3

import httpx
import pytest

from src.agent.loop import run_agent
from src.agent.tools import TOOL_SPECS, ToolRegistry
from src.agent.verifier import TEMPLATES, verify
from src.budget import TokenBudget
from src.evidence import EvidenceStore
from src.provider import ChatResult, MockProvider
from src.scanner.http_client import HttpClient
from src.severity import severity

BASE = "http://127.0.0.1:8081"
USAGE = {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140,
         "completion_tokens_details": {"reasoning_tokens": 30}}


def call(name, args, cid="c1"):
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": args if isinstance(args, str) else json.dumps(args)}}


def tool_reply(*calls, usage=USAGE):
    return ChatResult("", True, dict(usage), tool_calls=list(calls), finish_reason="tool_calls")


def final_reply(claims, usage=USAGE):
    return ChatResult(json.dumps({"claims": claims}), True, dict(usage), finish_reason="stop")


@pytest.fixture
def env(tmp_path):
    """Registry over a faked site; `requests` records every HTTP request that actually went out."""
    requests = []

    def site(req):
        requests.append(req.url.path)
        if req.url.path == "/.env":
            return httpx.Response(200, headers={"content-type": "text/plain"}, text="API_KEY=fake-demo-key-not-real\n")
        return httpx.Response(200, headers={"content-type": "text/html", "server": "Shop/2.1"}, text="<p>shop</p>")

    store = EvidenceStore(tmp_path / "ev.db")
    client = HttpClient(transport=httpx.MockTransport(site), store=store, min_interval=0)
    registry = ToolRegistry(client, BASE, store)
    budget = TokenBudget("test", path=tmp_path / "usage.jsonl")
    return registry, budget, requests


# --- tools ---

def test_allowed_tool_runs(env):
    registry, _, requests = env
    out = registry.call("run_check", json.dumps({"check_id": "missing_csp", "path": "/login"}))
    assert out["ok"] and out["result"]["findings"][0]["id"] == "missing_csp:/login"
    assert out["result"]["findings"][0]["severity"] == severity("missing_csp")
    assert requests == ["/login"]
    assert registry.log[-1]["ok"] is True and "missing_csp:/login" in registry.findings


def test_unknown_tool_rejected_and_logged(env):
    registry, _, requests = env
    out = registry.call("delete_site", "{}")
    assert not out["ok"] and "unknown tool" in out["error"]
    assert registry.log == [{"tool": "delete_site", "args": "{}", "ok": False, "summary": out["summary"]}]
    assert requests == []


@pytest.mark.parametrize("name,args", [
    ("run_check", "{not json"),
    ("run_check", {"check_id": "drop_tables", "path": "/"}),
    ("run_check", {"check_id": "missing_csp", "path": "http://evil.example/"}),
    ("run_check", {"check_id": "missing_csp", "path": "//evil.example/x"}),
    ("run_check", {"check_id": "missing_csp", "path": "login"}),
    ("run_check", {"check_id": "missing_csp", "path": "/a/../../etc/passwd"}),
    ("run_check", {"check_id": "missing_csp", "path": "/x@evil.example"}),
    ("run_check", {"check_id": "missing_csp", "path": "/a b"}),
    ("run_check", {"check_id": "missing_csp", "path": "/x\\y"}),
    ("run_check", {"check_id": "missing_csp", "path": 5}),
    ("run_check", {"check_id": "missing_csp"}),
    ("run_check", {"check_id": "missing_csp", "path": "/", "severity": "critical"}),
    ("list_findings", {"extra": 1}),
    ("get_evidence", {"id": "../../etc/passwd"}),
    ("run_check", "[1, 2]"),
])
def test_invalid_args_rejected(env, name, args):
    registry, _, requests = env
    raw = args if isinstance(args, str) else json.dumps(args)
    out = registry.call(name, raw)
    assert not out["ok"] and out["error"].startswith("rejected:")
    assert registry.log[-1]["ok"] is False
    assert requests == []  # never executed


def test_non_string_args_rejected(env):
    registry, _, requests = env
    assert not registry.call("run_check", {"check_id": "missing_csp", "path": "/"})["ok"]
    assert requests == []


def test_non_http_check_reports_error(env):
    registry, _, requests = env
    out = registry.call("run_check", json.dumps({"check_id": "missing_spf", "path": "/"}))
    assert not out["ok"] and "cannot run over HTTP" in out["error"] and requests == []


def test_tool_specs_match_allowlist():
    assert [s["function"]["name"] for s in TOOL_SPECS] == ["run_check", "list_findings", "get_evidence"]


# --- loop ---

def test_loop_runs_tool_then_final(env):
    registry, budget, requests = env
    provider = MockProvider(script=[
        tool_reply(call("run_check", {"check_id": "missing_csp", "path": "/"})),
        final_reply([{"finding_id": "missing_csp:/", "claim_type": "header_missing", "quoted_evidence": "HTTP 200"}]),
    ])
    result = run_agent(provider, registry, budget, "Check the shop.")
    assert result.stop_reason == "final" and result.final_answer_valid
    assert [(t["step"], t["tool"], t["result_summary"]) for t in result.trace] == [
        (1, "run_check", "1 finding(s)"), (2, None, "final answer")]
    assert result.trace[0]["tokens"] == {"prompt": 100, "completion": 40, "reasoning": 30}
    assert provider.calls[0]["tools"] == TOOL_SPECS and provider.calls[0]["tool_choice"] == "auto"
    tool_msg = provider.calls[1]["messages"][-1]
    assert tool_msg["role"] == "tool" and json.loads(tool_msg["content"])["ok"] is True
    assert [v["finding_id"] for v in result.report["verified"]] == ["missing_csp:/"]
    assert budget.run["total_tokens"] == 280 and budget.run["reasoning_tokens"] == 60
    assert requests == ["/"]


def test_loop_rejects_unknown_tool_and_continues(env):
    registry, budget, requests = env
    provider = MockProvider(script=[tool_reply(call("shell_exec", {"cmd": "rm -rf /"})), final_reply([])])
    result = run_agent(provider, registry, budget, "go")
    assert result.trace[0]["result_summary"].startswith("rejected: unknown tool")
    assert result.stop_reason == "final" and requests == []


def test_loop_stops_at_max_steps(env):
    registry, budget, _ = env
    provider = MockProvider(script=[tool_reply(call("list_findings", {})) for _ in range(20)])
    result = run_agent(provider, registry, budget, "go", max_steps=3)
    assert result.stop_reason == "max_steps"
    assert len(provider.calls) == 3 and [t["step"] for t in result.trace] == [1, 2, 3]
    assert result.report == {"verified": [], "insufficient": []}


def test_default_max_steps_is_8(env):
    registry, budget, _ = env
    provider = MockProvider(script=[tool_reply(call("list_findings", {})) for _ in range(20)])
    assert run_agent(provider, registry, budget, "go").stop_reason == "max_steps" and len(provider.calls) == 8


def test_budget_exceeded_stops_cleanly(env, tmp_path):
    registry, _, requests = env
    budget = TokenBudget("tiny", cap=100, path=tmp_path / "u2.jsonl")
    provider = MockProvider(script=[tool_reply(call("run_check", {"check_id": "missing_csp", "path": "/"}))])
    result = run_agent(provider, registry, budget, "go")  # must not raise
    assert result.stop_reason == "budget_exceeded"
    assert "stopped:" in result.trace[-1]["result_summary"]
    assert requests == [] and registry.log == []  # the tool call after the cap was never executed


def test_too_many_calls_in_one_step(env):
    registry, budget, requests = env
    calls = [call("run_check", {"check_id": "missing_csp", "path": f"/p{i}"}, cid=f"c{i}") for i in range(6)]
    result = run_agent(MockProvider(script=[tool_reply(*calls), final_reply([])]), registry, budget, "go")
    assert [t["result_summary"] for t in result.trace[:6]] == ["1 finding(s)"] * 4 + ["rejected"] * 2
    assert requests == ["/p0", "/p1", "/p2", "/p3"]


def test_non_json_final_answer(env):
    registry, budget, _ = env
    result = run_agent(MockProvider(script=[ChatResult("All good, trust me!", False, dict(USAGE))]),
                       registry, budget, "go")
    assert result.stop_reason == "final" and not result.final_answer_valid
    assert result.report == {"verified": [], "insufficient": []}


# --- verifier ---

def test_verifier_drops_fake_claims(env):
    registry, _, _ = env
    registry.call("run_check", json.dumps({"check_id": "missing_csp", "path": "/"}))
    registry.call("run_check", json.dumps({"check_id": "server_version_disclosure", "path": "/"}))
    claims = [
        {"finding_id": "missing_csp:/", "claim_type": "header_missing", "quoted_evidence": "HTTP 200"},
        {"finding_id": "exposed_git_dir:/.git/HEAD", "claim_type": "file_exposed", "quoted_evidence": "ref: refs"},
        {"finding_id": "server_version_disclosure:/:server", "claim_type": "version_disclosed",
         "quoted_evidence": "server: Shop/9.9-invented"},
        {"finding_id": "missing_csp:/", "claim_type": "file_exposed", "quoted_evidence": "HTTP 200"},
        {"finding_id": "missing_csp:/", "claim_type": "header_missing", "quoted_evidence": "HTTP 200",
         "severity": "critical"},
        {"finding_id": "missing_csp:/", "claim_type": "header_missing", "quoted_evidence": "H"},
        {"finding_id": "missing_csp:/", "claim_type": "made_up", "quoted_evidence": "HTTP 200"},
        "not a dict",
    ]
    report = verify(claims, registry.findings, registry.store)
    assert len(report["verified"]) == 1
    v = report["verified"][0]
    f = registry.findings["missing_csp:/"]
    assert v["severity"] == severity("missing_csp")  # from the table, not the claim
    assert v["text"] == TEMPLATES["header_missing"].format(location="/", target=BASE, check="missing_csp",
                                                           severity="medium")
    assert v["evidence_id"] == f.evidence[0].record_id
    reasons = [i["reason"] for i in report["insufficient"]]
    assert all(i["status"] == "insufficient evidence" for i in report["insufficient"])
    assert reasons[0] == "unknown finding_id"
    assert reasons[1] == "quote not found verbatim in evidence"
    assert reasons[2].startswith("claim_type 'file_exposed' does not match")
    assert all(r.startswith("malformed claim") for r in reasons[3:])
    assert len(reasons) == 7


def test_verifier_drops_tampered_evidence(env):
    registry, _, _ = env
    registry.call("run_check", json.dumps({"check_id": "missing_csp", "path": "/"}))
    rid = registry.findings["missing_csp:/"].evidence[0].record_id
    claim = [{"finding_id": "missing_csp:/", "claim_type": "header_missing", "quoted_evidence": "HTTP 200"}]
    assert len(verify(claim, registry.findings, registry.store)["verified"]) == 1
    with sqlite3.connect(registry.store.path) as c:
        c.execute("UPDATE evidence SET summary = replace(summary, 'shop', 'SHOP') WHERE id = ?", (rid,))
    c.close()
    report = verify(claim, registry.findings, registry.store)
    assert report["verified"] == [] and report["insufficient"][0]["reason"] == "evidence record missing or hash mismatch"


# --- end to end against the demo app (scripted model, real HTTP to 127.0.0.1) ---

def e2e_script():
    return [
        tool_reply(call("run_check", {"check_id": "missing_csp", "path": "/"}, "a"),
                   call("run_check", {"check_id": "exposed_env_file", "path": "/.env"}, "b")),
        tool_reply(call("list_findings", {})),
        final_reply([
            {"finding_id": "missing_csp:/", "claim_type": "header_missing", "quoted_evidence": "HTTP 200"},
            {"finding_id": "exposed_env_file:/.env", "claim_type": "file_exposed",
             "quoted_evidence": "API_KEY=fak...al"},
            {"finding_id": "exposed_git_dir:/.git/HEAD", "claim_type": "file_exposed", "quoted_evidence": "ref: refs"},
        ]),
    ]


@pytest.mark.parametrize("fixed", [False, True], ids=["vulnerable", "fixed"])
def test_agent_end_to_end_demo(start_demo, tmp_path, fixed):
    store = EvidenceStore(tmp_path / "ev.db")
    registry = ToolRegistry(HttpClient(store=store), start_demo(fixed), store)
    result = run_agent(MockProvider(script=e2e_script()), registry, TokenBudget("e2e", path=tmp_path / "u.jsonl"),
                       "Scan the demo shop.")
    assert result.stop_reason == "final"
    verified = {v["finding_id"]: v for v in result.report["verified"]}
    if fixed:
        assert verified == {} and registry.findings == {}
        assert [i["reason"] for i in result.report["insufficient"]] == ["unknown finding_id"] * 3
    else:
        assert set(verified) == {"missing_csp:/", "exposed_env_file:/.env"}
        assert verified["exposed_env_file:/.env"]["severity"] == "critical"
        assert all(v["demo"] for v in verified.values())
        assert [i["finding_id"] for i in result.report["insufficient"]] == ["exposed_git_dir:/.git/HEAD"]
        stored = store.get(verified["exposed_env_file:/.env"]["evidence_id"])["response"]["body"]
        assert "fake-demo-key-not-real" not in stored
