"""Lookalike domain watch for the user's own domain. Passive only.

1. A deterministic generator builds candidate NAMES (strings only): typos (omission, swap, duplicate,
   keyboard-adjacent), homoglyphs (ASCII look-alikes and Cyrillic letters in punycode form), hyphenation,
   TLD swap, brand + keyword, and a subdomain trick. Sorted, de-duplicated, at most 40.
2. For the top 25 (by a fixed round-robin priority over the generator kinds) MAYA asks a DNS-over-HTTPS
   resolver (cloudflare-dns.com) for A and NS records, and, for names that have records, certificate-
   transparency entries from crt.sh. GET only, no redirects, at most 1 request per second, 10 s timeout,
   2 retries. A failed lookup is recorded as "lookup failed (unknown)"; nothing is invented.
3. MAYA never connects to a candidate domain itself: the only hosts contacted are the two above.

Wording: "resembles your domain". Shared IPs or nameservers are reported as shared infrastructure, which is
common for hosting and does not identify an operator.
"""
import json
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import httpx

from src.brand_check import normalise_domain
from src.models import Finding
from src.scanner.findings import make_finding

DOH_URL = "https://cloudflare-dns.com/dns-query"
CRTSH_URL = "https://crt.sh/"
ALLOWED_HOSTS = {"cloudflare-dns.com", "crt.sh"}
MAX_CANDIDATES = 40
LOOKUP_TOP = 25
TIMEOUT_S = 10.0
RETRIES = 2
MIN_INTERVAL_S = 1.0
RECENT_DAYS = 90
KEYWORDS = ("support", "care", "refund", "pay", "login", "offer")
TLDS = ("com", "net", "org", "co", "io", "shop", "info", "online")
ROWS = ("1234567890", "qwertyuiop", "asdfghjkl", "zxcvbnm")
ASCII_GLYPHS = {"o": "0", "l": "1", "i": "1", "e": "3", "m": "rn", "w": "vv"}
CYRILLIC = {"a": "а", "e": "е", "o": "о", "p": "р", "c": "с", "x": "х", "y": "у",
            "i": "і"}
LOOKALIKE_CHECKS = ("lookalike_recent_certificate", "lookalike_has_dns", "lookalike_no_dns")
UNKNOWN = "lookup failed (unknown)"
SHARED_LABEL = "shares infrastructure (IP/NS); shared hosting is common; this does not identify an operator"
STATUS = {"lookalike_recent_certificate": "resembles your domain and a recent certificate was issued",
          "lookalike_has_dns": "resembles your domain and has DNS records",
          "lookalike_no_dns": "resembles your domain, no DNS records found"}


# --- 1. candidate names (strings only) ---

def _adjacent(ch: str) -> list[str]:
    out = []
    for r, row in enumerate(ROWS):
        i = row.find(ch)
        if i < 0:
            continue
        out += [row[j] for j in (i - 1, i + 1) if 0 <= j < len(row)]
        for rr in (r - 1, r + 1):  # the keys above and below
            if 0 <= rr < len(ROWS) and i < len(ROWS[rr]):
                out.append(ROWS[rr][i])
    return out


def _by_kind(label: str, tld: str) -> dict[str, list[str]]:
    n = len(label)
    kinds = {
        "homoglyph": [label[:i] + ASCII_GLYPHS[c] + label[i + 1:] for i, c in enumerate(label) if c in ASCII_GLYPHS],
        # Cyrillic letters, registered in punycode form (xn--...); a kind of its own so the round-robin reaches it
        "homoglyph (punycode)": [label[:i] + CYRILLIC[c] + label[i + 1:] for i, c in enumerate(label) if c in CYRILLIC],
        "keyword": [f"{label}-{k}" for k in KEYWORDS] + [f"{label}{k}" for k in KEYWORDS],
        "tld swap": [f"{label}.{t}" for t in TLDS if t != tld],
        "hyphenation": [label[:i] + "-" + label[i:] for i in range(1, n)],
        "omission": [label[:i] + label[i + 1:] for i in range(n)],
        "swap": [label[:i] + label[i + 1] + label[i] + label[i + 2:] for i in range(n - 1) if label[i] != label[i + 1]],
        "duplicate": [label[:i] + label[i] + label[i:] for i in range(n)],
        "keyboard": [label[:i] + a + label[i + 1:] for i, c in enumerate(label) for a in _adjacent(c)],
        # your full domain used as the first part of someone else's name: demoshop.example-login.com
        "subdomain trick": [f"{label}.{tld}-{k}.com" for k in ("login", "secure", "account")],
    }
    return {k: [v if "." in v else f"{v}.{tld}" for v in vs] for k, vs in kinds.items()}


def _ascii(name: str) -> str | None:
    try:
        return normalise_domain(name.encode("idna").decode("ascii") if any(ord(c) > 127 for c in name) else name)
    except UnicodeError:
        return None


def generate(domain: str) -> list[dict]:
    """[{domain (ASCII/punycode), display (unicode), kind, rank}], sorted by domain; rank < 25 gets looked up."""
    official = normalise_domain(domain)
    if not official:
        raise ValueError("not a domain name")
    labels = official.split(".")
    label, tld = labels[-2], labels[-1]
    kinds = {k: sorted(set(vs)) for k, vs in _by_kind(label, tld).items()}
    picked, seen = [], {official}
    while len(picked) < MAX_CANDIDATES and any(kinds.values()):
        for kind, names in kinds.items():  # round-robin over kinds keeps the 40 varied and the order fixed
            while names:
                display = names.pop(0)
                ascii_name = _ascii(display)
                if ascii_name and ascii_name not in seen:
                    seen.add(ascii_name)
                    picked.append({"domain": ascii_name, "display": display, "kind": kind, "rank": len(picked)})
                    break
            if len(picked) >= MAX_CANDIDATES:
                break
    return sorted(picked, key=lambda c: c["domain"])


# --- 2. lookups (DoH + crt.sh only) ---

class Lookups:
    """GET-only JSON lookups against the two allowed hosts: no redirects, 10 s timeout, 2 retries, 1 req/s."""

    def __init__(self, transport=None, sleep=time.sleep, clock=time.monotonic):
        self.client = httpx.Client(transport=transport, timeout=TIMEOUT_S, follow_redirects=False, trust_env=False,
                                   headers={"User-Agent": "MAYA-lookalike-watch/0.1 (passive)"})
        self.sleep, self.clock, self._last = sleep, clock, None
        self.requests = 0

    def close(self):
        self.client.close()

    def _get(self, url: str, params: dict, accept: str) -> tuple[object | None, str]:
        host = httpx.URL(url).host
        if host not in ALLOWED_HOSTS:  # never a candidate domain, never anything else
            raise ValueError(f"lookup host not allowed: {host}")
        for attempt in range(RETRIES + 1):
            if self._last is not None:
                wait = self._last + MIN_INTERVAL_S - self.clock()
                if wait > 0:
                    self.sleep(wait)
            self._last = self.clock()
            self.requests += 1
            try:
                r = self.client.get(url, params=params, headers={"accept": accept})
            except httpx.HTTPError as e:
                err = type(e).__name__
                continue
            if r.status_code == 429 or r.status_code >= 500:
                err = f"HTTP {r.status_code}"
                continue
            if r.status_code != 200:  # includes redirects, which are never followed
                return None, f"HTTP {r.status_code}"
            try:
                return r.json(), r.text[:240]
            except ValueError:
                return None, "not JSON"
        return None, err

    def dns(self, name: str, rtype: str) -> tuple[list[str] | None, str]:
        data, raw = self._get(DOH_URL, {"name": name, "type": rtype}, "application/dns-json")
        if not isinstance(data, dict) or data.get("Status") not in (0, 3):
            return None, raw
        code = {"A": 1, "NS": 2}[rtype]
        return sorted(a.get("data", "").rstrip(".") for a in data.get("Answer") or [] if a.get("type") == code), raw

    def certs(self, name: str) -> tuple[list[dict] | None, str]:
        data, raw = self._get(CRTSH_URL, {"q": name, "output": "json"}, "application/json")
        if not isinstance(data, list):
            return None, raw
        return [{"id": c.get("id"), "not_before": c.get("not_before"), "issuer_name": c.get("issuer_name")}
                for c in data if isinstance(c, dict)], raw


def _recent(certs: list[dict], now: datetime) -> list[dict]:
    out = []
    for c in certs:
        try:
            nb = datetime.fromisoformat(str(c.get("not_before"))).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if now - timedelta(days=RECENT_DAYS) <= nb <= now + timedelta(days=1):
            out.append(c)
    return out


def check(candidate: str, lookups: Lookups, now: datetime) -> dict:
    a, raw_a = lookups.dns(candidate, "A")
    ns, raw_ns = lookups.dns(candidate, "NS")
    result = {"a": a, "ns": ns, "certs": None, "recent_certs": None,
              "raw": {"doh_a": raw_a, "doh_ns": raw_ns, "crtsh": None}}
    if a is None and ns is None:
        result["status"] = UNKNOWN
        return result
    if not (a or ns):
        result["status"] = STATUS["lookalike_no_dns"]
        return result
    certs, raw_c = lookups.certs(candidate)  # only for names that have records: a certificate is a second signal
    result["raw"]["crtsh"] = raw_c
    if certs is None:
        result["status"] = STATUS["lookalike_has_dns"] + "; certificate lookup failed (unknown)"
        return result
    recent = _recent(certs, now)
    result.update(certs=len(certs), recent_certs=recent[:3])
    result["status"] = STATUS["lookalike_recent_certificate" if recent else "lookalike_has_dns"]
    return result


def watch(official: str, lookups: Lookups, store=None, now: datetime | None = None,
          progress=None) -> tuple[list[Finding], list[dict], list[dict]]:
    """(findings, items, groups). progress(i, item) is called after each lookup (live trace)."""
    now = now or datetime.now(timezone.utc)
    official = normalise_domain(official)
    candidates = generate(official)
    items = []
    findings = []
    for c in sorted(candidates, key=lambda c: c["rank"]):
        item = {"kind": "domain", **c, "verdict": "resembles your domain"}
        if c["rank"] >= LOOKUP_TOP:
            item["status"] = "not looked up (beyond the top 25)"
            items.append(item)
            continue
        r = check(c["domain"], lookups, now)
        item.update(status=r["status"], a=r["a"], ns=r["ns"], certs=r["certs"], recent_certs=r["recent_certs"])
        if r["status"] != UNKNOWN:
            # parsed answers first (what claims are checked against), short raw snippets as evidence; the
            # store keeps at most 2 KB of a body, so nothing here may grow unbounded
            body = json.dumps({"candidate": c["domain"], "official": official, "generator": c["kind"],
                               "a": (r["a"] or [])[:5], "ns": (r["ns"] or [])[:5], "a_ok": r["a"] is not None,
                               "ns_ok": r["ns"] is not None, "recent_certs": r["recent_certs"],
                               "doh_a_raw": r["raw"]["doh_a"][:240], "doh_ns_raw": r["raw"]["doh_ns"][:240]},
                              sort_keys=True)
            eid = store.add("LOOKALIKE", f"lookalike://{c['domain']}", [], 0, [], body.encode()) if store else None
            check_id = ("lookalike_recent_certificate" if r["recent_certs"] else
                        "lookalike_has_dns" if (r["a"] or r["ns"]) else "lookalike_no_dns")
            detail = (f"A {', '.join(r['a'] or []) or '-'}; NS {', '.join(r['ns'] or []) or '-'}"
                      + (f"; {len(r['recent_certs'])} certificate(s) issued in the last {RECENT_DAYS} days"
                         if r["recent_certs"] else ""))
            findings.append(make_finding(
                check_id, f"lookalike:{official}", f"domain:{c['domain']}", f"lookups for {c['domain']}",
                f"{c['domain']} ({c['kind']}): {detail}",
                f"{c['domain']} {STATUS[check_id].replace('resembles your domain', f'resembles your domain {official}')} "
                f"(generated by: {c['kind']}).", eid))
        items.append(item)
        if progress:
            progress(len(items), item)
    return findings, sorted(items, key=lambda i: i["domain"]), groups(items)


def groups(items: list[dict]) -> list[dict]:
    """Candidates that share an IP address or a nameserver (two or more)."""
    shared = defaultdict(set)
    for it in items:
        for ip in it.get("a") or []:
            shared[f"IP {ip}"].add(it["domain"])
        for ns in it.get("ns") or []:
            shared[f"NS {ns}"].add(it["domain"])
    return [{"source": k, "label": SHARED_LABEL, "findings": sorted(v)}
            for k, v in sorted(shared.items()) if len(v) >= 2]


def support_lines(check: str, body: str, captured_at: str | None = None) -> list[str]:
    """Lines of the stored lookup record that still show the finding (for verifying model claims)."""
    try:
        r = json.loads(body)
    except ValueError:
        return []
    a, ns = r.get("a") or [], r.get("ns") or []
    has = bool(a or ns)
    if check == "lookalike_no_dns":
        return [f"{r['candidate']}: no A or NS records"] if not has and r.get("a_ok") and r.get("ns_ok") else []
    if check == "lookalike_has_dns":
        return [f"{r['candidate']}: A {', '.join(a) or '-'}; NS {', '.join(ns) or '-'}"] if has else []
    if check == "lookalike_recent_certificate":
        return [f"{r['candidate']}: certificate {c.get('id')} not_before {c.get('not_before')} issuer "
                f"{c.get('issuer_name')}" for c in r.get("recent_certs") or []] if has else []
    return []
