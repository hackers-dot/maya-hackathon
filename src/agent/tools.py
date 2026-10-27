"""Tool registry with an allowlist. The model proposes a call; code validates, executes and logs it."""
import json
import logging
import re

import httpx

from src.agent.verifier import evidence_line
from src.evidence import EvidenceStore, redact_text
from src.models import Finding
from src.scanner.http_client import HttpClient
from src.scanner.runner import HTTP_CHECKS, NotRunnable, run_check
from src.scope import OutOfScope

log = logging.getLogger("maya.agent.tools")

# Only checks that can actually run over HTTP are offered. DNS/TLS checks stay in the severity table
# (src/severity.py) but are hidden from the model until they are runnable.
RUNNABLE_CHECKS = sorted(HTTP_CHECKS)
MAX_GET_EVIDENCE = 2
# must start with a single "/", no scheme/host, no "..", no "@", "\" or "#", no whitespace
PATH_RE = re.compile(r"^/(?!/)[A-Za-z0-9._~!$&'()*+,;=:%/?-]{0,255}$")
EVIDENCE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
DEFERRED_MESSAGE = "deferred: per-step limit reached, call again next step"
EVIDENCE_LIMIT_MESSAGE = (f"limit reached: get_evidence is capped at {MAX_GET_EVIDENCE} calls per run; "
                          "use the evidence summary returned by run_check")

TOOL_SPECS = [
    {"type": "function", "function": {
        "name": "run_check",
        "description": "Run one MAYA security check against a path on the target. Returns the findings code "
                       "detected, each with a one-line evidence summary.",
        "parameters": {"type": "object", "properties": {
            "check_id": {"type": "string", "enum": RUNNABLE_CHECKS},
            "path": {"type": "string", "description": "URL path starting with /, e.g. /login"},
        }, "required": ["check_id", "path"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "list_findings",
        "description": "List all findings recorded so far in this run.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "get_evidence",
        "description": f"Get a full stored evidence record by id (at most {MAX_GET_EVIDENCE} calls per run; "
                       "usually not needed because run_check already returns the evidence line).",
        "parameters": {"type": "object", "properties": {"id": {"type": "string"}},
                       "required": ["id"], "additionalProperties": False},
    }},
]
ARG_KEYS = {s["function"]["name"]: set(s["function"]["parameters"]["properties"]) for s in TOOL_SPECS}


class ToolError(ValueError):
    pass


class ToolRegistry:
    def __init__(self, client: HttpClient, base_url: str, store: EvidenceStore):
        self.client, self.base_url, self.store = client, base_url, store
        self.findings: dict[str, Finding] = {}
        self.attempted_checks: set[str] = set()
        self.evidence_calls = 0
        self.log: list[dict] = []
        self._handlers = {"run_check": self._run_check, "list_findings": self._list_findings,
                          "get_evidence": self._get_evidence}
        assert set(self._handlers) == set(ARG_KEYS)  # allowlist and specs stay in sync

    def all_checks_attempted(self) -> bool:
        return set(RUNNABLE_CHECKS) <= self.attempted_checks

    def summary(self, f: Finding) -> dict:
        line = evidence_line(f, self.store)
        return {"id": f.id, "check": f.check, "severity": f.severity, "location": f.location,
                "evidence_id": f.evidence[0].record_id, "evidence_summary": f"{f.id} | {f.check} | {line}"}

    def call(self, name: str, raw_args) -> dict:
        """Validate then execute. Never raises; returns {"ok", "result"|"error"|"message", "summary"}."""
        try:
            args = self._validate(name, raw_args)
        except ToolError as e:
            return self._record(name, raw_args, {"ok": False, "error": f"rejected: {e}", "summary": f"rejected: {e}"})
        if name == "get_evidence":
            if self.evidence_calls >= MAX_GET_EVIDENCE:
                return self._record(name, args, {"ok": False, "status": "limited", "message": EVIDENCE_LIMIT_MESSAGE,
                                                 "summary": "limit reached"})
            self.evidence_calls += 1
        if name == "run_check":
            self.attempted_checks.add(args["check_id"])
        try:
            result, summary = self._handlers[name](**args)
            out = {"ok": True, "result": result, "summary": summary}
        except (NotRunnable, OutOfScope, KeyError) as e:
            out = {"ok": False, "error": str(e), "summary": f"error: {e}"}
        except httpx.HTTPError as e:
            out = {"ok": False, "error": f"request failed: {type(e).__name__}", "summary": "error: request failed"}
        return self._record(name, args, out)

    def defer(self, name, raw_args) -> dict:
        """Per-step limit hit: not validated, not executed; logged as deferred so the model can retry."""
        return self._record(name, raw_args, {"ok": False, "status": "deferred", "message": DEFERRED_MESSAGE,
                                             "summary": "deferred"})

    def _record(self, name, args, out) -> dict:
        status = out.get("status") or ("ok" if out["ok"] else
                                       "rejected" if out.get("error", "").startswith("rejected") else "error")
        entry = {"tool": name, "args": redact_text(json.dumps(args) if not isinstance(args, str) else args),
                 "ok": out["ok"], "status": status, "summary": out["summary"]}
        self.log.append(entry)
        log.info("tool call %s", entry)
        return out

    def _validate(self, name, raw_args) -> dict:
        if not isinstance(name, str) or name not in self._handlers:
            raise ToolError(f"unknown tool {name!r}")
        if raw_args in (None, ""):
            args = {}
        elif isinstance(raw_args, str):
            try:
                args = json.loads(raw_args)
            except ValueError:
                raise ToolError("arguments are not valid JSON") from None
        else:
            raise ToolError("arguments must be a JSON string")
        if not isinstance(args, dict):
            raise ToolError("arguments must be a JSON object")
        if set(args) != ARG_KEYS[name]:
            raise ToolError(f"expected exactly {sorted(ARG_KEYS[name])}, got {sorted(args)}")
        if name == "run_check":
            if args["check_id"] not in RUNNABLE_CHECKS:
                raise ToolError(f"check_id not available: {args['check_id']!r}")
            path = args["path"]
            if not isinstance(path, str) or not PATH_RE.match(path) or "://" in path or ".." in path:
                raise ToolError("path must start with / and contain no scheme or host")
        if name == "get_evidence" and not (isinstance(args["id"], str) and EVIDENCE_ID_RE.match(args["id"])):
            raise ToolError("id must be a 32-char hex evidence id")
        return args

    def _run_check(self, check_id: str, path: str):
        found = run_check(self.client, self.base_url, check_id, path)
        for f in found:
            self.findings[f.id] = f
        return {"findings": [self.summary(f) for f in found]}, f"{len(found)} finding(s)"

    def _list_findings(self):
        return {"findings": [self.summary(f) for f in self.findings.values()]}, f"{len(self.findings)} finding(s)"

    def _get_evidence(self, id: str):
        record = self.store.get(id)  # KeyError if absent; already redacted at storage time
        return {"record": record, "verified": self.store.verify(id)}, f"evidence {id[:8]}"
