"""TLS certificate expiry and protocol version for a verified https target.

The scope guard runs BEFORE any socket is opened, and only the target's own host and port are contacted.
The handshake reads the certificate without trusting it (an expired or self-signed certificate must still be
readable to report on it); nothing is sent after the handshake. Severity comes from the fixed table:
  cert_expires_within_14_days (high), cert_expires_within_30_days (medium), tls_protocol_below_1_2 (medium).
"""
import json
import os
import socket
import ssl
import tempfile
import warnings
from datetime import datetime, timezone

from src import scope
from src.models import Finding
from src.scanner.findings import make_finding

TLS_TIMEOUT_S = 5.0
OLD_PROTOCOLS = {"SSLv2", "SSLv3", "TLSv1", "TLSv1.1"}
TLS_FINDING_CHECKS = ("cert_expires_within_14_days", "cert_expires_within_30_days", "tls_protocol_below_1_2")


def _name(rdns) -> str:
    short = {"commonName": "CN", "organizationName": "O", "countryName": "C", "organizationalUnitName": "OU"}
    return ", ".join(f"{short.get(k, k)}={v}" for rdn in rdns for k, v in rdn)


def decode_der(der: bytes) -> dict:
    """Certificate fields without extra packages: the stdlib decoder only reads PEM files."""
    fd, path = tempfile.mkstemp(suffix=".pem")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(ssl.DER_cert_to_PEM_cert(der))
        # ponytail: private stdlib helper (stable since 3.x, used by CPython's own tests); swap for a DER
        # parser if it ever disappears
        return ssl._ssl._test_decode_cert(path)
    finally:
        os.unlink(path)


def fetch_tls(host: str, verified, port: int = 443, timeout: float = TLS_TIMEOUT_S) -> dict:
    """{"host", "port", "not_after" (UTC ISO), "protocol", "issuer"} from one handshake with host:port."""
    host = scope.check_target(f"https://{host}:{port}/", verified)  # raises OutOfScope before any socket
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE  # read, do not trust: we report on it
    try:  # allow old protocols so a server that only speaks them can be reported, not just fail
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)  # deliberate: we detect, never prefer, old TLS
            ctx.minimum_version = ssl.TLSVersion.TLSv1
        ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
    except (ValueError, ssl.SSLError):
        pass
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            der, protocol = tls.getpeercert(binary_form=True), tls.version()
    cert = decode_der(der)
    not_after = datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notAfter"]), timezone.utc)
    return {"host": host, "port": port, "not_after": not_after.isoformat(), "protocol": protocol,
            "issuer": _name(cert.get("issuer", ()))}


def tls_findings(info: dict, now: datetime | None = None, evidence_id: str | None = None) -> list[Finding]:
    now = now or datetime.now(timezone.utc)
    days = (datetime.fromisoformat(info["not_after"]) - now).total_seconds() / 86400
    target, location = f"https://{info['host']}", f"tls://{info['host']}:{info['port']}"
    excerpt = f"not_after={info['not_after']}; protocol={info['protocol']}; issuer={info['issuer']}"
    out = []
    expiry = "cert_expires_within_14_days" if days < 14 else "cert_expires_within_30_days" if days < 30 else None
    if expiry:
        when = f"expired {-days:.0f} days ago" if days < 0 else f"expires in {days:.0f} days"
        out.append(make_finding(expiry, target, location, f"TLS certificate of {location}", excerpt,
                                f"The certificate {when} (not_after {info['not_after']}).", evidence_id))
    if info["protocol"] in OLD_PROTOCOLS:
        out.append(make_finding("tls_protocol_below_1_2", target, location, f"TLS handshake with {location}",
                                excerpt, f"The server negotiated {info['protocol']}, below TLS 1.2.", evidence_id))
    return out


def run_tls_check(host: str, verified, store=None, port: int = 443, fetch=fetch_tls,
                  now: datetime | None = None) -> list[Finding]:
    info = fetch(host, verified, port=port)
    eid = store.add("TLS", f"https://{info['host']}:{info['port']}/", [], 0, [],
                    json.dumps(info, sort_keys=True).encode()) if store is not None else None
    return tls_findings(info, now, eid)
