"""Web 7: Disconnect an asset, and "Delete my data" (per user, CSRF, type DELETE to confirm)."""
import io
import json
import sqlite3
import threading
import time
import zipfile

import httpx
import pytest

from src.approval import AuditLog
from src.web.server import STATIC_DIR, make_server

PASSWORD = "correct-horse-battery"  # test-only


@pytest.fixture
def web(tmp_path):
    token = ["none"]

    def site(req):
        if req.url.host == "shop.example" and req.url.path == "/":
            return httpx.Response(200, headers={"content-type": "text/html"},
                                  text=f'<meta name="maya-verify" content="{token[0]}">')
        return httpx.Response(404, text="nope")

    server = make_server(port=0, data_dir=tmp_path, http_transport=httpx.MockTransport(site),
                         provider_factory=lambda: (None, "none"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    clients = []

    def new_client():
        c = httpx.Client(base_url=base, trust_env=False, follow_redirects=False, timeout=60)
        clients.append(c)
        return c

    yield {"new_client": new_client, "tmp": tmp_path, "token": token, "server": server}
    for c in clients:
        c.close()
    server.shutdown()
    server.server_close()


def post(c, path, body=None, csrf=True):
    headers = {"X-CSRF-Token": c.get("/api/session").json()["csrf"]} if csrf else {}
    return c.post(path, json=body or {}, headers=headers)


def company(c, email):
    assert post(c, "/api/signup", {"email": email, "password": PASSWORD}).status_code == 200
    for path, body in [("/api/profile", {"account_type": "company"}), ("/api/asset", {"asset": "website"}),
                       ("/api/connect", {"connect": True}), ("/api/consent", {"allow": True})]:
        assert post(c, path, body).status_code == 200
    return c


def finished(c, asset_id):
    run_id = post(c, f"/api/assets/{asset_id}/scan").json()["run"]["id"]
    for _ in range(300):
        body = c.get(f"/api/runs/{run_id}").json()
        if body["run"]["status"] != "running":
            return run_id, body
        time.sleep(0.1)
    raise AssertionError("scan did not finish")


def code_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("app.py", 'SECRET_KEY = "fake-demo-secret-1234567890"\nDEBUG = True\n')
    return buf.getvalue()


def everything(c, web):
    """One user's full footprint: demo scan + applied fixes, a code upload, a chat."""
    demo = post(c, "/api/assets", {"demo": True}).json()["asset"]
    run_id, body = finished(c, demo["id"])
    assert post(c, f"/api/runs/{run_id}/approve-all", {"approver": "demo-user"}).status_code == 200
    assert post(c, f"/api/runs/{run_id}/apply").status_code == 200
    up = c.post("/api/uploads/code", content=code_zip(), headers={
        "Content-Type": "application/zip", "X-Upload-Name": "app.zip",
        "X-CSRF-Token": c.get("/api/session").json()["csrf"]})
    assert up.status_code == 200
    chat = post(c, "/api/chats", {"title": "hello"}).json()["chat"]
    return {"run": run_id, "upload_run": up.json()["run"]["id"], "chat": chat["id"]}


def evidence_owners(tmp) -> dict:
    with sqlite3.connect(tmp / "evidence.db") as db:
        return dict(db.execute("SELECT owner, COUNT(*) FROM evidence GROUP BY owner").fetchall())


def test_delete_my_data_removes_only_this_users_data(web):
    tmp, app = web["tmp"], web["server"].app
    a, b = company(web["new_client"](), "a@shop.example"), company(web["new_client"](), "b@shop.example")
    fa, fb = everything(a, web), everything(b, web)
    before = evidence_owners(tmp)
    assert before["user-1"] > 0 and before["user-2"] > 0
    assert app.demo_control(1).exists() and app.demo_control(2).exists()
    b_control = app.demo_control(2).read_text(encoding="utf-8")

    assert post(a, "/api/data/delete", {"confirm": "delete"}).json()["error"] == "type DELETE to confirm"
    assert post(a, "/api/data/delete", {"confirm": "DELETE"}, csrf=False).status_code == 403
    assert post(web["new_client"](), "/api/data/delete", {"confirm": "DELETE"}).status_code == 401
    r = post(a, "/api/data/delete", {"confirm": "DELETE"})
    assert r.status_code == 200, r.text
    counts = r.json()["deleted"]
    assert counts["assets"] == 2 and counts["runs"] == 2 and counts["chats"] == 1 and counts["demo_control_file"] == 1
    assert counts["evidence_records"] == before["user-1"]

    # Alice: everything gone
    assert a.get("/api/assets").json()["assets"] == [] and a.get("/api/chats").json()["chats"] == []
    for rid in (fa["run"], fa["upload_run"]):
        assert a.get(f"/api/runs/{rid}").status_code == 404
    assert a.get(f"/api/chats/{fa['chat']}").status_code == 404
    assert "user-1" not in evidence_owners(tmp) and not app.demo_control(1).exists()
    audit = AuditLog(tmp / "audit" / "user-1.jsonl")
    entries = audit._entries()
    assert audit.verify() == (True, None) and [e["event"] for e in entries] == ["user_data_deleted"]
    assert set(entries[0]["details"]) == {"assets", "runs", "evidence_records", "chats", "demo_control_file"}
    assert a.get("/api/session").json()["user"]["email"] == "a@shop.example"  # the account itself remains

    # Bob: untouched
    assert len(b.get("/api/assets").json()["assets"]) == 2 and len(b.get("/api/chats").json()["chats"]) == 1
    for rid in (fb["run"], fb["upload_run"]):
        assert b.get(f"/api/runs/{rid}").status_code == 200
    assert evidence_owners(tmp)["user-2"] == before["user-2"]
    assert app.demo_control(2).read_text(encoding="utf-8") == b_control
    assert [e["event"] for e in AuditLog(tmp / "audit" / "user-2.jsonl")._entries()].count("approved") > 0


def test_delete_refused_while_scan_runs(web):
    from src.web.runs import Run
    c = company(web["new_client"](), "busy@shop.example")
    web["server"].app.runs.runs["d" * 24] = Run(id="d" * 24, user_id=1, asset_id="e" * 16, demo=True,
                                                 mode="deterministic", model="none", started_at=0.0)
    r = post(c, "/api/data/delete", {"confirm": "DELETE"})
    assert r.status_code == 409 and r.json()["error"] == "wait for the running scan to finish"


def test_disconnect_removes_asset_and_verified_status(web):
    tmp = web["tmp"]
    a, b = company(web["new_client"](), "own@shop.example"), company(web["new_client"](), "x@shop.example")
    site = post(a, "/api/assets", {"url": "https://shop.example"}).json()["asset"]
    web["token"][0] = site["verification"]["token"]
    assert post(a, f"/api/assets/{site['id']}/verify").json()["asset"]["status"] == "verified"
    assert "shop.example" in json.loads((tmp / "verified_targets.json").read_text(encoding="utf-8"))
    assert post(b, f"/api/assets/{site['id']}/disconnect").status_code == 404  # not Bob's
    assert post(a, f"/api/assets/{site['id']}/disconnect", csrf=False).status_code == 403
    r = post(a, f"/api/assets/{site['id']}/disconnect")
    assert r.status_code == 200 and r.json() == {"disconnected": site["id"]}
    assert a.get("/api/assets").json()["assets"] == []
    assert "shop.example" not in json.loads((tmp / "verified_targets.json").read_text(encoding="utf-8"))
    assert post(a, f"/api/assets/{site['id']}/scan").status_code == 404  # gone: cannot be scanned any more


def test_data_use_note_is_shown():
    chat = (STATIC_DIR / "chat.html").read_text(encoding="utf-8")
    consent = (STATIC_DIR / "consent.html").read_text(encoding="utf-8")
    for words in ("What is stored:", "Where:", "How long:", "only on this computer", "never saved",
                  "Type DELETE to confirm"):
        assert words in chat
    assert "local data folder on this computer" in consent and "never saved" in consent


def test_connectors_listed_with_coming_soon_not_buttons():
    import re
    chat = (STATIC_DIR / "chat.html").read_text(encoding="utf-8")
    block = chat.split('<div id="connectors"', 1)[1].split("</div>", 1)[0]
    assert re.search(r'href="/connect-website"><span>Website</span>', block)
    assert '<button id="conn-code"' in block and '<button id="conn-logs"' in block
    for name in ("API", "Mobile", "Cloud"):
        li = re.search(rf'<li class="connector connector-soon"><span>{name}</span><span class="chip">coming soon</span></li>',
                       block)
        assert li and "<button" not in li.group(0) and "<a " not in li.group(0)
