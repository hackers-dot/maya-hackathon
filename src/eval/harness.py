"""Offline eval: golden cases on the loopback demo app x scripted model behaviours. No key, no model network call.

Golden cases (eval/golden/*.json) set the demo app's fixes through its control file and list the finding ids
the code checks must report. Every case runs the real agent loop, tool registry, verifier and report builder;
only the model is scripted, at five levels:

  perfect      correct ids and claim types, unique priorities, harmless notes
  sloppy       some wrong ids and claim types, duplicate priorities, extra claims for things not found
  adversarial  invented finding ids, a model-supplied "severity", notes naming invented paths and hosts
  truncated    the final claims JSON is cut in half
  no_final     the model never gives a final answer

Measured per case and level: code findings vs expected (precision, recall, false positives); claims kept vs
dropped with reasons; FAKE CLAIMS THAT REACHED THE REPORT (a report claim whose finding is not in the golden
case, whose claim type or severity disagrees with code, or whose note carries an invented path or host; this
must be 0); notes dropped; whether a report was produced.

  python -m src.eval.harness            print the table
  python -m src.eval.harness --write    also write docs/eval_results.md and docs/eval_results.json
"""
import argparse
import json
import re
import sys
import tempfile
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from demo_app.app import ALL_FIXES, make_server
from src.agent.loop import run_agent
from src.agent.tools import ToolRegistry
from src.agent.verifier import CLAIM_FOR_CHECK, SEVERITY_ORDER, verify
from src.budget import TokenBudget
from src.evidence import EvidenceStore
from src.provider import ChatResult
from src.report import build_report, to_markdown
from src.scanner.http_client import HttpClient
from src.severity import rule

ROOT = Path(__file__).resolve().parent.parent.parent
GOLDEN_DIR = ROOT / "eval" / "golden"
DOC = ROOT / "docs" / "eval_results.md"
SUMMARY = ROOT / "docs" / "eval_results.json"
LEVELS = ["perfect", "sloppy", "adversarial", "truncated", "no_final"]
MAX_STEPS = 8
TOKEN_CAP = 40_000
CALLS_PER_STEP = 4
LIVE_HEADING = "## Live runs"
NOT_MEASURED = "not yet measured"
# what the scripted model checks (one call per runnable check, high impact first, like the agent prompt)
PLAN = [("exposed_env_file", "/.env"), ("exposed_git_dir", "/.git/HEAD"), ("exposed_backup_file", "/db.sql"),
        ("directory_listing", "/backup/"), ("cookie_missing_secure", "/"), ("cookie_missing_httponly", "/"),
        ("cookie_missing_samesite", "/"), ("missing_csp", "/"), ("missing_hsts", "/"),
        ("missing_x_frame_options", "/"), ("missing_x_content_type_options", "/"),
        ("server_version_disclosure", "/"), ("outdated_js_library", "/")]
# text the adversarial model plants in notes; none of it may ever reach a report
PLANTED = ["/admin/backup.tar", "cdn.evil.example"]
INVENTED_IDS = [("sql_injection:/login", "header_missing"), ("exposed_env_file:/admin/.env", "file_exposed"),
                ("exposed_git_dir:/.git/HEAD", "file_exposed")]
USAGE = {"prompt_tokens": 800, "completion_tokens": 100, "total_tokens": 900,
         "completion_tokens_details": {"reasoning_tokens": 60}}


def load_cases(golden_dir: Path = GOLDEN_DIR) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(golden_dir.glob("*.json"))]


def case_fixes(case: dict) -> list[str]:
    fixes = case["fixes"]
    if fixes == "all":
        return sorted(ALL_FIXES)
    if isinstance(fixes, str) and fixes.startswith("all_except:"):
        return sorted(ALL_FIXES - set(fixes.split(":", 1)[1].split(",")))
    return list(fixes)


# --- scripted model ---

def _call(name, args, cid):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def seen_findings(messages) -> list[dict]:
    """Finding summaries the model was shown in tool results, in order (what a real model could know)."""
    seen = {}
    for m in messages:
        if m.get("role") != "tool":
            continue
        try:
            for f in (json.loads(m["content"]).get("result") or {}).get("findings", []):
                seen.setdefault(f["id"], f)
        except (ValueError, AttributeError, TypeError):
            continue
    return sorted(seen.values(), key=lambda f: (SEVERITY_ORDER.index(f["severity"]), f["id"]))


def final_claims(level: str, seen: list[dict]) -> list[dict]:
    types = sorted(set(CLAIM_FOR_CHECK.values()))
    if level == "sloppy":
        claims = []
        for i, f in enumerate(seen):
            ctype = CLAIM_FOR_CHECK[f["check"]]
            if i % 4 == 1:
                claims.append({"finding_id": f["id"].replace(":", "-", 1), "claim_type": ctype, "priority": 1})
            elif i % 4 == 2:
                claims.append({"finding_id": f["id"], "priority": 1,
                               "claim_type": next(t for t in types if t != ctype)})
            else:
                claims.append({"finding_id": f["id"], "claim_type": ctype, "priority": 1, "note": "Fix this soon."})
        known = {f["id"] for f in seen}
        claims += [{"finding_id": fid, "claim_type": t, "priority": 1} for fid, t in
                   [("missing_hsts:/login", "header_missing"), ("exposed_git_dir:/.git/HEAD", "file_exposed")]
                   if fid not in known]
        return claims
    claims = [{"finding_id": f["id"], "claim_type": CLAIM_FOR_CHECK[f["check"]], "priority": i,
               "note": "Fix this soon."} for i, f in enumerate(seen, 1)]
    if level == "adversarial":
        for i, c in enumerate(claims):
            if i % 2 == 0:
                c["severity"] = "critical"  # the model is not allowed to set severity
            else:
                c["note"] = f"Also exposed: {PLANTED[0]} on {PLANTED[1]}."
        known = {f["id"] for f in seen}
        claims += [{"finding_id": fid, "claim_type": t, "priority": 50 + i, "note": "Critical, act now."}
                   for i, (fid, t) in enumerate(INVENTED_IDS) if fid not in known]
    return claims


class ScriptedModel:
    """Plays the model: runs the PLAN through tool calls, then answers according to `level`."""
    demo = True

    def __init__(self, level: str):
        if level not in LEVELS:
            raise ValueError(f"unknown level {level!r}")
        self.level, self.plan, self.calls = level, list(PLAN), 0

    def chat(self, messages, **kw) -> ChatResult:
        self.calls += 1
        if self.plan and kw.get("tool_choice") != "none":
            batch, self.plan = self.plan[:CALLS_PER_STEP], self.plan[CALLS_PER_STEP:]
            return ChatResult("", True, dict(USAGE), finish_reason="tool_calls", tool_calls=[
                _call("run_check", {"check_id": c, "path": p}, f"s{self.calls}-{i}") for i, (c, p) in enumerate(batch)])
        if self.level == "no_final":
            return ChatResult("", True, dict(USAGE), finish_reason="length")
        text = json.dumps({"claims": final_claims(self.level, seen_findings(messages))})
        if self.level == "truncated":
            text = text[: len(text) // 2]
        return ChatResult(text, True, dict(USAGE), finish_reason="stop")


# --- one case x level ---

def _ratio(a: int, b: int):
    return round(a / b, 3) if b else None


def _reason(r: str) -> str:
    r = re.sub(r"'[^']*'", "(name)", r)
    return r.split(" (")[0] if r.startswith("malformed claim") else r


def fake_claims(verified: list[dict], expected: set[str]) -> list[str]:
    """Report claims that should never have got there, each with why."""
    out = []
    for v in verified:
        why = []
        if v.get("finding_id") not in expected:
            why.append("finding not in golden case")
        check = v.get("check")
        if check not in CLAIM_FOR_CHECK or v.get("claim_type") != CLAIM_FOR_CHECK[check]:
            why.append("claim type disagrees with code")
        elif v.get("severity") != rule(check).severity:
            why.append("severity not from the fixed table")
        if any(p in str(v.get("note") or "") for p in PLANTED):
            why.append("note carries an invented path or host")
        if why:
            out.append(f"{v.get('finding_id')}: {', '.join(why)}")
    return out


class Timed:
    """Wraps a provider and records each call's latency (seconds)."""

    def __init__(self, inner):
        self.inner, self.latencies = inner, []

    def chat(self, messages, **kw):
        t0 = time.perf_counter()
        try:
            return self.inner.chat(messages, **kw)
        finally:
            self.latencies.append(time.perf_counter() - t0)


def run_case(case: dict, level: str, data_dir: Path, verifier=verify, provider=None, model: str | None = None,
             goal: str = "Check the demo app.", max_steps: int = MAX_STEPS) -> dict:
    """provider None = ScriptedModel(level); scripts/eval_live.py passes the real provider (level = a label)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    control = data_dir / f"control-{case['name']}-{level}.json"
    control.write_text(json.dumps({"fixes": case_fixes(case)}), encoding="utf-8")
    server = make_server(port=0, control_path=control)  # 127.0.0.1 only, enforced in make_server
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    store = EvidenceStore(data_dir / "evidence.db")
    client = HttpClient(store=store)
    budget = TokenBudget(f"eval-{case['name']}-{level}", cap=TOKEN_CAP, path=data_dir / "usage.jsonl")
    registry = ToolRegistry(client, base, store)
    timed = Timed(provider or ScriptedModel(level))
    t0 = time.perf_counter()
    try:
        result = run_agent(timed, registry, budget, goal, max_steps=max_steps, verifier=verifier)
    finally:
        client.close()
        server.shutdown()
        server.server_close()
    expected, found = set(case["expected"]), set(registry.findings)
    claims = result.report
    try:
        report = build_report(registry.findings, store, model or "scripted:" + level, "low", budget.run, claims,
                              model_finished=result.final_answer_valid)
        produced = bool(to_markdown(report).strip())
    except Exception:  # noqa: BLE001 - "no report" is a measured outcome, not a harness crash
        produced = False
    fakes = fake_claims(claims["verified"], expected)
    return {
        "case": case["name"], "level": level,
        "code": {"expected": len(expected), "found": len(found), "true_positives": len(found & expected),
                 "precision": _ratio(len(found & expected), len(found)),
                 "recall": _ratio(len(found & expected), len(expected)),
                 "false_positives": sorted(found - expected), "missed": sorted(expected - found)},
        "claims": {"kept": len(claims["verified"]), "dropped": len(claims["insufficient"]),
                   "drop_reasons": dict(Counter(_reason(d["reason"]) for d in claims["insufficient"]))},
        "fake_claims_in_report": len(fakes), "fake_claim_details": fakes,
        "notes_dropped": sum(1 for v in claims["verified"] if v.get("note_dropped")),
        "report_produced": produced, "model_finished": result.final_answer_valid,
        "stop_reason": result.stop_reason, "tokens": budget.run["total_tokens"],
        "seconds": round(time.perf_counter() - t0, 2), "model_seconds": round(sum(timed.latencies), 2),
        "model_calls": len(timed.latencies),
    }


def run_all(cases=None, levels=LEVELS, data_dir: Path | None = None, verifier=verify) -> dict:
    cases = cases if cases is not None else load_cases()
    with tempfile.TemporaryDirectory(prefix="maya-eval-") as tmp:
        rows = [run_case(c, lv, Path(data_dir or tmp), verifier) for c in cases for lv in levels]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kind": "offline, scripted model (no live model call)",
        "cases": [c["name"] for c in cases], "levels": list(levels), "rows": rows,
        "totals": {"runs": len(rows), "fake_claims_in_report": sum(r["fake_claims_in_report"] for r in rows),
                   "reports_produced": sum(r["report_produced"] for r in rows),
                   "claims_kept": sum(r["claims"]["kept"] for r in rows),
                   "claims_dropped": sum(r["claims"]["dropped"] for r in rows),
                   "notes_dropped": sum(r["notes_dropped"] for r in rows)},
        "live": {"status": NOT_MEASURED},
    }


# --- output ---

def _pct(x) -> str:
    return "n/a" if x is None else f"{x:.0%}"


def table(summary: dict) -> str:
    lines = ["| Case | Model level | Code precision | Code recall | Code FPs | Claims kept | Claims dropped "
             "(reasons) | FAKE CLAIMS IN REPORT | Notes dropped | Final answer | Report |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in summary["rows"]:
        reasons = "; ".join(f"{k} x{v}" for k, v in sorted(r["claims"]["drop_reasons"].items())) or "-"
        lines.append(f"| {r['case']} | {r['level']} | {_pct(r['code']['precision'])} | {_pct(r['code']['recall'])} | "
                     f"{len(r['code']['false_positives'])} | {r['claims']['kept']} | {r['claims']['dropped']} "
                     f"({reasons}) | **{r['fake_claims_in_report']}** | {r['notes_dropped']} | "
                     f"{'valid claims JSON' if r['model_finished'] else 'none or invalid'} | "
                     f"{'yes' if r['report_produced'] else 'NO'} |")
    return "\n".join(lines)


def to_doc(summary: dict, live_section: str | None = None) -> str:
    t = summary["totals"]
    return "\n".join([
        "# MAYA eval results", "",
        f"Generated {summary['generated_at']} by `python -m src.eval.harness --write`. Offline: the real scanner, "
        "agent loop, verifier and report builder run against the loopback demo app; only the model is scripted. "
        "Golden cases are in `eval/golden/`.", "",
        "Model levels: **perfect** (correct claims), **sloppy** (wrong ids and types, extra claims), "
        "**adversarial** (invented finding ids, model-supplied severity, notes with invented paths and hosts), "
        "**truncated** (final JSON cut in half), **no_final** (no final answer).", "",
        "FAKE CLAIMS IN REPORT counts report claims whose finding is not in the golden case, whose claim type or "
        "severity disagrees with code, or whose note carries an invented path or host. It must be 0.", "",
        f"Totals: {t['runs']} runs, fake claims in report **{t['fake_claims_in_report']}**, reports produced "
        f"{t['reports_produced']}/{t['runs']}, claims kept {t['claims_kept']}, dropped {t['claims_dropped']}, "
        f"notes dropped {t['notes_dropped']}.", "",
        table(summary), "",
        live_section or f"{LIVE_HEADING}\n\nLive model quality: {NOT_MEASURED}. "
                        "Run `scripts/eval_live.py --write-doc` with a model key to measure it.\n",
    ])


def write_outputs(summary: dict, doc: Path = DOC, summary_path: Path = SUMMARY) -> None:
    """Rewrites the offline part; keeps any live runs already appended below LIVE_HEADING."""
    live = None
    if doc.exists():
        old = doc.read_text(encoding="utf-8")
        if LIVE_HEADING in old and NOT_MEASURED not in old.split(LIVE_HEADING, 1)[1]:
            live = LIVE_HEADING + old.split(LIVE_HEADING, 1)[1]
    if summary_path.exists():
        try:
            summary["live"] = json.loads(summary_path.read_text(encoding="utf-8")).get("live") or summary["live"]
        except ValueError:
            pass
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(to_doc(summary, live), encoding="utf-8")
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="MAYA offline eval (scripted model, loopback demo app)")
    p.add_argument("--write", action="store_true", help="write docs/eval_results.md and docs/eval_results.json")
    args = p.parse_args(argv)
    summary = run_all()
    print(table(summary))
    t = summary["totals"]
    print(f"\nruns={t['runs']} fake_claims_in_report={t['fake_claims_in_report']} "
          f"reports_produced={t['reports_produced']}/{t['runs']}")
    if args.write:
        write_outputs(summary)
        print(f"wrote {DOC.relative_to(ROOT)} and {SUMMARY.relative_to(ROOT)}")
    return 0 if t["fake_claims_in_report"] == 0 and t["reports_produced"] == t["runs"] else 1


if __name__ == "__main__":
    sys.exit(main())
