"""Header checks: pure functions over a Response (no network). Severity always comes from src.severity."""
import re

from src.evidence import redact_header
from src.scanner.findings import from_response
from src.models import Finding

SECURITY_HEADERS = [
    ("content-security-policy", "missing_csp"),
    ("strict-transport-security", "missing_hsts"),
    ("x-frame-options", "missing_x_frame_options"),
    ("x-content-type-options", "missing_x_content_type_options"),
]
VERSION = re.compile(r"\d+(?:\.\d+)+")


def check_security_headers(resp) -> list[Finding]:
    names = sorted({k.lower() for k, _ in resp.headers})
    excerpt = f"HTTP {resp.status}; response header names: {', '.join(names)}"
    csp = (resp.header("content-security-policy") or "").lower()
    findings = []
    for header, check in SECURITY_HEADERS:
        value = (resp.header(header) or "").strip()
        if header == "x-content-type-options":
            ok = value.lower() == "nosniff"
        else:  # CSP frame-ancestors supersedes X-Frame-Options
            ok = bool(value) or (header == "x-frame-options" and "frame-ancestors" in csp)
        if not ok:
            findings.append(from_response(resp, check, excerpt, f"{header} is missing or invalid."))
    return findings


def check_cookies(resp) -> list[Finding]:
    findings = []
    for raw in resp.header_all("set-cookie"):
        name = raw.split("=", 1)[0].strip()
        attrs = {a.strip().split("=", 1)[0].lower() for a in raw.split(";")[1:]}
        excerpt = "Set-Cookie: " + redact_header("set-cookie", raw)
        for flag, check in (("secure", "cookie_missing_secure"), ("httponly", "cookie_missing_httponly"),
                            ("samesite", "cookie_missing_samesite")):
            if flag not in attrs:
                findings.append(from_response(resp, check, excerpt, f"Cookie {name!r} has no {flag} attribute.",
                                              suffix=f":{name}"))
    return findings


def check_version_disclosure(resp) -> list[Finding]:
    findings = []
    for header in ("server", "x-powered-by"):
        value = resp.header(header)
        if value and VERSION.search(value):
            findings.append(from_response(resp, "server_version_disclosure", f"{header}: {redact_header(header, value)}",
                                          f"{header} header reveals a version: {value!r}.", suffix=f":{header}"))
    return findings


def run_all(resp) -> list[Finding]:
    return check_security_headers(resp) + check_cookies(resp) + check_version_disclosure(resp)
