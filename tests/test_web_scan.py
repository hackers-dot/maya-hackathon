"""Web 2: connect website, ownership verification, scans, live trace, authorization.

No network except the loopback demo app the server starts itself. Ownership checks use a fake site
(httpx.MockTransport); the model is a scripted fake (MockProvider) or absent (deterministic-only mode).
"""
import json
import socket
import threading
import time

import httpx
import pytest

from src.provider import ChatResult, MockProvider
from src.web.assets import AssetError, parse_target
from src.web.server import make_server

PASSWORD = "correct-horse-battery"  # test-only


def tool_reply(*calls):
    usage = {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140,
             "completion_tokens_details": {"reasoning_tokens": 30}}
    return ChatResult("", True, usage, tool_calls=list(calls), finish_reason="tool_calls")


def call(check, path, cid):
    return {"id": cid, "type": "function",
            "function": {"name": "run_check", "arguments": json.dumps({"check_id": check, "path": path})}}


def final_reply(claims):
    usage = {"prompt_tokens": 200, "completion_tokens": 50, "total_tokens": 250}
    return ChatResult(json.dumps({"claims": claims}), True, usage, finish_reason="stop")


def scripted():
    return MockProvider(script=[
        tool_reply(call("exposed_env_file", "/.env", "a"), call("missing_csp", "/", "b")),
        final_reply([{"finding_id": "exposed_env_file:/.env", "claim_type": "file_exposed", "priority": 1,
                      "note": "Block /.env and rotate its keys."},
                     {"finding_id": "missing_csp:/", "claim_type": "header_missing", "priority": 2}]),
    ])


class Gated:
    """Fake provider that waits for release() before each reply, so tests can poll between steps."""

    def __init__(self, inner):
        self.inner, self.gate = inner, threading.Semaphore(0)

    def release(self, n=1):
        for _ in range(n):
            self.gate.release()

    def chat(self, messages, **kw):
        assert self.gate.acquire(timeout=30), "test never released the step"
        return self.inner.chat(messages, **kw)


def fake_site(token_holder):
    """shop.example serves the meta tag with whatever token the test placed in token_holder[0]."""
    def handler(req):
        if req.url.host == "shop.example" and req.url.path == "/":
            return httpx.Response(200, headers={"content-type": "text/html"},
                                  text=f'<html><head><meta name="maya-verify" content="{token_holder[0]}"></head></html>')
        return httpx.Response(404, text="nope")
    return httpx.MockTransport(handler)


@pytest.fixture
def web(tmp_path):
    token_holder = ["nothing-yet"]
    state = {"provider": None}
    server = make_server(port=0, data_dir=tmp_path, http_transport=fake_site(token_holder),
                         provider_factory=lambda: (state["provider"], "fake-model" if state["provider"] else "none"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    clients = []

    def new_client():
        c = httpx.Client(base_url=base, trust_env=False, follow_redirects=False)
        clients.append(c)
        return c

    yield {"server": server, "new_client": new_client, "token": token_holder, "state": state}
    for c in clients:
        c.close()
    server.shutdown()
    server.server_close()


def csrf(c):
    return c.get("/api/session").json()["csrf"]


def post(c, path, body=None):
    return c.post(path, json=body or {}, headers={"X-CSRF-Token": csrf(c)})


def company_user(c, email):
    assert post(c, "/api/signup", {"email": email, "password": PASSWORD}).status_code == 200
    post(c, "/api/profile", {"account_type": "company"})
    post(c, "/api/asset", {"asset": "website"})
    post(c, "/api/connect", {"connect": True})
    r = post(c, "/api/consent", {"allow": True})
    assert r.json()["next"] == "/connect-website"  # consent now leads to the connect-website card
    return c


def add_asset(c, url):
    r = post(c, "/api/assets", {"url": url})
    assert r.status_code == 200, r.text
    return r.json()["asset"]


def wait_run(c, run_id, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = c.get(f"/api/runs/{run_id}").json()["run"]
        if run["status"] != "running":
            return run
        time.sleep(0.1)
    raise AssertionError("run did not finish")


# --- URL validation ---

@pytest.mark.parametrize("url,kind,origin", [
    ("https://shop.example", "website", "https://shop.example"),
    ("shop.example", "website", "https://shop.example"),
    ("HTTP://Shop.Example:8080/path?q=1#x", "website", "http://shop.example:8080"),
    ("  https://www.shop.example/  ", "website", "https://www.shop.example"),
    ("http://127.0.0.1:8081", "demo", "demo"),
    ("http://localhost", "demo", "demo"),
    ("http://[::1]:5432", "demo", "demo"),       # any loopback = the demo app, never another local service
    ("http://127.0.0.2:3306/admin", "demo", "demo"),
])
def test_url_validation_accepts(url, kind, origin):
    t = parse_target(url)
    assert (t["kind"], t["origin"]) == (kind, origin)


@pytest.mark.parametrize("url", [
    "ftp://shop.example", "javascript:alert(1)", "file:///etc/passwd",
    "https://user@shop.example", "https://user:pw@shop.example", "http://127.0.0.1@evil.example",
    "http://2130706433", "http://0x7f000001", "http://127.1", "http://0177.0.0.1",
    "http://8.8.8.8", "http://10.0.0.1", "http://169.254.169.254", "http://[fe80::1]",
    "http://intranet", "https://shop.example:99999", "https://shop example.com", "https://shop.example\\@evil",
    "", "   ", "x" * 3000, None, 42,
])
def test_url_validation_refuses(url):
    with pytest.raises(AssetError):
        parse_target(url)


# --- token issue / verify ---

def test_token_issue_and_verify_flow(web):
    c = company_user(web["new_client"](), "a@shop.example")
    asset = add_asset(c, "https://shop.example/login")
    assert asset["status"] == "pending" and asset["label"] == "shop.example" and not asset["scannable"]
    v = asset["verification"]
    assert v["token"].startswith("maya-verify-")
    assert v["meta_tag"] == f'<meta name="maya-verify" content="{v["token"]}">'
    assert v["file_path"] == "/.well-known/maya-verify.txt"
    assert add_asset(c, "https://shop.example")["id"] == asset["id"]  # same origin: same asset, same token

    web["token"][0] = "maya-verify-some-other-token-0000000"
    failed = post(c, f"/api/assets/{asset['id']}/verify").json()["asset"]
    assert failed["status"] == "failed" and "token not found" in failed["reason"]

    web["token"][0] = v["token"]
    ok = post(c, f"/api/assets/{asset['id']}/verify").json()["asset"]
    assert ok["status"] == "verified" and ok["scannable"] is True
    listed = c.get("/api/assets").json()["assets"]
    assert [(a["label"], a["status"]) for a in listed] == [("shop.example", "verified")]
    assert c.get("/").headers["location"] == "/chat"  # has an asset now


def test_loopback_url_is_the_demo_app(web):
    c = company_user(web["new_client"](), "demo@shop.example")
    asset = add_asset(c, "http://127.0.0.1:5432")
    assert (asset["kind"], asset["status"], asset["scannable"]) == ("demo", "loopback", True)
    assert asset["label"] == "Demo app (loopback, no verification needed)"
    assert "verification" not in asset and asset["origin"] is None
    assert add_asset(c, "http://localhost:9999")["id"] == asset["id"]  # every loopback URL is the same demo asset
    r = post(c, "/api/assets", {"demo": True})  # what the "Use MAYA's demo app" button sends
    assert r.json()["asset"]["id"] == asset["id"]
    assert post(c, "/api/assets", {"demo": "yes"}).status_code == 400  # only an explicit true picks the demo


def test_assets_need_consent(web):
    c = web["new_client"]()
    post(c, "/api/signup", {"email": "early@shop.example", "password": PASSWORD})
    assert post(c, "/api/assets", {"url": "https://shop.example"}).status_code == 409
    assert c.get("/connect-website").headers["location"] == "/onboarding"


def test_bad_url_reason_is_shown(web):
    c = company_user(web["new_client"](), "bad@shop.example")
    r = post(c, "/api/assets", {"url": "http://8.8.8.8"})
    assert r.status_code == 400 and r.json()["error"] == "address refused: use your website's domain name, not an IP address"


# --- scans ---

def test_scan_refused_for_unverified_asset(web):
    c = company_user(web["new_client"](), "u@shop.example")
    asset = add_asset(c, "https://shop.example")
    for status_before in ("pending", "failed"):
        if status_before == "failed":
            post(c, f"/api/assets/{asset['id']}/verify")
        r = post(c, f"/api/assets/{asset['id']}/scan")
        assert (r.status_code, r.json()["error"]) == (409, "verify ownership before scanning")


def test_deterministic_only_mode(web):
    web["state"]["provider"] = None  # no model key configured
    c = company_user(web["new_client"](), "det@shop.example")
    asset = add_asset(c, "http://127.0.0.1")
    run = post(c, f"/api/assets/{asset['id']}/scan").json()["run"]
    assert run["mode"] == "deterministic" and run["mode_label"] == "Deterministic only (no model key configured)"
    assert run["model"] == "none" and run["demo"] is True
    done = wait_run(c, run["id"])
    assert done["status"] == "finished"
    body = c.get(f"/api/runs/{run['id']}").json()
    checks = {f["check"] for f in body["result"]["findings"]}
    assert {"exposed_env_file", "missing_csp", "directory_listing"} <= checks
    assert body["result"]["claims"] == [] and body["result"]["usage"]["total_tokens"] == 0
    trace = c.get(f"/api/runs/{run['id']}/trace?after=0").json()
    assert trace["steps"][0]["tool"] == "deterministic check" and len(trace["steps"]) == trace["next"]
    sev = [f["severity"] for f in body["result"]["findings"]]
    assert sev[0] == "critical"  # severity-sorted, from the code table
    assert web["server"].app.demo_port(1) is not None  # this user's demo app started on demand (user id 1)


def test_model_mode_result(web):
    web["state"]["provider"] = scripted()
    c = company_user(web["new_client"](), "model@shop.example")
    asset = add_asset(c, "http://127.0.0.1")
    run = post(c, f"/api/assets/{asset['id']}/scan").json()["run"]
    assert run["mode"] == "model" and run["model"] == "fake-model"
    done = wait_run(c, run["id"])
    assert done["status"] == "finished"
    result = c.get(f"/api/runs/{run['id']}").json()["result"]
    assert [cl["priority"] for cl in result["claims"]] == [1, 2]
    assert result["claims"][0]["note"] == "Block /.env and rotate its keys."
    assert result["claims"][0]["evidence_line"] == "API_KEY=fak...al"
    assert result["usage"]["total_tokens"] == 390 and result["usage"]["reasoning_tokens"] == 30
    steps = c.get(f"/api/runs/{run['id']}/trace?after=0").json()["steps"]
    assert steps[0]["tool"] == "run_check" and steps[0]["tokens"]["reasoning"] == 30
    assert isinstance(steps[0]["latency"], float)


def test_trace_polling_is_incremental(web):
    gated = Gated(scripted())
    web["state"]["provider"] = gated
    c = company_user(web["new_client"](), "poll@shop.example")
    asset = add_asset(c, "http://127.0.0.1")
    run_id = post(c, f"/api/assets/{asset['id']}/scan").json()["run"]["id"]

    first = c.get(f"/api/runs/{run_id}/trace?after=0").json()
    assert first["steps"] == [] and first["next"] == 0 and first["run"]["status"] == "running"

    gated.release()  # step 1: two run_check calls
    deadline = time.monotonic() + 15
    while (t := c.get(f"/api/runs/{run_id}/trace?after=0").json())["next"] < 2:
        assert time.monotonic() < deadline
        time.sleep(0.05)
    assert [s["step"] for s in t["steps"]] == [1, 1]
    assert {f["check"] for f in t["findings"]} == {"exposed_env_file", "missing_csp"}  # findings appear live

    again = c.get(f"/api/runs/{run_id}/trace?after={t['next']}").json()
    assert again["steps"] == [] and again["next"] == t["next"]  # nothing new until the next step

    gated.release()  # step 2: final answer
    wait_run(c, run_id)
    rest = c.get(f"/api/runs/{run_id}/trace?after={t['next']}").json()
    assert [s["step"] for s in rest["steps"]] == [2] and rest["next"] == 3
    assert c.get(f"/api/runs/{run_id}/trace?after=abc").status_code == 400


def test_one_scan_per_user_at_a_time(web):
    gated = Gated(scripted())
    web["state"]["provider"] = gated
    c = company_user(web["new_client"](), "one@shop.example")
    asset = add_asset(c, "http://127.0.0.1")
    first = post(c, f"/api/assets/{asset['id']}/scan").json()["run"]
    second = post(c, f"/api/assets/{asset['id']}/scan")
    assert (second.status_code, second.json()["error"]) == (409, "a scan is already running; wait for it to finish")
    gated.release(2)
    wait_run(c, first["id"])
    web["state"]["provider"] = None
    assert post(c, f"/api/assets/{asset['id']}/scan").status_code == 200  # allowed again once finished


def test_other_users_runs_and_assets_are_not_found(web):
    web["state"]["provider"] = None
    a = company_user(web["new_client"](), "alice@shop.example")
    b = company_user(web["new_client"](), "bob@shop.example")
    asset = add_asset(a, "http://127.0.0.1")
    run_id = post(a, f"/api/assets/{asset['id']}/scan").json()["run"]["id"]
    wait_run(a, run_id)
    for path in (f"/api/runs/{run_id}", f"/api/runs/{run_id}/trace?after=0"):
        assert b.get(path).status_code == 404
        assert a.get(path).status_code == 200
    for action in ("verify", "scan"):
        assert post(b, f"/api/assets/{asset['id']}/{action}").status_code == 404
    assert b.get("/api/assets").json()["assets"] == []
    anon = web["new_client"]()
    assert anon.get(f"/api/runs/{run_id}").status_code == 401
    assert anon.get("/api/assets").status_code == 401


def test_unknown_ids_are_not_found(web):
    c = company_user(web["new_client"](), "ids@shop.example")
    assert c.get("/api/runs/" + "f" * 24).status_code == 404
    assert post(c, "/api/assets/" + "f" * 16 + "/scan").status_code == 404
    assert c.get("/api/runs/../../etc").status_code == 404
    assert post(c, "/api/assets/xyz/scan").status_code == 404


def test_scan_needs_csrf(web):
    web["state"]["provider"] = None
    c = company_user(web["new_client"](), "csrf@shop.example")
    asset = add_asset(c, "http://127.0.0.1")
    r = c.post(f"/api/assets/{asset['id']}/scan", json={}, headers={"X-CSRF-Token": "forged"})
    assert r.status_code == 403


def test_demo_app_stops_with_the_server(tmp_path):
    server = make_server(port=0, data_dir=tmp_path, provider_factory=lambda: (None, "none"))
    port = server.app.demo_port()
    assert port is None
    server.app.ensure_demo()
    port = server.app.demo_port()
    with socket.create_connection(("127.0.0.1", port), timeout=5):
        pass
    server.server_close()
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=2).close()


# --- preview sample data ---

def test_preview_sample_only_in_preview_mode(tmp_path, web):
    assert web["new_client"]().get("/__preview/sample.json").status_code == 404
    server = make_server(port=0, data_dir=tmp_path / "p", ui_preview=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_address[1]}", trust_env=False) as c:
            data = c.get("/__preview/sample.json").json()
            assert set(data) == {"assets", "running", "finished", "extras", "chat", "eval", "logs", "message",
                                 "lookalike"}
            assert data["eval"]["available"] is False or "totals" in data["eval"]  # read from the real file
            assert c.get("/__preview/connect-website.html").status_code == 200
            assert c.get("/api/assets").status_code == 401  # preview gives no account data
    finally:
        server.shutdown()
        server.server_close()
