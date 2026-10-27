"""Fix proposals from fixed templates (never model prose). Every proposal is 'proposed, not applied'
until it passes the approval gate (src/approval.py)."""
from dataclasses import dataclass, field

from src.models import Finding

PROPOSED_NOTE = "proposed, not applied"
RELOAD = "then reload: `nginx -s reload` / `apachectl graceful`"


@dataclass(frozen=True)
class FixPlan:
    kind: str          # "file block rule" | "header addition" | "config diff"
    nginx: str
    apache: str
    python_note: str   # for apps served with Python's http.server / a Python framework
    risk: str          # "low" | "medium": chance the change breaks something legitimate
    rollback: str


def _header(name: str, value: str, risk: str, why: str = "") -> FixPlan:
    return FixPlan(
        "header addition",
        f'add_header {name} "{value}" always;',
        f'Header always set {name} "{value}"',
        f"Send `{name}: {value}` from the handler (e.g. self.send_header in http.server).{why}",
        risk, f"Remove the {name} line, {RELOAD}.")


def _cookie(attr: str, risk: str) -> FixPlan:
    return FixPlan(
        "config diff",
        f"proxy_cookie_flags ~ {attr.lower()};  # nginx >= 1.19.3, in the proxied location",
        f'Header always edit Set-Cookie "^(.*)$" "$1; {attr}"',
        f"Add `; {attr}` where the app builds the Set-Cookie header.",
        risk, f"Remove the cookie-flag line, {RELOAD}.")


def _block(nginx_pattern: str, apache_match: str, what: str, risk: str, extra: str = "") -> FixPlan:
    return FixPlan(
        "file block rule",
        f"location ~ {nginx_pattern} {{ deny all; return 404; }}",
        f'<FilesMatch "{apache_match}">\n    Require all denied\n</FilesMatch>',
        f"Serve only a dedicated public folder (python -m http.server --directory public), never the project root; "
        f"keep {what} outside it.{extra}",
        risk, f"Remove the block rule, {RELOAD}.")


PLANS: dict[str, FixPlan] = {
    "exposed_env_file": _block(r"/\.env", r"^\.env", ".env files", "low",
                               " Rotate every value the exposed file contained."),
    "exposed_git_dir": FixPlan(
        "file block rule", r"location ~ /\.git { deny all; return 404; }", r'RedirectMatch 404 "/\.git"',
        "Deploy without the .git folder (export a build, do not copy the repository).",
        "low", f"Remove the .git rule, {RELOAD}."),
    "exposed_backup_file": _block(r"\.(sql|bak|zip|tar|gz)$", r"\.(sql|bak|zip|tar|gz)$", "dumps and archives",
                                  "medium", " Check nothing legitimate is served with these extensions."),
    "directory_listing": FixPlan(
        "config diff", "autoindex off;", "Options -Indexes",
        "http.server lists any folder without index.html; add an index.html or serve through an app.",
        "low", f"Restore `autoindex on;` / `Options +Indexes`, {RELOAD}."),
    "cookie_missing_secure": _cookie("Secure", "medium"),   # cookie stops being sent over plain HTTP
    "cookie_missing_httponly": _cookie("HttpOnly", "low"),
    "cookie_missing_samesite": _cookie("SameSite=Lax", "low"),
    "missing_csp": _header("Content-Security-Policy", "default-src 'self'", "medium",
                           " Test pages that load scripts or styles from other origins."),
    "missing_hsts": _header("Strict-Transport-Security", "max-age=31536000", "medium",
                            " Only send it over HTTPS; browsers remember it for max-age seconds."),
    "missing_x_frame_options": _header("X-Frame-Options", "DENY", "low"),
    "missing_x_content_type_options": _header("X-Content-Type-Options", "nosniff", "low"),
    "server_version_disclosure": FixPlan(
        "config diff", "server_tokens off;", "ServerTokens Prod\nServerSignature Off",
        "Override BaseHTTPRequestHandler.version_string() to return a name without versions.",
        "low", f"Restore the previous server_tokens / ServerTokens value, {RELOAD}."),
}


@dataclass
class Proposal:
    id: str
    check_id: str
    finding_ids: list[str]
    target: str
    plan: FixPlan
    status: str = "PROPOSED"
    note: str = PROPOSED_NOTE
    approved_by: str | None = None
    approved_at: str | None = None
    applied_at: str | None = None
    history: list[str] = field(default_factory=lambda: ["PROPOSED"])


def build_proposals(findings: dict[str, Finding]) -> list[Proposal]:
    """One proposal per check that has a plan, referencing every finding id it addresses."""
    by_check: dict[str, list[Finding]] = {}
    for f in findings.values():
        by_check.setdefault(f.check, []).append(f)
    return [Proposal(id=f"fix-{check}", check_id=check, finding_ids=sorted(f.id for f in fs),
                     target=fs[0].target, plan=PLANS[check])
            for check, fs in sorted(by_check.items()) if check in PLANS]
