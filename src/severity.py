"""Fixed severity table. Severity is decided here, in code, never by a model."""
from typing import NamedTuple


class Rule(NamedTuple):
    severity: str
    rationale: str      # plain-language: why it matters
    remediation: str    # short fix hint


class UnknownCheck(KeyError):
    pass


TABLE: dict[str, Rule] = {
    "missing_csp": Rule("medium",
        "Without a Content-Security-Policy, an injected script can run freely in your visitors' browsers.",
        "Send a Content-Security-Policy header, e.g. default-src 'self'."),
    "missing_hsts": Rule("medium",
        "Without HSTS, browsers may connect over plain HTTP where traffic can be read or changed.",
        "Serve the site over HTTPS and send Strict-Transport-Security: max-age=31536000."),
    "missing_x_frame_options": Rule("medium",
        "Other sites can load your pages in a hidden frame and trick users into clicking (clickjacking).",
        "Send X-Frame-Options: DENY, or CSP frame-ancestors 'none'."),
    "missing_x_content_type_options": Rule("low",
        "Browsers may guess file types and run uploaded content as a script.",
        "Send X-Content-Type-Options: nosniff."),
    "cookie_missing_secure": Rule("medium",
        "The cookie can be sent over unencrypted HTTP and stolen on the network.",
        "Add the Secure flag to the cookie."),
    "cookie_missing_httponly": Rule("medium",
        "Scripts on the page can read the cookie, so an XSS bug can steal sessions.",
        "Add the HttpOnly flag to the cookie."),
    "cookie_missing_samesite": Rule("low",
        "The cookie is sent on cross-site requests, which helps CSRF attacks.",
        "Add SameSite=Lax (or Strict) to the cookie."),
    "exposed_env_file": Rule("critical",
        "Your .env file is public; it usually holds passwords and API keys.",
        "Block .env from being served and rotate every secret it contained."),
    "exposed_git_dir": Rule("high",
        "Your .git folder is public; anyone can download your source code and history.",
        "Block /.git/ in the web server and deploy without the .git folder."),
    "exposed_backup_file": Rule("high",
        "A backup file is downloadable and may contain data or source code.",
        "Remove backup files from the web root."),
    "server_version_disclosure": Rule("low",
        "Headers reveal exact software versions, which helps attackers pick known exploits.",
        "Remove version numbers from Server and X-Powered-By headers."),
    "directory_listing": Rule("medium",
        "A folder lists all its files, exposing things that were never meant to be linked.",
        "Disable directory listing (autoindex) in the web server."),
    "weak_tls_or_expiring_cert": Rule("medium",
        "Weak TLS settings or an expiring certificate can break or weaken encrypted connections.",
        "Disable TLS < 1.2 and renew the certificate before it expires."),
    "missing_spf": Rule("low",
        "Without SPF, others can more easily send email pretending to be your domain.",
        "Publish an SPF TXT record listing your mail senders."),
    "missing_dmarc": Rule("low",
        "Without DMARC, receivers have no policy for rejecting spoofed email from your domain.",
        "Publish a DMARC TXT record at _dmarc.<domain>, starting with p=none and tightening."),
    "cert_expires_within_14_days": Rule("high",
        "The TLS certificate expires within 14 days (or already has); visitors will soon get browser errors.",
        "Renew the certificate now and set up automatic renewal."),
    "cert_expires_within_30_days": Rule("medium",
        "The TLS certificate expires within 30 days.",
        "Renew the certificate and set up automatic renewal."),
    "tls_protocol_below_1_2": Rule("medium",
        "The server accepts a TLS version older than 1.2, which has known weaknesses.",
        "Allow only TLS 1.2 and 1.3."),
    "outdated_js_library": Rule("medium",
        "A JavaScript library on the page appears outdated; old releases often have publicly documented issues.",
        "Update the library to a current release and test the pages that use it."),
}


def rule(check_id: str) -> Rule:
    try:
        return TABLE[check_id]
    except KeyError:
        raise UnknownCheck(f"unknown check id: {check_id!r}") from None


def severity(check_id: str) -> str:
    return rule(check_id).severity
