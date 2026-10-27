"""Build Findings. Severity always comes from the fixed table; demo=True for loopback (our demo app)."""
from urllib.parse import urlsplit

from src.models import Evidence, Finding
from src.scope import is_loopback
from src.severity import rule


def make_finding(check: str, target: str, location: str, description: str, excerpt: str, technical: str,
                 evidence_id: str | None, suffix: str = "", demo: bool = False) -> Finding:
    r = rule(check)
    return Finding(
        id=f"{check}:{location}{suffix}", check=check, target=target, location=location, severity=r.severity,
        evidence=[Evidence.capture(description, excerpt, record_id=evidence_id)],
        plain_explanation=r.rationale, technical_explanation=f"{technical} Fix: {r.remediation}", demo=demo,
    )


def from_response(resp, check: str, excerpt: str, technical: str, suffix: str = "") -> Finding:
    parts = urlsplit(resp.url)
    return make_finding(check, f"{parts.scheme}://{parts.netloc}", parts.path or "/", f"response to {resp.url}",
                        excerpt, technical, resp.evidence_id, suffix, demo=is_loopback(parts.hostname or ""))
