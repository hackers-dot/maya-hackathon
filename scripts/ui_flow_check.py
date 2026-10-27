"""Automated UI flow check (no browser): drives the MAYA web app with urllib, a cookie jar and CSRF tokens.

Default: starts its own server on a free 127.0.0.1 port with a throwaway data directory (never data/).
--base URL: drive an already running server instead (start it with run_web.py --data-dir <tmp>).
Prints one line per check and exits 1 if any check fails.
"""
import argparse
import http.cookiejar
import json
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.web.server import SECURITY_HEADERS, make_server  # noqa: E402

PASSWORD = "ui-flow-check-password"  # throwaway account in a throwaway data dir


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # report 3xx as-is so the check can see where the server sends the user


class Browserless:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),  # never route through environment proxies
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), NoRedirect)
        self.csrf = None

    def request(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
            req.add_header("X-CSRF-Token", self.csrf or "")
        try:
            with self.opener.open(req, timeout=10) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def session(self):
        status, headers, raw = self.request("GET", "/api/session")
        self.csrf = json.loads(raw)["csrf"]
        return status, headers, json.loads(raw)

    def post(self, path: str, body: dict):
        if self.csrf is None:
            self.session()
        status, headers, raw = self.request("POST", path, body)
        data = json.loads(raw or b"{}")
        if isinstance(data.get("csrf"), str):
            self.csrf = data["csrf"]
        return status, headers, data


class Report:
    def __init__(self):
        self.failures = 0

    def check(self, ok: bool, label: str, detail: str = ""):
        self.failures += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'}  {label}{('  [' + detail + ']') if detail else ''}", flush=True)


def has_security_headers(headers: dict) -> bool:
    lower = {k.lower(): v for k, v in headers.items()}
    return all(lower.get(k.lower()) == v for k, v in SECURITY_HEADERS.items())


def page_ok(b: Browserless, rep: Report, path: str, marker: str):
    status, headers, raw = b.request("GET", path)
    rep.check(status == 200 and has_security_headers(headers) and marker in raw.decode("utf-8", "replace"),
              f"GET {path} renders with security headers", f"HTTP {status}")


def run_checks(base: str) -> int:
    rep = Report()
    b = Browserless(base)
    status, headers, _ = b.request("GET", "/health")
    rep.check(status == 200 and has_security_headers(headers), "GET /health", f"HTTP {status}")
    status, headers, _ = b.request("GET", "/chat")
    rep.check(status == 303 and headers.get("Location") == "/signin", "signed-out /chat redirects to /signin",
              f"HTTP {status} -> {headers.get('Location')}")
    page_ok(b, rep, "/signin", 'id="signin-form"')

    # --- company path ---
    email = "company@flow.example"
    status, _, data = b.post("/api/signup", {"email": email, "password": PASSWORD})
    rep.check(status == 200 and data.get("next") == "/onboarding", "sign up", f"HTTP {status} next={data.get('next')}")
    status, _, data = b.post("/api/signout", {})
    rep.check(status == 200, "sign out", f"HTTP {status}")
    status, _, data = b.post("/api/signin", {"email": email, "password": "definitely-wrong-pw"})
    rep.check(status == 401, "wrong password refused", f"HTTP {status} {data.get('error')}")
    status, _, data = b.post("/api/signin", {"email": email, "password": PASSWORD})
    rep.check(status == 200 and data.get("next") == "/onboarding", "sign in", f"HTTP {status} next={data.get('next')}")
    page_ok(b, rep, "/onboarding", 'id="choose-company"')
    status, _, data = b.post("/api/connect", {"connect": True})
    rep.check(status == 409, "out-of-order step refused (connect before choosing)", f"HTTP {status}")
    status, _, data = b.post("/api/consent", {"allow": True})
    rep.check(status == 409, "out-of-order step refused (consent before connect)", f"HTTP {status}")
    status, _, data = b.post("/api/profile", {"account_type": "company"})
    rep.check(data.get("next") == "/company", "choose Company", f"HTTP {status} next={data.get('next')}")
    page_ok(b, rep, "/company", 'id="asset-website"')
    status, _, data = b.post("/api/asset", {"asset": "source_code"})
    rep.check(status == 400 and data.get("error") == "coming soon", "non-website asset is 'coming soon'",
              f"HTTP {status}")
    status, _, data = b.post("/api/asset", {"asset": "website"})
    rep.check(data.get("next") == "/connect", "choose Website", f"HTTP {status} next={data.get('next')}")
    page_ok(b, rep, "/connect", 'id="connect-yes"')
    status, _, data = b.post("/api/connect", {"connect": True})
    rep.check(data.get("next") == "/consent", "connect: Yes", f"HTTP {status} next={data.get('next')}")
    page_ok(b, rep, "/consent", 'id="consent-allow"')
    status, _, data = b.post("/api/consent", {"allow": True})
    rep.check(data.get("next") == "/chat", "consent: Allow", f"HTTP {status} next={data.get('next')}")
    page_ok(b, rep, "/chat", 'id="composer"')
    _, _, s = b.session()
    rep.check(s["user"] == {"email": email, "account_type": "company", "asset": "website", "connect": True,
                            "consented": True}, "server state after the company path", json.dumps(s["user"]))
    saved = b.csrf
    b.csrf = "forged-token"
    status, _, data = b.post("/api/profile", {"account_type": "individual"})
    rep.check(status == 403, "POST with a wrong CSRF token refused", f"HTTP {status}")
    b.csrf = saved

    # --- individual path (fresh browser) ---
    b2 = Browserless(base)
    status, _, data = b2.post("/api/signup", {"email": "individual@flow.example", "password": PASSWORD})
    rep.check(status == 200, "sign up (individual)", f"HTTP {status}")
    status, _, data = b2.post("/api/profile", {"account_type": "individual"})
    rep.check(data.get("next") == "/chat", "choose Individual -> chat", f"HTTP {status} next={data.get('next')}")
    page_ok(b2, rep, "/chat", "Chat coming next")

    print(f"\n{'ALL CHECKS PASSED' if rep.failures == 0 else str(rep.failures) + ' CHECK(S) FAILED'}")
    return 1 if rep.failures else 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--base", help="drive an already running server, e.g. http://127.0.0.1:8090")
    args = p.parse_args(argv)
    if args.base:
        return run_checks(args.base)
    data_dir = Path(tempfile.mkdtemp(prefix="maya-ui-flow-"))  # throwaway, removed below
    server = make_server(port=0, data_dir=data_dir)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"server: {base}  data dir: {data_dir} (throwaway)")
    try:
        return run_checks(base)
    finally:
        server.shutdown()
        server.server_close()
        shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
