"""Lookalike domain watch from the command line.

Live (the user runs this; it sends the candidate NAMES to cloudflare-dns.com and crt.sh, never to the
candidates themselves; at most 1 request per second):
    .venv\\Scripts\\python scripts\\lookalike_live.py --domain your-domain.example
Offline (faked DoH and crt.sh answers, no network, no waiting):
    .venv\\Scripts\\python scripts\\lookalike_live.py --dry-run

Prints every candidate with how it was generated and its lookup status, the groups that share an IP or
nameserver, and the findings (severity from the fixed table).
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from src.lookalike import ALLOWED_HOSTS, Lookups, watch  # noqa: E402

DRY_NOW = datetime(2026, 10, 28, tzinfo=timezone.utc)


def dry_transport(seen: list):
    """Fictional answers for demoshop.example lookalikes (documentation IPs, made-up certificate ids)."""
    registered = {"demoshop-care.example": ["192.0.2.10"], "demoshop.com": ["198.51.100.7"],
                  "d3moshop.example": ["192.0.2.10"]}
    certs = {"demoshop-care.example": [{"id": 1001, "not_before": "2026-10-20T09:00:00",
                                        "issuer_name": "C=XX, O=Example CA, CN=Example Issuing CA"}],
             "demoshop.com": [{"id": 1002, "not_before": "2025-01-05T09:00:00", "issuer_name": "C=XX, O=Example CA"}]}

    def handler(req: httpx.Request):
        seen.append(req.url.host)
        if req.url.host == "crt.sh":
            return httpx.Response(200, json=certs.get(req.url.params["q"], []))
        name, rtype = req.url.params["name"], req.url.params["type"]
        if name == "dem0shop.example":  # one resolver failure: must show as "lookup failed (unknown)"
            return httpx.Response(503)
        if name in registered:
            ans = ([{"type": 1, "data": ip} for ip in registered[name]] if rtype == "A"
                   else [{"type": 2, "data": "ns1.hosting.example."}])
            return httpx.Response(200, json={"Status": 0, "Answer": ans})
        return httpx.Response(200, json={"Status": 3})
    return httpx.MockTransport(handler)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--domain", default="demoshop.example", help="your own domain (default: the fictional demo)")
    p.add_argument("--dry-run", action="store_true", help="faked DoH/crt.sh answers; no network, no waiting")
    args = p.parse_args(argv)
    seen = []
    if args.dry_run:
        lookups, now = Lookups(transport=dry_transport(seen), sleep=lambda s: None), DRY_NOW
    else:
        lookups, now = Lookups(), None
    print(f"lookalike watch for {args.domain} | {'DRY RUN (faked answers)' if args.dry_run else 'LIVE'} | "
          f"lookups only via {', '.join(sorted(ALLOWED_HOSTS))}")
    try:
        findings, items, groups = watch(args.domain, lookups, now=now)
    finally:
        lookups.close()
    for it in items:
        print(f"  {it['domain']:40} {it['kind']:16} {it['status']}")
    print("\ngroups:")
    for g in groups:
        print(f"  {g['source']}: {', '.join(g['findings'])} -- {g['label']}")
    print("\nfindings:")
    for f in sorted(findings, key=lambda f: ("high", "medium", "low", "info").index(f.severity)):
        print(f"  [{f.severity.upper()}] {f.id}")
    print(f"\nrequests: {lookups.requests} | hosts contacted: {sorted(set(seen)) if args.dry_run else 'see above'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
