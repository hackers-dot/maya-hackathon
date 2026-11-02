"""Audience-aware English wording for the guide pet (fixed templates, never from a model).

Two dimensions: LEVEL (new / some / technical; "some" reuses "new") and AUDIENCE (what the user runs).
- STEP_CONTEXT adds one short, audience-specific sentence to the guide steps about findings, approval, apply and
  re-test.
- FINDING_CONTEXT explains each kind of finding for that audience (an everyday analogy for "new", the precise
  detail for "technical").
- tour_lines() builds the post-scan tour stops.
Rules: no reassurance or compliance claims (secure, safe, hacked, guarantee, compliant, protected), no time or cost
estimates, and only numbers taken from the stored results. The clinic wording is about the website and the data it
handles: security guidance only, never medical advice and never a claim about privacy law.
"""

AUDIENCES = ("shop", "company", "clinic", "developer", "other")
AUDIENCE_LABELS = {"shop": "Shop or small business", "company": "Company or team",
                   "clinic": "Clinic or healthcare practice", "developer": "Developer", "other": "Something else"}

# state -> context group (states without a group get no extra sentence)
STEP_GROUP = {"ready_to_scan": "scan", "scan_finished": "findings", "proposals_not_approved": "approve",
              "approved_not_applied": "apply", "applied_not_retested": "retest", "retest_fixed": "fixed",
              "retest_still_present": "still"}

# group -> audience -> (new, technical)
STEP_CONTEXT = {
    "scan": {
        "shop": ("Think of it as walking past your shop window to see what a customer can see.",
                 "Checks headers, cookies, exposed files and outdated scripts on the storefront; nothing is changed."),
        "company": ("It sees only what any visitor to your company site could see.",
                    "Unauthenticated read-only requests: headers, cookies, exposed files, script versions."),
        "clinic": ("It looks at the booking site the way a patient's browser would, and changes nothing.",
                   "Read-only requests to the booking site: headers, cookies, exposed files, script versions."),
        "developer": ("It reads your app from the outside, like a first-time visitor.",
                      "GET/HEAD only, scope-guarded; each response is stored with a sha256 as evidence."),
        "other": ("It only looks at what any visitor could see.",
                  "Read-only requests inside the verified scope; responses are stored as evidence."),
    },
    "findings": {
        "shop": ("Each one is something a customer, or a stranger, could stumble on.",
                 "Storefront and checkout pages matter most."),
        "company": ("Each one could touch client data or your team's accounts.",
                    "Each finding links to its stored evidence."),
        "clinic": ("Each one is about the website and the patient details it handles.",
                   "Booking and contact pages matter most."),
        "developer": ("Each one comes with the exact response that shows it.",
                      "Severity is a fixed table, not a model; evidence is hashed."),
        "other": ("Each one comes with proof you can open.",
                  "Each comes with stored evidence."),
    },
    "approve": {
        "shop": ("It's like signing off a repair before anyone touches the till.",
                 "Approval writes your name to the hash-chained audit log; the storefront is untouched."),
        "company": ("Your sign-off is recorded, so the team can see who agreed to what.",
                    "Each approval is a signed-off entry in the hash-chained audit log."),
        "clinic": ("Your sign-off is recorded before anything on the booking site changes.",
                   "Approval is logged with your name; the booking site itself is not touched."),
        "developer": ("Think of it as a code review approval, not a merge.",
                      "Approval is an audit-log entry; Apply is the separate, demo-only deploy step."),
        "other": ("Your sign-off is recorded, and nothing changes yet.",
                  "Approval is recorded in the hash-chained audit log only."),
    },
    "apply": {
        "shop": ("On your real shop, you or your web person paste the snippet.",
                 "For the live storefront, add the snippet to the web server config yourself."),
        "company": ("For the real company site, pass the snippet to whoever runs the server.",
                    "Production changes go through your own change process; MAYA never deploys there."),
        "clinic": ("For the real booking site, give the snippet to the person who manages it.",
                   "MAYA never changes the real booking site; the snippet goes to whoever runs the server."),
        "developer": ("For your own app, commit the snippet and deploy it your usual way.",
                      "Apply edits only the loopback demo's control file; ship real changes through CI."),
        "other": ("On your own site, you apply the snippet yourself.",
                  "Apply touches only the loopback demo app; real servers are yours to change."),
    },
    "retest": {
        "shop": ("It's like trying the shop door again after fixing the lock.",
                 "Same read-only checks again; a finding is FIXED only when the check no longer sees it."),
        "company": ("A fix counts only when the same check proves it.",
                    "Re-test reruns the identical checks and compares finding ids."),
        "clinic": ("I check the booking site the same way again to confirm the change.",
                   "Identical read-only checks; FIXED only when the finding id is gone."),
        "developer": ("Like rerunning the failing test after your fix.",
                      "Reruns the same checks; resolved, still-present and new ids are listed."),
        "other": ("I run the same checks again to see what really changed.",
                  "Same checks, compared by finding id."),
    },
    "fixed": {
        "shop": ("Your customers' pages have fewer open doors in these checks.",
                 "Resolved ids are in the re-test result and the evidence pack."),
        "company": ("You can share the client report to show what changed.",
                    "The client report carries the re-test result and the manifest hash."),
        "clinic": ("The booking site has fewer open issues in these checks; patients see no difference.",
                   "The re-test result and manifest hash are in the client report."),
        "developer": ("Worth adding the same header checks to your CI.",
                      "Pin it with a CI assertion on the response headers."),
        "other": ("You can download the report to keep a record.",
                  "The evidence pack lists every resolved id."),
    },
    "still": {
        "shop": ("The rest still matter for customers; their fixes are waiting below.",
                 "Remaining ids keep their stored evidence; approve and apply, then re-test."),
        "company": ("The remaining items still have their evidence attached.",
                    "Still-present ids keep their evidence; repeat approve, apply, re-test."),
        "clinic": ("The remaining items on the booking site still need a fix.",
                   "Still-present ids keep their evidence; the same cycle applies."),
        "developer": ("Some checks still fail; the diff shows which.",
                      "Still-present ids are listed; fix and rerun."),
        "other": ("A few issues are still there; their fixes are below.",
                  "Remaining ids are listed with their evidence."),
    },
}

# check id -> finding category
CATEGORY = {
    "exposed_env_file": "files", "exposed_backup_file": "files", "exposed_git_dir": "files", "directory_listing": "files",
    "missing_csp": "headers", "missing_hsts": "headers", "missing_x_frame_options": "headers",
    "missing_x_content_type_options": "headers",
    "cookie_missing_httponly": "cookies", "cookie_missing_secure": "cookies", "cookie_missing_samesite": "cookies",
    "server_version_disclosure": "disclosure", "missing_spf": "email", "missing_dmarc": "email",
    "cert_expires_within_14_days": "tls", "cert_expires_within_30_days": "tls", "tls_protocol_below_1_2": "tls",
    "weak_tls_or_expiring_cert": "tls", "outdated_js_library": "js",
}
for _c in ("code_dangerous_eval", "code_debug_enabled", "code_env_file_committed", "code_hardcoded_secret",
           "code_outdated_dependency", "code_shell_true", "code_unsafe_deserialization", "code_weak_password_hash"):
    CATEGORY[_c] = "code"
for _c in ("log_failed_login_burst", "log_injection_pattern", "log_login_after_failures", "log_many_4xx",
           "log_sensitive_path_probe"):
    CATEGORY[_c] = "logs"
for _c in ("lookalike_has_dns", "lookalike_no_dns", "lookalike_recent_certificate"):
    CATEGORY[_c] = "lookalike"
for _c in ("msg_domain_not_in_list", "msg_domain_resembles_yours", "msg_payment_request", "msg_phone_not_in_list",
           "msg_urgency_language"):
    CATEGORY[_c] = "message"

# category -> audience -> (new, technical)
FINDING_CONTEXT = {
    "files": {
        "shop": ("Like leaving the back-office key on the counter: anyone can open that file.",
                 "A config or backup file is served publicly; block it at the web server and rotate any keys in it."),
        "company": ("A file meant for the server room can be downloaded by anyone.",
                    "Publicly served config or backup (OWASP A05); evidence is the masked response body."),
        "clinic": ("A file meant to stay behind the scenes can be opened by anyone visiting the booking site.",
                   "Publicly served file (OWASP A05); block the path and rotate any credentials it held."),
        "developer": ("A deploy left a private file in the web root.",
                      "Served from the web root (OWASP A05); deny the path and rotate secrets; the body is stored masked."),
        "other": ("A private file can be downloaded by anyone.",
                  "Publicly served file (OWASP A05); block the path and rotate any keys."),
    },
    "headers": {
        "shop": ("Browsers get no house rules from your shop's pages, so they allow more than they should.",
                 "Missing security header (CSP, HSTS, X-Frame-Options or nosniff); set it at the web server."),
        "company": ("Your site doesn't tell browsers its house rules, so they fall back to loose defaults.",
                    "Missing CSP, HSTS or framing header (OWASP A05); evidence is the stored response headers."),
        "clinic": ("The booking site doesn't tell browsers its house rules for its pages.",
                   "Missing response header such as CSP or HSTS; add it at the web server or CDN."),
        "developer": ("A response header your framework could send is missing.",
                      "CSP, HSTS or X-Frame-Options absent (OWASP A05); add it in server config and assert it in CI."),
        "other": ("Your pages don't give browsers a few useful house rules.",
                  "Missing security response header (OWASP A05)."),
    },
    "cookies": {
        "shop": ("A customer's login cookie is handled more loosely than it should be, like a ticket anyone can copy.",
                 "Session cookie lacks HttpOnly, SameSite or the HTTPS-only flag; set them where the cookie is issued."),
        "company": ("Staff or client login cookies are missing a setting that keeps them private.",
                    "Cookie flag missing (HttpOnly, SameSite or HTTPS-only); evidence is the Set-Cookie header."),
        "clinic": ("A login cookie on the booking site is handled too loosely.",
                   "Set-Cookie without HttpOnly, SameSite or the HTTPS-only flag; fix where the session is created."),
        "developer": ("A cookie flag is missing in your session setup.",
                      "Set-Cookie lacks HttpOnly, SameSite or the HTTPS-only flag (OWASP A05); fix the session config."),
        "other": ("A login cookie is missing a setting that keeps it to itself.",
                  "Cookie flag missing: HttpOnly, SameSite or HTTPS-only."),
    },
    "disclosure": {
        "shop": ("Your site tells strangers exactly which software it runs, like a label on the till.",
                 "Server header reveals the software version; hide or shorten it in the server config."),
        "company": ("Your server announces its software version to every visitor.",
                    "Version disclosure in the Server header (OWASP A05)."),
        "clinic": ("The booking site announces which software it runs.",
                   "Server header shows the version; turn version tokens off."),
        "developer": ("Your server banner leaks its version.",
                      "Server header version disclosure; set server_tokens off or the equivalent."),
        "other": ("The site tells everyone which software version it runs.",
                  "Version shown in the Server header."),
    },
    "email": {
        "shop": ("Without these records, someone could send emails that look like they come from your shop.",
                 "SPF or DMARC TXT record missing; add it in your DNS (looked up via cloudflare-dns.com)."),
        "company": ("Fake emails could more easily pretend to come from your company.",
                    "SPF/DMARC record missing in DNS; checked through DNS-over-HTTPS at cloudflare-dns.com."),
        "clinic": ("Fake emails could more easily pretend to come from your clinic, for example about appointments.",
                   "SPF/DMARC record missing; add the TXT record at your DNS provider."),
        "developer": ("Your domain doesn't publish its email sending rules.",
                      "Missing SPF or DMARC TXT record (DoH lookup via cloudflare-dns.com)."),
        "other": ("Fake emails in your name are easier to send.",
                  "SPF or DMARC record missing in DNS."),
    },
    "tls": {
        "shop": ("The padlock on your shop's pages needs attention, like a shop licence close to expiry.",
                 "Certificate expiring soon or old TLS version; renew or update at the server."),
        "company": ("The site's padlock certificate needs renewing or updating.",
                    "TLS certificate near expiry or protocol below TLS 1.2 (one handshake to port 443)."),
        "clinic": ("The padlock on the booking site needs attention before patients see a warning.",
                   "Certificate near expiry or old TLS; renew it and turn on automatic renewal."),
        "developer": ("Your TLS setup needs an update.",
                      "Cert notAfter is close or the handshake offered an old protocol; fix renewal or the cipher config."),
        "other": ("The site's padlock certificate needs attention.",
                  "TLS certificate or protocol issue on port 443."),
    },
    "js": {
        "shop": ("A script on your shop's pages is an old version with known problems.",
                 "Outdated JavaScript library by version string; update it in the page."),
        "company": ("A script on the company site is out of date and has known problems.",
                    "Outdated library version detected in a script tag (OWASP A06)."),
        "clinic": ("A script on the booking site is an old version.",
                   "Outdated JavaScript library (OWASP A06); update the bundled file."),
        "developer": ("A front-end dependency is behind.",
                      "Outdated library by filename version (OWASP A06); bump it and rebuild."),
        "other": ("A script on the site is out of date.",
                  "Outdated JavaScript library (OWASP A06)."),
    },
    "code": {
        "shop": ("Something in your shop's code needs tidying, like a spare key taped inside a drawer.",
                 "Code finding from the uploaded ZIP; evidence is a masked line, the file was never saved."),
        "company": ("The code your team uploaded has a risky pattern.",
                    "Pattern found in the uploaded code (masked excerpt); the ZIP was read in memory only."),
        "clinic": ("The code behind your site has something worth fixing.",
                   "Code pattern found in the upload; only a masked excerpt is stored."),
        "developer": ("Your code has a pattern worth a fix before the next release.",
                      "Static match in the upload (masked line, file:line); test paths are ranked one level lower."),
        "other": ("The code has something worth a look.",
                  "Pattern found in the uploaded code."),
    },
    "logs": {
        "shop": ("Your logs show someone trying the doors, like a stranger rattling the shop shutters.",
                 "Pattern in the uploaded log, grouped by source address; detection only."),
        "company": ("The logs show unusual activity from one place.",
                    "Log detection grouped by source address; thresholds are configurable heuristics."),
        "clinic": ("The logs show repeated attempts against the site.",
                   "Log pattern grouped by source; the file was read in memory and not saved."),
        "developer": ("Your access logs show a suspicious pattern.",
                      "Parsed log detection per source address; a heuristic, not proof of a break-in."),
        "other": ("The logs show something unusual.",
                  "Log pattern grouped by source address."),
    },
    "lookalike": {
        "shop": ("A name that looks like your shop's could fool customers.",
                 "Lookalike domain found via DNS (cloudflare-dns.com) and certificate logs (crt.sh); never visited."),
        "company": ("A web address that looks like yours exists.",
                    "Lookalike domain from DNS and certificate-log lookups; the domain itself is never contacted."),
        "clinic": ("A web address that looks like the clinic's could mislead patients.",
                   "Lookalike domain seen in DNS or crt.sh; MAYA never visits it."),
        "developer": ("Someone registered a name close to yours.",
                      "Lookalike candidate with DNS or a recent certificate (crt.sh); passive lookups only."),
        "other": ("A name like yours exists online.",
                  "Lookalike domain found by passive lookups."),
    },
    "message": {
        "shop": ("This message uses tricks that fake shop messages often use.",
                 "Message check: addresses and numbers compared with your official lists by code."),
        "company": ("The message has signs often seen in fake company messages.",
                    "Code compared the message's addresses and numbers with your official lists."),
        "clinic": ("The message has signs often seen in fake messages to patients.",
                   "Addresses and numbers in the message were compared with the clinic's official lists."),
        "developer": ("The message has phishing-style signals.",
                      "Extracted addresses compared with your lists by code; the model only read the text."),
        "other": ("The message looks like a common fake.",
                  "Compared with your official lists."),
    },
}


def level_index(level: str) -> int:
    return 1 if level == "technical" else 0


def step_context(state: str, audience: str, level: str) -> str:
    group = STEP_GROUP.get(state)
    if not group:
        return ""
    return STEP_CONTEXT[group][audience][level_index(level)]


def finding_line(check: str, title: str, location: str, severity: str, audience: str, level: str,
                 index: int, total: int, first_of_kind: bool = True) -> str:
    """The first finding of each kind gets the audience sentence; later ones of the same kind get that check's own
    plain explanation (or, for technical readers, its fix), so a tour never repeats one sentence."""
    from src.report import TEXT
    cat = CATEGORY.get(check)
    if first_of_kind and cat:
        context = FINDING_CONTEXT[cat][audience][level_index(level)]
    elif check in TEXT:
        context = TEXT[check].fix.replace("`", "") if level == "technical" else TEXT[check].plain
    else:
        context = ""
    head = f"{severity.capitalize()} issue, {index} of {total}: {title} at {location}."
    return (head + " " + context).strip()


def _finding_lines(findings: list[dict], audience: str, level: str) -> dict:
    seen, out = set(), {}
    for i, f in enumerate(findings):
        cat = CATEGORY.get(f["check"], f["check"])
        out[f["id"]] = finding_line(f["check"], f["title"], f["location"], f["severity"], audience, level, i + 1,
                                    len(findings), cat not in seen)
        seen.add(cat)
    return out


def tour_lines(findings: list[dict], score_line: str, audience: str, level: str) -> dict:
    """findings: open findings in severity order ({id, check, title, location, severity})."""
    tech = level == "technical"
    n = len(findings)
    if not n:
        score = ("Score: no issues detected in the checks run. That covers only these checks."
                 if tech else "Here's your score: no issues detected in the checks run. That covers only these checks.")
    else:
        score = (f"Heuristic score {score_line} from {n} finding(s), by the fixed severity table."
                 if tech else f"This is your score: {score_line}. The checks found {n} issue(s). Let's look at them in order.")
    return {
        "score": score,
        "findings": _finding_lines(findings, audience, level),
        "fixfirst": ("Fix first: the top three by the severity table, each with its fixed fix template."
                     if tech else "These Fix first cards are the three things to do first."),
        "approve": ("Proposals: approval is an audit-log entry; Apply is separate and demo-only. I never approve for you."
                    if tech else "Here are the fix proposals. Approving is your decision; I never approve for you."),
    }
