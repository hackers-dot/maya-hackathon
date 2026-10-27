"""Tool registry with an allowlist. The model proposes a call; code validates, executes and logs it."""
import json
import logging
import re

import httpx

from src.evidence import EvidenceStore, redact_text
from src.models import Finding
from src.scanner.http_client import HttpClient
from src.scanner.runner import NotRunnable, run_check
from src.scope import OutOfScope
from src.severity import TABLE

log = logging.getLogger("maya.agent.tools")

CHECK_IDS = sorted(TABLE)
# must start with a single "/", no scheme/host, no "..", no "@", "\" or "#", no whitespace
PATH_RE = re.compile(r"^/(?!/)[A-Za-z0-9._~!$&'()*+,;=:%/?-]{0,255}$")
EVIDENCE_ID_RE = re.compile(r"^[0-9a-f]{32}$")

TOOL_SPECS = [
    {"type": "function", "function": {
        "name": "run_check",
        "description": "Run one MAYA security check against a path on the target. Returns findings found by code.",
        "parameters": {"type": "object", "properties": {
            "check_id": {"type": "string", "enum": CHECK_IDS},
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
        "description": "Get a stored, redacted evidence record by its id, with its hash verification result.",
        "parameters": {"type": "object", "properties": {"id": {"type": "string"}},
                       "required": ["id"], "additionalProperties": False},
    }},
]
ARG_KEYS = {s["function"]["name"]: set(s["function"]["parameters"]["properties"]) for s in TOOL_SPECS}


class ToolError(ValueError):
    pass


def finding_summary(f: Finding) -> dict:
    return {"id": f.id, "check": f.check, "severity": f.severity, "location": f.location,
            "evidence_id": f.evidence[0].record_id}


class ToolRegistry:
    def __init__(self, client: HttpClient, base_url: str, store: EvidenceStore):
        self.client, self.base_url, self.store = client, base_url, store
        self.findings: dict[str, Finding] = {}
        self.log: list[dict] = []
        self._handlers = {"run_check": self._run_check, "list_findings": self._list_findings,
                          "get_evidence": self._get_evidence}
        assert set(self._handlers) == set(ARG_KEYS)  # allowlist and specs stay in sync

    def call(self, name: str, raw_args) -> dict:
        """Validate then execute. Never raises; returns {"ok", "result"|"error", "summary"}."""
        try:
            args = self._validate(name, raw_args)
        except ToolError as e:
            return self._record(name, raw_args, {"ok": False, "error": f"rejected: {e}", "summary": f"rejected: {e}"})
        try:
            result, summary = self._handlers[name](**args)
            out = {"ok": True, "result": result, "summary": summary}
        except (NotRunnable, OutOfScope, KeyError) as e:
            out = {"ok": False, "error": str(e), "summary": f"error: {e}"}
        except httpx.HTTPError as e:
            out = {"ok": False, "error": f"request failed: {type(e).__name__}", "summary": "error: request failed"}
        return self._record(name, args, out)

    def _record(self, name, args, out) -> dict:
        entry = {"tool": name, "args": redact_text(json.dumps(args) if not isinstance(args, str) else args),
                 "ok": out["ok"], "summary": out["summary"]}
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
            if args["check_id"] not in TABLE:
                raise ToolError(f"check_id not allowed: {args['check_id']!r}")
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
        return {"findings": [finding_summary(f) for f in found]}, f"{len(found)} finding(s)"

    def _list_findings(self):
        return {"findings": [finding_summary(f) for f in self.findings.values()]}, f"{len(self.findings)} finding(s)"

    def _get_evidence(self, id: str):
        record = self.store.get(id)  # KeyError if absent; already redacted at storage time
        return {"record": record, "verified": self.store.verify(id)}, f"evidence {id[:8]}"
