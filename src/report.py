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


def to_html(report: dict) -> str:
    u = report["usage"]
    b = ["<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">",
         "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">",
         f"<title>MAYA report</title><style>{CSS}</style></head><body><h1>MAYA report</h1>"]
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
