"""Automated UI flow check (no browser): drives the MAYA web app with urllib, a cookie jar and CSRF tokens.

Default: starts its own server on a free 127.0.0.1 port with a throwaway data directory (never data/).
--base URL: drive an already running server instead (start it with run_web.py --data-dir <tmp>).
Prints one line per check and exits 1 if any check fails.
"""
import argparse
import http.cookiejar
import io
import json
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.agent.verifier import SEVERITY_ORDER  # noqa: E402
from src.evidence_pack import verify_zip  # noqa: E402
from src.web.server import SECURITY_HEADERS, make_server  # noqa: E402

PASSWORD = "ui-flow-check-password"  # throwaway account in a throwaway data dir


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # report 3xx as-is so the check can see where the server sends the user


def data_asset(b, kind):
    """The id of this user's first asset of `kind`, or None."""
    _, _, raw = b.request("GET", "/api/assets")
    return next((a["id"] for a in json.loads(raw)["assets"] if a["kind"] == kind), None)


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

    def upload(self, path: str, data: bytes, name: str, ctype: str, confirm: bool = False):
        if self.csrf is None:
            self.session()
        req = urllib.request.Request(self.base + path, data=data, method="POST")
        extra = {"X-Confirm-Send": "yes"} if confirm else {}
        for k, v in {"Content-Type": ctype, "X-Upload-Name": name, "X-CSRF-Token": self.csrf or "", **extra}.items():
            req.add_header(k, v)
        try:
            with self.opener.open(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

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


def run_checks(base: str, scan_allowed: bool = False) -> int:
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
    rep.check(data.get("next") == "/connect-website", "consent: Allow", f"HTTP {status} next={data.get('next')}")
    page_ok(b, rep, "/connect-website", 'id="asset-form"')

    # --- connect website (Web 2) ---
    status, _, data = b.post("/api/assets", {"url": "https://user@shop.example"})
    rep.check(status == 400, "URL with userinfo refused", f"HTTP {status} {data.get('error')}")
    status, _, data = b.post("/api/assets", {"url": "http://2130706433"})
    rep.check(status == 400, "numeric-IP URL refused", f"HTTP {status} {data.get('error')}")
    status, _, data = b.post("/api/assets", {"url": "https://shop.example"})
    site = data.get("asset") or {}
    token = (site.get("verification") or {}).get("token", "")
    rep.check(status == 200 and site.get("status") == "pending" and token.startswith("maya-verify-"),
              "website added: pending with a verification token", f"HTTP {status} status={site.get('status')}")
    status, _, data = b.post(f"/api/assets/{site.get('id')}/scan", {})
    rep.check(status == 409, "scan refused before ownership is verified", f"HTTP {status} {data.get('error')}")
    status, _, data = b.post("/api/assets", {"url": "http://127.0.0.1:8081"})
    demo = data.get("asset") or {}
    rep.check(demo.get("status") == "loopback" and demo.get("label") == "Demo app (loopback, no verification needed)",
              "loopback URL becomes the demo app", f"HTTP {status} label={demo.get('label')}")
    page_ok(b, rep, "/chat", 'id="composer"')
    status, _, data = b.request("GET", "/api/assets")
    labels = [a["label"] for a in json.loads(data)["assets"]] if status == 200 else []
    rep.check(len(labels) == 2, "sidebar assets list", ", ".join(labels))
    run_id = None
    if scan_allowed:
        status, _, data = b.post(f"/api/assets/{demo.get('id')}/scan", {})
        run = data.get("run") or {}
        run_id = run.get("id")
        rep.check(status == 200 and run.get("mode") == "deterministic", "scan started (deterministic only)",
                  f"HTTP {status} mode={run.get('mode_label')}")
        status, _, data = b.post(f"/api/assets/{demo.get('id')}/scan", {})
        rep.check(status == 409, "second scan refused while one is running", f"HTTP {status}")
        nexts, final = [], None
        for _ in range(100):
            st, hd, raw = b.request("GET", f"/api/runs/{run_id}/trace?after={nexts[-1] if nexts else 0}")
            body = json.loads(raw)
            nexts.append(body["next"])
            if body["run"]["status"] != "running":
                final = body
                break
            time.sleep(0.2)
        rep.check(final is not None and final["run"]["status"] == "finished" and has_security_headers(hd),
                  "trace polled until finished", f"polls={len(nexts)} next={nexts[-1] if nexts else None}")
        rep.check(nexts == sorted(nexts), "trace index only grows (incremental polling)", str(nexts[:8]))
        st, _, raw = b.request("GET", f"/api/runs/{run_id}")
        result = json.loads(raw).get("result") or {}
        sev = [f["severity"] for f in result.get("findings", [])]
        rep.check(st == 200 and "critical" in sev and sev == sorted(sev, key=SEVERITY_ORDER.index),
                  "result findings, severity-sorted from the code table", f"{len(sev)} findings")
        checks = {c["check"]: c["status"] for c in result.get("checks", [])}
        na = sorted(c for c, v in checks.items() if v == "not applicable (loopback demo)")
        rep.check(na == ["missing_dmarc", "missing_spf", "weak_tls_or_expiring_cert"] and
                  checks.get("outdated_js_library") == "ran" and
                  any(f["id"] == "outdated_js_library:/:jquery" for f in result.get("findings", [])),
                  "checks ran vs not applicable (DNS/TLS on the loopback demo); old jQuery found",
                  f"ran={sum(v == 'ran' for v in checks.values())} not_applicable={na}")

        # --- Web 3: fix proposals, approval gate, apply, re-test, audit ---
        full = json.loads(raw)
        props = full.get("proposals") or []
        f0 = (result.get("findings") or [{}])[0]
        rep.check(all(f0.get(k) for k in ("plain", "analogy", "technical", "fix")) and
                  str(f0.get("owasp", "")).startswith("mapped to") and len(result.get("fix_first", [])) == 3,
                  "finding details (owner/developer/technical texts, OWASP label) and fix-first top 3",
                  f0.get("owasp", ""))
        rep.check(props and all(p["status"] == "PROPOSED" and p["note"] == "proposed, not applied" for p in props),
                  "fix proposals start PROPOSED, 'proposed, not applied'", f"{len(props)} proposals")
        status, _, data = b.post(f"/api/runs/{run_id}/apply", {})
        rep.check(status == 409, "apply refused before any approval", f"HTTP {status} {data.get('error')}")
        status, _, data = b.post(f"/api/runs/{run_id}/proposals/{props[0]['id']}/approve", {"approver": "  "})
        rep.check(status == 400, "approval without an approver name refused", f"HTTP {status}")
        sev = {f["check"]: f["severity"] for f in result.get("findings", [])}
        keys = [(SEVERITY_ORDER.index(sev[p["check_id"]]), p["check_id"]) for p in props]
        rep.check(keys == sorted(keys), "proposals sorted by severity, then check id",
                  " > ".join(p["check_id"] for p in props[:3]))
        status, _, data = b.post(f"/api/runs/{run_id}/approve-all", {"approver": "flow-check"})
        rep.check(status == 200 and {p["status"] for p in data.get("proposals", [])} == {"APPROVED"},
                  "approve all (each approval audited individually)", f"HTTP {status} {len(props)} proposals")
        status, _, data = b.post(f"/api/runs/{run_id}/apply", {})
        applied = {p["status"] for p in data.get("proposals", [])}
        rep.check(status == 200 and applied == {"APPLIED"}, "approved fixes applied to the loopback demo app",
                  f"HTTP {status} statuses={sorted(applied)}")
        status, _, data = b.post(f"/api/runs/{run_id}/retest", {})
        rt = data.get("retest") or {}
        rep.check(status == 200 and rt.get("status") == "FIXED" and not rt.get("still_present"),
                  "re-test: FIXED", f"resolved={len(rt.get('resolved', []))} still={len(rt.get('still_present', []))}")
        st, hd, raw = b.request("GET", "/api/audit")
        audit = json.loads(raw)
        rep.check(st == 200 and audit.get("intact") is True and audit["entries"][0]["event"] == "retested" and
                  audit.get("count") == 1 + len(props) + 3,
                  "audit log hash chain verified (1 refusal + one entry per approval + apply/health/retest)",
                  f"{audit.get('count')} entries")
        st, _, raw = b.request("GET", "/api/usage")
        rep.check(st == 200 and json.loads(raw).get("runs") == 1, "token usage panel data", raw.decode()[:80])
        # --- Web 5: downloads and the Quality panel ---
        st, hd, raw = b.request("GET", f"/api/runs/{run_id}/evidence.zip")
        with tempfile.TemporaryDirectory(prefix="maya-flow-pack-") as tmp:
            zpath = Path(tmp) / "pack.zip"
            zpath.write_bytes(raw)
            verdict = verify_zip(zpath) if st == 200 else {"ok": False, "problems": [f"HTTP {st}"]}
            names = []
            if st == 200:
                with zipfile.ZipFile(zpath) as z:
                    names = z.namelist()
        rep.check(st == 200 and verdict["ok"] and "custody_log.jsonl" in names,
                  "evidence pack downloads and verifies (hashes + custody chain)",
                  f"HTTP {st} {len(raw)} bytes, {len(names)} files, problems={verdict['problems']}")
        for name, ctype in (("report.html", "text/html"), ("report.md", "text/markdown")):
            st, hd, raw = b.request("GET", f"/api/runs/{run_id}/{name}")
            rep.check(st == 200 and hd.get("Content-Type", "").startswith(ctype) and
                      hd.get("Content-Disposition", "").startswith("attachment"), f"download {name}",
                      f"HTTP {st} {hd.get('Content-Type')}")
        st, _, raw = b.request("GET", "/api/eval")
        q = json.loads(raw) if st == 200 else {}
        rep.check(st == 200 and "available" in q, "Quality panel data (from docs/eval_results.json, or 'run the eval')",
                  f"available={q.get('available')} fake_claims_in_report={(q.get('totals') or {}).get('fake_claims_in_report')}")
        # --- Web 5.1: reset the demo app back to vulnerable ---
        status, _, data = b.post(f"/api/assets/{demo.get('id')}/reset-demo", {})
        st, _, raw = b.request("GET", "/api/audit")
        audit = json.loads(raw)
        rep.check(status == 200 and len(data.get("previous_fixes", [])) == 12 and audit.get("intact") and
                  audit["entries"][0]["event"] == "demo_reset", "reset demo app (audited as demo_reset)",
                  f"HTTP {status} previous_fixes={len(data.get('previous_fixes', []))}")
    else:
        print("SKIP  scan steps: --base server may be configured with a live model")
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
    page_ok(b2, rep, "/chat", 'id="chats-list"')

    # --- Web 4: chat (this check's server has no model, so chat must refuse rather than fake a reply) ---
    st, _, raw = b2.request("GET", "/api/chats")
    status_body = json.loads(raw) if st == 200 else {}
    rep.check(st == 200 and status_body.get("model_configured") is False, "chat status: model not configured",
              f"HTTP {st} model={status_body.get('model')}")
    status, _, data = b2.post("/api/chats", {})
    chat_id = (data.get("chat") or {}).get("id")
    rep.check(status == 200 and chat_id, "new chat created", f"HTTP {status}")
    status, _, data = b2.post(f"/api/chats/{chat_id}/messages", {"content": "What is a CSP header?"})
    rep.check(status == 409 and str(data.get("error", "")).startswith("model not configured"),
              "message refused without a model (no fake reply)", f"HTTP {status} {data.get('error')}")
    st, _, raw = b2.request("GET", f"/api/chats/{chat_id}")
    rep.check(st == 200 and json.loads(raw)["chat"]["messages"] == [], "nothing stored for the refused message",
              f"HTTP {st}")
    status, _, data = b2.post(f"/api/chats/{chat_id}/messages", {"content": "x" * 2001})
    rep.check(status in (400, 409), "over-long message refused", f"HTTP {status}")
    st, _, _ = b.request("GET", f"/api/chats/{chat_id}")
    rep.check(st == 404, "another user's chat is not found", f"HTTP {st}")
    if run_id:
        status, _, _ = b2.request("GET", f"/api/runs/{run_id}")
        rep.check(status == 404, "another user's run is not found", f"HTTP {status}")
        status, _, _ = b2.request("GET", f"/api/runs/{run_id}/trace?after=0")
        rep.check(status == 404, "another user's trace is not found", f"HTTP {status}")
        status, _, _ = b2.post(f"/api/runs/{run_id}/retest", {})
        rep.check(status == 404, "another user's re-test is not found", f"HTTP {status}")
        status, _, _ = b2.request("GET", f"/api/runs/{run_id}/evidence.zip")
        rep.check(status == 404, "another user's evidence pack is not found", f"HTTP {status}")
        st, _, raw = b2.request("GET", "/api/audit")
        rep.check(st == 200 and json.loads(raw).get("count") == 0, "another user sees only their own audit log",
                  f"count={json.loads(raw).get('count')}")
    status, _, _ = b2.post(f"/api/assets/{demo.get('id')}/scan", {})
    rep.check(status == 404, "another user's asset cannot be scanned", f"HTTP {status}")
    status, _, _ = b2.post(f"/api/assets/{demo.get('id')}/reset-demo", {})
    rep.check(status == 404, "another user's demo app cannot be reset", f"HTTP {status}")

    # --- Web 7: code ZIP and log uploads, disconnect, delete my data (company user) ---
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:  # FAKE secret, made up for this check
        z.writestr("app.py", 'SECRET_KEY = "fake-flow-check-secret-123456"\nDEBUG = True\n')
    status, data = b.upload("/api/uploads/code", buf.getvalue(), "app.zip", "application/zip")
    code_run = (data.get("run") or {}).get("id")
    st, _, raw = b.request("GET", f"/api/runs/{code_run}")
    found = sorted(f["id"] for f in json.loads(raw).get("result", {}).get("findings", [])) if st == 200 else []
    rep.check(status == 200 and found == ["code_debug_enabled:app.py:2", "code_hardcoded_secret:app.py:1"]
              and b"fake-flow-check-secret-123456" not in raw, "code ZIP upload: findings, secret masked",
              f"HTTP {status} {found}")
    log = "\n".join(f"Oct 28 11:00:{i:02d} web1 sshd[1]: Failed password for root from 203.0.113.7 port {i} ssh2"
                    for i in range(10)).encode()
    status, data = b.upload("/api/uploads/logs", log, "auth.log", "text/plain")
    log_run = (data.get("run") or {}).get("id")
    st, _, raw = b.request("GET", f"/api/runs/{log_run}")
    res = json.loads(raw).get("result", {}) if st == 200 else {}
    rep.check(status == 200 and [f["id"] for f in res.get("findings", [])] == ["log_failed_login_burst:ip:203.0.113.7"]
              and res.get("groups", [{}])[0].get("label") == "shares source address",
              "log upload: failed-login burst grouped by source address", f"HTTP {status}")
    status, _, data = b.post(f"/api/runs/{log_run}/summarize", {})
    rep.check(status == 409 and str(data.get("error", "")).startswith("Model not configured"),
              "optional model summary without a key: 'Model not configured'", f"HTTP {status}")
    status, _, _ = b2.request("GET", f"/api/runs/{code_run}")
    rep.check(status == 404, "another user's upload result is not found", f"HTTP {status}")

    # --- Web 8: suspicious message check (this server has no model key) ---
    status, _, data = b.post("/api/official", {"domains": ["shopexample.com"], "numbers": ["+1 555 010 0100"]})
    rep.check(status == 200 and data.get("domains") == ["sho...om"] and data.get("numbers") == ["+15...00"],
              "official domains and numbers saved per user, shown masked", f"HTTP {status} {data.get('domains')}")
    tiny_png = (b"\x89PNG\r\n\x1a\n" + bytes(32))
    status, data = b.upload("/api/uploads/image", tiny_png, "message.png", "image/png")
    rep.check(status == 400 and data.get("error") == "confirm that this image will be sent to the model provider",
              "image is not sent without the user's confirmation", f"HTTP {status}")
    status, data = b.upload("/api/uploads/image", b"GIF89a fake image", "message.png", "image/png", confirm=True)
    rep.check(status == 400 and data.get("error") == "only PNG or JPEG images are accepted",
              "image type checked by magic bytes, not the file name", f"HTTP {status}")
    status, data = b.upload("/api/uploads/image", tiny_png, "message.png", "image/png", confirm=True)
    rep.check(status == 409 and str(data.get("error", "")).startswith("Model not configured"),
              "no model key: 'Model not configured', nothing stored", f"HTTP {status}")
    status, _, _ = b.post(f"/api/assets/{data_asset(b, 'code')}/disconnect", {})
    rep.check(status == 200 and data_asset(b, "code") is None, "disconnect removes the asset", f"HTTP {status}")
    status, _, data = b.post("/api/data/delete", {"confirm": "nope"})
    rep.check(status == 400, "delete my data needs DELETE typed", f"HTTP {status}")
    status, _, data = b.post("/api/data/delete", {"confirm": "DELETE"})
    st, _, raw = b.request("GET", "/api/audit")
    audit = json.loads(raw)
    st2, _, raw2 = b.request("GET", "/api/assets")
    rep.check(status == 200 and json.loads(raw2)["assets"] == [] and audit.get("intact") and
              [e["event"] for e in audit["entries"]] == ["user_data_deleted"],
              "delete my data: assets gone, one audit entry user_data_deleted", json.dumps(data.get("deleted")))

    print(f"\n{'ALL CHECKS PASSED' if rep.failures == 0 else str(rep.failures) + ' CHECK(S) FAILED'}")
    return 1 if rep.failures else 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--base", help="drive an already running server, e.g. http://127.0.0.1:8090")
    args = p.parse_args(argv)
    if args.base:
        return run_checks(args.base, scan_allowed=False)
    data_dir = Path(tempfile.mkdtemp(prefix="maya-ui-flow-"))  # throwaway, removed below
    # deterministic-only: this check never makes a live model call, even if a model key is configured
    server = make_server(port=0, data_dir=data_dir, provider_factory=lambda: (None, "none"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"server: {base}  data dir: {data_dir} (throwaway)")
    try:
        return run_checks(base, scan_allowed=True)
    finally:
        server.shutdown()
        server.server_close()
        shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
