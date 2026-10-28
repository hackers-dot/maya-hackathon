"""Approval gate: PROPOSED -> APPROVED (named approver, UTC time) -> APPLIED, loopback demo app only.

Applying writes the approved check ids into the demo app's control file (the demo app re-reads it on every
request), after backing up the previous state. A health check (GET /) runs afterwards; if it fails, the
previous state is restored (ROLLED_BACK). Every transition and every refusal goes to a hash-chained audit log.
"""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from src import scope
from src.fixes import Proposal
from src.scanner.http_client import HttpClient

DEFAULT_AUDIT = Path(__file__).resolve().parent.parent / "data" / "audit.jsonl"
GENESIS = "0" * 64


class ApplyRefused(PermissionError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(entry: dict) -> str:
    body = {k: v for k, v in entry.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()


class AuditLog:
    """Append-only JSONL; each entry carries the previous entry's hash, so edits and deletions break the chain."""

    def __init__(self, path: str | Path = DEFAULT_AUDIT):
        self.path = Path(path)

    def _entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(l) for l in self.path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def append(self, event: str, **details) -> dict:
        entries = self._entries()
        entry = {"seq": len(entries), "at": _now(), "event": event, "details": details,
                 "prev_hash": entries[-1]["hash"] if entries else GENESIS}
        entry["hash"] = _digest(entry)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
        return entry

    def verify(self) -> tuple[bool, int | None]:
        """(True, None) if intact, else (False, seq of the first broken entry)."""
        prev = GENESIS
        try:
            entries = self._entries()
        except ValueError:
            return False, None
        for i, e in enumerate(entries):
            if e.get("seq") != i or e.get("prev_hash") != prev or e.get("hash") != _digest(e):
                return False, i
            prev = e["hash"]
        return True, None


def http_health(base_url: str) -> bool:
    """GET / through the scope-checked client; healthy means HTTP 200."""
    client = HttpClient(min_interval=0)
    try:
        return client.get(base_url.rstrip("/") + "/").status == 200
    except Exception:  # noqa: BLE001 - any failure is "unhealthy"
        return False
    finally:
        client.close()


class ApprovalGate:
    def __init__(self, audit: AuditLog, control_path: str | Path, health=http_health):
        self.audit, self.control_path, self.health = audit, Path(control_path), health
        self._backup: str | None = None
        self._has_backup = False

    # --- approve ---
    def approve(self, proposal: Proposal, approver: str, run: str | None = None) -> Proposal:
        ctx = {"run": run} if run else {}  # the web app tags entries with its run id (evidence packs)
        if not isinstance(approver, str) or not approver.strip():
            self.audit.append("approve_refused", proposal=proposal.id, reason="approver name is required", **ctx)
            raise ValueError("approval needs a non-empty approver name")
        if proposal.status != "PROPOSED":
            raise ValueError(f"{proposal.id} is {proposal.status}, only PROPOSED can be approved")
        proposal.status, proposal.approved_by, proposal.approved_at = "APPROVED", approver.strip(), _now()
        proposal.history.append("APPROVED")
        self.audit.append("approved", proposal=proposal.id, check=proposal.check_id, approver=proposal.approved_by,
                          finding_ids=proposal.finding_ids, **ctx)
        return proposal

    # --- apply ---
    def _refuse(self, proposals, target, reason, ctx=None):
        self.audit.append("apply_refused", proposals=[p.id for p in proposals], target=target, reason=reason,
                          **(ctx or {}))
        raise ApplyRefused(reason)

    def apply(self, proposals: list[Proposal], target_url: str, run: str | None = None) -> bool:
        """Apply approved proposals to the loopback demo app. Returns True if applied and healthy."""
        ctx = {"run": run} if run else {}
        host = (urlsplit(target_url).hostname or "").lower()
        try:
            scope.check_target(target_url)  # no verified list passed: only loopback can pass
        except scope.OutOfScope as e:
            self._refuse(proposals, target_url, f"target refused: {e}", ctx)
        if not scope.is_loopback(host):
            self._refuse(proposals, target_url, "fixes are applied only to the loopback demo app", ctx)
        for p in proposals:
            if p.status != "APPROVED":
                self._refuse(proposals, target_url, f"{p.id} is {p.status}; apply needs APPROVED", ctx)
            if p.target.rstrip("/") != target_url.rstrip("/"):
                self._refuse(proposals, target_url, f"{p.id} was proposed for {p.target}, not {target_url}", ctx)

        self._backup = self.control_path.read_text(encoding="utf-8") if self.control_path.exists() else None
        self._has_backup = True
        current = set()
        if self._backup:
            try:
                current = set(json.loads(self._backup).get("fixes", []))
            except (ValueError, AttributeError):
                current = set()
        self._write({"fixes": sorted(current | {p.check_id for p in proposals})})
        self.audit.append("applied", proposals=[p.id for p in proposals], target=target_url,
                          control_file=self.control_path.name, **ctx)

        if not self.health(target_url):
            self.rollback(reason="health check failed after apply", run=run)
            for p in proposals:
                p.status = "ROLLED_BACK"
                p.history.append("ROLLED_BACK")
            return False
        stamp = _now()
        for p in proposals:
            p.status, p.applied_at, p.note = "APPLIED", stamp, "applied to the loopback demo app"
            p.history.append("APPLIED")
        self.audit.append("health_ok", proposals=[p.id for p in proposals], target=target_url, **ctx)
        return True

    def reset_demo(self) -> list[str]:
        """Back to the vulnerable demo app: back up the control file (so rollback() can restore it), then
        clear its fixes. Returns the fix ids that were on. Audited as "demo_reset"."""
        self._backup = self.control_path.read_text(encoding="utf-8") if self.control_path.exists() else None
        self._has_backup = True
        previous = []
        if self._backup:
            try:
                previous = sorted(json.loads(self._backup).get("fixes", []))
            except (ValueError, AttributeError):
                previous = []
        self._write({"fixes": []})
        self.audit.append("demo_reset", control_file=self.control_path.name, previous_fixes=previous)
        return previous

    def rollback(self, reason: str = "manual", run: str | None = None) -> None:
        if not self._has_backup:
            raise RuntimeError("nothing to roll back")
        if self._backup is None:
            self.control_path.unlink(missing_ok=True)
        else:
            self._write_text(self._backup)
        self._has_backup = False
        self.audit.append("rolled_back", reason=reason, control_file=self.control_path.name, **({"run": run} if run else {}))

    def _write(self, data: dict) -> None:
        self._write_text(json.dumps(data, indent=2))

    def _write_text(self, text: str) -> None:
        tmp = self.control_path.with_suffix(".tmp")
        self.control_path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, self.control_path)  # atomic: the demo app never reads a half-written file
