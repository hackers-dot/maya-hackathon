"""Report builder: verified claims + findings -> markdown and self-contained HTML.

All prose comes from fixed tables in this file. Severity comes from src.severity. Category labels say
"mapped to", never "compliant". Wording sticks to "appears" / "detected" / "not detected in the checks
run"; words like "hacked" or "secure" are not used in prose (`Secure` appears only as the literal
cookie attribute, inside code formatting).
"""
import html
import re
from dataclasses import dataclass
from pathlib import Path

from src.agent.verifier import evidence_line
from src.evidence import EvidenceStore
from src.severity import TABLE, rule

SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]

OWASP = {  # check -> (code, name); None = outside the OWASP Top 10
    "missing_csp": ("A05:2021", "Security Misconfiguration"),
    "missing_hsts": ("A02:2021", "Cryptographic Failures"),
    "missing_x_frame_options": ("A05:2021", "Security Misconfiguration"),
    "missing_x_content_type_options": ("A05:2021", "Security Misconfiguration"),
    "cookie_missing_secure": ("A02:2021", "Cryptographic Failures"),
    "cookie_missing_httponly": ("A05:2021", "Security Misconfiguration"),
    "cookie_missing_samesite": ("A01:2021", "Broken Access Control"),
    "exposed_env_file": ("A05:2021", "Security Misconfiguration"),
    "exposed_git_dir": ("A05:2021", "Security Misconfiguration"),
    "exposed_backup_file": ("A05:2021", "Security Misconfiguration"),
    "server_version_disclosure": ("A05:2021", "Security Misconfiguration"),
    "directory_listing": ("A05:2021", "Security Misconfiguration"),
    "weak_tls_or_expiring_cert": ("A02:2021", "Cryptographic Failures"),
    "missing_spf": None,
    "missing_dmarc": None,
    "cert_expires_within_14_days": ("A02:2021", "Cryptographic Failures"),
    "cert_expires_within_30_days": ("A02:2021", "Cryptographic Failures"),
    "tls_protocol_below_1_2": ("A02:2021", "Cryptographic Failures"),
    "outdated_js_library": ("A06:2021", "Vulnerable and Outdated Components"),  # the official category name
    "code_hardcoded_secret": ("A07:2021", "Identification and Authentication Failures"),
    "code_env_file_committed": ("A05:2021", "Security Misconfiguration"),
    "code_debug_enabled": ("A05:2021", "Security Misconfiguration"),
    "code_dangerous_eval": ("A03:2021", "Injection"),
    "code_unsafe_deserialization": ("A08:2021", "Software and Data Integrity Failures"),
    "code_shell_true": ("A03:2021", "Injection"),
    "code_weak_password_hash": ("A02:2021", "Cryptographic Failures"),
    "code_outdated_dependency": ("A06:2021", "Vulnerable and Outdated Components"),  # the official category name
    # log detections describe observed activity, not a weakness in the app: no OWASP category
    "log_failed_login_burst": None,
    "log_sensitive_path_probe": None,
    "log_many_4xx": None,
    "log_injection_pattern": None,
    "log_login_after_failures": None,
    # message checks describe a third-party message, not a weakness in the app: no OWASP category
    "msg_domain_resembles_yours": None,
    "msg_domain_not_in_list": None,
    "msg_phone_not_in_list": None,
    "msg_payment_request": None,
    "msg_urgency_language": None,
    # lookalike watch: observed registrations, not a weakness in the app
    "lookalike_recent_certificate": None,
    "lookalike_has_dns": None,
    "lookalike_no_dns": None,
}


@dataclass(frozen=True)
class Text:
    title: str
    plain: str
    analogy: str
    technical: str
    fix: str


# Backticks mark code spans (rendered as <code> in HTML).
TEXT = {
    "missing_csp": Text(
        "Content-Security-Policy header not detected",
        "The page does not tell the browser which scripts it may run, so an injected script would run without limits.",
        "Like a building with no guest list: anyone who gets through the door is treated as invited.",
        "The response has no `Content-Security-Policy` header.",
        "Send a `Content-Security-Policy` header, starting from `default-src 'self'` and adding the sources the page needs."),
    "missing_hsts": Text(
        "HSTS header not detected",
        "Browsers are not told to always use HTTPS here, so a visit can start over unencrypted HTTP.",
        "Like a bank that never tells customers to use the main entrance, so some walk in through an unwatched side door.",
        "The response has no `Strict-Transport-Security` header.",
        "Serve the site over HTTPS and send `Strict-Transport-Security: max-age=31536000`."),
    "missing_x_frame_options": Text(
        "Framing protection not detected",
        "Other websites can show this page inside a hidden frame and trick visitors into clicking buttons on it.",
        "Like someone laying a glass sheet over your shop counter and painting fake buttons on it.",
        "Neither `X-Frame-Options` nor a CSP `frame-ancestors` directive is set.",
        "Send `X-Frame-Options: DENY`, or CSP `frame-ancestors 'none'`."),
    "missing_x_content_type_options": Text(
        "Content-type sniffing protection not detected",
        "Browsers may guess what a file is and could treat an uploaded file as a script.",
        "Like a mail room that guesses what is inside a parcel instead of reading the label.",
        "`X-Content-Type-Options: nosniff` is missing or has another value.",
        "Send `X-Content-Type-Options: nosniff`."),
    "cookie_missing_secure": Text(
        "Cookie may be sent over plain HTTP",
        "A cookie may travel over unencrypted connections, where others on the same network could read it.",
        "Like posting a house key in a clear envelope.",
        "The `Set-Cookie` header lacks the `Secure` attribute.",
        "Add the `Secure` attribute to the cookie."),
    "cookie_missing_httponly": Text(
        "Cookie readable by page scripts",
        "Scripts on the page can read this cookie, so an injected script could copy a visitor's session.",
        "Like leaving the session key on the reception desk instead of in a locked drawer.",
        "The `Set-Cookie` header lacks the `HttpOnly` attribute.",
        "Add the `HttpOnly` attribute to the cookie."),
    "cookie_missing_samesite": Text(
        "Cookie sent on cross-site requests",
        "The cookie is attached when other websites send requests here, which can help them act on a visitor's behalf.",
        "Like a doorman who admits anyone carrying your badge, even if a stranger handed it to them.",
        "The `Set-Cookie` header lacks the `SameSite` attribute.",
        "Add `SameSite=Lax` (or `SameSite=Strict`) to the cookie."),
    "exposed_env_file": Text(
        "Configuration file appears publicly readable",
        "A file that usually holds passwords and API keys can be downloaded by anyone.",
        "Like taping the office alarm code to the front door.",
        "The path returned HTTP 200 with `KEY=value` lines.",
        "Block the file in the web server and rotate every value it contained."),
    "exposed_git_dir": Text(
        "Git repository data appears publicly readable",
        "The site's source code and its history can be downloaded.",
        "Like leaving the building's blueprints in the lobby.",
        "`/.git/HEAD` returned a git reference.",
        "Block `/.git/` in the web server and deploy without the `.git` folder."),
    "exposed_backup_file": Text(
        "Backup file appears publicly downloadable",
        "A backup copy can be downloaded and may contain data or source code.",
        "Like a spare copy of the customer files left on the shop counter.",
        "The path returned HTTP 200 with a non-HTML file.",
        "Remove backup files from the web root."),
    "server_version_disclosure": Text(
        "Software version detected in response headers",
        "The server announces exact software versions, which makes it easier to look up known weaknesses.",
        "Like a lock with its model number printed on the outside.",
        "`Server` or `X-Powered-By` contains a version number.",
        "Remove version numbers from the `Server` and `X-Powered-By` headers."),
    "directory_listing": Text(
        "Directory listing detected",
        "A folder shows a list of all its files, including ones that were never meant to be linked.",
        "Like a filing cabinet left open with every drawer labelled.",
        "The response is an auto-generated index page (`Index of /`).",
        "Disable directory listing (autoindex) in the web server."),
    "weak_tls_or_expiring_cert": Text(
        "TLS configuration weakness detected",
        "The encrypted connection uses outdated settings or a certificate that is close to expiry.",
        "Like a door lock that is worn out or about to stop working.",
        "Weak protocol versions are enabled or the certificate expires soon.",
        "Disable TLS versions below 1.2 and renew the certificate early."),
    "missing_spf": Text(
        "SPF record missing or invalid",
        "Others can more easily send email that looks like it comes from your domain.",
        "Like a company with no list of who may sign letters on its behalf.",
        "There is no single valid `v=spf1` TXT record, or it ends in `+all`.",
        "Publish one SPF record that lists your mail senders and ends in `-all` or `~all`."),
    "missing_dmarc": Text(
        "DMARC policy missing or invalid",
        "Mail receivers have no instruction for handling email that fakes your domain.",
        "Like a post office with no rule for letters that carry a forged return address.",
        "There is no valid `v=DMARC1` record with a `p=` policy at `_dmarc`.",
        "Publish a DMARC record, start with `p=none`, then tighten to `quarantine` or `reject`."),
    "cert_expires_within_14_days": Text(
        "TLS certificate expires within 14 days",
        "The certificate that protects the site expires very soon (or already has); browsers will then warn visitors.",
        "Like an ID card that runs out next week.",
        "The certificate's `notAfter` date is less than 14 days away.",
        "Renew the certificate now and turn on automatic renewal."),
    "cert_expires_within_30_days": Text(
        "TLS certificate expires within 30 days",
        "The certificate that protects the site expires within a month.",
        "Like an ID card that runs out this month.",
        "The certificate's `notAfter` date is less than 30 days away.",
        "Renew the certificate and turn on automatic renewal."),
    "tls_protocol_below_1_2": Text(
        "TLS version below 1.2 accepted",
        "The server still agrees to an old encryption protocol version.",
        "Like a bank that still accepts an old, easily copied style of signature.",
        "The handshake negotiated a protocol older than `TLSv1.2`.",
        "Allow only TLS 1.2 and 1.3 (`ssl_protocols TLSv1.2 TLSv1.3;`)."),
    "outdated_js_library": Text(
        "JavaScript library appears outdated",
        "The page loads an old release of a JavaScript library; old releases often have publicly documented issues.",
        "Like running last decade's version of an app that has had many updates since.",
        "A `<script src>` names a library version older than MAYA's short reference table.",
        "Update the library to a current release and test the pages that use it."),
    "code_hardcoded_secret": Text(
        "Hard-coded secret detected in source",
        "A password, key or token appears to be written into the code itself.",
        "Like writing the combination on the door of the safe.",
        "A secret-looking value is assigned in the file at the line shown (value masked).",
        "Rotate the secret now, then read it from an environment variable or secret store."),
    "code_env_file_committed": Text(
        "Environment file (.env) found in the code",
        "A settings file that usually holds passwords and keys is part of the code.",
        "Like packing the house keys into a box that strangers will open.",
        "A `.env` file (not a template such as `.env.example`) is in the upload.",
        "Remove the file from the code, add `.env` to `.gitignore`, and rotate every value it held."),
    "code_debug_enabled": Text(
        "Debug mode appears enabled",
        "Debug mode can show code, settings and data to anyone who triggers an error.",
        "Like leaving the workshop door open with all the plans on the wall.",
        "`DEBUG = True` or `debug=True` is set in the file shown.",
        "Turn debug mode off in production; read the setting from an environment variable."),
    "code_dangerous_eval": Text(
        "eval / exec call detected",
        "Text is run as code; if a user can influence that text, they can run their own code.",
        "Like a cashier who does whatever is written on any note handed over.",
        "An `eval(` or `exec(` call appears at the line shown.",
        "Replace it with explicit parsing (`json.loads`, `ast.literal_eval`) or a lookup table."),
    "code_unsafe_deserialization": Text(
        "pickle deserialization detected",
        "pickle can run code hidden inside the data it reads.",
        "Like opening a parcel that assembles itself into a machine inside your house.",
        "A `pickle.load(` or `pickle.loads(` call appears at the line shown.",
        "Use JSON (or another data-only format) for anything that comes from outside."),
    "code_shell_true": Text(
        "Shell command with shell=True detected",
        "The command goes through a shell, so extra text in it can become extra commands.",
        "Like reading a shopping list aloud to someone who obeys every word on it.",
        "A call with `shell=True` appears at the line shown.",
        "Pass the command as a list without `shell=True`; never build it from user input."),
    "code_weak_password_hash": Text(
        "Fast hash (MD5/SHA-1) near password handling",
        "Passwords hashed with MD5 or SHA-1 can be guessed quickly if the data leaks.",
        "Like a lock that can be tried a billion times a second.",
        "`md5` or `sha1` appears on a line that also mentions a password.",
        "Use bcrypt, scrypt or argon2 for passwords and rehash on next login."),
    "code_outdated_dependency": Text(
        "Dependency appears outdated",
        "A pinned package version is older than the short reference table in MAYA.",
        "Like running an app version that has had many updates since.",
        "`requirements.txt` or `package.json` pins a version older than the example table.",
        "Update the dependency to a current release and run your tests."),
    "log_failed_login_burst": Text(
        "Burst of failed logins from one source address",
        "Many failed logins from one source address in a short time can mean someone is guessing passwords.",
        "Like someone trying key after key in your front door.",
        "At least the configured number of failed logins share one source address within the configured window (a heuristic, not a standard).",
        "Rate-limit or block the source address, require strong passwords, and turn on MFA."),
    "log_sensitive_path_probe": Text(
        "Requests for sensitive paths",
        "Someone asked for files like `.env`, `.git` or admin tools, which suggests automated scanning.",
        "Like a stranger trying the handles of every back door.",
        "Requests for `.env`, `.git`, `wp-login.php` or `phpmyadmin` share one source address.",
        "Make sure these paths return 404; consider blocking the source address."),
    "log_many_4xx": Text(
        "Many client errors from one source address",
        "One source address caused many error responses, which often comes from automated scanning.",
        "Like someone ringing every doorbell in the building.",
        "At least the configured number of 4xx responses share one source address.",
        "Review the requests; rate-limit the source address if they are unwanted."),
    "log_injection_pattern": Text(
        "Injection-like or traversal strings in requests",
        "Requests contained text typical of database-injection or file-path tricks; this shows attempts, not success.",
        "Like finding pry marks on a door that stayed shut.",
        "Request lines contain SQL-injection-like (`union select`, `or 1=1`) or traversal (`../`) strings.",
        "Check that the endpoints use parameterised queries and safe path handling."),
    "log_login_after_failures": Text(
        "Successful login after repeated failures",
        "A login succeeded right after several failed tries from the same source address.",
        "Like a door that opened after many wrong keys were tried.",
        "A successful login follows the configured number of failed logins that share one source address.",
        "Confirm the login with the account owner, reset the password if unsure, and turn on MFA."),
    "msg_domain_resembles_yours": Text(
        "Web address resembles your domain",
        "A link in the message looks a lot like your own domain, so people may trust it.",
        "Like a shop sign with one letter changed.",
        "The domain differs from yours by a fixed look-alike rule (edit distance, look-alike characters, hyphen, keyword, or the same name with another ending).",
        "Check the domain owner; if it is not you, report it to the registrar and the platform (MAYA can draft the note)."),
    "msg_domain_not_in_list": Text(
        "Web address not in your list",
        "A link in the message points to a domain that is not on your official list.",
        "Like a letter on your letterhead with a return address you do not recognise.",
        "The normalised domain is neither a verified asset nor in your official-domains list.",
        "Check whether it is yours or a partner's; update your list or report it."),
    "msg_phone_not_in_list": Text(
        "Phone number not in your list",
        "The message gives a phone number that is not on your official list.",
        "Like a business card with your logo and someone else's number.",
        "The normalised number is not in your official-numbers list.",
        "Check the number; add it to your list if it is yours, otherwise warn customers."),
    "msg_payment_request": Text(
        "Payment request in the message",
        "The message asks the reader to pay, which needs review if it is not yours.",
        "Like an invoice in your name that your accounts team never sent.",
        "The model read one or more payment requests in the image; code only counts them.",
        "Confirm internally whether the request is genuine before anyone pays."),
    "msg_urgency_language": Text(
        "Urgent wording in the message",
        "The message pushes the reader to act quickly.",
        "Like a caller who says the offer ends in five minutes.",
        "The model read one or more urgency phrases in the image; code only counts them.",
        "Tell customers how you really contact them, and that you never rush payments."),
    "lookalike_recent_certificate": Text(
        "Lookalike domain with a recent certificate",
        "A domain that looks like yours is registered and recently got a certificate.",
        "Like a new shop across the street using almost your name and a freshly printed sign.",
        "DNS-over-HTTPS shows A/NS records and crt.sh lists a certificate issued in the last 90 days.",
        "Check the owner; report it to the registrar (MAYA can draft the note) and tell customers your real domains."),
    "lookalike_has_dns": Text(
        "Lookalike domain has DNS records",
        "A domain that looks like yours is registered and may be in use.",
        "Like a shop name almost like yours appearing in the business register.",
        "DNS-over-HTTPS shows A or NS records for the generated name.",
        "Check who registered it, keep watching, and report it if it uses your brand."),
    "lookalike_no_dns": Text(
        "Lookalike domain without DNS records",
        "A domain that looks like yours does not appear to be in use today.",
        "Like an empty plot next to your shop with no sign yet.",
        "DNS-over-HTTPS returns no A or NS records for the generated name.",
        "No action needed now; consider registering important variants yourself."),
}

DEMO_BANNER = "DEMO: findings below come from MAYA's deliberately vulnerable demo app on loopback, not a real site."
SCOPE_NOTE = ("This report covers only the checks that were run. Issues not listed here were not detected in "
              "the checks run; that does not show they are absent.")


def owasp_label(check: str) -> str:
    entry = OWASP[check]
    if entry is None:
        return "mapped to: email authentication (outside the OWASP Top 10)"
    return f"mapped to OWASP Top 10 {entry[0]} {entry[1]}"


NO_ISSUES = "No issues detected in the checks run."
DETERMINISTIC = "Detected by deterministic checks"
MODEL_UNFINISHED = "model did not finish; findings are from code checks"
MODEL_FINISHED = "model finished with a final answer"


def build_report(findings: dict, store: EvidenceStore | None, model: str, reasoning_effort: str, usage: dict,
                 claims: dict | None = None, model_finished: bool = False) -> dict:
    """Report from ALL code-detected findings of the run; it does not depend on the model finishing.

    claims: verifier output {"verified": [...], "insufficient": [...]}, shown in its own section.
    """
    items = [{
        "finding_id": f.id, "check": f.check, "severity": rule(f.check).severity, "location": f.location,
        "target": f.target, "evidence_id": f.evidence[0].record_id,
        "evidence_line": evidence_line(f, store),  # code-derived line that shows the issue
        "category": owasp_label(f.check), "text": TEXT[f.check], "demo": f.demo,
    } for f in findings.values()]
    items.sort(key=lambda i: (SEVERITY_ORDER.index(i["severity"]), i["check"], i["location"]))
    claims = claims or {"verified": [], "insufficient": []}
    model_claims = [{"finding_id": v["finding_id"], "severity": rule(v["check"]).severity, "text": v["text"],
                     "evidence_line": v["supporting_evidence"], "priority": v.get("priority"),
                     "note": v.get("note"), "note_dropped": v.get("note_dropped")}
                    for v in claims["verified"]]  # already in display order (model priority or severity)
    return {
        "model": model, "reasoning_effort": reasoning_effort,
        "usage": {k: usage.get(k, 0) for k in ("prompt_tokens", "completion_tokens", "reasoning_tokens", "total_tokens")},
        "model_status": MODEL_FINISHED if model_finished else MODEL_UNFINISHED,
        "model_finished": model_finished,
        "demo": any(i["demo"] for i in items),
        "items": items,
        "fix_first": items[:3],
        "model_claims": model_claims,
        "order_note": claims.get("order_note", ""),
        "insufficient": list(claims["insufficient"]),
    }


# --- markdown ---

def _md_code(s: str) -> str:
    """Inline code span that survives backticks and newlines in evidence text."""
    s = str(s).replace("\r", " ").replace("\n", " ⏎ ")
    fence = "`" * (max((len(m) for m in re.findall(r"`+", s)), default=0) + 1)
    return f"{fence} {s} {fence}"


def to_markdown(report: dict) -> str:
    u = report["usage"]
    out = ["# MAYA report", ""]
    if report["demo"]:
        out += [f"> **{DEMO_BANNER}**", ""]
    out += [f"- Model: {_md_code(report['model'])}", f"- Reasoning effort: {_md_code(report['reasoning_effort'])}",
            f"- Tokens: prompt {u['prompt_tokens']}, completion {u['completion_tokens']} "
            f"(reasoning {u['reasoning_tokens']}), total {u['total_tokens']}",
            f"- Model status: {report['model_status']}", ""]
    out += [f"## {DETERMINISTIC}", ""]
    if not report["items"]:
        out += [NO_ISSUES, ""]
    else:
        out += [f"Issues detected: {len(report['items'])}. Severity comes from MAYA's fixed table, not from the model.",
                "", "### Fix first", ""]
        out += [f"{n}. **{i['severity'].upper()}**: {i['text'].title} at {_md_code(i['location'])}"
                for n, i in enumerate(report["fix_first"], 1)]
        out += [""]
    for i in report["items"]:
        t = i["text"]
        out += [f"### [{i['severity'].upper()}] {t.title}", "",
                f"- Location: {_md_code(i['location'])} on {_md_code(i['target'])}",
                f"- Check: {_md_code(i['check'])} ({i['category']})",
                f"- Evidence id: {_md_code(i['evidence_id'])}",
                f"- Supporting evidence: {_md_code(i['evidence_line'])}", "",
                f"**What this means:** {t.plain} {t.analogy}", "",
                f"**Technical detail:** {t.technical}", "",
                f"**Suggested fix:** {t.fix}", ""]
    out += ["## Model-verified claims", ""]
    if report["model_claims"]:
        out += [f"Order: {report['order_note']}.", ""]
        for n, c in enumerate(report["model_claims"], 1):
            out += [f"{n}. **{c['severity'].upper()}**: {c['text']}",
                    f"   - Code evidence (verified): {_md_code(c['evidence_line'])}",
                    f"   - Model note (unverified model text): {_md_code(c['note'])}" if c["note"] else
                    f"   - Model note dropped: {c['note_dropped']}" if c["note_dropped"] else
                    "   - Model note: none"]
    else:
        out += ["None." + ("" if report["model_finished"] else " The model did not finish.")]
    out += [""]
    if report["insufficient"]:
        out += ["## Claims not included (insufficient evidence)", ""]
        out += [f"- {_md_code(c.get('finding_id'))}: {c['reason']}" for c in report["insufficient"]]
        out += [""]
    out += [SCOPE_NOTE]
    return "\n".join(out) + "\n"


# --- HTML (self-contained: inline CSS, no scripts, no external assets; every string escaped) ---

def _e(s) -> str:
    return html.escape(str(s), quote=True)


def _inline(s: str) -> str:
    """Escape, then turn `backtick` spans from the fixed templates into <code>."""
    parts = str(s).split("`")
    return "".join(f"<code>{_e(p)}</code>" if n % 2 else _e(p) for n, p in enumerate(parts))


CSS = ("body{font-family:system-ui,sans-serif;max-width:860px;margin:2rem auto;padding:0 1rem;line-height:1.5;"
       "color:#1d1d1f;background:#fff}code{background:#f2f2f4;padding:0 .25rem;border-radius:3px}"
       ".banner{background:#fff3cd;border:1px solid #e0b100;padding:.6rem;border-radius:6px;font-weight:600}"
       ".sev{display:inline-block;padding:0 .4rem;border-radius:4px;color:#fff;font-size:.85rem}"
       ".critical{background:#8b0000}.high{background:#c0392b}.medium{background:#d35400}"
       ".low{background:#2c7a7b}.info{background:#555}section{border-top:1px solid #ddd;margin-top:1rem}")


def to_html(report: dict, printable: bool = False) -> str:
    """Self-contained HTML (inline CSS) for download; printable=True instead links /static/report-print.css and
    /static/print.js, so the page can be served by MAYA under its CSP (no inline styles or scripts)."""
    u = report["usage"]
    head = ('<link rel="stylesheet" href="/static/report-print.css"><script src="/static/print.js" defer></script>'
            if printable else f"<style>{CSS}</style>")
    b = ["<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">",
         "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">",
         f"<title>MAYA report</title>{head}</head><body>"
         + ('<p class="print-bar"><button id="print-btn" type="button">Print or save as PDF</button> '
            "Use your browser's print dialog and choose \"Save as PDF\".</p>" if printable else "")
         + "<h1>MAYA report</h1>"]
    if report["demo"]:
        b.append(f"<p class=\"banner\">{_e(DEMO_BANNER)}</p>")
    b.append(f"<ul><li>Model: <code>{_e(report['model'])}</code></li>"
             f"<li>Reasoning effort: <code>{_e(report['reasoning_effort'])}</code></li>"
             f"<li>Tokens: prompt {_e(u['prompt_tokens'])}, completion {_e(u['completion_tokens'])} "
             f"(reasoning {_e(u['reasoning_tokens'])}), total {_e(u['total_tokens'])}</li>"
             f"<li>Model status: {_e(report['model_status'])}</li></ul>")
    b.append(f"<h2>{_e(DETERMINISTIC)}</h2>")
    if not report["items"]:
        b.append(f"<p>{_e(NO_ISSUES)}</p>")
    else:
        b.append(f"<p>Issues detected: {len(report['items'])}. Severity comes from MAYA's fixed table, "
                 "not from the model.</p><h3>Fix first</h3><ol>")
        b += [f"<li><span class=\"sev {_e(i['severity'])}\">{_e(i['severity'].upper())}</span> "
              f"{_inline(i['text'].title)} at <code>{_e(i['location'])}</code></li>" for i in report["fix_first"]]
        b.append("</ol>")
    for i in report["items"]:
        t = i["text"]
        b.append(f"<section><h3><span class=\"sev {_e(i['severity'])}\">{_e(i['severity'].upper())}</span> "
                 f"{_inline(t.title)}</h3><ul>"
                 f"<li>Location: <code>{_e(i['location'])}</code> on <code>{_e(i['target'])}</code></li>"
                 f"<li>Check: <code>{_e(i['check'])}</code> ({_e(i['category'])})</li>"
                 f"<li>Evidence id: <code>{_e(i['evidence_id'])}</code></li>"
                 f"<li>Supporting evidence: <code>{_e(i['evidence_line'])}</code></li></ul>"
                 f"<p><strong>What this means:</strong> {_inline(t.plain)} {_inline(t.analogy)}</p>"
                 f"<p><strong>Technical detail:</strong> {_inline(t.technical)}</p>"
                 f"<p><strong>Suggested fix:</strong> {_inline(t.fix)}</p></section>")
    b.append("<h2>Model-verified claims</h2>")
    if report["model_claims"]:
        def note_html(c):
            if c["note"]:
                return f"Model note (unverified model text): <q>{_e(c['note'])}</q>"
            return f"Model note dropped: {_e(c['note_dropped'])}" if c["note_dropped"] else "Model note: none"
        b.append(f"<p>Order: {_e(report['order_note'])}.</p><ol>" + "".join(
            f"<li><span class=\"sev {_e(c['severity'])}\">{_e(c['severity'].upper())}</span> {_e(c['text'])}"
            f"<ul><li>Code evidence (verified): <code>{_e(c['evidence_line'])}</code></li>"
            f"<li>{note_html(c)}</li></ul></li>" for c in report["model_claims"]) + "</ol>")
    else:
        b.append("<p>None." + ("" if report["model_finished"] else " The model did not finish.") + "</p>")
    if report["insufficient"]:
        b.append("<h2>Claims not included (insufficient evidence)</h2><ul>")
        b += [f"<li><code>{_e(c.get('finding_id'))}</code>: {_e(c['reason'])}</li>" for c in report["insufficient"]]
        b.append("</ul>")
    b.append(f"<p>{_e(SCOPE_NOTE)}</p></body></html>")
    return "".join(b)


def write_report(report: dict, out_dir: str | Path, name: str) -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md, page = out_dir / f"{name}.md", out_dir / f"{name}.html"
    md.write_text(to_markdown(report), encoding="utf-8")
    page.write_text(to_html(report), encoding="utf-8")
    return md, page


assert set(TEXT) == set(TABLE) == set(OWASP)  # every check has fixed wording and a category
