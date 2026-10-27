"""Run the MAYA agent loop against the local demo app (127.0.0.1 only).

Live (user runs this; makes real model calls):  .venv\\Scripts\\python scripts\\live_agent_run.py [--fixed]
Offline (scripted fake model, no key read):      .venv\\Scripts\\python scripts\\live_agent_run.py --dry-run [--fixed]

Hard caps: 6 steps, 40000 tokens, reasoning_effort=low. Prints per step: tool, args, result summary,
tokens (reasoning separate) and model latency; then the verified report and dropped claims.
Never prints reasoning text or the key. Report files go to data/reports/ (git-ignored).
"""
import argparse
import json
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from demo_app.app import make_server  # noqa: E402
from src.agent.loop import run_agent  # noqa: E402
from src.agent.tools import ToolRegistry  # noqa: E402
from src.budget import TokenBudget  # noqa: E402
from src.evidence import EvidenceStore  # noqa: E402
from src.provider import ChatResult, MockProvider, Provider, ProviderError, load_config  # noqa: E402
from src.report import build_report, write_report  # noqa: E402
from src.scanner.http_client import HttpClient  # noqa: E402

MAX_STEPS = 6
TOKEN_CAP = 40_000
REASONING_EFFORT = "low"
GOAL = ("Check the target web app for misconfigurations. Use run_check on relevant paths (for example /, /.env, "
        "/db.sql, /backup/), read evidence with get_evidence, then give the final claims JSON.")


class TimedProvider:
    """Wraps a provider and records each call's latency; nothing else is touched or logged."""

    def __init__(self, inner):
        self.inner, self.latencies = inner, []

    def chat(self, messages, **kw):
        t0 = time.perf_counter()
        try:
            return self.inner.chat(messages, **kw)
        finally:
            self.latencies.append(time.perf_counter() - t0)


def _call(name, args, cid):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def dry_script() -> list[ChatResult]:
    usage = {"prompt_tokens": 900, "completion_tokens": 120, "total_tokens": 1020,
             "completion_tokens_details": {"reasoning_tokens": 80}}
    claims = [
        {"finding_id": "missing_csp:/", "claim_type": "header_missing", "quoted_evidence": "HTTP 200"},
        {"finding_id": "exposed_env_file:/.env", "claim_type": "file_exposed", "quoted_evidence": "API_KEY=fak...al"},
        {"finding_id": "exposed_git_dir:/.git/HEAD", "claim_type": "file_exposed", "quoted_evidence": "ref: refs"},
    ]
    return [
        ChatResult("", True, dict(usage), tool_calls=[
            _call("run_check", {"check_id": "missing_csp", "path": "/"}, "d1"),
            _call("run_check", {"check_id": "exposed_env_file", "path": "/.env"}, "d2")], finish_reason="tool_calls"),
        ChatResult("", True, dict(usage), tool_calls=[_call("list_findings", {}, "d3")], finish_reason="tool_calls"),
        ChatResult(json.dumps({"claims": claims}), True, dict(usage), finish_reason="stop"),
    ]


def main(argv=None, transport=None, data_dir: Path | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--fixed", action="store_true", help="run the demo app in fixed mode")
    p.add_argument("--dry-run", action="store_true", help="scripted fake model; no key, no model network call")
    args = p.parse_args(argv)
    data_dir = Path(data_dir or ROOT / "data")
    cfg = load_config(ROOT / "config.yaml")

    if args.dry_run:
        inner, model = MockProvider(script=dry_script()), "mock (dry-run, scripted)"
    else:
        inner = Provider.from_config(ROOT / "config.yaml", reasoning_effort=REASONING_EFFORT, transport=transport)
        model = cfg["model"]
    provider = TimedProvider(inner)

    server = make_server(port=0, fixed=args.fixed)  # 127.0.0.1 only, enforced in make_server
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    run_id = uuid.uuid4().hex[:12]
    store = EvidenceStore(data_dir / "evidence.db")
    client = HttpClient(store=store)
    budget = TokenBudget(run_id, cap=TOKEN_CAP, path=data_dir / "usage.jsonl")
    registry = ToolRegistry(client, base, store)
    print(f"MAYA agent run {run_id} | model={model} | effort={REASONING_EFFORT} | max_steps={MAX_STEPS} | "
          f"token_cap={TOKEN_CAP} | demo app {'FIXED' if args.fixed else 'VULNERABLE'} at {base}")
    try:
        result = run_agent(provider, registry, budget, GOAL, max_steps=MAX_STEPS)
    except ProviderError as e:  # messages are already key-scrubbed by the provider
        print(f"Model call failed: {e}")
        return 1
    finally:
        client.close()
        server.shutdown()
        server.server_close()

    for t in result.trace:
        tok = t["tokens"]
        lat = provider.latencies[t["step"] - 1] if t["step"] - 1 < len(provider.latencies) else float("nan")
        print(f"step {t['step']}: tool={t['tool']} args={t['args']} -> {t['result_summary']} | "
              f"tokens prompt={tok['prompt']} completion={tok['completion']} reasoning={tok['reasoning']} | "
              f"model latency={lat:.2f}s")
    print(f"stop_reason={result.stop_reason} | run tokens={budget.run}")

    print("\nVerified claims:")
    for v in result.report["verified"] or []:
        print(f"  [{v['severity'].upper()}] {v['text']} evidence={v['evidence_id']} quote={v['quoted_evidence']!r}")
    if not result.report["verified"]:
        print("  (none)")
    print("Dropped claims (insufficient evidence):")
    for d in result.report["insufficient"] or []:
        print(f"  {d['finding_id']}: {d['reason']}")
    if not result.report["insufficient"]:
        print("  (none)")

    report = build_report(result.report["verified"], registry.findings, model, REASONING_EFFORT,
                          budget.run, result.report["insufficient"])
    md, page = write_report(report, data_dir / "reports", f"report-{run_id}")
    print(f"\nReport written: {md} and {page}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
