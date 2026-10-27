"""MAYA web app: stdlib ThreadingHTTPServer on 127.0.0.1 only. Pages + a small JSON API.

Every response carries security headers. Requests whose Host header is not 127.0.0.1 / localhost are
refused (DNS-rebinding guard). Every POST needs a session and its CSRF token (X-CSRF-Token header).
Static files come only from src/web/static; the pages render server strings with textContent only.
"""
import json
import re
import time
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from src.web.auth import DEFAULT_DB, SESSION_TTL, Accounts, AuthError, RateLimited, Sessions

HOST = "127.0.0.1"
DEFAULT_PORT = 8090
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_NAME = re.compile(r"^[a-z0-9_-]+\.(html|js|css)$")
CONTENT_TYPES = {"html": "text/html; charset=utf-8", "js": "text/javascript; charset=utf-8",
                 "css": "text/css; charset=utf-8"}
COOKIE = "maya_session"
MAX_BODY = 16 * 1024
DRAIN_MAX = 1024 * 1024  # larger bodies are not read at all; the connection is closed
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
# page path -> (static file, requires sign-in)
PAGES = {
    "/signin": ("signin.html", False),
    "/onboarding": ("onboarding.html", True),
    "/company": ("company.html", True),
    "/connect": ("connect.html", True),
    "/consent": ("consent.html", True),
    "/chat": ("chat.html", True),
}
# what the user has; only Website works now
ASSETS = {"website": True, "webapp_api": False, "source_code": False, "server_tool": False, "mobile_desktop": False}


def next_page(user: dict | None) -> str:
    if user is None:
        return "/signin"
    if user["account_type"] is None:
        return "/onboarding"
    if user["account_type"] == "individual":
        return "/chat"
    if user["asset"] != "website":
        return "/company"
    if user["connect"] is None:
        return "/connect"
    if user["connect"] and user["consent_at"] is None:
        return "/consent"
    return "/chat"


def public_user(user: dict | None) -> dict | None:
    if user is None:
        return None
    return {"email": user["email"], "account_type": user["account_type"], "asset": user["asset"],
            "connect": user["connect"], "consented": user["consent_at"] is not None}


PREVIEW_PREFIX = "/__preview/"  # test-only: exists only when the server is started with ui_preview=True


class App:
    def __init__(self, data_dir: Path | None, session_ttl: float, clock, ui_preview: bool = False):
        db = Path(data_dir) / "web.db" if data_dir else DEFAULT_DB
        self.accounts = Accounts(db, clock=clock)
        self.sessions = Sessions(ttl=session_ttl, clock=clock)
        self.clock = clock
        self.ui_preview = ui_preview


class Reply(Exception):
    """Short-circuit a handler with a JSON error."""

    def __init__(self, status: int, error: str):
        super().__init__(error)
        self.status, self.error = status, error


class WebHandler(BaseHTTPRequestHandler):
    app: App = None  # set by make_server
    server_version = "MAYA"
    sys_version = ""

    def version_string(self):
        return "MAYA"

    def log_message(self, *args):
        pass  # no request logging: nothing a user typed ever reaches a log

    def end_headers(self):  # also covers send_error, so every response gets the headers
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        super().end_headers()

    # --- helpers ---
    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        name = host[: host.rfind(":")] if ":" in host else host
        return name in ALLOWED_HOSTS

    def _send(self, status: int, body: bytes, ctype: str, headers: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, obj: dict, headers: dict | None = None):
        self._send(status, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8", headers)

    def _redirect(self, location: str, headers: dict | None = None):
        self._send(303, b"", "text/plain; charset=utf-8", {"Location": location, **(headers or {})})

    def _cookie_header(self, session) -> dict:
        return {"Set-Cookie": f"{COOKIE}={session.token}; Path=/; HttpOnly; SameSite=Strict; "
                              f"Max-Age={int(self.app.sessions.ttl)}"}

    def _session(self):
        try:
            jar = SimpleCookie(self.headers.get("Cookie") or "")
        except CookieError:
            return None
        morsel = jar.get(COOKIE)
        return self.app.sessions.get(morsel.value) if morsel else None

    def _user(self, session):
        return self.app.accounts.get(session.user_id) if session and session.user_id else None

    # --- GET ---
    def do_GET(self):
        if not self._host_ok():
            return self._json(400, {"error": "invalid Host header"})
        path = urlsplit(self.path).path
        if path == "/health":
            return self._json(200, {"status": "ok"})
        session = self._session()
        user = self._user(session)
        if path == "/":
            return self._redirect(next_page(user))
        if path == "/api/session":
            headers = {}
            if session is None:
                session = self.app.sessions.create()
                headers = self._cookie_header(session)
            return self._json(200, {"csrf": session.csrf, "user": public_user(user)}, headers)
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        if path.startswith(PREVIEW_PREFIX):
            # page HTML only (no data, no session), for screenshots; refused unless started in preview mode
            name = path[len(PREVIEW_PREFIX):]
            if not self.app.ui_preview or name not in {f for f, _ in PAGES.values()}:
                return self._json(404, {"error": "not found"})
            return self._static(name)
        if path in PAGES:
            name, needs_user = PAGES[path]
            if needs_user and user is None:
                return self._redirect("/signin")
            if path == "/signin" and user is not None:
                return self._redirect(next_page(user))
            return self._static(name)
        return self._json(404, {"error": "not found"})

    def do_HEAD(self):
        self.do_GET()

    def _static(self, name: str):
        if not STATIC_NAME.match(name) or not (STATIC_DIR / name).is_file():
            return self._json(404, {"error": "not found"})
        self._send(200, (STATIC_DIR / name).read_bytes(), CONTENT_TYPES[name.rsplit(".", 1)[1]])

    # --- POST (all state-changing) ---
    POSTS = {"/api/signup", "/api/signin", "/api/signout", "/api/profile", "/api/asset", "/api/connect",
             "/api/consent"}

    def do_POST(self):
        if not self._host_ok():
            return self._json(400, {"error": "invalid Host header"})
        path = urlsplit(self.path).path
        if path not in self.POSTS:
            return self._json(404, {"error": "not found"})
        try:
            raw = self._read_body()  # always drain first, so an early refusal does not reset the connection
            session = self._session()
            if not self.app.sessions.csrf_ok(session, self.headers.get("X-CSRF-Token")):
                raise Reply(403, "missing or invalid CSRF token; reload the page")
            body = self._parse_json(raw)
            status, payload, headers = getattr(self, "_post_" + path.rsplit("/", 1)[1])(session, body)
            self._json(status, payload, headers)
        except Reply as r:
            self._json(r.status, {"error": r.error})

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self.close_connection = True
            raise Reply(400, "bad Content-Length") from None
        if length < 0 or length > DRAIN_MAX:
            self.close_connection = True  # too big to drain: answer and hang up
            raise Reply(413, "request too large")
        raw = self.rfile.read(length)
        if length > MAX_BODY:
            raise Reply(413, "request too large")
        return raw

    def _parse_json(self, raw: bytes) -> dict:
        if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
            raise Reply(415, "expected application/json")
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            raise Reply(400, "invalid JSON") from None
        if not isinstance(data, dict):
            raise Reply(400, "expected a JSON object")
        return data

    def _signed_in(self, session, user_id: int):
        """Replace the session (no fixation) and point the client to its next page."""
        self.app.sessions.destroy(session.token)
        new = self.app.sessions.create(user_id)
        return 200, {"ok": True, "next": next_page(self.app.accounts.get(user_id)), "csrf": new.csrf}, \
            self._cookie_header(new)

    def _require_user(self, session) -> dict:
        user = self._user(session)
        if user is None:
            raise Reply(401, "please sign in")
        return user

    def _post_signup(self, session, body):
        try:
            user_id = self.app.accounts.sign_up(body.get("email"), body.get("password"))
        except AuthError as e:
            raise Reply(400, str(e)) from None
        return self._signed_in(session, user_id)

    def _post_signin(self, session, body):
        try:
            user_id = self.app.accounts.sign_in(body.get("email"), body.get("password"))
        except RateLimited as e:
            raise Reply(429, str(e)) from None
        except AuthError as e:
            raise Reply(401, str(e)) from None
        return self._signed_in(session, user_id)

    def _post_signout(self, session, body):
        """End the signed-in session and hand out a fresh anonymous one (new token and CSRF)."""
        self.app.sessions.destroy(session.token)
        anon = self.app.sessions.create()
        return 200, {"ok": True, "next": "/signin", "csrf": anon.csrf}, self._cookie_header(anon)

    def _done(self, user_id: int):
        return 200, {"ok": True, "next": next_page(self.app.accounts.get(user_id))}, None

    def _post_profile(self, session, body):
        user = self._require_user(session)
        kind = body.get("account_type")
        if kind not in ("individual", "company"):
            raise Reply(400, "choose Individual or Company")
        self.app.accounts.update(user["id"], account_type=kind, asset=None, connect=None, consent_at=None)
        return self._done(user["id"])

    def _post_asset(self, session, body):
        user = self._require_user(session)
        if user["account_type"] != "company":
            raise Reply(409, "choose Company first")
        asset = body.get("asset")
        if asset not in ASSETS:
            raise Reply(400, "unknown option")
        if not ASSETS[asset]:
            raise Reply(400, "coming soon")
        self.app.accounts.update(user["id"], asset=asset, connect=None, consent_at=None)
        return self._done(user["id"])

    def _post_connect(self, session, body):
        user = self._require_user(session)
        if user["asset"] != "website":
            raise Reply(409, "choose what you have first")
        if not isinstance(body.get("connect"), bool):
            raise Reply(400, "answer yes or no")
        self.app.accounts.update(user["id"], connect=int(body["connect"]), consent_at=None)
        return self._done(user["id"])

    def _post_consent(self, session, body):
        user = self._require_user(session)
        if not user["connect"]:
            raise Reply(409, "connect MAYA first")
        if body.get("allow") is not True:
            raise Reply(400, "consent needs an explicit Allow")
        self.app.accounts.update(user["id"], consent_at=self.app.clock())
        return self._done(user["id"])

    # anything else (PUT, DELETE, ...) is answered by BaseHTTPRequestHandler with 501, headers included


def make_server(port: int = DEFAULT_PORT, host: str = HOST, data_dir=None, session_ttl: float = SESSION_TTL,
                clock=None, ui_preview: bool = False) -> ThreadingHTTPServer:
    if host != HOST:
        raise ValueError(f"MAYA web binds to {HOST} only; refused {host!r}")
    app = App(data_dir, session_ttl, clock or time.time, ui_preview=ui_preview is True)
    handler = type("Handler", (WebHandler,), {"app": app})
    return ThreadingHTTPServer((HOST, port), handler)
