"""Verify the model's final claims against code-produced findings and hashed evidence.

A claim survives only if:
  1. its finding exists (produced by code in this run),
  2. its claim_type matches the finding's check,
  3. the evidence record's hash verifies,
  4. the quote appears verbatim in that record, and
  5. entailment: code re-checks the record for the issue (e.g. the recorded header list lacks the header,
     or an exposed file is HTTP 200 with a content signature) and derives the "support lines" that show it;
     the quote must lie inside one of them. A real quote that does not show the issue (e.g. homepage body
     text for a missing header) is dropped with "quote does not support claim".
Report text comes from fixed templates; severity comes from the finding (the fixed table), never the model.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.evidence import EvidenceStore
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

HEADER_FOR_CHECK = {check: header for header, check in SECURITY_HEADERS}
COOKIE_FLAG = {"cookie_missing_secure": "secure", "cookie_missing_httponly": "httponly",
               "cookie_missing_samesite": "samesite"}
HEADER_LIST_PREFIX = "recorded response headers: "


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")  # e.g. a model-supplied "severity" makes the claim malformed
    finding_id: str = Field(min_length=1)
    claim_type: ClaimType
    quoted_evidence: str = Field(min_length=4)


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
        return [l for l in body_lines if ENV_LINE.search(l.encode())] if ok_200 and not_html else []
    if check == "exposed_git_dir":
        body = resp["body"].strip()
        return [body] if ok_200 and GIT_HEAD.match(body.encode()) else []
    if check == "exposed_backup_file":
        return ([f"content-type: {value('content-type')}"] + body_lines[:1]) if ok_200 and not_html and body_lines else []
    if check == "directory_listing":
        return [l for l in body_lines if LISTING.search(l.encode())] if ok_200 else []
    return []  # DNS/TLS evidence is not recorded over HTTP, so nothing here can support those claims


def verify(claims, findings: dict[str, Finding], store: EvidenceStore) -> dict:
    verified, insufficient = [], []

    def drop(raw, reason):
        fid = raw.get("finding_id") if isinstance(raw, dict) else None
        insufficient.append({"finding_id": fid, "status": "insufficient evidence", "reason": reason})

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
        if claim.quoted_evidence not in record_text(record):
            drop(raw, "quote not found verbatim in evidence")
            continue
        support = support_lines(finding, record)
        if not support:
            drop(raw, "quote does not support claim: the evidence record does not show the issue")
            continue
        line = next((l for l in support if claim.quoted_evidence in l), None)
        if line is None:
            drop(raw, "quote does not support claim")
            continue
        verified.append({
            "finding_id": finding.id, "check": finding.check, "severity": finding.severity,
            "location": finding.location, "claim_type": claim.claim_type, "evidence_id": record_id,
            "quoted_evidence": claim.quoted_evidence, "supporting_evidence": line, "demo": finding.demo,
            "text": TEMPLATES[claim.claim_type].format(location=finding.location, target=finding.target,
                                                       check=finding.check, severity=finding.severity),
        })
    return {"verified": verified, "insufficient": insufficient}
