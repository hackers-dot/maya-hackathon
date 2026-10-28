"""Web 11: investigation agent, owner summary, client report, re-scan reminders. Scripted fake models only."""
import hashlib
import io
import json
import re
import threading
import time
import zipfile

import httpx
import pytest

from src import investigate as inv
from src.client_report import build as client_html
from src.owner_summary import build as summary_build
from src.owner_summary import check_reword
from src.provider import ChatResult, MockProvider
from src.reminders import DAY, due_at, ics
from src.web.server import make_server

PASSWORD = "correct-horse-battery"  # test-only


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


def call(name, args, cid):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def tool_reply(*calls, tokens=200):
    return ChatResult("", True, {"prompt_tokens": tokens - 20, "completion_tokens": 20, "total_tokens": tokens},
                      tool_calls=list(calls), finish_reason="tool_calls")


def final_reply(obj, tokens=200):
    return ChatResult(json.dumps(obj) if not isinstance(obj, str) else obj, True,
                      {"prompt_tokens": tokens - 20, "completion_tokens": 20, "total_tokens": tokens})


class Scripted(MockProvider):
    """Returns the scripted replies in order; a function in the script is called with the messages."""

    def chat(self, messages, **kw):
        self.calls.append({"messages": [dict(m) for m in messages], **kw})
        nxt = self.script.pop(0) if self.script else final_reply({"answer": "done", "claims": []})
        return nxt(messages) if callable(nxt) else nxt


@pytest.fixture
def web(tmp_path):
    state = {"chat": None, "cap": None}
    clock = Clock()

    def handler(req):
        if req.url.host == "crt.sh":
            return httpx.Response(200, json=[])
        if req.url.host == "cloudflare-dns.com":
            return httpx.Response(200, json={"Status": 3})
        return httpx.Response(599)
    server = make_server(port=0, data_dir=tmp_path, clock=clock, http_transport=httpx.MockTransport(handler),
                         provider_factory=lambda: (None, "none"),  # scans stay deterministic
                         chat_provider_factory=lambda: (state["chat"], "fake-model" if state["chat"] else None),
                         chat_token_cap=5000, lookalike_sleep=lambda s: None)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    clients = []

    def new_client():
        c = httpx.Client(base_url=base, trust_env=False, timeout=60)
        clients.append(c)
        return c

    yield {"new_client": new_client, "state": state, "clock": clock, "server": server}
    for c in clients:
        c.close()
    server.shutdown()
    server.server_close()


def post(c, path, body=None):
    return c.post(path, json=body or {}, headers={"X-CSRF-Token": c.get("/api/session").json()["csrf"]})


def company(c, email):
    assert post(c, "/api/signup", {"email": email, "password": PASSWORD}).status_code == 200
    for path, body in [("/api/profile", {"account_type": "company"}), ("/api/asset", {"asset": "website"}),
                       ("/api/connect", {"connect": True}), ("/api/consent", {"allow": True})]:
        assert post(c, path, body).status_code == 200
    return c


def scan(c, asset_id):
    run_id = post(c, f"/api/assets/{asset_id}/scan").json()["run"]["id"]
    for _ in range(300):
        body = c.get(f"/api/runs/{run_id}").json()
        if body["run"]["status"] != "running":
            return run_id, body
        time.sleep(0.1)
    raise AssertionError("scan did not finish")


def demo(c):
    asset = post(c, "/api/assets", {"demo": True}).json()["asset"]
    run_id, body = scan(c, asset["id"])
    return asset, run_id, body


def investigate_msg(c, text="What is open on my demo app?"):
    chat_id = post(c, "/api/chats").json()["chat"]["id"]
    return post(c, f"/api/chats/{chat_id}/investigate", {"content": text})


# --- 1. investigation agent ---

def test_scan_agent_unchanged_and_tools_read_only():
    from src.agent.tools import TOOL_SPECS as SCAN_TOOLS
    assert [t["function"]["name"] for t in SCAN_TOOLS] == ["run_check", "list_findings", "get_evidence"]
    names = {t["function"]["name"] for t in inv.TOOL_SPECS}
    assert names == {"get_findings", "get_scan_diff", "get_score_history", "get_fix_status", "explain_check",
                     "watch_lookalikes"}
    for n in names:  # read verbs only: no tool may scan, apply, approve, delete, send or reset
        assert n.startswith(("get_", "explain_", "watch_")), n
        assert not any(w in n for w in ("apply", "approve", "delete", "send", "reset", "run_check")), n
    src = open(inv.__file__, encoding="utf-8").read()
    for forbidden in (".start(", "gate.apply", "approve(", "delete_owner", "remove_all", "reset_demo", "httpx"):
        assert forbidden not in src, forbidden


def test_no_key_says_model_not_configured(web):
    c = company(web["new_client"](), "nokey@shop.example")
    r = investigate_msg(c)
    assert r.status_code == 409 and r.json()["error"].startswith("Model not configured")


def test_allowed_tools_and_claims_verified_against_this_user(web):
    a, b = company(web["new_client"](), "a@shop.example"), company(web["new_client"](), "b@shop.example")
    asset, run_a, body = demo(a)
    _, run_b, body_b = demo(b)
    real = body["result"]["findings"][0]["id"]
    web["state"]["chat"] = Scripted(script=[
        tool_reply(call("get_findings", {}, "c1"), call("explain_check", {"check_id": "missing_csp"}, "c2"),
                   call("get_score_history", {"asset_id": asset["id"]}, "c3")),
        final_reply({"answer": "Twelve issues are open; start with the critical one.", "claims": [
            {"text": "The .env file is exposed.", "finding_ids": [real], "run_ids": [run_a]},
            {"text": "An admin panel is open.", "finding_ids": ["exposed_admin_panel:/admin"]},
            {"text": "Bob's scan is relevant.", "run_ids": [run_b]},
            {"text": "No ids at all."},
            {"text": "x", "finding_ids": [real], "severity": "critical"}]})])
    r = investigate_msg(a)
    assert r.status_code == 200, r.text
    meta = r.json()["reply"]["meta"]
    i = meta["investigation"]
    assert meta["label"] == "unverified model text" and i["stop_reason"] == "final" and i["valid_json"]
    assert [t["tool"] for t in i["trace"]] == ["get_findings", "explain_check", "get_score_history", None]
    assert i["trace"][0]["summary"] == "12 finding(s)" and i["trace"][2]["summary"] == "1 run(s)"
    assert [c["findings"][0]["id"] for c in i["claims"]] == [real]
    assert i["claims"][0]["findings"][0]["severity"] == body["result"]["findings"][0]["severity"]
    reasons = sorted(d["reason"].split(":")[0] for d in i["dropped"])
    assert reasons == ["cites ids that are not yours", "cites ids that are not yours", "cites no finding or run",
                       "malformed claim"]
    sent = web["state"]["chat"].calls[0]
    assert sent["max_tokens"] == 1200 and sent["reasoning_effort"] == "low"
    tool_msgs = [m for m in web["state"]["chat"].calls[1]["messages"] if m["role"] == "tool"]
    assert run_b not in json.dumps(tool_msgs)  # Bob's run never reaches Alice's investigation


def test_unknown_tools_bad_args_and_other_users_data_rejected(web):
    a, b = company(web["new_client"](), "a2@shop.example"), company(web["new_client"](), "b2@shop.example")
    demo(a)
    b_asset, run_b, _ = demo(b)
    post(b, "/api/official", {"domains": ["bobshop.example"], "numbers": []})
    web["state"]["chat"] = Scripted(script=[
        tool_reply(call("delete_everything", {}, "x1"), call("get_findings", {"extra": "1"}, "x2"),
                   call("get_scan_diff", {"asset_id": "nothex"}, "x3")),
        tool_reply(call("get_findings", {"run_id": run_b}, "y1"), call("get_fix_status", {"asset_id": b_asset["id"]}, "y2"),
                   call("watch_lookalikes", {"domain": "bobshop.example"}, "y3")),
        tool_reply(call("explain_check", {"check_id": "not_a_check"}, "z1"), call("run_check", {"check_id": "missing_csp",
                                                                                                "path": "/"}, "z2"),
                   call("get_findings", {}, "z3"), call("get_findings", {}, "z4")),
        final_reply({"answer": "ok", "claims": []})])
    i = investigate_msg(a).json()["reply"]["meta"]["investigation"]
    s = [t["summary"] for t in i["trace"] if t["tool"]]
    assert s[0] == "rejected: unknown tool 'delete_everything'"
    assert s[1].startswith("rejected: expected ['run_id']")
    assert s[2] == "rejected: asset_id must be a 16-character hex id"
    assert s[3] == s[4] == "not found"  # Bob's run and asset look like missing ones
    assert s[5] == "rejected: domain must be one of the user's verified websites or official domains"
    assert s[6] == "rejected: unknown check id 'not_a_check'" and s[7] == "rejected: unknown tool 'run_check'"
    assert s[8] == "12 finding(s)" and s[9] == "rejected: too many tool calls in one step"


def test_loop_stops_at_max_steps_and_budget(web):
    c = company(web["new_client"](), "loop@shop.example")
    demo(c)
    web["state"]["chat"] = Scripted(script=[tool_reply(call("get_findings", {}, f"c{i}")) for i in range(10)])
    i = investigate_msg(c).json()["reply"]["meta"]["investigation"]
    calls = web["state"]["chat"].calls
    assert len(calls) == inv.MAX_STEPS == 6 and calls[-1]["tool_choice"] == "none"
    assert i["stop_reason"] == "max_steps" and i["trace"][-1]["summary"].endswith("(forced at the step limit)")
    c2 = company(web["new_client"](), "budget@shop.example")
    web["server"].app.chat_token_cap = 900  # remaining budget for this user's investigation: 900 tokens
    web["state"]["chat"] = Scripted(script=[tool_reply(call("get_findings", {}, f"b{i}"), tokens=500) for i in range(5)])
    r = investigate_msg(c2).json()["reply"]
    assert r["meta"]["investigation"]["stop_reason"] == "budget_exceeded"
    assert r["content"] == "The investigation stopped before a final answer (token budget)."
    assert len(web["state"]["chat"].calls) == 2


def test_watch_lookalikes_tool_only_for_own_domain_and_reuses_watch(web):
    c = company(web["new_client"](), "w@shop.example")
    post(c, "/api/official", {"domains": ["demoshop.example"], "numbers": []})
    web["state"]["chat"] = Scripted(script=[tool_reply(call("watch_lookalikes", {"domain": "demoshop.example"}, "w1")),
                                            final_reply({"answer": "started", "claims": []})])
    i = investigate_msg(c).json()["reply"]["meta"]["investigation"]
    assert i["trace"][0]["summary"] == "watch started for demoshop.example"
    tool_msg = [m for m in web["state"]["chat"].calls[1]["messages"] if m["role"] == "tool"][0]["content"]
    assert "cloudflare-dns.com and crt.sh" in tool_msg  # the disclosure reaches the model and the user
    runs = [r for r in web["server"].app.runs.runs.values() if r.mode == "lookalike"]
    assert len(runs) == 1


# --- 2. owner summary ---

F = [{"id": "exposed_env_file:/.env", "check": "exposed_env_file", "severity": "critical", "location": "/.env",
      "title": ".env file appears publicly accessible"},
     {"id": "missing_csp:/", "check": "missing_csp", "severity": "medium", "location": "/", "title": "CSP missing"},
     {"id": "cookie_missing_samesite:/:s", "check": "cookie_missing_samesite", "severity": "low", "location": "/",
      "title": "SameSite"},
     {"id": "server_version_disclosure:/:server", "check": "server_version_disclosure", "severity": "low",
      "location": "/", "title": "Version"}]
SCORE = {"score": 56, "grade": "D", "label": "heuristic score of the checks run"}
CHECKS = [{"check": "missing_spf", "status": "not applicable (loopback demo)"}, {"check": "missing_csp", "status": "ran"}]


def test_owner_summary_content():
    s = summary_build(F, SCORE, CHECKS, ["exposed_env_file:/.env"], {"score": 92, "grade": "A"})
    assert s["counts"]["critical"] == 0 and s["counts"]["medium"] == 1 and s["counts"]["low"] == 2
    assert [t["id"] for t in s["top"]] == ["missing_csp:/", "cookie_missing_samesite:/:s",
                                           "server_version_disclosure:/:server"]
    assert [t["risk"] for t in s["top"]] == ["medium", "low", "low"]  # from the fix templates
    assert s["fixed"] == ["exposed_env_file:/.env"] and s["not_run"] == [CHECKS[0]]
    en, hi = s["lines"]["en"], s["lines"]["hinglish"]
    assert en[0].startswith("Heuristic score of the checks run, at the time of this scan: D (56/100)")
    assert en[1] == "After the re-test: A (92/100)." and len(en) == len(hi)
    nums = lambda ls: sorted(re.findall(r"\d+", " ".join(ls)))  # noqa: E731
    assert nums(en) == nums(hi)  # both languages carry exactly the same numbers
    for line in en + hi:
        assert not re.search(r"(?i)\b(safe|secure|protected|hours?|minutes?|weeks?|costs?|price|rupees?)\b|[$₹]", line), line
    src = open(__import__("src.owner_summary", fromlist=["x"]).__file__, encoding="utf-8").read()
    assert "NEED NATIVE-SPEAKER REVIEW" in src
    none = summary_build([], None, [], [])
    assert none["lines"]["en"][1] == "No open issues were detected in the checks run."


def test_reword_check_drops_new_numbers_and_reassurance():
    s = summary_build(F, SCORE, CHECKS, [])
    ok, why = check_reword("Your grade is D with 56 of 100. Fix the CSP header first.", s, "en")
    assert ok and why is None
    assert check_reword("Fixing this takes about 7 hours.", s, "en") == (
        None, "the reply contained numbers that are not in the summary: ['7']")
    # "2" and "3" do occur in the summary (counts, list numbers), but time and cost words are never allowed
    hours = check_reword("Fixing this takes about 2 hours.", s, "en")[1]
    assert hours == "the reply used a word MAYA never uses for results: 'hours'"
    assert check_reword("It costs 3 dollars.", s, "en")[1].startswith("the reply used a word")
    assert check_reword("After this your site is safe.", s, "en")[1].startswith("the reply used a word")
    assert check_reword("", s, "en")[1] == "the model returned no text"


def test_summary_and_reword_endpoints(web):
    c = company(web["new_client"](), "sum@shop.example")
    _, run_id, body = demo(c)
    s = c.get(f"/api/runs/{run_id}/summary.json").json()
    assert s["counts"]["critical"] == 1 and len(s["top"]) == 3 and s["score"]["grade"] == "F"
    assert post(c, f"/api/runs/{run_id}/reword", {"lang": "en"}).json()["error"].startswith("Model not configured")
    web["state"]["chat"] = Scripted(script=[final_reply(" ".join(s["lines"]["en"]).replace("Open issues", "Issues")),
                                            final_reply("Fix these within 3 days.")])
    r = post(c, f"/api/runs/{run_id}/reword", {"lang": "hinglish"}).json()
    assert r["text"] and r["dropped"] is None and r["label"] == "unverified model text"
    r = post(c, f"/api/runs/{run_id}/reword", {"lang": "en"}).json()
    assert r["text"] is None and r["dropped"] == "the reply used a word MAYA never uses for results: 'days'"
    assert post(c, f"/api/runs/{run_id}/reword", {"lang": "fr"}).status_code == 400
    other = company(web["new_client"](), "other-sum@shop.example")
    assert other.get(f"/api/runs/{run_id}/summary.json").status_code == 404


# --- 3. client report ---

def test_client_report_escaping_and_wording_unit():
    evil = [{"id": "exposed_env_file:/<script>", "check": "exposed_env_file", "severity": "critical",
             "location": "/<script>alert(1)</script>", "title": "t", "evidence_line": 'API_KEY=fak...al"><img src=x>'}]
    page = client_html('"><b>label</b>', "r" * 24, evil, SCORE, True, None, None, "a" * 64, 1_800_000_000)
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert '"><img src=x>' not in page and "<b>label</b>" not in page
    assert page.count("<script") == 1 and '<script src="/static/print.js" defer></script>' in page  # no inline JS


def test_client_report_through_the_app(web):
    c = company(web["new_client"](), "client@shop.example")
    asset, r1, body = demo(c)
    assert post(c, f"/api/runs/{r1}/approve-all", {"approver": "demo-user"}).status_code == 200
    assert post(c, f"/api/runs/{r1}/apply").status_code == 200
    assert post(c, f"/api/runs/{r1}/retest").json()["retest"]["status"] == "FIXED"
    resp = c.get(f"/api/runs/{r1}/client-report.html")
    assert resp.status_code == 200 and "attachment" in resp.headers["content-disposition"]
    page = resp.text
    assert "DEMO: findings below come from MAYA" in page and "Heuristic score of the checks run:" in page
    assert "Re-test result: <strong>FIXED</strong>" in page and "Tamper-evident, not tamper-proof (unsigned)" in page
    assert "API_KEY=fak...al" in page and "fake-demo-key-not-real" not in page
    pack = c.get(f"/api/runs/{r1}/evidence.zip").content
    with zipfile.ZipFile(io.BytesIO(pack)) as z:
        manifest_sha = hashlib.sha256(z.read("manifest.json")).hexdigest()
    assert f"<code>{manifest_sha}</code>" in page  # the same run gives the same pack manifest
    prose = re.sub(r"<(pre|code)>.*?</\1>", " ", page, flags=re.S).replace("`Secure`", "")
    for word in ("secure", "safe", "hacked", "compliant"):
        assert not re.search(rf"(?i)\b{word}\b", prose), word
    r2, _ = scan(c, asset["id"])
    page2 = c.get(f"/api/runs/{r2}/client-report.html").text
    assert "12 resolved" in page2 and "No re-test has been run for this scan." in page2
    other = company(web["new_client"](), "other-client@shop.example")
    assert other.get(f"/api/runs/{r1}/client-report.html").status_code == 404


# --- 4. re-scan reminders ---

def test_due_date_logic_and_ics_format():
    assert due_at(1000.0, 7) == 1000.0 + 7 * DAY
    with pytest.raises(ValueError):
        due_at(1000.0, 5)
    text = ics("shop.example, main; site\\x " + "y" * 80, 1_800_000_000 + 14 * DAY, "abc-1", 1_800_000_000)
    lines = text.split("\r\n")
    assert text.endswith("\r\n") and "\n" not in text.replace("\r\n", "")
    assert lines[0] == "BEGIN:VCALENDAR" and lines[-2] == "END:VCALENDAR" and "BEGIN:VEVENT" in lines
    assert "DTSTART;VALUE=DATE:20270129" in lines and "UID:abc-1@maya.local" in lines
    unfolded = text.replace("\r\n ", "")
    assert "SUMMARY:Re-scan shop.example\\, main\\; site\\\\x " + "y" * 80 + " with MAYA" in unfolded
    assert all(len(l.encode()) <= 75 for l in lines)
    assert "http" not in text and "@" not in text.replace("@maya.local", "")


def test_reminders_through_the_app(web):
    c = company(web["new_client"](), "rem@shop.example")
    asset = post(c, "/api/assets", {"demo": True}).json()["asset"]
    assert c.get(f"/api/assets/{asset['id']}/reminder.ics").status_code == 404  # none set yet
    for bad in (5, "7", True, -7):
        assert post(c, f"/api/assets/{asset['id']}/reminder", {"days": bad}).status_code == 400
    r = post(c, f"/api/assets/{asset['id']}/reminder", {"days": 14}).json()["asset"]["reminder"]
    assert r["due"] == web["clock"].t + 14 * DAY and r["overdue"] is False and r["days"] == 14
    listed = c.get("/api/assets").json()["assets"][0]["reminder"]
    assert listed["overdue"] is False
    web["clock"].t += 14 * DAY  # time passes; nothing runs by itself (the session expires too: sign in again)
    assert post(c, "/api/signin", {"email": "rem@shop.example", "password": PASSWORD}).status_code == 200
    assert c.get("/api/assets").json()["assets"][0]["reminder"]["overdue"] is True
    ics_resp = c.get(f"/api/assets/{asset['id']}/reminder.ics")
    assert ics_resp.status_code == 200 and ics_resp.headers["content-type"].startswith("text/calendar")
    assert "SUMMARY:Re-scan MAYA demo app with MAYA" in ics_resp.text
    other = company(web["new_client"](), "rem-other@shop.example")
    assert other.get(f"/api/assets/{asset['id']}/reminder.ics").status_code == 404
    assert post(other, f"/api/assets/{asset['id']}/reminder", {"days": 7}).status_code == 404
    assert post(c, f"/api/assets/{asset['id']}/reminder", {"days": 0}).json()["asset"].get("reminder") is None
    post(c, f"/api/assets/{asset['id']}/reminder", {"days": 30})
    assert post(c, f"/api/assets/{asset['id']}/disconnect").status_code == 200
    assert web["server"].app.assets.reminders(1) == {}  # disconnect removes the reminder too
