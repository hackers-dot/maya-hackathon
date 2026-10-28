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
from src.web.chat import (CHAT_TOKEN_CAP, LABEL_UNVERIFIED, MAX_CHARS, MAX_TOKENS, ChatError, ChatStore,
                          build_messages, clean_user_text, findings_context, label_reply)
from src.web.auth import DEFAULT_DB, SESSION_TTL, Accounts, AuthError, RateLimited, Sessions
from src.web.runs import RunBusy, RunManager, findings_view, proposal_view, retest_view, run_view

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
RUN_ACTION = re.compile(r"^/api/runs/([0-9a-f]{24})/(apply|retest|approve-all)$")
CHAT_PATH = re.compile(r"^/api/chats/([0-9a-f]{16})$")
CHAT_SEND = re.compile(r"^/api/chats/([0-9a-f]{16})/messages$")
APPROVE = re.compile(r"^/api/runs/([0-9a-f]{24})/proposals/(fix-[a-z_]{3,60})/approve$")
AUDIT_ENTRIES_SHOWN = 12
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


def _preview_extras(findings: list[dict]) -> dict:
    """Detail texts, proposals, re-test, audit and usage for the preview sample, built from the same fixed
    templates the real views use (src/report.py, src/fixes.py)."""
    from src.fixes import PLANS
    from src.report import TEXT, owasp_label
    for f in findings:
        t = TEXT[f["check"]]
        f.update(plain=t.plain, analogy=t.analogy, technical=t.technical, fix=t.fix, owasp=owasp_label(f["check"]))
    statuses = ["APPLIED", "APPLIED", "APPROVED", "PROPOSED"]
    proposals = []
    for f, status in zip(findings, statuses):
        p = PLANS[f["check"]]
        proposals.append({"id": f"fix-{f['check']}", "check_id": f["check"], "finding_ids": [f["id"]], "kind": p.kind,
                          "nginx": p.nginx, "apache": p.apache, "python_note": p.python_note, "risk": p.risk,
                          "rollback": p.rollback, "status": status,
                          "note": "applied to the loopback demo app" if status == "APPLIED" else "proposed, not applied",
                          "approved_by": None if status == "PROPOSED" else "demo-user",
                          "approved_at": None, "applied_at": None})
    retest = {"status": "STILL_PRESENT", "resolved": [findings[0]["id"], findings[1]["id"]],
              "still_present": [findings[2]["id"], findings[3]["id"]], "new": [],
              "per_check": {findings[0]["check"]: "FIXED", findings[1]["check"]: "FIXED",
                            findings[2]["check"]: "STILL_PRESENT", findings[3]["check"]: "STILL_PRESENT"}}
    audit = {"intact": True, "first_bad_entry": None, "count": 5, "entries": [
        {"seq": 4, "at": "2026-10-27T10:42:31+00:00", "event": "retested", "details": {"status": "STILL_PRESENT"},
         "hash": "9f2c1a7d0b44"},
        {"seq": 3, "at": "2026-10-27T10:42:29+00:00", "event": "health_ok", "details": {}, "hash": "51e0c9a2f8d3"},
        {"seq": 2, "at": "2026-10-27T10:42:29+00:00", "event": "applied", "details": {}, "hash": "c7a3b5e19d02"},
        {"seq": 1, "at": "2026-10-27T10:42:20+00:00", "event": "approved", "details": {"approver": "demo-user"},
         "hash": "0d8e4f6a2b91"},
        {"seq": 0, "at": "2026-10-27T10:42:18+00:00", "event": "approved", "details": {"approver": "demo-user"},
         "hash": "7b13ce05a9f4"}]}
    usage = {"runs": 2, "model_runs": 1, "cap_per_run": 40000, "prompt_tokens": 8852, "completion_tokens": 913,
             "reasoning_tokens": 675, "total_tokens": 9765}
    return {"proposals": proposals, "retest": retest, "audit": audit, "usage": usage}


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
           "model": "meta-models/muse-spark-open-1026", "demo": True, "error": None, "steps": len(steps),
           "can_apply": True}
    extras = _preview_extras(findings)
    chat = {"id": "2" * 16, "title": "What should I fix first?", "messages": [
        {"role": "user", "content": "Based on my verified findings, what should I fix first?"},
        {"role": "assistant", "content":
            "Start with the exposed configuration file [exposed_env_file:/.env]: anyone can download it, so block "
            "it and rotate the keys it held.\nNext, remove the downloadable database backup "
            "[exposed_backup_file:/db.sql].\nAfter that, add a Content-Security-Policy header; it limits what an "
            "injected script could do.",
         "meta": {"model": "meta-models/muse-spark-open-1026", "label": "unverified model text",
                  "cited": ["exposed_backup_file:/db.sql", "exposed_env_file:/.env"], "cited_unknown": [],
                  "tokens": {"prompt": 912, "completion": 188, "reasoning": 120, "total": 1100}}},
        {"role": "user", "content": "Kya MAYA kisi aur ki website scan kar sakta hai?"},
        {"role": "assistant", "content":
            "Nahi. MAYA sirf un websites ko check karta hai jo aapki hain aur jinki ownership verify ho chuki hai. "
            "Kisi aur ki site scan karna galat hai; uske liye owner ki likhit permission chahiye.",
         "meta": {"model": "meta-models/muse-spark-open-1026", "label": "unverified model text", "cited": [],
                  "cited_unknown": [], "tokens": {"prompt": 1040, "completion": 96, "reasoning": 70, "total": 1136}}},
    ]}
    return {
        "extras": extras,
        "chat": {"chat": chat, "list": [{"id": chat["id"], "title": chat["title"]},
                                        {"id": "3" * 16, "title": "Explain a missing CSP header"}],
                 "status": {"model_configured": True, "model": "meta-models/muse-spark-open-1026"}},
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
                     "result": {"findings": findings, "fix_first": findings[:3],
                                "order_note": "ordered by the model's priority",
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
                 provider_factory=None, http_transport=None, chat_provider_factory=None,
                 chat_token_cap: int | None = None):
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
        self.chat_provider_factory = chat_provider_factory or self.provider_factory
        self.chats = ChatStore(db, clock)
        self.chat_token_cap = chat_token_cap or CHAT_TOKEN_CAP
        self.http_transport = http_transport  # tests inject a fake site for ownership checks; None = real HTTP
        self._demos: dict[int, object] = {}   # user id -> that user's own demo app (fixes never leak between users)
        self._gates: dict[int, object] = {}   # user id -> ApprovalGate with that user's audit log + control file
        self._demo_lock = threading.Lock()

    def demo_control(self, user_id: int) -> Path:
        return self.data_dir / "demo" / f"control-{int(user_id)}.json"

    def ensure_demo(self, user_id: int = 0) -> str:
        """Start this user's demo app on a free 127.0.0.1 port the first time it is needed.
        Its fixes are driven by a per-user control file that only the approval gate writes."""
        from demo_app.app import make_server as make_demo
        with self._demo_lock:
            if user_id not in self._demos:
                self.demo_control(user_id).parent.mkdir(parents=True, exist_ok=True)
                demo = make_demo(port=0, control_path=self.demo_control(user_id))  # 127.0.0.1 only (enforced)
                threading.Thread(target=demo.serve_forever, daemon=True, name=f"demo-app-{user_id}").start()
                self._demos[user_id] = demo
            return f"http://127.0.0.1:{self._demos[user_id].server_address[1]}"

    def demo_port(self, user_id: int = 0) -> int | None:
        demo = self._demos.get(user_id)
        return demo.server_address[1] if demo else None

    def gate(self, user_id: int):
        """The existing approval gate + hash-chained audit log, one per user."""
        from src.approval import ApprovalGate, AuditLog
        with self._demo_lock:
            if user_id not in self._gates:
                audit = AuditLog(self.data_dir / "audit" / f"user-{int(user_id)}.jsonl")
                self._gates[user_id] = ApprovalGate(audit, self.demo_control(user_id))
            return self._gates[user_id]

    def close(self):
        with self._demo_lock:
            for demo in self._demos.values():
                demo.shutdown()
                demo.server_close()
            self._demos.clear()


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
        if path in ("/api/assets", "/api/audit", "/api/usage", "/api/chats"):
            if user is None:
                raise Reply(401, "please sign in")
            if path == "/api/assets":
                self._json(200, {"assets": self._assets_view(user)})
            elif path == "/api/audit":
                self._json(200, self._audit_view(user))
            elif path == "/api/chats":
                self._json(200, {"chats": self.app.chats.list(user["id"]), **self._chat_status(user)})
            else:
                self._json(200, self.app.runs.usage_for(user["id"]))
            return True
        chat_m = CHAT_PATH.match(path)
        if chat_m:
            if user is None:
                raise Reply(401, "please sign in")
            chat = self.app.chats.get(user["id"], chat_m.group(1))  # another user's chat is simply "not found"
            if chat is None:
                raise Reply(404, "not found")
            self._json(200, {"chat": chat, **self._chat_status(user)})
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
            self._json(200, self._run_full(run))
        return True

    def _run_full(self, run) -> dict:
        return {"run": run_view(run), "result": run.result,
                "proposals": [proposal_view(p) for p in run.proposals.values()],
                "retest": retest_view(run.retest) if run.retest else None}

    def _audit_view(self, user) -> dict:
        audit = self.app.gate(user["id"]).audit
        intact, bad = audit.verify()
        try:
            entries = audit._entries()
        except ValueError:
            entries = []
        last = [{"seq": e.get("seq"), "at": e.get("at"), "event": e.get("event"), "details": e.get("details"),
                 "hash": (e.get("hash") or "")[:12]} for e in entries[-AUDIT_ENTRIES_SHOWN:]][::-1]
        return {"intact": intact, "first_bad_entry": bad, "count": len(entries), "entries": last}

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
        run_action, approve = RUN_ACTION.match(path), APPROVE.match(path)
        chat_send = CHAT_SEND.match(path)
        known = (path in self.POSTS or path in ("/api/assets", "/api/chats")
                 or bool(action or run_action or approve or chat_send))
        try:
            raw = self._read_body()  # always drain first, so an early refusal (even a 404) never resets the connection
            if not known:
                raise Reply(404, "not found")
            session = self._session()
            if not self.app.sessions.csrf_ok(session, self.headers.get("X-CSRF-Token")):
                raise Reply(403, "missing or invalid CSRF token; reload the page")
            body = self._parse_json(raw)
            if action:
                status, payload, headers = self._post_asset_action(session, action.group(1), action.group(2))
            elif run_action or approve:
                m = run_action or approve
                status, payload, headers = self._post_run_action(
                    session, m.group(1), "approve" if approve else m.group(2), body,
                    m.group(2) if approve else None)
            elif path == "/api/assets":
                status, payload, headers = self._post_assets(session, body)
            elif path == "/api/chats":
                status, payload, headers = self._post_new_chat(session, body)
            elif chat_send:
                status, payload, headers = self._post_chat_message(session, chat_send.group(1), body)
            else:
                status, payload, headers = getattr(self, "_post_" + path.rsplit("/", 1)[1])(session, body)
            self._json(status, payload, headers)
        except Reply as r:
            self._json(r.status, {"error": r.error})
        except Exception:  # noqa: BLE001 - never drop the connection; never leak internals
            self._json(500, {"error": "internal error"})

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

    # --- chat ---
    def _chat_status(self, user) -> dict:
        provider, model = self.app.chat_provider_factory()
        return {"model_configured": provider is not None, "model": model if provider else None,
                "max_chars": MAX_CHARS}

    def _user_findings(self, user) -> list[dict]:
        labels = {a["id"]: ("MAYA demo app (loopback)" if a["kind"] == "demo" else a["host"])
                  for a in self.app.assets.list(user["id"])}
        return self.app.runs.findings_for_user(user["id"], labels)

    def _post_new_chat(self, session, body):
        user = self._require_user(session)
        title = body.get("title")
        chat = self.app.chats.create(user["id"], title if isinstance(title, str) and title.strip() else "New chat")
        return 200, {"chat": {**chat, "messages": []}, **self._chat_status(user)}, None

    def _post_chat_message(self, session, chat_id: str, body):
        from src.budget import BudgetExceeded, TokenBudget
        from src.provider import ProviderError
        user = self._require_user(session)
        chat = self.app.chats.get(user["id"], chat_id)  # another user's chat is simply "not found"
        if chat is None:
            raise Reply(404, "not found")
        provider, model = self.app.chat_provider_factory()
        if provider is None:
            raise Reply(409, "model not configured: chat needs a model key on the server")
        try:
            text = clean_user_text(body.get("content"))
        except ChatError as e:
            raise Reply(e.status, str(e)) from None
        if not self.app.chats.allow_message(user["id"]):
            raise Reply(429, "too many messages; wait a minute and try again")
        used = self.app.chats.tokens_used(user["id"])
        if used >= self.app.chat_token_cap:
            raise Reply(429, "chat token budget reached for today")
        title = text[:60] if not chat["messages"] else None
        user_msg = self.app.chats.add(user["id"], chat_id, "user", text, title=title)
        findings = self._user_findings(user)
        messages = build_messages(findings_context(findings), chat["messages"] + [user_msg])
        try:
            reply = provider.chat(messages, max_tokens=MAX_TOKENS, reasoning_effort="low")
        except ProviderError as e:  # already key-scrubbed by the provider
            raise Reply(502, f"model call failed: {e}") from None
        usage = reply.usage or {}
        budget = TokenBudget(f"chat-{user['id']}", cap=max(1, self.app.chat_token_cap - used),
                             path=self.app.data_dir / "usage.jsonl")
        try:
            budget.add(usage)  # recorded even when this reply crosses the cap; the next message is refused
        except (BudgetExceeded, ValueError, TypeError):
            pass  # over the cap, or a malformed usage report from the provider: the reply is still returned
        answer = reply.answer or "(the model returned no answer)"
        tokens = {"prompt": usage.get("prompt_tokens", 0), "completion": usage.get("completion_tokens", 0),
                  "reasoning": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0),
                  "total": usage.get("total_tokens", 0)}
        meta = {"model": model, "tokens": tokens, **label_reply(answer, {f["id"] for f in findings})}
        if not reply.answer:
            meta["label"] = LABEL_UNVERIFIED
        assistant = self.app.chats.add(user["id"], chat_id, "assistant", answer, meta=meta,
                                       tokens=int(tokens["total"] or 0))
        return 200, {"user_message": user_msg, "reply": assistant, "chat_id": chat_id,
                     "budget": {"used": used + int(tokens["total"] or 0), "cap": self.app.chat_token_cap}}, None

    def _post_run_action(self, session, run_id: str, action: str, body: dict, proposal_id: str | None):
        """approve / apply / retest. All gating is done by the existing ApprovalGate and retest code."""
        from src.approval import ApplyRefused
        from src.retest import retest
        user = self._require_user(session)
        run = self.app.runs.get(user["id"], run_id)  # another user's run is simply "not found"
        if run is None:
            raise Reply(404, "not found")
        if run.status == "running" or run.base is None:
            raise Reply(409, "wait for the scan to finish")
        gate = self.app.gate(user["id"])
        if action == "approve-all":
            pending = [p for p in run.proposals.values() if p.status == "PROPOSED"]
            if not pending:
                raise Reply(409, "nothing left to approve")
            for p in pending:  # each approval goes through the gate and is audited on its own
                try:
                    gate.approve(p, body.get("approver"))
                except ValueError as e:
                    raise Reply(400 if "approver" in str(e) else 409, str(e)) from None
        elif action == "approve":
            proposal = run.proposals.get(proposal_id)
            if proposal is None:
                raise Reply(404, "not found")
            try:
                gate.approve(proposal, body.get("approver"))
            except ValueError as e:
                raise Reply(400 if "approver" in str(e) else 409, str(e)) from None
        elif action == "apply":
            ids = body.get("proposal_ids")
            if ids is None:
                ids = [p.id for p in run.proposals.values() if p.status == "APPROVED"]
            if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
                raise Reply(400, "proposal_ids must be a list of ids")
            if not ids:
                raise Reply(409, "approve at least one fix first")
            if any(i not in run.proposals for i in ids):
                raise Reply(404, "not found")
            try:
                gate.apply([run.proposals[i] for i in ids], run.base)  # loopback-only and APPROVED-only, audited
            except ApplyRefused as e:
                raise Reply(409, str(e) if run.demo else "fixes can be applied only to the loopback demo app") \
                    from None
        else:
            client = self.app.runs.client_for(run, self._store())
            try:
                run.retest = retest(client, run.base, dict(run.findings))
            finally:
                client.close()
            gate.audit.append("retested", run=run.id, status=run.retest["status"],
                              resolved=len(run.retest["resolved"]), still_present=len(run.retest["still_present"]))
        return 200, self._run_full(run), None

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
                clock=None, ui_preview: bool = False, provider_factory=None, http_transport=None,
                chat_provider_factory=None, chat_token_cap: int | None = None) -> MayaServer:
    if host != HOST:
        raise ValueError(f"MAYA web binds to {HOST} only; refused {host!r}")
    app = App(data_dir, session_ttl, clock or time.time, ui_preview=ui_preview is True,
              provider_factory=provider_factory, http_transport=http_transport,
              chat_provider_factory=chat_provider_factory, chat_token_cap=chat_token_cap)
    handler = type("Handler", (WebHandler,), {"app": app})
    server = MayaServer((HOST, port), handler)
    server.app = app
    return server
