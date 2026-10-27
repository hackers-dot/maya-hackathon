import logging
import re
import threading
from pathlib import Path

import httpx
import pytest

from src.web.auth import (LOGIN_MAX_FAILURES, LOGIN_WINDOW, MIN_PASSWORD, Accounts, AuthError, RateLimited, Sessions,
                          hash_password, verify_password)
from src.web.server import SECURITY_HEADERS, STATIC_DIR, make_server

PASSWORD = "correct-horse-battery"  # test-only
EMAIL = "owner@shop.example"


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def web(tmp_path):
    """Server on a free 127.0.0.1 port; client keeps cookies and sends the right Host header."""
    clock = Clock()
    server = make_server(port=0, data_dir=tmp_path, session_ttl=3600, clock=clock)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    client = httpx.Client(base_url=base, trust_env=False, follow_redirects=False)
    yield client, clock, tmp_path
    client.close()
    server.shutdown()
    server.server_close()


def csrf(client) -> str:
    return client.get("/api/session").json()["csrf"]


def post(client, path, body, token=None):
    return client.post(path, json=body, headers={"X-CSRF-Token": token if token is not None else csrf(client)})


def signed_up(client, email=EMAIL):
    r = post(client, "/api/signup", {"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r


# --- passwords ---

def test_password_hash_and_verify():
    salt, digest = hash_password(PASSWORD)
    assert len(salt) == 16 and len(digest) == 32
    assert verify_password(PASSWORD, salt, digest)
    assert not verify_password(PASSWORD + "x", salt, digest)
    assert hash_password(PASSWORD)[1] != digest  # random salt per hash


def test_wrong_password_and_unknown_account(tmp_path):
    accts = Accounts(tmp_path / "w.db")
    uid = accts.sign_up(EMAIL, PASSWORD)
    assert accts.sign_in(" OWNER@shop.example ", PASSWORD) == uid
    for email, pw in [(EMAIL, "wrong-password-123"), ("nobody@shop.example", PASSWORD), ("not-an-email", PASSWORD)]:
        with pytest.raises(AuthError, match="invalid email or password"):
            accts.sign_in(email, pw)


@pytest.mark.parametrize("pw", ["short", "x" * (MIN_PASSWORD - 1), None, "y" * 2000])
def test_password_length_enforced(tmp_path, pw):
    with pytest.raises(AuthError):
        Accounts(tmp_path / "w.db").sign_up(EMAIL, pw)


def test_duplicate_account_rejected(tmp_path):
    accts = Accounts(tmp_path / "w.db")
    accts.sign_up(EMAIL, PASSWORD)
    with pytest.raises(AuthError):
        accts.sign_up(EMAIL.upper(), PASSWORD)


def test_rate_limit_per_account(tmp_path):
    clock = Clock()
    accts = Accounts(tmp_path / "w.db", clock=clock)
    accts.sign_up(EMAIL, PASSWORD)
    accts.sign_up("other@shop.example", PASSWORD)
    for _ in range(LOGIN_MAX_FAILURES):
        with pytest.raises(AuthError):
            accts.sign_in(EMAIL, "wrong-password-123")
    with pytest.raises(RateLimited):
        accts.sign_in(EMAIL, PASSWORD)  # even the right password is refused while limited
    assert accts.sign_in("other@shop.example", PASSWORD)  # other accounts unaffected
    clock.t += LOGIN_WINDOW + 1
    assert accts.sign_in(EMAIL, PASSWORD)


def test_password_never_stored_or_logged(web, caplog, capsys):
    client, _, tmp_path = web
    caplog.set_level(logging.DEBUG)
    signed_up(client)
    post(client, "/api/signout", {})
    post(client, "/api/signin", {"email": EMAIL, "password": PASSWORD})
    assert PASSWORD.encode() not in (tmp_path / "web.db").read_bytes()
    out = capsys.readouterr()
    assert PASSWORD not in caplog.text and PASSWORD not in out.out and PASSWORD not in out.err


# --- sessions and CSRF ---

def test_session_expiry():
    clock = Clock()
    sessions = Sessions(ttl=60, clock=clock)
    s = sessions.create(user_id=1)
    assert sessions.get(s.token) is s
    clock.t += 61
    assert sessions.get(s.token) is None


def test_session_expiry_over_http(web):
    client, clock, _ = web
    signed_up(client)
    assert client.get("/api/session").json()["user"]["email"] == EMAIL
    clock.t += 3601
    assert client.get("/api/session").json()["user"] is None  # expired; a fresh anonymous session is issued
    assert client.get("/onboarding").headers["location"] == "/signin"


@pytest.mark.parametrize("token", ["", "wrong-token", None])
def test_csrf_required_on_every_post(web, token):
    client, _, _ = web
    csrf(client)  # session exists, but the request does not carry its token
    headers = {} if token is None else {"X-CSRF-Token": token}
    for path in ["/api/signup", "/api/signin", "/api/profile", "/api/signout"]:
        r = client.post(path, json={"email": EMAIL, "password": PASSWORD}, headers=headers)
        assert r.status_code == 403 and "CSRF" in r.json()["error"], path


def test_csrf_without_session_rejected(web):
    client, _, _ = web
    r = client.post("/api/signin", json={}, headers={"X-CSRF-Token": "anything"})
    assert r.status_code == 403


def test_cookie_flags_and_session_rotation(web):
    client, _, _ = web
    first = client.get("/api/session")
    cookie = first.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Path=/" in cookie
    anon_token = client.cookies["maya_session"]
    r = signed_up(client)
    assert client.cookies["maya_session"] != anon_token  # new session after sign-in (no fixation)
    assert r.json()["csrf"] != first.json()["csrf"]


def test_http_login_rate_limit(web):
    client, _, _ = web
    signed_up(client)
    post(client, "/api/signout", {})
    codes = [post(client, "/api/signin", {"email": EMAIL, "password": "wrong-password-123"}).status_code
             for _ in range(LOGIN_MAX_FAILURES)]
    assert codes == [401] * LOGIN_MAX_FAILURES
    r = post(client, "/api/signin", {"email": EMAIL, "password": PASSWORD})
    assert r.status_code == 429 and "too many attempts" in r.json()["error"]


# --- host, bind, headers ---

@pytest.mark.parametrize("host", ["evil.example", "127.0.0.1.evil.example", "192.168.1.5:8090", "", "localhost.evil"])
def test_host_header_rejected(web, host):
    client, _, _ = web
    for method in ("GET", "POST"):
        r = client.request(method, "/health" if method == "GET" else "/api/signin", headers={"Host": host})
        assert r.status_code == 400 and r.json()["error"] == "invalid Host header"


def test_localhost_host_header_accepted(web):
    client, _, _ = web
    port = client.base_url.port
    assert client.get("/health", headers={"Host": f"localhost:{port}"}).status_code == 200


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "localhost", "::"])
def test_non_loopback_bind_refused(tmp_path, host):
    with pytest.raises(ValueError, match="127.0.0.1 only"):
        make_server(port=0, host=host, data_dir=tmp_path)


@pytest.mark.parametrize("method,path", [("GET", "/health"), ("GET", "/signin"), ("GET", "/static/app.js"),
                                         ("GET", "/api/session"), ("GET", "/nope"), ("POST", "/api/signin"),
                                         ("PUT", "/api/signin"), ("GET", "/onboarding")])
def test_security_headers_on_every_response(web, method, path):
    client, _, _ = web
    r = client.request(method, path)
    for k, v in SECURITY_HEADERS.items():
        assert r.headers.get(k) == v, (path, k)
    assert r.headers["content-security-policy"].startswith("default-src 'self'")
    assert r.headers["x-frame-options"] == "DENY" and r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["server"] == "MAYA"


@pytest.mark.parametrize("path", ["/static/../auth.py", "/static/..%2Fauth.py", "/static/x.py", "/static/APP.JS",
                                  "/static/", "/static/nope.js"])
def test_static_only_serves_known_files(web, path):
    client, _, _ = web
    assert client.get(path).status_code == 404


# --- static files: no HTML injection sinks, no external URLs ---

SINKS = re.compile(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\(|new Function|https?://", re.I)


EXPECTED_STATIC = {"app.js", "theme.js", "style.css", "signin.html", "onboarding.html", "company.html",
                   "connect.html", "consent.html", "chat.html"}


def test_static_files_have_no_injection_sinks_or_external_urls():
    files = sorted(STATIC_DIR.glob("*"))
    assert {p.name for p in files} == EXPECTED_STATIC  # a new file must be added here, and so gets scanned
    hits = [(p.name, m.group()) for p in files for m in SINKS.finditer(p.read_text(encoding="utf-8"))]
    assert hits == []


def test_local_storage_only_inside_try_catch():
    for name in ("app.js", "theme.js"):
        for line in (STATIC_DIR / name).read_text(encoding="utf-8").splitlines():
            if "localStorage" in line:
                assert line.strip().startswith("try {"), (name, line)


def test_chat_is_not_faked():
    page = (STATIC_DIR / "chat.html").read_text(encoding="utf-8")
    assert '<textarea id="prompt"' in page and "disabled></textarea>" in page
    assert 'class="send" type="submit" aria-label="Send" disabled' in page
    assert "Chat coming next" in page
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "fetch(\"/api/chat" not in js and "/api/chat" not in js  # no chat endpoint, no generated replies


def test_static_scanner_catches_bad_patterns(tmp_path):
    for bad in ["el.innerHTML = x", "el.insertAdjacentHTML('a', x)", "document.write(x)", "eval(x)",
                '<script src="https://cdn.example/x.js">', "@import url(http://fonts.example/f.css)"]:
        assert SINKS.search(bad), bad


def test_pages_use_no_inline_script_or_style():
    for p in STATIC_DIR.glob("*.html"):
        text = p.read_text(encoding="utf-8")
        assert re.findall(r"<script(?![^>]*\bsrc=)", text) == [], p.name  # every script is external
        assert " style=" not in text and "<style" not in text, p.name    # CSP default-src 'self' blocks inline CSS
        assert not re.search(r"\son\w+\s*=", text), p.name               # no inline event handlers


# --- user flow ---

def test_pages_require_sign_in(web):
    client, _, _ = web
    for path in ["/onboarding", "/company", "/connect", "/consent", "/chat"]:
        r = client.get(path)
        assert (r.status_code, r.headers["location"]) == (303, "/signin"), path
    assert client.get("/signin").status_code == 200
    assert client.get("/").headers["location"] == "/signin"


def test_company_website_flow_to_consent(web):
    client, _, _ = web
    assert signed_up(client).json()["next"] == "/onboarding"
    assert post(client, "/api/profile", {"account_type": "company"}).json()["next"] == "/company"
    for other in ["webapp_api", "source_code", "server_tool", "mobile_desktop"]:
        r = post(client, "/api/asset", {"asset": other})
        assert (r.status_code, r.json()["error"]) == (400, "coming soon")
    assert post(client, "/api/asset", {"asset": "website"}).json()["next"] == "/connect"
    assert post(client, "/api/connect", {"connect": True}).json()["next"] == "/consent"
    assert post(client, "/api/consent", {"allow": False}).status_code == 400
    assert post(client, "/api/consent", {"allow": True}).json()["next"] == "/chat"
    user = client.get("/api/session").json()["user"]
    assert user == {"email": EMAIL, "account_type": "company", "asset": "website", "connect": True, "consented": True}
    assert client.get("/signin").headers["location"] == "/chat"  # signed-in users skip sign-in


@pytest.mark.parametrize("steps", [
    [("/api/profile", {"account_type": "individual"})],
    [("/api/profile", {"account_type": "company"}), ("/api/asset", {"asset": "website"}),
     ("/api/connect", {"connect": False})],
], ids=["individual", "company-no"])
def test_individual_and_no_lead_to_chat_placeholder(web, steps):
    client, _, _ = web
    signed_up(client)
    for path, body in steps:
        r = post(client, path, body)
    assert r.json()["next"] == "/chat"
    page = client.get("/chat")
    assert page.status_code == 200 and "Chat coming next" in page.text


def test_out_of_order_steps_refused(web):
    client, _, _ = web
    signed_up(client)
    post(client, "/api/profile", {"account_type": "individual"})
    assert post(client, "/api/asset", {"asset": "website"}).status_code == 409
    assert post(client, "/api/consent", {"allow": True}).status_code == 409


def test_consent_page_lists_will_and_will_not():
    text = (STATIC_DIR / "consent.html").read_text(encoding="utf-8")
    for phrase in ["HTTP response headers", "cookies", ".env, .git and backups", "DNS records",
                   "Run exploits", "Brute force", "without your approval", 'id="consent-allow"']:
        assert phrase in text, phrase


def test_coming_soon_items_are_not_buttons():
    text = (STATIC_DIR / "company.html").read_text(encoding="utf-8")
    main = text[text.index("<main"):text.index("</main>")]  # the corner also has theme and Sign out buttons
    assert main.count("<button") == 1 and 'id="asset-website"' in main
    assert main.count("coming soon") == 4 and main.count('class="option is-soon" aria-disabled="true"') == 4


def test_signout_issues_fresh_anonymous_session(web):
    client, _, _ = web
    signed_up(client)
    signed_in_token = client.cookies["maya_session"]
    r = post(client, "/api/signout", {})
    assert r.status_code == 200 and r.json()["next"] == "/signin"
    assert client.cookies["maya_session"] != signed_in_token
    assert client.get("/api/session").json()["user"] is None
    # the returned CSRF token belongs to the new session, so the client can sign in again without a reload
    again = client.post("/api/signin", json={"email": EMAIL, "password": PASSWORD},
                        headers={"X-CSRF-Token": r.json()["csrf"]})
    assert again.status_code == 200


# --- test-only preview route ---

@pytest.mark.parametrize("path", ["/__preview/chat.html", "/__preview/consent.html", "/__preview/signin.html"])
def test_preview_route_refused_by_default(web, path):
    client, _, _ = web
    assert client.get(path).status_code == 404


def test_preview_route_only_in_preview_mode(tmp_path):
    server = make_server(port=0, data_dir=tmp_path, ui_preview=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_address[1]}", trust_env=False) as c:
            r = c.get("/__preview/chat.html")
            assert r.status_code == 200 and 'id="composer"' in r.text
            assert r.headers["content-security-policy"] == SECURITY_HEADERS["Content-Security-Policy"]
            for bad in ["/__preview/app.js", "/__preview/../auth.py", "/__preview/style.css", "/__preview/x.html"]:
                assert c.get(bad).status_code == 404, bad  # page HTML only
            assert c.get("/api/session").json()["user"] is None  # preview grants no session or data
            assert c.get("/chat").headers["location"] == "/signin"  # normal routes still need sign-in
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("value", [1, "yes", "1", None])
def test_preview_mode_needs_explicit_true(tmp_path, value):
    server = make_server(port=0, data_dir=tmp_path, ui_preview=value)
    try:
        assert server.RequestHandlerClass.app.ui_preview is False
    finally:
        server.server_close()


# --- automated UI flow script ---

def test_ui_flow_check_script(capsys):
    from scripts import ui_flow_check
    assert ui_flow_check.main([]) == 0
    out = capsys.readouterr().out
    assert "ALL CHECKS PASSED" in out and "FAIL" not in out
    assert "(throwaway)" in out and str(Path("data") / "web.db") not in out


def test_body_limits(web):
    client, _, _ = web
    token = csrf(client)
    big = client.post("/api/signin", content=b"x" * 20000,
                      headers={"X-CSRF-Token": token, "Content-Type": "application/json"})
    assert big.status_code == 413
    form = client.post("/api/signin", content=b"a=b", headers={"X-CSRF-Token": token,
                                                                 "Content-Type": "application/x-www-form-urlencoded"})
    assert form.status_code == 415
