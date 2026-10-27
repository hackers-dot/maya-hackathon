"""Accounts and sessions for the MAYA web app (stdlib only).

- Passwords: hashlib.scrypt with a random 16-byte salt, compared with hmac.compare_digest. Min length 10.
  Passwords are never logged, printed or stored in clear.
- Users: SQLite under data/ (git-ignored).
- Login rate limit: after 5 failed attempts for an account within 5 minutes, further attempts for that
  account (even with the right password) are refused until the window passes.
- Sessions: random token in an HttpOnly, SameSite=Strict cookie; server-side store with expiry; each
  session carries its own CSRF token. Signing in replaces the session (no fixation).
"""
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import defaultdict, deque
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "web.db"
MIN_PASSWORD = 10
MAX_PASSWORD = 1024  # bounds scrypt work per request
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,189}\.[^@\s.]{2,63}$")
SESSION_TTL = 8 * 3600
LOGIN_WINDOW = 300
LOGIN_MAX_FAILURES = 5
PROFILE_FIELDS = {"account_type", "asset", "connect", "consent_at"}


class AuthError(ValueError):
    pass


class RateLimited(AuthError):
    pass


def hash_password(password: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    salt = salt if salt is not None else os.urandom(16)
    return salt, hashlib.scrypt(password.encode("utf-8"), salt=salt, **SCRYPT)


def verify_password(password: str, salt: bytes, digest: bytes) -> bool:
    return hmac.compare_digest(hash_password(password, salt)[1], digest)


def normalize_email(email) -> str:
    if not isinstance(email, str) or not EMAIL_RE.match(email.strip()):
        raise AuthError("enter a valid email address")
    return email.strip().lower()


class Accounts:
    def __init__(self, path: str | Path = DEFAULT_DB, clock=time.time):
        self.path, self.clock = Path(path), clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._failures: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()
        self._dummy = hash_password("not-a-real-password")  # same work for unknown accounts (no timing oracle)
        with closing(sqlite3.connect(self.path)) as c, c:
            c.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, "
                      "salt BLOB NOT NULL, pw_hash BLOB NOT NULL, created_at REAL NOT NULL, account_type TEXT, "
                      "asset TEXT, connect INTEGER, consent_at REAL)")

    def _db(self):
        return closing(sqlite3.connect(self.path))

    def sign_up(self, email, password) -> int:
        email = normalize_email(email)
        if not isinstance(password, str) or not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
            raise AuthError(f"password must be {MIN_PASSWORD} to {MAX_PASSWORD} characters")
        salt, digest = hash_password(password)
        try:
            with self._db() as c, c:
                return c.execute("INSERT INTO users (email, salt, pw_hash, created_at) VALUES (?, ?, ?, ?)",
                                 (email, salt, digest, self.clock())).lastrowid
        except sqlite3.IntegrityError:
            raise AuthError("could not create that account") from None

    def sign_in(self, email, password) -> int:
        try:
            email = normalize_email(email)
        except AuthError:
            raise AuthError("invalid email or password") from None
        now = self.clock()
        with self._lock:
            fails = self._failures[email]
            while fails and fails[0] <= now - LOGIN_WINDOW:
                fails.popleft()
            if len(fails) >= LOGIN_MAX_FAILURES:
                raise RateLimited("too many attempts for this account; try again in a few minutes")
        with self._db() as c:
            row = c.execute("SELECT id, salt, pw_hash FROM users WHERE email = ?", (email,)).fetchone()
        salt, digest = (row[1], row[2]) if row else self._dummy
        ok = isinstance(password, str) and len(password) <= MAX_PASSWORD and verify_password(password, salt, digest)
        with self._lock:
            if not (ok and row):
                self._failures[email].append(now)
                raise AuthError("invalid email or password")
            self._failures.pop(email, None)
        return row[0]

    def get(self, user_id: int) -> dict | None:
        with self._db() as c:
            row = c.execute("SELECT id, email, account_type, asset, connect, consent_at FROM users WHERE id = ?",
                            (user_id,)).fetchone()
        if row is None:
            return None
        keys = ("id", "email", "account_type", "asset", "connect", "consent_at")
        user = dict(zip(keys, row))
        user["connect"] = None if user["connect"] is None else bool(user["connect"])
        return user

    def update(self, user_id: int, **fields) -> None:
        assert fields and set(fields) <= PROFILE_FIELDS, fields  # column names never come from requests
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self._db() as c, c:
            c.execute(f"UPDATE users SET {cols} WHERE id = ?", (*fields.values(), user_id))


@dataclass
class Session:
    token: str
    csrf: str
    user_id: int | None
    expires: float


class Sessions:
    """Server-side session store. ponytail: in memory, so sessions end on restart; move to SQLite if needed."""

    def __init__(self, ttl: float = SESSION_TTL, clock=time.time):
        self.ttl, self.clock = ttl, clock
        self._store: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self, user_id: int | None = None) -> Session:
        s = Session(secrets.token_urlsafe(32), secrets.token_urlsafe(32), user_id, self.clock() + self.ttl)
        with self._lock:
            self._store[s.token] = s
        return s

    def get(self, token) -> Session | None:
        if not isinstance(token, str):
            return None
        with self._lock:
            s = self._store.get(token)
            if s is not None and s.expires <= self.clock():
                del self._store[token]
                return None
            return s

    def destroy(self, token) -> None:
        with self._lock:
            self._store.pop(token, None)

    @staticmethod
    def csrf_ok(session: Session | None, value) -> bool:
        return bool(session and isinstance(value, str) and value
                    and hmac.compare_digest(session.csrf.encode(), value.encode()))
