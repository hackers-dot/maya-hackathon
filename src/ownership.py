"""Ownership verification: prove control of a host before MAYA may scan it.

The owner places a random token either in a homepage meta tag
    <meta name="maya-verify" content="TOKEN">
or as the whole content of /.well-known/maya-verify.txt.

To check, MAYA builds a one-off HttpClient whose allow-list holds ONLY the host the user submitted,
and fetches exactly those two paths with GET. Redirects to any other host are refused by the scope
guard as usual. On success the host is persisted to data/verified_targets.json with a timestamp.
Loopback (our demo app) needs no verification.
"""
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from src import scope
from src.evidence import EvidenceStore
from src.scanner.http_client import HttpClient

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "verified_targets.json"
TOKEN_PREFIX = "maya-verify-"
META_NAME = "maya-verify"
WELL_KNOWN_PATH = "/.well-known/maya-verify.txt"


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(24)


class _MetaTokens(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values: list[str] = []

    def handle_starttag(self, tag, attrs):  # also called for self-closing <meta ... />
        if tag == "meta":
            a = {k.lower(): (v or "") for k, v in attrs}
            if a.get("name", "").strip().lower() == META_NAME:
                self.values.append(a.get("content", "").strip())


def meta_tokens(html: str) -> list[str]:
    """Contents of <meta name="maya-verify"> tags; comments and scripts are ignored by the parser."""
    p = _MetaTokens()
    p.feed(html)
    return p.values


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


@dataclass
class OwnershipResult:
    host: str
    verified: bool
    method: str | None  # "meta" | "file" | "loopback"
    reason: str


class VerifiedTargets:
    """Hosts that passed ownership verification, persisted as JSON {host: {verified_at, method}}."""

    def __init__(self, path: str | Path = DEFAULT_PATH):
        self.path = Path(path)

    def load(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def hosts(self) -> frozenset[str]:
        return frozenset(self.load())

    def add(self, host: str, method: str) -> None:
        data = self.load()
        data[host] = {"verified_at": datetime.now(timezone.utc).isoformat(), "method": method}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    def remove(self, host: str) -> None:
        data = self.load()
        if data.pop(host, None) is not None:
            self.path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def verify_ownership(target_url: str, token: str, targets: VerifiedTargets, store: EvidenceStore | None = None,
                     transport: httpx.BaseTransport | None = None) -> OwnershipResult:
    if not isinstance(token, str) or not token.startswith(TOKEN_PREFIX) or len(token) < len(TOKEN_PREFIX) + 16:
        raise ValueError("token must come from new_token()")
    parts = urlsplit(target_url)
    host = (parts.hostname or "").lower()
    if scope.is_loopback(host):
        return OwnershipResult(host, True, "loopback", "loopback demo app needs no verification")
    origin = f"{parts.scheme}://{parts.netloc}"
    try:
        scope.check_target(origin, {host})  # same URL rules as every scan (no userinfo, no raw IPs, http/s only)
    except scope.OutOfScope as e:
        return OwnershipResult(host, False, None, f"target refused: {e}")

    checks = [
        ("meta", "/", lambda r: any(_same(v, token) for v in meta_tokens(r.body.decode("utf-8", "replace")))),
        ("file", WELL_KNOWN_PATH, lambda r: _same(r.body.decode("utf-8", "replace").strip(), token)),
    ]
    reasons = []
    client = HttpClient(verified=frozenset({host}), store=store, transport=transport)
    try:
        for method, path, has_token in checks:
            try:
                resp = client.get(origin + path)
            except scope.OutOfScope as e:
                reasons.append(f"{path}: refused ({e})")
                continue
            except httpx.HTTPError as e:
                reasons.append(f"{path}: request failed ({type(e).__name__})")
                continue
            if resp.status == 200 and has_token(resp):
                targets.add(host, method)
                return OwnershipResult(host, True, method, f"token found via {method} at {path}")
            reasons.append(f"{path}: token not found (HTTP {resp.status})")
    finally:
        client.close()
    return OwnershipResult(host, False, None, "; ".join(reasons))
