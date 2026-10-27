"""Bounded agent loop over native tool calling. The model plans; code executes and verifies.

The last step is reserved for the final answer: on it (or as soon as every runnable check type has been
attempted) the loop sends tool_choice="none" plus an instruction to write the final claims. Tool calls
returned on that step are ignored, never executed. The report does not depend on the model finishing.
"""
import json
from dataclasses import dataclass, field

from src.agent.tools import MAX_GET_EVIDENCE, RUNNABLE_CHECKS, TOOL_SPECS, ToolRegistry
from src.agent.verifier import CLAIM_FOR_CHECK, verify
from src.budget import BudgetExceeded, TokenBudget
from src.evidence import redact_text

MAX_STEPS = 8
MAX_CALLS_PER_STEP = 4

PRIORITY = [
    ("exposed_env_file", "/.env"), ("exposed_git_dir", "/.git/HEAD"), ("exposed_backup_file", "/db.sql"),
    ("exposed_backup_file", "/backup.zip"), ("directory_listing", "/backup/"),
    ("cookie_missing_secure", "/"), ("cookie_missing_httponly", "/"), ("cookie_missing_samesite", "/"),
    ("missing_csp", "/"), ("missing_hsts", "/"), ("missing_x_frame_options", "/"),
    ("missing_x_content_type_options", "/"), ("server_version_disclosure", "/"),
]
assert {c for c, _ in PRIORITY} == set(RUNNABLE_CHECKS)  # the prompt names exactly the runnable checks

SYSTEM_PROMPT = (
    "You are MAYA, a security agent checking a site its owner authorized. Use only the provided tools; "
    "code runs them and returns findings. Severity is decided by code, never by you. "
    f"The runnable checks are exactly: {', '.join(RUNNABLE_CHECKS)}. Paths must start with '/'. "
    f"Make at most {MAX_CALLS_PER_STEP} run_check calls per step; extra calls are deferred and must be repeated "
    "in a later step. Order: high-impact items first, then cookies, then headers: "
    + "; ".join(f"{c} {p}" for c, p in PRIORITY) + ". "
    "Each run_check result includes an evidence_summary line per finding; quote from it. You do not need "
    f"get_evidence (it is capped at {MAX_GET_EVIDENCE} calls per run). When every check above was attempted, "
    "stop calling tools and give the final answer. "
    "Final reply: ONLY a JSON object "
    '{"claims": [{"finding_id": "...", "claim_type": "...", "quoted_evidence": "..."}]}. '
    f"claim_type is one of: {', '.join(sorted(set(CLAIM_FOR_CHECK.values())))}. "
    "quoted_evidence must be copied exactly from the evidence line AND show the issue itself: for a missing "
    "header, the 'recorded response headers:' line; for a cookie, its set-cookie line; for a version, the "
    "server or x-powered-by line; for an exposed file, a KEY=value line, the git ref, or the content-type line; "
    "for a directory listing, the 'Index of' line. Never quote unrelated page body text. Claims whose quote does "
    "not support them are dropped."
)
FINAL_INSTRUCTION = (
    "Stop calling tools now. Write the final answer: ONLY the JSON object "
    '{"claims": [...]} for the findings returned so far, quoting their evidence_summary lines.'
)


@dataclass
class AgentResult:
    stop_reason: str  # "final" (model chose to finish) | "forced_final" (reserved step) | "budget_exceeded"
    trace: list[dict] = field(default_factory=list)
    report: dict = field(default_factory=lambda: {"verified": [], "insufficient": []})
    final_answer_valid: bool = False  # True only if the model returned a parseable claims JSON


def _tokens(usage: dict) -> dict:
    details = usage.get("completion_tokens_details") or {}
    return {"prompt": usage.get("prompt_tokens"), "completion": usage.get("completion_tokens"),
            "reasoning": details.get("reasoning_tokens")}


def _parse_claims(answer: str):
    try:
        data = json.loads(answer)
    except ValueError:
        return None
    return data.get("claims") if isinstance(data, dict) and isinstance(data.get("claims"), list) else None


def run_agent(provider, registry: ToolRegistry, budget: TokenBudget, goal: str,
              max_steps: int = MAX_STEPS) -> AgentResult:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": goal}]
    result = AgentResult(stop_reason="forced_final")
    for step in range(1, max_steps + 1):
        forced = step == max_steps or registry.all_checks_attempted()
        if forced:
            reply = provider.chat(messages + [{"role": "user", "content": FINAL_INSTRUCTION}],
                                  tools=TOOL_SPECS, tool_choice="none")
        else:
            reply = provider.chat(messages, tools=TOOL_SPECS, tool_choice="auto")
        tokens = _tokens(reply.usage)
        try:
            budget.add(reply.usage)
        except BudgetExceeded as e:
            result.trace.append({"step": step, "tool": None, "args": None, "result_summary": f"stopped: {e}",
                                 "tokens": tokens})
            result.stop_reason = "budget_exceeded"
            break
        if forced or not reply.tool_calls:
            claims = _parse_claims(reply.answer)
            result.final_answer_valid = claims is not None
            summary = "final answer" if claims is not None else "final answer is not valid claims JSON"
            if forced:
                summary = "forced final step: " + summary + (" (tool calls ignored)" if reply.tool_calls else "")
            result.trace.append({"step": step, "tool": None, "args": None, "tokens": tokens, "result_summary": summary})
            result.report = verify(claims or [], registry.findings, registry.store)
            result.stop_reason = "forced_final" if forced else "final"
            break
        messages.append({"role": "assistant", "content": reply.answer or None, "tool_calls": reply.tool_calls})
        for i, call in enumerate(reply.tool_calls):
            fn = call.get("function") or {}
            if i < MAX_CALLS_PER_STEP:
                out = registry.call(fn.get("name"), fn.get("arguments"))
            else:  # never executed; still answered so every tool_call_id gets a result
                out = registry.defer(fn.get("name"), fn.get("arguments"))
            result.trace.append({"step": step, "tool": fn.get("name"), "args": redact_text(str(fn.get("arguments"))),
                                 "result_summary": out["summary"], "tokens": tokens})
            messages.append({"role": "tool", "tool_call_id": call.get("id", f"call_{step}_{i}"),
                             "content": json.dumps({k: v for k, v in out.items() if k != "summary"})})
    return result
