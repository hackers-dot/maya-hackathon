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
    assert registry.log == [{"tool": "delete_site", "args": "{}", "ok": False, "status": "rejected",
                             "summary": out["summary"]}]
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
        final_reply([{"finding_id": "missing_csp:/", "claim_type": "header_missing",
                      "quoted_evidence": "content-type, server"}]),
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


def test_calls_over_step_limit_are_deferred_then_run_later(env):
    registry, budget, requests = env
    calls = [call("run_check", {"check_id": "missing_csp", "path": f"/p{i}"}, cid=f"c{i}") for i in range(15)]
    retry = calls[4:8]  # the model repeats 4 deferred calls in the next step
    provider = MockProvider(script=[tool_reply(*calls), tool_reply(*retry), final_reply([])])
    result = run_agent(provider, registry, budget, "go")

    step1 = [t for t in result.trace if t["step"] == 1]
    assert [t["result_summary"] for t in step1] == ["1 finding(s)"] * 4 + ["deferred"] * 11
    assert [e["status"] for e in registry.log[:15]] == ["ok"] * 4 + ["deferred"] * 11  # not "rejected"
    # every call id answered, so the API conversation stays valid; deferred ones carry the retry message
    tool_msgs = [m for m in provider.calls[1]["messages"] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == [f"c{i}" for i in range(15)]
    deferred = json.loads(tool_msgs[10]["content"])
    assert deferred["status"] == "deferred"
    assert deferred["message"] == "deferred: per-step limit reached, call again next step"
    # deferred calls were never executed in step 1, and ran when repeated in step 2
    assert requests == [f"/p{i}" for i in range(4)] + [f"/p{i}" for i in range(4, 8)]
    assert [t["result_summary"] for t in result.trace if t["step"] == 2] == ["1 finding(s)"] * 4
    assert result.stop_reason == "final"


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
        {"finding_id": "missing_csp:/", "claim_type": "header_missing", "quoted_evidence": "content-type, server"},
        {"finding_id": "exposed_git_dir:/.git/HEAD", "claim_type": "file_exposed", "quoted_evidence": "ref: refs"},
        {"finding_id": "server_version_disclosure:/:server", "claim_type": "version_disclosed",
         "quoted_evidence": "server: Shop/9.9-invented"},
        {"finding_id": "missing_csp:/", "claim_type": "file_exposed", "quoted_evidence": "content-type, server"},
        {"finding_id": "missing_csp:/", "claim_type": "header_missing", "quoted_evidence": "content-type, server",
         "severity": "critical"},
        {"finding_id": "missing_csp:/", "claim_type": "header_missing", "quoted_evidence": "H"},
        {"finding_id": "missing_csp:/", "claim_type": "made_up", "quoted_evidence": "content-type, server"},
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
    assert v["supporting_evidence"] == "recorded response headers: content-length, content-type, server"
    reasons = [i["reason"] for i in report["insufficient"]]
    assert all(i["status"] == "insufficient evidence" for i in report["insufficient"])
    assert reasons[0] == "unknown finding_id"
    assert reasons[1] == "quote not found verbatim in evidence"
    assert reasons[2].startswith("claim_type 'file_exposed' does not match")
    assert all(r.startswith("malformed claim") for r in reasons[3:])
    assert len(reasons) == 7


# --- verifier entailment: the quote must show the issue, and code re-checks the record ---

def csp_claim(quote):
    return [{"finding_id": "missing_csp:/", "claim_type": "header_missing", "quoted_evidence": quote}]


def test_homepage_body_quote_for_missing_csp_is_dropped(env):
    registry, _, _ = env
    registry.call("run_check", json.dumps({"check_id": "missing_csp", "path": "/"}))
    for quote in ["<p>shop</p>", "HTTP 200", "server: Shop/2.1"]:  # all real text in the record, none shows the gap
        report = verify(csp_claim(quote), registry.findings, registry.store)
        assert report["verified"] == [], quote
        assert report["insufficient"][0]["reason"] == "quote does not support claim"


def test_header_list_quote_is_kept(env):
    registry, _, _ = env
    registry.call("run_check", json.dumps({"check_id": "missing_csp", "path": "/"}))
    line = "recorded response headers: content-length, content-type, server"
    for quote in [line, "content-type, server"]:
        report = verify(csp_claim(quote), registry.findings, registry.store)
        assert [v["supporting_evidence"] for v in report["verified"]] == [line]


def test_header_claim_dropped_when_record_shows_the_header(env):
    """Even a header-list quote fails if the stored record actually contains the header."""
    registry, _, _ = env
    from src.scanner.findings import make_finding
    rid = registry.store.add("GET", BASE + "/", [], 200, [("Content-Type", "text/html"),
                                                         ("Content-Security-Policy", "default-src 'self'")], b"x")
    fake = make_finding("missing_csp", BASE, "/", "d", "e", "t", rid)
    report = verify(csp_claim("recorded response headers"), {fake.id: fake}, registry.store)
    assert report["insufficient"][0]["reason"].startswith("quote does not support claim")


def env_finding(registry, status, body, ctype="text/plain"):
    from src.scanner.findings import make_finding
    rid = registry.store.add("GET", BASE + "/.env", [], status, [("Content-Type", ctype)], body)
    f = make_finding("exposed_env_file", BASE, "/.env", "d", "e", "t", rid)
    return {f.id: f}


def env_claim(quote):
    return [{"finding_id": "exposed_env_file:/.env", "claim_type": "file_exposed", "quoted_evidence": quote}]


def test_exposed_env_kept_only_with_200_and_signature(env):
    registry, _, _ = env
    good = env_finding(registry, 200, b"# config\nAPI_KEY=fake-demo-key-not-real\n")
    kept = verify(env_claim("API_KEY=fak...al"), good, registry.store)  # stored body is redacted
    assert [v["supporting_evidence"] for v in kept["verified"]] == ["API_KEY=fak...al"]
    # same record, but the quote is not a KEY=value line
    assert verify(env_claim("# config"), good, registry.store)["insufficient"][0]["reason"] == \
        "quote does not support claim"
    # quote present, but status is not 200
    not_200 = env_finding(registry, 404, b"API_KEY=fake-demo-key-not-real\n")
    r = verify(env_claim("API_KEY=fak...al"), not_200, registry.store)
    assert r["verified"] == [] and r["insufficient"][0]["reason"].startswith("quote does not support claim")
    # 200 but no KEY=value signature
    no_sig = env_finding(registry, 200, b"hello world, nothing here\n")
    r = verify(env_claim("hello world"), no_sig, registry.store)
    assert r["verified"] == [] and r["insufficient"][0]["reason"].startswith("quote does not support claim")
    # 200 with the signature but served as HTML (a catch-all page)
    html = env_finding(registry, 200, b"API_KEY=fake-demo-key-not-real\n", ctype="text/html")
    assert verify(env_claim("API_KEY=fak...al"), html, registry.store)["verified"] == []


def test_verifier_drops_tampered_evidence(env):
    registry, _, _ = env
    registry.call("run_check", json.dumps({"check_id": "missing_csp", "path": "/"}))
    rid = registry.findings["missing_csp:/"].evidence[0].record_id
    claim = csp_claim("content-type, server")
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
            {"finding_id": "missing_csp:/", "claim_type": "header_missing",
             "quoted_evidence": "content-type, server, set-cookie"},
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
