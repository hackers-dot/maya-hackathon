"""SPF / DMARC checks: pure functions over TXT record strings (the caller does the lookup)."""
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
