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
STEP_GROUP = {"no_account_type": "account", "no_asset_type": "asset", "no_connect": "connect", "no_consent": "consent",
              "no_asset": "website", "ready_to_scan": "scan", "scan_finished": "findings", "proposals_not_approved": "approve",
              "approved_not_applied": "apply", "applied_not_retested": "retest", "retest_fixed": "fixed",
              "retest_still_present": "still"}

# group -> audience -> (new, technical)
STEP_CONTEXT = {
    "account": {
        "shop": ("For a shop, Company is the usual choice: it lets me check the storefront.",
                 "Pick Company for the storefront; Individual is chat only."),
        "company": ("For a company or team, choose Company to check the site.",
                    "Company also unlocks the connectors; Individual is chat only."),
        "clinic": ("For a clinic's booking site, choose Company.",
                   "Pick Company for the booking site; Individual is chat only."),
        "developer": ("To check an app you run, choose Company.",
                      "Company adds the ZIP, logs and GitHub connectors; Individual is chat only."),
        "other": ("Pick Company if you have a website to check.",
                  "Individual is chat only, for learning."),
    },
    "asset": {
        "shop": ("Your online shop counts as a website.",
                 "The storefront is one website asset."),
        "company": ("The company site counts, even if it's just a landing page.",
                    "Add one asset per company origin."),
        "clinic": ("Your booking or practice website counts.",
                   "The booking site is one website asset."),
        "developer": ("Your web app counts as a website here.",
                      "A web app counts; one origin per asset."),
        "other": ("Any site you own counts.",
                  "One website per asset."),
    },
    "connect": {
        "shop": ("Nothing touches your shop until you say so.",
                 "Yes opens the storefront URL step."),
        "company": ("Nothing touches the company site until you agree.",
                    "Yes opens the URL step for the company site."),
        "clinic": ("Nothing touches the booking site until you agree.",
                   "Yes opens the URL step for the booking site."),
        "developer": ("No request hits your app at this step.",
                      "Yes only routes to the URL form."),
        "other": ("Nothing is checked at this step.",
                  "Yes opens the URL step."),
    },
    "consent": {
        "shop": ("I never place orders, log in or change your stock.",
                 "No forms, logins or writes to the storefront."),
        "company": ("I never log in, submit forms or change anything on the company site.",
                    "No authentication, form submission or writes."),
        "clinic": ("I never book appointments, log in or open private areas of the booking site.",
                   "No logins and no form posts to the booking system."),
        "developer": ("No logins, no payloads, no brute force.",
                      "GET/HEAD only, no auth, no fuzzing; the scope guard enforces it in code."),
        "other": ("I never log in or fill in forms on your site.",
                  "No logins or form posts."),
    },
    "website": {
        "shop": ("The practice shop lets you see a whole scan before touching your real store.",
                 "Enter the storefront's origin."),
        "company": ("The practice shop is an easy way to show the team how it works.",
                    "Enter the company site's origin."),
        "clinic": ("That way you see a whole scan before your real booking site is checked.",
                   "Enter the booking site's origin."),
        "developer": ("The demo app is deliberately vulnerable, so you see real findings.",
                      "Real origins verify via a meta tag, a /.well-known file or a DNS TXT record."),
        "other": ("The practice shop shows what a full scan looks like.",
                  "Enter your site's origin."),
    },
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
                      "Approval is an audit-log entry, like a review, not a merge."),
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


# audience words for the per-check lines below
WORDS = {
    "shop": {"site": "your shop's website", "people": "customers", "person": "customer"},
    "company": {"site": "the company website", "people": "staff and clients", "person": "client"},
    "clinic": {"site": "your clinic's booking site", "people": "patients", "person": "patient"},
    "developer": {"site": "your app", "people": "users", "person": "user"},
    "other": {"site": "your website", "people": "visitors", "person": "visitor"},
}
# a later finding of the same kind: the check's own fact, in the audience's words (new and some levels)
CHECK_LINE = {
    "exposed_env_file": "Anyone, not just {people}, can download a file from {site} that usually holds passwords and API keys.",
    "exposed_backup_file": "Anyone can download a backup copy from {site}; it may hold data or source code.",
    "exposed_git_dir": "The source code behind {site} and its history can be downloaded.",
    "directory_listing": "A folder on {site} lists all its files, including ones never meant for {people}.",
    "missing_csp": "Browsers loading {site} are not told which scripts may run, so an injected script would run without limits.",
    "missing_hsts": "Browsers are not told to always use HTTPS for {site}, so a {person} can start on unencrypted HTTP.",
    "missing_x_frame_options": "Another website could show {site} in a hidden frame and trick {people} into clicking on it.",
    "missing_x_content_type_options": "Browsers may guess file types on {site} and could treat an uploaded file as a script.",
    "cookie_missing_httponly": "Scripts on the page can read this cookie, so an injected script could copy a {person}'s session.",
    "cookie_missing_secure": "This cookie may travel over unencrypted connections, where someone on a {person}'s network could read it.",
    "cookie_missing_samesite": "Other websites' requests also carry this cookie, which can help them act on a {person}'s behalf.",
    "server_version_disclosure": "The server behind {site} announces its exact software versions, so known weaknesses are easier to look up.",
    "missing_spf": "Others can more easily send email that looks like it comes from you, and {people} may trust it.",
    "missing_dmarc": "Mail receivers have no instruction for email that fakes your domain, so fakes can reach {people}.",
    "cert_expires_within_14_days": "The certificate for {site} expires very soon (or already has); browsers will then warn {people}.",
    "cert_expires_within_30_days": "The certificate for {site} expires within a month; browsers warn {people} once it lapses.",
    "tls_protocol_below_1_2": "The server behind {site} still agrees to an old encryption protocol version.",
    "weak_tls_or_expiring_cert": "The encrypted connection to {site} uses outdated settings or a certificate close to expiry.",
    "outdated_js_library": "Pages on {site} load an old release of a JavaScript library with publicly documented issues.",
}
# technical fix wording where the report's text would use a word the pet never says as a claim
TECH_FIX = {"cookie_missing_secure": "Add the HTTPS-only attribute to the cookie."}
# one audience sentence for the score and Fix first tour stops: (new, technical)
TOUR_EXTRA = {
    "score": {
        "shop": ("It reflects only what a customer's browser could see.", "Outside-in checks of the storefront only."),
        "company": ("It reflects only what any visitor to the company site could see.", "Outside-in checks of the company site only."),
        "clinic": ("It reflects only what a patient's browser could see on the booking site.", "Outside-in checks of the booking site only."),
        "developer": ("It reflects only these outside-in checks, not your code.", "Outside-in checks only; no source code was read."),
        "other": ("It reflects only these checks.", "Outside-in checks only."),
    },
    "fixfirst": {
        "shop": ("They are the highest severity, so start there for your customers.",
                 "Each card names the storefront path and its fix template."),
        "company": ("They are the highest severity: a good first item for the team's list.",
                    "Each card names the path and the fix template for the ticket."),
        "clinic": ("They are the highest severity on the booking site, so start there.",
                   "Each card names the booking-site path and its fix template."),
        "developer": ("Highest severity first; each has a fix template you can review.",
                      "Each card has the path, the evidence and a fix template to diff against your config."),
        "other": ("They are the highest severity, so start there.", "Each card names the path and the fix template."),
    },
}


def finding_line(check: str, title: str, location: str, severity: str, audience: str, level: str,
                 index: int, total: int, first_of_kind: bool = True) -> str:
    """The first finding of each kind gets the audience sentence; later ones of the same kind get that check's own
    plain explanation (or, for technical readers, its fix), so a tour never repeats one sentence."""
    from src.report import TEXT
    cat = CATEGORY.get(check)
    if first_of_kind and cat:
        context = FINDING_CONTEXT[cat][audience][level_index(level)]
    elif level == "technical" and check in TEXT:  # the exact fix, framed by where it applies
        fix = TECH_FIX.get(check) or TEXT[check].fix.replace("`", "")
        context = f"For {WORDS[audience]['site']}: {fix[0].lower()}{fix[1:]}"
    elif check in CHECK_LINE:
        context = CHECK_LINE[check].format(**WORDS[audience])
    elif check in TEXT:
        context = TEXT[check].plain
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
    i = level_index(level)
    return {
        "score": score + " " + TOUR_EXTRA["score"][audience][i],
        "findings": _finding_lines(findings, audience, level),
        "fixfirst": ("Fix first: the top three by the severity table." if tech
                     else "These Fix first cards are your top three.") + " " + TOUR_EXTRA["fixfirst"][audience][i],
        "approve": ("Proposals: approving is your call; I never approve for you." if tech
                    else "Here are the fix proposals. Approving is your decision; I never approve for you.")
        + " " + STEP_CONTEXT["approve"][audience][i],
    }
