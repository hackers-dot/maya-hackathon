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
from src.report import TEXT
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


def finding_view(f, store) -> dict:
    return {"id": f.id, "check": f.check, "severity": rule(f.check).severity, "location": f.location,
            "title": TEXT[f.check].title, "evidence_line": evidence_line(f, store)}


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

    # --- worker ---
    def _work(self, run: Run, asset: dict, provider):
        store = EvidenceStore(self.app.data_dir / "evidence.db")
        try:
            if run.demo:
                base, verified = self.app.ensure_demo(), frozenset()
                client = HttpClient(store=store)
            else:
                base, verified = asset["origin"], frozenset({asset["host"]})
                client = HttpClient(verified=verified, store=store, transport=self.app.http_transport)
            try:
                if run.mode == "model":
                    self._model_run(run, provider, client, base, store)
                else:
                    self._deterministic_run(run, client, base, store)
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
        run.result = {
            "findings": findings_view(registry.findings, store),
            "claims": [{"priority": v.get("priority"), "severity": v["severity"], "text": v["text"],
                        "evidence_line": v["supporting_evidence"], "note": v.get("note"),
                        "note_dropped": v.get("note_dropped")} for v in claims.get("verified", [])],
            "order_note": claims.get("order_note", ""),
            "dropped": [{"finding_id": d.get("finding_id"), "reason": d["reason"]} for d in claims["insufficient"]],
            "usage": dict(budget.run), "model_finished": result.final_answer_valid, "stop_reason": result.stop_reason,
        }
        run.status = {"final": "finished", "forced_final": "forced_final"}.get(result.stop_reason, result.stop_reason)

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
        run.status = "finished"


def run_view(run: Run) -> dict:
    return {"id": run.id, "asset_id": run.asset_id, "status": run.status, "mode": run.mode,
            "mode_label": MODE_LABELS[run.mode], "model": run.model, "demo": run.demo, "error": run.error,
            "started_at": run.started_at, "finished_at": run.finished_at, "steps": len(run.trace)}
