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

--fixed applies FIXED_CONFIG: security headers added, cookie flags set, version hidden,
/.env, /backup/ and /db.sql return 404. A re-test against --fixed proves the fixes.

Run:  python demo_app/app.py [--port 8081] [--fixed]
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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

FIXED_CONFIG = {
    "headers": {
        "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'",
        "Strict-Transport-Security": "max-age=31536000",
        "X-Frame-Options": "DENY",
        "X-Content-Type-Options": "nosniff",
    },
    "cookie_flags": "; Secure; HttpOnly; SameSite=Lax",
    "server": "DemoShop",
    "blocked_paths": {"/.env", "/backup", "/backup/", "/backup/shop-db.sql.bak", "/db.sql"},
}


class DemoHandler(BaseHTTPRequestHandler):
    fixed = False

    def version_string(self):
        return FIXED_CONFIG["server"] if self.fixed else "DemoShop/1.4.2 Python/3.10"

    def log_message(self, *args):
        pass  # keep test output clean

    def do_GET(self):
        self._route(send_body=True)

    def do_HEAD(self):
        self._route(send_body=False)

    def _route(self, send_body: bool):
        path = self.path.split("?", 1)[0]
        if self.fixed and path in FIXED_CONFIG["blocked_paths"]:
            return self._send(404, "text/plain", "Not found", send_body)
        if path == "/":
            cookie = COOKIE + (FIXED_CONFIG["cookie_flags"] if self.fixed else "")
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
        if self.fixed:
            for k, v in FIXED_CONFIG["headers"].items():
                self.send_header(k, v)
        self.end_headers()
        if send_body:
            self.wfile.write(data)


def make_server(port: int = DEFAULT_PORT, fixed: bool = False, host: str = HOST) -> ThreadingHTTPServer:
    if host != HOST:
        raise ValueError(f"demo app binds to {HOST} only; refused {host!r}")
    handler = type("Handler", (DemoHandler,), {"fixed": fixed})
    return ThreadingHTTPServer((HOST, port), handler)


def main(argv=None):
    p = argparse.ArgumentParser(description="MAYA deliberately vulnerable demo shop (loopback only)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--host", default=HOST, help="must be 127.0.0.1; anything else is refused")
    p.add_argument("--fixed", action="store_true", help="apply FIXED_CONFIG")
    args = p.parse_args(argv)
    server = make_server(args.port, args.fixed, args.host)
    print(f"Demo shop ({'FIXED' if args.fixed else 'VULNERABLE'}) on http://{HOST}:{server.server_address[1]}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
