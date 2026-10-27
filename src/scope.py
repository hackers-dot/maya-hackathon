"""Target guard: only loopback or explicitly verified hosts may be scanned. Pure logic, no network."""
import ipaddress
import re
from urllib.parse import urlsplit

LOOPBACK_NAMES = {"localhost"}
# decimal / hex / octal / short IPv4 spellings (2130706433, 0x7f000001, 127.1) that some stacks resolve as IPs
NUMERIC_HOST = re.compile(r"^(?:0x[0-9a-f]*|\d+)(?:\.(?:0x[0-9a-f]*|\d+))*\.?$")
# a real dotted DNS name whose last label starts with a letter
DNS_NAME = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$")


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

    if NUMERIC_HOST.match(host):
        raise OutOfScope(f"non-canonical numeric host {host!r}")  # even if someone put it on the verified list
    if host in LOOPBACK_NAMES:
        return host
    if DNS_NAME.match(host) and host in {v.lower() for v in verified}:
        return host
    raise OutOfScope(f"host {host!r} is not loopback and not ownership-verified")


check = check_target


def is_loopback(host: str) -> bool:
    """True for our own demo app's hosts (localhost, 127.0.0.0/8, ::1)."""
    if host.lower() in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
