"""Target guard: only loopback or explicitly verified hosts may be scanned. Pure logic, no network."""
import ipaddress
from urllib.parse import urlsplit

LOOPBACK_NAMES = {"localhost"}


class OutOfScope(ValueError):
    pass


def check_target(url: str, verified: frozenset[str] | set[str] = frozenset()) -> str:
    """Return the normalized hostname if `url` is in scope, else raise OutOfScope."""
    if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url):
        raise OutOfScope("invalid URL")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise OutOfScope(f"scheme not allowed: {parts.scheme!r}")
    # userinfo and backslashes make host parsing ambiguous across libraries: refuse outright
    if "@" in parts.netloc or "\\" in url:
        raise OutOfScope("credentials or backslashes in URL are not allowed")
    try:
        parts.port  # raises on garbage ports
    except ValueError:
        raise OutOfScope("invalid port") from None
    host = (parts.hostname or "").lower()
    if not host:
        raise OutOfScope("missing host")

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        # ip_address only accepts canonical dotted/colon forms, so decimal/hex/short forms land below as names
        if ip.is_loopback:
            return host
        raise OutOfScope(f"IP {host} is not loopback")

    if host in LOOPBACK_NAMES:
        return host
    if host in {v.lower() for v in verified}:
        return host
    raise OutOfScope(f"host {host!r} is not loopback and not ownership-verified")


check = check_target
