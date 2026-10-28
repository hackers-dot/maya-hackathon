"""Live eval: N scans of the vulnerable loopback demo app with the real model (the user runs this).

Live (real model calls, key read by the provider at call time):
    .venv\\Scripts\\python scripts\\eval_live.py [--runs 3] [--write-doc]
Offline check of this script (scripted perfect model, no key, no model network call):
    .venv\\Scripts\\python scripts\\eval_live.py --dry-run

Per run: findings detected by code, claims kept / dropped, FAKE CLAIMS THAT REACHED THE REPORT, tokens and model
latency. Every scan has the usual hard caps: 6 steps, 40000 tokens (TokenBudget), reasoning_effort=low.
--write-doc appends the measured table to docs/eval_results.md (labelled with the UTC date and the model) and
records it in docs/eval_results.json for the dashboard. It is refused with --dry-run: scripted numbers are
never written down as live ones.
"""
import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.eval.harness import (DOC, LIVE_HEADING, NOT_MEASURED, SUMMARY, TOKEN_CAP, ScriptedModel,  # noqa: E402
                              load_cases, run_case)
from src.provider import Provider, ProviderError, load_config  # noqa: E402
from src.web.runs import GOAL, MAX_STEPS  # noqa: E402

REASONING_EFFORT = "low"


def live_table(rows: list[dict]) -> str:
    lines = ["| Run | Findings (code) | Claims kept | Claims dropped | FAKE CLAIMS IN REPORT | Tokens | "
             "Model latency (s) | Stop reason |", "|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        lines.append(f"| {i} | {r['code']['found']} | {r['claims']['kept']} | {r['claims']['dropped']} | "
                     f"**{r['fake_claims_in_report']}** | {r['tokens']} | {r['model_seconds']} | {r['stop_reason']} |")
    return "\n".join(lines)


def write_doc(rows: list[dict], model: str, doc: Path = DOC, summary_path: Path = SUMMARY) -> None:
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    section = (f"### {date} UTC, model `{model}`, {len(rows)} runs on the vulnerable demo app "
               f"(cap {TOKEN_CAP} tokens per scan, {MAX_STEPS} steps, effort {REASONING_EFFORT})\n\n"
               + live_table(rows) + "\n")
    text = doc.read_text(encoding="utf-8") if doc.exists() else f"# MAYA eval results\n\n{LIVE_HEADING}\n\n"
    if LIVE_HEADING not in text:
        text = text.rstrip() + f"\n\n{LIVE_HEADING}\n\n"
    head, live = text.split(LIVE_HEADING, 1)
    if NOT_MEASURED in live:  # first measurement replaces the placeholder line
        live = "\n\n"
    doc.write_text(head + LIVE_HEADING + live.rstrip() + "\n\n" + section, encoding="utf-8")
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    live_data = summary.get("live") or {}
    measured = live_data.get("measured", [])
    measured.append({"date": date, "model": model, "rows": [
        {k: r[k] for k in ("fake_claims_in_report", "tokens", "model_seconds", "stop_reason", "model_finished")}
        | {"findings": r["code"]["found"], "claims_kept": r["claims"]["kept"],
           "claims_dropped": r["claims"]["dropped"]} for r in rows]})
    summary["live"] = {"status": "measured", "measured": measured}
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--runs", type=int, default=3, help="number of scans (default 3)")
    p.add_argument("--dry-run", action="store_true", help="scripted perfect model; no key, no model network call")
    p.add_argument("--write-doc", action="store_true", help="append the measured table to docs/eval_results.md")
    args = p.parse_args(argv)
    if args.dry_run and args.write_doc:
        p.error("--write-doc is refused with --dry-run: scripted numbers are not live measurements")
    if not 1 <= args.runs <= 10:
        p.error("--runs must be between 1 and 10")
    case = next(c for c in load_cases() if c["name"] == "vulnerable")
    model = "scripted perfect model (dry-run)" if args.dry_run else load_config(ROOT / "config.yaml")["model"]
    print(f"MAYA live eval | model={model} | runs={args.runs} | cap={TOKEN_CAP} tokens/scan | "
          f"max_steps={MAX_STEPS} | effort={REASONING_EFFORT} | case=vulnerable demo app (loopback)")
    rows = []
    with tempfile.TemporaryDirectory(prefix="maya-eval-live-") as tmp:
        for i in range(1, args.runs + 1):
            provider = ScriptedModel("perfect") if args.dry_run else \
                Provider.from_config(ROOT / "config.yaml", reasoning_effort=REASONING_EFFORT)
            try:
                r = run_case(case, f"live-{i}", Path(tmp) / f"run-{i}", provider=provider, model=model, goal=GOAL,
                             max_steps=MAX_STEPS)
            except ProviderError as e:  # already key-scrubbed by the provider
                print(f"run {i}: model call failed: {e}")
                return 1
            rows.append(r)
            reasons = "; ".join(f"{k} x{v}" for k, v in r["claims"]["drop_reasons"].items()) or "-"
            print(f"run {i}: findings={r['code']['found']} (expected {r['code']['expected']}, "
                  f"FPs {len(r['code']['false_positives'])}, missed {len(r['code']['missed'])}) | "
                  f"claims kept={r['claims']['kept']} dropped={r['claims']['dropped']} ({reasons}) | "
                  f"FAKE CLAIMS IN REPORT={r['fake_claims_in_report']} | notes dropped={r['notes_dropped']} | "
                  f"tokens={r['tokens']} | model latency={r['model_seconds']}s over {r['model_calls']} calls | "
                  f"stop={r['stop_reason']} | report={'yes' if r['report_produced'] else 'NO'}")
            for d in r["fake_claim_details"]:
                print(f"    fake claim: {d}")
    print("\n" + live_table(rows))
    if args.write_doc:
        write_doc(rows, model)
        print(f"\nappended to {DOC.relative_to(ROOT)} and {SUMMARY.relative_to(ROOT)}")
    elif not args.dry_run:
        print("\n(not written; add --write-doc to append this table to docs/eval_results.md)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
