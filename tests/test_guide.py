"""Guide pet: the deterministic state machine (src/guide.py), GET /api/guide, the chat level, and guide.js."""
import json
import re
import shutil
import subprocess
import threading
import time

import httpx
import pytest

from src.guide import LANGS, LEVELS, STATES, T, guide, states, step
from src.owner_summary import FORBIDDEN
from src.provider import ChatResult, MockProvider
from src.web.chat import LEVEL_PROMPTS
from src.web.server import STATIC_DIR, make_server

PASSWORD = "correct-horse-battery"  # test-only
TOP = {"check": "exposed_env_file", "title": "Configuration file appears publicly readable", "location": "/.env"}
COUNTS = {"critical": 1, "high": 1, "medium": 2, "low": 0, "info": 0}
SITE = {"account_type": "company", "asset_choice": "website", "connect": True, "consented": True,
        "asset": {"id": "a" * 16, "kind": "demo", "status": "loopback"}}


def run(**kw):
    base = {"status": "finished", "can_apply": True, "counts": COUNTS, "top": TOP, "proposals": [], "retest": None,
            "score_improved": False}
    return {**base, **kw}


# facts that lead to each state (also checks the state machine reaches every state)
FACTS = {
    "no_account_type": {"account_type": None},
    "no_asset_type": {"account_type": "company", "asset_choice": None},
    "no_connect": {"account_type": "company", "asset_choice": "website", "connect": None},
    "no_consent": {"account_type": "company", "asset_choice": "website", "connect": True, "consented": False},
    "no_asset": {**SITE, "asset": None},
    "verification_pending": {**SITE, "asset": {"id": "b" * 16, "kind": "website", "status": "pending"}},
    "verification_failed": {**SITE, "asset": {"id": "b" * 16, "kind": "website", "status": "failed"}},
    "ready_to_scan": {**SITE, "run": None},
    "scan_running": {**SITE, "run": run(status="running")},
    "scan_failed": {**SITE, "run": run(status="failed")},
    "scan_finished": {**SITE, "run": run()},
    "proposals_not_approved": {**SITE, "run": run(proposals=["PROPOSED", "PROPOSED"])},
    "approved_not_applied": {**SITE, "run": run(proposals=["APPROVED", "PROPOSED"])},
    "applied_not_retested": {**SITE, "run": run(proposals=["APPLIED"])},
    "retest_fixed": {**SITE, "run": run(proposals=["APPLIED"], counts={s: 0 for s in COUNTS},
                                        retest={"status": "FIXED", "resolved": ["x"], "still_present": []})},
    "retest_still_present": {**SITE, "run": run(proposals=["APPLIED", "PROPOSED"],
                                                retest={"status": "STILL_PRESENT", "resolved": ["x"],
                                                        "still_present": ["y"]})},
    "nothing_to_do": {**SITE, "run": run(counts={s: 0 for s in COUNTS})},
}
TIME_OR_COST_NUMBER = re.compile(r"(?i)\d+\s*(?:s|sec|seconds?|min|minutes?|hours?|hrs?|days?|weeks?|months?|"
                                 r"years?|%|usd|inr|rs\.?)\b|[$₹€]\s*\d")


def test_every_state_is_reachable_from_stored_facts():
    assert set(FACTS) == set(STATES)
    for state, facts in FACTS.items():
        assert state in states(facts), (state, states(facts))


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("lang", LANGS)
def test_every_line_exists_and_never_reassures_or_estimates(state, level, lang):
    s = step(state, level, lang, FACTS[state])
    assert s["line"].strip() and s["targets"] and s["mood"] in ("idle", "thinking", "happy", "concerned", "pointing")
    for text in (s["line"], s["why"] or ""):
        assert not FORBIDDEN.search(text), (state, level, lang, FORBIDDEN.search(text).group(0))
        assert not TIME_OR_COST_NUMBER.search(text), (state, text)
        assert "{" not in text  # every placeholder was filled
    assert s == step(state, level, lang, FACTS[state])  # deterministic


def test_levels_change_the_wording_and_the_role():
    a, b = step("ready_to_scan", "new", "en", FACTS["ready_to_scan"]), step("ready_to_scan", "technical", "en",
                                                                             FACTS["ready_to_scan"])
    assert a["line"] != b["line"] and (a["role"], b["role"]) == ("owner", "technical")
    assert step("ready_to_scan", "some", "en", FACTS["ready_to_scan"])["role"] == "developer"
    # findings: beginners get the analogy from the fixed report template, technical readers the technical text
    from src.report import TEXT
    new = step("scan_finished", "new", "en", FACTS["scan_finished"])["why"]
    tech = step("scan_finished", "technical", "en", FACTS["scan_finished"])["why"]
    assert TEXT["exposed_env_file"].analogy in new and TEXT["exposed_env_file"].technical in tech


def test_hinglish_differs_and_is_marked_for_review():
    for state in STATES:
        en = step(state, "new", "en", FACTS[state])["line"]
        hi = step(state, "new", "hinglish", FACTS[state])["line"]
        assert en != hi, state
    assert "NEED NATIVE-SPEAKER REVIEW" in (STATIC_DIR.parent.parent / "guide.py").read_text(encoding="utf-8")
    assert "NEED NATIVE-SPEAKER REVIEW" in (STATIC_DIR / "pet-lines.js").read_text(encoding="utf-8")


def test_explains_before_asking_and_never_approves_for_the_user():
    order = states(FACTS["proposals_not_approved"])
    assert order == ["scan_finished", "proposals_not_approved"]  # what was found comes before the approval ask
    line = step("proposals_not_approved", "new", "en", FACTS["proposals_not_approved"])["line"]
    assert "approving only records your decision" in line
    js = (STATIC_DIR / "guide.js").read_text(encoding="utf-8")
    for action in ("/approve", "/apply", "/retest", "/scan", "/reset-demo", "/disconnect", "/api/data"):
        assert action not in js, action  # the guide points; only the user presses
    assert set(re.findall(r'fetch\("([^"]+)"', js)) <= {"/api/guide", "/api/chats", "/api/session",
                                                       "/__preview/sample.json"}
    assert set(re.findall(r'postJSON\("([^"]+)"', js)) <= {"/api/chats", "/api/chats/"}


def test_moods_follow_the_data():
    assert step("scan_finished", "new", "en", FACTS["scan_finished"])["mood"] == "concerned"  # critical/high open
    calm = {**SITE, "run": run(counts={"critical": 0, "high": 0, "medium": 1, "low": 0, "info": 0})}
    assert step("scan_finished", "new", "en", calm)["mood"] == "idle"
    assert step("retest_fixed", "new", "en", FACTS["retest_fixed"])["mood"] == "happy"
    still = FACTS["retest_still_present"]
    assert step("retest_still_present", "new", "en", still)["mood"] == "concerned"
    better = {**SITE, "run": {**still["run"], "score_improved": True}}
    assert step("retest_still_present", "new", "en", better)["mood"] == "happy"
    assert step("scan_running", "new", "en", FACTS["scan_running"])["mood"] == "thinking"


def test_pointing_targets_exist_in_the_pages():
    ids = set()
    for p in STATIC_DIR.glob("*.html"):
        ids |= set(re.findall(r'\bid="([a-z0-9-]+)"', p.read_text(encoding="utf-8")))
    ids |= set(re.findall(r'\.id = "([a-z0-9-]+)"', (STATIC_DIR / "guide.js").read_text(encoding="utf-8")))
    for state, t in T.items():
        for target in t["targets"]:
            assert target in ids, (state, target)
    assert "retest-btn" in ids and "pet-call" in ids and "score-card" in ids


def test_bad_inputs_are_refused():
    with pytest.raises(ValueError):
        step("nope", "new", "en", {})
    with pytest.raises(ValueError):
        step("ready_to_scan", "expert", "en", {})
    with pytest.raises(ValueError):
        step("ready_to_scan", "new", "fr", {})


# --- the API and the chat level ---

class RecordingModel(MockProvider):
    def chat(self, messages, **kw):
        super().chat(messages, **kw)
        return ChatResult(self.answer, True, {"prompt_tokens": 40, "completion_tokens": 10, "total_tokens": 50})


@pytest.fixture
def web(tmp_path):
    state = {"chat": RecordingModel(answer="A header tells the browser what it may load.")}
    server = make_server(port=0, data_dir=tmp_path, provider_factory=lambda: (None, "none"),
                         chat_provider_factory=lambda: (state["chat"], "fake-chat-model" if state["chat"] else None))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    clients = []

    def new_client():
        c = httpx.Client(base_url=base, trust_env=False, follow_redirects=False, timeout=30)
        clients.append(c)
        return c

    yield {"new_client": new_client, "state": state}
    for c in clients:
        c.close()
    server.shutdown()
    server.server_close()


def post(c, path, body=None):
    csrf = c.get("/api/session").json()["csrf"]
    return c.post(path, json=body or {}, headers={"X-CSRF-Token": csrf})


def signed_up(c, email):
    assert post(c, "/api/signup", {"email": email, "password": PASSWORD}).status_code == 200
    return c


def first_state(c, query=""):
    r = c.get("/api/guide" + query)
    assert r.status_code == 200, r.text
    return r.json()["steps"][0]


def test_guide_api_walks_the_real_flow(web):
    c = signed_up(web["new_client"](), "walk@shop.example")
    assert first_state(c)["state"] == "no_account_type"
    post(c, "/api/profile", {"account_type": "company"})
    assert first_state(c)["state"] == "no_asset_type"
    post(c, "/api/asset", {"asset": "website"})
    assert first_state(c)["state"] == "no_connect"
    post(c, "/api/connect", {"connect": True})
    assert first_state(c)["state"] == "no_consent"
    post(c, "/api/consent", {"allow": True})
    assert first_state(c)["state"] == "no_asset"
    site = post(c, "/api/assets", {"url": "https://shop.example"}).json()["asset"]
    assert first_state(c)["state"] == "verification_pending"
    demo = post(c, "/api/assets", {"demo": True}).json()["asset"]
    assert first_state(c, f"?asset={demo['id']}")["state"] == "ready_to_scan"
    assert first_state(c, f"?asset={site['id']}")["state"] == "verification_pending"
    run_id = post(c, f"/api/assets/{demo['id']}/scan").json()["run"]["id"]
    deadline = time.monotonic() + 30
    while c.get(f"/api/runs/{run_id}").json()["run"]["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.1)
    steps = c.get(f"/api/guide?asset={demo['id']}&level=technical&lang=hinglish").json()["steps"]
    assert [s["state"] for s in steps] == ["scan_finished", "proposals_not_approved"]
    assert steps[0]["mood"] == "concerned" and steps[0]["level"] == "technical" and steps[0]["lang"] == "hinglish"
    # the guide only reads: proposals are still PROPOSED and the audit log is empty after these calls
    body = c.get(f"/api/runs/{run_id}").json()
    assert {p["status"] for p in body["proposals"]} == {"PROPOSED"}
    assert c.get("/api/audit").json()["count"] == 0


def test_guide_api_needs_sign_in_valid_params_and_is_per_user(web):
    anon = web["new_client"]()
    assert anon.get("/api/guide").status_code == 401
    a = signed_up(web["new_client"](), "a@shop.example")
    for path, body in [("/api/profile", {"account_type": "company"}), ("/api/asset", {"asset": "website"}),
                       ("/api/connect", {"connect": True}), ("/api/consent", {"allow": True})]:
        post(a, path, body)
    a_demo = post(a, "/api/assets", {"demo": True}).json()["asset"]
    assert a.get("/api/guide?level=expert").status_code == 400
    assert a.get("/api/guide?lang=fr").status_code == 400
    assert a.get("/api/guide?asset=../x").status_code == 400
    b = signed_up(web["new_client"](), "b@shop.example")
    for path, body in [("/api/profile", {"account_type": "company"}), ("/api/asset", {"asset": "website"}),
                       ("/api/connect", {"connect": True}), ("/api/consent", {"allow": True})]:
        post(b, path, body)
    # B asks about A's asset: treated as "no asset chosen", so B sees only B's own state
    assert first_state(b, f"?asset={a_demo['id']}")["state"] == "no_asset"


def test_ask_the_pet_sends_the_level_to_the_existing_chat(web):
    c = signed_up(web["new_client"](), "ask@shop.example")
    post(c, "/api/profile", {"account_type": "individual"})
    chat_id = post(c, "/api/chats", {"title": "Guide questions"}).json()["chat"]["id"]
    r = post(c, f"/api/chats/{chat_id}/messages", {"content": "What is a CSP header?", "level": "new"})
    assert r.status_code == 200, r.text
    system = web["state"]["chat"].calls[-1]["messages"][0]["content"]
    assert LEVEL_PROMPTS["new"] in system and "Never provide exploit code" in system
    assert r.json()["reply"]["meta"]["label"] == "unverified model text"
    plain = post(c, f"/api/chats/{chat_id}/messages", {"content": "And HSTS?"})
    assert plain.status_code == 200
    assert "Reader:" not in web["state"]["chat"].calls[-1]["messages"][0]["content"]  # no level, no change
    assert post(c, f"/api/chats/{chat_id}/messages", {"content": "x", "level": "expert"}).status_code == 400
    web["state"]["chat"] = None  # no model key
    r = post(c, f"/api/chats/{chat_id}/messages", {"content": "hello", "level": "new"})
    assert r.status_code == 409 and r.json()["error"].startswith("model not configured")


# --- guide.js: syntax and the DOM-free helpers, run in Node when it is installed ---

NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_guide_js_syntax_and_pure_helpers():
    js = STATIC_DIR / "guide.js"
    assert subprocess.run([NODE, "--check", str(js)], capture_output=True).returncode == 0
    script = """
const core = require(process.argv[1]);
const out = {
  speak: core.speakable("Start with [exposed_env_file:/.env]: block `/.env` at https-free /admin/path. Then more text here."),
  masked: core.speakable("The key sk_live_************ was found in config. It is masked."),
  long: core.speakable("word ".repeat(100)).length,
  voiceEn: (core.pickVoice([{lang: "hi-IN"}, {lang: "en-US"}, {lang: "en-IN"}], "en").voice || {}).lang,
  voiceHi: (core.pickVoice([{lang: "en-US"}, {lang: "hi_IN"}], "hinglish").voice || {}).lang,
  voiceNone: core.pickVoice([], "en").voice,
  noSpeech: core.speechSupported({}),
  noWindow: core.speechSupported(undefined),
  speech: core.speechSupported({speechSynthesis: {}, SpeechSynthesisUtterance: function () {}}),
  noMic: core.recognitionCtor({}),
  target: core.firstTarget(["missing", "scan-btn", "other"], (id) => id !== "missing"),
  noTarget: core.firstTarget(["a"], () => false),
};
console.log(JSON.stringify(out));
"""
    r = subprocess.run([NODE, "-e", script, str(js)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert "[" not in out["speak"] and "/" not in out["speak"] and out["speak"].startswith("Start with")
    assert "*" not in out["masked"] and "sk_live" not in out["masked"]
    assert out["long"] <= 201
    assert (out["voiceEn"], out["voiceHi"], out["voiceNone"]) == ("en-IN", "hi_IN", None)
    assert (out["noSpeech"], out["noWindow"], out["speech"]) == (False, False, True)
    assert out["noMic"] is None and out["target"] == "scan-btn" and out["noTarget"] is None


def test_guide_js_static_rules():
    js = (STATIC_DIR / "guide.js").read_text(encoding="utf-8")
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function",
                "XMLHttpRequest", "WebSocket", "sendBeacon", "getUserMedia", "MediaRecorder"):
        assert bad not in js, bad  # no HTML sinks, no other network or raw-audio APIs
    assert "speechSynthesis" in js and "Voice uses your browser's built-in text-to-speech, not a Meta model" in js
    assert "may send the audio to its speech provider" in js
    for p in STATIC_DIR.glob("*.html"):
        page = p.read_text(encoding="utf-8")
        if p.name == "pet-sheet.html":
            continue  # test-only contact sheet, served only in preview mode
        if p.name == "signin.html":
            assert "guide.js" not in page  # signed-in pages only
        else:
            assert '<script src="/static/guide.js" defer></script>' in page, p.name
