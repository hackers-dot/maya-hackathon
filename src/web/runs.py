"""Scan runs for the web app: one background thread per run, one active run per user.

Mode "model": the existing agent loop (src/agent/loop.py) with the real provider, hard caps unchanged
(6 steps, 40000 tokens, low reasoning effort). Mode "deterministic": no model key configured, so every
HTTP check runs in a fixed order and nothing is generated. Both stream steps into run.trace, and
findings into run.findings, which the trace endpoint reads while the run is in progress.
"""
import secrets
import threading
import time
from dataclasses import dataclass, field

from src.agent.loop import run_agent
from src.agent.tools import ToolRegistry
from src.agent.verifier import SEVERITY_ORDER, evidence_line
from src.budget import TokenBudget
from src.evidence import EvidenceStore
from src.provider import ProviderError
from src.fixes import build_proposals
from src.report import TEXT, owasp_label
from src.scanner.http_client import HttpClient
from src.scanner.runner import FULL_SCAN
from src.severity import rule

MAX_STEPS = 6
TOKEN_CAP = 40_000
MODE_LABELS = {"model": "Model-guided scan", "deterministic": "Deterministic only (no model key configured)"}
GOAL = ("Check the target web app for misconfigurations. Use run_check on relevant paths (for example /, /.env, "
        "/db.sql, /backup/), then give the final claims JSON.")


class RunBusy(RuntimeError):
    pass


@dataclass
class Run:
    id: str
    user_id: int
    asset_id: str
    demo: bool
    mode: str
    model: str
    started_at: float
    status: str = "running"  # running | finished | forced_final | budget_exceeded | failed
    trace: list = field(default_factory=list)
    findings: dict = field(default_factory=dict)
    result: dict | None = None
    error: str | None = None
    finished_at: float | None = None
    base: str | None = None              # the URL that was scanned (the user's own demo app for loopback)
    verified: frozenset = frozenset()    # scope allow-list used for this run (empty for the demo app)
    proposals: dict = field(default_factory=dict)  # fix proposals from fixed templates, built when the scan ends
    retest: dict | None = None
    claims: dict | None = None           # raw verifier output (model runs only), for the downloadable report


def finding_status(run: Run, finding_id: str) -> str:
    """FIXED only when a re-test showed the finding gone; APPLIED when its fix was applied but not yet
    re-tested (or the re-test still saw it); otherwise DETECTED."""
    if run.retest and finding_id in run.retest["resolved"]:
        return "FIXED"
    if any(p.status == "APPLIED" and finding_id in p.finding_ids for p in run.proposals.values()):
        return "APPLIED"
    return "DETECTED"


def finding_view(f, store) -> dict:
    """All three audiences' texts come from the fixed templates in src/report.py, never from a model."""
    t = TEXT[f.check]
    return {"id": f.id, "check": f.check, "severity": rule(f.check).severity, "location": f.location,
            "title": t.title, "evidence_line": evidence_line(f, store), "plain": t.plain, "analogy": t.analogy,
            "technical": t.technical, "fix": t.fix, "owasp": owasp_label(f.check)}


def proposal_view(p) -> dict:
    return {"id": p.id, "check_id": p.check_id, "finding_ids": p.finding_ids, "kind": p.plan.kind,
            "nginx": p.plan.nginx, "apache": p.plan.apache, "python_note": p.plan.python_note,
            "risk": p.plan.risk, "rollback": p.plan.rollback, "status": p.status, "note": p.note,
            "approved_by": p.approved_by, "approved_at": p.approved_at, "applied_at": p.applied_at}


def findings_view(findings: dict, store) -> list[dict]:
    items = [finding_view(f, store) for f in list(dict(findings).values())]
    return sorted(items, key=lambda i: (SEVERITY_ORDER.index(i["severity"]), i["check"], i["location"]))


class RunManager:
    def __init__(self, app):
        self.app = app
        self.runs: dict[str, Run] = {}
        self._lock = threading.Lock()

    def active_for(self, user_id: int) -> Run | None:
        return next((r for r in self.runs.values() if r.user_id == user_id and r.status == "running"), None)

    def last_for_asset(self, user_id: int, asset_id: str) -> str | None:
        mine = [r for r in self.runs.values() if r.user_id == user_id and r.asset_id == asset_id]
        return max(mine, key=lambda r: r.started_at).id if mine else None

    def get(self, user_id: int, run_id) -> Run | None:
        """The run only if it belongs to this user; another user's run behaves like a missing one."""
        run = self.runs.get(run_id) if isinstance(run_id, str) else None
        return run if run is not None and run.user_id == user_id else None

    def start(self, user_id: int, asset: dict) -> Run:
        provider, model = self.app.provider_factory()
        with self._lock:
            if self.active_for(user_id):
                raise RunBusy("a scan is already running; wait for it to finish")
            run = Run(id=secrets.token_hex(12), user_id=user_id, asset_id=asset["id"], demo=asset["kind"] == "demo",
                      mode="model" if provider else "deterministic", model=model if provider else "none",
                      started_at=self.app.clock())
            self.runs[run.id] = run
        threading.Thread(target=self._work, args=(run, asset, provider), daemon=True, name=f"run-{run.id}").start()
        return run

    def client_for(self, run: Run, store) -> HttpClient:
        """Same scope as the scan: the demo app on loopback, or only the verified host."""
        if run.demo:
            return HttpClient(store=store)
        return HttpClient(verified=run.verified, store=store, transport=self.app.http_transport)

    def findings_for_user(self, user_id: int, labels: dict[str, str]) -> list[dict]:
        """This user's verified findings: the latest completed scan of each of their assets (labels: asset id ->
        label). Nothing from any other user's runs is ever included."""
        latest: dict[str, Run] = {}
        for r in self.runs.values():
            if r.user_id == user_id and r.result and r.asset_id in labels:
                if r.asset_id not in latest or r.started_at >= latest[r.asset_id].started_at:  # tie: later start
                    latest[r.asset_id] = r
        items = []
        for asset_id, r in latest.items():
            for f in r.result["findings"]:
                items.append({**f, "asset": labels[asset_id], "status": finding_status(r, f["id"])})
        return sorted(items, key=lambda i: (SEVERITY_ORDER.index(i["severity"]), i["id"]))

    def history_for_user(self, user_id: int, labels: dict[str, str], limit: int = 3) -> list[dict]:
        """This user's last `limit` completed scans, newest first: run id, UTC date, findings count, status
        counts and the ids of findings that were FIXED (re-tested). Only this user's runs."""
        mine = [(r.started_at, i, r) for i, r in enumerate(self.runs.values())  # dict order = start order (ties)
                if r.user_id == user_id and r.result and r.asset_id in labels]
        done = [r for _, _, r in sorted(mine, key=lambda t: t[:2], reverse=True)[:limit]]
        out = []
        for r in done:
            statuses = {f["id"]: finding_status(r, f["id"]) for f in r.result["findings"]}
            counts = {s: list(statuses.values()).count(s) for s in ("DETECTED", "APPLIED", "FIXED")}
            out.append({"run": r.id, "asset": labels[r.asset_id], "findings": len(statuses),
                        "date": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(r.started_at)),
                        "statuses": {k: v for k, v in counts.items() if v},
                        "fixed_ids": sorted(i for i, s in statuses.items() if s == "FIXED")})
        return out

    def usage_for(self, user_id: int) -> dict:
        totals = {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0, "total_tokens": 0}
        mine = [r for r in self.runs.values() if r.user_id == user_id]
        for r in mine:
            for k in totals:
                totals[k] += int(((r.result or {}).get("usage") or {}).get(k) or 0)
        return {"runs": len(mine), "model_runs": sum(r.mode == "model" for r in mine), "cap_per_run": TOKEN_CAP,
                **totals}

    # --- worker ---
    def _work(self, run: Run, asset: dict, provider):
        store = EvidenceStore(self.app.data_dir / "evidence.db")
        try:
            if run.demo:
                base, verified = self.app.ensure_demo(run.user_id), frozenset()
            else:
                base, verified = asset["origin"], frozenset({asset["host"]})
            run.base, run.verified = base, verified
            client = self.client_for(run, store)
            try:
                if run.mode == "model":
                    final = self._model_run(run, provider, client, base, store)
                else:
                    final = self._deterministic_run(run, client, base, store)
                ordered = sorted(build_proposals(dict(run.findings)),  # critical first, then check id
                                 key=lambda p: (SEVERITY_ORDER.index(rule(p.check_id).severity), p.check_id))
                run.proposals = {p.id: p for p in ordered}
                run.result["fix_first"] = run.result["findings"][:3]
                run.status = final  # published last, so a finished run always has its proposals
            finally:
                client.close()
        except ProviderError as e:  # messages are key-scrubbed by the provider
            run.status, run.error = "failed", f"model call failed: {e}"
        except Exception as e:  # noqa: BLE001 - surface any failure on the run instead of a silent thread death
            run.status, run.error = "failed", f"scan failed: {type(e).__name__}"
        finally:
            run.finished_at = self.app.clock()

    def _model_run(self, run: Run, provider, client, base: str, store):
        registry = ToolRegistry(client, base, store)
        run.findings = registry.findings  # same dict: findings appear live
        budget = TokenBudget(run.id, cap=TOKEN_CAP, path=self.app.data_dir / "usage.jsonl")
        result = run_agent(provider, registry, budget, GOAL, max_steps=MAX_STEPS, trace=run.trace)
        claims = result.report
        run.claims = claims
        run.result = {
            "findings": findings_view(registry.findings, store),
            "claims": [{"priority": v.get("priority"), "severity": v["severity"], "text": v["text"],
                        "evidence_line": v["supporting_evidence"], "note": v.get("note"),
                        "note_dropped": v.get("note_dropped")} for v in claims.get("verified", [])],
            "order_note": claims.get("order_note", ""),
            "dropped": [{"finding_id": d.get("finding_id"), "reason": d["reason"]} for d in claims["insufficient"]],
            "usage": dict(budget.run), "model_finished": result.final_answer_valid, "stop_reason": result.stop_reason,
        }
        return {"final": "finished", "forced_final": "forced_final"}.get(result.stop_reason, result.stop_reason)

    def _deterministic_run(self, run: Run, client, base: str, store):
        responses = {}
        for step, (path, fn) in enumerate(FULL_SCAN, 1):
            t0 = time.perf_counter()
            if path not in responses:
                responses[path] = client.get(base.rstrip("/") + path)
            found = fn(responses[path])
            for f in found:
                run.findings[f.id] = f
            run.trace.append({"step": step, "tool": "deterministic check", "args": f"{fn.__name__} {path}",
                              "result_summary": f"{len(found)} finding(s)",
                              "tokens": {"prompt": 0, "completion": 0, "reasoning": 0},
                              "latency": round(time.perf_counter() - t0, 3)})
        run.result = {"findings": findings_view(run.findings, store), "claims": [], "order_note": "",
                      "dropped": [], "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                                               "reasoning_tokens": 0},
                      "model_finished": False, "stop_reason": "deterministic"}
        return "finished"


def run_view(run: Run) -> dict:
    return {"id": run.id, "asset_id": run.asset_id, "status": run.status, "mode": run.mode,
            "mode_label": MODE_LABELS[run.mode], "model": run.model, "demo": run.demo, "error": run.error,
            "started_at": run.started_at, "finished_at": run.finished_at, "steps": len(run.trace),
            "can_apply": run.demo}  # fixes are applied only to the user's own loopback demo app


def retest_view(r: dict) -> dict:
    return {k: r[k] for k in ("status", "resolved", "still_present", "new", "per_check")}
