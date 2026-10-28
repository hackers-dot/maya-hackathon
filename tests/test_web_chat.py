"""Web 4: chat (scripted fake model only, no network except the loopback demo app), approve-all, sorted proposals."""
import json
import threading
import time

import httpx
import pytest

from src.provider import ChatResult, MockProvider
from src.web.chat import HISTORY_CAP, MAX_CHARS, MAX_TOKENS, RATE_PER_MINUTE, SYSTEM_PROMPT, label_reply
from src.web.server import make_server

PASSWORD = "correct-horse-battery"  # test-only


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


class RecordingModel(MockProvider):
    """Scripted fake model: records every request; replies with `answer` and `usage`."""

    def __init__(self, answer="Here is a short answer.", total=50):
        super().__init__(answer=answer)
        self.total = total

    def chat(self, messages, **kw):
        super().chat(messages, **kw)  # records messages + kwargs in self.calls
        completion = min(10, self.total)
        usage = {"prompt_tokens": self.total - completion, "completion_tokens": completion,
                 "total_tokens": self.total, "completion_tokens_details": {"reasoning_tokens": min(6, completion)}}
        return ChatResult(self.answer, True, usage)


@pytest.fixture
def web(tmp_path):
    state = {"chat": RecordingModel(), "cap": None}
    clock = Clock()
    server = make_server(port=0, data_dir=tmp_path, clock=clock,
                         provider_factory=lambda: (None, "none"),  # scans: deterministic only
                         chat_provider_factory=lambda: (state["chat"], "fake-chat-model" if state["chat"] else None),
                         chat_token_cap=500)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    clients = []

    def new_client():
        c = httpx.Client(base_url=base, trust_env=False, follow_redirects=False, timeout=30)
        clients.append(c)
        return c

    yield {"server": server, "new_client": new_client, "state": state, "clock": clock, "tmp": tmp_path}
    for c in clients:
        c.close()
    server.shutdown()
    server.server_close()


def post(c, path, body=None):
    csrf = c.get("/api/session").json()["csrf"]
    return c.post(path, json=body if body is not None else {}, headers={"X-CSRF-Token": csrf})


def user(c, email, company=False):
    assert post(c, "/api/signup", {"email": email, "password": PASSWORD}).status_code == 200
    if company:
        for path, body in [("/api/profile", {"account_type": "company"}), ("/api/asset", {"asset": "website"}),
                           ("/api/connect", {"connect": True}), ("/api/consent", {"allow": True})]:
            assert post(c, path, body).status_code == 200
    else:
        post(c, "/api/profile", {"account_type": "individual"})
    return c


def new_chat(c):
    r = post(c, "/api/chats")
    assert r.status_code == 200, r.text
    return r.json()["chat"]["id"]


def send(c, chat_id, text):
    return post(c, f"/api/chats/{chat_id}/messages", {"content": text})


def scan_demo(c):
    asset = post(c, "/api/assets", {"demo": True}).json()["asset"]
    run_id = post(c, f"/api/assets/{asset['id']}/scan").json()["run"]["id"]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        body = c.get(f"/api/runs/{run_id}").json()
        if body["run"]["status"] != "running":
            return run_id, body
        time.sleep(0.1)
    raise AssertionError("scan did not finish")


# --- basic chat ---

def test_chat_roundtrip_and_reply_metadata(web):
    c = user(web["new_client"](), "ind@example.org")
    status = c.get("/api/chats").json()
    assert status["model_configured"] is True and status["model"] == "fake-chat-model" and status["chats"] == []
    chat_id = new_chat(c)
    r = send(c, chat_id, "What is HTTPS?")
    assert r.status_code == 200
    reply = r.json()["reply"]
    assert reply["role"] == "assistant" and reply["content"] == "Here is a short answer."
    assert reply["meta"]["model"] == "fake-chat-model" and reply["meta"]["label"] == "unverified model text"
    assert reply["meta"]["tokens"] == {"prompt": 40, "completion": 10, "reasoning": 6, "total": 50}
    chat = c.get(f"/api/chats/{chat_id}").json()["chat"]
    assert [m["role"] for m in chat["messages"]] == ["user", "assistant"] and chat["title"] == "What is HTTPS?"
    assert [x["id"] for x in c.get("/api/chats").json()["chats"]] == [chat_id]
    call = web["state"]["chat"].calls[-1]
    assert call["max_tokens"] == MAX_TOKENS == 1200 and call["reasoning_effort"] == "low"


def test_system_prompt_has_the_safety_rules(web):
    c = user(web["new_client"](), "rules@example.org")
    send(c, new_chat(c), "Hello")
    system = web["state"]["chat"].calls[-1]["messages"][0]
    assert system["role"] == "system" and system["content"].startswith(SYSTEM_PROMPT)
    for phrase in ("cybersecurity assistant", "simple, friendly language", "Hindi/Hinglish",
                   "Never provide exploit code", "attack instructions against systems the user does not own",
                   "Never help scan, probe or test sites the user does not own", "refuse politely",
                   "Do not make claims about the user's own systems unless they come from MAYA's verified findings",
                   "cite finding ids"):
        assert phrase in system["content"], phrase


def test_user_secrets_are_masked_before_storing_and_sending(web):
    c = user(web["new_client"](), "secret@example.org")
    chat_id = new_chat(c)
    key = "sk-" + "live" + "FAKE1234567890abcdXY"  # fake, built so no secret-shaped literal sits in the repo
    send(c, chat_id, f"my key is {key} is that ok?")
    sent = json.dumps(web["state"]["chat"].calls[-1]["messages"])
    stored = json.dumps(c.get(f"/api/chats/{chat_id}").json())
    assert key not in sent and key not in stored and "sk-...XY" in stored


# --- limits ---

def test_length_cap(web):
    c = user(web["new_client"](), "len@example.org")
    chat_id = new_chat(c)
    assert send(c, chat_id, "x" * (MAX_CHARS + 1)).status_code == 400
    assert send(c, chat_id, "  ").status_code == 400
    assert send(c, chat_id, "y" * MAX_CHARS).status_code == 200


def test_rate_limit_20_per_minute(web):
    web["state"]["chat"] = RecordingModel(total=1)
    c = user(web["new_client"](), "rate@example.org")
    chat_id = new_chat(c)
    codes = [send(c, chat_id, f"q{i}").status_code for i in range(RATE_PER_MINUTE)]
    assert codes == [200] * RATE_PER_MINUTE
    r = send(c, chat_id, "one too many")
    assert (r.status_code, r.json()["error"]) == (429, "too many messages; wait a minute and try again")
    web["clock"].t += 61
    assert send(c, chat_id, "after a minute").status_code == 200


def test_history_sent_to_model_is_capped(web):
    web["state"]["chat"] = RecordingModel(total=1)
    c = user(web["new_client"](), "hist@example.org")
    chat_id = new_chat(c)
    for i in range(8):
        send(c, chat_id, f"message {i}")
    msgs = web["state"]["chat"].calls[-1]["messages"]
    turns = [m for m in msgs if m["role"] != "system"]
    assert len(turns) == HISTORY_CAP == 10
    assert turns[-1] == {"role": "user", "content": "message 7"}  # the newest message is always included
    assert "message 0" not in json.dumps(turns)  # older ones dropped
    assert len(c.get(f"/api/chats/{chat_id}").json()["chat"]["messages"]) == 16  # the full chat stays stored


def test_chat_token_budget_cap(web):
    web["state"]["chat"] = RecordingModel(total=300)  # cap in this fixture is 500 tokens per user per 24 h
    c = user(web["new_client"](), "budget@example.org")
    chat_id = new_chat(c)
    assert send(c, chat_id, "one").status_code == 200
    r = send(c, chat_id, "two")
    assert r.status_code == 200 and r.json()["budget"] == {"used": 600, "cap": 500}  # this reply crossed the cap
    blocked = send(c, chat_id, "three")
    assert (blocked.status_code, blocked.json()["error"]) == (429, "chat token budget reached for today")
    other = user(web["new_client"](), "other-budget@example.org")
    assert send(other, new_chat(other), "fine").status_code == 200  # the budget is per user
    lines = (web["tmp"] / "usage.jsonl").read_text(encoding="utf-8").splitlines()
    assert sum(json.loads(l)["run_id"].startswith("chat-") for l in lines) == 3  # counted through TokenBudget
    web["clock"].t += 86_401  # past the rolling 24 hours (and past the 8 h session, so sign in again)
    assert post(c, "/api/signin", {"email": "budget@example.org", "password": PASSWORD}).status_code == 200
    assert send(c, chat_id, "next day").status_code == 200


def test_no_model_key(web):
    web["state"]["chat"] = None
    c = user(web["new_client"](), "nokey@example.org")
    status = c.get("/api/chats").json()
    assert status["model_configured"] is False and status["model"] is None
    chat_id = new_chat(c)
    r = send(c, chat_id, "hello?")
    assert r.status_code == 409 and r.json()["error"].startswith("model not configured")
    assert c.get(f"/api/chats/{chat_id}").json()["chat"]["messages"] == []  # nothing stored, nothing faked


# --- authorization and CSRF ---

def test_other_users_chats_are_not_found(web):
    a = user(web["new_client"](), "alice@example.org")
    b = user(web["new_client"](), "bob@example.org")
    chat_id = new_chat(a)
    send(a, chat_id, "private question")
    assert b.get(f"/api/chats/{chat_id}").status_code == 404
    assert send(b, chat_id, "let me in").status_code == 404
    assert b.get("/api/chats").json()["chats"] == []
    anon = web["new_client"]()
    assert anon.get("/api/chats").status_code == 401 and anon.get(f"/api/chats/{chat_id}").status_code == 401
    assert a.get("/api/chats/" + "f" * 16).status_code == 404


def test_chat_needs_csrf(web):
    c = user(web["new_client"](), "csrf@example.org")
    chat_id = new_chat(c)
    for path in ("/api/chats", f"/api/chats/{chat_id}/messages"):
        assert c.post(path, json={"content": "hi"}, headers={"X-CSRF-Token": "forged"}).status_code == 403
    assert len(web["state"]["chat"].calls) == 0  # nothing reached the model


# --- company context: only the user's own verified findings ---

def test_context_has_only_this_users_findings(web):
    a = user(web["new_client"](), "owner@shop.example", company=True)
    b = user(web["new_client"](), "neighbour@shop.example", company=True)
    _, body = scan_demo(a)
    a_ids = {f["id"] for f in body["result"]["findings"]}
    send(a, new_chat(a), "What should I fix first on my site?")
    a_system = web["state"]["chat"].calls[-1]["messages"][0]["content"]
    assert "VERIFIED FINDINGS (the only facts you may state about the user's systems):" in a_system
    for fid in a_ids:
        assert f"[{fid}]" in a_system
    assert "evidence: API_KEY=fak...al" in a_system and "fake-demo-key-not-real" not in a_system

    send(b, new_chat(b), "What about my site?")
    b_system = web["state"]["chat"].calls[-1]["messages"][0]["content"]
    b_context = b_system[len(SYSTEM_PROMPT):]  # the fixed prompt's own example "[missing_csp:/]" is not data
    assert b_context.strip() == ("VERIFIED FINDINGS: none. MAYA has not scanned any site this user owns, "
                                 "so make no claims about it.")
    assert not any(fid in b_context for fid in a_ids)  # Alice's findings never reach Bob's prompt


def test_reply_labels_against_the_users_findings(web):
    c = user(web["new_client"](), "label@shop.example", company=True)
    scan_demo(c)
    chat_id = new_chat(c)
    web["state"]["chat"] = RecordingModel(answer="Your /.env file is public [exposed_env_file:/.env].")
    reply = send(c, chat_id, "status?").json()["reply"]["meta"]
    assert reply["label"] == "restates verified findings" and reply["cited"] == ["exposed_env_file:/.env"]
    web["state"]["chat"] = RecordingModel(answer="Your login page is broken [made_up_check:/login].")
    reply = send(c, chat_id, "and?").json()["reply"]["meta"]
    assert reply["label"] == "unverified model text" and reply["cited_unknown"] == ["made_up_check:/login"]


@pytest.mark.parametrize("text,label", [
    ("The file is exposed [a:/x]. Block it [a:/x].", "restates verified findings"),
    ("Your /.env file is public [a:/x].", "restates verified findings"),  # a dot inside a path is not a sentence end
    ("Line one cites [a:/x]\nline two cites [a:/x] too", "restates verified findings"),
    ("The file is exposed [a:/x]. Also rotate your keys.", "unverified model text"),
    ("No findings cited here.", "unverified model text"),
    ("", "unverified model text"),
])
def test_label_reply_unit(text, label):
    assert label_reply(text, {"a:/x"})["label"] == label


# --- Web 4 fix flow: sorted proposals and approve-all ---

def test_proposals_sorted_by_severity_then_check(web):
    c = user(web["new_client"](), "sort@shop.example", company=True)
    _, body = scan_demo(c)
    sev = {f["check"]: f["severity"] for f in body["result"]["findings"]}
    order = ["critical", "high", "medium", "low", "info"]
    keys = [(order.index(sev[p["check_id"]]), p["check_id"]) for p in body["proposals"]]
    assert keys == sorted(keys) and body["proposals"][0]["check_id"] == "exposed_env_file"


def test_approve_all_audits_each_approval(web):
    c = user(web["new_client"](), "all@shop.example", company=True)
    run_id, body = scan_demo(c)
    assert post(c, f"/api/runs/{run_id}/approve-all", {"approver": "  "}).status_code == 400
    assert {p["status"] for p in c.get(f"/api/runs/{run_id}").json()["proposals"]} == {"PROPOSED"}
    r = post(c, f"/api/runs/{run_id}/approve-all", {"approver": "demo-user"})
    assert r.status_code == 200 and {p["status"] for p in r.json()["proposals"]} == {"APPROVED"}
    audit = c.get("/api/audit").json()
    approved = [e for e in audit["entries"] if e["event"] == "approved"]
    assert audit["intact"] and audit["count"] == 1 + len(body["proposals"])  # 1 refused + one entry per proposal
    assert len(approved) == min(len(body["proposals"]), 12)  # the panel shows the latest 12 entries
    assert post(c, f"/api/runs/{run_id}/approve-all", {"approver": "demo-user"}).status_code == 409
    other = user(web["new_client"](), "x@shop.example", company=True)
    assert post(other, f"/api/runs/{run_id}/approve-all", {"approver": "mallory"}).status_code == 404


# --- Web 5: the chat knows each finding's fix status ---

def statuses_in_context(web):
    system = web["state"]["chat"].calls[-1]["messages"][0]["content"]
    context = system[len(SYSTEM_PROMPT):]
    return {line.split("]")[0][3:]: line.split("status ")[1].split(":")[0]
            for line in context.splitlines() if line.startswith("- [")}


def test_system_prompt_forbids_calling_fixed_findings_present():
    assert "never describe a FIXED finding as still present" in SYSTEM_PROMPT
    assert "fixed and re-tested" in SYSTEM_PROMPT and "cite its id" in SYSTEM_PROMPT


def test_context_without_retest_lists_detected_only(web):
    c = user(web["new_client"](), "detected@shop.example", company=True)
    _, body = scan_demo(c)
    send(c, new_chat(c), "status?")
    got = statuses_in_context(web)
    assert set(got) == {f["id"] for f in body["result"]["findings"]} and set(got.values()) == {"DETECTED"}


def test_context_after_apply_and_fixed_retest(web):
    c = user(web["new_client"](), "fixed@shop.example", company=True)
    b = user(web["new_client"](), "other@shop.example", company=True)
    _, b_body = scan_demo(b)  # Bob scans but never fixes anything
    run_id, body = scan_demo(c)
    ids = {f["id"] for f in body["result"]["findings"]}
    assert post(c, f"/api/runs/{run_id}/approve-all", {"approver": "demo-user"}).status_code == 200
    assert post(c, f"/api/runs/{run_id}/apply").status_code == 200
    chat_id = new_chat(c)
    send(c, chat_id, "status?")
    assert set(statuses_in_context(web).values()) == {"APPLIED"}  # applied, not yet re-tested
    assert post(c, f"/api/runs/{run_id}/retest").json()["retest"]["status"] == "FIXED"
    send(c, chat_id, "and now?")
    got = statuses_in_context(web)
    assert set(got) == ids and set(got.values()) == {"FIXED"}

    send(b, new_chat(b), "status?")  # Alice's statuses never reach Bob's prompt: his stay DETECTED
    got_b = statuses_in_context(web)
    assert set(got_b) == {f["id"] for f in b_body["result"]["findings"]} and set(got_b.values()) == {"DETECTED"}


# --- Web 5.1: scan history in the chat context ---

def context_of(web):
    return web["state"]["chat"].calls[-1]["messages"][0]["content"][len(SYSTEM_PROMPT):]


def test_system_prompt_never_says_no_scan_when_history_exists():
    assert "never say that no scan was done" in SYSTEM_PROMPT
    assert "no issues detected in the checks run" in SYSTEM_PROMPT


def test_latest_scan_with_no_issues_keeps_history_and_fixed_ids(web):
    c = user(web["new_client"](), "history@shop.example", company=True)
    run1, body = scan_demo(c)
    ids = sorted(f["id"] for f in body["result"]["findings"])
    assert post(c, f"/api/runs/{run1}/approve-all", {"approver": "demo-user"}).status_code == 200
    assert post(c, f"/api/runs/{run1}/apply").status_code == 200
    assert post(c, f"/api/runs/{run1}/retest").json()["retest"]["status"] == "FIXED"
    run2, body2 = scan_demo(c)  # same demo app, fixes on: 0 findings
    assert body2["result"]["findings"] == []
    chat_id = new_chat(c)
    send(c, chat_id, "Is my site ok now?")
    ctx = context_of(web)
    assert "SCAN HISTORY (newest first; MAYA has scanned this user's site):" in ctx
    lines = ctx.strip().splitlines()
    assert lines[1].startswith(f"- run {run2} ")
    assert "scan found 0 findings; later re-test: no re-test (statuses: no findings)" in lines[1]
    assert lines[2] == (f"  changes since the previous scan of this asset (run {run1}): 0 new, 12 resolved, "
                        "0 unchanged, 0 severity change(s)")  # Web 9: one diff line per run pair
    assert lines[3].startswith(f"- run {run1} ")
    assert "scan found 12 findings; later re-test: all fixed (statuses: FIXED 12)" in lines[3]
    assert f"latest scan: no issues detected in the checks run (run {run2}" in ctx
    assert all(f"[{i}] (run {run1})" in ctx for i in ids)
    assert "MAYA has not scanned" not in ctx and "VERIFIED FINDINGS: none open in the latest scan" in ctx
    # citing an earlier FIXED id is a known id, not an invented one
    web["state"]["chat"] = RecordingModel(answer="The exposed .env was fixed and re-tested [exposed_env_file:/.env].")
    meta = send(c, chat_id, "and .env?").json()["reply"]["meta"]
    assert meta["cited"] == ["exposed_env_file:/.env"] and meta["cited_unknown"] == []


def test_history_is_capped_at_three_runs_and_per_user(web):
    c = user(web["new_client"](), "many@shop.example", company=True)
    b = user(web["new_client"](), "nosy@shop.example", company=True)
    runs = [scan_demo(c)[0] for _ in range(4)]
    send(c, new_chat(c), "history?")
    run_lines = [l for l in context_of(web).splitlines() if l.startswith("- run ")]
    assert [l.split()[2] for l in run_lines] == runs[::-1][:3]  # newest first, last 3 only
    send(b, new_chat(b), "history?")
    b_ctx = context_of(web)
    assert b_ctx.strip().startswith("VERIFIED FINDINGS: none. MAYA has not scanned")
    assert not any(r in b_ctx for r in runs)


def test_demo_prepare_prints_the_click_order(capsys):
    from scripts import demo_prepare
    assert demo_prepare.main(["--reset"]) == 0
    out = capsys.readouterr().out
    order = ['"Reset demo app"', '"Run security scan"', '"Approve all"', '"Apply approved fixes"', '"Re-test"',
             '"Download evidence pack"', "New chat"]
    positions = [out.index(o) for o in order]
    assert positions == sorted(positions)
    demo_prepare.main([])
    assert '"Reset demo app"' not in capsys.readouterr().out


# --- Web 6: explicit scan result + later re-test outcome per run ---

def test_system_prompt_separates_scan_result_from_retest():
    assert "Never say a scan found nothing if the history says it found N > 0 findings" in SYSTEM_PROMPT
    assert "re-test outcome separately" in SYSTEM_PROMPT


def run_line(web, run_id):
    return next(l for l in context_of(web).splitlines() if l.startswith(f"- run {run_id} "))


def test_history_line_no_retest_then_still_open_then_all_fixed(web):
    c = user(web["new_client"](), "retestline@shop.example", company=True)
    run_id, body = scan_demo(c)
    n = len(body["result"]["findings"])
    chat_id = new_chat(c)
    send(c, chat_id, "status?")
    assert f"scan found {n} findings; later re-test: no re-test" in run_line(web, run_id)
    # approve everything except the .env fix, apply, re-test: one finding stays open
    for p in body["proposals"]:
        if p["check_id"] != "exposed_env_file":
            assert post(c, f"/api/runs/{run_id}/proposals/{p['id']}/approve", {"approver": "demo-user"}).status_code == 200
    assert post(c, f"/api/runs/{run_id}/apply").status_code == 200
    assert post(c, f"/api/runs/{run_id}/retest").json()["retest"]["still_present"] == ["exposed_env_file:/.env"]
    send(c, chat_id, "and now?")
    assert f"scan found {n} findings; later re-test: 1 still open" in run_line(web, run_id)
    assert post(c, f"/api/runs/{run_id}/approve-all", {"approver": "demo-user"}).status_code == 200
    assert post(c, f"/api/runs/{run_id}/apply").status_code == 200
    assert post(c, f"/api/runs/{run_id}/retest").json()["retest"]["status"] == "FIXED"
    send(c, chat_id, "and now?")
    line = run_line(web, run_id)
    assert f"scan found {n} findings; later re-test: all fixed" in line and n > 0
    assert "latest scan: no issues detected" not in context_of(web)  # the scan found n > 0: never "nothing"


def test_dns_finding_ids_count_as_citations():
    known = {"missing_spf:TXT shop.example"}
    meta = label_reply("Add an SPF record [missing_spf:TXT shop.example].", known)
    assert meta["cited"] == ["missing_spf:TXT shop.example"] and meta["label"] == "restates verified findings"
