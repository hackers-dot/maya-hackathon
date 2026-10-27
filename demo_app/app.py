"""MAYA demo shop: a DELIBERATELY VULNERABLE app for local testing only.

EVERY secret in this file is FAKE (for example "fake-demo-key-not-real"). None of them is a real
credential, and none of them works anywhere.

It binds to 127.0.0.1 only; any other host is refused in code.

Intentional weaknesses (default mode):
  - no Content-Security-Policy / HSTS / X-Frame-Options / X-Content-Type-Options
  - session cookie without Secure, HttpOnly, SameSite
  - Server header revealing a version
  - /.env served with FAKE values
  - directory listing on /backup/
  - a downloadable database dump at /db.sql (FAKE content)

Each weakness has an independent fix keyed by MAYA's check id. --fixed turns on all of them.
--control FILE turns on the fixes listed in that JSON file ({"fixes": [...]}), re-read on every request;
this is how MAYA's approval gate applies approved fixes (src/approval.py), and a re-test proves them.

Run:  python demo_app/app.py [--port 8081] [--fixed]
"""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = "127.0.0.1"
DEFAULT_PORT = 8081

FAKE_ENV = (
    "# FAKE demo values, not real credentials\n"
    "APP_ENV=demo\n"
    "API_KEY=fake-demo-key-not-real\n"
    "DB_PASSWORD=fake-demo-password-not-real\n"
)
FAKE_BACKUP = "-- FAKE demo dump\nINSERT INTO products VALUES (1, 'Demo mug', 9.99);\n"
INDEX_HTML = "<!doctype html><title>Demo Shop</title><h1>Demo Shop</h1><p>Mugs, 9.99</p>"
LISTING_HTML = ("<!doctype html><title>Index of /backup/</title><h1>Index of /backup/</h1>"
                '<ul><li><a href="shop-db.sql.bak">shop-db.sql.bak</a></li></ul>')
COOKIE = "session=demo-session-0001; Path=/"

# Each fix is independent, keyed by the MAYA check id it resolves, so fixes can be applied one by one.
FIX_HEADERS = {
    "missing_csp": ("Content-Security-Policy", "default-src 'self'"),
    "missing_hsts": ("Strict-Transport-Security", "max-age=31536000"),
    "missing_x_frame_options": ("X-Frame-Options", "DENY"),
    "missing_x_content_type_options": ("X-Content-Type-Options", "nosniff"),
}
FIX_COOKIE = {"cookie_missing_secure": "; Secure", "cookie_missing_httponly": "; HttpOnly",
              "cookie_missing_samesite": "; SameSite=Lax"}
FIX_BLOCKED = {"exposed_env_file": {"/.env"},
               "exposed_backup_file": {"/db.sql", "/backup/shop-db.sql.bak"},
               "directory_listing": {"/backup", "/backup/"}}
ALL_FIXES = frozenset(FIX_HEADERS) | frozenset(FIX_COOKIE) | frozenset(FIX_BLOCKED) | {"server_version_disclosure"}
FIXED_CONFIG = {"fixes": sorted(ALL_FIXES)}  # what --fixed turns on


def read_control(path) -> frozenset:
    """Fix ids listed in the control file {"fixes": [...]}; unknown ids and unreadable files are ignored."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return frozenset(data.get("fixes", [])) & ALL_FIXES
    except (OSError, ValueError, AttributeError, TypeError):
        return frozenset()


class DemoHandler(BaseHTTPRequestHandler):
    fixed = False
    control_path = None  # read on every request so applied fixes take effect without a restart

    def active_fixes(self) -> frozenset:
        if self.fixed:
            return ALL_FIXES
        return read_control(self.control_path) if self.control_path else frozenset()

    def version_string(self):
        fixes = getattr(self, "_fixes", frozenset())
        return "DemoShop" if "server_version_disclosure" in fixes else "DemoShop/1.4.2 Python/3.10"

    def log_message(self, *args):
        pass  # keep test output clean

    def do_GET(self):
        self._route(send_body=True)

    def do_HEAD(self):
        self._route(send_body=False)

    def _route(self, send_body: bool):
        self._fixes = self.active_fixes()
        path = self.path.split("?", 1)[0]
        if any(path in FIX_BLOCKED[f] for f in self._fixes & FIX_BLOCKED.keys()):
            return self._send(404, "text/plain", "Not found", send_body)
        if path == "/":
            cookie = COOKIE + "".join(FIX_COOKIE[f] for f in sorted(self._fixes & FIX_COOKIE.keys()))
            return self._send(200, "text/html", INDEX_HTML, send_body, cookie)
        if path == "/.env":
            return self._send(200, "text/plain", FAKE_ENV, send_body)
        if path in ("/backup", "/backup/"):
            return self._send(200, "text/html", LISTING_HTML, send_body)
        if path in ("/backup/shop-db.sql.bak", "/db.sql"):
            return self._send(200, "application/octet-stream", FAKE_BACKUP, send_body)
        return self._send(404, "text/plain", "Not found", send_body)

    def _send(self, status, ctype, text, send_body, cookie=None):
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        for fix in sorted(self._fixes & FIX_HEADERS.keys()):
            self.send_header(*FIX_HEADERS[fix])
        self.end_headers()
        if send_body:
            self.wfile.write(data)


def make_server(port: int = DEFAULT_PORT, fixed: bool = False, host: str = HOST,
                control_path=None) -> ThreadingHTTPServer:
    if host != HOST:
        raise ValueError(f"demo app binds to {HOST} only; refused {host!r}")
    handler = type("Handler", (DemoHandler,), {"fixed": fixed, "control_path": control_path})
    return ThreadingHTTPServer((HOST, port), handler)


def main(argv=None):
    p = argparse.ArgumentParser(description="MAYA deliberately vulnerable demo shop (loopback only)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--host", default=HOST, help="must be 127.0.0.1; anything else is refused")
    p.add_argument("--fixed", action="store_true", help="apply every fix (FIXED_CONFIG)")
    p.add_argument("--control", help="JSON control file {\"fixes\": [check ids]} read on every request")
    args = p.parse_args(argv)
    server = make_server(args.port, args.fixed, args.host, args.control)
    print(f"Demo shop ({'FIXED' if args.fixed else 'VULNERABLE'}) on http://{HOST}:{server.server_address[1]}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
