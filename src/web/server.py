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

import threading

from src.web.assets import AssetError, Assets, public_asset
from src.web.auth import DEFAULT_DB, SESSION_TTL, Accounts, AuthError, RateLimited, Sessions
from src.web.runs import RunBusy, RunManager, findings_view, run_view

ROOT = Path(__file__).resolve().parents[2]
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
    "/connect-website": ("connect-website.html", True),
    "/chat": ("chat.html", True),
}
ASSET_ACTION = re.compile(r"^/api/assets/([0-9a-f]{16})/(verify|scan)$")
RUN_PATH = re.compile(r"^/api/runs/([0-9a-f]{24})(/trace)?$")
# what the user has; only Website works now
ASSETS = {"website": True, "webapp_api": False, "source_code": False, "server_tool": False, "mobile_desktop": False}


def next_page(user: dict | None, has_assets: bool = False) -> str:
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
    if user["connect"] and not has_assets:
        return "/connect-website"
    return "/chat"


def public_user(user: dict | None) -> dict | None:
    if user is None:
        return None
    return {"email": user["email"], "account_type": user["account_type"], "asset": user["asset"],
            "connect": user["connect"], "consented": user["consent_at"] is not None}


PREVIEW_PREFIX = "/__preview/"  # test-only: exists only when the server is started with ui_preview=True


def _preview_sample() -> dict:
    """Fixed sample data for UI screenshots (preview mode only). Not from any real run or account."""
    tok = {"prompt": 1840, "completion": 212, "reasoning": 160}
    steps = [
        {"step": 1, "tool": "run_check", "args": '{"check_id": "exposed_env_file", "path": "/.env"}',
         "result_summary": "1 finding(s)", "tokens": tok, "latency": 2.41},
        {"step": 1, "tool": "run_check", "args": '{"check_id": "exposed_backup_file", "path": "/db.sql"}',
         "result_summary": "1 finding(s)", "tokens": tok, "latency": 2.41},
        {"step": 2, "tool": "run_check", "args": '{"check_id": "missing_csp", "path": "/"}',
         "result_summary": "1 finding(s)", "tokens": {"prompt": 2310, "completion": 188, "reasoning": 141},
         "latency": 1.97},
        {"step": 3, "tool": None, "args": None, "result_summary": "forced final step: final answer",
         "tokens": {"prompt": 2702, "completion": 301, "reasoning": 233}, "latency": 3.12, "forced": True},
    ]
    findings = [
        {"id": "exposed_env_file:/.env", "check": "exposed_env_file", "severity": "critical", "location": "/.env",
         "title": "Configuration file appears publicly readable", "evidence_line": "API_KEY=fak...al"},
        {"id": "exposed_backup_file:/db.sql", "check": "exposed_backup_file", "severity": "high",
         "location": "/db.sql", "title": "Backup file appears publicly downloadable",
         "evidence_line": "INSERT INTO products VALUES (1, 'Demo mug', 9.99);"},
        {"id": "missing_csp:/", "check": "missing_csp", "severity": "medium", "location": "/",
         "title": "Content-Security-Policy header not detected",
         "evidence_line": "recorded response headers: content-length, content-type, server, set-cookie"},
        {"id": "server_version_disclosure:/:server", "check": "server_version_disclosure", "severity": "low",
         "location": "/", "title": "Software version detected in response headers",
         "evidence_line": "server: DemoShop/1.4.2 Python/3.10"},
    ]
    run = {"id": "0" * 24, "asset_id": "0" * 16, "mode": "model", "mode_label": "Model-guided scan",
           "model": "meta-models/muse-spark-open-1026", "demo": True, "error": None, "steps": len(steps)}
    return {
        "assets": [
            {"id": "0" * 16, "kind": "demo", "label": "Demo app (loopback, no verification needed)", "origin": None,
             "status": "loopback", "reason": None, "scannable": True, "last_run": "0" * 24},
            {"id": "1" * 16, "kind": "website", "label": "shop.example", "origin": "https://shop.example",
             "status": "pending", "reason": None, "scannable": False, "last_run": None,
             "verification": {"token": "maya-verify-PREVIEWxSAMPLExTOKENx0000000000",
                              "meta_tag": '<meta name="maya-verify" content="maya-verify-PREVIEWxSAMPLExTOKENx0000000000">',
                              "file_path": "/.well-known/maya-verify.txt",
                              "file_url": "https://shop.example/.well-known/maya-verify.txt"}},
        ],
        "running": {"run": {**run, "status": "running"}, "steps": steps[:3], "next": 3, "findings": findings[:3]},
        "finished": {"run": {**run, "status": "forced_final"}, "steps": steps, "next": 4, "findings": findings,
                     "result": {"findings": findings, "order_note": "ordered by the model's priority",
                                "claims": [
                                    {"priority": 1, "severity": "critical", "text": "/.env on the demo app serves a "
                                     "file that should not be public (exposed_env_file). Severity: critical.",
                                     "evidence_line": "API_KEY=fak...al",
                                     "note": "Block /.env and rotate the keys it holds.", "note_dropped": None},
                                    {"priority": 2, "severity": "medium", "text": "/ on the demo app is missing a "
                                     "security header (missing_csp). Severity: medium.",
                                     "evidence_line": "recorded response headers: content-length, content-type",
                                     "note": None, "note_dropped": "note mentions '/admin', which is not in this "
                                     "finding's evidence"}],
                                "dropped": [{"finding_id": "exposed_git_dir:/.git/HEAD", "reason": "unknown finding_id"}],
                                "usage": {"prompt_tokens": 8852, "completion_tokens": 913, "reasoning_tokens": 675,
                                          "total_tokens": 9765},
                                "model_finished": True, "stop_reason": "forced_final"}},
    }


PREVIEW_SAMPLE = _preview_sample()


def default_provider_factory():
    """(provider, model name) when a model key is configured, else (None, "none") for deterministic-only scans."""
    from src.provider import Provider, key_configured, load_config
    cfg = load_config(ROOT / "config.yaml")
    if not key_configured(cfg["api_key_env"]):
        return None, "none"
    return Provider.from_config(ROOT / "config.yaml", reasoning_effort="low"), cfg["model"]


class App:
    def __init__(self, data_dir: Path | None, session_ttl: float, clock, ui_preview: bool = False,
                 provider_factory=None, http_transport=None):
        self.data_dir = Path(data_dir) if data_dir else DEFAULT_DB.parent
        self.data_dir.mkdir(parents=True, exist_ok=True)
        db = self.data_dir / "web.db"
        self.accounts = Accounts(db, clock=clock)
        self.sessions = Sessions(ttl=session_ttl, clock=clock)
        self.assets = Assets(db, self.data_dir / "verified_targets.json", clock)
        self.runs = RunManager(self)
        self.clock = clock
        self.ui_preview = ui_preview
        self.provider_factory = provider_factory or default_provider_factory
        self.http_transport = http_transport  # tests inject a fake site for ownership checks; None = real HTTP
        self._demo = None
        self._demo_lock = threading.Lock()

    def ensure_demo(self) -> str:
        """Start MAYA's own demo app on a free 127.0.0.1 port the first time a demo scan needs it."""
        from demo_app.app import make_server as make_demo
        with self._demo_lock:
            if self._demo is None:
                self._demo = make_demo(port=0)  # 127.0.0.1 only, enforced by the demo app
                threading.Thread(target=self._demo.serve_forever, daemon=True, name="demo-app").start()
            return f"http://127.0.0.1:{self._demo.server_address[1]}"

    def demo_port(self) -> int | None:
        return self._demo.server_address[1] if self._demo else None

    def close(self):
        with self._demo_lock:
            if self._demo is not None:
                self._demo.shutdown()
                self._demo.server_close()
                self._demo = None


class MayaServer(ThreadingHTTPServer):
    daemon_threads = True
    app: App = None

    def server_close(self):
        if self.app is not None:
            self.app.close()  # also stop the on-demand demo app
        super().server_close()


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

    def _next(self, user) -> str:
        return next_page(user, bool(user and self.app.assets.list(user["id"])))

    def _assets_view(self, user) -> list[dict]:
        return [public_asset(a, self.app.runs.last_for_asset(user["id"], a["id"]))
                for a in self.app.assets.list(user["id"])]

    def _get_api(self, path: str, user) -> bool:
        """Signed-in JSON reads. Returns False if the path is not one of them."""
        if path == "/api/assets":
            if user is None:
                raise Reply(401, "please sign in")
            self._json(200, {"assets": self._assets_view(user)})
            return True
        m = RUN_PATH.match(path)
        if not m:
            return False
        if user is None:
            raise Reply(401, "please sign in")
        run = self.app.runs.get(user["id"], m.group(1))  # another user's run is simply "not found"
        if run is None:
            raise Reply(404, "not found")
        store = self._store()
        if m.group(2):  # /trace?after=N
            try:
                after = max(0, int(dict(p.split("=", 1) for p in (urlsplit(self.path).query or "").split("&")
                                        if "=" in p).get("after", "0")))
            except ValueError:
                raise Reply(400, "after must be a number") from None
            steps = run.trace[after:]
            self._json(200, {"run": run_view(run), "steps": steps, "next": after + len(steps),
                             "findings": findings_view(run.findings, store)})
        else:
            self._json(200, {"run": run_view(run), "result": run.result})
        return True

    def _store(self):
        from src.evidence import EvidenceStore
        return EvidenceStore(self.app.data_dir / "evidence.db")

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
            return self._redirect(self._next(user))
        try:
            if self._get_api(path, user):
                return None
        except Reply as r:
            return self._json(r.status, {"error": r.error})
        if path == PREVIEW_PREFIX + "sample.json":
            if not self.app.ui_preview:
                return self._json(404, {"error": "not found"})
            return self._json(200, PREVIEW_SAMPLE)
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
                return self._redirect(self._next(user))
            if path == "/connect-website" and not (user["connect"] and user["consent_at"]):
                return self._redirect(self._next(user))  # consent first
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
        action = ASSET_ACTION.match(path)
        if path not in self.POSTS and path != "/api/assets" and not action:
            return self._json(404, {"error": "not found"})
        try:
            raw = self._read_body()  # always drain first, so an early refusal does not reset the connection
            session = self._session()
            if not self.app.sessions.csrf_ok(session, self.headers.get("X-CSRF-Token")):
                raise Reply(403, "missing or invalid CSRF token; reload the page")
            body = self._parse_json(raw)
            if action:
                status, payload, headers = self._post_asset_action(session, action.group(1), action.group(2))
            elif path == "/api/assets":
                status, payload, headers = self._post_assets(session, body)
            else:
                status, payload, headers = getattr(self, "_post_" + path.rsplit("/", 1)[1])(session, body)
            self._json(status, payload, headers)
        except Reply as r:
            self._json(r.status, {"error": r.error})

    def _post_assets(self, session, body):
        user = self._require_user(session)
        if not (user["account_type"] == "company" and user["connect"] and user["consent_at"]):
            raise Reply(409, "give consent before connecting a website")
        try:
            # {"demo": true} picks MAYA's demo app without the page having to spell a loopback URL
            url = "http://127.0.0.1" if body.get("demo") is True else body.get("url")
            asset = self.app.assets.add(user["id"], url)
        except AssetError as e:
            raise Reply(400, str(e)) from None
        return 200, {"asset": public_asset(asset, self.app.runs.last_for_asset(user["id"], asset["id"]))}, None

    def _post_asset_action(self, session, asset_id: str, action: str):
        user = self._require_user(session)
        asset = self.app.assets.get(user["id"], asset_id)  # another user's asset is simply "not found"
        if asset is None:
            raise Reply(404, "not found")
        if action == "verify":
            asset = self.app.assets.verify(user["id"], asset_id, store=self._store(),
                                           transport=self.app.http_transport)
            return 200, {"asset": public_asset(asset, self.app.runs.last_for_asset(user["id"], asset_id))}, None
        if asset["status"] not in ("verified", "loopback"):
            raise Reply(409, "verify ownership before scanning")
        try:
            run = self.app.runs.start(user["id"], asset)
        except RunBusy as e:
            raise Reply(409, str(e)) from None
        return 200, {"run": run_view(run)}, None

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
        return 200, {"ok": True, "next": self._next(self.app.accounts.get(user_id)), "csrf": new.csrf}, \
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
        return 200, {"ok": True, "next": self._next(self.app.accounts.get(user_id))}, None

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
                clock=None, ui_preview: bool = False, provider_factory=None, http_transport=None) -> MayaServer:
    if host != HOST:
        raise ValueError(f"MAYA web binds to {HOST} only; refused {host!r}")
    app = App(data_dir, session_ttl, clock or time.time, ui_preview=ui_preview is True,
              provider_factory=provider_factory, http_transport=http_transport)
    handler = type("Handler", (WebHandler,), {"app": app})
    server = MayaServer((HOST, port), handler)
    server.app = app
    return server
