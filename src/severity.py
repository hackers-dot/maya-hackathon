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
    "code_hardcoded_secret": Rule("high",
        "A secret (key, token, password or private key) appears to be written into the source code, where anyone with the code can read it.",
        "Rotate the secret now, then load it from an environment variable or a secret store; never commit it."),
    "code_env_file_committed": Rule("high",
        "A .env file with settings appears in the code; such files usually hold passwords and keys.",
        "Remove the .env file from the code, add it to .gitignore, and rotate every value it held."),
    "code_debug_enabled": Rule("medium",
        "Debug mode appears to be on; debug pages can show code, settings and data to visitors.",
        "Turn debug mode off in production and read the setting from an environment variable."),
    "code_dangerous_eval": Rule("medium",
        "eval( or exec( runs text as code; if that text can come from a user, they can run their own code.",
        "Replace eval/exec with explicit parsing (e.g. json.loads, ast.literal_eval) or a lookup table."),
    "code_unsafe_deserialization": Rule("medium",
        "pickle.load(s) can run code hidden in the data it reads, so it must never read untrusted data.",
        "Use a data-only format such as JSON for anything that comes from outside."),
    "code_shell_true": Rule("medium",
        "shell=True passes the command through a shell, so user text in it can add extra commands.",
        "Pass the command as a list without shell=True, and never build it from user input."),
    "code_weak_password_hash": Rule("medium",
        "MD5 and SHA-1 are fast hashes; passwords stored with them can be guessed quickly if the database leaks.",
        "Hash passwords with a slow password hash (bcrypt, scrypt or argon2) and rehash on next login."),
    "code_outdated_dependency": Rule("medium",
        "A pinned dependency appears outdated; old releases often have publicly documented issues.",
        "Update the dependency to a current release and run your tests."),
    "log_failed_login_burst": Rule("high",
        "Many failed logins from one source address in a short time can mean password guessing.",
        "Rate-limit or temporarily block the source address, require strong passwords, and enable MFA."),
    "log_sensitive_path_probe": Rule("medium",
        "Requests for files like .env, .git or admin tools suggest automated scanning for exposed files.",
        "Make sure those paths return 404, and consider blocking the source address."),
    "log_many_4xx": Rule("low",
        "Many client errors from one source address often come from automated scanning or a broken client.",
        "Review the requests; rate-limit the source address if they are unwanted."),
    "log_injection_pattern": Rule("medium",
        "Request lines contain SQL-injection-like or path-traversal strings; this shows attempts, not success.",
        "Check that the targeted endpoints use parameterised queries and safe path handling; consider a WAF rule."),
    "log_login_after_failures": Rule("high",
        "A successful login came right after repeated failures from the same source address; the account may have been guessed.",
        "Confirm the login with the account owner, reset the password if unsure, and enable MFA."),
    "msg_domain_resembles_yours": Rule("high",
        "A web address in the message resembles your domain, so people may take it for yours.",
        "Check who owns that domain; if it is not yours, report it to its registrar and to the platform where the message appeared."),
    "msg_domain_not_in_list": Rule("medium",
        "A web address in the message is not in your list of official domains.",
        "Check whether the domain is yours or a partner's; add it to your official list, or report it if it is not."),
    "msg_phone_not_in_list": Rule("medium",
        "A phone number in the message is not in your list of official numbers.",
        "Check whether the number is yours; add it to your official list, or warn customers if it is not."),
    "msg_payment_request": Rule("medium",
        "The message asks for a payment, which needs review if it did not come from you.",
        "Confirm with your team whether this payment request is genuine before anyone pays."),
    "msg_urgency_language": Rule("low",
        "The message uses urgent wording, which pushes readers to act before checking.",
        "Tell customers how you really contact them, and that you never rush payments."),
}


def rule(check_id: str) -> Rule:
    try:
        return TABLE[check_id]
    except KeyError:
        raise UnknownCheck(f"unknown check id: {check_id!r}") from None


def severity(check_id: str) -> str:
    return rule(check_id).severity
