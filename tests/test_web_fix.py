"""Web 3: report UI data, fix proposals, approval gate, apply, re-test, audit, usage, authorization.

Scans use the loopback demo app (each user has their own) in deterministic-only mode, or a fake site for
the verified non-demo asset. No live model calls.
"""
import re
import threading
import time

import httpx
import pytest

from src.web.server import STATIC_DIR, make_server

PASSWORD = "correct-horse-battery"  # test-only
NON_RUNNABLE = {"missing_spf", "missing_dmarc", "weak_tls_or_expiring_cert"}


def fake_site(token_holder):
    def handler(req):
        if req.url.host == "shop.example" and req.url.path == "/":
            return httpx.Response(200, headers={"content-type": "text/html"},
                                  text=f'<meta name="maya-verify" content="{token_holder[0]}">')
        return httpx.Response(404, text="nope")
    return httpx.MockTransport(handler)


@pytest.fixture
def web(tmp_path):
    token = ["none"]
    server = make_server(port=0, data_dir=tmp_path, http_transport=fake_site(token),
                         provider_factory=lambda: (None, "none"))  # deterministic-only: never a model call
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    clients = []

    def new_client():
        c = httpx.Client(base_url=base, trust_env=False, follow_redirects=False, timeout=30)
        clients.append(c)
        return c

    yield {"server": server, "new_client": new_client, "token": token, "tmp": tmp_path}
    for c in clients:
        c.close()
    server.shutdown()
    server.server_close()


def post(c, path, body=None):
    csrf = c.get("/api/session").json()["csrf"]
    return c.post(path, json=body or {}, headers={"X-CSRF-Token": csrf})


def company_user(c, email):
    assert post(c, "/api/signup", {"email": email, "password": PASSWORD}).status_code == 200
    for path, body in [("/api/profile", {"account_type": "company"}), ("/api/asset", {"asset": "website"}),
                       ("/api/connect", {"connect": True}), ("/api/consent", {"allow": True})]:
        assert post(c, path, body).status_code == 200
    return c


def finished_run(c, asset_id):
    run_id = post(c, f"/api/assets/{asset_id}/scan").json()["run"]["id"]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        body = c.get(f"/api/runs/{run_id}").json()
        if body["run"]["status"] != "running":
            return run_id, body
        time.sleep(0.1)
    raise AssertionError("scan did not finish")


def demo_run(c):
    asset = post(c, "/api/assets", {"demo": True}).json()["asset"]
    return finished_run(c, asset["id"])


def approve_all(c, run_id, proposals, approver="demo-user", skip=()):
    for p in proposals:
        if p["check_id"] not in skip:
            r = post(c, f"/api/runs/{run_id}/proposals/{p['id']}/approve", {"approver": approver})
            assert r.status_code == 200, r.text


# --- result data: details, fix-first, proposals ---

def test_result_has_fixed_template_details_and_proposals(web):
    c = company_user(web["new_client"](), "a@shop.example")
    run_id, body = demo_run(c)
    findings = body["result"]["findings"]
    assert len(findings) == 11
    for f in findings:
        for key in ("plain", "analogy", "technical", "fix", "owasp"):
            assert f[key], (f["id"], key)
        assert f["owasp"].startswith("mapped to")  # never "compliant"
    assert body["result"]["fix_first"] == findings[:3] and findings[0]["severity"] == "critical"
    props = body["proposals"]
    assert {p["check_id"] for p in props} == {f["check"] for f in findings}
    for p in props:
        assert p["status"] == "PROPOSED" and p["note"] == "proposed, not applied"
        assert p["nginx"] and p["apache"] and p["python_note"] and p["rollback"] and p["risk"] in ("low", "medium")
    assert body["run"]["can_apply"] is True and body["retest"] is None


# --- approval gate through the API ---

@pytest.mark.parametrize("approver", ["", "   ", None])
def test_approve_needs_a_name(web, approver):
    c = company_user(web["new_client"](), "n@shop.example")
    run_id, body = demo_run(c)
    pid = body["proposals"][0]["id"]
    r = post(c, f"/api/runs/{run_id}/proposals/{pid}/approve", {"approver": approver})
    assert r.status_code == 400 and "approver" in r.json()["error"]
    assert c.get(f"/api/runs/{run_id}").json()["proposals"][0]["status"] == "PROPOSED"


def test_apply_refused_for_unapproved_proposal(web):
    c = company_user(web["new_client"](), "u@shop.example")
    run_id, body = demo_run(c)
    env = next(p for p in body["proposals"] if p["check_id"] == "exposed_env_file")
    r = post(c, f"/api/runs/{run_id}/apply", {"proposal_ids": [env["id"]]})
    assert r.status_code == 409 and "apply needs APPROVED" in r.json()["error"]
    assert post(c, f"/api/runs/{run_id}/apply").status_code == 409  # nothing approved at all
    audit = c.get("/api/audit").json()
    assert audit["intact"] and audit["entries"][0]["event"] == "apply_refused"
    # the demo app is untouched: re-test still finds everything
    assert post(c, f"/api/runs/{run_id}/retest").json()["retest"]["status"] == "STILL_PRESENT"


def test_apply_refused_for_non_demo_asset(web):
    c = company_user(web["new_client"](), "site@shop.example")
    asset = post(c, "/api/assets", {"url": "https://shop.example"}).json()["asset"]
    web["token"][0] = asset["verification"]["token"]
    assert post(c, f"/api/assets/{asset['id']}/verify").json()["asset"]["status"] == "verified"
    run_id, body = finished_run(c, asset["id"])
    assert body["run"]["can_apply"] is False and body["proposals"]  # header findings on the fake site
    approve_all(c, run_id, body["proposals"])
    r = post(c, f"/api/runs/{run_id}/apply")
    assert (r.status_code, r.json()["error"]) == (409, "fixes can be applied only to the loopback demo app")
    statuses = {p["status"] for p in c.get(f"/api/runs/{run_id}").json()["proposals"]}
    assert statuses == {"APPROVED"}  # nothing applied
    assert c.get("/api/audit").json()["entries"][0]["event"] == "apply_refused"


def test_full_fix_flow_then_retest_fixed(web):
    c = company_user(web["new_client"](), "flow@shop.example")
    run_id, body = demo_run(c)
    before = {f["id"] for f in body["result"]["findings"]}
    approve_all(c, run_id, body["proposals"])
    applied = post(c, f"/api/runs/{run_id}/apply")
    assert applied.status_code == 200
    assert {p["status"] for p in applied.json()["proposals"]} == {"APPLIED"}
    assert {p["approved_by"] for p in applied.json()["proposals"]} == {"demo-user"}
    r = post(c, f"/api/runs/{run_id}/retest").json()["retest"]
    assert r["status"] == "FIXED" and set(r["resolved"]) == before and r["still_present"] == [] and r["new"] == []
    audit = c.get("/api/audit").json()
    assert audit["intact"] is True
    events = [e["event"] for e in reversed(audit["entries"])]
    assert events[-3:] == ["applied", "health_ok", "retested"] and events.count("approved") == 9  # last 12 shown
    assert audit["count"] == 11 + 3


def test_retest_still_present_when_one_fix_is_held_back(web):
    c = company_user(web["new_client"](), "partial@shop.example")
    run_id, body = demo_run(c)
    approve_all(c, run_id, body["proposals"], skip={"exposed_env_file"})
    post(c, f"/api/runs/{run_id}/apply")
    r = post(c, f"/api/runs/{run_id}/retest").json()["retest"]
    assert r["status"] == "STILL_PRESENT" and r["still_present"] == ["exposed_env_file:/.env"]
    assert r["per_check"]["exposed_env_file"] == "STILL_PRESENT" and r["per_check"]["missing_csp"] == "FIXED"


def test_health_check_failure_rolls_back_via_api(web):
    c = company_user(web["new_client"](), "health@shop.example")
    run_id, body = demo_run(c)
    approve_all(c, run_id, body["proposals"])
    web["server"].app.gate(1).health = lambda url: False  # simulate a broken site after the change
    r = post(c, f"/api/runs/{run_id}/apply").json()
    assert {p["status"] for p in r["proposals"]} == {"ROLLED_BACK"}
    assert post(c, f"/api/runs/{run_id}/retest").json()["retest"]["status"] == "STILL_PRESENT"
    events = [e["event"] for e in c.get("/api/audit").json()["entries"]]
    assert "rolled_back" in events


def test_each_user_has_their_own_demo_app(web):
    a = company_user(web["new_client"](), "alice@shop.example")
    b = company_user(web["new_client"](), "bob@shop.example")
    run_a, body_a = demo_run(a)
    approve_all(a, run_a, body_a["proposals"])
    post(a, f"/api/runs/{run_a}/apply")
    _, body_b = demo_run(b)  # Bob's demo app is still vulnerable: Alice's fixes never reach it
    assert len(body_b["result"]["findings"]) == 11
    assert web["server"].app.demo_port(1) != web["server"].app.demo_port(2)


# --- authorization ---

def test_other_users_cannot_touch_runs_proposals_or_audit(web):
    a = company_user(web["new_client"](), "owner@shop.example")
    b = company_user(web["new_client"](), "other@shop.example")
    run_id, body = demo_run(a)
    pid = body["proposals"][0]["id"]
    assert post(b, f"/api/runs/{run_id}/proposals/{pid}/approve", {"approver": "mallory"}).status_code == 404
    assert post(b, f"/api/runs/{run_id}/apply").status_code == 404
    assert post(b, f"/api/runs/{run_id}/retest").status_code == 404
    assert b.get(f"/api/runs/{run_id}").status_code == 404
    approve_all(a, run_id, body["proposals"][:1])
    assert b.get("/api/audit").json()["count"] == 0  # Bob sees only his own (empty) audit log
    assert a.get("/api/audit").json()["count"] == 1
    assert b.get("/api/usage").json()["runs"] == 0 and a.get("/api/usage").json()["runs"] == 1
    anon = web["new_client"]()
    for path in ("/api/audit", "/api/usage"):
        assert anon.get(path).status_code == 401


def test_unknown_proposal_and_bad_ids(web):
    c = company_user(web["new_client"](), "ids@shop.example")
    run_id, _ = demo_run(c)
    assert post(c, f"/api/runs/{run_id}/proposals/fix-made_up_check/approve", {"approver": "x"}).status_code == 404
    assert post(c, f"/api/runs/{run_id}/apply", {"proposal_ids": ["fix-nope"]}).status_code == 404
    assert post(c, f"/api/runs/{run_id}/apply", {"proposal_ids": "fix-missing_csp"}).status_code == 400
    assert post(c, f"/api/runs/{run_id}/proposals/../../approve", {"approver": "x"}).status_code == 404


def test_actions_need_csrf(web):
    c = company_user(web["new_client"](), "csrf@shop.example")
    run_id, body = demo_run(c)
    pid = body["proposals"][0]["id"]
    for path in (f"/api/runs/{run_id}/proposals/{pid}/approve", f"/api/runs/{run_id}/apply",
                 f"/api/runs/{run_id}/retest"):
        assert c.post(path, json={"approver": "x"}, headers={"X-CSRF-Token": "forged"}).status_code == 403


def test_actions_wait_for_running_scan(web):
    from src.web.runs import Run
    c = company_user(web["new_client"](), "busy@shop.example")
    app = web["server"].app
    run = Run(id="a" * 24, user_id=1, asset_id="b" * 16, demo=True, mode="deterministic", model="none",
              started_at=0.0)
    app.runs.runs[run.id] = run  # a scan still in progress
    assert post(c, f"/api/runs/{run.id}/retest").json()["error"] == "wait for the scan to finish"
    assert post(c, f"/api/runs/{run.id}/apply").status_code == 409


# --- role toggle is display-only ---

def test_role_toggle_only_changes_displayed_text():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    body = re.search(r"function setRole\(role\) \{(.*?)\n  \}", js, re.S).group(1)
    for forbidden in ("fetch(", "post(", "getJSON(", "innerHTML", ".textContent"):
        assert forbidden not in body, forbidden  # no request, no rewritten text: it flips one attribute
    assert 'panel.setAttribute("data-role", role)' in body
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    for role in ("owner", "developer", "technical"):
        assert f'[data-role="{role}"] .finding-body' in css
    page = (STATIC_DIR / "chat.html").read_text(encoding="utf-8")
    assert 'id="findings-panel" class="panel" data-role="owner"' in page
    for role in ("owner", "developer", "technical"):
        assert f'id="role-{role}"' in page
    # every role's text is built into each card up front (the toggle never asks the server for anything)
    card = re.search(r"function findingCard\(f\) \{(.*?)\n  \}", js, re.S).group(1)
    for cls, field in (("role-owner", "f.plain"), ("role-owner analogy", "f.analogy"),
                       ("role-developer role-technical", "f.technical"), ("role-owner role-developer fix", "f.fix")):
        assert cls in card and field in card


def test_result_texts_do_not_depend_on_role(web):
    c = company_user(web["new_client"](), "role@shop.example")
    run_id, body = demo_run(c)
    for role in ("owner", "developer", "technical"):  # the server ignores any role hint
        again = c.get(f"/api/runs/{run_id}?role={role}").json()
        assert again["result"]["findings"] == body["result"]["findings"]


# --- Web 5.1: reset the demo app ---

def demo_asset(c):
    return post(c, "/api/assets", {"demo": True}).json()["asset"]


def test_reset_demo_restores_the_vulnerable_app(web):
    c = company_user(web["new_client"](), "reset@shop.example")
    asset = demo_asset(c)
    run_id, body = finished_run(c, asset["id"])
    original = {f["id"] for f in body["result"]["findings"]}
    assert len(original) == 11
    approve_all(c, run_id, body["proposals"])
    assert post(c, f"/api/runs/{run_id}/apply").status_code == 200
    assert post(c, f"/api/runs/{run_id}/retest").json()["retest"]["status"] == "FIXED"
    _, fixed = finished_run(c, asset["id"])
    assert fixed["result"]["findings"] == []  # fixes are on
    r = post(c, f"/api/assets/{asset['id']}/reset-demo")
    assert r.status_code == 200 and r.json()["reset"] is True and len(r.json()["previous_fixes"]) == 11
    audit = c.get("/api/audit").json()
    assert audit["intact"] and audit["entries"][0]["event"] == "demo_reset"
    assert len(audit["entries"][0]["details"]["previous_fixes"]) == 11
    _, again = finished_run(c, asset["id"])
    assert {f["id"] for f in again["result"]["findings"]} == original  # the original 11 issues are back


def test_reset_demo_touches_only_this_users_demo_app(web):
    a = company_user(web["new_client"](), "reset-a@shop.example")
    b = company_user(web["new_client"](), "reset-b@shop.example")
    a_asset = demo_asset(a)
    run_b, body_b = demo_run(b)
    approve_all(b, run_b, body_b["proposals"])
    assert post(b, f"/api/runs/{run_b}/apply").status_code == 200
    b_control = web["server"].app.demo_control(2)
    before = b_control.read_text(encoding="utf-8")
    assert post(a, f"/api/assets/{a_asset['id']}/reset-demo").status_code == 200
    assert b_control.read_text(encoding="utf-8") == before  # Bob's fixes stay on
    b_asset_id = b.get("/api/assets").json()["assets"][0]["id"]
    _, b_again = finished_run(b, b_asset_id)
    assert b_again["result"]["findings"] == []
    assert post(a, f"/api/assets/{b_asset_id}/reset-demo").status_code == 404  # not Alice's asset
    assert [e["event"] for e in b.get("/api/audit").json()["entries"]].count("demo_reset") == 0


def test_reset_demo_refusals(web):
    c = company_user(web["new_client"](), "reset-no@shop.example")
    web["token"][0] = "tok"
    site = post(c, "/api/assets", {"url": "https://shop.example"}).json()["asset"]
    assert post(c, f"/api/assets/{site['id']}/reset-demo").json()["error"] == "only MAYA's demo app can be reset"
    asset = demo_asset(c)
    assert c.post(f"/api/assets/{asset['id']}/reset-demo", json={},
                  headers={"X-CSRF-Token": "forged"}).status_code == 403
    assert web["new_client"]().post(f"/api/assets/{asset['id']}/reset-demo", json={}).status_code in (401, 403)
    from src.web.runs import Run
    app = web["server"].app
    app.runs.runs["c" * 24] = Run(id="c" * 24, user_id=1, asset_id=asset["id"], demo=True, mode="deterministic",
                                  model="none", started_at=0.0)  # a scan still in progress
    r = post(c, f"/api/assets/{asset['id']}/reset-demo")
    assert r.status_code == 409 and r.json()["error"] == "wait for the running scan to finish"
    assert c.get("/api/audit").json()["count"] == 0  # nothing reset, nothing audited
