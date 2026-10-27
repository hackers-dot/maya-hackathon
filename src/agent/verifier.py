"""Verify the model's final claims against code-produced findings and hashed evidence.

Claims are {finding_id, claim_type, priority, note}. The model never writes the evidence quote.
A claim survives only if:
  1. its finding exists (produced by code in this run),
  2. its claim_type matches the finding's check,
  3. the evidence record's hash verifies, and
  4. entailment: code re-checks the record for the issue (e.g. the recorded header list lacks the header,
     or an exposed file is HTTP 200 with a content signature) and derives the "support lines" that show it.
Code then attaches the first support line verbatim as the evidence. The model's note is unverified text:
restricted words are masked, and a note that mentions a host, path or number not present in the finding's
evidence is dropped (the claim is kept). Priorities order the claims only if they are unique and present;
otherwise claims are ordered by severity. Report text comes from fixed templates; severity comes from the
finding (the fixed table), never the model.
"""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.evidence import EvidenceStore, mask
from src.models import Finding
from src.scanner.checks_files import ENV_LINE, GIT_HEAD, LISTING
from src.scanner.checks_headers import SECURITY_HEADERS, VERSION

ClaimType = Literal["header_missing", "cookie_flag_missing", "version_disclosed", "file_exposed",
                    "directory_listing", "dns_record_missing", "tls_weakness"]

CLAIM_FOR_CHECK = {
    "missing_csp": "header_missing", "missing_hsts": "header_missing",
    "missing_x_frame_options": "header_missing", "missing_x_content_type_options": "header_missing",
    "cookie_missing_secure": "cookie_flag_missing", "cookie_missing_httponly": "cookie_flag_missing",
    "cookie_missing_samesite": "cookie_flag_missing",
    "server_version_disclosure": "version_disclosed",
    "exposed_env_file": "file_exposed", "exposed_git_dir": "file_exposed", "exposed_backup_file": "file_exposed",
    "directory_listing": "directory_listing",
    "missing_spf": "dns_record_missing", "missing_dmarc": "dns_record_missing",
    "weak_tls_or_expiring_cert": "tls_weakness",
}

TEMPLATES = {
    "header_missing": "{location} on {target} is missing a security header ({check}). Severity: {severity}.",
    "cookie_flag_missing": "A cookie set by {location} on {target} lacks a required flag ({check}). Severity: {severity}.",
    "version_disclosed": "{location} on {target} reveals software version information ({check}). Severity: {severity}.",
    "file_exposed": "{location} on {target} serves a file that should not be public ({check}). Severity: {severity}.",
    "directory_listing": "{location} on {target} lists its directory contents ({check}). Severity: {severity}.",
    "dns_record_missing": "{location} for {target} is missing or invalid ({check}). Severity: {severity}.",
    "tls_weakness": "{target} has a TLS or certificate weakness ({check}). Severity: {severity}.",
}

SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]
SECRET_KEY = re.compile(r"(?i)KEY|TOKEN|SECRET|PASSWORD|API")
BACKUP_SIGNATURE = re.compile(r"(?i)\b(?:CREATE TABLE|INSERT INTO)\b|PK\x03\x04")


def _mask_env(line: str) -> str:
    """KEY=value -> KEY=<first 3>...<last 2>. Idempotent on values the store already masked."""
    key, _, val = line.partition("=")
    return f"{key}={mask(val.strip())}"
HEADER_FOR_CHECK = {check: header for header, check in SECURITY_HEADERS}
COOKIE_FLAG = {"cookie_missing_secure": "secure", "cookie_missing_httponly": "httponly",
               "cookie_missing_samesite": "samesite"}
HEADER_LIST_PREFIX = "recorded response headers: "


NOTE_MAX = 160
RESTRICTED = re.compile(r"(?i)\b\w*(?:fake|scam|hack|compliant|secure)\w*\b")
_SEP = r"(?:,|;|/|&|and|or)"


def _is_cookie_attribute(note: str, m: re.Match) -> bool:
    """'Secure' used as the cookie attribute name (not as 'safe'): quoted, followed by flag/attribute,
    or listed next to HttpOnly / SameSite."""
    if m.group().lower() != "secure":
        return False
    before, after = note[:m.start()], note[m.end():]
    return bool((before and after and before[-1] in "`'\"" and after[0] in "`'\"")
                or re.match(r"\s+(?:flag|attribute)s?\b", after, re.I)
                or re.match(rf"\s*{_SEP}\s*(?:HttpOnly|SameSite)\b", after, re.I)
                or re.search(rf"\b(?:HttpOnly|SameSite)\s*{_SEP}\s*$", before, re.I))


def mask_note(note: str) -> str:
    return RESTRICTED.sub(lambda m: m.group() if _is_cookie_attribute(note, m) else "[masked]", note)
# things a note may only mention if they appear in the finding's own evidence
NOTE_REFERENCES = re.compile(
    r"(?i)\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b"     # host names (also file names such as db.sql)
    r"|(?<![\w./])/[\w./~-]*"                  # paths
    r"|\d+(?:\.\d+)*"                          # numbers, versions, IPs
)


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")  # e.g. a model-supplied "severity" makes the claim malformed
    finding_id: str = Field(min_length=1)
    claim_type: ClaimType
    priority: int | None = Field(default=None, ge=1)
    note: str = ""
    quoted_evidence: str | None = None  # legacy: ignored; code attaches the evidence line itself


def _headers(record: dict) -> list[tuple[str, str]]:
    return [(k.lower(), v) for k, v in record["response"]["headers"]]


def header_list_line(record: dict) -> str:
    """One line naming every response header the record holds (all security headers are always recorded)."""
    return HEADER_LIST_PREFIX + ", ".join(sorted({k for k, _ in _headers(record)}))


def record_text(record: dict) -> str:
    """The quotable text of a stored record: request line, status, header list, headers, body."""
    req, resp = record["request"], record["response"]
    lines = [f"{req['method']} {req['url']}", f"HTTP {resp['status']}", header_list_line(record)]
    lines += [f"{k}: {v}" for k, v in resp["headers"]]
    return "\n".join(lines) + "\n\n" + resp["body"]


def support_lines(finding: Finding, record: dict) -> list[str]:
    """Lines of the record that show this finding's issue; [] if the record does not show it."""
    check, resp = finding.check, record["response"]
    headers = _headers(record)
    value = lambda name: next((v for k, v in headers if k == name), None)  # noqa: E731
    suffix = finding.id.rsplit(":", 1)[-1]
    body_lines = [l for l in resp["body"].splitlines() if l.strip()]
    ok_200 = resp["status"] == 200
    not_html = "html" not in (value("content-type") or "").lower()

    if check in HEADER_FOR_CHECK:
        header, present = HEADER_FOR_CHECK[check], (value(HEADER_FOR_CHECK[check]) or "").strip()
        if check == "missing_x_content_type_options":
            if present.lower() == "nosniff":
                return []
            return [header_list_line(record)] + ([f"{header}: {present}"] if present else [])
        if check == "missing_x_frame_options" and "frame-ancestors" in (value("content-security-policy") or "").lower():
            return []
        return [] if present else [header_list_line(record)]
    if check in COOKIE_FLAG:
        lines = []
        for k, v in headers:
            if k == "set-cookie" and v.split("=", 1)[0].strip() == suffix:
                attrs = {a.strip().split("=", 1)[0].lower() for a in v.split(";")[1:]}
                if COOKIE_FLAG[check] not in attrs:
                    lines.append(f"{k}: {v}")
        return lines
    if check == "server_version_disclosure":
        v = value(suffix)
        return [f"{suffix}: {v}"] if v and VERSION.search(v) else []
    if check == "exposed_env_file":
        if not (ok_200 and not_html):
            return []
        env = [l for l in body_lines if ENV_LINE.search(l.encode())]
        secret = [_mask_env(l) for l in env if SECRET_KEY.search(l.split("=", 1)[0])]
        return secret + [l for l in env if not SECRET_KEY.search(l.split("=", 1)[0])]
    if check == "exposed_git_dir":
        body = resp["body"].strip()
        return [body] if ok_200 and GIT_HEAD.match(body.encode()) else []
    if check == "exposed_backup_file":
        if not (ok_200 and not_html and body_lines):
            return []
        signature = [l.replace("\x03", "\\x03").replace("\x04", "\\x04") for l in body_lines if BACKUP_SIGNATURE.search(l)]
        return signature + [f"content-type: {value('content-type')}"]
    if check == "directory_listing":
        return [l for l in body_lines if LISTING.search(l.encode())] if ok_200 else []
    return []  # DNS/TLS evidence is not recorded over HTTP, so nothing here can support those claims


def evidence_line(finding: Finding, store: EvidenceStore | None) -> str:
    """The code-derived line that shows a finding's issue, for reports and tool summaries."""
    rid = finding.evidence[0].record_id
    try:
        if store is not None and rid is not None:
            if not store.verify(rid):
                return "(evidence record failed hash verification)"
            lines = support_lines(finding, store.get(rid))
            if lines:
                return lines[0]
    except KeyError:
        pass
    return finding.evidence[0].data  # e.g. DNS findings, or no store: the redacted excerpt captured by the check


def check_note(note: str, evidence_text: str) -> tuple[str | None, str | None]:
    """Return (masked note, None) or (None, reason). A bad note is dropped; the claim itself is kept."""
    note = note.strip()
    if not note:
        return None, None
    if len(note) > NOTE_MAX:
        return None, f"note longer than {NOTE_MAX} characters"
    haystack = evidence_text.lower()
    for ref in NOTE_REFERENCES.findall(note):
        ref = ref.rstrip(".")  # sentence punctuation, not part of the path or number
        if ref and ref.lower() not in haystack:
            return None, f"note mentions {ref!r}, which is not in this finding's evidence"
    return mask_note(note), None


def verify(claims, findings: dict[str, Finding], store: EvidenceStore) -> dict:
    """Returns {"verified", "insufficient", "order", "order_note"}; verified is in the final display order."""
    verified, insufficient = [], []

    def drop(raw, reason):
        d = raw if isinstance(raw, dict) else {}
        q = d.get("quoted_evidence")
        insufficient.append({"finding_id": d.get("finding_id"), "status": "insufficient evidence", "reason": reason,
                             "quoted_evidence": q if isinstance(q, str) else None})

    for raw in claims if isinstance(claims, list) else []:
        try:
            claim = Claim.model_validate(raw)
        except ValidationError as e:
            drop(raw, f"malformed claim: {e.errors()[0]['msg']} ({'.'.join(map(str, e.errors()[0]['loc']))})")
            continue
        finding = findings.get(claim.finding_id)
        if finding is None:
            drop(raw, "unknown finding_id")
            continue
        if CLAIM_FOR_CHECK[finding.check] != claim.claim_type:
            drop(raw, f"claim_type {claim.claim_type!r} does not match check {finding.check!r}")
            continue
        record_id = finding.evidence[0].record_id
        try:
            if record_id is None or not store.verify(record_id):
                drop(raw, "evidence record missing or hash mismatch")
                continue
            record = store.get(record_id)
        except KeyError:
            drop(raw, "evidence record missing or hash mismatch")
            continue
        support = support_lines(finding, record)
        if not support:
            drop(raw, "the evidence record does not show the issue")
            continue
        note, note_problem = check_note(claim.note, record_text(record))
        verified.append({
            "finding_id": finding.id, "check": finding.check, "severity": finding.severity,
            "location": finding.location, "claim_type": claim.claim_type, "evidence_id": record_id,
            "supporting_evidence": support[0],  # attached by code, verbatim from the hash-verified record
            "priority": claim.priority, "note": note, "note_dropped": note_problem, "demo": finding.demo,
            "text": TEMPLATES[claim.claim_type].format(location=finding.location, target=finding.target,
                                                       check=finding.check, severity=finding.severity),
        })

    priorities = [v["priority"] for v in verified]
    by_severity = lambda v: (SEVERITY_ORDER.index(v["severity"]), v["check"], v["location"])  # noqa: E731
    if verified and None not in priorities and len(set(priorities)) == len(priorities):
        verified.sort(key=lambda v: v["priority"])
        order, order_note = "model", "ordered by the model's priority"
    else:
        verified.sort(key=by_severity)
        order = "severity"
        order_note = ("ordered by severity (model priorities were duplicate or missing)" if verified
                      else "no verified claims")
    return {"verified": verified, "insufficient": insufficient, "order": order, "order_note": order_note}
