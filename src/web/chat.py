"""Chat for the MAYA web app: per-user chats in SQLite, model replies through the existing provider.

- The model sees MAYA's fixed system prompt, a compact list of THIS user's verified findings (the only facts
  about their systems), and at most the last 10 messages of the chat.
- User text is redacted (src/evidence.redact_text) before it is stored or sent, so pasted secrets are masked.
- Replies are plain text, rendered with textContent. A reply is labelled "unverified model text" unless every
  sentence cites one of the user's real finding ids; ids the model invents are listed and force the label.
- Limits: 2000 characters per message, 20 messages per minute per user, a rolling 24 h token budget per user,
  reasoning_effort low, max_tokens 1200. Every reply's usage is also recorded through TokenBudget.
"""
import json
import re
import secrets
import sqlite3
import threading
from collections import defaultdict, deque
from contextlib import closing
from pathlib import Path

from src.evidence import redact_text

MAX_CHARS = 2000
RATE_PER_MINUTE = 20
HISTORY_CAP = 10
MAX_TOKENS = 1200
CHAT_TOKEN_CAP = 60_000  # per user, rolling 24 hours
CONTEXT_FINDINGS_CAP = 30
LABEL_UNVERIFIED = "unverified model text"
LABEL_RESTATES = "restates verified findings"

SYSTEM_PROMPT = (
    "You are MAYA, a cybersecurity assistant for website owners and people learning about security.\n"
    "Rules:\n"
    "1. Explain in simple, friendly language. Reply in English, or in Hindi/Hinglish if the user writes that way.\n"
    "2. Never provide exploit code, attack payloads, or step-by-step attack instructions against systems the "
    "user does not own. Never help scan, probe or test sites the user does not own. If asked, refuse politely, "
    "explain why, and suggest a safe alternative (MAYA's demo app, or written permission from the owner).\n"
    "3. Do not make claims about the user's own systems unless they come from MAYA's verified findings listed "
    "below. When you mention the user's site, cite finding ids in square brackets, for example "
    "[missing_csp:/]. If something is not in the findings, say MAYA has not checked it.\n"
    "4. MAYA only runs read-only checks on sites the user has verified they own; fixes need the user's approval.\n"
    "5. Each finding has a status. FIXED means the fix was applied and a re-test showed the issue is gone: never "
    "describe a FIXED finding as still present; say it was fixed and re-tested, and cite its id. APPLIED means "
    "the fix was applied but not yet confirmed by a re-test. DETECTED means no fix has been applied yet.\n"
    "6. If a SCAN HISTORY is listed, MAYA has scanned the user's site: never say that no scan was done. If the "
    "latest scan found no issues, say \"no issues detected in the checks run\" (never that the site is secure or "
    "safe), and mention earlier findings that were fixed and re-tested, citing their ids.\n"
    "7. Never say a scan found nothing if the history says it found N > 0 findings. Describe the scan result "
    "and the later re-test outcome separately, e.g. \"the scan found 12 findings; a later re-test showed all of "
    "them fixed\"."
)


class ChatError(ValueError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


NO_SCANS = "VERIFIED FINDINGS: none. MAYA has not scanned any site this user owns, so make no claims about it."
NO_ISSUES = "latest scan: no issues detected in the checks run"


def history_context(history: list[dict]) -> list[str]:
    """history: newest first, from RunManager.history_for_user."""
    lines = ["SCAN HISTORY (newest first; MAYA has scanned this user's site):"]
    for h in history:
        n = h["findings"]
        counts = ", ".join(f"{k} {v}" for k, v in h["statuses"].items()) or "no findings"
        lines.append(f"- run {h['run']} on {h['asset']}, {h['date']}: scan found {n} finding{'s' if n != 1 else ''}; "
                     f"later re-test: {h.get('retest', 'no re-test')} (statuses: {counts})")
    latest = history[0]
    if latest["findings"] == 0:
        lines.append(f"{NO_ISSUES} (run {latest['run']} on {latest['asset']}).")
        fixed = [(h["run"], i) for h in history[1:] for i in h["fixed_ids"]]
        if fixed:
            lines.append("Earlier findings that were FIXED and re-tested: "
                         + ", ".join(f"[{i}] (run {run})" for run, i in fixed))
    return lines


def known_ids(items: list[dict], history: list[dict] | None = None) -> set[str]:
    """Finding ids the model may cite: current findings plus earlier FIXED ones listed in the history."""
    return {f["id"] for f in items} | {i for h in history or [] for i in h["fixed_ids"]}


def findings_context(items: list[dict], history: list[dict] | None = None) -> str:
    """Compact block of the user's own verified findings. items: dicts with asset/id/severity/check/location/
    evidence_line and status (DETECTED / APPLIED / FIXED, from the latest run's fix flow). history: the user's
    last scans (newest first), so an empty latest scan is never mistaken for "no scan"."""
    if not items and not history:
        return NO_SCANS
    lines = history_context(history) if history else []
    if not items:
        return "\n".join(lines + ["VERIFIED FINDINGS: none open in the latest scan of each site."])
    lines.append("VERIFIED FINDINGS (the only facts you may state about the user's systems):")
    for f in items[:CONTEXT_FINDINGS_CAP]:
        ev = str(f.get("evidence_line") or "")[:120]
        lines.append(f"- [{f['id']}] status {f.get('status', 'DETECTED')}: {f['severity']} {f['check']} at "
                     f"{f['location']} on {f['asset']}; evidence: {ev}")
    return "\n".join(lines)


# a sentence ends at . ! ? followed by whitespace, or at a line break (so "/.env" is not a sentence end)
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n+")
CITATION = re.compile(r"\[([a-z_]+:[^\]\n]*)\]")  # DNS ids contain a space: [missing_spf:TXT shop.example]


def label_reply(text: str, known_ids: set[str]) -> dict:
    cited = CITATION.findall(text)
    known = sorted({c for c in cited if c in known_ids})
    unknown = sorted({c for c in cited if c not in known_ids})
    sentences = [s.strip() for s in SENTENCE_BREAK.split(text) if s.strip(" .!?\t")]
    restates = bool(sentences) and not unknown and all(any(k in s for k in known) for s in sentences)
    return {"label": LABEL_RESTATES if restates else LABEL_UNVERIFIED, "cited": known, "cited_unknown": unknown}


class ChatStore:
    def __init__(self, db_path: Path, clock):
        self.path, self.clock = Path(db_path), clock
        self._recent: dict[int, deque] = defaultdict(deque)
        self._lock = threading.Lock()
        with closing(sqlite3.connect(self.path)) as c, c:
            c.execute("CREATE TABLE IF NOT EXISTS chats (id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, "
                      "title TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL)")
            c.execute("CREATE TABLE IF NOT EXISTS chat_messages (id INTEGER PRIMARY KEY, chat_id TEXT NOT NULL, "
                      "user_id INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, meta TEXT, "
                      "tokens INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL)")

    def _db(self):
        return closing(sqlite3.connect(self.path))

    def create(self, user_id: int, title: str = "New chat") -> dict:
        now = self.clock()
        chat = {"id": secrets.token_hex(8), "title": (title or "New chat")[:80], "created_at": now, "updated_at": now}
        with self._db() as c, c:
            c.execute("INSERT INTO chats VALUES (?, ?, ?, ?, ?)",
                      (chat["id"], user_id, chat["title"], now, now))
        return chat

    def list(self, user_id: int) -> list[dict]:
        with self._db() as c:
            rows = c.execute("SELECT id, title, created_at, updated_at FROM chats WHERE user_id = ? "
                             "ORDER BY updated_at DESC", (user_id,)).fetchall()
        return [dict(zip(("id", "title", "created_at", "updated_at"), r)) for r in rows]

    def get(self, user_id: int, chat_id) -> dict | None:
        """The chat only if it belongs to this user; another user's id behaves like a missing one."""
        if not isinstance(chat_id, str):
            return None
        with self._db() as c:
            row = c.execute("SELECT id, title, created_at, updated_at FROM chats WHERE id = ? AND user_id = ?",
                            (chat_id, user_id)).fetchone()
            if row is None:
                return None
            msgs = c.execute("SELECT role, content, meta, created_at FROM chat_messages WHERE chat_id = ? "
                             "ORDER BY id", (chat_id,)).fetchall()
        chat = dict(zip(("id", "title", "created_at", "updated_at"), row))
        chat["messages"] = [{"role": r, "content": t, "meta": json.loads(m) if m else None, "created_at": at}
                            for r, t, m, at in msgs]
        return chat

    def add(self, user_id: int, chat_id: str, role: str, content: str, meta: dict | None = None, tokens: int = 0,
            title: str | None = None) -> dict:
        now = self.clock()
        with self._db() as c, c:
            c.execute("INSERT INTO chat_messages (chat_id, user_id, role, content, meta, tokens, created_at) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?)",
                      (chat_id, user_id, role, content, json.dumps(meta) if meta else None, tokens, now))
            if title:
                c.execute("UPDATE chats SET title = ?, updated_at = ? WHERE id = ?", (title[:80], now, chat_id))
            else:
                c.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (now, chat_id))
        return {"role": role, "content": content, "meta": meta, "created_at": now}

    def delete_user(self, user_id: int) -> int:
        with self._db() as c, c:
            c.execute("DELETE FROM chat_messages WHERE user_id = ?", (user_id,))
            return c.execute("DELETE FROM chats WHERE user_id = ?", (user_id,)).rowcount

    def tokens_used(self, user_id: int, window: float = 86_400) -> int:
        with self._db() as c:
            row = c.execute("SELECT COALESCE(SUM(tokens), 0) FROM chat_messages WHERE user_id = ? AND created_at > ?",
                            (user_id, self.clock() - window)).fetchone()
        return int(row[0])

    def allow_message(self, user_id: int) -> bool:
        """20 messages per rolling minute per user."""
        now = self.clock()
        with self._lock:
            q = self._recent[user_id]
            while q and q[0] <= now - 60:
                q.popleft()
            if len(q) >= RATE_PER_MINUTE:
                return False
            q.append(now)
            return True


def clean_user_text(content) -> str:
    if not isinstance(content, str) or not content.strip():
        raise ChatError(400, "type a message first")
    if len(content) > MAX_CHARS:
        raise ChatError(400, f"messages are limited to {MAX_CHARS} characters")
    return redact_text(content.strip())  # pasted keys, tokens and passwords are masked before storing/sending


def build_messages(context: str, history: list[dict]) -> list[dict]:
    """System prompt + the user's verified findings + at most the last HISTORY_CAP chat messages."""
    turns = [{"role": m["role"], "content": m["content"]} for m in history if m["role"] in ("user", "assistant")]
    return [{"role": "system", "content": SYSTEM_PROMPT + "\n\n" + context}] + turns[-HISTORY_CAP:]
