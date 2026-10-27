import json
import sqlite3

import httpx
import pytest

from src.agent.loop import FINAL_INSTRUCTION, MAX_STEPS, SYSTEM_PROMPT, run_agent
from src.agent.tools import MAX_GET_EVIDENCE, RUNNABLE_CHECKS, TOOL_SPECS, ToolRegistry
from src.agent.verifier import TEMPLATES, evidence_line, verify
from src.budget import TokenBudget
from src.evidence import EvidenceStore
from src.provider import ChatResult, MockProvider
from src.scanner.http_client import HttpClient
from src.severity import TABLE, severity

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


NOT_RUNNABLE = ["missing_spf", "missing_dmarc", "weak_tls_or_expiring_cert"]


@pytest.mark.parametrize("check_id", NOT_RUNNABLE)
def test_non_http_checks_not_offered_and_rejected(env, check_id):
    registry, _, requests = env
    enum = TOOL_SPECS[0]["function"]["parameters"]["properties"]["check_id"]["enum"]
    assert check_id not in enum and check_id not in RUNNABLE_CHECKS
    assert check_id in TABLE  # still in the severity table
    out = registry.call("run_check", json.dumps({"check_id": check_id, "path": "/"}))
    assert out["error"] == f"rejected: check_id not available: {check_id!r}" and requests == []


def test_enum_and_prompt_list_exactly_the_runnable_checks():
    enum = TOOL_SPECS[0]["function"]["parameters"]["properties"]["check_id"]["enum"]
    assert enum == RUNNABLE_CHECKS and len(enum) == 12 and set(enum) == set(TABLE) - set(NOT_RUNNABLE)
    listed = SYSTEM_PROMPT.split("The runnable checks are exactly: ")[1].split(". ")[0]
    assert listed.split(", ") == RUNNABLE_CHECKS
    assert not any(c in SYSTEM_PROMPT for c in NOT_RUNNABLE)


def test_tool_specs_match_allowlist():
    assert [s["function"]["name"] for s in TOOL_SPECS] == ["run_check", "list_findings", "get_evidence"]


def test_run_check_returns_evidence_summary(env):
    registry, _, _ = env
    out = registry.call("run_check", json.dumps({"check_id": "missing_csp", "path": "/"}))
    assert out["result"]["findings"][0]["evidence_summary"] == (
        "missing_csp:/ | missing_csp | recorded response headers: content-length, content-type, server")


def test_get_evidence_capped_at_two(env):
    registry, _, _ = env
    rid = registry.call("run_check", json.dumps({"check_id": "missing_csp", "path": "/"}))["result"]["findings"][0][
        "evidence_id"]
    outs = [registry.call("get_evidence", json.dumps({"id": rid})) for _ in range(4)]
    assert [o["ok"] for o in outs] == [True, True, False, False]
    assert outs[2]["message"].startswith("limit reached") and MAX_GET_EVIDENCE == 2
    assert [e["status"] for e in registry.log[1:]] == ["ok", "ok", "limited", "limited"]


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


def test_last_step_forces_final_answer_request(env):
    """A model that keeps calling tools still gets a final-answer request on the last step."""
    registry, budget, _ = env
    provider = MockProvider(script=[tool_reply(call("list_findings", {})) for _ in range(20)])
    result = run_agent(provider, registry, budget, "go", max_steps=3)
    assert len(provider.calls) == 3
    assert [c["tool_choice"] for c in provider.calls] == ["auto", "auto", "none"]
    assert provider.calls[-1]["messages"][-1] == {"role": "user", "content": FINAL_INSTRUCTION}
    assert result.stop_reason == "forced_final" and not result.final_answer_valid
    assert result.trace[-1]["result_summary"] == (
        "forced final step: final answer is not valid claims JSON (tool calls ignored)")
    assert len(registry.log) == 2  # the tool call returned on the forced step was never executed


def test_forced_final_with_valid_claims(env):
    registry, budget, _ = env
    provider = MockProvider(script=[
        tool_reply(call("run_check", {"check_id": "missing_csp", "path": "/"})),
        final_reply([{"finding_id": "missing_csp:/", "claim_type": "header_missing",
                      "quoted_evidence": "content-type, server"}]),
    ])
    result = run_agent(provider, registry, budget, "go", max_steps=2)
    assert provider.calls[1]["tool_choice"] == "none"
    assert result.stop_reason == "forced_final" and result.final_answer_valid
    assert [v["finding_id"] for v in result.report["verified"]] == ["missing_csp:/"]


def test_default_max_steps_is_8(env):
    registry, budget, _ = env
    provider = MockProvider(script=[tool_reply(call("list_findings", {})) for _ in range(20)])
    assert run_agent(provider, registry, budget, "go").stop_reason == "forced_final" and len(provider.calls) == 8
    assert provider.calls[7]["tool_choice"] == "none" and MAX_STEPS == 8


def test_final_requested_once_all_checks_attempted(env):
    registry, budget, _ = env
    batches = [RUNNABLE_CHECKS[i:i + 4] for i in range(0, 12, 4)]
    provider = MockProvider(script=[
        tool_reply(*[call("run_check", {"check_id": c, "path": "/"}, cid=c) for c in batch]) for batch in batches
    ] + [final_reply([])])
    result = run_agent(provider, registry, budget, "go")  # max_steps 8, but all 12 checks done after step 3
    assert registry.all_checks_attempted()
    assert [c["tool_choice"] for c in provider.calls] == ["auto", "auto", "auto", "none"]
    assert result.stop_reason == "forced_final" and result.final_answer_valid


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
    assert result.report["verified"] == [] and result.report["insufficient"] == []


# --- verifier: claims carry no quote; code attaches the evidence line ---

def claim(fid="missing_csp:/", ctype="header_missing", **extra):
    return {"finding_id": fid, "claim_type": ctype, **extra}


def test_claim_without_quote_is_kept_with_code_attached_evidence(env):
    registry, _, _ = env
    registry.call("run_check", json.dumps({"check_id": "missing_csp", "path": "/"}))
    report = verify([claim(priority=1, note="Add a CSP header to /.")], registry.findings, registry.store)
    v = report["verified"][0]
    f = registry.findings["missing_csp:/"]
    assert "quoted_evidence" not in v
    assert v["supporting_evidence"] == "recorded response headers: content-length, content-type, server"
    assert v["severity"] == severity("missing_csp") and v["evidence_id"] == f.evidence[0].record_id
    assert v["text"] == TEMPLATES["header_missing"].format(location="/", target=BASE, check="missing_csp",
                                                           severity="medium")
    assert v["note"] == "Add a CSP header to /." and v["note_dropped"] is None


def test_legacy_quote_is_ignored(env):
    """A quote the model still sends (even a wrong one) neither helps nor hurts: code attaches its own line."""
    registry, _, _ = env
    registry.call("run_check", json.dumps({"check_id": "missing_csp", "path": "/"}))
    v = verify([claim(quoted_evidence="<p>shop</p>")], registry.findings, registry.store)["verified"][0]
    assert v["supporting_evidence"].startswith("recorded response headers:")


def test_invalid_claims_dropped(env):
    registry, _, _ = env
    registry.call("run_check", json.dumps({"check_id": "missing_csp", "path": "/"}))
    claims = [
        claim(),                                              # kept
        claim("exposed_git_dir:/.git/HEAD", "file_exposed"),  # unknown finding_id
        claim(ctype="file_exposed"),                          # wrong claim_type
        claim(severity="critical"),                           # model may not set severity
        claim(ctype="made_up"),
        claim(priority=0),
        "not a dict",
    ]
    report = verify(claims, registry.findings, registry.store)
    assert [v["finding_id"] for v in report["verified"]] == ["missing_csp:/"]
    reasons = [i["reason"] for i in report["insufficient"]]
    assert all(i["status"] == "insufficient evidence" for i in report["insufficient"])
    assert reasons[0] == "unknown finding_id"
    assert reasons[1] == "claim_type 'file_exposed' does not match check 'missing_csp'"
    assert all(r.startswith("malformed claim") for r in reasons[2:]) and len(reasons) == 6


def test_claim_dropped_when_record_does_not_show_the_issue(env):
    registry, _, _ = env
    from src.scanner.findings import make_finding
    rid = registry.store.add("GET", BASE + "/", [], 200, [("Content-Type", "text/html"),
                                                         ("Content-Security-Policy", "default-src 'self'")], b"x")
    fake = make_finding("missing_csp", BASE, "/", "d", "e", "t", rid)
    report = verify([claim()], {fake.id: fake}, registry.store)
    assert report["insufficient"][0]["reason"] == "the evidence record does not show the issue"


def env_finding(registry, status, body, ctype="text/plain"):
    from src.scanner.findings import make_finding
    rid = registry.store.add("GET", BASE + "/.env", [], status, [("Content-Type", ctype)], body)
    f = make_finding("exposed_env_file", BASE, "/.env", "d", "e", "t", rid)
    return {f.id: f}


def test_exposed_env_kept_only_with_200_and_signature(env):
    registry, _, _ = env
    env_claim = [claim("exposed_env_file:/.env", "file_exposed")]
    good = env_finding(registry, 200, b"# config\nAPI_KEY=fake-demo-key-not-real\n")
    assert [v["supporting_evidence"] for v in verify(env_claim, good, registry.store)["verified"]] == [
        "API_KEY=fak...al"]  # first KEY=value line, redacted at storage
    for bad in (env_finding(registry, 404, b"API_KEY=fake-demo-key-not-real\n"),            # not 200
                env_finding(registry, 200, b"hello world, nothing here\n"),                 # no signature
                env_finding(registry, 200, b"API_KEY=fake-demo-key-not-real\n", "text/html")):  # catch-all page
        r = verify(env_claim, bad, registry.store)
        assert r["verified"] == [] and r["insufficient"][0]["reason"] == "the evidence record does not show the issue"


def test_verifier_drops_tampered_evidence(env):
    registry, _, _ = env
    registry.call("run_check", json.dumps({"check_id": "missing_csp", "path": "/"}))
    rid = registry.findings["missing_csp:/"].evidence[0].record_id
    assert len(verify([claim()], registry.findings, registry.store)["verified"]) == 1
    with sqlite3.connect(registry.store.path) as c:
        c.execute("UPDATE evidence SET summary = replace(summary, 'shop', 'SHOP') WHERE id = ?", (rid,))
    c.close()
    report = verify([claim()], registry.findings, registry.store)
    assert report["verified"] == [] and report["insufficient"][0]["reason"] == "evidence record missing or hash mismatch"


# --- model notes: unverified text, masked and cross-checked against the finding's own evidence ---

def note_for(env, check, note):
    registry, _, _ = env
    registry.call("run_check", json.dumps({"check_id": check, "path": "/"}))
    fid = next(f for f in registry.findings if f.startswith(check))
    from src.agent.verifier import CLAIM_FOR_CHECK
    return verify([claim(fid, CLAIM_FOR_CHECK[check], note=note)], registry.findings, registry.store)["verified"][0]


def test_note_with_invented_path_is_dropped_claim_kept(env):
    v = note_for(env, "missing_csp", "Also add CSP on /admin/login.")
    assert v["note"] is None and v["note_dropped"] == "note mentions '/admin/login', which is not in this finding's evidence"
    assert v["finding_id"] == "missing_csp:/" and v["supporting_evidence"]  # claim kept


@pytest.mark.parametrize("note,ref", [
    ("Visit evil.example to see the leak.", "evil.example"),
    ("The 3 cookies are exposed.", "3"),
    ("Server at 10.0.0.5 is affected.", "10.0.0.5"),
])
def test_note_with_invented_host_or_number_is_dropped(env, note, ref):
    v = note_for(env, "missing_csp", note)
    assert v["note"] is None and repr(ref) in v["note_dropped"]


def test_note_with_numbers_from_evidence_is_kept(env):
    v = note_for(env, "server_version_disclosure", "The server header shows version 2.1 on /.")
    assert v["note"] == "The server header shows version 2.1 on /."


def test_note_restricted_words_masked(env):
    v = note_for(env, "missing_csp", "The site is not secure, looks hacked, a scam, fake and non-compliant.")
    assert v["note"] == "The site is not [masked], looks [masked], a [masked], [masked] and non-[masked]."


def test_note_too_long_is_dropped(env):
    v = note_for(env, "missing_csp", "x " * 100)
    assert v["note"] is None and v["note_dropped"] == "note longer than 160 characters"


@pytest.mark.parametrize("note", [
    "Add the Secure flag to the session cookie.",
    "Set Secure; HttpOnly; SameSite=Lax on the cookie.",
    "The cookie lacks the `Secure` attribute.",
    "Use HttpOnly and Secure on it.",
    "Add the secure attribute.",
])
def test_cookie_attribute_secure_is_not_masked(env, note):
    assert note_for(env, "missing_csp", note)["note"] == note


@pytest.mark.parametrize("note,masked", [
    ("The site is secure.", "The site is [masked]."),
    ("Once fixed the shop is secure now.", "Once fixed the shop is [masked] now."),
    ("This page is insecure.", "This page is [masked]."),
    ("A Secure site needs this.", "A [masked] site needs this."),
    ("Not hacked, not a scam, not fake, not compliant.", "Not [masked], not a [masked], not [masked], not [masked]."),
])
def test_restricted_words_still_masked(env, note, masked):
    assert note_for(env, "missing_csp", note)["note"] == masked


# --- evidence line choice (Day 6) ---

def file_finding(registry, check, path, body: bytes, ctype="text/plain"):
    from src.scanner.findings import make_finding
    rid = registry.store.add("GET", BASE + path, [], 200, [("Content-Type", ctype)], body)
    return make_finding(check, BASE, path, "d", "e", "t", rid)


@pytest.mark.parametrize("body,line", [
    (b"APP_ENV=prod\nDEBUG=0\nAPI_KEY=fake-demo-key-not-real\n", "API_KEY=fak...al"),   # masked at storage
    (b"APP_ENV=prod\nSTRIPE_KEY=livevalue123456\n", "STRIPE_KEY=liv...56"),             # masked by the line chooser
    (b"APP_ENV=prod\nDB_PASSWORD=x\n", "DB_PASSWORD=***"),                              # short value fully masked
    (b"# c\nAPP_ENV=prod\nDEBUG=0\n", "APP_ENV=prod"),                                  # no secret-looking key
])
def test_env_evidence_line_prefers_masked_secret_key(env, body, line):
    registry, _, _ = env
    f = file_finding(registry, "exposed_env_file", "/.env", body)
    assert evidence_line(f, registry.store) == line
    assert "livevalue123456" not in evidence_line(f, registry.store)


@pytest.mark.parametrize("body,ctype,line", [
    (b"-- dump\nCREATE TABLE users (id int);\nINSERT INTO users VALUES (1);\n", "application/octet-stream",
     "CREATE TABLE users (id int);"),
    (b"-- FAKE demo dump\nINSERT INTO products VALUES (1, 'Demo mug', 9.99);\n", "application/sql",
     "INSERT INTO products VALUES (1, 'Demo mug', 9.99);"),
    (b"PK\x03\x04\x14\x00rest-of-zip", "application/zip", "PK\\x03\\x04\x14\x00rest-of-zip"),
    (b"just some bytes", "application/octet-stream", "content-type: application/octet-stream"),
])
def test_backup_evidence_line_prefers_signature(env, body, ctype, line):
    registry, _, _ = env
    f = file_finding(registry, "exposed_backup_file", "/db.sql", body, ctype)
    assert evidence_line(f, registry.store) == line


# --- priorities ---

def three_findings(env):
    registry, _, _ = env
    for c in ("missing_csp", "missing_x_content_type_options", "server_version_disclosure"):
        registry.call("run_check", json.dumps({"check_id": c, "path": "/"}))
    return registry


def ordered(env, p_csp, p_xcto, p_ver):
    registry = three_findings(env)
    claims = [claim("server_version_disclosure:/:server", "version_disclosed", priority=p_ver),
              claim("missing_x_content_type_options:/", priority=p_xcto),
              claim("missing_csp:/", priority=p_csp)]
    report = verify([{k: v for k, v in c.items() if v is not None} for c in claims], registry.findings,
                    registry.store)
    return [v["check"] for v in report["verified"]], report["order"], report["order_note"]


def test_valid_priorities_set_the_order(env):
    checks, order, _ = ordered(env, 3, 1, 2)  # model puts a low finding first: allowed
    assert checks == ["missing_x_content_type_options", "server_version_disclosure", "missing_csp"] and order == "model"


@pytest.mark.parametrize("prios", [(1, 1, 2), (1, None, 2), (None, None, None)], ids=["duplicate", "missing", "none"])
def test_bad_priorities_fall_back_to_severity(env, prios):
    checks, order, note = ordered(env, *prios)
    assert order == "severity" and note == "ordered by severity (model priorities were duplicate or missing)"
    assert checks == ["missing_csp", "missing_x_content_type_options", "server_version_disclosure"]  # medium, low, low


# --- end to end against the demo app (scripted model, real HTTP to 127.0.0.1) ---

def e2e_script():
    return [
        tool_reply(call("run_check", {"check_id": "missing_csp", "path": "/"}, "a"),
                   call("run_check", {"check_id": "exposed_env_file", "path": "/.env"}, "b")),
        tool_reply(call("list_findings", {})),
        final_reply([
            {"finding_id": "missing_csp:/", "claim_type": "header_missing", "priority": 2,
             "note": "The homepage / sends no Content-Security-Policy header."},
            {"finding_id": "exposed_env_file:/.env", "claim_type": "file_exposed", "priority": 1,
             "note": "The /.env file is public; block it and rotate its keys."},
            {"finding_id": "exposed_git_dir:/.git/HEAD", "claim_type": "file_exposed", "priority": 3},
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
        assert [v["finding_id"] for v in result.report["verified"]] == ["exposed_env_file:/.env", "missing_csp:/"]
        assert result.report["order"] == "model"  # priorities 1, 2 are unique among verified claims
        assert verified["exposed_env_file:/.env"]["severity"] == "critical"
        assert verified["exposed_env_file:/.env"]["supporting_evidence"] == "API_KEY=fak...al"
        assert verified["missing_csp:/"]["note"] == "The homepage / sends no Content-Security-Policy header."
        assert all(v["demo"] for v in verified.values())
        assert [i["finding_id"] for i in result.report["insufficient"]] == ["exposed_git_dir:/.git/HEAD"]
        stored = store.get(verified["exposed_env_file:/.env"]["evidence_id"])["response"]["body"]
        assert "fake-demo-key-not-real" not in stored
