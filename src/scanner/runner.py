"""Run checks by id. All fetching goes through HttpClient (scope-checked, GET only)."""
from src.models import Finding
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
}
BACKUP_PATHS = ["/backup.zip", "/db.sql", "/index.php.bak"]
LISTING_PATHS = ["/backup/", "/uploads/"]
FULL_SCAN = (
    [("/", fn) for fn in (check_security_headers, check_cookies, check_version_disclosure)]
    + [("/.env", check_env_file), ("/.git/HEAD", check_git_head)]
    + [(p, check_backup_file) for p in BACKUP_PATHS]
    + [(p, check_directory_listing) for p in LISTING_PATHS]
)


class NotRunnable(ValueError):
    pass


def run_check(client: HttpClient, base_url: str, check_id: str, path: str) -> list[Finding]:
    fn = HTTP_CHECKS.get(check_id)
    if fn is None:
        raise NotRunnable(f"{check_id} needs DNS/TLS data and cannot run over HTTP here")
    resp = client.get(base_url.rstrip("/") + path)
    return [f for f in fn(resp) if f.check == check_id]


def scan_all(client: HttpClient, base_url: str) -> list[Finding]:
    """Deterministic full scan: every HTTP check on its usual paths, one GET per path."""
    base = base_url.rstrip("/")
    responses, findings = {}, []
    for path, fn in FULL_SCAN:
        if path not in responses:
            responses[path] = client.get(base + path)
        findings += fn(responses[path])
    return findings
