"""Evidence store: redacted request/response summaries in SQLite, each sealed with a sha256."""
import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "evidence.db"
MAX_BODY_BYTES = 2048
SELECTED_RESPONSE_HEADERS = {
    "content-type", "content-length", "location", "server", "x-powered-by", "set-cookie",
    "content-security-policy", "strict-transport-security", "x-frame-options", "x-content-type-options",
}
SENSITIVE_HEADERS = {"authorization", "proxy-authorization", "x-api-key", "x-auth-token"}

_SECRET_NAME = r"[\w.-]*(?:api[_-]?key|apikey|secret|token|passw(?:or)?d|pwd|auth|session|access[_-]?key|private[_-]?key|credential)[\w.-]*"
_PATTERNS = [
    # PEM private key blocks (also a block cut off before its END line)
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S),
     lambda m: "[REDACTED PRIVATE KEY]"),
    # name=value / name: value / "name": "value" where the name looks secret
    (re.compile(rf"""(?i)(\b{_SECRET_NAME}["']?\s*[:=]\s*["']?(?:(?:bearer|basic)\s+)?)([^\s"'&;,<>]+)"""),
     lambda m: m[1] + mask(m[2])),
    # Authorization-style "Bearer xxx"
    (re.compile(r"(?i)\b(bearer|basic)(\s+)([A-Za-z0-9._~+/=-]{8,})"),
     lambda m: m[1] + m[2] + mask(m[3])),
    # well-known token prefixes
    (re.compile(r"\b(?:(?:sk|pk|rk)[-_][A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_\w{20,}"
                r"|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,})"),
     lambda m: mask(m[0])),
    # ponytail: generic long mixed letter+digit strings; may over-mask asset hashes, which is the safe side
    (re.compile(r"\b(?=[A-Za-z0-9_+/-]*\d)(?=[A-Za-z0-9_+/-]*[A-Za-z])[A-Za-z0-9_+/-]{32,}"),
     lambda m: mask(m[0])),
]


def mask(secret: str) -> str:
    """first 3 + '...' + last 2; short values are fully masked so most of them never leaks."""
    return secret[:3] + "..." + secret[-2:] if len(secret) >= 8 else "***"


def redact_text(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


def redact_header(name: str, value: str) -> str:
    name = name.lower()
    if name in ("cookie", "set-cookie"):
        parts = value.split(";")
        # Cookie: every pair is a cookie. Set-Cookie: only the first; the rest are attributes (Path, SameSite...)
        for i in range(len(parts) if name == "cookie" else 1):
            key, eq, val = parts[i].partition("=")
            if eq:
                parts[i] = f"{key}={mask(val.strip())}"
        return ";".join(parts)
    if name in SENSITIVE_HEADERS:
        scheme, sep, cred = value.partition(" ")
        return f"{scheme} {mask(cred)}" if sep and cred else mask(value)
    return redact_text(value)


def _redact_headers(pairs) -> list[list[str]]:
    return [[k, redact_header(k, v)] for k, v in pairs]


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class EvidenceStore:
    def __init__(self, path: str | Path = DEFAULT_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS evidence "
                      "(id TEXT PRIMARY KEY, captured_at TEXT NOT NULL, summary TEXT NOT NULL, sha256 TEXT NOT NULL)")

    def _conn(self):
        return _Conn(self.path)

    def add(self, method: str, url: str, request_headers, status: int, response_headers, body: bytes = b"") -> str:
        text = redact_text(body.decode("utf-8", "replace"))  # redact BEFORE truncating so no secret is cut in half
        summary = json.dumps({
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "request": {"method": method, "url": redact_text(url), "headers": _redact_headers(request_headers)},
            "response": {
                "status": status,
                "headers": _redact_headers((k, v) for k, v in response_headers if k.lower() in SELECTED_RESPONSE_HEADERS),
                "body": text.encode("utf-8")[:MAX_BODY_BYTES].decode("utf-8", "ignore"),
            },
        }, sort_keys=True)
        record_id = uuid.uuid4().hex
        with self._conn() as c:
            c.execute("INSERT INTO evidence VALUES (?, ?, ?, ?)",
                      (record_id, json.loads(summary)["captured_at"], summary, _sha256(summary)))
        return record_id

    def _row(self, record_id: str):
        with self._conn() as c:
            row = c.execute("SELECT summary, sha256 FROM evidence WHERE id = ?", (record_id,)).fetchone()
        if row is None:
            raise KeyError(f"no evidence record {record_id!r}")
        return row

    def get(self, record_id: str) -> dict:
        summary, digest = self._row(record_id)
        return {"id": record_id, "sha256": digest, **json.loads(summary)}

    def verify(self, record_id: str) -> bool:
        summary, digest = self._row(record_id)
        return _sha256(summary) == digest


class _Conn:
    """sqlite3's own context manager commits but never closes; Windows needs the file closed."""
    def __init__(self, path):
        self.conn = sqlite3.connect(path)

    def __enter__(self):
        return self.conn.__enter__()

    def __exit__(self, *exc):
        with closing(self.conn):
            return self.conn.__exit__(*exc)
