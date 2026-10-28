"""Run checks by id. HTTP checks fetch through HttpClient (scope-checked, GET only). DNS and TLS checks run
only for a verified, non-loopback target: DNS via DNS-over-HTTPS (checks_dns), TLS via one handshake with the
target's own port 443 (checks_tls); both scope-check the host first."""
from urllib.parse import urlsplit

from src import scope
from src.models import Finding
from src.scanner.checks_dns import run_dns_check
from src.scanner.checks_js import check_js_libraries
from src.scanner.checks_tls import TLS_FINDING_CHECKS, run_tls_check
from src.scanner.checks_files import (check_backup_file, check_directory_listing, check_env_file,
                                      check_git_head)
from src.scanner.checks_headers import check_cookies, check_security_headers, check_version_disclosure
from src.scanner.http_client import HttpClient

HTTP_CHECKS = {
    "missing_csp": check_security_headers,
    "missing_hsts": check_security_headers,
    "missing_x_frame_options": check_security_headers,
    "missing_x_content_type_options": check_security_headers,
    "cookie_missing_secure": check_cookies,
    "cookie_missing_httponly": check_cookies,
    "cookie_missing_samesite": check_cookies,
    "server_version_disclosure": check_version_disclosure,
    "exposed_env_file": check_env_file,
    "exposed_git_dir": check_git_head,
    "exposed_backup_file": check_backup_file,
    "directory_listing": check_directory_listing,
    "outdated_js_library": check_js_libraries,
}
DNS_CHECKS = ("missing_dmarc", "missing_spf")
TLS_CHECK = "weak_tls_or_expiring_cert"
NET_CHECKS = DNS_CHECKS + (TLS_CHECK,)
ALL_CHECKS = sorted(HTTP_CHECKS) + list(NET_CHECKS)
# finding check id -> the check that produces (and re-tests) it
PRODUCED_BY = {**{c: c for c in ALL_CHECKS}, **{c: TLS_CHECK for c in TLS_FINDING_CHECKS}}
LOOPBACK_NA = "not applicable (loopback demo)"
NOT_HTTPS_NA = "not applicable (target is not HTTPS)"
BACKUP_PATHS = ["/backup.zip", "/db.sql", "/index.php.bak"]
LISTING_PATHS = ["/backup/", "/uploads/"]
FULL_SCAN = (
    [("/", fn) for fn in (check_security_headers, check_cookies, check_version_disclosure)]
    + [("/.env", check_env_file), ("/.git/HEAD", check_git_head)]
    + [(p, check_backup_file) for p in BACKUP_PATHS]
    + [(p, check_directory_listing) for p in LISTING_PATHS]
    + [("/", check_js_libraries)]
)


class NotRunnable(ValueError):
    pass


def not_applicable(check_id: str, base_url: str) -> str | None:
    """Why a check does not apply to this target, or None if it does."""
    if check_id not in NET_CHECKS:
        return None
    parts = urlsplit(base_url)
    if scope.is_loopback(parts.hostname or ""):
        return LOOPBACK_NA
    if check_id == TLS_CHECK and parts.scheme != "https":
        return NOT_HTTPS_NA
    return None


def runnable_checks(base_url: str) -> list[str]:
    return [c for c in ALL_CHECKS if not_applicable(c, base_url) is None]


def run_check(client: HttpClient, base_url: str, check_id: str, path: str) -> list[Finding]:
    """path is used by HTTP checks; DNS and TLS checks look at the target's host."""
    if check_id not in PRODUCED_BY.values():
        raise NotRunnable(f"unknown check {check_id!r}")
    reason = not_applicable(check_id, base_url)
    if reason:
        raise NotRunnable(f"{check_id}: {reason}")
    if check_id in DNS_CHECKS:
        host = urlsplit(base_url).hostname or ""
        return run_dns_check(check_id, host, client.verified, transport=client.transport, store=client.store)
    if check_id == TLS_CHECK:
        return run_tls_check(urlsplit(base_url).hostname or "", client.verified, store=client.store)
    resp = client.get(base_url.rstrip("/") + path)
    return [f for f in HTTP_CHECKS[check_id](resp) if f.check == check_id]


def scan_all(client: HttpClient, base_url: str) -> list[Finding]:
    """Deterministic full scan: every HTTP check on its usual paths, one GET per path."""
    base = base_url.rstrip("/")
    responses, findings = {}, []
    for path, fn in FULL_SCAN:
        if path not in responses:
            responses[path] = client.get(base + path)
        findings += fn(responses[path])
    return findings
