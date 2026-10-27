"""Verify the model's final claims against code-produced findings and hashed evidence.

A claim survives only if: its finding exists, its claim_type matches the finding's check, the evidence
record's hash verifies, and the quote appears verbatim in that record. Report text comes from fixed
templates; severity comes from the finding (i.e. the fixed table), never from the model.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.evidence import EvidenceStore
from src.models import Finding

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


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")  # e.g. a model-supplied "severity" makes the claim malformed
    finding_id: str = Field(min_length=1)
    claim_type: ClaimType
    quoted_evidence: str = Field(min_length=4)


def record_text(record: dict) -> str:
    """The quotable text of a stored record: request line, status, headers, body."""
    req, resp = record["request"], record["response"]
    lines = [f"{req['method']} {req['url']}", f"HTTP {resp['status']}"]
    lines += [f"{k}: {v}" for k, v in resp["headers"]]
    return "\n".join(lines) + "\n\n" + resp["body"]


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
            text = record_text(store.get(record_id))
        except KeyError:
            drop(raw, "evidence record missing or hash mismatch")
            continue
        if claim.quoted_evidence not in text:
            drop(raw, "quote not found verbatim in evidence")
            continue
        verified.append({
            "finding_id": finding.id, "check": finding.check, "severity": finding.severity,
            "location": finding.location, "claim_type": claim.claim_type, "evidence_id": record_id,
            "quoted_evidence": claim.quoted_evidence, "demo": finding.demo,
            "text": TEMPLATES[claim.claim_type].format(location=finding.location, target=finding.target,
                                                       check=finding.check, severity=finding.severity),
        })
    return {"verified": verified, "insufficient": insufficient}
