"""Connected assets (websites) per user: URL validation, verification tokens, ownership checks.

- A URL is validated with the same scope rules as every scan (http/https only, no userinfo, no numeric-IP
  spellings, no single-label names, no non-loopback IP literals).
- Any loopback URL means MAYA's own demo app: the port the user typed is ignored and the server scans the
  demo app it starts itself, so a loopback URL can never point MAYA at another local service.
- Other sites get a random token; ownership is verified by src/ownership.py (HttpClient only).
"""
import secrets
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

from src import scope
from src.ownership import WELL_KNOWN_PATH, VerifiedTargets, new_token, txt_record, verify_dns_txt, verify_ownership

DEMO_LABEL = "Demo app (loopback, no verification needed)"
MAX_URL = 2048


class AssetError(ValueError):
    pass


def parse_target(url) -> dict:
    """Return {"kind": "demo"|"website", "origin", "host"} or raise AssetError with a plain reason."""
    if not isinstance(url, str) or not url.strip() or len(url) > MAX_URL:
        raise AssetError("enter your website's address, for example https://shop.example")
    url = url.strip()
    if "://" not in url:
        url = "https://" + url  # a bare domain means its HTTPS site
    parts = urlsplit(url)
    try:
        parts.port  # noqa: B018 - raises on a bad port
    except ValueError:
        raise AssetError("that address has an invalid port") from None
    host = (parts.hostname or "").lower()
    if parts.scheme in ("http", "https") and scope.is_loopback(host):
        try:
            scope.check_target(url)  # still refuse userinfo, backslashes, control characters
        except scope.OutOfScope as e:
            raise AssetError(f"address refused: {e}") from None
        return {"kind": "demo", "origin": "demo", "host": "127.0.0.1"}
    try:
        scope.check_target(url, {host})  # same rules as any scan, with this one host allowed
    except scope.OutOfScope as e:
        reason = str(e)
        if reason.startswith("IP "):
            reason = "use your website's domain name, not an IP address"
        raise AssetError(f"address refused: {reason}") from None
    return {"kind": "website", "origin": f"{parts.scheme}://{parts.netloc.lower()}", "host": host}


class Assets:
    def __init__(self, db_path: Path, verified_path: Path, clock):
        self.path, self.clock = Path(db_path), clock
        self.targets = VerifiedTargets(verified_path)
        with closing(sqlite3.connect(self.path)) as c, c:
            c.execute("CREATE TABLE IF NOT EXISTS official_entries (user_id INTEGER NOT NULL, kind TEXT NOT NULL, "
                      "value TEXT NOT NULL, UNIQUE (user_id, kind, value))")
            c.execute("CREATE TABLE IF NOT EXISTS assets (id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, "
                      "kind TEXT NOT NULL, origin TEXT NOT NULL, host TEXT NOT NULL, token TEXT, "
                      "status TEXT NOT NULL, reason TEXT, created_at REAL NOT NULL, verified_at REAL, "
                      "UNIQUE (user_id, origin))")

    def _db(self):
        return closing(sqlite3.connect(self.path))

    COLS = ("id", "user_id", "kind", "origin", "host", "token", "status", "reason", "created_at", "verified_at")

    def _rows(self, where: str, args) -> list[dict]:
        with self._db() as c:
            rows = c.execute(f"SELECT {', '.join(self.COLS)} FROM assets WHERE {where} ORDER BY created_at", args)
            return [dict(zip(self.COLS, r)) for r in rows.fetchall()]

    def add(self, user_id: int, url) -> dict:
        target = parse_target(url)
        existing = self._rows("user_id = ? AND origin = ?", (user_id, target["origin"]))
        if existing:
            return existing[0]
        demo = target["kind"] == "demo"
        asset = {"id": secrets.token_hex(8), "user_id": user_id, "kind": target["kind"], "origin": target["origin"],
                 "host": target["host"], "token": None if demo else new_token(),
                 "status": "loopback" if demo else "pending", "reason": None, "created_at": self.clock(),
                 "verified_at": None}
        with self._db() as c, c:
            c.execute(f"INSERT INTO assets ({', '.join(self.COLS)}) VALUES ({', '.join('?' * len(self.COLS))})",
                      tuple(asset[k] for k in self.COLS))
        return asset

    def add_upload(self, user_id: int, kind: str, name: str) -> dict:
        """A code ZIP or log file upload. Only its name and kind are kept; the content never is."""
        assert kind in ("code", "logs", "image", "watch")
        asset = {"id": secrets.token_hex(8), "user_id": user_id, "kind": kind,
                 "origin": f"upload:{kind}:{secrets.token_hex(8)}", "host": name, "token": None,
                 "status": "uploaded", "reason": None, "created_at": self.clock(), "verified_at": None}
        with self._db() as c, c:
            c.execute(f"INSERT INTO assets ({', '.join(self.COLS)}) VALUES ({', '.join('?' * len(self.COLS))})",
                      tuple(asset[k] for k in self.COLS))
        return asset

    def remove(self, user_id: int, asset_id) -> dict | None:
        """Disconnect: delete the asset row and, if no other verified asset uses its host, its verified status."""
        asset = self.get(user_id, asset_id)
        if asset is None:
            return None
        with self._db() as c, c:
            c.execute("DELETE FROM assets WHERE id = ? AND user_id = ?", (asset["id"], user_id))
            still = c.execute("SELECT COUNT(*) FROM assets WHERE host = ? AND status = 'verified'",
                              (asset["host"],)).fetchone()[0]
        if asset["status"] == "verified" and not still:
            self.targets.remove(asset["host"])
        return asset

    def remove_all(self, user_id: int) -> int:
        assets = self.list(user_id)
        for a in assets:
            self.remove(user_id, a["id"])
        return len(assets)

    # --- official domains / phone numbers (for the suspicious-message check), per user ---
    def official(self, user_id: int) -> dict:
        with self._db() as c:
            rows = c.execute("SELECT kind, value FROM official_entries WHERE user_id = ? ORDER BY value",
                             (user_id,)).fetchall()
        return {"domains": [v for k, v in rows if k == "domain"], "numbers": [v for k, v in rows if k == "number"]}

    def set_official(self, user_id: int, domains: list[str], numbers: list[str]) -> None:
        with self._db() as c, c:
            c.execute("DELETE FROM official_entries WHERE user_id = ?", (user_id,))
            c.executemany("INSERT OR IGNORE INTO official_entries VALUES (?, ?, ?)",
                          [(user_id, "domain", d) for d in domains] + [(user_id, "number", n) for n in numbers])

    def delete_official(self, user_id: int) -> int:
        with self._db() as c, c:
            return c.execute("DELETE FROM official_entries WHERE user_id = ?", (user_id,)).rowcount

    def list(self, user_id: int) -> list[dict]:
        return self._rows("user_id = ?", (user_id,))

    def get(self, user_id: int, asset_id) -> dict | None:
        """The asset only if it belongs to this user (another user's id behaves like a missing one)."""
        if not isinstance(asset_id, str):
            return None
        rows = self._rows("id = ? AND user_id = ?", (asset_id, user_id))
        return rows[0] if rows else None

    def verify(self, user_id: int, asset_id, store=None, transport=None, method: str = "web") -> dict | None:
        """method "web": meta tag or /.well-known file over HTTP; "dns": TXT record via DNS-over-HTTPS."""
        asset = self.get(user_id, asset_id)
        if asset is None or asset["kind"] != "website":
            return asset
        check = verify_dns_txt if method == "dns" else verify_ownership
        try:
            result = check(asset["origin"], asset["token"], self.targets, store=store, transport=transport)
            verified, reason = result.verified, result.reason
        except scope.OutOfScope as e:
            verified, reason = False, f"target refused: {e}"
        status = "verified" if verified else "failed"
        with self._db() as c, c:
            c.execute("UPDATE assets SET status = ?, reason = ?, verified_at = ? WHERE id = ?",
                      (status, reason, self.clock() if verified else None, asset["id"]))
        return self.get(user_id, asset_id)


def public_asset(asset: dict, last_run: str | None = None) -> dict:
    demo = asset["kind"] == "demo"
    out = {"id": asset["id"], "kind": asset["kind"], "label": DEMO_LABEL if demo else asset["host"],
           "origin": None if demo else asset["origin"], "status": asset["status"], "reason": asset["reason"],
           "scannable": asset["status"] in ("verified", "loopback"), "last_run": last_run}
    if asset["kind"] in ("code", "logs", "image", "watch"):
        out["label"] = asset["host"]
        out["origin"] = None
    elif not demo:
        out["verification"] = {"token": asset["token"],
                               "meta_tag": f'<meta name="maya-verify" content="{asset["token"]}">',
                               "file_path": WELL_KNOWN_PATH, "file_url": asset["origin"] + WELL_KNOWN_PATH,
                               "dns_txt": txt_record(asset["host"], asset["token"])}
    return out
