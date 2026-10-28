"""SPF / DMARC checks: pure functions over TXT record strings, plus a DNS-over-HTTPS TXT lookup that runs
only for verified, non-loopback domains."""
import json

import httpx

from src import scope
from src.evidence import redact_text
from src.models import Finding
from src.scanner.findings import make_finding


def _clean(txts) -> list[str]:
    # TXT values often arrive quoted and split ("v=spf1 " "-all"); join the pieces
    return [t.replace('" "', "").strip().strip('"').strip() for t in txts]


def _dns_finding(check, domain, name, txts, technical, evidence_id) -> Finding:
    excerpt = redact_text(f"TXT {name}: " + (" | ".join(txts) or "(none)"))
    return make_finding(check, f"dns:{domain}", f"TXT {name}", f"TXT records for {name}", excerpt, technical,
                        evidence_id)


def check_spf(domain: str, txts, evidence_id: str | None = None) -> list[Finding]:
    txts = _clean(txts)
    spf = [t for t in txts if t.lower().split(" ")[0] == "v=spf1"]
    if not spf:
        problem = "No SPF record (v=spf1) found."
    elif len(spf) > 1:
        problem = f"{len(spf)} SPF records found; RFC 7208 allows exactly one, so receivers treat it as an error."
    elif any(tok in ("+all", "all") for tok in spf[0].lower().split()):
        problem = "SPF ends in +all, which authorizes every sender."
    else:
        return []
    return [_dns_finding("missing_spf", domain, domain, txts, problem, evidence_id)]


def check_dmarc(domain: str, txts, evidence_id: str | None = None) -> list[Finding]:
    """txts are the TXT records of _dmarc.<domain>."""
    txts = _clean(txts)
    name = f"_dmarc.{domain}"
    records = [t for t in txts if t.replace(" ", "").lower().startswith("v=dmarc1")]
    if not records:
        problem = f"No DMARC record (v=DMARC1) at {name}."
    elif len(records) > 1:
        problem = f"{len(records)} DMARC records at {name}; there must be exactly one."
    else:
        tags = dict(p.strip().split("=", 1) for p in records[0].split(";") if "=" in p)
        policy = {k.strip().lower(): v.strip().lower() for k, v in tags.items()}.get("p")
        if policy in ("none", "quarantine", "reject"):
            return []
        problem = f"DMARC record has no valid p= policy (got {policy!r})."
    return [_dns_finding("missing_dmarc", domain, name, txts, problem, evidence_id)]


# --- DNS-over-HTTPS lookup (verified, non-loopback domains only) ---
# The domain name (and _dmarc.<domain>) is sent to this public resolver; the UI and README say so.
DOH_URL = "https://cloudflare-dns.com/dns-query"
DOH_TIMEOUT_S = 5.0
TXT_TYPE = 16


class DnsUnavailable(RuntimeError):
    pass


def check_domain(domain: str, verified) -> str:
    """The domain only if the scope guard accepts it AND it is not loopback (the demo app has no DNS)."""
    host = scope.check_target(f"https://{domain}/", verified)  # raises OutOfScope
    if scope.is_loopback(host):
        raise scope.OutOfScope("DNS checks do not apply to loopback targets")
    return host


def doh_txt(name: str, transport=None, store=None) -> tuple[list[str], str | None]:
    """TXT answers for `name` via DNS-over-HTTPS: GET only, 5 s timeout, no redirects, no env proxies.
    Returns (answers, evidence id of the stored, redacted raw JSON answer)."""
    with httpx.Client(transport=transport, timeout=DOH_TIMEOUT_S, follow_redirects=False, trust_env=False,
                      headers={"accept": "application/dns-json"}) as c:
        r = c.get(DOH_URL, params={"name": name, "type": "TXT"})
        body = r.content[:64 * 1024]
        sent = list(r.request.headers.multi_items())
    evidence_id = store.add("GET", str(r.request.url), sent, r.status_code, list(r.headers.multi_items()), body) \
        if store is not None else None
    if r.status_code != 200:
        raise DnsUnavailable(f"DNS-over-HTTPS answered HTTP {r.status_code}")
    try:
        data = json.loads(body)
    except ValueError:
        raise DnsUnavailable("DNS-over-HTTPS answer is not JSON") from None
    if data.get("Status") not in (0, 3):  # NOERROR, NXDOMAIN (no record is a valid answer)
        raise DnsUnavailable(f"DNS lookup failed (status {data.get('Status')})")
    answers = [a.get("data", "") for a in data.get("Answer") or [] if a.get("type") == TXT_TYPE]
    return answers, evidence_id


def run_dns_check(check_id: str, domain: str, verified, transport=None, store=None) -> list[Finding]:
    """missing_spf / missing_dmarc for a verified domain. Findings use the fixed severity table."""
    domain = check_domain(domain, verified)
    if check_id == "missing_spf":
        answers, eid = doh_txt(domain, transport, store)
        return check_spf(domain, answers, eid)
    answers, eid = doh_txt(f"_dmarc.{domain}", transport, store)
    return check_dmarc(domain, answers, eid)
